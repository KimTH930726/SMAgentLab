"""정책서 v0 변환기 — 성질(불변식)로 검사한다: 유실 0 · 중복 0 · 새로 생긴 내용 0 · 끊긴 연결 0 · 압축 무손실.
가짜 원천(작은 dict)으로 변환 → check_plan이 비어야 하고, 결과를 일부러 망가뜨리면 check_plan이 잡아야 한다."""
import copy
import importlib.util
import random
import sys
from pathlib import Path

# conftest가 service 패키지를 MagicMock으로 두므로 파일로 직접 불러온다(변환기는 앱 의존이 없다)
_spec = importlib.util.spec_from_file_location(
    "v0_convert_under_test", str(Path(__file__).resolve().parents[1] / "service/policy/v0_convert.py"))
vc = importlib.util.module_from_spec(_spec)
sys.modules["v0_convert_under_test"] = vc   # dataclass가 모듈을 찾을 수 있게
_spec.loader.exec_module(vc)


def _cell(col, text, src_row, inherited=False):
    return {"column": col, "text": text, "source": {"row": src_row, "cell": col}, "inherited": inherited, "horizontal_span": 1}


def _table(tid, doc, blk, title, header1, header2, data, second_is_header=True, second_inherited=True):
    """header2가 진짜 머리글이면(second_is_header) 셀 출처 줄을 1로(세로 병합) — 아니면 2로(데이터 줄)."""
    hdr = [{"row": 1, "cells": [_cell(i + 1, t, 1) for i, t in enumerate(header1)]},
           {"row": 2, "cells": [_cell(i + 1, t, 1 if second_is_header else 2, second_inherited and second_is_header)
                                for i, t in enumerate(header2)]}]
    rows = [{"row": 1, "cells": hdr[0]["cells"]}, {"row": 2, "cells": hdr[1]["cells"]}]
    rows += [{"row": 3 + i, "cells": [_cell(j + 1, v, 3 + i) for j, v in enumerate(vals)]} for i, vals in enumerate(data)]
    return {"table_id": tid, "document_id": doc, "title": title, "source_ref": {"document_id": doc, "body_block": blk},
            "header_rows": hdr, "rows": rows}


def make_src():
    pols = []

    def pol(pid, doc, path, title, body, blk, row, notes=None, terms=()):
        pols.append({"policy_id": pid, "document_id": doc, "section_path": path, "title": title, "source_text": body,
                     "source_notes": notes, "source_ref": {"body_block": blk, "row": row}, "term_ids": list(terms)})
    pol("p1", "d1", ["1. 가입", "1-1. 필수"], "필수항목", "이름과 연락처는 필수다.", 10, 1, notes="외부 시스템 값을 따름", terms=["t1"])
    pol("p2", "d1", ["1. 가입", "1-1. 필수"], "예외", "미성년자는 보호자 동의.\n둘째 줄", 10, 2)
    pol("p3", "d1", ["1. 가입", "1-1. 필수"], "예외", "외국인은 여권 번호.", 10, 3)
    pol("p4", "d1", ["2. 적립", "2-1. 스탬프형 챌린지"], "적립 기준", "스탬프형은 표를 따른다.", 20, 1)
    pol("p5", "d2", ["1. 주문", "1-1. 취소"], "취소", "결제 전엔 자유 취소.", 5, 1)
    left = [["1", "일반", "구매", "일", "A"], ["2", "일반", "방문", "일", "A"], ["3", "일반", "구매", "주", "A"], ["4", "확률", "구매", "일", "B"]]
    right = [["1", "1", "익일"], ["2", "1", "익일"], ["3", "3", "익일"], ["4", "1", "즉시"]]
    refs = [
        _table("tL", "d1", 30, "[참고] 스탬프형 챌린지 응모기회", ["No.", "유형", "미션", "단위", "상세"], ["No.", "유형", "미션", "단위", "상세"], left),
        _table("tR", "d1", 31, "[참고] 스탬프형 챌린지 응모기회", ["No.", "결과", "결과"], ["No.", "수량", "시점"], right,
               second_inherited=False),
        # 두 번째 줄이 머리글처럼 잡혔지만 실제론 데이터(셀 출처 줄 2, 병합 없음)
        _table("tS", "d2", 40, "[참고] 유형표", ["유형", "설명"], ["스탬프", "도장을 모음"], [["확률", "추첨"]], second_is_header=False),
    ]
    blocks = {
        "d1": [{"block": 1, "type": "paragraph", "text": "이 정책서는 가입과 적립을 다룬다."},
               {"block": 2, "type": "paragraph", "text": "1. 가입"}, {"block": 3, "type": "paragraph", "text": "1-1. 필수"},
               {"block": 4, "type": "paragraph", "text": ""}, {"block": 10, "type": "table", "text": "..."},
               {"block": 11, "type": "paragraph", "text": "적용 대상은 전 채널이다."}],
        "d2": [{"block": 1, "type": "paragraph", "text": "주문 정책서 서문."}, {"block": 5, "type": "table", "text": "..."}],
    }
    cases = [{"source_tables": ["tL", "tR"], "left_rows": [{"cells": [{"text": v} for v in l]}],
              "right_rows": [{"cells": [{"text": v} for v in r]}]} for l, r in zip(left, right)]
    return {
        "documents": [{"document_id": "d1", "domain": "회원 (통합)", "source_file": "회원.docx", "source_blocks_path": "x"},
                      {"document_id": "d2", "domain": "주문", "source_file": "주문.docx", "source_blocks_path": "y"}],
        "policies": pols,
        "terms": [{"term_id": "t1", "document_id": "d1", "name": "회원", "definition": "가입한 고객", "notes": "약관"},
                  {"term_id": "t2", "document_id": "d2", "name": "회원", "definition": "주문 고객", "notes": None}],
        "references": refs,
        "policy_references": [
            {"from_policy_id": "p2", "to_policy_id": "p1", "status": "uniquely_resolved"},
            {"from_policy_id": "p5", "to_policy_id": None, "status": "resolved_section", "target_document_id": "d1",
             "target_section": "1-1. 필수"},
            {"from_policy_id": "p5", "to_policy_id": None, "status": "resolved_section", "target_document_id": "d1",
             "target_section": "1-1. 필수"},   # 같은 참조 두 번
            {"from_policy_id": "p3", "to_policy_id": None, "status": "unresolved"},
        ],
        "review_items": [{"kind": "effective_date_scope"}],
        "reference_cases": cases,
        "blocks": blocks,
    }


