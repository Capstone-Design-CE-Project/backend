"""디자인 화면 플로우를 그대로 따라가는 통합 테스트.

상담사: 로그인 → 내담자 등록 → 일정 등록 → 상담 시작 → 녹음 업로드
        → 요약 생성 → 이미지 생성 → 완료 저장 → 대시보드
어르신: PIN 로그인 → 홈 → 앨범 → 요약 보기 → 설정 변경
"""
import io

CLIENT_PIN = "4821"


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
              "login_id": "park_test", "pin": "1111"},
    )
    client_id = res.json()["id"]
    session_id = client.post(
        "/sessions", headers=auth, json={"client_id": client_id}
    ).json()["id"]

    token = client.post(
        "/auth/client/login", json={"login_id": "park_test", "pin": "1111"}
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 완료 전 세션은 어르신에게 노출되지 않는다
    res = client.get(f"/me/sessions/{session_id}/summary", headers=headers)
    assert res.status_code == 403
