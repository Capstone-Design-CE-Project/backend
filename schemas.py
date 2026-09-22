"""요청/응답 스키마 (Pydantic v2).

프론트(Flutter) 파싱 편의를 위해
 - 날짜/시간은 ISO8601 문자열로 직렬화
 - 리스트 응답은 {"items": [...], "total": n} 형태로 통일
   (단 /pipeline/album/{id}은 기존 Dart 코드 호환을 위해 배열 그대로 반환)
"""
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

ORM = ConfigDict(from_attributes=True)


# ══════════════════════════════════════════════════════════════
# 인증
# ══════════════════════════════════════════════════════════════
class CounselorLoginRequest(BaseModel):
    login_id: str = Field(..., examples=["counselor_lee"])
    password: str = Field(..., min_length=4)


class ClientLoginRequest(BaseModel):
    """어르신 앱: ID + 4~6자리 PIN."""
    login_id: str = Field(..., examples=["kim_young_hee"])
    pin: str = Field(..., min_length=4, max_length=6)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: Literal["counselor", "client"]
    expires_in: int          # 초
    user_id: int
    name: str


class CounselorCreate(BaseModel):
    login_id: str = Field(..., max_length=50)
    password: str = Field(..., min_length=8)
    name: str = Field(..., max_length=50)
    email: Optional[str] = None
    phone: Optional[str] = Field(None, max_length=20)


class CounselorOut(BaseModel):
    model_config = ORM
    id: int
    login_id: str
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    profile_image_url: Optional[str] = None
    created_at: Optional[datetime] = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8)


# ══════════════════════════════════════════════════════════════
# 내담자
# ══════════════════════════════════════════════════════════════
class ClientCreate(BaseModel):
    """new-client-form 화면."""
    name: str = Field(..., max_length=50)
    age: Optional[int] = Field(None, ge=0, le=130)
    gender: Optional[Literal["M", "F"]] = None
    phone: Optional[str] = Field(None, max_length=20)
    memo: Optional[str] = None
    cognitive_stage: Optional[str] = Field(None, max_length=50)
    login_id: Optional[str] = Field(None, max_length=50)
    pin: Optional[str] = Field(None, min_length=4, max_length=6)
    profile_image_url: Optional[str] = None


class ClientUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=50)
    age: Optional[int] = Field(None, ge=0, le=130)
    gender: Optional[Literal["M", "F"]] = None
    phone: Optional[str] = Field(None, max_length=20)
    memo: Optional[str] = None
    cognitive_stage: Optional[str] = Field(None, max_length=50)
    login_id: Optional[str] = Field(None, max_length=50)
    pin: Optional[str] = Field(None, min_length=4, max_length=6)
    profile_image_url: Optional[str] = None
    is_active: Optional[bool] = None


class ClientBrief(BaseModel):
    """client-list 카드 한 줄."""
    model_config = ORM
    id: int
    name: str
    age: Optional[int] = None
    gender: Optional[str] = None
    cognitive_stage: Optional[str] = None
    member_code: Optional[str] = None
    profile_image_url: Optional[str] = None
    last_visit_at: Optional[datetime] = None


class ClientDetail(ClientBrief):
    """client-detail 화면."""
    model_config = ORM
    phone: Optional[str] = None
    memo: Optional[str] = None
    login_id: Optional[str] = None
    has_pin: bool = False
    is_active: bool = True
    created_at: Optional[datetime] = None
    session_count: int = 0
    next_appointment: Optional["AppointmentOut"] = None


class ClientListResponse(BaseModel):
    total: int
    items: List[ClientBrief]


# ══════════════════════════════════════════════════════════════
# 상담 일정
# ══════════════════════════════════════════════════════════════
class AppointmentCreate(BaseModel):
    client_id: int
    scheduled_at: datetime
    duration_minutes: int = Field(60, ge=5, le=480)
    purpose: Optional[str] = Field(None, max_length=120)
    place: Optional[str] = Field(None, max_length=120)
    memo: Optional[str] = None


class AppointmentUpdate(BaseModel):
    scheduled_at: Optional[datetime] = None
    duration_minutes: Optional[int] = Field(None, ge=5, le=480)
    purpose: Optional[str] = Field(None, max_length=120)
    place: Optional[str] = Field(None, max_length=120)
    memo: Optional[str] = None
    status: Optional[Literal["scheduled", "done", "canceled"]] = None


class AppointmentOut(BaseModel):
    model_config = ORM
    id: int
    client_id: int
    client_name: Optional[str] = None
    scheduled_at: datetime
    duration_minutes: int
    purpose: Optional[str] = None
    place: Optional[str] = None
    memo: Optional[str] = None
    status: str


class CalendarDay(BaseModel):
    """calendar 화면: 날짜별 점(dot) 표시용."""
    date: str                 # 'YYYY-MM-DD'
    count: int
    appointments: List[AppointmentOut]


class CalendarMonthResponse(BaseModel):
    year: int
    month: int
    total: int
    days: List[CalendarDay]


# ══════════════════════════════════════════════════════════════
# 상담 세션
# ══════════════════════════════════════════════════════════════
class SessionCreate(BaseModel):
    """session-start-popup의 '네, 시작합니다'."""
    client_id: int
    title: Optional[str] = Field(None, max_length=120)
    appointment_id: Optional[int] = None


