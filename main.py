"""기억의 조각 - 치매 어르신 회상 치료 상담 앱 백엔드.

상담사 앱 / 어르신 앱 두 클라이언트를 하나의 FastAPI 서버로 지원한다.

  [상담사]  로그인 → 대시보드 → 내담자 관리 → 상담 녹음
            → STT/형태소 분석 → AI 요약 → 회상 이미지 생성 → 리포트 저장
  [어르신]  ID+PIN 로그인 → 홈(다이어리) → 앨범 → 요약 보기 → 접근성 설정
"""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from core.config import settings
from database import init_db
from routers import (
    appointments, auth, client_app, clients, dashboard, pipeline, sessions,
)

@asynccontextmanager
async def lifespan(_: FastAPI):
    """기동 시 테이블 생성 및 외부 연동 상태 점검."""
    try:
        init_db()
        print(f"[INIT] DB 연결 OK ({settings.database_url.split('@')[-1]})")
    except Exception as exc:
        # DB가 아직 안 떠 있어도 서버는 기동시키고 로그만 남긴다.
        print(f"[INIT] DB 초기화 실패: {exc}")

    if not settings.clova_enabled:
        print("[INIT] CLOVA 키 없음 - STT는 목 데이터로 동작합니다.")
    if not settings.openai_enabled:
        print("[INIT] OpenAI 키 없음 - 요약/이미지는 대체 로직으로 동작합니다.")
    if settings.AUTO_ISSUE_CLIENT_PIN:
        print(
            "[INIT] ⚠ AUTO_ISSUE_CLIENT_PIN=True - PIN 없이 등록된 내담자에게 "
            f"기본 PIN('{settings.DEFAULT_CLIENT_PIN}')이 자동 발급됩니다. "
            "실제 환자 데이터를 다루기 전에 .env에서 False로 변경하세요."
        )
    yield


app = FastAPI(
    title=settings.APP_NAME,
    description=__doc__,
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

# ── CORS ───────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 라우터 ─────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(clients.router)
app.include_router(appointments.router)
app.include_router(sessions.router)
app.include_router(client_app.router)
if settings.LEGACY_PIPELINE_ENABLED:
    app.include_router(pipeline.router)


# ── 업로드 파일 정적 서빙 ──────────────────────────────────────
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
app.mount(
    settings.MEDIA_URL_PREFIX,
    StaticFiles(directory=settings.UPLOAD_DIR),
    name="media",
)


# ── 공통 예외 핸들러 ───────────────────────────────────────────
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    print(f"[ERROR] {request.method} {request.url.path} - {exc!r}")
    return JSONResponse(
        status_code=500,
        content={"detail": "서버 내부 오류가 발생했습니다.", "path": request.url.path},
    )


# ── 헬스체크 ───────────────────────────────────────────────────
@app.get("/", tags=["health"])
def root():
    return {
        "status": "ok",
        "message": "서버 정상 동작 중",
        "version": settings.APP_VERSION,
        "docs": "/docs",
    }


@app.get("/health", tags=["health"])
def health():
    from sqlalchemy import text

    from database import engine
    from services import morph_service

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception as exc:
        print(f"[HEALTH] DB 오류: {exc}")
        db_ok = False

    return {
        "status": "ok" if db_ok else "degraded",
        "database": db_ok,
        "stt": "clova" if settings.clova_enabled else "mock",
        "image": settings.OPENAI_IMAGE_MODEL if settings.openai_enabled else "mock",
        "morph_analysis": morph_service.is_available(),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
