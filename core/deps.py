"""FastAPI 의존성: 인증 주체 확인 및 권한 체크."""
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session as DBSession

from core.security import decode_access_token
from database import get_db
from models import Counselor, CounselSession, User

bearer_scheme = HTTPBearer(auto_error=False)

_CRED_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="인증 정보가 올바르지 않습니다.",
    headers={"WWW-Authenticate": "Bearer"},
)


def _payload(cred: Optional[HTTPAuthorizationCredentials]) -> dict:
    if cred is None or not cred.credentials:
        raise _CRED_ERROR
    payload = decode_access_token(cred.credentials)
    if payload is None:
        raise _CRED_ERROR
    return payload


def get_current_counselor(
    cred: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: DBSession = Depends(get_db),
) -> Counselor:
    """상담사 전용 엔드포인트용."""
    payload = _payload(cred)
    if payload.get("role") != "counselor":
        raise HTTPException(status_code=403, detail="상담사 권한이 필요합니다.")

    counselor = db.query(Counselor).filter(Counselor.id == payload.get("uid")).first()
    if not counselor or not counselor.is_active:
        raise _CRED_ERROR
    return counselor


def get_current_client(
    cred: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: DBSession = Depends(get_db),
) -> User:
    """어르신(내담자) 앱 전용 엔드포인트용."""
    payload = _payload(cred)
    if payload.get("role") != "client":
        raise HTTPException(status_code=403, detail="내담자 권한이 필요합니다.")

    client = db.query(User).filter(User.id == payload.get("uid")).first()
    if not client or not client.is_active:
        raise _CRED_ERROR
    return client


def get_owned_client(
    client_id: int,
    db: DBSession,
    counselor: Counselor,
) -> User:
    """상담사가 담당하는 내담자인지 확인 후 반환."""
    client = db.query(User).filter(User.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="내담자를 찾을 수 없습니다.")
    if client.counselor_id is not None and client.counselor_id != counselor.id:
        raise HTTPException(status_code=403, detail="담당 내담자가 아닙니다.")
    return client


def get_owned_session(
    session_id: int,
    db: DBSession,
    counselor: Counselor,
) -> CounselSession:
    """상담사가 접근 가능한 세션인지 확인 후 반환."""
    sess = db.query(CounselSession).filter(CounselSession.id == session_id).first()
    if not sess:
        raise HTTPException(status_code=404, detail="상담 세션을 찾을 수 없습니다.")
    if sess.counselor_id is not None and sess.counselor_id != counselor.id:
        raise HTTPException(status_code=403, detail="담당 상담 세션이 아닙니다.")
    return sess
