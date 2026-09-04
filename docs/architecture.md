# Ops-Navigator 시스템 아키텍처 (v2.58)

## 개요

Ops-Navigator는 IT 운영팀의 반복적인 조회·확인 업무를 자동화하는 **지능형 운영 보조 에이전트 플랫폼**이다.
사용자는 에이전트를 선택해 목적에 맞는 AI를 사용한다: 지식 기반 Q&A(KnowledgeRAG), HTTP API 연동(MCP 도구).

> Text-to-SQL 에이전트는 v2.51에서 `dev_0`/`main`에서 분리·제거됐다(현재 과업 범위 아님) — 코드는
> `archive/with-text2sql` 브랜치(2026-09-03 시점 스냅샷)에 형상관리용으로 보존돼 있다.

**주요 이력 요약** (스키마 변경 상세는 `table-definition.md` §20 마이그레이션 이력 참조)
- v2.58: **딜리버스 정책서 실 데이터 적재 + "정책서 미분류" 화면 가독성/설명 개선** — 딜리버스
  DB 네임스페이스에 용어정의 31건 + 정책 71건(총 102건) 적재 완료(온라인스토어와 합쳐 스타벅스
  CSP팀 2개 파일 전체가 실 DB에 있음, `policy-doc-pipeline-plan.md` §9). 사용자 피드백 반영:
  ①라이트모드에서 amber 강조색 텍스트 대비가 낮던 문제 — `slate` 팔레트는 CSS 변수로 테마
  자동 전환되지만 accent 색상은 `dark:` 변형을 명시해야 하는데 놓쳤던 부분 수정(`Badge.tsx`가
  이미 쓰던 패턴을 여기 적용). ②"표준화 요청 근거로 쓰라"는 설명이 추상적이라 다음 액션이
  안 와닿는다는 지적 — 원문/실패사유/가능한 다음 액션(개발팀 공유 vs 원문팀에 재작성 요청) 을
  명시적으로 분리해 안내 문구 재작성.
- v2.57: **딜리버스 용어집 비고(remark) 유실 버그 수정** — 딜리버스 파일 용어정의 시트 실제
  화면 확인 중, 비고 컬럼에 "상태코드 : 10" 같은 정보가 있는 행을 발견. 파서는 정확히
  파싱했지만(`ParsedGlossaryRow.remark`) `service.py`가 `create_glossary(namespace, term,
  description)`만 호출해 remark를 버리고 있었음(`rag_glossary`에 remark 컬럼 없음). remark가
  있으면 description에 `"{description} (비고: {remark})"`로 이어붙이도록 수정(스키마 변경
  없음). 실 HTTP E2E로 확인. 테스트 2개 추가(총 301개).
- v2.56: **온라인스토어 정책서 실 데이터 최초 적재** — 새 네임스페이스 "온라인스토어 DB" 생성,
  대화로 받은 8개 시트(용어정의 91건 + 정책 307건, 398건)를 실제 화면/셀 구조로 재구성해
  `POST /api/policy/import`로 실제 DB 적재(약 12분). param 305건/narrative 364건/unresolved
  43건(21개 item). `GET /api/policy/unresolved-summary`·`GET /api/policy/search`·관리자 "정책서
  미분류" 화면 전부 실 데이터로 재검증. 이전까지의 검증은 전부 `decompose_policy_body()` 단독
  호출이었고 DB엔 안 들어가 있었음 — 이번이 최초의 실제 프로덕션성 데이터 적재. §8 v2 우선순위
  판단과 §4-1 골든셋 초안의 실측 근거로 사용 가능(`policy-doc-pipeline-plan.md` §9).
- v2.55: **LLM 분해 계산식 파괴/할루시네이션 버그 수정** — 실 정책서 7개 시트(상품/전시/주문/
  배송/클레임/리워드/재고)로 LLM 분해 스트레스 테스트 중 발견. 여러 줄이 하나의 계산식인
  경우("기초재고\n-안전재고\n-변동재고...") 줄 단위로 쪼개 관계를 파괴하거나, 이벤트-필드변화
  규칙("결제완료 시 출하예정수량+, 배분주문수량+")을 원문에 없는 관계("합으로 산정")로
  할루시네이션하는 버그 — 이전 버그들과 달리 unresolved 안전망을 우회해 확신을 갖고 틀린
  내용을 만들어냈다는 점에서 지금까지 발견된 것 중 가장 심각. 프롬프트 규칙 4번 추가(계산식/
  이벤트규칙은 관계를 보존하거나 unresolved로, 원문에 없는 관계를 지어내지 말 것) + 실패사례
  2건 few-shot 추가. 실 LLM 재호출로 수정 확인 + 안 보여준 다른 계산식으로 일반화 검증 통과.
  같은 라운드에서 "우선순위 체인"(A→B→C 순서 목록) 분류가 실행마다 unresolved/narrative로
  갈리는 비일관성도 발견했으나 데이터 손실은 없어 지금은 그대로 두고 Track 2 골든셋 평가로
  넘김(과도한 개별 사례 튜닝은 과적합 위험).
- v2.54: **실 정책서 원본 기반 파서 재검증 + param value 배열 버그 수정** — 실제 회사 정책서
  2건을 받았으나 IRM/RMS 보안 레이블(신세계아이앤씨 전용)로 암호화돼 있어 openpyxl로 직접 열
  수 없음을 확인(DRM 우회는 시도하지 않음). 대신 사용자가 확인해준 실제 화면/셀 내용으로
  동일한 워크북을 재구성해 검증 — 제목/Desc/공백행이 헤더 위에 있는 실제 레이아웃에서도 헤더
  자동 감지 정상, 3단 category_path 정확 추출, Alt+Enter 멀티라인 셀 손실 없이 보존. LLM
  분해를 실 데이터로 돌리다 실버그 발견: "판매상태가 판매대기/판매중/판매종료" 같은 열거형
  파라미터에서 LLM이 `value`를 배열로 반환 → `service.py`가 `str()`로만 감싸 DB에 파이썬
  repr 문자열(`"['판매대기', '판매중', '판매종료']"`)이 그대로 저장되는 데이터 오염. 프롬프트
  규칙 추가(value는 항상 단일 문자열, 여러 값이면 쉼표 join) + `service.py`에 방어적
  `_coerce_param_field()` 추가(리스트/튜플 join). 테스트 7개 추가(총 299개).
- v2.53: **정책서 unresolved 팀별 집계 리포트 + 읽기 전용 화면** — v2.52 파이프라인이 unresolved로
  캡처만 하고 아무도 정기적으로 보지 않던 갭을 메움(2026-09-04 발표 데모 준비 중 헤더/데이터유실
  버그를 "시스템이 알려줘서"가 아니라 "우연히" 발견한 것이 이 갭을 드러냄). `GET
  /api/policy/unresolved-summary`(`service/policy/unresolved_report.py`, namespace+선택
  system_key)가 `parse_status IN ('unresolved','partial')` 항목을 팀별로 묶어 반환, reason
  자동 클러스터링은 하지 않음(자유 텍스트라 정확 매칭이 무의미 — YAGNI). 관리자 화면
  "정책서 미분류" 탭(`PolicyUnresolvedReport.tsx`) 신규 — 팀별 건수/segment 목록을 사람이
  훑어볼 수 있는 읽기 전용 화면만 우선 추가(사용자 피드백: 집계 API만으론 결국 사람이 봐야
  하므로 GUI 없인 루프가 안 닫힘). 승인·재분류 등 쓰기 동작이 있는 "검토 UI"는 여전히 별도
  범위(§6 미착수) — 이 화면은 근거 자료를 보여주기만 함.
- v2.52: **정책서 데이터화 파이프라인 v1** — `service/policy/` 신규(`docs/policy-doc-pipeline-plan.md`).
  엑셀 정책서 한 row를 `policy_item`(원문+메타)/`policy_param`(파라미터 팩트)/`policy_chunk`(서술
  청크) 3층으로 분해해 적재. 팀마다 대분류/중분류/소분류 깊이가 달라(실측: 3단 vs 2단) 고정 컬럼
  대신 `category_path TEXT[]` 채택, 헤더는 정확 매칭이 아니라 퍼지매핑 + "정책명 앞 컬럼 전부"
  동적 감지로 흡수. 용어집 시트는 새 테이블 없이 기존 `rag_glossary` 재사용. 버전 관리는
  `rag_knowledge` 병합이 content를 덮어써 이력 소실됐던 문제(`knowledge-lifecycle-design.md`
  우선순위 1위)를 반복하지 않도록 UPDATE 대신 새 row INSERT(logical_id 유지, version+1,
  supersedes_id, 이전 row는 deprecated로 보존)로 설계. LLM 분해는 서술/파라미터/미해결(unresolved+
  사유) 3분류 — 완전 자동화 대신 "자동 처리 비율 최대화 + 실패 사유 캡처"를 목표로 함(팀별 표준화가
  안 돼 있다는 게 실측 확인됨). `POST /api/policy/import`(엑셀→파싱→LLM분해→적재, 재업로드 시
  content_hash로 불변 row는 LLM 재호출 없이 스킵)와 `GET /api/policy/search`(파라미터 RDB tsquery
  + 서술 벡터 검색, 항상 둘 다 실행) 둘 다 실 HTTP E2E 검증 완료. 검색은 전용 엔드포인트로 분리—
  저장 전략 실험(rag_knowledge 단일 저장 vs 이 하이브리드 스키마, 골든셋 기반 정답률 비교)이 아직
  실행 전이라 검증 안 된 방식을 모든 네임스페이스가 공유하는 채팅 검색 경로(`retrieval.py`/
  `agent.py`)에 먼저 섞지 않기 위함. 자체검증 중 실 버그 2건 발견·수정: ①병합셀이 있으면
  category_path가 빈 문자열로 들어감(openpyxl이 병합영역 첫 셀에만 값을 줌, forward-fill로 해결)
  ②검색이 ILIKE 부분문자열 매칭이라 "장바구니 개수"가 "장바구니 최대 메뉴 개수"를 못 찾음
  (`retrieval.py`와 동일한 to_tsquery lexeme 매칭으로 교체). row별 순차 LLM 호출은 100~200행
  규모에서 수 분이 걸릴 것으로 예상돼 버전체크(순차)→LLM분해(동시,세마포어5)→DB쓰기(순차,
  커넥션 안전성) 3단계로 재구성, 실측 10행 동시처리 27.5초·매핑 무결성 확인. 검토/승인 UI는 v1
  범위 밖 — 전부 `status='pending_review'`로 쌓이고 검색 대상엔 포함시킴(안 그러면 아무것도 안 나옴).
