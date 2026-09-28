"""테이블 명세서(엑셀) 추출 — 실 DB 카탈로그 기준 + 공통화 분류 초안 (2026-09-28).

팀 공통 RDS로 옮길 테이블을 나누기 위해 "지금 실제로 있는 것"을 뽑는다. 구조(타입·제약·인덱스·
트리거·행 수·컬럼별 채움률)는 전부 카탈로그와 실제 데이터에서 계산하고, 사람이 판단할 부분(용도·
도메인·분류 초안·근거·컬럼 설명)만 아래 NOTES에 코드·마이그레이션 주석을 근거로 적어 병합한다.
`docs/table-definition.md`는 2026-09-16부터 마이그레이션 이력만 두므로 이 산출물이 전체 명세다.

데이터 값은 한 줄도 내보내지 않는다(행 수·채움률만). 재실행하면 그 시점 기준으로 다시 뽑힌다.

실행 (컨테이너 안):
    docker compose exec backend python scripts/export_table_spec.py /tmp/table_spec.xlsx
"""
import asyncio
import datetime
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, "/app")

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

from core.database import close_pool, get_conn, init_pool

APP_ROOT = Path("/app")

# ── 분류 체계 ────────────────────────────────────────────────────────────────
CLS_PLATFORM = "공통 · 플랫폼"
CLS_ASSET = "공통 · 지식 데이터 자산"
CLS_EVAL = "공통 후보 · 운영/평가"
CLS_SERVICE = "서비스 전용 · 챗봇"
CLS_VOC = "서비스 전용 · VOC 이메일"
CLS_DROP = "폐기 검토"
CLS_REDESIGN = "재설계 후 결정"

CLS_ORDER = [CLS_PLATFORM, CLS_ASSET, CLS_EVAL, CLS_SERVICE, CLS_VOC, CLS_REDESIGN, CLS_DROP]
CLS_DESC = {
    CLS_PLATFORM: "어떤 팀 서비스든 필요한 조직·사용자·권한·설정. 공통 RDS의 기반 스키마 후보.",
    CLS_ASSET: "로드맵의 '공통 지식 데이터 자산' — 여러 서비스가 같은 지식을 조회·검증. 공통화의 핵심.",
    CLS_EVAL: "질의 감사·피드백·정확도 실험 이력. 공통화 가치는 있으나 지금 스키마가 이 앱/정책 전용이라 일반화 필요.",
    CLS_SERVICE: "챗봇 서비스 자체의 대화 상태. 챗봇을 공통 서비스로 제공하면 그 서비스 소유 — 공통 데이터 레이어는 아님.",
    CLS_VOC: "VOC 이메일 분석 파이프라인 전용 운영 데이터.",
    CLS_REDESIGN: "구조를 다시 정한 뒤 넣기로 한 것(2026-09-28 결정). 지금 스키마 그대로 옮기지 않음.",
    CLS_DROP: "행 0건 + 코드가 읽지 않음, 또는 제거된 기능의 잔재. 공통 RDS로 옮기지 않는 쪽을 권장.",
}
CLS_FILL = {
    CLS_PLATFORM: "DBEAFE", CLS_ASSET: "D1FAE5", CLS_EVAL: "FEF3C7", CLS_SERVICE: "EDE9FE",
    CLS_VOC: "FCE7F3", CLS_REDESIGN: "FFEDD5", CLS_DROP: "E5E7EB",
}