def test_clean_source_passes_all_invariants():
    src = make_src()
    plan = vc.build_plan(src)
    assert vc.check_plan(src, plan) == []
    assert [p.name for p in plan.parts] == ["회원", "주문"]            # 끝 괄호 설명 제거
    assert len(plan.policies) == 5 and len(plan.terms) == 2 and len(plan.overviews) == 2


def test_body_and_remark_kept_verbatim_and_duplicate_titles_renamed():
    src = make_src()
    plan = vc.build_plan(src)
    by = {r.source_key: r for r in plan.policies}
    assert by["p1"].raw_body == "이름과 연락처는 필수다." and by["p1"].remark == "외부 시스템 값을 따름"
    assert by["p2"].policy_name == "예외 — 미성년자는 보호자 동의." and by["p3"].policy_name == "예외 — 외국인은 여권 번호."
    assert by["p1"].policy_name == "필수항목"
    assert [r.source_row for r in plan.policies if r.doc_id == "d1"] == [1, 2, 3, 4]


def test_header_rows_judged_by_merge_and_data_row_not_lost():
    src = make_src()
    tS = src["references"][2]
    assert vc.header_count(tS) == 1                       # 두 번째 줄은 데이터
    plan = vc.build_plan(src)
    small = next(t for t in plan.tables if t.title == "유형표")
    assert [r["cells"]["유형"] for r in small.rows] == ["스탬프", "확률"]


def test_pair_join_and_lossless_compression():
    plan = vc.build_plan(make_src())
    t = next(t for t in plan.tables if len(t.source_keys) == 2)
    assert len(t.rows) == 4
    dec = [c["name"] for c in t.columns if c["decisive"]]
    assert "미션" not in dec                                # 구매/방문이 결과를 안 바꿈
    view = vc.compressed_view(t)
    for r in t.rows:                                      # 압축본으로 찾으면 원표와 같은 결과
        k = tuple(r["cells"][c] for c in dec)
        res = tuple(r["cells"][c["name"]] for c in t.columns if c["role"] == "result")
        assert dict(view)[k] == res


