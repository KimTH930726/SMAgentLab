# OpsLens 시스템 아키텍처 (v2.177)

## 개요

OpsLens(구 Ops-Navigator, 2026-10-06 이름 변경)는 정책서·매뉴얼·운영 지식·공통코드처럼 흩어진 IT 운영 지식을 한곳에
모아 근거와 함께 답하는 **IT 운영 지식 통합 AI 플랫폼**이다. 틀린 답은 사용자 신고 → 담당자 검토로 바로잡는다.
사용자는 에이전트를 선택해 목적에 맞는 AI를 사용한다: 지식 기반 Q&A(KnowledgeRAG).

> Text-to-SQL 에이전트는 v2.51에서 `dev_0`/`main`에서 분리·제거됐다(현재 과업 범위 아님) — 코드는
> `archive/with-text2sql` 브랜치(2026-09-03 시점 스냅샷)에 형상관리용으로 보존돼 있다.
> MCP 도구 에이전트는 v2.67에서 완전 제거됐다(Text2SQL과 달리 브랜치 보존 없음 — 배경은
> `docs/archive/architecture-history-v2.0-v2.99.md` v2.67 항목).

**주요 이력 요약** (스키마 변경 상세는 `table-definition.md` §20 마이그레이션 이력 참조)
- v2.177 (2026-10-27, dev_0): **품질 화면 가시성(사용자 "가시성과 단순성이 핵심 — 안 그럼 안 쓴다") + 내렸던 화면 되살림 + 옛 표 삭제.**
  ① 틀린 문항 리포트를 좌(고칠 방법을 단계별로 묶고 겨냥 문항 수 · [원인별] 전환 · "고칠 방법 없음")·우(고른 방법의 결정 카드 + 그 문항 8개씩 페이지)로 —
  문항↔고칠 방법 연결이 보이게. 측정 안 되는 문항은 점수 줄의 숫자 하나 → 창. ② 문항 상세 창 ①~⑥ 상자, 판정 색 줄, ⑤ 저장 구조 = 정답 데이터 카드(원문 글자·파라미터·표·행 칩) +
  꼬리표마다 왜 문제인가·해 볼 기법, ⑥ 고칠 방법 = 후보 카드(상태·마지막 측정). ③ 평가 게이트 숫자 카드를 누르면 설명 창, 이름 바꿈(놓친 문항 → 정답을 못 넘긴 문항, 첫째로 찾음 → 정답이 1순위).
  ④ 정정 요청 기본 = 한 건씩 보기(이전/다음), 한꺼번에 승인은 [목록]에서만. ⑤ **품질 › AI 판단·답 기법 다시 올림**(v2.172에서 내리며 되묻기 관리·"AI가 틀렸나요?"·원문 확인 오탐률·
  다른 컬렉션 누름 비율이 볼 곳 없이 사라졌던 것): AI 판단 = 카드 5장(위치·되묻기·원문 확인·다른 컬렉션·개선 후보) → 창에 **"지금 기준"**(답 기법 대장의 숫자·근거) + 분포 + 사람이 바꾼 건에
  "AI가 틀렸나요?"(`POST /api/eval/decisions/{id}/verdict`, 바꾼 사람만 — 아니면 403). 답 기법 = 흐름 단계 줄 + 기법 한 줄씩 → 창. 대장에 단계 "적재"와 위치 판정·참고표 되묻기·원문 확인·후보 추천 기준 추가.
  ⑥ 컬렉션 고르는 창 카드에 처리 전 **정정 대기 n · 원문 확인 n** 칩(`/api/namespaces/detail`의 `pending_corrections`·`pending_source_checks`).
  ⑦ 다른 컬렉션 찾아보기(C2) 부하 측정(`scripts/load_collection_suggest.py`, 동시 0·10·30·60): 동시 검색만으로 18ms → 359ms(PC 사양 영향), 찾아보기가 다른 검색을 더 늦추진 않음,
  찾아보기 자체는 동시 60에서 3.8~5.3초 → **2초 넘으면 건너뜀**(`SUGGEST_TIMEOUT`, 답은 안 기다림) + 컬렉션·용어 범위 60초 캐시. ⑧ 참고표만 채팅에(#12, 개요 따로 `POLICY_REF_OVERVIEW` 기본 꺼짐) + 표 읽는 법 한 줄:
  참고표 73.3 → 80.0%, 참고표 되묻기 83.3 → 73.3%, 기존 441문항 −0.5%p·답 없음 −2 → **계속 꺼 둠**. 채점은 이번 1회만 Claude(사용자 허용 10/27) — 같은 답을 로컬 채점은 사람 문항 79.7%·전체 89.8%,
  Claude는 96.2%·93.0%(Claude가 너그럽고 로컬이 엄격 — 어느 쪽이 맞는지는 사람 40문항 눈가림 채점으로 정할 일). ⑨ 옛 v1 표 `policy_track2_run`·`eval_answer_run` 삭제(#93, 사용자 승인 — JSON 백업 eval_out/backup_v1).
- v2.176 (2026-10-26 밤, dev_0): **옛 v1 평가 화면 정리(D, 사용자 10/26 승인)** — 어디서도 안 쓰이던 화면 2(실험실 PolicyLab·답변 정확도 AnswerEvalHistory)·
  상태(useTrack2Store)·API 4(`/api/policy/track2/axes|run|history`, `/api/policy/answer-eval/history`)·`service/policy/answer_eval.py`·`eval_answers.py report --save`를 뺐다
  (판정·기록은 `eval_gate.py --save` 한 곳). 표 `eval_answer_run`(5행)·`policy_track2_run`(8행)은 JSON 백업(eval_out/backup_v1)만 — **삭제는 대상별 확인 뒤**
  (그때 마이그레이션 CREATE와 `track2.save_run/list_run_history`·`pipeline_stats`의 읽기도 함께).
- v2.175 (2026-10-26 밤, dev_0): **원문 확인 카드 정리(C3 1단계, 사용자 10/26 결정)** — 카드의 정책 칩에서 연 정책으로 올린 수정 제안 = 카드
  "정정 요청 중 #n"(#92 `ops_improvement_item.source_check_id`) → 승인되면 같은 트랜잭션에서 카드 자동 해결(메모 "정정 #n 승인"), 반려되면 카드는 처리 전 그대로
  ("정정 #n 반려됨"). [닫기] → **[문제 없음]**(사유 4개: 원문이 맞음·이미 고쳐짐·중복·기타 — 기타만 메모 필수), 사유는 AI 판단 원장(kind `source_check`)에도
  (검사기가 짚었는데 사람이 문제 없다고 한 비율 = 오탐률 — 해결·정정 승인도 "맞게 짚음"으로 남겨 분모를 만든다). 연결은 그 정책을 짚은 같은 영역 처리 전 카드만.
  정책 추가·폐기·옮기기(C3 2단계)는 다음.
- v2.174 (2026-10-26 밤, dev_0): **채팅 "다른 컬렉션에서 찾아보기"(C2, 기본 꺼짐 — 설정 `chat.collection_suggest`)** — 답을 낸 뒤 다른 업무 컬렉션에서
  같은 검색, 더 맞는 지식이 보이면 답 아래 한 줄 + [그 컬렉션에서 찾아보기](같은 질문을 그 컬렉션에서 한 번 더, 보는 컬렉션은 그대로). 캐시 답에도.
  제안·누름은 AI 판단 원장(kind `collection_suggest`). 제안으로 받은 답은 `answered_namespace` — "답변 틀림"이 답한 컬렉션 건으로 가고 "○○ 지식으로 답함" 표시.
  다른 컬렉션 검색은 동시 2개까지(커넥션 풀 보호), 설정 확인은 30초 캐시(꺼짐이면 커넥션도 안 잡음). 하한 **0.48**: 합성 거짓 제안 0/106 · 맞게 84% / **실사용 질문 174개**(질의 기록, 로컬 LLM 판정)
  제안 11 → 0.45면 맞음 8·틀림 2, 틀린 2건이 최저 점수라 0.48로 올림 → 맞음 8·틀림 0(`scripts/eval_collection_suggest_real.py`). `service/chat/collection_suggest.py`.
- v2.173 (2026-10-26 밤, dev_0): **참고표·문서 개요를 채팅 문맥에(v0 #12, 기본 꺼짐 — `POLICY_REF_CHAT`)** — 요약 벡터로 표 1·개요 1을 찾아 표 압축본(결정 열 +
  결과 열, 크면 질문에 나온 조건값으로 행 거름·잘리면 표시)·개요 본문을 문맥에, 결정 조건이 질문에 빠졌으면 되묻기(선택지 = 그 열 값, 표가 정책 서술보다
  강할 때만). 붙이는 규칙 = 유사도만으론 못 가름(0.6에서도 정책 질문 42/455) → **같은 질문의 정책 서술 최고점과 비교**(표 ≥ 서술 − 0.05 · 개요 ≥ 서술 + 0.05).
  측정(같은 날 채점, 기준 #12): 새로 재게 된 44문항 개요 86% · 참고표 73% · 되묻기 60%, 기존 441문항 −0.5%p(오차 안) — 사람 문항 −1.2%p는 **답·문맥이 같은데
  채점만 바뀐 흔들림**(같은 날·같은 답 5문항 뒤집힘), 답 없음 −2문항은 진짜 부작용(도전 과제 질문에 표가 붙어 답함 — 점수로 못 가름). 골든 측정에 참고표·개요 문항 포함
  (`eval_golden_v0`·`eval_answers`, 이어 붙인 표 키 "a+b" 통째·조각 둘 다). 파라미터 파생 실험 #18(카드 10장)·#19(3장)은 사람 문항 효과 없음 → 채택 안 함(설정도 뺌).
- v2.172 (2026-10-26, dev_0): **적재 검수 루프 — 설계대로 적재됐나 + 적재한 데이터가 채팅에서 쓰이나(사용자 "지금 데이터 구조가 잘 적재돼 있는지
  자체 검수하는 개선 루프").** ① `scripts/check_ingest.py`: 설계서 완료 기준 = 적재 계약(업계 데이터 계약·기대값 검사 방식)으로 16개 검사(LLM 0) + **쓰임 검사**
  (정답이 참고표·개요인 골든 문항을 실제 채팅 문맥 조립에 넣어 정답 데이터가 문맥에 들어가나 — 코드를 읽어 적지 않고 잰다). 어긋난 검사마다 영향 문항·정답률.
  첫 결과(18개 중 어긋남 4): **파라미터 파생 981/981**(v0 설계 #9 미실행 — 현행 파라미터 0건, 채팅 파라미터 경로가 빈 표) · 지식 중복 8/74 ·
  **참고표 30/30 · 개요 14/14 문맥에 없음**(#12 미연결). 오탐 2(머리는 임베딩에만 붙음 · 직접 입력 지식은 위치 없음)는 확인 후 검사 수정.
  ② #90 `ingest_check_run`(--save) · `GET /api/eval/ingest-checks/latest`(화면은 ⑥ 리포트 안으로). ③ 어긋난 검사 → 다음 진단(`eval_improve.py`)이 개선 후보로
  (`ingest_candidates` — 처리 기법 후보와 같은 실험·측정·운영 반영 흐름, 사람 승인). 답 기법 대장에 파라미터 파생·참고표 채팅 연결·지식 중복 정리·적재 계약 4개.
  ④ 파라미터 파생(v0 #9): `service/policy/param_derive.py`(값이 원문에 글자로 있는 것만 — 불변식 테스트) · `scripts/derive_policy_params.py extract/load`
  (로컬 LLM, 981개 중 341개 정책에서 899값, 만든 값 2개 버림). 운영 DB엔 넣지 않고 사본 DB(opsdb_exp)에서 측정 중. ⑤ 후보의 측정 연결은 결정과 별개로 이어받음.
  ⑥ **틀린 문항 리포트**(사용자 10/26 "AI가 내린 답을 관리자가 리포트 받고 개선할 수 있게") — 평가 게이트 탭 = [리포트][검색][답][즉석 질의]. 리포트 한 화면에
  지금 점수(사람이 쓴 질문 · 전체 · 검색) → 틀린 문항마다 질문 · 왜(첫 실패 칸) · **저장 꼬리표**(정답 데이터가 어떻게 저장돼 있나 — 원문만·파라미터 0개 · 표·구간 ·
  질문 표현이 문서와 다름 · 정책 여러 개에 흩어짐 · 문맥에 못 들어감 · 참고표/개요에만 있음 등, 데이터 사실에서만) · 고칠 기법 → 측정도 안 되는 문항(쓰임 검사) →
  고칠 방법(기법별 **잠근 시험지 효과**, AI 문항에서만 잰 건 "효과 확인 안 됨") + 사람 결정. `service/eval/report.py`·`GET /api/eval/report`, 문항 상세에 ⑥ 저장 구조.
  옛 [개선]·[적재 검수]·[판정 이력]·[실사용 문항] 탭과 품질 › AI 판단·답 기법 메뉴는 뺐다(데이터·API는 그대로, 실사용 문항은 정정 승인 때 자동으로 시험지에 들어가고
  리포트에 "실사용" 표시). **[운영에 반영했어요] = 그 측정이 지금 점수(기준선)** — 화면에서 기준선 고르기를 없앰(같은 잠금으로 한 트랜잭션).
  ⑦ [검색]·[답] 탭: 표 → 숫자 카드 + 층별 막대(가장 약한 층 강조, 답은 낮은 순), 층 줄·카드를 누르면 그 층의 놓친·틀린 문항(`GET /api/eval/gate-runs/{id}/layers`,
  판정 숫자와 같은 규칙 — 층별 합 = 카드 숫자).
- v2.171 (2026-10-26, dev_0): **품질 화면을 관리자가 쓸 수 있게 + 잠근 시험지 + 실사용 문항(사용자 10/26 결정 A·C1·잠금·실사용).**
  ① 문항 상세 창 — 문항 번호를 누르면 질문 → 정답 → 검색(정답 정책을 넘겼나) → 답(회차별·채점 이유) → 왜(첫 실패 칸·AI 묶음) → 후보 → 측정 결과(이 문항이
  좋아졌나). 질문·답·이유는 **결과 파일에서 읽기만**(`service/eval/files.py`, DB 저장 없음, 관리자만 `GET /api/eval/gate-runs/{id}/questions/{qid}`).
  ② [개선] 버튼을 할 일 이름으로(실험해 보기 · 나중에 · 안 하기 · 운영에 반영했어요 · 결정 취소) + 진행 단계 막대(제안 → 실험 → 측정 → 운영 반영),
  #88 `eval_candidate.measured_gate_run_ids`(`eval_gate.py --candidate`)로 "측정 #11 통과" 표시. ③ [판정 이력] 한 줄 문장(✔ 통과 · ✖ 실패 + 무엇이 얼마나) +
  맨 위 "지금 비교 기준" + "이걸 비교 기준으로"(확인 창). ④ AI 묶음 [맞아요]/[다시 묶기] — 맞아요 한 이름은 다음 진단이 그대로 씀.
  ⑤ 품질 › AI 판단: 사람이 바꾼 건마다 AI 고른 곳·이유 → 사람 · "AI가 틀렸나요?" 답. 품질 › 답 기법: 흐름 그림 위 기법 + 왜 있나·효과(판정 번호)·걸린 후보·바꾸려면.
  ⑥ **잠근 시험지**(hold-out): 사람이 쓴 문항·실사용 문항(`LOCKED_TYPES`)은 진단·AI 묶음·후보를 만들 때 빼고 판정 때만 본다. [답] 맨 위 숫자 = "사람이 쓴 질문"
  정답률, 운영 반영은 잠근 층에서 내려간 측정이면 막힘(`adopt_error`). 근거: 10/23 2회차 전체 +0.9%p인데 사람 문항 −1.2%p — 같은 문항으로 고치고 채점한 착시.
  ⑦ **실사용 문항** #89 `eval_question_candidate`: 채팅 신고가 정정 승인되면 그 질문 = 시험지 후보(정답 = 고친 정책) → [실사용 문항] 탭에서 [시험지에 넣기] →
  골든 측정(`eval_golden_v0`·`eval_answers`)이 "실사용"(real) 층으로 함께 잰다. 지식 정답 문항은 쌓기만(지식 측정 연결은 다음).
- v2.170 (2026-10-23 밤, dev_0): **자가 개선 루프 — 정답률 → 왜 틀렸나 → 개선 후보 → 사람 검수(사용자 "평가 게이트의 핵심") + AI 판단·답 기법 화면.**
  ① 진단(`trustkit.eval.diagnose`, LLM 0): 틀린 문항(회차 과반 비정답)을 흐름의 **첫 실패** 한 칸에 — LLM 오류 · 답 없는 질문에 답함 · 검색 실패 · 근거 있는데 거절 ·
  목록 덜 나옴 · 문맥 있는데 틀림(오류 분석 축 코딩, Husain·Shankar). 리플레이 = 저장된 측정으로 규칙을 바꿨을 때 문항별 고침·망가짐을 다시 셈.
  ② 후보(`service/eval/improve.py`): 원인마다 볼 기법(대장 키)·바꿀 것·잴 방법(LLM 0 리플레이 / 검색 재측정 / 답 재측정). 근거 없음 하한은 바로 리플레이 —
  기준선에서 **어느 하한도 망가짐 없이 고치는 문항 0**(답 없는 질문 7개는 점수가 높아 하한으로 못 거름, 0.55부터 맞던 답이 망가짐) → 기각 추천 + 생성 쪽 후보.
  ③ `scripts/eval_improve.py --reasons --save`: 채점 이유를 **로컬 LLM**이 묶어 가설(묶음 이름·qid만 저장, 이유 원문은 DB·화면에 없음). 기각·보류는 다음 진단이 이어받음.
  ④ #87 `eval_improvement_run`·`eval_candidate`(상태 제안·시도 승인·보류·기각·채택, 채택은 시도 승인 뒤에 잰 통과 게이트와 연결될 때만 — DB CHECK + API, 기각·보류·승인은 다음 진단이 이어받음).
  ⑤ 평가 게이트 [개선] 탭(원인 막대 · 후보 카드 · 리플레이 표 · 가설 · 관리자 검수), 품질 › **AI 판단**(원장 종류별 사람 확정·변경·틀림·확신인데 틀림 ·
  되묻기 고른 분포 · 개선 후보 추천대로 결정/반대, 관리자), 품질 › **답 기법**(기법 대장 14개 — 단계·기준값(운영값)·근거·코드 위치, 테스트가 위치를 검사).
  ⑥ API `GET /api/eval/improvements/latest` · `POST /api/eval/candidates/{id}/review`·`/adopt`(관리자) · `GET /api/eval/techniques` · `GET /api/eval/decisions`(관리자).
- v2.169 (2026-10-23, dev_0): **평가 게이트 2단계 — 탭 [검색] [답] [판정 이력] [즉석 질의](10/23 목업 검수).** 옛 "골든셋 실행"(v1 Track2)·
  "답변 정확도"(v1 이력)는 v0 숫자를 못 보여 줘 판정 기록(#86) 읽기로 바꿨다(옛 화면·표 정리는 5단계, 대상별 승인). ① [검색]: 기준선 Recall@10·Top-1 + 층별.
  ② [답]: 정답률 + 95% 구간 + 라벨 분포 + 채점 조건 + "검색은 맞았는데 답이 틀림" 문항 번호. ③ [판정 이력]: 판정마다 결과·검색 좋아짐/나빠짐·답 차이 [구간]·커밋,
  펼치면 층별 전/후 표 + 나빠진/좋아진 문항 번호, 관리자 [기준선으로](통과한 판정만). ④ 목록 층은 검색 파일(list)·답 파일(navigation) 이름이 달라 화면에서 "목록" 하나로.
  ⑤ 문항은 번호만 — 원문은 DB·화면에 두지 않는다(eval_out/ 파일). 업계 대조(LangSmith 비교 화면·promptfoo CI)는 eval-gate-redesign §4-5.
- v2.168 (2026-10-23, dev_0): **평가 게이트 개편 1단계 — 판정 결과를 기록으로(화면 변화 없음).** 지금까지 게이트 판정은 터미널에만 찍히고
  사라져, 화면(옛 v1 이력)은 v0 숫자를 못 보여 줬다. ① #86 `eval_gate_run` — 판정 1회 = 1줄(검색·답 판정 그대로 + 후보 쪽 헤드라인 + 채점 조건),
  원문 없이 qid·라벨·숫자만. 기준선은 한 행(부분 고유 인덱스). ② `scripts/eval_gate.py --save "라벨"`(판정과 함께 저장 — 통과하면 기준선이 됨,
  채택 안 한 실험은 `--keep-baseline`), `--baseline --save`(판정 없이 단독 측정 → 기준선), `--commit`·`--run-at`(옛 파일 재기록용). 터미널 출력은
  옛 스크립트와 글자 단위로 같음(10/21 세 쌍 비교). ③ `trustkit.eval.summary` — 골든셋 버전(문항 집합 지문, 전/후 세트가 다르면 경고·기록),
  검색·답 헤드라인(층별·라벨 분포·구간), "검색 맞고 답 틀림" 문항(답 생성 쪽 후보). ④ API `GET /api/eval/gate-runs`·`/{id}`(로그인),
  `POST /{id}/baseline`(관리자 — 실패 판정은 409). ⑤ 10/21 기록 4건 재기록(LLM 호출 0): 기준선 89.7% → 프롬프트 정리 88.7%(통과) → 동의어
  89.6%(통과) → 목록형 답 89.9%(통과, 현재 기준선), 검색 Recall 375/381·Top-1 312/381 동일. ⑥ `eval_answers collect`가 답 생성 모델(`answer_model`)을
  행에 기록, `--golden` 기본값 v0.
- v2.167 (2026-10-23, dev_0): **"목록"은 "전부"가 아니다 — 큰 목록이면 되묻기 + 목록 답 표시(사용자 10/23 실사례).**
  "배송 정책 목록 말해줘"가 36개를 한 번에 늘어놓던 것: 목록 질문 알아보기(NAV_SIGNAL_RE, "목록·리스트" 포함)가 곧 "전체 명시"로 쓰였다 →
  전체 명시는 "전부·모든·전체·모두·한번에·한눈에·다보여·싹다"만(`category_list._ALL_WORDS`, "목록" 뺌). 20개 넘으면 범위 선택지로 되묻는다.
  목록 답엔 근거 카드(1건 역추적) 위에 "📑 목록 답 · ○○ 정책 n건에서"(ChatContext.list_answer → 메시지 metadata). 측정: 골든 441 검색·재질문 문항별 동일
  (골든에 "목록" + 20개 초과 문항이 없어 못 잡았다 — 평가 게이트 개편 때 문항 보강), 실채팅 E2E 2건.
  되묻기 판단(explicit_all = 전체 명시만)과 목록 답 판단(is_listing = 목록 질문 알아보기 또는 전체 명시 — 문맥 거르기 v2.138·표시)을 나눔(리뷰 H1:
  둘을 함께 풀면 20개 이하 "목록" 질문이 문맥 거르기를 잃는다). 측정 상세에 LLM 문맥 지문(ctx_hash) — 원래 코드 대비 441문항 문맥 달라짐 0.
  목록 답 표시는 캐시 응답에도, 문맥에 다 못 실린 목록은 "n건 중 m건으로 답함".
- v2.166 (2026-10-23, dev_0): **넣을 곳 카드 다듬기(사용자 10/23).** [잘 모르겠어요] 뺌 — AI가 추천해 미리 고르고 사람은 그대로 두거나 다른 곳을 고른다.
  [다른 곳…] = 보는 컬렉션 고르기와 같은 창(CollectionPickerModal로 뽑아 사이드바·등록이 같이 씀, 담당 아닌 곳엔 "승인 요청" 표시).
  추천 이유를 보이게(AI 판단·이유 / 비슷한 지식 n건 분포 / 둘이 같은지) + 원장 signals에 AI 이유 한 문장(300자 이하, 자체 개선 리포트 재료).
- v2.165 (2026-10-23, dev_0): **E2 — 지식 등록에 넣을 곳 카드(위치 판정 연결).** 설계 collection-design §7·§17, 흐름 flow.md §5.
  등록은 지금 보는 컬렉션 담당이 아니어도 된다(조회 전용 제외). 미리보기 뒤 넣을 곳 카드(AI 제안 · 애매하면 버튼 · 다른 곳…) → 고른 곳으로 등록 + decision_id.
  담당이 아닌 곳 = 제안: 전부 승인 대기, 원문·컨플루언스 페이지 연결 뗌(담당의 기존 조각을 같은 원본 교체로 폐기하지 않게), 판정 줄이 내 것·확정 전일 때만.
  미리보기 원문 초안은 고른 곳으로 옮김(내 초안·draft만, /bulk·/bulk/replace-check). 원장 같은 줄 확정(choice·suggested, 다른 곳 = 오답 신호).
  컬렉션 화면 [지식 등록 →] = ?into=(넣을 곳 고정, AI는 검사만). 미리 고르는 곳: 본문에 AI가 고른 업무 이름이 나오면 AI, 아니면 이웃 4/5.
  제안은 자동 용어 추출 안 함(승인 전 내용이 담당 용어집에 들어가지 않게) · 제안 묶음 200건 상한 · 올린 본인은 자기 작업 중지 가능 ·
  제안 묶음도 진행 창 → 실제 결과(승인 대기 n·실패) 안내 · ?into=는 한 번만(등록·컬렉션 변경 시 지움) · 미리보기 초안은 조회 전용만 막음.
  시나리오 registration_location 21/21 · E2E knowledge-location. 마이그레이션 없음.
- v2.164 (2026-10-23, dev_0): **메뉴 노출 관리(영역별) + 조직 컬렉션 카드로 보는 컬렉션 고르기.** 사용자 10/23 "필요 없는 메뉴는 미노출 — 나중엔 권한별도".
  두 겹: ① 담을 수 있는 것 = 코드(`adminMenu.ts` 항목 `kinds` — 정책서·시스템 정보·VOC는 업무만, 서버도 조직엔 400) ② 보이게 할 것 = 설정(시스템·권한 › 설정 › 메뉴 노출,
  관리자, `ops_system_config.menu_visibility` = {"hidden": {"kind:<종류>": [메뉴 id]}} — 나중 권한별은 `role:<역할>` 같은 모양). 왼쪽 메뉴·본문이 `useAdminMenu` 하나로,
  숨긴 메뉴 주소로 오면 기존 바로잡기가 지식 문서로(옛 "조직 컬렉션엔 없음" 흐린 안내 삭제). API `GET/PUT /api/admin/menu-visibility`(읽기 = 로그인 전부, 쓰기 = 관리자).
  조직 컬렉션 카드 본문을 누르면 보는 컬렉션이 바뀐다(업무 컬렉션 표의 줄과 같게, "· 지금" 표시). 마이그레이션 없음.
- v2.163 (2026-10-23, dev_0): **E1 — AI 판단 원장 + 등록 위치 판정(백엔드만, 등록 동작은 그대로).** 설계 `docs/tech/collection-design.md` §16·§17.
  원장 `ops_ai_decision`(#85): 판단 1건 = 1줄(AI 값·확신·근거 신호·모델/프롬프트 버전), 사람이 확정·변경하면 같은 줄에 최종 값·"AI가 틀렸나요?" 답. 원문 안 남김.
  계산은 `trustkit.decision.ledger`(확신 = AI 값 = 이웃 5중 4 · 종류별 집계), DB는 `service/decision/ledger.py`(판단하는 쪽은 이 함수만).
  위치 판정 `ingestion/locator.py` + `POST /api/knowledge/locate`: LLM(§4 기준 프롬프트 `knowledge_locate_system`, 후보 = 업무 전부 + **넣는 사람 팀의** 조직,
  컬렉션마다 대표 용어 10개를 데이터에서) × 가장 가까운 사용 중 지식 5건의 컬렉션 다수결. 게이트웨이 보안 필터 거부·답 모양 오류 = "AI 판단 실패"(본문 변환 안 함).
  채팅 되묻기도 원장에(물음 1줄 → 다음 질문이 고른 범위/직접 입력으로 닫음, 고른 글자는 선택지일 때만 · 답에는 영향 없음). 측정 `scripts/eval_locator.py`(문서 단위, 숫자만).
  판정 LLM = 설정 `LOCATOR_LLM`(기본 local = `OPENAI_COMPAT_BASE_URL`, 없으면 시스템 LLM — 사용자 10/23 "일단 로컬, 팀 논의"). 이웃은 업무 + 넣는 사람 팀 조직에서만,
  벡터 색인 반복 탐색(`hnsw.iterative_scan`)으로 5건 채움. 미리 고르는 곳 = 이웃 4/5 이상이면 이웃, 아니면 AI.
  채팅: 게이트웨이의 다른 거부 문구("민감 정보가 포함된 요청은 처리되지 않")도 장애로(`is_gateway_refusal`, 질의 기록 system_error·캐시 제외 — 예전엔 옛 문구만).
  시나리오 `scripts/scenarios/ai_decision_ledger.py`(남의 줄·두 번 확정 안 됨, 되묻기 글자 주입 안 남음, 옛 열린 줄 닫힘, 조회 전용 403).
- v2.162 (2026-10-23, dev_0): **컬렉션 현황 화면 = 보기 + 넘기기, 설정은 시스템·권한으로(사용자 10/23).** 업무·조직 컬렉션 줄/카드마다 [지식 등록 →](쓸 수 있을 때) · [보기 →](항상) — 그 컬렉션을 고른 채 지식 문서 등록 탭 / 목록(`CollectionLinks`). 업무 컬렉션 수정 창 = 이름만,
  담당 파트 지정은 시스템·권한 › 조직·권한 › 담당 업무 한 곳(프론트 `setNamespaceOwners` 삭제, 백엔드 `PATCH /namespaces/{name}/settings`는 API·시나리오용으로 유지).
  운영 반입 1회성 `scripts/move_team_common_to_org.py`(업무 "팀 공통" → 조직 팀 공통, 미리보기 기본 · `--apply` · `--rollback`, 정책·VOC·참조·정책 정정·하위·진행 중 적재 있으면 멈춤,
  지식 updated_at 보존). 마이그레이션 없음.
- v2.161 (2026-10-23, dev_0): **업무구분 칸·표 삭제(C6, 마이그레이션 #84).** 설계 `docs/tech/category-removal-design.md`. `ops_prompt(category_suggest)` 줄 삭제 · `DROP TABLE rag_knowledge_category` · `ALTER TABLE rag_knowledge DROP COLUMN category`(모두 IF EXISTS — 새 설치는 init이 만들고 #84가 지움).
  매 기동 도는 앞 마이그레이션도 맞춤: #60 스냅샷에서 category 뺌 · 영역 id 이관 표 목록에서 `rag_knowledge_category` 뺌 · #72는 칸이 없으면 건너뜀. 백업: 표 `*_backup_20261023` 3개 + 전체 덤프(10/23).
  검증: 복사본 DB 두 번 기동 통과 · 검색 게이트 질문별 동일(106·441) · 시나리오 4종 · E2E.
- v2.136 ~ v2.160(10/13 ~ 10/22) 이력은 `docs/archive/architecture-history-v2.136-v2.160.md`, 그 이전은 `docs/archive/`의 다른 이력 파일.

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
│   │   ├── pattern_detection.py #   반복 VOC 클러스터링(centroid 기반, issue_signature 임베딩 비교, LLM 호출 없음) + 커버리지 LLM 검증 게이트(DB/공통코드 카테고리 제외, v2.49) (v2.47 신규)
│   │   ├── pipeline.py        #   수집→관련지식필터→분석→반복패턴탐지→중복제거→알림 오케스트레이션, 이력 조회
│   │   ├── routing_service.py #   파트별 메일함 라우팅 CRUD, 폴링 설정(관련지식 임계치 포함), Graph 자격증명(Fernet 암호화) CRUD
│   │   ├── delegated_auth.py  #   Delegated Permission 로그인 상태 관리 (Authorization Code Flow/PKCE, v2.41 신규·v2.42 인증방식 교체)
│   │   ├── teams_notify.py    #   Teams Workflows 웹훅 발송
│   │   ├── scheduler.py       #   백그라운드 폴링 루프 (asyncio.create_task, lifespan 등록)
│   │   ├── retention.py       #   30일 고정 보관정책 자동 정리
│   │   ├── schemas.py         #   Pydantic 스키마
│   │   └── router.py          #   /api/email-voc/* 엔드포인트
│   └── policy/           #   정책서(v0 원문 통째, docs/tech/policy-v0-design.md) — 옛 엑셀 분해 파이프라인은 v2.150에서 삭제(docs/archive/policy-doc-pipeline-plan.md)
│       ├── v0_convert.py      #   팀원 구조화 데이터 → 저장 단위 변환(LLM 없음·결정적, 적재는 scripts/load_policy_v0.py)
│       ├── service.py         #   서술 조각 머리 붙인 임베딩(chunk_embed_text) + 기동 때 재임베딩
│       ├── visibility.py      #   검색 대상 조건(searchable_sql) + 정책을 겨눈 대기 중 정정 조회(open_correction_sql) — 잎 모듈
│       ├── catalog.py         #   정책서 화면 조회(목록·상세·버전·참고표·개요, 읽기 전용 — 내용 변경은 정정 원장 제안으로만)
│       ├── caches.py          #   정책이 바뀐 뒤 비울 캐시 한 곳(답변 캐시 전 영역 + 키워드·분류 색인)
│       ├── search.py          #   파라미터(RDB tsquery)+서술(벡터·BM25 RRF) 검색 — 채팅(agent.py)과 /api/policy/search가 같이 사용
│       ├── keyword_index.py · category_list.py · scope.py  #   키워드 색인(BM25) · 탐색형 분류 목록 · 채팅의 정책 범위(영역 넘어 찾기)
│       ├── query_type.py · korean_text.py  #   navigation형 질문 판별 · 한국어 조사/어미 제거
│       ├── track2.py · answer_eval.py · pipeline_stats.py  #   검색 비교 실행 · 답변 정확도 이력 · 축적 현황 집계
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
ops_voc_cluster       -- 반복 VOC 클러스터 (representative_embedding=centroid, member_count, coverage_knowledge_id/coverage_verified — 해결방안 LLM 검증 캐시) (v2.47 신규)

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
