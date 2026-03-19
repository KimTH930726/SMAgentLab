# Ops-Navigator → AI 통합 플랫폼 확장 전략

> **최종 수정**: 2026-03-19

---

## 현재 상태

```
[완료]  AgentRegistry + 3개 에이전트 (KnowledgeRag / Text2SQL / McpTool)
        AgentSelect UI + 에이전트별 어드민 탭 스코핑
        MCP 도구 agent_type 분리
        SQL Few-shot 피드백 연동 (pending→승인 워크플로우)
[완료]  domain/ → service/ + agents/{agent}/ 디렉터리 리팩터링 (2026-03-19)
        (platform/ 명칭 → Python stdlib 충돌로 service/ 로 확정)
[완료]  DB prefix rename: ops_knowledge → rag_knowledge, ops_glossary → rag_glossary,
        ops_fewshot → rag_fewshot, ops_knowledge_category → rag_knowledge_category,
        ops_conv_summary → rag_conv_summary (자동 마이그레이션, 2026-03-19)
```

---

## 핵심 아키텍처: 에이전트 레지스트리 패턴

```
사용자 질문
  └→ chat/router.py (플랫폼: 세션·인증)
       └→ AgentRegistry.get(agent_type)
            └→ agent.stream_chat(query, user, context)
                  ├─ knowledge_rag: 용어매핑 → 하이브리드 검색 → Few-shot → LLM
                  ├─ text2sql:      parse → rag → generate → validate → fix → execute → summarize
                  └─ mcp_tool:      도구 선택 → 파라미터 수집 → HTTP 호출 → 답변
```

- 플랫폼 = 에이전트 실행 런타임 (인증·세션·피드백·통계)
- 에이전트 = 독립 플러그인 (`agents/` 디렉토리에 각자 파이프라인)
- 에이전트 추가 = `agents/{name}/agent.py` + `main.py`에 `register()` 1줄

---

## 현재 코드 구조

```
backend/
├── agents/
│   ├── base.py                            # AgentBase + AgentRegistry
│   ├── knowledge_rag/
│   │   ├── agent.py                       # KnowledgeRagAgent
│   │   ├── knowledge/                     # 지식베이스 CRUD + 검색 (was domain/knowledge)
│   │   └── fewshot/                       # Few-shot CRUD (was domain/fewshot)
│   ├── text2sql/
│   │   ├── agent.py                       # Text2SqlAgent (7단계 파이프라인)
│   │   ├── admin/                         # Text2SQL 어드민 API (was domain/text2sql)
│   │   └── pipeline/                      # 7단계 파이프라인 모듈
│   ├── mcp_tool/agent.py                  # McpToolAgent
│   └── http_tool/
│       ├── agent.py                       # HttpToolAgent (레거시)
│       └── admin/                         # HTTP 도구 CRUD (was domain/http_tool)
├── service/                               # 플랫폼 공통 레이어 (was platform/)
│   ├── auth/                              # 인증·사용자 관리
│   ├── admin/                             # 네임스페이스·통계·LLM 설정
│   ├── chat/                              # 채팅 라우터·헬퍼·메모리
│   ├── feedback/                          # 피드백 API
│   ├── llm/                               # LLM 팩토리·프로바이더
│   ├── mcp_tool/                          # MCP 도구 API
│   └── prompt/                            # 프롬프트 관리
├── core/                                  # DB, JWT, security, config
└── shared/                                # embedding, cache
```

---

## DB 전략

- **테이블 분리**: 플랫폼 공유 `ops_*` + 에이전트별 `sql_*` (text2sql)
- **agent_type 컬럼**: `ops_conversation`, `ops_query_log`, `ops_feedback`, `ops_mcp_tool` — 에이전트별 필터링
- **pgvector**: HNSW `m=16, ef_construction=64` (768차원)

---

## 인프라 단계

| 단계 | 트리거 | 내용 |
|------|--------|------|
| Phase 1 | 현재 | 단일 컨테이너, 코드 모듈화 |
| Phase 1.5 | 에이전트 3개+ | Celery + Redis 비동기 워커 분리 |
| Phase 2 | 운영 규모 | 서비스 분리: platform-api + agent-rag + agent-sql |

---

## 플랫폼 어드민 확장 (에이전트 거버넌스)

| 기능 | 상태 | 재검토 트리거 |
|------|------|---------------|
| 에이전트 디렉토리 UI | 미착수 | 에이전트 3개+ |
| 파트-에이전트 접근 제어 | 미착수 | 에이전트 3개+ |
| 통합 대시보드 agent_type 필터 | 미착수 | 에이전트 3개+ |
| 토큰 쿼터/비용 제어 | 보류 | 외부 유료 LLM 전환 시 |
| 크로스 에이전트 트레이싱 | 보류 | Phase 2 이후 |

**설계 원칙**: Self-registration — `AgentRegistry.register()` 호출 시 어드민 UI 자동 노출.
