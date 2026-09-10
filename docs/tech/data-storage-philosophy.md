# 데이터 저장 철학 — 표준화 · 테이블 설계 · 적재 (2026-09-10)

> **목적**: 데이터 축(정책서 → CMDB → Confluence → …)마다 "어떻게 저장하지"를 즉흥으로
> 정하지 않도록, **표준화 · 테이블 설계 · 적재**의 판단 기준을 고정한다. 축이 바뀌어도 이
> 철학은 안 바뀐다.
>
> 이 문서는 새 설계가 아니라, 정책서 파이프라인에서 이미 내린 결정(통합 스키마 /
> `content_hash` 기반 INSERT+deprecated / `unresolved` 캡처 / `rag_glossary` 재사용 /
> PostgreSQL = Source of Truth)을 **일반화**한 것이다.
>
> **`retrieval-routing-pattern.md`와의 관계** — 겹치지 않고 앞뒤다:
> | 문서 | 스코프 |
> |---|---|
> | `data-storage-philosophy.md` (이 문서) | 데이터를 **어떻게 모델링·저장·적재**하는가 (넓음, 앞단) |
> | `retrieval-routing-pattern.md` | 질의 유형 → **검색 전략 라우팅** (좁음, 뒷단) |

---

## 1. 핵심 철학 (4)

1. **판단은 모델, 구조는 결정론.** 구조 파싱·검증·경계는 코드가, 의미 라벨링·문장 합성만
   LLM이 한다. (2026-09-04 정책 본문 분해 버그 = 이 선을 넘겨 LLM에 구조 파싱까지 맡긴 증상)
2. **PostgreSQL = Source of Truth. 벡터 인덱스·파생 컬럼 = 재생성 가능한 파생물.** 비싸고
   비가역인 것(원본 파싱 + LLM 분해 + 사람 검수)만 원천으로 남긴다. 임베딩 모델 교체 →
   재인덱싱은 당연한 일로 본다.
3. **정확·고정은 컬럼, 유동·희소·중첩은 JSONB, 자연어는 벡터.** 한 행이 세 곳에 동시에
   흔적을 남길 수 있다.
4. **재현성**: 같은 원본을 재적재하면 같은 결과가 나와야 한다. LLM 생성물(`embed_text`,
   트리플 정규화 결과 등)은 콘텐츠로 취급해 저장·해시·버전에 포함한다. LLM 호출은
   `temperature=0`.

---

## 2. 저장 구조 3분기 — 무엇을 어디에

| 성격 | 저장 | 쓰임 |
|---|---|---|
| **정확한 고정값** — 미리 알고, 필터·조인·집계·제약을 거는 필드 | **타입 컬럼** (btree/GIN 인덱스) | `WHERE` 정확조회, 스코프·버전 필터 |
| **구조는 있으나 모양이 유동적 / 희소 / 파서 raw 트리 / 유형별 특이 필드** | **JSONB** (`spec`, `attrs`, `raw_structure`) | 답변 payload로 fetch. 필요 시 `@>` / `->>` + GIN. 직접 쿼리는 예외적 |
| **자연어 서술** (원문 또는 구조에서 합성) | **벡터** (`embedding` + 원문 `embed_text`) | 의미 유사도 검색 |

### 결정 규칙
- 쿼리·필터·조인·제약 대상이고 필드를 **미리 안다** → **컬럼**
- 행마다 다르고 / 희소하고 / 중첩이고 / 그냥 실려가는 raw → **JSONB**
- 자연어로 "설명"에 해당 → **벡터** (텍스트와 임베딩을 같은 행에)
- 기본 형태 = **타입 컬럼 몇 개 + JSONB 하나 + 벡터 하나**. 전부 JSONB 한 덩어리로 뭉치면
  플래너·제약을 잃는 안티패턴.
- 자주 쓰는 JSONB 키는 **generated column으로 승격** 가능 → 아는 hot 필드는 처음부터 컬럼으로.

### 고정 depth vs 가변 depth (팀별 정책서처럼)
- **고정 depth까지 = 컬럼** (`category_path text[]`로 깊이 가변 경로를 흡수, GIN 인덱스)
- **그 아래 가변 depth = JSONB** (`spec` / `raw_structure`)
- 파서(1단계)만 팀마다 다르고, 컬럼 매핑·렌더는 공통.

