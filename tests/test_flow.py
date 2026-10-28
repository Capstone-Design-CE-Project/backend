"""디자인 화면 플로우를 그대로 따라가는 통합 테스트.

상담사: 로그인 → 내담자 등록 → 일정 등록 → 상담 시작 → 녹음 업로드
        → 요약 생성 → 이미지 생성 → 완료 저장 → 대시보드
어르신: PIN 로그인 → 홈 → 앨범 → 요약 보기 → 설정 변경
"""
import io

CLIENT_PIN = "482100"   # 앱이 6자리만 받는다


def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["database"] is True
    assert body["stt"] == "mock"


def test_login_requires_valid_credentials(client):
    res = client.post(
        "/auth/counselor/login",
        json={"login_id": "counselor_test", "password": "wrong-password"},
    )
    assert res.status_code == 401


def test_protected_endpoint_rejects_anonymous(client):
    assert client.get("/dashboard").status_code == 401
    assert client.get("/clients").status_code == 401


def test_full_counseling_flow(client, auth):
    # ── 1. 내담자 등록 (new-client-form) ──────────────────────
    res = client.post(
        "/clients",
        headers=auth,
        json={
            "name": "배병옥",
            "age": 72,
            "gender": "F",
            "phone": "010-0000-0000",
            "cognitive_stage": "인지 단계 유지",
            "memo": "고향집 마당 기억이 선명함",
            "login_id": "bae_test",
            "pin": CLIENT_PIN,
        },
    )
    assert res.status_code == 201, res.text
    created = res.json()
    client_id = created["id"]
    assert created["member_code"].startswith("#MEM-")
    assert created["has_pin"] is True

    # ── 2. 목록 / 검색 (client-list) ──────────────────────────
    res = client.get("/clients", headers=auth, params={"q": "배병옥"})
    assert res.status_code == 200
    assert res.json()["total"] == 1

    # ── 3. 상담 일정 등록 (calendar) ──────────────────────────
    res = client.post(
        "/appointments",
        headers=auth,
        json={
            "client_id": client_id,
            "scheduled_at": "2030-05-15T14:30:00",
            "purpose": "치매 상담",
            "place": "상담실 1",
        },
    )
    assert res.status_code == 201, res.text
    appointment_id = res.json()["id"]

    res = client.get(
        "/appointments/calendar", headers=auth, params={"year": 2030, "month": 5}
    )
    assert res.status_code == 200
    calendar = res.json()
    assert calendar["total"] == 1
    assert calendar["days"][0]["date"] == "2030-05-15"

    # ── 4. 상담 시작 (session-start-popup) ────────────────────
    res = client.post(
        "/sessions",
        headers=auth,
        json={"client_id": client_id, "appointment_id": appointment_id},
    )
    assert res.status_code == 201, res.text
    session_id = res.json()["id"]
    assert res.json()["status"] == "recording"

    # ── 5. 녹음 업로드 → STT (recording) ──────────────────────
    audio = io.BytesIO(b"fake-audio-bytes")
    res = client.post(
        f"/sessions/{session_id}/recording",
        headers=auth,
        files={"file": ("capstone.mp3", audio, "audio/mpeg")},
        data={"duration_seconds": "765"},
    )
    assert res.status_code == 200, res.text
    recording = res.json()
    assert recording["status"] == "transcribed"
    assert recording["stt_engine"] == "mock"
    assert len(recording["lines"]) == 8
    # 화자 분리: 상담사 Q / 내담자 A
    assert recording["lines"][0]["speaker"] == "상담사"
    assert recording["lines"][1]["speaker"] == "내담자"

    # ── 6. 실시간 텍스트 확인 (text-confirm-popup) ────────────
    res = client.get(f"/sessions/{session_id}/transcript", headers=auth)
    assert res.status_code == 200
    assert len(res.json()["lines"]) == 8

    # ── 7. AI 요약 (summary-review) ───────────────────────────
    res = client.post(f"/sessions/{session_id}/summary", headers=auth)
    assert res.status_code == 200, res.text
    summary = res.json()
    assert summary["summary"]
    assert summary["generated_by"] == "rule-based"   # 키 없는 환경 폴백
    assert summary["keywords"]

    # 상담사가 요약을 직접 수정 (기록 수정)
    res = client.patch(
        f"/sessions/{session_id}/summary",
        headers=auth,
        json={"title": "고향집 마당", "summary": "수정된 요약본입니다.",
              "keywords": ["고향집", "감나무"]},
    )
    assert res.status_code == 200
    assert res.json()["title"] == "고향집 마당"
    assert res.json()["keywords"] == ["고향집", "감나무"]

    # ── 8. 회상 이미지 생성 (image-generation → image-review) ─
    res = client.post(f"/sessions/{session_id}/image", headers=auth, json={})
    assert res.status_code == 200, res.text
    first_album = res.json()
    assert first_album["image_url"].startswith("http")

    # '다시 생성'
    res = client.post(
        f"/sessions/{session_id}/image",
        headers=auth,
        json={"extra_prompt": "조금 더 따뜻한 색감으로"},
    )
    assert res.status_code == 200
    second_album = res.json()
    assert second_album["id"] != first_album["id"]

    res = client.get(f"/sessions/{session_id}/images", headers=auth)
    assert len(res.json()) == 2

    # ── 9. 완료 저장 (session-complete) ───────────────────────
    res = client.post(
        f"/sessions/{session_id}/complete",
        headers=auth,
        json={"album_id": second_album["id"]},
    )
    assert res.status_code == 200, res.text
    completed = res.json()
    assert completed["status"] == "completed"
    assert completed["reference_code"].startswith("#CC-")
    assert completed["selected_album"]["id"] == second_album["id"]

    # ── 10. 세션 상세 (session-detail) ────────────────────────
    res = client.get(f"/sessions/{session_id}", headers=auth)
    detail = res.json()
    assert detail["summary"] == "수정된 요약본입니다."
    assert detail["thumbnail_url"] == second_album["image_url"]
    assert len(detail["qa_logs"]) == 8

    # ── 11. 대시보드 (dashboard) ──────────────────────────────
    res = client.get("/dashboard", headers=auth)
    assert res.status_code == 200
    dash = res.json()
    assert dash["completed_report_count"] == 1
    assert dash["client_count"] >= 1
    assert len(dash["cards"]) == 4
    assert dash["unread_notification_count"] >= 1

    res = client.get("/notifications", headers=auth)
    assert res.json()["unread"] >= 1

    # ── 12. 어르신 앱 로그인 (client-login) ───────────────────
    res = client.post(
        "/auth/client/login", json={"login_id": "bae_test", "pin": CLIENT_PIN}
    )
    assert res.status_code == 200, res.text
    client_auth = {"Authorization": f"Bearer {res.json()['access_token']}"}
    assert res.json()["role"] == "client"

    # 홈 (client-home)
    res = client.get("/me/home", headers=client_auth)
    assert res.status_code == 200
    home = res.json()
    assert home["name"] == "배병옥"
    assert home["featured"] is not None
    assert home["album_count"] == 2
    assert home["daily_tip"]

    # 앨범 (client-album)
    res = client.get("/me/album", headers=client_auth)
    assert res.json()["total"] == 2
    assert res.json()["items"][0]["date_label"]

    # 요약 보기 (client-summary-detail)
    res = client.get(f"/me/sessions/{session_id}/summary", headers=client_auth)
    assert res.status_code == 200
    assert res.json()["summary"] == "수정된 요약본입니다."

    # 설정 (client-settings)
    res = client.get("/me/settings", headers=client_auth)
    assert res.json()["font_scale"] == "large"
    res = client.patch(
        "/me/settings",
        headers=client_auth,
        json={"font_scale": "xlarge", "high_contrast": True},
    )
    assert res.json()["font_scale"] == "xlarge"
    assert res.json()["high_contrast"] is True

    # 상담사 전용 API는 어르신 토큰으로 접근 불가
    assert client.get("/dashboard", headers=client_auth).status_code == 403


