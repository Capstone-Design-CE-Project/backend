"""수동 점검 스크립트: 음성 없이 텍스트만으로 저장/분석 흐름 확인.

    python -m scripts.check_db [session_id] ["확인할 문장"]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import promptmaker                                # noqa: E402
from database import SessionLocal                 # noqa: E402
from models import CounselSession                 # noqa: E402
from services import morph_service                # noqa: E402

DEFAULT_TEXT = "남색 등대 옆에 흰색 배가 묶여있는 바닷가가 보여요"


def main() -> None:
    session_id = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    text = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_TEXT

    db = SessionLocal()
    try:
        if not db.query(CounselSession).filter(CounselSession.id == session_id).first():
            print(f"session_id={session_id} 세션이 없습니다. 먼저 세션을 생성하세요.")
            sys.exit(1)

        promptmaker.save_stt_result(session_id, text, db, qa_type="A")
        print("qa_log 저장 완료")

        for analyzed in morph_service.analyze(text):
            promptmaker.save_morphs_to_db(session_id, analyzed, db)

        print(f"장면 설명: {promptmaker.make_scene_description(session_id, db) or '(없음)'}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
