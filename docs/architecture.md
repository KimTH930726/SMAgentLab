# Ops-Navigator 시스템 아키텍처 (v2.99)

## 개요

Ops-Navigator는 IT 운영팀의 반복적인 조회·확인 업무를 자동화하는 **지능형 운영 보조 에이전트 플랫폼**이다.
사용자는 에이전트를 선택해 목적에 맞는 AI를 사용한다: 지식 기반 Q&A(KnowledgeRAG).

> Text-to-SQL 에이전트는 v2.51에서 `dev_0`/`main`에서 분리·제거됐다(현재 과업 범위 아님) — 코드는
> `archive/with-text2sql` 브랜치(2026-09-03 시점 스냅샷)에 형상관리용으로 보존돼 있다.
> MCP 도구 에이전트는 v2.67에서 완전 제거됐다(Text2SQL과 달리 브랜치 보존 없음 — 배경은 아래
> v2.67 항목 참고).

**주요 이력 요약** (스키마 변경 상세는 `table-definition.md` §20 마이그레이션 이력 참조)
- v2.99: **청킹 규칙 확정분 반영 + 조건/예외 연결 표현 규칙(둘 다 결정론적, LLM 미사용).**
  `confluence-chunking-spec.md` §3이 실측(89문항 유사도 비교)으로 확정해뒀지만 코드
  반영이 안 돼 있던 규칙("섹션 ≥2개면 무조건 섹션당 1청크로 분리, 합쳐도 max_chars
  이하라고 병합하지 않음")을 `_chunk_by_sections()`에 `always_split` 인자로 추가.
  스펙 문서의 경고대로 이 함수는 confluence 전용이 아니라 txt/md/pdf 등도 공유하므로,
  실측된 소스 타입(confluence/web)에만 한정 적용 — 다른 소스 타입은 기존 동작(병합)
  그대로. 동시에 사용자 제안으로 "단/다만/예외/주의로 시작하는 문단은 직전 내용과
  분리하면 안 된다"는 규칙 추가(`_CONTINUATION_RE`) — 기존 `SHORT_INTRO_CHARS`
  메커니즘(2026-09-17, 실사고 대응)의 일반화이자, 오늘 발견한 B2C 정책("1. 자사몰만
  적용"이 뒤 항목들과 분리되던 사례)과 같은 계열의 문제를 청킹 단계에서부터 예방.
  이 연결 규칙은 `always_split=True`여도 예외 없이 항상 직전 버퍼에 강제 병합.
  회귀 테스트 3건 추가(`TestAlwaysSplitAndContinuation`) — always_split on/off 차이,
  연결 표현 강제 병합, 문장 중간에 등장하는 "단"/"주의" 오탐 방지까지 확인.
  **실 수치 검증(`scripts/verify_always_split_impact.py`)**: confluence-chunking-
  spec.md §3의 수동 시뮬레이션(과거 수치)과 별개로, 오늘 실제 구현한 `always_split`
  코드를 id=68 실 문서 + 실 활성 지식 39건(distractor) + 실 임베딩 모델로 재측정.
  4문항 중 2개가 순위 2위→1위로 상승, 나머지도 전부 유사도 점수 상승(0.4883→0.5536,
  0.5378→0.6126, 0.4711→0.5490, 0.4063→0.4506) — 이슈번호 질문만 6위→5위로
  top_k=5 경계에 걸쳐 완전 해결은 아님. 문서 1건·질문 4개짜리 소규모 실측이라 정식
  골든셋 검증은 아니며, 연결 표현 규칙(단/다만/예외/주의)은 이 문서에 해당 사례가
  없어 유닛테스트로만 검증됨(실 데이터 수치 미확인).
- v2.98: **Parent-Child 일반 메커니즘 (rag_knowledge, 컨플루언스 전용) — 결정론적,
  LLM 미사용.** v2.97의 응급 패치(2건 수동 라벨링)에 이어 일반 해법 구현. 핵심 통찰:
  `web_crawler.py`의 `_extract_heading_sections()`가 h1~h4 레벨을 이미 추출해두고
  있었는데 청킹 단계(`chunker.py`)에서 그 레벨 정보가 버려지고 있었다 — 새 LLM 호출
  없이 이미 있는 구조 정보를 조상 스택으로 추적하기만 하면 됐다(`data-storage-
  philosophy.md` §3 "구조는 결정론 파서로" 원칙 그대로). 구현: `Chunk.heading_path`
  필드 신설, `_chunk_by_sections()`가 섹션 레벨 기반 조상 스택(같은/얕은 레벨 등장
  시 pop)을 유지하며 각 청크에 자기 조상 헤딩 목록을 부여. `rag_knowledge.heading_path
  TEXT[]` 컬럼 추가, 컨플루언스 벌크 수집 경로(`router.py`→`service.py`)에 배선,
  검색 결과 조립(`retrieval.py`의 `build_context()`, `agent.py`의 `_build_rrf_context()`)
  에 "상위 맥락: ..."으로 포함(정책의 category_path와 동일 패턴). 회귀 테스트 2건
  추가(`test_chunker.py::TestHeadingPath`) — 실사고를 그대로 재현(동일 하위 섹션
  번호가 서로 다른 상위 채널 밑에 반복되는 구조)해 조상 경로로 정확히 구분되는 것,
  형제 섹션이 조상으로 새지 않는 것 확인. 기존 25건(외부서비스 14 + 딜리버스 11)엔
  `scripts/backfill_heading_path.py`로 소급 적용(원문에 남아있는 "## 1.2.1 제목"
  텍스트의 점(.) 개수를 깊이로 근사) — 적용 중 경계 오류 2건 발견(서로 무관한 원본
  페이지가 한 벌크 파일에 이어붙어 있어 헤딩이 다음 페이지로 잘못 전파, id=12865/
  12867) 즉시 정정(heading_path NULL로 원복). **한계**: v2.97에서 수동 패치한
  id=12857/12862(배달의민족·쿠팡이츠 "시점" 섹션)는 원본에 실제 h1 경계가 없어서
  이 소급 채우기로도 재현 불가 — 그 2건은 여전히 수동 라벨이 유일한 해법. 실 E2E로
  확인: "배차완료 상태를 수신하면 어떻게 되나요" 질의 시 실제 검색 결과에 "상위 맥락:
  [채널: 배달의민족]"이 정확히 포함됨.
- v2.97: **Parent-Child 문맥 유실 수정 (사용자 반박으로 발견한 실사고)** — 직전 WBS
  (`docs/tech/rag-improvement-wbs.md`)에서 "Parent-Child 확장은 데이터 축적형(나중)"
  이라고 판단했는데, 사용자가 "딜리버스/온라인스토어 데이터가 다 산문이고 컨플루언스가
  앞으로 메인 영역"이라고 반박해 실측한 결과 판단이 틀렸음이 확인됨.
  ① **정책(`policy_item`)**: 378건 중 125건(33%)이 이미 여러 `policy_chunk`로 쪼개져
  있고, 실측 사례(`id=56` "B2C")에서 "1. 자사몰만 적용"이라는 적용범위 조건이 별도
  청크로 분리돼, "각인옵션이 제공된다"만 매칭되면 그 조건 없이 LLM에 전달되는 게
  확인됨. 원문 전체(`raw_body`)가 검색 시점에 이미 fetch되고 있었지만 `build_policy_
  context()`/`_build_rrf_context()` 둘 다 `chunk_text`(쪼개진 조각)만 쓰고 있었던 것 —
  `raw_body`로 전환(설계 불필요, 기존 fetch 재사용). 실측 재확인: "각인 옵션 되나요"
  질의 시 이제 "1. 자사몰만 적용"부터 5개 항목 전체가 컨텍스트에 포함됨.
  ② **컨플루언스(`rag_knowledge`)**: "아직 본격 수집 안 됨"이라는 전제 자체가 틀림 —
  `외부서비스DB`에 confluence_bulk 14건이 이미 있고, 그중 2건(id=12857/12862, "배달의
  민족 주문 검증" vs "쿠팡이츠 주문 검증" 섹션)이 **완전히 동일한 텍스트**이면서 어느
  채널 얘기인지 구분할 정보가 청크 자체엔 전혀 없음을 확인(정책 사례보다 심각 — 19
  페이지가 뭉친 대량 청크라 policy_item처럼 "원문 전체" 개념이 없어 즉시 전면 해결은
  불가). 지금 규모(14건)에서 완전히 모호한 이 2건만 채널명 라벨(`[채널: 배달의민족]`
  / `[채널: 쿠팡이츠]`)을 앞에 붙여 재임베딩하는 응급 조치로 완화 — 일반 메커니즘(헤딩
  계층 저장·조회)은 `confluence-chunking-spec.md`와 병합해 별도 설계(WBS 2-7).
  **교훈**: "실 사례 없다"는 판단은 실제로 데이터를 보고 내려야 한다 — 이번엔 그
  판단을 데이터 확인 없이 내렸다가 사용자가 직접 반박해 실측으로 정정됨.
- v2.96: **RAG 거버넌스 감사 v2 후속 — 레드팀 검토 통과분만 적용.** "수집 원자적
  활성화"는 감사 직후 Quick Win으로 분류했다가 레드팀 재검토 결과 제외 — 대량 job을
  단일 트랜잭션으로 묶으면 임베딩에 걸리는 몇 분 동안 락 경합/타임아웃 위험이 있어,
  2단계(스테이징 삽입 후 일괄 전환) 설계가 필요한 별도 과제로 재분류.
  ① **수집 작업 고아 정리** — `main.py`에 `_cleanup_orphaned_ingestion_jobs()` 신설,
  `_cleanup_stale_generating_messages()`와 동일 논리(프로세스 막 기동 = 이 프로세스가
  만들었을 리 없는 'processing' job은 전부 이전 프로세스가 죽으며 남긴 고아)로 시작
  시 `failed` 처리. 전제: 백엔드 단일 인스턴스(수평 확장 시 재검토 필요, 주석에 명시).
  실제 고아 job을 만들어 재시작 후 `failed` + 에러 메시지로 정정됨을 DB로 직접 확인.
  ② **지식 중복 검토 시 `reviewed_at`/`owner` 실제 기록** — `resolve_duplicate()`에
  `reviewer_username` 인자 추가, approve/reject/merge 3개 분기 모두 스탬프. 이전엔
  두 컬럼이 스키마에만 있고 어디서도 안 쓰여 "누가 언제 승인/반려/병합했는지" 감사
  추적이 불가능했음(권한 분리 자체를 새로 만든 건 아님 — 같은 네임스페이스 쓰기
  권한자면 여전히 누구나 처리 가능, 기록만 남김). 실 E2E로 확인 — pending_review
  테스트 행을 실제 `/api/knowledge/{id}/resolve` API로 승인 처리 후 `reviewed_at`/
  `owner='admin'`이 정확히 기록됨을 DB로 확인.
  ③ **부분 색인 노출 현상 회귀 테스트화** — `tests/test_ingestion.py`에
  `test_batches_commit_independently_before_job_completes` 추가. 배치 크기를 1로
  강제해 `rag_knowledge` INSERT가 배치마다 독립적으로 커밋되고 각 행이 즉시
  `status='active'`가 됨을 캡처 — 동작을 고치는 게 아니라 지금 동작을 기준선으로
  남겨서, 나중에 원자적 활성화를 구현할 때 회귀 여부를 이 테스트로 확인할 수 있게 함.
  상세 진단: `docs/tech/rag-governance-audit.md`.
- v2.95: **RAG 거버넌스 감사 후속 — "무조건 이득" 4개 항목 적용** (레드팀 검토 후 우선순위
  재조정, `docs/tech/rag-governance-audit.md` 참고).
  ① **정책 컨텍스트에 `category_path` 포함**(`service/policy/search.py`의
  `build_policy_context()`, `agent.py`의 `_build_rrf_context()`) — DB엔 있었지만 LLM이
  실제로 보는 텍스트에서는 빠져있던 분류 정보를 추가. 실측 확인: "최소 주문금액"이
  배달의민족(7,000원)/온라인주문결제(15,000원)/땡겨요(12,000원)로 실제로 갈리는
  정책인데, 수정 전엔 이 셋이 구분 없이 뒤섞여 LLM에 전달됐음 — `[분류: 1.배달의민족 >
  주문 > 주문 수량/금액 제한]` 식으로 태깅되도록 수정.
  ② **임베딩 모델 메타데이터 하드코딩 버그 발견·수정** — `knowledge/service.py`의
  `_EMBEDDING_MODEL_NAME`이 v2.72 임베딩 모델 교체(mpnet→KURE-v1) 이후에도 옛 모델명
  문자열로 하드코딩된 채 남아있어, 그 이후 등록된 활성 지식 18건이 실제로는 KURE-v1
  (1024차원)로 임베딩됐으면서도 metadata엔 옛 모델명이 찍히는 조용한 오염이 있었음
  (`vector_dims(embedding)`으로 실제 차원 대조해 확인, 검색 동작 자체엔 영향 없었음 —
  메타데이터만 어긋남). 하드코딩 대신 `core/config.py`의 `settings.embedding_model`을
  그대로 참조하도록 수정 + 기존 18건 일괄 정정(1회성). 재발 방지로 `lifespan()` 시작
  시 활성 지식 중 설정과 다른 `embedding_model`이 있으면 경고 로그(`_warn_if_embedding_
  model_stale`) — 실제로 불일치 1건을 주입해 경고가 정확히 뜨는 것까지 확인 후 원복.
  ③ **읽기 시점 네임스페이스 접근 로그(강제 아님)** — 감사에서 발견된 P0(채팅/검색
  경로에 파트 기반 접근 통제가 전혀 없음, 쓰기 경로에만 있었음)에 대해, 곧바로 차단부터
  하지 않고 `core/dependencies.py`에 `log_cross_part_namespace_read()` 신설 —
  `/api/chat`, `/api/chat/stream`, `/api/chat/debug` 3곳에서 호출, 파트가 다른 네임스페이스
  조회 시 차단 없이 경고 로그만 남김(`_KEYWORD_ONLY_CATEGORIES` 때와 동일하게, 강제
  전환 전에 실제 사용 패턴부터 실측). 실 E2E로 확인: 임시로 네임스페이스 소유 파트를
  지정하고 다른 파트 사용자로 조회 → 200 정상 응답 + 정확한 경고 로그 확인, 이후 원복.
  ④ **ABSTAIN 판정 임계값 오프라인 파일럿**(`backend/scripts/abstain_pilot.py`, 프로덕션
  미반영) — 골든셋 89문항(전부 실제 정답 존재) 대상으로 후보 임계값별 false-abstain
  비율을 실측: 0.4(기존 "보통" 신뢰도 라벨과 동일 기준) = **0% false abstain**, 0.6
  ("높음" 기준) = 3.4%(3건, 전부 "~에만 따로 적용되는 규정" 유형 질문) false abstain.
  향후 ABSTAIN 계층을 실제로 만들 때 0.4를 1차 후보로 쓸 근거 확보 — 단, 이 골든셋엔
  "진짜 답이 없는" 사례가 없어 true-abstain 정확도는 이 파일럿으로 측정 불가(별도 과제).
  **하지 않기로 한 것**: `rag_knowledge.supersedes_id` 연결은 착수 전 재검토 결과 제외 —
  컨플루언스 재수집이 페이지 단위로 구버전 N개를 배치 deprecate하고 신버전 M개를 별도
  삽입하는 구조라(N≠M 가능) 단일값 FK로 1:1 계보를 만들 자연스러운 방법이 없고, 억지로
  매칭 규칙을 만들면 오히려 잘못된 계보 정보가 남을 위험 — 검색 정확성은 이미
  `status='deprecated'`로 문제없이 보장되고 있어 이건 조사 설계가 더 필요한 항목으로
  재분류(B축, 무조건 이득 아님).
- v2.94: **보존정책 — `ops_conversation`/`ops_message` 나이 기반 삭제 스캐폴딩(기본 비활성)**
  — 거버넌스 선제 설계 검토(`docs/tech/governance-design-review.md` §3-3). 기존엔 이 두
  테이블에 시간 기준 보존정책이 전혀 없었고(§2-3), `cleanup_old_messages()`의
  네임스페이스당 100건 캡만 있었음. `ops_system_config`에 `chat_retention_days` 키
  추가(기본값 `'0'` = 비활성 — 보존일수는 거버넌스팀이 아직 확정 안 해서 임의로 실
  대화를 지우지 않도록 안전한 기본값 선택). `cleanup_old_conversations()`
  (`service/chat/helpers.py`)가 이 값이 0보다 클 때만, "마지막 메시지 기준으로 N일간
  활동 없는 대화"를 삭제(대화 생성일이 아니라 최근 활동 기준 — 오래전 시작했지만 계속
  쓰는 대화가 삭제되는 걸 방지). `ops_message`는 `ON DELETE CASCADE`로 함께 삭제.
  기존 `post_save_tasks()`의 확률적 하우스키핑 트리거(`CLEANUP_SAMPLE_RATE`)에 그대로
  얹음(전용 스케줄러 신규 도입 없음 — VOC 스케줄러는 외부 API 폴링용이라 이 문제와
  성격이 다름, 기존 같은 도메인의 `cleanup_resolved_query_logs()`와 동일 패턴 재사용).
  실 E2E로 검증 — 비활성(0) 상태에서 10일 지난 대화가 그대로 남는 것 확인, 이후
  7일로 활성화하고 재실행해 "10일간 활동 없는 대화"는 메시지까지 cascade 삭제,
  "생성은 오래됐지만 1시간 전 메시지가 있는 대화"는 보존되는 것 확인 후 다시
  비활성(0)으로 복구. **실제 보존일수는 거버넌스팀 확정 전까지 `'0'`으로 유지** —
  숫자가 정해지면 `UPDATE ops_system_config SET value = '<일수>' WHERE key =
  'chat_retention_days'`로 활성화(전용 관리 UI는 아직 없음, 최소 스캐폴딩 범위).
- v2.93: **접근권한 세분화 — `viewer` 역할 최소 스캐폴딩** — 거버넌스 선제 설계 검토
  (`docs/tech/governance-design-review.md` §3-2 A안). 기존엔 역할이 `admin`/`user`
  2단계뿐이고 쓰기 권한은 오직 파트 일치 여부로만 갈렸음(같은 파트면 전원 쓰기 가능,
  `owner_part_id`가 NULL인 공통 네임스페이스는 로그인만 하면 전원 쓰기 가능) — 파트
  내부에서 "이 사람은 조회만" 같은 세분화가 불가능했음. `core/dependencies.py`의
  `check_part_ownership()`/`check_namespace_ownership()`에 `role == 'viewer'`면 파트
  일치 여부와 무관하게 항상 403을 반환하는 분기를 admin 체크 다음으로 추가(스키마 변경
  없음 — `ops_user.role`은 원래 제약 없는 VARCHAR). 프론트 `useNamespaceAccess`도 동일
  규칙 반영. `UserManager.tsx`는 아직 viewer를 지정하는 전용 UI가 없어(의도적 — 최소
  스캐폴딩 범위, 실 수요 생기면 추가) 기존 admin/user 토글 버튼이 실수로 viewer를
  관리자로 승격시키지 않도록 viewer 행만 비활성 배지로 별도 표시. 실 E2E로 검증 —
  임시 사용자를 `owner_part_id IS NULL`인 공통 네임스페이스 소속으로 만들고
  `role='viewer'`로 설정한 뒤 실제 로그인 토큰으로 PATCH(네임스페이스 rename) 시도 →
  403 확인, 같은 토큰으로 GET(네임스페이스 목록) → 200 확인.
- v2.92: **질의응답 감사로그 — `ops_query_log.user_id` 추가** — 거버넌스 선제 설계 검토
  (`docs/tech/governance-design-review.md` §3-1 A안)의 첫 단계. 기존엔 `ops_query_log`에
  `user_id`가 없어 "누가 언제 뭘 물었고 뭘 근거로 답했는지"를 재구성할 조인 경로 자체가
  끊겨 있었음. `create_query_log()`에 `user_id` 인자 추가, 호출부 3곳(`agent.py`의
  시맨틱 캐시 히트/일반 스트리밍 경로, `router.py`의 비스트리밍 `/api/chat`) 모두 이미
  스코프에 있던 인증 사용자(`user["id"]`)를 그대로 전달하도록 연결. 기존 행은 당시
  누가 물었는지 알 길이 없어 `ops_conversation.user_id`처럼 admin으로 소급 귀속하지
  않고 NULL로 남김(감사로그를 사실과 다르게 채우지 않음). 실 E2E로 비스트리밍/스트리밍
  두 경로 모두 검증 — 실제 로그인 사용자(admin, id=1)로 질문 전송 후 해당
  `ops_query_log` 행의 `user_id`가 정확히 1로 기록됨을 확인.
- v2.91: **시맨틱 캐시 키 오염 버그 수정 (실사고)** — 사용자가 실제 대화에서 발견:
  같은 대화에서 "쿠폰 회수 정책 알려줘" 다음에 전혀 무관한 "정책적으로 최대 재고
  갯수가 몇개?"를 물었는데, 완전히 무관한 이전 답(쿠폰 회수 정책)이 그대로 재사용됨.
  원인 — `memory.augment_query_for_search()`(멀티턴 검색 보강)가 직전 턴과의 관련성을
  판단하는 `MULTITURN_RELEVANCE_THRESHOLD`(0.35)가 너무 낮아 "정책"이라는 단어 하나
  겹치는 것만으로 완전히 다른 주제를 "이어지는 질문"으로 오판, 직전 턴 전체를 검색
  질의에 붙였음(`search_question`). `agent.py`가 **캐시 벡터를 이 오염된 병합
  텍스트로 계산**하고 있었던 게 진짜 문제 — 실측: 원본 두 질문의 순수 임베딩 유사도는
  0.51(무관 판정 안전권)인데, 병합된 캐시 벡터끼리는 0.9309로 나와 캐시 임계치
  (0.88~0.92)를 넘어 오답이 재사용됨. 수정: 캐시 키는 항상 순수 원본 질문(`query`)
  만으로 계산하도록 분리 — 멀티턴 보강은 검색 재현율에는 여전히 도움이 되므로
  `search_question`은 검색에만 계속 사용. 실제 대화(conversation_id=472)로 재현·
  수정 후 재검증 완료(유사도 0.9309→0.51로 임계치 미달 확인).
- v2.90: **`_KEYWORD_ONLY_CATEGORIES` 재발 방지 가드 + 하드코딩 중복 제거** — v2.87~
  v2.89 사고 체인의 근본 메커니즘(`_KEYWORD_ONLY_CATEGORIES`) 자체는 그대로 남아있어,
  누군가 새 지식을 "DB"/"공통코드"로 다시 태깅하면 같은 top_k 배제 문제가 조용히
  재현될 수 있음(완전 제거는 이 카테고리를 만든 원래 이유 — 구조화 덤프의 벡터 오탐 —
  를 다시 열게 돼 하지 않음, 지금은 실 인스턴스 0건으로 확인됨). ① `service.py`의
  `_require_category()`에 경고 로그 추가 — 등록 자체는 막지 않되(기존 저장 경로
  보존) 재발 시 로그로 알 수 있게. 관리자 화면(`RequiredCategoryField`, 8개 폼에서
  공용)에도 이 카테고리 선택 시 "참조데이터 등록을 권장" 안내 문구 추가. ②
  하드코딩 중복 제거 — `retrieval._KEYWORD_ONLY_CATEGORIES`(백엔드)와
  `UnifiedAdhocSearch.tsx`의 별도 배열(프론트)이 같은 값을 각자 유지하던 걸 신규
  `GET /api/knowledge/keyword-only-categories`로 단일 소스화. 테스트 4건 추가.
- v2.87~v2.89: **채택 게이트 정합성 수정 + 참조데이터 축 신설** — 평가 게이트에 "실제
  chat 프롬프트 포함 여부(채택/제외)" 배지를 달면서(v2.85) 발견한 실사고 3단 체인.
  ① (v2.87) `knowledge_min_score`(0.35)가 사실상 무력화돼 있었음 — 실측: "오늘 날씨
  어때?"처럼 완전 무관한 질문도 후보 20/20건이 채택 통과. 원인은 `final_score =
  결합점수 × (1+base_weight)`인데 신규 지식 base_weight 기본값이 1.0이라 결합점수가
  항상 2배가 됨. `retrieval.relevance_score()`(base_weight를 나눠 걷어낸 원점수)를
  게이트/신뢰도 라벨에 사용하도록 전환. ② 이 수정 직후 "DS14가 뭐야?"(공통코드
  카테고리 id=20의 자기 매칭 대상 질문)가 여전히 0건 채택으로 확인 — `to_tsvector
  ('simple', ...)`엔 한국어 형태소 분석이 없어 질의의 "DS14가"(조사 융합, 'ds14가')와
  본문의 깔끔한 "DS14"('ds14')가 매칭 안 됨. 이미 정책 검색에 있던 `policy_strip_ko()`
  (v2.71)를 `retrieval.py` 일반지식 키워드 검색에도 처음 적용 — 동시에 이 함수가
  `init/06-policy-strip-ko.sql`(빈 pgdata에서만 자동실행)에만 있어 기존 배포엔 없을
  수 있던 걸 재시작마다 실행되는 멱등 마이그레이션(`_migrate_ensure_ko_text_search_
  helpers`)으로 보장. **이 함수 수정판을 배포하며 plpgsql 예약어(`trailing`)를 변수명
  으로 써 실제로 백엔드가 몇 차례 크래시 루프에 빠짐 — 즉시 `tail_punct`로 변경해
  복구(실사고, 재발 방지 위해 기록)**. ③ (v2.88) 조사 제거 후에도 "DS14가 뭐야?"가
  여전히 답 안 됨 — id=20의 final_score(ts_rank 기반, 0.03~0.09대)가 벡터 스코어 문서
  (0.4~0.6대)와 같은 `ORDER BY final_score DESC LIMIT top_k`로 경쟁하다 보니, 프로덕션
  기본 top_k=5 후보 풀에 애초에 못 들어감(27건 중 27등 실측). RRF가 풀었던 것과 정확히
  같은 스케일 불일치가 단일 축 SQL 안에서 재현된 것. `is_adopted()` 신설(키워드 전용
  카테고리는 매칭 여부로만 이진 판정)로도 이 문제(후보 풀 진입 자체가 안 됨)는 못 풀어,
  ④ (v2.89) `rag_knowledge` 안에서 SQL을 더 복잡하게 만드는 대신 원래 이 목적으로
  스키마만 만들어져 있던(v2.73) `ref_common_code`/`ref_db_column`으로 id=20을 완전히
  이전(`scripts/migrate_id20_to_refdata.py`, 252건)하고 `agent.py`의 `_build_rrf_
  context()`에 네 번째 RRF 축으로 신규 연결(`service/refdata/service.py`의 검색
  함수는 v2.73에 구현만 되고 chat에서 호출 안 되고 있었음 — 순수 배선 문제였음이 드러남).
  CMDB 데이터가 앞으로 같은 성격(정확 조회용 RDB)이라 이 문제가 반복될 것으로 예상돼
  처음부터 독립 축으로 분리(장기적으로 벡터축/키워드축을 나누는 방향 자체는 Track2
  실측(하이브리드가 4개 유형 전부 우세)으로 이미 검증돼 있음 — 문제는 "나누는 것"이
  아니라 "원점수로 합치는 것"이었다는 게 이 체인 전체의 결론). 평가 게이트
  (UnifiedAdhocSearch)에도 신규 `GET /api/refdata/search`로 동일한 네 번째 축을
  연결(테일/카테고리 배지 추가) — "chat과 게이트가 똑같이 실행되고 투명하게 보여야
  한다"는 원칙을 이번 축까지 계속 맞춤. `service/refdata/service.py`의 검색 함수
  (v2.73 구현, 지금까지 어디서도 호출 안 됨)를 이번에 처음으로 HTTP 엔드포인트로
  노출한 것도 이 라우터가 유일.
- v2.86: **chat 프로덕션 파이프라인에 RRF 컨텍스트 병합 적용** — v2.85에서 즉석 질의
  화면에만 적용했던 Reciprocal Rank Fusion(RRF, k=60)을 실제 `agent.py`의 답변 생성
  경로에도 적용. 기존엔 `retrieval.build_context(results)` + `policy_search.
  build_policy_context(policy_result)`를 그냥 텍스트로 이어붙여(항상 "일반지식 먼저,
  정책 나중") 축마다 점수 스케일이 달라도(코사인 0~1 vs RDB ts_rank 0~5+) 순서에
  전혀 반영이 안 됐다. `agent.py`에 `_build_rrf_context()` 신규 — 일반지식/정책
  파라미터/정책 서술을 항목 단위로 RRF 순위 매겨 하나의 컨텍스트로 재구성. 공유
  함수(`build_context`/`build_policy_context`)는 디버그검색·이메일VOC 등 다른 화면이
  그대로 쓰고 있어 안 건드리고, agent.py 안에서만 새로 포맷(신규 `shared/rrf.py`).
  **정확도 재검증(2회)**: 실제 채팅 답변에 반영해도 손해가 없는지, 실 질의 로그
  기반 질문(`ops_query_log` resolved 10건 + 교차축 질문 3건)으로 OLD/NEW 컨텍스트 각각
  LLM에 태워 비교. 1차는 "답변이 같아 보인다"는 정성 판단이었고, 재검증(사용자 요청)은
  RRF 1위 항목 본문에서 숫자·코드성 "핵심 사실"을 자동 추출해 OLD/NEW 답변에 남아있는지
  정량 비교 — 10문항 중 7건 동일, 2건 NEW가 더 나음, 1건만 OLD가 근소 우위(사용자
  체감 정보 아닌 테이블명 토큰 1개 차이). 순손해 없음 확인 후 적용(`scripts/
  verify_rrf_chat_accuracy.py`, `scripts/compare_rrf_context.py`, 유닛테스트 6건).
- v2.85: **평가 게이트 "즉석 질의" 통합 재설계 + 지식 오염 관리** — "파이프라인
  디버그"(일반지식 단건 조회)와 "정책 저장소 실험실"(정책 골든셋 집계)을 하나의
  최상위 탭 "평가 게이트"로 통합(향후 측정지표 기반 루프 엔지니어링 게이팅을
  염두에 둔 선제적 명명, 지금은 여전히 수동 측정 도구일 뿐 — 실제 차단 로직 없음).
  즉석 질의는 "일반지식/정책 중 택1 토글" → "두 패널 동시 표시" → **하나의 RRF
  통합 리스트(`UnifiedAdhocSearch.tsx`)** 순으로 세 번 재설계됨(정책을 별도 패널로
  보여주면 "뭔가 대단한 별도 축"처럼 보인다는 지적). 이후 UI 피드백으로 카드+모달
  형태로 재구성 — 카드 상단에 상대 강도 막대(원점수 아닌 "이번 검색 1위 대비 RRF
  상대값"), 클릭 시 전체 내용/메타 모달, top_k는 "축별 후보 수"이자 "최종 표시
  개수"로 의미 통일(과거엔 축별 후보 수로만 써서 top_k=10이어도 최대 30개가 그대로
  나열됐음), 카드형 5열 그리드로 좌상단부터 순위대로 배치. **채택 여부 표시**:
  RRF 순위와 별개로 실제 chat 프롬프트 포함 여부를 배지로 노출 — 일반지식은
  `final_score < knowledge_min_score`면 순위와 무관하게 제외, 정책은 임계치 자체가
  없어 top_k 이내면 항상 채택(코드 확인 완료). **검색 오염 지식 리뷰 신호**: 딜리버스
  DB에서 무관한 질문에 공통코드 표(id=20)가 코사인 0.93~0.95로 1위 노출되는 실제
  사례 발견(카테고리를 `_KEYWORD_ONLY_CATEGORIES`에 속하는 "공통코드"로 재태깅해
  해결) — 같은 클래스 문제가 반복될 수 있어, 카드에서 바로 기존
  `rag_knowledge_review_flag` 큐(나빠요 피드백과 동일)에 `reason='search_noise'`로
  플래그하는 기능 추가(`POST /api/knowledge/{id}/flag-for-review`). 리뷰 신호 큐에는
  "확인 완료"만 있고 수정/삭제가 없다는 지적으로 인라인 "수정" 버튼도 추가(기존 편집
  모달 재사용). **인라인 수정**: 즉석 질의에서 검색된 일반지식을 그 자리에서
  수정할 수 있게(품질 관리 워크플로우) — 저장 후 점수·채택 여부가 바뀔 수 있어 같은
  질문으로 재검색해 최신 상태를 반영.
- v2.84: **fewshot 기능 전체 제거** — 후보 12건/활성 1건, 2개월째 방치 실측(2026-09-17).
  "유사도 기반 동적 예시 선택은 업계 표준 패턴"이라는 일반론으로 존치(승격 게이트
  수정, 카테고리 가산점 추가)를 검토했으나, "그 표준 패턴이 실제로 이 시스템에서
  효과가 있었다는 증거는 없다"로 정정 — 판단 기준은 업계 일반론이 아니라 "우리
  조직 구성상 실제 효과가 있었나"(`feedback_effectiveness_bar_for_org_specific_
  features` 메모리). `rag_fewshot` 테이블·`rag_ingestion_job.auto_fewshot` 컬럼·
  `agents/knowledge_rag/fewshot/` 라우터·`FewshotTable.tsx`·`qa_gen.py`·피드백→
  INSERT 로직까지 전부 삭제(비활성화가 아니라 완전 제거). 대체 수단은 v2.83의
  `ops_prompt_category_guide`.
- v2.83: **카테고리별 정적 답변 안내문 스키마 선추가** — fewshot의 "카테고리별 답변
  가이드" 역할을 승인 절차 없는 정적 텍스트로 대체할 `ops_prompt_category_guide`
  테이블(namespace_id, category, guide_text, `UNIQUE(namespace_id, category)`) 추가.
  **스키마만 — CRUD/UI는 미착수**(다음 단계).
- v2.82: **미사용 지식 필드(container_name/target_tables/query_template) 완전 제거** —
  2026-09-17 실사용 데이터 감사: 전체 50건 중 각각 25/13/2건만 채워짐, 게다가
  `retrieval.py`의 벡터/키워드 랭킹 어디에도 안 쓰이고 `build_context()`가 LLM
  컨텍스트에 장식으로 덧붙이는 용도뿐이었음("검색 정확도에 기여한다"는 원래 전제가
  틀림) — 본문 텍스트에 이미 같은 정보가 있어 FTS/벡터 검색이 어차피 커버. 컨플루언스
  등 지금 주력 채널엔 입력 UI 자체가 없어 0%였음. DB 컬럼·Python 스키마/라우터/서비스
  (~15개 백엔드 파일)·프론트 폼/배지/CSV매핑/자동태그 프롬프트(~15개 프론트 파일)에서
  전부 제거(YAGNI, 필요해지면 재추가).
- v2.81: **Confluence 페이지 버전 추적 도입** — 재임포트(`POST /import/url/bulk-pages`)가
  같은 URL을 돌릴 때마다 무조건 새 행을 만들던 문제 수정. Confluence REST API의
  `page.version.number`를 `rag_knowledge.confluence_version`에 저장해 재임포트 시
  비교 — 안 바뀐 페이지는 스킵(`unchanged_pages` 응답 필드), 바뀐 페이지는 기존 행을
  `deprecated` 처리 후 재삽입. 같은 커밋에서 Confluence 청킹 버그 2건도 별도 수정
  (매크로 잔재 텍스트 유출, 짧은 상위 섹션 소개글이 자식 서브섹션과 분리되며 유실).
- v2.80: **정책서 미분류(unresolved) 리포트에 첫 쓰기 액션 추가** — "조회만 있고 승인/
  수정/재분류 할 방법이 없다"는 실사용 지적(2026-09-16)으로 착수. 완전한 검토 UI
  (`docs/policy-doc-pipeline-plan.md` §6, 여전히 미착수)의 대체가 아니라 "완전 방치"를
  벗어나는 최소 액션 하나만 추가 — **"서술로 편입"**: unresolved segment 원문을 그대로
  `policy_chunk`에 넣어 최소한 벡터 검색은 되게 만든다(정밀한 param 필드 추출은 아님).
  신규 `unresolved_report.promote_segment_to_narrative()`, `POST /api/policy/unresolved/
  {item_id}/promote`. 편입 후 `policy_item.unresolved_segments`에서 해당 segment 제거,
  남은 게 없으면 `parse_status`를 `partial`→`parsed`로 전환(전부 없어졌으면 `parsed`,
  일부만 남으면 `partial` 유지). segment 식별은 배열 인덱스 기반이라, 프론트는 편입 성공
  때마다 목록을 다시 받아와 인덱스가 밀리는 문제를 피한다(로컬에서 배열을 직접 안 자름).
  테스트 5건 추가(경계값 포함).
- v2.79: **Track2 엔진 파라미터화** (work-os lab.md L2, 실험실 게이트 다음 순번) —
  `track2.py`의 `run_comparison()`이 golden_set_path/전략(A/B 검색함수)/정답id추출을
  전부 하드코딩하고 있던 걸 풀었다. `Strategy(name, search)` dataclass 신규(`search:
  (namespace, query, top_k) -> 결과`), `POLICY_STRATEGIES = [Strategy("A_unified",
  _search_a), Strategy("B_hybrid", _search_b)]`로 정책 A/B 등록. 골든셋 로드+정답id
  리졸브는 `_load_golden_set()`로 분리(정책 전용 포맷은 그대로 유지 — L6 경계: 포맷
  일반화·전략 자동추천은 범위 밖, 사례 1개로 프레임워크 먼저 안 만든다는 원칙 유지).
  배경: "축 #2 증명"을 억지 PoC로 만들지 않고, 팀 데이터전환(CMDB 등, 10월 착수 예정)이
  실제로 올 때 Strategy 등록+골든셋 파일 추가만으로 흡수하기 위한 선제 준비 — 채점
  로직(hit@K/precision@K/Top-1/채널기여도)은 무변경. **리팩토링 중 실제 버그 발견**:
  `run_comparison(golden_set_path=_GOLDEN_SET_PATH, ...)`처럼 모듈 상수를 기본 인자값
  으로 바로 쓰면 def 시점에 값이 고정돼, 테스트가 `monkeypatch.setattr(track2,
  "_GOLDEN_SET_PATH", ...)`로 바꿔치기해도 반영이 안 되는 문제로 테스트 4건이 깨짐 —
  None 센티널 + 함수 본문에서 모듈 전역 조회하는 패턴으로 수정. 검증: 실제 운영 DB로
  라이브 재실행해 리팩토링 전과 정확히 동일한 값 확인(`b_hit_rate=0.8764...`,
  `a_top1_accuracy=0.4719...` 등, 회귀 없음). 신규 테스트 4건(주입한 golden_set_path/
  strategies가 모듈 기본값이 아니라 실제로 쓰이는지, AXIS_REGISTRY 형태 검증) 추가.
  **가시성 후속**(사용자 요청 — 엔진만 바뀌고 화면은 그대로면 눈에 안 보임): `track2.py`에
  `AXIS_REGISTRY`/`AXIS_LABELS` 신규(지금은 `"policy"` 한 항목뿐), `GET /track2/axes`
  신규, `POST /track2/run`에 `axis` 쿼리파라미터 추가(모르는 axis는 400). `PolicyLab.tsx`에
  "데이터 축" 드롭다운 추가 — 지금은 선택지가 1개뿐이라 사실상 비활성(disabled, 툴팁으로
  안내)이지만, 두 번째 축이 `AXIS_REGISTRY`에 등록되는 순간 자동으로 선택 가능해진다(화면
  코드 변경 불필요).
- v2.78: **VOC 반복패턴 임계치 긴급 재상향** (`email_pattern_similarity_threshold`
  0.58 → **0.80**, `ops_system_config` 런타임 값, 코드 변경 없음) — v2.75에서 0.85→
  0.58로 낮춘 지 하루 만에 실제 오염 확인. 클러스터 175("쿠폰사용문의")에 "지갑을
  잃어버렸어요"/"매장파트너 업무처리 불만" 등 무관한 이메일이 섞여 들어가 이미 실제
  Teams 알림까지 발송됐고, 기존 클러스터 150("주소지 오배송")은 하루 만에 31건이
  붙으며 환불/분실물/오결제 등 사실상 아무 CS 메일이나 흡수하는 블랙홀 버킷이 됨.
  **정책 검색(hit@K)과 달리 VOC 클러스터링은 사람이 결과를 거르는 단계 없이 바로
  자동 액션(알림 발송·반복 카운트·커버리지 판정)으로 이어져서, 오탐이 recall 자체를
  무의미하게 만든다**(클러스터 구성을 못 믿으면 그 recall도 허수) — v2.75에서
  참고했던 `_COVERAGE_MIN_SIMILARITY`의 "오탐이 미탐보다 위험" 원칙이 이 임계치에도
  그대로 적용됨을 실증. 0.80 적용 후 어제 스윕 데이터 기준 recall 18.6%/FPR 2.9%로
  후퇴(0.58의 recall 71.2%/FPR 39.9% 대비 오염 위험 크게 낮춤). 이미 오염된 클러스터
  175/150은 정리하지 않음(추가 조치는 다음에 판단). 근본 해결(2단계 검증 — 코사인은
  후보 추출만, 최종 판단은 LLM)은 여전히 미착수, DevX 크레딧 문제는 같은 날 해결
  확인됨(별도 이슈).
- v2.77: **정책 지식 파이프라인 모니터** (work-os 전달 프롬프트 작업3, "핵심 산출물")
  — `PolicyPipelineMonitor.tsx` 신규(정책 탭 서브탭, `PolicyLab.tsx` 확장이 아니라 별도
  컴포넌트로 분리 — 실험 도구 하나가 아니라 파이프라인 전체 조망이라 성격이 다름).
  4개 섹션: ①기준정보 축적(`policy_item`/`param`/`chunk`/`ref_db_column`/`ref_common_code`
  건수, 신규 `GET /policy/pipeline-stats`), ②지식화 레이어 갭 이슈 6개 체크리스트
  (`data-storage-philosophy.md` §9 기준, 스키마 재조회로 실제 상태 확인 — content_hash
  재현성/raw_structure/결정론 아웃라인 파서/embed_text render/embedding_model+GIN
  전부 미착수, DB스키마사전·공통코드는 테이블+파서만 완료고 재적재는 0건이라 "부분
  완료"), ③평가체계 축적(Track2 실행 이력 건수·최근 실행일, 기존 `/track2/history`
  재사용), ④retrieval 평가 현황(최신 hit@K/Top-1 + 추이 막대그래프). 새 테이블 없음
  (순수 집계 API). `pipeline-stats`는 `policy_item`/`ref_db_column`/`ref_common_code`가
  전부 namespace_id로 격리되는 테이블이라 다른 정책 엔드포인트와 동일하게 namespace로
  스코프(`check_namespace_ownership`).
  **재설계(같은 날)**: 첫 버전은 숫자 카드 4개를 그냥 나열해서 "왜 있는지/뭘 보여주는지
  모호하다"는 실사용 피드백을 받음 — 4단계가 원본→구조화→평가 실행→평가 결과로 이어지는
  하나의 파이프라인이라는 걸 전달하도록, 맨 위에 단계 커넥터(색으로 단계별 상태 표시)와
  동적 요약 문장(실제 수치로 조립되는 한 문단 — "기준정보는 N건 쌓였지만 갭 이슈 M건이
  미착수라...")을 추가하고, 각 섹션 첫 줄에 "그래서 뭘 알 수 있는지"를 숫자보다 먼저
  쓰게 바꿈. 추이 막대그래프는 실행 1회뿐일 때 막대 1개가 전체 폭을 채워 단색 블록처럼
  보이는 문제가 있어(실사용 스크린샷으로 발견) 2회 미만이면 안내 문구로 대체.
- v2.76: **실험실 지표 선택 UI + 저장 전략 현황 배지** (work-os 전달 프롬프트 작업2,
  `PolicyLab.tsx`) — 소비 패턴→추천 지표 결정론 규칙표(`METRIC_INFO`, LLM 불필요)를
  코드화: 풀컨텍스트 주입(채팅 답변 생성, top_k 전체를 LLM에 넣음)=hit@K, 근거카드
  1건 노출(현재 채팅 UI 방식)=Top-1 Accuracy, 후보 정제 필요=precision@K. 지표
  체크박스 토글로 hit@K/Top-1/Precision/채널기여도를 화면에서 켜고 끔(계산은 항상
  전부 수행, 토글은 표시만 제어). **Top-1 Accuracy를 화면에 처음 표시**(v2.74에서
  백엔드엔 있었지만 프론트에 안 그려지고 있던 값). 저장 전략 현황 배지(RDB✓/벡터✓/
  그래프○-미도입)도 추가 — 향후 확장 후보 자리를 미리 잡아둠. 하네스(L4, 지표 결과로
  자동 제어)는 이번 라운드 명시적 제외(work-os 09-11 팀장 논의) — 이번 건 L3(가시성)
  UI까지만, `METRIC_INFO`는 순수 코드 상수라 나중에 하네스가 그대로 재사용 가능한
  형태로 분리해둠.
- v2.75: **VOC 반복패턴 감지 유사도 임계치 재보정** (`email_pattern_similarity_threshold`
  0.85 → 0.58, `ops_system_config` 런타임 값, 코드 변경 없음) — v2.72 임베딩 모델 교체
  (mpnet-768→KURE-v1) 이후 VOC 클러스터링 임계치를 재검증하지 않았던 걸 뒤늦게 발견.
  실측(104개 클러스터·884건 기준): 이미 같은 이슈로 확인된 쌍조차 새 모델 기준으론
  95%(369/387)가 0.85를 못 넘김 — recall 17.9%로 반복패턴 감지가 사실상 무력화된
  상태였음. 전수(91,936쌍) 임계치 스윕으로 Youden's J 최적점(0.58, recall 71.2%/
  FPR 39.9%, J=0.313)을 찾아 적용. **단, 이게 완전한 해결은 아님** — 같은 클러스터
  내부 유사도(0.39~0.69)와 서로 다른 클러스터 간 유사도(0.65~0.86)가 큰 폭으로
  겹쳐서, 코사인 유사도 단일 임계치로는 J 0.31 이상의 분리력을 못 냄(완전 분리는
  1.0). 오탐(다른 이슈를 같은 클러스터로 잘못 합치는 경우)이 실제로 얼마나 느는지
  며칠 모니터링 필요 — 늘면 2단계 검증(코사인은 후보 추출만, 최종 판단은 LLM 등)
  같은 구조 변경 검토 대상.
  같은 이유로 `_COVERAGE_MIN_SIMILARITY`(지식 커버리지 판정, `pattern_detection.py`
  모듈 상수)도 0.70 → **0.55**로 잠정 하향(코드 변경, backend 재시작으로 반영).
  이쪽은 정답이 "이 지식 문서가 진짜 이 VOC를 해결하냐"는 의미 판단이라 원래도
  LLM 최종검증이 있어야 하는데, 지금 사내 LLM 게이트웨이가 월간 크레딧 초과로
  막혀있어(2026-09 소진, 위 v2.75 항목과 별개 이슈) 대량 검증 불가 — 수동으로
  고른 정/오탐 8쌍만으로 방향만 확인(확실한 오탐 0.40~0.44, 확실한 정탐
  0.60~0.70)한 잠정치라 VOC 임계치만큼 정밀하지 않음. **크레딧 복구 후 LLM
  대량 검증으로 재보정 필요.**
- v2.74: **Track2 실행 이력 저장 + Top-1 Accuracy 지표** — work-os 세션 전달 프롬프트
  "실험실 게이트 최소선"(작업2·3 착수분). `policy_track2_run` 신규 테이블 — 지금까지
  Track2는 매번 라이브로만 실행되고 결과가 어디에도 안 남아 "retrieval 평가가 시간에
  따라 어떻게 변해왔는지" 추이를 볼 수 없었다. `/track2/run`을 호출할 때마다 자동으로
  스냅샷 저장(실행 실패해도 조회 응답 자체는 정상 반환, 저장 실패는 로그만),
  `GET /track2/history`로 이력 조회. **Top-1 Accuracy 신규 지표** — "근거카드 1건 노출"
  소비 패턴에 대응. A(지식-only)는 단일 랭킹 리스트라 0번째가 정답인지로 정의되지만,
  B(하이브리드)는 RDB/벡터 두 채널로 나뉘어 있어 "진짜 하나의 순위"가 원래 없다는 게
  이 아키텍처 자체의 특징(v2.71부터 계속 확인돼온 것)이라 억지로 합치지 않고
  **채널별로 따로**(`b_top1_param_accuracy`, `b_top1_narrative_accuracy`) 측정한다.
  실측(89문항): `a_top1_accuracy` 47.2%, `b_top1_narrative_accuracy` 48.3%인데
  **`b_top1_param_accuracy`는 18.0%로 낮음** — RDB(policy_param) 채널이 hit@K(95.7%)는
  높지만 정답을 1등으로 올리는 데는 약하다는 새 발견(`ts_rank` 정렬 자체의 한계로 추정,
  원인 분석은 다음 과제). 골든셋 v1도 work-os 89건 전수검토 결과 반영해 정정 —
  navigation 중복 3건(딜리버스_외부서비스/배치) 통합, 빈 슬롯 2개는 커버리지 0이던
  온라인스토어 리워드정책으로 재배정, 원본 대조 2건 중 q056은 질문을 실제 row 내용에
  맞게 정정(q008은 재확인 결과 원래 정답이 맞았음). 골든셋 파일 자체는 `.gitignore`
  대상(민감정보)이라 git 이력엔 안 남음.
- v2.73: **DB 스키마 사전/공통코드 RDB화 + 리랭커 조건부 적용**
  1. **`ref_db_column`/`ref_common_code` 신규 테이블** — 2026-09-10 정리한 `rag_knowledge`
     DB/공통코드 오염 데이터(3,040건 soft-delete 후 영구 삭제, 원본 파일 자체가 업로드 시
     디스크 저장 없이 복구 불가)를 "예전 데이터 복구"가 아니라 **앞으로 같은 종류의 데이터가
     들어올 때 쓸 구조**로 재설계. 정확 조회 전용이라 embedding 컬럼 자체가 없음(§2 결정
     규칙). `service/refdata/parser.py` — 마크다운 파이프 표 결정론 파서(LLM 미사용,
     forward-fill로 병합셀 스타일 빈 칸 처리), `service.py` — 적재/키워드검색. 원본 파일이
     없어 실물 재검증은 못 했고, 컬럼 순서는 삭제 전 관찰 기준(unit test는 합성 데이터).
     업로드 UI/엔드포인트는 아직 없음(실제 재업로드 파일이 생기기 전까진 YAGNI로 보류).
  2. **정책 검색 리랭커 조건부 적용** — 리랭커 후보 비교(v2.72) 때 dragonkue/BAAI/Dongjin-kr
     3종 전부 **navigation형(카테고리 전체조회) 질문에서만 예외 없이 악화**(-10.0/-5.0/
     -25.0%p)되는 게 확인됨 — 정답이 여러 개인 유형에 "가장 딱 맞는 문서 1개"를 고르는
     CrossEncoder 방식이 구조적으로 안 맞기 때문으로 추정. `service/policy/query_type.py`의
     규칙 기반 판별(89문항 실측 precision 96.0%/recall 100.0%)로 navigation형만 걸러내고,
     `search.py:search_policy()`가 그 외 질문에서만 채널별(param/narrative 각각 독립)로
     넓게 가져온 후보(`reranker_candidates`)를 재정렬해 top_k를 뽑는다 — **채널을 합쳐서
     전체 top_k로 줄이지 않는다**: LLM 컨텍스트 재현율을 지키려고 param/narrative를 각각
     top_k씩 유지하는 기존 설계(2026-09-07, "근거 1건만 남겼다가 정답이 통째로 빠진"
     실사고로 확정된 원칙)를 그대로 지킨 것. `reranker_enabled` 기본값은 여전히 `False` —
     이 분기 자체가 관리자가 켜기 전까진 항상 비활성(실측: Track2 재검증 결과 82.0%→88.8%→
     **88.8%로 동일**, 회귀 없음 확인). 활성화 여부는 추후 별도 판단.
- v2.72: **임베딩 모델 교체 — mpnet(768차원) → KURE-v1(1024차원), 벡터 컬럼 전량 재색인** —
  `docs/tech/embedding-reranker-upgrade-plan.md`(2026-09-07 작성, huggingface.co 사내망
  접근 제한으로 실측 보류돼 있던 조사)를 2026-09-11 사내망 접근이 열려 실행. 89문항 골든셋
  실측: 현재 모델 hit@10 55.1% vs 후보 3종(BGE-M3 77.5% / dragonkue-BGE-m3-ko 80.9% /
  **KURE-v1 82.0%**, 전 유형 개선) — MTEB-ko-retrieval 공개 벤치마크 1위 순위와 실측 순위가
  일치해 KURE-v1로 확정. `vector(768)` 보유 8개 테이블(`rag_knowledge`/`policy_chunk`/
  `rag_glossary`/`rag_fewshot`/`rag_conv_summary`/`ops_email_analysis`/`ops_voc_cluster`/
  `rag_knowledge_history`, Text2SQL 제거로 미등록 상태인 `sql_*` 3개 테이블은 제외)를
  `vector(1024)`로 스키마 변경 후 전량 재임베딩(`backend/scripts/migrate_embedding_model.py`).
  원문 텍스트를 각 테이블의 실제 저장 코드와 동일한 소스로 재구성(예: `ops_email_analysis`는
  LLM 생성 `issue_signature`가 DB에 영속화된 적이 없어, 코드에 이미 있던 "원문 임베딩으로
  안전하게 폴백" 경로를 그대로 재사용). 실측 중 매번 `rag_knowledge` 인코딩 단계에서
  프로세스가 죽는 문제(exit 137)를 겪었는데, 원인은 asyncio가 아니라 2026-09-10 soft-delete
  판단 보류 상태였던 id=17(22KB 마크다운 표 블롭)의 토큰 수가 12,061개로 KURE-v1의
  max_seq_length(8192)를 넘은 것 — 모든 테이블 공통으로 임베딩 입력 텍스트를 안전하게
  자르는 안전장치를 추가해 해결. 재색인 후 실 프로덕션 API(`POST /api/policy/track2/run`,
  v2.71 조사/어미 제거와 함께 작동한 결과)로 재검증: 하이브리드 hit@10 82.0%→**88.8%**
  (narrative 유형은 100%), precision@10도 8.9%→10.8%로 함께 상승(잡음 증가 없음). 사용하지
  않게 된 모델 캐시는 정리(임베딩 후보 3종 + 리랭커 후보 3종, 총 9.5GB 삭제해 최종 4.3GB만
  유지). 이어서 리랭커(CrossEncoder) 후보도 같은 골든셋으로 비교(`backend/scripts/
  bench_reranker_models.py`, 실 하이브리드 검색 top-20 후보 재정렬 hit@1 기준) — 현재
  기본값(`ms-marco-MiniLM`, 영어전용)은 재정렬 없음(53.1%) 대비 오히려 **-33.3%p 악화**
  (`reranker_enabled=False`로 꺼둔 게 옳았음이 실측으로 확인됨). 임베딩과 같은 BGE-M3 계열인
  `dragonkue/bge-reranker-v2-m3-ko`가 **+11.1%p(64.2%)**로 1위 — `reranker_model` 기본값만
  이걸로 교체(`core/config.py`, `Dockerfile`), `reranker_enabled`는 VOC 관련성 게이트 등
  다른 경로에도 영향을 주는 앱 전체 동작 변경이라 아직 `False` 유지(활성화는 별도 검증 후 결정).
- v2.71: **정책 RDB(policy_param) 검색에 한국어 조사/어미 규칙 기반 제거 적용** — v2.70에서
  진단만 하고 미뤄뒀던 "`to_tsvector('simple', ...)`가 한국어 형태소 분석을 안 해서 RDB가
  자기 전문 분야(param 질의)에서도 벡터에 밀린다"는 문제를 실제로 개선. mecab-ko/Kiwi 같은
  진짜 형태소 분석기는 별도 프로세스·사전 유지보수 부담이 커(정책 파라미터 389건 규모엔
  과함), 먼저 저비용 규칙 기반 조사/어미 제거를 실측 검증(`backend/scripts/
  bench_suffix_stripping.py`, 89문항 골든셋)한 뒤 적용. 접미사 목록·최소 잔여 어간 2글자
  보장 로직은 `backend/service/policy/korean_text.py`(오프라인 재사용용)와 `init/
  06-policy-strip-ko.sql`의 `policy_strip_ko()` 함수(실제 쿼리 경로, `search.py`의
  `search_policy()`가 콘텐츠·쿼리 양쪽에 적용) 두 곳에 있고, 동작이 같아야 한다 — SQL
  쪽은 "일치하는 접미사 중 가장 긴 것" 방식이라 배열 순서와 무관하게 동일 집합이면 항상
  같은 결과를 낸다. 저장 컬럼 추가 없이 조회 시점 변환(건수가 작아 인덱스 없이도 문제
  없음). 실제 프로덕션 API(`POST /api/policy/track2/run`)로 재검증한 결과(89문항, 적용
  전/후): 전체 hit@10 77.5%→**82.0%**(+4.5%p), param(RDB 전문 분야) 87.0%→**95.7%**,
  navigation 58.3%→66.7%. precision@10은 8.9%→8.9%로 사실상 변화 없음(잡음이 늘지 않음).
  기여도 분해(v2.70)로 보면 RDB만 13.5%→17.98%, 둘 다 13.5%→25.8%로 늘고 벡터만
  50.6%→38.2%로 줄어 — RDB가 예전엔 놓치던 걸 벡터와 함께/단독으로 더 많이 맞히게 된
  것으로 확인. 테스트 1개 파일 추가(`test_policy_korean_text.py`, 총 354개).
- v2.70: **Track 2에 RDB/벡터 기여도 분해(source attribution) 추가** — "B안(하이브리드)이
  hit@K에서 이겼다"까지는 알아도 그 안에서 RDB(policy_param)와 벡터(policy_chunk) 중 실제로
  뭐가 정답을 찾아내고 있는지는 안 보인다는 지적에서 착수. `run_comparison()`이 B그룹의 hit을
  "RDB만/벡터만/둘 다"세 갈래로 쪼개 반환(`b_hit_rdb_only`/`b_hit_vector_only`/`b_hit_both`,
  세 값의 합은 항상 `b_hit_rate`와 일치). 내부적으로 `_QueryScore` 데이터클래스로 리팩토링
  (기존 raw tuple 누적 방식은 필드가 늘면서 가독성이 떨어져 교체). 관리자 화면에 "B 근거:
  표만/의미검색만/둘 다" 표시 추가. 실 재실행 결과(전체): RDB만 13.5% / 벡터만 50.6% / 둘 다
  13.5%(합 77.5%=b_hit_rate) — **param 타입(RDB가 전문이어야 할 영역)에서조차 벡터 단독
  기여(47.8%)가 RDB 단독 기여(26.1%)보다 큼**이 새로 확인됨. 원인으로 PostgreSQL
  `to_tsvector('simple', ...)`가 한국어 형태소 분석(조사/어미 활용 정규화)을 하지 않아
  "담을"/"담기" 같은 활용형이 안 겹치는 문제를 대화 중 진단 — 형태소 분석기 추가는 실행
  안 하고 진단만 문서화(YAGNI, 다음에 착수할 때 참고할 근거). 테스트 1개 추가(총 344개).
- v2.69: **Track 2 저장소 실험실에 precision@K 추가** — 대화 중 "hit@K는 정답 유무만 보고
  후보군의 잡음 비율은 안 잰다"는 지적에서 착수. `track2.run_comparison()`이 기존 hit@K
  (top-K 안에 정답 포함 여부)에 더해 precision(검색된 고유 item 중 실제 정답 비율)을
  A/B·유형별로 함께 계산·반환하도록 확장(`a_precision`/`b_precision` 필드 추가, DB 스키마
  변경 없음 — 순수 계산값). A/B가 반환하는 후보 개수가 다를 수 있어(B는 param+narrative
  합산) 분모는 고정 K가 아니라 실제 반환된 고유 item 수를 사용. 관리자 화면 "저장소
  실험실"에 "정답 집중도(precision)" 표시 추가(전체 요약 + 유형별 세부). 실제 재실행
  결과: hit@K는 기존 측정(A 57.3%→75.3%)과 유사한 수준(A 55.1%, B 77.5%) 재확인, precision은
  4개 유형 전부에서 B가 A보다 높음(전체 6.1%→9.0%) — B가 정답을 더 많이 찾을 뿐 아니라
  후보군 잡음도 A보다 적다는 게 새로 확인됨(특히 navigation 유형에서 격차 큼, 5.4%→11.1%).
  기존 `test_policy_track2.py` 테스트에 precision 검증 assertion 추가(신규 테스트 함수는
  아님 — 전체 343개 그대로 통과).
- v2.68: **지식 생명주기 관리 — "할 수 있는 것" 실행분** (`docs/tech/knowledge-lifecycle-design.md`
  §6, 2026-08-28 분석 문서의 실측 기반 우선순위 1~3위 + 대화 중 새로 나온 4번째 항목).
  ① **병합 이력 보존**: `resolve_duplicate()`의 merge 처리가 대상 지식을 그 자리에서 덮어써
  이전 내용이 어디에도 안 남던 문제 — 덮어쓰기 전 신규 테이블 `rag_knowledge_history`에
  content/embedding을 같은 트랜잭션에서 먼저 적재하도록 수정.
  ② **삭제 하드→소프트**: `delete_knowledge`/`bulk_delete_knowledge`가 `DELETE FROM` 대신
  `status='deleted'` UPDATE로 바뀜 — 검색/목록 쿼리가 이미 `status='active'`만 보므로 별도
  필터링 추가 없이 즉시 숨겨지고, 실수 삭제 시 복구 가능.
  ③ **스키마 선추가(Phase 0)**: `rag_knowledge`에 `logical_document_id`/`version`/
  `supersedes_id`/`embedding_model`/`quality_score`/`reviewed_at`/`owner` nullable 컬럼 추가.
  로직은 최소화(embedding_model만 신규 등록분부터 기록) — 데이터 3천여 건인 지금이 컬럼 추가
  제일 싼 시점이라는 원칙(문서 §4)대로 컬럼만 미리 준비.
  ④ **피드백→지식 리뷰 신호(신규)**: 나빠요 피드백에 message_id가 있으면, 그 답변이 실제
  근거로 삼았던 지식 전체(`ops_message.results`)를 신규 테이블 `rag_knowledge_review_flag`에
  적재 — 지금까지는 프론트가 넘긴 지식 하나만 감점되고 나머지 근거는 아무 신호도 안 남았다.
  자동 감점이 아니라 사람이 볼 리뷰 큐. Admin > 지식 베이스에 "리뷰 신호" 서브탭 신설(카운트
  배지 포함). few-shot 승인 큐가 활성1/대기12로 11일째 방치됐던 사례(§ 대화 논의)가 이 레버를
  안 써서 생긴 패턴이라는 진단에서 착수 — 신설한 큐도 실제로 쓰이는지는 계속 지켜볼 것.
  실 시나리오로 검증(중복 등록→병합→history 보존 확인, 삭제→status+목록제외 확인, 나빠요
  피드백→리뷰 플래그 생성+중복방지 확인). 백엔드 테스트 343개, tsc, build 전부 통과.
- v2.67: **MCP 도구 에이전트 완전 제거** — 관리자 화면·채팅 UI에 걸쳐 안 쓰는 기능이 계속
  노출되는 게 잡다하다는 판단으로 시작, 이후 `McpToolAgent`가 RAG 검색 로직(`retrieval.py`)을
  내부에서 직접 재구현해 안고 있는 구조적 결합까지 확인되어 제거 근거가 명확해짐. VOC Teams
  발송이 MCP 에이전트의 `_execute_http_call`을 재사용 중이던 의존성은 먼저 `shared/http_client.py`
  (`call_http()`)로 이관해 끊음 — 도구 레지스트리 전용 개념(파라미터 타입 변환 등)은 가져오지
  않고 순수 HTTP 호출 기능만 옮김. 백엔드(`agents/mcp_tool/`, `service/mcp_tool/`)·프론트(관리자
  MCP 도구 탭, 채팅 MCP 토글, 디버그 패널의 MCP 실행 섹션, 관련 SSE 이벤트·타입) 전부 제거.
  `ops_mcp_tool`/`ops_mcp_tool_log` 테이블과 `ops_prompt`의 mcp_tool 프롬프트 3행은 Text2SQL
  전례와 동일하게 삭제 마이그레이션 없이 방치(기존 설치엔 남지만 무해). 검증: 백엔드 테스트
  343개 통과, `npx tsc --noEmit` + `npm run build` 통과.
- v2.66: **정책 근거 카드를 1건으로 압축 — 답변 생성 후 역추적 방식** — v2.65 배포 직후
  사용자가 "근거가 너무 많이 보인다(지식 3개 + 정책 10개), 원문 정책 1건만 보여달라"고
  지적, 이어서 "10개를 참조해서 답변 만든 거냐"는 확인 질문(답은 "그동안은 그랬다" —
  `build_policy_context()`가 param+narrative 최대 10건 전부를 LLM 프롬프트에 넣고 있었음).
  **1차 시도(바로 폐기)**: 검색 직후 벡터 점수 1위 후보 하나로 LLM 컨텍스트까지 줄이는
  `select_top_policy_hit()`를 만들었으나, 실 E2E에서 "장바구니 최대 개수" 질문의 벡터 점수
  1위 narrative가 실제로 무관한 "배송지"였고, 컨텍스트를 그거 하나로 줄이자 진짜 정답인
  "장바구니 최대 보관 수량" 항목이 통째로 빠져 LLM이 "관련 지식을 찾지 못했습니다"로 답변
  자체에 실패하는 걸 확인 — 즉시 되돌림. **최종 설계**: LLM 컨텍스트(`build_policy_context()`)
  는 원래대로 top_k=5×2(param+narrative) 다중 후보를 그대로 유지해 재현율을 지키고, 화면에
  보여줄 근거 카드만 **답변이 다 생성된 뒤에** 신규 `select_cited_hit()`으로 역추적한다 —
  최종 답변 텍스트와 각 후보의 원문(`raw_body`)이 토큰 단위로 얼마나 겹치는지 세어 가장 많이
  겹치는 1건만 고른다(겹치는 게 하나도 없으면 — LLM이 정책 데이터를 안 썼거나 "모른다"류
  답변이면 — 억지로 아무거나 보여주지 않고 근거 없음으로 처리). `agent.py`는 이 때문에 정책
  인용을 LLM 스트리밍이 끝난 뒤 두 번째 `meta` SSE 이벤트로 늦게 내려보낸다(1차 meta는
  `mapped_term`/`results`만, `policy_citations`는 빈 배열). 부수 효과로 param 검색 SQL의
  `ORDER BY`가 `i.id DESC`(단순 최신순, 사실상 무의미)에서 `ts_rank DESC`(실제 관련도순)로
  교정됨 — `ParamHit`에 `score` 필드 추가. 프론트엔드는 변경 없음(`PolicyCitationCard` 목록
  렌더링이 배열 길이에 자동 적응). 실 E2E로 재확인: 온라인스토어 DB/딜리버스 DB 양쪽에서
  정책 근거가 정확히 1건만 뜨고, 그 1건이 실제 답변 수치와 정확히 일치함을 확인. 테스트
  4개 교체(총 343개).
- v2.65: **정책 검색 메인 채팅 편입 2단계 — "정책 근거" 인용 카드 UI** — v2.64에서 남겨둔
  숙제("지식이 정책에서 온 답인지 기준정보에서 온 답인지 알기 어렵다, 원문도 근거로 보여달라"
  는 사용자 피드백) 해결. `search_policy()`의 `ParamHit`/`NarrativeHit`에 `raw_body`(LLM
  분해 전 원문 그대로) 필드 추가, 신규 `build_policy_citations()`가 검색 결과를 화면 렌더링용
  구조화 데이터(`kind`/`policy_name`/`category_path`/`detail`/`raw_body`)로 변환. 기존
  `results`(rag_knowledge 인용) 배열엔 절대 안 섞음 — `FeedbackSection`이 `results[0].id`를
  "이 답변이 참조한 rag_knowledge id"로 써서 피드백→지식수정 흐름에 연결하는데, 다른 테이블
  (policy_param/policy_chunk)의 id가 섞이면 오인식 위험이 있기 때문. 대신 SSE `meta` 이벤트와
  `ops_message.metadata`(JSONB, text2sql 시절부터 있던 범용 컬럼 재사용)에 `policy_citations`
  라는 완전히 별도 필드로 얹는다. Semantic Cache 히트 경로도 `policy_citations`를 캐시 payload
  에 함께 저장해 재계산 없이도 동일한 인용을 재생. 프론트엔드: `ChatMessage.policyCitations`
  (SSE 라이브 스트림 경로 `useStreamStore.ts`, 과거 대화 재조회 경로 `ChatContainer.tsx`의
  `convertMessages()` 양쪽 모두에서 채움), 신규 `PolicyCitationCard.tsx`(`SearchResultCard.tsx`
  와 같은 아코디언 패턴이되 보라/자홍 계열 배지로 시각적으로 구분, 펼치면 "원문 정책" 섹션에
  `raw_body` 그대로 노출)를 `MessageItem.tsx`에 "📋 정책 근거 N건" 섹션으로 추가(기존 "검색된
  문서" 섹션과 별개, `results` 배열 변경 없음). 실 E2E로 확인: 온라인스토어 DB에서 "장바구니
  최대 개수가 몇 개야?" 질문에 `policy_citations` 10건(파라미터 5 + 서술 5, 각각 `raw_body`
  포함)이 SSE meta 이벤트로 내려오고 `ops_message.metadata`에도 정확히 영속화됨, 정책 데이터
  없는 네임스페이스(팀 공통 DB)에서는 `policy_citations: []`로 기존 동작 그대로 무회귀 확인.
  테스트 4개 추가(총 339개).
- v2.64: **정책 검색을 메인 채팅에 편입(1단계)** — Track 2로 하이브리드 스키마 우세가
  확정된 뒤(v2.62), `agents/knowledge_rag/agent.py:stream_chat()`이 `rag_knowledge` 검색과
  함께 `service.policy.search.search_policy()`도 호출해 정책 데이터를 `doc_context`에 텍스트로
  더한다. `has_policy_data(namespace)`로 정책 데이터 없는 네임스페이스에서는 매 턴 불필요한
  쿼리를 스킵. `search_policy()`에 `query_vec` 선택 인자 추가 — `agent.py`가 이미 계산해둔
  임베딩을 재사용해 채팅 메인 경로에서 매 턴 중복 임베딩이 생기는 걸 방지. **1단계 범위**:
  텍스트 컨텍스트만 합치고 인용 카드 UI(`results_to_payload`)는 아직 `rag_knowledge` 모양
  그대로라 정책 출처가 채팅 화면 카드로는 안 뜨고 LLM 답변 본문에만 반영됨(실사용 피드백 보고
  2단계=카드 UI 확장 여부 결정). 비스트리밍 `/api/chat`(debug 전용, `_run_pipeline()`)은 이번
  범위에서 제외 — 실사용자가 쓰는 `/api/chat/stream`(SSE, `AgentRegistry` 경유)만 대상.
  실 E2E로 확인: 온라인스토어 DB에서 "장바구니에 최대 몇 개까지 담을 수 있어?" 질문에 실제
  정책 데이터("일반 탭 20개, 예약 탭 20개, 추가구매상품 제외")로 정확히 답변, 정책 데이터
  없는 네임스페이스에서도 에러 없이 정상 동작 확인. 테스트 10개 추가(총 335개).
- v2.63: **저장소 실험실 API화 + 정책 항목 브라우저 개선 + 관리자 화면 통합** — 사용자 피드백
  3건 반영. ①`POST /api/policy/track2/run`(`service/policy/track2.py`) 신규 — Track 2를 매번
  일회성 스크립트로 짜지 않고 admin이 버튼으로 재실행 가능(1~2분 소요, 실행 후 임시 데이터
  자동 정리). 실행 결과가 수동 스크립트와 정확히 동일함을 실측 확인(전체 75.3%). ②`GET
  /api/policy/items`의 `q`가 `policy_name` ILIKE에서 실제 `search_policy()`(RDB+벡터) 재사용
  으로 개선, `raw_body`/`matched_via` 필드 추가 — 단 벡터 매칭에 임계치가 없으면 사실상
  전체가 "매칭"돼버리는 걸 실측으로 발견(딜리버스 71개 중 67개=94%)해 최소 유사도(0.4) 필터
  추가(41%로 개선). ③관리자 화면에서 "정책서 미분류"/"정책 항목 브라우저"로 흩어져 있던 탭을
  "정책" 대분류 탭 하나로 묶고 그 아래 서브탭(항목 브라우저/미분류/저장소 실험실) 3개로 재구성
  (`PolicyPanel.tsx`, `VocEmailPanel.tsx`와 동일한 서브탭 패턴). 실사용 중 대분류 드롭다운이
  필터링된 목록에서 선택지를 다시 뽑아 카테고리를 고르는 순간 다른 선택지가 사라지는 자기잠식
  버그도 발견·수정. 테스트 21개 추가(총 325개).
- v2.62: **정책 벡터 폴백 구현 — Track 2 재측정으로 하이브리드 우세 확인** — v2.61에서 발견한
  갭(item의 36%가 벡터 색인 없음)을 바로 수정: `service.py`가 narrative segment 없는 item에
  정책명+category_path+raw_body 전체를 폴백 `policy_chunk`로 색인(`SheetSummary.
  fallback_chunks_added`로 실제 LLM narrative와 구분 집계). 기존 480건 중 163건은 재임포트
  없이 직접 백필(`content_hash` 불변이라 재임포트해도 재생성 안 되므로 별도 스크립트 필요).
  Track 2 골든셋 89건 재측정: param 34.8%→**82.6%**(A 역전), condition_filter
  47.4%→**73.7%**, 전체 57.3%→**75.3%**. 4개 유형 전부 하이브리드 우세로 확정 —
  `agent.py`/`retrieval.py` 편입을 막던 이유 해소. 테스트 3개 추가(총 312개).
  `policy-doc-pipeline-plan.md` §4-4.
- v2.61: **Track 2 저장소 전략 A/B 실행 완료 — "하이브리드가 무조건 낫다" 가설 기각** — 골든셋
  89건으로 A(`rag_knowledge` 지식-only, 전체 378 item을 격리 테스트 네임스페이스에 얹음) vs
  B(지금 하이브리드 스키마) 실측 비교(item-id 기반 hit@10). narrative는 B 압승(91.3% vs
  60.9%), navigation도 B 우세(54.2% vs 37.5%)로 가설대로였지만, **param 유형은 A가 크게
  앞섬(73.9% vs 34.8%)** — 원인 실측: 전체 item의 36%(136/378)가 파라미터로만 분해돼
  `policy_chunk`(벡터)가 하나도 없어서, 자연어 질문이 짧은 param 필드와 어휘가 안 겹치면
  B는 못 찾지만 A는 전체를 벡터로 색인해서 찾아낸다. v2 후보: item마다 최소 1개 벡터 청크를
  보장(지금은 narrative segment가 있을 때만 생성) — `policy-doc-pipeline-plan.md` §4-3.
  `agent.py`/`retrieval.py` 편입은 이 폴백 보완 전엔 보류 권장. 테스트 전용 자원은 평가 후
  삭제(프로덕션 데이터 영향 없음).
- v2.60: **골든셋 v1 자동 생성** — Track 2용 §4-1 스펙 골든셋을 태훈에게 수작업 요청하는 대신
  실 DB(온라인스토어+딜리버스, 480건)에서 89건 자동 생성(param/narrative/navigation/
  condition_filter 4종). query는 LLM으로 원문과 다른 자연어로 재구성, expected_answer는 실제
  DB 값 그대로. 1차 96건 중 파라미터 추출 품질이 낮았던/조건이 너무 일반적인 7건은 사람이
  검토해 제거. `backend/tests/fixtures/golden_set/online_delivus_v1.jsonl`(git 커밋 금지).
  `docs/policy-doc-pipeline-plan.md` §6 참고. 다음 액션은 Track 2 비교군 A/B 실행.
- v2.59: **정책 항목 브라우저** — `GET /api/policy/items`(`service/policy/browse.py`) 신규:
  item 단위 목록 조회, 각 item에 실제로 달린 param(RDB)/narrative(벡터) 자식까지 함께 반환.
  `/search`(질의 기반 히트 리스트)와 달리 쿼리 없이도 전체를 훑어볼 수 있고 item→param/chunk
  3층 구조 그대로 나온다 — "지금 뭐가 어떻게 저장돼 있는지" 확인하는 용도. 사용자 피드백(정책이
  잘 저장됐는지 조회할 화면이 없다, RDB/벡터 연결 흐름을 가시적으로 보고 싶다)으로 착수. 관리자
  화면 "정책 항목 브라우저" 탭 신규 — 항목을 펼치면 param(RDB 아이콘)/narrative(벡터 아이콘)를
  구분해 보여줌. 테스트 8개 추가(총 309개), 실 HTTP E2E로 실제 480건(온라인스토어+딜리버스)
  데이터 조회 검증.
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
| **Chat** (`/`) | 에이전트별 채팅 — SSE 스트리밍, 결과 카드, 피드백(👍→few-shot/base_weight), 대화 메모리(요약+리콜), Markdown 답변 |
| **Admin** (`/admin`) | 에이전트별 관리 화면 — `agentScope` 필드로 탭 필터링. knowledge_rag: 네임스페이스·지식·용어집·Few-shot·캐시현황·통계·디버그. 공통: 시스템설정·사용자관리. (에이전트현황 탭 제거 — AgentSelect 화면에 헬스배지로 대체) |

- **Agent-centric 라우팅**: `useAppStore.selectedAgent: 'knowledge_rag' | null`. null이면 AgentSelect 표시, 설정 시 에이전트별 UI로 전환. 로그아웃 시 null로 리셋
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
├── service/             # 플랫폼 공통 레이어 (was domain/, platform/ 명칭 stdlib 충돌로 service/ 확정)
│   ├── auth/            #   인증/계정 (JWT, bcrypt, Fernet API Key 암호화)
│   ├── chat/            #   채팅 라우터·헬퍼·메모리 (AgentRegistry 위임)
│   ├── feedback/        #   피드백 기록 + base_weight 조정
│   ├── admin/           #   네임스페이스·통계·LLM 설정
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
│       ├── search.py          #   파라미터(RDB tsquery)+서술(벡터) 검색. 전용 엔드포인트(/api/policy/search)로도, agent.py 채팅 흐름에 has_policy_data()/build_policy_context()/build_policy_citations()로도 사용 (v2.64 편입 1단계, v2.65 인용 카드 2단계)
│       ├── unresolved_report.py #   unresolved/partial 항목 system_key별 집계 (v2.53 신규)
│       ├── browse.py          #   item 단위 브라우저 — param/chunk 자식 포함, q는 실제 검색 재사용(matched_via) (v2.59 신규, v2.63 q 개선)
│       ├── track2.py          #   Track 2 A(지식-only)/B(하이브리드) 비교를 API로 실행 (v2.63 신규)
│       ├── schemas.py         #   Pydantic 스키마
│       └── router.py          #   POST /api/policy/import, GET /api/policy/search, GET /api/policy/unresolved-summary, GET /api/policy/items, POST /api/policy/track2/run
├── core/
│   ├── config.py        # pydantic-settings, JWT·Fernet 키
│   ├── database.py      # asyncpg 풀 + resolve_namespace_id() 헬퍼
│   ├── security.py      # JWT, bcrypt, Fernet
│   └── dependencies.py  # get_current_user, get_current_admin, check_namespace_ownership
└── shared/
    ├── embedding.py     # Sentence-Transformers 싱글톤
    ├── cache.py         # Semantic Cache (Redis, 유사도 0.88, TTL 30분, graceful degradation)
    └── http_client.py   # 아웃바운드 HTTP 호출 공용 레이어 (call_http, v2.67 — VOC Teams 발송이 사용)
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

**권한 모델 (네임스페이스 기반):**
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
ops_prompt            -- 프롬프트 관리 (agent_type별 에이전트 스코핑, Admin 시스템설정 탭에서 편집)
ops_system_config     -- 시스템 설정 key-value (캐시 임계값/TTL 등 영속화, VOC 폴링 정책/Graph 자격증명도 여기 저장)

-- VOC 이메일 분석 채널 전용 (v2.40 신규)
ops_voc_routing       -- 파트별 담당 메일함 ↔ Teams 웹훅 ↔ 온콜 연락처 매핑
ops_email_analysis    -- 이메일 건별 분석 결과 (source_message_id UNIQUE로 중복 수집 방지, 30일 보관, v2.47: embedding VECTOR(768)/voc_cluster_id FK 추가)
ops_email_poll_cycle  -- 폴링 사이클(스케줄러 실행 회차)별 성공/실패 이력 (30일 보관)
ops_voc_cluster       -- 반복 VOC 클러스터 (representative_embedding=centroid, member_count, coverage_knowledge_id/coverage_verified — 해결방안 LLM 검증 캐시) (v2.47 신규)

-- KnowledgeRAG 전용 (rag_* prefix, v2.8에서 ops_*→rag_* 변경)
rag_knowledge         -- 지식 베이스 (HNSW + GIN FTS, base_weight, source_file/chunk_idx 추적,
                      --   status: active/pending_review/rejected/deleted(v2.68 소프트삭제),
                      --   logical_document_id/version/supersedes_id/embedding_model 등 스키마
                      --   선추가만 됨(v2.68 Phase 0, 로직 아직 없음))
rag_knowledge_duplicate_match -- 중복탐지 매칭 후보 (v2.34)
rag_knowledge_history -- 병합(merge) 시 덮어써지기 전 content/embedding 보존 (v2.68)
rag_knowledge_review_flag -- 나빠요 피드백이 근거로 삼은 지식 리뷰 큐 (v2.68)
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
| 모델명 | `nlpai-lab/KURE-v1` (v2.72부터, 이전 `paraphrase-multilingual-mpnet-base-v2`) |
| 벡터 차원 | 1024 |
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
| `EMBEDDING_MODEL` | `nlpai-lab/KURE-v1` (v2.72부터) | 임베딩 모델명 |
| `VECTOR_DIM` | `1024` | 벡터 차원 수 |
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

`ops_part`, `ops_user`, `ops_namespace`, `rag_knowledge_category`, `ops_feedback`, `ops_query_log`, `ops_conversation`, `ops_message`, `ops_prompt`, `ops_system_config`

전체 스키마 정의는 `docs/table-definition.md` 참조.
