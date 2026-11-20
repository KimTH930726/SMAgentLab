"""빈 DB 기동(11/19) — 백엔드가 먼저 실행하는 기본 표 SQL은 Postgres init 스크립트와 같은 파일이어야 한다(한쪽만 고치면 설치 방식마다 표가 달라진다)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_base_schema_matches_init_sql():
    init = next((p for p in (ROOT.parent / "init" / "01-init.sql", Path("/init/01-init.sql")) if p.exists()), None)   # 저장소 · dev 컨테이너(/init 마운트)
    if init is None:   # 운영 이미지엔 init이 없다
        import pytest
        pytest.skip("init/ 없음(컨테이너)")
    assert (ROOT / "db" / "base_schema.sql").read_bytes() == init.read_bytes()


def test_base_schema_is_idempotent_sql():
    sql = (ROOT / "db" / "base_schema.sql").read_text(encoding="utf-8")
    for line in sql.splitlines():
        u = line.strip().upper()
        if u.startswith("CREATE TABLE") or u.startswith("CREATE INDEX") or u.startswith("CREATE EXTENSION"):
            assert "IF NOT EXISTS" in u, line
        assert not u.startswith(("DROP TABLE", "DROP SCHEMA", "DELETE ", "TRUNCATE ")), line   # 트리거 다시 만들기(DROP TRIGGER IF EXISTS)는 허용


def test_base_schema_not_gitignored():
    """`.gitignore`의 `*.sql`(로컬 백업 제외)에 걸리면 파일이 커밋·CI 이미지에서 빠져 빈 DB 기동이 깨진다(11/19 커밋 직전 발견 —
    위 시험은 디스크 파일만 봐서 못 잡음). 저장소에서만(컨테이너엔 .gitignore 없음)."""
    gi = ROOT.parent / ".gitignore"
    if not gi.exists():
        import pytest
        pytest.skip(".gitignore 없음(컨테이너)")
    import fnmatch
    rel = "backend/db/base_schema.sql"
    ignored = False
    for raw in gi.read_text(encoding="utf-8").splitlines():   # 마지막에 맞은 규칙이 이긴다(git 규칙, 이 파일에 필요한 만큼만)
        pat = raw.strip()
        if not pat or pat.startswith("#"):
            continue
        neg = pat.startswith("!")
        pat = pat[1:] if neg else pat
        if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(rel.rsplit("/", 1)[-1], pat):
            ignored = not neg
    assert not ignored, "backend/db/*.sql 이 .gitignore에 걸림 — !backend/db/*.sql 필요"
