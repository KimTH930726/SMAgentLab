# 문서 지도 (2026-10-29 정리)

코드의 도메인 경계(지식 · 정책서 · 품질 · 조직)와 같은 폴더로 나눴다. 새 문서는 **그 일이 속한 도메인 폴더**에 두고 여기 한 줄을 더한다.
끝난 계획·보고서는 `archive/`로 옮긴다(지우지 않음 — 이력). 상태: **현행** = 지금 기준 · **설계** = 개발 전·진행 중 · **참고** = 배경.

## 시스템 전체 (변경 시 동기화 — CLAUDE.md 배포 전 체크리스트)
| 문서 | 내용 |
|---|---|
| [architecture.md](architecture.md) | 시스템 구성 + 버전별 변경 이력(옛 이력은 `archive/architecture-history-*`) |
| [flow.md](flow.md) | 처리 흐름(채팅·적재·용어·정정) |
| [table-definition.md](table-definition.md) | 마이그레이션 이력(전체 칼럼 스펙은 실 DB) |
| [backlog.md](backlog.md) | 남은 일 목록(리뷰 낮음·보류 항목 포함) |

## projects — 프로젝트 산출물 (진행하며 갱신 · 갱신 이력은 문서 안)
| 문서 | 내용 |
|---|---|
| [knowledge-datafication/index.html](projects/knowledge-datafication/index.html) | 지식 데이터화 현황 — 정책서 · 컨플루언스(팀·파트·영역별 진척, 적재 레이어, 개선, PoC 전략, 계획) |

## domains/knowledge — 지식 코어 (`backend/agents/knowledge_rag`)
| 문서 | 상태 | 내용 |
|---|---|---|
| [collection-design.md](domains/knowledge/collection-design.md) | 현행 | 컬렉션(업무 영역 트리) · 넣을 곳 AI 판단 · AI 판단 원장 |
| [taxonomy-standard.md](domains/knowledge/taxonomy-standard.md) | 현행 | 지식 분류 표준 — 정책 / 시스템 컬렉션, 운영 지식은 시스템에만, 조직은 담당 연결만(11/02, #105·#106) |
| [collection-key-plan.md](domains/knowledge/collection-key-plan.md) | 계획 | 컬렉션 키 정리 — 이름 → id·업무 코드(POL-·SYS-) 전수검사·전환 단계(11/02) |
| [source-ledger-plan.md](domains/knowledge/source-ledger-plan.md) | 현행 | 원문 원장 — 원문 = 기준, 조각 = 파생물, 다시 분할 |
| [knowledge-lifecycle-design.md](domains/knowledge/knowledge-lifecycle-design.md) | 참고 | 지식 생명주기(중복·버전·삭제) 현황 분석·장기 설계 |
| [retrieval-routing-pattern.md](domains/knowledge/retrieval-routing-pattern.md) | 참고 | 검색 전략 라우팅 원칙 |
| [glossary/glossary-curation-design.md](domains/knowledge/glossary/glossary-curation-design.md) | 설계 | 용어집 업계식 재구성(제안 → 근거 → 승인) |
| [sources/confluence/confluence-chunking-spec.md](domains/knowledge/sources/confluence/confluence-chunking-spec.md) | 현행 | 컨플루언스 청킹 규격 |
| [sources/confluence/confluence-writing-guide.md](domains/knowledge/sources/confluence/confluence-writing-guide.md) | 현행 | 팀에 주는 컨플 작성 가이드(규칙 6) — SPM 지표 받으면 재실측 |
| [sources/confluence/confluence-sync-research.md](domains/knowledge/sources/confluence/confluence-sync-research.md) | 설계 | 현행화(증분 동기화) 조사 + MVP — MCP 게이트웨이 도구 목록 대기 |
| [sources/confluence/confluence-curation-layer-design.md](domains/knowledge/sources/confluence/confluence-curation-layer-design.md) | 설계 | 정제 레이어(수집기 보강 → 팀 진단 → AI 초안·검수) + 파일럿 진단 |

## domains/policy — 정책서 (`backend/service/policy`)
| 문서 | 상태 | 내용 |
|---|---|---|
| [policy-v0-design.md](domains/policy/policy-v0-design.md) | 현행 | 정책서 v0 적재·관리(원문 통째 + 파라미터 파생 + 참고표) |
| [policy-lifecycle-design.md](domains/policy/policy-lifecycle-design.md) | 현행 | 정책 추가·폐기·분류 옮기기 · 원문 확인 |

## domains/quality — 평가·신뢰도 (`backend/service/eval` · `backend/libs/trustkit`)
| 문서 | 상태 | 내용 |
|---|---|---|
| [eval-gate-redesign.md](domains/quality/eval-gate-redesign.md) | 현행 | 평가 게이트(판정 기록·기준선·화면) |
| [self-improvement-loop-design.md](domains/quality/self-improvement-loop-design.md) | 현행 | 자가 개선 루프(게이트 · AI 감사 로그 · 적용 이력) |
| [ops-query-monitoring-design.md](domains/quality/ops-query-monitoring-design.md) | 설계 | 운영 질의 감시(기록 → 진단 → 표본 채점 → 골든셋) — 1·5단계 구현 |
| [golden-set-lifecycle-plan.md](domains/quality/golden-set-lifecycle-plan.md) | 현행 | 골든셋 관리 사이클 |
| [golden-set-procedure.md](domains/quality/golden-set-procedure.md) | 현행 | 골든셋 재생성 절차(재정제 전후 같은 기준) |
| [trustkit-design.md](domains/quality/trustkit-design.md) | 현행 | 신뢰도 레이어(이식 가능한 팀 자산) |

## domains/org — 조직·권한
| 문서 | 상태 | 내용 |
|---|---|---|
| [org-space-design.md](archive/org-space-design.md) | 이력(폐지 11/02) | 조직 공간 · 등록 창구 · 영역 선택기 |
| [governance-design-review.md](domains/org/governance-design-review.md) | 참고 | 감사로그 · 접근권한 · 보존 정책 선제 검토 |
| [internal-sso-request.md](domains/org/internal-sso-request.md) | 참고 | 사내 IdP(SSO) 연동 확인 체크리스트 |

## platform — 도메인 공통 원칙
| 문서 | 상태 | 내용 |
|---|---|---|
| [data-storage-philosophy.md](platform/data-storage-philosophy.md) | 현행 | 데이터 저장 철학(표준화 · 테이블 설계 · 적재 검증) |
| [admin-ia-design.md](platform/admin-ia-design.md) | 현행 | 관리자 화면 구조(도메인 경계대로 메뉴) |

## operations — 배포·운영·규율
| 문서 | 내용 |
|---|---|
| [deployment-closed-network.md](operations/deployment-closed-network.md) | 폐쇄망 배포·업그레이드·롤백 |
| [development-conventions.md](operations/development-conventions.md) | 개발 규율(실사고 사례 포함) |
| [incidents/](operations/incidents/) | 장애 보고서 |

## archive — 끝난 것(이력)
옛 변경 이력, 끝난 계획(업무구분 폐지 · v1 정책 파이프라인), 일회성 보고서(10/19 정확도 구조). 안의 옛 링크는 그 당시 기록이라 고치지 않는다.