class QaLogOut(BaseModel):
    model_config = ORM
    id: int
    type: str                 # 'Q' | 'A'
    speaker: str              # '상담사' | '내담자'
    text: str
    order_index: int
    spoken_at_ms: Optional[int] = None


class AlbumOut(BaseModel):
    """Dart AlbumImage와 필드명을 맞춘다."""
    model_config = ORM
    id: int
    album_id: int             # 기존 응답 호환 필드
    session_id: int
    title: Optional[str] = None
    image_url: str
    prompt: Optional[str] = None
    scene: Optional[str] = None
    is_selected: bool = False
    created_at: Optional[datetime] = None


class SessionBrief(BaseModel):
    model_config = ORM
    id: int
    session_id: int
    user_id: int
    client_name: Optional[str] = None
    title: Optional[str] = None
    status: str
    duration_seconds: int = 0
    reference_code: Optional[str] = None
    thumbnail_url: Optional[str] = None
    created_at: Optional[datetime] = None


class SessionDetail(SessionBrief):
    """session-detail 화면 (요약 탭 + 기록 탭)."""
    counselor_id: Optional[int] = None
    summary: Optional[str] = None
    keywords: List[str] = []
    transcript: Optional[str] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    qa_logs: List[QaLogOut] = []
    albums: List[AlbumOut] = []


class SessionListResponse(BaseModel):
    total: int
    items: List[SessionBrief]


class TranscriptResponse(BaseModel):
    """text-confirm-popup: 실시간 텍스트 확인."""
    session_id: int
    status: str
    transcript: Optional[str] = None
    lines: List[QaLogOut] = []


class SummaryResponse(BaseModel):
    """summary-review 화면."""
    session_id: int
    title: Optional[str] = None
    summary: str
    keywords: List[str] = []
    generated_by: str          # 'openai' | 'rule-based'


class SummaryUpdate(BaseModel):
    summary: Optional[str] = None
    keywords: Optional[List[str]] = None
    title: Optional[str] = Field(None, max_length=120)


class ImageGenerateRequest(BaseModel):
    """image-review의 '다시 생성' 시 추가 지시문 전달용."""
    extra_prompt: Optional[str] = None
    replace_previous: bool = False


class SessionCompleteRequest(BaseModel):
    album_id: Optional[int] = None    # '이대로 저장'으로 고른 이미지
    summary: Optional[str] = None
    title: Optional[str] = Field(None, max_length=120)


class SessionCompleteResponse(BaseModel):
    session_id: int
    status: str
    reference_code: str
    recorded_at: Optional[datetime] = None
    selected_album: Optional[AlbumOut] = None


class RecordingUploadResponse(BaseModel):
    session_id: int
    status: str
    transcript: str
    lines: List[QaLogOut] = []
    duration_seconds: int = 0
    stt_engine: str            # 'clova' | 'mock'
    morph_saved: int = 0       # 저장된 명사 수


# ══════════════════════════════════════════════════════════════
# 어르신 앱 (홈/앨범/설정)
# ══════════════════════════════════════════════════════════════
class ClientHomeResponse(BaseModel):
    """client-home 화면."""
    client_id: int
    name: str
    featured: Optional[AlbumOut] = None        # '기억 속 쉼터' 대표 이미지
    featured_caption: Optional[str] = None
    next_appointment: Optional[AppointmentOut] = None
    daily_tip: str                             # '오늘의 마음 다스림'
    album_count: int = 0


class AlbumCardOut(BaseModel):
    """client-album 그리드 카드."""
    id: int
    session_id: int
    title: str
    image_url: str
    created_at: Optional[datetime] = None
    date_label: str            # '4월 16일'


class AlbumListResponse(BaseModel):
    total: int
    items: List[AlbumCardOut]


class ClientSummaryResponse(BaseModel):
    """client-summary-detail: 어르신에게 보여줄 요약."""
    session_id: int
    title: Optional[str] = None
    summary: Optional[str] = None
    image_url: Optional[str] = None
    session_date: Optional[datetime] = None
    date_label: str


class SettingOut(BaseModel):
    model_config = ORM
    user_id: int
    font_scale: str
    high_contrast: bool
    haptic_feedback: bool
    show_help_tips: bool


class SettingUpdate(BaseModel):
    font_scale: Optional[Literal["medium", "large", "xlarge"]] = None
    high_contrast: Optional[bool] = None
    haptic_feedback: Optional[bool] = None
    show_help_tips: Optional[bool] = None


# ══════════════════════════════════════════════════════════════
# 대시보드 / 알림
# ══════════════════════════════════════════════════════════════
class DashboardCard(BaseModel):
    key: str                  # 'clients' | 'schedule' | 'reports' | 'notifications'
    label: str                # '환자 명단'
    value: str                # '관리 환자: 24명'


class DashboardResponse(BaseModel):
    """dashboard 화면."""
    counselor_name: str
    greeting: str
    today_appointment_count: int
    client_count: int
    next_appointment: Optional[AppointmentOut] = None
    completed_report_count: int
    unread_notification_count: int
    cards: List[DashboardCard] = []


class NotificationOut(BaseModel):
    model_config = ORM
    id: int
    type: str
    title: str
    body: Optional[str] = None
    link_session_id: Optional[int] = None
    is_read: bool
    created_at: Optional[datetime] = None


class NotificationListResponse(BaseModel):
    total: int
    unread: int
    items: List[NotificationOut]


class MessageResponse(BaseModel):
    message: str


ClientDetail.model_rebuild()
