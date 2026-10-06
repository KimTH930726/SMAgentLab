# 폐쇄망 리눅스 서버 배포 가이드

> 인터넷이 차단된 사내 리눅스 서버에 SMAgentLab(OpsLens)을 배포하는 절차.
> **현재 배포 버전: `v2.119`** (2026-10-02, 직전 운영 반입 `v2.17`). 아래 명령의 `<TAG>`는 배포할 버전으로 바꿔 쓴다.
> 처음 설치는 §1→§2, 기존 서버 업그레이드는 §1→§3(**v2.17에서 올리면 §3-4 필수**).

---

## 0. 배포 전 체크리스트

### 양쪽 환경 사전 준비

| 환경 | 필요 사항 |
|---|---|
| **빌드 PC** (인터넷 가능) | Docker Desktop 또는 Docker 24+, 인터넷 접속, 본 저장소 clone |
| **운영 서버** (폐쇄망) | Linux (Rocky Linux 9 / RHEL 9 / Ubuntu 22.04 LTS 권장), Docker 24+, Docker Compose v2, 디스크 여유 ≥ 30GB (이미지 묶음 + 로드된 이미지 + 직전 버전 1개 유지) |

### 반입할 파일 목록

폐쇄망 서버로 옮겨야 하는 파일·디렉토리:

```
SMAgentLab/
├── docker-compose.yml              # 베이스 compose
├── docker-compose.prod.yml         # 운영 오버라이드 (build 제거, 볼륨 마운트 제거)
├── .env                            # 시크릿 + IMAGE_TAG (운영 PC에서 작성)
├── init/                           # DB 초기화 SQL — 빈 DB 최초 기동 시 1회 자동 실행(01~08 전부 반입)
├── scripts/
│   ├── import-and-run.sh           # 폐쇄망 배포 스크립트
│   ├── update-images.sh            # 버전 업데이트 스크립트
│   ├── backup-db.sh                # DB 백업
│   └── restore-db.sh               # DB 복원
└── smagentlab-images-<TAG>.tar.gz  # 이미지 묶음 (별도 전송)
```

> 소스 코드(`backend/`, `frontend-react/`)는 **반입 불필요** — 이미지에 동봉됩니다(일회성 마이그레이션 스크립트 `backend/scripts/*`도 이미지 안에 있음).

---

## 1. 빌드 PC에서 이미지 생성 (인터넷 환경)

### 1-1. 저장소 준비

```bash
git clone https://your-internal-git/SMAgentLab.git
cd SMAgentLab
git checkout main          # 또는 특정 릴리즈 태그
```

### 1-2. `.env` 작성 (운영용)

#### Step 1) 시크릿 키 먼저 생성 (각 명령을 따로 실행 후 출력값을 복사)

```bash
# JWT 시크릿 (32바이트 hex, 64자)
python -c "import secrets; print(secrets.token_hex(32))"
# 출력 예: a1b2c3d4e5f6...   ← 이 값을 복사

# Fernet 시크릿 (44자, =로 끝남)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# 출력 예: Unq_TuJ4yU_...=    ← 이 값을 복사
```

> `cryptography` 미설치 시: `pip install cryptography`

#### Step 2) `.env` 파일 작성

```bash
cp .env.example .env
```

`.env`를 에디터로 열어 아래 항목을 **위에서 복사한 값들로 채워서** 저장:

