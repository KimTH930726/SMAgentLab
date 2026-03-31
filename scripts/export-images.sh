#!/bin/bash
# ============================================================
# Ops-Navigator 이미지 내보내기 (인터넷 PC에서 실행)
#
# 사용법:
#   cd SMAgentLab
#   bash scripts/export-images.sh [태그]
#
# 결과물: smagentlab-images-{태그}.tar.gz (약 1.5~2GB)
# 이 파일을 USB/SCP 등으로 폐쇄망 서버에 전달
# ============================================================
set -e

TAG=${1:-latest}
EXPORT_FILE="smagentlab-images-${TAG}.tar.gz"

echo "=========================================="
echo " Ops-Navigator 이미지 빌드 + 내보내기"
echo " 태그: ${TAG}"
echo "=========================================="

# 1. 백엔드/프론트엔드 빌드
echo ""
echo "[1/4] 이미지 빌드 중..."
docker compose build --no-cache

# 2. 외부 이미지 pull (postgres, redis)
echo ""
echo "[2/4] 외부 이미지 pull..."
docker compose pull postgres redis

# 3. 모든 이미지를 tar.gz로 내보내기
echo ""
echo "[3/4] 이미지 내보내기 → ${EXPORT_FILE}"
docker save \
  smagentlab-backend:latest \
  smagentlab-frontend:latest \
  pgvector/pgvector:pg16 \
  redis:7-alpine \
| gzip > "${EXPORT_FILE}"

# 4. 결과 확인
echo ""
echo "[4/4] 완료!"
SIZE=$(du -h "${EXPORT_FILE}" | cut -f1)
echo "  파일: ${EXPORT_FILE}"
echo "  크기: ${SIZE}"
echo ""
echo "이 파일을 폐쇄망 서버로 전달 후"
echo "  bash scripts/import-and-run.sh ${EXPORT_FILE}"
echo "으로 실행하세요."
