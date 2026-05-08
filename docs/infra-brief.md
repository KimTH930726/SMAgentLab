# 인프라팀 브리핑 — SMAgentLab 서버 배포

> 폐쇄망 리눅스 서버 1대에 Docker로 올라갑니다. 본 문서는 인프라팀이 **서버 스펙·네트워크·보안 설정**을 사전 준비하는 데 필요한 정보를 한 페이지로 정리합니다.

---

## 1. 한 줄 요약

사내 운영지식 기반 RAG 챗봇(FastAPI + React + PostgreSQL/pgvector + Redis) — Docker Compose로 4개 컨테이너 구동, 폐쇄망 단독 서버 배포.

---

## 2. 컨테이너 구성

```
┌──────────────────────────────────────────────────────────────┐
│  Linux 서버 1대 (Docker host)                                 │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐        │
│  │  frontend    │  │   backend    │  │   postgres   │        │
│  │  nginx :80   │◄─│  FastAPI     │─►│  pgvector    │        │
│  │  (정적빌드)   │  │  :8000       │  │  :5432       │        │
│  └──────┬───────┘  └──────┬───────┘  └──────────────┘        │
│         │                 │                                  │
│         │                 ▼                                  │
│         │          ┌──────────────┐                          │
│         │          │   redis      │                          │
│         │          │  :6379       │                          │
│         │          │  (시맨틱캐시)  │                          │
│         │          └──────────────┘                          │
│         │                 │                                  │
│         │                 ▼ (외부 호출)                        │
│         │          ┌──────────────────────┐                  │
│         │          │ 사내 DevX LLM API     │ ← HTTPS, 외부망  │
│         │          │ (사내 LLM 엔드포인트) │                  │
│         │          └──────────────────────┘                  │
│         ▼                                                    │
│  외부 사용자 (사내 PC, 브라우저)                              │
└──────────────────────────────────────────────────────────────┘
```

| 컨테이너 | 이미지 | 내부 포트 | 호스트 노출 | 역할 |
|---|---|---|---|---|
| `ops-frontend` | `smagentlab-frontend:vX.Y` | 80 | **8501** | nginx 정적 React 빌드 서빙 |
| `ops-backend` | `smagentlab-backend:vX.Y` | 8000 | **8000** | FastAPI 애플리케이션 (RAG 파이프라인) |
| `ops-postgres` | `pgvector/pgvector:pg16` | 5432 | (선택) 5432 | PostgreSQL + pgvector 확장 (벡터 검색) |
| `ops-redis` | `redis:7-alpine` | 6379 | 미노출 | LLM 응답 시맨틱 캐시 (메모리 256MB 제한) |

---

## 3. 서버 스펙 권장값

### 최소 사양 (10~30명 동시 사용)

| 자원 | 권장 |
|---|---|
| **CPU** | 4 vCore (x86_64) |
| **RAM** | 8 GB |
| **디스크** | 80 GB SSD |
| **OS** | Rocky Linux 9 / RHEL 9 / Ubuntu 22.04 LTS |
| **CPU 아키텍처** | **x86_64 필수** — ARM(Graviton 등) 사용 시 이미지 재빌드 필요 |

### 권장 사양 (50~100명 동시 사용)

| 자원 | 권장 |
|---|---|
| **CPU** | 8 vCore |
| **RAM** | 16 GB |
| **디스크** | 200 GB SSD (DB 증가 + 백업) |

### 디스크 사용 분포 (예상)

```
Docker 이미지       :  5 GB   (4개 이미지 + 빌드 캐시)
PostgreSQL pgdata   : 변동    (지식 1만 건당 약 200MB, 임베딩 포함)
Redis 데이터        : 256MB   (캐시 maxmemory 제한)
백업                : 10~50GB (일별 백업 30일 보관 가정)
이미지 tar.gz        :  2 GB   (배포 시 일시적, 정리 가능)
```

---

## 4. 네트워크 — 방화벽 정책

### 인바운드 (서버로 들어오는 트래픽)

| 포트 | 프로토콜 | 출발지 | 용도 | 외부 노출 여부 |
|---|---|---|---|---|
| **8501** | TCP | 사내망 사용자 | 웹 UI (브라우저 접속) | ✅ 필요 |
| **8000** | TCP | 사내망 사용자 | API 직접 호출 (선택) | ⚠️ 사내망 한정 권장 |
| **5432** | TCP | (없음) | PostgreSQL DB 직접 접속 | ❌ 미노출 권장 |
| 22 | TCP | 운영자 PC | SSH 관리 | 운영자 한정 |

