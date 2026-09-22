"""테스트 공통 설정 - 임시 SQLite DB로 앱 전체를 띄운다."""
import os
import tempfile

import pytest

# 앱 import 전에 환경을 고정해야 settings가 올바르게 잡힌다.
_TMP = tempfile.mkdtemp(prefix="capstone-test-")
os.environ["SQLITE_PATH"] = os.path.join(_TMP, "test.db")
os.environ["UPLOAD_DIR"] = os.path.join(_TMP, "uploads")
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_TMP, 'test.db')}"
os.environ["JWT_SECRET"] = "test-secret"
os.environ["USE_MOCK_ON_MISSING_KEY"] = "True"
os.environ["ENABLE_MORPH_ANALYSIS"] = "False"   # konlpy(JVM) 의존 제거
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("CLOVA_SPEECH_SECRET", None)

from fastapi.testclient import TestClient      # noqa: E402

from database import Base, engine             # noqa: E402
from main import app                          # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def counselor_token(client):
    """부트스트랩 상담사 계정 생성 후 토큰 반환."""
    client.post(
        "/auth/counselor/register",
        json={
            "login_id": "counselor_test",
            "password": "test-password-1234",
            "name": "이전수",
            "email": "test@example.com",
        },
    )
    res = client.post(
        "/auth/counselor/login",
        json={"login_id": "counselor_test", "password": "test-password-1234"},
    )
    assert res.status_code == 200, res.text
    return res.json()["access_token"]


@pytest.fixture(scope="session")
def auth(counselor_token):
    return {"Authorization": f"Bearer {counselor_token}"}
