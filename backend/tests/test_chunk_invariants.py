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
        kind = rng.choice(["empty", "tiny", "short", "mid", "long", "numline"])
        nlines = {"empty": 0, "tiny": 1, "short": 2, "mid": 4, "long": 30, "numline": 1}[kind]
        lines = []
        if kind == "numline":   # 본문이 번호 제목 줄 하나뿐(홈페이지 모양, 11/04 ①A 제목만 조각 합치기)
            lines.append(f"0{counter}-2.1.{counter}. 소제목{counter} 현황")
        for j in range(nlines if kind != "numline" else 0):
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


# ── 11/04 ①A 제목만 조각 합치기 ──────────────────────────────────────────────
@pytest.mark.parametrize("seed", range(200))
def test_no_heading_only_chunk_when_neighbor_fits(seed):
    """성질(I1~I5 그대로 + 하나 더): 섹션 분리 모드에서 제목만 조각은, 이웃과 합쳐 max를 넘는 경우 말고는 남지 않는다."""
    from agents.knowledge_rag.ingestion.chunker import _is_heading_only
    rng = random.Random(10_000 + seed)
    chunks = _check(_gen_sections(rng, rng.randint(2, 18)))
    for i, c in enumerate(chunks):
        if _is_heading_only(c.text) and len(chunks) > 1:
            nbrs = [chunks[j] for j in (i - 1, i + 1) if 0 <= j < len(chunks)]
            assert all(len(n.text) + 1 + len(c.text) > MAX for n in nbrs), (i, c.text[:40])


def test_numbered_title_chunk_goes_in_front_of_next():
    secs = [
        {"title": "개요", "content": "05-2.1.3. 결제 현황", "level": 2},
        {"title": "결제 흐름", "content": "결제는 승인 → 매입 → 정산 순서로 처리한다. " + "가" * 40, "level": 2},
        {"title": "취소", "content": "취소는 매입 전까지 전체 취소만 가능하다. " + "나" * 40, "level": 2},
    ]
    chunks = _check(secs)
    assert len(chunks) == 2 and chunks[0].text.startswith("## 개요\n05-2.1.3. 결제 현황\n## 결제 흐름")


def test_last_heading_only_goes_after_previous():
    secs = [
        {"title": "결제 흐름", "content": "결제는 승인 → 매입 → 정산 순서로 처리한다. " + "가" * 40, "level": 2},
        {"title": "참고", "content": "(참고) 정산 일정표", "level": 2},
    ]
    chunks = _check(secs)
    assert len(chunks) == 1 and chunks[0].text.endswith("## 참고\n(참고) 정산 일정표")


def test_merge_respects_max_chars():
    big = "\n".join(f"줄{i} " + "가" * 90 for i in range(21))[:1990]   # 제목만 조각을 붙이면 max를 넘는 길이
    chunks = _check([{"title": "개요", "content": "1.2. 현황", "level": 2}, {"title": "본문", "content": big, "level": 2}])
    assert all(len(c.text) <= MAX for c in chunks)


def test_sentence_numbered_line_is_body():
    """번호로 시작해도 문장(. 다 요로 끝남)이면 본문 — 제목 같은 줄로 보지 않는다."""
    from agents.knowledge_rag.ingestion.chunker import _is_heading_like
    assert _is_heading_like("05-2.1.3. 결제 현황") and _is_heading_like("(참고) 정산 일정표") and _is_heading_like("## 제목")
    assert not _is_heading_like("1. 주문 취소 후 환불을 처리합니다.") and not _is_heading_like("2) 결과를 확인한다")


def test_heading_only_not_glued_onto_other_parent():
    """독립 리뷰 M1: A › A-1(번호 줄만) 다음이 다른 부모의 B면, A-1을 B 앞에 붙이지 않는다(B가 A 아래로 잘못 분류됨) — 앞 조각(A) 뒤에."""
    secs = [
        {"title": "A", "content": "A 본문 " + "가" * 80, "level": 1},
        {"title": "A-1", "content": "1.2. 현황", "level": 2},
        {"title": "B", "content": "B 본문 " + "나" * 80, "level": 1},
    ]
    chunks = _check(secs)
    b = next(c for c in chunks if "B 본문" in c.text)
    assert "A" not in b.heading_path and "1.2. 현황" not in b.text
    assert any("A 본문" in c.text and c.text.endswith("1.2. 현황") for c in chunks)


