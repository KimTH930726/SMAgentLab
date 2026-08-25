"""VOC 이메일 건별 분석 — 2단계 RAG 지식 파이프라인 (docs/email-analysis-channel-plan.md §2, §10).

기존 채팅 파이프라인(agents/knowledge_rag/agent.py)과 동일하게 용어 매핑 + 하이브리드
검색 + LLM 판단을 재사용하되, 대화 이력·SSE 스트리밍·시맨틱 캐시는 건별 배치 분석에
불필요해 제외한 단발성(single-shot) 버전이다.

관련성 사전 필터(v3.9): 모든 메일을 무조건 LLM에 태우면 VOC와 무관한 메일(스팸,
CC 참조, 사내 공지 등)까지 비용을 쓰고 Teams 알림 노이즈를 만든다. 그래서 검색·분석
단계를 check_relevance()/analyze_email() 두 함수로 분리했다 — 파이프라인이 먼저
check_relevance()로 임베딩+검색만 수행해 관련 지식과의 최고 유사도(top_score)를
구하고, 관리자가 설정한 임계치(email_relevance_min_score, §9) 이상일 때만
analyze_email()을 호출(이때 precomputed로 검색 결과를 넘겨 임베딩·검색 중복 방지)한다.
"""
import logging
import re
from dataclasses import dataclass
from typing import Optional

from agents.knowledge_rag.knowledge import retrieval
from agents.knowledge_rag.knowledge.retrieval import RetrievalResult
from service.llm.factory import get_llm_provider
from service.prompt.loader import get_prompt
from shared.embedding import embedding_service
from shared.json_utils import parse_json_object

logger = logging.getLogger(__name__)


@dataclass
class RelevanceCheck:
    mapped_term: Optional[str]
    results: list[RetrievalResult]
    context: str
    top_score: float
    # 반복 VOC 패턴 탐지(pattern_detection.py)가 지식 베이스 비교와 별개로 과거
    # VOC와도 비교할 때 재사용 — 이미 계산된 값을 그대로 넘겨 재임베딩하지 않는다.
    query_vec: list[float]

# ops_prompt(func_key='email_voc_analysis_system'/'email_voc_analysis_prompt')로
# 관리자가 "시스템 설정 > 프롬프트 관리"에서 동적으로 수정 가능 — 아래는 DB에 값이
# 없을 때(마이그레이션 전 등)만 쓰이는 폴백 기본값이다.
_DEFAULT_ANALYSIS_SYSTEM = """You are a VOC(Voice of Customer) triage expert for an IT operations team.
Given an internal support email and related knowledge base excerpts, classify the issue.
Always respond with valid JSON only."""

_DEFAULT_ANALYSIS_PROMPT = """아래는 사내 VOC(문의) 이메일과, 이와 관련해 검색된 참고 지식입니다.

[이메일 제목]
{subject}

[이메일 본문]
{body}

[수신 메일함 담당 파트]
{part}

[참고 지식]
{context}

다음을 판단해 JSON으로만 답하세요:
1. category: 다음 중 하나
   - "system_error": 시스템이 실제로 오작동했다는 구체적 근거가 있는 경우(예: 처리 이력이 누락됨, 정상 흐름이라면 안 나와야 할 오류가 발생함)
   - "user_mistake": 사용자가 절차를 잘못 따르거나 직접 조작을 잘못한 경우
   - "not_it_related": IT 시스템/기술적 오류에 대한 언급이나 정황이 전혀 없는 경우 — 상품 품질(맛, 신선도 등), 배송/배달 자체(지연, 파손), 가격·취소정책에 대한 불만처럼 순수 CS성 문의. 시스템은 설계/정책대로 정상 동작했는데 그 결과(예: 취소 가능 시간이 지나 취소가 거절됨)에 대한 불만인 경우도 여기로 분류하세요. "시스템이 오작동했다"는 구체적 근거 없이 그냥 화가 났다는 이유만으로 system_error로 분류하지 마세요. 이메일에 시스템/기술 이슈 언급이 아예 없다면 uncertain이 아니라 이 항목을 우선 고려하세요
   - "uncertain": system_error일 가능성이 있어 보이는데(오류·오작동 정황은 있지만) 정보가 부족해 확신할 수 없는 경우만 — "IT 업무와 무관해 보인다"는 이유로는 uncertain이 아니라 not_it_related를 쓰세요
2. severity: "low" / "medium" / "high" / "urgent" 중 하나 — 업무 영향도와 긴급성 기준
3. mismatch_flagged: 이메일 내용이 위 "수신 메일함 담당 파트"의 업무 영역과 명백히 다르면 true, 아니면 false
4. resolution_draft: category가 system_error일 때 참고 지식 기반 해결 방안 초안(2~3문장), 아니면 null
5. reasoning: 판단 근거 요약 (1문장)

[판단 예시 — 실제로 헷갈렸던 경계 사례]
예시 1:
제목: 배달이요
본문: 주문한 음료가 30분 넘게 늦게 배달됐습니다. 왜 이렇게 오래 걸리나요?
판단: 시스템이 오작동했다는 구체적 근거 없이 배송 자체가 늦은 것에 대한 불만 → category: not_it_related (system_error 아님)

예시 2:
제목: 주문 취소가 시스템에 반영이 안돼요
본문: 취소 버튼을 눌렀고 취소 완료 문자도 받았는데, 주문 내역에는 아직도 "배달 중"으로 뜨고 결제도 그대로 잡혀 있습니다.
판단: 정상 흐름이면 안 나와야 할 처리 누락이 구체적으로 확인됨 → category: system_error

예시 3:
제목: 로그인이 안돼요
본문: 비밀번호를 5번 연속 잘못 입력해서 계정이 잠겼습니다. 어떻게 풀 수 있나요?
판단: 시스템 오류가 아니라 사용자 본인의 조작(비밀번호 오입력)이 원인 → category: user_mistake

예시 4:
제목: 주문이 이상해요
본문: 결제는 됐는데 배송 정보가 계속 안 뜹니다. 시스템 오류인지 그냥 늦는 건지 모르겠어요.
판단: system_error일 가능성이 있어 보이지만 오작동 여부를 판단할 정보가 부족함 → category: uncertain

응답 형식 (JSON 객체만 반환):
{{"category": "...", "severity": "...", "mismatch_flagged": false, "resolution_draft": "...", "reasoning": "..."}}"""

