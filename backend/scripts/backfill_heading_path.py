"""기존 confluence_bulk 지식 25건에 heading_path 소급 채우기 (2026-09-22).

새 청킹 코드(chunker.py의 heading_path 추적)는 원본 HTML의 h1~h4 레벨을 그대로 쓰지만,
이미 저장된 행은 그 레벨 정보가 없고 content 문자열 안에 "## 1.2.1. 매장" 형태로 헤딩
텍스트만 평평하게 남아있다. 원본 레벨을 다시 알 방법이 없으므로, 번호 표기의 점(.) 개수를
깊이로 쓰는 결정론적 근사치를 쓴다("1.2.1"은 점 2개 → 깊이 3, "1.2"는 점 1개 → 깊이 2).
번호가 없는 헤딩(순수 텍스트 제목)은 깊이 1로 취급.

한계: 원본에 실제 h1 경계가 없었던 문서(예: "배달의민족"/"쿠팡이츠" 두 섹션이 상위
구분 헤딩 없이 나열된 경우)는 이 소급 채우기로도 못 구분한다 — 그 특정 사례(id=12857/
12862)는 이미 별도로 채널명 라벨을 수동 삽입해뒀다(2026-09-22, architecture.md v2.97).
이 스크립트는 "번호 체계가 있는 나머지 문서"에 유효하다.

실행: docker compose exec backend python scripts/backfill_heading_path.py
"""
from __future__ import annotations

import asyncio
import re
import sys

sys.path.insert(0, "/app")

from core.database import init_pool, get_conn

_HEADING_RE = re.compile(r"^##\s+(\d+(?:\.\d+)*\.?)?\s*(.*)$")


def _depth_from_number(number: str) -> int:
    """"1.2.1" → 3, "1.2" → 2, "1" → 1, 번호 없음 → 1."""
    if not number:
        return 1
    return number.rstrip(".").count(".") + 1


def compute_heading_paths(chunks: list[str]) -> list[list[str]]:
    """같은 source_file의 청크들(chunk_idx 순)을 순서대로 훑으며 조상 경로를 계산."""
    stack: list[tuple[int, str]] = []
    paths: list[list[str]] = []
    for text in chunks:
        first_line = text.split("\n", 1)[0]
        m = _HEADING_RE.match(first_line)
        if not m:
            paths.append([t for _, t in stack])
            continue
        number, title = m.group(1), m.group(2).strip()
        depth = _depth_from_number(number)
        while stack and stack[-1][0] >= depth:
            stack.pop()
        paths.append([t for _, t in stack])
        full_title = f"{number} {title}".strip() if number else title
        if full_title:
            stack.append((depth, full_title))
    return paths


async def main() -> None:
    await init_pool()
    async with get_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT id, source_file, source_chunk_idx, content
            FROM rag_knowledge
            WHERE source_type = 'confluence_bulk'
            ORDER BY source_file, source_chunk_idx
            """
        )
        by_file: dict[str, list[dict]] = {}
        for r in rows:
            by_file.setdefault(r["source_file"] or "", []).append(dict(r))

        total_updated = 0
        for source_file, group in by_file.items():
            paths = compute_heading_paths([g["content"] for g in group])
            for g, path in zip(group, paths):
                if not path:
                    continue
                await conn.execute(
                    "UPDATE rag_knowledge SET heading_path = $1 WHERE id = $2 AND heading_path IS NULL",
                    path, g["id"],
                )
                total_updated += 1
                print(f"id={g['id']} ({source_file}, idx={g['source_chunk_idx']}): {path}")

        print(f"\n총 {total_updated}건 업데이트")


if __name__ == "__main__":
    asyncio.run(main())
