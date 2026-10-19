"""청크 불변식 (2026-10-07) — "있어야 할 게 있나"만 보던 테스트가 중복·유실을 못 잡은 근본 원인 대응.

실사고: 컨플루언스 일괄 등록 51청크 중 20쌍이 인접 청크와 같은 줄을 공유(짧은 섹션 재부착이 형제·상위에도 적용),
그걸 고치자 50자 미만 형제 섹션이 통째로 사라지는 회귀. 기존 테스트는 둘 다 통과했다 — 특정 사례에서 "X가 들어있다"만
확인하고 출력 전체의 성질(유실·중복·경계)은 보지 않았기 때문. 여기서는 어떤 섹션 구조든 지켜야 할 성질을 무작위 구조와
실데이터 모양(번호 반복·제목뿐인 섹션·짧은 형제 연속)으로 확인한다:
  I1 유실 0 — 입력의 모든 본문 줄이 어떤 청크엔가 있다
  I2 중복 0 — 본문 줄은 정확히 한 청크에만 있다(제목 줄은 하위 청크 맥락용으로 반복 허용)
  I3 청크가 제목 줄로 끝나지 않는다(다음 섹션 제목이 앞 청크에 매달리지 않음)
  I4 크기 — max_chars + 제목 머리말 여유 이내
  I5 heading_path — 청크 첫 본문 줄이 속한 섹션의 조상 제목이 heading_path에(또는 청크 앞 제목으로) 들어 있다
"""
import random

import pytest

from agents.knowledge_rag.ingestion.adapters import ParsedDocument
from agents.knowledge_rag.ingestion.chunker import chunk_document
from trustkit.ingest.invariants import check_chunks

MAX = 2000
HEAD_SLACK = 0   # 재분할 조각의 제목 머리말까지 포함해 max 이내(제목이 아주 길어 max의 절반을 넘는 경우만 예외 — 생성기엔 없음)


def _doc(sections, st="confluence"):
    return ParsedDocument(source_type=st, source_name="t", raw_text="", sections=sections)


def _gen_sections(rng: random.Random, n: int) -> list[dict]:
    """번호 제목 트리 — 레벨은 위로는 자유롭게, 아래로는 한 단계씩. 본문 길이는 0/짧음/중간/김."""
    secs, level, counter = [], 1, 0
    for _ in range(n):
        level = max(1, min(4, level + rng.choice([-2, -1, 0, 0, 1, 1])))
        counter += 1
        kind = rng.choice(["empty", "tiny", "short", "mid", "long"])
        nlines = {"empty": 0, "tiny": 1, "short": 2, "mid": 4, "long": 30}[kind]
        lines = []
        for j in range(nlines):
            width = {"tiny": 8, "short": 30, "mid": 60, "long": 90}[kind]
            lines.append(f"본문{counter}-{j} " + "가" * width)
        secs.append({"title": f"{counter}. 제목{counter}", "content": "\n".join(lines), "level": level})
    return secs


def _body_lines(sections):
    return [ln.strip() for s in sections for ln in (s["content"] or "").split("\n") if ln.strip()]


def _check(sections, st="confluence"):
    """I1~I5 검사 본체는 trustkit.ingest.invariants.check_chunks(이식 가능한 신뢰도 레이어, 2026-10-19) — 여기는 우리 분할기를 넣어 돌린다."""
    chunks = chunk_document(_doc(sections, st), strategy="section")
    errs = check_chunks(sections, [(c.text, c.heading_path) for c in chunks], max_chars=MAX + HEAD_SLACK)
    assert not errs, errs[:5]
    return chunks


@pytest.mark.parametrize("seed", range(200))
@pytest.mark.parametrize("st", ["confluence", "md"])   # 섹션마다 분리(always_split) / 작으면 병합
def test_invariants_random_trees(seed, st):
    rng = random.Random(seed)
    _check(_gen_sections(rng, rng.randint(1, 18)), st)


def test_real_shape_coupang_store():
    """외부서비스DB 쿠팡이츠 페이지 모양(본문 없이 번호·길이만): 번호 1.2.1.2 반복, 제목뿐인 상위, 짧은 형제 연속."""
    secs = [
        {"title": "1.2.1. 매장", "content": "", "level": 3},
        {"title": "1.2.1.1. 매장 관리", "content": "b1 " + "가" * 68, "level": 4},
        {"title": "1.2.1.2. 매장 개점 관리", "content": "\n".join(f"b2-{i} " + "나" * 90 for i in range(3)), "level": 4},
        {"title": "1.2.1.2. 매장 폐점 관리", "content": "b3 " + "다" * 50 + "\nb4 " + "라" * 63, "level": 4},
        {"title": "1.2.2. 메뉴", "content": "", "level": 3},
        {"title": "1.2.2.1. 메뉴 등록", "content": "b5 " + "마" * 68 + "\nb6 " + "바" * 31, "level": 4},
        {"title": "1.2.2.2. 메뉴 수정", "content": "\n".join(f"b7-{i} " + "사" * 80 for i in range(3)), "level": 4},
    ]
    chunks = _check(secs)
    assert len(chunks) == 5


def test_short_sibling_kept():
    """수정 중 생긴 회귀 재현: 50자 미만 형제 섹션이 최소 길이 필터로 사라지면 안 된다."""
    secs = [
        {"title": "1. 개요", "content": "가" * 300, "level": 2},
        {"title": "2. 환불", "content": "환불 불가 상품", "level": 2},
        {"title": "3. 배송", "content": "나" * 300, "level": 2},
    ]
    _check(secs)


def test_parent_intro_once_and_on_child():
    """짧은 상위 도입부는 하위 청크 앞에 한 번만(따로 한 번 더 저장하지 않음)."""
    secs = [
        {"title": "1.2 부모", "content": "부모 도입 설명 문장입니다. " * 5, "level": 2},
        {"title": "1.2.1 자식", "content": "다" * 300, "level": 3},
    ]
    chunks = _check(secs)
    assert len(chunks) == 1 and chunks[0].text.startswith("## 1.2 부모")


def test_long_section_without_blank_lines_splits_on_line_boundaries():
    """리뷰 2026-10-07: HTML 섹션 본문은 빈 줄이 없어 통째로 고정 길이 분할로 가 문장 중간에서 잘리고 다음 청크가 앞 줄 일부를 다시 담았다."""
    lines = [f"줄{i} " + "가나다라마바사 " * 8 + "이렇습니다." for i in range(60)]
    chunks = _check([{"title": "A", "content": "\n".join(lines), "level": 2},
                     {"title": "B", "content": "짧은 본문입니다 충분히", "level": 2}])
    assert all(len(c.text) <= MAX for c in chunks)
    assert all(c.text.startswith("## A") for c in chunks if "줄" in c.text)   # 나뉜 조각마다 자기 제목
