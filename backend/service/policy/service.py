"""정책서 임포트 파이프라인 — 엑셀 업로드 → 파싱 → LLM 분해 → RDB 적재.

docs/policy-doc-pipeline-plan.md §3 파이프라인, §2-1 버전 관리를 구현한다.

버전 관리(§2-1): 같은 정책의 이전 버전과 content_hash를 비교한다. "같은 정책"은 2026-10-06부터
엑셀 위치가 아니라 정책 식별키로 찾는다(`_PolicyMatcher` — 행 삽입·파일명 변경에 흔들리지 않게). 같으면 재처리 없이 스킵(같은 파일 재업로드 시 불필요한 LLM 호출
방지). 다르면(내용 변경 또는 신규) — 절대 UPDATE하지 않고 새 policy_item row를 INSERT한다
(logical_id는 이전 row와 동일하게 유지, version+1, supersedes_id=이전 row). 이전 row는
status='deprecated'로 전환하되 삭제하지 않는다 — rag_knowledge 병합이 content를 그 자리에서
덮어써 이력이 소실되던 문제(knowledge-lifecycle-design.md 우선순위 1위)를 반복하지 않기 위함.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field

from core.database import get_conn, resolve_namespace_id
from shared.embedding import embedding_service
from service.policy import excel_parser, decompose
from agents.knowledge_rag.knowledge.service import create_glossary

logger = logging.getLogger(__name__)

# 한 시트(100~200 row 규모, docs/policy-doc-pipeline-plan.md §1)를 row마다 순차로 LLM
# 분해하면 row당 1~3초씩 걸려 파일 하나 임포트에 수 분이 걸린다 — LLM 호출은 I/O 바운드라
# 동시에 여러 개를 태워도 무방하다. 다만 무제한 동시 호출은 LLM 게이트웨이/DB 커넥션 풀에
# 부담이 되므로 상한을 둔다(db_pool_max_size=10 대비 여유 있게).
_DECOMPOSE_CONCURRENCY = 5


def _coerce_param_field(v, max_len: int | None = None) -> str | None:
    """LLM이 param 필드(특히 value)를 배열/딕셔너리로 반환하는 경우가 실측으로 확인됨(예:
    "판매상태가 판매대기/판매중/판매종료" 같은 열거형 팩트 — 2026-09-04). 프롬프트에 단일
    문자열 규칙을 추가했지만, LLM이 100% 지키리라는 보장은 없어 코드 쪽에도 안전망을 둔다.
    str()로 그냥 감싸면 "['판매대기', '판매중', '판매종료']" 같은 파이썬 repr이 그대로 DB에
    저장돼버리는 실데이터 오염이 생기므로, 리스트/튜플은 쉼표로 join해 깔끔한 문자열로 만든다."""
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        v = ", ".join(str(x) for x in v)
    else:
        v = str(v)
    return v[:max_len] if max_len else v


# 재처리 경로(2026-09-28, data-storage-philosophy.md §9-①) — 원본 content_hash만 보고 스킵하면 파서·
# 분해 프롬프트를 개선해도 같은 파일 재업로드 시 "변경 없음"으로 건너뛰어 개선이 기존 데이터에 반영될 길이
# 없었다. 행마다 만든 파이프라인 버전을 기록하고, 원본이 같아도 버전이 다르면 다시 분해한다.
# 분해 프롬프트(decompose.SYSTEM_PROMPT)는 해시로 자동 반영되고, 파서·청크 조립처럼 프롬프트 밖의
# 출력 로직(excel_parser, _chunk_texts, _write_policy_result)을 바꿀 땐 이 번호를 올린다.
PIPELINE_REVISION = 1


def pipeline_version() -> str:
    prompt_hash = hashlib.sha256(decompose.SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:10]
    return f"r{PIPELINE_REVISION}-{prompt_hash}"


def _content_hash(category_path: list[str], policy_name: str, raw_body: str, remark: str | None) -> str:
    payload = json.dumps(
        {"category_path": category_path, "policy_name": policy_name, "raw_body": raw_body, "remark": remark},
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class SheetSummary:
    sheet_name: str
    kind: str
    created_items: int = 0
    new_versions: int = 0
    unchanged_skipped: int = 0
    params_extracted: int = 0
    narratives_extracted: int = 0
    unresolved_segments: int = 0
    glossary_added: int = 0
    glossary_duplicate_skipped: int = 0
    fallback_chunks_added: int = 0
    pipeline_reprocessed: int = 0  # 원본은 같지만 파이프라인 버전이 달라(또는 강제) 다시 분해한 행
    moved: int = 0               # 내용은 같고 위치(파일·시트·행)만 바뀌어 그 자리에서 위치만 갱신한 행
    matched_by_body: int = 0     # 분류 경로·정책명이 바뀌었지만 본문이 같아 같은 정책으로 이은 행
    duplicate_keys: int = 0      # 엑셀 안에 같은 식별키(분류 경로+정책명)가 또 나와 신규로 넣은 행
    skip_reason: str | None = None


@dataclass
class ImportSummary:
    source_file: str
    sheets: list[SheetSummary] = field(default_factory=list)
    missing_marked: int = 0      # 이번 파일의 정책 시트에서 사라져 "원본에서 사라짐"으로 검토 큐에 올린 항목


def _norm(text: str | None) -> str:
    """식별키·본문 비교용 — 앞뒤·연속 공백만 정리(대소문자·문장부호는 그대로: 과매칭 방지)."""
    return " ".join((text or "").split())


def _policy_key(category_path, policy_name: str) -> tuple:
    return (tuple(_norm(p) for p in (category_path or [])), _norm(policy_name))


class _PolicyMatcher:
    """재임포트 때 엑셀 행 → 현행 policy_item(같은 파트)을 찾는다 (2026-10-06, 10/1 회의 액션 "정책 버전 관리").

    예전엔 (파일·시트·행번호) 위치로만 찾아서 행 하나만 끼워 넣어도 아래 행이 전부 다른 정책의 새 버전으로 잘못
    이어지고, 파일명이 바뀌면 전부 신규 + 옛 파일 정책이 활성으로 남아 중복됐다. 엑셀엔 고정 정책 ID 열이 없어(앞으로도
    없음 — 사용자 확인) 식별키 = (분류 경로, 정책명) — 실데이터 활성 378건 전부 유일(정책명만으론 33건 중복).
    1) 식별키 일치 → 2) 없으면 본문(raw_body+비고) 일치 1건(분류만 옮기거나 이름만 바꾼 경우, 2건 이상이면 안 이음)
    → 3) 신규. 유사도(임베딩) 매칭은 "이름·분류·본문이 다 조금씩 바뀐" 실사례가 생길 때까지 안 한다 — 그런 건
    신규 + "원본에서 사라짐" 한 쌍으로 검토 큐에 나와 사람이 판단한다.
    한 임포트 안에서 같은 현행 항목을 두 행이 가져가지 않게 이미 이은 항목은 다시 안 준다(먼저 나온 행 우선)."""

    _SELECT = """
        SELECT id, logical_id, version, content_hash, pipeline_version, status, category_path, policy_name,
               raw_body, remark, source_file, source_sheet, source_row, source_missing_at
        FROM policy_item WHERE namespace_id = $1 AND status <> 'deprecated'
        ORDER BY version DESC, id DESC
    """

    def __init__(self, rows):
        self.items = list(rows)
        self.by_key: dict[tuple, list] = {}
        self.by_body: dict[tuple, list] = {}
        for r in self.items:
            self.by_key.setdefault(_policy_key(r["category_path"], r["policy_name"]), []).append(r)
            self.by_body.setdefault((_norm(r["raw_body"]), _norm(r["remark"])), []).append(r)
        self.taken: set[int] = set()
        self.seen_keys: set[tuple] = set()

    @classmethod
    async def load(cls, conn, ns_id: int) -> "_PolicyMatcher":
        return cls(await conn.fetch(cls._SELECT, ns_id))

    def assign(self, rows: list[tuple]) -> dict:
        """rows: [(ref, ParsedPolicyRow)] (파일 전체, 파일 순서) → {ref: (현행 항목 또는 None, 방법, 엑셀 안 중복 키 여부)}.

        두 단계(/code-review 지적) — ① 모든 행의 식별키 매칭을 먼저 끝내고 ② 남은 행만 본문 매칭. 행 순서대로 섞어 하면
        이름이 바뀐 앞 행이 본문으로 뒤 행의 정확한 키 항목을 먼저 가져가 이력이 엉뚱한 정책으로 옮겨 갈 수 있다.
        엑셀 안에 같은 키가 또 나오면 그 키의 아직 안 가져간 현행 항목부터 준다 — 안 그러면 같은 파일을 다시 올릴 때마다
        둘째 행이 신규로 쌓이고 기존 둘째 항목은 "사라짐"으로 표시되는 일이 반복된다."""
        out: dict = {}
        seen: set[tuple] = set()
        rest: list[tuple] = []
        for ref, row in rows:
            key = _policy_key(row.category_path, row.policy_name)
            dup = key in seen
            seen.add(key)
            hit = next((r for r in self.by_key.get(key, []) if r["id"] not in self.taken), None)
            if hit is not None:
                self.taken.add(hit["id"])
                out[ref] = (hit, "key", dup)
            elif dup:
                out[ref] = (None, "new", True)   # 같은 키가 또 나왔는데 남은 현행 항목이 없음 — 신규
            else:
                rest.append((ref, row))
        for ref, row in rest:
            free = [r for r in self.by_body.get((_norm(row.raw_body), _norm(row.remark)), []) if r["id"] not in self.taken]
            if len(free) == 1:
                self.taken.add(free[0]["id"])
                out[ref] = (free[0], "body", False)
            else:
                out[ref] = (None, "new", False)
        return out

    def missing(self, sheet_names: set[str], filename: str) -> list[int]:
        """이번 파일이 다룬 범위에 있던 현행 항목 중 아무 행과도 안 이어진 것.

        범위 = 이번 파일의 정책 시트 이름 × "이번 임포트가 건드린 파일"(이번 파일명 + 이어진 항목들의 이전 파일명 — 파일명이
        바뀐 경우까지). 같은 파트에 같은 시트 이름을 쓰는 다른 엑셀이 있어도 그 항목은 건드리지 않는다(/code-review 지적).
        파일에 없는 시트의 항목도 건드리지 않는다(시트 일부만 올린 실수로 대량 표시되는 사고 방지). 반려 항목은 제외."""
        files = {filename} | {r["source_file"] for r in self.items if r["id"] in self.taken}
        return [r["id"] for r in self.items
                if r["source_sheet"] in sheet_names and r["source_file"] in files
                and r["id"] not in self.taken and r["status"] != "rejected"]


async def _find_current_version(conn, ns_id: int, source_file: str, sheet_name: str, source_row: int):
    """같은 원본 위치(namespace/파일/시트/행)의 가장 최근 버전(deprecated 제외) row.
    없으면 None — 신규로 처리."""
    return await conn.fetchrow(
        """
        SELECT id, logical_id, version, content_hash, pipeline_version FROM policy_item
        WHERE namespace_id = $1 AND source_file = $2 AND source_sheet = $3 AND source_row = $4
          AND status != 'deprecated'
        ORDER BY version DESC LIMIT 1
        """,
        ns_id, source_file, sheet_name, source_row,
    )


async def _check_version(
    conn, ns_id: int, source_file: str, sheet_name: str, row: excel_parser.ParsedPolicyRow,
    *, force: bool = False, matcher: "_PolicyMatcher | None" = None,
):
    """버전 체크(§2-1) — DB만 건드리는 저렴한 단계. LLM 호출과 분리해둬야 여러 row를
    동시(concurrent)에 처리할 때 "내용 안 바뀐 row"는 LLM 비용을 아예 안 태울 수 있다.

    스킵 조건 = 원본 content_hash 동일 **그리고** 파이프라인 버전 동일(그리고 force 아님).
    파이프라인 버전이 NULL(2026-09-28 이전 행 — 어느 프롬프트로 만들었는지 모름)이면 재처리.

    Returns: (skip: bool, current: Optional[Record], new_hash: str)
    """
    new_hash = _content_hash(row.category_path, row.policy_name, row.raw_body, row.remark)
    # 파일 임포트는 식별키 매칭(matcher), 단건 경로(`_ingest_policy_row`)는 예전처럼 위치로 찾는다
    current = (matcher.match(row)[0] if matcher is not None
               else await _find_current_version(conn, ns_id, source_file, sheet_name, row.source_row))
    skip = (
        not force
        and current is not None
        and current["content_hash"] == new_hash
        and current.get("pipeline_version") == pipeline_version()
    )
    # 근거 정정(2026-10-01)으로 담당자가 승인한 버전은 엑셀 원본이 그대로면 재처리하지 않는다 —
    # 파이프라인 버전이 바뀌거나 강제 재처리여도 다시 분해하면 엑셀의 옛(틀린) 본문으로 새 버전이 생겨
    # 정정이 조용히 사라진다. 엑셀 원본이 실제로 바뀐 경우(content_hash 다름)만 원본 소유자가 우선.
    if not skip and current is not None and current["content_hash"] == new_hash:
        corrected = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM ops_improvement_item WHERE applied_target_id = $1 "
            "AND target_type IN ('policy_param', 'policy_narrative') AND status = 'approved')",
            current["id"],
        )
        if corrected is True:
            logger.info("정책 재처리 스킵 — 승인된 정정 버전 유지 (policy_item=%s)", current["id"])
            skip = True
    return skip, current, new_hash


def _is_pipeline_reprocess(current, new_hash: str) -> bool:
    """원본은 그대로인데 다시 분해하는 경우(파이프라인 변경·강제) — 요약 집계용."""
    return current is not None and current["content_hash"] == new_hash


def _chunk_texts(row: excel_parser.ParsedPolicyRow, segments: list[decompose.Segment]) -> tuple[list[str], bool]:
    """policy_chunk로 들어갈 텍스트 목록과 폴백 여부. 임베딩을 트랜잭션 밖에서 미리 계산하려고
    (import_excel) 적재 로직에서 분리 — 적재 시에도 같은 함수를 써서 순서가 어긋날 수 없다.

    벡터 폴백(2026-09-04, Track 2 실측) — narrative segment가 하나도 안 남은 item(param만
    있거나 전부 unresolved인 경우, 실측 378건 중 136건=36%가 여기 해당)은 policy_chunk가
    아예 없어서 벡터 검색으로 못 찾는다. 자연어 질문이 짧은 param 필드(name/condition)와
    어휘가 안 겹치면 to_tsquery 정확매칭도 실패해 이런 item은 아예 검색 불가능해진다 —
    Track 2 A/B 비교에서 param 유형만 하이브리드(B)가 지식-only(A)에 오히려 크게 진 원인.
    원문 전체(정책명+본문)를 벡터 색인해두면 어휘가 안 겹쳐도 의미 유사도로는 찾을 수 있다.
    """
    texts = [s.text for s in segments if s.type == "narrative" and s.text.strip()]
    if texts:
        return texts, False
    return [f"{row.policy_name} ({' / '.join(row.category_path)}): {row.raw_body}"], True


async def _write_policy_result(
    conn, ns_id: int, system_key: str, source_file: str, sheet_name: str,
    row: excel_parser.ParsedPolicyRow, current, new_hash: str,
    segments: list[decompose.Segment], summary: SheetSummary,
    chunk_embeddings: list[list[float]] | None = None,
) -> None:
    """LLM 분해 결과(segments)를 실제 policy_item/param/chunk로 적재. DB 쓰기만 하는
    단계라 여러 row를 처리할 때도 이 부분은 커넥션 하나로 순차 실행해야 안전하다
    (asyncpg 커넥션은 동시 쿼리를 지원하지 않음) — LLM 분해(느림, 동시 처리 가능)와
    분리해둔 이유.

    chunk_embeddings: `_chunk_texts()` 순서대로 미리 계산한 임베딩. 없으면 여기서 계산한다
    (단건 경로 `_ingest_policy_row`)."""
    logical_id = current["logical_id"] if current is not None else None
    version = (current["version"] + 1) if current is not None else 1
    supersedes_id = current["id"] if current is not None else None

    has_unresolved = any(s.type == "unresolved" for s in segments)
    has_resolved = any(s.type in ("narrative", "param") for s in segments)
    parse_status = "unresolved" if not has_resolved else ("partial" if has_unresolved else "parsed")
    unresolved_payload = [
        {"text": s.text, "reason": s.reason} for s in segments if s.type == "unresolved"
    ] or None

    item_row = await conn.fetchrow(
        """
        INSERT INTO policy_item (
            namespace_id, system_key, category_path, policy_name, raw_body, remark,
            source_file, source_sheet, source_row, content_hash, status,
            logical_id, version, supersedes_id, parse_status, unresolved_segments, pipeline_version
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'pending_review',$11,$12,$13,$14,$15::jsonb,$16)
        RETURNING id, logical_id
        """,
        ns_id, system_key, row.category_path, row.policy_name, row.raw_body, row.remark,
        source_file, sheet_name, row.source_row, new_hash,
        logical_id, version, supersedes_id, parse_status,
        json.dumps(unresolved_payload, ensure_ascii=False) if unresolved_payload else None,
        pipeline_version(),
    )
    item_id = item_row["id"]

    if supersedes_id is not None:
        await conn.execute("UPDATE policy_item SET status = 'deprecated' WHERE id = $1", supersedes_id)
        if _is_pipeline_reprocess(current, new_hash):
            summary.pipeline_reprocessed += 1
        else:
            summary.new_versions += 1
    else:
        summary.created_items += 1

    for seg in segments:
        if seg.type == "param" and seg.extracted:
            await conn.execute(
                """
                INSERT INTO policy_param (policy_item_id, name, condition, value, unit)
                VALUES ($1, $2, $3, $4, $5)
                """,
                item_id,
                _coerce_param_field(seg.extracted.get("name"), max_len=500) or "",
                _coerce_param_field(seg.extracted.get("condition")),
                _coerce_param_field(seg.extracted.get("value")),
                _coerce_param_field(seg.extracted.get("unit")),
            )
            summary.params_extracted += 1
        elif seg.type == "unresolved":
            summary.unresolved_segments += 1

    texts, is_fallback = _chunk_texts(row, segments)
    if chunk_embeddings is None:
        chunk_embeddings = [await embedding_service.embed(t) for t in texts]
    for text, embedding in zip(texts, chunk_embeddings, strict=True):
        await conn.execute(
            "INSERT INTO policy_chunk (policy_item_id, chunk_text, embedding, chunk_idx) VALUES ($1, $2, $3::vector, $4)",
            item_id, text, str(embedding), summary.narratives_extracted,
        )
        summary.narratives_extracted += 1
    if is_fallback:
        summary.fallback_chunks_added += 1


async def _ingest_policy_row(
    conn, ns_id: int, system_key: str, source_file: str, sheet: excel_parser.ParsedSheet,
    row: excel_parser.ParsedPolicyRow, summary: SheetSummary,
) -> None:
    """단일 row를 버전체크→LLM 분해→적재까지 순차로 처리. `import_excel()`의 정책 시트
    처리는 여러 row를 동시 처리하기 위해 이 세 단계를 직접 조합해 쓰지만(성능, 아래
    `import_excel` 참고), 이 함수는 "한 row"를 독립적으로 다뤄야 하는 경우(단건 재처리 등)를
    위해 그대로 남겨둔다 — `_check_version`/`_write_policy_result`의 얇은 조합일 뿐, 로직
    중복 아님."""
    skip, current, new_hash = await _check_version(conn, ns_id, source_file, sheet.sheet_name, row)
    if skip:
        summary.unchanged_skipped += 1
        return
    segments = await decompose.decompose_policy_body(row.policy_name, row.raw_body)
    await _write_policy_result(conn, ns_id, system_key, source_file, sheet.sheet_name, row, current, new_hash, segments, summary)


async def _ingest_glossary_row(
    namespace: str, row: excel_parser.ParsedGlossaryRow, ns_id: int, conn, summary: SheetSummary,
) -> None:
    # §2-2 예외(일부 용어집 항목이 정의문 아닌 파라미터 팩트에 가까움, 예: "결제완료=상태코드11")는
    # v1에서 별도 분류 없이 전부 rag_glossary로 보낸다 — policy_param은 policy_item FK가 필수라
    # 용어집 단독으로는 넣을 자리가 없고, 이 소수 사례를 위해 v1 스코프를 늘리지 않는다(과설계 방지,
    # 필요하면 검토 UI 도입 시 재분류). rag_glossary는 remark 컬럼이 없어(스키마 변경 없이 가려고)
    # 비고는 description에 이어붙여 보존한다 — 실측(2026-09-04, 딜리버스 파일)으로 "상태코드: 11"
    # 같은 비고가 그냥 버려지는 실데이터 손실이 확인돼 추가.
    description = row.description
    if row.remark:
        description = f"{description} (비고: {row.remark})"
    try:
        await create_glossary(namespace, row.term, description)
        summary.glossary_added += 1
    except ValueError:
        summary.glossary_duplicate_skipped += 1


async def import_excel(
    namespace: str, system_key: str, filename: str, file_bytes: bytes, *, force_reprocess: bool = False,
) -> ImportSummary:
    """force_reprocess=True면 원본·파이프라인 버전이 같아도 모든 정책 행을 다시 분해한다(재정제 후
    같은 파일로 전체를 다시 돌리고 싶을 때). 결과는 평소처럼 새 버전 INSERT + 이전 버전 deprecated."""
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
    if ns_id is None:
        raise ValueError(f"네임스페이스를 찾을 수 없습니다: {namespace}")

    sheets = excel_parser.parse_workbook(file_bytes)
    result = ImportSummary(source_file=filename)
    async with get_conn() as conn:
        matcher = await _PolicyMatcher.load(conn, ns_id)
    imported_policy_sheets: set[str] = set()
    # 파일 전체를 한 번에 매칭(시트를 넘나드는 이동·키 우선 순서를 위해) — 시트 처리 루프는 결과만 꺼내 쓴다
    assignment = matcher.assign([((sh.sheet_name, i), row) for sh in sheets if sh.kind == "policy"
                                 for i, row in enumerate(sh.policy_rows)])

    for sheet in sheets:
        summary = SheetSummary(sheet_name=sheet.sheet_name, kind=sheet.kind, skip_reason=sheet.skip_reason)
        if sheet.kind == "glossary":
            async with get_conn() as conn:
                for row in sheet.glossary_rows:
                    await _ingest_glossary_row(namespace, row, ns_id, conn, summary)
        elif sheet.kind == "policy":
            # 3단계로 나눠 처리(성능): ① 버전 체크는 DB 조회만이라 저렴 — 순차로 먼저 돌려
            # "내용 안 바뀐 row"를 걸러낸다(스킵되는 row는 LLM을 아예 안 태움). ② 남은 row의
            # LLM 분해만 동시(concurrent)로 실행 — 여기가 진짜 병목이라 가장 이득이 큼.
            # ③ DB 쓰기는 커넥션 하나로 다시 순차 실행(asyncpg 커넥션은 동시 쿼리를 지원하지
            # 않음 — 그래서 쓰기 단계는 병렬화 대상에서 뺐다).
            to_process: list[tuple] = []
            relocate: list[tuple] = []   # 내용 그대로 — 위치만 갱신(+ "사라짐" 표시가 있었으면 해제)
            if sheet.policy_rows:
                imported_policy_sheets.add(sheet.sheet_name)
            async with get_conn() as conn:
                for i, row in enumerate(sheet.policy_rows):
                    current, how, dup = assignment[(sheet.sheet_name, i)]
                    summary.matched_by_body += how == "body"
                    summary.duplicate_keys += dup
                    skip, current, new_hash = await _check_version(
                        conn, ns_id, filename, sheet.sheet_name, row, force=force_reprocess,
                        matcher=_Fixed(current),
                    )
                    if skip:
                        summary.unchanged_skipped += 1
                        if current is not None and (
                            (current["source_file"], current["source_sheet"], current["source_row"])
                            != (filename, sheet.sheet_name, row.source_row) or current["source_missing_at"] is not None
                        ):
                            relocate.append((current["id"], row.source_row))
                    else:
                        to_process.append((row, current, new_hash))

            semaphore = asyncio.Semaphore(_DECOMPOSE_CONCURRENCY)

            async def _bounded_decompose(row: excel_parser.ParsedPolicyRow):
                async with semaphore:
                    return await decompose.decompose_policy_body(row.policy_name, row.raw_body)

            segments_list = await asyncio.gather(*[_bounded_decompose(item[0]) for item in to_process])

            # ④ 시트 단위 원자적 적재(2026-09-28, WBS 1-2와 같은 문제) — 예전엔 행마다 autocommit이라
            # 적재 도중 반쯤 들어간 시트가 챗 검색(status NOT IN deprecated/rejected — pending_review도
            # 노출)에 바로 잡혔고, 중간에 임베딩이 실패하면 "새 버전은 청크 없이 들어가고 옛 버전은
            # deprecated"인 행이 남아 그 정책이 검색에서 사라졌다. 임베딩은 트랜잭션 밖에서 미리
            # 한 번에 계산해 트랜잭션은 DB 쓰기만 담도록 짧게 유지한다.
            chunk_plan = [_chunk_texts(row, segs)[0] for (row, _, _), segs in zip(to_process, segments_list)]
            flat_texts = [t for texts in chunk_plan for t in texts]
            flat_embeddings = await embedding_service.embed_batch(flat_texts) if flat_texts else []
            per_row_embeddings, offset = [], 0
            for texts in chunk_plan:
                per_row_embeddings.append(flat_embeddings[offset:offset + len(texts)])
                offset += len(texts)

            async with get_conn() as conn:
                async with conn.transaction():
                    for item_id, source_row in relocate:
                        # "사라짐"으로 큐에 올라갔던 항목이 그대로 돌아오면: 전에 승인(사람·자동)된 적 있으면 active로 복귀
                        # (검토 이력 reviewed_at 기준 — 원래 검토 대기였던 항목은 그대로). SET의 CASE는 갱신 전 값으로 계산된다.
                        await conn.execute(
                            "UPDATE policy_item SET source_file = $2, source_sheet = $3, source_row = $4, "
                            "status = CASE WHEN source_missing_at IS NOT NULL AND status = 'pending_review' "
                            "AND reviewed_at IS NOT NULL THEN 'active' ELSE status END, "
                            "source_missing_at = NULL WHERE id = $1",
                            item_id, filename, sheet.sheet_name, source_row,
                        )
                    summary.moved += len(relocate)
                    for (row, current, new_hash), segments, embeddings in zip(to_process, segments_list, per_row_embeddings):
                        await _write_policy_result(
                            conn, ns_id, system_key, filename, sheet.sheet_name,
                            row, current, new_hash, segments, summary, chunk_embeddings=embeddings,
                        )
            # 시트 커밋 직후 바로 비운다 — 파일 끝에서 한 번만 하면 뒤 시트가 실패했을 때 이미
            # 커밋된 앞 시트가 반영 안 된 캐시 답이 그대로 남는다
            if to_process:
                await _invalidate_semantic_cache(namespace)
        result.sheets.append(summary)

    # 원본에서 사라진 정책 → 자동 폐기하지 않고 검토 큐로(사용자 결정 2026-10-06). active였으면 pending_review로 되돌려
    # 큐에 올린다 — 검색은 pending_review도 포함이라 담당자가 결정하기 전까진 답변이 그대로다(위험도 "높음" 사유로 표시,
    # 반려 = 폐기 / 승인 = 유지). 모든 시트가 성공한 뒤에만(중간에 실패하면 여기까지 안 옴).
    missing_ids = matcher.missing(imported_policy_sheets, filename)
    if missing_ids:
        async with get_conn() as conn:
            res = await conn.execute(
                "UPDATE policy_item SET source_missing_at = COALESCE(source_missing_at, NOW()), "
                "status = CASE WHEN status = 'active' THEN 'pending_review' ELSE status END "
                "WHERE id = ANY($1::int[]) AND status NOT IN ('deprecated', 'rejected')",
                missing_ids,
            )
        result.missing_marked = int(str(res).split()[-1]) if res else 0
        await _invalidate_semantic_cache(namespace)

    return result


class _Fixed:
    """이미 고른 매칭 결과를 _check_version에 그대로 넘기는 얇은 어댑터(match를 두 번 돌리지 않게)."""

    def __init__(self, current):
        self._current = current

    def match(self, row):
        return self._current, "fixed"


async def _invalidate_semantic_cache(namespace: str) -> None:
    """시맨틱 캐시엔 임포트 전 계산된 답이 TTL 동안 남아 새 정책이 반영 안 된 답이 계속 서빙될
    수 있다 — 적재가 커밋된 뒤 네임스페이스 캐시를 비운다(best-effort)."""
    try:
        from shared.cache import invalidate_namespace
        await invalidate_namespace(namespace)
    except Exception:
        logger.warning("정책 임포트 후 시맨틱 캐시 무효화 실패 (namespace=%s)", namespace, exc_info=True)
