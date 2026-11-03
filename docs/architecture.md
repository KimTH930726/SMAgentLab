# OpsLens 시스템 아키텍처 (v2.199)

## 개요

OpsLens(구 Ops-Navigator, 2026-10-06 이름 변경)는 정책서·매뉴얼·운영 지식·공통코드처럼 흩어진 IT 운영 지식을 한곳에
모아 근거와 함께 답하는 **IT 운영 지식 통합 AI 플랫폼**이다. 틀린 답은 사용자 신고 → 담당자 검토로 바로잡는다.
사용자는 에이전트를 선택해 목적에 맞는 AI를 사용한다: 지식 기반 Q&A(KnowledgeRAG).

> Text-to-SQL 에이전트는 v2.51에서 `dev_0`/`main`에서 분리·제거됐다(현재 과업 범위 아님) — 코드는
> `archive/with-text2sql` 브랜치(2026-09-03 시점 스냅샷)에 형상관리용으로 보존돼 있다.
> MCP 도구 에이전트는 v2.67에서 완전 제거됐다(Text2SQL과 달리 브랜치 보존 없음 — 배경은
> `docs/archive/architecture-history-v2.0-v2.99.md` v2.67 항목).

**주요 이력 요약** (스키마 변경 상세는 `table-definition.md` §20 마이그레이션 이력 참조)
- (브랜치 voc-cluster-fix, 2026-11-03, 병합 시 버전 부여): **VOC 반복 묶음 — 대표 고정 + LLM 같은 문제 확인 · 확인 필요 표시.**
  ① 묶음 대표(`ops_voc_cluster.representative_embedding`)는 만들 때 고정 — 합류마다 옮기던 점증 갱신이 대표를 끌어 사슬 묶음(최대 130건)을 만들었다.
  ② 임베딩은 후보 찾기만, 합류는 LLM이 기준 건(첫 멤버)과 같은 문제라고 할 때만(`email_pattern_llm_confirm`, 기본 켬, 건당 최대 2회·실패 = 합류 안 함).
  ③ `email_pattern_review_size`(기본 20, 0 = 끔): 묶음이 N건 넘으면 팀즈 카드에 "확인 필요". ④ 같은 영역 묶기는 한 번에 하나씩(프로세스 안 잠금).
  재생 비교(DB 쓰기 없음): 최대 묶음 133→28 · 발송 135→36. 정확도는 사람 블라인드 20쌍에서 묶음 안 같은 문제 5/12(42%) — 로컬 Qwen 자기 채점(76.3%)은
  크게 부풀었다. 확인 프롬프트에 "다른 문제"의 예(분야만 같음·문의 종류만 같음·증상 다름·발생 조건 다름·내용 없는 참조 메일)를 명시해
  1차 20쌍 일치 12→16/20, 새로 뽑은 2차 20쌍은 묶음 안 12/12 같은 문제·갈린 후보 8쌍 중 3쌍 놓침. 검증 데이터 92%가 한 주제라 다른 주제는
  미확인 — 숫자는 사람 검수로만 판단한다. 칸 변경 없음.