def test_short_numbered_list_is_body():
    """독립 리뷰 M2: 짧은 번호 목록(문장 끝 없음)만 있는 섹션은 진짜 본문 — 이웃과 합치지 않는다."""
    secs = [
        {"title": "준비물", "content": "1) 신분증\n2) 통장 사본\n3) 신청서", "level": 2},
        {"title": "환불", "content": "환불은 신청 후 3영업일 안에 처리한다. " + "다" * 40, "level": 2},
    ]
    chunks = _check(secs)
    assert len(chunks) == 2 and chunks[0].text.startswith("## 준비물")


def test_merge_mode_sources_untouched(monkeypatch):
    """병합 모드(md 등)는 이 단계를 안 탄다 — 기존 동작 그대로."""
    from agents.knowledge_rag.ingestion import chunker
    secs = [{"title": "개요", "content": "1.2. 현황", "level": 2}, {"title": "본문", "content": "본문 " + "가" * 60, "level": 2}]
    before = [c.text for c in chunk_document(_doc(secs, "md"), strategy="section")]

    def boom(*a, **k):
        raise AssertionError("병합 모드에서 호출되면 안 됨")
    monkeypatch.setattr(chunker, "_merge_heading_only", boom)
    assert [c.text for c in chunk_document(_doc(secs, "md"), strategy="section")] == before



def _heading_shape_ok(chunks):
    """본문 있는 조각이 제목 줄로 끝나지 않고, 한 조각 안에 같은 제목 줄이 두 번 나오지 않는다."""
    for c in chunks:
        ls = [ln.strip() for ln in c.text.split("\n") if ln.strip()]
        heads = [ln for ln in ls if ln.startswith("#")]
        assert not (ls[-1].startswith("#") and len(heads) < len(ls)), c.text[:80]
        assert len(heads) == len(set(heads)), c.text[:80]


def test_line_longer_than_max_heading_not_dangling_or_doubled():
    """빈 줄 없는 한 줄이 max보다 길면 고정 길이로 나누고 조각마다 제목을 다시 붙인다 — 그때 남는 제목만 조각을 앞 조각 끝에
    매달거나(다음 조각에도 같은 제목) 다음 조각 앞에 또 붙이지 않는다(11/05 앱 공간 job 1954: 2,659자 한 줄 섹션).
    고정 길이로 자른 줄은 I2b(잘린 줄) 대상이라 _check 대신 모양만 본다."""
    huge = "긴 설명 " + "가나다라 " * 660   # 약 3,300자 한 줄
    for secs in (
        [{"title": "앞", "content": "앞 섹션 본문입니다 " + "마" * 40, "level": 2}, {"title": "긴 섹션", "content": huge, "level": 2},
         {"title": "뒤", "content": "뒤 섹션 본문입니다 " + "바" * 40, "level": 2}],
        [{"title": "긴 섹션", "content": huge, "level": 2}],
    ):
        chunks = chunk_document(_doc(secs), strategy="section")
        _heading_shape_ok(chunks)
        assert sum(c.text.count("긴 설명") for c in chunks) == 1   # 본문 첫머리는 한 번만(유실·중복 없음)


def test_merge_drops_heading_only_already_leading_next():
    """단위: 다음 조각이 이미 같은 제목으로 시작하면 제목만 조각은 버린다(붙일 자리와 크기에 상관없이)."""
    from agents.knowledge_rag.ingestion.chunker import Chunk, _merge_heading_only
    prev, head = Chunk(text="## 앞\n앞 본문", idx=0), Chunk(text="## 긴 섹션", idx=1)
    for body in ("가" * 1990, "가" * 10):   # 합치면 max를 넘는 경우 / 안 넘는 경우
        out = _merge_heading_only([prev, head, Chunk(text="## 긴 섹션\n" + body, idx=2)], 2000)
        assert [c.text.count("## 긴 섹션") for c in out] == [0, 1]


@pytest.mark.parametrize("seed", range(100))
def test_no_dangling_heading_random_with_huge_lines(seed):
    """성질(무작위): 빈 줄 없는 아주 긴 줄 섹션이 섞여도 본문 있는 조각이 제목 줄로 끝나지 않는다."""
    rng = random.Random(seed)
    secs = _gen_sections(rng, rng.randint(2, 12))
    for n, s in enumerate(secs):
        if rng.random() < 0.3:
            s["content"] = f"긴줄{n} " + "가나다 " * rng.randint(300, 490)   # 한 줄이 max 이하(넘는 줄은 고정 길이로 잘려 I2b 대상 밖)
    for c in _check(secs):
        ls = [ln.strip() for ln in c.text.split("\n") if ln.strip()]
        assert not (ls[-1].startswith("#") and any(not x.startswith("#") for x in ls)), c.text[:80]
