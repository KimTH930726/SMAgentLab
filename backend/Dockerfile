FROM python:3.12-slim

WORKDIR /app

# 시스템 의존성
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# PyTorch CPU 전용 설치 (GPU 미포함으로 이미지 크기 절감)
RUN pip install --no-cache-dir torch \
    --index-url https://download.pytorch.org/whl/cpu

# Sentence-Transformers는 임베딩 모델을 huggingface.co에서 미리 받아 캐싱해야 하므로
# 나머지 의존성(requirements.txt)보다 먼저, 별도 레이어로 고정 설치한다.
# requirements.txt COPY를 이 앞에 두면 새 패키지 하나만 추가해도(예: msal, 2026-07-29)
# 그 아래 모든 레이어가 캐시 무효화되어 매번 huggingface.co 재다운로드가 발생한다 —
# 사내망에서 huggingface.co 접속이 막혀 있으면 그때마다 빌드 자체가 실패하므로,
# 버전이 잘 안 바뀌는 이 레이어를 requirements.txt보다 앞에 분리해 방지한다.
RUN pip install --no-cache-dir sentence-transformers==3.3.1

ENV TRANSFORMERS_CACHE=/root/.cache/huggingface
ENV HF_HOME=/root/.cache/huggingface

# 빌드 시 임베딩 모델 사전 다운로드 (컨테이너 시작 지연 방지).
# 이 경로(HF_HOME)는 런타임에 model-cache 명명 볼륨으로 덮어써지므로, 빌드 시점에
# huggingface.co 접속이 막혀 실패해도 빌드 자체를 막지 않는다(|| echo) — 그 볼륨에
# 이미 모델이 캐싱돼 있다면(로컬 개발 환경 등) 런타임엔 정상 동작한다. 다만 볼륨이
# 비어있는 새 환경(진짜 폐쇄망 최초 배포 등)에서는 이 레이어가 실제로 모델을
# 받아둬야 런타임에도 실패하지 않으므로, huggingface.co 접속이 되는 빌드 환경에서
# 최소 한 번은 이 단계가 성공해야 한다.
ARG EMBEDDING_MODEL=paraphrase-multilingual-mpnet-base-v2
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('${EMBEDDING_MODEL}')" \
    || echo "[build] 임베딩 모델 사전 다운로드 실패(네트워크?) — 런타임 캐시 볼륨에 의존"

# 리랭커(CrossEncoder) 모델도 같은 이유로 빌드 시점에 번들링 — 폐쇄망에서
# reranker_enabled=True로 전환하려면 이 레이어가 이미지에 들어있어야 한다
# (core/config.py의 reranker_model 기본값과 동일 모델). 실패해도 빌드를 막지 않는
# 이유는 위 임베딩 모델과 동일.
ARG RERANKER_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
RUN python -c "from sentence_transformers import CrossEncoder; CrossEncoder('${RERANKER_MODEL}')" \
    || echo "[build] 리랭커 모델 사전 다운로드 실패(네트워크?) — 런타임 캐시 볼륨에 의존"

# 나머지 Python 의존성 설치 — 이 레이어가 바뀌어도 위 임베딩 모델 캐시는 그대로 재사용됨
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스 코드 복사
COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
