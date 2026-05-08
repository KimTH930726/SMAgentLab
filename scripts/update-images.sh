#!/bin/bash
# ============================================================
# Ops-Navigator 이미지 업데이트 (폐쇄망 서버에서 실행)
#
# 새 버전 이미지를 반입했을 때 사용
# pgdata, redisdata 볼륨은 유지되어 DB 데이터 손실 없음
#
# 사용법:
#   cd /opt/smagentlab
#   bash scripts/update-images.sh <새이미지파일.tar.gz>
#
# 주의: .env의 IMAGE_TAG도 새 버전으로 업데이트 후 실행하세요
# ============================================================
set -e

IMPORT_FILE=${1:-}

if [ -z "${IMPORT_FILE}" ] || [ ! -f "${IMPORT_FILE}" ]; then
  echo "사용법: bash scripts/update-images.sh <smagentlab-images-X.tar.gz>"
  exit 1
fi

if [ ! -f ".env" ]; then
  echo "오류: .env 파일이 없습니다."
  exit 1
fi

IMAGE_TAG=$(grep -E "^IMAGE_TAG=" .env | cut -d= -f2 | tr -d '"' | tr -d "'")
IMAGE_TAG=${IMAGE_TAG:-latest}

COMPOSE_FILES="-f docker-compose.yml -f docker-compose.prod.yml"

echo "=========================================="
echo " Ops-Navigator 이미지 업데이트"
echo " 새 태그(.env): ${IMAGE_TAG}"
echo "=========================================="

# 1. (선택) 자동 백업
echo ""
read -p "[선택] 업데이트 전 DB 백업을 받으시겠습니까? (y/N): " backup_yn
if [ "${backup_yn}" = "y" ] || [ "${backup_yn}" = "Y" ]; then
  bash scripts/backup-db.sh || echo "백업 실패 — 계속 진행"
fi

# 2. 새 이미지 로드
echo ""
echo "[1/3] 새 이미지 로드 중..."
docker load -i "${IMPORT_FILE}"

# 3. 컨테이너 재생성 (DB/Redis 볼륨은 유지, app만 교체)
echo ""
echo "[2/3] 백엔드/프론트엔드 컨테이너 재생성 중..."
docker compose ${COMPOSE_FILES} up -d --no-build --force-recreate backend frontend

# 4. 상태 확인
echo ""
echo "[3/3] 서비스 상태"
docker compose ${COMPOSE_FILES} ps
echo ""
echo "기동 확인: curl http://localhost:8000/health"
echo "이전 이미지 정리: docker image prune -f"