_VALID_CATEGORIES = {"system_error", "user_mistake", "uncertain", "not_it_related"}
_VALID_SEVERITIES = {"low", "medium", "high", "urgent"}
_KNOWLEDGE_SNIPPET_LEN = 60
_MAX_KNOWLEDGE_REFS_SHOWN = 3  # Teams 카드에 다 보여주면 너무 길어져서 상위 N개만

_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
# 전화번호: 010-1234-5678, 02-1234-5678, +82-10-1234-5678, 82.10.2547.7280(국제
# 표기 서명란), "+82 10 5000 3985"(공백 구분) 등. 첫 그룹을 1~3자리로 제한해
# "2026-08-20" 같은 날짜(YYYY-MM-DD, 첫 그룹 4자리)를 오탐하지 않는다 — 만료일
# 등 날짜는 심각도 판단에 중요한 정보라 실수로 마스킹되면 안 된다.
_PHONE_RE = re.compile(r"(?<!\d)\+?\d{1,3}[.\-\s]\d{2,4}[.\-\s]\d{2,4}(?:[.\-\s]\d{4})?(?!\d)")
# 구분자 없는 긴 숫자(10자리 이상) — VOC 티켓 ID(예: "C202608150816"), SKU 코드
# (예: "9900000000339") 등. 형식이 명확한 IP/전화번호와 달리 "그냥 긴 숫자"를
# 게이트웨이가 민감정보로 오판하는 것으로 실사용 중 확인됨(거부 응답이 해당
# 숫자값 자체를 그대로 나열함). 날짜(YYYY-MM-DD)는 구분자로 끊겨 있어 10자리
# 연속이 안 되므로 오탐하지 않는다.
_LONG_ID_RE = re.compile(r"(?<!\d)\d{10,}(?!\d)")

# 이메일 전달/회신 체인의 시작을 표시하는 헤더 줄 — Outlook 기준 "보낸 사람:"/
# "발신:"/"From:", 또는 "-----Original Message-----" 구분선. 이게 나오는 지점부터는
# 새로 쓴 내용이 아니라 예전에 주고받은 메일을 그대로 인용한 것이므로, 관련성
# 검색·LLM 분석에는 그 앞부분("알짜 내용")만 있으면 충분하다 — 실측 확인(§7-13,
# 2026-08-21): 4만자짜리 이메일도 실제 새로 쓴 내용은 이 마커 이전 1~2천자 안에
# 다 들어있고, 그 뒤는 예전 메일이 반복·중첩되며 길어지는 구조였다.
_FORWARD_MARKER_RE = re.compile(
    r"^\s*(?:보낸\s*사람|발신|From)\s*[:：].*$|^-{5,}\s*(?:Original Message|원본 메일)\s*-{5,}",
    re.MULTILINE | re.IGNORECASE,
)


def _strip_forwarded_chain(text: str) -> str:
    """전달/회신 체인 마커 이전(새로 쓴 부분)만 남긴다. 마커가 없으면 원문 그대로.

    마커를 못 찾아도 안전하게 원문을 반환하므로, 마커가 없는 일반 메일(대부분의
    VOC 문의)에는 아무 영향이 없다 — 이 함수는 "전달된 긴 스레드"에만 실질적으로
    작용한다.
    """
    match = _FORWARD_MARKER_RE.search(text)
    return (text[:match.start()] if match else text).strip()


