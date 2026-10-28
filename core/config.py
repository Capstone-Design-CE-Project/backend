"""애플리케이션 전역 설정.

.env 파일 또는 환경변수에서 값을 읽어온다.
DB 정보가 없으면 로컬 개발용 SQLite로 자동 폴백하므로
PostgreSQL 없이도 서버를 띄워 API 형태를 확인할 수 있다.
"""
from functools import lru_cache
from typing import List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ── 앱 ────────────────────────────────────────────────────
    APP_NAME: str = "기억의 조각 API"
    APP_VERSION: str = "3.0.0"
    DEBUG: bool = False

    # ── DB ────────────────────────────────────────────────────
    DATABASE_URL: Optional[str] = None      # 직접 지정 시 최우선
    DB_HOST: Optional[str] = None
    DB_PORT: str = "5432"
    DB_NAME: Optional[str] = None
    DB_USER: Optional[str] = None
    DB_PASSWORD: Optional[str] = None
    SQLITE_PATH: str = "./capstone.db"

    # ── 인증 ──────────────────────────────────────────────────
    JWT_SECRET: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 12      # 상담사 12시간
    CLIENT_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 14  # 어르신 앱은 길게

    # ── 외부 API ──────────────────────────────────────────────
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_TEXT_MODEL: str = "gpt-4o-mini"
    OPENAI_IMAGE_MODEL: str = "dall-e-3"
    CLOVA_SPEECH_SECRET: Optional[str] = None
    CLOVA_SPEECH_INVOKE_URL: Optional[str] = None

    # ── 파일 ──────────────────────────────────────────────────
    UPLOAD_DIR: str = "./uploads"
    MEDIA_URL_PREFIX: str = "/media"

    # ── 데모 편의 (운영 전 반드시 끌 것) ──────────────────────
    # 내담자 등록 시 PIN을 주지 않으면 기본 PIN을 자동 발급한다.
    # 프론트에 PIN 입력 UI가 아직 없어 어르신 로그인이 막히는 것을 피하기 위한 장치다.
    # 누구나 기본 PIN으로 로그인할 수 있게 되므로,
    # 실제 환자 데이터를 다루기 전에 반드시 False 로 바꿀 것.
    AUTO_ISSUE_CLIENT_PIN: bool = True
    DEFAULT_CLIENT_PIN: str = "123456"   # 어르신 앱이 6자리만 받는다

    # ── 시간대 ────────────────────────────────────────────────
    # DB에 저장하는 모든 시각의 기준 시간대. 서버 OS/DB 시간대와 무관하게 적용된다.
    APP_TIMEZONE: str = "Asia/Seoul"
    APP_TZ_FALLBACK_OFFSET_MINUTES: int = 540   # tzdata 없을 때 KST(+09:00)

    # ── 기타 ──────────────────────────────────────────────────
    CORS_ORIGINS: str = "*"
    # 외부 키가 없을 때 목(mock) 응답으로 대체할지 여부.
    # 프론트 연동/데모 단계에서 True 권장.
    USE_MOCK_ON_MISSING_KEY: bool = True
    # konlpy(Java) 미설치 환경에서도 서버가 뜨도록 형태소 분석은 선택 기능
    ENABLE_MORPH_ANALYSIS: bool = True
    # 인증 없이 열려 있는 구버전 /pipeline 라우터 노출 여부
    LEGACY_PIPELINE_ENABLED: bool = True

    @property
    def database_url(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL
        if self.DB_HOST and self.DB_NAME and self.DB_USER:
            return (
                f"postgresql+psycopg2://{self.DB_USER}:{self.DB_PASSWORD}"
                f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
            )
        return f"sqlite:///{self.SQLITE_PATH}"

    @property
    def cors_origin_list(self) -> List[str]:
        if self.CORS_ORIGINS.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def openai_enabled(self) -> bool:
        return bool(self.OPENAI_API_KEY)

    @property
    def clova_enabled(self) -> bool:
        return bool(self.CLOVA_SPEECH_SECRET and self.CLOVA_SPEECH_INVOKE_URL)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
