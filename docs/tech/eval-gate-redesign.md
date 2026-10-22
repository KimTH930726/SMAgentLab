# 평가 게이트 화면 다시 설계 (설계안 — 2026-10-22 검수 완료)

> **10/22 검수 결정(사용자, §7 질문 전부 추천안)**: ① 측정은 스크립트, 화면은 판정 기록 읽기만 ② 나빠진 문항은 qid·라벨만 DB에(원문 저장 안 함)
> ③ 옛 이력 표 2개(policy_track2_run 8·eval_answer_run 5) JSON 백업 후 삭제 ④ 채점기 확인 탭은 이번엔 안 함 ⑤ 기준선 = 통과한 최근 판정 자동 + 관리자 변경 가능.
> 순서: ④ 업무구분 폐지 → 조직 공간 → **이 화면** → 정확도 95%(참고표 연결 등). 화면 목업은 2단계 착수 때 다시 검수.

> 2026-10-22. 상태: **설계만**(코드·DB 변경 없음). 관리자 "품질 › 평가 게이트"(`EvaluationGate.tsx`)는 v1 엑셀 정책 시절(9/15~10/8)에
> 만든 화면이다. 정책서 v0 재출발(10/19~21) 뒤 실제 평가는 전부 스크립트(`eval_golden_v0.py` · `eval_answers.py` · `eval_gate.py`)로
> 돌고 있어 화면과 끊겼다. 이 문서는 그 단절을 코드·DB로 확인한 사실과, 우리 규모에 맞춘 갱신안이다.
> 골든셋 원문(질문·답)은 이 문서에 넣지 않는다 — 숫자·구조만. 관련: `golden-set-lifecycle-plan.md`, `trustkit-design.md` §5,
> `policy-v0-design.md` §8-1, `admin-ia-design.md`, `post-demo-backlog.md` §2-2.

## 1. 지금 화면 — 무엇을 보여 주고 무엇을 읽나 (코드·dev DB 확인, 10/22)

| 탭 | 프론트 → API | 백엔드 | 읽는/쓰는 데이터 | 실행 |
|---|---|---|---|---|
| 즉석 질의(단건) `UnifiedAdhocSearch.tsx` | `debugSearch` → `POST /api/chat/debug` | `service/chat/router.py:291` `_run_pipeline` | `rag_knowledge`(지식 검색 + 용어 매핑) | 즉시 |
| 〃 | `searchPolicy` → `GET /api/policy/search` | `service/policy/router.py:153` → `search_policy(namespace)` | `policy_item`·`policy_chunk`·`policy_param` — **고른 영역 하나만** | 즉시 |
| 〃 | `searchRefdata` → `GET /api/refdata/search` | `service/refdata/router.py` | `ref_common_code`·`ref_db_column` | 즉시 |
| 〃 "이상해요" | `flagKnowledgeForReview` → `POST /api/knowledge/{id}/flag-for-review` | `improvement.record_search_noise` | `ops_improvement_item`(kind=search_noise, source=eval_gate) 쓰기 | 즉시 |
| 〃 수정 | `updateKnowledge` | knowledge router | `rag_knowledge` 쓰기(재임베딩) | 즉시 |
| 골든셋 실행(집계) `PolicyLab.tsx` + `useTrack2Store.ts` | `GET /policy/track2/axes` · `POST /policy/track2/run` · `GET /policy/track2/history` | `service/policy/track2.py` | 골든셋 `tests/fixtures/golden_set/online_delivus_v1.jsonl`(89문항), 정답 = `policy_item.source_file/source_sheet/source_row`, A그룹은 정책 전체를 `rag_knowledge` 임시 영역에 넣고 임베딩 후 삭제. 결과 `policy_track2_run` 쓰기 | 화면에서 동기 실행(수 분) |
| 답변 정확도(최종 답) `AnswerEvalHistory.tsx` | `GET /policy/answer-eval/history` | `service/policy/answer_eval.py` | `eval_answer_run`(집계만: 라벨 개수·유형별·검색은 맞고 답 틀림) | 화면 실행 없음 — `eval_answers.py report --save`가 쓴다 |

### 1-1. v0에서 깨졌거나 낡은 곳

