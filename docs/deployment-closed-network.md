# 폐쇄망 리눅스 서버 배포 가이드

> 인터넷이 차단된 사내 리눅스 서버에 SMAgentLab(Ops-Navigator)을 배포하는 절차.
> 일반 사내 배포는 [`deployment-guide.md`](deployment-guide.md) 참고.

---

## 0. 배포 전 체크리스트

### 양쪽 환경 사전 준비

| 환경 | 필요 사항 |
|---|---|
| **빌드 PC** (인터넷 가능) | Docker Desktop 또는 Docker 24+, 인터넷 접속, 본 저장소 clone |
| **운영 서버** (폐쇄망) | Linux (Rocky Linux 9 / RHEL 9 / Ubuntu 22.04 LTS 권장), Docker 24+, Docker Compose v2, 디스크 여유 ≥ 10GB |

### 반입할 파일 목록

폐쇄망 서버로 옮겨야 하는 파일·디렉토리:

```
SMAgentLab/
├── docker-compose.yml              # 베이스 compose
├── docker-compose.prod.yml         # 운영 오버라이드 (build 제거, 볼륨 마운트 제거)
├── .env                            # 시크릿 + IMAGE_TAG (운영 PC에서 작성)
├── init/                           # DB 초기화 SQL
│   ├── 01-init.sql
│   └── 02-migrate-fk.sql
├── scripts/
│   ├── import-and-run.sh           # 폐쇄망 배포 스크립트
│   ├── update-images.sh            # 버전 업데이트 스크립트
│   ├── backup-db.sh                # DB 백업
│   ├── restore-db.sh               # DB 복원
│   └── dist/OpsNavHelper.exe       # Teams 헬퍼 (선택)
└── smagentlab-images-v2.16.tar.gz  # 이미지 묶음 (별도 전송)
```

> 소스 코드(`backend/`, `frontend-react/`)는 **반입 불필요** — 이미지에 동봉됩니다.

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
IMAGE_TAG=v2.16

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
# IMAGE_TAG=v2.16
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
bash scripts/export-images.sh v2.16
```

**Windows (PowerShell):**
```powershell
powershell -ExecutionPolicy Bypass -File scripts\export-images.ps1 -Tag v2.16
```

**처리 내용:**
1. `docker compose build --no-cache` (백엔드/프론트엔드 빌드, 약 10~15분)
   - 백엔드 빌드 시 임베딩 모델(`paraphrase-multilingual-mpnet-base-v2`, ~420MB) **이미지 안에 사전 다운로드**
2. `pgvector/pgvector:pg16`, `redis:7-alpine` pull
3. 4개 이미지를 단일 tar.gz로 패키징 → `smagentlab-images-v2.16.tar.gz` (약 1.5~2GB)

#### Step 3) 결과물 검증

```bash
ls -lh smagentlab-images-v2.16.tar.gz       # 1.5~2GB 정도여야 정상
docker images | grep -E "smagentlab|pgvector|redis"
# 4개 이미지가 모두 v2.16 또는 pg16/7-alpine 태그로 있어야 함
```

#### Step 4) 체크섬 생성 (반입 후 무결성 확인용)

```bash
# Linux/macOS
sha256sum smagentlab-images-v2.16.tar.gz > smagentlab-images-v2.16.tar.gz.sha256

# Windows PowerShell
Get-FileHash smagentlab-images-v2.16.tar.gz -Algorithm SHA256 | `
  ForEach-Object { "$($_.Hash)  smagentlab-images-v2.16.tar.gz" } | `
  Out-File smagentlab-images-v2.16.tar.gz.sha256 -Encoding ASCII
