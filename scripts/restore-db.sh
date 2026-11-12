#!/bin/bash
# ============================================================
# OpsLens DB 복원
#
# 주의: 복원은 기존 데이터를 덮어씁니다. (pg_dump --clean --if-exists)
#
# 사용법:
#   bash scripts/restore-db.sh <백업파일.sql.gz>
# ============================================================
set -eo pipefail

BACKUP_FILE=${1:-}

if [ -z "${BACKUP_FILE}" ] || [ ! -f "${BACKUP_FILE}" ]; then
  echo "사용법: bash scripts/restore-db.sh <백업파일.sql.gz>"
  echo ""
  echo "사용 가능한 백업:"
  ls -lh backups/*.sql.gz 2>/dev/null || echo "  (backups/ 디렉토리에 백업 없음)"
  exit 1
fi

# DB 정보: 환경변수 > .env > 기본값 (.env에 줄이 없어도 멈추지 않게 || true — pipefail)
if [ -f ".env" ]; then
  POSTGRES_USER=${POSTGRES_USER:-$(grep -E "^POSTGRES_USER=" .env | cut -d= -f2 | tr -d '"' | tr -d "'" || true)}
  POSTGRES_DB=${POSTGRES_DB:-$(grep -E "^POSTGRES_DB=" .env | cut -d= -f2 | tr -d '"' | tr -d "'" || true)}
fi
POSTGRES_USER=${POSTGRES_USER:-ops}
POSTGRES_DB=${POSTGRES_DB:-opsdb}

CONTAINER=${POSTGRES_CONTAINER:-ops-postgres}

echo "=========================================="
echo " OpsLens DB 복원"
echo " 컨테이너: ${CONTAINER}"
echo " DB:       ${POSTGRES_DB} (user: ${POSTGRES_USER})"
echo " 백업파일: ${BACKUP_FILE}"
echo "=========================================="
echo ""
echo "주의: 기존 ${POSTGRES_DB} 데이터가 덮어씌워집니다."
echo "      복원 전에 backend를 멈추세요(docker compose ... stop backend) — 복원은 한 트랜잭션이라 끝날 때까지 표 잠금을 쥔다."
read -p "계속하시겠습니까? (yes/no): " confirm
if [ "${confirm}" != "yes" ]; then
  echo "취소됨."
  exit 0
fi

if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
  echo "오류: ${CONTAINER} 컨테이너가 실행 중이 아닙니다."
  exit 1
fi

echo ""
echo "복원 진행 중..."
# 오류가 나면 바로 멈추고(ON_ERROR_STOP) 전부 되돌린다(--single-transaction) — 반쯤 복원된 DB로 기동하지 않게(11/06 반입 전 검사)
gunzip -c "${BACKUP_FILE}" | docker exec -i "${CONTAINER}" psql \
  -U "${POSTGRES_USER}" \
  -d "${POSTGRES_DB}" \
  -v ON_ERROR_STOP=1 --single-transaction \
  --quiet

# 답 캐시(Redis semcache:*)는 DB 밖에 있어 복원으로 안 돌아간다 — 남기면 복원된 DB에 없는 문서를 근거로 한 옛 답이 TTL 동안 나간다
# (11/11 시연 녹화 실례: 사본 DB로 바꿨는데 캐시 답이 DB에 없는 #65251을 근거로 냄). 답 캐시 키만 지운다(로그인 제한 등 다른 키는 그대로).
REDIS_CONTAINER=${REDIS_CONTAINER:-ops-redis}
REDIS_DB=${REDIS_DB:-}
if [ -z "${REDIS_DB}" ] && [ -f ".env" ]; then
  REDIS_DB=$(grep -E "^REDIS_URL=" .env | sed -E 's#.*/([0-9]+)[[:space:]]*$#\1#' | grep -E '^[0-9]+$' || true)
fi
REDIS_DB=${REDIS_DB:-0}
if docker ps --format '{{.Names}}' | grep -q "^${REDIS_CONTAINER}$"; then
  n=$(docker exec "${REDIS_CONTAINER}" sh -c "redis-cli -n ${REDIS_DB} --scan --pattern 'semcache:*' | wc -l")
  # 키 하나씩(줄 단위) — 컬렉션 이름에 띄어쓰기("앱 시스템")가 있어 xargs로 넘기면 키가 쪼개져 안 지워졌다(리뷰 11/11)
  docker exec "${REDIS_CONTAINER}" sh -c "redis-cli -n ${REDIS_DB} --scan --pattern 'semcache:*' | while IFS= read -r k; do redis-cli -n ${REDIS_DB} UNLINK \"\$k\" >/dev/null; done"
  left=$(docker exec "${REDIS_CONTAINER}" sh -c "redis-cli -n ${REDIS_DB} --scan --pattern 'semcache:*' | wc -l")
  echo "답 캐시 비움: ${n}개 → 남은 ${left}개 (Redis db ${REDIS_DB})"
  if [ "${left}" != "0" ]; then
    echo "경고: 답 캐시 ${left}개가 남았습니다 — 관리자 › 설정 › 캐시에서 비우거나 백엔드 재시작 전 다시 실행하세요"
  fi
else
  echo "경고: ${REDIS_CONTAINER} 컨테이너가 없어 답 캐시를 못 비웠습니다 — 기동 후 관리자 › 설정 › 캐시에서 비우거나 redis-cli로 semcache:* 삭제"
fi

echo ""
echo "복원 완료. 백엔드 재시작 권장:"
echo "  docker compose -f docker-compose.yml -f docker-compose.prod.yml restart backend"