- v2.51: **Text-to-SQL 에이전트 제거** — 현재 과업이 아니라는 판단에 따라 `dev_0`/`main`에서
  분리, 형상관리 브랜치(`archive/with-text2sql`)로 보존. 제거 전 실측 점검: 백엔드는
  `agents/text2sql/` 디렉토리 하나에 격리돼 있었고 역방향 참조(다른 에이전트가 text2sql을
  import하는 경우)가 전혀 없었음 — `AgentRegistry` 패턴으로 처음부터 멀티 에이전트 구조를
  의도한 설계 덕분에 영향 범위가 명확했음. 제거 내용: 라우터/에이전트 등록, `sql_*` 테이블
  10개 생성 마이그레이션(`_migrate_text2sql_tables`), text2sql 프롬프트 시드(`sql2_*` 8개),
  프론트엔드 전용 파일 2개(`Text2SqlAdmin.tsx`/`api/text2sql.ts`) + 공용 컴포넌트 6곳의 얕은
  분기(에이전트 탭 라우팅, 라벨/색상 매핑), 전용 테스트 4개. **기존 DB의 `sql_*` 테이블은
  삭제하지 않고 그대로 둠**(마이그레이션 호출만 제거돼 더 이상 갱신되지 않을 뿐 — 데이터
  삭제는 이번 범위 밖). 제거 후 전체 테스트(257개, 기존 303개에서 text2sql 전용 46개 제외)
  통과, 실 HTTP로 `/api/text2sql/*` 404 확인, 프론트 `tsc --noEmit` 및 프로덕션 빌드 통과.
- v2.50: 팀 규모 SSO 인프라 초석 — 소스 점검 중 발견한 실제 취약점 2건 수정 + `ops_user` 스키마 선확장.
  ① **admin 계정 비밀번호 강제 리셋 버그 수정**: `main.py`의 마이그레이션 루틴이 서버 재시작마다
  `admin` 계정의 `hashed_password`를 `ADMIN_DEFAULT_PASSWORD`(기본값 `1111`) 값으로 무조건
  덮어쓰고 있었다 — 관리자가 UI로 비밀번호를 바꿔도 다음 배포/재시작 때 조용히 원복되는 실제
  취약점이었음(role/part_id 동기화 로직에 실수로 얹혀 있던 부작용). 이제 최초 계정 생성 시에만
  비밀번호를 세팅하고, 이후 재시작에서는 role/part_id만 동기화한다. 실측: `PUT /me/password`로
  변경 → 백엔드 재시작 → 변경된 비밀번호로 로그인 성공 확인(수정 전엔 실패했을 케이스).
  ② **보안 설정 플레이스홀더 경고**: `JWT_SECRET_KEY`/`ADMIN_DEFAULT_PASSWORD`가 코드 기본값
  그대로면(현재 이 배포도 그 상태임을 실측 확인) 서버 시작 시 로그에 눈에 띄게 경고하도록
  `_warn_if_insecure_defaults()` 추가 — 하드 실패(startup 중단)로는 안 만들었다, 지금 이 값
  그대로 운영 중인 배포가 실제로 있어 강제 종료하면 그 배포부터 멈추기 때문. 실제 시크릿
  교체는 활성 로그인 세션 전부 무효화 + 관리자 비밀번호 변경이라는 파급이 있어 별도로
  조율해서 진행하기로 함(이번 커밋에 포함 안 함).
  ③ **`ops_user` SSO 연동 기반 스키마 선추가**: `auth_provider`(기본값 `local`)/`external_id`
  (SSO 프로바이더가 발급하는 불변 식별자, 예: Azure AD `oid`)/`email` 컬럼 추가,
  `(auth_provider, external_id)` 부분 유니크 인덱스(`external_id IS NOT NULL`), SSO 전용
  계정은 로컬 비밀번호가 없을 수 있어 `hashed_password`를 nullable로 완화(단, `authenticate_user()`는
  아직 NULL을 다루지 않음 — 실제 SSO 로그인 흐름 구현 시 함께 수정 필요, 지금은 스키마만
  선반영). 지금(로컬 계정 소수) 하면 싸고 팀 규모로 커진 뒤 하면 비싸다는 논리
  (`knowledge-lifecycle-design.md`의 "Phase 0 스키마 선추가"와 동일 패턴).
  실제 로그인 흐름(OIDC Authorization Code Flow, ID 토큰 검증, JIT 프로비저닝)은 Azure AD
  앱 등록 승인 이후 구현 — 요청 준비 문서: `docs/tech/sso-login-request.md`(이미 검증된
  mail-agent 앱 등록 사례의 함정들을 반영해 작성).
