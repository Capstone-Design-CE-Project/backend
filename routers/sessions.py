"""상담 세션 - 녹음부터 회상 이미지 저장까지의 전체 플로우.

디자인 화면 대응
  session-info-input  → POST /sessions            (상담 시작)
  recording           → POST /sessions/{id}/recording
  text-confirm-popup  → GET  /sessions/{id}/transcript
  summary-review      → POST /sessions/{id}/summary
  image-generation    → POST /sessions/{id}/image
  session-complete    → POST /sessions/{id}/complete
  session-detail      → GET  /sessions/{id}
"""
import secrets
from typing import List, Optional

from fastapi import (
    APIRouter, Depends, File, Form, HTTPException, Query, UploadFile,
)
from sqlalchemy.orm import Session as DBSession

from core.deps import get_current_counselor, get_owned_client, get_owned_session
from core.security import generate_code
from core.timeutil import now_local
from database import get_db
from models import (
    Album, Appointment, AppointmentStatus, Counselor, CounselSession,
    Notification, SessionStatus,
)
import promptmaker
from schemas import (
    AlbumOut, ImageGenerateRequest, MessageResponse, RecordingUploadResponse,
    SessionCompleteRequest, SessionCompleteResponse, SessionCreate, SessionDetail,
    SessionListResponse, SummaryResponse, SummaryUpdate, TranscriptResponse,
)
from services import image_service, morph_service, serializers, stt_service
from services import summary_service

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _reference_code(db: DBSession) -> str:
    """#CC-8821-XP 형태의 참조 번호."""
    letters = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    for _ in range(10):
        suffix = "".join(secrets.choice(letters) for _ in range(2))
        code = f"{generate_code('#CC')}-{suffix}"
        if not db.query(CounselSession).filter(
            CounselSession.reference_code == code
        ).first():
            return code
    raise HTTPException(status_code=500, detail="참조 번호 생성에 실패했습니다.")


# ══════════════════════════════════════════════════════════════
# 세션 시작 / 조회
# ══════════════════════════════════════════════════════════════
@router.post("", response_model=SessionDetail, status_code=201)
def start_session(
    payload: SessionCreate,
    counselor: Counselor = Depends(get_current_counselor),
    db: DBSession = Depends(get_db),
):
    """session-start-popup의 '네, 시작합니다'."""
    client = get_owned_client(payload.client_id, db, counselor)

    sess = CounselSession(
        user_id=client.id,
        counselor_id=counselor.id,
        title=payload.title,
        status=SessionStatus.RECORDING,
        started_at=now_local(),
    )
    db.add(sess)

    # 예약에서 시작한 경우 해당 일정을 완료 처리
    if payload.appointment_id:
        appt = (
            db.query(Appointment)
            .filter(
                Appointment.id == payload.appointment_id,
                Appointment.counselor_id == counselor.id,
            )
            .first()
        )
        if appt:
            appt.status = AppointmentStatus.DONE

    client.last_visit_at = now_local()
    db.commit()
    db.refresh(sess)
    return serializers.session_detail(sess)


