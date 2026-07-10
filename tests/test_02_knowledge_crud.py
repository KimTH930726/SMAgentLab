"""
TC-02: 지식 베이스 CRUD
"""
import pytest

from conftest import TEST_CATEGORY

TEST_NS = "test_coupon"


class TestKnowledgeCreate:
    def test_create_returns_201(self, client):
        """지식 등록 시 201과 id를 반환한다."""
        resp = client.post("/api/knowledge", json={
            "namespace": TEST_NS,
            "content": "임시 테스트 지식",
            "base_weight": 1.0,
            "category": TEST_CATEGORY,
        })
        assert resp.status_code == 201
        body = resp.json()
        assert "id" in body
        assert body["namespace"] == TEST_NS
        # cleanup
        client.delete(f"/api/knowledge/{body['id']}")

    def test_create_with_all_fields(self, client):
        """모든 필드를 포함한 지식 등록이 정상 저장된다."""
        resp = client.post("/api/knowledge", json={
            "namespace": TEST_NS,
            "container_name": "coupon-api",
            "target_tables": ["coupon_issue", "coupon_use_history"],
            "content": "전체 필드 테스트 지식입니다.",
            "query_template": "SELECT * FROM coupon_issue WHERE id = :id;",
            "base_weight": 2.5,
            "category": TEST_CATEGORY,
        })
        assert resp.status_code == 201
        body = resp.json()
        assert body["container_name"] == "coupon-api"
        assert "coupon_issue" in body["target_tables"]
        assert body["base_weight"] == 2.5
        client.delete(f"/api/knowledge/{body['id']}")

    def test_create_missing_content_fails(self, client):
        """content 누락 시 422를 반환한다."""
        resp = client.post("/api/knowledge", json={"namespace": TEST_NS})
        assert resp.status_code == 422


class TestKnowledgeRead:
    def test_list_all(self, client, sample_knowledge):
        """지식 목록 전체 조회가 성공한다."""
        resp = client.get("/api/knowledge")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_list_by_namespace(self, client, sample_knowledge):
        """namespace 필터 시 해당 네임스페이스 지식만 반환한다."""
        resp = client.get("/api/knowledge", params={"namespace": TEST_NS})
        assert resp.status_code == 200
        items = resp.json()
        assert all(i["namespace"] == TEST_NS for i in items)
        ids = [i["id"] for i in items]
        assert sample_knowledge["id"] in ids

    def test_list_unknown_namespace_empty(self, client):
        """존재하지 않는 namespace 필터 시 빈 목록을 반환한다."""
        resp = client.get("/api/knowledge", params={"namespace": "nonexistent_ns_xyz"})
        assert resp.status_code == 200
        assert resp.json() == []


class TestKnowledgeUpdate:
    def test_update_content(self, client, sample_knowledge):
        """content 수정 시 updated_at 이 갱신된다."""
        kid = sample_knowledge["id"]
        original_updated = sample_knowledge["updated_at"]

        import time; time.sleep(1)  # updated_at 차이를 만들기 위해

        resp = client.put(f"/api/knowledge/{kid}", json={
            "content": "수정된 내용입니다. 임베딩도 재생성됩니다.",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["content"] == "수정된 내용입니다. 임베딩도 재생성됩니다."
        assert body["updated_at"] != original_updated

    def test_update_base_weight(self, client, sample_knowledge):
        """base_weight 수정이 반영된다."""
        kid = sample_knowledge["id"]
        resp = client.put(f"/api/knowledge/{kid}", json={"base_weight": 3.0})
        assert resp.status_code == 200
        assert resp.json()["base_weight"] == 3.0

    def test_update_nonexistent_returns_404(self, client):
        """존재하지 않는 id 수정 시 404를 반환한다."""
        resp = client.put("/api/knowledge/99999999", json={"base_weight": 1.0})
        assert resp.status_code == 404


class TestKnowledgeDelete:
    def test_delete_returns_204(self, client):
        """등록 후 삭제 시 204를 반환한다."""
        create = client.post("/api/knowledge", json={
            "namespace": TEST_NS, "content": "삭제 테스트용", "category": TEST_CATEGORY,
        })
        kid = create.json()["id"]
        resp = client.delete(f"/api/knowledge/{kid}")
        assert resp.status_code == 204

    def test_delete_nonexistent_returns_404(self, client):
        """존재하지 않는 id 삭제 시 404를 반환한다."""
        resp = client.delete("/api/knowledge/99999999")
        assert resp.status_code == 404

    def test_deleted_item_not_in_list(self, client):
        """삭제된 지식은 목록 조회에서 나타나지 않는다."""
        create = client.post("/api/knowledge", json={
            "namespace": TEST_NS, "content": "삭제 후 목록 확인용", "category": TEST_CATEGORY,
        })
        kid = create.json()["id"]
        client.delete(f"/api/knowledge/{kid}")

        resp = client.get("/api/knowledge", params={"namespace": TEST_NS})
        ids = [i["id"] for i in resp.json()]
        assert kid not in ids