# ── 테이블 주석: 도메인 / 용도 / 분류 초안 / 판단 근거 ─────────────────────────
T = {
    "ops_user": ("사용자·권한", "로그인 사용자(로컬 계정 + SSO 준비 컬럼), 역할(admin/user/viewer), 소속 파트, 개인 자격증명(암호화)",
                 CLS_PLATFORM, "모든 팀 서비스의 인증 주체. 단 개인 Confluence PAT·LLM 자격증명(암호화)은 이 앱의 Fernet 키에 묶여 있어 공통화 시 키 관리 주체부터 정해야 함. external_id/email은 SSO(Azure AD)용 선추가, 현재 0건."),
    "ops_part": ("사용자·권한", "조직 파트(팀) 마스터", CLS_PLATFORM,
                 "권한 스코프의 단위(네임스페이스 소유 파트, 사용자 소속). 공통 조직 마스터 후보 — 사내 인사/조직 SoR가 있으면 그걸 참조하는 쪽이 더 나음."),
    "ops_part_agent_access": ("사용자·권한", "파트별 사용 가능 에이전트(멀티 에이전트 시절 권한표)", CLS_DROP,
                              "행 0건, 앱 코드에서 참조 없음(마이그레이션만). Text2SQL·MCP 에이전트 제거(v2.51/v2.67) 뒤 남은 잔재."),
    "ops_namespace": ("테넌트", "데이터 격리 단위(예: '딜리버스 DB'). 거의 모든 데이터 테이블이 namespace_id FK로 매달림",
                      CLS_PLATFORM, "공통화 시 가장 먼저 정할 개념 — '네임스페이스=시스템/팀/도메인 중 무엇'인지. owner_part_id가 현재 0건(전부 공용)이라 소유 모델이 실사용으로 검증된 적 없음. ON DELETE CASCADE로 하위 데이터가 전부 지워지니 공통 DB에선 삭제 정책 재검토."),
    "ops_system_config": ("설정", "키-값 시스템 설정(캐시·VOC 폴링·보존정책·검색 임계치 등)", CLS_PLATFORM,
                          "키-값 구조라 공통화 쉬움. 단 키 이름이 이 앱 기능명(email_*, cache_*, retrieval_*)이라 공통 DB에선 서비스 접두사/스코프 컬럼 필요. email_graph_delegated 값은 암호화 자격증명."),
    "ops_prompt": ("설정", "LLM 프롬프트 템플릿(관리자 화면에서 수정). func_key로 조회", CLS_PLATFORM,
                   "프롬프트 관리 자체는 공통 기능 후보. 단 agent_type에 제거된 에이전트(text2sql, mcp_tool) 프롬프트 행이 남아 있어 옮기기 전 정리 필요."),
    "ops_prompt_category_guide": ("설정", "카테고리별 정적 답변 안내문(fewshot 대체안)", CLS_DROP,
                                  "v2.83에 스키마만 선추가, CRUD/UI 미착수, 행 0건, 앱 코드 참조 없음."),
    "ops_conversation": ("챗봇", "대화 세션(사용자·네임스페이스·에이전트)", CLS_SERVICE,
                         "챗봇 서비스 상태. 보존정책(chat_retention_days) 대상. 사내 LLM(DevX) 대화 id(inhouse_conv_id) 포함."),
    "ops_message": ("챗봇", "대화 메시지(질문/답변, 검색 결과·정책 인용 JSON)", CLS_SERVICE,
                    "챗봇 서비스 상태. content는 정책 원문이 섞일 수 있는 평문 — 보존·민감도 정책 대상."),
    "rag_conv_summary": ("챗봇", "대화 요약(4회 교환마다 LLM 요약) + 임베딩 — 시맨틱 리콜용", CLS_SERVICE,
                         "챗봇 메모리 기능 전용. 벡터 1024(KURE-v1) 고정."),
    "ops_query_log": ("운영·평가", "질의 감사 로그(누가·언제·무엇을 물었고 해결됐는지)", CLS_EVAL,
                      "감사로그(v2.92 user_id)·지식 공백 파악의 원천. 공통 서비스에 필요한 개념이지만 컬럼이 이 챗봇의 상태값(pending/resolved/no_knowledge)에 맞춰져 있음."),
    "ops_feedback": ("운영·평가", "답변 좋아요/나빠요 피드백", CLS_EVAL,
                     "피드백→지식 리뷰 신호의 입력. 실사용 43건으로 적음. message_id·meta 0건."),
    "policy_track2_run": ("운영·평가", "검색 전략 A/B 비교(골든셋) 실행 이력", CLS_EVAL,
                          "로드맵의 '정확도 검증 기반'에 해당해 공통 평가 이력 후보. 단 컬럼이 정책 A/B 전용(a_hit_rate, b_hit_rdb_only…)이라 그대로 옮기면 다른 축이 못 씀 — 지표를 행 단위(전략, 지표명, 값)로 일반화 필요."),
    "rag_knowledge": ("지식 자산", "일반 지식 청크(수동·파일·컨플루언스) + 임베딩, 상태·버전·원본 추적", CLS_ASSET,
                      "지식 데이터 자산의 중심 테이블. 생명주기(staging→active, deprecated, deleted)·원본 추적(confluence_page_id)·임베딩 모델 기록까지 갖춤. 공통화 시 created_by_part(문자열)→part FK 정규화, 사용 안 하는 선추가 컬럼(quality_score, logical_document_id 로직 없음) 정리 검토."),
    "rag_knowledge_category": ("지식 자산", "네임스페이스별 지식 카테고리(업무구분) 마스터", CLS_ASSET,
                               "지식 분류 체계. 공통화 시 네임스페이스 스코프를 유지할지 공통 분류체계로 올릴지 결정."),
    "rag_knowledge_duplicate_match": ("지식 자산", "등록 시 중복 의심 판정 근거(신규↔기존 유사도)", CLS_ASSET,
                                      "승인 대기 검토 화면의 근거 데이터. rag_knowledge와 한 묶음으로 이동."),
    "rag_knowledge_history": ("지식 자산", "지식 병합 시 덮어쓰기 전 내용 보존(이력)", CLS_ASSET,
                              "0건이지만 코드가 사용(병합 시 적재) — 데이터 소실 방지 장치라 rag_knowledge와 함께 이동."),
    "rag_knowledge_review_flag": ("지식 자산", "나빠요 피드백이 가리킨 근거 지식을 리뷰 후보로 표시", CLS_ASSET,
                                  "0건이지만 코드가 사용. 지식 품질 루프의 일부."),
    "rag_ingestion_job": ("지식 자산", "대량 등록 작업(진행률·취소·결과 카운트)", CLS_ASSET,
                          "지식 적재 파이프라인의 작업 단위(v2.108 원자적 활성화의 기준). 적재 파이프라인을 공통화하면 함께 이동."),
    "rag_glossary": ("지식 자산", "용어집(용어·설명·임베딩) — 질문 용어 매핑", CLS_ASSET,
                     "정책서 용어정의 시트와 자동 용어 추출이 모두 여기로 적재. 공통 용어 사전 후보(팀 간 용어 통일 가치 큼)."),
    "policy_item": ("지식 자산", "정책서 한 행(원문·분류경로·버전·검토상태·파싱상태·파이프라인 버전)", CLS_ASSET,
                    "로드맵이 '공통 지식 데이터'의 출발점으로 삼은 정책서 구조화 결과. INSERT-only 버전관리(logical_id/version/supersedes_id) + 재처리(pipeline_version)까지 갖춤. 단 이름·코드가 정책 전용(service/policy 하드코딩)이라 공통화 시 '조건·값 단위 지식' 일반 스키마로 볼지 결정."),
    "policy_param": ("지식 자산", "정책에서 추출한 파라미터 팩트(이름·조건·값·단위) — 정확 조회용", CLS_ASSET,
                     "'조건·값 단위 지식'의 실체. external_source 0건·코드 미사용(외부 SoR 연동 대비 선추가)."),
    "policy_chunk": ("지식 자산", "정책 서술 청크 + 임베딩 — 의미 검색용", CLS_ASSET,
                     "policy_item의 벡터 채널. 벡터 1024 고정, embedding_model 컬럼 없음(rag_knowledge와 불일치)."),
    "ref_common_code": ("참조 데이터", "공통코드(그룹코드·코드값·코드명) — 정확 조회 전용", CLS_ASSET,
                        "결정론 파서로 적재한 구조화 참조데이터(252건). 시스템 코드표는 팀 공통 가치 큼. work_code·mgmt_values 0건."),
    "ref_db_column": ("참조 데이터", "DB 스키마 사전(테이블·컬럼·설명) — 정확 조회 전용", CLS_REDESIGN,
                      "행 0건. 2026-09-28 결정: 지금 구조(마크다운 표→컬럼당 1행) 그대로 넣지 않고 구조화 방식을 다시 정한 뒤 적재."),
    "ops_email_analysis": ("VOC 이메일", "수신 메일 건별 분석 결과(분류·심각도·해결초안·클러스터)", CLS_VOC,
                           "VOC 파이프라인 전용. subject/body/sender는 개인정보·사내 메일 원문 — 보존정책 30일(retention.py) 대상."),
    "ops_email_poll_cycle": ("VOC 이메일", "메일 폴링 주기별 실행 통계", CLS_VOC,
                             "운영 모니터링 로그(행 수가 가장 많음, 5,700+). 공통화 가치 낮음."),
    "ops_voc_cluster": ("VOC 이메일", "반복 VOC 패턴 클러스터(대표 임베딩·건수·알림)", CLS_VOC,
                        "VOC 반복 탐지 전용. coverage_knowledge_id로 지식과 연결."),
    "ops_voc_routing": ("VOC 이메일", "메일함→파트 라우팅, Teams 웹훅, 당직 연락처", CLS_VOC,
                        "VOC 전용 설정. teams_webhook_url·당직 연락처는 민감 정보."),
}

