"""실서버 시나리오 실행 잠금 — 두 시나리오가 동시에 돌면 캐시 비우기·임시 파트가 서로를 망친다(2026-10-13 실측: 멀티턴 테스트가 두 번
겹쳐 돌아 캐시 오염 확인이 무효가 됐다). 호스트·컨테이너 어디서 돌려도 같은 파일(backend/eval_out/scenario.lock — 볼륨 공유)."""
from __future__ import annotations

import atexit
import os
import sys
import time
from pathlib import Path

LOCK = Path(__file__).resolve().parents[2] / "eval_out" / "scenario.lock"
STALE_SEC = 2 * 3600   # 이보다 오래된 잠금은 죽은 실행이 남긴 것으로 본다


def hold(name: str) -> None:
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    if LOCK.exists() and time.time() - LOCK.stat().st_mtime > STALE_SEC:
        LOCK.unlink(missing_ok=True)
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        print(f"다른 시나리오가 실행 중입니다({LOCK.read_text(encoding='utf-8', errors='ignore').strip()}) — 끝난 뒤 다시 실행하세요. "
              f"실행 중인 게 없는데 남아 있으면(강제 종료 등) 이 파일을 지우세요: {LOCK}")
        sys.exit(2)
    mine = f"{Path(name).name} pid={os.getpid()} {time.strftime('%Y-%m-%d %H:%M:%S')}"
    os.write(fd, mine.encode())
    os.close(fd)

    def _release():   # 내 잠금일 때만 지운다 — 오래돼 남이 가져간 잠금을 끝나면서 지우지 않게(리뷰)
        try:
            if LOCK.read_text(encoding="utf-8", errors="ignore") == mine:
                LOCK.unlink(missing_ok=True)
        except FileNotFoundError:
            pass
    atexit.register(_release)
