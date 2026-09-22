"""STT 서비스 - CLOVA Speech 장문 인식 래퍼.

키가 설정되어 있지 않으면(로컬/데모 환경) 목(mock) 결과를 돌려주어
프론트엔드가 전체 플로우를 그대로 테스트할 수 있게 한다.
"""
import os
import shutil
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from core.config import settings

# 상담사=화자1, 어르신=화자2 라는 CLOVA diarization 규약
COUNSELOR_LABEL = "1"

_MOCK_SEGMENTS: List[Dict] = [
    {"speaker": "1", "text": "오늘 하루는 어떠셨어요?", "start": 0},
    {"speaker": "2", "text": "고향집 마당이 자꾸 생각나요.", "start": 2600},
    {"speaker": "1", "text": "그 마당에는 무엇이 있었나요?", "start": 6100},
    {"speaker": "2", "text": "커다란 감나무가 한 그루 서 있었어요.", "start": 8800},
    {"speaker": "1", "text": "감나무는 어떤 모습이었나요?", "start": 13200},
    {"speaker": "2", "text": "가을이면 주황색 감이 주렁주렁 열렸습니다.", "start": 15900},
    {"speaker": "1", "text": "그때 누구와 함께 계셨어요?", "start": 21000},
    {"speaker": "2", "text": "어머니와 함께 마루에 앉아 감을 깎아 먹었어요.", "start": 23400},
]


def save_upload(file_obj, filename: Optional[str]) -> str:
    """업로드된 음성 파일을 UPLOAD_DIR에 저장하고 경로를 반환."""
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    ext = os.path.splitext(filename or "")[1] or ".mp3"
    safe_name = f"{datetime.now():%Y%m%d}_{uuid.uuid4().hex[:8]}{ext}"
    path = os.path.join(settings.UPLOAD_DIR, safe_name)
    with open(path, "wb") as out:
        shutil.copyfileobj(file_obj, out)
    return path


def transcribe(file_path: str) -> Dict:
    """음성 파일 → {full_text, segments, engine}.

    segments: [{"speaker": "1"|"2", "text": str, "start": int(ms)}, ...]
    """
    if settings.clova_enabled:
        import clova_speech

        result = clova_speech.transcribe(file_path)
        full_text = result.get("full_text", "")
        segments = result.get("segments", [])
        if full_text or segments:
            return {"full_text": full_text, "segments": segments, "engine": "clova"}
        # CLOVA가 빈 결과를 주면 실패로 간주
        return {"full_text": "", "segments": [], "engine": "clova"}

    if settings.USE_MOCK_ON_MISSING_KEY:
        print("[STT] CLOVA 키가 없어 목 데이터로 대체합니다.")
        segments = [dict(s) for s in _MOCK_SEGMENTS]
        return {
            "full_text": " ".join(s["text"] for s in segments),
            "segments": segments,
            "engine": "mock",
        }

    return {"full_text": "", "segments": [], "engine": "none"}


def split_speakers(segments: List[Dict]) -> Dict[str, List[str]]:
    """화자별 발화 묶음. 형태소 분석은 어르신(A) 발화만 사용한다."""
    counselor, client = [], []
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        if str(seg.get("speaker", COUNSELOR_LABEL)) == COUNSELOR_LABEL:
            counselor.append(text)
        else:
            client.append(text)
    return {"counselor": counselor, "client": client}