```env
# ── 이미지 버전 태그 (필수, :latest 비추천) ────────────────
IMAGE_TAG=v2.119

# ── DB ──────────────────────────────────────────────────
POSTGRES_DB=opsdb
POSTGRES_USER=ops
POSTGRES_PASSWORD=강력한비밀번호로변경
DATABASE_URL=postgresql://ops:강력한비밀번호로변경@postgres:5432/opsdb
# ↑ POSTGRES_PASSWORD와 DATABASE_URL의 비밀번호 일치시켜야 함

# ── LLM (OAuth2 Client Credentials — DevX 게이트웨이) ───
LLM_PROVIDER=inhouse
INHOUSE_LLM_BASE_URL=https://devx-gw.shinsegae-inc.com
INHOUSE_LLM_CLIENT_ID=DevX발급client_id
INHOUSE_LLM_CLIENT_SECRET=DevX발급client_secret
INHOUSE_LLM_AGENT_ID=DevX_agent_id_UUID
INHOUSE_LLM_AGENT_CODE=playground
# ⚠️ DevX dify에 사전 등록된 conversation_id (없으면 첫 호출이 0바이트 응답)
# 우리 자체 메모리(요약+시맨틱 리콜)가 history를 query에 직렬화하므로
# dify의 멀티턴 메모리는 영향 없음 — 시스템 공통 한 개만 사용
INHOUSE_LLM_CONVERSATION_ID=DevX_등록된_conversation_id_UUID

# ── 시크릿 키 (Step 1 출력값 붙여넣기) ─────────────────────
JWT_SECRET_KEY=a1b2c3d4e5f6...           # Step 1의 첫 번째 출력
FERNET_SECRET_KEY=Unq_TuJ4yU_...=        # Step 1의 두 번째 출력

# ── 초기 관리자 ────────────────────────────────────────
ADMIN_DEFAULT_PASSWORD=admin초기비밀번호

# ── 기타 (기본값 그대로) ──────────────────────────────
REDIS_URL=redis://ops-redis:6379/0
BACKEND_URL=http://backend:8000
```

#### Step 3) `.env` 검증

```bash
# 모든 필수 변수가 채워졌는지 확인
grep -E "^(IMAGE_TAG|POSTGRES_PASSWORD|JWT_SECRET_KEY|FERNET_SECRET_KEY)=" .env

# 출력 예 (값이 비어있거나 'change-this' 같은 기본값이면 안 됨):
# IMAGE_TAG=v2.119
# POSTGRES_PASSWORD=Pa$$w0rd!2026
# JWT_SECRET_KEY=a1b2c3...
# FERNET_SECRET_KEY=Unq_TuJ4...=
```

### 1-3. 이미지 빌드 + 내보내기

#### Step 1) Docker 가동 확인

```bash
docker version          # Server 섹션이 보여야 함
docker info             # 에러 없이 실행되어야 함
```

> Windows: Docker Desktop 실행 중인지 확인 (트레이 아이콘 녹색)

#### Step 2) 빌드 실행

**Linux/macOS (bash):**
```bash
bash scripts/export-images.sh <TAG>
```

**Windows (PowerShell):**
```powershell
powershell -ExecutionPolicy Bypass -File scripts\export-images.ps1 -Tag <TAG>
```

**처리 내용:**
1. `docker compose build --no-cache` (백엔드/프론트엔드 빌드, 약 10~15분)
   - 백엔드 빌드 시 임베딩 모델(`nlpai-lab/KURE-v1`, ~2.3GB)과 리랭커 모델(`dragonkue/bge-reranker-v2-m3-ko`, ~2.3GB,
     기본 꺼짐 — 폐쇄망에서 켤 수 있게 번들) **이미지 안에 사전 다운로드**
   - ⚠️ 다운로드가 실패해도 빌드는 `[build] ... 사전 다운로드 실패` 로그만 남기고 계속된다 — 모델 없는 이미지는
     폐쇄망에서 임베딩이 안 된다. Step 3의 모델 포함 확인을 반드시 할 것
2. `pgvector/pgvector:pg16`, `redis:7-alpine` pull
3. 4개 이미지를 단일 tar.gz로 패키징 → `smagentlab-images-<TAG>.tar.gz` (약 4.4GB, 백엔드 이미지 자체는 11.4GB)

#### Step 3) 결과물 검증

```bash
ls -lh smagentlab-images-<TAG>.tar.gz       # 약 4.4GB
docker images | grep -E "smagentlab|pgvector|redis"
# 4개 이미지가 모두 <TAG> 또는 pg16/7-alpine 태그로 있어야 함

# 모델이 이미지에 들어갔는지 확인 — models--nlpai-lab--KURE-v1, models--dragonkue--bge-reranker-v2-m3-ko 둘 다 보여야 함(없으면 인터넷 확인 후 재빌드)
docker run --rm --entrypoint sh smagentlab-backend:<TAG> -c 'ls /root/.cache/huggingface /root/.cache/huggingface/hub | grep -E "KURE|reranker"'
```

