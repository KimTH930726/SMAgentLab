# OpsLens 시스템 아키텍처 (v2.211)

## 개요

OpsLens(구 Ops-Navigator, 2026-10-06 이름 변경)는 정책서·매뉴얼·운영 지식·공통코드처럼 흩어진 IT 운영 지식을 한곳에
모아 근거와 함께 답하는 **IT 운영 지식 통합 AI 플랫폼**이다. 틀린 답은 사용자 신고 → 담당자 검토로 바로잡는다.
사용자는 에이전트를 선택해 목적에 맞는 AI를 사용한다: 지식 기반 Q&A(KnowledgeRAG).

> Text-to-SQL 에이전트는 v2.51에서 `dev_0`/`main`에서 분리·제거됐다(현재 과업 범위 아님) — 코드는
> `archive/with-text2sql` 브랜치(2026-09-03 시점 스냅샷)에 형상관리용으로 보존돼 있다.
> MCP 도구 에이전트는 v2.67에서 완전 제거됐다(Text2SQL과 달리 브랜치 보존 없음 — 배경은
> `docs/archive/architecture-history-v2.0-v2.99.md` v2.67 항목).

**주요 이력 요약** (스키마 변경 상세는 `table-definition.md` §20 마이그레이션 이력 참조)
- v2.212 (2026-11-05, dev_0): **VOC 검색 = 채팅과 같은 경로 + 가까운 시스템 자동 판단 · VOC를 시스템 컬렉션으로**. 막힌 일: 11/02 개편으로 지식이 시스템 컬렉션으로 옮겨져, 라우팅 영역("딜리버스", 정책 컬렉션 · 지식 0)만 찾던 VOC는 11/03부터 근거 지식 0건(201건 중 0, 그 전 약 33%) — 메일함 문의도 300건 중 284건이 홈페이지 시스템 쪽. 바꾼 것: `knowledge.api.chat_context`(→ `agent.build_chat_context` 그대로, 나중에 API로 제공) · 지식 범위 `nearest_system`(받침 연결 + 메일과 가장 가까운 시스템, 1·2위 차이 < 0.03이면 2개) · 정책 범위 설정 `email_voc_policy_collections`(비면 채팅 규칙, 임시 "리워드") · 근거 있음 = 지식 관련도 ≥ 기준 또는 정책(서술 ≥ 0.50 · 파라미터 > 0.05, 하한 넘은 정책만 카드에) · 카드 관련 시스템 · 시스템별 참고 지식 · 참고 정책 · 묶음 해결 방안·이력 근거 조회도 시스템 컬렉션까지 · VOC 메뉴 = 시스템 컬렉션 + 이력 이동 `scripts/move_voc_to_system.py`(라우팅·분석·묶음 한 트랜잭션, 건수 점검, 되돌리기). 재생 300건: 근거 0 → 299(지식 290). 사람 확인 전 임시 오픈 — 채팅 규칙 정책 하한이 긴 메일엔 느슨함(딜리버스 정책 293/300 통과) 확인 필요.
- v2.211 (2026-11-05, dev_0): **검토 필요 사유 · 비교 창 차이·병합 방향 · 피드백 컬렉션 필터 · 근거 최신성 가시성**(태훈 오전 검수 반영).
  ① 검토 필요 줄마다 사유 reason(source_changed 원문 바뀜 · used 쓰이는 중 = 기한 지남 + 최근 30일 채팅 근거 · unused 미사용 = 기한만 지남) —
  기본 목록·홈 카드 수 = 미사용 제외, [미사용] 칩으로 따로(컨플 400페이지 적재 뒤 90일에 한꺼번에 뜨던 구조, 로컬: 컬렉션 A 기한 지남 18 = 쓰임 9 · 미사용 9).
  ② 비교 창: 뜻 유사도 소수 한 자리 내림 · 글이 다르면 100% 안 보임(한 줄 지웠는데 100%) · 줄 차이 수(− 기존에만 · + 새 글에만) · GitHub식 −/+ 줄 ·
  [#id에 병합] → 교체 / 이어 붙이기 고름(등록 = 교체, 지식 공백·업로드 조각 = 이어 붙이기 기본, 고친 뒤 방식 바꾸면 확인) · 저장되는 곳 표시.
  ③ 사용자 피드백 컬렉션(마이그레이션 120 ops_user_feedback.namespace_id, 보던 컬렉션 · 옛 줄은 대화의 컬렉션) — 보드 [컬렉션] 필터('-' = 미지정).
  ④ 채팅 근거 최신성: 펼치기 전 [근거 ▸] 줄에 '기한 지난 근거 N', 배지 ✓ 검토 / ⚠ 기한 지남. ⑤ [답변 틀림] 칩마다 예시 문구.
  ⑥ 지식 조회 검색↔목록 오가도 검색어·결과·카드 필터 유지(글자 거르기는 목록에서만). ⑦ 승인 대기 원문 링크에 페이지 제목(POST /page-urls → {url, title}).
  ⑧ AI 감사 잦은 번복이 '? → ?'(추천 없음·지운 컬렉션) → '추천 없음 / 삭제된 컬렉션'.
- v2.210 (2026-11-04, dev_0): **등록 순간 비슷한 지식 · 검토 기한 · 근거 번호 프롬프트 · 화면 정리**.
  ① 근거 번호 프롬프트(chat_system_knowledge, 스위치 cite_prompt) = 답 문맥에 운영 지식이 채택될 때만(agent.chat_prompt_for, 측정 스크립트도 같은 함수) —
  로컬 LLM 11/04: 업무지식 106 정답 75.5→84.0%·단정 오답 19.8→15.1%, 정책서 485 85.4→86.8%·11.8→10.3%(둘 다 게이트 통과). 어디서나 쓰면 정책서 −1.9%p·오답 증가라 안 씀.
  ② 직접 입력 등록 순간 "비슷한 지식이 이미 있어요"(POST /api/knowledge/similar — 등록과 같은 판정 함수) → 등록 안 함·합치기·새로 등록(keep_new), 판단은 AI 감사 '중복 의심'(stage register).
  ③ 검토 기한(ops_namespace.review_interval_days, 운영 지식 › 검토 필요 · 홈 카드, [검토 완료]). ④ 데이터 고치기 후보(ingest_*) 운영 반영 = 게이트 통과 + 승인 뒤 적재 검사 어긋남 0(improve.ingest_fix_error).
  ⑤ 화면: 사용자 피드백 칸반(신규·선정·진행 중·완료 + 종료 접힘) · 지식 검색 카드 전체 클릭 = 수정·관련도 색 구간(35% 미만 = 근거 약함) · 원문 확인 카드 [이슈 아님으로 닫기]·[처리 완료로 닫기]·[정정 요청 ▶] ·
  정책 상세 [수정 제안] 하나 + ⋯(분류 옮기기·폐기) · AI 감사 메뉴 이름·[기록 보기 ›]·날짜별 다섯 갈래 막대·걸린 거르기 칩. ⑥ main.py 이름 누락 검사 테스트(test_main_names, 사고 INC-2026-11-04-01).
  ⑦ 업계 방법론 묶음(조사 → 우리 규모로 덜어냄): 조각 미리보기 비슷한 지식 배지·[비교](POST /similar/batch, 조각 keep_new) · 지식 공백 등록 창도 같은 비교 ·
  승인 대기 비교 창 [원문 ↗](POST /page-urls) · 채팅 근거 최신성 배지(POST /freshness — 검토 필요와 같은 규칙) · 검토 필요 "원문 바뀜"(동기화 충돌 페이지 맨 위) ·
  [답변 틀림] 사유 칩 4개(ops_improvement_item.user_reason, 정정 카드에 표시 — 사람 피드백은 규격 선택지로).
  ⑧ 결함 수정(10/08~): 중복 비교의 "같은 원문 옛 버전 빼기" 조건에 NULL 가드가 없어 원문 연결 없는 조각(직접 입력 등, 사용 중 1,756 중 80)이 일괄 등록·미리보기 중복 비교에서 통째로 빠졌다
  (retrieval.find_similar_active_knowledge — E2E 에이전트 발견). ⑨ 업무지식 조각 맥락 머리 재판정 = 안 씀(검색 106문항, 임베딩 머리 좋아짐 2·나빠짐 3 — 기법 대장 chunk_header).
- v2.209 (2026-11-04, dev_0): **AI 감사 리포트 — 사람 검토(HITL) 결과 다섯 갈래 + 표본 감사**. 판단마다 수용·번복·자동 적용·규칙 적용·검토 대기(override.REVIEW_SQL = review_status), 번복률 = 번복 ÷ 사람 검토(수용+번복) — 팀 적재 묶음 규칙 413건이 "수용"으로 섞여 1.1%로 보이던 것을 바로잡음(사람이 본 등록 위치 21건 중 번복 5, 24%). 표본 감사(spot check): 자동 적용·수용된 관리자 검토 종류에서 무작위 n건 → 수용/번복+사유(ops_ai_decision.spot_*), `GET·POST /api/eval/decisions/spot-check`. 화면: 요약 4칸 · 주요 발견 · 판단 ↔ 검토 표 · 번복 패턴 → 조치 · 미검토·표본 감사 · 감사 로그(검토 결과 거르기). 용어는 업계 HITL(수용·번복·번복 사유 = AI 오류/선호 차이/맥락 변화).
- v2.208 (2026-11-04, dev_0): **적재 데이터 품질 — 중복 판정 2단 · 같은 원본 안 완전 중복 건너뜀 · 제목만 조각 합치기 + 지식 검색 화면 + VOC 같은 문제 확인 로컬 LLM + 피드백 단계 이동**. 등록: 의미 ≥ 0.88 후보 중 양식 줄(컬렉션 데이터에서 3쪽·1% 이상)을 뺀 글자 유사도 ≥ 0.8만 승인 대기(trustkit `ingest/dedupe.py`), 같은 원본 안 공백만 다른 반복은 한 번만 저장(작업 `skipped_exact_*`) — 다른 원본의 같은 글은 승인 대기(건너뛰면 원본 삭제·롤백 때 영구 유실, 리뷰 H1). 분할: 섹션 분리 모드에서 본문 줄 없는 제목만 조각을 같은 부모 아래 다음 조각 앞(아니면 앞 조각 뒤)에. check_ingest `knowledge_dup`(공백만 다른 것 포함)·`knowledge_heading_only`. 홈페이지 승인 대기 457 → 226(재판정 259 사용 중, 다른 원본 완전 일치 19는 다시 대기). 지식 조회 기본 [검색] = 채팅과 같은 검색(정책 파트 용어·연결 컬렉션)을 카드로(관련도·위치·채널·채팅 노출 횟수, 패싯 건수) — `POST /api/knowledge/explore`. VOC 같은 문제 확인: 원문이 들어가 게이트웨이 민감 정보 필터에 거의 전부 거부(3일 26번) → `get_local_llm()` + 분석 단계와 같은 PII 가림(사람 40쌍 일치 31 → 35). 사용자 피드백 상태는 다음 단계·한 단계 되돌리기만(서버 409).
- v2.207 (2026-11-04, dev_0): **승인 대기 좌우 차이 병합 · VOC 확인 실패 칸 · 팀 적재 미리보기 취소**. 승인 대기 = 가장 비슷한 기존 지식 유사도 높은 순 + 좌우 차이(기존 ↔ 신규, 고친 줄은 바뀐 낱말만 강조, utils/textDiff.ts) · 비교할 기존 지식이 없으면 [원문] · 매칭 조회 실패는 오류로("매칭 없음"과 구분). VOC 수집 사이클 표 `total_skipped_confirm_failed`(같은 문제 확인 실패 — 반복 묶음·Teams 발송에서 빠짐) + 실시간 상태 경고. 팀 적재 배치 상태 `cancelled`(#3 GIFT 미리보기 — 청크 규칙 개선 뒤 다시), 취소한 미리보기의 제외·보류 결정은 동기화에 안 씀. 측정 스크립트 `--cite-keep`(인용 규칙 프롬프트만, 11/03 측정: 정답 75.5→84.0% · 단정 오답 19.8→16.0%, 채팅 반영은 승인 뒤).
- v2.206 (2026-11-03 밤, dev_0): **관리 화면 가시성 — 감사 로그 카드 · 피드백 상세 창 · 정책 컬렉션 메뉴 정리 + 중복 의심 판단 기록**.
  정책 컬렉션은 운영 지식 메뉴·승인 대기 카드 없음([보기 →] = 정책서), 컬렉션 종류 확인 전엔 주소를 바로잡지 않음. 기법 대장 상세 = 모달.
  AI 감사 로그: 종류별 카드(판단 · 번복 · 비율, 누르면 거르기) + 개선 후보 추천 카드 + 날짜별 카드(KST, `GET /api/eval/overrides` daily ·
  `decisions/log?day=`) + 종류 드롭다운. 새 판단 종류 `duplicate_check` — 승인 대기 처리(그대로 저장 = 번복 · 덮어쓰기 · 반려)를
  `ops_ai_decision`에 한 줄(매칭 없는 대기·이미 처리한 건은 안 남김, 실패는 저장점으로 처리 안 막음) · 기법 `duplicate_review`.
  승인 대기 목록 API에 `max_similarity`(높은 순). 사용자 피드백: 목록은 상태만, 누르면 상세 창에서 상태 고르기. 정책서 메뉴·원문 확인 탭에 처리 전 수.
  trustkit `answer/citation.py`(인용 강제 — 번호 없는 본문 줄 기계적 제거, LLM 0) + 측정 옵션 `eval_knowledge_answers --cite`·`--rejudge`.
  설계: 청크 구조화 + 중복 판정 2단 + 완전 일치 거부(docs/domains/knowledge/chunk-structure-dedupe-design.md, 구현은 승인 뒤).
  승인 대기 좌우 차이 화면은 별도 브랜치 `review-diff-ui`(모양 검수 대기). DB 칸 변경 없음.
- v2.205 (2026-11-03, dev_0): **관리자 화면을 보는 사람별로 — 컬렉션 관리(시니어) / 플랫폼 관리(관리자)** (설계 docs/platform/admin-audience-split-design.md 2·3단계).
  메뉴 왼쪽 위 범위 전환(플랫폼 관리는 관리자만). 컬렉션 관리: 홈 · 운영 지식 · 정책서 · 정정 요청 · 용어집 · 통계 · 소스 연결 · 컬렉션 정보(보는 컬렉션 한 줄, 읽기).
  플랫폼 관리: 홈 · 평가 게이트 · AI 감사 로그 · 사용자 피드백 · 컬렉션 · 조직·권한 · VOC 이메일 · 설정(캐시는 설정 탭). 화면 내용은 그대로 자리만, 옛 주소 `/admin/<id>` 유지,
  비관리자가 플랫폼 주소로 오면 안내 뒤 컬렉션 관리 홈. 두 홈 = 카드(큰 숫자 + [자세히], 기존 API만 — 읽는 중 "…", 실패 "불러오지 못함"). 시스템 정보(준비 중) 메뉴 내림,
  "중복 의심" 카드는 컬렉션별 집계 API가 없어 뺌. 실 DB 통합 시험 표준(`tests/realdb.py` — 트랜잭션 → 되돌림)으로 쓰기 경로 9개 추가.
  팀 적재 공간 목록 읽기 대기 180초·실패 사유 남김(앱 공간 미리보기가 30초 시간 초과·빈 사유로 실패).
- v2.204 (2026-11-03, dev_0 — voc-cluster-fix 병합): **VOC 반복 묶음 — 대표 고정 + LLM 같은 문제 확인 · 확인 필요 표시.**
  ① 묶음 대표(`ops_voc_cluster.representative_embedding`)는 만들 때 고정 — 합류마다 옮기던 점증 갱신이 대표를 끌어 사슬 묶음(최대 130건)을 만들었다.
  ② 임베딩은 후보 찾기만, 합류는 LLM이 기준 건(첫 멤버)과 같은 문제라고 할 때만(`email_pattern_llm_confirm`, 기본 켬, 건당 최대 2회·실패 = 합류 안 함).
  ③ `email_pattern_review_size`(기본 20, 0 = 끔): 묶음이 N건 넘으면 팀즈 카드에 "확인 필요". ④ 같은 영역 묶기는 한 번에 하나씩(프로세스 안 잠금).
  재생 비교(DB 쓰기 없음): 최대 묶음 133→28 · 발송 135→36. 정확도는 사람 블라인드 20쌍에서 묶음 안 같은 문제 5/12(42%) — 로컬 Qwen 자기 채점(76.3%)은
  크게 부풀었다. 확인 프롬프트에 "다른 문제"의 예(분야만 같음·문의 종류만 같음·증상 다름·발생 조건 다름·내용 없는 참조 메일)를 명시해
  1차 20쌍 일치 12→16/20, 새로 뽑은 2차 20쌍은 묶음 안 12/12 같은 문제·갈린 후보 8쌍 중 3쌍 놓침. 검증 데이터 92%가 한 주제라 다른 주제는
  미확인 — 숫자는 사람 검수로만 판단한다. 칸 변경 없음.
- v2.203 (2026-11-03, dev_0): **기법 대장 = 흐름도 + 켜고 끄기**(태훈 "흐름도상 어떤 단계에서 쓰이는 기법인지 · 필수는 필수 · 끄고 켤 수 있는 건 끄고 켜게").
  품질 › 평가 게이트 ③ 기법 대장: 흐름(채팅 7단계·적재 5단계·평가) → 단계 칸마다 기법(필수 🔒 / 적용 중 / 미적용 + 사유) → 누르면 하는 일·기준값·근거
  (`TechniqueFlow.tsx`, 대장 `service/eval/techniques.py` FLOW_OF). 실행 중 스위치 4개(`core/technique_flags.py`, ops_system_config `technique.<key>`, 관리자만,
  바꾸면 답변 캐시 비움): 관련 정책 연결 검색 · 문맥 예산 · 참고표 연결 · 다른 컬렉션 제안(옛 설정 줄 chat.collection_suggest 그대로). 기본값 = 지금 동작.
  채팅 인자 기본 = `agent.CHAT_DEFAULT`(스위치 반영), 측정 스크립트가 직접 준 --scope/--context-budget는 스위치와 무관. 대장에 빠졌던 4개 추가
  (관련 정책 연결 검색 · 문맥 예산 · 허용 호스트 검사 · 검수 후 게시).
  검증된 최적(`technique_flags.BASELINE` + 근거 게이트 #71·#75): 흐름도 맨 위 "최적 상태" 표시, 벗어나면 주황 띠 + [최적으로 되돌리기], 최적에서 벗어나는 스위치는
  기법별 손실 경고(예: 관련 정책 연결 검색 끄면 정책 질문 운영 지식 0%). 필수 기법은 서버가 403으로 막음. 측정 스크립트도 스위치를 읽는다(`init_pool`).
- v2.202 (2026-11-03, dev_0): **팀 적재 = 검수 후 게시**(11/03 결정 가). 적재하면 조각은 staging·작업 'held'(채팅에 안 나옴, 같은 페이지 옛 버전은 그대로 답에),
  소스 연결 › 배치 화면 "검수 대기" 카드(게시할 조각·중복 의심·품질 경고·검색 확인) → [게시] = 옛 버전 내림·원문 확정·활성화(`_switch_staged` — 등록 끝과 같은 코드를
  게시 시점 기준으로 다시)·캐시 비움·자동 용어 추출(게시자 기준, 롤백이 지움). 검색 확인 = 보류 조각 10개로 **로컬 LLM**이 질문 → 벡터 상위 3에 그 페이지
  (사내 LLM이면 건너뜀). 게시 중 표시로 두 번 누름·게시 중 롤백 막음, 검수 대기 배치가 있으면 그 공간 동기화 멈춤, 다른 배치의 검수 대기 페이지는 "이미 있음".
  단건 등록·이미 게시된 공간 동기화는 그대로 바로 반영.
- v2.201 (2026-11-03, dev_0): **정책 컬렉션의 공백·빠진 내용 = 정책 추가 제안**(태훈 "정책은 운영 지식과 성향이 달라 정책 등록·정정으로").
  통계 › 지식 공백(정책 컬렉션): [지식 등록] 대신 [정책 추가 제안](질문이 정책 이름에, 올리면 그 공백은 메움 — `ops_query_log.resolved_correction_id` #111, 반려되면 다시 공백).
  정정 요청 › 빠진 내용(정책 컬렉션): [승인] 대신 [정책 추가 제안으로] — `POST /api/corrections/{id}/to-policy-add`(`policy_change.forward_missing`, 한 트랜잭션:
  추가 제안 1건 + 원래 신고 처리됨·`ai_verdict.forwarded_to`, 원래 질문 이어받음). 일괄 승인에서 빠짐, 신고자 배너·카드 "정책 추가 제안 #N으로 넘김".
  시스템 컬렉션은 그대로(운영 지식 등록·승인).
- v2.200 (2026-11-03, dev_0): **운영 전 필수(외부 컨설팅 관점 리뷰) — 보안 4 · 성능 3 · 설계 2 · 시스템 먼저.**
  보안: 운영 프로필(`APP_ENV=production`)에서 기본·짧은 JWT 시크릿·관리자/DB 비밀번호면 기동 중단, /docs 닫음, prod `IMAGE_TAG` 필수 ·
  마크다운 rehype-raw 뒤 rehype-sanitize(`utils/markdown.ts`) + nginx CSP·nosniff·referrer · 수집 클라이언트는 전부 `web_crawler.guarded_client`
  (요청·리다이렉트마다 허용 호스트 `URL_IMPORT_ALLOWED_HOSTS`, 응답 20MB 상한 — SSRF·PAT 유출) · 5432·8000은 127.0.0.1에만.
  성능: 키워드 검색 저장 칸 `content_tsv` + GIN(#109, 2.5초 → 1.7ms, 검색 101/106 그대로) · 답변 캐시 비우기가 관련 정책 연결(+하위)까지,
  단건 등록·수정·삭제·중복 처리·묶음 삭제도 비움 · /health = DB 확인(안 되면 503) + LLM 1분 캐시·토큰 재사용(토큰 발급 5,299회 → 기동 1회) ·
  compose 헬스체크·로그 회전 · gzip(번들 2MB → 0.7MB) · 검색 top_k ≤ 50.
  설계: 살아 있는 내용(운영 지식·원문·정책·참고표·용어·검토 중 정정)이 있으면 컬렉션 삭제 409(이력은 막지 않음, 관리자 `?purge=true`는 시험 정리용) ·
  마이그레이션 목록 `_MIGRATIONS` + advisory lock + 기동 원장 `schema_migration_run`(#110). 백업→복원 한 바퀴 확인(지식 3,262 그대로).
  화면: 컬렉션 고르는 창 [시스템 | 정책], 기본 시스템 탭, 처음 기본 컬렉션 = 내 담당 시스템. 반입 조건: `docs/operations/deployment-closed-network.md` §3-8.
- v2.180 ~ v2.199(10/27~11/03: 자가 개선 루프·게이트 두 축·VOC 분리·정책/시스템 컬렉션·운영 전 필수 리뷰) 이력은 `docs/archive/architecture-history-v2.180-v2.199.md`.
- v2.171 ~ v2.179(10/26~27: 틀린 문항 리포트·적재 검수·C2/C3·품질 화면 가시성) 이력은 `docs/archive/architecture-history-v2.171-v2.179.md`.
- v2.161 ~ v2.170(10/23) 이력은 `docs/archive/architecture-history-v2.161-v2.170.md`, v2.136 ~ v2.160(10/13 ~ 10/22)은 `docs/archive/architecture-history-v2.136-v2.160.md`, 그 이전은 `docs/archive/`의 다른 이력 파일.

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
| **Chat** (`/`) | 에이전트별 채팅 — SSE 스트리밍, 근거 카드, "답변 틀림" 신고(정정 검토로), 대화 메모리(요약+리콜), Markdown 답변 |
| **Admin** (`/admin`) | 에이전트별 관리 화면 — `agentScope` 필드로 탭 필터링. knowledge_rag: 기준정보·지식 베이스(정정 검토 포함)·VOC·용어집·평가 게이트·정책·캐시현황·통계. 공통: 시스템설정·사용자관리. (에이전트현황 탭 제거 — AgentSelect 화면에 헬스배지로 대체) |

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
│   │   │   └── web_crawler.py   #   URL/Confluence 수집 (httpx + BeautifulSoup + Confluence REST API)
├── service/             # 플랫폼 공통 레이어 (was domain/, platform/ 명칭 stdlib 충돌로 service/ 확정)
│   ├── auth/            #   인증/계정 (JWT, bcrypt, Fernet API Key 암호화)
│   ├── chat/            #   채팅 라우터·헬퍼·메모리 (AgentRegistry 위임)
│   ├── feedback/        #   "답변 틀림" 신고 접수 → 개선 원장 (v2.121부터 가중치·질의 상태 안 바꿈)
│   ├── improvement/     #   개선 원장(정정 검토) — 신고·AI 판정/수정안(draft.py)·승인 시 버전 교체 (v2.118/120)
│   ├── refdata/         #   공통코드·DB 컬럼 참조 데이터 검색
│   ├── admin/           #   네임스페이스·통계·LLM 설정
│   ├── prompt/          #   프롬프트 관리 (get_prompt: DB 우선, fallback)
│   ├── llm/             #   LLM Provider 추상화 (ollama / inhouse)
│   ├── email_voc/       #   VOC 이메일 분석 채널 (v2.40 신규)
│   │   ├── graph_client.py    #   Microsoft Graph API 클라이언트 (msal 토큰 발급, 메일 조회, 페이지네이션/재시도)
│   │   ├── service.py         #   check_relevance()(관련지식 사전 필터, v2.41) + analyze_email() — 기존 RAG 파이프라인 재사용 분류/심각도/오배치 판정 + issue_signature(정규화 이슈요약) 출력 (v2.49)
│   │   ├── pattern_detection.py #   반복 VOC 클러스터링(issue_signature 임베딩으로 후보 → 대표 고정 · LLM 같은 문제 확인 후 합류, 11/03) + 커버리지 LLM 검증 게이트(DB/공통코드 카테고리 제외, v2.49) (v2.47 신규)
│   │   ├── pipeline.py        #   수집→관련지식필터→분석→반복패턴탐지→중복제거→알림 오케스트레이션, 이력 조회
│   │   ├── routing_service.py #   파트별 메일함 라우팅 CRUD, 폴링 설정(관련지식 임계치 포함), Graph 자격증명(Fernet 암호화) CRUD
│   │   ├── delegated_auth.py  #   Delegated Permission 로그인 상태 관리 (Authorization Code Flow/PKCE, v2.41 신규·v2.42 인증방식 교체)
│   │   ├── teams_notify.py    #   Teams Workflows 웹훅 발송
│   │   ├── scheduler.py       #   백그라운드 폴링 루프 (asyncio.create_task, lifespan 등록)
│   │   ├── retention.py       #   30일 고정 보관정책 자동 정리
│   │   ├── schemas.py         #   Pydantic 스키마
│   │   └── router.py          #   /api/email-voc/* 엔드포인트
│   └── policy/           #   정책서(v0 원문 통째, docs/domains/policy/policy-v0-design.md) — 옛 엑셀 분해 파이프라인은 v2.150에서 삭제(docs/archive/policy-doc-pipeline-plan.md)
│       ├── v0_convert.py      #   팀원 구조화 데이터 → 저장 단위 변환(LLM 없음·결정적, 적재는 scripts/load_policy_v0.py)
│       ├── service.py         #   서술 조각 머리 붙인 임베딩(chunk_embed_text) + 기동 때 재임베딩
│       ├── visibility.py      #   검색 대상 조건(searchable_sql) + 정책을 겨눈 대기 중 정정 조회(open_correction_sql) — 잎 모듈
│       ├── catalog.py         #   정책서 화면 조회(목록·상세·버전·참고표·개요, 읽기 전용 — 내용 변경은 정정 원장 제안으로만)
│       ├── caches.py          #   정책이 바뀐 뒤 비울 캐시 한 곳(답변 캐시 전 영역 + 키워드·분류 색인)
│       ├── search.py          #   파라미터(RDB tsquery)+서술(벡터·BM25 RRF) 검색 — 채팅(agent.py)과 /api/policy/search가 같이 사용
│       ├── keyword_index.py · category_list.py · scope.py  #   키워드 색인(BM25) · 탐색형 분류 목록 · 채팅의 정책 범위(영역 넘어 찾기)
│       ├── query_type.py · korean_text.py  #   navigation형 질문 판별 · 한국어 조사/어미 제거
│       ├── track2.py · answer_eval.py  #   검색 비교 실행 · 답변 정확도 이력
│       ├── schemas.py         #   Pydantic 스키마
│       └── router.py          #   GET /api/policy/policies(/{logical_id}), /ref-tables(/{id}), /overviews, /search, /track2/*, /answer-eval/history, /pipeline-stats
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
- **네임스페이스 소유 파트 기반 권한**: 네임스페이스의 `owner_part`와 동일한 부서 구성원만 해당 네임스페이스의 데이터 CRUD 가능, 타 부서는 읽기 전용. `owner_part` NULL(담당 미지정)이면 **관리자만** 수정(v2.142 — 예전엔 누구나). 업무 영역 만들기·이름·삭제는 관리자만, 새 영역은 담당 미지정으로 생김. Admin은 모든 권한 보유
- **수정 시 작성자 갱신**: 지식/용어 수정 시 `created_by_part`/`created_by_user_id`가 최종 수정자로 갱신됨
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
| | `check_namespace_ownership` — 업무 영역 수정 권한(규칙 본체 `edit_denied_reason`), `check_namespace_read` — 관리 화면 조회 권한 |
| `service/auth/service.py` | 회원가입(조회 전용으로 시작, bcrypt, Fernet API Key 암호화), 로그인, 토큰 갱신, 팀·파트·사용자 관리 |
| `service/auth/router.py` | `/api/auth/*` 엔드포인트 |

**조직·업무 영역·권한 (v2.142):**
```
사람 축  ops_team(팀) ─ ops_part(파트: 소속·권한만, 데이터에 이름을 남기지 않음) ─ ops_user(role admin/user/viewer)
지식 축  ops_namespace(업무 영역) — kind domain / policy_test(E2E), parent_id는 트리 자리(지금 비움, P2 하위 업무)
         정책·지식 문서·용어·시스템 정보가 모두 namespace_id로 한 영역에 소속
연결     ops_namespace.owner_part_id(담당 파트) — 관리자 API(PATCH /api/namespaces/{name}/settings)·스크립트, 화면은 조직 관리 메뉴(설계 검수 중)
```
| 동작 | 규칙 |
|---|---|
| 데이터 수정(지식·용어·업무구분·정책 검토·VOC) | 관리자 / 담당 파트 사람(viewer 제외) / 담당 없는 영역은 관리자만. 프론트 `utils/domainAccess.ts`가 같은 규칙 |
| 지식 조회(지식·정책·용어·참고 데이터) | 로그인 사용자 누구나 — 다른 파트 담당 영역도(v2.144, `check_knowledge_read`). 화면은 수정 버튼만 숨김 |
| 사람 데이터 조회(통계·질의 기록·VOC 메일) | viewer 불가, 담당 없는 영역은 누구나, 담당이 있으면 그 파트·관리자(`check_namespace_read`) |
| 업무 영역 만들기·이름·삭제·담당 지정 | 관리자만. 삭제 시 글자 키 연결(ops_link)도 정리 |
| 가입 | 조회 전용(viewer)으로 시작 → 관리자가 사용자 관리에서 승인. 화면은 앱 열 때·창 포커스 때 `/auth/me`로 역할·소속을 다시 읽음 |
| 채팅 | 로그인 사용자 누구나(다른 파트 영역 조회는 막지 않고 기록만 — `log_cross_part_namespace_read`) |
- 대화 소유권: `ops_conversation.user_id` FK로 사용자별 대화 격리.
- 입력 검증: 이름 칸은 `service/validation.py` 공통 타입(앞뒤 공백 제거·길이·'/' 금지). DB 값 오류·중복·연결 경쟁은 공통 처리기가 400/409 + 한국어 사유(로그 남김),
  요청 검증 오류(422)도 한국어 문구.

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
ops_improvement_item  -- 개선 원장(정정 검토): 답변 틀림·근거 정정·빠진 내용·검색 노이즈 + AI 수정안, 승인 전 검색 미노출 (v2.118/120)
ops_query_log         -- 질의 로그 (status: pending=답변 / no_knowledge=지식 공백 / system_error=LLM 연결 실패(통계 밖),
                      --   공백을 메우면 resolved_knowledge_id — v2.121)
ops_prompt            -- 프롬프트 관리 (agent_type별 에이전트 스코핑, Admin 시스템설정 탭에서 편집)
ops_system_config     -- 시스템 설정 key-value (캐시 임계값/TTL 등 영속화, VOC 폴링 정책/Graph 자격증명도 여기 저장)

-- VOC 이메일 분석 채널 전용 (v2.40 신규)
ops_voc_routing       -- 파트별 담당 메일함 ↔ Teams 웹훅 ↔ 온콜 연락처 매핑
ops_email_analysis    -- 이메일 건별 분석 결과 (source_message_id UNIQUE로 중복 수집 방지, 30일 보관, v2.47: embedding/voc_cluster_id FK 추가 — 벡터는 1024차원)
ops_email_poll_cycle  -- 폴링 사이클(스케줄러 실행 회차)별 성공/실패 이력 (30일 보관)
ops_voc_cluster       -- 반복 VOC 클러스터 (representative_embedding=대표(만들 때 고정, 11/03), member_count, coverage_knowledge_id/coverage_verified — 해결방안 LLM 검증 캐시) (v2.47 신규)

-- KnowledgeRAG 전용 (rag_* prefix, v2.8에서 ops_*→rag_* 변경)
rag_knowledge         -- 지식 베이스 (HNSW + GIN FTS, base_weight(v2.121부터 입력 없음·전부 1.0), source_file/chunk_idx 추적,
                      --   status: active/pending_review/rejected/deleted(v2.68 소프트삭제),
                      --   logical_document_id/version/supersedes_id: 근거 정정 승인 시 버전 교체에 사용(v2.118))
rag_knowledge_duplicate_match -- 중복탐지 매칭 후보 (v2.34)
rag_knowledge_history -- 병합(merge) 시 덮어써지기 전 content/embedding 보존 (v2.68)
rag_knowledge_category -- 카테고리 목록
rag_glossary          -- 용어집 (질문에 글자로 나온 용어만 매칭 — v2.128, 동의어는 rag_glossary_synonym)
rag_conv_summary      -- 대화 요약 (embedding VECTOR(1024), Semantic Recall용)
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

- **비상용**(v2.143): `.env LLM_PROVIDER=ollama` 또는 API로만 — 관리자 설정 화면 선택지는 사내 LLM / 로컬 LLM(OpenAI 호환)
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

## API 엔드포인트

손으로 관리하던 엔드포인트 목록은 제거했다(2026-10-02) — 실제와 계속 어긋났다(없어진 `/api/feedback` 가중치 조정,
`/api/stats` 해결 처리 등이 남아 있었음). 현재 스펙은 FastAPI 자동 문서 `http://<backend>:8000/docs`가 정확하다.

## LLM Provider 확장 구조

```python
# service/llm/base.py
def build_messages(context, question, history=None) -> list[dict]:
    # [system: 시스템프롬프트+참고문서] + [history...] + [user: 질문]

class LLMProvider(ABC):
    async def generate(context, question, history=None, api_key=None) -> str: ...
    async def generate_stream(context, question, history=None, api_key=None) -> AsyncIterator[str]: ...
    async def health_check() -> bool: ...

# 현재 구현체
OllamaProvider     # LLM_PROVIDER=ollama — /api/chat (messages 배열, multi-turn) — 비상용
OpenAICompatProvider # LLM_PROVIDER=openai_compat — 로컬 LLM(vLLM 등) /v1/chat/completions, 생각 과정 걸러냄, 모델 자동 감지
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
| `OPENAI_COMPAT_BASE_URL` | (없음) | 로컬 LLM(OpenAI 호환) 주소 — 비면 화면에서 고를 수 없음(운영 .env엔 없음) |
| `OPENAI_COMPAT_MODEL` | (없음) | 비우면 서버의 첫 모델 자동 사용 |
| `OPENAI_COMPAT_TIMEOUT` | `300` | 응답 대기 시간(초) |
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
| `rag_knowledge` | `embedding VECTOR(1024)` | 문서 내용 임베딩 → 질문과 코사인 유사도로 관련 문서 검색 |
| `rag_glossary` | `embedding VECTOR(1024)` | 용어집 화면의 벡터 검색·동의어 품질 게이트용(v2.128부터 채팅 용어 매칭은 글자 매칭, `GLOSSARY_MATCH_MODE=embedding`이면 예전 방식) |
| `rag_conv_summary` | `embedding VECTOR(1024)` | 과거 대화 요약 임베딩 → 현재 질문과 유사한 과거 맥락 Semantic Recall (유사도 0.45 이상) |
| `policy_chunk` · `ops_email_analysis` · `ops_voc_cluster` | `VECTOR(1024)` | 정책 서술 검색 · VOC 메일 유사도/클러스터 |

### 검색 점수 공식

```
final_score = (w_vec × v_score + w_kw × k_score) × (1 + base_weight)
               └벡터 유사도    └BM25 키워드 점수    └문서 자체 가중치
```

- **v_score**: 코사인 유사도 (0~1) — HNSW 인덱스로 근사 탐색
- **k_score**: `ts_rank` BM25 점수 — GIN 인덱스로 전문 검색
- **base_weight**: 문서별 가중치 — v2.121부터 입력 경로가 없고 전부 1.0(상수). 채팅 채택 게이트는 이 값을 걷어낸 원점수로 판단.
  식에서 빼려면 메일 VOC 인용 임계값 재측정이 먼저(컬럼 삭제 때 같이)

### 비벡터 주요 테이블

`ops_part`, `ops_user`, `ops_namespace`, `rag_knowledge_category`, `ops_improvement_item`, `ops_query_log`, `ops_conversation`, `ops_message`, `ops_prompt`, `ops_system_config`

전체 스키마 정의는 `docs/table-definition.md` 참조.
