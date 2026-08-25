"""Tests for service/email_voc/teams_notify.py — build_teams_message() 포맷 검증.

conftest.py가 격리 목적으로 sys.modules["service"] 전체를 MagicMock으로 치환해두므로
(graph_client 테스트와 동일한 문제), 파일 경로 기반으로 직접 로드해 실제 코드를 검증한다.
"""
import importlib.util as _ilu
import sys
from pathlib import Path

_backend_dir = Path(__file__).resolve().parent.parent
_spec = _ilu.spec_from_file_location(
    "email_voc_teams_notify_under_test", str(_backend_dir / "service" / "email_voc" / "teams_notify.py"),
)
teams_notify = _ilu.module_from_spec(_spec)
sys.modules[_spec.name] = teams_notify
_spec.loader.exec_module(teams_notify)


def _base_analysis(**overrides) -> dict:
    base = {
        "category": "system_error", "severity": "low",
        "mismatch_flagged": False, "resolution_draft": None,
    }
    base.update(overrides)
    return base


class TestBuildTeamsMessage:
    def test_sections_present(self):
        msg = teams_notify.build_teams_message(
            subject="결제 오류 문의", sender="user@example.com", part="결제팀",
            analysis=_base_analysis(resolution_draft="재시도 안내"),
        )
        text = msg["text"]
        assert ">제목</h3>" in text
        assert "결제 오류 문의" in text
        assert ">내용</h3>" in text
        assert ">해결 방안</h3>" in text
        assert "<blockquote>재시도 안내</blockquote>" in text

    def test_no_resolution_draft_shows_reason_for_non_system_error(self):
        # 해결방안이 왜 없는지 사람이 알 수 있어야 한다(실사용 중 헷갈린다는 피드백) —
        # 섹션 자체를 생략하지 않고 이유를 명시한다.
        msg = teams_notify.build_teams_message(
            subject="s", sender="s@example.com", part="p",
            analysis=_base_analysis(category="uncertain", resolution_draft=None),
        )
        assert ">해결 방안</h3>" in msg["text"]
        assert "판단 보류" in msg["text"]
        assert "해당 없음" in msg["text"]

    def test_no_resolution_draft_shows_warning_for_system_error(self):
        # category=system_error인데 resolution_draft가 없으면(LLM 실패 등) 다른 경고 문구
        msg = teams_notify.build_teams_message(
            subject="s", sender="s@example.com", part="p",
            analysis=_base_analysis(category="system_error", resolution_draft=None),
        )
        assert ">해결 방안</h3>" in msg["text"]

    def test_reasoning_shown_as_bold_summary_in_content_section(self):
        # 심각도만 있고 해결방안이 없으면(판단 보류 등) 뭔 내용인지 전혀 알 수 없다는
        # 실사용 피드백 — LLM 판단 근거(이미 계산돼 있음)를 내용 섹션에 굵게 보여준다.
        msg = teams_notify.build_teams_message(
            subject="s", sender="s@example.com", part="p",
            analysis=_base_analysis(category="uncertain", reasoning="SSGPAY DB 전환 오픈 협조 요청 메일입니다."),
        )
        assert "<p><b>SSGPAY DB 전환 오픈 협조 요청 메일입니다.</b></p>" in msg["text"]

    def test_metadata_shown_at_top_before_subject_and_content(self):
        # 담당파트/분류/심각도/발신자를 다 읽어야 알 수 있으면 스캔하기 불편하다는
        # 실사용 피드백 — 헤더 바로 아래, 제목·내용보다 먼저 보여준다.
        msg = teams_notify.build_teams_message(
            subject="s", sender="s@example.com", part="p",
            analysis=_base_analysis(reasoning="판단 근거 요약"),
        )
        text = msg["text"]
        assert text.index("담당 파트") < text.index(">제목</h3>")
        assert text.index("담당 파트") < text.index("판단 근거 요약")

    def test_divider_separates_top_level_sections(self):
        msg = teams_notify.build_teams_message(
            subject="s", sender="s@example.com", part="p",
            analysis=_base_analysis(resolution_draft="방안"),
        )
        assert msg["text"].count(teams_notify._DIVIDER) >= 2

    def test_body_excerpt_shown_before_reasoning_summary(self):
        # reasoning은 LLM이 만든 "판단 근거"지 원문이 아니다 — 실사용 피드백
        # ("메일 내용 원문이 아니네?")에 따라 전처리된 원문 발췌를 맨 위에 추가로 보여준다.
        msg = teams_notify.build_teams_message(
            subject="s", sender="s@example.com", part="p", body="결제가 계속 실패해서 문의드립니다.",
            analysis=_base_analysis(reasoning="결제 실패 관련 문의입니다."),
        )
        text = msg["text"]
        assert "<blockquote>결제가 계속 실패해서 문의드립니다.</blockquote>" in text
        assert text.index("결제가 계속 실패") < text.index("결제 실패 관련 문의")

    def test_long_body_truncated_with_ellipsis(self):
        msg = teams_notify.build_teams_message(
            subject="s", sender="s@example.com", part="p", body="가" * 400,
            analysis=_base_analysis(),
        )
        assert f"<blockquote>{'가' * 300}…</blockquote>" in msg["text"]

    def test_empty_body_omits_excerpt_block(self):
        # 해결 방안 섹션도 blockquote를 쓰므로, "내용" 섹션 구간만 잘라서 확인한다.
        msg = teams_notify.build_teams_message(
            subject="s", sender="s@example.com", part="p", body="",
            analysis=_base_analysis(),
        )
        text = msg["text"]
        content_section = text[text.index(">내용</h3>"):text.index(">해결 방안</h3>")]
        assert "<blockquote>" not in content_section

    def test_no_reasoning_omits_summary_paragraph(self):
        msg = teams_notify.build_teams_message(
            subject="s", sender="s@example.com", part="p",
            analysis=_base_analysis(reasoning=None),
        )
        assert "<p><b>" not in msg["text"]
        assert "분석이 불완전했을 수 있습니다" in msg["text"]

    def test_urgent_severity_uses_red_and_urgent_header(self):
        msg = teams_notify.build_teams_message(
            subject="s", sender="s@example.com", part="p", analysis=_base_analysis(severity="urgent"),
        )
        assert "🚨 긴급 VOC 알림" in msg["text"]
        assert "#DC2626" in msg["text"]

    def test_low_severity_uses_default_color_and_plain_header(self):
        msg = teams_notify.build_teams_message(
            subject="s", sender="s@example.com", part="p", analysis=_base_analysis(severity="low"),
        )
        assert "VOC 분석 알림" in msg["text"]
        assert "#DC2626" not in msg["text"]

    def test_all_four_severities_use_distinct_colors(self):
        """예전엔 high/urgent가 같은 빨강이라 사실상 2단계로만 보였음 — 4단계 전부 달라야 한다."""
        colors = {
            severity: [
                c for c in ("#6B7280", "#0891B2", "#D97706", "#DC2626")
                if c in teams_notify.build_teams_message(
                    subject="s", sender="s@example.com", part="p", analysis=_base_analysis(severity=severity),
                )["text"]
            ]
            for severity in ("low", "medium", "high", "urgent")
        }
        used = [colors[s][0] for s in ("low", "medium", "high", "urgent")]
        assert len(set(used)) == 4, f"심각도별 색상이 겹침: {colors}"

    def test_mismatch_flagged_shown_in_red(self):
        msg = teams_notify.build_teams_message(
            subject="s", sender="s@example.com", part="p", analysis=_base_analysis(mismatch_flagged=True),
        )
        assert "오배치 의심" in msg["text"]

    def test_oncall_only_shown_when_urgent(self):
        not_urgent = teams_notify.build_teams_message(
            subject="s", sender="s@example.com", part="p",
            analysis=_base_analysis(severity="low"), oncall_contact_name="홍길동",
        )
        assert "홍길동" not in not_urgent["text"]

        urgent = teams_notify.build_teams_message(
            subject="s", sender="s@example.com", part="p",
            analysis=_base_analysis(severity="urgent"), oncall_contact_name="홍길동",
        )
        assert "홍길동" in urgent["text"]

    def test_html_special_chars_in_subject_are_escaped(self):
        """이메일 원문(subject)에 HTML 특수문자가 섞여도 카드 렌더링이 깨지지 않아야 한다."""
        msg = teams_notify.build_teams_message(
            subject="<script>alert(1)</script> & 가격<100원", sender="s@example.com", part="p",
            analysis=_base_analysis(),
        )
        assert "<script>" not in msg["text"]
        assert "&lt;script&gt;" in msg["text"]
        assert "&amp;" in msg["text"]

    def test_knowledge_refs_shown_with_score(self):
        msg = teams_notify.build_teams_message(
            subject="s", sender="s@example.com", part="p",
            analysis=_base_analysis(knowledge_refs=[
                {"id": 1, "snippet": "배달 중단 해제 처리 절차", "score": 0.42},
            ]),
        )
        text = msg["text"]
        assert "참고 지식(근거)" in text
        assert "유사도 0.42" in text
        assert "배달 중단 해제 처리 절차" in text

    def test_no_knowledge_refs_omits_section(self):
        msg = teams_notify.build_teams_message(
            subject="s", sender="s@example.com", part="p", analysis=_base_analysis(),
        )
        assert "참고 지식(근거)" not in msg["text"]

    def test_empty_subject_shows_placeholder(self):
        msg = teams_notify.build_teams_message(
            subject="", sender="s@example.com", part="p", analysis=_base_analysis(),
        )
        assert "(제목 없음)" in msg["text"]


class TestBuildPatternAlertMessage:
    """반복 VOC 패턴이 처음 임계치를 넘을 때 발송하는 별도 알림 — pattern_detection.py 참고."""

    def test_includes_member_count_and_window(self):
        msg = teams_notify.build_pattern_alert_message(
            part="테스트", representative_subject="배달 오배송 불만", member_count=3,
            sample_subjects=["배달 오배송 불만 A", "배달 오배송 불만 B"], window_days=7,
        )
        text = msg["text"]
        assert "🔁 반복 패턴 감지" in text
        assert "7일간" in text
        assert "<b>3건</b>" in text
        assert "테스트" in text
        assert "배달 오배송 불만" in text
        assert "배달 오배송 불만 A" in text

    def test_html_special_chars_are_escaped(self):
        msg = teams_notify.build_pattern_alert_message(
            part="p", representative_subject="<script>alert(1)</script>", member_count=3,
            sample_subjects=[], window_days=7,
        )
        assert "<script>alert(1)</script>" not in msg["text"]
        assert "&lt;script&gt;" in msg["text"]