@router.get("", response_model=SessionListResponse)
def list_sessions(
    client_id: Optional[int] = None,
    status: Optional[str] = Query(None, description="pending|recording|...|completed"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    counselor: Counselor = Depends(get_current_counselor),
    db: DBSession = Depends(get_db),
):
    query = db.query(CounselSession).filter(
        CounselSession.counselor_id == counselor.id
    )
    if client_id:
        query = query.filter(CounselSession.user_id == client_id)
    if status:
        query = query.filter(CounselSession.status == status)

    total = query.count()
    rows = (
        query.order_by(CounselSession.created_at.desc(), CounselSession.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return SessionListResponse(
        total=total, items=[serializers.session_brief(s) for s in rows]
    )


@router.get("/{session_id}", response_model=SessionDetail)
def get_session(
    session_id: int,
    counselor: Counselor = Depends(get_current_counselor),
    db: DBSession = Depends(get_db),
):
    """session-detail 화면 (요약 탭 + 기록 탭)."""
    sess = get_owned_session(session_id, db, counselor)
    return serializers.session_detail(sess)


@router.delete("/{session_id}", response_model=MessageResponse)
def delete_session(
    session_id: int,
    counselor: Counselor = Depends(get_current_counselor),
    db: DBSession = Depends(get_db),
):
    sess = get_owned_session(session_id, db, counselor)
    db.delete(sess)
    db.commit()
    return MessageResponse(message="상담 세션이 삭제되었습니다.")


# ══════════════════════════════════════════════════════════════
# 녹음 업로드 → STT → 형태소 분석
# ══════════════════════════════════════════════════════════════
@router.post("/{session_id}/recording", response_model=RecordingUploadResponse)
def upload_recording(
    session_id: int,
    file: UploadFile = File(..., description="녹음 파일 (.mp3 / .wav / .m4a)"),
    duration_seconds: int = Form(0),
    counselor: Counselor = Depends(get_current_counselor),
    db: DBSession = Depends(get_db),
):
    """recording 화면의 '완료' → 음성 업로드 후 STT + 형태소 분석까지 수행."""
    sess = get_owned_session(session_id, db, counselor)

    audio_path = stt_service.save_upload(file.file, file.filename)
    sess.raw_filename = file.filename
    sess.audio_path = audio_path
    if duration_seconds:
        sess.duration_seconds = duration_seconds
    db.commit()

    result = stt_service.transcribe(audio_path)
    segments = result.get("segments", [])
    full_text = result.get("full_text", "")

    if not full_text and not segments:
        sess.status = SessionStatus.FAILED
        db.commit()
        raise HTTPException(status_code=502, detail="음성 인식(STT)에 실패했습니다.")

    # 개인정보 마스킹 후 저장
    for seg in segments:
        seg["text"] = morph_service.mask_pii(seg.get("text", ""))
    full_text = morph_service.mask_pii(full_text)

    if segments:
        promptmaker.save_segments_to_db(session_id, segments, db)
    else:
        # 화자 분리 실패 시 전문을 어르신 발화로 저장 (fallback)
        promptmaker.save_stt_result(session_id, full_text, db, qa_type="A")

    # 어르신 발화만 형태소 분석 → noun/adjective/verb 저장
    morph_saved = 0
    for utterance in stt_service.split_speakers(segments).get("client", []) or [full_text]:
        for analyzed in morph_service.analyze(utterance):
            morph_saved += promptmaker.save_morphs_to_db(session_id, analyzed, db)

    sess.transcript = full_text
    sess.status = SessionStatus.TRANSCRIBED
    sess.ended_at = now_local()
    db.commit()
    db.refresh(sess)

    return RecordingUploadResponse(
        session_id=sess.id,
        status=sess.status,
        transcript=full_text,
        lines=[serializers.qa_out(q) for q in sess.qa_logs],
        duration_seconds=sess.duration_seconds or 0,
        stt_engine=result.get("engine", "unknown"),
        morph_saved=morph_saved,
    )


@router.get("/{session_id}/transcript", response_model=TranscriptResponse)
def get_transcript(
    session_id: int,
    counselor: Counselor = Depends(get_current_counselor),
    db: DBSession = Depends(get_db),
):
    """text-confirm-popup: 화자별 대화 로그."""
    sess = get_owned_session(session_id, db, counselor)
    return TranscriptResponse(
        session_id=sess.id,
        status=sess.status,
        transcript=sess.transcript,
        lines=[serializers.qa_out(q) for q in sess.qa_logs],
    )


@router.post("/{session_id}/transcript", response_model=TranscriptResponse)
def append_transcript_line(
    session_id: int,
    text: str = Form(...),
    speaker: str = Form("A", description="'Q'=상담사 / 'A'=내담자"),
    counselor: Counselor = Depends(get_current_counselor),
    db: DBSession = Depends(get_db),
):
    """녹음 중 실시간 텍스트를 직접 덧붙일 때 사용 (STT 없이 테스트도 가능)."""
    sess = get_owned_session(session_id, db, counselor)
    qa_type = "Q" if speaker.upper() == "Q" else "A"
    cleaned = morph_service.mask_pii(text.strip())

    promptmaker.save_stt_result(session_id, cleaned, db, qa_type=qa_type)
    if qa_type == "A":
        for analyzed in morph_service.analyze(cleaned):
            promptmaker.save_morphs_to_db(session_id, analyzed, db)

    sess.transcript = "\n".join(q.text for q in sess.qa_logs)
    if sess.status == SessionStatus.RECORDING:
        sess.status = SessionStatus.TRANSCRIBED
    db.commit()
    db.refresh(sess)

    return TranscriptResponse(
        session_id=sess.id,
        status=sess.status,
        transcript=sess.transcript,
        lines=[serializers.qa_out(q) for q in sess.qa_logs],
    )


# ══════════════════════════════════════════════════════════════
# 요약
# ══════════════════════════════════════════════════════════════
@router.post("/{session_id}/summary", response_model=SummaryResponse)
def create_summary(
    session_id: int,
    counselor: Counselor = Depends(get_current_counselor),
    db: DBSession = Depends(get_db),
):
    """summary-review 화면: 'AI가 작성한 초기 요약본' 생성."""
    sess = get_owned_session(session_id, db, counselor)

    utterances = promptmaker.get_client_utterances(session_id, db)
    if not utterances and not sess.transcript:
        raise HTTPException(status_code=400, detail="요약할 대화 기록이 없습니다.")

    scene = promptmaker.make_scene_description(session_id, db)
    result = summary_service.generate_summary(
        transcript=sess.transcript or "\n".join(utterances),
        client_utterances=utterances,
        scene_description=scene,
    )

    sess.summary = result["summary"]
    sess.keywords = ", ".join(result["keywords"])
    if not sess.title and result.get("title"):
        sess.title = result["title"]
    sess.status = SessionStatus.SUMMARIZED
    db.commit()
    db.refresh(sess)

    return SummaryResponse(
        session_id=sess.id,
        title=sess.title,
        summary=sess.summary,
        keywords=sess.keyword_list,
        generated_by=result["generated_by"],
    )


@router.patch("/{session_id}/summary", response_model=SummaryResponse)
def update_summary(
    session_id: int,
    payload: SummaryUpdate,
    counselor: Counselor = Depends(get_current_counselor),
    db: DBSession = Depends(get_db),
):
    """session-detail의 '기록 수정' - 상담사가 요약을 직접 손본다."""
    sess = get_owned_session(session_id, db, counselor)
    data = payload.model_dump(exclude_unset=True)

    if "summary" in data:
        sess.summary = data["summary"]
    if "title" in data:
        sess.title = data["title"]
    if "keywords" in data and data["keywords"] is not None:
        sess.keywords = ", ".join(k.strip() for k in data["keywords"] if k.strip())

    db.commit()
    db.refresh(sess)
    return SummaryResponse(
        session_id=sess.id,
        title=sess.title,
        summary=sess.summary or "",
        keywords=sess.keyword_list,
        generated_by="counselor",
    )


# ══════════════════════════════════════════════════════════════
# 회상 이미지
# ══════════════════════════════════════════════════════════════
@router.post("/{session_id}/image", response_model=AlbumOut)
def generate_session_image(
    session_id: int,
    payload: Optional[ImageGenerateRequest] = None,
    counselor: Counselor = Depends(get_current_counselor),
    db: DBSession = Depends(get_db),
):
    """summary-review의 '이미지 생성하기' / image-review의 '다시 생성'."""
    payload = payload or ImageGenerateRequest()
    sess = get_owned_session(session_id, db, counselor)

    scene = promptmaker.make_scene_description(session_id, db)
    if not scene:
        # 형태소 분석 결과가 없으면 요약 키워드로 장면을 구성
        scene = promptmaker.make_scene_from_keywords(sess.keyword_list)
    if not scene and not sess.summary:
        raise HTTPException(
            status_code=400, detail="이미지를 만들 근거(형태소/요약)가 없습니다."
        )

    prompt = promptmaker.make_dalle_prompt(scene or sess.summary, payload.extra_prompt)
    result = image_service.generate_image(prompt)
    if not result.get("image_url"):
        raise HTTPException(
            status_code=502, detail=f"이미지 생성 실패: {result.get('error')}"
        )

    if payload.replace_previous:
        db.query(Album).filter(
            Album.session_id == session_id, Album.is_selected.is_(False)
        ).delete(synchronize_session=False)

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
    return serializers.album_out(album)


@router.get("/{session_id}/images", response_model=List[AlbumOut])
def list_session_images(
    session_id: int,
    counselor: Counselor = Depends(get_current_counselor),
    db: DBSession = Depends(get_db),
):
    sess = get_owned_session(session_id, db, counselor)
    return [serializers.album_out(a) for a in sess.albums]


# ══════════════════════════════════════════════════════════════
# 완료 저장
# ══════════════════════════════════════════════════════════════
@router.post("/{session_id}/complete", response_model=SessionCompleteResponse)
def complete_session(
    session_id: int,
    payload: Optional[SessionCompleteRequest] = None,
    counselor: Counselor = Depends(get_current_counselor),
    db: DBSession = Depends(get_db),
):
    """image-review의 '이대로 저장' → session-complete 화면."""
    payload = payload or SessionCompleteRequest()
    sess = get_owned_session(session_id, db, counselor)

    if payload.summary is not None:
        sess.summary = payload.summary
    if payload.title is not None:
        sess.title = payload.title

    selected: Optional[Album] = None
    if payload.album_id:
        selected = (
            db.query(Album)
            .filter(Album.id == payload.album_id, Album.session_id == session_id)
            .first()
        )
        if not selected:
            raise HTTPException(status_code=404, detail="선택한 이미지를 찾을 수 없습니다.")
    elif sess.albums:
        selected = sess.albums[-1]

    if selected:
        for album in sess.albums:
            album.is_selected = album.id == selected.id
        if sess.title and not selected.title:
            selected.title = sess.title

    if not sess.reference_code:
        sess.reference_code = _reference_code(db)
    if not sess.ended_at:
        sess.ended_at = now_local()
    sess.status = SessionStatus.COMPLETED

    db.add(
        Notification(
            counselor_id=counselor.id,
            type="report",
            title="상담 리포트가 저장되었습니다.",
            body=f"{sess.user.name} 내담자 상담 기록 {sess.reference_code}",
            link_session_id=sess.id,
        )
    )
    db.commit()
    db.refresh(sess)

    return SessionCompleteResponse(
        session_id=sess.id,
        status=sess.status,
        reference_code=sess.reference_code,
        recorded_at=sess.ended_at,
        selected_album=serializers.album_out(selected) if selected else None,
    )
