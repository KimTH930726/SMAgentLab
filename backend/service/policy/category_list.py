"""탐색형 질문의 분류 목록 (2026-10-07) — "○○ 관련 정책 다 보여줘"에 그 분류의 정책을 빠짐없이 AI에게 준다.

검색(하이브리드·벡터·리랭커 무엇이든)은 "가장 관련 깊은 몇 개"를 고르는 도구라 "분류에 속한 것 전부"를 보장하지 못한다 — 실측(골든
탐색형 24문항): 정답 분류의 정책 중 AI 문맥에 들어간 비율 평균 42%, 작은 분류(3~8개)도 25~67%, 최종 답 정답 4/24(부분 18).
분류 전체가 문맥에 들어간 문항은 5개 중 4개가 정답이었다 → 고칠 곳은 "무엇을 가져오느냐".

분류 찾기(측정 후 결정, scripts 측정 기준): 질문에 분류 이름(번호 접두사 "4-2." 제거·띄어쓰기 무시)이 글자로 나오면 그중 가장 긴 이름,
없으면 질문과 의미가 가장 가까운 분류 1개(유사도 _MIN_SIM 이상일 때만). 같은 이름의 분류가 여러 곳(시트·단계)에 있으면 합친다.
측정: 포함률 42% → 91%, 가져온 것 중 정답 86%(임베딩만 쓰면 55%/58%, 합집합은 96%/74%). 탐색형으로 잘못 판별된 비탐색 1문항은
의미 유사도 0.570이라 _MIN_SIM(0.58)에서 걸러진다 — 표본이 작아 운영 중 다시 본다.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Optional, Sequence, Union

import logging

from core.database import get_conn
from service.policy.visibility import searchable_sql
from trustkit.answer.shaping import list_line_summary

logger = logging.getLogger(__name__)
_NUM = re.compile(r"^\s*[\d\-]+[.)]\s*")
_NON_WORD = re.compile(r"[\s\W_]+")
_AND = re.compile(r"\s+및\s+")
_MIN_SIM = 0.58
try:   # 측정용 환경값(pick_categories) — 잘못된 값이면 기본값으로(기동이 막히지 않게)
    _SPECIFIC_MARGIN = float(__import__("os").environ.get("CATEGORY_SPECIFIC_MARGIN", "0.15"))
except ValueError:
    __import__("logging").getLogger(__name__).warning("CATEGORY_SPECIFIC_MARGIN 값이 숫자가 아님 — 0.15 사용")
    _SPECIFIC_MARGIN = 0.15
_MAX_ITEMS = 40
# 재질문(2026-10-12, 사용자 결정 "20개로 시작해서 조절") — 주제만 말한 질문의 목록이 이보다 크면 어느 부분을 볼지 먼저 묻는다
try:
    CLARIFY_MAX_ITEMS = int(__import__("os").environ.get("CATEGORY_CLARIFY_MAX_ITEMS", "20"))
except ValueError:
    logger.warning("CATEGORY_CLARIFY_MAX_ITEMS 값이 숫자가 아님 — 20 사용")
    CLARIFY_MAX_ITEMS = 20
_CLARIFY_MIN_COVER = 0.8   # 하위 분류 선택지가 목록의 이 비율 이상을 덮어야 하위 분류로 묻는다
_CLARIFY_MAX_OPTIONS = 3   # 범위 선택지 수(+ "전부 보기" — 선택지 5개 이하가 업계 관행, 화면의 "기타(직접 입력)" 포함)
_CACHE_TTL = 600.0   # 정책 임포트·정정이 분류를 바꿀 수 있어 10분마다 다시 만든다(분류 수백 개 임베딩 ~수 초)


def norm_category(s: str) -> str:
    """"4-2.배송정책" == "배송 정책" — 번호 접두사·띄어쓰기·문장부호 무시. 띄어 쓴 접속어 "및"도 뺀다(10/21) — 분류 이름은
    "환불 및 교환"인데 질문은 "환불·교환"이라 글자 매칭이 안 돼, 더 짧은 "환불"·"교환" 분류를 따로 골랐다."""
    return _NON_WORD.sub("", _AND.sub(" ", _NUM.sub("", s or "")).lower())


@dataclass
class CategoryIndex:
    item_ids: dict[str, set[int]] = field(default_factory=dict)   # 정규화 이름 → 정책 id
    display: dict[str, str] = field(default_factory=dict)          # 정규화 이름 → 화면용 이름(번호 뺀 첫 표기)
    vectors: dict[str, list[float]] = field(default_factory=dict)
    # 같은 이름이 어느 위치(업무 경로)들에 있나 — 정규화 이름 → {경로("배송 > B2B 배송정책 > 공통"): 정책 id}. 재질문·범위 좁히기용
    places: dict[str, dict[str, set[int]]] = field(default_factory=dict)
    # 경로 → {바로 아래 하위 분류 경로: 정책 id}
    children: dict[str, dict[str, set[int]]] = field(default_factory=dict)
    built_at: float = 0.0


def pick_categories(question: str, names: list[str], sims: Optional[dict[str, float]]) -> list[str]:
    """글자 매칭(가장 긴 이름 — 같은 길이면 전부, 각 이름은 그 이름을 품은 더 구체적인 분류가 의미상 _SPECIFIC_MARGIN 이상 가까우면
    그쪽으로) → 없으면 의미 1위(_MIN_SIM 이상). 못 찾으면 빈 목록."""
    qn = norm_category(question)
    lex = [n for n in names if len(n) >= 2 and n in qn]
    if lex:
        longest = max(len(n) for n in lex)
        picked = [n for n in lex if len(n) == longest]
        # 글자로 나온 이름을 품은 더 구체적인 분류가 의미상 확연히 가까우면 그쪽(2026-10-12 진단: "주문서에 적용되는 쿠폰 정책 전부"가
        # "주문서"(0.60)를 골라 정답 "주문서 쿠폰정책"(0.85)을 놓침 — 질문 말투가 분류 이름과 글자로 안 맞아서). 품은 이름만 후보라 엉뚱한 분류로 안 샌다.
        # 이름마다 따로(같은 길이로 함께 잡힌 다른 이름은 그대로 — 리뷰 2026-10-12). 유사도가 없는 이름은 바꾸지 않는다.
        if sims:
            out = []
            for n in picked:
                if n in sims:
                    wider = [m for m in names if m != n and n in m and m in sims and sims[m] >= sims[n] + _SPECIFIC_MARGIN]
                    n = max(wider, key=lambda m: sims[m]) if wider else n
                if n not in out:
                    out.append(n)
            return out
        return picked
    if sims:
        best = max(sims, key=sims.get)
        if sims[best] >= _MIN_SIM:
            return [best]
    return []


# "주제 전체" 질문 판별(2026-10-07) — 키워드 판별기(전체·모든·목록…)는 골든셋 말투("~ 모든 정책을 한 번에")에 맞춰져, 실제 질문 기록의
# "재고 정책 좀", "쿠폰에 대해서 알려줘", "재고에 관련된 정책은 없나?"를 거의 다 놓쳤다(실측 144개 중 주제 전체 질문 약 8개 → 탐지 0).
# 질문이 "분류 이름 + 아래 일반 표현"만으로 이뤄졌으면 주제 전체를 묻는 것으로 본다 — "배송비 얼마야?"처럼 구체적으로 묻는 말
# (얼마·몇·언제·어떻게·가능 등)이 남으면 단건 질문. 분류 이름 자체가 데이터라 말투 키워드를 따로 늘리지 않아도 된다.
_GENERIC = sorted({
    "정책", "규칙", "규정", "기준", "내용", "관련된", "관련", "대해서", "대해", "대한", "알려줘", "알려주세요", "알려", "설명해줘", "설명해주세요",
    "설명", "정리해줘", "정리", "보여줘", "보여주세요", "목록", "종류", "전부", "전체", "모든", "모두", "어떤게", "어떤", "뭐가", "뭐있어", "있어",
    "있나", "없나", "있나요", "있어요", "있습니까", "주세요", "해줘", "좀", "들", "것", "거", "은", "는", "이", "가", "을", "를",
    "에", "의", "요", "다", "말해줘", "말해봐", "찾아줘", "찾아봐", "궁금해", "궁금합니다", "확인", "볼래", "보고싶어", "싶어", "싶은데",
}, key=len, reverse=True)


def is_topic_overview(question: str, names: list[str]) -> list[str]:
    """질문이 분류 이름(여러 개 가능) + 일반 표현뿐이면 그 분류 이름들, 아니면 빈 목록. 글자 매칭만(의미 추정 없음 — 정밀도 우선).
    다른 이름 안에 들어가는 이름은 뺀다("재고정책"이 있으면 "재고"는 안 씀). "상품 환불 정책은?"처럼 두 분류가 함께 나오면 둘 다 돌려주고,
    목록은 두 분류에 함께 속한 정책으로 좁힌다(find_category_list) — 큰 "상품" 분류 전체를 붙이지 않게."""
    qn = norm_category(question)
    lex = [n for n in names if len(n) >= 2 and n in qn]
    if not lex or len(qn) > 40:
        return []
    picked = [n for n in lex if not any(n != m and n in m for m in lex)]
    rest = qn
    for n in sorted(picked, key=len, reverse=True):
        rest = rest.replace(n, "", 1)
    for g in _GENERIC:
        rest = rest.replace(g, "")
    return sorted(picked) if len(rest) <= 1 else []


_cache: dict[tuple[int, ...], CategoryIndex] = {}
_label_vecs: dict[str, list[float]] = {}   # 분류 이름 → 임베딩. 이름이 같으면 벡터도 같아 색인을 다시 만들어도 재사용(TTL 무관)


def _key(ns_ids: Union[int, Sequence[int]]) -> tuple[int, ...]:
    return (ns_ids,) if isinstance(ns_ids, int) else tuple(sorted(set(ns_ids)))


def invalidate() -> None:
    """정책 가져오기·정정 승인 뒤 분류 색인을 비운다 — 안 비우면 TTL(10분) 동안 목록이 옛 정책 id를 가리켜, 목록 답 문맥 거르기(v2.138)가
    새 버전을 "목록 밖"으로 보고 뺄 수 있다(리뷰 10/13)."""
    _cache.clear()


async def _index(conn, key: tuple[int, ...]) -> CategoryIndex:
    """파트가 여러 개면(채팅 정책서 범위, 2026-10-20) 파트 이름을 맨 위 분류로 둔다 — 파트마다 같은 절 이름("1-1. 공통 기준")이 있어
    합치면 다른 업무가 한 분류로 섞인다. 파트 이름 자체도 분류가 된다("회원 정책 전부" → 회원 파트 전체)."""
    idx = _cache.get(key)
    if idx and time.time() - idx.built_at < _CACHE_TTL:
        return idx
    from shared.embedding import embedding_service
    idx = CategoryIndex(built_at=time.time())
    multi = len(key) > 1
    for r in await conn.fetch(
            "SELECT i.id, i.category_path, n.name AS part FROM policy_item i JOIN ops_namespace n ON n.id = i.namespace_id "
            f"WHERE i.namespace_id = ANY($1::int[]) AND {searchable_sql('i')}", list(key)):   # v0 검토 대기 제외(10/21)
        path_raw = ([r["part"]] if multi else []) + list(r["category_path"] or [])
        segs = [_NUM.sub("", c).strip() for c in path_raw]
        for i, cat in enumerate(path_raw):
            n = norm_category(cat)
            path = " > ".join(segs[:i + 1])
            if i + 1 < len(segs) and len(norm_category(segs[i + 1])) >= 2:
                idx.children.setdefault(path, {}).setdefault(" > ".join(segs[:i + 2]), set()).add(r["id"])
            if len(n) < 2:
                continue
            idx.item_ids.setdefault(n, set()).add(r["id"])
            idx.display.setdefault(n, _NUM.sub("", cat).strip())
            idx.places.setdefault(n, {}).setdefault(path, set()).add(r["id"])
    # 성능: 이름마다 한 번씩 임베딩하던 것을 새 이름만 묶어서 한 번에(정책 파트 14개 = 분류 수백 개, 첫 질문이 수십 초 걸렸을 것)
    new = [label for label in set(idx.display.values()) if label not in _label_vecs]
    for i in range(0, len(new), 64):
        for label, v in zip(new[i:i + 64], await embedding_service.embed_batch(new[i:i + 64])):
            _label_vecs[label] = v
    idx.vectors = {n: _label_vecs[label] for n, label in idx.display.items()}
    _cache[key] = idx
    return idx


@dataclass
class CategoryList:
    names: list[str]
    total: int
    lines: list[str]
    places: list[tuple[str, int]] = field(default_factory=list)     # 고른 이름이 있는 위치(경로, 정책 수) — 2곳 이상이면 업무가 다른 같은 이름
    children: list[tuple[str, int]] = field(default_factory=list)   # 위치가 하나일 때 그 바로 아래 하위 분류(경로, 정책 수)
    explicit_all: bool = False   # 질문이 "전부·모든·목록"으로 전체를 명시(키워드 판별) — 그러면 묻지 않고 다 보여준다
    item_ids: list[int] = field(default_factory=list)   # 목록에 든 정책 id — 목록 답의 문맥에서 목록 밖 검색 결과를 빼는 데(v2.138)
    scope_missed: bool = False   # 재질문에서 고른 범위를 못 찾아 질문 글로 다시 찾음(분류가 바뀐 경우 등) — 화면에 알린다

    def block(self) -> str:
        more = f" — 그중 {len(self.lines)}개만 표시" if self.total > len(self.lines) else ""
        # "전부·목록" 질문의 답 형식(사용자 결정 2026-10-12): 빠짐없이, 정책마다 "정책명 — 한 줄 요약". 세부 조건은 묻는 사람이 다음 질문으로
        # 같은 이름이 여러 업무에 있으면 업무(경로)별로 묶어 — 섞어서 한 분류처럼 보이지 않게(재질문 대신, v2.134)
        group = (f" 같은 이름이 여러 업무({', '.join(p for p, _ in self.places)})에 있으니 업무별로 묶어 제목을 달아라."
                 if len(self.places) >= 2 else "")
        # 잘린 목록이면 나머지가 있다는 것도 알리게(리뷰 10/21 — "아래에 없는 정책은 넣지 않는다"만 있으면 이게 전부인 것처럼 답한다)
        tail = (f" 이 분류엔 정책이 {self.total - len(self.lines)}개 더 있으니, 더 좁은 범위로 다시 물어보면 나머지도 볼 수 있다고 알린다."
                if self.total > len(self.lines) else "")
        # 10/21: 개수·한 줄 원칙을 못 박는다 — 목록형 오답의 절반이 한 정책의 세부 조건을 글머리·표 행으로 펼친 것(채점상 목록 밖 항목),
        # 다른 분류 정책을 끼워 넣은 것이었다. 줄마다 요약이 붙어 있으니 그걸 쓰게 한다
        return (f"[분류 목록: {', '.join(self.names)} — 정책 {self.total}개{more}. \"전부·목록\" 질문이면 아래 정책 {len(self.lines)}개를 "
                f"빠짐없이 이 순서대로, 정책 하나당 글머리 한 줄(\"정책명 — 한 줄 요약\", 요약은 각 줄의 — 뒤 내용 기준)로 답한다. "
                f"아래에 없는 정책은 목록에 넣지 않고, 한 정책의 세부 조건을 따로 글머리나 표 행으로 펼치지 않는다. "
                f"세부 조건은 궁금한 정책을 다시 물어보라고 안내.{group}{tail}]\n"
                # 번호를 붙인다 — 긴 목록(20개)에서 LLM이 중간을 건너뛰던 것(q077 등). 줄 내용은 그대로
                + "\n".join(f"{i}. {ln[2:] if ln.startswith('- ') else ln}" for i, ln in enumerate(self.lines, 1)))


_ALL_WORDS = ("전부", "모든", "전체", "모두", "목록", "한번에", "한눈에", "다보여", "싹다")


def says_all(question: str, names: list[str] = ()) -> bool:
    """질문이 전체를 명시했나("배송 정책 전부 보여줘") — 키워드 판별기를 안 거치고 주제 질문 경로로 온 것도 잡는다(2026-10-12 진단).
    분류 이름 안의 단어("전체취소", "목록관리")는 빼고 본다(리뷰 2026-10-12)."""
    qn = norm_category(question)
    for n in sorted((norm_category(x) for x in names), key=len, reverse=True):
        if n:
            qn = qn.replace(n, "")
    return any(w in qn for w in _ALL_WORDS)


def _narrow_by_parent(question: str, places: dict[str, set[int]]) -> dict[str, set[int]]:
    """같은 이름이 여러 위치에 있을 때 질문에 상위 이름이 나온 위치만("클레임 공통 정책" → 클레임 > 공통). 못 좁히면 그대로."""
    # 질문에 나온 상위 이름을 긴 것부터 지워 가며 찾는다 — "주문서 공통"에서 "주문"이 "주문서" 안에 있다고 잡히지 않게(리뷰 2026-10-12)
    qn = norm_category(question)
    for leaf in sorted({norm_category(p.split(" > ")[-1]) for p in places}, key=len, reverse=True):
        if leaf:
            qn = qn.replace(leaf, "")
    parents = sorted({norm_category(seg) for p in places for seg in p.split(" > ")[:-1] if len(norm_category(seg)) >= 2},
                     key=len, reverse=True)
    hit = set()
    for n in parents:
        if n in qn:
            hit.add(n)
            qn = qn.replace(n, "")
    kept = {p: ids for p, ids in places.items() if any(norm_category(seg) in hit for seg in p.split(" > ")[:-1])}
    return kept if 0 < len(kept) < len(places) else places


def clarify_for(cl: Optional[CategoryList], question: str) -> Optional[dict]:
    """재질문할지 — 대화가 끊기지 않게 좁은 규칙 하나만(LLM 없음, 2026-10-12). 아니면 None(평소대로 답).
    묻는 경우 = 분류 목록이 붙은 질문 중 **주제만 말하고**("재고 정책 좀" — "전부·모든·목록"으로 전체를 명시하지 않음) 정책이
    CLARIFY_MAX_ITEMS개 초과일 때. 선택지 = 업무가 여럿이면 업무별, 하나면 하위 분류별(정책 많은 순 최대 3) + "전부 보기".
    안 묻는 경우: 일반 질문(목록 없음), 전체를 명시한 질문(다 보여주되 업무별로 묶음), 작은 목록, 재질문에서 고른 질문(호출부가 막음).
    진단(10/12): 업무가 여럿이면 묻는 규칙은 골든 탐색형 6/24·실제 질문 1건(구체 질문)에 걸려 버림 — "배치 정책 전부"처럼 서비스마다
    같은 분류가 있는 건 다 보여주는 게 맞았다."""
    if not cl or cl.explicit_all or cl.total <= CLARIFY_MAX_ITEMS:
        return None
    name = cl.names[0] if cl.names else ""
    if len(cl.places) >= 2:
        opts, msg = cl.places, f"'{name}' 정책이 여러 업무에 {cl.total}개 있어요. 어느 쪽을 볼까요?"
    elif len(cl.children) >= 2 and sum(n for _, n in cl.children) >= cl.total * _CLARIFY_MIN_COVER:
        # 하위 분류들이 목록 대부분을 덮을 때만 — 분류에 바로 붙은 정책이 많으면 하위로 나눠 물어도 대부분이 "전부 보기"에만 있다(리뷰)
        opts, msg = cl.children, f"'{name}' 정책이 {cl.total}개로 많아요. 어느 부분을 볼까요?"
    else:
        return None
    opts = opts[:_CLARIFY_MAX_OPTIONS]
    options = [{"label": f"{p} ({n}개)", "scope": p, "query": f"{p} 정책 전부 보여줘"} for p, n in opts]
    options.append({"label": f"전부 보기 ({cl.total}개)", "scope": "*", "query": question})
    return {"message": msg, "options": options}


async def find_category_list(ns_ids: Union[int, Sequence[int]], question: str, query_vec: list[float], *,
                             keyword_nav: bool, scope: Optional[str] = None) -> Optional[CategoryList]:
    """대상 분류의 정책 목록(분류 경로 > 정책명: 본문 첫 줄). 두 경로:
    - keyword_nav(키워드 판별기 "전체·모든·목록…"가 잡음): 분류 이름 글자 매칭 → 없으면 의미 1위(_MIN_SIM 이상). 같은 이름은 합침
    - 그 외: 질문이 "분류 이름 + 일반 표현"뿐일 때만(is_topic_overview — "재고 정책 좀"). 여러 분류면 함께 속한 정책만
    실측(2026-10-07, 실제 질문 기록 103개 사람 라벨): 키워드만 재현 1/10·정밀도 33% → 결합 재현 6/10(분류가 있는 6개 전부)·
    새 규칙 오탐 0. 나머지 4개("사이렌 오더 정책은?" 등)는 그런 분류가 없어 목록 대상이 아님(검색으로 답함). 못 찾으면 None."""
    key = _key(ns_ids)
    if not key:
        return None
    async with get_conn() as conn:
        idx = await _index(conn, key)
        if not idx.item_ids:
            return None
        names = list(idx.item_ids)
        scoped = None
        if scope and scope != "*":
            # 재질문에서 고른 범위 — 경로로 바로 찾는다(질문 글을 다시 해석하지 않아 "클레임 > 공통"이 "클레임" 전체로 새지 않는다)
            for n, pl in idx.places.items():
                if scope in pl:
                    scoped = (n, pl[scope])
                    break
        if scope and scope != "*" and not scoped:
            logger.warning("재질문에서 고른 범위를 찾지 못함(분류가 바뀌었을 수 있음) — 질문 글로 다시 찾음: %s", scope)
        if scoped:
            picked, ids = [scoped[0]], sorted(scoped[1])
        elif keyword_nav:
            sims = {n: sum(a * b for a, b in zip(query_vec, v)) for n, v in idx.vectors.items()}
            picked = pick_categories(question, names, sims)
            sets = [idx.item_ids[n] for n in picked]
            ids = sorted(set().union(*sets)) if sets else []
        else:
            picked = is_topic_overview(question, names)
            sets = [idx.item_ids[n] for n in picked]
            both = set.intersection(*sets) if sets else set()
            ids = sorted(both or (min(sets, key=len) if sets else set()))
        if keyword_nav and not scoped:
            # 질문에 상위·하위 이름이 함께 나오면("클레임 공통 정책") 하위 위치로 — 글자 수로만 고르면 긴 상위("클레임" 39개)가 이겼다
            # (2026-10-12 테스트로 발견). 상위 이름이 그 위치의 조상인 경우만이라 엉뚱한 분류로 새지 않는다.
            qn = norm_category(question)
            lex_all = {n for n in names if len(n) >= 2 and n in qn}
            # 질문에 나온 더 긴 이름 안에 든 이름은 뺀다("환불교환"이 나왔으면 "환불"·"교환"은 아님 — is_topic_overview와 같은 규칙, 10/21)
            lex_all = {n for n in lex_all if not any(n != m and n in m for m in lex_all)}
            # 이미 고른 이름이 그 위치의 조상일 때만(고른 것보다 더 구체적일 때만) — "주문서 쿠폰정책"을 고른 걸 "주문서"로 되돌리지 않게
            specific = {p: (n, pid) for n in lex_all for p, pid in idx.places.get(n, {}).items()
                        if {norm_category(seg) for seg in p.split(" > ")[:-1]} & (set(picked) - {n})}
            # 서로 조상·자손인 위치만 깊은 쪽으로(배송 > 공통 / 배송 > B2B > 공통처럼 조상 관계가 아니면 둘 다 — 재리뷰 2026-10-12)
            specific = {p: v for p, v in specific.items() if not any(q.startswith(p + " > ") for q in specific)}
            if specific:
                picked = sorted({n for n, _ in specific.values()})
                ids = sorted(set().union(*(pid for _, pid in specific.values())))
        if not picked or not ids:
            return None
        if scoped:
            places = {scope: set(ids)}
        else:
            idset = set(ids)
            places = {p: pid & idset for n in picked for p, pid in idx.places.get(n, {}).items() if pid & idset}
            # 같은 이름이 자기 아래에 또 있으면("재고" > "재고") 조상 하나로 — 다른 업무가 아니다(실제 질문 진단 2026-10-12)
            places = {p: v for p, v in places.items() if not any(p.startswith(q + " > ") for q in places)}
            if len(places) >= 2:
                places = _narrow_by_parent(question, places)
                ids = sorted(set().union(*places.values()))
        children = []
        if len(places) == 1:
            only = next(iter(places))
            children = sorted(((c, len(cid & set(ids))) for c, cid in idx.children.get(only, {}).items() if cid & set(ids)),
                              key=lambda x: -x[1])
        part_sql = "n.name || ' > ' || " if len(key) > 1 else ""   # 여러 파트면 어느 파트 정책인지
        rows = await conn.fetch(
            f"SELECT {part_sql}array_to_string(i.category_path, ' > ') AS cat, i.policy_name, "
            "coalesce(i.raw_body, '') AS body "
            "FROM policy_item i JOIN ops_namespace n ON n.id = i.namespace_id "
            "WHERE i.id = ANY($1::int[]) ORDER BY n.name, i.category_path, i.source_row, i.id LIMIT $2", ids, _MAX_ITEMS)
    # 줄 = "경로 > 정책명 — 요약"(10/21) — 예전엔 본문 첫 줄(중앙값 15자, 대개 이름과 같은 제목)이라 LLM이 요약을 본문에서 다시
    # 만들며 세부 조건을 글머리·표 행으로 줄줄이 펼쳤다(채점에서 목록 밖 항목). 요약은 본문 앞쪽 줄을 그대로 이어 붙임(LLM 없음, trustkit)
    lines = [f"- {r['cat']} > {r['policy_name']} — {list_line_summary(r['policy_name'], r['body'])}" for r in rows]
    return CategoryList(names=[idx.display[n] for n in picked], total=len(ids), lines=lines, item_ids=list(ids),
                        places=sorted(((p, len(v)) for p, v in places.items()), key=lambda x: -x[1]), children=children,
                        explicit_all=not scoped and (bool(keyword_nav) or says_all(question, [idx.display[n] for n in picked])),
                        scope_missed=bool(scope and scope != "*" and not scoped))
