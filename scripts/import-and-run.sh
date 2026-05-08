#!/bin/bash
# ============================================================
# Ops-Navigator 폐쇄망 배포 (폐쇄망 리눅스 서버에서 실행)
#
# 사전 조건:
#   - Docker 24+ 및 Docker Compose v2 설치됨
#   - smagentlab-images-*.tar.gz 파일 반입됨
#   - 다음 파일들이 현재 디렉토리에 있음:
#       docker-compose.yml
#       docker-compose.prod.yml
#       init/                       (DB 초기화 SQL)
#       .env                        (시크릿/환경변수)
#       scripts/                    (Teams 헬퍼 등 정적 자산)
#
# 사용법:
#   cd /opt/smagentlab            # 또는 배포 디렉토리
#   bash scripts/import-and-run.sh [이미지파일.tar.gz]
# ============================================================
set -e

IMPORT_FILE=${1:-}

# 인자 미지정 시 가장 최근 tar.gz 자동 탐색
if [ -z "${IMPORT_FILE}" ]; then
  IMPORT_FILE=$(ls -t smagentlab-images-*.tar.gz 2>/dev/null | head -1 || true)
fi

if [ -z "${IMPORT_FILE}" ] || [ ! -f "${IMPORT_FILE}" ]; then
  echo "오류: 이미지 파일을 찾을 수 없습니다."
  echo ""
  echo "사용법: bash scripts/import-and-run.sh <smagentlab-images-X.tar.gz>"
  exit 1
fi

# .env 검증
if [ ! -f ".env" ]; then
  echo "오류: .env 파일이 없습니다."
  echo "  .env.example 을 복사한 후 시크릿 키들을 채우세요."
  exit 1
fi

# IMAGE_TAG 추출 (compose가 동일 태그를 참조하도록)
IMAGE_TAG=$(grep -E "^IMAGE_TAG=" .env | cut -d= -f2 | tr -d '"' | tr -d "'")
IMAGE_TAG=${IMAGE_TAG:-latest}

echo "=========================================="
echo " Ops-Navigator 폐쇄망 배포"
echo " 이미지 파일: ${IMPORT_FILE}"
echo " 태그:        ${IMAGE_TAG}"
echo "=========================================="

# 1. 이미지 로드
echo ""
echo "[1/4] 이미지 로드 중... (1~3분 소요)"
docker load -i "${IMPORT_FILE}"

# 2. 로드된 이미지 확인
echo ""
echo "[2/4] 로드된 이미지 확인..."
docker images | grep -E "smagentlab|pgvector|redis" | head -10

# 3. 운영용 compose로 서비스 시작 (빌드 없이)
echo ""
echo "[3/4] 서비스 시작 중 (운영 모드, --no-build)..."
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --no-build

# 4. 상태 + 헬스체크
echo ""
echo "[4/4] 서비스 상태"
echo "------------------------------------------"
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
echo ""

echo "백엔드 기동 대기 중..."
for i in $(seq 1 60); do
  if curl -fs http://localhost:8000/health >/dev/null 2>&1; then
    echo ""
    echo "=========================================="
    echo " 배포 완료!"
    echo "  백엔드:    http://$(hostname -I | awk '{print $1}'):8000"
    echo "  프론트엔드: http://$(hostname -I | awk '{print $1}'):8501"
    echo "  API 문서:  http://$(hostname -I | awk '{print $1}'):8000/docs"
    echo "=========================================="
    exit 0
  fi
  sleep 2
  printf "."
done

echo ""
echo "[경고] 백엔드가 120초 내에 응답하지 않습니다."
echo "  로그 확인: docker compose -f docker-compose.yml -f docker-compose.prod.yml logs backend"
exit 1
