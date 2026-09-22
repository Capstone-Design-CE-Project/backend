"""레거시 파이프라인 API (프론트 ApiService 호환 유지).

기존 Flutter 코드(api_service.dart)가 호출하는 경로를 그대로 유지한다.
  POST /pipeline/session          세션 생성
  POST /pipeline/stt/{id}         음성 → STT → 형태소 저장
  POST /pipeline/generate/{id}    DB → 프롬프트 → 이미지
  POST /pipeline/run/{id}         위 과정을 한 번에
  GET  /pipeline/album/{id}       앨범 조회 (배열 그대로 반환)
  GET  /pipeline/session/{id}     세션 상세 (ConsultationSession 모델 대응)

신규 화면은 /auth, /clients, /sessions 쪽 엔드포인트를 쓰는 것을 권장한다.
이 라우터는 토큰 없이 접근 가능하므로 운영 환경에서는
LEGACY_PIPELINE_ENABLED=False 로 끄고 마이그레이션할 것.
"""
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session as DBSession

from database import get_db
from models import Album, CounselSession, SessionStatus, User
import promptmaker
from schemas import AlbumOut
from services import image_service, morph_service, serializers, stt_service
from services import summary_service

router = APIRouter(prefix="/pipeline", tags=["pipeline (legacy)"])


def _get_session(session_id: int, db: DBSession) -> CounselSession:
    sess = db.query(CounselSession).filter(CounselSession.id == session_id).first()
    if not sess:
        raise HTTPException(status_code=404, detail="상담 세션을 찾을 수 없습니다.")
    return sess


# ── 세션 생성 ─────────────────────────────────────────────────
@router.post("/session")
def create_session(
    user_id: int = Query(..., description="내담자 ID (users.id)"),
    filename: str = Query("audio.mp3"),
    db: DBSession = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="유저를 찾을 수 없습니다.")

    sess = CounselSession(
        user_id=user_id,
        counselor_id=user.counselor_id,
        raw_filename=filename,
        status=SessionStatus.RECORDING,
        started_at=datetime.now(),
    )
    db.add(sess)
    db.commit()
    db.refresh(sess)

    print(f"[SESSION] 세션 생성 완료: session_id={sess.id}")
    return {
        "session_id": sess.id,
        "id": sess.id,
        "user_id": user_id,
        "filename": filename,
        "status": sess.status,
        "created_at": sess.created_at,
    }


# ── STT + 형태소 분석 ─────────────────────────────────────────
@router.post("/stt/{session_id}")
def process_audio(
    session_id: int,
    order_index: int = Query(1),
    file: UploadFile = File(...),
    db: DBSession = Depends(get_db),
):
    sess = _get_session(session_id, db)

    audio_path = stt_service.save_upload(file.file, file.filename)
    sess.raw_filename = file.filename
    sess.audio_path = audio_path
    db.commit()

    result = stt_service.transcribe(audio_path)
    segments = result.get("segments", [])
    full_text = morph_service.mask_pii(result.get("full_text", ""))

    if not full_text and not segments:
        sess.status = SessionStatus.FAILED
        db.commit()
        raise HTTPException(status_code=400, detail="STT 변환 실패")

    for seg in segments:
        seg["text"] = morph_service.mask_pii(seg.get("text", ""))

    if segments:
        promptmaker.save_segments_to_db(session_id, segments, db)
    else:
        promptmaker.save_stt_result(
            session_id, full_text, db, order_index=order_index, qa_type="A"
        )

    morph_saved = 0
    utterances = stt_service.split_speakers(segments).get("client", []) or [full_text]
    for utterance in utterances:
        for analyzed in morph_service.analyze(utterance):
            morph_saved += promptmaker.save_morphs_to_db(session_id, analyzed, db)

    sess.transcript = full_text
    sess.status = SessionStatus.TRANSCRIBED
    db.commit()

    return {
        "session_id": session_id,
        "stt_text": full_text,
        "sentences": utterances,
        "order_index": order_index,
        "stt_engine": result.get("engine"),
        "morph_saved": morph_saved,
    }


