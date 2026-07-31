"""VOC 분석 결과 Teams 알림 발송 (docs/email-analysis-channel-plan.md §8, §10, §11 Track A #3).

발송 채널은 §8에서 Teams "Workflows" 웹훅으로 확정됐고, 호출 로직은 §2에서 확인한 기존
MCP 도구 실행기(agents/mcp_tool/agent.py의 _execute_http_call)를 그대로 재사용한다.
신규 발송 모듈을 처음부터 만들 필요가 없다는 게 설계의 핵심 전제였다.

페이로드 포맷: 처음엔 Adaptive Card(attachments)로 만들었으나, 이미 검증된 사내 다른
프로젝트(playwrite/modules/teams_notifier.py)의 Power Automate "Workflows" 플로우가
단순 {"text": "..."} 페이로드(<br> 줄바꿈)로 동작하는 걸 확인해 동일 포맷으로 맞췄다 —
같은 웹훅 URL을 재사용하려면 그 플로우가 실제로 파싱하는 스키마를 따라야 한다.

§3 Q6 결정: 온콜 자동 전화는 하지 않는다 — 심각도 "높음/긴급"은 멘션+강조 표시까지만
자동화하고, 실제 전화는 이 알림을 본 담당자가 수동으로 건다. oncall_contact_name은
표시 전용이며 자동 발신에는 쓰이지 않는다.
"""
import logging
from typing import Optional

from agents.mcp_tool.agent import _execute_http_call

logger = logging.getLogger(__name__)

_URGENT_SEVERITIES = {"high", "urgent"}
_SEVERITY_LABEL = {"low": "낮음", "medium": "보통", "high": "높음", "urgent": "긴급"}
_CATEGORY_LABEL = {"system_error": "시스템 오류", "user_mistake": "사용자 실수", "uncertain": "판단 보류"}


def build_teams_message(
    *, subject: str, sender: str, part: str, analysis: dict,
    oncall_contact_name: Optional[str] = None,
) -> dict:
    """§10 심각도 기반 포맷 — 높음/긴급은 강조 표시, 낮음/보통은 일반 알림.

    teams_notifier.py와 동일하게 {"text": "..."} 단순 페이로드로 만든다(<br> 줄바꿈).
    """
    severity = analysis.get("severity", "low")
    category = analysis.get("category", "uncertain")
    urgent = severity in _URGENT_SEVERITIES

    lines = [
        f"[{'🚨 긴급 VOC 알림' if urgent else 'VOC 분석 알림'}]",
        "",
        subject or "(제목 없음)",
        "",
        f"• 담당 파트: {part or '-'}",
        f"• 분류: {_CATEGORY_LABEL.get(category, category)}",
        f"• 심각도: {_SEVERITY_LABEL.get(severity, severity)}",
        f"• 발신자: {sender or '-'}",
    ]
    if analysis.get("mismatch_flagged"):
        lines.append("• ⚠️ 오배치 의심 — 이 메일함 담당 업무와 내용이 다를 수 있습니다")
    if urgent and oncall_contact_name:
        lines.append(f"• 온콜 담당자: {oncall_contact_name} — 필요 시 직접 전화 부탁드립니다")
    if analysis.get("resolution_draft"):
        lines.append("")
        lines.append(f"해결 방안 초안: {analysis['resolution_draft']}")

    return {"text": "<br>".join(lines)}


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
