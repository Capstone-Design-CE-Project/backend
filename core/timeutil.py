"""시간대 처리 유틸.

규칙: **DB에는 항상 서비스 기준 시간대(기본 Asia/Seoul)의 naive datetime을 저장한다.**

이유
  - 운영 서버(EC2)의 OS/DB 시간대가 UTC일 수 있어 func.now()/datetime.now()를
    그대로 쓰면 저장 값이 환경마다 달라진다.
  - Flutter는 scheduled_at을 `toUtc().toIso8601String()`('...Z')로 보내는데,
    응답은 `DateTime.tryParse()`로 읽는다. Dart는 오프셋이 없는 문자열을
    '로컬'로 해석하므로, 저장/응답을 한국 시간 기준으로 통일하면
    앱이 고른 시각과 화면에 보이는 시각이 일치한다.

따라서 들어오는 값은 aware/naive를 모두 받아 기준 시간대로 변환해 저장하고,
내부 비교는 datetime.now() 대신 now_local()을 쓴다.
"""
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from core.config import settings

# 한국은 서머타임이 없어 고정 오프셋으로도 정확하다.
# tzdata가 없는 최소 컨테이너에서도 동작하도록 폴백을 둔다.
_FALLBACK_TZ = timezone(timedelta(minutes=settings.APP_TZ_FALLBACK_OFFSET_MINUTES))


def _resolve_tz():
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(settings.APP_TIMEZONE)
    except Exception as exc:  # tzdata 미설치 등
        print(f"[TZ] '{settings.APP_TIMEZONE}' 로드 실패, 고정 오프셋 사용: {exc}")
        return _FALLBACK_TZ


APP_TZ = _resolve_tz()


def now_local() -> datetime:
    """기준 시간대의 현재 시각 (naive). DB 저장/비교에 사용."""
    return datetime.now(APP_TZ).replace(tzinfo=None)


def today_local() -> date:
    return now_local().date()


def to_local_naive(value: Optional[datetime]) -> Optional[datetime]:
    """입력 datetime을 기준 시간대의 naive로 정규화.

    - aware('...Z', '+09:00' 등)  → 기준 시간대로 변환 후 tzinfo 제거
    - naive                       → 이미 기준 시간대라고 보고 그대로 사용
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(APP_TZ).replace(tzinfo=None)


def start_of_day(value: Optional[datetime] = None) -> datetime:
    base = value or now_local()
    return datetime(base.year, base.month, base.day)