# ── 이미지 생성 ───────────────────────────────────────────────
@router.post("/generate/{session_id}")
def generate_image(session_id: int, db: DBSession = Depends(get_db)):
    sess = _get_session(session_id, db)

    scene = promptmaker.make_scene_description(session_id, db)
    if not scene:
        scene = promptmaker.make_scene_from_keywords(sess.keyword_list)
    if not scene and not sess.summary:
        raise HTTPException(
            status_code=400, detail="장면 설명 생성 실패 - DB에 명사 데이터가 없습니다."
        )

    prompt = promptmaker.make_dalle_prompt(scene or sess.summary)
    result = image_service.generate_image(prompt)
    if not result.get("image_url"):
        raise HTTPException(
            status_code=500, detail=f"이미지 생성 실패: {result.get('error')}"
        )

    album = Album(
        session_id=session_id,
        title=sess.title,
        image_url=result["image_url"],
        prompt=prompt,
        scene=scene or None,
    )
    db.add(album)
    sess.status = SessionStatus.IMAGE_READY
    db.commit()
    db.refresh(album)

    return {
        "album_id": album.id,
        "id": album.id,
        "session_id": session_id,
        "scene": scene,
        "prompt": prompt,
        "image_url": album.image_url,
        "created_at": album.created_at,
    }


# ── 전체 파이프라인 ───────────────────────────────────────────
@router.post("/run/{session_id}")
def run_full_pipeline(
    session_id: int,
    order_index: int = Query(1),
    file: UploadFile = File(...),
    db: DBSession = Depends(get_db),
):
    """STT → 형태소 분석 → 요약 → 이미지 생성을 한 번에 실행."""
    stt_result = process_audio(session_id, order_index, file, db)
    sess = _get_session(session_id, db)

    # 요약까지 만들어 두면 프론트가 한 번의 호출로 화면을 채울 수 있다.
    utterances = promptmaker.get_client_utterances(session_id, db)
    scene = promptmaker.make_scene_description(session_id, db)
    summary = summary_service.generate_summary(
        transcript=sess.transcript or "", client_utterances=utterances,
        scene_description=scene,
    )
    sess.summary = summary["summary"]
    sess.keywords = ", ".join(summary["keywords"])
    if not sess.title and summary.get("title"):
        sess.title = summary["title"]
    db.commit()

    image_result = generate_image(session_id, db)

    return {
        "session_id": session_id,
        "stt_text": stt_result["stt_text"],
        "summary": sess.summary,
        "keywords": sess.keyword_list,
        "scene": image_result["scene"],
        "prompt": image_result["prompt"],
        "image_url": image_result["image_url"],
        "album_id": image_result["album_id"],
        "id": image_result["album_id"],
    }


# ── 앨범 조회 (Dart: List<AlbumImage>) ────────────────────────
@router.get("/album/{session_id}", response_model=List[AlbumOut])
def get_album(session_id: int, db: DBSession = Depends(get_db)):
    """기존 Dart 코드가 배열을 기대하므로 배열 그대로 반환한다."""
    _get_session(session_id, db)
    albums = (
        db.query(Album)
        .filter(Album.session_id == session_id)
        .order_by(Album.id)
        .all()
    )
    return [serializers.album_out(a) for a in albums]


# ── 세션 상세 (Dart: ConsultationSession) ─────────────────────
@router.get("/session/{session_id}")
def get_session_detail(session_id: int, db: DBSession = Depends(get_db)):
    sess = _get_session(session_id, db)
    return {
        "session_id": sess.id,
        "id": sess.id,
        "user_id": sess.user_id,
        "created_at": sess.created_at,
        "status": sess.status,
        "title": sess.title,
        "summary": sess.summary,
        "keywords": sess.keyword_list,
        "albums": [serializers.album_out(a).model_dump() for a in sess.albums],
    }