### 키 설계 (불가변 원칙)
- 키를 **팀·조직 같은 가변 단위에 걸지 않는다** → **시스템·소스 같은 불가변 단위** 기준.
- 조직·팀은 조인/룩업으로 유연 조회. "모양이 자꾸 바뀌어서 JSONB"의 진짜 해법이 키 모델링일
  때가 많다.

---

## 3. 표준화 · 적재 파이프라인 (1단계 = 분석 → 파서 → 정제 → LLM 의미만)

축마다 이 순서를 밟는다:

1. **구조 분석** — 샘플 10~20건으로 모양 파악: 평평? 중첩? 계층? 병합셀? 다단 헤더? 셀 안 트리?
2. **포맷 결정** (치트시트)
   | 모양 | 포맷 |
   |---|---|
   | 산문 | Markdown |
   | 평평한 표(2D) | CSV (작으면 MD 테이블) |
   | 중첩·들쭉날쭉 | JSON |
   | 설정 key-value | YAML |
   | 트리·아웃라인 | 들여쓰기 불릿 / 중첩 JSON |
   | 그래프·관계 | 엣지 리스트(`A→B`) / 트리플 |
   | 이벤트·시계열 | JSONL |
   | 코드·로그 | 원본 그대로 |
   | 희소 행렬 | `(행, 열, 값)`만 |
   | LLM 출력 | JSON + 스키마 |
3. **결정론 파서** — 원본 → 정제 구조. 병합셀 forward-fill, 다단 헤더 평탄화, indent 트리
   파싱. **규칙에 안 맞는 건 `unresolved`로 캡처** (LLM에 안 넘긴다).
4. **LLM = 의미만** — 콘텐츠 유형 분류 / 스키마 매핑 / `(action, event, trigger)` 같은
   트리플 정규화 / `embed_text` 합성. **구조 파싱은 절대 시키지 않는다.**
   - `narrative` 유형은 산문 입력이 맞다. 틀린 건 "구조 있는 걸 산문으로 뭉갠 것".
5. 각 행에 **안정적 id** — LLM 출력 역추적 + `unresolved` 연결.

포맷 패밀리 인제스터(표류 / 문서류 / JSON류 / 로그류) — 각각 = 결정론 뼈대 + LLM 안쪽 +
`unresolved` 캡처. 파서만 축별로 갈아 끼운다.

---

## 4. embed_text 생성 규칙

**정의**: "사용자가 이 행을 두고 던질 법한 질문의 언어로 이 행을 서술한 한 문장(또는 짧은 문단)."

- **넣음**: 정책명, `category_path` 끝 1~2단, 트리거·조건·이벤트·대상·값. 약어는 풀거나
  병기(용어집 치환).
- **뺌**: id, hash, version, 내부 플래그, timestamp (질문에 안 나옴).
- **구성**: `[{category_path 조인}] {policy_name} — {content_type}. ` + `render(spec)`
  - `render` = JSONB 트리를 **재귀로 자연어화**하는 공통 함수. depth 3이든 5이든 같은 함수.
    노드 역할(trigger / condition / target / value)에 따라 접속사를 결정하고 리프를 나열.
- **한 청크 = 한 개념** — 최상위 액션이 여럿이면 청크를 분리. 임베딩 모델 토큰 한도 고려
  (현행 `paraphrase-multilingual-mpnet-base-v2`는 128 토큰 — 특히 주의).
- **결정론 vs LLM**:
  - (a) LLM이 역할 라벨만 → 코드 템플릿이 문장을 stitch (뻣뻣하지만 결정론적)
  - (b) LLM이 문장 직접 작성 (자연스러움) + **제약: 출력 토큰이 전부 입력에 존재하는지
    코드로 검증, 없으면 retry** (할루시네이션 차단)
  - `embed_text`는 유사도 매칭에만 쓰이고 "정답"으로 노출되지 않으므로 (b)가 상대적으로
    허용된다. 단 `temperature=0` + `embed_text`를 저장·해시해 재적재 시 재사용.

---

## 5. 검색 시 각 요소의 역할

검색 = SQL 한 방. 컬럼마다 역할이 다르다:

