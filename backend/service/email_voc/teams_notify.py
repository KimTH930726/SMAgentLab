"""VOC 분석 결과 Teams 알림 발송.

발송 채널은 Teams "Workflows" 웹훅으로 확정됐고, 호출 로직은 공용 아웃바운드 HTTP
레이어(shared/http_client.py)를 쓴다. 원래는 MCP 도구 에이전트의 실행기를 그대로 재사용했으나
(2026-09-08 이전), MCP 도구 기능 자체를 걷어내면서 그 실행기 중 "그냥 HTTP 호출" 부분만
shared로 이관했다 — 도구 레지스트리·LLM 파라미터 추출 같은 MCP 전용 개념과는 무관한, 순수
HTTP 클라이언트 재사용이라는 원래 설계 전제는 그대로 유지된다.

페이로드 포맷: 처음엔 Adaptive Card(attachments)로 만들었으나, 이미 검증된 사내 다른
프로젝트(playwrite/modules/teams_notifier.py)의 Power Automate "Workflows" 플로우가
단순 {"text": "..."} 페이로드(<br> 줄바꿈)로 동작하는 걸 확인해 동일 포맷으로 맞췄다 —
같은 웹훅 URL을 재사용하려면 그 플로우가 실제로 파싱하는 스키마를 따라야 한다.
(2026-08-19 재확인: Adaptive Card를 이 웹훅에 다시 보내봤더니 HTTP 202는 받지만
Teams에 아예 도착하지 않음 — 이 플로우는 트리거 body의 `text` 필드만 메시지로
전달하고 `attachments`(카드)는 무시/드롭하는 것으로 결론. 이 웹훅을 쓰는 한 진짜
카드형 레이아웃(컬럼/배경색/큰 폰트 등)은 불가능하고, HTML 서식이 들어간 텍스트가
현실적 최대치다. 진짜 Adaptive Card가 필요하면 이 웹훅과 별개로 "Post adaptive
card in a chat or channel" 액션을 쓰는 새 Power Automate 플로우 + 새 웹훅을
발급받아야 한다 — Power Automate 포털 작업이라 코드로는 할 수 없음.)

§3 Q6 결정: 온콜 자동 전화는 하지 않는다 — 심각도 "높음/긴급"은 멘션+강조 표시까지만
자동화하고, 실제 전화는 이 알림을 본 담당자가 수동으로 건다. oncall_contact_name은
표시 전용이며 자동 발신에는 쓰이지 않는다.
"""
import html
import logging
from typing import Optional

from shared.http_client import call_http

# 분석 실패 표시 — service.analyze_email이 reasoning 맨 앞에 붙이는 것과 같은 문구(일치는 테스트가 확인). 실패하면 분류가 uncertain으로
# 깔려 분류로는 "분석 실패"와 "분류상 초안 없음"을 못 가른다(11/05 리뷰).
LLM_FAILED_PREFIX = "⚠️ LLM 호출 실패"

# 초안 제목(근거 확인 결과별, 11/05) — None(확인 실패·옛 캐시)은 "없음"으로 단정하지 않고 미확인으로(대체 처리는 보이게, 리뷰 L2).
# 카드는 툴팁이 없어 뒤에 짧은 안내를 붙인다. 화면(VocEmailPanel DRAFT_LABEL)과 같은 이름.
DRAFT_TITLES = {True: "해결 방안 · 근거 확인됨", False: "AI 제안 · 참고 지식에 없음 — 확인 필요", None: "AI 제안 · 근거 미확인 — 확인 필요"}

logger = logging.getLogger(__name__)

