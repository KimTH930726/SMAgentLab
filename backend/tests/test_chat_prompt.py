"""채팅 시스템 프롬프트 점검(2026-10-13) — 코드 기본값 = DB 시드 한 곳, 지식 공백 문구 일치, 안 쓰는 지시 없음."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("_llm_base_real", str(Path(__file__).resolve().parent.parent / "service" / "llm" / "base.py"))
B = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(B)


def _helpers_marker():
    import re
    src = (Path(__file__).resolve().parent.parent / "service" / "chat" / "helpers.py").read_text(encoding="utf-8")
    return re.search(r'^NO_KNOWLEDGE_MARKER\s*=\s*"([^"]+)"', src, re.M).group(1)


def test_no_knowledge_marker_matches_gap_detection():
    # service/chat/helpers.NO_KNOWLEDGE_MARKER — 지식 공백 감지 문자열과 프롬프트 지시가 따로 놀면 공백이 안 세진다
    assert f'"{_helpers_marker()}"' in B.CHAT_SYSTEM_PROMPT


OLD_SEED = """IT 운영 지식 AI(OpsLens). 아래 규칙을 따르세요.

[원칙]
- 반드시 제공된 [참고 문서]만 근거로 답변. 문서에 없는 내용은 절대 만들어내지 마세요.
- 관련 문서가 없으면 다른 설명이나 마크다운 서식 없이 정확히 "관련 지식을 찾지 못했습니다"라고만
  답변하세요. 이 경우 아래 [형식] 규칙(마크다운, 근거 표시 등)은 적용하지 않습니다.
- 신뢰도 높음 문서를 우선 근거로 사용. 낮음은 보조 참고만.

[문맥 활용]
- [과거 유사 사례]가 있으면 답변 형식을 참고하되 현재 문서 내용 우선.
- 이전 대화가 있으면 맥락을 이어서 답변.

[형식]
- Markdown(표, 목록, 코드 블록, 볼드) 사용. 한국어 답변.
- 컨테이너명, 테이블명, SQL이 있으면 반드시 포함.
- 답변 끝에 근거 표시: 📎 문서 N, 문서 M 참고"""


def _migrate(text):
    for old, new in B.CHAT_PROMPT_MIGRATIONS:
        if old in text:
            text = text.replace(old, new)
    return text


def test_migration_turns_old_db_prompt_into_code_prompt_idempotently():
    """운영 DB(옛 시드) → 마이그레이션 #75 → 코드 기본 문구와 정확히 같고, 다시 돌려도 그대로(리뷰 2026-10-13)."""
    once = _migrate(OLD_SEED.replace("\n", "\r\n").replace("\r\n", "\n"))
    assert once == B.CHAT_SYSTEM_PROMPT
    assert _migrate(once) == once


def test_unused_instructions_removed():
    assert "📎" not in B.CHAT_SYSTEM_PROMPT            # 근거 표시는 화면·코드가 안 읽는다(근거 카드는 따로)
    assert "과거 유사 사례" not in B.CHAT_SYSTEM_PROMPT  # 그런 블록을 붙이는 코드가 없다
    assert B._FALLBACK_SYSTEM_PROMPT is B.CHAT_SYSTEM_PROMPT


def test_over_refusal_guard_present():
    """근거 표시 줄을 빼자 거절이 늘어 넣은 줄(88문항 A/B) — 빠지면 다시 과잉 거절."""
    assert "일부만 있어도 있는 만큼 답하고" in B.CHAT_SYSTEM_PROMPT and "관련 문서가 전혀 없으면" in B.CHAT_SYSTEM_PROMPT



def test_removed_field_instruction_gone():
    """컨테이너·테이블 칸은 v2.82에 지식에서 뺐다 — 지시도 없어야(10/21). SQL·코드 포함 지시는 남는다."""
    assert "컨테이너명" not in B.CHAT_SYSTEM_PROMPT and "테이블명" not in B.CHAT_SYSTEM_PROMPT
    assert "SQL" in B.CHAT_SYSTEM_PROMPT