- v2.49: 3건 개선 — ① **VOC 반복 클러스터링용 정규화 임베딩(`issue_signature`)**: 실측 중 "앱 로그인이 안 돼요"/"로그인 오류 문의드립니다"/"로그인 시 500에러 발생"처럼 같은 이슈의 다른 표현이 원문 임베딩으로는 0.85 임계값을 못 넘어 클러스터링에 실패하는 사례가 확인됨. `analyze_email()`의 LLM 응답에 정규화된 짧은 이슈 요약(`issue_signature`, 예: "로그인 500 에러")을 추가로 출력시키고, `pipeline.py`가 반복 패턴 비교(`detect_and_update_cluster`)에는 이 정규화 임베딩을 쓰고 지식 검색(RAG)에는 기존 원문 임베딩(`relevance.query_vec`)을 그대로 쓰도록 분리 — "정밀 매칭(장애 원인 분석)엔 문맥을 보존한 원문이, 표현 차이를 넘어선 그룹핑(반복 유형 탐지)엔 정규화가 유리하다"는 판단 기준에 따른 용도별 임베딩 이원화(`issue_signature`가 없으면 원문 임베딩으로 폴백). 실 데이터로 3개 패러프레이즈가 모두 동일 클러스터로 묶이는 것을 확인. ② **임베딩 서비스 동시성 버그 수정**: 5-way 동시 실행 전체 재분석 중 234건 중 4건이 이력 없이 조용히 유실되는 문제가 발견됨 — `RuntimeError: Already borrowed`(HuggingFace fast tokenizer가 GIL을 놓는 Rust 구현이라, `embed_long()`의 메인 이벤트루프 `tokenizer.encode()` 호출과 `embed()`/`embed_batch()`의 executor 스레드 `model.encode()` 호출이 동시에 같은 토크나이저/모델 인스턴스를 건드리며 발생)가 원인. `EmbeddingService`에 `asyncio.Lock` 1개를 추가해 세 메서드의 실제 모델/토크나이저 접근을 전부 그 락으로 감싸 해결(`embed_long()`은 락 재획득으로 인한 교착을 피하려 `embed()`/`embed_batch()`를 내부 호출하지 않고 `run_in_executor`를 직접 호출). 50콜 동시성 스트레스 테스트로 검증. ③ **`rag_knowledge` 카테고리 기반 검색 라우팅**: `category IN ('DB','공통코드')`가 전체 지식의 88%를 차지하는데, 이 카테고리는 코드표를 그대로 덤프한 구조화 데이터라 코사인 유사도로 비교하면 어휘만 겹쳐도 오탐이 나는 것이 실측 확인됨(VOC 커버리지 판정에서 무관한 배송 클러스터가 "사이렌오더 결제 취소" 공통코드 문서와 매칭). 질의 의도를 분류하는 게 아니라 **이미 등록된 지식 행의 category 값**으로 판단 — `retrieval.py`에 `_KEYWORD_ONLY_CATEGORIES=("DB","공통코드")` 상수 추가, `search_knowledge()`의 `final_score` 산식을 해당 카테고리는 벡터 점수를 0으로 만들고 키워드(RDB 텍스트) 점수만으로 랭킹하도록 CASE 분기(admin 설정으로 노출하지 않고 모듈 상수로 하드코딩 — 등록 시점 고정값이라 런타임 조정 필요성이 낮고, 기존 few-shot 승인 큐처럼 안 쓰이는 admin 설정 표면을 늘리지 않으려는 판단). `pattern_detection.py`의 `get_cluster_coverage()`/`list_clusters()`도 동일 카테고리를 후보에서 제외(NULL 카테고리는 실수로 함께 제외되지 않도록 `category IS NULL OR category != ALL(...)`로 처리). 실 데이터로 검증: DB/공통코드 카테고리 행은 벡터 유사도가 높아도(`v_score` 0.65+) `final_score`가 키워드 점수만 반영해 낮게(0.10대) 나오는 것을 확인. 상세 설계 논의: `docs/tech/knowledge-lifecycle-design.md`. (2026-09-04: 이 "구조화=키워드/서술=벡터" 원칙이 정책서 파이프라인(v2.52)에서 독립적으로 재발견돼, 재사용 가능한 패턴으로 `docs/tech/retrieval-routing-pattern.md`에 별도 정리함)
- v2.48: VOC Teams 발송 게이트 통합 — 명시적 피드백("게이트가 나뉘어져있으면 안 된다, 반복 게이트일 때만 팀즈를 보내고 형식은 기존 개별 VOC 카드에 반복 정보를 얹어라")에 따라 발송 여부 판단 지점을 "반복 패턴 확정" 하나로 완전히 합쳤다. v2.40부터 유지되던 "관련지식 임계치+not_it_related만 넘으면 카테고리·심각도 무관하게 항상 발송"(§10) 원칙이 폐기됨 — 이제 클러스터가 없는(반복이 아닌) 단독 VOC는 발송하지 않고, 클러스터가 있어도 `email_pattern_min_count` 채우기 전엔 발송 안 함, 채운 뒤로는 그 배수(3/6/9건째 등)에서만 발송한다(건마다 계속 보내던 v2.47 방식은 노이즈가 컸음). `pattern_info`에 `min_count`/`nth_detection`(몇 번째 배수 감지인지)을 추가해 "🔁 반복 패턴 — N건째 발생 (M건마다 감지 · K번째 감지)" 형태로 카드에 표시. 카드 포맷 자체는 새로 안 만들고 기존 `build_teams_message()`를 그대로 재사용(반복 정보는 그 안의 한 줄일 뿐). 부수적으로 `pattern_detection.detect_and_update_cluster()`가 이제 아무도 안 읽는 `pattern_info`(대표/샘플 제목) 계산을 위해 매 건마다 불필요한 DB 쿼리를 날리고 있던 게 자체 점검 중 발견돼 제거(성능 개선). 실 프로덕션 메일함으로 재현 검증 완료(최근 90건 분석기록을 초기화 후 재수집 → 클러스터 48건이 원래 17개 클러스터로 정확히 복원, 카테고리 게이트로 걸러지지 않은 단독 VOC 1건만 옛 로직으로 발송된 것을 확인 후 이번 변경으로 그 케이스도 막힘 확인).
- v2.47: VOC 반복 패턴 탐지 + 통계 대시보드 신규 — `service/email_voc/pattern_detection.py` 추가. `service.check_relevance()`가 이미 계산하는 임베딩(`RelevanceCheck.query_vec`)을 그대로 저장·재사용해(`ops_email_analysis.embedding`), 지식 베이스 비교(기존)와 별개로 **과거 VOC와의 비교**를 pgvector 코사인 연산만으로 수행 — 추가 LLM 호출 없이 반복 유형을 감지한다. 새 VOC는 클러스터의 개별 멤버가 아니라 **centroid(대표 임베딩, 점증 가중평균 갱신)**와 비교해 합류 여부를 판단(단일 링크 클러스터링의 사슬형 오분류 방지). 유사 건이 `email_pattern_window_days`(7일) 내 `email_pattern_min_count`(3건) 이상 쌓이면, 별도 Teams 메시지를 만들지 않고 이미 발송 중인 개별 VOC 카드에 "🔁 반복 패턴" 한 줄 + 해결방안(있으면)을 얹는다(min_count를 넘긴 이후 모든 건에 매번 표시 — 최초 1건만 표시하면 후속 발생에서 반복 맥락이 안 보이는 문제가 실사용 중 발견돼 변경). 해결방안 존재 여부(`get_cluster_coverage`)는 `rag_knowledge`와의 코사인 유사도 1차 필터(`_COVERAGE_MIN_SIMILARITY=0.70`) 통과 후 **LLM 재검증**까지 거친다 — 순수 코사인 유사도는 무관한 문서가 CS 보일러플레이트 문구만으로 임계치를 넘는 오탐을 못 막는다는 게 실측으로 확인돼(같은 유사도 대역에 몰려 있어 임계치 조정만으로는 해결 불가), 크로스인코더 리랭커(huggingface.co 접근 제한으로 보류 중) 대신 기존 LLM 프로바이더로 "이 문서가 실제로 이 VOC 유형의 해결책이 맞는지" 1회 확인한다. 검증 결과는 `ops_voc_cluster.coverage_knowledge_id`/`coverage_verified`에 캐싱해 매칭된 지식이 바뀔 때만 재호출. 관리자 화면에 "VOC 통계" 탭 신규 — 유형/심각도 분포 도넛차트(클릭 시 클러스터 목록 필터링), 반복 클러스터 목록(페이징, 클러스터 내 category 불일치 경고), 클러스터에서 바로 지식 등록. 상세: `docs/tech/voc-email-handoff.md`.
- v2.46: VOC 이메일 — 인하우스 LLM 게이트웨이가 프롬프트에 IP·이메일·전화번호가 섞이면 "민감 정보 포함"으로 응답을 통째로 거부하는 정책이 실사용 중 확인됨(호스트 IP·CC 목록·서명란 연락처가 거의 모든 실 메일에 있어, 사실상 대부분의 메일이 분석되지 못하던 상태). `service.py`에 `_mask_pii()` 추가 — LLM 프롬프트에 넣기 직전에만 IP/이메일/전화번호를 마스킹(DB 저장·Teams 알림은 원본 유지). 아울러 관련성 게이트 개선안으로 `shared/reranker.py`(기존 chat 전용 CrossEncoder 리랭커)에 점수 노출용 `score()`/`is_available()`을 추가해뒀으나, 이 개발 환경은 huggingface.co 접속이 막혀 모델을 못 받아 게이트 연동·실측은 보류(상세: `docs/tech/voc-email-handoff.md` §7-11, §7-12, git 비추적).
- v2.45: VOC 이메일 관련지식 필터 무력화 버그 수정 — `retrieval.search_knowledge()`가 반환하는 `final_score`는 검색 랭킹용으로 `(가중합)*(1+base_weight)`가 곱해져 있는데(base_weight 기본값 1.0), `service.check_relevance()`가 이 부풀려진 값을 그대로 관련성 게이트(`email_relevance_min_score`)와 비교해와 무관한 메일도 임계치를 가볍게 넘어 Teams 알림 노이즈를 유발하던 것이 실사용 중 확인됨. 게이트 판단은 base_weight 부스팅 없는 원점수(`w_vector*v_score + w_keyword*k_score`)로 계산하도록 수정(지식 인용 랭킹은 기존 `final_score` 유지 — 목적이 다른 두 계산을 분리). 실측 재조정으로 임계치 0.35 → 0.38.
- v2.44: VOC 이메일 3건 개선 — ① **메일 폴더 범위 제한**: `graph_client.list_mail_folders()`/`GET /mail-folders`로 실제 Outlook 폴더 목록을 조회해 라우팅에서 선택 가능(`ops_voc_routing.mail_folder_id`) — 지정 시 그 폴더만 폴링. ② **이력 필터링**: `GET /history`에 심각도/상태/오배치여부/키워드 쿼리 파라미터 추가. ③ **Teams 카드 개선**: 제목/내용/해결방안/참고지식(근거+유사도) 섹션 구조화, 심각도 4단계(low/medium/high/urgent)를 각각 다른 색으로 구분(기존엔 사실상 2단계로만 시각 구분됨).
- v2.43: VOC 이메일 Delegated 로그인 실사용 성공 + 실사용 중 발견한 버그 2건 수정. ① **Confidential Client 지원**: 등록된 리다이렉트 URI가 Azure AD "Web" 플랫폼으로 등록된 경우 PKCE만으로 토큰 교환이 거부되는(AADSTS7000218) 사례가 실측 확인돼, `delegated_auth.py`의 `_build_app()`이 `client_secret` 설정 여부에 따라 `PublicClientApplication`/`ConfidentialClientApplication`을 분기하도록 변경(시크릿 값은 Fernet 암호화로 DB 저장, API 응답엔 `client_secret_configured` bool만 노출). ② **키워드 검색 tsquery 크래시 수정(공용 검색 엔진 영향)**: `agents/knowledge_rag/knowledge/retrieval.py`의 `search_knowledge()`가 lexeme을 `string_agg`로 이어붙여 `to_tsquery`에 그대로 넘기던 것을, URL 등에서 추출된 lexeme에 짝 안 맞는 괄호 등 tsquery 특수문자가 섞이면 `syntax error in tsquery`로 죽던 버그(VOC 실메일 fetch 중 실측 재현) — `quote_literal()`로 각 lexeme을 감싸 해결. 채팅 KnowledgeRAG 검색도 동일 함수를 쓰므로 함께 수정됨. ③ **폴링 성능 최적화**: `pipeline.run_manual_collection()`이 재조회 윈도우(lookback_days) 안의 메일을 매 사이클 다시 fetch하면서, 이미 처리된 메일까지 관련지식 검색+LLM 분석을 먼저 돌리고서야 DB `ON CONFLICT`로 중복임을 알던 구조를 — fetch 직후 `(namespace_id, source_message_id)` 배치 조회로 먼저 걸러내도록 변경. 실측: 144건 재조회 시 190초(전량 재분석) → 1.3초(전량 사전 스킵)로 개선.
- v2.42: VOC 이메일 Delegated 로그인 방식 교체 — Device Code Flow(코드를 사람이 손으로 옮겨 입력) 대신 **Authorization Code Flow(PKCE, Public Client)**로 변경. 사유: Device Code Flow는 "Device Code Phishing"(공격자가 자기 코드를 발급받아 피해자에게 입력시켜 토큰을 가로채는 공격) 리스크가 있어, 필요 설정("Allow public client flows")을 보안팀이 비권장 사유로 거부. Authorization Code Flow는 사람이 코드를 옮겨 입력하는 과정 자체가 없어 이 리스크가 없고, Azure AD 쪽엔 정식 지원 필드인 리다이렉트 URL만 등록하면 된다. `service/email_voc/delegated_auth.py`의 `start_login()`을 재작성하고 `complete_login()`을 신규 추가, `router.py`에 콜백 엔드포인트(`GET /delegated-auth/callback`, 인증 불필요 — MSAL의 PKCE/state 검증으로 CSRF 방어) 추가. `ops_system_config.email_graph_delegated`에 `redirect_uri` 필드 추가(프론트가 `window.location.origin` 기준으로 자동 계산). 로컬 CLI 스크립트(`email_voc_local_test.py`)는 터미널 환경 특성상 Device Code Flow를 그대로 유지(관리자 화면과는 별개 경로).
- v2.41: VOC 이메일 채널 2건 추가 — ① **관련지식 사전 필터**: `service/email_voc/service.py`의 `analyze_email()`을 `check_relevance()`(임베딩+검색)와 LLM 분석 단계로 분리, 등록된 지식과의 최고 유사도가 관리자 설정 임계치(`email_relevance_min_score`, 기본 0.35) 미만이면 LLM 호출·Teams 발송 없이 `status='skipped_relevance'`로만 기록해 무관한 메일(스팸·사내공지 등)의 비용·알림 노이즈를 억제. ② **Delegated Permission 로그인**(`service/email_voc/delegated_auth.py`, Device Code Flow): Application 권한(Track B) 승인 전에도 관리자가 본인 계정으로 1회 로그인하면 이후 기존 폴링 토글/수동실행이 그 세션으로 자동 대체 동작 — `pipeline.run_manual_collection()`에 `access_token`/`skip_credential_resolution` 파라미터 추가로 연결(운영 경로인 Application 권한 흐름은 변경 없음). 로컬 검증용 `backend/scripts/email_voc_local_test.py` 추가.
- v2.40: VOC 이메일 분석 채널(Track A) 신규 구현 — `service/email_voc/` 모듈(수집·RAG 분석·라우팅·Teams 알림·백그라운드 폴링 스케줄러·30일 보관정책). 파트별 공용 메일함을 Microsoft Graph API(msal client_credentials)로 수집해 기존 하이브리드 검색·LLM 파이프라인으로 분석 후 담당 파트 Teams 채널에 자동 알림. Graph API 실 연동(Track B)은 M365 보안성 검토·API 제공 등 조직 승인 대기 중 — 승인 전까지는 텍스트 직접 입력 테스트/수동 자격증명 미설정 상태로 동작. 상세 설계는 `docs/email-analysis-channel-plan.md` 참조.
- v2.39: 로그인 브루트포스 방어 — Redis 기반 rate limiting(사용자명+IP 조합, 5분 내 5회 실패 시 5분 잠금, `shared/rate_limit.py`, Redis 미연결 시 제한 없이 통과) + 아이디 존재 여부와 비밀번호 오류를 구분하지 않는 통합 에러 메시지로 계정 열거(enumeration) 오라클 제거.
- v2.38: 백엔드 전수 감사 2차(auth/admin/teams/text2sql) — 관리자 파트로 셀프 회원가입 가능하던 구멍, 파트 삭제 시 소유 네임스페이스가 조용히 "공통 파트"(전원 허용)로 전환되던 문제, `PUT /api/llm/config` 관리자 권한 누락, 네임스페이스 rename/delete 시 시맨틱 캐시 미무효화, 캐시 Redis glob 인젝션, text2sql SQL 안전성 검사 우회(pg_read_file/dblink 등), text2sql 크로스테넌트 쓰기 등 Critical/High 다수 수정. 1차 감사 잔여 Medium/Low 10건(no_knowledge 판정 보강, 대화요약 tie-breaker, 대량등록 배치 내 상호중복검사, fewshot 상태 응답 버그, TOCTOU 완화 등)도 함께 정리. 지식조회 화면에 가중치 정렬(높은순/낮은순) 추가.
- v2.37: 백엔드 전수 감사 1차(fewshot/chat/knowledge_rag/mcp_tool) — 나빠요 피드백 후 지식 등록 시 오답 원인 지식의 가중치가 오히려 올라가던 버그를 계기로 같은 클래스(플래그 오버로딩, stale 데이터, 소유권 검사 누락)의 버그를 다른 모듈에서도 탐색. MCP 도구 승인 카드가 미입력 필수 파라미터를 example 힌트값으로 몰래 채워 승인을 통과시키던 것, 시맨틱 캐시가 agent_type 구분 없이 knowledge_rag/mcp_tool 답변을 서로 새게 하던 것 등 Critical 2건 포함.
- v2.36: 나빠요 피드백 → 지식등록/지식공백 해결 흐름 버그 수정 — `is_positive` 플래그를 상태 판정과 가중치 방향 두 용도로 겹쳐 쓰던 게 원인. `ops_query_log.resolved_knowledge_id` 컬럼 추가로 해결 처리된 질의를 실제 등록 지식과 연결(통계 화면이 원래 AI 오답 대신 등록 내용을 보여줌), 피드백/해결 매칭을 질문 텍스트 대신 message_id로 정밀화, 통계 페이지 "승인" 원클릭 버튼이 raw INSERT로 중복검사·업무구분 없이 등록하던 것을 `create_knowledge()` 재사용으로 교체, 등록 폼들의 하드코딩된 "없음(파트 공통)" 카테고리 옵션 제거.
- v2.35: 파이프라인 디버그 탭 UX — 업무구분 필터와 MCP 도구 사용 토글을 같은 행(좌/우)에 배치, 업무구분 드롭다운의 "전체"를 다른 항목과 동일한 체크박스로 바꿔 상호배타 선택 명확화.
- v2.34: 지식 중복 등록 방지 — 등록 시점에 청크 단위로 기존 활성 지식과 유사도 비교(`duplicate_min_similarity`, 기본 0.88), 임계값 이상이면 `rag_knowledge.status='pending_review'`로 저장해 검색에서 숨기고 승인 대기 큐로 전환. 관리자가 승인(그대로 인정)/반려(status='rejected', 감사 기록 보존)/덮어쓰기(기존 지식의 content를 새 내용으로 교체) 중 선택. 대량 업로드는 배치 단위 병렬(`asyncio.gather`) 유사도 검사로 대규모 등록에도 지연 없음. 매칭된 기존 지식 후보는 `rag_knowledge_duplicate_match` 테이블에 top-N 기록, 리뷰 화면에서 좌(신규)/우(기존, 아코디언으로 펼쳐 전문 확인) 비교 후 처리.
- v2.33: 지식 등록/수정 폼 UX 개선 — 업무구분(category)을 모든 등록·수정 폼(수정 모달, 직접입력, 파일업로드, 텍스트분할, URL/Confluence, Teams)의 최상단 필드로 재배치. 가장 먼저 결정해야 하는 값인데 폼마다 위치가 달라 놓치기 쉬웠음.
- v2.32: 지식 공백(no_knowledge) 판정 보정 — 검색 문서가 점수 임계값은 넘었지만 실제로 무관해 LLM이 "관련 지식을 찾지 못했습니다"로 답한 경우도 지식 공백으로 분류하도록 `create_query_log`에 답변 문구 기반 게이트 추가, 기존 오분류 데이터 소급 보정.
- v2.31: 어드민 지식 테이블 일괄 수정 — 다건 선택 후 업무구분/소스유형을 한 번에 변경(`POST /api/knowledge/bulk-update`, 값을 지정한 필드만 변경). 기존 다건 삭제와 동일한 선택 UI 재사용.
- v2.30: 실사용 피드백 반영 3건 — (1) 멀티턴 검색 관련성 게이트(무관한 주제로 전환된 질문이 직전 맥락에 오염되지 않도록 임베딩 유사도 확인 후 결합), (2) 에이전트별 대화방 분리(`ops_conversation.agent_type` 실제 저장 + 목록 필터링 + 다른 에이전트로 이어쓰기 시 409 거부), (3) 채팅 업무구분 필터를 단일 선택(사이드바)에서 입력창 위 다중 선택 드롭다운으로 교체 — `ChatRequest.categories: string[]`, `search_knowledge`가 `k.category = ANY(...)`로 다중 필터.
- v2.29: 대용량 등록 진행률 표시 + 중지/롤백 — `bulk_create_knowledge` 백그라운드(asyncio.create_task) + 배치(50건) 처리로 재구성, job_id 즉시 반환 + 배치마다 진행률 갱신. 신규 API `ingestion-jobs/{id}`(폴링) / `.../cancel`(중지 시 등록분 롤백). 청크 검토 모달에 업무구분 필드 보완.
- v2.28: 업무구분 기본값 `'공통지식'` 지정 + 기존 미분류 지식 일괄 백필, 카테고리 없는 네임스페이스 자동 생성.
- v2.27: 업무구분(category) 전 등록 경로 필수화 — 서비스 레벨 공통 검증(`_require_category`), `ValueError`→400 글로벌 핸들러 추가.
- v2.26: AI 용어추천 데이터소스 선택(미매핑 질문/등록된 지식) + 용어집 중복 등록 방지(대소문자 무시 체크, 프롬프트+응답 이중 필터).
- v2.25: 어드민 UI 개선 — 등록자 노출, 토글 knob 위치 버그 수정, 업무유형 분포 라벨을 용어집 설명 기반으로 개선, 미구현 placeholder 카드 제거.
- v2.24: 엑셀 임포트 샘플 템플릿(xlsx) 다운로드 API/버튼 추가.
- v2.23: Text2SQL 스키마 엑셀 임포트 — 헤더 퍼지 매핑, preview/confirm 2단계, 중복 skip, 임베딩 자동 생성.
- v2.22: 지식 공백(no_knowledge) 대시보드 시각화 — 카드 클릭 → 질문 목록 → 등록 폼 팝업 흐름.
- v2.21: 런타임 안정성 버그 3건 수정(namespace FK 오류, 요약 파싱 오류, null byte ingestion 실패).
- v2.20: EUC-KR 인코딩 지원(CSV/MD/TXT), PDF 리소스 누수 수정.
- v2.19: CrossEncoder 리랭커, 지식 신선도 Decay 옵션, 지식 갭(no_knowledge) 자동 감지.
- v2.18: 안정성 버그 수정 배치(URL preview, HTML 파싱, 청킹, 캐시 키 충돌 등).
- v2.17: DevX LLM OAuth2 전환(사용자별 자격증명 Fernet 암호화) + Confluence BFS 일괄 등록.
- v2.16: 어드민 테이블 텍스트/벡터 검색 + 체크박스 다건 삭제.
- v2.15: Teams 메시지 수집(OpsNavHelper.exe 기반 토큰 캡처) → 지식베이스 등록.
- v2.14: ChunkReviewModal, LLM Analyzer 자동 청킹, 레거시 Streamlit 삭제.
- v2.13: URL/Confluence 인제스천(httpx+BeautifulSoup, 등록 전 확인 모달).
- v2.12: 지식 인제스천 고도화 — 텍스트 분할, 파일 업로드, 자동 태깅/Q&A 생성.
- v2.9~v2.11: Text2SQL 고도화 — Oracle 지원 + Dialect 패턴 리팩터링, 스키마 스캔 diff 개선, `ops_prompt` 에이전트별 분리.
- v2.5~v2.8: Text2SQL 에이전트 도입(7단계 파이프라인) + ERD + MCP 도구, `domain/`→`service/` 구조 재편.
- v2.0~v2.4: 초기 구조 — DDD, JWT 인증, AgentRegistry 패턴, Semantic Cache.