#### Step 4) 체크섬 생성 (반입 후 무결성 확인용)

```bash
# Linux/macOS
sha256sum smagentlab-images-<TAG>.tar.gz > smagentlab-images-<TAG>.tar.gz.sha256

# Windows PowerShell
Get-FileHash smagentlab-images-<TAG>.tar.gz -Algorithm SHA256 | `
  ForEach-Object { "$($_.Hash.ToLower())  smagentlab-images-<TAG>.tar.gz" } | `
  Out-File smagentlab-images-<TAG>.tar.gz.sha256 -Encoding ASCII
```

### 1-4. 폐쇄망 서버로 반입

다음 파일들을 함께 전송 (USB/SCP/사내 파일전송):

```
smagentlab-images-<TAG>.tar.gz       # 메인 이미지 묶음
smagentlab-images-<TAG>.tar.gz.sha256 # 체크섬
docker-compose.yml
docker-compose.prod.yml
.env                                  # ⚠️ 시크릿 — 안전한 채널로 전송
init/                                 # 01~08 전부
scripts/import-and-run.sh
scripts/update-images.sh
scripts/backup-db.sh
scripts/restore-db.sh
```

> **시크릿 전송 주의:** `.env`는 별도 보안 채널(암호화 USB, KMS, 사내 시크릿 매니저)로 전송하고, 같은 메일/채팅에 첨부하지 마세요.

---

## 2. 폐쇄망 서버에서 배포

### 2-1. 사전 설치 (서버 최초 1회)

#### A. Docker 설치 — 사내 미러 있는 경우

**Rocky Linux 9 / RHEL 9:**
```bash
sudo dnf install -y dnf-plugins-core
sudo dnf config-manager --add-repo <사내 docker repo URL>
sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker $USER          # 재로그인 필요
```

**Ubuntu 22.04 / 24.04 LTS:**
```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker $USER          # 재로그인 필요
```

#### B. Docker 설치 — 사내 미러도 없는 완전 폐쇄망

빌드 PC에서 오프라인 패키지를 미리 받아서 반입:

**Rocky/RHEL용 (인터넷 PC에서):**
```bash
# 의존성 포함 RPM 일괄 다운로드
mkdir docker-offline && cd docker-offline
dnf download --resolve --alldeps docker-ce docker-ce-cli containerd.io docker-compose-plugin
# 결과: 약 200MB 분량의 .rpm 파일들
```

**서버에서 (반입 후):**
```bash
sudo rpm -ivh --replacepkgs docker-offline/*.rpm
sudo systemctl enable --now docker
sudo usermod -aG docker $USER
```

**Ubuntu용 대응:** `apt-get download` 또는 `dpkg-repack`로 .deb 패키지 수집 → `sudo dpkg -i *.deb`

#### C. 설치 검증

```bash
docker --version          # Docker version 24.x 이상
docker compose version    # Docker Compose version v2.x 이상
docker run hello-world    # ⚠️ 폐쇄망에선 이미지 pull 실패 — 정상
                           # 대신 다음으로 데몬 가동 확인:
docker info               # Server 섹션 보이면 OK
```

#### D. 방화벽 설정

**Rocky/RHEL (firewalld):**
```bash
sudo firewall-cmd --permanent --add-port=8501/tcp   # 웹 UI (외부 노출)
sudo firewall-cmd --permanent --add-port=8000/tcp   # API (사내망 한정 권장)
sudo firewall-cmd --reload
sudo firewall-cmd --list-ports
```

**Ubuntu (ufw):**
```bash
sudo ufw allow 8501/tcp comment 'SMAgentLab Web'
sudo ufw allow 8000/tcp comment 'SMAgentLab API'
sudo ufw status
```

#### E. SELinux 대응 (Rocky/RHEL만)

```bash
sestatus      # Current mode 확인
```

`Enforcing` 상태면 bind mount 시 권한 문제 발생 가능. 두 가지 옵션:

```bash
# 옵션 1) 도커 컨테이너 컨텍스트 허용 (권장)
sudo setsebool -P container_manage_cgroup true

# 옵션 2) 마운트 디렉토리에 SELinux 라벨 부여
sudo chcon -Rt svirt_sandbox_file_t /opt/smagentlab/scripts
sudo chcon -Rt svirt_sandbox_file_t /opt/smagentlab/init
```

