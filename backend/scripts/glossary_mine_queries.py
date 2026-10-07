"""반복된 지식 공백 → 용어 동의어 확인을 지금 한 번 실행 (2026-10-07, v2.128 개편). 평소엔 앱이 하루 1회 자동(glossary_mining).

  --dry-run  DB에 쓰지 않고 처리 표시도 안 남김 — 걸린 반복 공백, LLM 후보, 효과 확인 결과만 출력(품질 점검용)

실행(컨테이너 안): python scripts/glossary_mine_queries.py [--dry-run]
"""
import argparse
import asyncio
import json
import sys

sys.path.insert(0, "/app")

from core.database import init_pool  # noqa: E402
from agents.knowledge_rag.knowledge import glossary_mining  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    await init_pool()
    from shared.embedding import embedding_service
    embedding_service.load()   # 관련 용어 고르기·품질 게이트·효과 확인용(앱에선 기동 시 로드됨)
    from core.database import get_conn
    from agents.knowledge_rag.knowledge import retrieval
    async with get_conn() as conn:
        await retrieval.load_runtime_overrides_from_db(conn)   # 운영 임계값으로 효과 확인
    from service.llm.factory import get_llm_provider
    out = await glossary_mining.run_once(get_llm_provider(), dry_run=args.dry_run)
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
