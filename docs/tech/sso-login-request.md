# Ops-Navigator SSO 로그인 — Azure AD 앱 등록 요청 준비 (2026-09-03 기준)

> ⚠️ **2026-09-03 정정**: 로그인 SSO는 이 문서가 가정한 Azure AD 직접 연동이 아니라
> **사내 자체 IdP(SSO 게이트웨이, Azure AD와 별개, 프로토콜 OIDC)**를 통해 연동하기로
> 확인됨. 이 문서는 **M365 메일함(VOC 이메일 채널) 전용 Azure AD 앱 등록 트랙에만
> 유효**하다 — 로그인 SSO는 `docs/tech/internal-sso-request.md`를 참고할 것.

## 1. 목적

로그인 인프라를 로컬 계정(username/password)에서 회사 SSO(Azure AD/Entra ID)로 전환하기 위한
기반 마련. 팀 규모로 실사용자가 늘어나는 시점에 맞춰, 코드 작업과 별도로 **가장 오래 걸리는
조직 승인 절차를 먼저 신청**해둔다.

## 2. 왜 신청부터 먼저 넣는가

VOC 이메일 채널(Track A) 때 M365 연동 승인(보안성 검토 → 퍼블릭클라우드팀 JSM 앱 생성 →
리다이렉트 URL 등록 → client secret 발급)에 걸린 시간이 코드 구현 시간보다 훨씬 길었다
(`docs/tech/voc-email-handoff.md` §3, 1~13단계 전체 과정 참고). SSO 로그인도 같은 조직
프로세스를 타야 하므로, 코드/스키마 설계와 **병행해서** 신청부터 넣는 게 우선순위 1위다.

## 3. 기존 앱과는 별개로 신청해야 함

이미 등록된 앱(`InC_OpsNavigator_MailAgent_SCK`, tenant `d4ffc887-d88d-41cc-bf6a-6bb47ec0f3ca`)이
있지만, 이건 **단일 관리자 계정이 메일함(Mail.Read)에 접근하는 위임 권한 전용**이다. SSO
로그인은 **전 직원이 로그인하는 용도**라 목적·권한 범위·보안 검토 대상이 완전히 다르므로,
같은 앱을 재사용하지 말고 **신규 앱으로 별도 신청**해야 한다. 다만 **같은 tenant는 재사용
가능**하므로 신청서에 tenant_id를 미리 적어두면 절차를 단축할 수 있다.

## 4. 보안성 검토 신청 (보안팀) — 1단계

| 항목 | 내용 |
|---|---|
| 사용 API | Microsoft Graph "Sign in"(OpenID Connect 로그인) |
| 권한 유형 | Delegated Permission — `openid`, `profile`, `email` (메일 접근 권한 없음, 최소 스코프) |
| 목적 | Ops-Navigator 로그인을 회사 SSO 계정으로 통합 |
| 담당 | mail-agent 앱 때와 동일하게 보안팀(이민재님) 창구로 시작 권장 |
| 강조 포인트 | Mail.Read 같은 광범위 권한이 아니라 **로그인 신원 확인용 최소 스코프**임을 명시 — 승인 속도에 유리 |

## 5. 앱 생성 요청 (퍼블릭클라우드팀 JSM) — 2단계

포털: `https://jira.sinc.co.kr/servicedesk/customer/portal/802` → M365 서비스 → 업무/지원 요청
(로그인은 사번/비번, 블라썸 로그인과 동일)

| 필드 | 제출할 내용 |
|---|---|
| 앱 이름 및 설명 | `OpsNavigator-SSO-Login` — Ops-Navigator 로그인 SSO 통합 |
| 필요한 API 권한 | Microsoft Graph, Delegated permissions, `openid` / `profile` / `email` |
| 리다이렉트 URL | `<배포 도메인>/api/auth/sso/callback` (배포 주소 확정 후 기입 — 로컬 개발은 `http://localhost:8501/api/auth/sso/callback`) |
| 인증서 암호 기간 | 12개월 요청(mail-agent 앱 때는 미확인으로 남았던 항목 — 이번엔 처음부터 명시) |
| 인증 흐름 | **Authorization Code Flow (PKCE)** — Device Code Flow는 요청하지 말 것(§6 참고) |
| client secret | 처음부터 함께 요청 — 리다이렉트 URL이 "Web" 플랫폼으로 등록되면 PKCE만으론 토큰 교환이 거부되는 게 mail-agent 앱 사례에서 실측 확인됐다(AADSTS7000218). 처음부터 발급까지 같이 요청해 왕복을 줄인다. |

## 6. 이미 알고 있는 함정 (mail-agent 앱 신청 때 실측으로 확인, 같은 조직/tenant라 재현 가능성 높음)

- **Device Code Flow(퍼블릭 클라이언트) 요청하지 말 것** — "Allow public client flows"는
  피싱 리스크(Device Code Phishing)로 보안팀이 비권장 사유로 거절한 전례가 있다. 처음부터
  Authorization Code Flow(PKCE)로 요청.
- **Client ID와 Object ID를 혼동하지 말 것** — 로그인에 쓰는 값은 **Application (client) ID**.
  Object ID를 잘못 넣으면 `AADSTS700016`(앱을 찾을 수 없음)으로 실패한다.
- **회신의 "SecretKey ID"/"SecretKey Text" 라벨이 실제 값과 반대로 붙어 있었던 전례가 있다** —
  `~`가 포함된 복잡한 문자열이 실제 **Secret Value**, GUID 형식이 Secret ID.
- 리다이렉트 URL은 완전히 일치해야 한다(오타/트레일링 슬래시 등) — 배포 주소가 바뀌면
  재등록 요청이 필요하다.

## 7. 준비된 코드 자산

- `service/email_voc/delegated_auth.py`에 이미 검증된 MSAL Authorization Code Flow(PKCE,
  Public/Confidential Client 자동 분기) 구현체가 있다 — 로그인 흐름 구현 시 이 패턴을
  그대로 재사용할 수 있다. 단, 이건 "Graph API 위임 호출"용이라 **ID 토큰 검증 로직이
  없다** — SSO 로그인 구현 시 ID 토큰 서명/audience/issuer/exp 검증을 새로 추가해야 한다.
- `ops_user` 테이블은 이미 SSO 계정을 담을 수 있게 스키마 확장 완료(`auth_provider`/
  `external_id`/`email` 컬럼, `hashed_password` nullable 전환, 2026-09-03). 앱 등록이
  승인되면 로그인 흐름 구현만 남는다.

## 8. 다음 액션

- [ ] 위 §4~5 내용으로 JSM 티켓 상신
- [ ] 배포 도메인 확정(리다이렉트 URL 확정에 필요)
- [ ] 승인 완료 시 이 문서에 §9로 결과(tenant_id/client_id/secret 등, mail-agent 앱 사례와
      동일 형식으로) 기록
