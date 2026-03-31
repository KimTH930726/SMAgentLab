#!/bin/bash
# ============================================================
# Ops-Navigator 폐쇄망 배포 (폐쇄망 서버에서 실행)
#
# 사전 조건:
#   - Docker + Docker Compose 설치됨
#   - smagentlab-images-*.tar.gz 파일 전달됨
#   - SMAgentLab 소스 코드 (docker-compose.yml, init/, .env) 전달됨
#
# 사용법:
#   cd SMAgentLab
#   bash scripts/import-and-run.sh [이미지파일]
# ============================================================
set -e

IMPORT_FILE=${1:-smagentlab-images-latest.tar.gz}

if [ ! -f "${IMPORT_FILE}" ]; then
  echo "오류: ${IMPORT_FILE} 파일을 찾을 수 없습니다."
  echo ""
  echo "사용법: bash scripts/import-and-run.sh <이미지파일.tar.gz>"
  exit 1
fi

echo "=========================================="
echo " Ops-Navigator 폐쇄망 배포"
echo " 이미지: ${IMPORT_FILE}"
echo "=========================================="

# 1. 이미지 로드
echo ""
echo "[1/3] 이미지 로드 중... (1~2분 소요)"
docker load -i "${IMPORT_FILE}"

# 2. .env 확인
echo ""
if [ ! -f ".env" ]; then
  echo "[주의] .env 파일이 없습니다. 기본값으로 실행됩니다."
  echo "  운영 환경에서는 .env 파일을 생성하세요."
  echo "  참고: .env.example"
else
  echo "[확인] .env 파일 감지됨"
fi

# 3. 서비스 시작 (빌드 없이)
echo ""
echo "[2/3] 서비스 시작 중..."
docker compose up -d --no-build

# 4. 상태 확인
echo ""
echo "[3/3] 서비스 상태"
echo "------------------------------------------"
docker compose ps
echo ""

# health check 대기
echo "백엔드 기동 대기 중..."
for i in $(seq 1 30); do
  if curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health 2>/dev/null | grep -q "200"; then
    echo ""
    echo "=========================================="
    echo " 배포 완료!"
    echo "  백엔드:    http://localhost:8000"
    echo "  프론트엔드: http://localhost:8501"
    echo "  API 문서:  http://localhost:8000/docs"
    echo "=========================================="
    exit 0
  fi
  sleep 2
  printf "."
done

echo ""
echo "[경고] 백엔드가 60초 내에 응답하지 않습니다."
echo "  docker compose logs backend 로 로그를 확인하세요."