#### F. 시간 동기화 확인

```bash
timedatectl status
# System clock synchronized: yes 가 보여야 함
# 폐쇄망 NTP 서버 있다면: sudo timedatectl set-ntp true
```

> JWT 토큰 검증과 DB 트랜잭션 로그 정합성 때문에 시계가 어긋나면 안 됩니다.

### 2-2. 배포 디렉토리 구성

```bash
sudo mkdir -p /opt/smagentlab
sudo chown $USER:$USER /opt/smagentlab
cd /opt/smagentlab
```

반입한 파일들을 이 디렉토리에 배치 후 구조 확인:

```bash
ls -la
# 다음 구조여야 함:
# ├── smagentlab-images-<TAG>.tar.gz
# ├── smagentlab-images-<TAG>.tar.gz.sha256
# ├── docker-compose.yml
# ├── docker-compose.prod.yml
# ├── .env
# ├── init/            (01~08 *.sql)
# └── scripts/
#     ├── import-and-run.sh
#     ├── update-images.sh
#     ├── backup-db.sh
#     └── restore-db.sh
```

#### 무결성 검증

```bash
sha256sum -c smagentlab-images-<TAG>.tar.gz.sha256
# 출력: smagentlab-images-<TAG>.tar.gz: OK   ← 이 메시지여야 진행
```

#### 권한 설정

```bash
chmod 600 .env                  # 시크릿 파일 — 소유자만 읽기
chmod +x scripts/*.sh           # 스크립트 실행 권한
mkdir -p backups && chmod 700 backups   # 백업 디렉토리
```

### 2-3. 실행

```bash
bash scripts/import-and-run.sh smagentlab-images-<TAG>.tar.gz
```

**처리 내용:**
1. `docker load` — 이미지 로드 (수 분, 이미지가 커서 디스크 속도에 좌우)
2. `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --no-build`
3. 백엔드 헬스체크 폴링 (최대 120초)
4. 성공 시 접속 URL 출력

### 2-4. 접속

| 서비스 | URL |
|---|---|
| 웹 UI | `http://<서버IP>:8501` |
| API 문서 | `http://<서버IP>:8000/docs` |
| 헬스체크 | `http://<서버IP>:8000/health` |

> 외부 PC에서 접속하려면 방화벽에서 8501, 8000 포트를 열어야 합니다.

### 2-5. 초기 어드민 작업

`.env`의 `ADMIN_DEFAULT_PASSWORD`로 admin 계정 로그인 후 순서대로 진행:

| 순서 | 작업 | 경로 |
|---|---|---|
| 1 | admin 계정 로그인 | 우측 상단 로그인 |
| 2 | 파트 생성 | 어드민 → 사용자 관리 → 파트 관리 |
| 3 | 네임스페이스(지식 범위) 생성 | 어드민 → 기준 정보 관리 |
| 4 | 지식 등록 / 정책서 임포트 | 어드민 → 지식 베이스 / 정책 |
| 5 | 일반 사용자 계정 생성 | 어드민 → 사용자 관리 |

> 초기 로그인 후 반드시 admin 비밀번호를 변경하세요.

---

## 3. 버전 업데이트 (재배포)

### 3-1. 빌드 PC에서 새 이미지 생성

```bash
git pull origin main
bash scripts/export-images.sh <TAG>      # §1-3 Step 3의 모델 포함 확인까지
```

### 3-2. 폐쇄망 서버에서 적용

```bash
cd /opt/smagentlab
# 1) 새 tar.gz 반입 + sha256 확인(§2-2), .env의 IMAGE_TAG=<TAG> 로 갱신
# 2) init/ 디렉토리도 새 것으로 교체(빈 DB 재설치 대비 — 기존 DB엔 자동 적용 안 됨)
bash scripts/update-images.sh smagentlab-images-<TAG>.tar.gz   # 백업 제안에 반드시 y
```

