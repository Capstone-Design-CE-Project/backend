"""ORM 객체 → 응답 스키마 변환 헬퍼."""
from datetime import datetime
from typing import List, Optional

from sqlalchemy.orm import Session as DBSession

from models import Album, Appointment, CounselSession, QaLog, User
from schemas import (
    AlbumCardOut, AlbumOut, AppointmentOut, ClientDetail, QaLogOut,
    SessionBrief, SessionDetail,
)

SPEAKER_NAMES = {"Q": "상담사", "A": "내담자"}


def date_label(value: Optional[datetime]) -> str:
    """'4월 16일' 형태의 한국어 날짜 라벨."""
    if not value:
        return ""
    return f"{value.month}월 {value.day}일"


def qa_out(row: QaLog) -> QaLogOut:
    return QaLogOut(
        id=row.id,
        type=row.type,
        speaker=SPEAKER_NAMES.get(row.type, "내담자"),
        text=row.text,
        order_index=row.order_index,
        spoken_at_ms=row.spoken_at_ms,
    )


def album_out(row: Album) -> AlbumOut:
    return AlbumOut(
        id=row.id,
        album_id=row.id,
        session_id=row.session_id,
        title=row.title,
        image_url=row.image_url,
        prompt=row.prompt,
        scene=row.scene,
        is_selected=bool(row.is_selected),
        created_at=row.created_at,
    )


def album_card(row: Album, session: Optional[CounselSession] = None) -> AlbumCardOut:
    created = row.created_at or (session.created_at if session else None)
    title = row.title or (session.title if session else None) or "기억 한 조각"
    return AlbumCardOut(
        id=row.id,
        session_id=row.session_id,
        title=title,
        image_url=row.image_url,
        created_at=created,
        date_label=date_label(created),
    )


def _thumbnail(sess: CounselSession) -> Optional[str]:
    """대표 이미지: '이대로 저장'으로 선택된 것 우선."""
    selected = [a for a in sess.albums if a.is_selected]
    pool = selected or list(sess.albums)
    return pool[-1].image_url if pool else None


def session_brief(sess: CounselSession) -> SessionBrief:
    return SessionBrief(
        id=sess.id,
        session_id=sess.id,
        user_id=sess.user_id,
        client_name=sess.user.name if sess.user else None,
        title=sess.title,
        status=sess.status,
        duration_seconds=sess.duration_seconds or 0,
        reference_code=sess.reference_code,
        thumbnail_url=_thumbnail(sess),
        created_at=sess.created_at,
    )


def session_detail(sess: CounselSession) -> SessionDetail:
    brief = session_brief(sess)
    return SessionDetail(
        **brief.model_dump(),
        counselor_id=sess.counselor_id,
        summary=sess.summary,
        keywords=sess.keyword_list,
        transcript=sess.transcript,
        started_at=sess.started_at,
        ended_at=sess.ended_at,
        qa_logs=[qa_out(q) for q in sess.qa_logs],
        albums=[album_out(a) for a in sess.albums],
    )


def appointment_out(row: Appointment) -> AppointmentOut:
    return AppointmentOut(
        id=row.id,
        client_id=row.client_id,
        client_name=row.client.name if row.client else None,
        scheduled_at=row.scheduled_at,
        duration_minutes=row.duration_minutes,
        purpose=row.purpose,
        place=row.place,
        memo=row.memo,
        status=row.status,
    )


def next_appointment(db: DBSession, client_id: int) -> Optional[Appointment]:
    from models import AppointmentStatus

    return (
        db.query(Appointment)
        .filter(
            Appointment.client_id == client_id,
            Appointment.status == AppointmentStatus.SCHEDULED,
            Appointment.scheduled_at >= datetime.now(),
        )
        .order_by(Appointment.scheduled_at)
        .first()
    )


def client_detail(db: DBSession, client: User) -> ClientDetail:
    session_count = (
        db.query(CounselSession).filter(CounselSession.user_id == client.id).count()
    )
    upcoming = next_appointment(db, client.id)
    return ClientDetail(
        id=client.id,
        name=client.name,
        age=client.age,
        gender=client.gender,
        cognitive_stage=client.cognitive_stage,
        member_code=client.member_code,
        profile_image_url=client.profile_image_url,
        last_visit_at=client.last_visit_at,
        phone=client.phone,
        memo=client.memo,
        login_id=client.login_id,
        has_pin=bool(client.pin_hash),
        is_active=bool(client.is_active),
        created_at=client.created_at,
        session_count=session_count,
        next_appointment=appointment_out(upcoming) if upcoming else None,
    )


def albums_of_client(db: DBSession, client_id: int) -> List[Album]:
    """내담자의 모든 회상 이미지(최신순)."""
    return (
        db.query(Album)
        .join(CounselSession, Album.session_id == CounselSession.id)
        .filter(CounselSession.user_id == client_id)
        .order_by(Album.created_at.desc(), Album.id.desc())
        .all()
    )
