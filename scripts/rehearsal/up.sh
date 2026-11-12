#!/bin/bash
# 시나리오 리허설용 사본 띄우기(C-4, 11/12) — 원본 opsdb는 pg_dump로 읽기만 한다.
# 사본: DB opsdb_rehearsal · 백엔드 ops-backend-rehearsal(:8001) · 화면 ops-frontend-rehearsal(:8502) · Redis 5번 칸.
# 화면 이미지는 nginx가 "backend"로 붙으므로, 사본 전용 네트워크에서 사본 백엔드에 backend 별명을 준다(원본과 안 섞임).
# 사용: scripts/rehearsal/up.sh            (반입할 이미지 = .env IMAGE_TAG, 없으면 latest)
#       REHEARSAL_SOURCE=1 scripts/rehearsal/up.sh   (백엔드만 지금 소스 ./backend로 — 개발 중 확인)
set -euo pipefail
export MSYS_NO_PATHCONV=1
cd "$(dirname "$0")/../.."

DB=opsdb_rehearsal; NET=smagentlab_rehearsal; BE=ops-backend-rehearsal; FE=ops-frontend-rehearsal
BE_PORT=${REHEARSAL_BACKEND_PORT:-8001}; FE_PORT=${REHEARSAL_FRONTEND_PORT:-8502}; RDB=5
TAG=${IMAGE_TAG:-$(grep -E '^IMAGE_TAG=' .env 2>/dev/null | cut -d= -f2 | sed 's/#.*//' | tr -d '[:space:]' || true)}; TAG=${TAG:-latest}
[ "$DB" != "opsdb" ] || { echo "사본 이름이 원본과 같음 — 중단"; exit 1; }
case "$BE_PORT$FE_PORT" in *8000*|*8501*) echo "원본 포트(8000·8501) — 중단"; exit 1;; esac
docker inspect ops-postgres ops-redis ops-backend >/dev/null   # 원본 스택이 떠 있어야 한다(설정을 원본 백엔드에서 읽음)

scripts/rehearsal/down.sh --quiet   # 지난 사본이 남아 있으면 먼저 정리

mkdir -p scripts/rehearsal/out; ENVF=scripts/rehearsal/out/.rehearsal.env   # 상대 경로 — Git Bash의 /tmp는 docker.exe가 못 읽음
# 끝나면 시크릿 파일을 지우고, 중간에 실패하면 사본도 정리한다(사본 백엔드의 백그라운드 작업이 로컬 LLM을 계속 쓰지 않게 — 리뷰 11/12)
trap 'rc=$?; rm -f "$ENVF"; if [ $rc -ne 0 ]; then echo "실패 — 사본 정리"; scripts/rehearsal/down.sh --quiet; fi' EXIT

echo "[1/4] 사본 DB $DB (opsdb 읽기만)"
docker exec ops-postgres createdb -U ops "$DB"
docker exec ops-postgres sh -c "pg_dump -U ops -Fc opsdb | pg_restore -U ops -d $DB --no-owner --exit-on-error"
P="docker exec ops-postgres psql -U ops -d $DB -Atc"
$P "update ops_system_config set value='false' where key='email_collection_enabled'" >/dev/null   # 사본이 메일함을 읽지 않게
# 사본 실험 실행기가 복제된 대기 작업을 돌리지 않게(소스 마운트면 원본 작업 폴더에 결과 파일을 쓴다 — 리뷰 11/12)
$P "update eval_experiment_job set status='failed' where status in ('queued','running')" >/dev/null
docker exec ops-redis redis-cli -n $RDB FLUSHDB >/dev/null

