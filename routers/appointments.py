"""상담 일정 - calendar / dashboard / client-home의 '다음 상담 예약'."""
import calendar as _calendar
from collections import defaultdict
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session as DBSession

from core.deps import get_current_counselor, get_owned_client
from core.timeutil import now_local
from database import get_db
from models import Appointment, AppointmentStatus, Counselor
from schemas import (
    AppointmentCreate, AppointmentOut, AppointmentUpdate, CalendarDay,
    CalendarMonthResponse, MessageResponse,
)
from services import serializers

router = APIRouter(prefix="/appointments", tags=["appointments"])


def _get_owned_appointment(
    appointment_id: int, db: DBSession, counselor: Counselor
) -> Appointment:
    row = db.query(Appointment).filter(Appointment.id == appointment_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="상담 일정을 찾을 수 없습니다.")
    if row.counselor_id != counselor.id:
        raise HTTPException(status_code=403, detail="담당 일정이 아닙니다.")
    return row


# ── 목록 ──────────────────────────────────────────────────────
@router.get("", response_model=List[AppointmentOut])
def list_appointments(
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    client_id: Optional[int] = None,
    status: Optional[str] = Query(None, description="scheduled | done | canceled"),
    counselor: Counselor = Depends(get_current_counselor),
    db: DBSession = Depends(get_db),
):
    query = db.query(Appointment).filter(Appointment.counselor_id == counselor.id)
    if date_from:
        query = query.filter(Appointment.scheduled_at >= date_from)
    if date_to:
        query = query.filter(Appointment.scheduled_at <= date_to)
    if client_id:
        query = query.filter(Appointment.client_id == client_id)
    if status:
        query = query.filter(Appointment.status == status)

    rows = query.order_by(Appointment.scheduled_at).all()
    return [serializers.appointment_out(r) for r in rows]


# ── 월별 캘린더 ───────────────────────────────────────────────
@router.get("/calendar", response_model=CalendarMonthResponse)
def month_calendar(
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    counselor: Counselor = Depends(get_current_counselor),
    db: DBSession = Depends(get_db),
):
    """calendar 화면: 날짜별 일정 개수 + 상세."""
    start = datetime(year, month, 1)
    last_day = _calendar.monthrange(year, month)[1]
    end = datetime(year, month, last_day, 23, 59, 59)

    rows = (
        db.query(Appointment)
        .filter(
            Appointment.counselor_id == counselor.id,
            Appointment.scheduled_at >= start,
            Appointment.scheduled_at <= end,
            Appointment.status != AppointmentStatus.CANCELED,
        )
        .order_by(Appointment.scheduled_at)
        .all()
    )

    grouped = defaultdict(list)
    for row in rows:
        grouped[row.scheduled_at.strftime("%Y-%m-%d")].append(
            serializers.appointment_out(row)
        )

    days = [
        CalendarDay(date=day, count=len(items), appointments=items)
        for day, items in sorted(grouped.items())
    ]
    return CalendarMonthResponse(year=year, month=month, total=len(rows), days=days)


# ── 다가오는 일정 ─────────────────────────────────────────────
@router.get("/upcoming", response_model=List[AppointmentOut])
def upcoming(
    days: int = Query(7, ge=1, le=90),
    limit: int = Query(10, ge=1, le=50),
    counselor: Counselor = Depends(get_current_counselor),
    db: DBSession = Depends(get_db),
):
    """dashboard의 '다음 상담' 및 예정 면담 리스트."""
    now = now_local()
    rows = (
        db.query(Appointment)
        .filter(
            Appointment.counselor_id == counselor.id,
            Appointment.status == AppointmentStatus.SCHEDULED,
            Appointment.scheduled_at >= now,
            Appointment.scheduled_at <= now + timedelta(days=days),
        )
        .order_by(Appointment.scheduled_at)
        .limit(limit)
        .all()
    )
    return [serializers.appointment_out(r) for r in rows]


# ── 등록 ──────────────────────────────────────────────────────
@router.post("", response_model=AppointmentOut, status_code=201)
def create_appointment(
    payload: AppointmentCreate,
    counselor: Counselor = Depends(get_current_counselor),
    db: DBSession = Depends(get_db),
):
    client = get_owned_client(payload.client_id, db, counselor)

    row = Appointment(
        counselor_id=counselor.id,
        client_id=client.id,
        scheduled_at=payload.scheduled_at,
        duration_minutes=payload.duration_minutes,
        purpose=payload.purpose,
        place=payload.place,
        memo=payload.memo,
        status=AppointmentStatus.SCHEDULED,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return serializers.appointment_out(row)


# ── 수정 / 취소 ───────────────────────────────────────────────
@router.patch("/{appointment_id}", response_model=AppointmentOut)
def update_appointment(
    appointment_id: int,
    payload: AppointmentUpdate,
    counselor: Counselor = Depends(get_current_counselor),
    db: DBSession = Depends(get_db),
):
    row = _get_owned_appointment(appointment_id, db, counselor)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return serializers.appointment_out(row)


@router.delete("/{appointment_id}", response_model=MessageResponse)
def cancel_appointment(
    appointment_id: int,
    counselor: Counselor = Depends(get_current_counselor),
    db: DBSession = Depends(get_db),
):
    row = _get_owned_appointment(appointment_id, db, counselor)
    row.status = AppointmentStatus.CANCELED
    db.commit()
    return MessageResponse(message="상담 일정이 취소되었습니다.")
