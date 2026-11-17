# OpsLens 시스템 아키텍처 (v2.235)

## 개요

OpsLens(구 Ops-Navigator, 2026-10-06 이름 변경)는 정책서·매뉴얼·운영 지식·공통코드처럼 흩어진 IT 운영 지식을 한곳에
모아 근거와 함께 답하는 **IT 운영 지식 통합 AI 플랫폼**이다. 틀린 답은 사용자 신고 → 담당자 검토로 바로잡는다.
사용자는 에이전트를 선택해 목적에 맞는 AI를 사용한다: 지식 기반 Q&A(KnowledgeRAG).

> Text-to-SQL 에이전트는 v2.51에서 `dev_0`/`main`에서 분리·제거됐다(현재 과업 범위 아님) — 코드는
> `archive/with-text2sql` 브랜치(2026-09-03 시점 스냅샷)에 형상관리용으로 보존돼 있다.
> MCP 도구 에이전트는 v2.67에서 완전 제거됐다(Text2SQL과 달리 브랜치 보존 없음 — 배경은
> `docs/archive/architecture-history-v2.0-v2.99.md` v2.67 항목).

**주요 이력 요약** (스키마 변경 상세는 `table-definition.md` §20 마이그레이션 이력 참조)
- v2.235 (2026-11-16 밤, dev_0 미커밋): **골든셋 자동화 1·2단계 — 정답 닻 · 범위 밖 문항 · 골든셋 후보**(제안서 TVjEPGCmiJ2EToJ1pfFaZH, 태훈 승인 — 배포 전 전체 새로 넣기 대비). ① 정답 닻(`trustkit/eval/anchors.py`): 지식 골든셋 정답을 조각 id 대신 정답 조각에만 있던 줄의 해시로(같은 페이지 범위·사본 제외, 고유 줄 없으면 80% 기준) — 다시 적재해 id가 바뀌어도 찾음, 못 찾으면 개수로 보임. 컨플 106·CX 93 변환: 같은 데이터에서 정답 잃음 0 · 검색 93.4→94.3%(정답을 통째 담은 상위 조각 인정 +1문항)·CX 83.9% 그대로 — 게이트는 닻 기준끼리 비교. 변환 `scripts/golden_anchors.py`. ② 생성기 `confluence_synth_questions.py`: 정답 닻으로 적음 + `--oos`(범위 밖 = 정답 '모른다') · 사람·연락처 질문 코드 필터(PERSON_Q — 표본 9 중 사람 판정 개인정보 5와 일치). 범위 밖은 검색·정답률에서 빼고 `scripts/eval_abstain.py`로 따로(거절 = 정답, 단정 = 단정 오답). ③ 골든셋 후보(평가 게이트 카드 + 목록, 시안 A): 답변 오류 판정·지식 없음 거절 중 아직 안 정한 것을 그때그때 모아 [정답 = 근거]·[정답 문서 #]·[모른다가 정답]·[제외] — 사람이 골라야만 골든셋(자동 추가 없음, 같은 질문 한 줄·잠금). API `GET /api/eval/golden-candidates`·`POST /api/eval/golden-candidates/decide`(관리자), `--real`로 지식 측정에 실사용 문항(물은 컬렉션 그대로). 마이그레이션 #128 `eval_question_candidate.query_log_id`. 검증: 백엔드 2,503 통과 · 실 DB 롤백 시나리오 · E2E golden-candidates · 독립 리뷰 중간 3(질문 끝 줄바꿈이면 정한 뒤에도 남음·실사용 문항을 정답 컬렉션으로 옮겨 실패 가림·빈 닻 KeyError)·낮음 5 반영/백로그 · 재리뷰 낮음 1(SQL이 BOM을 지워 파이썬 strip과 어긋남 → 성질 테스트) 반영. 남은 것: 게이트 통과 뒤 게시(배치 화면 시안 먼저) · 범위 밖 채점기 사람 일치도(표본 단정 1건을 채점기가 놓침).
- v2.234 (2026-11-16, dev_0): **"내용이 낡음" 칩 정정 대상 고르기 — 숫자 덩어리 대조 · 정책 근거도 · 캐시 근거 확인 = 검색 기준**(work-os 배포 전 확인 B1~B3, v2.231·232 독립 리뷰 지적). ① 신고 숫자를 답 줄·근거 글에 부분 문자열로 대조해 "30"이 [#20130]·2030에 걸리고 그 대상이 확정(추정 아님)으로 표시되던 것 → 근거 번호 표식을 지우고 숫자 덩어리(쉼표·점·콜론 포함)째 비교(쉼표 무시, 네 자리 연도만 점 날짜 앞에도) · 답 줄 근거 번호는 [#12, 13]의 뒷번호까지. ② 낡은 값이 정책 근거에만 있는데 운영 지식만 봐서 엉뚱한 운영 지식(후보 하나면 그것)이 대상이 되던 것 → 숫자 대조는 정책 서술 조각·파라미터(조건·값만 — 원문 전체를 보면 같은 정책의 다른 값에 걸림)도 보고, 숫자가 맞은 정책은 대상으로(정책당 대기 1건 잠금·승인하면 새 버전 — LLM이 정책을 고른 길과 같음). 숫자 단서가 없으면 예전처럼 운영 지식 안에서 임베딩 추정. ③ 캐시 답 근거 확인이 정책 `status='active'`만 보던 것 → `visibility.searchable_sql` 그대로(옛 엑셀 경로 검토 대기 = 검색에 나옴 = 살아 있음, 실해 0건이었음). 덤: 시나리오 `ledger_race.py`가 컬렉션을 정책 컬렉션(기본값)으로 만들어 11/02 이후 등록 400으로 판정 전에 멈추던 것 → 시스템 컬렉션으로(동시 확정 3/3 통과). 검증: 백엔드 전체 2,482 통과(새 테스트가 고치기 전 코드에선 실패), 독립 리뷰 중간 1(파라미터 원문 전체 대조)·낮음 3 반영, 재리뷰 중간 1(점 날짜·소수 쪼개짐) 반영.
- v2.233 (2026-11-12, dev_0): **VOC Teams 카드 — 필요한 것만 · 묶음 전부 IT 무관이면 안 보냄**(태훈 "읽지도 않는다·가시성"). 카드 위 줄은 분류·심각도 한 줄 · 관련 시스템 · 🔁 반복(최근 N일 유사 n건) · 근거 지식 n건(이력 탭과 같은 수) · 근거 정책 이름 · 조건 줄(오배치·확인 필요·📭 관련 지식 없음·온콜)만 — 담당 파트(= 채널)·발신자(14일 194건 전부 CS 전달 주소)·"n건마다·n번째 감지" 뺌. "관련 지식 · 참고"·"참고 지식(근거)"·"참고 정책(근거)" 칸 삭제(앞부분 조각은 읽히지 않고 오탐 확인에도 안 쓰임) → 카드마다 돌던 관련 지식 LLM 확인 1회 없어짐. 해결 방안 칸은 항상, 없으면 이유(반복 유형 지식이 이유 줄 자리를 차지해 IT 무관인데 이유가 안 보였음). 묶음에 든 메일이 전부 IT 무관이면 발송 안 함(`skipped_not_it` 칸 재사용, 한 건 판정은 틀릴 수 있어 하나라도 다른 분류면 보냄 — 10/22 "반복되면 일단 팀즈"를 이 경우만 바꿈) — 14일 실발송 194건 중 72건 해당. 검증: 백엔드 전체 2,441 통과, 실발송 194건 재계산 72 · 실 IT 무관 카드 모양(제목·내용·해결 방안 "해당 없음"), 독립 리뷰 중간 1(근거 건수 이력 탭과 어긋남·정책 근거 볼 곳 없음) · 낮음 3 반영, 수정분 재리뷰 0.
- v2.232 (2026-11-12, dev_0): **답 본문 근거 칩**(시안 A안 승인 11/12) — 답의 `[#번호]`·`[#12, #13]`·`[문서 #12]`를 번호 칩으로(숫자 = 근거 카드 순서,
  마우스 올리면 문서 번호·첫 줄, 누르면 근거를 펼쳐 그 카드로 이동 + 1.5초 강조), 문맥에 없는 번호는 회색 "?", `[정책]`·`[참고표]` 종류 색 칩(정책 근거가 있으면 정책 근거로 이동),
  `[공통코드]`·`[DB]`는 회색 칩, "확인 안 됨"은 그 자리 주황 배지. 서버 답 글자·저장 형식은 그대로(화면에서만, 옛 대화도 같은 규칙) — rehype 플러그인을 sanitize 뒤에
  돌리고 코드·링크 안은 건드리지 않음(`chat/citeChips.tsx`, 표식 형식은 trustkit `CITE_IDS`와 같음). 테스트: e2e `cite-chips.spec.ts`(나누기 유실·추가 0 무작위 2,000 + 화면)
- v2.231 (2026-11-12, dev_0): **시연 녹화에서 나온 결함 수정 + PoC 전 확인**(backlog 0-0h B·C, 11/11 밤샘). ① 캐시 답 근거 확인 — 적중한 캐시의 근거 지식·정책이
  전부 지금 active일 때만 내보내고 아니면 버림(그 키도 지움)·새로 답·`trace.cache_stale`(실례: 사본 DB로 바꿨는데 DB에 없는 문서를 근거로 한 캐시 답) +
  `restore-db.sh`가 복원 뒤 답 캐시(`semcache:*`)만 줄 단위로 비움(이름 띄어쓰기 키 포함, 남은 개수 경고) — 같은 id 옛 내용은 비우기만 막음(반입 절차서 §4-2).
  캐시 비우기 API는 실제 Redis 키 개수로 정상 확인(시험 고정). ② 정정 판정 — 사유 칩 "내용이 낡음" + AI "근거에 없는 내용" + 운영 지식 근거 있음 →
  근거 정정으로(대상: 답에서 신고 숫자가 나온 줄의 근거 → 숫자가 든 근거 → 임베딩 추정, AI 원래 판정은 `ai_verdict.llm_verdict`, 임베딩 실패면 규칙 없이 그대로).
  정정 판정 골든 20(규칙 정답, 로컬 Qwen) 13→18/20(낡음 지적 0→4/6 · 값 변경 7→8/8 · 새 사실 6/6 그대로 — 남은 2 = 후보 셋 다 같은 연도). ③ 넣을 곳 판정 —
  AI 판단이 없을 때(형식 깨짐·애매) 1위 이웃이 등록 중복 기준 이상이면 그 컬렉션(다수결이 0.95짜리 거의 같은 글을 이김, 안내 문구). ④ 정정 카드·일괄 승인 —
  바뀐 줄 0이면 승인 막음. ⑤ trustkit `check_numbers`(근거 줄의 숫자·시각이 인용 문서에 있나) — **측정만, 채팅 미연결**: 11/10 답 212에서 표시 15 중 14가
  채점 정답 → 답 바꾸기 보류. 독립 리뷰 7건(중간 2: 띄어쓰기 키 미삭제·칩 경로 임베딩 실패 500) 반영 + 재리뷰. 검증: 백엔드 2,443 · 실 Redis 4 · 실 DB 3 ·
  사본 백엔드 실경로(캐시 → 근거 SQL 삭제 → 새 답 / 칩 신고 → #20511 근거 정정) · 사본 화면 바뀐 줄 0 카드.
- v2.230 (2026-11-11, dev_0): **다음 팀 적재 전 필수 2건**(밤샘 점검 적재 경로). ① 팀 적재 같은 배치의 형제 페이지끼리 비슷한 글이 승인 대기로 감 — 페이지마다 따로 보류 작업이라 서로를 "다른 보류 작업 조각(롤백될 수 있음)"으로 봤다(기록상 601건 전부 사람이 승인, 반려 0) → 배치에서 먼저 넣은 페이지 작업(`peer_job_ids`)은 다른 원본 사용 중 글과 같게 둘 다 등록(배치는 함께 게시·롤백, 한쪽 페이지만 빠져도 남은 쪽은 중복이 아님). 같은 원본·원본 표시 없음·배치 밖 보류 작업은 그대로 대기. ② 동기화 때 페이지 본문이 비면 '빈 페이지'로 세기만 하고 옛 조각이 계속 현행으로 검색됨 → 사라진 페이지와 같게 승인 대기로 내림(지우지 않음 — 파싱 실패·일시적 빈 판일 수 있다, 로그). 독립 리뷰: 중간 1(형제 보류 조각이 판단할 상대 다시 보기 10자리를 채워 그 뒤 수기 글이 가려진 채 바로 등록 → 다시 보기에서 같은 배치 작업 제외) 반영, 낮음 2(빈 페이지 대기 사유 표시·전 경로 불변식 시험)는 backlog 0-0g. 검증: 판정 표 단위 4건 · 실 DB 2건(다시 보기 후보에서 형제 제외·배치 밖 보류와 수기는 남음 / 빈 페이지 → 그 페이지 사용 중 조각 0, 다른 페이지 그대로).
- v2.229 (2026-11-11, dev_0): **밤샘 점검 실제 위반 2건**(시안 승인). ① "관련 지식을 찾지 못했습니다"뿐인 답에 [근거 N건]이 붙어 나감(실데이터 4/4) → 채팅 화면이 근거 줄을 안 보여 줌(저장 기록은 그대로 — 정정 후보·AI 분석용, 답 일부에만 나온 문구는 그대로). ② 폐기된 정책을 겨눈 대기 정정(#122·#189)이 승인 불가인 채 목록에 남음 → 정정 카드 [대상 폐기 ⓘ] + 승인·일괄 선택 막음(`target_stale`) · 대상이 현행(사용 중·검토 대기)에서 빠지거나 지워지면 커밋 때 트리거가 대기 정정 전체를 보고 이어받을 현행도 없는 것을 자동 반려(#127 — 폐기 경로가 가져오기·삭제·동기화·다시 분할·보류·스크립트로 여럿이라 표에, 커밋 때 판정이라 옛 판을 먼저 내려도 오판 없음). #122·#189는 승인 뒤 같은 사유로 반려. 독립 리뷰: 중간 3(바뀐 행만 봐 이어받은 현행이 나중에 사라지면 옛 대상 정정이 남음 → 트랜잭션마다 대기 전체 · 지식 검토 대기 판을 현행으로 안 쳐 보류·재적재 때 되돌릴 수 없는 반려 → 현행 = 사용 중·검토 대기 · 원문 확인 카드는 target_id가 logical_id → 제외) · 낮음 2(승인과 교착 → SKIP LOCKED · 파일 재등록 원문 연쇄) 반영. 검증: 실 DB 7건(전체 개수 불변식 — 정책 사라짐 / 같은 트랜잭션 뒤에 넣은 새 판 / 지식 지움·같은 페이지 현행 / 이어받은 뒤 그것도 지움 / 보류 / 원문 교체 / 원문 확인 카드 그대로) · 화면 확인(근거 없음 답 근거 줄 0 · #122 표시·승인 비활성).
- v2.228 (2026-11-10, dev_0): **합쳐진 같은 글을 정정하면 사본도 같이**(work-os v2.227 검수 중간 1 · 낮음 2, 시안 A 승인). 막힌 일: 검색은 본문이 같은 사본을 한 자리로 합쳐 근거·[답변 틀림] 대상이 그 1건뿐인데, 정정 승인은 그 1건만 새 버전 — 사본(같은 글 묶음 128·440행)이 옛 글로 남아 다음 검색에 따로 한 자리를 받고 정정본과 함께 문맥에 실렸다(표시 없음). 바꾼 것: 정정 요청 카드에 같은 글 사본 목록(고칠 수 있는 컬렉션 것 기본 체크, 아닌 것은 "그 컬렉션 담당 몫") → 승인 = 대상 + 체크된 사본이 같은 새 글로 새 버전, 버튼 "승인 · 사본 N곳 포함", 일괄 승인 = 고칠 수 있는 사본 전부. API는 체크를 푼 것만 받음(`exclude_copy_ids`). 대상·사본을 id 순서로 함께 잠가 같은 글·현행 재확인(어긋나면 409, 아무것도 반영 안 함) · 사본을 겨눈 대기 신고 자동 종료 · 한 일은 `ai_verdict.copies{applied, left}`. 덤: 리랭커를 켜면 사본 소속 상한이 후보 수 기준이던 것 → 문맥 수(`search_knowledge(context_k)`) · 골든셋 정답이 정정된 새 버전·그 사본을 따라감(`scripts/knowledge_eval_set.py` — md5만 보면 사본까지 고친 뒤 정답 0개, 독립 점검 M6) · 백로그 문구. 검증: 실 DB 불변식 6건(`tests/test_realdb_correction_copies.py` — 기본 승인 뒤 고칠 수 있는 컬렉션의 옛 글 사용 중 행 0 · 체크 푼 것만 예외 · 다른 글 불변 · 사본 쪽 대기 신고 종료 · 그 사이 바뀐 사본이면 409 원자적 · 관리자는 전 컬렉션) · E2E(임시 컬렉션 — 하나 풀고 승인 → DB 전후) + 정정 직접 수정·일괄 승인 E2E 통과 · 백엔드 전체 통과. 독립 리뷰: 중간 1(고른 사본을 상태로 들고 있어 대상 변경·다시 받기 뒤 승인이 계속 409/기본이 빈 선택 → "푼 것"만 상태·API) · 낮음 4 반영(그 사이 생긴 사본 조용히 남김 · 사본 쪽 대기 신고 · 잠금 순서 교착·잠그기 전 틈 · 감사 기록).
- v2.227 (2026-11-10, dev_0): **검색 접기가 소속을 지우지 않게 + 승인 대기 판정 보강 + 원문 '#' 오탐**(work-os 외부 검수 요청 — v2.221·v2.225 중간 1·낮음 2·사소 + C3). 막힌 일: 채팅 문맥엔 조각마다 "상위 맥락(소속)" 줄이 붙는데 접기는 본문만 비교 — "실버 혜택"·"골드 혜택"에 같은 문장이 있으면 실버 조각이 접혀 소속이 사라졌다(106문항엔 이 경우가 없어 게이트 통과). 우리 독립 리뷰 3회는 의뢰서 성질을 "본문 문장" 기준으로 줘서 못 잡음(development-conventions §10에 사례). 바꾼 것: ① 같은 글(content_norm_md5)은 한 자리로 합치되 사본 소속을 `other_headings`로 문맥 줄에 모두(`상위 맥락: 실버 혜택 > 적립 | 골드 혜택 > 적립`, 점수 순·top_k−1개까지, `retrieval.heading_line` — 문맥 만드는 곳 공용) — 처음엔 "소속까지 같아야 접기"로 고쳤다가 재리뷰(높음): 컨플은 소속 첫 칸이 페이지 제목이라 다른 페이지끼리 하나도 안 접힘(본문 같고 소속 다른 묶음 127·436조각) → 합치기로 ② 포함 접기는 AI가 보는 글(소속 줄 + 본문)로 비교 — 긴 쪽에 소속을 합치면 그 쪽에만 있는 문장이 짧은 쪽 소속에도 붙어(실버에 생일 쿠폰) 틀린 연결이라 소속이 같을 때만 ③ 미리보기 "같은 글" 표시는 상대가 사용 중일 때만(다른 보류 작업 조각이면 등록은 승인 대기 — 비교로 보임) ④ 가까운 후보 3건이 승인 대기 근거 없는 상대(판단할 것 없음·글자 미달)로 차면 판단할 상대만 다시 조회(`review_only_for`, 10건) — 등록·미리보기 같은 규칙 ⑤ 지식 검색 화면 카드에 [같은 글 N곳] + 사본 [열기](시안 승인 — 사본 1벌만 보여 고치면 나머지에 옛 내용이 남던 것) ⑥ C3: 원문 문단 첫 '#'(제목 태그 아님)을 이스케이프 — 변환 뒤 제목 줄로 읽혀 "제목만 조각" 오탐(job 3567·3991, 508~1,999자 본문 4조각), 파서 판 v2.227, 두 원문 다시 분할(경고 0 · 적재 점검 제목만 조각 10→6, 남은 6 = 11/02 진짜 제목만, 11/05 청커 수정 뒤 재발 0 · #824는 이스케이프 외 한글 4글자 자리 차이 1곳 — 두 글자열 모두 원문에 있음, 11/06 뒤 파서 변경으로 보임, 원문 대조 경고 0) ⑦ 기법 대장 duplicate_review·contained_collapse 설명·근거. 측정(로컬 LLM, 같은 날 106문항 ×2): 접기 끔 → 켬 답 85.8→86.3%(단정 오답 13.7→12.7%, 좋아짐 5·나빠짐 4) 게이트 통과(연결 시간 초과 1줄은 다시 잼) · 실데이터 (소속, 문장) 쌍 유실 0(`tests/test_realdb_retrieval_collapse.py::test_collapse_only_among_adopted_keeps_adopted_content_and_positions` 무작위 소속 포함 + eval_out/exp/collapse_pairs_1110.py) · 같은 글 사본 한 자리로 합친 문항 8 · 본문 기준 중복 자리 10→9(남은 9 = 소속이 다르고 긴 쪽이 더 담은 경우, 일부러 둠) — 11/09의 "11→0"은 본문 기준이라 대부분 소속이 다른 조각이었다(AI가 보는 글 기준 1→0). 독립 리뷰 6회: 높음 1(소속까지 같아야 접기 → 다른 페이지끼리 안 접힘 → 합치기) · 중간 4(가려짐 판정에 글자 검사 누락 · 사본 [열기]가 검색된 조각 글로 열려 그대로 저장해도 사본이 바뀌고 "사람이 고침"으로 찍힘 → 사본 자기 글 · 문단 안 둘째 줄 '#' → 줄마다(사용 중 HTML 원문 전체 재파싱 성질 검사 통과, REALDB_FULL=1) · 화면 백슬래시 떼는 정규식이 아무것도 안 바꿈 → 고침) · 낮음 반영(사본 소속 순서 고정·top_k−1 상한 · 다시 보기 10건 · 다른 컬렉션 사본 [열기] 숨김 · Esc가 막히던 키 처리) · 남은 낮음은 backlog 0-0f.
- v2.226 (2026-11-10, dev_0): **원문 확인 [참고표 보기]가 문제 행부터**(태훈 "1번부터 나와서 어딜 수정하라는건지 찾기 어렵다", 시안 승인). 카드에 문제 위치가 설명 문장에만 있어 창이 1행부터 열렸고, 창의 # 칸은 원표 행 번호가 아니라 화면 순번이었다(3행부터 시작하는 표는 카드의 "원표 3행"과 어긋남). 이제 카드 `original.focus = [{table, table_id(그때 버전), rows(원표 행 번호), columns}]` — 카드를 만드는 쪽이 적는다(씨앗 jsonl `focus`, 적재기가 그 버전에 행·열이 실제로 있는지 검사, 설명 문장에서 뽑지 않음). 목록 API가 표마다 `focus{rows, columns, current}`(적어 둔 버전 = 지금 버전일 때만 current — 새 버전이면 강조 안 하고 안내 한 줄), 참고표 상세 API에 `row_nos`. 화면: [참고표 보기 · 문제 n행] → 카드 설명을 창 위에, 첫 문제 행으로 스크롤, 문제 행 노랑·짚은 칸 빨간 테두리, ◀ ▶, [문제 행만](위아래 1행), # = 원표 행 번호. 새 표·칸 없음(jsonb 키). 데이터: 기존 참고표 오류 카드 4건(#268~271)에 표 구조 검사로 찾은 행을 채움(태훈 건별 승인 — 머리글 반복 3행 · '기타' 밀림 6행 ×2 · 빈 챌린지 유형 4행, 설명 문장의 행 수·번호와 일치). 검증: 백엔드 전체 통과, 실 서버 API(문제 행·current·row_nos), E2E 신규(임시 컬렉션 실제 행 — 스크롤·강조·이동·문제 행만·새 버전이면 강조 안 함·검색에 가려짐) + 기존 원문 확인 E2E 통과(단계가 많아 30초를 넘기던 1건은 제한 90초로). 독립 리뷰: 중간 2 — focus 항목이 dict가 아니면 검증이 예외로 죽음 → 형식 오류로 · 표 안 검색마다 문제 행으로 끌려감 → 검색은 안 끌고 감 · 낮음 4 반영(검색에 가려지면 표시 · 이미 있는 카드에 focus는 안 들어감을 적재기가 알림 · 같은 표 두 항목 거부 · 행 번호 중복 대비 key).
- v2.225 (2026-11-10, dev_0): **승인 대기 정리 — 다른 원본의 비슷한 글은 바로 등록 + 검색에서 포함 조각 접기**(태훈 "diff 치고 집합관계 따지면 어지간한건 될 것" · "중복이 top k 자리를 차지해 생성 정확도가 내려갈 수도", 밤샘 실험 브리핑 K8C62DjHzir3kmcAMCgdoi → A안 승인). 막힌 일: 비슷한 글은 전부 승인 대기(460건) — 사람이 볼 게 없는 경우(다른 페이지·같은 적재의 두 조각)가 거의 전부였고, 그동안 그 내용은 검색에 안 나왔다. 바꾼 것: ① 적재 `needs_review` — 비슷한 글(의미 ≥0.88·글자 ≥0.8)이 승인 대기로 가는 건 같은 원본의 다른 작업 글·어느 한쪽 원본 표시 없음(수기·옛 일괄)·다른 보류 작업 조각뿐, 같은 작업·다른 원본 사용 중 글은 바로 등록(로그 수). 같은 배치 임베딩 내적 비교는 삭제(같은 배치 = 같은 작업). 미리보기(`/similar/batch`)도 같은 판정 ② 검색 `collapse_adopted` — 최종 top_k의 채택 조각끼리, 문장이 다른 조각 안에 같은 순서로 이어서 그대로 있으면 빼고 긴 쪽이 앞 자리, 빈자리 안 채움(설정 `KNOWLEDGE_COLLAPSE_CONTAINED`, 기본 켬 · 지식 검색 화면은 끔 — 고치려고 찾는 화면). 업계: 다 색인하고 보여 줄 때 묶기(field collapsing·Broder 1997 포함도). 기법 대장 `contained_collapse`. 측정(로컬 LLM, 106문항 ×2, 460 등록 사본 opsdb_exp): 답 84.0→84.9%(단정 오답 14.6→14.6%) 게이트 통과, 문맥 중복 자리 11→0·글자 −2% · 빈자리 채움은 무관한 조각이 들어와 1문항 5회 4→2(기각) · 임베딩 0.95 접기는 검색 100→94(기각) · 검색 1문항(k16971-1) 밀림 = 새 거의 같은 글 둘이 정답을 4위로(답은 원래 거절·오답, 기록하고 수용). 지금 대기 460을 새 규칙으로 다시 판정 = 458 등록 · 2 대기(수기 시험 데이터 1, 원본 표시 없는 옛 일괄 조각과 겹침 1). 검증: 백엔드 전체 통과, 실 서버 채팅 HTTP(끔이면 3조각 → 켬 1조각, 캐시 아님), 리뷰 수정 뒤 채팅 문맥 = 측정 때와 같음(106 중 105 같음, 1문항은 "안 접음" 측정 문맥과 같고 그때 4/4 정답). 독립 리뷰 3회: 중간 3 — 바꿀 수 없는 큰 조각이 버려짐 → 채택 조각끼리만 접기로 해소 · 채택 안 되는 큰 조각이 채택되는 작은 조각을 지움(문맥에서 내용 사라짐) → 같음 · 후보 3건을 이번 작업 조각이 차지하면 판단할 상대가 가려져 미리보기와 등록 판정이 갈림 → 이번 작업 뺀 후보 재조회(3번째가 하한 미만이면 생략) · 낮음 반영(문장 순서 무시로 "[실버] 적립 3%"가 등급표에 접힘 → 연속 포함만(사용 중 포함 쌍 3,060 중 92쌍) · 리랭커 켜면 후보 단계 접기라 게이트 다시 · 미리보기 원문 id 판정 등록과 같게 · 등록 이력 수 이중 집계 · 안내 문구 · 측정 스크립트 순위 진단은 안 접음). 데이터(승인 대기 459 등록)는 커밋 뒤 따로.
- v2.224 (2026-11-09, dev_0): **정책 JSON 가져오기 3-way 비교 — 옛 파일이 시스템 수정을 되돌리지 않게**(정책 관리 독립 검수 후속 ②, 브리핑 VdvoSmuRYb3DNHbep38nvy 승인). 정책은 시스템이 기준(10/23)인데 가져오기는 파일 ↔ 지금만 비교해, 내려받은 뒤 시스템에서 고친 정책이 옛 파일에선 "바뀜"으로 잡혀 되돌리는 정정 요청이 경고 없이 생겼다. 이제 기준(파일이 나온 버전 — 내려받기가 정책마다 적은 version, 없거나 모르면 현행이었던 옛 버전과 내용 대조)·파일·지금을 같이 본다: 파일 = 기준이고 지금이 다름 → **구버전 — 건너뜀**, 양쪽 다 다름 → **충돌**(올리지 않고 목록만, 시스템에서 직접 수정), 지금 = 기준 → 예전대로 바뀜. 기준 후보는 같은 정책(logical_id)의 active·deprecated 버전만(반려·검토 대기는 현행이었던 적 없음). 파일 이름 규칙 불필요. 새 표·칸 없음, 미리보기 칸 2개(0이면 안 보임)·충돌 줄. 검증: 경우별·무작위 성질 테스트(한 칸씩 · 기준 모르면 옛 내용으로 되돌리는 정정 없음), 실 DB 시나리오(되돌림) — 시스템 v2 만든 뒤 v1 파일 그대로 = 구버전 1(전엔 바뀜 1 → 되돌림 정정) · 같은 정책 파일에서도 고침 = 충돌 1 · 버전 칸 없는 옛 파일 = 구버전 1, 실 서버 HTTP 내려받기→미리보기 그대로 10/10 같음 · 한 건 고침 = 바뀜 1, 백엔드 전체 통과. 독립 리뷰: 중간 1 — "시스템에서 고쳤나"를 버전 번호로 봐서, 원문 그대로 버전만 오르는 분류 옮기기·파라미터만 정정 뒤엔 정당한 파일 수정이 가짜 충돌로 빠졌다(팀원 파일은 전부) → 내용으로 판단(기준 내용 = 지금이면 바뀜, 옛 버전은 지금과 내용이 다른 것만), 실 DB 재현(버전만 오른 정책 파일에서 고침 = 바뀜 1)·성질 테스트 강화 · 낮음 — 결과 줄 0 숨김 · 이력 읽기 범위는 백로그.
- v2.223 (2026-11-09, dev_0): **정책 원문 정정 승인 시 옛 파라미터 정리**(정책 관리 독립 검수 후속 ①). 원문을 고쳐 승인하면(예: 30,000원→50,000원) 옛 값 파라미터가 새 버전에 그대로 붙어 검색·답 근거로 남았다 → 원문이 바뀐 경우에만, 새 원문에 글자로 없는 파라미터는 안 옮김(파생 기준 `value_in_text`와 같음 · 사람이 고친 정정 대상과 값 없는 항목은 그대로 · 원문 그대로인 정정은 예전대로 전부). 빠진 값은 `derive_policy_params.py` extract→load로 채움 — load는 원래 "파라미터 있으면 건너뜀"이라 못 채웠던 것을, 이전 버전보다 적어진 정책에만 빠진 개수까지 없는 값을 더하게(다른 정책은 예전대로 건너뜀 — 다른 실행 결과와 섞지 않음). 검증: 백엔드 전체 통과, 실 스키마에서 load 판정 쿼리(되돌림). 독립 리뷰 2회: 중간 2(다시 채우기가 막힘 → 이전 버전보다 적은 정책만 채움 / 그 수정이 모든 정책 가드를 풀어 새 파일 load 시 비슷한 값 이중 적재 → 범위 좁힘)·낮음 2(원문 그대로인 정정도 대조 → 원문 바뀔 때만 / 값만 보는 겹침 판정 → 백로그). 본인 제안 본인 승인은 유지(승인 권한 = 파트 담당자·관리자, 태훈 11/09).
- v2.222 (2026-11-09, dev_0): **토큰 낭비 점검 — 고칠 수 있는 것만, 정확도는 그대로**(태훈 "토큰 감당 가능한가" · "고칠 수 있는 낭비는 고쳐야" · "정확도가 내려가면 그건 써야 하는 토큰"; 발표 기준 = Bedrock). 독립 에이전트가 LLM 호출 16곳 전수 조사 → 낭비 5건 중 3건 수정·1건 유지·1건 운영 LLM 몫: ① 근거 없음 즉시 거절 다시 켬(기본 0.50) — v0 전환(10/20)으로 꺼 뒀던 것을 LLM 없이 재보정: 골든 거짓 거절 정책 0/485 · 업무지식 0/106, 답 없는 첫 질문 25/60을 LLM 호출 없이 거절(0.52부터 정책 1건 거짓 거절). compose는 안 주면 0.50·빈 값이면 끔 ② 로컬 LLM(openai_compat)도 사용량 기록(provider 구분, 스트림은 마지막 조각 usage) — 운영 LLM 기준 기능별 사용량을 개발 중에도 잰다 ③ 채팅·요약 출력 상한 4,096토큰(기록된 최대 답 2,294 · p90 384 — 폭주 방지만, 정상 답 안 잘림) ④ 옛 단일 응답 `POST /api/chat` 삭제(화면 미사용, 캐시·문맥 예산·되묻기·즉시 거절 없이 LLM 호출) · 유지: 문맥 예산을 넘는 입력(정책 v0 통째 조각 19/485문항 최대 1만 4천 자 · 대화 이력 최근 2교환) — 답에 필요한 토큰이고 여러 턴 정확도는 잴 수단이 없어 손대지 않음 · 운영 LLM 몫: 게이트웨이(Dify 에이전트)가 서버 쪽에서 붙이는 입력(보낸 3천 자 → 1만 7천 토큰 기록)·요청에 출력 상한 칸 없음 — Bedrock으로 가면 사라짐. 검증: 실 서버 — 답 없는 질문 8개 중 6개 LLM 호출 0으로 거절 · 정상 질문은 호출 + 사용량 기록(입력 5,943·출력 933토큰) · `/api/chat` 404, 백엔드 전체 통과. 독립 리뷰: 중간 2 — 후속 질문(이전 턴 있음)도 검색 신호만으로 거절될 수 있었다(측정은 한 턴뿐) → 후속 질문은 즉시 거절 안 함(예전대로 이력으로 답) · 사용량 화면에 로컬 LLM 기록이 섞임 → 화면은 지금 시스템 LLM 종류만(`current_provider_type`) · 낮음 2 — 서버가 출력 상한(문맥 한도)·usage 요청을 400으로 거부하면 그 칸 없이 한 번 더 · 옛 설명 주석·문서 정리, 기본값·중단·오류·재요청 테스트 추가. 여유가 좁다(0.50 vs 0.52) — 대량 적재 뒤 같은 측정(eval_out/exp/abstain_ctx_1109.py)으로 다시 본다.
- v2.221 (2026-11-09, dev_0): **승인 대기 재발 방지 — 다른 페이지의 똑같은 글은 바로 등록 + 검색에서 한 건으로**(태훈 "시스템적으로 막을 수 있게", "사람 공수 최대한 줄여야"). 막힌 일: 컨플 적재 뒤 승인 대기 815건 중 323건(40%)이 다른 페이지와 글자까지 같은 조각 — 판단할 게 없는데 사람 몫. 건너뛰면 그 페이지가 지워질 때 내용 유실(11/04 H1). 바꾼 것: ②' 적재 — 같은 글 상대가 사용 중(또는 이번 작업)이면 승인 대기 없이 등록, 작업 `cross_source_copies`에 수(다른 보류 작업 조각이면 롤백될 수 있어 예전대로) · 검색 — 저장 칸 `content_norm_md5`(공백 집합 = Python str.isspace 전체, 로케일 무관)로 같은 글은 점수 높은 하나만(업계 "내용 해시 접기") · `check_ingest` 중복 0 규칙을 같은 원본 안으로(10/07 사고도 같은 원본 안) · 골든셋 정답에 같은 글 id 포함. 데이터: 11/06 반려한 같은 글 269건 사용 중으로(감사 원장 269줄, 642 묶음 58건은 반려 유지). 검증: 검색 게이트 100/106 → 100/106(나빠짐 0) · 실 서버 20질의 같은 글 두 번 0 · 같은 원본 중복 0/5,494 · 검색 지연 중앙 4→5ms · 실 DB 시험(대표 지워지면 사본이 이어받음 · 점수 높은 사본 · DB=Python 정규화 무작위 300) · 백엔드 전체 통과. 독립 리뷰: 중간 1(행마다 정규화로 검색 4~5배 느림 → 저장 칸) · 낮음 반영(공백 집합 차이 · 보류 작업 상대 · 골든셋 범위 · 화면 툴팁·문서 옛 설명) · 백로그(등록 이력 배지 — 시안과 함께, 키워드 구제 후보 접기).
- v2.220 (2026-11-06, dev_0): **반입 전 검사 C 결정 6건**(권장안대로, 태훈 승인). ① AI 감사 — 표본 감사에서 고른 번복 사유가 저장(`spot_reason`)만 되고 리포트 집계가 안 읽었다 → 종류별 `spot.reasons`, 카드에 "번복 사유 AI 오류 n · …" ② 평가 게이트 — 운영 이미지엔 골든셋·결과 파일(eval_out/)이 없다(.dockerignore). 환경 이름이 아니라 그 서버에 파일이 있나로: 골든셋이 없으면 후보 `auto.golden=false`(사유 "개발 환경에서 실험") → [실험해 보기]·[다시 실행] 숨김, 결과 파일 폴더가 없으면 빨간 "파일 없음" 경고 대신 회색 안내 한 줄(`result_files`) ③ VOC [분석·알림 테스트] — API를 관리자만(전엔 로그인 사용자 누구나 LLM 호출), 탭도 관리자만(메뉴가 이미 관리자 전용이라 이중 방어) · 로그인 문구의 "임시" 정리 ④ 리랭커 모델(2.3GB)을 이미지에서 뺌 — `--build-arg BUNDLE_RERANKER=true`일 때만, 백엔드 이미지 11.4GB → 7.23GB(임베딩 모델 그대로) ⑤ 원문 원장(#125) — 교체될 때 `superseded_by`에 새 원문(같은 위치 > 같은 내용 > 원문 하나), 옛 줄은 같은 위치의 다음 판(같은 컬렉션 먼저, 없으면 옮긴 컬렉션) 994/1,092 채움(나머지 98 = 다음 판 없음) · 7일 지난 미리보기 초안 중 지식·팀 적재 페이지가 안 쓰는 것만 기동 때 정리(33건, 승인 대기 지식이 문 초안 112건은 유지) ⑥ `seed_demo_admin.py` 삭제(예시 60+12줄은 남아 있음 — 지우는 SQL은 운영 반입 정리 목록). 검증: DB 복사본(지운 것 = 대상 33 정확히 · 사용 중·교체됨 건수 그대로 · 잘못 이은 연결 0 · 두 번째 실행 변화 0 · 새 확정 경로 채움) → 실 DB 재기동 같은 숫자, 표본 감사 1건 매김 → 리포트·화면 "AI 오류 1" → 되돌림, 일반 사용자 테스트 API 403, 새 이미지 빌드·모델 확인, 백엔드 전체 2,324 통과. 독립 리뷰: 중간 1 — 같은 내용으로 다시 미리본 초안은 재사용돼 날짜가 옛것이라 [등록] 전 재기동에 지워질 수 있었다 → 재사용 때 `created_at` 갱신(복사본 시나리오 확인) · 낮음 3 반영(본문 없는 판을 다음 판으로 안 침, 다른 페이지를 가리키는 대체 2곳 제거) · 낮음 2 백로그(롤백 경로 연결 뜻, 채우기 인덱스).
- v2.200 ~ v2.219(11/03~11/06: 운영 전 필수 리뷰·팀 적재 검수 후 게시·VOC 채팅 경로·등록 순간 비슷한 지식·반입 전 전수 검사) 이력은 `docs/archive/architecture-history-v2.200-v2.219.md`.
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
│   │                 ┌──────────┴─────────┐               │  │
│   │          ┌──────┴───────┐     ┌──────┴──────┐        │  │
│   │          │  PostgreSQL  │     │    Redis    │        │  │
│   │          │  + pgvector  │     │ 답변 캐시   │        │  │
│   │          │   :5432      │     │  :6379      │        │  │
│   │          └──────────────┘     └─────────────┘        │  │
│   └──────────────────────────────────────────────────────┘  │
└──────────────────────────────┬──────────────────────────────┘
                               │ LLM (설정 화면에서 전환, .env LLM_PROVIDER)
        ┌──────────────────────┼───────────────────────┐
  사내 LLM 게이트웨이     로컬 LLM(OpenAI 호환,     Ollama(호스트,
  (inhouse, DevX)        openai_compat · Qwen)     비상용)
```
임베딩(KURE-v1)은 Backend 컨테이너 안(CPU). 운영(폐쇄망) 반입은 `docs/operations/deployment-closed-network.md`.

---

## 컴포넌트별 역할

### 1. Frontend — React + Nginx (`:8501`)

| 페이지 | 역할 |
|--------|------|
| **Login** (`/login`) | JWT 로그인 — Access Token + Refresh Token 발급 |
| **Register** (`/register`) | 회원가입 — 조회 전용(viewer)으로 시작, 관리자가 승인 |
| **Chat** (`/`) | 채팅 — SSE 스트리밍, 근거 카드(지식·정책), "답변 틀림" 신고(정정 요청으로), 대화 메모리(요약+리콜), Markdown 답변 |
| **Admin** (`/admin`) | 관리 범위 두 개(관리자만 전환 — v2.205): **컬렉션 관리**(홈·운영 지식·정책서·정정 요청·용어집·통계·소스 연결·컬렉션 정보) / **플랫폼 관리**(홈·평가 게이트·AI 감사·사용자 피드백·컬렉션(정책·시스템)·조직·권한·VOC 이메일·설정). 메뉴 정의 `pages/adminMenu.ts` |

- 로그인 직후 바로 채팅(에이전트 선택 화면은 10/30 삭제). **ProtectedRoute**: 로그인 안 했으면 `/login`
- **useAuthStore** (Zustand): localStorage 토큰, 자동 Bearer · **401 자동 갱신**(Refresh Token, 실패 시 로그아웃)
- 보는 컬렉션 = 사이드바 칩 + 고르는 창(시스템·정책), 수정 버튼은 그 컬렉션 수정 권한일 때만(`utils/domainAccess.ts` — 서버와 같은 규칙)
- 운영 지식 [검색 | 목록]: 검색 = 채팅과 같은 검색을 카드로(관련도·채널·채팅 노출·같은 글 N곳, v2.210/2.227), 목록 = 글자 거르기·일괄 선택
- 브라우저 기본 alert/confirm 대신 앱 모달(`store/useDialogStore`), 쓰기 실패는 화면에 표시(`useMutation` onError)
- Backend REST API만 호출 · nginx 정적 빌드 서빙 + `/api/*`를 Backend(`:8000`)로 프록시(dev에서도 프론트는 다시 빌드해야 반영)

### 2. Backend — FastAPI (`:8000`)

```
backend/
├── main.py              # 앱 진입점·라이프사이클(DB 풀·임베딩·LLM·에이전트) + 마이그레이션 목록(_MIGRATIONS, advisory lock)
├── agents/knowledge_rag/
│   ├── agent.py         #   채팅 문맥 만들기(지식·정책·참고표·분류 목록) + 근거 없음 즉시 거절 + LLM 스트리밍
│   ├── knowledge/       #   지식·용어집
│   │   ├── retrieval.py #     하이브리드 검색(벡터+키워드, 같은 글 합치기·포함 접기 v2.225/227, 키워드 구제) · 중복 후보 조회
│   │   ├── service.py   #     등록·일괄 적재(중복 판정 needs_review)·미리보기 비교·지식 검색 화면·다시 분할
│   │   ├── api.py       #     지식 코어 API — VOC 등 바깥 창구는 이것만(tests/test_voc_boundary.py)
│   │   ├── glossary_terms.py · glossary_mining.py  # 질문 용어 글자 매칭 · 지식 공백에서 동의어 후보
│   │   ├── locate.py · review.py · team_import_router.py · router.py
│   └── ingestion/       #   적재
│       ├── web_crawler.py · adapters.py  # HTML(컨플·웹)·파일 → 섹션(제목은 h1~h6만), md/txt/pdf
│       ├── chunker.py   #     섹션·단락·고정 길이 분할(제목만 조각 병합)
│       ├── source_ledger.py  # 원문 원장(원문 = 기준, 조각 = 파생, PARSER_VERSION)
│       ├── confluence_sync.py · sync_changes.py · confluence_media.py  # 컨플 연결 동기화·바뀐 페이지·그림
│       ├── team_import.py # 컨플 공간 일괄(팀 적재, 검수 후 게시)
│       ├── locator.py   #     넣을 곳(컬렉션) 판정
│       └── quality.py · analyzer.py · tagger.py
├── service/             # 플랫폼 공통
│   ├── auth/            #   JWT·bcrypt·Fernet, 팀·파트·사용자·로그인 실패 제한
│   ├── chat/            #   채팅 라우터(SSE)·메모리(요약+리콜)·다른 컬렉션 제안
│   ├── policy/          #   정책서 v0(통째 + 파라미터 파생)·검색·참고표·원문 확인·JSON 가져오기(3-way)
│   ├── improvement/     #   정정 요청 원장(신고·AI 수정안·승인 시 버전 교체)
│   ├── eval/            #   평가 게이트·골든셋·자가 개선 루프·기법 대장(techniques.py)
│   ├── decision/        #   AI 판단 원장(ops_ai_decision) — AI 감사 리포트
│   ├── email_voc/       #   VOC 메일 분석(Graph API 수집 → 채팅 경로 검색 → 분석·반복 묶음 → Teams 알림)
│   ├── user_feedback/ · feedback/  # 사용자 피드백(화면 의견) · "답변 틀림" 신고
│   ├── refdata/         #   공통코드·DB 컬럼 참조 데이터
│   ├── admin/ · prompt/ · llm/  # 컬렉션·통계·설정 · 프롬프트(DB 우선) · LLM 프로바이더(inhouse/openai_compat/ollama)
│   └── validation.py · retention.py
├── libs/trustkit/       # 신뢰도 레이어(이식 가능한 팀 자산, 표준 라이브러리만 — 앱 import 금지): ingest·retrieval·query·answer·decision·eval·text
├── core/                # config(pydantic-settings) · database(asyncpg 풀, 범위 SQL) · security · dependencies(권한) · technique_flags(기법 스위치)
└── shared/              # embedding(KURE-v1 싱글톤) · cache(Redis 답변 캐시) · reranker · rate_limit · http_client · rrf
```

**주요 설계 원칙:**
- **AgentRegistry 패턴**: `chat_stream` → `AgentRegistry.get(agent_type).stream_chat()` 위임. 새 에이전트 추가 시 `agents/` 하위 모듈 + Registry 등록만으로 완결. 플랫폼(인증/세션/피드백)과 에이전트(파이프라인)를 분리.
- **DDD 구조**: 도메인별 디렉토리로 schemas/service/router를 응집 — 플랫 구조 대비 코드 탐색·확장 용이
- **비동기 전용**: asyncpg + httpx async — 블로킹 없는 I/O
- **임베딩 싱글톤**: 앱 시작 시 모델 1회 로드, 이후 thread executor로 재사용
- **LLM Provider 패턴**: `inhouse`(사내 게이트웨이) / `openai_compat`(로컬 LLM) / `ollama`(비상용) — `.env LLM_PROVIDER` 또는 설정 화면에서 전환
- **LLM별 프롬프트 형식**: messages 배열(`build_messages()` — openai_compat·ollama), InHouse(DevX MCP API)는 `_build_query()`로 단일 query 문자열
- **신뢰도 레이어 분리**: 검색·적재 품질·평가 기법 본체는 `libs/trustkit`(앱 import 금지), 앱 쪽은 DB 어댑터만 — 설계 `docs/domains/quality/trustkit-design.md`
- **대화 맥락**: ConversationSummaryBuffer + Semantic Recall — 오래된 교환을 LLM으로 요약·벡터 저장, 현재 질문과 유사한 과거 요약 + 최근 2회 raw 교환을 history로 LLM에 전달
- **멀티턴 검색 보강**: 직전 Q+A(각 80자)를 현재 질문에 결합하여 임베딩/검색 — 짧은 후속 질문에서도 이전 대화 맥락이 반영되어 유사도 향상 (추가 LLM 호출 없음)
- **마크다운 답변**: 시스템 프롬프트에 Markdown 형식 지시 포함, 프론트엔드에서 `react-markdown` + `remark-gfm` + `rehype-raw`로 테이블/코드/리스트/HTML 태그 렌더링
- **JWT 인증/인가**: Access Token(30분) + Refresh Token(7일), FastAPI Depends로 라우터 수준 보호
- **컬렉션 담당 파트 기반 권한**: 컬렉션(ops_namespace)의 담당 파트(ops_namespace_owner) 구성원만 그 컬렉션 데이터를 수정, 다른 파트는 조회만. 담당 미지정이면 **관리자만** 수정(v2.142). 컬렉션 만들기·이름·삭제는 관리자만. 운영 지식은 시스템 컬렉션에만(정책 컬렉션엔 정책서만)
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

테이블은 묶음별로만 적는다 — 칸 정의는 실 DB가 항상 정확하고, 바뀐 이력은 `docs/table-definition.md`(마이그레이션 이력).

| 묶음 | 테이블 |
|---|---|
| 조직·사용자 | `ops_team` · `ops_part` · `ops_user` · `ops_namespace`(컬렉션: 시스템·정책, parent_id 트리) · `ops_namespace_owner`(담당 파트) |
| 채팅 | `ops_conversation` · `ops_message`(results JSONB = 근거) · `rag_conv_summary`(대화 요약 벡터) · `ops_query_log`(질의·지식 공백) · `ops_llm_usage`(토큰 사용량) |
| 운영 지식 | `rag_knowledge`(조각: status active · pending_review · staging · staging_review · deprecated · rejected · deleted, content_norm_md5 = 같은 글, heading_path = 소속) · `rag_source_document`(원문 원장 — 원문이 기준, PARSER_VERSION) · `rag_ingestion_job`(적재 작업·품질) · `rag_knowledge_duplicate_match`(승인 대기 근거) · `rag_knowledge_history` · `rag_source_connection`(컨플 연결 동기화) · `rag_import_batch` · `rag_import_page` · `rag_import_rule`(팀 적재) |
| 용어 | `rag_glossary` · `rag_glossary_synonym` |
| 정책서 | `policy_item`(정책 = v0 원문 통째, 버전) · `policy_param`(원문에서 파생한 값) · `policy_chunk`(서술 검색 조각) · `ref_table` · `ref_table_row`(참고표) · `policy_doc_overview`(문서 개요) |
| 참조 데이터 | `ref_common_code` · `ref_db_column` |
| 정정·판단·피드백 | `ops_improvement_item`(정정 요청·원문 확인 kind=source_check) · `ops_ai_decision`(AI 판단 원장 — AI 감사) · `ops_user_feedback` · `ops_user_feedback_file` · `ops_link`(정책·용어·절·참고표·컬렉션 사이 연결 — 대부분 정책→용어 uses) |
| 평가 | `eval_gate_run` · `eval_candidate` · `eval_question_candidate` · `eval_experiment_job` · `eval_improvement_run` · `ingest_check_run`(적재 점검 기록) |
| VOC | `ops_voc_routing` · `ops_email_analysis` · `ops_email_poll_cycle` · `ops_voc_cluster` |
| 설정·운영 | `ops_prompt` · `ops_system_config` · `ops_data_marker` · `schema_migration_run`(마이그레이션 실행 기록) |

- **HNSW**(`vector_cosine_ops`) 벡터 근사 검색 · **GIN** 전문 검색(`content_tsv` 저장 칸 — 조사 제거 `policy_strip_ko`) · pg_trgm
- 모든 데이터는 `namespace_id` FK로 컬렉션에 소속 · `ops_conversation.user_id` CASCADE(사용자 삭제 시 대화도)

### 5. LLM

- **사내 LLM 게이트웨이**(`inhouse`, DevX MCP API) — 운영에서 쓰는 것(운영 `.env`에 `LLM_PROVIDER=inhouse` — compose 기본값은 ollama). 발표·토큰·비용은 Bedrock 기준으로 설명
- **로컬 LLM**(`openai_compat`, vLLM 등 `/v1/chat/completions`) — 개발·측정 기본(측정 스크립트 `--llm local`), 사용량도 기록(v2.222)
- **Ollama**(`ollama`, 호스트 `host.docker.internal:11434`) — 비상용, `.env`나 API로만(설정 화면 선택지 아님)

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
| `LLM_PROVIDER` | compose `ollama` · 코드 `inhouse` | `inhouse`(사내 게이트웨이) · `openai_compat`(로컬 LLM) · `ollama`(비상용). compose가 안 주면 `ollama`로 뜬다 — 운영 `.env`에 반드시 적을 것. dev `.env`는 지금 `openai_compat`(11/05) |
| `OLLAMA_BASE_URL` | `http://host.docker.internal:11434` | Ollama 서버 주소 |
| `OLLAMA_MODEL` | `exaone3.5:7.8b` | Ollama 모델명(비상용, 코드 기본값 — compose가 안 넘김) |
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
| `DEFAULT_TOP_K` | `3` | 채팅 지식 검색 결과 수(top_k) — compose가 안 넘김, 설정 화면(검색 기본값)에서 바꿈 |
| `DEFAULT_W_VECTOR` | `0.7` | 기본 벡터 검색 비중 |
| `DEFAULT_W_KEYWORD` | `0.3` | 기본 키워드 검색 비중 |
| `REDIS_URL` | (비면 캐시 끔) | 답변 캐시(compose: `redis://ops-redis:6379/0`) |
| `POLICY_ABSTAIN_MIN_SCORE` | `0.50` | 근거 없음 즉시 거절 하한(빈 값 = 끔, v2.222) |
| `KNOWLEDGE_COLLAPSE_CONTAINED` | `1` | 검색 top_k 안 포함 조각 접기(v2.225, 0 = 끔) |
| `KNOWLEDGE_KEYWORD_RESCUE` | `0` | 지식 키워드 구제(검색 게이트 미통과로 꺼 둠) |
| (`duplicate_min_similarity` · `duplicate_min_text_similarity`) | `0.88` · `0.8` | 적재 비슷한 글 하한(의미·글자) — 코드 기본값, compose가 안 넘겨 `.env`로는 못 바꿈(의미 하한은 설정 화면 임계값) · 승인 대기는 그중 판단할 상대만(v2.225) |
| `POLICY_REF_CHAT` · `POLICY_REF_OVERVIEW` | `true` · `false` | 참고표·문서 개요를 채팅 문맥에 |
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
| `policy_chunk` · `ref_table` · `policy_doc_overview` · `ops_email_analysis` · `ops_voc_cluster` | `VECTOR(1024)` | 정책 서술 · 참고표 요약 · 문서 개요 검색 · VOC 메일 유사도/클러스터 (`rag_knowledge_history`는 병합 전 보존) |

### 검색 점수 공식

```
final_score = (w_vec × v_score + w_kw × k_score) × (1 + base_weight)
               └벡터 유사도    └BM25 키워드 점수    └문서 자체 가중치
```

- **v_score**: 코사인 유사도 (0~1) — HNSW 인덱스로 근사 탐색
- **k_score**: `ts_rank` BM25 점수 — GIN 인덱스로 전문 검색
- **base_weight**: 문서별 가중치 — v2.121부터 입력 경로가 없고 전부 1.0(상수). 채팅 채택 게이트는 이 값을 걷어낸 원점수로 판단.
  식에서 빼려면 메일 VOC 인용 임계값 재측정이 먼저(컬럼 삭제 때 같이)
- 그 뒤: 같은 글(content_norm_md5)은 한 자리 + 사본 소속 모두 · 채택 조각끼리 포함 접기 · (켜면) 키워드 구제 — 단계별 상세는 `docs/flow.md` 지식 검색

테이블 묶음은 위 §4, 칸 정의는 실 DB(이력은 `docs/table-definition.md`).