### 아웃바운드 (서버에서 나가는 트래픽)

| 대상 | 포트 | 용도 | 비고 |
|---|---|---|---|
| 사내 DevX LLM API | 443 | LLM 호출 (필수) | `INHOUSE_LLM_URL` 사내 엔드포인트 |
| 사내 NTP 서버 | 123 | 시간 동기화 | JWT 검증·로그 정합성 |
| 사내 DNS | 53 | 이름 해석 | LLM URL 도메인 → IP |
| (인터넷) | - | **불필요** | 운영 시 인터넷 접속 안 함 |

> **운영 중 인터넷 접속 0건.** 임베딩 모델은 이미지에 동봉, 외부 의존성 전부 사내 자원만 사용.

---

## 5. 스토리지 — Docker Volume

```
볼륨명                위치                                     역할
─────────────────────────────────────────────────────────────────────
pgdata          /var/lib/docker/volumes/.../pgdata/_data    PostgreSQL 데이터
redisdata       /var/lib/docker/volumes/.../redisdata/_data Redis 영속화
model-cache     /var/lib/docker/volumes/.../model-cache     임베딩 모델 캐시
```

- 모두 Docker 관리 named volume — 호스트 경로는 `/var/lib/docker/volumes/`
- 컨테이너 재생성/이미지 업데이트 시에도 **데이터 유지**
- `docker compose down -v` 로만 삭제 가능 (실수 방지)

### 백업 전략

| 항목 | 빈도 | 방법 |
|---|---|---|
| PostgreSQL | 일별 (cron 03:00) | `pg_dump` → `backups/*.sql.gz` |
| 백업 보관 | 30일 권장 | 별도 NAS/백업 디스크 권장 |
| Redis | 백업 불필요 | 캐시 데이터 (재생성 가능) |

---

## 6. 배포 방식

### 인터넷 PC ↔ 폐쇄망 서버 분리 모델

```
[인터넷 가능 빌드 PC]                    [폐쇄망 서버]
─────────────────────────                 ─────────────────────
git clone 저장소
docker compose build         ──tar.gz──►  docker load
docker save → tar.gz                      docker compose up -d
                                          (--no-build, 운영 모드)
```

- 서버에서는 **빌드를 하지 않음** — 미리 빌드된 이미지를 `docker load` 로 반입
- 인터넷 PC에서 사내 Git 저장소 → 빌드 → 이미지 tar.gz로 묶어서 서버 반입
- 서버에서 인터넷이나 외부 레지스트리 pull 시도 없음 (`pull_policy: never`)

### 반입 자료

| 파일 | 크기 | 빈도 |
|---|---|---|
| `smagentlab-images-vX.Y.tar.gz` | ~2 GB | 버전 업데이트마다 |
| `docker-compose.yml`, `docker-compose.prod.yml` | 1~2 KB | 거의 변경 없음 |
| `init/*.sql` | ~50 KB | 스키마 변경 시 |
| `.env` (시크릿 포함) | 1 KB | 최초 1회 |
| `scripts/*.sh` | 5 KB | 거의 변경 없음 |

---

## 7. 외부 의존성

### 필수

| 항목 | 설명 | 인프라팀 협조 사항 |
|---|---|---|
| 사내 DevX LLM API | RAG 답변 생성용 | 서버 → LLM 엔드포인트 HTTPS 허용 |
| 사내 NTP | 시간 동기화 | 서버 NTP 설정 |

### 선택

| 항목 | 사용 시점 | 비고 |
|---|---|---|
| 사내 SMTP | (현재 미사용) | 알림 메일 도입 시 |
| 사내 Confluence | RAG 지식 인제스트 | 사용자가 PAT 토큰 직접 입력 (개인별 암호화 저장) |
| 사내 Teams | 메시지 수집 기능 | 클라이언트 PC에서 헬퍼 .exe 실행 (서버 무관) |

### 인터넷 의존성 — 없음

- 임베딩 모델(`paraphrase-multilingual-mpnet-base-v2`, 420MB)은 **이미지 빌드 시점에 동봉**
- HuggingFace, npm registry, PyPI 등 일체 접속 안 함

---

## 8. 보안 고려사항

