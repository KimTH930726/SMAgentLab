"""참고표·문서 개요를 채팅 문맥에 (정책서 v0 설계 §6·§8 #12, 2026-10-26) — 적재만 되고 채팅이 안 쓰던 두 데이터(쓰임 검사 30/30·14/14).

업계 방식: 표·문서는 **요약 벡터로 찾고 원본(압축본)을 LLM에 넘긴다**(multi-representation indexing — 찾는 표현과 읽는 표현을 나눔,
Dense X·LangChain MultiVector). 표 행 단위 검색은 v0 실험에서 정답 행 1위 2~3%라 버림(§3-2) — 표 하나를 통째로(결정 열 압축본).
결정 조건이 질문에 빠졌으면 되묻는다(§3-2 결론 "병목은 질문이 결정 조건을 다 말하지 않는 것") — 기존 채팅 되묻기 형식 그대로.

붙이는 규칙(10/26 골든 485문항 측정, eval_out/refchat/margin_1026 — 유사도만으론 못 가름: 0.6에서도 정책 질문 42/455에 표가 붙음):
- 참고표: 요약 유사도 ≥ REF_MIN_SIM 이고 같은 질문의 정책 서술 최고점 − REF_MARGIN 이상 → 정답 1위 23/30 · 엉뚱하게 붙음 7/455
- 되묻기: 표가 서술보다 높을 때만(여유 0) → 15/30 · 1/455 — 엉뚱한 되묻기는 답을 통째로 막아 문맥 잡음보다 비싸다
- 개요: 유사도 ≥ OVERVIEW_MIN_SIM 이고 서술 최고점 + OVERVIEW_MARGIN 이상 → 12/14 · 0/471
합성 문항 기준이라 실사용 질문으로 다시 잰다. 켜는 것은 설정(policy_ref_chat, 기본 꺼짐) — 정확도 게이트 통과 후 사람 결정.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional, Sequence

from core.database import get_conn

logger = logging.getLogger(__name__)

REF_MIN_SIM = 0.5
REF_MARGIN = 0.05          # 표 ≥ 서술 − 0.05
CLARIFY_MARGIN = 0.0       # 되묻기는 표 ≥ 서술
OVERVIEW_MIN_SIM = 0.5
OVERVIEW_MARGIN = 0.05     # 개요 ≥ 서술 + 0.05
# 표에 질문 조건에 맞는 줄이 없을 때 표의 다른 줄로 답을 지어내지 않게(10/26 측정: 답 없는 질문 2개에 표가 붙어 답해 버림)
TABLE_HINT = "질문의 조건에 맞는 줄이 이 표에 없으면 표에서 찾을 수 없다고 답할 것"
MAX_TABLE_CHARS = 8000     # 문맥에 넣을 표 글자 상한 — 넘으면 질문에 나온 조건값으로 행을 거르고, 그래도 넘으면 앞부분만(잘랐다고 표시)
CLARIFY_MAX_OPTIONS = 6    # 선택지가 이보다 많은 열은 버튼으로 안 묻는다(문맥의 표로 LLM이 답하게)
REF_SCOPE = "ref:"         # 되묻기에서 고른 값 — 분류 목록 범위(clarify_scope)와 섞이지 않게 앞에 붙인다


@dataclass
class RefTableHit:
    id: int
    title: str
    score: float
    block: str
    decisive: list[str] = field(default_factory=list)
    truncated: bool = False


@dataclass
class OverviewHit:
    id: int
    title: str
    score: float
    body: str


@dataclass
class RefResult:
    tables: list[RefTableHit] = field(default_factory=list)
    overviews: list[OverviewHit] = field(default_factory=list)
    clarify: Optional[dict] = None


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "").lower()


def mentioned(value: str, question: str) -> bool:
    """조건값이 질문에 나왔나 — 공백 무시 글자 포함. 한 글자 값("Y")은 잘못 걸리기 쉬워 안 본다."""
    v = _norm(value)
    return len(v) >= 2 and v in _norm(question)


def table_view(columns: list[dict], rows: list[dict]) -> tuple[list[str], list[list[str]], list[str]]:
    """표 → (보일 열, 행, 결정 열). 결정 열이 있으면 압축본(결정 열 + 결과 열, 같은 줄 하나로 — 적재 때 무손실 검사함, v0_convert.compress),
    없으면 통째(번호 열만 뺌)."""
    dec = [c["name"] for c in columns if c.get("decisive")]
    res = [c["name"] for c in columns if c.get("role") == "result"]
    if dec and res:
        cols = dec + res
        seen: dict = {}
        for r in rows:
            seen.setdefault(tuple(r.get(c, "") for c in cols), None)
        return cols, [list(k) for k in seen], dec
    cols = [c["name"] for c in columns if c.get("role") != "id"]
    return cols, [[r.get(c, "") for c in cols] for r in rows], []


def table_md(cols: list[str], rows: list[list[str]]) -> str:
    esc = lambda x: str(x or "").replace("|", "/").replace("\n", " ")   # noqa: E731
    return "\n".join(["| " + " | ".join(map(esc, cols)) + " |", "|" + "---|" * len(cols)] + ["| " + " | ".join(map(esc, r)) + " |" for r in rows])


def table_block(title: str, columns: list[dict], rows: list[dict], question: str) -> tuple[str, list[str], bool, list[list[str]], list[str]]:
    """문맥 블록 · 결정 열 · 잘림 여부 · 조건값으로 거른 행(잘리기 전 — 되묻기 선택지용) · 보일 열. 크면 질문에 나온 결정 조건값으로 행을 거른다(§6 "큰 표는 조건으로 행 거르기")."""
    cols, body, dec = table_view(columns, rows)
    idx = {c: i for i, c in enumerate(cols)}
    text = table_md(cols, body)
    truncated = False
    if len(text) > MAX_TABLE_CHARS and dec:
        kept = body
        for c in dec:
            hit = [r for r in kept if mentioned(r[idx[c]], question)]
            if hit:
                kept = hit
        body = kept
        text = table_md(cols, body)
    filtered = body   # 되묻기 선택지는 잘리기 전 행으로 — 잘린 행으로 세면 고를 값이 버튼에 없었다(297행 표, 리뷰 M2)
    if len(text) > MAX_TABLE_CHARS:
        lines = text.splitlines()
        out, n = [], 0
        for ln in lines:
            if n + len(ln) > MAX_TABLE_CHARS:
                break
            out.append(ln)
            n += len(ln) + 1
        truncated = True
        body = body[:max(0, len(out) - 2)]
        text = "\n".join(out)
    head = f"[참고표: {title}]" + (f" (결과를 정하는 조건: {', '.join(dec)})" if dec else "")
    head += f" — {TABLE_HINT}"
    if truncated:
        head += f" — 표가 커서 앞 {len(body)}행만. 조건을 더 알려 주면 정확한 행을 볼 수 있음"
    return f"{head}\n{text}", dec, truncated, filtered, cols


def clarify_for_table(hit_title: str, dec: list[str], cols: list[str], rows: list[list[str]], question: str) -> Optional[dict]:
    """결정 조건 중 질문에 안 나온 열이 있고, 그 열 값에 따라 결과가 갈리면 되묻는다(첫 열 하나만 — 한 번에 하나씩).
    선택지 = 그 열의 값(지금 남은 행 기준), 고르면 "질문 (열: 값)"으로 다시 묻는다. 값이 너무 많으면 묻지 않는다(LLM이 표로 답함)."""
    idx = {c: i for i, c in enumerate(cols)}
    for c in dec:
        values = sorted({r[idx[c]] for r in rows if str(r[idx[c]]).strip()})
        if len(values) < 2 or any(mentioned(v, question) for v in values):
            continue
        if len(values) > CLARIFY_MAX_OPTIONS:
            return None
        options = [{"label": v, "scope": f"{REF_SCOPE}{v}", "query": f"{question} ({c}: {v})"} for v in values]
        return {"message": f"'{hit_title}' 기준으로 답이 '{c}'에 따라 달라요. 어느 쪽인가요?", "options": options}
    return None


async def search_refs(ns_ids: Sequence[int], query_vec: list[float], question: str, *, top_narrative: float,
                      allow_clarify: bool = True, overviews: bool = False) -> RefResult:
    """참고표 1개·개요 1개까지(붙이는 규칙은 모듈 설명). 실패는 호출부가 로그로 남기고 없이 진행."""
    out = RefResult()
    if not ns_ids:
        return out
    vec = str(query_vec)
    async with get_conn() as conn:
        t = await conn.fetchrow(
            """SELECT id, title, columns, 1 - (embedding <=> $1::vector) AS s FROM ref_table
               WHERE status = 'active' AND namespace_id = ANY($2::int[]) AND embedding IS NOT NULL
               ORDER BY embedding <=> $1::vector LIMIT 1""", vec, list(ns_ids))
        o = await conn.fetchrow(
            """SELECT id, title, body, 1 - (embedding <=> $1::vector) AS s FROM policy_doc_overview
               WHERE status = 'active' AND namespace_id = ANY($2::int[]) AND embedding IS NOT NULL
               ORDER BY embedding <=> $1::vector LIMIT 1""", vec, list(ns_ids)) if overviews else None
        rows = None
        if t and float(t["s"]) >= REF_MIN_SIM and float(t["s"]) >= top_narrative - REF_MARGIN:
            rows = [r["cells"] if isinstance(r["cells"], dict) else json.loads(r["cells"])
                    for r in await conn.fetch("SELECT cells FROM ref_table_row WHERE ref_table_id = $1 ORDER BY row_no, id", t["id"])]
    if rows is not None:
        columns = t["columns"] if isinstance(t["columns"], list) else json.loads(t["columns"])
        block, dec, truncated, body, cols = table_block(t["title"], columns, rows, question)
        score = float(t["s"])
        out.tables.append(RefTableHit(id=t["id"], title=t["title"], score=score, block=block, decisive=dec, truncated=truncated))
        if allow_clarify and dec and score >= top_narrative + CLARIFY_MARGIN:
            out.clarify = clarify_for_table(t["title"], dec, cols, body, question)
    if o and float(o["s"]) >= OVERVIEW_MIN_SIM and float(o["s"]) >= top_narrative + OVERVIEW_MARGIN:
        out.overviews.append(OverviewHit(id=o["id"], title=o["title"], score=float(o["s"]), body=o["body"] or ""))
    return out


def context_block(r: RefResult) -> str:
    parts = [t.block for t in r.tables] + [f"[정책서 개요: {o.title}]\n{o.body}" for o in r.overviews]
    return "\n\n".join(parts)


async def ref_key_map(conn) -> dict:
    """참고표·개요 원천 키 → (종류, id) — 골든 측정(eval_golden_v0·eval_answers)이 같이 쓴다. 이어 붙인 표는 키가 "a+b" —
    골든은 통째로 적어 통째·조각 둘 다 같은 표로(조각 키가 다른 표 키와 같으면 통째 키가 이긴다 — 통째를 나중에 넣음)."""
    out: dict = {}
    rows = await conn.fetch("SELECT id, source_key FROM ref_table WHERE status = 'active' AND source_key IS NOT NULL")
    for r in rows:
        for k in r["source_key"].split("+"):
            out.setdefault(k, ("ref_table", r["id"]))
    for r in rows:
        out[r["source_key"]] = ("ref_table", r["id"])
    for r in await conn.fetch("SELECT id, source_key FROM policy_doc_overview WHERE status = 'active' AND source_key IS NOT NULL"):
        out[r["source_key"]] = ("overview", r["id"])
    return out
