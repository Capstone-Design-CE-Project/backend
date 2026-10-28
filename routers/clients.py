"""내담자 관리 - client-list / client-detail / new-client-form 화면."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session as DBSession

from core.config import settings
from core.deps import get_current_counselor, get_owned_client
from core.phone import normalize_phone
from core.security import generate_code, hash_password
from database import get_db
from models import Counselor, CounselSession, User
from schemas import (
    ClientCreate, ClientDetail, ClientListResponse, ClientUpdate,
    MessageResponse, SessionListResponse,
)
from services import serializers

router = APIRouter(prefix="/clients", tags=["clients"])


def _check_phone_available(db: DBSession, phone: Optional[str], exclude_id=None) -> Optional[str]:
    """전화번호 중복 검사 후 정규화된 값을 반환.

    어르신 앱이 전화번호로 로그인하므로 중복이 있으면 로그인이 409로 막힌다.
    등록 시점에 걸러 그런 상태가 생기지 않게 한다.
    """
    if not phone:
        return None

    digits = normalize_phone(phone)
    if not digits:
        raise HTTPException(status_code=400, detail="전화번호 형식이 올바르지 않습니다.")

    taken = db.query(User).filter(User.phone_digits == digits)
    if exclude_id is not None:
        taken = taken.filter(User.id != exclude_id)
    if taken.first():
        raise HTTPException(status_code=409, detail="이미 등록된 전화번호입니다.")
    if db.query(Counselor).filter(Counselor.phone_digits == digits).first():
        raise HTTPException(status_code=409, detail="상담사 계정이 쓰는 전화번호입니다.")

    return digits


def _unique_member_code(db: DBSession) -> str:
    for _ in range(10):
        code = generate_code("#MEM")
        if not db.query(User).filter(User.member_code == code).first():
            return code
    raise HTTPException(status_code=500, detail="고유 번호 생성에 실패했습니다.")


# ── 목록 (검색 + 페이징) ──────────────────────────────────────
@router.get("", response_model=ClientListResponse)
def list_clients(
    q: Optional[str] = Query(None, description="이름 또는 고유 번호 검색"),
    include_inactive: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    counselor: Counselor = Depends(get_current_counselor),
    db: DBSession = Depends(get_db),
):
    """면담자 관리 화면의 리스트. '총 12명의 관리 환자' 카운트 포함."""
    query = db.query(User).filter(
        or_(User.counselor_id == counselor.id, User.counselor_id.is_(None))
    )
    if not include_inactive:
        query = query.filter(User.is_active.is_(True))
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(User.name.like(like), User.member_code.like(like)))

    total = query.count()
    rows = (
        query.order_by(User.last_visit_at.desc().nullslast(), User.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return ClientListResponse(total=total, items=rows)


# ── 등록 ──────────────────────────────────────────────────────
@router.post("", response_model=ClientDetail, status_code=201)
def create_client(
    payload: ClientCreate,
    counselor: Counselor = Depends(get_current_counselor),
    db: DBSession = Depends(get_db),
):
    """new-client-form 화면의 '저장하기'."""
    pin = payload.pin

    # 프론트가 PIN을 보내지 않는 동안 어르신 로그인이 막히지 않도록
    # 기본 PIN을 자동 발급한다 (AUTO_ISSUE_CLIENT_PIN=False 로 끌 수 있음).
    if not pin and settings.AUTO_ISSUE_CLIENT_PIN:
        pin = settings.DEFAULT_CLIENT_PIN
        print(
            f"[CLIENT] PIN 미지정 - 기본 PIN 자동 발급 (name={payload.name}, "
            f"phone={payload.phone}). 운영 전 AUTO_ISSUE_CLIENT_PIN=False 로 변경할 것."
        )

    if payload.login_id:
        if db.query(User).filter(User.login_id == payload.login_id).first():
            raise HTTPException(status_code=409, detail="이미 사용 중인 아이디입니다.")
        if not pin:
            raise HTTPException(status_code=400, detail="앱 로그인 ID를 지정하면 PIN도 필요합니다.")

    client = User(
        counselor_id=counselor.id,
        name=payload.name.strip(),
        age=payload.age,
        gender=payload.gender,
        phone=payload.phone,
        phone_digits=_check_phone_available(db, payload.phone),
        memo=payload.memo,
        cognitive_stage=payload.cognitive_stage,
        member_code=_unique_member_code(db),
        login_id=payload.login_id,
        pin_hash=hash_password(pin) if pin else None,
        profile_image_url=payload.profile_image_url,
    )
    db.add(client)
    db.commit()
    db.refresh(client)
    return serializers.client_detail(db, client)


# ── 상세 ──────────────────────────────────────────────────────
@router.get("/{client_id}", response_model=ClientDetail)
def get_client(
    client_id: int,
    counselor: Counselor = Depends(get_current_counselor),
    db: DBSession = Depends(get_db),
):
    """client-detail 화면: 기본 정보 + 다음 예정 상담 + 세션 수."""
    client = get_owned_client(client_id, db, counselor)
    return serializers.client_detail(db, client)


# ── 수정 ──────────────────────────────────────────────────────
@router.patch("/{client_id}", response_model=ClientDetail)
def update_client(
    client_id: int,
    payload: ClientUpdate,
    counselor: Counselor = Depends(get_current_counselor),
    db: DBSession = Depends(get_db),
):
    client = get_owned_client(client_id, db, counselor)
    data = payload.model_dump(exclude_unset=True)

    pin = data.pop("pin", None)
    if pin:
        client.pin_hash = hash_password(pin)

    login_id = data.get("login_id")
    if login_id and login_id != client.login_id:
        if db.query(User).filter(User.login_id == login_id).first():
            raise HTTPException(status_code=409, detail="이미 사용 중인 아이디입니다.")

    for key, value in data.items():
        setattr(client, key, value)

    if "phone" in data:
        client.phone_digits = _check_phone_available(
            db, data["phone"], exclude_id=client.id
        )

    db.commit()
    db.refresh(client)
    return serializers.client_detail(db, client)


# ── 비활성화(소프트 삭제) ─────────────────────────────────────
@router.delete("/{client_id}", response_model=MessageResponse)
def deactivate_client(
    client_id: int,
    hard: bool = Query(False, description="true면 상담 기록까지 완전 삭제"),
    counselor: Counselor = Depends(get_current_counselor),
    db: DBSession = Depends(get_db),
):
    """기본은 비활성화. 상담 기록 보존이 원칙이라 완전 삭제는 옵션."""
    client = get_owned_client(client_id, db, counselor)
    if hard:
        db.delete(client)
        db.commit()
        return MessageResponse(message="내담자와 모든 상담 기록이 삭제되었습니다.")

    client.is_active = False
    db.commit()
    return MessageResponse(message="내담자가 비활성화되었습니다.")


# ── 내담자의 상담 세션 목록 ───────────────────────────────────
@router.get("/{client_id}/sessions", response_model=SessionListResponse)
def list_client_sessions(
    client_id: int,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    counselor: Counselor = Depends(get_current_counselor),
    db: DBSession = Depends(get_db),
):
    """client-detail의 '세션 기록 보기'."""
    get_owned_client(client_id, db, counselor)
    query = db.query(CounselSession).filter(CounselSession.user_id == client_id)
    total = query.count()
    rows = (
        query.order_by(CounselSession.created_at.desc(), CounselSession.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return SessionListResponse(
        total=total, items=[serializers.session_brief(s) for s in rows]
    )
