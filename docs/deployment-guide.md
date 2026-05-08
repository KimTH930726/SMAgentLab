# SMAgentLab 사내 배포 방안

> 사내 Git + NAS 환경 기준 (각 팀이 직접 `docker compose build` 가능한 환경)
>
> **인터넷이 차단된 폐쇄망 리눅스 서버**에 배포해야 한다면
> [`deployment-closed-network.md`](deployment-closed-network.md) 를 참고하세요.

---

## 아키텍처 개요

```
docker compose up --build
    ├── ops-postgres   PostgreSQL + pgvector   port 5432  (벡터 DB, 내부 전용)
    ├── ops-backend    FastAPI                 port 8000  (API 서버)
    └── ops-frontend   React + nginx           port 8501  (웹 UI)
```

- 팀별로 독립된 DB 인스턴스 (데이터 격리 자동)
- 접속 URL: `http://localhost:8501` 하나로 통일
- 외부 의존: 사내 LLM (URL 동일), 임베딩 모델 (NAS에서 1회 복사)

---

## 1. 배포 준비 (최초 1회, 배포자 수행)

### 1-1. 사내 Git에 소스 등록

```bash
# 사내 Git에 저장소 생성 후
git remote add internal http://사내git주소/SMAgentLab.git
git push internal main
```

### 1-2. 임베딩 모델 NAS 업로드

허깅페이스 접근 가능한 PC에서 1회 수행:

```bash
# 모델 다운로드
python -c "
from sentence_transformers import SentenceTransformer
model = SentenceTransformer('paraphrase-multilingual-mpnet-base-v2')
model.save('./models/paraphrase-multilingual-mpnet-base-v2')
"

# NAS에 업로드
cp -r ./models /nas경로/SMAgentLab/models
```

> 모델 크기: 약 420MB

---

## 2. 팀별 온보딩

### 2-1. 소스 받기

```bash
git clone http://사내git주소/SMAgentLab.git
cd SMAgentLab
```

### 2-2. 모델 복사 (NAS → 로컬)

```bash
cp -r /nas경로/SMAgentLab/models ./models
```

### 2-3. 환경 설정

```bash
cp .env.example .env
```

`.env` 파일에서 아래 항목 수정:

```env
# 사내 LLM 설정 (전팀 동일)
LLM_PROVIDER=inhouse
INHOUSE_LLM_URL=http://사내LLM주소

# 보안 키 — 팀별로 새로 생성 (아래 명령어 사용)
JWT_SECRET_KEY=
FERNET_SECRET_KEY=

# 어드민 초기 비밀번호
ADMIN_DEFAULT_PASSWORD=원하는값
```

보안 키 생성:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
# 위 명령어를 2번 실행해서 JWT_SECRET_KEY, FERNET_SECRET_KEY에 각각 입력
```

### 2-4. 실행

```bash
docker compose up --build -d
```

브라우저에서 `http://localhost:8501` 접속

> 최초 빌드 시 PyTorch 설치로 10~15분 소요. 이후 재실행은 수 초.

---

## 3. 초기 세팅 (실행 후 어드민 작업)

| 순서 | 작업 | 경로 |
|------|------|------|
| 1 | admin 계정으로 로그인 | 우측 상단 로그인 |
| 2 | 파트 생성 | 어드민 → 사용자 관리 → 파트 관리 |
| 3 | 네임스페이스 생성 | 어드민 → 네임스페이스 |
| 4 | 지식베이스 등록 | 어드민 → 지식베이스 |
| 5 | (선택) HTTP 도구 등록 | 어드민 → HTTP 도구 |
| 6 | 일반 사용자 계정 생성 | 어드민 → 사용자 관리 |

---

## 4. 일반 사용자 사용 방법

1. `http://localhost:8501` 접속
2. 회원가입 → 사내 LLM API 키 입력
3. 네임스페이스 선택 후 채팅

---

## 5. 업데이트 배포

소스 변경 시:

```bash
git pull origin main
docker compose build backend frontend
docker compose up -d --force-recreate backend frontend
```

> DB 스키마 변경이 있는 경우 별도 마이그레이션 안내 확인

---

## 6. 데이터 백업 / 복원

```bash
# 백업
docker exec ops-postgres pg_dump -U ops opsdb > backup_$(date +%Y%m%d).sql

# 복원
docker exec -i ops-postgres psql -U ops opsdb < backup.sql
```

---

## 7. 자주 묻는 문제

| 증상 | 원인 | 해결 |
|------|------|------|
| 빌드 중 모델 다운로드 실패 | 허깅페이스 방화벽 차단 | `models/` 폴더 NAS에서 복사됐는지 확인 |
| 컨테이너 시작 안 됨 | DB 헬스체크 대기 | 1~2분 후 재시도 |
| LLM 응답 없음 | API 키 미입력 | 회원 정보에서 사내 LLM API 키 입력 |
| 포트 충돌 | 8501/8000 사용 중 | `.env`에서 포트 변경 후 재실행 |
