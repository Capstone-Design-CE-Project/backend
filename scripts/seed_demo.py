"""데모 데이터 생성 스크립트.

디자인 시안에 등장하는 상담사/내담자/일정/상담 기록을 그대로 만들어
프론트엔드가 곧바로 화면을 붙여볼 수 있게 한다.

    python -m scripts.seed_demo          # 데이터 추가
    python -m scripts.seed_demo --reset  # 전부 지우고 다시 생성
"""
import argparse
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.security import generate_code, hash_password       # noqa: E402
from database import Base, SessionLocal, engine, init_db     # noqa: E402
from models import (                                          # noqa: E402
    Album, Appointment, AppointmentStatus, ClientSetting, Counselor,
    CounselSession, Notification, QaLog, SessionStatus, User,
)

COUNSELOR_PASSWORD = "capstone1234"
CLIENT_PIN = "1234"

CLIENTS = [
    {
        "name": "김영희", "age": 78, "gender": "F", "phone": "010-1111-2222",
        "cognitive_stage": "경도 인지 장애", "login_id": "kim_young_hee",
        "memo": "치매 상담 진행 중. 바닷가와 고향 이야기에 반응이 좋음.",
    },
    {
        "name": "배병옥", "age": 72, "gender": "F", "phone": "010-3333-4444",
        "cognitive_stage": "인지 단계 유지", "login_id": "bae_byung_ok",
        "memo": "고향집 마당, 감나무 기억이 선명함.",
    },
    {
        "name": "박명수", "age": 65, "gender": "M", "phone": "010-5555-6666",
        "cognitive_stage": "예방 및 정서 관리", "login_id": "park_myung_su",
        "memo": "단기 기억 저하 호소. 가족 사진 활용 권장.",
    },
]

DEMO_DIALOG = [
    ("Q", "오늘 하루는 어떠셨어요?"),
    ("A", "고향집 마당이 자꾸 생각나요."),
    ("Q", "그 마당에는 무엇이 있었나요?"),
    ("A", "커다란 감나무가 한 그루 서 있었어요."),
    ("Q", "감나무는 어떤 모습이었나요?"),
    ("A", "가을이면 주황색 감이 주렁주렁 열렸습니다."),
    ("Q", "그때 누구와 함께 계셨어요?"),
    ("A", "어머니와 함께 마루에 앉아 감을 깎아 먹었어요."),
]

DEMO_SUMMARY = (
    "내담자는 어린 시절 충청도 고향집 마당에 면한 감나무와 감이 익어가던 계절을 "
    "떠올렸습니다. 감나무에 주황색 감이 주렁주렁 열리던 가을 풍경과 어머니와 함께 "
    "마루에 앉아 감을 깎아 먹던 장면을 구체적으로 회상했습니다. 기억을 떠올리는 "
    "동안 정서적으로 안정된 기조를 보였으며, 가족과 함께한 일상에 대해 긍정적인 "
    "반응을 보였습니다."
)

DEMO_ALBUMS = [
    ("평화로운 해변", "https://picsum.photos/seed/memory-beach/1024/1024"),
    ("숲속 산책", "https://picsum.photos/seed/memory-forest/1024/1024"),
    ("가족과의 기억", "https://picsum.photos/seed/memory-family/1024/1024"),
    ("봄날의 매화", "https://picsum.photos/seed/memory-plum/1024/1024"),
    ("아침 명상", "https://picsum.photos/seed/memory-morning/1024/1024"),
    ("정겨운 고향", "https://picsum.photos/seed/memory-hometown/1024/1024"),
]