- v2.199 (2026-11-03, dev_0): **운영 지식 쓰기 서버 막기 · 정정은 근거 주인에게(M2) · 컬렉션 삭제 빈 자리 정리 · pipeline-stats 삭제.**
  ① 정책 컬렉션엔 운영 지식 금지를 서버에서: 등록 API 400(`core/dependencies.reject_knowledge_outside_system`), 서비스 길목
  (`core/database.resolve_knowledge_namespace_id` — create·bulk), 소스 연결 동기화·팀 적재는 초안 전에 건너뜀, 정정 "빠진 내용" 승인 막음.
  예전엔 화면·위치 판단만 막아 API·동기화로 들어갔다(E2E 잔재 6건 정리). ② 정정 라우팅: 답변 근거가 관련 정책 연결로 찾은 다른 컬렉션 것이어도 후보로
  받고(`_IN_REACH_2`), 신고는 그 근거의 컬렉션 담당에게(`namespace_id` = 처리할 곳, `asked_namespace_id` = 물은 곳, #108). 승인은 처리할 곳 범위로.
  ③ 키 정리 5단계만: 컬렉션 삭제가 FK 없는 id 칸·연결·설정 줄을 같이 정리(`release_namespace_refs`), 기존 161건 #107. 3(캐시)·4(골든셋)·6(같은 이름)은 이득 없어 안 함.
  ④ 관련 정책 변경 시 답변 캐시 비움 · 연결 조회 실패해도 채팅은 고른 컬렉션만으로 · `/api/policy/pipeline-stats`(화면 없음) 삭제.
  테스트: 실 Postgres 경우의 수(`tests/test_correction_routing.py`, 트랜잭션 → 되돌림), 정책 컬렉션 거부·직접 INSERT 경로 고정(`test_knowledge_system_only.py`).
- v2.198 (2026-11-02 밤, dev_0): **운영 지식은 시스템에만(D 이동) · 채팅 검색 범위 linked + 문맥 예산 1만 2천 자 · 컬렉션 키 전환 1~2단계.**
  ① 정책 컬렉션 안 운영 지식 → 시스템(`scripts/move_knowledge_to_system.py`, 되돌리기 기록): 딜리버스 → 딜리버스 시스템(활성 39) · 제휴서비스 → 외부서비스 시스템(63) ·
  온라인스토어(적재 페이지 넣은 곳 302 · 배치 기본 위치 1). 원문·조각 같은 컬렉션, 행 수 보존.
  ② 채팅 기본: `agent.CHAT_KNOWLEDGE_SCOPE="linked"` · `CHAT_CONTEXT_BUDGET=12000` — 게이트 #71(업무지식 73.6→77.4%, 단정 오답 26.4→18.9%) · #72(정책서 84.9→86.0%, 12.0→11.5%).
  ①과 한 묶음(옮긴 뒤 linked 없이 정책에서 물으면 운영 지식 검색 0%). 측정 스크립트는 플래그 없음 = 채팅과 같음(`--scope none` · `--context-budget 0` = 끔).
  ③ 키 전환(domains/knowledge/collection-key-plan.md): 1단계 `core/collection_ref.py` 미들웨어 — API가 이름·업무 코드·id 모두 받음(쿼리·경로·JSON 본문의 namespace, 이름은 DB 안 봄).
  2단계 고른 컬렉션 기억 = {id, name}(이름이 바뀌어도 따라감, 옛 값 읽음). 화면 API 호출을 id로 바꾸는 건 3단계(캐시 키)와 함께.
- v2.197 (2026-11-02 밤, dev_0): **정책 / 시스템 컬렉션 — 조직 컬렉션 폐지 · 관련 정책 · 업무 코드 · 운영 지식은 시스템에만.** 기준: domains/knowledge/taxonomy-standard.md.
  ① #105 `ops_namespace.area`(policy | system — 시스템은 최상위만) · `code`(POL-xxx / SYS-xxx, 불변·유일) · 팀 적재 원문 초안 보관 자리 `stage:import`(kind staging, 목록·검색·쓰기 안 됨).
  시스템 ↔ 관련 정책 N:N(ops_link `supports`, `PUT /api/namespaces/{시스템}/supports`). 시스템 화면 이름은 키에서 " 시스템"을 뗀 것.
  ② #106 조직 컬렉션(kind org_space) 폐지 — 기동 백필(#83) 제거, 내용 없는 행 삭제(있으면 남기고 경고 + 쓰기 막음, 팀 삭제는 409), 담당은 파트 → 컬렉션 연결만(팀 공통 담당 없음).
  ③ 위치 판단 v3: 후보 = 시스템 컬렉션만, 종류 "대상 아님"(IT 운영 지식 아님 → 소스 연결이 묶음 제외 제안). 정책은 정책 컬렉션에만(시스템엔 400).
  ④ 화면: 채팅 [정책 | 시스템], 컬렉션 › 정책 컬렉션 · 시스템 컬렉션(관련 정책 · 코드 · 이름 수정·삭제 공용 창), 정책 줄 "관련 시스템 없음" 경고·[지식 등록 →] 없음,
  용어 지식 문서 → 운영 지식 · 데이터 소스 → 소스 연결 · 담당 업무 → 담당 컬렉션.
  ⑤ 측정 옵션(채팅 기본값 그대로): `knowledge_scope` linked(관련 정책 연결 양방향) · `context_budget`(한 줄 순위 + 문맥 예산), 측정 스크립트 기본 `--llm local`.
  CX 질문 93개(회원에서 묻기) 답 0 → 83.3%(게이트 #67), 업무지식 106 검색 A = linked 95.3%, 지식 질문에서 정책 빼기 #65 통과(정답 77.6→83.3%).
  안 하는 것: 새 kind 추가(시스템은 domain의 구분값) · "공통" 구분 · 자동 컬렉션 생성 · 검색 범위 similar(효과 없음). 키 이름 → id·코드 전환은 domains/knowledge/collection-key-plan.md.
- v2.196 (2026-11-02, dev_0): **사용자 피드백 — 이미지 첨부 · 오류 창에서 바로 신고 · 진행 중 + 개발 요청서.**
  ① 첨부 이미지(#104 `ops_user_feedback_file`, DB bytea — 폐쇄망에 저장 폴더 따로 안 둠): Ctrl+V 화면 캡처 붙여넣기 · 끌어 놓기 · 파일 선택, 최대 3장·장당 5MB,
  이미지 판정은 앞 바이트(PNG·JPEG·GIF·WEBP)로, 보기는 보낸 사람·관리자만(`GET /api/user-feedback/{id}/files/{file_id}`, nosniff). 제출은 multipart(글+이미지 한 트랜잭션).
  ② 오류 창 [오류 신고]: `showError`(앱 오류 모달) 전부 + 채팅 장애 답(LLM 연결 실패·빈 응답·처리 오류 — [정정 요청] 대신) → 같은 피드백 창이
  오류 문맥(문구·요청 "PATCH /api/..."·상태 코드, `ApiError.path`)을 붙여 열림, 글은 선택. 서버는 정한 칸·길이만 저장(`clean_context`). 창은 앱에 하나(`FeedbackHost`, `useFeedbackStore`).
  ③ 상태 **진행 중**(in_progress): 선정 → 진행 중 → 완료. 진행 중일 때만 [개발 요청서] — 폐쇄망 운영에서는 개발을 못 하니 로컬 CLI에 붙여 넣을 사실 묶음
  (글·화면·대화·오류 문맥·이미지 장수·관리자 메모, 이미지·고객 정보는 안 담음, 절차는 저장소 CLAUDE.md가 정함). http에서 클립보드가 막히면 글을 골라 두고 Ctrl+C 안내.
  ④ 품질 › 사용자 피드백 메뉴를 품질 맨 아래로(사용자). 안 하는 것: 화면 자동 캡처(붙여넣기로 충분, 민감 화면이 그대로 찍힘) · 운영↔로컬 처리 결과 자동 동기화(망 분리).
- v2.195 (2026-10-30 밤, dev_0): **개선 후보 상태값·한 목록 · 사용자 피드백 채널 · 답 뒤 근거 확인 실험.**
  ① 개선 후보 화면 단계(`improve.phase_of`, 서버가 정함): 제안 · 실험 중 · 실험 오류 · 검토 대기(게이트 통과 — 반영할지 사람) · 반영 · 실패 · 기각 · 보류
  (사용자 "실험이 끝났는데 '실험 중'?"). 상태 **failed** 추가(#102) — 실험이 끝났는데 운영 반영 불가(게이트 실패·단정 오답 증가 등)면 실행기가 자동 종결(사유 자동,
  결정자 없음, 기동 때 남은 것도 정리), [다시 실행]은 남음, 다음 진단이 이어받음. ② 평가 게이트 ② **개선 후보** = 한 목록 + 상태 칩(`/api/eval/improvements/board` —
  최근 진단 후보 + 그 전 진단에서 종결된 반영·실패·기각), 기본 [진행 중] = 맨 위 "결정할 후보"와 같은 범위(검토 대기가 맨 위), 종결 줄은 측정 번호·전/후·날짜 한 줄.
  ③ 적용 이력 → **기법 대장**(후보 결정은 ②의 필터로, 기법마다 기준값·근거 — 반영·실패 측정이 근거 줄로 자동, 손으로 안 적음).
  ④ **사용자 피드백**(#103 `ops_user_feedback`, `service/user_feedback`): 사이드바 [피드백 보내기] = 오류 신고 · 기능 제안(Jira Service Management 요청 유형), 화면·대화 번호 자동,
  답 내용 오류는 기존 [정정 요청]으로 안내. 품질 › 사용자 피드백: 상태 신규 · 선정 · 완료 · 중복 · 기각(사유 필수) + **자동 감지**(최근 14일 채팅 장애 = 질의 기록
  system_error를 게이트웨이 거부·LLM 연결 실패·처리 오류로, 원문 없음). 보낸 사람은 [내 피드백]에서 상태·답변(새 결과 점, Canny 방식). 개선 후보와 한 목록 합치기는 오픈 뒤.
  ⑤ **답 뒤 근거 확인**(trustkit `answer/grounding.py`, 기법 `grounding_check`): 초안 답의 주장이 문맥에 있나 같은 모델이 한 번 더 — 없으면 빼고 핵심이 없으면 "못 찾음"
  (RAGAS faithfulness·Azure groundedness·CoVe 계열). 측정 옵션 `eval_knowledge_answers.py --verify` · 실험 change `{"verify": true}` · 후보 `answer_verify`.
  지시문은 시스템 자리, 문서는 답 생성과 같은 문맥 자리로(한 덩어리로 보내면 사내 게이트웨이 거부 2/5). **실험 #11 결과 실패**(단정 오답 17.2→21.9% · 정답 79.7→77.1% · unsupported 판정 0건) → 채팅 반영 안 함, 후보 #128 자동 종결.
- v2.194 (2026-10-30, dev_0): **오픈 기준 = 단정 오답 · 개선 후보 실험을 실제로 돌린다.**
  오픈 최소 기준 합의(백로그 0-0: 업무지식 정답 ≥80% · 단정 오답 ≤10% · 거절 ≤10%, 정책서는 현재 유지 — "틀린 단정보다 모른다가 낫다").
  ① 게이트 **맞바꿈**: 정답률이 떨어져도 단정 오답 감소 ≥ 잃은 정답 × 3 · 전체 정답률 ≥ 하한(80%·85%)이면 통과(`eval_gate.py --tradeoff-ratio --floor`, trustkit `abstain_tradeoff`),
  단정 오답 비율은 측정마다 출력·기록(`answers.confident_wrong`). ② **운영 반영 조건**: 답을 잰 측정이면 단정 오답이 늘지 않고 2회 이상(`improve.confident_wrong_error`),
  후보 모달에 **실험 결과** 칸(정답률·단정 오답·거절·잠근 층·검색 전/후 + 서버가 미리 본 "운영 반영 가능/불가"). ③ **실험 실행기**(`service/eval/experiment.py`, #99):
  후보가 기계 적용 변경(`change` — env · 지시문 한 줄 · 문맥 옵션)을 들고 있으면 [실험해 보기]가 대기열에 — 지금/바꾼 설정을 같은 날 재고(답은 2회) 게이트 판정을
  후보에 연결(기준선 안 옮김), 운영은 그대로. 진행은 카드에(단계·n/m), 실패는 이유 + [다시 돌리기], 바꿀 것이 글뿐이면 "자동 실험 불가 — 개발 작업".
  한 번에 하나(게이트웨이 한도), 재시작 때 돌던 작업은 실패로 닫음. 실측: 업무지식 단정 오답 17.9%(틀린 17 중 14는 검색 맞고 답에서 틀림 — 지어냄 7·섞음 6),
  지시문 두 줄 실험은 안 씀(19.8→24.0%), 문맥 = 지식 2.9조각 + 상위 섹션 보충 29.9 + 정책 서술 10(1.8만 자) → 문맥 좁히기 후보.
  ④ **수집기 = 묶음 단위 연결**(사용자 "길을 잃은 것 같다 → 업계 수집기처럼", Rovo·Glean·Copilot 커넥터): 페이지 단위 분류 검토·표본 검수·최종 검수를 없애고
  [적재 검토] = 묶음(1단계 상위 페이지)마다 컬렉션 하나 + 성격이 다른 하위 갈래(2단계) 예외 → [연결 · 적재] 한 번(`/connect` = 규칙 저장 `rag_import_rule` #100 + 전 페이지
  정함 → `/confirm`). 근거: GIFT 324건 중 285가 공간 기본 위치 하나로, 사람이 본 22건 중 15가 기본 위치, AI 조직 판단 37% — 분류 검수가 정확도를 정하는 곳이 아니었다.
  품질은 적재 뒤 평가 게이트·롤백(답 정확도). 탭·배치 버튼 줄임(배치 = 선택 상자, 상태로 검수/게이트 화면). 다음: 적재 뒤 게이트 자동 측정 · 하루 1회 변경 감지·권한 재확인.
  ⑤ 정정 요청 화면 정리 — 출처 → **채널** 칩(건수, 0건 숨김), 상태 탭 건수, 처리 버튼 카드 머리, 당시 답변 접음, AI 분석 행 = 요청 요지·판단 근거·불일치 항목·반영 결과.
  ⑥ 단일 에이전트 — 에이전트 선택 화면·사이드바 변경 버튼 걷어냄(로그아웃 때 대화 상태 초기화는 `resetSession`). ⑦ 채팅 근거 카드 접음("근거 N건 ▸", 정정 검토 중은 접혀도 표시,
  AI가 답을 못 냈을 때만 펼침). ⑧ 이름: [AI가 본 근거] → **[AI 분석]**, 시험지 → **골든셋**(사용자 지침 맨 위 "업계 표준 용어").
  ⑨ 수집기를 **지식 › 데이터 소스** 메뉴로(지식 문서 [적재 검토] 서브탭 걷어냄, 메뉴 배지 = 연결할 묶음 수) + **컨플 공간 동기화**(`ingestion/confluence_sync.py`, #101):
  하루 1회 증분(CQL lastmodified, 1시간 여유) · 주 1회 전체 대조(목록을 끝까지 받았을 때만, 사라짐이 30% 넘으면 내리지 않고 보고). 읽기 = 연결한 사람 개인 PAT(A안 —
  `ConfluenceSource` 공급자만 바꾸면 사내 API/MCP로). **이 연결로 들어온 페이지만** 고친다(다른 팀·개인 등록으로 들어온 같은 페이지는 건너뜀) · 쓰기 전 담당자의 컬렉션 권한
  검사 · 동기화가 넣은 조각·작업도 배치에 기록(배치 롤백이 같이 되돌리고, 남은 적재가 없으면 연결 멈춤) · 적재 전·적재 중·실험 중엔 안 돎 · 실패·충돌 페이지는 다음에 다시
  (충돌 = 사람이 고친 조각, 덮지 않고 화면 '확인 필요'에 제목). 자동 실행은 설정 `confluence_sync`(기본 꺼짐, dev만 켬 — 개인 토큰을 서버가 정기적으로 쓰는 것은 반입 전 보안 확인).
  독립 리뷰 2회(1차 높음 1: 남의 컬렉션 덮어쓰기 · 중간 6 반영). ⑩ [적재 검토] 묶음 근거 — AI 판단 분포 막대(같음·다른 업무·조직·판단 못 함) + 대표 페이지 5개,
  하위 갈래 예외 = AI가 묶음 값과 다르게 본 비율 > 40% · 3페이지 이상(실측 split_probe: 묶음 통째 추정 오분류 10.8% → 28칸 7.9%, 더 잘게는 효과 없음) — 값은 묶음 값으로
  채움(AI "다른 곳" 판단이 사람과 맞은 비율 31%).
- v2.193 (2026-10-29 밤, dev_0): **적재 검토 탭 · 결정 기록화 · 평가 게이트 두 축 같은 루프.**
  ① 적재 검사 "꺼 둠(결정)"을 코드(check_ingest.decided_off)에서 **개선 후보의 사람 결정(기각·보류 + 사유)**으로 — 설정을 켜면 다시 셈. 후보 #75 파라미터 파생·#78 개요
  기각 기록, 진단 #12(정책 #39)에서 파라미터 파생 사라짐(사용자 "하드코딩이니?"). ② 지식 문서 **[적재 검토]** 서브탭(할 일 수 = 분류 안 한 검토 필요 + 안 본 표본) —
  검토는 등록이 아니라서(사용자). 검토 필요는 대분류별로, 줄마다 AI 판단·이유 · 경로(대›중›소, #98 `path`) · 묶음 위치(공간 기본 위치로 미리 채움, [확정] 전엔 저장 안 됨) ·
  기본값/직접 바꿈/분류됨. 경로는 **AI 입력엔 안 넣음** — 실측(path_probe, 정답 44p×2회): 적재 컨플 정답 55→73%지만 GIFT 조직 문서 맞힘 13→4/14, 자동인데 틀림 1→10.
  ③ 진단·개선 후보를 **업무지식 축도**(`eval_improve.py --axis knowledge`, 진단 #13) — 후보·이어받기·가설·채택이 축마다 따로, 적재 검사 후보는 축별(지식 조각 = 업무지식),
  운영 번복(위치 판정) 후보는 업무지식 축. ④ **평가 게이트 한 템플릿**(축 = 선택값만): 맨 위 카드(전체 정답률·검색·사람이 쓴 질문(잠근 문항 있는 축만)·결정할 후보) →
  단계 줄(원인 줄과 같은 수, 적재는 기준 다름 표시) → ① 개선할 곳(원인별, 고칠 방법은 원인 안 · 잠근 문항은 각주) → ② 진행 중인 개선(사람이 정할 후보만) → ③ 적용 이력(접힘).
  옛 [틀린 문항]/[성적]/[적용 이력] 탭·"고칠 방법별"·머리 % 줄 중복 없앰(업계 조사: LangSmith·Braintrust·Patronus·Galileo — 실패는 원인 유형으로 묶고 고칠 방법은 그 안).
  리포트·문항 상세 저장 꼬리표는 정책서 축만. 독립 UX 검수 1회 + 코드 리뷰 2회(1차 8건: 업무지식 후보 측정 막힘(높음)·켠 기능의 옛 기각 이어받음 등, 2차 5건) 반영.
  **10/30 화면 검수 반영**: 적재 검토 — 묶음은 접힌 채로 시작, 묶음 위치 = **일괄 지정**(모든 줄이 따라감, 줄마다 다르면 "줄마다 다름"), [AI 판단으로](처음 AI 판단으로
  일괄 되돌리기), 펼친 목록에 "◀ AI 판단" 표시, AI 판단 칸 세 줄(꼬리표·고른 곳·이유 두 줄). 확정 뒤 새 값이 올 때까지 버튼 잠금(리뷰 M1: 그 틈에 기본값으로 보였다가 다시 눌리면 덮어씀).
  평가 게이트 — 원인 줄·② 진행 중인 개선·원인 안 개선·문항 상세 고칠 방법이 모두 **같은 후보 모달**(항목 줄: 무엇을 바꾸나·왜·어떻게 재나·AI 추천 → 단계 → 결정),
  [보기]로 내려간 곳 테두리 강조, 문항 상세 6칸 → 4칸(질문 · 정답/AI 답 대표 1회 · 왜 틀렸나 한 칸 · 고칠 방법). AI 감사 로그 [기준] 모달 = 예시 한 장면(가상 문장) +
  규칙 3줄(기법 대장 `example`·`rules`, 규칙 숫자는 지금 기준값으로 채움).
- v2.192 (2026-10-29, dev_0): **팀 적재를 등록 창구 안으로 + 공간 기본 위치.** 따로 메뉴(지식 › 팀 적재)를 없애고 지식 문서 › 등록 › URL/Confluence의
  세 갈래(이 페이지만 · 하위 골라 넣기 · **공간·하위 전체 넣기**)로 — 등록 창구는 하나(10/20 결정, 사용자 10/29 "당연히 합쳐야"). 배치 목록은 등록 탭 아래 "적재 배치".
  **공간 기본 위치**: 사람이 "이 공간 = 온라인스토어"를 한 번 — 업무 페이지는 거기로(사람 결정, 원장 assigned_by=space_default), AI는 예외만
  (조직 지식으로 보임 · 확신하는데 다른 곳 · 쓸 수 없는 곳). 표본은 (누가 정했나 × 곳)별로 따로(`sample_key`). 미리보기 중에도 정하기·바꾸기(저장된 AI 판정으로
  다시 분류, LLM 다시 안 부름, 사람이 손댄 페이지는 그대로). GIFT 배치 #3: 검토 필요 321 → **39**(조직으로 보임 36 · 기본 위치와 다름 3 — 이웃 쏠림으로 AI가
  딜리버스라 본 것) · 기본 위치 285 · 표본 10. 칸 추가 #98 안(`default_namespace_id`·`assigned_by`·`sample_ns`). 공간 기본값은 원장에 preset(사람 결정)으로 —
  AI 번복·수락 통계에서 빠짐. 독립 리뷰 1차 5건(중 1) · 재리뷰 3건(중 1: 포함 때 사람 분류를 덮던 결함) 반영, 1건 백로그.
- v2.191 (2026-10-29, dev_0): **팀 적재(컨플 수집기) — 팀·파트 컨플 공간을 통째로: 분류 미리보기 → 분류 확정·적재 → 게이트 · 배치 롤백(설계 `confluence-curation-layer-design.md` §12).**
  메뉴 지식 › 팀 적재. 주소(페이지 또는 공간 /display/KEY — 최상위가 여럿인 공간도) → 백그라운드로 페이지마다 읽기 제한 기록·분할·원장 초안(팀 공통 조직 컬렉션, draft)·
  "이미 적재됨" 확인·위치 판정 → **미리보기에선 아무것도 적재 안 함**. 업무 후보는 등록하는 팀 범위(팀 파트 담당 업무 + 담당 없는 업무, 다른 팀 업무 제외 — GIFT 실측: 이웃이
  영업시스템으로 샘). 자동 분류 = 지금 확신 기준(LLM = 이웃 5중 4)만, 나머지는 검토 필요(사유: 신뢰도 낮음·담당 미지정·AI 판정 실패) — 사람이 LLM이 고른 곳별 묶음으로
  일괄 분류(넣을 곳 미리 골라 두지 않음). 표본 검토(A2I 무작위 표본): 컬렉션마다 10건, 정정 나오면 목표 2배·필수, 팀 첫 적재는 필수 — 그 팀 바로 앞 배치가 정정 0이면 선택(B안).
  확정 = 페이지마다 원장 초안을 넣을 곳으로 옮겨 다시 분할·등록(페이지 = 작업 1개), 위치 판단 원장 기록, 자동 용어 컬렉션마다 1회. 롤백 = 그 배치 조각 soft delete·원문 교체됨·
  배치가 만든 자동 용어 삭제. 기동 때 멈춘 배치 닫기(`close_interrupted`). **기법 결정(실측)**: 묶음 다수 의견 자동 분류 기각(13건 중 틀림 3, 지금 기준 10건 중 0) ·
  "LLM 고른 곳에 지식 적으면 LLM 제안" 기각(정답 9건 중 5→3) · 검토 묶음 미리 고르기 없음. 실데이터: GIFT 공간 500페이지(상한) → 자동 3 · 검토 필요 427 · 내용 없음 70 —
  첫 적재는 이웃이 없어 거의 다 사람 몫(정확도 우선). **한계**: 적재 즉시 채팅에 쓰임(로컬, 실사용자 없음) — 운영 반입 전 "게이트 통과 뒤 게시"로(백로그 0-11).
  독립 리뷰 2회(1차 13건 · 재리뷰 6건 중 5건 반영, 1건 백로그). 마이그레이션 #98 `rag_import_batch`·`rag_import_page`.
  **같은 날 참고표 채팅 연결 켬**(설정 `policy_ref_chat` 기본 True, 태훈 결정): 게이트 통과·판정 기록 #39(정책서 기준선) — 답 82.3→85.8% · 참고표 문항 12.5→75.0% ·
  검색 나빠짐 0, 남은 부작용 챌린지-018 하나(백로그 0-12). 실서버 채팅으로 참고표 인용 확인.
- v2.190 (2026-10-29, dev_0): **컨플 수집기 보강 — 그림 속 글 살리기 · 읽기 제한 페이지 기록 · 적재 LLM 로컬(정제 레이어 ①, 설계 `docs/domains/knowledge/sources/confluence/confluence-curation-layer-design.md`).**
  예전엔 storage HTML의 그림·draw.io가 표시 없이 버려졌다(파일럿 진단: 첫 공간 내용 페이지의 66%에 draw.io, 그림 속 글이 본문의 119%). `ingestion/confluence_media.py` —
  draw.io는 같은 페이지 첨부의 원본 XML에서 도형·연결("구성: …", "흐름: A → B")을 **코드로** 뽑아 매크로 자리에(환각 없음, 원본을 못 받으면 "원문에서 확인" 표시),
  일반 그림은 "[이미지: 파일명 — 원문 페이지에서 확인]". 뽑은 글·페이지 정보(주소·space·수정 시각·작성자·라벨)는 원장 `media`(#97)에 — 다시 분할이 네트워크 없이 같은 결과(원장 시나리오 13/13).
  일괄 등록 요청 `diagram_text`(기본 켬)·`image_marks`(기본 끔 — 효과를 재기 전, 독립 검토 10/29: 스크린샷 많은 공간에서 끔/켬을 재서 정함). **측정(같은 22페이지 끔 → 켬, 실제 등록 경로)**: 검색 51.7 → 93.1%(그림 속 내용 문항 10/26 → 26/26) ·
  답 21.2 → 82.7%(좋아짐 32 · 나빠짐 0, 게이트 통과 — 리뷰 반영 최종 코드, 첫 측정 86.5%와는 오차 범위) · 기존 지식 축 106문항 문항별 그대로. **읽기 제한**: 페이지 또는 상위 페이지(물려받음)에 읽기 제한이면 조각을 만들지 않고
  원장에 `restricted`로 번호·제목·주소만(지식 공백 감지용) — 화면은 "읽기 제한이라 건너뜀(기록만)". **적재 LLM**: 용어 자동 추출·동의어가 설정 `ingest_llm`(기본 local)을 따른다
  (넣을 곳 판단 `locator_llm`과 같은 방식, 주소 없으면 시스템 LLM + 경고). 로컬 vs Claude 용어 추출(정책서 5건): Claude 용어의 75%를 로컬도 뽑음 · 설명 틀림 4%.
  같이: 화면이 안 쓰는 옛 직접 등록 API 4개(`/import/csv`·`/import/text-split`·`/import/file`·`/import/url`)와 프론트 함수 삭제 — 등록은 미리보기 → 검수 → `/bulk` 하나로.
  지식 측정이 정답 = 페이지(`gold_pages`)인 문항을 지원(다시 적재해 조각이 바뀌어도 같은 문항으로) · 채점 기준 원문을 문항에 실을 수 있게(`ref`).
  독립 리뷰 반영: 단건 URL 경로도 같은 함수로(제한 검사·그림 처리 — M1) · 제한 정보가 안 오면 "모름" = 넣지 않음 + 로그(M2) · 이미 적재된 페이지가 나중에 제한되면
  그 페이지 조각을 승인 대기로 내림(M3) · 다시 적재되면 제한 기록 내림 · 문서 분석 LLM도 `ingest_llm` · 일괄 미리보기 동시 8페이지 · 글 라벨의 "<"·">" 유실·연결선 위 글 상자 위치·스스로 닫힌 매크로.
  실DB 시나리오(되돌림 트랜잭션) 4/4 — 보류·한 줄 기록(본문 없음)·다시 적재 시 기록 내림.
- v2.189 (2026-10-29, dev_0): **팀 간 용어 참고 한 줄(사용자 10/29 "정확도는 훼손하지 않되 정보는 전달 — 중복되는 게 있을 때만 간단하게 참고", 목업 검수 "좋은데").**
  질문에 나온 용어(이미 하는 용어집 글자 매칭)가 다른 업무 영역 용어집에도 있고 뜻(설명)이 다르면 답 아래 "다른 팀에서는 'X'을(를) 다른 뜻으로 써요" + 팀 뜻 최대 2개·"n개 팀 더".
  **답·검색·LLM 문맥엔 안 넣는다**(답이 끝난 뒤 용어집 조회만, LLM 0) — SSE `term_notes` + 메시지 `metadata.term_notes`(대화 기록에도), 캐시 답은 처음 답의 것.
  같은 용어 = `norm()` 하나로(SQL 정규식은 로케일·기호에서 갈라짐) · 설명이 같거나 비면 안 붙임 · 고른 영역 + 하위·이 답에 쓴 뜻의 파트는 뺌 · 업무 영역(kind domain)만
  (조직 컬렉션은 이름이 내부 키라 뺌 — 독립 리뷰 M1) · 1초 상한·실패하면 참고 없이(로그). 지금 데이터: 같은 용어·다른 설명 35개. 업계: 용어를 업무 영역(도메인) 안에 두고
  영역끼리 다른 뜻을 나란히(Purview 거버넌스 도메인 용어집). E2E `term-notes.spec.ts`(화면 = 서버 저장값 · 대화 다시 열어도 · 시험 대화·질의 기록 정리). 목업의 [용어집에서 보기]는 뺌
  (일반 사용자는 용어집 화면 권한이 없음 — 쓸 일이 보이면).
- v2.188 (2026-10-29, dev_0): **평가 게이트 지식(컨플) 축 — 운영 질의 감시 개발 1단계(사용자 10/29 "그 기준대로 개발 시작").**
  맨 위 [정책서 | 지식(컨플)], 판정 기록 `eval_gate_run.axis`(#96) — 기준선이 축마다 한 줄(`eval_gate.py --axis knowledge`, API `?axis=`). 지식 축은 [성적]만
  (단계 흐름·틀린 문항·적용 이력은 정책서 문항으로 진단한 자가 개선 루프라 정책서만 — 개선 후보 운영 반영도 정책서 측정만, 서버가 막음). 지식 기준선 #34(사용자 확정):
  검색 95.3%(101/106)·1순위 72.6% · 답 82.1%(95문항). **답을 못 낸 문항(오류·채점실패 = 게이트웨이 거부·연결 실패)은 정답률에서 빼고 따로**(trustkit `NOT_SCORED`,
  게이트·리포트·층별 문항·적재 검수·측정 스크립트 출력 같은 규칙, 회차 하나라도 못 냈으면 그 문항 전체) · **새로 답 못 낸 문항이 생기면 게이트 실패**(짝이 없어 비교에서 빠지므로 —
  "전엔 맞던 문항이 거부되는 변경"이 통과하던 구멍, 독립 리뷰 M1) · 결과 파일이 지식 측정인데 `--axis`가 다르면 입력 오류(리뷰 M2) — 거부 11건은 정책서 조각의 게이트웨이 오탐(대표 번호·"무기명"·회원 유형 용어, 로컬 LLM 확인 → 오탐 신고). 정책서 측정은
  지금까지 장애 0건이라 옛 숫자 그대로. 지식 측정 스크립트는 문항을 정답 지식의 **지금** 영역으로 묻는다(`scripts/knowledge_eval_set.py` — 영역 재구성 뒤 옛 이름으로 0%가 났던 일,
  옛 이름 파일로도 95.3% 재현). 지식 검색 측정에 1순위 칸 추가. 실DB: 지식 기준선을 API로 옮겨도 정책서 #12 그대로 · 마이그레이션 두 번(옛 상태 흉내 낸 복사본) 통과. 옛 이미지로 롤백 땐 지식 기준선을 먼저 내림(배포 문서 §3-3). 같이 고침: v2.187 trace 뒤 깨져 있던 채팅 테스트 2개(기대값에 trace 없음).
- v2.187 (2026-10-28, dev_0): **운영 질의 기록(trace) — 실사용 질의 진단의 1단계(사용자 "실사용 질의가 검색에서 top-k를 제대로 찾았나·답을 잘 만들었나를 알아야").**
  채팅 답(답·되묻기·근거 없음)마다 메시지 `metadata.trace`에 AI에게 넘긴 근거의 종류·id·순위·점수(정책 파라미터·서술 · 지식+채택 여부 · 참고표·개요),
  근거 판정 신호, 용어, 되묻기·근거 없음·목록 답 여부, 부모 섹션 보충 id·분류 목록 항목 id — **원문·검색어 글자 없음**(약 0.5~1KB). 목록 답이면 실제로 문맥에 실린(거른) 정책 기준.
  검색 직후 먼저 저장해 오류·사용자 중단으로 끝난 답에도 남는다(실측: 답 도중 끊은 메시지에 근거 13개 기록). 캐시 답은 `{"cached": true, "src": 처음 답한 메시지}`. 기록 실패는 답을 막지 않음(로그).
  2단계(LLM 없는 질의별 진단)·3단계(표본 채점: 질문 모호/검색/생성/정상, 로컬 LLM)·문제 질의 → 시험지는 다음(화면은 목업부터). 업계: 운영 기록 → 검토 큐 → 데이터셋(LangSmith 등).
- v2.186 (2026-10-28, dev_0): **용어 자동 추출 출처 표시(용어집 업계식 재구성 1단계, 설계 `docs/domains/knowledge/glossary/glossary-curation-design.md`).** 지식 등록 때 LLM이 뽑은 용어는
  `rag_glossary.source = 'auto_extract'`, 용어집 화면 "자동 추출" 표시 — 동작은 그대로, 재적재 날 자동 용어 포함/제외를 검색·답 둘 다 재서 결정(떨어뜨리면 끔 · 섞이면 후보 → 근거 → 자동 승격).
  실측: 두 문장 지식 1건에 용어 6개(시험 표지·버튼 이름 포함) + 동의어 LLM 6회. 컨플루언스 리서치에 MCP 게이트웨이 전환 메모.
- v2.185 (2026-10-28, dev_0): **데이터 보관 주기(사용자 "쌓이는 데이터 삭제 주기", "90은 짧다 — 180일") — `service/retention.py`, 하루 1회(기동 10분 뒤 첫 실행).**
  AI 판단 원장 180일(확정·미확정 모두 — 진단은 90일만 봄) · 지운 지식(status deleted) 지운 지 180일(updated_at 기준 — 사용 중 원문의 조각은 재등록 확인의 근거라 남김,
  새 버전·정정 원장·시험지가 가리켜도 남김) ·
  질의 기록 1년 · 채팅 대화 1년(#95 `chat_retention_days` 0 → 365 한 번만, 대화가 없는 날에도 하루 한 번). 미확정 판단 24시간·VOC 30일은 그대로. 정정 원장·정책 변경·측정 기록은 안 지움.
  줄마다 따로 실패(로그). 실DB 시나리오(오래된 시험 줄 심고 한 번 돌림) 8/8, 독립 리뷰 반영(사용 중 원문 조각 보호 · 미확정 되묻기 영구 보관 막음).
- v2.184 (2026-10-28, dev_0): **지식 등록 넣을 곳 판단을 미리(사용자 "6.7초면 꽤 길다") + 중간 판단 정리(사용자 "최종 판단만 중요, 중간 이력은 필터링").**
  직접 입력 폼은 쓰다 2초 멈추면(30자 이상, 한 번에 하나) 뒤에서 판단 → [넣을 곳 확인]이 캐시로 바로. 정확도·판단 방법 그대로(10/22 실측: LLM이 이웃만 대비 자동 틀림 2/60 → 0/51).
  쓰다 버린 판단은 등록 때 지움(`discard_decision_ids` → `ledger.discard_open`: 내 것·확정 전만), 떠나서 남은 미확정은 24시간 뒤(`purge_stale_open`, 판단 낼 때 같이).
  등록 성공·실패 때 판단 캐시를 비움(지워진 판단 재사용 방지 — 리뷰 M1). 시나리오 `registration_location.py` 4) 추가(24/24). 컨플루언스 현행화 리서치: `docs/domains/knowledge/sources/confluence/confluence-sync-research.md`.
- v2.183 (2026-10-28, dev_0): **VOC 레이어 분리(사용자 10/22 결정, 백로그 §0-3) — 같은 프로젝트 안에서 경계만.** 지식 코어 API
  `agents/knowledge_rag/knowledge/api.py`(search · min_score · nearest · contents)를 두고, VOC(`service/email_voc`)는 이것만 부른다 — 검색 내부(retrieval·glossary_terms)
  import와 지식 표 직접 SQL 3곳(관련성 검색 · 반복 유형 커버리지 2 · 근거 지식 조회)을 API로. 커버리지는 클러스터마다 LATERAL 대신 한 쿼리(`nearest`, 같은 범위 규칙 — 벡터 변환은 MATERIALIZED로 키마다 한 번, 리뷰 실측 53→7.5ms).
  경계 = `tests/test_voc_boundary.py`(VOC가 knowledge.api 밖 agents import·rag_*/policy_* 표 SQL을 하면 실패). 동작 동일: 전/후 스냅샷(`eval_out/voc_boundary_snapshot.py` —
  반복 유형 24·커버리지 24·메일 30건 관련성·검색·근거 지식) 전부 같음. 별도 에이전트로 떼는 조건은 백로그 그대로.
- v2.182 (2026-10-28, dev_0): **검색 미리보기 → "AI가 본 근거", 쓸 일이 생기는 자리로(사용자 "개발하는 나조차 인지가 안 되면 아무도 못 쓴다", "검색 미리보기? 뭘 미리 봐").**
  평가 게이트 탭은 내리고 [AI가 본 근거 ▸] 버튼을 ① 정정 요청 신고 카드(그 질문으로 바로 — AI가 무엇을 보고 답했나) ② 지식 상세("이 지식이 검색에 잡히는지 보기" — 그 지식 순위·안 잡힘)에.
  결과 아래 "다음에 할 일"(지식·정책 고치기 · [이상해요] → 정정 요청 · 관리자는 [이게 맞는 답] → [시험지에 넣기]). `POST /api/eval/preview` 권한 = 채팅과 같게(로그인 사용자 + 다른 파트 열람 감사 기록 —
  신고 처리 담당은 관리자가 아님), 시험지에 넣기는 관리자만. 같은 v2.182: 평가 게이트 적재 칸 = 적재 문제가 걸린 틀린 문항 수(다른 칸과 같은 단위).
- v2.181 (2026-10-28, dev_0): **평가 게이트 두 축 정리(사용자 "위 단계를 누르면 계속 리포트로 돌아가고 아래 탭이랑 관계가 안 맞는다").**
  위 단계 흐름 = 공통 필터(고르면 탭은 그대로, [틀린 문항]·[성적]·[적용 이력]이 모두 그 단계로 — 적용 이력 안의 따로 놀던 단계 칩 제거),
  아래 탭 = 무엇을 볼까 [틀린 문항] [성적] [적용 이력] [검색 미리보기](v2.182에서 탭은 내림). 옛 [검색]·[답] 탭 → [성적] 하나(단계 검색 = 검색 성적, 생성·근거 판정·답 모양 = 답 성적과 해당 층 안내,
  적재 = 적재 검수 안내, 단계 안 고르면 답 성적 | 검색 성적) — "검색"이 단계 이름·탭 이름 두 뜻이던 것.
- v2.180 (2026-10-27 밤, dev_0): **자가 개선 루프 재구성(사용자 "평가 게이트·감사 로그는 자가 개선을 위한 과정", 설계 `docs/domains/quality/self-improvement-loop-design.md`).**
  ① **번복은 기능 자리에서**: 지식 등록 넣을 곳 카드에서 AI 추천(확신 없으면 후보)과 다른 곳을 고르면 이유 칩(AI가 틀림·둘 다 맞음·상황이 바뀜, 선택·비차단) →
  원장 final_value.override_reason(+ 틀림·상황이 바뀜은 ai_verdict). 번복 판정은 저장 칸이 아니라 값으로(`service/eval/override.py` is_override = OVERRIDE_SQL).
  ② **진단에 운영 번복**: 종류별 번복률·이유·패턴 + 위치 판정 제안 규칙 LLM 없는 리플레이 → `eval_improve.py --save` 후보(원인 `override_location`). `GET /api/eval/overrides`.
  ③ **AI 감사 로그 = 기록만**: "AI가 틀렸나요?" 버튼 제거, 요약 3칸 + 형식적 승인 경고 + 번복만(기본) 한 줄 표(번복 이유 · 이어진 개선 후보).
  ④ **RAG 파이프라인 메뉴 내림 → 평가 게이트**: 맨 위 단계 흐름(단계별 틀림·번복, 누르면 리포트가 그 단계로) + [적용 이력] 탭(`GET /api/eval/history` — 기법 대장 근거 + 후보 결정 날짜순, 항목 전체 보기).
  ⑤ **즉석 질의 → 검색 미리보기**: `POST /api/eval/preview`(채팅과 같은 build_chat_context, LLM 0 — 채팅이 할 일·용어·문맥 후보) + [시험지에 추가] `POST /api/eval/preview/questions`(실사용 층, #94).
  ⑥ 가독성: 결정 카드(상태 배지·세 칸·패턴 표·결정 사유 상자), 문항 상세 ④⑤⑥(두 상자·저장 한 줄·고칠 방법 표), 기법 창(값 상자·측정 타임라인).
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
# domain/llm/base.py
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