**처리 내용:** (선택) DB 백업 → 이미지 로드 → `backend`·`frontend`만 재생성(postgres·redis 볼륨 유지).
백엔드가 기동하면서 **스키마 마이그레이션을 자동 적용**한다(`main.py`의 멱등 마이그레이션 — 새 테이블·컬럼·함수).
로그에서 오류가 없는지 확인: `docker compose -f docker-compose.yml -f docker-compose.prod.yml logs --tail=100 backend`

### 3-3. 롤백

이미지만 되돌리면 되는 경우: `.env`의 `IMAGE_TAG`를 직전 버전으로 바꾸고

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --force-recreate backend frontend
```

> ⚠️ **§3-4 임베딩 재색인을 실행한 뒤에는 이미지만 되돌리면 안 된다** — DB 벡터가 1024차원으로 바뀌어 옛 이미지(768차원)가
> 동작하지 않는다. 이 경우 업데이트 직전 백업으로 DB 복원(§4-2) 후 이미지를 되돌린다.
> 운영 서버에는 직전 버전 이미지 1개를 남겨둔다(정리했다면 그 tar.gz을 다시 `docker load`).

### 3-4. v2.17 → v2.119 업그레이드 시 추가 작업 (1회)

자동 마이그레이션이 못 하는 일회성 작업 두 가지. 순서대로, §3-2 직후에 실행한다.

**(1) 임베딩 모델 교체 재색인 — 벡터 768 → 1024차원 (v2.72)**

```bash
# 먼저 현재 차원 확인 — vector(768)이면 실행, vector(1024)면 이미 끝난 것이니 건너뜀
docker exec ops-postgres psql -U ops -d opsdb -tAc \
  "SELECT format_type(atttypid, atttypmod) FROM pg_attribute WHERE attrelid='rag_knowledge'::regclass AND attname='embedding'"

