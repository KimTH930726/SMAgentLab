# Ops-Navigator 로그인 SSO — 사내 IdP 연동 확인 체크리스트 (2026-09-03 기준)

## 1. 이 문서의 용도

`docs/tech/sso-login-request.md`(Azure AD 직접 앱 등록, M365 메일함용)와는 **다른 트랙**이다.
로그인 SSO는 사내 자체 IdP(SSO 게이트웨이, Azure AD와 별개)를 통해 연동하기로 확인됨
(2026-09-03). 프로토콜은 OIDC(OAuth 2.0 기반)로 확인됐으나, 그 외 구체적인 연동 방법은
아직 모른다 — 타 팀이 이미 이 포털과 연동한 사례가 있다고 하니, 그 팀 또는 포털을 운영하는
조직에 아래 항목을 확인해서 채워야 실제 설계/구현이 가능하다.

## 2. 확인해야 할 것 — 연동 신청 절차

- [ ] 이 포털을 운영/관리하는 조직(팀)이 어디인가 — 신청 창구
- [ ] 신규 애플리케이션 연동은 어떤 절차로 신청하는가 (JSM 티켓 / 별도 포털 셀프서비스 / 담당자 메일 등)
- [ ] 승인까지 통상 소요 기간

## 3. 확인해야 할 것 — OIDC 연동 정보

- [ ] **Discovery 문서 URL** (`https://.../.well-known/openid-configuration`) — 있으면 authorization/token/JWKS 엔드포인트를 한 번에 확인 가능
- [ ] 없다면 개별로: `authorization_endpoint`, `token_endpoint`, `jwks_uri`, `issuer`
- [ ] 지원하는 인증 흐름 — Authorization Code Flow(+PKCE) 지원 여부, Confidential/Public Client 여부(client_secret 필요한지)
- [ ] 지원하는 scope 목록 — 최소 `openid`/`profile`/`email` 필요, 추가로 사번/부서 등 클레임이 필요하면 어떤 scope로 받는지
- [ ] **ID 토큰에 어떤 클레임이 들어오는가** — 특히:
  - 사용자를 유일하게 식별할 불변 값(예: `sub`, 사번 등) — `ops_user.external_id`에 매핑할 값
  - 이메일 클레임 이름(`email`인지 사내 커스텀 클레임인지)
  - 이름/부서 등 추가 클레임 제공 여부(있으면 `ops_user.email`/JIT 프로비저닝 시 활용)
- [ ] 리다이렉트 URI 등록 방식 — 셀프서비스로 우리가 직접 넣는지, 신청서로 제출하는지
- [ ] client_id/client_secret 발급 방식과 형식

## 4. 확인해야 할 것 — 타 팀 연동 사례 (가장 빠른 경로)

- [ ] 그 팀 담당자 연락처
- [ ] 그 팀이 쓴 라이브러리/코드(참고용 — 우리는 Python/FastAPI라 언어가 다를 수 있음, 그래도
      흐름/클레임 구조는 그대로 참고 가능)
- [ ] 그 팀이 겪은 함정/이슈가 있는지(있으면 Azure AD 앱 등록 때처럼 우리도 똑같이 겪을 가능성 높음)

## 5. 확인되면 진행할 것

정보가 모이면 `service/auth/`에 OIDC 로그인 모듈을 신규 구현한다 — 큰 흐름은 이미 설계됨
(대화로 정리, 요약: Authorization Code Flow+PKCE → ID 토큰 클레임에서 불변 식별자/이메일
추출 → `ops_user.auth_provider='<이 IdP 이름>'/external_id`로 조회 → 없으면 JIT 프로비저닝
(정책 결정 필요) → 기존 `create_access_token()`/`create_refresh_token()` 그대로 재사용해
우리 JWT 발급). `ops_user` 스키마는 이미 이 구조를 지원하도록 확장돼 있음(`auth_provider`/
`external_id`/`email` 컬럼, v2.50 — `docs/architecture.md` 참고). Azure AD든 사내 IdP든
같은 스키마로 수용 가능(`auth_provider` 값만 다르게 쓰면 됨 — 예: `'local'` / `'internal_sso'`).
