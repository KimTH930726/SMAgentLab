#!/bin/bash
# ============================================================
# Ops-Navigator 이미지 업데이트 (폐쇄망 서버에서 실행)
#
# 코드 수정 후 새 이미지를 반입했을 때 사용
# 기존 DB 데이터(pgdata, redisdata)는 유지됨
#
# 사용법:
#   cd SMAgentLab
#   bash scripts/update-images.sh <새이미지파일.tar.gz>
# ============================================================
set -e

IMPORT_FILE=${1:-}

if [ -z "${IMPORT_FILE}" ] || [ ! -f "${IMPORT_FILE}" ]; then
  echo "사용법: bash scripts/update-images.sh <이미지파일.tar.gz>"
  exit 1
fi

echo "=========================================="
echo " Ops-Navigator 이미지 업데이트"
echo "=========================================="

# 1. 서비스 중지
echo ""
echo "[1/4] 서비스 중지 중..."
docker compose down

# 2. 새 이미지 로드
echo ""
echo "[2/4] 새 이미지 로드 중..."
docker load -i "${IMPORT_FILE}"

# 3. 서비스 재시작
echo ""
echo "[3/4] 서비스 재시작 중..."
docker compose up -d --no-build

# 4. 상태 확인
echo ""
echo "[4/4] 서비스 상태"
docker compose ps
echo ""
echo "DB 데이터는 유지됩니다 (pgdata, redisdata 볼륨)."
echo "백엔드 기동 확인: curl http://localhost:8000/health"
