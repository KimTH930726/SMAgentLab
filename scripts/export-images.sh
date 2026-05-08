#!/bin/bash
# ============================================================
# Ops-Navigator 이미지 빌드 + 내보내기 (인터넷 PC에서 실행)
#
# 사용법:
#   cd SMAgentLab
#   bash scripts/export-images.sh [태그]
#
#   예) bash scripts/export-images.sh v2.16
#       태그 생략 시 .env의 IMAGE_TAG 또는 'latest'
#
# 결과물: smagentlab-images-{태그}.tar.gz (약 1.5~2GB)
# 이 파일을 USB/SCP 등으로 폐쇄망 서버에 전달
# ============================================================
set -e

# .env에서 IMAGE_TAG 읽기 (있으면)
if [ -f ".env" ]; then
  ENV_TAG=$(grep -E "^IMAGE_TAG=" .env | cut -d= -f2 | tr -d '"' | tr -d "'")
fi

TAG=${1:-${ENV_TAG:-latest}}
EXPORT_FILE="smagentlab-images-${TAG}.tar.gz"

BACKEND_IMG="smagentlab-backend:${TAG}"
FRONTEND_IMG="smagentlab-frontend:${TAG}"
PG_IMG="pgvector/pgvector:pg16"
REDIS_IMG="redis:7-alpine"

echo "=========================================="
echo " Ops-Navigator 이미지 빌드 + 내보내기"
echo " 태그: ${TAG}"
echo "=========================================="

# 1. 백엔드/프론트엔드 빌드 (지정 태그로)
echo ""
echo "[1/4] 이미지 빌드 중... (10~15분 소요)"
IMAGE_TAG="${TAG}" docker compose build --no-cache

# 2. 외부 이미지 pull
echo ""
echo "[2/4] 외부 이미지 pull..."
docker pull "${PG_IMG}"
docker pull "${REDIS_IMG}"

# 3. 모든 이미지를 tar.gz로 내보내기
echo ""
echo "[3/4] 이미지 내보내기 → ${EXPORT_FILE}"
docker save \
  "${BACKEND_IMG}" \
  "${FRONTEND_IMG}" \
  "${PG_IMG}" \
  "${REDIS_IMG}" \
| gzip > "${EXPORT_FILE}"

# 4. 결과 확인
echo ""
echo "[4/4] 완료!"
SIZE=$(du -h "${EXPORT_FILE}" | cut -f1)
echo "  파일: ${EXPORT_FILE}"
echo "  크기: ${SIZE}"
echo ""
echo "다음 단계:"
echo "  1) ${EXPORT_FILE} 를 폐쇄망 서버로 전송"
echo "  2) docker-compose.yml, docker-compose.prod.yml, init/, .env 도 함께 전송"
echo "  3) 서버에서: bash scripts/import-and-run.sh ${EXPORT_FILE}"
