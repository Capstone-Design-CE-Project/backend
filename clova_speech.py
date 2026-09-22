import requests
import os
import json
from dotenv import load_dotenv

load_dotenv()


def transcribe(file_path: str) -> dict:
    """
    CLOVA Speech 장문 인식 API
    화자 분리(Diarization) 포함

    Returns:
        {
            "full_text": "전체 텍스트",
            "segments": [
                {"speaker": "1", "text": "상담사 발화"},
                {"speaker": "2", "text": "어르신 발화"},
                ...
            ]
        }
    """
    invoke_url = os.getenv("CLOVA_SPEECH_INVOKE_URL")
    secret_key = os.getenv("CLOVA_SPEECH_SECRET")

    headers = {
        "X-CLOVASPEECH-API-KEY": secret_key
    }

    request_body = {
        "language":      "ko-KR",
        "completion":    "sync",       # 완료될 때까지 대기
        "wordAlignment": True,
        "fullText":      True,
        "diarization": {
            "enable":          True,
            "speakerCountMin": 2,      # 상담사 + 어르신 = 2명
            "speakerCountMax": 2
        }
    }

    try:
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
                timeout=300            # 장문 인식은 시간이 걸리므로 5분 설정
            )

    except FileNotFoundError:
        print(f"[STT] 파일을 찾을 수 없습니다: {file_path}")
        return {"full_text": "", "segments": []}
    except Exception as e:
        print(f"[STT] 요청 오류: {e}")
        return {"full_text": "", "segments": []}

    if response.status_code == 200:
        result = response.json()
        print(f"[원본 응답] segments: {result.get('segments', [])}")

        # 전체 텍스트
        full_text = result.get("text", "")

        # 화자별 분리된 세그먼트 추출
        segments = result.get("segments", [])
        speaker_texts = []
        for seg in segments:
            speaker_label = seg.get("speaker", {}).get("label", "1")
            text          = seg.get("text", "").strip()
            if text:  # 빈 텍스트 제외
                speaker_texts.append({
                    "speaker": speaker_label,
                    "text":    text
                })

        print(f"[STT] 변환 완료 - 전체: {full_text[:50]}...")
        print(f"[STT] 화자 분리 결과: {len(speaker_texts)}개 발화")
        for seg in speaker_texts:
            print(f"      화자 {seg['speaker']}: {seg['text']}")

        return {
            "full_text": full_text,
            "segments":  speaker_texts
        }

    else:
        print(f"[STT] 실패: {response.status_code} / {response.text}")
        return {"full_text": "", "segments": []}
    