# test_clova.py
from database import SessionLocal
import clova_speech
import promptmaker

db = SessionLocal()

# 음성 파일 경로 입력 (본인 파일 경로로 바꾸세요)
file_path = r"C:\Users\HyunSuk\Documents\capstone.m4a"

# 1. CLOVA STT 변환
text = clova_speech.transcribe(file_path)
print(f"변환된 텍스트: {text}")

# 2. DB 저장
if text:
    promptmaker.save_stt_result(
        session_id=1,
        text=text,
        order_index=3,
        qa_type="A",
        db=db
    )
    print("DB 저장 완료!")
else:
    print("STT 변환 실패")

db.close()