def test_legacy_pipeline_compatibility(client, auth):
    """기존 Flutter ApiService(api_service.dart) 호환 확인."""
    res = client.post(
        "/clients",
        headers=auth,
        json={"name": "김영희", "age": 78, "gender": "F"},
    )
    user_id = res.json()["id"]

    # 1. 세션 생성
    res = client.post(
        "/pipeline/session", params={"user_id": user_id, "filename": "audio.mp3"}
    )
    assert res.status_code == 200, res.text
    session_id = res.json()["session_id"]

    # 2. 전체 파이프라인 실행
    audio = io.BytesIO(b"fake-audio-bytes")
    res = client.post(
        f"/pipeline/run/{session_id}",
        params={"order_index": 1},
        files={"file": ("audio.mp3", audio, "audio/mpeg")},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["stt_text"]
    assert body["image_url"].startswith("http")
    assert body["album_id"]

    # 3. 앨범 조회 - Dart는 배열을 기대한다
    res = client.get(f"/pipeline/album/{session_id}")
    assert res.status_code == 200
    albums = res.json()
    assert isinstance(albums, list) and len(albums) == 1
    # AlbumImage.fromJson이 참조하는 키가 모두 있어야 한다
    for key in ("id", "session_id", "image_url", "created_at"):
        assert key in albums[0]

    # 4. 세션 상세 - ConsultationSession.fromJson 대응
    res = client.get(f"/pipeline/session/{session_id}")
    body = res.json()
    for key in ("session_id", "user_id", "created_at", "summary", "albums"):
        assert key in body


def test_client_cannot_see_incomplete_session(client, auth):
    res = client.post(
        "/clients",
        headers=auth,
        json={"name": "박명수", "age": 65, "gender": "M",
              "login_id": "park_test", "pin": "111111"},
    )
    client_id = res.json()["id"]
    session_id = client.post(
        "/sessions", headers=auth, json={"client_id": client_id}
    ).json()["id"]

    token = client.post(
        "/auth/client/login", json={"login_id": "park_test", "pin": "111111"}
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 완료 전 세션은 어르신에게 노출되지 않는다
    res = client.get(f"/me/sessions/{session_id}/summary", headers=headers)
    assert res.status_code == 403


def test_phone_login_and_timezone(client, auth):
    """프론트(client_login.dart / calendar)가 보내는 실제 형식 검증."""
    from datetime import datetime, timedelta, timezone

    from core.config import settings

    # 자동 발급을 끈 상태에서 전화번호만 입력해 등록
    original = settings.AUTO_ISSUE_CLIENT_PIN
    settings.AUTO_ISSUE_CLIENT_PIN = False
    try:
        res = client.post(
            "/clients",
            headers=auth,
            json={"name": "김순자", "age": 80, "gender": "F", "phone": "010-9876-5432"},
        )
    finally:
        settings.AUTO_ISSUE_CLIENT_PIN = original

    assert res.status_code == 201
    client_id = res.json()["id"]
    assert res.json()["has_pin"] is False

    # PIN 미설정 상태에서는 로그인 불가
    res = client.post(
        "/auth/client/login", json={"login_id": "01098765432", "pin": "246810"}
    )
    assert res.status_code == 401

    # 상담사가 PIN 발급 (updateClient(pin:))
    res = client.patch(f"/clients/{client_id}", headers=auth, json={"pin": "246810"})
    assert res.status_code == 200
    assert res.json()["has_pin"] is True

    # 단말이 주는 어떤 표기로도 로그인된다
    for login_id in ["010-9876-5432", "01098765432", "+82 10-9876-5432", "+821098765432"]:
        res = client.post(
            "/auth/client/login", json={"login_id": login_id, "pin": "246810"}
        )
        assert res.status_code == 200, f"{login_id} -> {res.status_code} {res.text}"
        assert res.json()["role"] == "client"

    # 잘못된 PIN은 거부
    res = client.post(
        "/auth/client/login", json={"login_id": "01098765432", "pin": "000000"}
    )
    assert res.status_code == 401

    # ── 시간대: 앱은 toUtc().toIso8601String() 으로 보낸다 ────────
    KST = timezone(timedelta(hours=9))
    for hour in (8, 14, 23):
        picked = datetime(2030, 11, 15, hour, 0, tzinfo=KST)
        sent = picked.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

        res = client.post(
            "/appointments",
            headers=auth,
            json={"client_id": client_id, "scheduled_at": sent},
        )
        assert res.status_code == 201, res.text
        returned = res.json()["scheduled_at"]

        # Dart DateTime.tryParse는 오프셋 없는 문자열을 로컬(KST)로 읽는다.
        # 저장/응답이 KST 기준이므로 앱이 고른 시각과 그대로 일치해야 한다.
        shown = datetime.fromisoformat(returned)
        assert shown.tzinfo is None
        assert (shown.hour, shown.minute) == (hour, 0), f"{returned} != {hour}:00"
        assert shown.date() == picked.date()

    # 캘린더 그룹핑도 앱 로컬 날짜와 같은 칸에 들어가야 한다 (오전 8시 포함)
    res = client.get(
        "/appointments/calendar", headers=auth, params={"year": 2030, "month": 11}
    )
    days = {d["date"]: d["count"] for d in res.json()["days"]}
    assert days.get("2030-11-15") == 3, days
    assert "2030-11-14" not in days


def test_naive_datetime_still_accepted(client, auth):
    """오프셋 없이 보내는 클라이언트도 그대로 동작해야 한다."""
    client_id = client.post(
        "/clients", headers=auth, json={"name": "이말순"}
    ).json()["id"]

    res = client.post(
        "/appointments",
        headers=auth,
        json={"client_id": client_id, "scheduled_at": "2030-12-01T09:30:00"},
    )
    assert res.status_code == 201
    assert res.json()["scheduled_at"].startswith("2030-12-01T09:30")


def test_auto_issued_pin_allows_phone_login(client, auth):
    """프론트가 PIN을 못 보내는 동안의 우회: 기본 PIN 자동 발급.

    new_client_form.dart 는 pin 없이 createClient 를 호출한다.
    AUTO_ISSUE_CLIENT_PIN=True 이면 기본 PIN이 발급돼 전화번호로 로그인된다.
    """
    from core.config import settings

    res = client.post(
        "/clients",
        headers=auth,
        json={"name": "최정자", "age": 83, "gender": "F", "phone": "010-2222-3333"},
    )
    assert res.status_code == 201, res.text
    assert res.json()["has_pin"] is True      # pin 을 안 보냈는데도 발급됨

    res = client.post(
        "/auth/client/login",
        json={"login_id": "010-2222-3333", "pin": settings.DEFAULT_CLIENT_PIN},
    )
    assert res.status_code == 200, res.text
    assert res.json()["role"] == "client"

    # 기본 PIN이 아닌 값은 여전히 거부
    res = client.post(
        "/auth/client/login", json={"login_id": "010-2222-3333", "pin": "999999"}
    )
    assert res.status_code == 401


def test_explicit_pin_overrides_default(client, auth):
    """명시적으로 보낸 PIN은 그대로 쓰이고 기본 PIN은 통하지 않는다."""
    res = client.post(
        "/clients",
        headers=auth,
        json={"name": "강복순", "phone": "010-4444-5555", "pin": "778899"},
    )
    assert res.status_code == 201
    assert client.post(
        "/auth/client/login", json={"login_id": "01044445555", "pin": "778899"}
    ).status_code == 200
    assert client.post(
        "/auth/client/login", json={"login_id": "01044445555", "pin": "123456"}  # 기본 PIN, 명시 PIN 아님
    ).status_code == 401


# ══════════════════════════════════════════════════════════════
# 회원가입 - 중복 검사 / 소속 기관
# ══════════════════════════════════════════════════════════════
def test_counselor_signup_with_organization(client, auth):
    res = client.post(
        "/auth/counselor/register",
        headers=auth,
        json={
            "login_id": "counselor_park",
            "password": "test-password-1234",
            "name": "박상담",
            "phone": "010-7000-8000",
            "organization": "행복요양병원",
        },
    )
    assert res.status_code == 201, res.text
    assert res.json()["organization"] == "행복요양병원"


def test_duplicate_login_id_is_rejected(client, auth):
    payload = {
        "login_id": "counselor_dup", "password": "test-password-1234",
        "name": "중복테스트",
    }
    assert client.post(
        "/auth/counselor/register", headers=auth, json=payload
    ).status_code == 201
    res = client.post("/auth/counselor/register", headers=auth, json=payload)
    assert res.status_code == 409
    assert "아이디" in res.json()["detail"]


def test_duplicate_phone_is_rejected_across_formats(client, auth):
    """표기가 달라도 같은 번호면 중복으로 본다."""
    assert client.post(
        "/auth/counselor/register", headers=auth,
        json={"login_id": "phone_owner", "password": "test-password-1234",
              "name": "번호주인", "phone": "010-5000-6000"},
    ).status_code == 201

    for phone in ["010-5000-6000", "01050006000", "+82 10-5000-6000"]:
        res = client.post(
            "/auth/counselor/register", headers=auth,
            json={"login_id": f"other_{phone[-4:]}_{len(phone)}",
                  "password": "test-password-1234", "name": "다른사람",
                  "phone": phone},
        )
        assert res.status_code == 409, f"{phone} -> {res.status_code}"
        assert "전화번호" in res.json()["detail"]


def test_client_phone_cannot_duplicate_counselor_phone(client, auth):
    """어르신은 전화번호로 로그인하므로 상담사 번호와도 겹치면 안 된다."""
    client.post(
        "/auth/counselor/register", headers=auth,
        json={"login_id": "org_counselor", "password": "test-password-1234",
              "name": "기관상담사", "phone": "010-1212-3434"},
    )
    res = client.post(
        "/clients", headers=auth, json={"name": "겹치는분", "phone": "01012123434"}
    )
    assert res.status_code == 409


def test_duplicate_client_phone_is_rejected(client, auth):
    assert client.post(
        "/clients", headers=auth, json={"name": "첫번째", "phone": "010-7777-8888"}
    ).status_code == 201
    res = client.post(
        "/clients", headers=auth, json={"name": "두번째", "phone": "+82 10-7777-8888"}
    )
    assert res.status_code == 409


def test_availability_check_endpoint(client, auth):
    """회원가입 화면의 중복 확인 (인증 불필요)."""
    client.post(
        "/auth/counselor/register", headers=auth,
        json={"login_id": "taken_id", "password": "test-password-1234",
              "name": "선점", "phone": "010-3131-4141"},
    )

    res = client.get("/auth/check", params={"login_id": "taken_id"})
    assert res.status_code == 200
    assert res.json()["available"] is False

    res = client.get("/auth/check", params={"login_id": "brand_new_id"})
    assert res.json()["available"] is True

    # 표기가 달라도 선점된 번호로 인식
    res = client.get("/auth/check", params={"phone": "+82 10-3131-4141"})
    assert res.json()["available"] is False
    assert res.json()["value"] == "01031314141"

    res = client.get("/auth/check", params={"phone": "010-0000-0001"})
    assert res.json()["available"] is True

    # 형식 오류
    assert client.get("/auth/check", params={"phone": "abc"}).json()["available"] is False
    # 둘 다 주거나 둘 다 안 주면 400
    assert client.get("/auth/check").status_code == 400
    assert client.get(
        "/auth/check", params={"login_id": "a", "phone": "010-1111-2222"}
    ).status_code == 400


def test_counselor_can_update_own_profile(client, auth):
    res = client.patch(
        "/auth/counselor/me", headers=auth,
        json={"organization": "서울시니어케어센터", "phone": "010-2020-3030"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["organization"] == "서울시니어케어센터"

    # 같은 번호를 본인이 다시 저장하는 것은 허용
    assert client.patch(
        "/auth/counselor/me", headers=auth, json={"phone": "01020203030"}
    ).status_code == 200


def test_open_signup_flag_controls_public_registration(client):
    """공개 회원가입 플래그가 가입 가능 여부를 바꾼다."""
    from core.config import settings

    body = {
        "login_id": "public_signup", "password": "test-password-1234",
        "name": "공개가입", "organization": "열린센터",
    }

    # 기본값: 이미 상담사가 있으면 토큰 없이는 막힌다
    assert settings.OPEN_COUNSELOR_SIGNUP is False
    assert client.post("/auth/counselor/register", json=body).status_code == 403

    original = settings.OPEN_COUNSELOR_SIGNUP
    settings.OPEN_COUNSELOR_SIGNUP = True
    try:
        res = client.post("/auth/counselor/register", json=body)
        assert res.status_code == 201, res.text
        assert res.json()["organization"] == "열린센터"
    finally:
        settings.OPEN_COUNSELOR_SIGNUP = original


# ══════════════════════════════════════════════════════════════
# 내담자 추가 - 입력 검증 (앱 로그인 화면과 규칙을 맞춘다)
# ══════════════════════════════════════════════════════════════
def test_client_pin_must_be_six_digits(client, auth):
    """앱(client_login.dart)이 6자리 숫자만 받으므로 서버도 같게 막는다.

    서버가 더 느슨하면 "등록은 됐는데 어르신이 로그인 화면에서
    입력조차 못 하는" 계정이 조용히 생긴다.
    """
    base = {"name": "검증대상", "gender": "F", "phone": "010-8800-0001"}

    for bad_pin in ["1234", "12345", "abcdef", "12 345", "12345a"]:
        res = client.post("/clients", headers=auth, json={**base, "pin": bad_pin})
        assert res.status_code == 422, f"{bad_pin} -> {res.status_code}"

    res = client.post("/clients", headers=auth, json={**base, "pin": "123456"})
    assert res.status_code == 201


def test_client_pin_requires_a_login_method(client, auth):
    """PIN 만 있고 전화번호·아이디가 없으면 로그인할 방법이 없다."""
    res = client.post(
        "/clients", headers=auth, json={"name": "연락처없음", "pin": "123456"}
    )
    assert res.status_code == 422
    assert "전화번호" in res.text

    # 전화번호가 있으면 통과
    assert client.post(
        "/clients", headers=auth,
        json={"name": "연락처있음", "phone": "010-8800-0002", "pin": "123456"},
    ).status_code == 201

    # 아이디가 있어도 통과
    assert client.post(
        "/clients", headers=auth,
        json={"name": "아이디있음", "login_id": "id_only", "pin": "123456"},
    ).status_code == 201


def test_client_update_pin_also_validated(client, auth):
    client_id = client.post(
        "/clients", headers=auth, json={"name": "수정대상", "phone": "010-8800-0003"}
    ).json()["id"]

    assert client.patch(
        f"/clients/{client_id}", headers=auth, json={"pin": "1234"}
    ).status_code == 422
    assert client.patch(
        f"/clients/{client_id}", headers=auth, json={"pin": "654321"}
    ).status_code == 200


def test_new_client_form_fields_round_trip(client, auth):
    """내담자 추가 화면 항목(이름·성별·전화번호·PIN·메모)이 그대로 저장된다."""
    res = client.post(
        "/clients",
        headers=auth,
        json={
            "name": "김순자", "gender": "F", "age": 80,
            "phone": "010-8800-0004", "pin": "246810",
            "memo": "고향 이야기에 반응이 좋음. 큰 소리에 놀람.",
            "cognitive_stage": "경도 인지 장애",
        },
    )
    assert res.status_code == 201, res.text
    created = res.json()
    assert created["gender"] == "F"
    assert created["memo"].startswith("고향 이야기")
    assert created["cognitive_stage"] == "경도 인지 장애"
    assert created["member_code"].startswith("#MEM-")
    assert created["has_pin"] is True

    # 등록 직후 어르신이 전화번호 + PIN 으로 로그인된다
    res = client.post(
        "/auth/client/login", json={"login_id": "010-8800-0004", "pin": "246810"}
    )
    assert res.status_code == 200
    assert res.json()["name"] == "김순자"
