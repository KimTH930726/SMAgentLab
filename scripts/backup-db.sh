#!/bin/bash
# ============================================================
# Ops-Navigator DB 백업
#
# pg_dump를 컨테이너 내부에서 실행하여 호스트 backups/ 디렉토리에 저장.
# pgvector 확장도 함께 덤프 (CREATE EXTENSION 포함).
#
# 사용법:
#   bash scripts/backup-db.sh [출력파일명]
#
#   예) bash scripts/backup-db.sh
#       → backups/opsdb-20260422-153012.sql.gz
# ============================================================
set -e

BACKUP_DIR="backups"
mkdir -p "${BACKUP_DIR}"

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
DEFAULT_FILE="${BACKUP_DIR}/opsdb-${TIMESTAMP}.sql.gz"
OUTPUT_FILE=${1:-${DEFAULT_FILE}}

# .env에서 DB 정보 읽기 (없으면 기본값)
if [ -f ".env" ]; then
  POSTGRES_USER=$(grep -E "^POSTGRES_USER=" .env | cut -d= -f2 | tr -d '"' | tr -d "'")
  POSTGRES_DB=$(grep -E "^POSTGRES_DB=" .env | cut -d= -f2 | tr -d '"' | tr -d "'")
fi
POSTGRES_USER=${POSTGRES_USER:-ops}
POSTGRES_DB=${POSTGRES_DB:-opsdb}

CONTAINER=${POSTGRES_CONTAINER:-ops-postgres}

echo "=========================================="
echo " Ops-Navigator DB 백업"
echo " 컨테이너: ${CONTAINER}"
echo " DB:       ${POSTGRES_DB} (user: ${POSTGRES_USER})"
echo " 출력:     ${OUTPUT_FILE}"
echo "=========================================="

# 컨테이너 가동 확인
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
  echo "오류: ${CONTAINER} 컨테이너가 실행 중이 아닙니다."
  exit 1
fi

echo ""
echo "백업 진행 중..."
docker exec "${CONTAINER}" pg_dump \
  -U "${POSTGRES_USER}" \
  -d "${POSTGRES_DB}" \
  --clean --if-exists \
  --no-owner --no-privileges \
  | gzip > "${OUTPUT_FILE}"

SIZE=$(du -h "${OUTPUT_FILE}" | cut -f1)
echo ""
echo "완료: ${OUTPUT_FILE} (${SIZE})"
echo ""
echo "복원: bash scripts/restore-db.sh ${OUTPUT_FILE}"