# ── 컬럼 주석(비직관 컬럼만; 나머지는 공통 규칙) ──────────────────────────────────
C = {
    ("ops_user", "hashed_password"): "bcrypt 해시", ("ops_user", "role"): "admin / user / viewer(v2.93, 파트 무관 읽기전용)",
    ("ops_user", "auth_provider"): "local(현재 전부) / SSO 도입 시 azure 등", ("ops_user", "external_id"): "SSO 외부 식별자 — 선추가, 코드 미사용",
    ("ops_user", "email"): "SSO용 — 선추가", ("ops_user", "encrypted_confluence_pat"): "개인 Confluence PAT(Fernet 암호화)",
    ("ops_user", "encrypted_llm_credentials"): "개인 사내 LLM 자격증명 트리플(Fernet 암호화)",
    ("ops_namespace", "owner_part_id"): "소유 파트 — NULL이면 공용(현재 전부 NULL)",
    ("ops_system_config", "key"): "설정 키(예: cache_ttl, email_*, retrieval_*, chat_retention_days)",
    ("ops_system_config", "value"): "문자열로 저장(숫자·불리언도). email_graph_delegated는 암호화 JSON",
    ("ops_prompt", "func_key"): "코드가 조회하는 키(예: chat_system, email_voc_analysis_prompt)",
    ("ops_prompt", "agent_type"): "all / knowledge_rag (text2sql·mcp_tool은 제거된 에이전트 잔재)",
    ("ops_conversation", "trimmed"): "메시지 100건 캡으로 앞부분이 잘렸는지", ("ops_conversation", "inhouse_conv_id"): "사내 LLM 게이트웨이 대화 id",
    ("ops_message", "results"): "근거 지식 검색 결과 JSON(답변 카드용)", ("ops_message", "metadata"): "policy_citations 등 부가 JSON",
    ("ops_message", "mapped_term"): "용어집 매핑된 용어", ("ops_message", "status"): "generating / completed / failed",
    ("ops_query_log", "status"): "pending / resolved / unresolved / no_knowledge", ("ops_query_log", "resolved_knowledge_id"): "해결 처리 시 연결한 지식",
    ("ops_query_log", "user_id"): "질문자(v2.92 감사로그, 이전 기록은 NULL 유지)",
    ("ops_feedback", "meta"): "부가 JSON — 0건", ("ops_feedback", "knowledge_id"): "피드백 대상 지식(프론트가 넘긴 첫 근거)",
    ("rag_knowledge", "status"): "staging / staging_review(수집 중, v2.108) → active / pending_review / rejected / deprecated / deleted",
    ("rag_knowledge", "base_weight"): "검색 가중치(기본 1.0, 피드백으로 조정)", ("rag_knowledge", "source_type"): "manual / file_upload / paste_split / csv_import / confluence / confluence_bulk",
    ("rag_knowledge", "source_chunk_idx"): "등록 job 안에서의 청크 순번", ("rag_knowledge", "ingestion_job_id"): "등록 작업(스테이징 전환·취소 정리의 키)",
    ("rag_knowledge", "logical_document_id"): "버전 체인 논리 id — 선추가(v2.84 Phase 0), 읽는 코드 없음",
    ("rag_knowledge", "version"): "버전 번호 — 선추가, 로직 없음", ("rag_knowledge", "supersedes_id"): "이전 버전 — 선추가, 0건",
    ("rag_knowledge", "embedding_model"): "임베딩 생성 모델(재인덱싱 대상 추적)", ("rag_knowledge", "quality_score"): "품질 점수 — 선추가, 코드 미사용, 0건",
    ("rag_knowledge", "reviewed_at"): "검토 시각", ("rag_knowledge", "owner"): "담당자",
    ("rag_knowledge", "confluence_page_id"): "원본 Confluence 페이지(재등록 시 옛 버전 교체 키, v2.115부터 채워짐)",
    ("rag_knowledge", "confluence_version"): "원본 페이지 버전", ("rag_knowledge", "heading_path"): "조상 헤딩 경로(검색 컨텍스트 보강)",
    ("rag_knowledge", "created_by_part"): "등록 파트명(문자열 — part FK 아님)",
    ("rag_ingestion_job", "status"): "processing / completed / failed / cancelled", ("rag_ingestion_job", "pending_chunks"): "중복 의심으로 승인 대기가 된 수",
    ("rag_ingestion_job", "cancel_requested"): "취소 요청 플래그(배치 경계·완료 시 확인)", ("rag_ingestion_job", "auto_glossary"): "job 성공 후 자동 추출된 용어 수",
    ("rag_ingestion_job", "analyzer_result"): "LLM 문서 분석 결과 JSON", ("rag_ingestion_job", "chunk_strategy"): "청킹 전략 — 현재 0건",
    ("rag_knowledge_duplicate_match", "similarity"): "코사인 유사도", ("rag_knowledge_history", "replaced_by_knowledge_id"): "병합으로 덮어쓴 새 지식",
    ("rag_knowledge_review_flag", "reason"): "리뷰 사유", ("rag_conv_summary", "turn_start"): "요약 구간 시작 턴",
    ("rag_glossary", "created_by_part"): "등록 파트명(문자열)",
    ("policy_item", "system_key"): "네임스페이스 안의 하위 시스템 구분", ("policy_item", "category_path"): "분류 경로 배열(팀마다 깊이가 달라 가변)",
    ("policy_item", "raw_body"): "정책 원문(조건/상세 셀)", ("policy_item", "remark"): "비고 — 0건",
    ("policy_item", "source_file"): "원본 엑셀 파일명", ("policy_item", "source_row"): "원본 행 번호(골든셋 정답 위치)",
    ("policy_item", "content_hash"): "원본 4필드 해시 — 재업로드 변경 감지", ("policy_item", "status"): "pending_review / active / rejected / deprecated",
    ("policy_item", "logical_id"): "버전 체인 논리 id(트리거로 자기 id 기본값)", ("policy_item", "supersedes_id"): "이전 버전",
    ("policy_item", "parse_status"): "parsed / partial / unresolved (LLM 분해 결과)", ("policy_item", "unresolved_segments"): "분류 실패 조각과 사유 JSON",
    ("policy_item", "reviewed_by"): "승인/반려자(v2.105)", ("policy_item", "pipeline_version"): "생성 파이프라인 버전 r{개정}-{프롬프트해시}(v2.114) — 기존 행 NULL",
    ("policy_param", "external_source"): "외부 SoR 연동용 — 선추가, 코드 미사용, 0건",
    ("policy_chunk", "chunk_text"): "서술 조각(LLM 분해 결과 또는 폴백 원문)", ("policy_chunk", "chunk_idx"): "item 내 순번",
    ("policy_track2_run", "by_type"): "질의 유형별 지표 JSON", ("policy_track2_run", "triggered_by"): "실행한 사용자",
    ("ref_common_code", "work_code"): "업무 코드 — 0건", ("ref_common_code", "mgmt_values"): "관리 항목 값 JSON — 0건",
    ("ops_email_analysis", "status"): "analyzed / notified / skipped_relevance 등", ("ops_email_analysis", "category"): "system_error / user_mistake / not_it_related / uncertain",
    ("ops_email_analysis", "severity"): "low / medium / high / urgent", ("ops_email_analysis", "mismatch_flagged"): "담당 파트 오배치 의심",
    ("ops_email_analysis", "knowledge_ref_ids"): "판단 근거 지식 id 배열", ("ops_email_analysis", "embedding"): "이슈 요약 임베딩(반복 패턴 클러스터링)",
    ("ops_email_analysis", "notify_error"): "Teams 발송 실패 사유", ("ops_voc_cluster", "coverage_verified"): "기존 지식이 이 패턴을 커버하는지 LLM 검증 결과",
    ("ops_voc_routing", "mailbox_upn"): "수집 대상 메일함", ("ops_voc_routing", "teams_webhook_url"): "알림 웹훅 URL",
    ("ops_user", "username"): "로그인 아이디(유니크)", ("ops_prompt", "func_name"): "관리 화면 표시명",
    ("ops_prompt", "content"): "프롬프트 본문(관리자 수정 가능 — 코드 기본값은 최초 시드만)",
    ("policy_item", "policy_name"): "정책명", ("policy_item", "source_sheet"): "원본 시트명",
    ("policy_item", "version"): "버전 번호(재업로드로 내용이 바뀌면 +1)", ("policy_item", "reviewed_at"): "승인/반려 시각",
    ("policy_param", "name"): "파라미터명", ("policy_param", "condition"): "적용 조건(예: 특정 매장 유형)",
    ("policy_param", "value"): "값", ("policy_param", "unit"): "단위(개, 원, 분 등)",
    ("rag_glossary", "term"): "용어", ("rag_glossary", "description"): "용어 설명(용어집 비고 포함)",
    ("rag_ingestion_job", "source_type"): "등록 경로(rag_knowledge.source_type과 같은 값)", ("rag_ingestion_job", "total_chunks"): "요청 청크 수",
    ("rag_ingestion_job", "created_chunks"): "적재된 청크 수(진행률)", ("rag_ingestion_job", "embedding_model"): "사용 임베딩 모델",
    ("rag_ingestion_job", "error_message"): "실패 사유", ("rag_ingestion_job", "completed_at"): "종료 시각(완료·실패·취소)",
    ("rag_knowledge", "category"): "업무 구분(rag_knowledge_category.name과 대응, FK 아님)",
    ("rag_knowledge_category", "name"): "카테고리명",
    ("rag_knowledge_duplicate_match", "new_knowledge_id"): "새로 등록된(승인 대기) 지식",
    ("rag_knowledge_duplicate_match", "matched_knowledge_id"): "유사하다고 판정된 기존 지식",
    ("rag_knowledge_history", "replaced_at"): "덮어쓴 시각",
    ("rag_knowledge_review_flag", "resolved"): "리뷰 완료 여부", ("rag_knowledge_review_flag", "flagged_at"): "표시 시각",
    ("ref_common_code", "group_code"): "그룹 코드", ("ref_common_code", "group_code_name"): "그룹 코드명",
    ("ref_common_code", "code_id"): "코드 값", ("ref_common_code", "code_name"): "코드명",
    ("ref_db_column", "table_name"): "테이블명", ("ref_db_column", "table_comment"): "테이블 설명",
    ("ref_db_column", "column_name"): "컬럼명", ("ref_db_column", "column_comment"): "컬럼 설명",
    ("ref_db_column", "data_type"): "데이터 타입", ("ref_db_column", "nullable"): "NULL 허용 표기(원본 그대로)",
    ("ops_feedback", "question"): "피드백 당시 질문", ("ops_feedback", "is_positive"): "좋아요(true) / 나빠요(false)",
    ("ops_query_log", "question"): "질문", ("ops_query_log", "answer"): "답변", ("ops_query_log", "mapped_term"): "매핑된 용어",
    ("ops_query_log", "resolved_at"): "해결 처리 시각",
    ("policy_track2_run", "run_at"): "실행 시각", ("policy_track2_run", "top_k"): "평가 K", ("policy_track2_run", "total_n"): "채점 문항 수",
    ("policy_track2_run", "a_hit_rate"): "A(지식-only) hit@K", ("policy_track2_run", "b_hit_rate"): "B(하이브리드) hit@K",
    ("policy_track2_run", "a_precision"): "A precision@K", ("policy_track2_run", "b_precision"): "B precision@K",
    ("policy_track2_run", "b_hit_rdb_only"): "B 적중 중 RDB 채널만 맞힌 비율", ("policy_track2_run", "b_hit_vector_only"): "B 적중 중 벡터 채널만",
    ("policy_track2_run", "b_hit_both"): "B 적중 중 두 채널 모두", ("policy_track2_run", "a_top1_accuracy"): "A Top-1 정확도",
    ("policy_track2_run", "b_top1_param_accuracy"): "B Top-1(파라미터)", ("policy_track2_run", "b_top1_narrative_accuracy"): "B Top-1(서술)",
    ("policy_track2_run", "golden_set_file"): "사용한 골든셋 파일", ("policy_track2_run", "duration_seconds"): "실행 시간(초)",
    ("ops_email_analysis", "routing_id"): "적용된 라우팅 규칙", ("ops_email_analysis", "source_message_id"): "원본 메일 id(중복 방지 키)",
    ("ops_email_analysis", "mailbox_upn"): "수신 메일함", ("ops_email_analysis", "sender"): "발신자",
    ("ops_email_analysis", "subject"): "메일 제목", ("ops_email_analysis", "body"): "메일 본문", ("ops_email_analysis", "received_at"): "수신 시각",
    ("ops_email_analysis", "resolution_draft"): "해결 방안 초안(system_error일 때)", ("ops_email_analysis", "reasoning"): "판단 근거 요약",
    ("ops_email_analysis", "teams_sent_at"): "Teams 알림 발송 시각", ("ops_email_analysis", "voc_cluster_id"): "소속 반복 패턴 클러스터",
    ("ops_email_poll_cycle", "started_at"): "주기 시작", ("ops_email_poll_cycle", "finished_at"): "주기 종료",
    ("ops_email_poll_cycle", "error_summary"): "오류 요약",
    ("ops_email_poll_cycle", "namespaces_processed"): "처리한 네임스페이스 수", ("ops_email_poll_cycle", "mailboxes_ok"): "성공 메일함 수",
    ("ops_email_poll_cycle", "mailboxes_failed"): "실패 메일함 수", ("ops_email_poll_cycle", "total_fetched"): "가져온 메일 수",
    ("ops_email_poll_cycle", "total_analyzed"): "분석한 메일 수", ("ops_email_poll_cycle", "total_notified"): "알림 보낸 수",
    ("ops_email_poll_cycle", "total_notify_failed"): "알림 실패 수", ("ops_email_poll_cycle", "total_skipped_duplicate"): "중복 스킵 수",
    ("ops_email_poll_cycle", "total_skipped_low_relevance"): "관련성 낮아 스킵", ("ops_email_poll_cycle", "total_skipped_not_it"): "IT 무관 스킵",
    ("ops_voc_cluster", "representative_subject"): "대표 이슈 요약", ("ops_voc_cluster", "representative_embedding"): "대표 임베딩",
    ("ops_voc_cluster", "member_count"): "묶인 VOC 수", ("ops_voc_cluster", "first_seen_at"): "최초 발생",
    ("ops_voc_cluster", "last_seen_at"): "최근 발생", ("ops_voc_cluster", "notified_at"): "반복 알림 발송 시각",
    ("ops_voc_cluster", "coverage_knowledge_id"): "이 패턴을 다루는 기존 지식",
    ("ops_voc_routing", "part"): "담당 파트명", ("ops_voc_routing", "oncall_contact_name"): "당직자 이름",
    ("ops_voc_routing", "oncall_contact_phone"): "당직자 연락처", ("ops_voc_routing", "mail_folder_id"): "수집 대상 메일 폴더 id",
    ("ops_voc_routing", "mail_folder_name"): "메일 폴더명",
    ("ops_conversation", "title"): "대화 제목", ("ops_message", "role"): "user / assistant",
    ("rag_conv_summary", "summary"): "LLM 요약문", ("rag_conv_summary", "turn_end"): "요약 구간 끝 턴",
    ("ops_part_agent_access", "agent_type"): "허용 에이전트(제거된 에이전트 시절 권한표)",
    ("ops_prompt_category_guide", "category"): "대상 카테고리", ("ops_prompt_category_guide", "guide_text"): "정적 안내문",
    ("rag_knowledge", "content"): "지식 본문(청크)", ("rag_knowledge_history", "content"): "덮어쓰기 전 본문",
}

