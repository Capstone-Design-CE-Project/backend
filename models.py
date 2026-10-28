"""ORM 모델 - 디자인 시안(상담사 앱 + 어르신 앱)에 맞춘 스키마 v3.

  counselor   상담사 계정
  users       내담자(어르신) - 상담사가 등록, PIN으로 앱 로그인
  session     상담 세션 1회 (녹음 → 요약 → 회상 이미지)
  qa_log      화자 분리된 대화 원문
  noun/adjective/verb  형태소 분석 결과
  album       생성된 회상 이미지
  appointment 상담 일정(캘린더)
  notification 상담사 알림
  client_setting 어르신 앱 접근성 설정
"""
from sqlalchemy import (
    BigInteger, Boolean, CHAR, Column, DateTime, ForeignKey, Integer,
    String, Text, UniqueConstraint, Index,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from core.timeutil import now_local
from database import Base

# SQLite에는 BIGINT AUTOINCREMENT가 없어 variant로 처리(로컬 폴백 대응)
PK = BigInteger().with_variant(Integer, "sqlite")
FK = BigInteger().with_variant(Integer, "sqlite")


# ── 상수 ──────────────────────────────────────────────────────
class SessionStatus:
    """상담 세션 진행 상태 - 디자인 플로우와 1:1 대응."""
    PENDING = "pending"          # 상담 대기 (session-info-input)
    RECORDING = "recording"      # 녹음 중 (recording)
    TRANSCRIBED = "transcribed"  # STT 완료
    SUMMARIZED = "summarized"    # 요약 검토 (summary-review)
    IMAGE_READY = "image_ready"  # 이미지 검토 (image-review)
    COMPLETED = "completed"      # 저장 완료 (session-complete)
    FAILED = "failed"

    ALL = [PENDING, RECORDING, TRANSCRIBED, SUMMARIZED, IMAGE_READY, COMPLETED, FAILED]


class AppointmentStatus:
    SCHEDULED = "scheduled"
    DONE = "done"
    CANCELED = "canceled"

    ALL = [SCHEDULED, DONE, CANCELED]


# ── 상담사 ────────────────────────────────────────────────────
class Counselor(Base):
    """counselor-login 화면의 상담사 계정."""
    __tablename__ = "counselor"

    id            = Column(PK, primary_key=True, autoincrement=True)
    login_id      = Column(String(50), nullable=False, unique=True)  # counselor_lee
    password_hash = Column(String(255), nullable=False)
    name          = Column(String(50), nullable=False)               # 이전수
    email         = Column(String(120))
    phone         = Column(String(20))
    # 표기(+82/하이픈) 차이를 흡수한 중복 검사용 값
    phone_digits  = Column(String(20), index=True)
    organization  = Column(String(100))              # 소속 기관
    profile_image_url = Column(Text)
    is_active     = Column(Boolean, nullable=False, default=True)
    created_at    = Column(DateTime, default=now_local, server_default=func.now())

    clients       = relationship("User", back_populates="counselor")
    sessions      = relationship("CounselSession", back_populates="counselor")
    appointments  = relationship("Appointment", back_populates="counselor")
    notifications = relationship(
        "Notification", back_populates="counselor", cascade="all, delete-orphan"
    )


# ── 내담자(어르신) ────────────────────────────────────────────
class User(Base):
    """내담자. client-list / client-detail / new-client-form 화면 대응."""
    __tablename__ = "users"

    id           = Column(PK, primary_key=True, autoincrement=True)
    counselor_id = Column(FK, ForeignKey("counselor.id"))
    name         = Column(String(50), nullable=False)
    age          = Column(Integer)
    gender       = Column(CHAR(1))          # 'M' / 'F'
    phone        = Column(String(20))
    # 전화번호에서 숫자만 남긴 값. 어르신 앱이 단말 전화번호로 로그인하므로
    # 표기(+82/하이픈) 차이를 흡수하려고 조회용으로 따로 둔다.
    phone_digits = Column(String(20), index=True)
    memo         = Column(Text)             # 상담 메모 및 특이사항
    cognitive_stage = Column(String(50))    # 예: '경도 인지 장애'
    member_code  = Column(String(20), unique=True)   # 고유 번호 #MEM-7290
    login_id     = Column(String(50), unique=True)   # 어르신 앱 ID 로그인
    pin_hash     = Column(String(255))               # 4~6자리 PIN 해시
    profile_image_url = Column(Text)
    last_visit_at = Column(DateTime)
    is_active    = Column(Boolean, nullable=False, default=True)
    created_at   = Column(DateTime, default=now_local, server_default=func.now())

    counselor    = relationship("Counselor", back_populates="clients")
    sessions     = relationship(
        "CounselSession", back_populates="user", cascade="all, delete-orphan"
    )
    appointments = relationship(
        "Appointment", back_populates="client", cascade="all, delete-orphan"
    )
    setting      = relationship(
        "ClientSetting", back_populates="user", uselist=False,
        cascade="all, delete-orphan",
    )


# ── 상담 세션 ─────────────────────────────────────────────────
class CounselSession(Base):
    """상담 1회. 녹음 → STT → 요약 → 회상 이미지까지의 단위."""
    __tablename__ = "session"

    id           = Column(PK, primary_key=True, autoincrement=True)
    user_id      = Column(FK, ForeignKey("users.id"), nullable=False)
    counselor_id = Column(FK, ForeignKey("counselor.id"))
    title        = Column(String(120))       # '고향집 마당'
    status       = Column(String(20), nullable=False, default=SessionStatus.PENDING)
    raw_filename = Column(String(255))       # 원본 음성 파일명
    audio_path   = Column(Text)              # 서버에 저장된 경로
    duration_seconds = Column(Integer, default=0)
    transcript   = Column(Text)              # STT 전문
    summary      = Column(Text)              # AI 요약본(상담사 수정 가능)
    keywords     = Column(Text)              # 핵심 기억 키워드 (콤마 구분)
    analysis_json = Column(Text)             # 형태소/관계 분석 원본 (nouns/relations)
    reference_code = Column(String(30), unique=True)  # #CC-8821-XP
    started_at   = Column(DateTime)
    ended_at     = Column(DateTime)
    created_at   = Column(DateTime, default=now_local, server_default=func.now())

    user      = relationship("User", back_populates="sessions")
    counselor = relationship("Counselor", back_populates="sessions")
    qa_logs   = relationship(
        "QaLog", back_populates="session", cascade="all, delete-orphan",
        order_by="QaLog.order_index",
    )
    nouns     = relationship("Noun", back_populates="session", cascade="all, delete-orphan")
    albums    = relationship(
        "Album", back_populates="session", cascade="all, delete-orphan",
        order_by="Album.id",
    )

    @property
    def keyword_list(self) -> list:
        if not self.keywords:
            return []
        return [k.strip() for k in self.keywords.split(",") if k.strip()]


# 하위 호환: 기존 코드가 `from models import Session`으로 참조하던 이름
Session = CounselSession


# ── 대화 원문 ─────────────────────────────────────────────────
class QaLog(Base):
    """화자 분리된 대화 로그. text-confirm-popup 화면에 그대로 표시된다."""
    __tablename__ = "qa_log"
    __table_args__ = (
        UniqueConstraint("session_id", "order_index", "type", name="uq_qa_log_order"),
        Index("idx_qa_session", "session_id", "order_index"),
    )

    id          = Column(PK, primary_key=True, autoincrement=True)
    session_id  = Column(FK, ForeignKey("session.id"), nullable=False)
    type        = Column(CHAR(1), nullable=False)    # 'Q'=상담사, 'A'=어르신
    text        = Column(Text, nullable=False)
    order_index = Column(Integer, nullable=False)
    speaker_label = Column(String(10))               # CLOVA 화자 라벨 원본
    spoken_at_ms  = Column(Integer)                  # 발화 시작 시각(ms)

    session = relationship("CounselSession", back_populates="qa_logs")


# ── 형태소 분석 결과 ──────────────────────────────────────────
class Noun(Base):
    __tablename__ = "noun"
    __table_args__ = (Index("idx_noun_session", "session_id"),)

    id           = Column(PK, primary_key=True, autoincrement=True)
    session_id   = Column(FK, ForeignKey("session.id"), nullable=False)
    value        = Column(String(100), nullable=False)
    target_noun  = Column(String(100))
    is_base_noun = Column(Boolean, nullable=False, default=False)

    session    = relationship("CounselSession", back_populates="nouns")
    adjectives = relationship(
        "Adjective", back_populates="noun", cascade="all, delete-orphan"
    )
    verbs      = relationship(
        "Verb", back_populates="noun", cascade="all, delete-orphan"
    )


class Adjective(Base):
    __tablename__ = "adjective"

    id      = Column(PK, primary_key=True, autoincrement=True)
    noun_id = Column(FK, ForeignKey("noun.id"), nullable=False)
    value   = Column(String(100), nullable=False)

    noun = relationship("Noun", back_populates="adjectives")


class Verb(Base):
    __tablename__ = "verb"

    id      = Column(PK, primary_key=True, autoincrement=True)
    noun_id = Column(FK, ForeignKey("noun.id"), nullable=False)
    value   = Column(String(100), nullable=False)
    # 1=긍정, 0=부정. "안 먹었다"를 "먹었다"로 그리지 않기 위해 보존한다.
    polarity_bit = Column(Integer, nullable=False, default=1)

    noun = relationship("Noun", back_populates="verbs")


# ── 회상 이미지 앨범 ──────────────────────────────────────────
class Album(Base):
    """생성된 회상 이미지. client-album 화면의 카드 한 장."""
    __tablename__ = "album"
    __table_args__ = (Index("idx_album_session", "session_id"),)

    id          = Column(PK, primary_key=True, autoincrement=True)
    session_id  = Column(FK, ForeignKey("session.id"), nullable=False)
    title       = Column(String(120))        # '평화로운 해변'
    image_url   = Column(Text, nullable=False)
    prompt      = Column(Text)
    scene       = Column(Text)               # 한국어 장면 설명
    is_selected = Column(Boolean, nullable=False, default=False)  # '이대로 저장'된 것
    created_at  = Column(DateTime, default=now_local, server_default=func.now())

    session = relationship("CounselSession", back_populates="albums")


# ── 상담 일정 ─────────────────────────────────────────────────
class Appointment(Base):
    """calendar / client-home의 '다음 상담 예약'."""
    __tablename__ = "appointment"
    __table_args__ = (Index("idx_appointment_time", "counselor_id", "scheduled_at"),)

    id            = Column(PK, primary_key=True, autoincrement=True)
    counselor_id  = Column(FK, ForeignKey("counselor.id"), nullable=False)
    client_id     = Column(FK, ForeignKey("users.id"), nullable=False)
    scheduled_at  = Column(DateTime, nullable=False)
    duration_minutes = Column(Integer, nullable=False, default=60)
    purpose       = Column(String(120))      # '치매 상담'
    place         = Column(String(120))      # '김철수 영정'
    memo          = Column(Text)
    status        = Column(String(20), nullable=False, default=AppointmentStatus.SCHEDULED)
    created_at    = Column(DateTime, default=now_local, server_default=func.now())

    counselor = relationship("Counselor", back_populates="appointments")
    client    = relationship("User", back_populates="appointments")


# ── 상담사 알림 ───────────────────────────────────────────────
class Notification(Base):
    """dashboard의 '신규 알림 3개'."""
    __tablename__ = "notification"

    id           = Column(PK, primary_key=True, autoincrement=True)
    counselor_id = Column(FK, ForeignKey("counselor.id"), nullable=False)
    type         = Column(String(30), nullable=False, default="info")
    title        = Column(String(120), nullable=False)
    body         = Column(Text)
    link_session_id = Column(FK, ForeignKey("session.id"))
    is_read      = Column(Boolean, nullable=False, default=False)
    created_at   = Column(DateTime, default=now_local, server_default=func.now())

    counselor = relationship("Counselor", back_populates="notifications")


# ── 어르신 앱 접근성 설정 ─────────────────────────────────────
class ClientSetting(Base):
    """client-settings 화면: 글자 크기 / 고대비 모드 / 터치 진동."""
    __tablename__ = "client_setting"

    user_id        = Column(FK, ForeignKey("users.id"), primary_key=True)
    font_scale     = Column(String(10), nullable=False, default="large")  # medium|large|xlarge
    high_contrast  = Column(Boolean, nullable=False, default=False)
    haptic_feedback = Column(Boolean, nullable=False, default=True)
    show_help_tips = Column(Boolean, nullable=False, default=True)
    updated_at     = Column(DateTime, default=now_local, onupdate=now_local, server_default=func.now())

    user = relationship("User", back_populates="setting")
