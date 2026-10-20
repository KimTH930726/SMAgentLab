"""기동 마이그레이션을 같은 DB에 두 번 연속 실행해 멱등성을 확인한다 (2026-10-20, v2.142 재리뷰 H1).

실사고(배포 전 발견): #78이 같은 함수 안에서 옛 칸을 지운 뒤, 앞쪽 문장이 그 칸을 전제로 실행돼 "두 번째 기동"에서 백엔드가 뜨지 않을
뻔했다. 단위 테스트는 마이그레이션을 실제 DB에 돌리지 않아 못 잡았다. 칸을 지우거나 이름을 바꾸는 마이그레이션을 바꾸면 이걸 돌린다.

실행(컨테이너 안, 운영 DB 말고 복사본에): 백업을 임시 DB로 복원한 뒤
  docker exec -w /app -e DATABASE_URL=postgresql://ops:<pw>@postgres:5432/<임시DB> ops-backend python scripts/check_migrations_twice.py
"""
import asyncio
import sys

sys.path.insert(0, "/app")


async def main() -> int:
    import main as app_main
    from core.database import init_pool
    await init_pool()
    for i in (1, 2):
        try:
            await app_main._run_migrations()
        except Exception as e:   # noqa: BLE001 — 어떤 실패든 결과로 보고
            print(f"{i}회차 실패: {type(e).__name__}: {e}")
            return 1
        print(f"{i}회차 통과")
    print("멱등 — 두 번 연속 기동해도 마이그레이션이 깨지지 않음")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