```sql
SELECT id, policy_name, spec, embedding <=> :qvec AS distance
FROM policy
WHERE system = :sys                    -- 타입 컬럼: 스코프 좁히기 (hard filter)
  AND category_path @> :cat             -- 배열 컬럼: 포함 필터
  AND version = :ver                    -- 타입 컬럼: 버전
ORDER BY embedding <=> :qvec            -- 벡터 컬럼: 유사도 순위
LIMIT :k;                               -- top-k (이 K가 hit@K·precision@K의 K)
```

- **타입 컬럼** → `WHERE` (좁히기)
- **벡터 컬럼** → `ORDER BY <=> :qvec` (순위). `:qvec` = 질문을 **같은 임베딩 모델**로 벡터화
- **JSONB** → `SELECT`로 꺼내 답변 렌더링. 검색엔 예외적으로만
- **embed_text** → 근거 문장 표시 / 키워드 검색(`to_tsvector`) 대상

### 구조값이 hard filter냐 soft hint냐로 전략 결정
| 성격 | 방식 |
|---|---|
| **hard filter** (스코프: 이 매장만 / 이 시스템만 / 이 버전만) | `WHERE` 먼저 → 부분집합 안에서 벡터 (pre-filter). pgvector HNSW + 선택적 필터는 recall 저하 모서리 — `iterative_scan` 0.8로 완화 |
| **soft hint** (트리거 키워드·카테고리 = 참고 신호) | 두 채널 병렬 → 머지 (현행 union / RRF) |
| **완전 구조로 답이 됨** | structured-only → 비면 벡터 폴백 (cascade, union보다 저렴) |

현행 = union (정책 질의 대부분이 soft-hint). `condition_filter` 유형 = pre-filter 후보.
cascade = 실험실 L2 테스트 전략 후보.

---

## 6. 이력 · 버전 · 재현성

- **`content_hash`가 바뀌면 UPDATE 금지 → 새 row INSERT + 이전 row `deprecated` 보존**
  (merge가 content를 덮어써 이력이 소실되던 문제 반복 방지 —
  `knowledge-lifecycle-design.md` 우선순위 1위)
- `logical_document_id` / `version` / `supersedes_id` / `embedding_model` / `content_hash`
  = **컬럼** (쿼리·제약 대상)
- LLM 생성물(`embed_text`, 트리플 정규화 결과) = `content_hash` 계산에 포함 → 재적재 재현
- 임베딩 모델 버전을 행에 기록 → 모델 교체 시 어느 행이 재인덱싱 대상인지 추적

---

## 7. 새 데이터 축 착수 체크리스트

- [ ] 샘플 10~20건으로 구조 분석 (평평 / 중첩 / 계층 / 병합 / 트리)
- [ ] 고정 depth 확정 → 컬럼 스키마. 가변부 → JSONB. 키는 불가변 단위
- [ ] 포맷 결정 (§3 치트시트) + 결정론 파서 작성 + `unresolved` 캡처
- [ ] 콘텐츠 유형 분류 규칙 (서술 / 파라미터 / 상태전이 / …)
- [ ] `embed_text` render 규칙 + 청크 분리 임계 + 토큰 한도 확인
- [ ] 검색 전략 (union / pre-filter / cascade) — hard filter vs soft hint 판정
- [ ] 골든셋으로 질의 유형별 정답률 측정 (실험실) → render·임계치·전략 튜닝
- [ ] `retrieval-routing-pattern.md`에 "이 축: 표현계층 뭘 골랐고 왜 + 평가지표 뭘 왜" 한 항목 추가

---

## 8. 현황 대조 — 정책서 파이프라인(`policy_item` / `policy_param` / `policy_chunk`)

2026-09-10 기준 실제 스키마·코드(`backend/service/policy/{excel_parser,decompose,service,search}.py`)를
이 철학과 대조한 결과. 세부 답은 §8-A~D, 데이터 축별 갭은 §8-E.

### 8-A. 3분기 매핑 — 부분 충족, `raw_structure` JSONB 없음