---

## 전체 구성도

```
┌─────────────────────────────────────────────────────────────┐
│                        Host Machine                         │
│                                                             │
│   ┌──────────────────────────────────────────────────────┐  │
│   │              Docker Compose Network                  │  │
│   │                                                      │  │
│   │  ┌─────────────┐    ┌─────────────┐                 │  │
│   │  │  Frontend   │───▶│   Backend   │                 │  │
│   │  │  React+nginx│    │   FastAPI   │                 │  │
│   │  │  :8501      │    │   :8000     │                 │  │
│   │  └─────────────┘    └──────┬──────┘                 │  │
│   │                            │                         │  │
│   │                    ┌───────┴────────┐                │  │
│   │                    │   PostgreSQL   │                │  │
│   │                    │  + pgvector    │                │  │
│   │                    │   :5432        │                │  │
│   │                    └───────────────┘                 │  │
│   └──────────────────────────────────────────────────────┘  │
│                                                             │
│   ┌──────────────────────┐                                  │
│   │  Ollama (호스트 직접)  │  ◀── Backend이 host.docker.      │
│   │  exaone3.5:2.4b      │       internal:11434 으로 호출   │
│   │  :11434              │                                  │
│   └──────────────────────┘                                  │
└─────────────────────────────────────────────────────────────┘
```

---

## 컴포넌트별 역할

### 1. Frontend — React + Nginx (`:8501`)

| 페이지 | 역할 |
|--------|------|
| **Login** (`/login`) | JWT 로그인 — Access Token + Refresh Token 발급 |
| **Register** (`/register`) | 회원가입 — 부서 선택 + 선택적 LLM API Key 등록 |
| **AgentSelect** (로그인 직후) | 에이전트 선택 화면 — 지식베이스 AI 카드 선택. `selectedAgent=null`이면 이 화면 표시 (사이드바 없음) |
| **Chat** (`/`) | 에이전트별 채팅 — SSE 스트리밍, 결과 카드, 피드백(👍→few-shot/base_weight), 대화 메모리(요약+리콜), Markdown 답변, MCP 도구 토글 |
| **Admin** (`/admin`) | 에이전트별 관리 화면 — `agentScope` 필드로 탭 필터링. knowledge_rag: 네임스페이스·지식·용어집·Few-shot·MCP도구·캐시현황·통계·디버그. 공통: 시스템설정·사용자관리. (에이전트현황 탭 제거 — AgentSelect 화면에 헬스배지로 대체) |

- **Agent-centric 라우팅**: `useAppStore.selectedAgent: 'knowledge_rag' | null`. null이면 AgentSelect 표시, 설정 시 에이전트별 UI로 전환. 로그아웃 시 null로 리셋
- **MCP 도구 토글**: ChatContainer 내 `useHttpTool` boolean — ON 시 `agentType='mcp_tool'`, OFF 시 `selectedAgent` 값 사용. 에이전트가 아닌 도구
- **ProtectedRoute**: 로그인되지 않은 사용자는 `/login`으로 리다이렉트
- **useAuthStore** (Zustand): localStorage에 토큰 저장, 자동 Bearer 토큰 주입
- **401 Auto-refresh**: Access Token 만료 시 Refresh Token으로 자동 갱신, 실패 시 로그아웃
- **부서 기반 UI**: 지식/용어집/Q&A 테이블에 부서 배지 표시, 같은 부서만 수정/삭제 버튼 노출
- **어드민 테이블 검색**: 지식베이스·용어집·Q&A 세 테이블 모두 텍스트 검색(즉시 필터) + 벡터 유사도 검색([문자열|벡터] 토글) 지원. 벡터 검색은 Enter 또는 검색 버튼으로 실행하고 유사도 % 배지 표시
- **어드민 테이블 다건 삭제**: 전체 선택 체크박스 + 행별 체크박스, N개 선택 시 액션 바 표시 → 일괄 삭제
- Sidebar: 에이전트 배지 + 에이전트 변경 버튼, 사용자 정보 + 로그아웃, 네임스페이스 선택, 대화 목록, 검색 설정 슬라이더, 헬스 표시기
- Backend REST API만 호출 (직접 DB 접근 없음)
- 검색 비중(벡터/키워드 비율), Top-K를 사이드바 슬라이더로 실시간 조정 (개인 설정은 localStorage에 저장, DB 저장 없음)
- nginx 정적 빌드 서빙 + `/api/*` 요청을 Backend(`:8000`)로 프록시

