# test_db.py
from database import SessionLocal
from promptmaker import save_stt_result

db = SessionLocal()

# 음성파일 대신 텍스트 직접 입력
test_text = "남색 등대 옆에 흰색 배가 묶여있는 바닷가가 보여요"

# qa_log에 저장
save_stt_result(
    session_id=1,
    text=test_text,
    order_index=2,
    qa_type="A",
    db=db
)

print("DB 저장 완료!")
db.close()