SENSITIVE = {
    ("ops_user", "hashed_password"): "비밀번호 해시", ("ops_user", "encrypted_confluence_pat"): "암호화 자격증명",
    ("ops_user", "encrypted_llm_credentials"): "암호화 자격증명", ("ops_user", "email"): "개인정보",
    ("ops_system_config", "value"): "일부 키가 암호화 자격증명", ("ops_voc_routing", "teams_webhook_url"): "웹훅 비밀 URL",
    ("ops_voc_routing", "oncall_contact_name"): "개인정보", ("ops_voc_routing", "oncall_contact_phone"): "개인정보",
    ("ops_email_analysis", "sender"): "개인정보", ("ops_email_analysis", "subject"): "메일 원문", ("ops_email_analysis", "body"): "메일 원문",
    ("ops_message", "content"): "대화 원문(정책 원문 포함 가능)", ("ops_query_log", "question"): "질의 원문", ("ops_query_log", "answer"): "답변 원문",
}

GENERIC = {
    "id": "PK(일련번호)", "created_at": "생성 시각", "updated_at": "수정 시각", "namespace_id": "소속 네임스페이스",
    "embedding": "임베딩 벡터(nlpai-lab/KURE-v1)", "created_by_user_id": "등록 사용자", "user_id": "사용자",
    "conversation_id": "소속 대화", "message_id": "관련 메시지", "knowledge_id": "대상 지식", "policy_item_id": "소속 정책 항목",
    "agent_type": "에이전트 구분(현재 knowledge_rag만 운영)", "name": "이름", "description": "설명", "content": "본문",
    "status": "상태", "source_file": "원본 파일명/출처 표기", "is_active": "활성 여부", "part_id": "소속 파트",
}

