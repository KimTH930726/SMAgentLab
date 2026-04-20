"""Text2SQL 에이전트 — AgentBase 구현."""
import asyncio
import logging
import time
from typing import AsyncIterator, Optional

from agents.base import AgentBase
from core.database import get_conn
from service.chat.helpers import update_assistant_message
from service.llm.factory import get_llm_provider
from agents.text2sql.admin import service
from agents.text2sql.pipeline import parse, rag, generate, validate, fix, execute, summarize
from agents.text2sql.pipeline.safety import BlockedQueryError

logger = logging.getLogger(__name__)


class Text2SqlAgent(AgentBase):

    @property
    def agent_id(self) -> str:
        return "text2sql"

    @property
    def metadata(self) -> dict:
        return {
            "display_name": "Text-to-SQL",
            "description": "자연어 질문을 SQL로 변환하여 DB 데이터를 조회합니다.",
            "icon": "Database",
            "color": "emerald",
            "output_type": "table",
            "welcome_message": "DB에 대해 자연어로 질문해 보세요.",
            "supports_debug": False,
        }

    async def health_check(self) -> bool:
        try:
            stages = await service.get_pipeline_stages()
            return len(stages) > 0
        except Exception:
            return False

    async def stream_chat(
        self,
        query: str,
        user: dict,
        conversation_id: int,
        context: dict,
    ) -> AsyncIterator[dict]:
        namespace: str = context["namespace"]
        msg_id: int = context["msg_id"]

        # namespace_id 조회
        async with get_conn() as conn:
            namespace_id = await conn.fetchval(
                "SELECT id FROM ops_namespace WHERE name = $1", namespace
            )
        if not namespace_id:
            yield {"type": "token", "data": "네임스페이스를 찾을 수 없습니다."}
            yield {"type": "done", "message_id": msg_id, "status": "failed"}
            return

        llm = get_llm_provider()
        _stages_raw, relations, cfg_target = await asyncio.gather(
            service.get_pipeline_stages(),
            service.get_relations(namespace_id),
            service.get_target_db_config(namespace_id),
        )
        stages_cfg = {s["id"]: s for s in _stages_raw}
        db_type = cfg_target["db_type"] if cfg_target else "postgresql"

        api_key: Optional[str] = context.get("api_key")
        pipeline_ctx: dict = {
            "question": query,
            "history": context.get("history", ""),
            "api_key": api_key,
            "_target_db_cfg": cfg_target,
        }

        start_total = time.time()
        stages_trace = []
        sql_result: Optional[str] = None
        table_result: Optional[dict] = None
        status = "success"
        error_msg: Optional[str] = None
        _cache_hit = False

        try:
            # ── 캐시 확인 ──────────────────────────────────────────────────
            cached_sql = await service.get_cached_sql(namespace_id, query)
            if cached_sql:
                _cache_hit = True
                pipeline_ctx["sql"] = cached_sql
                yield {"type": "status", "step": "cache", "message": "캐시된 SQL 사용 중..."}
                yield {"type": "sql", "sql": cached_sql, "reasoning": "", "cached": True}
                sql_result = cached_sql
            else:
                # ── Stage 1: Parse ───────────────────────────────────────
                s_cfg = stages_cfg.get("parse", {})
                if s_cfg.get("is_enabled", True):
                    yield {"type": "status", "step": "parse", "message": "질문 분석 중..."}
                    t0 = time.time()
                    result = await parse.run(pipeline_ctx, llm, s_cfg)
                    pipeline_ctx.update(result)
                    stages_trace.append({"step": "parse", "ms": int((time.time() - t0) * 1000)})

                # ── Stage 2: RAG ─────────────────────────────────────────
                s_cfg = stages_cfg.get("rag", {})
                if s_cfg.get("is_enabled", True):
                    yield {"type": "status", "step": "rag", "message": "관련 스키마 검색 중..."}
                    t0 = time.time()
                    result = await rag.run(pipeline_ctx, namespace_id, s_cfg)
                    pipeline_ctx.update(result)
                    stages_trace.append({"step": "rag", "ms": int((time.time() - t0) * 1000)})

                # ── Stage 3: Generate ────────────────────────────────────
                s_cfg = stages_cfg.get("generate", {})
                if s_cfg.get("is_enabled", True):
                    yield {"type": "status", "step": "generate", "message": "SQL 생성 중..."}
                    t0 = time.time()
                    result = await generate.run(pipeline_ctx, llm, relations, db_type, s_cfg)
                    pipeline_ctx.update(result)
                    stages_trace.append({"step": "generate", "ms": int((time.time() - t0) * 1000)})
                    if pipeline_ctx.get("sql"):
                        yield {"type": "sql", "sql": pipeline_ctx["sql"],
                               "reasoning": pipeline_ctx.get("reasoning", ""), "cached": False}
                        sql_result = pipeline_ctx["sql"]

                # ── Stage 4: Validate ────────────────────────────────────
                s_cfg = stages_cfg.get("validate", {})
                if s_cfg.get("is_enabled", True) and pipeline_ctx.get("sql"):
                    yield {"type": "status", "step": "validate", "message": "SQL 검증 중..."}
                    t0 = time.time()
                    val_result = await validate.run(pipeline_ctx, db_type, s_cfg)
                    stages_trace.append({"step": "validate", "ms": int((time.time() - t0) * 1000)})
                    if not val_result["valid"]:
                        pipeline_ctx["validation_errors"] = val_result["errors"]

                        # ── Stage 5: Fix ─────────────────────────────────
                        fix_cfg = stages_cfg.get("fix", {})
                        if fix_cfg.get("is_enabled", True):
                            yield {"type": "status", "step": "fix", "message": "SQL 자동 수정 중..."}
                            t0 = time.time()
                            fix_result = await fix.run(pipeline_ctx, llm, fix_cfg)
                            pipeline_ctx.update(fix_result)
                            stages_trace.append({"step": "fix", "ms": int((time.time() - t0) * 1000)})
                            if fix_result.get("fixed"):
                                yield {"type": "sql", "sql": pipeline_ctx["sql"],
                                       "reasoning": "", "cached": False}
                                sql_result = pipeline_ctx["sql"]

            # ── Stage 6: Execute ─────────────────────────────────────────
            if pipeline_ctx.get("sql"):
                exec_cfg = stages_cfg.get("execute", {})
                yield {"type": "status", "step": "execute", "message": "쿼리 실행 중..."}
                t0 = time.time()
                exec_result = await execute.run(pipeline_ctx, namespace_id, exec_cfg)
                stages_trace.append({"step": "execute", "ms": int((time.time() - t0) * 1000)})

                if "execute_error" in exec_result:
                    error_msg = exec_result["execute_error"]
                    status = "error"
                    # auto-fix 재시도 후 실패한 경우, 최종 SQL을 UI에 전송하여 에러와 SQL이 일치하도록
                    if pipeline_ctx.get("sql") and pipeline_ctx["sql"] != sql_result:
                        yield {"type": "sql", "sql": pipeline_ctx["sql"],
                               "reasoning": "", "cached": False}
                        sql_result = pipeline_ctx["sql"]
                    yield {"type": "token", "data": f"쿼리 실행 실패: {error_msg}"}
                else:
                    pipeline_ctx.update(exec_result)
                    table_result = exec_result
                    yield {"type": "table", **exec_result}

                    # 캐시 저장 (성공 시)
                    if sql_result and not cached_sql:
                        await service.set_cached_sql(namespace_id, query, sql_result)

                    # ── Stage 7: Summarize ───────────────────────────────
                    sum_cfg = stages_cfg.get("summarize", {})
                    if sum_cfg.get("is_enabled", False):
                        yield {"type": "status", "step": "summarize", "message": "결과 요약 중..."}
                        t0 = time.time()
                        sum_result = await summarize.run(pipeline_ctx, llm, sum_cfg)
                        stages_trace.append({"step": "summarize", "ms": int((time.time() - t0) * 1000)})
                        if sum_result.get("summary"):
                            yield {"type": "token", "data": sum_result["summary"]}
                        if sum_result.get("chart"):
                            yield {"type": "chart", "chart": sum_result["chart"]}
            else:
                status = "error"
                error_msg = "SQL 생성에 실패했습니다."
                yield {"type": "token", "data": error_msg}

        except BlockedQueryError as e:
            status = "blocked"
            error_msg = f"보안 차단: {e}"
            yield {"type": "token", "data": error_msg}
        except Exception as e:
            logger.error("Text2SqlAgent 오류: %s", e, exc_info=True)
            status = "error"
            error_msg = str(e)
            yield {"type": "token", "data": f"오류가 발생했습니다: {e}"}

        # ── 감사 로그 저장 ──────────────────────────────────────────────
        duration_ms = int((time.time() - start_total) * 1000)
        summary_text = ""
        if table_result:
            rows = table_result.get("rows", [])
            summary_text = f"{len(rows)}건 조회됨"

        audit_entry = {
            "question": query,
            "sql": sql_result,
            "status": status,
            "duration_ms": duration_ms,
            "cached": _cache_hit,
            "tokens": 0,
            "error": error_msg,
            "result_preview": table_result.get("rows", [])[:5] if table_result else None,
            "stages": stages_trace,
        }
        try:
            audit_id = await service.save_audit_log(namespace_id, audit_entry)
        except Exception:
            audit_id = None

        # ── 메시지 DB 저장 ──────────────────────────────────────────────
        final_content = summary_text or error_msg or (f"SQL: {sql_result}" if sql_result else "처리 완료")
        msg_metadata: dict | None = None
        if sql_result or table_result:
            msg_metadata = {}
            if sql_result:
                msg_metadata["sql_result"] = {
                    "sql": sql_result,
                    "reasoning": pipeline_ctx.get("reasoning", ""),
                    "cached": _cache_hit,
                }
            if table_result:
                msg_metadata["table_result"] = {
                    "columns": table_result.get("columns", []),
                    "rows": table_result.get("rows", []),
                    "row_count": table_result.get("row_count", 0),
                    "truncated": table_result.get("truncated", False),
                }
            if pipeline_ctx.get("chart"):
                msg_metadata["chart_result"] = pipeline_ctx["chart"]
        await update_assistant_message(msg_id, final_content, status if status != "success" else "completed", metadata=msg_metadata)

        yield {"type": "done", "message_id": msg_id, "status": status,
               "audit_id": audit_id}