### 2. Backend — FastAPI (`:8000`)

```
backend/
├── main.py              # 앱 진입점, 라이프사이클 (DB풀·임베딩·LLM·에이전트 초기화)
├── agents/              # 에이전트 레이어 (AgentBase + AgentRegistry 패턴)
│   ├── base.py          #   AgentBase 추상 클래스 + AgentRegistry 싱글톤
│   ├── knowledge_rag/
│   │   ├── agent.py     #   KnowledgeRagAgent — 하이브리드 검색 + LLM 스트리밍
│   │   ├── knowledge/   #   지식/용어집 CRUD + 하이브리드 검색 (retrieval.py, DB/공통코드 카테고리는 벡터 점수 0 처리 v2.49)
│   │   ├── ingestion/   #   지식 인제스천 파이프라인 (Tier 1~3)
│   │   │   ├── adapters.py      #   파일 파싱 (.txt/.md/.pdf → ParsedDocument)
│   │   │   ├── chunker.py       #   청킹 엔진 (section/paragraph/fixed/auto)
│   │   │   ├── analyzer.py      #   LLM Analyzer Agent (doc_type, chunk_strategy 자동 결정)
│   │   │   ├── tagger.py        #   LLM 자동 태깅 + 용어 추출
│   │   │   ├── qa_gen.py        #   LLM Q&A 자동 생성 → fewshot candidate
│   │   │   ├── web_crawler.py   #   URL/Confluence 수집 (httpx + BeautifulSoup + Confluence REST API)
│   │   │   └── utils.py         #   공통 JSON 파싱 헬퍼
│   │   └── fewshot/     #   Few-shot CRUD (status: active/candidate)
│   ├── mcp_tool/
│   │   └── agent.py     #   McpToolAgent — 3-case 플로우 + RAG + 감사 로그
│   └── http_tool/       #   HttpToolAgent (레거시)
├── service/             # 플랫폼 공통 레이어 (was domain/, platform/ 명칭 stdlib 충돌로 service/ 확정)
│   ├── auth/            #   인증/계정 (JWT, bcrypt, Fernet API Key 암호화)
│   ├── chat/            #   채팅 라우터·헬퍼·메모리 (AgentRegistry 위임)
│   ├── feedback/        #   피드백 기록 + base_weight 조정
│   ├── admin/           #   네임스페이스·통계·LLM 설정
│   ├── mcp_tool/        #   MCP 도구 CRUD + 감사 로그
│   ├── prompt/          #   프롬프트 관리 (get_prompt: DB 우선, fallback)
│   ├── llm/             #   LLM Provider 추상화 (ollama / inhouse)
│   ├── email_voc/       #   VOC 이메일 분석 채널 (v2.40 신규)
│   │   ├── graph_client.py    #   Microsoft Graph API 클라이언트 (msal 토큰 발급, 메일 조회, 페이지네이션/재시도)
│   │   ├── service.py         #   check_relevance()(관련지식 사전 필터, v2.41) + analyze_email() — 기존 RAG 파이프라인 재사용 분류/심각도/오배치 판정 + issue_signature(정규화 이슈요약) 출력 (v2.49)
│   │   ├── pattern_detection.py #   반복 VOC 클러스터링(centroid 기반, issue_signature 임베딩 비교, LLM 호출 없음) + 커버리지 LLM 검증 게이트(DB/공통코드 카테고리 제외, v2.49) (v2.47 신규)
│   │   ├── pipeline.py        #   수집→관련지식필터→분석→반복패턴탐지→중복제거→알림 오케스트레이션, 이력 조회
│   │   ├── routing_service.py #   파트별 메일함 라우팅 CRUD, 폴링 설정(관련지식 임계치 포함), Graph 자격증명(Fernet 암호화) CRUD
│   │   ├── delegated_auth.py  #   Delegated Permission 로그인 상태 관리 (Authorization Code Flow/PKCE, v2.41 신규·v2.42 인증방식 교체)
│   │   ├── teams_notify.py    #   Teams Workflows 웹훅 발송
│   │   ├── scheduler.py       #   백그라운드 폴링 루프 (asyncio.create_task, lifespan 등록)
│   │   ├── retention.py       #   30일 고정 보관정책 자동 정리
│   │   ├── schemas.py         #   Pydantic 스키마
│   │   └── router.py          #   /api/email-voc/* 엔드포인트
│   └── policy/           #   정책서 데이터화 파이프라인 v1 (v2.52 신규, docs/policy-doc-pipeline-plan.md)
│       ├── excel_parser.py    #   시트 판별(용어집/정책) + 헤더 퍼지매핑 + 동적 깊이 감지(category_path)
│       ├── decompose.py       #   LLM segment 분해 — narrative/param/unresolved 3분류
│       ├── service.py         #   버전 관리(logical_id/version/supersedes_id, INSERT-only) + LLM 분해 동시성(세마포어5) + policy_item/param/chunk 적재
│       ├── search.py          #   파라미터(RDB tsquery)+서술(벡터) 검색 — 전용 엔드포인트, retrieval.py 미편입(Track 2 대기)
│       ├── unresolved_report.py #   unresolved/partial 항목 system_key별 집계 (v2.53 신규)
│       ├── schemas.py         #   Pydantic 스키마
│       └── router.py          #   POST /api/policy/import, GET /api/policy/search, GET /api/policy/unresolved-summary
├── core/
│   ├── config.py        # pydantic-settings, JWT·Fernet 키
│   ├── database.py      # asyncpg 풀 + resolve_namespace_id() 헬퍼
│   ├── security.py      # JWT, bcrypt, Fernet
│   └── dependencies.py  # get_current_user, get_current_admin, check_namespace_ownership
└── shared/
    ├── embedding.py     # Sentence-Transformers 싱글톤
    └── cache.py         # Semantic Cache (Redis, 유사도 0.88, TTL 30분, graceful degradation)
```

**주요 설계 원칙:**
- **AgentRegistry 패턴**: `chat_stream` → `AgentRegistry.get(agent_type).stream_chat()` 위임. 새 에이전트 추가 시 `agents/` 하위 모듈 + Registry 등록만으로 완결. 플랫폼(인증/세션/피드백)과 에이전트(파이프라인)를 분리.
- **DDD 구조**: 도메인별 디렉토리로 schemas/service/router를 응집 — 플랫 구조 대비 코드 탐색·확장 용이
- **비동기 전용**: asyncpg + httpx async — 블로킹 없는 I/O
- **임베딩 싱글톤**: 앱 시작 시 모델 1회 로드, 이후 thread executor로 재사용
- **LLM Provider 패턴**: `ollama` / `inhouse` 환경변수 하나로 교체 가능
- **LLM별 프롬프트 형식**: Ollama는 `build_messages()` messages 배열, InHouse(DevX MCP API)는 `_build_query()`로 단일 query 문자열 생성
- **대화 맥락**: ConversationSummaryBuffer + Semantic Recall — 오래된 교환을 LLM으로 요약·벡터 저장, 현재 질문과 유사한 과거 요약 + 최근 2회 raw 교환을 history로 LLM에 전달
- **멀티턴 검색 보강**: 직전 Q+A(각 80자)를 현재 질문에 결합하여 임베딩/검색 — 짧은 후속 질문에서도 이전 대화 맥락이 반영되어 유사도 향상 (추가 LLM 호출 없음)
- **마크다운 답변**: 시스템 프롬프트에 Markdown 형식 지시 포함, 프론트엔드에서 `react-markdown` + `remark-gfm` + `rehype-raw`로 테이블/코드/리스트/HTML 태그 렌더링
- **JWT 인증/인가**: Access Token(30분) + Refresh Token(7일), FastAPI Depends로 라우터 수준 보호
- **네임스페이스 소유 파트 기반 권한**: 네임스페이스의 `owner_part`와 동일한 부서 구성원만 해당 네임스페이스의 데이터 CRUD 가능, 타 부서는 읽기 전용. `owner_part` NULL이면 **모든 사용자(파트 무관)**가 CRUD 가능 (공통 namespace). Admin이 생성한 namespace는 자동으로 `owner_part = NULL`. Admin은 모든 권한 보유
- **수정 시 작성자 갱신**: 지식/용어/퓨샷 수정 시 `created_by_part`/`created_by_user_id`가 최종 수정자로 갱신됨
- **Graceful Degradation**: LLM 연결 실패 시 검색 결과는 정상 반환, 안내 메시지 출력

### 3. 인증/인가 시스템 (v2.0.0 신규)

```
┌──────────┐     POST /api/auth/register     ┌──────────────┐
│  사용자   │  ──────────────────────────────▶ │  auth/service │
│          │     (username, password,         │              │
│          │      part_id, api_key?)          │  bcrypt hash │
│          │                                  │  Fernet enc  │
│          │  ◀────────────────────────────── │              │
│          │     201 Created                  └──────┬───────┘
│          │                                         │
│          │     POST /api/auth/login                │
│          │  ──────────────────────────────▶        │
│          │  ◀──────────────────────────────        │
│          │     {access_token, refresh_token}       │
│          │                                         │
│          │     GET /api/chat/stream                │
│          │     Authorization: Bearer <access>      │
│          │  ──────────────────────────────▶ ┌──────┴───────┐
│          │                                  │ dependencies │
│          │                                  │ get_current_ │
│          │                                  │ user()       │
└──────────┘                                  └──────────────┘
```

**핵심 구성 요소:**

| 모듈 | 역할 |
|------|------|
| `core/security.py` | JWT 토큰 발급/검증 (HS256), bcrypt 비밀번호 해싱, Fernet 대칭 암호화 (LLM API Key + Confluence PAT) |
| `core/dependencies.py` | `get_current_user` — Bearer 토큰 검증 후 사용자 반환 |
| | `get_current_admin` — admin 역할 검증 |
| | `check_namespace_ownership` — 네임스페이스의 `owner_part`와 요청자 부서 일치 확인 |
| `service/auth/service.py` | 회원가입 (중복 체크, bcrypt 해싱, Fernet API Key 암호화), 로그인, 토큰 갱신 |
| `service/auth/router.py` | `/api/auth/*` 엔드포인트 |

