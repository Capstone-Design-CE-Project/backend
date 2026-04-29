import requests
import os
import json
import time
from dotenv import load_dotenv

load_dotenv()

def transcribe(file_path: str) -> str:
    """
    CLOVA Speech 장문 인식 API
    비동기 방식: 파일 업로드 → 완료 대기 → 결과 수신
    """
    invoke_url = os.getenv("CLOVA_SPEECH_INVOKE_URL")
    secret_key = os.getenv("CLOVA_SPEECH_SECRET")

    # 1. 파일 업로드 및 인식 요청
    headers = {
        "X-CLOVASPEECH-API-KEY": secret_key
    }

    request_body = {
        "language": "ko-KR",
        "completion": "sync",   # sync: 완료까지 기다림
        "wordAlignment": True,
        "fullText": True,
        "diarization": {
            "enable": True,
            "speakerCountMin": 2,
            "speakerCountMax" : 2
        }
    }

    with open(file_path, "rb") as f:
        files = {
            "media": f,
            "params": (
                None,
                json.dumps(request_body, ensure_ascii=False),
                "application/json"
            )
        }

        response = requests.post(
            invoke_url + "/recognizer/upload",
            headers=headers,
            files=files,
            timeout=300
        )

    if response.status_code == 200:
        result = response.json()
        text = result.get("text", "")
        print(f"[STT] 변환 완료: {text}")
        return text
    else:
        print(f"[STT] 실패: {response.status_code} / {response.text}")
        return ""