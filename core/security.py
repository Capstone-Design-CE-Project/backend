"""비밀번호 해싱 · JWT 토큰 발급/검증."""
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Literal, Optional

import bcrypt
from jose import JWTError, jwt

from core.config import settings

Role = Literal["counselor", "client"]

# bcrypt는 72바이트를 초과하는 입력을 거부하므로 미리 잘라서 넘긴다.
_BCRYPT_MAX_BYTES = 72


def _encode(password: str) -> bytes:
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_encode(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: Optional[str]) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(_encode(password), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(
    subject_id: int,
    role: Role,
    expires_minutes: Optional[int] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    """role(counselor/client)과 PK를 담은 액세스 토큰 발급."""
    if expires_minutes is None:
        expires_minutes = (
            settings.ACCESS_TOKEN_EXPIRE_MINUTES
            if role == "counselor"
            else settings.CLIENT_TOKEN_EXPIRE_MINUTES
        )
    now = datetime.now(timezone.utc)
    payload: Dict[str, Any] = {
        "sub": f"{role}:{subject_id}",
        "role": role,
        "uid": subject_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=expires_minutes)).timestamp()),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[Dict[str, Any]]:
    """유효하지 않으면 None."""
    try:
        return jwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
    except JWTError:
        return None


def generate_code(prefix: str, length: int = 4) -> str:
    """#MEM-7290, #CC-8821-XP 같은 표시용 코드 생성."""
    digits = "".join(secrets.choice("0123456789") for _ in range(length))
    return f"{prefix}-{digits}"
