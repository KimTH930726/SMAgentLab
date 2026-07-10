"""공통 유틸리티 — LLM 응답 JSON 파싱."""
import json
import re


def parse_json_object(text: str) -> dict:
    """LLM 응답에서 JSON 객체 추출. 코드 블록 래핑 및 부가 텍스트 자동 제거."""
    try:
        result = json.loads(_strip_code_fence(text))
    except json.JSONDecodeError:
        # 코드펜스 없이 "설명 텍스트 {...} 후기" 형태로 온 경우 { } 구간만 재추출
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise
        result = json.loads(m.group())
    if not isinstance(result, dict):
        raise ValueError("Expected JSON object, got array or primitive")
    return result


def parse_json_array(text: str) -> list:
    """LLM 응답에서 JSON 배열 추출. 코드 블록 래핑 및 부가 텍스트 자동 제거."""
    try:
        result = json.loads(_strip_code_fence(text))
    except json.JSONDecodeError:
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if not m:
            raise
        result = json.loads(m.group())
    if not isinstance(result, list):
        raise ValueError("Expected JSON array, got object or primitive")
    return result


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if "```json" in text:
        inner = text.split("```json", 1)[1]
        text = inner.rsplit("```", 1)[0]
    elif "```" in text:
        inner = text.split("```", 1)[1]
        text = inner.rsplit("```", 1)[0]
    return text.strip()
