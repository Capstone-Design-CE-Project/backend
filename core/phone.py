"""전화번호 정규화.

어르신 앱(client_login.dart)은 단말에서 읽은 전화번호를 그대로 login_id로 보낸다.
단말/통신사에 따라 '+82 10-1234-5678', '010-1234-5678', '01012345678' 등
표기가 제각각이라, 저장·조회 모두 숫자만 남긴 정규화 형태로 맞춘다.

    '+82 10-1234-5678' → '01012345678'
    '010-1234-5678'    → '01012345678'
    '0082-10-12345678' → '01012345678'
    '1012345678'       → '01012345678'
"""
import re
from typing import Optional

_NON_DIGIT = re.compile(r"\D")


def normalize_phone(value: Optional[str]) -> Optional[str]:
    """전화번호를 숫자만 남긴 국내 표기로 변환. 유효하지 않으면 None."""
    if not value:
        return None

    digits = _NON_DIGIT.sub("", value)
    if not digits:
        return None

    # 국제전화 접두사 제거: 0082..., +82...
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("82"):
        digits = "0" + digits[2:]

    # 선행 0이 빠진 채로 들어오는 경우 보정 (예: 1012345678)
    if not digits.startswith("0") and 9 <= len(digits) <= 10:
        digits = "0" + digits

    # 국내 번호 길이 범위를 벗어나면 신뢰하지 않는다 (지역번호 9자리 ~ 휴대폰 11자리)
    if not (9 <= len(digits) <= 11):
        return None

    return digits
