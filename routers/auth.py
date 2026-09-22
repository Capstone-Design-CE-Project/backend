"""인증 - splash / counselor-login / client-login 화면."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from core.config import settings
from core.deps import bearer_scheme, get_current_counselor
from core.security import (
    create_access_token, decode_access_token, hash_password, verify_password,
)
from database import get_db
from models import Counselor, User
from schemas import (
    ChangePasswordRequest, ClientLoginRequest, CounselorCreate,
    CounselorLoginRequest, CounselorOut, MessageResponse, TokenResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


# ── 상담사 로그인 ─────────────────────────────────────────────
@router.post("/counselor/login", response_model=TokenResponse)
def counselor_login(payload: CounselorLoginRequest, db: DBSession = Depends(get_db)):
    """counselor-login 화면: 상담사 아이디 + 비밀번호."""
    counselor = (
        db.query(Counselor).filter(Counselor.login_id == payload.login_id).first()
    )
    if not counselor or not verify_password(payload.password, counselor.password_hash):
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다.")
    if not counselor.is_active:
        raise HTTPException(status_code=403, detail="비활성화된 계정입니다.")

    token = create_access_token(counselor.id, "counselor")
    return TokenResponse(
        access_token=token,
        role="counselor",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user_id=counselor.id,
        name=counselor.name,
    )


# ── 어르신 로그인 (ID + PIN) ──────────────────────────────────
@router.post("/client/login", response_model=TokenResponse)
def client_login(payload: ClientLoginRequest, db: DBSession = Depends(get_db)):
    """client-login 화면: 'ID로 접속하기' + 비밀번호(PIN) 입력."""
    client = db.query(User).filter(User.login_id == payload.login_id).first()
    if not client or not verify_password(payload.pin, client.pin_hash):
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호(PIN)가 올바르지 않습니다.")
    if not client.is_active:
        raise HTTPException(status_code=403, detail="비활성화된 계정입니다.")

    token = create_access_token(client.id, "client")
    return TokenResponse(
        access_token=token,
        role="client",
        expires_in=settings.CLIENT_TOKEN_EXPIRE_MINUTES * 60,
        user_id=client.id,
        name=client.name,
    )


# ── 내 정보 ───────────────────────────────────────────────────
@router.get("/me")
def me(cred=Depends(bearer_scheme), db: DBSession = Depends(get_db)):
    """토큰 주체(상담사/어르신) 공통 조회."""
    if cred is None:
        raise HTTPException(status_code=401, detail="인증이 필요합니다.")
    payload = decode_access_token(cred.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="토큰이 유효하지 않습니다.")

    role, uid = payload.get("role"), payload.get("uid")
    if role == "counselor":
        row = db.query(Counselor).filter(Counselor.id == uid).first()
        if not row:
            raise HTTPException(status_code=404, detail="계정을 찾을 수 없습니다.")
        return {
            "role": "counselor",
            "id": row.id,
            "login_id": row.login_id,
            "name": row.name,
            "email": row.email,
            "profile_image_url": row.profile_image_url,
        }

    row = db.query(User).filter(User.id == uid).first()
    if not row:
        raise HTTPException(status_code=404, detail="계정을 찾을 수 없습니다.")
    return {
        "role": "client",
        "id": row.id,
        "login_id": row.login_id,
        "name": row.name,
        "age": row.age,
        "member_code": row.member_code,
        "profile_image_url": row.profile_image_url,
    }


# ── 상담사 계정 생성 ──────────────────────────────────────────
@router.post("/counselor/register", response_model=CounselorOut, status_code=201)
def register_counselor(
    payload: CounselorCreate,
    cred=Depends(bearer_scheme),
    db: DBSession = Depends(get_db),
):
    """상담사 계정 생성.

    첫 계정(부트스트랩)은 토큰 없이 만들 수 있고,
    그 이후부터는 기존 상담사 토큰이 있어야 한다.
    """
    exists_any = db.query(Counselor).first() is not None
    if exists_any:
        payload_token = decode_access_token(cred.credentials) if cred else None
        if not payload_token or payload_token.get("role") != "counselor":
            raise HTTPException(
                status_code=403, detail="상담사 계정 생성은 기존 상담사만 가능합니다."
            )

    if db.query(Counselor).filter(Counselor.login_id == payload.login_id).first():
        raise HTTPException(status_code=409, detail="이미 사용 중인 아이디입니다.")

    counselor = Counselor(
        login_id=payload.login_id,
        password_hash=hash_password(payload.password),
        name=payload.name,
        email=payload.email,
        phone=payload.phone,
    )
    db.add(counselor)
    db.commit()
    db.refresh(counselor)
    return counselor


@router.post("/counselor/password", response_model=MessageResponse)
def change_password(
    payload: ChangePasswordRequest,
    counselor: Counselor = Depends(get_current_counselor),
    db: DBSession = Depends(get_db),
):
    if not verify_password(payload.current_password, counselor.password_hash):
        raise HTTPException(status_code=400, detail="현재 비밀번호가 올바르지 않습니다.")
    counselor.password_hash = hash_password(payload.new_password)
    db.commit()
    return MessageResponse(message="비밀번호가 변경되었습니다.")