| 심각도 | 어디 | 사실(확인 방법) | 결과 |
|---|---|---|---|
| **깨짐** | 골든셋 실행 | 정답을 시트·행으로 찾는다(`track2._resolve_item_by_source`). dev DB: 현행(active) 정책 981건 중 `source_sheet` 있는 것 0 · `policy_param` 0건 · v1 목록형 문항의 분류 24개 중 현행 분류와 일치 1개 | 89문항 중 채점 대상이 거의 0(실행은 안 해 봄 — 실행하면 DB 쓰기). 경고는 서버 로그에만, 화면엔 "n문항"만 바뀐 결과가 **이력에 저장**돼 추이를 오염 |
| **깨짐** | 골든셋 실행 비용 | 실행마다 현행 정책 981건을 `rag_knowledge` 임시 영역에 넣고 하나씩 임베딩(`_setup_track_a`) | v0 본문이 길어(중앙 272자) v1 때(80~130초)보다 길어질 것 — 미측정. A/B 저장 전략 비교 질문 자체가 9/4에 결론 난 것 |
| **낡음** | 골든셋 실행 이력 | `policy_track2_run` 8건, 9/15~10/6, 전부 v1 89문항(B hit 87.6%) | 화면이 "최근 결과"로 v1 숫자를 보여 줌 |
| **낡음** | 답변 정확도 | `eval_answer_run` 5건, 전부 10/7 · v1 88문항 · 채점기 Qwen3-Coder-30B. 최신 표시 = "v2.129 적재 결함 수정 후" | v0 첫 기준선(10/21, 441문항×2회, 채점기 Qwen3.8-27B — `eval_out/golden_v0/judged_*_1021.jsonl` 4개)은 `--save`를 안 해서 **화면에 없음** |
| **잘못된 비교** | 답변 정확도 "직전 대비 ±%p"·추이 선 | 서로 다른 세트(88 vs 441)·다른 채점기·다른 날 채점을 그냥 이어 그림. 저장 칸에 골든셋 버전·채점일·채점 기준(rubric)·답 생성 모델이 없다 | 우리 규칙("같은 날 채점끼리만 비교", `eval_gate.py`)을 화면이 어김. 오차 구간도 없음 |
| 낡음 | 답변 정확도 유형 이름 | `TYPES` = param/narrative/condition_filter/navigation(v1). v0 층은 fact·condition·human·cross·list·abstain | v0 결과를 올려도 유형 대부분이 영문 키 그대로 |
| 없음 | 게이트 판정 | `trustkit/eval/gate.py`(짝 비교·군집 95% 구간·McNemar) + `scripts/eval_gate.py`는 터미널 출력만, 저장 안 함 | 이름이 "게이트"인데 판정 결과를 화면에서 못 봄. 무엇을 바꿔 통과/실패했는지 기록이 커밋 메시지·대화에만 |
| 어긋남 | 즉석 질의 정책 범위 | 채팅은 `build_chat_context` → `policy_scope.policy_namespace_ids`(정책 있는 영역 전체·같은 업무 연결)로 찾는데, 즉석 질의는 `/policy/search`를 **고른 영역 하나**로 부름 | "실제 채팅과 동일" 안내와 다름 — 다른 영역 정책이 답의 근거인 질문(v0 골든셋 cross 28문항 유형)은 화면에서 안 보임 |
| 어긋남 | 즉석 질의 표시 | 근거 없음 판정(`should_abstain`), 정책 문맥 상위 수(`POLICY_CONTEXT_TOP_K=10`, 화면 top_k와 별개), 참고표·개요(채팅 미연결 상태 포함)가 안 보임. 정책 카드 "RDB 파라미터" 배지는 v0 파라미터 0건이라 항상 빈 축 | 담당자가 "채팅이 왜 이렇게 답했나"를 이 화면으로 재현 못 함 |
| 미사용 | "이상해요" | `ops_improvement_item`에 source=eval_gate **0건**(전체 이력). 정책 카드엔 버튼 없음(지식 카드만) | 신호 출처로 등록돼 있지만 실제로 쓰인 적 없음 |
| 낡은 문구 | `EvaluationGate.tsx` 안내·docstring, PolicyLab "저장소 전략 실험실·A안/B안·그래프" | v1 실험 설명 | — |

### 1-2. 지금 실제 평가 경로(스크립트) — 화면과의 연결

```
골든셋 v0 485문항 (eval_out/golden_v0/reviewed/*.jsonl, git 제외, dev 호스트에만)
  층: fact 243 · abstain 60 · condition 52 · human 40 · cross 28 · list 18 · ref 15 · ref_clarify 15 · overview 14 (전부 approved)
  정답 = (종류, source_key) → 현행 logical_id. 참고표·개요 44문항은 채팅 미연결이라 분모 제외(개수 따로 출력)
     │
     ├─ eval_golden_v0.py  (LLM 없음, 실제 채팅 경로 build_chat_context)
     │     → 터미널 집계 + --details retr_*.jsonl (qid·hit·top1·type)        ── 화면 연결 없음
     │       10/21 기준선: 정답 있는 381문항 Recall@10 375(98.4%) · Top-1 312(81.9%)
     ├─ eval_answers.py collect → judge(로컬 Qwen, 정답/부분/오답/거절) → report [--save]
     │     → answers_*.jsonl / judged_*.jsonl (질문·답·정책 원문 포함)        ── --save 하면 eval_answer_run에 집계만
     │       10/21 기준선: 441문항×2회, 1회차 정답 397(90.0%) · 오답 37 · 부분 5 · 거절 2  (DB엔 안 올라감)
     │       review-html --blind / review-apply → 채점기-사람 일치(카파)         ── 화면 연결 없음
     └─ eval_gate.py --answers-before/after [--retrieval-before/after]
           → trustkit.eval.gate: 검색 = 나빠진 문항 0이어야 통과(McNemar p) /
             답 = 유형별 짝 차이 95% 구간(같은 원문 묶음 군집) 상한 < 0이면 실패, 채점일·기준 다르면 경고
           → 터미널 출력, 종료 코드 0/1                                          ── 화면 연결 없음, 저장 없음
```
- 즉, **지금 화면에서 v0 숫자를 볼 수 있는 곳은 없다.** 세 스크립트 중 DB에 쓰는 건 `eval_answers.py report --save` 하나(v1 형식 집계).
- `eval_answers.py collect --golden` 기본값이 아직 `v1`(옛 track2 로더). v0은 `--golden v0`를 붙여야 한다.
- 골든셋 원장 `eval_item`(lifecycle 계획 §4-1)은 아직 없음(dev DB `\dt *eval*` = `eval_answer_run`뿐).

