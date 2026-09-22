"""수동 점검 스크립트: 음성 파일 → STT → 화자 분리 → 형태소 분석 → DB 저장.

    python -m scripts.check_stt_pipeline <음성파일경로> [session_id]

기존 test_clova.py를 옮긴 것으로, 다음이 달라졌다.
  - 파일 경로를 하드코딩하지 않고 인자로 받는다
  - promptmaker가 SQLAlchemy 세션을 받도록 바뀌어 db 인자를 넘긴다
  - 루트에 두면 pytest가 수집해 버리므로 scripts/로 이동했다
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import promptmaker                                    # noqa: E402
from database import SessionLocal                     # noqa: E402
from models import CounselSession                     # noqa: E402
from services import morph_service, stt_service       # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    file_path = sys.argv[1]
    session_id = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    db = SessionLocal()
    try:
        if not db.query(CounselSession).filter(CounselSession.id == session_id).first():
            print(f"session_id={session_id} 세션이 없습니다. 먼저 세션을 생성하세요.")
            sys.exit(1)

        promptmaker.clear_session_data(session_id, db)

        print("=" * 50)
        print("1. STT 변환")
        print("=" * 50)
        result = stt_service.transcribe(file_path)
        full_text = result.get("full_text", "")
        segments = result.get("segments", [])

        if not full_text and not segments:
            print("STT 변환 실패 - 종료")
            sys.exit(1)

        print(f"엔진: {result.get('engine')}")
        print(f"전체 텍스트: {full_text}")
        print(f"분리된 발화 수: {len(segments)}개")

        print("\n" + "=" * 50)
        print("2. qa_log 저장 (화자 분리)")
        print("=" * 50)
        promptmaker.save_segments_to_db(session_id, segments, db)

        print("\n" + "=" * 50)
        print("3. 형태소 분석 → DB 저장 (어르신 발화만)")
        print("=" * 50)
        if not morph_service.is_available():
            print("형태소 분석기를 쓸 수 없습니다 (konlpy/JDK 확인).")
        else:
            total = 0
            for utterance in stt_service.split_speakers(segments)["client"]:
                print(f"\n어르신 발화 분석: {utterance}")
                for analyzed in morph_service.analyze(utterance):
                    total += promptmaker.save_morphs_to_db(session_id, analyzed, db)
            print(f"\n저장된 명사 수: {total}개")

        print("\n" + "=" * 50)
        print("4. 장면 설명")
        print("=" * 50)
        scene = promptmaker.make_scene_description(session_id, db)
        print(f"장면: {scene or '(없음)'}")
        print(f"프롬프트: {promptmaker.make_dalle_prompt(scene)}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