def _snippet(content: str, length: int = _KNOWLEDGE_SNIPPET_LEN) -> str:
    flat = " ".join(content.split())  # 줄바꿈/연속 공백 정리 — 카드에서 한 줄로 보이게
    return flat if len(flat) <= length else flat[:length] + "..."


def _mask_pii(text: str) -> str:
    """LLM에 넘기기 직전에만 IP/이메일/전화번호/긴 숫자 ID를 마스킹한다.

    실사용 중 발견: 인하우스 LLM 게이트웨이가 프롬프트에 IP·이메일·전화번호·
    구분자 없는 긴 숫자(티켓 ID, SKU 코드 등)가 섞이면 "민감 정보 포함" 사유로
    분석 없이 거부 응답을 반환한다(예: 서명란의 휴대폰 번호 하나만 있어도 전체
    거부). VOC 메일은 호스트 IP·CC 목록·서명란 연락처·업무 티켓 ID가 거의 항상
    섞여 있어 사실상 대부분의 실제 메일이 분석되지 못하고 있었다(§7 참고).
    DB 저장·Teams 알림에는 원본 그대로 쓰고, LLM 프롬프트에 넣는 텍스트에만
    이 함수를 적용한다.
    """
    text = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    text = _IP_RE.sub("[REDACTED_IP]", text)
    text = _PHONE_RE.sub("[REDACTED_PHONE]", text)
    text = _LONG_ID_RE.sub("[REDACTED_ID]", text)
    return text


async def check_relevance(namespace: str, subject: str, body: str) -> RelevanceCheck:
    """이메일과 등록된 지식 간 최고 유사도를 구한다 — LLM 호출 전 저비용 사전 필터용.

    analyze_email()의 앞부분(임베딩+용어매핑+하이브리드검색)과 동일한 로직이다.
    파이프라인이 관련성 게이트를 먼저 통과시킨 뒤 결과를 그대로 재사용할 수 있도록
    분리했다 — 그러지 않으면 임베딩·벡터검색이 이메일 1건당 두 번씩 돈다.
    """
    query_text = f"{subject}\n{_strip_forwarded_chain(body)}".strip()
    # embed()는 모델의 max_seq_length(128토큰)만 반영해 긴 이메일일수록 실제 내용을
    # 놓친다(§7-13, 2026-08-21 실측) — embed_long()은 여러 청크로 나눠 평균 풀링해
    # 훨씬 넓은 범위를 반영한다. 이메일 본문 특성상 여기서만 필요(채팅 질의는 보통
    # 짧아 embed()로 충분).
    query_vec = await embedding_service.embed_long(query_text)

    glossary_match = await retrieval.map_glossary_term(namespace, query_vec)
    mapped_term = glossary_match.term if glossary_match else None
    enriched_query = f"{query_text} {mapped_term}" if mapped_term else query_text

    defaults = retrieval.get_search_defaults()
    w_vector, w_keyword = defaults["default_w_vector"], defaults["default_w_keyword"]
    results = await retrieval.search_knowledge(namespace, query_vec, enriched_query, w_vector, w_keyword, int(defaults["default_top_k"]))
    context = retrieval.build_context(results)

    # r.final_score는 검색 순위용으로 (1 + base_weight)가 곱해져 있다(retrieval.py) —
    # base_weight 기본값이 1.0이라 사실상 거의 모든 지식의 점수가 2배로 부풀려짐.
    # 이 관련성 게이트는 "무관한 메일을 걸러내는 것"이 목적인데, final_score를 그대로
    # 쓰면 실제로는 완전 무관한 메일(순수 유사도 0.34~0.36 수준)도 임계치(0.35)를 가볍게
    # 넘어버려 필터가 사실상 작동하지 않는 게 실사용 중 발견됐다(예: "패스워드 만료
    # 안내" 메일이 "배달 중지 테이블" 문서와 0.7이 넘는 점수로 매칭). base_weight를 뺀
    # 가중합 원점수로 게이트를 걸어야 실제 의미적 유사도를 반영한다 — analyze_email()이
    # 인용할 지식을 고르는 랭킹(knowledge_min_score 비교)에는 base_weight 부스팅이 계속
    # 유효하므로 그쪽은 final_score를 그대로 둔다.
    top_score = max((w_vector * r.v_score + w_keyword * r.k_score for r in results), default=0.0)
    return RelevanceCheck(
        mapped_term=mapped_term, results=results, context=context, top_score=top_score, query_vec=query_vec,
    )