## 2. 업계 기준 (조사 10/22, 출처 §8)

회귀 게이트 화면이 공통으로 보여 주는 것:

| 업계 방식 | 대표 | 우리에게 있나 |
|---|---|---|
| **기준선 지정 + 실험 짝 비교 + 문항별 좋아짐/나빠짐 표시** | LangSmith 비교 화면(기준선 대비 나빠진 실행 빨강·좋아진 것 초록, 열마다 개수, 나빠진 것만 거르기), Langfuse(검토된 릴리스 실행을 기준선으로, 평균이 가리는 중요한 문항의 나빠짐을 점수 필터로 찾아 나란히 보기) | 로직은 있음(`gate.py`의 up/down), 화면 없음 |
| **오차 구간·짝 비교·군집** | Anthropic "A statistical approach to model evaluations"(Miller, arXiv 2411.00640): 같은 문항끼리 차이, 같은 지문 묶음은 군집 표준오차 | 있음(`trustkit/eval/stats.py`) — 화면은 구간 없이 % 하나 |
| **세트 버전 고정** | Arize Phoenix: 넣기·고치기·지우기마다 새 버전, 실험은 특정 버전에 묶임 / LangSmith 태그 | 없음 — 파일 이름·날짜뿐 |
| **CI 게이트(실패 시 막음)** | promptfoo(단정 실패 → 0 아닌 종료 코드, PR에서 전/후 비교), DeepEval(`deepeval test run`, 기준 미달 실패) — 현장 글들: LLM 채점 단정은 흔들려서 막는 경로에서 빼거나 통과율 기준으로, 기준은 바뀐 것 없는 상태에서 흔들림을 먼저 재고 정함 | 스크립트 종료 코드는 있음. 같은 날 재채점·2회 반복으로 흔들림 대응 |
| **채점기 보정·실패 사례 읽기** | Anthropic "Demystifying evals for AI agents": 채점이 맞는지는 기록·채점을 많이 읽어야 앎, 채점기는 전문가와 보정, 결정적 채점 우선, 회귀 세트는 거의 100% 통과가 정상·0%는 문항 고장 신호. OpenAI 평가 가이드: 목표→데이터→지표→실행·비교→지속 평가 | 블라인드 표본(`review-html --blind`)·카파 있음, 화면 없음 |
| **실사용에서 문항 승격** | Phoenix(운영 기록 → 데이터셋), Langfuse(골든셋은 실제 실패에서 자란다) | 정정 원장·질의 기록 있음, 승격 경로 없음(lifecycle 계획에서 "수동 1회 후" 보류) |
| RAG 전용 지표 | RAGAS(faithfulness·context recall), TruLens(RAG 삼각: 문맥 관련성·근거성·답 관련성, 버전별 리더보드 표) | 우리는 Recall@K·Top-1 + 원문 대조 4라벨 채점으로 대신 — 지표 추가 필요 근거 없음 |

**우리에게 맞춘 요약(5줄)**
1. 게이트 화면의 핵심은 "기준선 vs 후보, 같은 문항끼리, 나빠진 문항 목록" — 평균 하나와 추이 선이 아니다(LangSmith·Langfuse).
2. 숫자엔 구간을 붙이고 짝으로 비교한다 — 이미 `trustkit`에 있으니 **보여 주기만** 하면 된다(Anthropic/Miller).
3. 비교는 같은 세트 버전·같은 채점 조건끼리만 — 버전 표시가 없으면 화면이 잘못된 비교를 만든다(Phoenix; 우리 "같은 날 채점" 규칙).
4. LLM 채점은 흔들리므로 막는 기준은 결정적 검색 지표 + 구간 기반 답 판정, 채점기는 사람 표본으로 보정(promptfoo 현장 글·Anthropic).
5. 실행·버전 관리·문항 승격·주석 큐는 수백~수천 문항·여러 팀을 전제한 기능 — 우리(485문항, 소수, 로컬 Qwen)는 스크립트 실행 + 판정 기록 + 읽기 화면이면 된다.

## 3. 지금 실제로 막힌 일

1. **v0 결과를 화면에서 못 본다** — 화면 두 탭이 v1 숫자(87.6%·73%)를 "최신"으로 보여 준다. 팀·work-os에 v0 기준선(검색 98.4% / 답 90.0%)을
   설명하려면 터미널 출력을 옮겨 적어야 한다.
2. **게이트 판정이 남지 않는다** — 동의어·근거 없음 재보정·목록 줄 등 10/21~ 전후 측정이 이어지는데, 무엇을 바꿔 어느 유형이 몇 문항
   나빠졌는지가 저장되지 않는다. 다음 작업 때 "지금 기준선이 뭐였지"를 파일 이름으로 찾는다.
3. **화면이 잘못된 비교를 유도한다** — 세트·채점기·채점일이 다른 측정을 한 선으로 잇고 ±%p를 표시(위험: 개선/악화 오판).
4. **누르면 해로운 버튼이 있다** — "골든셋 실행"은 v0에서 채점 대상 ≈0인데 수 분간 임베딩·임시 쓰기를 하고 그 결과를 이력에 저장한다.
5. **즉석 질의가 채팅과 다르다** — 정책을 고른 영역 하나에서만 찾고 근거 없음 판정을 안 보여 줘, 오답 재현·원인 확인 도구로 못 믿는다.

