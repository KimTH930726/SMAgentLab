"""용어집 활용 재설계 (2026-10-06, v2.128) — 질문에 "실제로 나온" 용어만 찾아 쓴다.

예전(v2.x~v2.127): 질문 문장 전체 임베딩 ↔ 용어 *설명* 임베딩 최근접 1개가 0.5 이상이면 그 용어 이름을 검색어 뒤에 붙였다.
실측(골든셋 88): 매핑 79건 중 붙인 용어가 질문에 실제로 있던 건 6건뿐, 무관 질문 32개 중 13개도 매핑("오늘 날씨" →
"출고송신진행") — 엉뚱한 용어가 키워드·정책 파라미터·공통코드 검색어에 섞여 무관 질문에 근거가 생기고 LLM이 지어냈다.

실무 방식(Snowflake/Databricks 시맨틱 모델의 동의어+설명, Azure/Elasticsearch 동의어 맵, 엔터티 연결의 사전·별칭 일치)을 따라:
1) 질문에서 용어·동의어를 글자 그대로 찾는다(띄어쓰기·대소문자·문장부호 무시). 못 찾으면 아무것도 안 붙인다.
2) 찾은 용어의 이름·동의어로 키워드 검색만 넓힌다(벡터 검색은 원래 질문 그대로 — 호출부가 query_vec를 따로 넘김).
3) 찾은 용어의 설명은 LLM 문맥에 "용어 설명"으로 준다.
동의어는 사람이 등록하지 않는다(사용자 결정 2026-10-06) — 용어 등록·수정 시 LLM이 만들고, 실제 질문 기록에서 LLM이 사용자
표현을 뽑아 자동으로 붙인다. 질문할 때마다 LLM을 부르지 않는다(게이트웨이 30~150초 — 답변 대기가 두 배가 됨).
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from shared.json_utils import parse_json_array

logger = logging.getLogger(__name__)

MAX_MATCHES = 3                 # 한 질문에서 쓰는 용어 수 상한(긴 것부터) — 설명 블록이 문맥을 잡아먹지 않게
MAX_SYNONYMS_PER_TERM = 6
MIN_NORM_LEN = 2                # "주" 같은 한 글자 표현은 아무 데나 걸린다
MAX_NORM_LEN = 30
QUERY_MIN_EVIDENCE = 2          # 질문 기록에서 뽑은 표현은 서로 다른 질문 2건 이상에서 나와야 쓴다(한 번 나온 오추출 방지)
TERM_BATCH = 15
QUERY_BATCH = 20

_NON_WORD = re.compile(r"[\s\W_]+", re.UNICODE)


def norm(s: str) -> str:
    """비교용 정규화 — 소문자, 공백·문장부호·밑줄 제거. "기초 재고" == "기초재고", "APPUSER_SELECT_ROLE" == "appuserselectrole"."""
    return _NON_WORD.sub("", (s or "").lower())


@dataclass
class GlossaryEntry:
    term: str
    description: str
    synonyms: list[str] = field(default_factory=list)


@dataclass
class TermMatch:
    term: str
    description: str
    matched: str                # 질문에서 실제로 걸린 표현(용어 자체 또는 동의어)
    synonyms: list[str] = field(default_factory=list)


def find_terms(question: str, entries: list[GlossaryEntry], limit: int = MAX_MATCHES) -> list[TermMatch]:
    """질문에 글자 그대로 나온 용어·동의어. 긴 표현부터 잡고, 이미 잡힌 구간 안에 들어가는 짧은 표현은 버린다
    ("기초재고"가 잡히면 그 안의 "재고" 용어는 따로 안 씀). 같은 용어는 한 번만."""
    q = norm(question)
    if not q:
        return []
    candidates: list[tuple[int, int, GlossaryEntry, str]] = []   # (길이, 시작, 항목, 걸린 표현)
    for e in entries:
        for expr in [e.term, *e.synonyms]:
            n = norm(expr)
            if len(n) < MIN_NORM_LEN:
                continue
            start = q.find(n)
            if start >= 0:
                candidates.append((len(n), start, e, expr))
    candidates.sort(key=lambda c: (-c[0], c[1]))
    taken: list[tuple[int, int]] = []
    out: list[TermMatch] = []
    seen: set[str] = set()
    for length, start, e, expr in candidates:
        end = start + length
        if e.term in seen or any(s <= start and end <= t for s, t in taken):
            continue
        taken.append((start, end))
        seen.add(e.term)
        out.append(TermMatch(term=e.term, description=e.description, matched=expr, synonyms=list(e.synonyms)))
        if len(out) >= limit:
            break
    return out


def expansion_text(question: str, matches: list[TermMatch]) -> str:
    """키워드 검색용으로 넓힌 질문 — 질문에 *글자 그대로* 없는 용어 이름·동의어를 덧붙인다.
    비교는 띄어쓰기 포함 원래 표기로 한다: 질문이 "상품쿠폰", 문서가 "상품 쿠폰"이면 키워드 검색 토큰이 달라 못 찾으므로 용어 표기를
    붙여야 한다(2026-10-06 실측 골든 1문항 — 정규화 비교로 "이미 있음" 처리했다가 놓침)."""
    ql = question.lower()
    extra: list[str] = []
    seen: set[str] = set()
    for m in matches:
        for expr in [m.term, *m.synonyms]:
            e = expr.strip()
            if len(norm(e)) >= MIN_NORM_LEN and e.lower() not in ql and e.lower() not in seen:
                seen.add(e.lower())
                extra.append(e)
    return f"{question} {' '.join(extra)}" if extra else question


def definitions_block(matches: list[TermMatch]) -> str:
    """LLM 문맥 맨 앞에 붙는 용어 설명 — 근거 문서가 아니라 질문 해석용이라는 걸 명시."""
    if not matches:
        return ""
    lines = [f"- {m.term}: {m.description.strip()}" for m in matches if m.description and m.description.strip()]
    if not lines:
        return ""
    return "[용어 설명 — 질문에 나온 사내 용어의 뜻. 답의 근거는 아래 문서로]\n" + "\n".join(lines)


def clean_synonyms(term: str, raw) -> list[str]:
    """LLM이 낸 동의어 정리 — 용어 자신·너무 짧거나 긴 것·숫자뿐인 것·중복 제거, 상한."""
    if not isinstance(raw, list):
        return []
    t = norm(term)
    out: list[str] = []
    seen: set[str] = {t}
    for s in raw:
        if not isinstance(s, str):
            continue
        s = s.strip()
        n = norm(s)
        if len(n) < MIN_NORM_LEN or len(n) > MAX_NORM_LEN or n.isdigit() or n in seen:
            continue
        seen.add(n)
        out.append(s)
        if len(out) >= MAX_SYNONYMS_PER_TERM:
            break
    return out


# 자동 품질 게이트(사람 확인이 없으므로) — 표현 ↔ 용어 임베딩 유사도가 이 값 이상인 것만 남긴다. 2026-10-06 실측(맞음 12쌍·틀림 14쌍):
#   질문 기록분 0.85 — 틀림 0/14 통과, 맞음 8/12 유지(띄어쓰기·말 바꿈 변형). "환불→반품확정"(0.80) 같은 개념 건너뛰기를 막는다
#   용어 등록분 0.65 — 용어 설명을 보고 만든 거라 덜 엄격하게: "포스→POS"(0.67)는 살리고 "정산진행→구매확정"(0.51)은 버림
# 표본이 작아 운영 중 지워지는 표현을 보며 다시 맞춘다.
SYNONYM_MIN_SIMILARITY = {"llm_term": 0.65, "llm_query": 0.85}


async def gate_synonyms(pairs: list[tuple[str, str]], source: str, embed=None) -> list[bool]:
    """[(표현, 용어)] → 통과 여부. embed는 테스트용 주입(기본: 서비스 임베딩 모델, 정규화 벡터라 내적 = 코사인)."""
    if embed is None:
        from shared.embedding import embedding_service
        embed = embedding_service.embed
    th = SYNONYM_MIN_SIMILARITY[source]
    cache: dict[str, list[float]] = {}

    async def vec(s: str) -> list[float]:
        if s not in cache:
            cache[s] = await embed(s)
        return cache[s]
    out = []
    for expr, term in pairs:
        a, b = await vec(expr), await vec(term)
        out.append(sum(x * y for x, y in zip(a, b)) >= th)
    return out


_TERM_SYSTEM = (
    "너는 사내 IT 운영 용어 사전 편집자다. 용어마다, 실제 사용자가 질문할 때 그 용어 대신 쓸 법한 다른 표현"
    "(같은 뜻의 말, 줄임말, 한글/영문 표기, 띄어쓰기 변형, 흔한 구어 표현)을 최대 5개 제안한다.\n"
    "규칙: 그 용어와 뜻이 같거나 그 용어만 가리키는 표현만. 더 넓은 상위 개념(예: 주문, 상품, 고객)이나 관련은 있지만 다른 개념은 "
    "넣지 않는다. 확신이 없으면 빈 배열. 설명 없이 JSON 배열만 출력한다: "
    '[{"term": "용어", "synonyms": ["표현1", "표현2"]}]'
)


async def generate_term_synonyms(items: list[tuple[str, str]], llm, embed=None) -> dict[str, list[str]]:
    """(용어, 설명) 목록 → {용어: 동의어}(품질 게이트 통과분만). 배치마다 실패해도 나머지는 계속(실패는 경고 로그)."""
    raw = await _generate_term_synonyms_raw(items, llm)
    pairs = [(s, t) for t, syns in raw.items() for s in syns]
    keep = await gate_synonyms(pairs, "llm_term", embed) if pairs else []
    out: dict[str, list[str]] = {}
    for (s, t), ok in zip(pairs, keep):
        if ok:
            out.setdefault(t, []).append(s)
    if pairs:
        logger.info("용어 동의어 게이트: %d개 중 %d개 통과", len(pairs), sum(keep))
    return out


async def _generate_term_synonyms_raw(items: list[tuple[str, str]], llm) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for i in range(0, len(items), TERM_BATCH):
        batch = items[i:i + TERM_BATCH]
        names = {t for t, _ in batch}
        prompt = "\n".join(f"- 용어: {t} | 설명: {(d or '').strip()[:200]}" for t, d in batch)
        try:
            raw = await llm.generate_once(prompt, system=_TERM_SYSTEM)
            arr = parse_json_array(raw)
        except Exception as e:
            logger.warning("용어 동의어 생성 실패(배치 %d~%d, 건너뜀): %s", i, i + len(batch), e)
            continue
        for obj in arr:
            if isinstance(obj, dict) and obj.get("term") in names:
                syn = clean_synonyms(obj["term"], obj.get("synonyms"))
                if syn:
                    out[obj["term"]] = syn
    return out


_QUERY_SYSTEM = (
    "너는 사내 IT 운영 용어 사전 편집자다. 사용자 질문 목록과 용어집이 주어진다. 각 질문에서 용어집의 어떤 용어를 가리키지만 "
    "용어집에 적힌 이름과 다르게 쓴 표현을 찾는다.\n"
    "규칙: expression은 질문에 적힌 글자 그대로(조사 제외)의 짧은 명사구. term은 용어집에 있는 이름 그대로. 같은 개념의 다른 이름"
    "(띄어쓰기·줄임말·말 바꿈)일 때만 — 관련된 다른 상태·상위 개념·테이블·문장 조각은 넣지 않는다(예: '환불'은 '반품확정'이 아니다). "
    "용어 이름을 그대로 쓴 경우는 넣지 않는다. 없으면 빈 배열. 설명 없이 JSON 배열만 출력한다: "
    '[{"q": 질문번호, "expression": "질문 속 표현", "term": "용어집 용어"}]'
)


async def mine_query_expressions(
    questions: list[str], entries: list[GlossaryEntry], llm, embed=None,
) -> list[tuple[str, str, int]]:
    """질문 기록 → [(표현, 용어, 질문 인덱스)]. 검증: 표현이 그 질문에 실제로 있고, 용어가 용어집에 있고, 용어 이름·기존 동의어와
    다를 것(LLM이 지어낸 표현·이미 아는 표현은 버림) + 품질 게이트(임베딩 유사도 0.85 이상 — 개념 건너뛰기 차단)."""
    by_term = {e.term: e for e in entries}
    glossary_txt = "\n".join(f"- {e.term}: {(e.description or '').strip()[:80]}" for e in entries)
    found: list[tuple[str, str, int]] = []
    for i in range(0, len(questions), QUERY_BATCH):
        batch = questions[i:i + QUERY_BATCH]
        prompt = (f"[용어집]\n{glossary_txt}\n\n[질문]\n"
                  + "\n".join(f"{j + 1}. {q}" for j, q in enumerate(batch)))
        try:
            arr = parse_json_array(await llm.generate_once(prompt, system=_QUERY_SYSTEM))
        except Exception as e:
            logger.warning("질문 기록 용어 추출 실패(배치 %d~%d, 건너뜀): %s", i, i + len(batch), e)
            continue
        for obj in arr:
            if not isinstance(obj, dict):
                continue
            try:
                qi = int(obj.get("q")) - 1
            except (TypeError, ValueError):
                continue
            expr, term = str(obj.get("expression") or "").strip(), str(obj.get("term") or "").strip()
            e = by_term.get(term)
            if not (0 <= qi < len(batch)) or e is None:
                continue
            n = norm(expr)
            known = {norm(e.term), *(norm(s) for s in e.synonyms)}
            if len(n) < MIN_NORM_LEN or len(n) > MAX_NORM_LEN or n in known or n not in norm(batch[qi]):
                continue
            found.append((expr, term, i + qi))
    if not found:
        return found
    keep = await gate_synonyms([(expr, term) for expr, term, _ in found], "llm_query", embed)
    logger.info("질문 기록 표현 게이트: %d개 중 %d개 통과", len(found), sum(keep))
    return [f for f, ok in zip(found, keep) if ok]


async def load_entries(conn, ns_id: int) -> list[GlossaryEntry]:
    """이 파트의 용어집 + 쓸 수 있는 동의어(LLM 용어 생성분 전부 + 질문 기록분은 근거 QUERY_MIN_EVIDENCE건 이상).
    동의어 테이블이 아직 없으면(#69 전) 동의어 없이."""
    rows = await conn.fetch("SELECT id, term, description FROM rag_glossary WHERE namespace_id = $1", ns_id)
    syn: dict[int, list[str]] = {}
    try:
        for r in await conn.fetch(
            "SELECT s.glossary_id, s.synonym FROM rag_glossary_synonym s JOIN rag_glossary g ON g.id = s.glossary_id "
            "WHERE g.namespace_id = $1 AND NOT s.blocked AND (s.source <> 'llm_query' OR s.evidence_count >= $2) ORDER BY s.id",
            ns_id, QUERY_MIN_EVIDENCE,
        ):
            syn.setdefault(r["glossary_id"], []).append(r["synonym"])
    except Exception as e:  # asyncpg.UndefinedTableError — 배포 전 코드 경로(측정 스크립트 등)
        logger.warning("동의어 테이블 조회 실패(동의어 없이 진행): %s", e)
    return [GlossaryEntry(term=r["term"], description=r["description"] or "", synonyms=syn.get(r["id"], []))
            for r in rows]


async def save_synonyms(conn, glossary_id: int, synonyms: list[str], source: str) -> int:
    """동의어 저장. 같은 용어에 같은 표현(정규화 기준)이 있으면 근거 건수만 올린다(질문 기록분이 여러 번 나오면 활성화).
    사람이 지운(blocked) 표현은 그대로 막힌 채 남는다."""
    n = 0
    for s in synonyms:
        await conn.execute(
            "INSERT INTO rag_glossary_synonym (glossary_id, synonym, synonym_norm, source) VALUES ($1, $2, $3, $4) "
            "ON CONFLICT (glossary_id, synonym_norm) DO UPDATE SET evidence_count = rag_glossary_synonym.evidence_count + 1",
            glossary_id, s, norm(s), source,
        )
        n += 1
    return n


async def refresh_term_synonyms(glossary_ids: list[int], llm=None) -> int:
    """용어 등록·수정 뒤 — 그 용어들의 LLM 생성 동의어를 다시 만든다(설명이 바뀌었을 수 있음). 질문 기록에서 온 동의어는 유지.
    LLM이 실패한 용어는 기존 동의어를 지우지 않는다(일시 장애로 동의어가 사라지지 않게)."""
    from core.database import get_conn
    if llm is None:
        from service.llm.factory import get_llm_provider
        llm = get_llm_provider()
    async with get_conn() as conn:
        rows = await conn.fetch("SELECT id, term, description FROM rag_glossary WHERE id = ANY($1::int[])", glossary_ids)
    if not rows:
        return 0
    generated = await generate_term_synonyms([(r["term"], r["description"] or "") for r in rows], llm)
    saved = 0
    async with get_conn() as conn:
        async with conn.transaction():
            for r in rows:
                syn = generated.get(r["term"])
                if syn is None:
                    continue
                await conn.execute(
                    "DELETE FROM rag_glossary_synonym WHERE glossary_id = $1 AND source = 'llm_term' AND NOT blocked", r["id"])
                saved += await save_synonyms(conn, r["id"], syn, "llm_term")
    return saved


# 등록·수정 직후 동의어 생성 — 정책서 임포트처럼 용어가 한꺼번에 수십 개 들어오면 용어마다 게이트웨이를 부르지 않게 잠깐 모았다가
# TERM_BATCH개씩 처리한다. 실패해도 등록 자체엔 영향 없음(경고 로그만).
_pending: set[int] = set()
_worker: Optional[asyncio.Task] = None
_GATHER_SECONDS = 3.0


def schedule_synonym_refresh(glossary_id: int) -> None:
    global _worker
    _pending.add(glossary_id)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _worker is None or _worker.done():
        _worker = loop.create_task(_drain())


async def _drain() -> None:
    while _pending:
        await asyncio.sleep(_GATHER_SECONDS)
        ids = sorted(_pending)[:TERM_BATCH]
        _pending.difference_update(ids)
        try:
            n = await refresh_term_synonyms(ids)
            logger.info("용어 동의어 생성: 용어 %d개 → 동의어 %d건", len(ids), n)
        except Exception as e:
            logger.warning("용어 동의어 생성 실패(용어 %d개, 등록엔 영향 없음): %s", len(ids), e)
