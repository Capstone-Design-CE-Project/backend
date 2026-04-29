from fastapi import FastAPI
from database import engine, Base
from routers import pipeline

# ── 앱 생성 ────────────────────────────────────────────────────
app = FastAPI(
    title="치매 어르신 상담 이미지 생성 API",
    description="STT → 형태소 분석 → DALL-E 이미지 생성",
    version="1.0.0"
)

# ── 라우터 등록 ────────────────────────────────────────────────
app.include_router(pipeline.router)

# ── 헬스체크 ───────────────────────────────────────────────────
@app.get("/")
def root():
    return {"status": "ok", "message": "서버 정상 동작 중"}


# ── 실행 ───────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
