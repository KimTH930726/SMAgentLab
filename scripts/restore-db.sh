#!/bin/bash
# ============================================================
# Ops-Navigator DB 복원
#
# 주의: 복원은 기존 데이터를 덮어씁니다. (pg_dump --clean --if-exists)
#
# 사용법:
#   bash scripts/restore-db.sh <백업파일.sql.gz>
# ============================================================
set -e

BACKUP_FILE=${1:-}

if [ -z "${BACKUP_FILE}" ] || [ ! -f "${BACKUP_FILE}" ]; then
  echo "사용법: bash scripts/restore-db.sh <백업파일.sql.gz>"
  echo ""
  echo "사용 가능한 백업:"
  ls -lh backups/*.sql.gz 2>/dev/null || echo "  (backups/ 디렉토리에 백업 없음)"
  exit 1
fi

# .env에서 DB 정보 읽기
if [ -f ".env" ]; then
  POSTGRES_USER=$(grep -E "^POSTGRES_USER=" .env | cut -d= -f2 | tr -d '"' | tr -d "'")
  POSTGRES_DB=$(grep -E "^POSTGRES_DB=" .env | cut -d= -f2 | tr -d '"' | tr -d "'")
fi
POSTGRES_USER=${POSTGRES_USER:-ops}
POSTGRES_DB=${POSTGRES_DB:-opsdb}

CONTAINER=${POSTGRES_CONTAINER:-ops-postgres}

echo "=========================================="
echo " Ops-Navigator DB 복원"
echo " 컨테이너: ${CONTAINER}"
echo " DB:       ${POSTGRES_DB} (user: ${POSTGRES_USER})"
echo " 백업파일: ${BACKUP_FILE}"
echo "=========================================="
echo ""
echo "주의: 기존 ${POSTGRES_DB} 데이터가 덮어씌워집니다."
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
gunzip -c "${BACKUP_FILE}" | docker exec -i "${CONTAINER}" psql \
  -U "${POSTGRES_USER}" \
  -d "${POSTGRES_DB}" \
  --quiet

echo ""
echo "복원 완료. 백엔드 재시작 권장:"
echo "  docker compose -f docker-compose.yml -f docker-compose.prod.yml restart backend"