## 4. 하는 것 (MVP)

원칙: **실행은 스크립트(지금 그대로), 화면은 판정·기준선을 읽는다.** 답 측정은 문항당 로컬 LLM 호출×2회라 화면 실행 버튼을 두지 않는다
(기존 결정 유지). 새 저장은 "판정 기록" 하나 — 측정 결과 파일(질문·답 원문 포함)은 지금처럼 `eval_out/`에만.

### 4-1. 화면 (목업 — 검수 전)

탭 3개: **[판정] [즉석 질의] [채점기 확인]** (옛 "골든셋 실행"·"답변 정확도" 탭 대체)

```
품질 › 평가 게이트                                                       [판정]  즉석 질의  채점기 확인
┌ 현재 기준선 ────────────────────────────────────────────────────────────────────────────┐
│ 골든셋 v0 · 485문항(측정 441 · 참고표·개요 44 제외ⓘ) · 세트 a3f9…ⓘ                         │
│ 검색  Recall@10 98.4%  Top-1 81.9%   │  답  정답 90.0% [xx–xx]ⓘ  부분 1% 오답 8% 거절 0%     │
│ 10/21 "v0 첫 기준선" · 채점 Qwen3.8-27B · 2회 반복                                         │
└────────────────────────────────────────────────────────────────────────────────────────┘
 층별(기준선)       문항   Recall   Top-1   정답률 [95% 구간]
 사실(fact)          243   …        …       …
 조건(condition)      52   …
 사람 문항(human)     40   …
 영역 넘김(cross)     28   …
 목록(list)           18   포함률 …          …
 답 없음(abstain)     60   —        —       거절 맞힘 …
                                                      (숫자는 실제 기록에서 채움 — 여기선 자리만)
 판정 이력   (아래 두 줄은 모양 예시 — 값은 가상)
 날짜    무엇을 바꿨나              결과    검색(좋아짐/나빠짐)   답 차이 [구간]          커밋
 MM/DD  변경 A                      통과    0 / 0                +0.x%p [-a, +b]        abc1234
 MM/DD  변경 B                      실패    1 / 3  ▸              -x.x%p [-a, -b] ▸      (반영 안 함)
 ...
 ▸ 누르면 상세: 유형별 전/후·구간 표, 나빠진 문항 qid 목록(유형·전/후 라벨), 채점일·채점 기준 일치 여부
 ⚠ 표시: 채점일 다름 / 세트 버전 다름 / 묶음 1개(독립 가정 구간) — 툴팁으로 이유
```
- 숫자 뒤 ⓘ·⚠ 설명은 `title` 툴팁(관리자 문구 규칙). 색: 통과 emerald, 실패 rose(둘 다 `dark:` 쌍 명시), 나머지 slate.
- 추이 그래프는 넣지 않는다 — 세트 버전이 바뀌면 이을 수 없고, 판정 이력 표가 같은 정보를 정직하게 준다(§5).

```
즉석 질의 (지금 화면 유지, 3가지만 바꿈)
 [업무 영역 ▼] [질문................................] [찾기]
 채팅 판단: 근거 있음 → 답함   (또는 "근거 없음 → '관련 지식을 찾지 못했습니다'"ⓘ)
 정책 범위: 온라인스토어 외 13개 영역ⓘ        용어 매핑: ○○
 ── 결과 카드(지금과 같음, 정책은 채팅과 같은 범위·상위 10) ──
 (정책 파라미터 0건이면 "RDB 파라미터" 배지 숨김)
```

```
채점기 확인 (읽기만)
 최근 사람 표본: 10/2x · 40문항 블라인드 · 정답 여부 일치 9x% · 카파 0.xx(운영 가능)ⓘ · 다름 n건(qid)
 "표본이 없거나 30일 지남" 이면 실행 명령 한 줄 안내
```
※ 채점기 확인 탭은 `review-apply` 결과를 판정 기록과 같은 방식으로 저장할 때만 의미가 있다 → 검수 질문 Q4.

### 4-2. 저장 — 새 테이블 1개

`eval_gate_run` (판정 기록. 질문·답 원문 없음 — qid·라벨·숫자만)

| 칸 | 내용 |
|---|---|
| id, run_at, created_by | |
| label | 무엇을 바꿨나(사람이 한 줄) |
| kind | `gate`(전/후 판정) / `baseline`(기준선 단독 측정) / `judge_check`(채점기 사람 표본, Q4) |
| golden_version | 측정에 쓴 확정 문항 qid 목록의 해시 + 문항 수 — 세트가 바뀌면 다른 값 |
| verdict | pass / fail / null(baseline) |
| retrieval | jsonb — `retrieval_gate` 출력 그대로(전/후 수·up/down qid·p·유형별) |
| answers | jsonb — `answer_gate` 출력 그대로(유형별 전/후·차이·구간·up/down 수·failed) + 나빠진 qid 목록 |
| summary | jsonb — 후보(after) 쪽 헤드라인: Recall·Top-1·정답률·구간·라벨 분포·층별·검색 맞고 답 틀림 |
| judge_meta | jsonb — 채점 모델·채점일·채점 기준(rubric)·반복 수·답 생성 모델(지금 결과 파일에 없음 → collect에 기록 추가) |
| is_baseline | 현재 기준선 표시(한 행만) |
| git_commit, notes | |