CROSS_CUTTING = [
    ("네임스페이스 = 테넌트 키", "지식·정책·참조·VOC 거의 모든 테이블이 ops_namespace FK(대부분 ON DELETE CASCADE)에 매달려 있다. 공통 RDS에선 '네임스페이스가 시스템/팀/도메인 중 무엇인지'와 삭제 정책을 먼저 정해야 나머지 분류가 확정된다."),
    ("임베딩 차원 고정", "벡터 컬럼 5곳(rag_knowledge, rag_glossary, policy_chunk, rag_conv_summary, ops_email_analysis·ops_voc_cluster)이 vector(1024)=KURE-v1에 고정. 공통 DB에서 모델을 바꾸면 전부 재임베딩. embedding_model 기록은 rag_knowledge에만 있고 policy_chunk에는 없음."),
    ("필수 확장", "pgvector(현재 0.8.2)·pg_trgm 필요. RDS PostgreSQL 16에서 둘 다 지원되지만 pgvector 버전과 HNSW 인덱스 파라미터를 맞춰야 함."),
    ("명명 규칙 불일치", "접두사가 ops_ / rag_ / policy_ / ref_ 로 섞여 있고 기준이 없다. 공통화 시 스키마 분리(예: common / knowledge / app_opsnav)를 권장."),
    ("파트 참조 방식 불일치", "사용자·네임스페이스는 part_id FK인데 rag_knowledge·rag_glossary의 created_by_part는 파트명 문자열. 공통 DB에선 FK로 정규화 권장."),
    ("상태값에 CHECK 제약 없음", "status류가 전부 VARCHAR라 허용값은 코드가 사실상 규격(컬럼 상세 '설명'에 실제 값 기재). 공통 DB에선 CHECK 또는 ENUM으로 고정 권장."),
    ("암호화 키 종속", "개인 PAT·LLM 자격증명·Graph 자격증명이 이 앱의 Fernet 키로 암호화돼 있다. 공통 DB로 옮기면 키 소유·로테이션 주체를 정해야 복호화 가능."),
    ("선추가 후 미사용 컬럼", "quality_score, logical_document_id/version(로직 없음), external_source, external_id 등 — '스키마는 지금이 싸다'로 넣었지만 쓰이지 않은 것. 옮길 때 정리 기회."),
]