def reset() -> None:
    print("[SEED] 기존 테이블 삭제 후 재생성")
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def seed() -> None:
    db = SessionLocal()
    try:
        if db.query(Counselor).filter(Counselor.login_id == "counselor_lee").first():
            print("[SEED] 이미 데모 데이터가 있습니다. --reset 옵션을 쓰세요.")
            return

        counselor = Counselor(
            login_id="counselor_lee",
            password_hash=hash_password(COUNSELOR_PASSWORD),
            name="이전수",
            email="counselor.lee@example.com",
        )
        db.add(counselor)
        db.flush()

        clients = []
        for info in CLIENTS:
            client = User(
                counselor_id=counselor.id,
                name=info["name"],
                age=info["age"],
                gender=info["gender"],
                phone=info["phone"],
                memo=info["memo"],
                cognitive_stage=info["cognitive_stage"],
                member_code=generate_code("#MEM"),
                login_id=info["login_id"],
                pin_hash=hash_password(CLIENT_PIN),
                last_visit_at=datetime.now() - timedelta(days=len(clients) * 7 + 3),
            )
            db.add(client)
            db.flush()
            db.add(ClientSetting(user_id=client.id))
            clients.append(client)

        # ── 상담 일정 ─────────────────────────────────────────
        now = datetime.now()
        schedule = [
            (clients[0], now + timedelta(hours=3), "치매 상담", "상담실 1"),
            (clients[1], now + timedelta(days=1, hours=2), "회상 치료", "상담실 2"),
            (clients[2], now + timedelta(days=4), "정서 관리 상담", "상담실 1"),
            (clients[0], now + timedelta(days=10), "정기 면담", "상담실 1"),
        ]
        for client, when, purpose, place in schedule:
            db.add(
                Appointment(
                    counselor_id=counselor.id,
                    client_id=client.id,
                    scheduled_at=when,
                    duration_minutes=60,
                    purpose=purpose,
                    place=place,
                    status=AppointmentStatus.SCHEDULED,
                )
            )

        # ── 완료된 상담 세션 (배병옥) ─────────────────────────
        target = clients[1]
        sess = CounselSession(
            user_id=target.id,
            counselor_id=counselor.id,
            title="고향집 마당",
            status=SessionStatus.COMPLETED,
            raw_filename="capstone_demo.mp3",
            duration_seconds=765,
            transcript=" ".join(text for _, text in DEMO_DIALOG),
            summary=DEMO_SUMMARY,
            keywords="고향집, 감나무, 마당, 어머니, 가을",
            reference_code=f"{generate_code('#CC')}-XP",
            started_at=now - timedelta(days=2, hours=2),
            ended_at=now - timedelta(days=2, hours=1),
        )
        db.add(sess)
        db.flush()

        for index, (qa_type, text) in enumerate(DEMO_DIALOG, start=1):
            db.add(
                QaLog(
                    session_id=sess.id,
                    type=qa_type,
                    text=text,
                    order_index=index,
                    speaker_label="1" if qa_type == "Q" else "2",
                    spoken_at_ms=index * 3000,
                )
            )

        for offset, (title, url) in enumerate(DEMO_ALBUMS):
            db.add(
                Album(
                    session_id=sess.id,
                    title=title,
                    image_url=url,
                    prompt="A warm Korean watercolor illustration of a hometown yard "
                           "with a persimmon tree in autumn.",
                    scene="커다란 감나무, 주황색 감, 고향집 마당",
                    is_selected=(offset == 0),
                    created_at=now - timedelta(days=offset * 5 + 2),
                )
            )

        db.add(
            Notification(
                counselor_id=counselor.id,
                type="report",
                title="상담 리포트가 저장되었습니다.",
                body=f"{target.name} 내담자 상담 기록 {sess.reference_code}",
                link_session_id=sess.id,
            )
        )
        db.add(
            Notification(
                counselor_id=counselor.id,
                type="schedule",
                title="오늘 예정된 상담이 있습니다.",
                body="김영희 내담자 상담이 곧 시작됩니다.",
            )
        )
        db.add(
            Notification(
                counselor_id=counselor.id,
                type="info",
                title="신규 내담자가 등록되었습니다.",
                body="박명수 내담자가 목록에 추가되었습니다.",
            )
        )

        db.commit()
        print("[SEED] 데모 데이터 생성 완료")
        print(f"       상담사 로그인 : counselor_lee / {COUNSELOR_PASSWORD}")
        print(f"       어르신 로그인 : {CLIENTS[1]['login_id']} / PIN {CLIENT_PIN}")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="데모 데이터 생성")
    parser.add_argument("--reset", action="store_true", help="기존 데이터 삭제 후 생성")
    args = parser.parse_args()

    if args.reset:
        reset()
    else:
        init_db()
    seed()