- 쓰기: `eval_gate.py --save "라벨"`(판정과 동시에), `eval_gate.py --baseline --save`(단독 측정 → 기준선). 계산은 지금 `trustkit.eval.gate` 그대로 —
  화면·API는 저장된 결과를 읽기만(통계 재계산 없음, 같은 날 채점 규칙은 스크립트가 이미 경고).
- 기준선: 기본 = "통과한 판정 중 최신 after", 화면에서 관리자가 다른 판정을 기준선으로 바꿀 수 있게(버튼 하나) → 검수 질문 Q5.
- 문항별 행 테이블(`eval_run_item`)은 만들지 않는다 — 나빠진/좋아진 qid는 판정 jsonb에 들어가고, 임의 두 측정의 자유 비교는 채점 흔들림
  때문에 어차피 같은 날 재채점이 필요하다(§5).

### 4-3. API (읽기 2 + 관리 1)
- `GET /api/eval/gate-runs?limit=` — 판정 이력(관리자·품질 권한).
- `GET /api/eval/gate-runs/{id}` — 상세(유형별·나빠진 qid).
- `POST /api/eval/gate-runs/{id}/baseline` — 기준선 지정(관리자, `useMutation` + `onError` → `showError`).
- 즉석 질의: `/api/chat/debug` 응답에 `build_chat_context`의 정책 결과·근거 없음 판정·정책 범위를 붙이거나 `/policy/search`에 채팅 범위 옵션 —
  구현 때 둘 중 작은 쪽(앞쪽이 "채팅과 같은 경로"를 보장하므로 우선).

### 4-4. 스크립트 쪽 손볼 것
- `eval_gate.py`: `--save`, `--baseline`, `--commit`(기본 `git rev-parse`) 추가. 골든셋 버전 해시는 두 입력의 qid 집합으로 계산, 전/후가 다르면 경고 + 기록.
- `eval_answers.py collect`: 행에 답 생성 모델(`model_id` — 지금 해시에만 들어감) 기록, `--golden` 기본값 `v0`로.
- `eval_answers.py report --save`(옛 `eval_answer_run` 쓰기)는 `eval_gate.py --baseline --save`로 대체.
- 10/21 기존 결과 파일 4쌍(baseline·list2·syn·p13)으로 `eval_gate.py --save`를 다시 돌려 첫 기록을 채운다 — LLM 호출 0(파일 재사용).

## 5. 안 하는 것 + 다시 볼 조건

| 안 하는 것 | 이유 | 다시 볼 조건 |
|---|---|---|
| 화면에서 측정 실행(검색·답) | 골든셋이 git 제외 파일(dev 호스트에만), 답 측정은 로컬 LLM 수백 회 호출. 스크립트로 충분 | 골든셋이 DB 원장(`eval_item`)에 들어가고, 개발자 아닌 담당자가 측정을 돌려야 할 때 — 검색 측정(LLM 없음)부터 |
| 문항별 원문 드릴다운(질문·답·근거를 화면에) | 원문 파일은 `eval_out/`에만 두는 기존 결정(`answer_eval.py` docstring). qid로 파일에서 찾기 가능 | Q2 답이 "보여 달라"면 — 판정 기록에 나빠진 문항만 질문·답 요약 저장 |
| 측정 간 자유 비교(아무 두 측정 골라 비교) | 답 채점은 날마다 흔들려 같은 날 재채점이 전제 — 저장된 옛 측정끼리 비교는 오판 위험 | 채점 흔들림이 0에 가깝다는 근거(같은 답 다른 날 재채점 일치율 ≥ 99%)가 생기면 |
| 추이 그래프 | 세트 버전·채점기가 바뀌면 이을 수 없음 | 같은 세트 버전 기록이 5개 이상 쌓이면 버전 안에서만 |
| 골든셋 버전 테이블·문항 원장·검토 화면 | lifecycle 계획 단계 1(별도) — 이번은 해시로 "같은 세트인가"만 | lifecycle 계획 착수 때(`eval_item`) |
| CI 자동 실행(커밋마다) | 측정에 로컬 LLM·실 DB 필요, 사람이 한 번에 하나씩 돌리는 규칙(측정 순서) | 검색 측정만이라도 매일 돌릴 이유(데이터 자동 적재)가 생기면 |
| 실사용 실패 → 문항 자동 승격, 주석 큐 | 수동 1회 이후 원칙 | 정정 요청 "답변 틀림"을 손으로 골든셋에 옮긴 사례 2~3건 뒤 |
| 정책 카드 "이상해요" 추가 | 지식 카드 "이상해요"도 0건 사용. 정책은 정책서 화면 "수정 제안"·"원문 확인"이 이미 있음 | "이상해요" 사용이 실제로 생기면, 정책은 원문 확인 흐름으로 연결 |
| 지식(컨플루언스)·공통코드 축 평가 화면(백로그 §2-2) | 지식 원문 재정제 대기, 컨플 문항은 재생성 예정 | 지식 재적재 후 첫 게이트를 스크립트로 돌릴 때 — `golden_version`·`kind`에 축 칸 추가 |
| RAGAS·TruLens식 추가 지표 | 4라벨 원문 대조 채점 + Recall/Top-1로 판단에 필요한 것은 다 나옴 | 답이 근거 밖 내용을 지어내는 오답이 유형으로 늘면(근거성 지표) |

## 6. 지울 것 (제안만 — 삭제는 대상별로 다시 확인받는다)

