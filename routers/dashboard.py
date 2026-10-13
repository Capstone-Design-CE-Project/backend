"""상담사 대시보드 + 알림 - dashboard 화면."""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session as DBSession

from core.deps import get_current_counselor
from core.timeutil import now_local
from database import get_db
from models import (
    Appointment, AppointmentStatus, Counselor, CounselSession, Notification,
    SessionStatus, User,
)
from schemas import (
    DashboardCard, DashboardResponse, MessageResponse, NotificationListResponse,
)
from services import serializers

router = APIRouter(tags=["dashboard"])


def _greeting(now: datetime) -> str:
    if now.hour < 11:
        return "좋은 아침입니다."
    if now.hour < 17:
        return "좋은 하루입니다."
    return "오늘도 고생 많으셨습니다."


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard(
    counselor: Counselor = Depends(get_current_counselor),
    db: DBSession = Depends(get_db),
):
    """'오늘의 업무 현황' 카드 4개를 한 번에 내려준다."""
    now = now_local()
    today_start = datetime(now.year, now.month, now.day)
    today_end = today_start + timedelta(days=1)

    client_count = (
        db.query(User)
        .filter(
            or_(User.counselor_id == counselor.id, User.counselor_id.is_(None)),
            User.is_active.is_(True),
        )
        .count()
    )

    today_appointments = (
        db.query(Appointment)
        .filter(
            Appointment.counselor_id == counselor.id,
            Appointment.scheduled_at >= today_start,
            Appointment.scheduled_at < today_end,
            Appointment.status != AppointmentStatus.CANCELED,
        )
        .order_by(Appointment.scheduled_at)
        .all()
    )

    upcoming = (
        db.query(Appointment)
        .filter(
            Appointment.counselor_id == counselor.id,
            Appointment.status == AppointmentStatus.SCHEDULED,
            Appointment.scheduled_at >= now,
        )
        .order_by(Appointment.scheduled_at)
        .first()
    )

    completed_reports = (
        db.query(CounselSession)
        .filter(
            CounselSession.counselor_id == counselor.id,
            CounselSession.status == SessionStatus.COMPLETED,
        )
        .count()
    )

    unread = (
        db.query(Notification)
        .filter(
            Notification.counselor_id == counselor.id,
            Notification.is_read.is_(False),
        )
        .count()
    )

    next_label = (
        f"다음 상담: {upcoming.scheduled_at:%p %I:%M}".replace("AM", "오전").replace(
            "PM", "오후"
        )
        if upcoming
        else "예정된 상담 없음"
    )

    return DashboardResponse(
        counselor_name=counselor.name,
        greeting=f"{counselor.name} 상담사님, {_greeting(now)}",
        today_appointment_count=len(today_appointments),
        client_count=client_count,
        next_appointment=(
            serializers.appointment_out(upcoming) if upcoming else None
        ),
        completed_report_count=completed_reports,
        unread_notification_count=unread,
        cards=[
            DashboardCard(key="clients", label="환자 명단", value=f"관리 환자: {client_count}명"),
            DashboardCard(key="schedule", label="일정 관리", value=next_label),
            DashboardCard(
                key="reports", label="상담 리포트", value=f"{completed_reports}회 완료"
            ),
            DashboardCard(key="notifications", label="신규 알림", value=f"{unread}개 새 알림"),
        ],
    )


# ── 알림 ──────────────────────────────────────────────────────
@router.get("/notifications", response_model=NotificationListResponse)
def list_notifications(
    only_unread: bool = False,
    limit: int = Query(30, ge=1, le=100),
    counselor: Counselor = Depends(get_current_counselor),
    db: DBSession = Depends(get_db),
):
    query = db.query(Notification).filter(Notification.counselor_id == counselor.id)
    unread = query.filter(Notification.is_read.is_(False)).count()
    if only_unread:
        query = query.filter(Notification.is_read.is_(False))

    rows = query.order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit).all()
    return NotificationListResponse(total=len(rows), unread=unread, items=rows)


@router.post("/notifications/{notification_id}/read", response_model=MessageResponse)
def mark_read(
    notification_id: int,
    counselor: Counselor = Depends(get_current_counselor),
    db: DBSession = Depends(get_db),
):
    row = (
        db.query(Notification)
        .filter(
            Notification.id == notification_id,
            Notification.counselor_id == counselor.id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="알림을 찾을 수 없습니다.")
    row.is_read = True
    db.commit()
    return MessageResponse(message="알림을 읽음 처리했습니다.")


@router.post("/notifications/read-all", response_model=MessageResponse)
def mark_all_read(
    counselor: Counselor = Depends(get_current_counselor),
    db: DBSession = Depends(get_db),
):
    db.query(Notification).filter(
        Notification.counselor_id == counselor.id,
        Notification.is_read.is_(False),
    ).update({Notification.is_read: True}, synchronize_session=False)
    db.commit()
    return MessageResponse(message="모든 알림을 읽음 처리했습니다.")
