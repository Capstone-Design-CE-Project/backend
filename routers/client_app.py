"""어르신(내담자) 앱 전용 API.

화면 대응
  client-home           → GET  /me/home
  client-album          → GET  /me/album
  client-summary-detail → GET  /me/sessions/{id}/summary
  client-settings       → GET/PATCH /me/settings
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session as DBSession

from core.deps import get_current_client
from database import get_db
from models import ClientSetting, CounselSession, SessionStatus, User
from schemas import (
    AlbumListResponse, ClientHomeResponse, ClientSummaryResponse, SettingOut,
    SettingUpdate,
)
from services import serializers, summary_service

router = APIRouter(prefix="/me", tags=["client-app"])


def _get_or_create_setting(db: DBSession, client: User) -> ClientSetting:
    setting = db.query(ClientSetting).filter(ClientSetting.user_id == client.id).first()
    if setting is None:
        setting = ClientSetting(user_id=client.id)
        db.add(setting)
        db.commit()
        db.refresh(setting)
    return setting


# ── 홈 ────────────────────────────────────────────────────────
@router.get("/home", response_model=ClientHomeResponse)
def client_home(
    client: User = Depends(get_current_client),
    db: DBSession = Depends(get_db),
):
    """'나의 다이어리' 홈: 대표 회상 이미지 + 다음 상담 + 오늘의 마음 다스림."""
    albums = serializers.albums_of_client(db, client.id)
    featured = albums[0] if albums else None
    upcoming = serializers.next_appointment(db, client.id)

    featured_session = None
    if featured:
        featured_session = (
            db.query(CounselSession)
            .filter(CounselSession.id == featured.session_id)
            .first()
        )

    return ClientHomeResponse(
        client_id=client.id,
        name=client.name,
        featured=serializers.album_out(featured) if featured else None,
        featured_caption=(
            featured.title
            or (featured_session.title if featured_session else None)
            or "소중히 보관된 지난 추억을 둘러보세요"
        )
        if featured
        else None,
        next_appointment=(
            serializers.appointment_out(upcoming) if upcoming else None
        ),
        daily_tip=summary_service.make_daily_tip(client.name),
        album_count=len(albums),
    )


# ── 앨범 ──────────────────────────────────────────────────────
@router.get("/album", response_model=AlbumListResponse)
def client_album(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    client: User = Depends(get_current_client),
    db: DBSession = Depends(get_db),
):
    """client-album 그리드."""
    albums = serializers.albums_of_client(db, client.id)
    page = albums[offset : offset + limit]

    sessions = {
        s.id: s
        for s in db.query(CounselSession)
        .filter(CounselSession.id.in_([a.session_id for a in page] or [0]))
        .all()
    }
    return AlbumListResponse(
        total=len(albums),
        items=[serializers.album_card(a, sessions.get(a.session_id)) for a in page],
    )


# ── 상담 요약 보기 ────────────────────────────────────────────
@router.get("/sessions/{session_id}/summary", response_model=ClientSummaryResponse)
def client_session_summary(
    session_id: int,
    client: User = Depends(get_current_client),
    db: DBSession = Depends(get_db),
):
    """client-summary-detail: '오늘의 요약'."""
    sess = (
        db.query(CounselSession)
        .filter(CounselSession.id == session_id, CounselSession.user_id == client.id)
        .first()
    )
    if not sess:
        raise HTTPException(status_code=404, detail="상담 기록을 찾을 수 없습니다.")
    if sess.status != SessionStatus.COMPLETED:
        raise HTTPException(status_code=403, detail="아직 공개되지 않은 상담 기록입니다.")

    selected = [a for a in sess.albums if a.is_selected] or list(sess.albums)
    when = sess.ended_at or sess.created_at
    return ClientSummaryResponse(
        session_id=sess.id,
        title=sess.title,
        summary=sess.summary,
        image_url=selected[-1].image_url if selected else None,
        session_date=when,
        date_label=serializers.date_label(when),
    )


@router.get("/sessions")
def client_sessions(
    limit: int = Query(20, ge=1, le=100),
    client: User = Depends(get_current_client),
    db: DBSession = Depends(get_db),
):
    """완료된 상담 기록 목록 (어르신에게는 완료본만 노출)."""
    rows = (
        db.query(CounselSession)
        .filter(
            CounselSession.user_id == client.id,
            CounselSession.status == SessionStatus.COMPLETED,
        )
        .order_by(CounselSession.created_at.desc())
        .limit(limit)
        .all()
    )
    return {
        "total": len(rows),
        "items": [
            {
                "session_id": s.id,
                "title": s.title,
                "date_label": serializers.date_label(s.ended_at or s.created_at),
                "created_at": s.created_at,
                "thumbnail_url": serializers.session_brief(s).thumbnail_url,
            }
            for s in rows
        ],
    }


# ── 접근성 설정 ───────────────────────────────────────────────
@router.get("/settings", response_model=SettingOut)
def get_settings(
    client: User = Depends(get_current_client),
    db: DBSession = Depends(get_db),
):
    """client-settings: 글자 크기 / 고대비 모드 / 터치 진동."""
    return _get_or_create_setting(db, client)


@router.patch("/settings", response_model=SettingOut)
def update_settings(
    payload: SettingUpdate,
    client: User = Depends(get_current_client),
    db: DBSession = Depends(get_db),
):
    setting = _get_or_create_setting(db, client)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(setting, key, value)
    db.commit()
    db.refresh(setting)
    return setting