| 3분기 | 현재 | 판정 |
|---|---|---|
| 타입 컬럼 (정확·고정값) | `policy_item`: `namespace_id`, `system_key`, `category_path text[]`, `policy_name`, `source_file/sheet/row`, `content_hash`, `status`, `logical_id`, `version`, `supersedes_id`, `parse_status` | ✅ 대체로 충족. 단 ① `category_path` **GIN 인덱스 없음**(btree만 — `= ANY()` 스캔), ② `embedding_model` **컬럼 없음**(§6 위반, `rag_knowledge`엔 있음) |
| JSONB (유동·희소·중첩) | `policy_item.unresolved_segments jsonb` **하나뿐** — 파서/LLM이 **못 푼** 것 캡처용 | ⚠️ 철학이 말하는 `spec` / `raw_structure`(파서가 만든 **정제 구조 트리**, 답변 payload로 fetch)에 해당하는 컬럼이 **없다**. 파싱 결과를 보존할 곳이 없어 LLM이 매 적재마다 `raw_body` 문자열을 다시 구조 파싱함 → **`raw_structure jsonb` 추가 필요 (YES)** |
| 벡터 (자연어) | `policy_chunk.chunk_text` + `embedding vector(768)` + HNSW 인덱스, `chunk_idx`로 다중 청크 | ✅ 매핑 자체는 충족. 단 `chunk_text` 생성 방식이 §4 규칙 미준수(§8-C) |
| 트리플 (`policy_param`) | `name` / `condition` / `value` / `unit` (+ `external_source`, `approved`) | ⚠️ `(action, event, trigger)` 정규화 전용 아님. 상태전이는 아예 안 들어옴 |

### 8-B. `조건/상세` 3단 트리 → indent 파서 → JSON → 트리플 정규화 — **미구현**

- `excel_parser.py`: 행/열/병합셀 forward-fill·동적 카테고리 깊이까지만 결정론 처리.
  **셀 안(`raw_body`)의 중첩 리스트는 안 건드린다.**
- `decompose.py`: `raw_body` 문자열을 통째로 LLM에 넘겨 **구조 파싱 + 의미 분류를 한 번에**
  시킨다(프롬프트에 "숫자 헤더 줄만 뽑지 마라", "계산식 관계 소실 마라" 같은 사후 방어
  규칙이 4개 붙어 있는데, 이 규칙들이 필요하다는 것 자체가 §1의 선을 넘겼다는 증상).
- 결정론적 indent 트리 파서, JSON 중간 산출물, `(action, event, trigger)` 트리플 정규화
  파이프라인 **전부 없음**. 상태전이는 `unresolved`로 캡처만 됨 —
  실측 58개 `unresolved` 세그먼트 중 **32개(55%)가 "상태 전이" 사유**(30/378 item).

### 8-C. `embed_text` 생성 — (a)도 (b)도 아님, 사실상 "합성 안 함"

- `narrative` 세그먼트: `chunk_text = seg.text` — LLM이 분해한 **원문 조각 그대로**. `render(spec)`
  같은 공통 자연어화 함수 없음.
- 폴백(narrative가 하나도 안 남은 item, 실측 378건 중 136건=36%):
  `chunk_text = f"{policy_name} ({category_path}): {raw_body}"` — 단순 문자열 포맷, `raw_body`
  통째.
- **토큰 검증**(출력 토큰이 입력에 존재하는지): 없음 (LLM이 문장 합성을 안 하니 해당 없음).
  단 mpnet **128 토큰 한도 미고려** — `raw_body`가 길면 임베딩 단계에서 잘림.
- **약어 풀기 / 용어집 치환**, **"한 청크 = 한 개념" 분리**: 미구현.
- `embed_text` 저장: `chunk_text`로 저장은 됨. **해시엔 미포함**(§8-D).

### 8-D. `content_hash`에 LLM 생성물 포함 — **아니오, 재적재 재현 불완전**

- `_content_hash(category_path, policy_name, raw_body, remark)` — **원본 소스 4개 필드만.**
  세그먼트 분류 결과·`chunk_text`·param 추출값 등 LLM 생성물 전부 제외.
- 결과: 같은 xlsx를 재업로드하면 `_check_version`이 `content_hash` 동일로 판단 →
  **스킵**(재분해 자체가 안 일어남). **파서·프롬프트를 개선해도 기존 데이터엔 반영이 안 되고**,
  강제 재처리 경로도 없다.
- `embedding_model` 컬럼이 없어 모델 교체 시 재인덱싱 대상 추적 불가.

### 8-E. 현 데이터 축별 갭 (§8-1·§8-2의 구체 답)

