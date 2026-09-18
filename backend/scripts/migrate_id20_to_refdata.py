"""id=20(rag_knowledge, "공통코드" 카테고리)을 ref_common_code로 1회 이전(2026-09-18).

이 문서는 마크다운 표가 아니라 "- 그룹명 (테이블.컬럼)\n  . 코드 : 설명" 불릿 형식이라
service/refdata/parser.py의 parse_common_code_table()(마크다운 표 전용)로는 못 파싱한다 —
이 형식 전용 파서는 일회성이라 이 스크립트에만 둔다(CMDB 실 데이터가 같은 형식으로 들어오면
그때 parser.py에 정식으로 옮길 근거가 생김, 지금은 사례 1개뿐이라 일반화 안 함).

실행 후 사람이 반드시 출력을 훑어보고 확인 — parser.py 모듈 자체의 원칙(결정론 파서
결과도 사람이 확인)과 동일.
"""
import asyncio
import re
import sys

sys.path.insert(0, "/app")

from core.database import init_pool, close_pool, get_conn
from service.refdata.parser import ParsedCommonCode
from service.refdata.service import ingest_common_codes

NAMESPACE = "딜리버스 DB"
KNOWLEDGE_ID = 20

_GROUP_RE = re.compile(r"^-\s*(.+)$")
_CODE_RE = re.compile(r"^\.\s*([^:]+?)\s*:\s*(.+?)\s*$")


def parse_bullet_common_codes(text: str) -> list[ParsedCommonCode]:
    results = []
    group_name = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("."):
            m = _CODE_RE.match(line)
            if m:
                results.append(ParsedCommonCode(
                    # ingest_common_codes()가 group_code(그룹코드명, group_code_name과는
                    # 별개 필드)가 없으면 skipped_empty로 버린다 — 이 불릿 형식엔 그룹명
                    # 말고 별도 짧은 코드가 없어 group_name을 채워 필수값 체크만 통과시킨다.
                    # group_code는 VARCHAR(50)이라 원본 그룹명(테이블.컬럼 참조까지 포함돼
                    # 50자를 넘는 경우가 많음)을 자름 — 전체 텍스트는 group_code_name(200자)에
                    # 그대로 남고, 검색(search_common_codes)도 group_code_name을 기준으로 하므로
                    # group_code 자체가 잘려도 검색 결과엔 영향 없음.
                    work_code="", group_code=group_name[:50], group_code_name=group_name[:200],
                    code_id=m.group(1).strip()[:50], code_name=m.group(2).strip()[:200], mgmt_values={},
                ))
            continue
        m = _GROUP_RE.match(line)
        if m:
            group_name = m.group(1).strip()
    return results


async def main() -> None:
    await init_pool()
    try:
        async with get_conn() as conn:
            content = await conn.fetchval("SELECT content FROM rag_knowledge WHERE id = $1", KNOWLEDGE_ID)
        if content is None:
            print(f"id={KNOWLEDGE_ID} not found")
            return

        parsed = parse_bullet_common_codes(content)
        print(f"파싱 결과: {len(parsed)}건")
        for p in parsed[:15]:
            print(f"  [{p.group_code_name}] {p.code_id} : {p.code_name}")
        print("  ...")

        summary = await ingest_common_codes(NAMESPACE, parsed, source_file=f"rag_knowledge#{KNOWLEDGE_ID}(migrated)")
        print(f"\n적재 완료: inserted={summary.inserted} skipped_empty={summary.skipped_empty}")

        if summary.inserted == 0:
            print("[중단] 적재가 0건이라 rag_knowledge 원본을 보존한다(데이터 손실 방지).")
            return

        async with get_conn() as conn:
            await conn.execute(
                "UPDATE rag_knowledge SET status = 'deprecated', updated_at = NOW() WHERE id = $1",
                KNOWLEDGE_ID,
            )
        print(f"rag_knowledge id={KNOWLEDGE_ID} → status='deprecated' 처리 완료")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