**권한 모델 (네임스페이스 기반):** 상세 규칙은 `api-specification.md § 3. 인증 및 권한` 참조.
- Admin은 모든 리소스 CRUD 가능. 일반 사용자는 `owner_part` 일치 시에만 CRUD (불일치 시 읽기 전용). `owner_part = NULL` (공통 namespace)는 모든 사용자 CRUD 가능.
- 대화 소유권: `ops_conversation.user_id` FK로 사용자별 대화 격리.

**사용자별 LLM API Key:**
- 회원가입 또는 계정 설정에서 사내 LLM API Key 등록 (선택사항)
- Fernet 대칭 암호화로 DB에 저장 → 요청 시 복호화하여 InHouse LLM Provider에 전달
- 개인 키가 없으면 시스템 기본 키(`INHOUSE_LLM_API_KEY`) 사용

**사용자별 Confluence PAT (v2.14 신규):**
- 계정 설정 또는 URL 수집 폼에서 Confluence Personal Access Token 등록 (선택사항)
- LLM API Key와 동일한 Fernet 암호화 방식으로 `ops_user.encrypted_confluence_pat`에 저장
- URL 수집 시 PAT를 명시하지 않으면 DB에 저장된 개인 PAT 자동 로드 → Confluence 인증 자동 처리

### 4. PostgreSQL + pgvector (`:5432`)

```sql
-- 플랫폼 공통 (ops_* prefix)
ops_part              -- 부서 레지스트리
ops_user              -- 사용자 (role, part_id FK, encrypted_llm_api_key, encrypted_confluence_pat)
ops_namespace         -- 네임스페이스 (owner_part_id FK, created_by_user_id)
ops_conversation      -- 대화방 (namespace_id FK, user_id FK, agent_type)
ops_message           -- 대화 메시지 (role, content, results JSONB, metadata JSONB)
ops_feedback          -- 👍/👎 피드백 로그 (agent_type, meta JSONB)
ops_query_log         -- 질의 로그 (status: pending/resolved/unresolved, agent_type)
ops_mcp_tool          -- MCP 도구 정의 (hub_base_url, tool_path, param_schema JSONB, agent_type)
ops_mcp_tool_log      -- MCP 도구 감사 로그
ops_prompt            -- 프롬프트 관리 (agent_type별 에이전트 스코핑, Admin 시스템설정 탭에서 편집)
ops_system_config     -- 시스템 설정 key-value (캐시 임계값/TTL 등 영속화, VOC 폴링 정책/Graph 자격증명도 여기 저장)

-- VOC 이메일 분석 채널 전용 (v2.40 신규)
ops_voc_routing       -- 파트별 담당 메일함 ↔ Teams 웹훅 ↔ 온콜 연락처 매핑
ops_email_analysis    -- 이메일 건별 분석 결과 (source_message_id UNIQUE로 중복 수집 방지, 30일 보관, v2.47: embedding VECTOR(768)/voc_cluster_id FK 추가)
ops_email_poll_cycle  -- 폴링 사이클(스케줄러 실행 회차)별 성공/실패 이력 (30일 보관)
ops_voc_cluster       -- 반복 VOC 클러스터 (representative_embedding=centroid, member_count, coverage_knowledge_id/coverage_verified — 해결방안 LLM 검증 캐시) (v2.47 신규)

-- KnowledgeRAG 전용 (rag_* prefix, v2.8에서 ops_*→rag_* 변경)
rag_knowledge         -- 지식 베이스 (HNSW + GIN FTS, base_weight, source_file/chunk_idx 추적)
rag_knowledge_category -- 카테고리 목록
rag_glossary          -- 용어집 (HNSW, 유사도 0.5+ 매핑)
rag_fewshot           -- Few-shot Q&A (HNSW, status: active/candidate)
rag_conv_summary      -- 대화 요약 (embedding VECTOR(768), Semantic Recall용)
rag_ingestion_job     -- 인제스천 작업 이력 (source_type, status, auto_glossary/fewshot 수, analyzer_result JSONB)

-- Text-to-SQL 전용 (sql_* prefix) — v2.51에서 에이전트 제거, 마이그레이션 호출도 제거됨
-- (기존 설치엔 테이블이 남아있지만 더 이상 갱신되지 않음. 스키마 상세는 archive/with-text2sql 브랜치 참고)
```

- **HNSW 인덱스** (`vector_cosine_ops`): 벡터 근사 최근접 이웃 검색
- **GIN 인덱스** (`to_tsvector('simple', content)`): 전문 검색(FTS)
- **pg_trgm**: 트리그램 유사도 지원 (활성화됨)
- **namespace_id integer FK**: 모든 테이블에서 도메인 격리. namespace 이름 변경 시 cascade 업데이트 불필요
- **CASCADE 삭제**: `ops_conversation.user_id` → 사용자 삭제 시 대화 자동 삭제

### 5. Ollama — LLM 추론 (`:11434`)

- 호스트 머신에서 직접 실행 (컨테이너 외부)
- 모델: `exaone3.5:2.4b`
- Backend에서 `host.docker.internal:11434`로 접근
- **`/api/chat` 엔드포인트** 사용 (GPT 방식 messages 배열, multi-turn 지원)

---

## 임베딩 모델

| 항목 | 값 |
|------|-----|
| 모델명 | `paraphrase-multilingual-mpnet-base-v2` |
| 벡터 차원 | 768 |
| 한국어 지원 | O |
| 실행 위치 | Backend 컨테이너 내 (CPU) |
| 캐시 볼륨 | `model-cache:/root/.cache/huggingface` |

- Docker 빌드 시 이미지에 모델 사전 다운로드 (컨테이너 시작 지연 없음)
- `normalize_embeddings=True` 적용 → 코사인 유사도 = 내적
- `EmbeddingService`는 `asyncio.Lock` 1개로 `embed()`/`embed_batch()`/`embed_long()`의 모델·토크나이저 접근을 직렬화(v2.49) — HuggingFace fast tokenizer(Rust, GIL 해제)가 동시 요청 시 내부 상태가 깨지는 `RuntimeError: Already borrowed`를 동시성 부하 실측 중 확인해 추가
- 같은 텍스트라도 **용도에 따라 다른 임베딩을 쓰는 경우가 있다**(v2.49) — VOC는 지식 검색(RAG)엔 원문 임베딩을, 반복 유형 클러스터링엔 LLM이 뽑은 정규화 요약(`issue_signature`)의 임베딩을 쓴다: 정밀 매칭은 문맥 보존이 유리하고, 표현 차이를 넘어선 그룹핑은 정규화가 유리하다는 기준

---

## API 엔드포인트 목록

### 인증 (`/api/auth`) — v2.0.0 신규

| 메서드 | 경로 | 인증 | 설명 |
|--------|------|------|------|
| `POST` | `/api/auth/register` | 없음 | 회원가입 (부서 선택 + 선택적 LLM API Key) |
| `POST` | `/api/auth/login` | 없음 | 로그인 → Access Token(30min) + Refresh Token(7days) 발급 |
| `POST` | `/api/auth/refresh` | Refresh Token | Access Token 갱신 |
| `GET` | `/api/auth/me` | Bearer | 내 정보 조회 |
| `PUT` | `/api/auth/me/password` | Bearer | 비밀번호 변경 |
| `PUT` | `/api/auth/me/api-key` | Bearer | 개인 LLM API Key 등록/변경 (Fernet 암호화 저장) |
| `PUT` | `/api/auth/me/confluence-pat` | Bearer | 개인 Confluence PAT 등록/변경 (Fernet 암호화 저장) |
| `DELETE` | `/api/auth/me/confluence-pat` | Bearer | 개인 Confluence PAT 삭제 |
| `GET` | `/api/auth/me/confluence-pat/status` | Bearer | Confluence PAT 등록 여부 조회 |
| `GET` | `/api/auth/users` | Admin | 전체 사용자 목록 |
| `PUT` | `/api/auth/users/{id}` | Admin | 사용자 정보 수정 (역할 변경 등) |
| `DELETE` | `/api/auth/users/{id}` | Admin | 사용자 삭제 |
| `GET` | `/api/auth/parts` | 없음 | 부서 목록 조회 (회원가입용 — 슈퍼어드민 파트 자동 제외) |
| `GET` | `/api/auth/parts/all` | Admin | 부서 목록 전체 조회 (관리자용 — 슈퍼어드민 파트 포함) |
| `POST` | `/api/auth/parts` | Admin | 부서 생성 |
| `PATCH` | `/api/auth/parts/{id}` | Admin | 부서 이름 변경 (name 컬럼만 업데이트 — integer FK로 cascade 불필요) |
| `DELETE` | `/api/auth/parts/{id}` | Admin | 부서 삭제 (소속 사용자 없는 경우만) |

### 채팅/대화

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `GET` | `/health` | 서버·LLM 상태 확인 |
| `POST` | `/api/chat` | 하이브리드 검색 + LLM 답변 (JSON) |
| `POST` | `/api/chat/stream` | 하이브리드 검색 + LLM 답변 (SSE 스트리밍, 단계별 status 이벤트) |
| `POST` | `/api/chat/debug` | LLM 없이 검색 파이프라인 전 과정 반환 (v_score, k_score, 용어집 유사도, few-shot 목록, LLM 컨텍스트 미리보기 포함) |
| `GET` | `/api/conversations` | 네임스페이스별 대화방 목록 (최근 50개, 본인 소유만) |
| `POST` | `/api/conversations` | 대화방 신규 생성 (user_id 자동 연결) |
| `GET` | `/api/conversations/{id}/messages` | 대화방 전체 메시지 조회 (status 필드 포함) |
| `DELETE` | `/api/conversations/{id}` | 대화방 삭제 (메시지 cascade) |
| `PATCH` | `/api/chat/messages/{id}/content` | 메시지 부분 저장 (프론트엔드 스트림 중단 시) |
| `DELETE` | `/api/chat/messages/{id}` | Ghost 메시지 삭제 (빈 assistant + 짝 user + 빈 대화방) |