_URGENT_SEVERITIES = {"high", "urgent"}
_SEVERITY_LABEL = {"low": "낮음", "medium": "보통", "high": "높음", "urgent": "긴급"}
_CATEGORY_LABEL = {
    "system_error": "시스템 오류", "user_mistake": "사용자 실수",
    "uncertain": "판단 보류", "not_it_related": "IT 무관",
}
# Power Automate "Workflows" 웹훅이 {"text": "..."}를 HTML로 렌더링한다(<br>가 실제로
# 줄바꿈되는 걸 실측 확인) — Teams 리치 텍스트가 지원하는 표준 태그(b/span style=color)로
# 제목/심각도를 강조한다. 4단계가 전부 구분되도록 — 예전엔 high/urgent가 같은 빨강이라
# 사실상 2단계로만 보였음(관리자 화면도 동일 문제 있어 VocEmailPanel.tsx의
# SEVERITY_COLOR도 같은 단계로 맞춰서 함께 수정함 — 어디서 봐도 같은 색=같은 심각도).
_SEVERITY_COLOR = {"low": "#6B7280", "medium": "#0891B2", "high": "#D97706", "urgent": "#DC2626"}
# 카드가 너무 길어지지 않도록 원문 발췌는 앞부분만 — reasoning(판단 요약)이 이미
# 핵심을 짚어주므로 발췌는 "실제로 뭐라고 썼는지"를 확인하는 보조 용도로 충분하다.
_MAX_BODY_EXCERPT = 300
# 섹션 사이 구분선 — <hr> 등 미검증 태그 대신, 이미 렌더링 확인된 span
# style=color(모듈 상단 §8 실측 기록 참고)만으로 만든 순수 텍스트 구분선이라
# 이 웹훅에서 깨질 위험이 없다.
_DIVIDER = '<span style="color:#D1D5DB">─────────────────────</span>'


def _esc(value: Optional[str]) -> str:
    """이메일 원문에서 온 값(subject/sender/본문 유래 텍스트)을 그대로 HTML 문자열에
    끼워넣으면 <, >, & 같은 문자가 섞였을 때 렌더링이 깨지거나 의도치 않은 태그로
    해석될 수 있다 — 항상 이스케이프 후 삽입한다."""
    return html.escape(value or "", quote=False)


def _section_title(text: str) -> str:
    return f'<h3 style="font-size:17px">{text}</h3>'