| 항목 | 적용 방안 |
|---|---|
| 시크릿 관리 | `.env` 파일에 JWT/Fernet 키 저장. 권한 `chmod 600`. 사내 시크릿 매니저 통합 검토 가능 |
| DB 비밀번호 | `.env`의 `POSTGRES_PASSWORD` 강한 값으로 설정 |
| 어드민 계정 | 초기 비밀번호 `.env`에서 변경 필수, 첫 로그인 후 UI에서 재변경 |
| 컨테이너 권한 | 모든 컨테이너 비루트 실행 (애플리케이션 레벨) — Docker 데몬은 root |
| Docker 그룹 | `docker` 그룹 가입자는 사실상 root 권한 — 운영자 외 차단 |
| SELinux/AppArmor | Rocky/RHEL는 Enforcing 유지, bind mount에 라벨 부여 (가이드 §2-1 E) |
| 외부 API 키 | 사용자별 LLM API 키는 Fernet 암호화 후 DB 저장 |
| 사용자 인증 | JWT (Access Token + Refresh Token), 비밀번호 bcrypt |
| HTTPS | 본 시스템은 HTTP. **사내 리버스 프록시(nginx/HAProxy)에서 TLS 종단 권장** |

### TLS 종단 권장 구성

```
[사용자 브라우저] ──HTTPS:443──► [사내 리버스 프록시] ──HTTP:8501──► [SMAgentLab 서버]
                                  (사내 PKI 인증서)
```

---

## 9. 운영 사이클

| 작업 | 주기 | 담당 | 비고 |
|---|---|---|---|
| DB 백업 | 일별 (자동) | 시스템 cron | `scripts/backup-db.sh` |
| 백업 검증 | 월 1회 | 운영자 | 임의 백업으로 복원 테스트 |
| 디스크 사용량 점검 | 주 1회 | 운영자 | `df -h`, `docker system df` |
| 로그 점검 | 수시 | 운영자 | `docker compose logs` |
| 버전 업데이트 | 분기 1회 (예상) | 개발팀 → 운영자 | 새 tar.gz 반입 + `update-images.sh` |
| 보안 패치 | 인프라팀 정책 | 인프라팀 | 호스트 OS 패치, Docker 데몬 패치 |

---

## 10. 인프라팀 사전 준비 체크리스트

서비스 인스턴스 발주/구성 시 점검 항목:

### 하드웨어/OS
- [ ] x86_64 아키텍처 서버 (CPU 4코어 이상, RAM 8GB 이상, SSD 80GB 이상)
- [ ] Rocky Linux 9 / RHEL 9 / Ubuntu 22.04 LTS 중 1택
- [ ] 디스크 파티션: `/var/lib/docker` 별도 마운트 권장 (40GB+)

### Docker
- [ ] Docker Engine 24+ + Docker Compose Plugin v2 설치
- [ ] `docker` 그룹에 운영 계정 추가
- [ ] 사내 yum/apt 미러에 docker-ce 패키지 등록 (or 오프라인 RPM 반입 경로 확보)

### 네트워크
- [ ] 인바운드 8501/TCP 사내 사용자 허용
- [ ] 인바운드 8000/TCP 사내 사용자 허용 (또는 운영자 한정)
- [ ] 아웃바운드 → 사내 DevX LLM API 엔드포인트 HTTPS 허용
- [ ] 아웃바운드 → 사내 NTP 서버
- [ ] (선택) 리버스 프록시 등록 + TLS 인증서 발급

### 보안
- [ ] SELinux/AppArmor 정책 검토 (가이드 §2-1 E)
- [ ] `.env` 시크릿 파일 전송 채널 (암호화 USB / 사내 시크릿 매니저)
- [ ] 백업 디스크/NAS 마운트 (`/backup/smagentlab`)

### 모니터링/로그
- [ ] 시스템 로그 수집 (rsyslog → 사내 SIEM, 선택)
- [ ] Docker 로그 드라이버 설정 (json-file 기본, 로테이션 정책 확인)
- [ ] 디스크 사용량 임계값 알림 (사내 모니터링 시스템 연동)

---

## 11. 운영자 ↔ 인프라팀 연락 사항

운영자가 인프라팀에 요청할 가능성이 있는 사항:

| 시점 | 요청 내용 |
|---|---|
| 최초 배포 | 위 체크리스트 전체 |
| 트래픽 급증 | RAM/CPU 증설, 리버스 프록시 워커 수 조정 |
| DB 비대화 | 디스크 증설, 별도 백업 디스크 |
| 보안 사고 | 컨테이너 격리, 네트워크 차단, 로그 보존 |
| 장애 복구 | 호스트 OS 부팅, Docker 데몬 재시작, 백업 복원 협조 |

---

## 12. 참고 문서

- `docs/deployment-closed-network.md` — 폐쇄망 배포 상세 절차 (운영자용)
- `docs/architecture.md` — 시스템 아키텍처 상세
- `docs/flow.md` — 요청 처리 흐름도
- `docs/api-specification.md` — API 명세