async def analyze_email(
    namespace: str,
    subject: str,
    body: str,
    *,
    part: str = "",
    user_credentials: Optional[dict] = None,
    precomputed: Optional[RelevanceCheck] = None,
) -> dict:
    """이메일 본문을 RAG로 분석해 분류/심각도/오배치 여부/해결방안 초안을 산출한다.

    precomputed: check_relevance() 결과를 이미 갖고 있으면 그대로 재사용해 임베딩·
    검색을 중복 수행하지 않는다(파이프라인의 정상 경로). 넘기지 않으면(관리자 화면의
    "분석 테스트" 등 단독 호출) 여기서 새로 계산한다.

    Returns:
        {"category", "severity", "mismatch_flagged", "knowledge_ref_ids",
         "knowledge_refs", "resolution_draft", "reasoning", "mapped_term"}

    knowledge_refs: LLM 판단의 실제 근거(어떤 지식이 어느 정도 유사도로 매칭됐는지)를
    사람이 바로 확인할 수 있도록 상위 N개만 요약해 담는다 — 관련지식 필터를 통과했더라도
    실제로는 무관한 지식이 우연히 매칭돼(예: 완전히 다른 도메인인데 "실패/오류" 같은
    일반 단어만 겹침) LLM이 엉뚱한 컨텍스트로 답을 만드는 경우가 실사용 중 실제로
    발견됐다 — Teams 알림에 근거를 노출하면 이런 오탐을 사람이 바로 알아챌 수 있다.
    """
    check = precomputed if precomputed is not None else await check_relevance(namespace, subject, body)
    mapped_term = check.mapped_term
    results = check.results
    context = check.context

    min_score = retrieval.get_thresholds()["knowledge_min_score"]
    matched = sorted((r for r in results if r.final_score >= min_score), key=lambda r: r.final_score, reverse=True)
    knowledge_ref_ids = [r.id for r in matched]
    knowledge_refs = [
        {"id": r.id, "snippet": _snippet(r.content), "score": round(r.final_score, 2)}
        for r in matched[:_MAX_KNOWLEDGE_REFS_SHOWN]
    ]

    system_prompt = await get_prompt("email_voc_analysis_system", _DEFAULT_ANALYSIS_SYSTEM)
    prompt_template = await get_prompt("email_voc_analysis_prompt", _DEFAULT_ANALYSIS_PROMPT)
    # 전달 체인을 잘라낸 뒤 마스킹 — 검색용 query_text와 동일한 전처리를 LLM
    # 프롬프트에도 적용해 토큰 비용과 무관한 CC 목록 노출을 줄인다(§7-13).
    prompt = prompt_template.format(
        subject=_mask_pii(subject or "(제목 없음)"),
        body=_mask_pii(_strip_forwarded_chain(body)),
        part=part or "(미지정)",
        context=_mask_pii(context) if context else "(관련 지식 없음)",
    )

    # 동시 실행 시 LLM 프로바이더가 일시적으로 실패하는 경우가 실제로 관측됨(부하 시
    # 응답 실패) — 1회 재시도로 일시적 오류는 흡수하고, 그래도 실패하면 아래에서
    # "정말 판단 불가"와 명확히 구분해 표시한다.
    parsed: dict = {}
    last_error: Optional[Exception] = None
    llm = get_llm_provider()
    for attempt in range(2):
        try:
            raw = await llm.generate_once(
                prompt=prompt, system=system_prompt, max_tokens=800,
                user_credentials=user_credentials,
            )
            parsed = parse_json_object(raw)
            last_error = None
            break
        except Exception as e:
            last_error = e
            logger.warning(
                "VOC 이메일 분석 실패 (시도 %d/2, %s): %s", attempt + 1, type(e).__name__, e,
            )

    category = parsed.get("category") if parsed.get("category") in _VALID_CATEGORIES else "uncertain"
    reasoning = parsed.get("reasoning")

    if last_error is not None:
        # LLM 호출 자체가 끝내 실패한 경우 — 이건 "판단해봤는데 애매함"이 아니라
        # "판단을 못 함"이므로, 심각도를 낮음으로 깔아뭉개면 실제 장애가 조용히
        # 묻힐 위험이 있다. 사람이 반드시 확인하도록 medium 이상으로 올려둔다.
        severity = "medium"
        reasoning = f"⚠️ LLM 호출 실패로 자동 분석을 완료하지 못했습니다({type(last_error).__name__}) — 수동 검토 필요"
    else:
        severity = parsed.get("severity") if parsed.get("severity") in _VALID_SEVERITIES else "low"

    return {
        "category": category,
        "severity": severity,
        "mismatch_flagged": bool(parsed.get("mismatch_flagged", False)),
        "knowledge_ref_ids": knowledge_ref_ids,
        "knowledge_refs": knowledge_refs,
        "resolution_draft": parsed.get("resolution_draft"),
        "reasoning": reasoning,
        "mapped_term": mapped_term,
    }
