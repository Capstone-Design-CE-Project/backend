import requests
import os
from dotenv import load_dotenv

load_dotenv()

def transcribe(file_path: str) -> str:
    """
    음성/영상 파일 → 텍스트 변환 (CLOVA Speech STT)

    Args:
        file_path: 변환할 음성 파일 경로 (.mp3, .wav, .m4a 등)

    Returns:
        변환된 텍스트 문자열 (실패 시 빈 문자열)
    """
    url = "https://clovaspeech-gw.ncloud.com/recog/v1/stt?lang=Kor"

    headers = {
        "X-CLOVASPEECH-API-KEY": os.getenv("CLOVA_SPEECH_SECRET"),
        "Content-Type": "application/octet-stream"
    }

    try:
        with open(file_path, "rb") as f:
            audio_data = f.read()

        response = requests.post(url, headers=headers, data=audio_data)

        if response.status_code == 200:
            text = response.json().get("text", "")
            print(f"[STT] 변환 완료: {text}")
            return text
        else:
            print(f"[STT] 실패: {response.status_code} / {response.text}")
            return ""

    except FileNotFoundError:
        print(f"[STT] 파일을 찾을 수 없습니다: {file_path}")
        return ""
    except Exception as e:
        print(f"[STT] 오류 발생: {e}")
        return ""
