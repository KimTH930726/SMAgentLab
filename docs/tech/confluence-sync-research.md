# Confluence 현행화(증분 동기화) 업계 조사 + 우리 MVP 제안

작성 2026-10-28 · 공개 자료만 조사(사내 Confluence·시스템 접근 없음) · 관련: `docs/tech/source-ledger-plan.md`(원문 원장)

---

## 1. 요약 (한 화면)

- **업계 표준은 "자주 도는 증분 + 드물게 도는 전체 대조" 2단 구조다.**
  - 증분: CQL `lastmodified >= 지난 실행 시각`으로 바뀐 페이지만 가져온다(Onyx 30분, MS Copilot 커넥터 15분, Glean 1시간).
  - 전체 대조(pruning/full crawl): 지금 보이는 페이지 ID 목록 전체를 받아 우리 쪽 목록과 비교해 **사라진 것**을 찾는다(Onyx 7일, MS 매일, Glean 7일).
  - 이유: `lastmodified` 질의로는 **삭제·권한 제한·(일부) 이동이 안 잡힌다.** MS는 "증분 크롤은 ACL 변경과 삭제를 못 잡고 전체 크롤만 잡는다"고 명시한다
    ([MS Learn](https://learn.microsoft.com/en-us/microsoft-365/copilot/connectors/confluence-onpremises-deployment)).
- **변경 판정은 버전 번호/수정 시각으로 충분하다(LLM 불필요).** Onyx도 증분은 `lastmodified`만 쓰고 `version.number`는 쓰지 않는다
  ([Onyx connector.py](https://github.com/onyx-dot-app/onyx/blob/main/backend/onyx/connectors/confluence/connector.py)). 우리는 이미 `source_version`·`content_hash`가 있으니 "버전↑ 그리고 해시 다름"이면 변경.
- **삭제 판정은 2단계로 해야 안전하다.** `GET /rest/api/content/{id}` 404는 "없음"과 "권한 없음"을 구분 못 한다(Atlassian REST 문서). `?status=any`로 다시 물어 `trashed`면 휴지통, 계속 404면 "완전 삭제 또는 안 보임" → 사람 확인.
- **Webhook은 DC에서만 관리자 설정으로 가능**(page_updated/moved/removed/restored, content_permissions_updated). 하지만 페이로드는 ID뿐이고 전달 보장이 없다(서킷 브레이커로 건너뜀) → Glean도 webhook + 주기 크롤을 **병행**한다. 우리는 폐쇄망·소규모라 webhook 없이 폴링으로 충분.
- **"우리 쪽 정정 vs 원문 재적재" 충돌을 다루는 도구는 조사 범위에서 못 찾았다.** 전부 원문=정답(덮어쓰기)이다. → 우리 고유 문제이므로 단순 규칙(정정 있는 청크가 바뀌면 자동 덮어쓰기 금지 + 경고)로 시작.
- **MVP 제안:** 자동으로는 "확인(감지)"만, 반영은 사람이 [다시 가져오기]를 누르는 반자동. 감지 = (a) 하루 1회 증분 CQL + (b) 주 1회 ID 전체 대조. LLM 0.

---

## 2. 업계 방식 표

| 도구 | 변경 감지 | 삭제·이동 감지 | 기본 주기 | 로컬 수정 처리 | 출처 |
|---|---|---|---|---|---|
| **Onyx**(구 Danswer) | CQL `lastmodified >= '%Y-%m-%d %H:%M'` + `order by lastmodified asc`, 분 단위라 시간대 오차 대비 끝 시각 +1일, 오류 시 시작 −1시간 재시도. 버전 번호 미사용 | **Pruning**: "slim doc"(ID만) 전체 목록을 받아 인덱스와 비교 후 없는 것 삭제. 첨부 목록은 일부만 받으면 오삭제되므로 "전부 아니면 실패" | 증분 30분, pruning 7일 | 없음(원문 덮어씀) | [docs](https://docs.onyx.app/admins/connectors/official/confluence), [connector.py](https://github.com/onyx-dot-app/onyx/blob/main/backend/onyx/connectors/confluence/connector.py), [app_configs.py](https://github.com/onyx-dot-app/onyx/blob/main/backend/onyx/configs/app_configs.py) |
| **MS 365 Copilot 커넥터**(On-prem DC/Server 8.0+) | 증분 크롤 = 신규·수정만. `/rest/api/content/search`(CQL) 사용 | **전체 크롤에서만** 삭제·ACL 변경 감지 | 증분 15분, 전체 매일 | 없음 | [On-prem](https://learn.microsoft.com/en-us/microsoft-365/copilot/connectors/confluence-onpremises-deployment), [Cloud](https://learn.microsoft.com/en-us/microsoft-365/copilot/connectors/confluence-cloud-overview) |
| **Glean**(DC) | Webhook(생성·수정·삭제·이동/복원·권한) 근실시간 + 증분 크롤 | Webhook + 7일 전체 크롤로 보정 | 증분 1시간, 전체 7일, 권한(스페이스) 3시간, 사용자·그룹 10분 | 없음. 서버 부하 기본 합계 약 16 QPS(설정 가능) | [Glean DC](https://docs.glean.com/connectors/native/confluence-onprem) |
| **Atlassian Rovo**(Cloud) | 제품 내부 이벤트 | "삭제 이벤트를 듣고 인덱스 갱신" (SLA 수치 공개 없음) | 비공개 | 없음 | [Rovo 데이터 정책](https://intercom.help/rovo/en/articles/9426128-rovo-data-privacy-and-usage-guidelines) |
| **RAGFlow** | 갱신 주기(refresh interval)마다 | "Sync deleted files" 토글 + 별도 정리 주기(cleanup interval) — 증분과 삭제 대조를 분리 | 기본값 문서 미기재 | 없음 | [RAGFlow](https://ragflow.io/docs/add_data_source/data_source_configuration) |
| **Amazon Bedrock KB** | 원본의 변경 추적으로 증분(추가·수정·삭제). Cloud만 지원 | 증분에 삭제 포함(방법 비공개) | 사람이 Sync 실행/API | 없음 | [Bedrock](https://docs.aws.amazon.com/bedrock/latest/userguide/confluence-data-source-connector.html) |
| **Airbyte** | **Full Refresh만**(증분 미지원) | 매번 전체 | 스케줄러 | 없음 | [Airbyte](https://docs.airbyte.com/integrations/sources/confluence) |
| **LangChain** indexing API | RecordManager가 문서 해시 기록 → 같으면 건너뜀 | `cleanup="full"`만 사라진 원문 삭제(로더가 **전체**를 줘야 함, 일부만 주면 오삭제), `incremental`/`scoped_full`은 사라진 원문 못 지움 | 호출자 몫 | 없음 | [LangChain index()](https://reference.langchain.com/python/langchain-core/indexing/api/index), [how-to](https://js.langchain.com/docs/how_to/indexing) |
| **LlamaIndex** IngestionPipeline | docstore에 doc_id→해시, 해시 바뀌면 재처리(upsert) | `UPSERTS_AND_DELETE`: 이번 입력에 없는 doc 삭제 | 호출자 몫 | 없음 | [LlamaIndex 문서 관리](https://developers.llamaindex.ai/python/examples/ingestion/document_management_pipeline/) |
| **Dify** | 조사 범위에서 Confluence 네이티브 동기화 문서 못 찾음 | — | — | — | — |

**공통 교훈 3가지**
1. 시각 기반 증분은 삭제를 못 본다 → 삭제는 "전체 ID 대조"로 따로 한다(Onyx·MS·RAGFlow·LangChain 모두 같은 결론).
2. 전체 대조는 **목록을 끝까지 다 받았을 때만** 삭제 판정을 한다(Onyx 첨부 all-or-nothing, LangChain full 모드 경고). 중간 실패한 목록으로 지우면 대량 오삭제.
3. 권한 변경은 `lastmodified`를 안 바꾸므로 전체 크롤/별도 권한 크롤에서만 잡힌다(MS·Glean).

---

## 3. Confluence DC/Server vs Cloud API 차이

| 항목 | Data Center / Server | Cloud |
|---|---|---|
| 기본 경로 | `https://<host>[/context]/rest/api/...` | `https://<site>.atlassian.net/wiki/rest/api/...`(v1), `/wiki/api/v2/...`(v2) |
| 인증 | **PAT를 Bearer 토큰**으로(`Authorization: Bearer <PAT>`). Onyx는 Server 7.9+ 요구 | 이메일 + API 토큰(Basic) 또는 OAuth 2.0 |
| 검색(CQL) | `GET /rest/api/content/search?cql=...` | v1 `/wiki/rest/api/search?cql=...`(v2 `/pages`는 CQL 없음, cursor 페이징) |
| 페이징 | `start`/`limit` 오프셋 | cursor(`_links.next`) — Onyx 주석도 "Cloud는 cursor, Server는 offset" |
| Webhook | **관리자 UI/REST로 등록 가능**(7.7+). 이벤트: `page_created/updated/moved/removed/trashed/restored`, `content_permissions_updated` 등. 페이로드는 ID 위주, 5회 연속 실패 시 건너뜀(최대 10시간 백오프) — 전달 보장 문서화 안 됨 | 관리자 직접 webhook 없음 → Connect/Forge 앱 필요(일반 운영엔 폴링) |
| 레이트 리밋 | 관리자가 켜야 동작(토큰 버킷, 사용자·노드 단위). 초과 시 429 + `retry-after`, `X-RateLimit-*` 헤더. 문서상 대상 인증 = Basic/OAuth/세션 쿠키(PAT 언급 없음 → 운영 담당에 확인) | 2026-03부터 앱(Forge/Connect/OAuth) 포인트 기반. API 토큰 트래픽은 기존 burst 한도 |
| 지원 상태 | Server는 2024-02-15 지원 종료, DC 계속 | — |

출처: [CQL 필드](https://developer.atlassian.com/server/confluence/cql-field-reference/), [DC Webhook](https://confluence.atlassian.com/doc/managing-webhooks-1021225606.html), [DC 레이트 리밋](https://confluence.atlassian.com/doc/improving-instance-stability-with-rate-limiting-992679004.html), [Cloud 레이트 리밋](https://developer.atlassian.com/cloud/confluence/rate-limiting), [REST(server)](https://docs.atlassian.com/atlassian-confluence/REST/latest-server/), [Onyx docs](https://docs.onyx.app/admins/connectors/official/confluence)

> 주의: Atlassian 문서 표기상 `page_removed` = 휴지통으로 이동, `page_trashed` = 휴지통에서 완전 삭제(이름과 반대 느낌). 쓸 일이 생기면 실 인스턴스로 확인.

### 3-1. 변경 조회 CQL 예시 (DC)

```
GET /rest/api/content/search
  ?cql=space in (OPS,DEV) and type=page and lastmodified >= "2026-10-27 00:00" order by lastmodified asc
  &expand=version,space,ancestors
  &limit=100&start=0
```
- `lastmodified` 형식: `yyyy-MM-dd HH:mm` / `yyyy/MM/dd` 등, **분 단위**, 시간대 = 조회 사용자 설정(기본 서버 시간대). → 지난 실행 시각에서 여유(예: −1일)를 두고 겹치게 조회, 중복은 버전 비교로 걸러낸다(Onyx와 같은 방식).
- 특정 페이지 하위만: `ancestor = 123456`.
- CQL 결과는 **PAT 주인이 볼 수 있는 것만** 나온다(권한 필터). 그래서 "안 보이게 된 것"과 "삭제"가 목록상 똑같이 보인다.

### 3-2. 버전 조회

```
GET /rest/api/content/{id}?expand=version,space,ancestors          → version.number, version.when, space.key, ancestors[]
GET /rest/api/content/{id}?expand=body.storage,version             → 본문(재적재 시)
```
- 판정: `version.number > source_version`이면 후보, 본문 해시까지 다를 때만 "변경". 같은 번호면 본문을 받지 않는다(호출 절약).
- 이동은 `space.key`·`ancestors[-1].id`(부모)를 원장에 같이 저장해 두면 비교만으로 감지된다. 이동이 `lastmodified`/버전을 올리는지는 버전별로 다를 수 있어 **주 1회 전체 대조에서 같이 비교**하는 쪽이 안전(실 인스턴스 확인 필요).

### 3-3. 삭제 확인 방법

1. 전체 대조: `cql=space in (...) and type=page`로 ID만(`expand` 최소) 끝까지 받는다. **목록을 끝까지 다 받은 경우에만** "우리 원장엔 있고 목록엔 없는 ID"를 후보로.
2. 후보마다 `GET /rest/api/content/{id}?status=any`:
   - 200 + `status=trashed` → **휴지통**(복원 가능) → "삭제됨(휴지통)" 표시.
   - 200 + `status=current`인데 목록에 없었다 → 대조 대상 스페이스 밖으로 **이동** 또는 질의 문제 → "이동" 후보.
   - 404 → 완전 삭제 **또는 PAT 권한 상실(페이지 제한)** — API로 구분 불가 → "확인 필요"로 사람에게.
3. Cloud(v2)도 같은 구조: `GET /wiki/api/v2/pages/{id}`의 `status`(current/trashed/…)로 판정, 404는 동일하게 모호.

---

## 4. 우리에게 맞춘 MVP 제안

### ① 지금 막힌 일
- 팀별 초기 적재는 수동으로 끝나는데, 그 뒤 Confluence가 바뀌어도 **우리는 모른다.** 옛 내용으로 답하거나(정확도↓), 삭제된 페이지를 근거로 내세운다.
- 다시 가져오면 **우리 쪽 정정(corrections)이 조용히 덮인다**(원문=SoT 원칙과 정정 워크플로가 충돌).
- 이동·제목 변경 시 출처 링크·업무 영역 분류가 낡는다.

### ② 하는 것 (MVP)
1. **원장 칸 보강**(`rag_source_document`): `space_key`, `parent_id`, `title`, `source_modified_at`, `last_checked_at`, `sync_state`
   (`current | changed | moved | trashed | missing | error`). 기존 `source_version`·`content_hash` 그대로 활용.
2. **변경 확인(자동, 하루 1회 + 수동 버튼)** — 팀(스페이스)별 CQL `lastmodified >= 마지막 확인 −1일`. 버전·해시만 비교(LLM 0, 본문은 버전이 올랐을 때만 받음). 결과는 `sync_state=changed`로 **표시만**.
3. **삭제·이동 대조(자동, 주 1회)** — 3-3 절차. 목록이 중간에 실패하면 그 회차는 판정 전체를 버리고 `error`로 남긴다(대량 오판 방지, Onyx all-or-nothing 교훈). `trashed/missing`은 **검색에서 바로 빼지 않고** 관리 화면에 "확인 필요"로 — 사람이 [내리기] 누르면 그때 `superseded`/비활성.
4. **[다시 가져오기] 반자동** — 관리 화면 "원문 바뀜 N건" 카드 → 건별 [다시 가져오기]. 기존 적재 파이프라인을 페이지 1건 단위로 재실행하고, 새 원장 줄 + `superseded_by` 연결(원장 설계 그대로). 적재 후 `scripts/check_ingest.py` 같은 구조 검사.
5. **정정 충돌 경고** — 다시 가져오기 전, 그 페이지의 파생 청크에 승인된 정정이 있으면: 자동 덮어쓰기 금지, "정정 K건이 있는 페이지입니다 — 원문 변경 부분과 정정을 보고 결정" 경고 + 선택지 [원문으로 교체(정정 폐기)] / [정정 유지하고 원문만 원장에 기록] / [보류]. 어떤 정정이 걸렸는지 목록만 보여준다(비교는 사람이, LLM 0).
6. **실패는 보이게** — 확인 작업 실패(401 토큰 만료, 429, 네트워크)는 화면 카드에 "마지막 확인 실패: 사유·시각", 로그 남김. 429면 `retry-after` 따르고 순차 호출(동시성 1~2).

### ③ 안 하는 것 + 다시 볼 조건
| 안 하는 것 | 이유 | 다시 볼 조건 |
|---|---|---|
| Webhook 실시간 반영 | 폐쇄망 인바운드 경로·관리자 권한 필요, 전달 보장 없음(어차피 폴링 병행 필요), 수천 페이지·하루 단위면 충분 | "하루 늦게 반영돼서 틀린 답" 사례가 실제로 쌓일 때 |
| 변경 시 자동 재적재 | 정정 충돌·잘못된 원문 수정이 그대로 퍼짐, 사람 승인 원칙 | 정정 없는 페이지 재적재가 수십 건 연속 무사고일 때 "정정 없는 페이지만 자동" 검토 |
| 권한(페이지 제한) 동기화 | 우리 시스템 권한 모델(파트=조직·권한 매핑)과 별개, 현재 팀 지식은 팀 단위 공개 전제 | 제한 페이지를 적재해야 하는 팀이 나올 때 — 최소한 "제한 걸린 페이지는 적재 안 함" 가드부터 |
| 첨부파일 동기화 | 초기 적재 범위 밖, 형식별 파서 부담 | 첨부(엑셀·PDF)에만 있는 지식으로 못 답한 사례가 나올 때 |
| 버전 간 diff 요약(LLM) | 비용·민감 원문 처리 부담. 버전·해시로 "바뀜"만 알면 됨 | 사람이 바뀐 곳 찾기 어렵다는 피드백 → 우선 LLM 없는 텍스트 diff부터 |
| 댓글·블로그 | 업계도 부분 지원(Glean 댓글은 전체 크롤에서만) | 요청 시 |
| Cloud 대응 | 우리는 PAT = DC로 추정 | Cloud 이관 결정 시(경로·cursor 페이징·인증만 교체) |

---

## 5. 열린 질문 (우리가 결정할 것)

1. **우리 Confluence가 DC인지·버전**(webhook 7.7+, PAT 7.9+ 확인)과 **관리자가 REST 레이트 리밋을 켜 두었는지·PAT 요청도 대상인지** — 운영 담당 확인.
2. **PAT 주인** — 개인 PAT면 그 사람 권한에 따라 "삭제처럼 보임(404)"이 생기고, 퇴사·권한 변경 시 전부 missing이 된다. 팀별 서비스 계정 PAT로 갈지.
3. **확인 주기** — 제안: 증분 하루 1회, 대조 주 1회. 팀이 원하는 반영 지연 허용치는?
4. **trashed/missing 기본 동작** — 사람 확인 전까지 검색에 **계속 노출** vs **노출하되 "원문 삭제 의심" 표시** vs 즉시 숨김. (제안: 휴지통은 즉시 숨김 후보 + 확인, 404는 노출 유지 + 확인)
5. **정정 충돌 시 기본 선택지** — 원문 우선(SoT) vs 정정 우선. 정정을 "원문에 되돌려 써 달라"고 페이지 담당자에게 알리는 흐름이 필요한지(정정이 오래 살아남는 게 맞는지).
6. **이동의 의미** — 스페이스 이동 = 팀(업무 영역) 이관으로 볼지, 단순 링크 갱신으로 볼지. 업무 영역 트리 재분류를 사람이 할지.
7. **[다시 가져오기] 권한** — 지식 담당자(owner)만 vs 관리자만.
8. **대조 범위 정의** — 팀별 "스페이스 전체"인지 "특정 상위 페이지 하위(`ancestor=`)"인지. 범위 밖으로 옮겨진 페이지를 이동/삭제 중 무엇으로 볼지가 여기서 갈린다.