```

### 1-4. 폐쇄망 서버로 반입

다음 파일들을 함께 전송 (USB/SCP/사내 파일전송):

```
smagentlab-images-v2.16.tar.gz       # 메인 이미지 묶음
smagentlab-images-v2.16.tar.gz.sha256 # 체크섬
docker-compose.yml
docker-compose.prod.yml
.env                                  # ⚠️ 시크릿 — 안전한 채널로 전송
init/01-init.sql
init/02-migrate-fk.sql
scripts/import-and-run.sh
scripts/update-images.sh
scripts/backup-db.sh
scripts/restore-db.sh
scripts/dist/OpsNavHelper.exe         # (Teams 헬퍼 사용 시)
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
# ├── smagentlab-images-v2.16.tar.gz
# ├── smagentlab-images-v2.16.tar.gz.sha256
# ├── docker-compose.yml
# ├── docker-compose.prod.yml
# ├── .env
# ├── init/
# │   ├── 01-init.sql
# │   └── 02-migrate-fk.sql
# └── scripts/
#     ├── import-and-run.sh
#     ├── update-images.sh
#     ├── backup-db.sh
#     ├── restore-db.sh
#     └── dist/OpsNavHelper.exe   (선택)
```

#### 무결성 검증

```bash
sha256sum -c smagentlab-images-v2.16.tar.gz.sha256
# 출력: smagentlab-images-v2.16.tar.gz: OK   ← 이 메시지여야 진행
```

#### 권한 설정

```bash
chmod 600 .env                  # 시크릿 파일 — 소유자만 읽기
chmod +x scripts/*.sh           # 스크립트 실행 권한
mkdir -p backups && chmod 700 backups   # 백업 디렉토리
```

### 2-3. 실행

```bash
bash scripts/import-and-run.sh smagentlab-images-v2.16.tar.gz
```

**처리 내용:**
1. `docker load` — 이미지 로드 (1~3분)
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

`.env`의 `ADMIN_DEFAULT_PASSWORD`로 admin 계정 로그인 → 파트/네임스페이스 생성 → 사용자 등록.
상세 절차는 [`deployment-guide.md`](deployment-guide.md) §3 참고.

---

## 3. 버전 업데이트 (재배포)

### 3-1. 빌드 PC에서 새 이미지 생성

```bash
git pull origin main
# .env의 IMAGE_TAG 갱신: v2.16 → v2.17
bash scripts/export-images.sh v2.17
```

### 3-2. 폐쇄망 서버에서 적용

```bash
cd /opt/smagentlab

# 새 .env 반영 (IMAGE_TAG=v2.17 로 업데이트)
# 새 이미지 tar.gz 반입

bash scripts/update-images.sh smagentlab-images-v2.17.tar.gz
```

**처리 내용:**
1. (선택) DB 백업 자동 제안
2. 새 이미지 로드
3. `backend`, `frontend` 컨테이너만 재생성 (postgres, redis 볼륨은 그대로 유지)

### 3-3. 롤백

`.env`의 `IMAGE_TAG`를 이전 버전으로 되돌리고 `up -d --force-recreate backend frontend`만 실행하면 즉시 롤백됩니다 (이전 이미지가 서버에 남아있는 한).

```bash
# .env: IMAGE_TAG=v2.16 으로 되돌리기
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --force-recreate backend frontend
```

> 이전 이미지를 정리했다면 백업 tar.gz을 다시 `docker load` 해야 합니다. 운영 서버에서는 직전 버전 이미지 1개는 유지 권장.

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
| 백엔드 시작 시 "model not found" | 이미지 빌드 시 모델 다운로드 누락 | 빌드 PC에서 인터넷 확인 후 재빌드 |
| OAuth 토큰 발급 실패 (401/403) | client_id/client_secret 오류 또는 폐쇄망에서 게이트웨이 미허용 | `.env`의 `INHOUSE_LLM_CLIENT_ID/SECRET` 재확인, 방화벽에서 `INHOUSE_LLM_BASE_URL` 도메인 HTTPS 허용. 임시로 `LLM_PROVIDER=ollama` 전환 |
| 포트 8501/8000 충돌 | 다른 서비스 사용 중 | `.env`의 `FRONTEND_PORT`, `BACKEND_PORT` 변경 |
| 컨테이너 재시작 반복 | DB 헬스체크 대기 | 1~2분 대기 후 `docker compose ps` 재확인 |
| 이미지 아키텍처 불일치 | 빌드 PC가 ARM64이고 서버가 x86_64 (또는 반대) | 빌드 PC와 서버를 같은 아키텍처로 통일하거나 `docker buildx build --platform linux/amd64` 사용 |
| `permission denied` (bind mount) | SELinux Enforcing | `sudo chcon -Rt svirt_sandbox_file_t /opt/smagentlab/scripts` (§2-1 E 참고) |
| `host.docker.internal` 접속 실패 | Linux 호스트에서 별칭 미적용 | base compose에 `extra_hosts: ["host.docker.internal:host-gateway"]` 이미 포함됨 — Docker 20.10+ 필요 |
| `docker compose` 명령 없음 | Compose v1 만 설치 | `docker-compose` (구버전) 대신 `docker compose` (공백) 사용. Plugin 재설치 필요 |
| 한국어 텍스트 깨짐 (DB) | 로케일 미설정 | postgres 이미지는 기본 UTF-8, 문제없음. 클라이언트 PC의 입력 인코딩 확인 |

### 5-2. ⚠️ init/ SQL 스크립트는 최초 1회만 실행됨

PostgreSQL은 `pgdata` 볼륨이 **비어있을 때만** `init/*.sql`을 실행합니다.

**증상:** 두 번째 배포 시 새로운 `init/` 변경사항이 적용 안 됨.

**해결 (운영 데이터 손실 주의):**
```bash
# 옵션 1) 데이터 다 지우고 재초기화 — 운영 데이터 손실
docker compose -f docker-compose.yml -f docker-compose.prod.yml down -v
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# 옵션 2) 추가 SQL을 수동으로 적용 — 권장
docker exec -i ops-postgres psql -U ops -d opsdb < init/03-new-migration.sql
```

> 스키마 변경은 마이그레이션 파일(`init/03-...`, `init/04-...`)을 새로 만들고 수동 적용. 기존 파일 수정 금지.

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
- [ ] 빌드 PC에서 `bash scripts/export-images.sh v2.16` 실행
- [ ] `.env`의 시크릿 키들 운영용으로 새로 생성
- [ ] `smagentlab-images-v2.16.tar.gz` + 설정 파일들 서버 반입
- [ ] 서버에서 `bash scripts/import-and-run.sh` 실행
- [ ] `http://<서버IP>:8501` 접속 확인
- [ ] admin 로그인 → 비밀번호 변경 → 파트/네임스페이스/사용자 생성
- [ ] cron에 일일 백업 등록

**버전 업데이트:**
- [ ] 빌드 PC에서 `IMAGE_TAG`를 새 버전으로 갱신 후 export
- [ ] 서버 `.env`의 `IMAGE_TAG` 갱신
- [ ] `bash scripts/update-images.sh` 실행 (백업 자동 제안 → yes)
- [ ] 헬스체크 통과 후 `docker image prune -f` 로 이전 이미지 정리