| 대상 | 종류 | 근거 | 같이 손볼 곳 |
|---|---|---|---|
| `PolicyLab.tsx`, `store/useTrack2Store.ts` | 화면 | v1 A/B 실험 전용 | `EvaluationGate.tsx` 탭 |
| `AnswerEvalHistory.tsx` | 화면 | 판정 탭으로 대체 | |
| `api/policy.ts` — `runTrack2`·`getTrack2History`·`getTrack2Axes`·`getAnswerEvalHistory`·`Track2*`·`AnswerEvalRun` 타입 | 프론트 API | 호출처가 위 화면뿐 | |
| `GET /policy/track2/axes`, `POST /policy/track2/run`, `GET /policy/track2/history`, `GET /policy/answer-eval/history` | 백엔드 API | 〃 | `schemas.py` Track2 응답 모델 |
| `service/policy/track2.py`, `service/policy/answer_eval.py` | 백엔드 | v1 시트·행 로더. **단, `_load_golden_set`을 v1 스크립트가 import**: `eval_answers.py`(`--golden v1`), `eval_chat_retrieval.py`, `gen_holdout_questions.py`, `abstain_pilot.py`, `diagnose_no_knowledge.py`, `eval_prompt_guard.py`(`_namespace_for_file`), `scenarios/chat_multiturn.py` | v1 경로 제거 또는 스크립트 정리 — `eval_chat_retrieval.py`는 지식 측정에도 쓰이는지 먼저 확인(미확인) |
| `tests/test_policy_track2.py`, `tests/fixtures/golden_set/online_delivus_v1.jsonl`·`holdout_v1_*` | 테스트·자산 | v1 골든셋(옛 정책 평가 자산 폐기 결정 10/21) | `trustkit` 게이트 테스트는 유지 |
| 테이블 `policy_track2_run`(8행) | DB | v1 이력. 메모 "정책 테이블·칸 정리"의 옛 전용 목록에 이미 있음 | 삭제 전 JSON 백업, `export_table_spec.py` 항목, `table-definition.md` 이력 |
| 테이블 `eval_answer_run`(5행) | DB | v1 88문항 집계, 새 판정 기록으로 대체 | 〃 |
| `EvaluationGate.tsx` docstring·안내 문구 | 문구 | "저장소 전략 실험실·A안/B안·그래프" 등 v1 설명 | |
| (범위 밖, 확인만) `GET /policy/pipeline-stats` | 백엔드 API | 프론트에 호출처 없음(grep) — 고아 점검 스크립트로 확인 | |

## 7. 단계 · 작업량 · 검수 질문

| 단계 | 범위 | 완료 기준 | 추정 |
|---|---|---|---|
| 1 | `eval_gate_run` 마이그레이션 + `eval_gate.py --save/--baseline` + 읽기 API 2 + 기준선 지정 1 + 테스트 | 10/21 결과 4쌍 재판정 저장 → API 값이 터미널 출력과 문항 단위로 같음(up/down qid 동일), 같은 입력 두 번 저장해도 기준선 1개 | 0.5~1일 |
| 2 | 판정 탭 화면(기준선 카드·층별 표·판정 이력·상세) — **목업 검수 후** | `tsc` 통과, 실패 판정 상세에서 나빠진 qid가 스크립트 출력과 같음, 쓰기(기준선 지정) 실패 시 모달 | 1일 |
| 3 | 즉석 질의를 채팅 경로로(정책 범위·근거 없음 판정·빈 파라미터 배지 숨김) | 골든셋 cross 문항 몇 개를 즉석 질의로 넣어 채팅 문맥(`build_chat_context`)과 정책 목록이 같음 | 0.5~1일 |
| 4 | 정리(§6) — 대상별 승인 후 | 전체 회귀 통과, `check_orphaned_api_functions.py` 깨끗 | 0.5일 |
| (선택) | 채점기 확인 탭 (`review-apply --save`) | Q4 답에 따라 | 0.5일 |

합계 약 2.5~3.5일(선택 제외). 각 단계는 독립 리뷰 → 반영 → 커밋 승인(개발 규율 그대로).

**검수받을 질문 (5개)**
1. **실행은 스크립트, 화면은 판정 기록 읽기**로 충분한가? (화면 실행 버튼 없음 — 대신 "다음에 돌릴 명령" 한 줄 안내)
2. 판정 상세에 **나빠진 문항의 질문·답 원문을 DB에 남겨 화면에 보여 줄까**, 아니면 지금처럼 qid만(원문은 `eval_out/` 파일)?
3. 옛 이력 `policy_track2_run`(8행)·`eval_answer_run`(5행)은 **JSON 백업 후 테이블 삭제**해도 되나? (v1 숫자는 비교 불가 기준선이라 화면엔 안 씀)
4. **채점기 확인 탭**(사람 블라인드 표본·카파)도 이번에 넣을까, 판정 탭만 먼저 할까?
5. 기준선은 **"통과한 최신 판정" 자동** + 관리자가 바꿀 수 있게 — 이 방식이면 되나, 아니면 항상 사람이 지정?

