# OpsLens 시스템 아키텍처 (v2.238)

## 개요

OpsLens(구 Ops-Navigator, 2026-10-06 이름 변경)는 정책서·매뉴얼·운영 지식·공통코드처럼 흩어진 IT 운영 지식을 한곳에
모아 근거와 함께 답하는 **IT 운영 지식 통합 AI 플랫폼**이다. 틀린 답은 사용자 신고 → 담당자 검토로 바로잡는다.
사용자는 에이전트를 선택해 목적에 맞는 AI를 사용한다: 지식 기반 Q&A(KnowledgeRAG).

> Text-to-SQL 에이전트는 v2.51에서 `dev_0`/`main`에서 분리·제거됐다(현재 과업 범위 아님) — 코드는
> `archive/with-text2sql` 브랜치(2026-09-03 시점 스냅샷)에 형상관리용으로 보존돼 있다.
> MCP 도구 에이전트는 v2.67에서 완전 제거됐다(Text2SQL과 달리 브랜치 보존 없음 — 배경은
> `docs/archive/architecture-history-v2.0-v2.99.md` v2.67 항목).

**주요 이력 요약** (스키마 변경 상세는 `table-definition.md` §20 마이그레이션 이력 참조)
- v2.238 (2026-11-19, dev_0): **컨플 9공간 최종 적재 중 찾은 결함 수정 + 참조 자리 표시·문서형 첨부 글 + 다시 파싱 + 증분 자동 연결 + 빈 DB 기동**(태훈 "적재하면서 찾은 결함도 수정"·"6,7 고치고"·"추천안대로 최적화"·"증분 자동 반영"). 최종 적재: SP·ERP·POS·CDP·홈페이지·딜리버스·외부서비스·온라인스토어·앱 9공간 사용 중 컨플 문서 7,608(앱·CDP는 11/05 결정대로 읽기 제한 포함). ① 미리보기 시간 초과(SP) — 위치 추천 프로필이 컬렉션 전 조각 × 용어를 훑음 → 최근 2,000조각 표본 + 컬렉션당 한 쿼리 + 캐시(`locator.PROFILE_SAMPLE`, 표본·전체 순위 비교로 확인). ② 기동 시간 초과 — `logical_document_id` NULL 2.7만 채우기 → 넣을 때 채우는 트리거(#132). ③ 교체 확인 — 사람이 고친·지운 조각이 있는 페이지를 새로 적재하면 그 손질이 조용히 사라지던 것 → 적재 막힘 "교체 확인 N건" + 페이지별 [교체 허용]·[이 페이지들 빼기](`team_import.REPLACE_TOUCH_SQL`, 허용은 그 컬렉션에만). ④ 읽기 제한 결정 이어받기 — 같은 팀·공간 직전 적재에서 [읽기 제한 페이지도 넣기]를 켰으면 새 적재 기본값도 켬 + 배지(#133, `GET /team-import/restricted-default`). ⑤ 참조 자리 표시(파서 2) — Jira 매크로·Jira 목록·첨부 링크가 통째로 사라지던 것(567페이지) → `[Jira: KEY]`·`[Jira 목록: … — 원문 페이지에서 확인]`·`[첨부: 이름]`(문단 안이면 인라인, CDATA·주석 안은 안 건드림 — 무작위 성질 시험). ⑥ 첨부 글(파서 3, 문서형만) — pdf·docx·pptx·txt·md 본문을 페이지 끝 `첨부: 이름` 구역으로(페이지당 10개·15MB·압축 해제 60MB·8천 자 제한, 다른 페이지 첨부·그림 속 참조 제외), 표 계산(xlsx·csv)은 이름만(행 덩어리가 검색을 흐림 — 태훈 "추천안대로"), 원장 `media.attachments`에 글째 보존(다시 만들기·다시 분할이 같은 결과). 원장 첨부 글 줄 → 사용 중 조각 포함 ERP 52,497/52,498 · 나머지 100%. ⑦ 다시 파싱 — 동기화 `mode=reparse`: 원장 파서 판(`media.parser`)이 낮은 페이지만 다시 받기, 원문 그대로이고 새 파서로 달라질 게 없는 문서는 판만 올림(`bump_unchanged_parser`), 동기화 기준 시각은 안 옮김. 115연결 전부 완료(실패 0). ⑧ 증분 자동 연결 — 기본 위치 적재가 끝나면 증분 연결 + 묶음 규칙(넣은 곳 하나 = 그곳·여러 곳 = 규칙 없음·뺀 묶음 = 제외·새 묶음 `*` = 기본 위치), 사람 규칙이 있는 공간은 안 건드림, 롤백 때 그 배치 자동 규칙만 지움(#134). 연결 115(새 92·이어받음 23)·중지 2. ⑨ 빈 DB 기동 — `ops_namespace`가 없으면 `db/base_schema.sql`(init 사본, 시험으로 같음 고정) 먼저(#135, AWS 첫 기동 실패). 데이터: 옛 문서 교체(사람 지운 조각이 교체 확인에 걸려 HP 403/451 실패 → 옛 문서를 교체됨으로 정리 후 다시), 지식 원장에 안 묶인 컨플 외 조각 29건 복원(태훈 승인 — 지웠더니 업무지식 정답률 하락). 정확도(검색, 같은 날): 정책서 93.7%/78.9% · CX 83.9% 그대로, 업무지식 94.3 → 90.6%(적재 전 → 후, 남은 6문항은 정답 닻이 옛 조각 줄이라 못 찾음 — 다시 짚으면 5/6, 골든셋 다시 짚기는 사람 확인), 다시 파싱 전후 게이트 3축 통과(나빠짐 0). ERP 첨부 검증 25문항(로컬 LLM 생성): Recall@3 76%·상위 10 88%(첨부가 없으면 답이 데이터에 없음). 검증: 백엔드 2,581 통과 · E2E team-import-1119 2 + 기존 · 실 DB 롤백 시나리오(자동 연결·규칙 갱신·롤백·사람 규칙 보호·제한 이어받기) · 독립 리뷰(M2·M3·L1 등) 반영 · 수정분 재리뷰 중간 1(자동 규칙 뒤 사람이 [묶음 연결]해도 자동 표시가 남아 옛 배치 롤백이 사람 규칙을 지우고 '*'가 남던 것 → `team_import.save_human_rules`: 사람 규칙으로 바꾸고 그 공간 자동 규칙 지움)·낮음 2(연결 전 적재 상태 잠가 확인·다시 파싱 같은 페이지 한 번) 반영, 낮음 1 백로그.
- v2.236 (2026-11-17, dev_0): **게시 전 골든셋 확인 — 검수 대기 배치에 [골든셋 확인] + "골든셋 회귀" 카드 · 나빠지면 [그래도 게시]**(태훈 "시안 그대로 진행", 11/16 결정 "게이트 떨어지면 보류 + 사람이 게시 판단"). 기존 [검색 확인]은 이번 조각으로 새로 만든 질문만 봐서, 옛 버전을 내리며 다른 문항 정답이 사라지거나 새 조각이 정답을 밀어내는 건 못 봤다. `agents/knowledge_rag/ingestion/golden_check.py`: 이 배치가 닿는 컬렉션의 골든셋 문항(eval_out 닻 파일 + DB 실사용 골든셋)을 **지금 게시본** vs **게시했다면**(active − held_plan의 replacing_pages·replacing_docs로 내려갈 옛 버전 + 이 배치 staging, 강제 승인 대기 작업은 제외 — `_switch_staged`와 같은 조건) 두 쪽에서 같은 벡터 검색(상위 10)으로 찾고, 정답은 닻으로 각 쪽 조각에서 다시 찾는다. 나빠짐 = 상위 3 → 밖 · 좋아짐 = 반대 · 정답 사라짐 = 게시하면 닻이 안 맞음(내용 바뀜, 나빠짐과 겹쳐 세지 않음). LLM 0. 게시는 막지 않는다 — 나빠짐·정답 사라짐이 있으면 버튼이 [그래도 게시]로 바뀌고 확인 창에 수. 리포트 창: 문항마다 지금 → 게시하면 순위 · 게시하면 대신 나오는 것(이번 배치 표시). API `POST /api/knowledge/team-import/{id}/golden-check`(뒤에서, 도는 중 표시는 조건부 UPDATE로 잡아 두 번 눌러도 하나), 결과 `rag_import_batch.golden_check`(마이그레이션 #129), 재시작 때 끊김 표시. 한계(툴팁에 보임): 벡터만(키워드·다시 정렬 없음) · 문항이 물은 컬렉션 안에서만 · 골든셋 파일은 운영 이미지에 없음(없으면 실사용 골든셋만, 둘 다 없으면 건너뜀). 독립 리뷰: 높음 1(라우트의 도는 중 잡기가 409 raise 아래 들여써져 실행 안 됨 → 두 번 눌림·화면이 도는 중을 못 봄 — E2E에서 카드가 멈춘 진짜 원인, 화면 쪽 임시 대기 코드는 걷어냄) · 중간 1(가져오기 실패 시 표시 안 지워짐) · 낮음(게시 순간 다시 계산하는 replacing_docs 반영·결과 시각 표시·문구) 반영. 무작위 불변식 시험(실제 _switch_staged 결과 = "게시했다면" 집합, 40배치)이 같은 페이지를 교체하는 작업 둘(게시는 작업 id 순이라 뒤 작업이 앞 작업 새 조각도 내림)의 어긋남을 찾아 반영. 실서버 동시 두 번 POST = 200·409. 검증: 백엔드 2,512 · 실 DB 롤백 9(같은 내용 다시 넣기 = 나빠짐 0 · 정답 줄 바뀜 = 정답 사라짐 · 새 조각이 밀어냄 = 나빠짐 · 강제 승인 대기 제외 · 옛 버전 내림 · 잡기 한 번) · E2E golden-check + batch-publish·publish-progress.
- v2.235 (2026-11-16 밤, dev_0): **골든셋 자동화 1·2단계 — 정답 닻 · 범위 밖 문항 · 골든셋 후보**(제안서 TVjEPGCmiJ2EToJ1pfFaZH, 태훈 승인 — 배포 전 전체 새로 넣기 대비). ① 정답 닻(`trustkit/eval/anchors.py`): 지식 골든셋 정답을 조각 id 대신 정답 조각에만 있던 줄의 해시로(같은 페이지 범위·사본 제외, 고유 줄 없으면 80% 기준) — 다시 적재해 id가 바뀌어도 찾음, 못 찾으면 개수로 보임. 컨플 106·CX 93 변환: 같은 데이터에서 정답 잃음 0 · 검색 93.4→94.3%(정답을 통째 담은 상위 조각 인정 +1문항)·CX 83.9% 그대로 — 게이트는 닻 기준끼리 비교. 변환 `scripts/golden_anchors.py`. ② 생성기 `confluence_synth_questions.py`: 정답 닻으로 적음 + `--oos`(범위 밖 = 정답 '모른다') · 사람·연락처 질문 코드 필터(PERSON_Q — 표본 9 중 사람 판정 개인정보 5와 일치). 범위 밖은 검색·정답률에서 빼고 `scripts/eval_abstain.py`로 따로(거절 = 정답, 단정 = 단정 오답). ③ 골든셋 후보(평가 게이트 카드 + 목록, 시안 A): 답변 오류 판정·지식 없음 거절 중 아직 안 정한 것을 그때그때 모아 [정답 = 근거]·[정답 문서 #]·[모른다가 정답]·[제외] — 사람이 골라야만 골든셋(자동 추가 없음, 같은 질문 한 줄·잠금). API `GET /api/eval/golden-candidates`·`POST /api/eval/golden-candidates/decide`(관리자), `--real`로 지식 측정에 실사용 문항(물은 컬렉션 그대로). 마이그레이션 #128 `eval_question_candidate.query_log_id`. 검증: 백엔드 2,503 통과 · 실 DB 롤백 시나리오 · E2E golden-candidates · 독립 리뷰 중간 3(질문 끝 줄바꿈이면 정한 뒤에도 남음·실사용 문항을 정답 컬렉션으로 옮겨 실패 가림·빈 닻 KeyError)·낮음 5 반영/백로그 · 재리뷰 낮음 1(SQL이 BOM을 지워 파이썬 strip과 어긋남 → 성질 테스트) 반영. 남은 것: 게이트 통과 뒤 게시(배치 화면 시안 먼저) · 범위 밖 채점기 사람 일치도(표본 단정 1건을 채점기가 놓침).
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
- v2.220 ~ v2.229(11/06~11/11: 반입 전 검사 C 결정·승인 대기 재발 방지·토큰 낭비 점검·정책 정정·검색 접기·밤샘 점검 위반) 이력은 `docs/archive/architecture-history-v2.220-v2.229.md`.
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
