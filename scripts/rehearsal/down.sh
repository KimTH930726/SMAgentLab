#!/bin/bash
# 리허설 사본 정리 — up.sh가 만든 것만 이름으로 지운다(원본 opsdb·ops-backend·ops-frontend·Redis 0번은 건드리지 않음).
# 사본 DB를 못 지우면 이름을 찍고 1로 끝난다(조용히 남으면 다음 up.sh의 createdb가 멈춤 — 리뷰 11/12).
set -uo pipefail
export MSYS_NO_PATHCONV=1
DB=opsdb_rehearsal; NET=smagentlab_rehearsal; RDB=5
[ "$DB" != "opsdb" ] || exit 1
say() { [ "${QUIET:-0}" = 1 ] || echo "$@"; }
[ "${1:-}" = "--quiet" ] && QUIET=1
for c in ops-frontend-rehearsal ops-backend-rehearsal; do docker rm -f "$c" >/dev/null 2>&1 && say "지움: 컨테이너 $c"; done
docker network rm "$NET" >/dev/null 2>&1 && say "지움: 네트워크 $NET"
EXISTS=$(docker exec ops-postgres psql -U ops -d postgres -Atc "select count(*) from pg_database where datname='$DB'") \
  || { echo "postgres에 접속 못 함 — 사본 DB $DB 확인 못 함"; exit 1; }   # 접속 실패를 "없음"으로 넘기지 않는다(재리뷰 11/12)
if [ "$EXISTS" = 1 ]; then
  if docker exec ops-postgres dropdb -U ops --force "$DB"; then say "지움: DB $DB"; else echo "사본 DB $DB 못 지움"; exit 1; fi
fi
docker exec ops-redis redis-cli -n $RDB FLUSHDB >/dev/null 2>&1 && say "비움: Redis $RDB번"
exit 0