## 8. 참고 (조사 출처, 10/22)
- LangSmith 실험 비교(기준선 대비 나빠짐 빨강·좋아짐 초록, 개수, 필터): https://docs.smith.langchain.com/how_to_guides/evaluation/compare_experiment_results
- LangSmith 짝 평가(evaluate_comparative, 순서 무작위): https://docs.langchain.com/langsmith/evaluate-pairwise
- Langfuse 실험 비교(검토된 릴리스 실행을 기준선, 평균이 가리는 나빠짐, 점수 필터): https://langfuse.com/docs/evaluation/experiments/compare-experiments
- Langfuse 데이터셋 실행 비교 화면: https://langfuse.com/changelog/2024-11-18-dataset-runs-comparison-view
- Arize Phoenix 데이터셋 버전·운영 기록에서 데이터셋: https://arize.com/?p=17953 , https://www.mintlify.com/Arize-ai/phoenix/datasets/versioning (비공식 미러)
- Miller, Adding Error Bars to Evals(군집 표준오차): https://arxiv.org/pdf/2411.00640
- Anthropic, Demystifying evals for AI agents(회귀 세트 ~100% 통과, 기록 읽기, 채점기 보정, 반복 시행): https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- Anthropic, 테스트 케이스 만들기(실제 분포·경계 사례, 자동 채점, 측정 가능한 기준): https://platform.claude.com/docs/en/test-and-evaluate/develop-tests
- OpenAI 평가 모범 사례(목표→데이터→지표→실행·비교→지속 평가): https://developers.openai.com/api/docs/guides/evaluation-best-practices
- promptfoo CI 게이트(현장 글 — 공식 문서 아님): https://medium.com/@alexrodriguesj/testing-llm-prompts-like-code-regression-evals-in-ci-cd-with-promptfoo-5242b4dcb9be ,
  https://dev.to/ethanwritesai/we-gated-ci-on-six-open-source-llm-eval-frameworks-only-two-survived-the-merge-queue-5elf
- DeepEval/Confident AI 테스트 실행·회귀 비교: https://documentation.confident-ai.com/concepts/test-runs
- TruLens 버전별 리더보드·RAG 삼각: https://www.trulens.org/getting_started/quickstarts/batch_evaluation/ ,
  https://www.trulens.org/blog/2024/10/09/whats-new-in-trulens-11-dashboard-comparison-view-multi-app-support-metadata-editing-and-more/
- RAGAS 지표: https://docs.ragas.io/en/v0.1.21/concepts/metrics/
- (앞선 조사) 골든셋 수명주기 출처: `golden-set-lifecycle-plan.md` 참고 절

## 9. 사용자 추가 요구 (2026-10-22 밤, 컬렉션 설계 중)
1. **검색 근거 정확도 / 답 정확도를 나눠 보는 리포트 화면** — 한 화면에 섞지 않고 분기(검색: 정답 근거를 찾았나·순위 / 답: 채점 라벨 정답·부분·오답·거절).
   어느 쪽이 나빠졌는지 바로 보여야 고칠 곳(검색 vs 답 생성)이 갈린다.
2. **상태 → 문제 → 개선점이 보이는 리포트** — "지금 지식이 어떻게 관리되고 있고, 어떻게 관리되면 더 좋을지"까지. 이건 운영 리포트(남은 과업 3, AI 정기 점검 →
   우선순위 제안 → 사람은 승인)와 같은 축이라 그쪽에서 만들고, 평가 게이트는 그 리포트의 "정확도" 칸을 채우는 숫자 공급원으로 둔다.
   들어갈 것(초안): 컬렉션별 건수·빈 컬렉션·위치 어긋남 후보(collection-design §8 관찰 숫자) · 지식 공백·반복 질문 · 정정 요청 쌓임·처리 시간 ·
   검색/답 정확도 추이 · 원문 품질 문제(모순·참고표 오류) → 각 항목에 "제안"(옮기기·합치기·등록 독촉·프롬프트 보정)과 근거 숫자.

### 9-1. 업계 조사 (10/22, 웹 — 미확인은 표시)
**검색 / 답 분리** — Bedrock KB 평가는 작업 자체를 "검색만(context relevance·coverage)"과 "검색+생성(correctness·faithfulness·citation)"으로 나눈다
(docs.aws.amazon.com/bedrock/latest/userguide/knowledge-base-eval-retrieve-generate.html). DeepEval은 지표를 검색 쪽(Contextual Precision/Recall/Relevancy)·생성 쪽
(Faithfulness·Answer Relevancy)으로 묶고 낮은 지표 → 고칠 곳 대응표를 둔다(deepeval.com/guides/guides-rag-triad). TruLens RAG triad(context relevance·groundedness·
answer relevance, trulens.org). Azure Foundry: 검색 설정 조정은 정답 라벨 기반 Document Retrieval(NDCG 등), 끝에서 끝은 Groundedness·Relevance
(learn.microsoft.com/azure/foundry/concepts/evaluation-evaluators/rag-evaluators). LangSmith: 기준 실행 대비 문항별 빨강(나빠짐)·초록, "나빠진 것만" 필터
(docs.smith.langchain.com/evaluation/how_to_guides/compare_experiment_results). Vertex [미확인].
**지식 건강** — Guru: 카드별 검증 담당·만료, 답 못 한 질문 표, 주 1회 검토 권장(getguru.com/features/verification). ServiceNow Demand Insights: 지식 없는 장애를
주제로 묶어 큰 순 막대 + 80% 선 → "공백 작업" 생성(servicenow.com/docs). Moveworks: 월 1회 30일 티켓 분석 → 새 문서(gap)/수정(update) 제안 + 초안.
Confluence: 180일 무활동 "Inactive". Notion: 검증 만료 → 담당 알림. SharePoint SAM: 비활성 사이트 소유자 확인(Copilot이 낡은 근거를 안 쓰게). Document360: 결과 없는 검색어.
**가져올 것(우리 규모)**: ① 검색·답을 따로 재고 따로 보여 주기 + 원인 대응표 ② 문항별 전/후 빨강·초록, 나빠진 것만 보기(문항 id 고정) ③ 지식 건강 = 문제 유형별 목록 +
바로 할 행동(재검증·보관·새로 작성·수정) ④ 공백은 실제 질문 로그를 주제로 묶어 큰 순. **과한 것**: AQI 가중 점검표·조각 단위 Attribution·순위 지표 전부·다중 실행 비교.
**최소 구성(제안)** — (A) 평가 리포트: 위 KPI 두 줄(검색 Hit@k·Recall@k / 답 정답성·근거 충실·거절률 — 정답 있는데 거절과 정답 없어 거절 분리, 각 전/후 차이) ·
문항 표(컬렉션·검색 맞음/틀림·답 라벨·전후 변화, 나빠진 것만) · 진단 칸("검색 실패 n" vs "검색 성공·답 실패 m" + 볼 곳) · 컬렉션별 한 줄.
(B) 지식 건강 리포트(주 1회): 현황(컬렉션별 건수·마지막 갱신) · 문제 5종(오래됨·중복 의심·공백 주제·위치 어긋남·안 쓰임) 각 행동 버튼 · 이번 주 제안 상위 5(AI 제안, 담당 승인).

