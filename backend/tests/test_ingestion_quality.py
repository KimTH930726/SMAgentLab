"""등록 묶음 품질 검사 (2026-10-07) — 지표로 안 보이던 청크 경계 결함을 job마다 잡는다."""
from agents.knowledge_rag.ingestion.quality import assess


def test_clean_job_has_no_warnings():
    q = assess([("## 1. 가\n본문 하나입니다 길게", ["상위"]), ("## 2. 나\n본문 둘입니다 길게", ["상위"])])
    assert q["warnings"] == [] and q["with_heading_path"] == 2


def test_real_defect_shapes_are_flagged():
    """9/22 컨플루언스 일괄 등록의 실제 모양: 폐점 섹션이 두 청크에, 다음 상위 제목이 앞 청크 끝에."""
    closing = "## 1.2.1.2. 매장 폐점 관리\n영업 시간이 끝난 매장을 데몬이 폐점 처리한다"
    q = assess([(closing, []), (closing + "\n## 1.2.2. 메뉴", []), ("## 1.2.2. 메뉴", [])])
    assert q["adjacent_overlap"] == 1
    assert q["trailing_heading"] == 1
    assert q["heading_only"] == 1
    assert len(q["warnings"]) == 3


def test_repeated_parent_heading_alone_is_not_overlap():
    """상위 제목 줄만 반복되는 건(하위 청크 맥락) 중복이 아니다."""
    q = assess([("## 1. 상위\n## 1.1 가\n가 본문입니다 길게", []), ("## 1. 상위\n## 1.2 나\n나 본문입니다 길게", [])])
    assert q["adjacent_overlap"] == 0
