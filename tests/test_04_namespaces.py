"""
TC-04: 네임스페이스 목록 조회
"""

from conftest import TEST_CATEGORY

TEST_NS = "test_coupon"


def test_namespaces_returns_list(client):
    """GET /api/namespaces 가 리스트를 반환한다."""
    resp = client.get("/api/namespaces")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_namespace_appears_after_knowledge_create(client):
    """네임스페이스 생성 후 목록에 나타나고, 그 안에 지식을 등록할 수 있다.

    주의: namespace_id가 FK로 강제되므로(마이그레이션 이후) 지식 등록만으로 네임스페이스가
    자동 생성되던 예전 동작은 더 이상 없다 — 먼저 POST /api/namespaces로 생성해야 한다.
    """
    new_ns = "test_gift_ns_unique"
    create_ns = client.post("/api/namespaces", json={"name": new_ns})
    assert create_ns.status_code == 200, create_ns.text

    resp = client.get("/api/namespaces")
    assert new_ns in resp.json()

    create_cat = client.post(f"/api/namespaces/{new_ns}/categories", json={"name": TEST_CATEGORY})
    assert create_cat.status_code in (201, 409), create_cat.text

    create = client.post("/api/knowledge", json={
        "namespace": new_ns,
        "content": "네임스페이스 노출 테스트",
        "category": TEST_CATEGORY,
    })
    assert create.status_code == 201, create.text
    kid = create.json()["id"]

    client.delete(f"/api/knowledge/{kid}")
    client.delete(f"/api/namespaces/{new_ns}")