def test_links_resolved_deduped_and_unresolved_reported():
    plan = vc.build_plan(make_src())
    kinds = [(l.from_type, l.relation, l.to_type) for l in plan.links]
    assert kinds.count(("policy", "refers", "section")) == 1             # 같은 참조 두 번 → 한 번
    sec = next(l for l in plan.links if l.to_type == "section" and l.from_type == "policy")
    assert sec.to_ref == "d1|1. 가입 > 1-1. 필수"                          # 2단계 이름 → 전체 경로
    assert ("policy", "uses", "term") in kinds
    assert any("p3" in r for r in plan.review) and any("effective_date_scope" in r for r in plan.review)
    stamp = [l for l in plan.links if l.from_type == "ref_table" and l.relation == "about"]
    assert len(stamp) == 1 and stamp[0].to_ref == "d1|2. 적립 > 2-1. 스탬프형 챌린지"   # 표 제목에 절 이름
    assert sum(1 for l in plan.links if l.relation == "appendix_of") == 2   # 모든 표는 문서 부록으로


def test_overview_collects_only_uncaptured_paragraphs():
    plan = vc.build_plan(make_src())
    ov = {o.doc_id: o.body.split("\n") for o in plan.overviews}
    assert ov["d1"] == ["이 정책서는 가입과 적립을 다룬다.", "적용 대상은 전 채널이다."]   # 절 제목·빈 줄 제외
    assert ov["d2"] == ["주문 정책서 서문."]


def _broken(mutate):
    src = make_src()
    plan = vc.build_plan(src)
    plan = copy.deepcopy(plan)
    mutate(plan)
    return vc.check_plan(src, plan)


def test_check_catches_changed_body():
    assert any("본문" in e for e in _broken(lambda p: setattr(p.policies[0], "raw_body", p.policies[0].raw_body + " ")))


def test_check_catches_lost_policy_and_duplicate_name():
    assert any("정책 개수" in e for e in _broken(lambda p: p.policies.pop()))

    def dup(p):
        p.policies[2].policy_name = p.policies[1].policy_name
    assert any("식별키 중복" in e for e in _broken(dup))


def test_check_catches_lost_or_added_table_cell():
    def drop_row(p):
        t = next(t for t in p.tables if len(t.source_keys) == 2)
        t.rows.pop()
    errs = _broken(drop_row)
    assert any("칸 값 불일치" in e for e in errs) and any("이어 붙인 행과 다름" in e for e in errs)

    def add_cell(p):
        p.tables[-1].rows[0]["cells"]["새 열"] = "지어낸 값"
    assert any("칸 값 불일치" in e for e in _broken(add_cell))


def test_check_catches_dangling_link_and_lost_overview_line():
    assert any("끊긴 연결" in e for e in _broken(lambda p: p.links.append(vc.LinkRec("policy", "p1", "refers", "policy", "없음"))))

    def cut(p):
        p.overviews[0].body = p.overviews[0].body.split("\n")[0]
    assert any("유실" in e for e in _broken(cut))

    def invent(p):
        p.overviews[0].body += "\n원문에 없는 문장"
    assert any("원문에 없는 줄" in e for e in _broken(invent))


def test_compress_property_random_tables():
    """무작위 결정표: 결과가 일부 열에만 달렸으면 그 열은 반드시 남고, 압축본은 무손실."""
    rng = random.Random(7)
    for _ in range(200):
        ncond = rng.randint(2, 5)
        cond = [f"c{i}" for i in range(ncond)]
        dep = rng.sample(cond, rng.randint(1, ncond))
        fn = {}
        rows = []
        for _ in range(rng.randint(5, 40)):
            cells = {c: rng.choice("abc") for c in cond}
            k = tuple(cells[c] for c in dep)
            cells["r"] = fn.setdefault(k, rng.choice("xyz"))
            rows.append({"row_no": len(rows) + 1, "cells": cells})
        keep, conflicts = vc.compress(rows, cond, ["r"])
        assert not conflicts
        m = {}
        for r in rows:
            assert m.setdefault(tuple(r["cells"][c] for c in keep), r["cells"]["r"]) == r["cells"]["r"]


def _src_with_refs(quotes):
    src = make_src()
    src["policies"].append({"policy_id": "q1", "document_id": "d1", "section_path": ["3. 사고", "3-1. 보고 기준"],
                            "title": "사건 기준 - 분실", "source_text": "분실 시 보고.", "source_notes": None,
                            "source_ref": {"body_block": 50, "row": 1}, "term_ids": []})
    src["policies"].append({"policy_id": "q2", "document_id": "d1", "section_path": ["3. 사고", "3-1. 보고 기준"],
                            "title": "사건 기준 - 상해", "source_text": "상해 시 보고.", "source_notes": None,
                            "source_ref": {"body_block": 50, "row": 2}, "term_ids": []})
    src["policy_references"] = [{"from_policy_id": "p1", "to_policy_id": None, "status": "unresolved",
                                 "target_document_id": None, "source_quote": q} for q in quotes]
    return src


