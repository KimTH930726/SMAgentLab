"""공통 유틸리티 — LLM 응답 JSON 파싱."""
import json


def parse_json_object(text: str) -> dict:
    """LLM 응답에서 JSON 객체 추출. 코드 블록 래핑 자동 제거."""
    text = _strip_code_fence(text)
    result = json.loads(text)
    if not isinstance(result, dict):
        raise ValueError("Expected JSON object, got array or primitive")
    return result


def parse_json_array(text: str) -> list:
    """LLM 응답에서 JSON 배열 추출. 코드 블록 래핑 자동 제거."""
    text = _strip_code_fence(text)
    result = json.loads(text)
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
