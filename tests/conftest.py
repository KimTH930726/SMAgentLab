"""
pytest 공통 설정 및 fixture
"""
import pytest
import httpx

BASE_URL = "http://localhost:8000"
TEST_NS = "test_coupon"
UNRELATED_NS = "test_unrelated_ns"
TEST_CATEGORY = "공통지식"

# 기본 시드 관리자 계정 (core/config.py Settings.admin_default_password, .env ADMIN_DEFAULT_PASSWORD)
_ADMIN_USERNAME = "admin"
_ADMIN_PASSWORD = "1111"


@pytest.fixture(scope="session")
def client():
    """동기 httpx 클라이언트 (세션 전체 공유)."""
    with httpx.Client(base_url=BASE_URL, timeout=60.0) as c:
        yield c


@pytest.fixture(scope="session", autouse=True)
def wait_for_backend(client):
    """백엔드가 준비될 때까지 대기."""
    import time
    for _ in range(30):
        try:
            resp = client.get("/health")
            if resp.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(2)
    pytest.fail("Backend did not start within 60 seconds")


@pytest.fixture(scope="session", autouse=True)
def authenticate(client, wait_for_backend):
    """세션 시작 시 관리자로 로그인해 client에 Authorization 헤더를 부착.

    모든 지식/용어/네임스페이스 API가 JWT 인증을 요구하므로, 인증 없이 호출하면
    전부 403이 난다. admin 권한은 네임스페이스 소유권 체크를 통과하므로 CRUD
    테스트 전반에 적합하다.
    """
    resp = client.post("/api/auth/login", json={"username": _ADMIN_USERNAME, "password": _ADMIN_PASSWORD})
    resp.raise_for_status()
    client.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"


@pytest.fixture(scope="session", autouse=True)
def seed_namespaces(client, authenticate):
    """테스트가 의존하는 네임스페이스/업무구분을 세션 시작 시 준비하고 종료 시 정리.

    - namespace 생성은 UPSERT라 몇 번을 호출해도 안전 (ON CONFLICT DO UPDATE).
    - 업무구분 생성은 이미 있으면 409 — 무시.
    - 지식 등록은 namespace_id FK가 필요해 존재하지 않는 namespace에는 등록할 수
      없고(400), category도 필수(400)라서 두 시드 모두 있어야 지식 CRUD 테스트가 통과한다.
    """
    for ns in (TEST_NS, UNRELATED_NS):
        resp = client.post("/api/namespaces", json={"name": ns})
        assert resp.status_code == 200, resp.text

    resp = client.post(f"/api/namespaces/{TEST_NS}/categories", json={"name": TEST_CATEGORY})
    assert resp.status_code in (201, 409), resp.text

    yield

    client.delete(f"/api/namespaces/{TEST_NS}")
    client.delete(f"/api/namespaces/{UNRELATED_NS}")


@pytest.fixture
def sample_knowledge(client):
    """테스트용 지식 1건 등록 후 반환, 테스트 종료 시 삭제."""
    resp = client.post("/api/knowledge", json={
        "namespace": TEST_NS,
        "container_name": "coupon-api",
        "target_tables": ["coupon_issue", "coupon_use_history"],
        "content": "쿠폰 회수 처리 실패 시 coupon_issue 테이블에서 status='FAILED' 건을 확인하고 "
                   "coupon_use_history에서 이력을 조회한다. 회수 API는 coupon-api 컨테이너에서 처리한다.",
        "query_template": (
            "SELECT ci.id, ci.status, ci.user_id, cuh.action\n"
            "FROM coupon_issue ci\n"
            "LEFT JOIN coupon_use_history cuh ON ci.id = cuh.coupon_issue_id\n"
            "WHERE ci.status = 'FAILED'\n"
            "  AND ci.updated_at >= NOW() - INTERVAL '1 day'\n"
            "ORDER BY ci.updated_at DESC;"
        ),
        "base_weight": 1.0,
        "category": TEST_CATEGORY,
    })
    assert resp.status_code == 201, resp.text
    item = resp.json()
    yield item
    # cleanup
    client.delete(f"/api/knowledge/{item['id']}")


@pytest.fixture
def sample_glossary(client):
    """테스트용 용어 1건 등록 후 반환, 테스트 종료 시 삭제."""
    resp = client.post("/api/knowledge/glossary", json={
        "namespace": TEST_NS,
        "term": "회수",
        "description": "쿠폰 회수, 뺏어오기, 강제 반납, 쿠폰 취소 등 쿠폰을 사용자로부터 되돌리는 처리",
    })
    assert resp.status_code == 201, resp.text
    item = resp.json()
    yield item
    client.delete(f"/api/knowledge/glossary/{item['id']}")
