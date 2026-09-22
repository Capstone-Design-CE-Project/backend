"""DB 엔진 / 세션 / Base 정의."""
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from core.config import settings

DB_URL = settings.database_url

# SQLite는 스레드 제약이 있어 옵션을 따로 준다(로컬 개발 폴백용).
_connect_args = {"check_same_thread": False} if DB_URL.startswith("sqlite") else {}

engine = create_engine(
    DB_URL,
    echo=settings.DEBUG,
    pool_pre_ping=True,
    connect_args=_connect_args,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()


def get_db():
    """FastAPI 의존성 주입용 DB 세션."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """테이블이 없으면 생성 (schema_v3.sql을 직접 실행했다면 no-op)."""
    import models  # noqa: F401  (모델 등록)

    Base.metadata.create_all(bind=engine)
