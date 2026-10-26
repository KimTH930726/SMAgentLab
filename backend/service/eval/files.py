"""평가 결과 파일 읽기 (2026-10-26, 문항 상세 — 사용자 "어떤 질문이었고 어떻게 답했고 왜 그랬는지").

질문·답·채점 이유는 정책 내용을 담아 DB에 넣지 않는다(판정 기록엔 qid·숫자만, #86). 관리자가 문항을 누를 때만 서버가 측정 결과 파일
(eval_out/, git 제외)에서 그 문항 줄을 읽어 돌려준다. 파일 이름은 판정 기록 inputs(스크립트가 저장한 이름)뿐 — 경로 조각은 받지 않는다.
같은 이름이 여러 곳이면 어느 측정인지 모르므로 고르지 않는다(scripts/eval_improve.py와 같은 규칙).
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional

EVAL_DIR = Path("/app/eval_out")
_cache: dict[str, tuple[float, dict[str, list[dict]]]] = {}   # 경로 → (수정 시각, qid → 행들)
_lock = threading.Lock()   # asyncio.to_thread로 동시에 불려도 캐시 넣고 빼기가 꼬이지 않게(재리뷰 L3)
_MAX_FILES = 12


class ResultFileError(Exception):
    """결과 파일을 찾을 수 없거나 여러 개 — 화면에 그대로 보인다."""


def find_result(name: Optional[str]) -> Optional[Path]:
    if not name:
        return None
    if Path(name).name != name:   # 판정 기록엔 이름만 — 경로가 섞였으면 거부
        raise ResultFileError(f"결과 파일 이름이 이상합니다: {name}")
    # 이름 → 경로는 캐시하지 않는다 — 나중에 같은 이름 파일이 생기면 "여러 곳이면 거부" 규칙을 놓친다(재리뷰 L4). 훑기는 스레드에서(호출 쪽)
    hits = sorted(EVAL_DIR.rglob(name)) if EVAL_DIR.exists() else []
    if len(hits) > 1:
        raise ResultFileError(f"같은 이름 결과 파일이 {len(hits)}곳 — 어느 측정인지 정할 수 없습니다")
    return hits[0] if hits else None


def rows_by_qid(path: Path) -> dict[str, list[dict]]:
    """파일 → qid별 행(회차 순). 파일이 바뀌면 다시 읽는다. 최근 파일 몇 개만 메모리에(측정 파일 하나 ~2MB)."""
    key = str(path)
    mtime = path.stat().st_mtime
    with _lock:
        hit = _cache.get(key)
    if hit and hit[0] == mtime:
        return hit[1]
    out: dict[str, list[dict]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            if r.get("qid"):
                out.setdefault(r["qid"], []).append(r)
    for rs in out.values():
        rs.sort(key=lambda r: r.get("rep", 0))
    with _lock:
        if key not in _cache and len(_cache) >= _MAX_FILES:   # 같은 파일 갱신은 다른 항목을 내보내지 않는다
            _cache.pop(next(iter(_cache)), None)
        _cache[key] = (mtime, out)
    return out
