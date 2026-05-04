import os
import shutil
import sys

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy.orm import Session as DBSession
from openai import OpenAI
from dotenv import load_dotenv

from database import get_db
from models import Session, User, Album
import clova_speech
import promptmaker

# 팀원 A 형태소 분석 코드 import
# extract.py가 같은 폴더에 있어야 함
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from promptmaker_first import extract_nouns_adjectives_verbs, split_sentences_by_connective

load_dotenv()

router = APIRouter(prefix="/pipeline", tags=["pipeline"])
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


# ── 세션 생성 ──────────────────────────────────────────────────
@router.post("/session")
def create_session(user_id: int, filename: str, db: DBSession = Depends(get_db)):
    """
    새 상담 세션 생성

    Args:
        user_id:  어르신 ID (users 테이블)
        filename: 원본 파일명
    """
    # 유저 존재 확인
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="유저를 찾을 수 없습니다.")

    session = Session(user_id=user_id, raw_filename=filename)
    db.add(session)
    db.commit()
    db.refresh(session)

    print(f"[SESSION] 세션 생성 완료: session_id={session.id}")
    return {"session_id": session.id, "user_id": user_id, "filename": filename}


# ── 음성 파일 → STT → 형태소 분석 → DB 저장 ───────────────────
@router.post("/stt/{session_id}")
async def process_audio(
    session_id: int,
    order_index: int = 1,
    file: UploadFile = File(...),
    db: DBSession = Depends(get_db)
):
    """
    음성 파일 업로드 → CLOVA STT → 형태소 분석 → DB 저장

    Args:
        session_id:  상담 세션 ID
        order_index: Q&A 순서 번호
        file:        음성 파일 (.mp3, .wav, .m4a)
    """
    # 1. 파일 임시 저장
    file_path = f"/tmp/{file.filename}"
    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # 2. CLOVA STT 변환
    text = clova_speech.transcribe(file_path)
    if not text:
        raise HTTPException(status_code=400, detail="STT 변환 실패")

    # 3. qa_log에 원문 저장
    promptmaker.save_stt_result(
        session_id=session_id,
        text=text,
        order_index=order_index,
        qa_type="A",
        db=db
    )

    # 4. 문장 분리 후 형태소 분석
    sentences = split_sentences_by_connective(text)
    for sentence in sentences:
        result = extract_nouns_adjectives_verbs(sentence)
        if isinstance(result, dict) and 'noun_objects' in result:
            promptmaker.save_morphs_to_db(session_id, result, db)

    return {
        "session_id":  session_id,
        "stt_text":    text,
        "sentences":   sentences,
        "order_index": order_index
    }


# ── DB → 프롬프트 → DALL-E → 앨범 저장 ────────────────────────
@router.post("/generate/{session_id}")
def generate_image(session_id: int, db: DBSession = Depends(get_db)):
    """
    DB에서 형태소 분석 결과를 꺼내
    DALL-E로 이미지 생성 후 album 테이블에 저장

    Args:
        session_id: 상담 세션 ID
    """
    # 1. DB에서 꺼내서 장면 설명 생성
    scene = promptmaker.make_scene_description(session_id, db)
    if not scene:
        raise HTTPException(status_code=400, detail="장면 설명 생성 실패 - DB에 명사 데이터가 없습니다.")

    # 2. DALL-E 프롬프트 생성
    dalle_prompt = promptmaker.make_dalle_prompt(scene)

    # 3. DALL-E 이미지 생성
    try:
        image_response = client.images.generate(
            model="dall-e-3",
            prompt=dalle_prompt,
            size="1024x1024",
            quality="standard"
        )
        image_url = image_response.data[0].url
        print(f"[DALL-E] 이미지 생성 완료: {image_url}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DALL-E 이미지 생성 실패: {str(e)}")

    # 4. album 테이블에 저장
    album = Album(
        session_id=session_id,
        image_url=image_url,
        prompt=dalle_prompt
    )
    db.add(album)
    db.commit()
    db.refresh(album)

    return {
        "album_id":  album.id,
        "scene":     scene,
        "prompt":    dalle_prompt,
        "image_url": image_url
    }


# ── 전체 파이프라인 한 번에 실행 ───────────────────────────────
@router.post("/run/{session_id}")
async def run_full_pipeline(
    session_id: int,
    order_index: int = 1,
    file: UploadFile = File(...),
    db: DBSession = Depends(get_db)
):
    """
    STT → 형태소 분석 → DB 저장 → DALL-E 이미지 생성을
    한 번에 실행하는 파이프라인

    Args:
        session_id:  상담 세션 ID
        order_index: Q&A 순서 번호
        file:        음성 파일
    """
    # STT + DB 저장
    stt_result = await process_audio(session_id, order_index, file, db)

    # 이미지 생성
    image_result = generate_image(session_id, db)

    return {
        "session_id": session_id,
        "stt_text":   stt_result["stt_text"],
        "scene":      image_result["scene"],
        "prompt":     image_result["prompt"],
        "image_url":  image_result["image_url"],
        "album_id":   image_result["album_id"]
    }


# ── 앨범 조회 ──────────────────────────────────────────────────
@router.get("/album/{session_id}")
def get_album(session_id: int, db: DBSession = Depends(get_db)):
    """특정 세션의 앨범 조회"""
    albums = db.query(Album).filter(
        Album.session_id == session_id
    ).all()

    return [
        {
            "album_id":   a.id,
            "image_url":  a.image_url,
            "prompt":     a.prompt,
            "created_at": str(a.created_at)
        }
        for a in albums
    ]