### 지식/용어집

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `GET` | `/api/knowledge` | 지식 목록 조회 (namespace 필터) |
| `POST` | `/api/knowledge` | 지식 신규 등록 (네임스페이스 소유 파트 검증, 임베딩 자동 생성) |
| `PUT` | `/api/knowledge/{id}` | 지식 수정 (네임스페이스 소유 파트 또는 admin만) |
| `DELETE` | `/api/knowledge/{id}` | 지식 삭제 (네임스페이스 소유 파트 또는 admin만) |
| `POST` | `/api/knowledge/bulk` | JSON 배열 벌크 등록 — job_id 즉시 반환, 실제 임베딩+등록은 백그라운드 배치 처리 (v2.29) |
| `POST` | `/api/knowledge/import/csv` | CSV 파일 업로드 → 컬럼 매핑 → 벌크 등록 |
| `POST` | `/api/knowledge/import/text-split` | 대량 텍스트 붙여넣기 → 자동 분할 → 벌크 등록 |
| `POST` | `/api/knowledge/import/text-split/preview` | 텍스트 분할 미리보기 (등록 없음) |
| `POST` | `/api/knowledge/import/file` | 파일 업로드(.txt/.md/.pdf) → 파싱 → 청킹 → 벌크 등록 (Analyzer·태깅·용어추출·Q&A 선택적) |
| `POST` | `/api/knowledge/import/file/preview` | 파일 파싱+청킹 미리보기 (등록 없음) |
| `POST` | `/api/knowledge/import/url` | URL/Confluence 페이지 수집 → 청킹 → 벌크 등록 (PAT 미전달 시 DB 저장 개인 PAT 자동 로드) |
| `POST` | `/api/knowledge/import/url/preview` | URL 수집 미리보기 — LLM Analyzer 자동 청킹 전략 결정 (등록 없음) |
| `GET` | `/api/knowledge/ingestion-jobs` | 인제스천 작업 이력 조회 |
| `GET` | `/api/knowledge/ingestion-jobs/{id}` | 인제스천 작업 진행률 조회 (폴링용) — v2.29 신규 |
| `POST` | `/api/knowledge/ingestion-jobs/{id}/cancel` | 진행 중인 인제스천 작업 중지 요청 — 다음 배치 경계에서 중단 + 이미 등록된 데이터 롤백 — v2.29 신규 |
| `GET` | `/api/knowledge/glossary` | 용어집 목록 |
| `POST` | `/api/knowledge/glossary` | 용어 신규 등록 (임베딩 자동 생성) |
| `PUT` | `/api/knowledge/glossary/{id}` | 용어 수정 (재임베딩 자동, 같은 부서 또는 admin만) |
| `DELETE` | `/api/knowledge/glossary/{id}` | 용어 삭제 (같은 부서 또는 admin만) |

### 피드백/Few-shot

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `POST` | `/api/feedback` | 피드백 기록 + base_weight 조정 + few-shot 저장(👍시) |
| `GET` | `/api/fewshots` | Few-shot 목록 조회 (namespace 필터) |
| `POST` | `/api/fewshots` | Few-shot 신규 등록 (임베딩 자동 생성) |
| `PUT` | `/api/fewshots/{id}` | Few-shot 수정 (질문 변경 시 재임베딩, 같은 부서 또는 admin만) |
| `DELETE` | `/api/fewshots/{id}` | Few-shot 삭제 (같은 부서 또는 admin만) |
| `POST` | `/api/fewshots/search` | 질문으로 few-shot 검색 테스트 (실제 검색 결과 + 프롬프트 섹션 미리보기) |
| `PATCH` | `/api/fewshots/{id}/status` | Few-shot 상태 전환 (`active` ↔ `candidate`) |

### 관리/설정

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `GET` | `/api/namespaces` | 등록된 네임스페이스 목록 (문자열 배열) |
| `GET` | `/api/namespaces/detail` | 네임스페이스 상세 목록 (지식 수, 용어집 수 포함) |
| `POST` | `/api/namespaces` | 네임스페이스 신규 생성 (admin이 생성하면 owner_part=NULL) |
| `PATCH` | `/api/namespaces/{name}` | 네임스페이스 이름 변경 (name 컬럼만 업데이트 — integer FK로 cascade 불필요) |
| `DELETE` | `/api/namespaces/{name}` | 네임스페이스 및 하위 데이터 전체 삭제 |
| `GET` | `/api/llm/config` | 현재 LLM 프로바이더 설정 + 연결 상태 조회 |
| `PUT` | `/api/llm/config` | LLM 프로바이더 런타임 전환 — Admin은 전체 시스템 저장, 일반 사용자는 브라우저 localStorage에만 저장 |
| `POST` | `/api/llm/test` | 설정값으로 연결 테스트 (실제 전환 없음) |
| `GET` | `/api/stats` | 네임스페이스별 통계 (전체 namespace, 지식/용어집 개수 포함) |
| `GET` | `/api/stats/namespace/{name}` | 네임스페이스 상세 통계 (업무 유형별 분포, 미해결 목록) |
| `DELETE` | `/api/stats/query-log/{id}` | 미해결 질의 로그 삭제 (지식 등록 후 처리 완료 표시) |
| `GET` | `/api/admin/cache/stats` | 네임스페이스 Semantic Cache 통계 (total_entries, total_hits, connected) |
| `GET` | `/api/admin/cache/entries` | 캐시 엔트리 목록 (히트 수 내림차순, 질문·TTL·hits 포함) |
| `DELETE` | `/api/admin/cache` | 네임스페이스 캐시 전체 무효화 |
| `DELETE` | `/api/admin/cache/entry` | 단일 캐시 엔트리 삭제 |
| `POST` | `/api/admin/glossary/suggest` | 미매핑 질문 LLM 분석 → 용어 후보 반환 (`limit` 파라미터로 조회 건수 설정, 기본 50, 최대 200) |
| `POST` | `/api/admin/glossary/suggest/apply` | 추천 용어 1-click 등록 (임베딩 자동 생성) |

### Teams 수집 (`/api/teams-collect`) — v2.15 신규

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `GET` | `/api/teams-collect/auth/status` | Teams 인증 상태 조회 (토큰 유효성 검증 포함, 60초 캐시) |
| `POST` | `/api/teams-collect/auth/tokens` | 데스크톱 헬퍼가 캡처한 IC3/CSA 토큰 수신 → 인메모리 저장 |
| `POST` | `/api/teams-collect/auth/logout` | 인메모리 토큰·캐시 삭제 |
| `GET` | `/api/teams-collect/helper/download` | OpsNavHelper.exe 바이너리 다운로드 |
| `GET` | `/api/teams-collect/chats` | 캡처된 채팅방 목록 반환 |
| `POST` | `/api/teams-collect/messages` | syncState 페이징으로 채팅방 메시지 조회 (캐시 우선, 부족 시 Teams API 추가 로드) |
| `POST` | `/api/knowledge/import/teams` | 선택 메시지 스레드 → ParsedDocument → 청킹 → 벌크 등록 |

**Teams 인증 흐름:**
```
웹 UI "Teams 로그인" 버튼 (opsnav://teams-login?api_url=...&jwt=... 링크)
  → 사용자 PC의 OpsNavHelper.exe 자동 실행 (opsnav:// URL 스킴 등록 필요)
  → Playwright로 Teams 웹 로그인 화면 띄움
  → 사용자가 로그인하면 네트워크 후킹으로 IC3 토큰 + 채팅방 목록 캡처
  → POST /api/teams-collect/auth/tokens (JWT 인증)
  → 백엔드 인메모리 스토어에 저장 (DB 미저장, 재시작 시 소멸)
  → 프론트 2초 폴링으로 자동 감지 → 채팅방 목록 표시
```

**스크립트 구성 (`scripts/`):**
| 파일 | 역할 |
|------|------|
| `teams_desktop_login.py` | Playwright Teams 로그인·IC3 토큰 캡처 핵심 로직 |
| `install_url_handler.py` | `opsnav://` 커스텀 URL 스킴 OS 등록 (Windows 레지스트리) |
| `opsnav_helper_entry.py` | PyInstaller exe 진입점 (install/run/uninstall 모드 분기) |
| `dist/OpsNavHelper.exe` | PyInstaller 빌드 산출물 (docker-compose가 `/app/helper_assets`로 마운트) |

### VOC 이메일 수집 (`/api/email-voc`) — v2.40 신규, v2.44~v2.46 개선

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `POST` | `/api/email-voc/test-analyze` | 텍스트 직접 입력으로 분류/심각도/오배치 판정 프롬프트 테스트 (Graph API 연동 전에도 검증 가능) |
| `POST` | `/api/email-voc/test-notify` | 임의 Teams 웹훅 URL로 실제 알림 발송 테스트 (Admin) |
| `GET/PUT` | `/api/email-voc/settings` | 폴링 정책(활성화 여부/주기/조회기간) 조회·변경 (PUT은 Admin) |
| `GET/POST/PUT/DELETE` | `/api/email-voc/routing[/{id}]` | 파트별 메일함 라우팅 CRUD — PUT/DELETE는 `namespace` 쿼리로 소유 네임스페이스까지 검증(크로스 네임스페이스 변조 방지) |
| `GET/PUT` | `/api/email-voc/graph-credentials` | Microsoft Graph API 자격증명(Application 권한) 상태 조회·등록 (Admin, Fernet 암호화 저장, client_secret은 응답에 미포함) |
| `GET/PUT/POST` | `/api/email-voc/delegated-auth/{status,config,start}` | Delegated Permission(Authorization Code Flow, PKCE) 로그인 상태 조회·앱정보 저장·로그인 시작 (Admin, v2.41 신규·v2.42 인증방식 교체) — Application 권한 승인 전 임시 대체 경로 |
| `GET` | `/api/email-voc/delegated-auth/callback` | Microsoft 로그인 완료 후 리다이렉트 콜백 (인증 불필요 — 브라우저가 직접 호출, MSAL의 PKCE/state 검증으로 CSRF 방어, v2.42 신규) |
| `POST` | `/api/email-voc/collect/run` | 관리자가 기간(from~to, 최대 90일) 지정해 즉시 수집+분석+Teams 발송 1회 실행 (Admin) |
| `GET` | `/api/email-voc/history` | 이메일 분석+알림 이력 조회 (원본 메일 정보 + 분류결과 + 발송 성공/실패) |
| `GET` | `/api/email-voc/scheduler-status` | 백그라운드 폴링 스케줄러 실시간 상태 (동작 중 여부, 마지막 사이클 결과, 다음 예상 실행시각) |
| `GET` | `/api/email-voc/poll-cycles` | 폴링 사이클(스케줄러 실행 회차) 이력 조회 |
| `GET` | `/api/email-voc/mail-folders?mailbox_upn=` | 지정 메일함의 Outlook 폴더 목록 조회(Graph API 실조회, Admin) — 라우팅 등록 시 "전체 메일함" 대신 특정 폴더로 범위를 좁힐 때 사용(v2.44) |
| `GET` | `/api/email-voc/stats` | 유형(category)·심각도(severity) 분포 통계 — "VOC 통계" 탭 (v2.47 신규) |
| `GET` | `/api/email-voc/clusters` | 반복 VOC 클러스터 목록 — 멤버 수, 대표 제목, category/severity 분포(불일치 감지용), 해결방안 커버리지 (v2.47 신규) |
| `GET` | `/api/email-voc/clusters/{cluster_id}/members` | 클러스터에 속한 개별 VOC 목록 (v2.47 신규) |

`GET /api/email-voc/history`는 `severity`/`status`/`mismatch_only`/`keyword` 쿼리 파라미터로 필터링 가능(v2.44, keyword는 제목/발신자/본문 `ILIKE` 검색).