# vector(768)일 때만 — 8개 테이블 컬럼 변경 + 전량 재임베딩(데이터 양에 따라 수 분~수십 분)
docker exec -it ops-backend python scripts/migrate_embedding_model.py
```

> 끝나면 같은 확인 쿼리가 `vector(1024)`. 그 전까지 채팅 검색·지식 등록은 실패한다(작업 시간대에 진행).
> 모델은 이미지에 들어 있어 인터넷이 필요 없다. 개발망에서 `encode()` 단계 오류가 났던 이력이 있어(스크립트 주석 참고)
> 같은 증상이면 `-e HF_HUB_OFFLINE=0 -e TRANSFORMERS_OFFLINE=0`을 붙여 재실행 — 폐쇄망에서 이 옵션은 외부 접속 시도로
> 지연될 수 있으니 기본 실행이 먼저다.

**(2) 업무구분 기본값 백필 (v2.28)** — 업무구분이 필수가 되면서 비어 있는 지식을 '공통지식'으로 채운다. 여러 번 실행해도 안전.

```bash
docker exec -i ops-postgres psql -U ops -d opsdb < init/04-category-required-backfill.sql
```

`.env`에 새로 넣어야 하는 필수 변수는 없다(v2.17 이후 추가된 설정은 모두 기본값 있음 — 리랭커는 `RERANKER_ENABLED=true`로 켤 수 있음).
v2.16 → v2.17 절차(LLM 자격증명 컬럼 교체·환경변수 변경)는 이 문서의 git 이력(커밋 `cbcaacf`) 참고.

### 3-5. v2.121 이상으로 올릴 때 — 되돌릴 수 없는 자동 변경 (반드시 백업 먼저)

수동 작업은 없다. 새 백엔드가 처음 뜰 때 자동 마이그레이션이 아래를 실행하므로 **§4-1 DB 백업을 먼저** 받는다.

| # | 하는 일 | 되돌리기 |
|---|---|---|
| 61 | 질의 기록 상태 재분류 — 해결/미해결 → 답변·지식 공백 | 백업 복원만 |
| 62 | `ops_feedback`(좋아요/싫어요 기록)·`rag_knowledge_review_flag`(옛 리뷰 신호) **DROP** — 지우기 전 피드백이 남긴 가중치 가감을 되돌림 | 백업 복원만 |
| 63 | 지식 가중치(`base_weight`) 전부 1.0 — 문서 분석이 매긴 가중치(0.5~2.0)가 있던 문서는 **검색 순위가 바뀐다** | 백업 복원만 |
| 64 | LLM 연결 실패 기록 → `system_error`(통계 밖) | 백업 복원만 |

> 이 버전 이후 이미지를 되돌리려면 이미지만 바꾸지 말고 업데이트 직전 백업으로 DB를 복원(§4-2)한다 — 옛 이미지는 지워진
> 테이블(`ops_feedback`)을 쓴다. 기동 로그에서 `[migrate #6` 줄로 실행 결과를 확인할 수 있다.

---

## 4. 운영 작업

### 4-1. DB 백업

```bash
bash scripts/backup-db.sh
# → backups/opsdb-20260422-153012.sql.gz
```

**자동 일일 백업 (cron):**
```cron
0 3 * * * cd /opt/smagentlab && bash scripts/backup-db.sh >> /var/log/smagentlab-backup.log 2>&1
```

### 4-2. DB 복원

```bash
bash scripts/restore-db.sh backups/opsdb-20260422-153012.sql.gz
docker compose -f docker-compose.yml -f docker-compose.prod.yml restart backend
```

### 4-3. 로그 확인

```bash
COMPOSE="-f docker-compose.yml -f docker-compose.prod.yml"
docker compose $COMPOSE logs -f backend          # 실시간
docker compose $COMPOSE logs --tail=200 backend  # 최근 200줄
docker compose $COMPOSE ps                        # 상태
```

### 4-4. 서비스 중지/재시작

```bash
COMPOSE="-f docker-compose.yml -f docker-compose.prod.yml"
docker compose $COMPOSE stop          # 중지 (데이터 유지)
docker compose $COMPOSE start         # 재시작
docker compose $COMPOSE restart backend  # 백엔드만 재시작
docker compose $COMPOSE down          # 컨테이너 제거 (볼륨은 유지)
docker compose $COMPOSE down -v       # 컨테이너 + 볼륨 삭제 (데이터 손실)
```

---

## 5. 트러블슈팅

### 5-1. 자주 발생하는 문제

| 증상 | 원인 | 해결 |
|---|---|---|
| `docker load` 실패 (no space left) | 디스크 부족 | `df -h`, 이전 이미지 정리: `docker image prune -a` |
| `pgvector` 확장 오류 | 잘못된 postgres 이미지 사용 | 반드시 `pgvector/pgvector:pg16` 이미지 사용 확인 |
| 백엔드 시작 시 "model not found" | 이미지 빌드 시 모델 다운로드 누락(빌드는 실패하지 않음) | §1-3 Step 3 모델 포함 확인 → 빌드 PC 인터넷 확인 후 재빌드 |
| 채팅·지식 등록 시 `expected 768 dimensions, not 1024` 류 오류 | v2.17 DB에 새 이미지만 올림 | §3-4 (1) 임베딩 재색인 실행 |
| `$'\r': command not found` (스크립트 실행 시) | Windows에서 CRLF로 복사된 파일 | 저장소는 `.gitattributes`로 LF 고정(2026-10-02). 이미 반입했다면 `sed -i 's/\r$//' scripts/*.sh` |
| OAuth 토큰 발급 실패 (401/403) | client_id/client_secret 오류 또는 폐쇄망에서 게이트웨이 미허용 | `.env`의 `INHOUSE_LLM_CLIENT_ID/SECRET` 재확인, 방화벽에서 `INHOUSE_LLM_BASE_URL` 도메인 HTTPS 허용. 임시로 `LLM_PROVIDER=ollama` 전환 |
| 포트 8501/8000 충돌 | 다른 서비스 사용 중 | `.env`의 `FRONTEND_PORT`, `BACKEND_PORT` 변경 |
| 컨테이너 재시작 반복 | DB 헬스체크 대기 | 1~2분 대기 후 `docker compose ps` 재확인 |
| 이미지 아키텍처 불일치 | 빌드 PC가 ARM64이고 서버가 x86_64 (또는 반대) | 빌드 PC와 서버를 같은 아키텍처로 통일하거나 `docker buildx build --platform linux/amd64` 사용 |
| `permission denied` (bind mount) | SELinux Enforcing | `sudo chcon -Rt svirt_sandbox_file_t /opt/smagentlab/scripts` (§2-1 E 참고) |
| `host.docker.internal` 접속 실패 | Linux 호스트에서 별칭 미적용 | base compose에 `extra_hosts: ["host.docker.internal:host-gateway"]` 이미 포함됨 — Docker 20.10+ 필요 |
| `docker compose` 명령 없음 | Compose v1 만 설치 | `docker-compose` (구버전) 대신 `docker compose` (공백) 사용. Plugin 재설치 필요 |
| 한국어 텍스트 깨짐 (DB) | 로케일 미설정 | postgres 이미지는 기본 UTF-8, 문제없음. 클라이언트 PC의 입력 인코딩 확인 |

### 5-2. init/ SQL은 빈 DB 최초 1회만 — 스키마 변경은 백엔드 기동 시 자동

PostgreSQL은 `pgdata` 볼륨이 **비어있을 때만** `init/*.sql`을 실행한다. 이후 스키마 변경은 백엔드가 기동하면서
`main.py`의 멱등 마이그레이션으로 자동 적용되므로 업그레이드 때 SQL을 손으로 돌릴 필요가 없다 — **예외는 데이터
재가공이 필요한 일회성 작업**뿐이고, 해당 버전 업그레이드 절차(§3-4)에 명시한다. 마이그레이션 이력은
`docs/table-definition.md`.

### 5-3. 빠른 진단 명령

```bash
# 컨테이너 상태
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps

# 백엔드 헬스체크
curl -fs http://localhost:8000/health && echo OK

# 백엔드 로그 (마지막 100줄, 컬러)
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs --tail=100 backend

# DB 직접 접속
docker exec -it ops-postgres psql -U ops -d opsdb

# Redis 키 확인
docker exec -it ops-redis redis-cli KEYS 'semcache:*' | head -10

# 디스크 사용량
docker system df
df -h /var/lib/docker
```

---

## 6. 보안 점검 사항

| 항목 | 확인 |
|---|---|
| `.env`의 `JWT_SECRET_KEY`, `FERNET_SECRET_KEY` | `change-this-...` 같은 기본값 금지. 32바이트 랜덤 |
| `POSTGRES_PASSWORD` | `ops1234` 같은 기본값 금지 |
| `ADMIN_DEFAULT_PASSWORD` | 첫 로그인 후 어드민 UI에서 변경 |
| `.env` 파일 권한 | `chmod 600 .env` (소유자만 읽기) |
| 백업 파일 위치 | `backups/`도 `chmod 700` 권장. 별도 보안 디스크에 주기 백업 |
| Docker 데몬 권한 | `docker` 그룹 가입자는 사실상 root. 운영자 외 가입 금지 |
| 외부 노출 포트 | 8501(웹UI)만 허용, 5432(DB)/8000(API)는 사내망 전용 권장 |

---

## 7. 폐쇄망 운영 체크리스트 (요약)

**최초 배포:**
- [ ] 빌드 PC에서 `bash scripts/export-images.sh <TAG>` 실행 + 모델 포함 확인
- [ ] `.env`의 시크릿 키들 운영용으로 새로 생성
- [ ] `smagentlab-images-<TAG>.tar.gz` + 설정 파일들 서버 반입
- [ ] 서버에서 `bash scripts/import-and-run.sh` 실행
- [ ] `http://<서버IP>:8501` 접속 확인
- [ ] admin 로그인 → 비밀번호 변경 → 파트/네임스페이스/사용자 생성
- [ ] cron에 일일 백업 등록

**버전 업데이트:**
- [ ] 빌드 PC에서 `bash scripts/export-images.sh <TAG>` + 모델 포함 확인
- [ ] 서버 `.env`의 `IMAGE_TAG` 갱신, `init/` 교체
- [ ] `bash scripts/update-images.sh` 실행 (백업 제안 → **y**)
- [ ] 해당 버전의 일회성 작업(§3-4 — v2.17에서 올리면 임베딩 재색인 + 업무구분 백필)
- [ ] 헬스체크·채팅 질문 1건 확인 후 이전 이미지는 직전 버전 1개만 남기고 정리
