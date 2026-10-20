"""정책서 v0 변환기 — 팀원이 DOCX 14개를 구조화한 데이터를 우리 저장 단위로 옮긴다(docs/tech/policy-v0-design.md §4~5).

LLM 없음, 결정적. DB도 안 만진다(적재는 scripts/load_policy_v0.py) — 그래서 원천과 결과를 나란히 놓고 불변식으로 검사할 수 있다.
  정책 → 정책 1건 = 저장 1건(본문 통째, 비고는 따로). 같은 절·같은 제목이면 "제목 — 본문 첫 줄".
  용어 → 문서의 정책 파트 용어집.
  참고표 → 원표 그대로(머리글 줄 수는 셀 병합 정보로 판단). 같은 제목의 연달아 나온 No. 표 두 개는 No.로 이어 붙인 결정표
           (왼쪽 = 조건, 오른쪽 = 결과).
  서문 → 표 밖 문단 중 절 제목도, 정책·용어 본문도 아닌 것을 문서 순서대로 모은 문서 개요(문서마다 1건).
  관계 → 정책→정책(대상이 하나로 정해진 명시 참조), 정책→절(절 단위 참조), 정책→용어(본문이 쓰는 용어), 참고표→절(놓인 자리).
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

GLOSSARY_SOURCE = "policy_doc"   # 용어 출처(화면 "정책서") — 10/19 "출처 표기" 결정, 10/20 파트 이름 칸 대신 출처 칸
# policy_item.pipeline_version — 정책 1건 = 조각 1개(원문 통째, LLM 분해 없음). 위험도·수정·정정 경로가 이 값으로 옛 분해 항목과 가른다
PIPELINE_WHOLE = "v0_whole"
_NAME_LINE_MAX = 40
_NW = re.compile(r"[\s\W_]+", re.UNICODE)


def _norm(s: Optional[str]) -> str:
    return _NW.sub("", (s or "").lower())


@dataclass
class Part:
    doc_id: str
    name: str
    source_file: str


@dataclass
class PolicyRec:
    source_key: str
    doc_id: str
    category_path: list[str]
    title: str
    policy_name: str            # 같은 절·같은 제목이면 "제목 — 본문 첫 줄"
    raw_body: str
    remark: Optional[str]
    source_file: str
    source_row: int             # 문서 안 순서(1부터) — 목록 정렬용


@dataclass
class TermRec:
    source_key: str
    doc_id: str
    term: str
    description: str


@dataclass
class TableRec:
    source_keys: list[str]      # 원천 표 id(이어 붙였으면 2개)
    doc_id: str
    title: str
    category_path: list[str]    # 놓인 절(바로 앞 정책의 절)
    columns: list[dict]         # {name, role: condition|result|id|None, decisive: bool}
    rows: list[dict]            # {row_no, cells: {열 이름: 값}}
    source_file: str

    @property
    def source_key(self) -> str:
        return "+".join(self.source_keys)


@dataclass
class OverviewRec:
    doc_id: str
    title: str
    body: str
    source_file: str


@dataclass
class LinkRec:
    from_type: str
    from_ref: str               # 원천 키(정책 pol_…·용어 term_…·표 source_key) 또는 절 키 "doc_id|L1 > L2"
    relation: str
    to_type: str
    to_ref: str
    status: str = "active"      # suggested = 규칙으로 추정한 연결(원문 번호 오류 등) — 사람이 확인하기 전까지 확정 아님


@dataclass
class Plan:
    parts: list[Part] = field(default_factory=list)
    policies: list[PolicyRec] = field(default_factory=list)
    terms: list[TermRec] = field(default_factory=list)
    tables: list[TableRec] = field(default_factory=list)
    overviews: list[OverviewRec] = field(default_factory=list)
    links: list[LinkRec] = field(default_factory=list)
    review: list[str] = field(default_factory=list)      # 사람이 확인할 것(원천의 미해결 참조·날짜 범위 등)
    notes: list[str] = field(default_factory=list)       # 변환이 한 일(이름 바꿈 등) — 숨기지 않고 보여준다


def _jl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_source(src: Path) -> dict:
    """팀원 산출물 폴더를 읽는다. sources/ 는 서문(표 밖 문단)을 찾는 데만 쓴다."""
    out = {
        "documents": json.loads((src / "documents.json").read_text(encoding="utf-8")),
        "policies": _jl(src / "policies.jsonl"),
        "terms": _jl(src / "terms.jsonl"),
        "references": _jl(src / "references.jsonl"),
        "policy_references": _jl(src / "policy-references.jsonl"),
        "review_items": _jl(src / "review-items.jsonl") if (src / "review-items.jsonl").exists() else [],
        # 팀원이 따로 이어 붙인 결정표 행 — 우리 머리글 판단·이어 붙이기를 독립으로 대조하는 기준(적재엔 안 씀)
        "reference_cases": _jl(src / "reference-cases.jsonl") if (src / "reference-cases.jsonl").exists() else [],
        "blocks": {},
    }
    for d in out["documents"]:
        p = src / d["source_blocks_path"]
        if p.exists():
            out["blocks"][d["document_id"]] = json.loads(p.read_text(encoding="utf-8"))["blocks"]
    return out


def part_name(domain: str) -> str:
    """영역 이름에서 끝의 괄호 설명을 뗀다 — "온라인스토어 (통합커머스)" → "온라인스토어"(사용자 결정 10/19). 이름은 나중에 바꿀 수 있다."""
    return re.sub(r"\s*\([^)]*\)\s*$", "", domain).strip() or domain


def _first_line(body: str) -> str:
    line = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")
    return line if len(line) <= _NAME_LINE_MAX else line[:_NAME_LINE_MAX].rstrip() + "…"


def build_policies(src: dict, plan: Plan) -> None:
    files = {d["document_id"]: d["source_file"] for d in src["documents"]}
    ps = sorted(src["policies"], key=lambda p: (p["document_id"], p["source_ref"].get("body_block") or 0,
                                                 p["source_ref"].get("row") or 0))
    same = Counter((p["document_id"], tuple(p["section_path"]), p["title"]) for p in ps)
    order: Counter = Counter()
    for p in ps:
        order[p["document_id"]] += 1
        key = (p["document_id"], tuple(p["section_path"]), p["title"])
        name = p["title"] if same[key] == 1 else f"{p['title']} — {_first_line(p['source_text'])}"
        plan.policies.append(PolicyRec(
            source_key=p["policy_id"], doc_id=p["document_id"], category_path=list(p["section_path"]), title=p["title"],
            policy_name=name, raw_body=p["source_text"], remark=(p.get("source_notes") or "").strip() or None,
            source_file=files[p["document_id"]], source_row=order[p["document_id"]]))
    # 첫 줄까지 같아 아직 겹치면 순번을 붙인다(조용히 덮어쓰지 않게)
    names = Counter((r.doc_id, tuple(r.category_path), r.policy_name) for r in plan.policies)
    seen: Counter = Counter()
    for r in plan.policies:
        k = (r.doc_id, tuple(r.category_path), r.policy_name)
        if names[k] > 1:
            seen[k] += 1
            r.policy_name = f"{r.policy_name} ({seen[k]})"
    renamed = sum(1 for r in plan.policies if r.policy_name != r.title)
    if renamed:
        plan.notes.append(f"같은 절·같은 제목 정책 {renamed}건 이름 = '제목 — 본문 첫 줄'")


def build_terms(src: dict, plan: Plan) -> None:
    dup = [k for k, n in Counter((t["document_id"], t["name"]) for t in src["terms"]).items() if n > 1]
    for doc, name in dup:   # 한 문서 용어표에 같은 이름이 두 번 — 적재기는 관리 화면의 중복 방지를 거치지 않으니 여기서 알린다
        plan.review.append(f"용어 '{name}'이 {doc} 용어표에 두 번 이상 — 정의가 다르면 담당자 확인")
    for t in src["terms"]:
        notes = (t.get("notes") or "").strip()
        desc = t["definition"] + (f" (비고: {notes})" if notes else "")   # 옛 엑셀 용어 시트와 같은 형식(service.py)
        plan.terms.append(TermRec(t["term_id"], t["document_id"], t["name"], desc))


# ── 참고표 ──────────────────────────────────────────────────────────────────

def header_count(ref: dict) -> int:
    """머리글 줄 수. 원천은 위 두 줄을 머리글로 잡았지만, 두 번째 줄이 진짜 머리글이면 첫 줄과 세로로 합쳐진 칸이 있다
    (셀의 출처 줄이 1 = 위 칸이 내려온 것). 그런 칸이 하나도 없으면 두 번째 줄은 데이터다(실측: 유형표 2개·확률형 회수 왼쪽 표)."""
    hrs = ref.get("header_rows") or []
    if len(hrs) < 2:
        return len(hrs)
    second = hrs[1]["cells"]
    merged = any(c.get("inherited") or (c.get("source") or {}).get("row") == hrs[0]["row"] for c in second)
    return 2 if merged else 1


def column_names(ref: dict, h: int) -> list[str]:
    hrs = ref["header_rows"][:h]
    width = max(len(r["cells"]) for r in ref["rows"]) if ref["rows"] else len(hrs[0]["cells"])
    names = []
    for ci in range(width):
        parts: list[str] = []
        for r in hrs:
            t = r["cells"][ci]["text"].strip() if ci < len(r["cells"]) else ""
            if t and (not parts or parts[-1] != t):
                parts.append(t)
        names.append(" > ".join(parts) or f"열{ci + 1}")
    seen: Counter = Counter()
    out = []
    for n in names:                       # 같은 이름이 여러 열이면 순번(병합 머리글 아래 칸들)
        seen[n] += 1
        out.append(n if names.count(n) == 1 else f"{n} ({seen[n]})")
    return out


def data_rows(ref: dict, h: int) -> list[tuple[int, list[str]]]:
    rows = sorted(ref["rows"], key=lambda r: r["row"])[h:]
    return [(r["row"], [c["text"] for c in r["cells"]]) for r in rows]


def _is_no(name: str) -> bool:
    return _norm(name) in ("no", "번호", "순번")


def _strip_no(name: str) -> str:
    return _norm(re.sub(r"^[\dIVXⅠ-Ⅹ]+([-.]\d+)*\.?\s*", "", name))


def _table_section(src: dict, ref: dict) -> list[str]:
    """표와 이어지는 절 — 절 이름(번호 뗀 것)이 표 제목에 들어 있는 절이 문서 안에 딱 하나일 때만(예: "스탬프형 챌린지").
    위치로는 못 정한다: 실측(10/20) 참고표는 전부 문서 끝 부록에 모여 있어 "바로 앞 절"은 엉뚱한 절이 된다.
    못 정하면 빈 목록 — 문서(파트)의 부록으로만 연결한다."""
    title = _norm(ref["title"])
    paths = {tuple(p["section_path"]) for p in src["policies"] if p["document_id"] == ref["document_id"]}
    cands = set()
    for path in paths:
        for n in range(len(path), 0, -1):          # 깊은 절부터
            name = _strip_no(path[n - 1])
            if len(name) >= 3 and name in title:
                cands.add(path[:n])
                break
    return list(next(iter(cands))) if len(cands) == 1 else []


def compress(rows: list[dict], cond: list[str], result: list[str]) -> tuple[list[str], list[tuple]]:
    """결과를 정하는 조건 열만 남긴다(뺄 수 있는 열을 앞에서부터 빼 봄 — 뺀 뒤에도 "조건 → 결과"가 하나로 정해지면 뺀다).
    돌려주는 것: (결정 열, 모순 목록 = 조건 전부가 같은데 결과가 다른 경우). 모순이 있으면 압축하지 않는다(결정 열 없음 = 표 통째로,
    모순은 사람 확인 목록으로 — 결정 열을 조건 전부로 두면 무손실 검사가 정의상 실패해 적재 전체가 막혔다, 리뷰 M4)."""
    def key(r, cols):
        return tuple(r["cells"].get(c, "") for c in cols)

    full: dict = {}
    conflicts = []
    for r in rows:
        k, v = key(r, cond), key(r, result)
        if full.setdefault(k, v) != v:
            conflicts.append(k)
    if conflicts:
        return [], conflicts
    keep = list(cond)
    for c in cond:
        trial = [x for x in keep if x != c]
        m: dict = {}
        if all(m.setdefault(key(r, trial), key(r, result)) == key(r, result) for r in rows):
            keep = trial
    return keep, []


def compressed_view(table: TableRec) -> list[tuple[tuple, tuple]]:
    """압축본(파생) — (결정 열 값, 결과 값) 고유 목록. 결정 열이 없는 표는 빈 목록(통째로 쓴다)."""
    dec = [c["name"] for c in table.columns if c.get("decisive")]
    res = [c["name"] for c in table.columns if c.get("role") == "result"]
    if not dec:
        return []
    seen: dict = {}
    for r in table.rows:
        seen.setdefault(tuple(r["cells"].get(c, "") for c in dec), tuple(r["cells"].get(c, "") for c in res))
    return list(seen.items())


def build_tables(src: dict, plan: Plan) -> None:
    files = {d["document_id"]: d["source_file"] for d in src["documents"]}
    refs = sorted(src["references"], key=lambda r: (r["document_id"], r["source_ref"].get("body_block") or 0))
    used: set[str] = set()
    for i, ref in enumerate(refs):
        if ref["table_id"] in used:
            continue
        h = header_count(ref)
        cols = column_names(ref, h)
        nxt = refs[i + 1] if i + 1 < len(refs) else None
        pair = (nxt is not None and nxt["document_id"] == ref["document_id"] and nxt["title"] == ref["title"]
                and _is_no(cols[0]) and _is_no(column_names(nxt, header_count(nxt))[0]))
        title = re.sub(r"^\[참고\]\s*", "", ref["title"]).strip()
        section = _table_section(src, ref)
        if not pair:
            rows = [{"row_no": rn, "cells": {cols[ci]: v for ci, v in enumerate(vals) if ci < len(cols)}}
                    for rn, vals in data_rows(ref, h)]
            plan.tables.append(TableRec([ref["table_id"]], ref["document_id"], title, section,
                                        [{"name": c, "role": None, "decisive": False} for c in cols], rows,
                                        files[ref["document_id"]]))
            used.add(ref["table_id"])
            continue
        h2 = header_count(nxt)
        cols2 = column_names(nxt, h2)
        left = {vals[0].strip(): vals for _, vals in data_rows(ref, h) if vals}
        right = {vals[0].strip(): vals for _, vals in data_rows(nxt, h2) if vals}
        lcols, rcols = cols[1:], [c if c not in cols[1:] else f"{c} (결과)" for c in cols2[1:]]
        rows = []
        order = list(left) + [k for k in right if k not in left]
        for k in order:
            lv, rv = left.get(k), right.get(k)
            cells = {cols[0]: (lv or rv)[0]}     # No. 원문 그대로(왼쪽 우선)
            cells.update({c: (lv[ci + 1] if lv and ci + 1 < len(lv) else "") for ci, c in enumerate(lcols)})
            cells.update({c: (rv[ci + 1] if rv and ci + 1 < len(rv) else "") for ci, c in enumerate(rcols)})
            rows.append({"row_no": len(rows) + 1, "cells": cells})   # 순번 — 원문 No.는 칸에 그대로(숫자 No.와 순번이 겹치지 않게, 리뷰 L6)
            if lv is None or rv is None:
                plan.review.append(f"참고표 '{title}' No.{k}: {'조건' if lv is None else '결과'} 쪽 행 없음(짝 안 맞음)")
        dec, conflicts = compress(rows, lcols, rcols)
        for k in conflicts[:5]:
            plan.review.append(f"참고표 '{title}': 같은 조건에 결과가 둘 이상 {k[:3]}…")
        columns = ([{"name": cols[0], "role": "id", "decisive": False}]
                   + [{"name": c, "role": "condition", "decisive": c in dec} for c in lcols]
                   + [{"name": c, "role": "result", "decisive": False} for c in rcols])
        plan.tables.append(TableRec([ref["table_id"], nxt["table_id"]], ref["document_id"], title, section, columns, rows,
                                    files[ref["document_id"]]))
        used.update({ref["table_id"], nxt["table_id"]})
        plan.notes.append(f"참고표 '{title}': 조건표+결과표를 No.로 이어 붙임({len(rows)}행), 결과를 정하는 조건 열 {len(dec)}/{len(lcols)}개")


# ── 서문 ────────────────────────────────────────────────────────────────────

def _captured_texts(src: dict, doc_id: str) -> list[str]:
    """이 문서의 정책·용어·표 제목 글(항목마다 따로). 전 문서를 한 문자열로 이으면 다른 문서의 글이나 두 정책의 경계에 우연히 걸린
    서문 줄이 "이미 담김"으로 보여 조용히 빠졌다(리뷰 10/20 L1) — 같은 문서 안, 한 항목 안에 있을 때만 담긴 것으로 본다."""
    return ([_norm(p["title"] + " " + p["source_text"] + " " + (p.get("source_notes") or ""))
             for p in src["policies"] if p["document_id"] == doc_id]
            + [_norm(t["name"] + " " + t["definition"] + " " + (t.get("notes") or "")) for t in src["terms"] if t["document_id"] == doc_id]
            + [_norm(r["title"]) for r in src["references"] if r["document_id"] == doc_id])


def _is_captured(k: str, texts: list[str]) -> bool:
    return any(k in t for t in texts)


def overview_paragraphs(src: dict, doc_id: str) -> list[str]:
    """표 밖 문단 중 절 제목도 아니고 이 문서의 정책·용어·표 제목 글에도 없는 것(문서 순서)."""
    texts = _captured_texts(src, doc_id)
    heads = {_norm(s) for p in src["policies"] if p["document_id"] == doc_id for s in p["section_path"]}
    out = []
    for b in src["blocks"].get(doc_id, []):
        if b.get("type") == "table":
            continue
        t = (b.get("text") or "").strip()
        k = _norm(t)
        if not k or k in heads or _is_captured(k, texts):
            continue
        out.append(t)
    return out


def build_overviews(src: dict, plan: Plan) -> None:
    for d in src["documents"]:
        paras = overview_paragraphs(src, d["document_id"])
        if paras:
            plan.overviews.append(OverviewRec(d["document_id"], f"{part_name(d['domain'])} 정책서 개요", "\n".join(paras),
                                              d["source_file"]))
        elif d["document_id"] in src["blocks"]:
            plan.notes.append(f"{d['document_id']}: 서문 문단 없음")


# ── 관계 ────────────────────────────────────────────────────────────────────

def section_key(doc_id: str, path: list[str]) -> str:
    return f"{doc_id}|{' > '.join(path)}"


def _resolve_section(src: dict, doc_id: str, name: str) -> Optional[list[str]]:
    """참조가 가리키는 절 이름(어느 단계든) → 전체 경로. 문서 안에서 하나로 정해질 때만."""
    k = _norm(name)
    found = {tuple(p["section_path"][:i + 1]) for p in src["policies"] if p["document_id"] == doc_id
             for i, s in enumerate(p["section_path"]) if _norm(s) == k}
    return list(next(iter(found))) if len(found) == 1 else None


def _suggest_target(src: dict, doc_id: Optional[str], quote: str) -> tuple[Optional[list[str]], str]:
    """원천이 못 푼 참조("<2-1. 사이렌오더>")를 같은 문서 안에서 추정 — 하나로 정해질 때만 돌려준다(여럿이면 사람에게).
    ① 번호 뗀 이름이 같은 절이 하나 → 원문 번호 오류로 보고 그 절(실측: POS 문서는 사이렌오더가 5-1인데 "2-1"로 적음)
    ② 그 이름으로 시작하는 정책 제목들이 한 절에 모여 있음 → 그 절(실측: "긴급/일반 사건사고 기준 - 고객분실" 등)"""
    if not doc_id:
        return None, "문서 모름"
    name = re.sub(r"^[<\s]*", "", quote).rstrip("> ").strip()
    key = _strip_no(name)
    if len(key) < 2:
        return None, "이름 없음"
    paths = {tuple(p["section_path"]) for p in src["policies"] if p["document_id"] == doc_id}
    same = {path[:i + 1] for path in paths for i, s in enumerate(path) if _strip_no(s) == key}
    if len(same) == 1:
        return list(next(iter(same))), "절 번호 불일치, 이름 일치"
    if len(same) > 1:
        return None, f"같은 이름 절 {len(same)}곳"
    # 제목 하나가 우연히 같은 말로 시작하는 건 근거가 약하다(실측: "가용재고와 재고배분 배치" 하나로 엉뚱한 절을 제안) — 2건 이상 모인 절만
    starts = Counter(tuple(p["section_path"]) for p in src["policies"] if p["document_id"] == doc_id and _norm(p["title"]).startswith(key))
    grouped = {path for path, n in starts.items() if n >= 2}
    if len(grouped) == 1:
        return list(next(iter(grouped))), "이 이름으로 시작하는 정책들이 모인 절"
    if len(grouped) > 1:
        return None, f"후보 절 {len(grouped)}곳"
    loose = {path for path in paths if any(key in _strip_no(s) for s in path)} | {
        tuple(p["section_path"]) for p in src["policies"] if p["document_id"] == doc_id and key in _norm(p["title"])}
    if loose:
        return None, "후보 " + " / ".join(sorted(" > ".join(x) for x in loose))[:200]
    return None, "정책서 안에 없음(외부 문서로 보임)"


def build_links(src: dict, plan: Plan) -> None:
    pol = {p["policy_id"]: p for p in src["policies"]}
    term_ids = {t["term_id"] for t in src["terms"]}
    seen: set = set()

    def add(link: LinkRec) -> bool:          # 원천에 같은 참조가 두 번 적힌 경우(같은 문장 두 번 언급) 한 번만
        k = (link.from_type, link.from_ref, link.relation, link.to_type, link.to_ref)
        if k in seen:
            return False
        seen.add(k)
        plan.links.append(link)
        return True

    pending = []
    for r in src["policy_references"]:      # 1차: 원천이 푼 참조(확정)
        if r["status"] == "uniquely_resolved" and r.get("to_policy_id") in pol:
            add(LinkRec("policy", r["from_policy_id"], "refers", "policy", r["to_policy_id"]))
            continue
        path = (_resolve_section(src, r["target_document_id"], r["target_section"])
                if r["status"] == "resolved_section" and r.get("target_section") else None)
        if path:
            add(LinkRec("policy", r["from_policy_id"], "refers", "section", section_key(r["target_document_id"], path)))
        else:
            pending.append(r)
    for r in pending:                       # 2차: 못 푼 참조는 추정 — 확정 연결과 같은 대상이면 이미 있으니 확인할 것도 없다
        frm = pol.get(r["from_policy_id"])
        doc = r.get("target_document_id") or (frm["document_id"] if frm else None)
        guess, why = _suggest_target(src, doc, r.get("source_quote") or "")
        if guess:
            if add(LinkRec("policy", r["from_policy_id"], "refers", "section", section_key(doc, guess), status="suggested")):
                plan.review.append(f"정책 {r['from_policy_id']} 참조 {r.get('source_quote')} → '{' > '.join(guess)}' 제안({why}) — 확인 필요")
        else:
            plan.review.append(f"정책 {r['from_policy_id']} 참조 {r.get('source_quote')} 대상 못 찾음({why})")
    for p in src["policies"]:
        for t in p.get("term_ids") or []:
            if t in term_ids:
                add(LinkRec("policy", p["policy_id"], "uses", "term", t))
    for t in plan.tables:
        add(LinkRec("ref_table", t.source_key, "appendix_of", "domain", t.doc_id))
        if t.category_path:
            add(LinkRec("ref_table", t.source_key, "about", "section", section_key(t.doc_id, t.category_path)))
            plan.notes.append(f"참고표 '{t.title}' → 절 '{' > '.join(t.category_path)}'(표 제목에 절 이름)")
        else:
            plan.notes.append(f"참고표 '{t.title}' → 문서 부록으로만 연결(이어질 절을 하나로 못 정함)")
    for it in src["review_items"]:
        if it.get("kind") != "unresolved_explicit_reference":   # 미해결 참조는 위에서 이미 담음
            plan.review.append(f"원천 검토 항목: {it.get('kind')}")


def build_plan(src: dict) -> Plan:
    plan = Plan(parts=[Part(d["document_id"], part_name(d["domain"]), d["source_file"]) for d in src["documents"]])
    build_policies(src, plan)
    build_terms(src, plan)
    build_tables(src, plan)
    build_overviews(src, plan)
    build_links(src, plan)
    return plan


# ── 불변식 ──────────────────────────────────────────────────────────────────

def check_plan(src: dict, plan: Plan) -> list[str]:
    """위반 목록(빈 목록 = 통과). 원천과 결과를 나란히 놓고 전체로 검사 — 유실 0 · 중복 0 · 새로 생긴 내용 0 · 끊긴 연결 0."""
    errs: list[str] = []
    # 파트
    if len({p.name for p in plan.parts}) != len(plan.parts):
        errs.append("파트 이름 중복")
    docs = {d["document_id"] for d in src["documents"]}
    # 정책: 개수·원문·비고·경로 그대로, 파트 안 이름 유일
    srcp = {p["policy_id"]: p for p in src["policies"]}
    if len(plan.policies) != len(srcp) or {r.source_key for r in plan.policies} != set(srcp):
        errs.append(f"정책 개수/키 불일치 {len(plan.policies)} vs {len(srcp)}")
    for r in plan.policies:
        p = srcp.get(r.source_key)
        if p is None:
            errs.append(f"원천에 없는 정책 {r.source_key}")
            continue
        if r.raw_body != p["source_text"]:
            errs.append(f"본문 달라짐 {r.source_key}")
        if (r.remark or "") != (p.get("source_notes") or "").strip():
            errs.append(f"비고 달라짐 {r.source_key}")
        if r.category_path != list(p["section_path"]) or r.title != p["title"] or r.doc_id != p["document_id"]:
            errs.append(f"위치·제목 달라짐 {r.source_key}")
        if not r.policy_name.startswith(r.title):
            errs.append(f"정책명이 제목으로 시작 안 함 {r.source_key}")
    dup = [k for k, n in Counter((r.doc_id, tuple(r.category_path), r.policy_name) for r in plan.policies).items() if n > 1]
    if dup:
        errs.append(f"파트 안 식별키 중복 {len(dup)}건")
    # 용어
    srct = {t["term_id"]: t for t in src["terms"]}
    if {t.source_key for t in plan.terms} != set(srct) or len(plan.terms) != len(srct):
        errs.append("용어 개수/키 불일치")
    for t in plan.terms:
        s = srct.get(t.source_key)
        if s and (t.term != s["name"] or not t.description.startswith(s["definition"]) or t.doc_id != s["document_id"]):
            errs.append(f"용어 달라짐 {t.source_key}")
    # 참고표: 원천 표마다 정확히 한 번, 데이터 칸 값 그대로(유실·추가 0)
    covered = Counter(k for t in plan.tables for k in t.source_keys)
    for r in src["references"]:
        if covered[r["table_id"]] != 1:
            errs.append(f"원천 표 {r['table_id']}가 {covered[r['table_id']]}번 들어감")
    if set(covered) - {r["table_id"] for r in src["references"]}:
        errs.append("원천에 없는 표 키")
    refs = {r["table_id"]: r for r in src["references"]}
    for t in plan.tables:
        got = Counter(v for row in t.rows for v in row["cells"].values() if v != "")
        want: Counter = Counter()
        first = refs[t.source_keys[0]]
        left_rows = data_rows(first, header_count(first))
        for _, vals in left_rows:
            want.update(v for v in vals if v != "")
        if len(t.source_keys) == 2:   # 이어 붙인 표: No.는 한 칸으로 합쳐짐 — 오른쪽은 No.를 빼고, 왼쪽에 없는 No.만 더한다
            ref2 = refs[t.source_keys[1]]
            left_nos = {vals[0].strip() for _, vals in left_rows if vals}
            for _, vals in data_rows(ref2, header_count(ref2)):
                want.update(v for v in vals[1:] if v != "")
                if vals and vals[0].strip() not in left_nos and vals[0] != "":
                    want[vals[0]] += 1
        if +got != +want:
            missing, extra = +(want - got), +(got - want)
            errs.append(f"참고표 '{t.title}' 칸 값 불일치 — 빠짐 {sum(missing.values())} · 생김 {sum(extra.values())}")
        dec = [c["name"] for c in t.columns if c.get("decisive")]
        res = [c["name"] for c in t.columns if c.get("role") == "result"]
        if dec:   # 압축 무손실: 결정 열 값만으로 결과가 하나로 정해져야 한다
            m: dict = {}
            for row in t.rows:
                k = tuple(row["cells"].get(c, "") for c in dec)
                v = tuple(row["cells"].get(c, "") for c in res)
                if m.setdefault(k, v) != v:
                    errs.append(f"참고표 '{t.title}' 압축 손실")
                    break
    # 독립 대조: 팀원이 따로 이어 붙인 결정표 행과 우리 행이 같아야 한다(머리글 줄 판단·No. 잇기를 같은 함수로 재검사하지 않게)
    cases: dict = {}
    for c in src.get("reference_cases") or []:
        left = [x["text"] for x in c["left_rows"][0]["cells"]]
        right = [x["text"] for x in (c["right_rows"][0]["cells"] if c["right_rows"] else [])]
        cases.setdefault(tuple(c["source_tables"]), []).append(tuple(left + right[1:]))
    for t in plan.tables:
        if len(t.source_keys) == 2 and tuple(t.source_keys) in cases:
            ours = sorted(tuple(r["cells"].get(c["name"], "") for c in t.columns) for r in t.rows)
            if ours != sorted(cases[tuple(t.source_keys)]):
                errs.append(f"참고표 '{t.title}' 행이 원천의 이어 붙인 행과 다름({len(ours)} vs {len(cases[tuple(t.source_keys)])})")
    # 서문: 표 밖 문단은 절 제목·본문·개요 중 하나에 반드시
    ov = {o.doc_id: o for o in plan.overviews}
    for d in docs:
        blocks = [(b.get("text") or "").strip() for b in src["blocks"].get(d, []) if b.get("type") != "table"]
        heads = {_norm(s) for p in src["policies"] if p["document_id"] == d for s in p["section_path"]}
        lines = ov[d].body.split("\n") if d in ov else []
        if set(lines) - set(blocks):
            errs.append(f"개요에 원문에 없는 줄 {d}")              # 새로 생긴 내용 0
        line_keys = {_norm(x) for x in lines}
        texts = _captured_texts(src, d)
        lost = [t for t in blocks if _norm(t) and _norm(t) not in heads and not _is_captured(_norm(t), texts)
                and _norm(t) not in line_keys]
        if lost:
            errs.append(f"표 밖 문단 유실 {d}: {len(lost)}")       # 유실 0: 절 제목·본문·개요 중 어디엔가
        if len(lines) != len(set(lines)) and len(lines) - len(set(lines)) > sum(1 for x in Counter(blocks).values() if x > 1):
            errs.append(f"개요 줄 중복 {d}")
    # 관계: 끊긴 연결 0
    pol_keys = {r.source_key for r in plan.policies}
    term_keys = {t.source_key for t in plan.terms}
    table_keys = {t.source_key for t in plan.tables}
    sections = {section_key(r.doc_id, r.category_path[:n]) for r in plan.policies for n in range(1, len(r.category_path) + 1)}
    exists = {"policy": pol_keys, "term": term_keys, "ref_table": table_keys, "section": sections, "domain": docs}
    for ln in plan.links:
        for typ, ref in ((ln.from_type, ln.from_ref), (ln.to_type, ln.to_ref)):
            if ref not in exists.get(typ, set()):
                errs.append(f"끊긴 연결 {typ}:{ref}")
    if len({(l.from_type, l.from_ref, l.relation, l.to_type, l.to_ref) for l in plan.links}) != len(plan.links):
        errs.append("관계 중복")
    return errs