echo "[2/4] 사본 백엔드 $BE (:$BE_PORT, 이미지 smagentlab-backend:$TAG$([ "${REHEARSAL_SOURCE:-0}" = 1 ] && echo ' + 소스 마운트'))"
ORIG_ENV=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' ops-backend)
# 외부를 가리키는 칸은 아는 것만 통과 — 새 저장소·웹훅 칸이 생기면 사본이 원본 것을 그대로 쓰지 않게 멈춘다(리뷰 11/12)
KNOWN='^(DATABASE_URL|REDIS_URL|OLLAMA_BASE_URL|INHOUSE_LLM_BASE_URL|OPENAI_COMPAT_BASE_URL|URL_IMPORT_ALLOWED_HOSTS)$'
UNKNOWN=$(echo "$ORIG_ENV" | cut -d= -f1 | grep -E '(_URL|_DSN|_URI|_HOST|_HOSTS|WEBHOOK|ENDPOINT)$' | grep -vE "$KNOWN" || true)
[ -z "$UNKNOWN" ] || { echo "모르는 외부 연결 칸: $UNKNOWN — up.sh에서 사본 값을 정한 뒤 다시"; exit 1; }
# 원본 환경을 그대로 쓰되 DB·Redis·컨플 동기화·JWT 키만 바꾼다(사본 토큰이 원본에서 통하지 않게). 시크릿은 이 파일에만, 끝나면 지움
echo "$ORIG_ENV" | grep -vE '^(DATABASE_URL|REDIS_URL|CONFLUENCE_SYNC|JWT_SECRET_KEY|PATH|HOSTNAME|HOME|LANG|GPG_KEY|PYTHON_[A-Z_]*)=' | grep -v '^$' > "$ENVF"
ORIG_DB=$(echo "$ORIG_ENV" | grep '^DATABASE_URL=' | cut -d= -f2-)
{
  echo "DATABASE_URL=${ORIG_DB%/*}/$DB"
  echo "REDIS_URL=redis://ops-redis:6379/$RDB"
  echo "CONFLUENCE_SYNC=false"
  echo "JWT_SECRET_KEY=rehearsal-$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
} >> "$ENVF"
SRC=(); [ "${REHEARSAL_SOURCE:-0}" = 1 ] && SRC=(-v "$(pwd -W 2>/dev/null || pwd)/backend:/app")
docker network create "$NET" >/dev/null
docker run -d --name "$BE" --network smagentlab_default -p "127.0.0.1:$BE_PORT:8000" --env-file "$ENVF" \
  --add-host host.docker.internal:host-gateway -v smagentlab_model-cache:/root/.cache/huggingface "${SRC[@]}" \
  "smagentlab-backend:$TAG" >/dev/null
rm -f "$ENVF"
docker network connect --alias backend "$NET" "$BE"

echo "[3/4] 사본 화면 $FE (:$FE_PORT)"
docker run -d --name "$FE" --network "$NET" -p "127.0.0.1:$FE_PORT:80" \
  -e REAL_IP_FROM=127.0.0.1/32 -e REAL_IP_FROM_2=127.0.0.1/32 "smagentlab-frontend:$TAG" >/dev/null

echo "[4/4] 백엔드 기동 대기"
# 새 컨테이너라 로그 전체를 본다(--since는 호스트·VM 시계가 어긋나면 기동 줄을 놓친다 — 리뷰 11/12).
# 로그는 변수로 받아 검사 — `docker logs | grep -q`는 SIGPIPE로 pipefail이 실패를 내 조용히 빠졌다(11/12 첫 실행).
# Traceback 글자로는 멈추지 않는다(기동 중 백그라운드 작업 경고가 먼저 찍힐 수 있음) — 기동 완료·실패 줄이나 컨테이너 종료만 본다.
LOGS=""
for _ in $(seq 1 100); do
  LOGS=$(docker logs "$BE" 2>&1 || true)
  [[ "$LOGS" =~ startup\ complete|startup\ failed ]] && break
  [ "$(docker inspect -f '{{.State.Running}}' "$BE")" = true ] || break
  sleep 3
done
if [[ ! "$LOGS" =~ startup\ complete ]]; then
  echo "$LOGS" | tail -20; echo "사본 백엔드 기동 실패"; exit 1
fi
code=$(curl -s -w '\n%{http_code}' "http://127.0.0.1:$FE_PORT/health" | tail -1 || true)   # -o /dev/null은 MSYS_NO_PATHCONV 아래 curl.exe가 못 씀(종료 23)
[ "$code" = 200 ] || { echo "사본 화면 → 백엔드 연결 실패($code)"; exit 1; }
echo "준비 끝 — 화면 http://127.0.0.1:$FE_PORT · 백엔드 http://127.0.0.1:$BE_PORT · 다음: node scripts/rehearsal/run.mjs"