| 축 | 현재 저장 | 갭 | 방향 |
|---|---|---|---|
| **DB 스키마 사전** (`SR 테이블, 컬럼.md`) | `rag_knowledge` 벡터 청크 2531건 — 마크다운 표를 일반 청커로 잘라 헤더 없이 행 중간에서 분리. 2026-09-10 전량 soft-delete | 전용 RDB 테이블 없음 | 결정론 마크다운 표 파서 → `table_name / table_comment / column_name / column_comment / data_type / nullable` (컬럼당 1행). LLM 불필요. 벡터 불필요 |
| **공통코드** (`SR 공통코드.md`) | 동일 — `rag_knowledge` 509건, 2026-09-10 전량 soft-delete | 전용 RDB 테이블 없음 | `group_code / group_code_name / code_id / code_name / mgmt_item_values`. LLM·벡터 불필요 |
| **정책서 상태전이** (APP PUSH류 action→event→trigger) | `decompose.py`가 `unresolved`로 방치 (32/58 세그먼트, 30/378 item) | 결정론 트리 파서 + 전용 구조 없음 | indent/마커 기반 범용 아웃라인 파서 → `raw_structure jsonb` 보존 → LLM은 트리플 정규화만. `policy_param(name=event, condition=trigger, value=action)` 재사용 가능성 검토 |
| **정책서 `raw_body` 중첩구조 파싱** | LLM이 구조 파싱 + 의미 분류 동시 (§8-B) | 결정론 전단계 없음 | 아웃라인 파서를 `decompose.py` **앞단**에 삽입. 중첩 없으면 패스스루(현행 92% 정상 케이스 무영향) |
| **잔여 오염물** | `rag_knowledge` 공통지식에 잔류 — id=17(딜리버스 API 정책서 22KB 표 블롭), id=20(주문상태 공통코드 6KB 산문) | 이관 경로 미정 | id=17 → 정책서 파이프라인 대상. id=20 → 공통코드 테이블 대상. 재구조화 시 함께 처리 |

---

## 9. 갭 이슈 목록 (우선순위)

1. **`content_hash` 재현성** (§8-D) — 파서·프롬프트 개선을 기존 데이터에 반영할 경로가 없음.
   `content_hash`에 LLM 생성물 포함 or `unresolved`/`partial` item만 강제 재처리하는 배치.
   **다른 모든 재구조화 작업의 선행 조건**.
2. **`raw_structure jsonb` 컬럼 추가** (§8-A) — 파서가 만든 정제 구조 트리를 보존할 자리.
   이게 있어야 §8-B의 "결정론 파서 → JSON" 단계가 착지할 곳이 생김.
3. **DB 스키마 사전 / 공통코드 RDB 재적재** (§8-E) — 규모(삭제된 3040건)·심각도 최상.
   마크다운 표 문법이 고정이라 결정론 파서로 거의 100% 커버, LLM 불필요. `retrieval-routing-pattern.md`
   "테이블 분리(사례 2)" 적용.
4. **결정론 아웃라인 파서** (§8-B) — `decompose.py` 앞단. 상태전이(32건) + 향후 축 공용.
   먼저 `unresolved` 32건의 **원본 `raw_body`**를 샘플링해 마커 패턴이 몇 종류로 수렴하는지 확인.
5. **`embed_text` render 함수 + 토큰 한도** (§8-C) — `render(spec)` 공통 자연어화 함수,
   mpnet 128 토큰 청크 분리, 용어집 치환. §2·§4 준수.
6. **`embedding_model` 컬럼 + `category_path` GIN 인덱스** (§8-A) — §6 재인덱싱 추적,
   §5 포함 필터 성능.

착수는 이 문서가 아니라 각 이슈별로. 모두 "규모 있는 신규 기능"이라 `dev_0`에서 설계부터.

---

## 10. 관련 문서

- `docs/tech/retrieval-routing-pattern.md` — 질의 유형 → 검색 전략 라우팅 (이 문서의 뒷단)
- `docs/tech/knowledge-lifecycle-design.md` — `content_hash` INSERT+deprecated, merge 이력 소실 배경
- `docs/policy-doc-pipeline-plan.md` §2 "왜 3층인가" — `policy_item`/`param`/`chunk` 분리 배경
- `backend/service/policy/{excel_parser,decompose,service,search}.py` — 현행 구현
- `backend/service/policy/korean_text.py` + `init/06-policy-strip-ko.sql` — RDB 채널 한국어 조사/어미 제거 (v2.71)