def build_teams_message(
    *, subject: str, sender: str, part: str, analysis: dict,
    body: str = "", oncall_contact_name: Optional[str] = None,
    pattern_info: Optional[dict] = None,
) -> dict:
    """§10 심각도 기반 포맷 — "제목/내용/방안" 3섹션을 블록 태그(h2/h3/ul/blockquote)로
    구분한다. teams_notifier.py와 동일하게 {"text": "..."} 단순 페이로드를 쓰되
    (Adaptive Card는 이 웹훅에서 무시되는 것으로 실측 확인 — 모듈 docstring 참고),
    Teams 커넥터 카드가 지원하는 표준 서식 태그(h1~h3/b/ul·li/blockquote/span
    style=color)로 최대한 시각적 구분을 준다.

    pattern_info: 호출부(pipeline.py)가 "이번 건에서 실제로 반복 패턴 알림을 실을지"
    이미 판단을 끝낸 뒤에만 채워서 넘긴다({"member_count", "window_days", "knowledge_found", "needs_review"}).
    반복 확정 전·배수 지점이 아닌 건·묶음 전부 IT 무관인 건은 pipeline.py가 애초에
    Teams 발송 자체를 하지 않으므로(2026-08-27 — 매 건 알림이 노이즈였다는 실사용
    피드백) 이 함수는 "표시할지 말지"가 아니라 "이미 표시하기로 정해진 걸 어떻게
    그릴지"만 담당한다. 처음엔 "🔁 반복 패턴 감지"를 완전히 별도의 Teams 메시지로
    만들었으나, 실사용 피드백("두 개로 찢지 말고 하나의 흐름으로 녹여라")에 따라
    개별 VOC 카드 하나에 반복 여부를 한 줄 얹고, 해결방안도 그 안에서 같이 보여주는
    지금 방식으로 되돌렸다.
    """
    severity = analysis.get("severity", "low")
    category = analysis.get("category", "uncertain")
    urgent = severity in _URGENT_SEVERITIES
    color = _SEVERITY_COLOR.get(severity, _SEVERITY_COLOR["low"])
    header = "🚨 긴급 VOC 알림" if urgent else "VOC 분석 알림"

    # reasoning은 LLM이 "판단 근거"로 생성한 문장이라 원문 그 자체가 아니다 — 원문이
    # 아니라는 실사용 피드백에 따라, 이미 전처리(전달체인 제거)된 본문 앞부분을
    # 그대로 발췌해 보여준다. LLM을 추가로 호출하지 않으므로 비용 증가 없음.
    body_excerpt = body.strip()[:_MAX_BODY_EXCERPT]
    if len(body.strip()) > _MAX_BODY_EXCERPT:
        body_excerpt += "…"
    body_html = f"<blockquote>{_esc(body_excerpt)}</blockquote>" if body_excerpt else ""

    # reasoning(LLM 판단 근거, 이미 계산돼 있음)에 이메일이 뭘 요청/문의하는지가
    # 대체로 같이 서술돼 있어, 원문 발췌 아래에 굵게 보여준다.
    summary = analysis.get("reasoning")
    summary_html = f"<p><b>{_esc(summary)}</b></p>" if summary else ""

    # 카드 위 요약 줄은 판단에 쓰는 것만(11/12 태훈 "쓸모있는 데이터만") — 담당 파트(= 이 카드가 가는 채널)·발신자(14일 194건 전부 CS 전달 주소 하나)는
    # 뺐다. 둘 다 이력 탭엔 그대로. sender·part 인자는 호출부 호환으로 받기만 한다.
    severity_html = f'<span style="color:{color}"><b>{_esc(_SEVERITY_LABEL.get(severity, severity))}</b></span>'
    detail_items = [f"분류: {_esc(_CATEGORY_LABEL.get(category, category))} · 심각도: {severity_html}"]
    if analysis.get("systems"):
        # 메일함 라우팅과 실제 문의 시스템이 다를 수 있어(11/05 실측 대부분 다름) — 근거 지식을 찾은 시스템을 보여준다
        detail_items.append(f"관련 시스템: {_esc(' · '.join(analysis['systems']))}")
    if analysis.get("mismatch_flagged"):
        detail_items.append(
            '<span style="color:#DC2626"><b>⚠️ 오배치 의심</b></span> — 이 메일함 담당 업무와 내용이 다를 수 있습니다'
        )
    if pattern_info:
        # 반복은 심각도와 별개 정보라 심각도 색(#D97706·#DC2626) 대신 앱 강조색(indigo). "n건마다 · n번째 감지"는 설정 이야기라 뺐다(11/12)
        detail_items.append(
            f'<span style="color:#6366F1"><b>🔁 반복</b></span> — 최근 {pattern_info["window_days"]}일 유사 VOC {pattern_info["member_count"]}건'
        )
        if pattern_info.get("needs_review"):
            detail_items.append("⚠️ 확인 필요 — 묶음 건수가 많아 다른 문제가 섞였을 수 있습니다")
        if pattern_info.get("knowledge_found") is False:
            # 반복되는데 업무 지식에 관련 내용이 없음 = 지식 공백 후보(10/22) — 담당자가 지식을 만들 차례라는 신호
            detail_items.append("📭 관련 지식 없음 — 지식 공백 후보(이 유형의 답을 지식으로 등록해 주세요)")
    # 근거 내용은 카드에 싣지 않는다(11/12) — 앞부분 몇십 자로는 오탐을 알아챌 수 없고 읽히지도 않았다.
    # 지식은 건수만(이력 탭 "참조 지식"과 같은 knowledge_ref_ids — 카드용 knowledge_refs는 상위 3개로 잘려 숫자가 어긋남, 리뷰 M1).
    # 정책 근거는 DB에 안 남아 이력 탭에 없다 — 짧은 정책 이름만 싣는다.
    n_knowledge = len(analysis.get("knowledge_ref_ids") or [])
    if n_knowledge:
        detail_items.append(f"근거 지식 {n_knowledge}건 — OpsLens VOC 이력 탭에서 확인")
    policy_names = list(dict.fromkeys(r["name"] for r in analysis.get("policy_refs") or [] if r.get("name")))
    if policy_names:
        detail_items.append(f"근거 정책: {_esc(' · '.join(policy_names))}")
    if urgent and oncall_contact_name:
        detail_items.append(f"온콜 담당자: {_esc(oncall_contact_name)} — 필요 시 직접 전화 부탁드립니다")
    detail_list = "<ul>" + "".join(f"<li>{item}</li>" for item in detail_items) + "</ul>"

    # h2/h3 태그만으로는 Teams 렌더링 기본 폰트 크기가 기대보다 작고 섹션 간 여백도
    # 좁게 나오는 게 실측으로 확인돼, font-size를 명시적으로 지정하고 섹션 사이에
    # 구분선(_DIVIDER)을 넣어 시각적으로도 섹션이 뚜렷이 나뉘게 한다.
    header_color_style = f"color:{color};" if urgent else ""
    header_html = f'<h2 style="{header_color_style}font-size:22px">{header}</h2>{detail_list}'

    sections = [
        header_html,
        f"{_section_title('제목')}{_esc(subject) or '(제목 없음)'}",
        f"{_section_title('내용')}{body_html}{summary_html}",
    ]

    # 해결 방안 칸은 항상 — 없으면 이유를 한 줄로(실사용 중 "왜 없지?" 질문, 11/12 반복 유형 지식이 이유 줄 자리를 차지해 IT 무관인데 이유가 안 보였음).
    # 초안은 system_error일 때만 만든다(11/05 분류 무관 초안 19건 중 12건 지어냄).
    resolution_draft = analysis.get("resolution_draft")
    llm_failed = (analysis.get("reasoning") or "").startswith(LLM_FAILED_PREFIX)
    if resolution_draft:
        # 참고 지식 근거로 확인된 초안만 "해결 방안" — 아니면 AI 제안으로 구분해 단정하지 않는다(11/05)
        title = DRAFT_TITLES.get(analysis.get("draft_grounded"), DRAFT_TITLES[None])
        sections.append(f"{_section_title(title)}<blockquote>{_esc(resolution_draft)}</blockquote>")
    elif category != "system_error" and not llm_failed:
        # 심각도 색과 겹치지 않게 순수 텍스트로
        category_label = _esc(_CATEGORY_LABEL.get(category, category))
        sections.append(
            f'{_section_title("해결 방안")}<blockquote>해당 없음 — "{category_label}"(으)로 판단되어 해결 방안을 생성하지 않았습니다.</blockquote>'
        )
    elif pattern_info and pattern_info.get("knowledge_found") is False:
        # 시스템 오류인데 관련 지식이 없어 초안을 비운 경우 — 분석 실패로 설명하지 않는다(리뷰 L2)
        sections.append(f'{_section_title("해결 방안")}<blockquote>관련 지식 없음 — 이 유형의 답을 지식으로 등록하면 다음부터 함께 나갑니다.</blockquote>')
    else:
        # system_error인데 초안이 없거나 LLM 호출 자체가 실패한 경우 — 수동 확인을 유도한다
        sections.append(
            f'{_section_title("해결 방안")}<blockquote>⚠️ 생성되지 않음 — 분석이 불완전했을 수 있습니다. 이력 탭에서 판단 근거를 확인해 주세요.</blockquote>'
        )

    return {"text": f"<br>{_DIVIDER}<br>".join(sections)}


async def send_teams_notification(webhook_url: str, message: dict) -> tuple[bool, Optional[str]]:
    """Workflows 웹훅으로 POST — 공용 아웃바운드 HTTP 레이어(shared/http_client.py) 사용.

    Returns:
        (성공 여부, 실패 시 에러 메시지)
    """
    _body, error, status, _kb, _ms = await call_http(
        "POST", webhook_url, json_body=message, headers={"Content-Type": "application/json"},
    )
    if error:
        logger.warning("VOC Teams 알림 발송 실패 (status=%s): %s", status, error)
        return False, error
    return True, None