def test_unresolved_reference_suggestions_only_when_unique():
    src = _src_with_refs(["<9-9. 스탬프형 챌린지>",     # 번호 틀림, 이름은 한 절과 일치 → 제안
                          "<사건 기준>",                # 같은 말로 시작하는 정책 2건이 모인 절 → 제안
                          "<예외>",                     # 제목 '예외' 정책은 있지만 한 절에 2건 → 제안
                          "<외부 체크리스트>"])          # 어디에도 없음 → 제안 안 함
    plan = vc.build_plan(src)
    sug = {l.to_ref for l in plan.links if l.status == "suggested"}
    assert "d1|2. 적립 > 2-1. 스탬프형 챌린지" in sug and "d1|3. 사고 > 3-1. 보고 기준" in sug
    assert any("외부 문서" in r for r in plan.review)
    assert vc.check_plan(src, plan) == []


def test_single_title_prefix_is_not_enough_to_suggest():
    src = _src_with_refs(["<가용재고>"])
    src["policies"].append({"policy_id": "q3", "document_id": "d1", "section_path": ["4. 재고", "4-8. 배분"],
                            "title": "가용재고와 배분", "source_text": "배분한다.", "source_notes": None,
                            "source_ref": {"body_block": 60, "row": 1}, "term_ids": []})
    plan = vc.build_plan(src)
    assert not [l for l in plan.links if l.status == "suggested"]       # 제목 하나뿐 → 사람에게
    assert any("가용재고" in r and "후보" in r for r in plan.review)


def test_compress_reports_conflict_and_keeps_all_columns():
    rows = [{"row_no": 1, "cells": {"a": "1", "r": "x"}}, {"row_no": 2, "cells": {"a": "1", "r": "y"}}]
    keep, conflicts = vc.compress(rows, ["a"], ["r"])
    assert keep == [] and conflicts          # 압축 안 함(표 통째) + 모순 보고


def test_whole_body_marker_is_the_same_everywhere():
    """원문 통째 표시값은 테스트 격리 때문에 여러 곳에 값으로 적혀 있다 — 하나라도 다르면 위험도·수정·정정이 v0을 옛 항목으로 본다."""
    root = Path(__file__).resolve().parents[1]
    for rel in ("service/policy/edit.py", "service/improvement/service.py"):
        assert f'PIPELINE_WHOLE = "{vc.PIPELINE_WHOLE}"' in (root / rel).read_text(encoding="utf-8"), rel
    assert f"p.pipeline_version = '{vc.PIPELINE_WHOLE}'" in (root / "service/policy/risk.py").read_text(encoding="utf-8")



def test_conflicting_decision_table_goes_to_review_not_blocking_load():
    """같은 조건에 결과가 둘인 표(원천 오류)는 압축하지 않고 사람 확인으로 — 적재 전체를 막지 않는다(리뷰 M4, 끝까지 돌려 확인)."""
    src = make_src()
    left = src["references"][0]
    no1, no4 = left["rows"][2]["cells"], left["rows"][5]["cells"]
    for i in range(1, len(no4)):                 # No.4의 조건을 No.1과 똑같이(No.는 그대로) → 같은 조건, 결과는 원래대로 다름
        no4[i]["text"] = no1[i]["text"]
    src["reference_cases"] = []                                  # 독립 대조는 이 시나리오와 무관
    plan = vc.build_plan(src)
    t = next(t for t in plan.tables if len(t.source_keys) == 2)
    assert not [c for c in t.columns if c["decisive"]]          # 압축 안 함
    assert any("결과가 둘 이상" in r for r in plan.review)
    assert vc.check_plan(src, plan) == []


def test_overview_line_found_in_another_document_is_not_dropped():
    """서문 줄이 다른 문서의 정책 글에 우연히 들어 있어도 이 문서 개요에서 빠지면 안 된다(리뷰 L1 — 실데이터 doc_03 한 줄이 이렇게 빠졌었다)."""
    src = make_src()
    src["policies"][0]["source_text"] += " 참고로 주문 정책서 서문."      # d1 정책 글에 d2 서문 줄과 같은 글
    plan = vc.build_plan(src)
    assert "주문 정책서 서문." in next(o for o in plan.overviews if o.doc_id == "d2").body
    assert vc.check_plan(src, plan) == []