**수집 흐름 요약**: 백그라운드 스케줄러(`asyncio.create_task`, lifespan 등록)가 `email_polling_interval_minutes` 주기로 활성 라우팅 메일함을 Graph API로 조회(`mail_folder_id` 지정 시 그 폴더만, v2.44) → `check_relevance()`로 등록된 지식과의 최고 유사도 계산(base_weight 랭킹 부스팅이 섞이지 않은 원점수 기준, v2.45) → `email_relevance_min_score`(기본 0.38, v2.45에 0.35에서 재조정) 미만이면 LLM 호출 없이 `skipped_relevance`로 기록 후 다음 메일로(v2.41) → 이상이면 기존 하이브리드 검색+LLM 파이프라인 재사용해 분류(system_error/user_mistake/uncertain)·심각도·오배치 판정(LLM 프롬프트에 넣기 직전 IP/이메일/전화번호는 마스킹 — 인하우스 LLM 게이트웨이가 이런 패턴 포함 시 응답을 통째로 거부하는 정책이 있어 대응, v2.46) → `pattern_detection.detect_and_update_cluster()`로 반복 유형 클러스터링(category 무관하게 항상 수행 — 통계 화면 정확도용, v2.47; 비교 임베딩은 원문이 아니라 LLM이 뽑은 정규화 이슈요약 `issue_signature`의 임베딩 — 표현이 달라도 같은 이슈면 묶이도록, v2.49) → `source_message_id` UNIQUE 제약으로 중복 스킵(fetch 직후 배치 사전 체크로 이미 처리된 메일은 관련지식 검색·LLM 분석 자체를 건너뜀, v2.43) → **발송 게이트(v2.48)**: not_it_related면 미발송, 클러스터가 없으면(반복 아닌 단독 VOC) 미발송, 클러스터가 있어도 `email_pattern_min_count` 미만이면 미발송, 채운 뒤로는 그 배수(3/6/9건째)에서만 담당 파트 Teams 채널에 Workflows 웹훅으로 알림(제목/내용/해결방안/참고지식 근거 섹션 구조화, 심각도별 4단계 색상, v2.44 — "🔁 반복 패턴 — N건째 발생 (M건마다 감지·K번째 감지)" 한 줄 + 해결방안 추가, v2.47~v2.48 — 자동 전화는 없음, 온콜 담당자명만 멘션). `ops_email_analysis`/`ops_email_poll_cycle`은 30일 고정 보관정책으로 자동 정리됨.

**Graph API 토큰 조달 우선순위(v2.41)**: `pipeline.run_manual_collection()`이 ① 호출부가 넘긴 `access_token` → ② Application 권한 자격증명(`graph-credentials`) → ③ Delegated 로그인 세션(`delegated_auth`, 본인 메일함 한정) 순으로 시도. 셋 다 없으면 메일함별로 "자격증명 없음" 에러만 기록하고 다른 메일함은 계속 처리(전체 실패 처리 안 함). 스케줄러는 namespace 순회 전 credentials/access_token을 한 번만 해석해 `skip_credential_resolution=True`로 넘겨 매 namespace마다 재시도하지 않음. 로컬 검증은 `backend/scripts/email_voc_local_test.py`(Device Code Flow로 본인 메일함 대상 전체 파이프라인 실행) 참고.

상세 설계·의사결정 배경은 `docs/email-analysis-channel-plan.md` 참조.

### MCP 도구

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `GET` | `/api/mcp-tools` | 네임스페이스 MCP 도구 목록 |
| `POST` | `/api/mcp-tools` | 도구 등록 |
| `PATCH` | `/api/mcp-tools/{id}` | 도구 수정 |
| `PATCH` | `/api/mcp-tools/{id}/toggle` | 도구 활성/비활성 토글 |
| `DELETE` | `/api/mcp-tools/{id}` | 도구 삭제 |
| `POST` | `/api/mcp-tools/{id}/test` | 도구 테스트 실행 |
| `POST` | `/api/mcp-tools/autocomplete` | 자연어 입력 → 도구 JSON 자동완성 |
| `GET` | `/api/mcp-tools/logs` | 도구 호출 감사 로그 조회 |

---

## LLM Provider 확장 구조

```python
# domain/llm/base.py
def build_messages(context, question, history=None) -> list[dict]:
    # [system: 시스템프롬프트+참고문서] + [history...] + [user: 질문]

class LLMProvider(ABC):
    async def generate(context, question, history=None, api_key=None) -> str: ...
    async def generate_stream(context, question, history=None, api_key=None) -> AsyncIterator[str]: ...
    async def health_check() -> bool: ...

# 현재 구현체
OllamaProvider     # LLM_PROVIDER=ollama — /api/chat (messages 배열, multi-turn)
InHouseLLMProvider # LLM_PROVIDER=inhouse — DevX MCP API (usecase_code, query, response_mode)
                   #   inputs.model로 모델 선택 (GPT 5.2 / Claude Sonnet 4.5 / Gemini 3.0 Pro)
                   #   SSE: data JSON 안에 event 필드 포함하는 비표준 형식 대응
                   #   api_key: per-user 키 우선, 없으면 시스템 기본 키 사용
```

**`api_key` 파라미터 (v2.0.0 신규):**
- `generate()`, `generate_stream()`에 선택적 `api_key` 파라미터 추가
- 사용자가 개인 LLM API Key를 등록한 경우, Fernet 복호화 후 이 파라미터로 전달
- 개인 키가 없으면 `None` → Provider가 시스템 기본 키(`INHOUSE_LLM_API_KEY`) 사용
- OllamaProvider는 `api_key` 무시 (로컬 모델이므로 불필요)

**대화 맥락 전달 방식 (ConversationSummaryBuffer + Semantic Recall):**
```
messages = [
  {"role": "system",    "content": "시스템 프롬프트\n\n[참고 문서]\n{검색 결과}"},
  {"role": "system",    "content": "이 대화의 관련 과거 맥락:\n[과거 맥락 1]\n{요약}"},  ← Semantic Recall (유사도 0.45 이상)
  {"role": "user",      "content": "이전 질문"},   ← 최근 2회 raw 교환
  {"role": "assistant", "content": "이전 답변"},
  {"role": "user",      "content": "현재 질문"},
]
```

**메모리 동작 원리:**
- 4회 교환마다 오래된 대화를 LLM으로 요약 → `ops_conv_summary`에 임베딩 저장 (백그라운드)
- 새 질문 시 현재 질문 벡터로 과거 요약 검색 → 유사도 0.45 이상 최대 2개 추출
- 최근 2회 raw 교환은 항상 포함 (working memory)

**런타임 전환 (재시작 없음):**
- Admin → LLM 설정 탭에서 프로바이더 선택/설정 후 "저장 및 적용"
- `switch_provider(config)` 호출 → 싱글톤 교체, `_runtime_config` 전역 저장
- 컨테이너 재시작 시 `.env` 설정으로 복귀
- `get_runtime_config()`: 런타임 override 여부(`is_runtime_override`), 연결 상태(`is_connected`) 포함 반환

새 LLM 추가 시: `LLMProvider` 상속 → 3개 메서드 구현 → `service/llm/factory.py`에 등록

---

## 환경변수 설정

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `DATABASE_URL` | `postgresql://ops:ops1234@postgres:5432/opsdb` | DB 연결 문자열 |
| `LLM_PROVIDER` | `inhouse` | `ollama` 또는 `inhouse` |
| `OLLAMA_BASE_URL` | `http://host.docker.internal:11434` | Ollama 서버 주소 |
| `OLLAMA_MODEL` | `exaone3.5:7.8b` | 사용할 Ollama 모델명 |
| `OLLAMA_TIMEOUT` | `900` | CPU 추론 최대 대기 시간(초), httpx read timeout에 적용 |
| `INHOUSE_LLM_URL` | (없음) | DevX MCP API 엔드포인트 URL |
| `INHOUSE_LLM_API_KEY` | (없음) | 사내 LLM 시스템 기본 API 키 (Bearer 토큰) |
| `INHOUSE_LLM_MODEL` | (없음) | inputs.model 파라미터 (gpt-5.2, claude-sonnet-4.5, gemini-3.0-pro) |
| `INHOUSE_LLM_AGENT_CODE` | `playground` | DevX usecase_code |
| `INHOUSE_LLM_RESPONSE_MODE` | `streaming` | 응답 방식 (`streaming` \| `blocking`) |
| `EMBEDDING_MODEL` | `paraphrase-multilingual-mpnet-base-v2` | 임베딩 모델명 |
| `VECTOR_DIM` | `768` | 벡터 차원 수 |
| `DEFAULT_TOP_K` | `5` | 기본 검색 결과 수 |
| `DEFAULT_W_VECTOR` | `0.7` | 기본 벡터 검색 비중 |
| `DEFAULT_W_KEYWORD` | `0.3` | 기본 키워드 검색 비중 |
| `BACKEND_URL` | `http://backend:8000` | Frontend → Backend 주소 |
| `JWT_SECRET_KEY` | (필수) | JWT 서명 비밀 키 (HS256) |
| `FERNET_SECRET_KEY` | (필수) | Fernet 대칭 암호화 키 (사용자 API Key 암호화용) |
| `ADMIN_DEFAULT_PASSWORD` | (필수) | 초기 admin 계정 비밀번호 |

---

## pgvector 데이터 구성

### 벡터가 쓰이는 테이블

| 테이블 | 벡터 컬럼 | 용도 |
|--------|----------|------|
| `rag_knowledge` | `embedding VECTOR(768)` | 문서 내용 임베딩 → 질문과 코사인 유사도로 관련 문서 검색 |
| `rag_glossary` | `embedding VECTOR(768)` | 용어 설명 임베딩 → 질문과 비교해 표준 용어 자동 매핑 (유사도 0.5 이상만 사용) |
| `rag_fewshot` | `embedding VECTOR(768)` | 과거 질문 임베딩 → 유사 Q&A를 LLM 프롬프트에 few-shot 삽입 (유사도 0.6 이상) |
| `rag_conv_summary` | `embedding VECTOR(768)` | 과거 대화 요약 임베딩 → 현재 질문과 유사한 과거 맥락 Semantic Recall (유사도 0.45 이상) |

### 검색 점수 공식

```
final_score = (w_vec × v_score + w_kw × k_score) × (1 + base_weight)
               └벡터 유사도    └BM25 키워드 점수    └문서 자체 가중치
```

- **v_score**: 코사인 유사도 (0~1) — HNSW 인덱스로 근사 탐색
- **k_score**: `ts_rank` BM25 점수 — GIN 인덱스로 전문 검색
- **base_weight**: `ops_knowledge` 행(문서)에 직접 붙는 가중치. 👍 피드백 시 +0.1, 👎 시 -0.1 자동 조정

### 비벡터 주요 테이블

`ops_part`, `ops_user`, `ops_namespace`, `rag_knowledge_category`, `ops_feedback`, `ops_query_log`, `ops_conversation`, `ops_message`, `ops_mcp_tool`, `ops_mcp_tool_log`, `ops_prompt`, `ops_system_config`

전체 스키마 정의는 `docs/table-definition.md` 참조.