### 9-2. 평가 게이트와 AI 판단 원장은 같은 엔진 (사용자 10/22 — "원래 하던 품질 검증 게이트의 핵심 기능")
collection-design.md §16의 "제안을 과거 데이터로 다시 돌려 고침 m · 망가짐 k" 는 평가 게이트의 "같은 문항 전/후 비교, 나빠짐 0"과 같은 일이다. 하나로 만든다:
- **입력만 다르다** — 검색·답 게이트는 골든셋 문항(정답 근거·정답), AI 판단 쪽은 원장의 판단(사람이 고친 값 = 정답, 고치지 않고 승인된 값 = 맞음으로 간주).
- **출력은 같다** — 문항(판단)별 전/후 · 고침 m · 망가짐 k · 이유별 묶음 · "망가짐 > 0이면 제안 안 함". 화면도 같은 표(나빠진 것만 보기, 빨강·초록).
- **원인 자체 리포트도 같다** — 틀린 문항(판단)을 신호별로 묶어 가설("업무 이름 없는 짧은 메모 9/12", "탐색형 질문 목록 줄 누락 n")을 내고, 가설마다 바꿀 안을 리플레이로 재서 숫자로 제안.
- 순서: 평가 게이트 다시 만들기(남은 과업 2)에서 이 엔진을 먼저 만들고, AI 판단 원장(E와 함께)이 같은 엔진에 판단을 넣는다.

### 9-3. 위치 (사용자 10/22): 품질 메뉴 안의 기능
품질 › 통계 · 정정 요청 · 평가 게이트 옆에 리포트를 둔다 — ① 검색 / 답 리포트(분리) ② 지식 건강 · 자체 개선 리포트(상태 → 문제 → 개선 제안, 리플레이 숫자).
운영 리포트(남은 과업 3)도 별도 메뉴가 아니라 품질 메뉴의 이 리포트로 만든다.

### 9-4. 기법 대장 (사용자 10/22 — "답 하네스 기준이 소스에만 있어 까먹는다")
되묻기(정책 n개 이상이면 "어느 부분을 볼까요?" + 묶음 몇 개), 근거 없음 거절, 탐색형 목록, 키워드 구제, 부모 섹션 확장 등 답을 깎는 기법의 기준이 코드 상수로만 있다.
- **대장 하나가 기준(SoT)** — `libs/trustkit` 안 목록: 기법 이름 · 하는 일 · 기준값 · 설정 키(바꿀 수 있으면) · 도입 근거 측정(날짜·수치) · 코드 위치.
- **화면** — 품질 메뉴 "답 기법" 목록(읽기 + 설정 가능한 값만 수정, 수정은 게이트 통과 뒤 반영). 리포트가 "이 답에 어떤 기법이 걸렸나"를 대장 이름으로 보여 준다.
- **테스트** — 기법 함수가 대장에 등록돼 있지 않으면 실패(다시 코드에만 숨지 않게).
- 순서: 평가 게이트 다시 만들기와 함께(리포트가 대장을 참조).

### 9-5. 단일 채팅 (사용자 10/22 — "내 담당 업무 + 조직을 한 채팅에서")
합쳐서 검색 = 오늘 실측의 "전체"(지식 있는 업무 2개가 모두 온라인서비스 파트) — 지식 정답 95.3→94.3%, 섞임 0→6.6%, 정책 질문 끼어듦 42→243 → 정확도 원칙상 기본값 불가.
대안: 채팅방 하나 + **질문마다 AI가 내 담당 업무 중 하나를 골라 그 범위로**(+ "딜리버스에서 찾았어요" 표시) — 범위가 질문마다 컬렉션 하나라 섞임 없음. 위험 = 업무 고르기 오답 →
먼저 "업무 고르기 정확도"를 잰다(용어집 매칭·질문 임베딩 vs 컬렉션). D(조직 컬렉션) 뒤 측정·게이트, 그 전엔 컬렉션별 채팅방 유지.