def _fill(hex_):
    return PatternFill("solid", start_color=hex_, end_color=hex_)


HEAD_FILL = _fill("1E293B")
HEAD_FONT = Font(bold=True, color="FFFFFF")
THIN = Side(style="thin", color="CBD5E1")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP = Alignment(wrap_text=True, vertical="top")


def _write_table(ws, headers, rows, widths, name, start_row=1):
    for j, h in enumerate(headers, 1):
        c = ws.cell(row=start_row, column=j, value=h)
        c.fill, c.font, c.alignment, c.border = HEAD_FILL, HEAD_FONT, Alignment(vertical="center", wrap_text=True), BORDER
    for i, r in enumerate(rows, start_row + 1):
        for j, v in enumerate(r, 1):
            c = ws.cell(row=i, column=j, value=v)
            c.alignment, c.border = WRAP, BORDER
    for j, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(j)].width = w
    if rows:
        ref = f"A{start_row}:{get_column_letter(len(headers))}{start_row + len(rows)}"
        tbl = Table(displayName=name, ref=ref)
        tbl.tableStyleInfo = TableStyleInfo(name="TableStyleLight1", showRowStripes=False)
        ws.add_table(tbl)
    ws.freeze_panes = ws.cell(row=start_row + 1, column=1)


def _code_refs(tables: list[str]) -> dict[str, list[str]]:
    """테이블명을 참조하는 앱 코드 모듈(tests/scripts/main.py 마이그레이션 제외)."""
    refs: dict[str, set[str]] = defaultdict(set)
    pats = {t: re.compile(rf"\b{re.escape(t)}\b") for t in tables}
    for p in APP_ROOT.rglob("*.py"):
        rel = p.relative_to(APP_ROOT)
        if rel.parts[0] in ("tests", "scripts") or rel.name == "main.py" or "__pycache__" in rel.parts:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        module = "/".join(rel.parts[:2]) if len(rel.parts) > 2 else str(rel.parent if rel.parent != Path(".") else rel)
        for t, pat in pats.items():
            if pat.search(text):
                refs[t].add(module)
    return {t: sorted(v) for t, v in refs.items()}


