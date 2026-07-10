"""파이프라인 여러 스테이지(generate/fix)에서 공유하는 스키마 텍스트 포맷터."""


def format_schema(schema_results: list[dict]) -> str:
    """RAG로 조회된 컬럼 목록을 LLM 프롬프트용 텍스트로 변환 (테이블별 그룹핑, PK/FK/설명 포함)."""
    if not schema_results:
        return ""
    tables: dict[str, list] = {}
    for r in schema_results:
        tname = r["table_name"]
        if tname not in tables:
            tables[tname] = []
        pk_mark = " (PK)" if r.get("is_pk") else ""
        fk_mark = f" (FK -> {r['fk_reference']})" if r.get("fk_reference") else ""
        desc = f": {r['description']}" if r.get("description") else ""
        tables[tname].append(f"  - {r['name']} {r['data_type']}{pk_mark}{fk_mark}{desc}")
    lines = []
    for tname, cols in tables.items():
        lines.append(f"Table: {tname}")
        lines.extend(cols)
    return "\n".join(lines)
