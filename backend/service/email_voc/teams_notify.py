"""VOC 분석 결과 Teams 알림 발송 (docs/email-analysis-channel-plan.md §8, §10, §11 Track A #3).

발송 채널은 §8에서 Teams "Workflows" 웹훅으로 확정됐고, 호출 로직은 §2에서 확인한 기존
MCP 도구 실행기(agents/mcp_tool/agent.py의 _execute_http_call)를 그대로 재사용한다.
신규 발송 모듈을 처음부터 만들 필요가 없다는 게 설계의 핵심 전제였다.

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

from agents.mcp_tool.agent import _execute_http_call

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

    pattern_info: 이 VOC가 반복 패턴 임계치를 방금 넘긴 경우에만 채워짐
    ({"member_count", "window_days", "coverage"}, pattern_detection.py 참고).
    처음엔 "🔁 반복 패턴 감지"를 완전히 별도의 Teams 메시지로 만들었으나, 실사용
    피드백("두 개로 찢지 말고 하나의 흐름으로 녹여라 — 원래 하던 개별 VOC 발송에
    유사도 패턴 체크를 결합한 파이프라인이어야 한다")에 따라 되돌림 — 개별 VOC
    카드 하나에 반복 여부를 한 줄 얹고, 해결방안도 그 안에서 같이 보여준다.
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

    # 담당파트/분류/심각도/발신자 같은 핵심 정보는 내용을 다 읽어야 알 수 있으면
    # 스캔하기 불편하다는 실사용 피드백 — 헤더 바로 아래, 제목보다도 먼저 보여준다.
    detail_items = [
        f"담당 파트: {_esc(part) or '-'}",
        f"분류: {_esc(_CATEGORY_LABEL.get(category, category))}",
        f'심각도: <span style="color:{color}"><b>{_esc(_SEVERITY_LABEL.get(severity, severity))}</b></span>',
        f"발신자: {_esc(sender) or '-'}",
    ]
    if analysis.get("mismatch_flagged"):
        detail_items.append(
            '<span style="color:#DC2626"><b>⚠️ 오배치 의심</b></span> — 이 메일함 담당 업무와 내용이 다를 수 있습니다'
        )
    if pattern_info:
        # #D97706(high 심각도)이나 #DC2626(urgent/오배치)과 겹치면 "심각도 신호"로
        # 오인될 수 있어 — 반복 여부는 심각도와 무관한 별개 정보라 앱 강조색(indigo,
        # CLAUDE.md 팔레트)을 대신 쓴다.
        detail_items.append(
            f'<span style="color:#6366F1"><b>🔁 반복 패턴</b></span> — 최근 {pattern_info["window_days"]}일간 '
            f'유사한 VOC {pattern_info["member_count"]}건째 발생'
        )
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

    # resolution_draft는 category가 system_error일 때만 LLM에게 생성을 지시한다
    # (사용자 실수/판단 보류는 "고칠 버그"가 없으니 해결 방안 자체가 성립하지 않음).
    # 이 이유를 안 적으면 알림 받은 사람이 "왜 해결방안이 없지?"를 매번 헷갈려해서
    # (실사용 중 실제로 질문 받음) 없는 이유를 한 줄로 명시한다.
    # pattern_info로 이미 등록된 지식이 있으면(get_cluster_coverage) LLM이 이번 건
    # 자체에 대해 resolution_draft를 못 만든 경우(uncertain/user_mistake 등)에도
    # "이 반복 유형에 대한 답은 이미 있다"를 보여줄 수 있다 — resolution_draft보다는
    # 후순위(개별 LLM 판단이 이 VOC에 더 특화된 답이므로 우선), 없을 때만 대체한다.
    pattern_snippet = (
        pattern_info["coverage"]["snippet"]
        if pattern_info and pattern_info.get("coverage", {}).get("covered") else None
    )
    resolution_draft = analysis.get("resolution_draft")
    if resolution_draft:
        sections.append(f"{_section_title('해결 방안')}<blockquote>{_esc(resolution_draft)}</blockquote>")
    elif pattern_snippet:
        sections.append(f"{_section_title('해결 방안(반복 유형 기등록 지식)')}<blockquote>{_esc(pattern_snippet)}</blockquote>")
    elif category != "system_error":
        # 심각도 팔레트(low/medium/high/urgent) 색상과 겹치지 않도록 별도 색상
        # 없이 순수 텍스트로만 표시 — 색상을 넣으면 무관한 심각도 신호로 오인될 수 있다.
        category_label = _esc(_CATEGORY_LABEL.get(category, category))
        sections.append(
            f'{_section_title("해결 방안")}<blockquote>해당 없음 — "{category_label}"(으)로 판단되어 해결 방안을 생성하지 않았습니다.</blockquote>'
        )
    else:
        # category=system_error인데도 resolution_draft가 없는 경우 — LLM 호출
        # 실패 등으로 분석 자체가 불완전했을 가능성이 높으므로 수동 확인을 유도한다.
        sections.append(
            f'{_section_title("해결 방안")}<blockquote>⚠️ 생성되지 않음 — 분석이 불완전했을 수 있습니다. 이력 탭에서 판단 근거를 확인해 주세요.</blockquote>'
        )

    # 참고 지식(근거) — 맨 아래 배치: 판단 결과(내용/해결방안)를 먼저 보여주고,
    # 그 근거는 필요할 때 스크롤해서 확인하는 부가 정보로 취급한다. 관련지식 필터를
    # 통과했어도 실제로는 무관한 지식이 우연히 매칭되는 오탐이 실사용 중 발견돼(예:
    # 완전히 다른 도메인인데 "실패/오류" 같은 일반 단어만 겹침), 근거를 노출해두면
    # 그런 오탐을 사람이 바로 알아챌 수 있다.
    knowledge_refs = analysis.get("knowledge_refs") or []
    if knowledge_refs:
        ref_items = "".join(
            f'<li><span style="color:#6B7280">(유사도 {r["score"]:.2f})</span> {_esc(r["snippet"])}</li>'
            for r in knowledge_refs
        )
        sections.append(f"{_section_title('참고 지식(근거)')}<ul>{ref_items}</ul>")

    return {"text": f"<br>{_DIVIDER}<br>".join(sections)}


async def send_teams_notification(webhook_url: str, message: dict) -> tuple[bool, Optional[str]]:
    """Workflows 웹훅으로 POST — 기존 MCP 도구 실행기(_execute_http_call) 재사용.

    Returns:
        (성공 여부, 실패 시 에러 메시지)
    """
    tool = {"method": "POST", "url": webhook_url, "headers": {"Content-Type": "application/json"}}
    _body, error, status, _kb, _ms = await _execute_http_call(tool, message)
    if error:
        logger.warning("VOC Teams 알림 발송 실패 (status=%s): %s", status, error)
        return False, error
    return True, None