async def collect(conn) -> dict:
    tables = [r["relname"] for r in await conn.fetch(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND c.relkind='r' ORDER BY 1")]
    cols = await conn.fetch("""
        SELECT c.relname AS t, a.attnum AS n, a.attname AS col, format_type(a.atttypid, a.atttypmod) AS typ,
               NOT a.attnotnull AS nullable, pg_get_expr(d.adbin, d.adrelid) AS dflt
        FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace ns ON ns.oid=c.relnamespace
        LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
        WHERE ns.nspname='public' AND c.relkind='r' AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname, a.attnum""")
    cons = await conn.fetch("""
        SELECT conrelid::regclass::text AS t, conname, contype::text AS contype, pg_get_constraintdef(oid) AS def,
               confrelid::regclass::text AS ft,
               ARRAY(SELECT attname FROM pg_attribute WHERE attrelid=conrelid AND attnum=ANY(conkey)) AS cols,
               ARRAY(SELECT attname FROM pg_attribute WHERE attrelid=confrelid AND attnum=ANY(confkey)) AS fcols,
               confdeltype::text AS confdeltype  -- "char" 타입은 asyncpg가 bytes로 줘 비교가 전부 실패함
        FROM pg_constraint WHERE connamespace='public'::regnamespace AND contype IN ('p','f','u','c')""")
    idx = await conn.fetch("SELECT tablename AS t, indexname, indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY 1,2")
    trg = await conn.fetch("SELECT tgrelid::regclass::text AS t, tgname FROM pg_trigger WHERE NOT tgisinternal ORDER BY 1")
    counts, fill, sizes = {}, {}, {}
    by_table = defaultdict(list)
    for r in cols:
        by_table[r["t"]].append(r)
    for t in tables:
        counts[t] = await conn.fetchval(f'SELECT COUNT(*) FROM "{t}"')
        sizes[t] = await conn.fetchval("SELECT pg_size_pretty(pg_total_relation_size($1::regclass))", t)
        if counts[t]:
            exprs = ", ".join(f'COUNT("{r["col"]}")' for r in by_table[t])
            row = await conn.fetchrow(f'SELECT {exprs} FROM "{t}"')
            for i, r in enumerate(by_table[t]):
                fill[(t, r["col"])] = row[i] / counts[t]
    exts = await conn.fetch("SELECT extname, extversion FROM pg_extension ORDER BY 1")
    version = await conn.fetchval("SHOW server_version")
    return dict(tables=tables, by_table=by_table, cons=cons, idx=idx, trg=trg, counts=counts, fill=fill,
                sizes=sizes, exts=exts, version=version, refs=_code_refs(tables))


def build(d: dict, out: Path) -> None:
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    ondel = {"a": "NO ACTION", "r": "RESTRICT", "c": "CASCADE", "n": "SET NULL", "d": "SET DEFAULT"}
    pk, fk_out, fk_in, uq = defaultdict(set), defaultdict(list), defaultdict(list), defaultdict(set)
    fk_col = {}
    for c in d["cons"]:
        if c["contype"] == "p":
            pk[c["t"]].update(c["cols"])
        elif c["contype"] == "u":
            uq[c["t"]].update(c["cols"])
        elif c["contype"] == "f":
            fk_out[c["t"]].append(c)
            fk_in[c["ft"]].append(c)
            for col, fcol in zip(c["cols"], c["fcols"]):
                fk_col[(c["t"], col)] = f"{c['ft']}.{fcol} ({ondel.get(c['confdeltype'], '')})"

    wb = Workbook()

    # ── 개요 ──
    ws = wb.active
    ws.title = "개요"
    ws["A1"] = "Ops-Navigator(SMAgentLab) 테이블 명세서 — 공통화 분류 초안"
    ws["A1"].font = Font(bold=True, size=15)
    meta = [
        ("추출 기준", f"{now} · 실 DB(ops-postgres, opsdb) 카탈로그 직접 조회 — 문서가 아니라 DB가 기준"),
        ("DB", f"PostgreSQL {d['version']} · 확장: " + ", ".join(f"{e['extname']} {e['extversion']}" for e in d["exts"])),
        ("규모", f"테이블 {len(d['tables'])}개 · 컬럼 {sum(len(v) for v in d['by_table'].values())}개 · FK {sum(len(v) for v in fk_out.values())}개"),
        ("데이터", "값은 내보내지 않음 — 행 수·컬럼 채움률만 계산"),
        ("분류", "'분류 초안'과 '판단 근거'는 코드·마이그레이션 주석을 근거로 한 의견 — '팀 결정' 열에 확정값을 적어 주세요"),
        ("재추출", "backend/scripts/export_table_spec.py (컨테이너 안에서 실행하면 그 시점 기준으로 다시 생성)"),
    ]
    for i, (k, v) in enumerate(meta, 3):
        ws.cell(row=i, column=1, value=k).font = Font(bold=True)
        ws.cell(row=i, column=2, value=v).alignment = WRAP
    r0 = len(meta) + 5
    ws.cell(row=r0 - 1, column=1, value="분류 기준과 초안 집계").font = Font(bold=True, size=12)
    cls_count = defaultdict(list)
    for t in d["tables"]:
        cls_count[T.get(t, ("", "", "미분류", ""))[2]].append(t)
    rows = [(c, len(cls_count[c]), CLS_DESC[c], ", ".join(cls_count[c])) for c in CLS_ORDER if cls_count[c]]
    _write_table(ws, ["분류", "테이블 수", "의미", "테이블"], rows, [26, 10, 60, 70], "SummaryTbl", start_row=r0)
    for i, row in enumerate(rows, r0 + 1):
        ws.cell(row=i, column=1).fill = _fill(CLS_FILL[row[0]])
    r1 = r0 + len(rows) + 3
    ws.cell(row=r1 - 1, column=1, value="공통화 시 먼저 정해야 할 것 (테이블을 가로지르는 이슈)").font = Font(bold=True, size=12)
    _write_table(ws, ["이슈", "내용"], CROSS_CUTTING, [26, 140], "CrossTbl", start_row=r1)

    # 분류 초안대로 DB를 나누면 끊기는 FK — 공통 쪽 테이블이 서비스 전용/폐기 쪽을 참조하는 경우.
    # 공통 DB가 서비스 DB를 가리키면 분리 후 FK를 유지할 수 없어 설계를 바꿔야 한다(자동 계산).
    common = {CLS_PLATFORM, CLS_ASSET, CLS_EVAL}
    cls_of = {t: T.get(t, ("", "", "", ""))[2] for t in d["tables"]}
    broken = []
    for t in d["tables"]:
        for c in fk_out[t]:
            if cls_of[t] in common and cls_of.get(c["ft"]) not in common:
                broken.append((f"{t}.{', '.join(c['cols'])}", cls_of[t], c["ft"], cls_of.get(c["ft"], ""),
                               ondel.get(c["confdeltype"], ""),
                               "공통 쪽이 서비스 쪽을 참조 — FK 대신 느슨한 id 참조로 바꾸거나 두 테이블을 같은 쪽에 둬야 함"))
    r2 = r1 + len(CROSS_CUTTING) + 3
    ws.cell(row=r2 - 1, column=1, value=f"분류 초안대로 나누면 끊기는 FK — {len(broken)}건 (자동 계산)").font = Font(bold=True, size=12)
    if broken:
        _write_table(ws, ["참조하는 컬럼", "분류", "참조 대상", "대상 분류", "ON DELETE", "조치"], broken,
                     [34, 22, 24, 22, 12, 70], "BrokenFkTbl", start_row=r2)
    else:
        ws.cell(row=r2, column=1, value="없음")
    ws.freeze_panes = None
    ws.column_dimensions["B"].width = 60

    # ── 테이블 목록 ──
    ws = wb.create_sheet("테이블 목록")
    headers = ["No", "도메인", "테이블", "용도", "행 수", "크기", "컬럼 수", "참조(FK →)", "참조됨(← FK)",
               "사용 코드 모듈", "분류 초안", "판단 근거", "팀 결정", "메모"]
    order = sorted(d["tables"], key=lambda t: (CLS_ORDER.index(T[t][2]) if t in T else 99, T.get(t, ("",))[0], t))
    rows = []
    for i, t in enumerate(order, 1):
        dom, purpose, cls, why = T.get(t, ("?", "(주석 없음 — NOTES 추가 필요)", "미분류", ""))
        rows.append((
            i, dom, t, purpose, d["counts"][t], d["sizes"][t], len(d["by_table"][t]),
            "\n".join(sorted({c["ft"] for c in fk_out[t]})) or "-",
            "\n".join(sorted({c["t"] for c in fk_in[t]})) or "-",
            "\n".join(d["refs"].get(t, [])) or "(앱 코드 참조 없음)",
            cls, why, "", "",
        ))
    _write_table(ws, headers, rows, [5, 12, 28, 42, 8, 9, 8, 24, 24, 30, 22, 70, 16, 20], "TableList")
    for i, row in enumerate(rows, 2):
        ws.cell(row=i, column=11).fill = _fill(CLS_FILL.get(row[10], "FFFFFF"))
        ws.cell(row=i, column=13).fill = _fill("FFFBEB")

    # ── 컬럼 상세 ──
    ws = wb.create_sheet("컬럼 상세")
    headers = ["테이블", "#", "컬럼", "타입", "NULL", "기본값", "키", "FK 대상 (ON DELETE)", "채움률", "설명", "민감", "분류 초안(테이블)"]
    rows = []
    for t in order:
        for r in d["by_table"][t]:
            col = r["col"]
            keys = [k for k, cond in (("PK", col in pk[t]), ("FK", (t, col) in fk_col), ("UQ", col in uq[t])) if cond]
            fr = d["fill"].get((t, col))
            fill_txt = "-" if d["counts"][t] == 0 else f"{fr:.0%}"
            desc = C.get((t, col)) or GENERIC.get(col, "")
            if fr == 0 and d["counts"][t]:
                desc = (desc + " · " if desc else "") + "값 0건"
            rows.append((t, r["n"], col, r["typ"], "Y" if r["nullable"] else "N", r["dflt"] or "",
                         ", ".join(keys), fk_col.get((t, col), ""), fill_txt, desc,
                         SENSITIVE.get((t, col), ""), T.get(t, ("", "", "", ""))[2]))
    _write_table(ws, headers, rows, [26, 4, 26, 24, 6, 26, 8, 30, 8, 56, 16, 20], "ColumnDetail")
    for i, row in enumerate(rows, 2):
        if row[10]:
            ws.cell(row=i, column=11).fill = _fill("FEE2E2")
        if row[8] == "0%":
            ws.cell(row=i, column=9).fill = _fill("E5E7EB")

    # ── 관계 ──
    ws = wb.create_sheet("관계(FK)")
    rows = []
    for t in order:
        for c in fk_out[t]:
            rows.append((t, ", ".join(c["cols"]), c["ft"], ", ".join(c["fcols"]), ondel.get(c["confdeltype"], ""), c["conname"]))
    _write_table(ws, ["테이블", "컬럼", "참조 테이블", "참조 컬럼", "ON DELETE", "제약명"], rows, [28, 26, 28, 18, 12, 44], "FkList")
    for i, row in enumerate(rows, 2):
        if row[4] == "CASCADE":
            ws.cell(row=i, column=5).fill = _fill("FEF3C7")

    # ── 인덱스·트리거·제약 ──
    ws = wb.create_sheet("인덱스·트리거")
    rows = [(r["t"], "인덱스", r["indexname"], r["indexdef"]) for r in d["idx"]]
    rows += [(r["t"], "트리거", r["tgname"], "") for r in d["trg"]]
    rows += [(c["t"], "CHECK/UNIQUE", c["conname"], c["def"]) for c in d["cons"] if c["contype"] in ("c", "u")]
    rows.sort(key=lambda x: (x[0], x[1], x[2]))
    _write_table(ws, ["테이블", "종류", "이름", "정의"], rows, [28, 12, 44, 110], "IndexList")

    wb.save(out)


async def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/table_spec.xlsx")
    await init_pool()
    try:
        async with get_conn() as conn:
            d = await collect(conn)
    finally:
        await close_pool()
    missing = [t for t in d["tables"] if t not in T]
    if missing:
        print("주석 없는 테이블(NOTES 추가 필요):", missing)
    build(d, out)
    print(f"저장: {out} — 테이블 {len(d['tables'])}개")


if __name__ == "__main__":
    asyncio.run(main())
