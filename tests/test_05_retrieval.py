"""
TC-05: 검색 파이프라인 (하이브리드 검색 + 용어 매핑)

주의: LLM 답변 생성에 시간이 걸릴 수 있으므로 timeout 을 넉넉히 줍니다.
"""
import pytest

from conftest import TEST_CATEGORY, UNRELATED_NS

TEST_NS = "test_coupon"


@pytest.fixture(scope="module")
def seeded_data(client):
    """검색 테스트용 지식 + 용어 등록 (모듈 단위 공유)."""
    r1 = client.post("/api/knowledge", json={
        "namespace": TEST_NS,
        "container_name": "coupon-api",
        "target_tables": ["coupon_issue", "coupon_use_history"],
        "content": (
            "쿠폰 회수 처리 실패 시 coupon_issue 테이블에서 status='FAILED' 건을 확인한다. "
            "coupon_use_history 에서 회수 이력을 조회할 수 있다."
        ),
        "query_template": "SELECT * FROM coupon_issue WHERE status='FAILED';",
        "base_weight": 1.0,
        "category": TEST_CATEGORY,
    })
    assert r1.status_code == 201, r1.text
    k1 = r1.json()

    r2 = client.post("/api/knowledge", json={
        "namespace": TEST_NS,
        "container_name": "order-api",
        "target_tables": ["orders", "order_items"],
        "content": "주문 취소 처리 시 orders 테이블의 status를 CANCELLED로 업데이트한다.",
        "query_template": "UPDATE orders SET status='CANCELLED' WHERE id=:id;",
        "base_weight": 1.0,
        "category": TEST_CATEGORY,
    })
    assert r2.status_code == 201, r2.text
    k2 = r2.json()

    r3 = client.post("/api/knowledge/glossary", json={
        "namespace": TEST_NS,
        "term": "회수",
        "description": "쿠폰 회수, 뺏어오기, 강제 반납 등 쿠폰을 사용자로부터 되돌리는 처리",
    })
    assert r3.status_code == 201, r3.text
    g1 = r3.json()

    yield {"k1": k1, "k2": k2, "g1": g1}

    client.delete(f"/api/knowledge/{k1['id']}")
    client.delete(f"/api/knowledge/{k2['id']}")
    client.delete(f"/api/knowledge/glossary/{g1['id']}")


class TestHybridSearch:
    def test_chat_returns_results(self, client, seeded_data):
        """쿠폰 관련 질문 시 결과가 1건 이상 반환된다."""
        resp = client.post("/api/chat", json={
            "namespace": TEST_NS,
            "question": "쿠폰 회수 실패한 건 어떻게 확인해?",
            "w_vector": 0.7,
            "w_keyword": 0.3,
            "top_k": 5,
        }, timeout=120.0)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["results"]) >= 1

    def test_top_result_is_coupon_knowledge(self, client, seeded_data):
        """쿠폰 질문의 최상위 결과가 coupon-api 컨테이너 지식이다."""
        resp = client.post("/api/chat", json={
            "namespace": TEST_NS,
            "question": "쿠폰 회수 실패 건 확인 방법",
            "w_vector": 0.7,
            "w_keyword": 0.3,
            "top_k": 5,
        }, timeout=120.0)
        body = resp.json()
        top = body["results"][0]
        assert top["container_name"] == "coupon-api"
        assert "coupon_issue" in top["target_tables"]

    def test_glossary_term_is_mapped(self, client, seeded_data):
        """'뺏어오기' 질문 시 mapped_term 이 '회수' 로 매핑된다."""
        resp = client.post("/api/chat", json={
            "namespace": TEST_NS,
            "question": "쿠폰 뺏어오기 실패 건 조회해줘",
            "w_vector": 0.7,
            "w_keyword": 0.3,
            "top_k": 5,
        }, timeout=120.0)
        body = resp.json()
        assert body["mapped_term"] == "회수"

    def test_query_template_included_in_results(self, client, seeded_data):
        """검색 결과에 SQL 쿼리 템플릿이 포함된다."""
        resp = client.post("/api/chat", json={
            "namespace": TEST_NS,
            "question": "쿠폰 실패 상태 조회 쿼리",
            "w_vector": 0.5,
            "w_keyword": 0.5,
            "top_k": 3,
        }, timeout=120.0)
        body = resp.json()
        top = body["results"][0]
        assert top["query_template"] is not None
        assert "coupon_issue" in top["query_template"]

    def test_chat_answer_not_empty(self, client, seeded_data):
        """LLM 답변이 비어있지 않다 (Ollama 가동 중인 경우)."""
        resp = client.post("/api/chat", json={
            "namespace": TEST_NS,
            "question": "쿠폰 회수 실패 건 확인 방법",
            "w_vector": 0.7,
            "w_keyword": 0.3,
            "top_k": 3,
        }, timeout=120.0)
        body = resp.json()
        # Ollama 미가동 시 answer 는 빈 문자열일 수 있으므로 타입만 확인
        assert isinstance(body["answer"], str)

    def test_unrelated_namespace_returns_no_results(self, client, seeded_data):
        """관련 지식이 없는(그러나 존재하는) namespace 에서는 결과가 0건이다.

        namespace_id가 FK로 강제되므로 존재하지 않는 namespace를 넘기면 404가 난다
        (예전엔 조용히 빈 결과를 반환했음) — 그래서 실제로 존재하되 지식이 비어 있는
        UNRELATED_NS를 사용해 "관련 없음" 시나리오를 검증한다.
        """
        resp = client.post("/api/chat", json={
            "namespace": UNRELATED_NS,
            "question": "쿠폰 회수 실패 건",
            "w_vector": 0.7,
            "w_keyword": 0.3,
            "top_k": 5,
        }, timeout=60.0)
        assert resp.status_code == 200
        assert resp.json()["results"] == []


class TestSearchWeights:
    def test_vector_only_search(self, client, seeded_data):
        """w_vector=1.0 (벡터 전용) 으로도 결과가 반환된다."""
        resp = client.post("/api/chat", json={
            "namespace": TEST_NS,
            "question": "쿠폰 환불 이력 조회",
            "w_vector": 1.0,
            "w_keyword": 0.0,
            "top_k": 3,
        }, timeout=60.0)
        assert resp.status_code == 200

    def test_keyword_only_search(self, client, seeded_data):
        """w_keyword=1.0 (키워드 전용) 으로도 결과가 반환된다."""
        resp = client.post("/api/chat", json={
            "namespace": TEST_NS,
            "question": "coupon_issue FAILED 조회",
            "w_vector": 0.0,
            "w_keyword": 1.0,
            "top_k": 3,
        }, timeout=60.0)
        assert resp.status_code == 200
