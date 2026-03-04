"""
Ops-Navigator — 관리 화면 (Admin View)
지식 등록/수정/삭제 + 네임스페이스 관리 + 통계 대시보드
"""
import os

import httpx
import pandas as pd
import plotly.express as px
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

st.title("🗄️ 관리 화면")

tab_ns, tab_knowledge, tab_glossary, tab_stats, tab_debug = st.tabs(
    ["🏷️ 네임스페이스", "📚 지식 베이스", "📖 용어집", "📊 통계 대시보드", "🔬 벡터 검색 테스트"]
)


# ─── 공통 유틸 ────────────────────────────────────────────────────────────────

def api_get(path: str, params: dict = None):
    try:
        resp = httpx.get(f"{BACKEND_URL}{path}", params=params, timeout=10.0)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        st.error(f"API 오류: {e}")
        return None


def api_post(path: str, body: dict):
    try:
        resp = httpx.post(f"{BACKEND_URL}{path}", json=body, timeout=30.0)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        st.error(f"API 오류: {e}")
        return None


def api_put(path: str, body: dict):
    try:
        resp = httpx.put(f"{BACKEND_URL}{path}", json=body, timeout=30.0)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        st.error(f"API 오류: {e}")
        return None


def api_delete(path: str):
    try:
        resp = httpx.delete(f"{BACKEND_URL}{path}", timeout=10.0)
        return resp.status_code == 204
    except Exception as e:
        st.error(f"API 오류: {e}")
        return False


# ─── 탭 1: 네임스페이스 관리 ──────────────────────────────────────────────────

with tab_ns:
    st.markdown(
        "Namespace는 운영 도메인 구분 단위입니다. (예: `coupon`, `gift`, `order`) "
        "각 namespace 안에 지식 베이스와 용어집을 독립적으로 관리합니다."
    )

    if st.button("🔄 새로고침", key="ns_refresh"):
        st.cache_data.clear()

    ns_list = api_get("/api/namespaces/detail") or []

    # ── 현재 목록 ──
    if ns_list:
        st.subheader(f"등록된 Namespace ({len(ns_list)}개)")

        for ns in ns_list:
            confirm_key = f"confirm_del_ns_{ns['name']}"
            col_info, col_del = st.columns([5, 1])

            with col_info:
                st.markdown(
                    f"**`{ns['name']}`** "
                    f"&nbsp; 📚 지식 {ns['knowledge_count']}건 "
                    f"&nbsp; 📖 용어집 {ns['glossary_count']}건"
                    + (f"  \n> {ns['description']}" if ns['description'] else ""),
                    unsafe_allow_html=False,
                )

            with col_del:
                if st.button("🗑️ 삭제", key=f"del_ns_{ns['name']}", type="secondary"):
                    st.session_state[confirm_key] = True

            # 삭제 확인 패널
            if st.session_state.get(confirm_key):
                total = ns["knowledge_count"] + ns["glossary_count"]
                if total > 0:
                    st.warning(
                        f"⚠️ **`{ns['name']}`** namespace를 삭제하면 "
                        f"지식 {ns['knowledge_count']}건, "
                        f"용어집 {ns['glossary_count']}건이 **모두 영구 삭제**됩니다."
                    )
                else:
                    st.info(f"**`{ns['name']}`** namespace를 삭제합니다. (등록된 데이터 없음)")

                c_yes, c_no, _ = st.columns([1, 1, 4])
                with c_yes:
                    if st.button("✅ 삭제 확인", key=f"yes_ns_{ns['name']}", type="primary"):
                        if api_delete(f"/api/namespaces/{ns['name']}"):
                            st.success(f"`{ns['name']}` 삭제 완료")
                            st.session_state.pop(confirm_key, None)
                            st.cache_data.clear()
                            st.rerun()
                with c_no:
                    if st.button("취소", key=f"no_ns_{ns['name']}"):
                        st.session_state.pop(confirm_key, None)
                        st.rerun()

            st.divider()
    else:
        st.info("등록된 Namespace가 없습니다. 아래에서 새로 추가하세요.")

    # ── 신규 추가 ──
    st.subheader("➕ 새 Namespace 추가")
    with st.form("ns_create_form"):
        col_name, col_desc = st.columns([1, 2])
        with col_name:
            new_ns_name = st.text_input(
                "Namespace 이름 *",
                placeholder="예: order, payment, delivery",
                help="영문 소문자, 숫자, 하이픈만 사용 권장",
            )
        with col_desc:
            new_ns_desc = st.text_input(
                "설명 (선택)",
                placeholder="예: 주문 관련 운영 가이드",
            )
        if st.form_submit_button("추가", type="primary"):
            if not new_ns_name:
                st.error("Namespace 이름을 입력하세요.")
            else:
                result = api_post("/api/namespaces", {"name": new_ns_name.strip(), "description": new_ns_desc})
                if result:
                    st.success(f"✅ `{new_ns_name}` namespace가 추가되었습니다.")
                    st.cache_data.clear()
                    st.rerun()


# ─── 탭 2: 지식 베이스 ────────────────────────────────────────────────────────

with tab_knowledge:
    all_ns = api_get("/api/namespaces") or []

    # 신규 등록 폼
    with st.expander("➕ 신규 지식 등록", expanded=False):
        if not all_ns:
            st.warning("먼저 **네임스페이스 탭**에서 Namespace를 추가하세요.")
        else:
            with st.form("knowledge_form"):
                col1, col2 = st.columns(2)
                with col1:
                    ns = st.selectbox("Namespace *", all_ns, key="kn_form_ns")
                    container = st.text_input(
                        "컨테이너명",
                        placeholder="예: coupon-api",
                        help="표시 전용 — 검색에 사용되지 않습니다.",
                    )
                with col2:
                    tables_raw = st.text_input(
                        "관련 테이블 (쉼표 구분)",
                        placeholder="예: coupon_issue, coupon_use_history",
                        help="표시 전용 — 검색에 사용되지 않습니다.",
                    )
                    base_weight = st.number_input(
                        "base_weight",
                        min_value=0.0,
                        value=1.0,
                        step=0.1,
                        help="최종 점수 배율: (벡터+키워드 점수) × (1 + base_weight)",
                    )

                content = st.text_area(
                    "📌 가이드 내용 *  ←  🔍 벡터 + 키워드 검색 대상",
                    height=150,
                    placeholder="운영 처리 방법, 원인 설명 등을 상세히 기술하세요. 이 내용이 임베딩되어 검색 기준이 됩니다.",
                    help="이 필드가 임베딩(벡터화)되며, 키워드 전문검색(tsvector) 인덱스도 이 필드에 적용됩니다.",
                )
                query_tmpl = st.text_area(
                    "SQL 쿼리 템플릿",
                    height=100,
                    placeholder="SELECT * FROM coupon_issue WHERE ...",
                    help="표시 전용 — 검색에 사용되지 않습니다.",
                )

                submitted = st.form_submit_button("등록", type="primary")
                if submitted:
                    if not content:
                        st.error("내용은 필수입니다.")
                    else:
                        tables = [t.strip() for t in tables_raw.split(",") if t.strip()] if tables_raw else []
                        result = api_post(
                            "/api/knowledge",
                            {
                                "namespace": ns,
                                "container_name": container or None,
                                "target_tables": tables or None,
                                "content": content,
                                "query_template": query_tmpl or None,
                                "base_weight": base_weight,
                            },
                        )
                        if result:
                            st.success(f"등록 완료! (ID: {result['id']})")
                            st.cache_data.clear()

    st.divider()

    # 필터
    filter_ns = st.selectbox("Namespace 필터", ["(전체)"] + all_ns, key="kn_filter")
    ns_param = None if filter_ns == "(전체)" else filter_ns

    knowledge_list = api_get("/api/knowledge", {"namespace": ns_param} if ns_param else None) or []
    st.caption(f"총 {len(knowledge_list)}건")

    for item in knowledge_list:
        with st.expander(
            f"[{item['namespace']}] #{item['id']} — {item['content'][:60]}...",
            expanded=False,
        ):
            edit_key = f"edit_{item['id']}"

            # 내용 표시
            st.text_area(
                "📌 가이드 내용  ← 🔍 검색 대상 (벡터 + 키워드)",
                value=item["content"],
                height=100,
                disabled=True,
                key=f"content_{item['id']}",
            )
            st.caption(
                f"표시 전용 —  컨테이너: `{item.get('container_name') or '-'}`  "
                + ("  ".join(f"테이블: `{t}`" for t in (item.get("target_tables") or [])) or "테이블: -")
                + f"  |  base_weight: `{item['base_weight']}`"
            )
            if item.get("query_template"):
                st.code(item["query_template"], language="sql")
            st.caption(f"등록: {item['created_at']} | 수정: {item['updated_at']}")

            # 수정 / 삭제 버튼 (나란히)
            c_edit, c_del, _ = st.columns([1, 1, 4])
            with c_edit:
                if st.button("✏️ 수정", key=f"edit_btn_{item['id']}", use_container_width=True):
                    st.session_state[edit_key] = not st.session_state.get(edit_key, False)
                    st.rerun()
            with c_del:
                if st.button("🗑️ 삭제", key=f"del_{item['id']}", type="secondary", use_container_width=True):
                    if api_delete(f"/api/knowledge/{item['id']}"):
                        st.success("삭제 완료")
                        st.rerun()

            if st.session_state.get(edit_key):
                with st.form(f"edit_form_{item['id']}"):
                    new_content = st.text_area(
                        "📌 가이드 내용  ←  🔍 검색 대상 (벡터 + 키워드)",
                        value=item["content"],
                        height=100,
                        help="저장 시 자동으로 재임베딩됩니다.",
                    )
                    col_ea, col_eb = st.columns(2)
                    with col_ea:
                        new_container = st.text_input(
                            "컨테이너  (표시 전용)",
                            value=item.get("container_name") or "",
                        )
                        new_weight = st.number_input(
                            "base_weight",
                            value=item["base_weight"],
                            step=0.1,
                        )
                    with col_eb:
                        new_tables = st.text_input(
                            "테이블 (쉼표)  (표시 전용)",
                            value=", ".join(item.get("target_tables") or []),
                        )
                        new_query = st.text_area(
                            "SQL  (표시 전용)",
                            value=item.get("query_template") or "",
                            height=80,
                        )
                    c_save, c_cancel = st.columns([1, 1])
                    with c_save:
                        if st.form_submit_button("저장", type="primary"):
                            updated = api_put(
                                f"/api/knowledge/{item['id']}",
                                {
                                    "container_name": new_container or None,
                                    "target_tables": [t.strip() for t in new_tables.split(",") if t.strip()] or None,
                                    "content": new_content,
                                    "query_template": new_query or None,
                                    "base_weight": new_weight,
                                },
                            )
                            if updated:
                                st.success("수정 완료")
                                st.session_state.pop(edit_key, None)
                                st.rerun()
                    with c_cancel:
                        if st.form_submit_button("취소"):
                            st.session_state.pop(edit_key, None)
                            st.rerun()


# ─── 탭 3: 용어집 ─────────────────────────────────────────────────────────────

with tab_glossary:
    all_ns_g = api_get("/api/namespaces") or []

    with st.expander("➕ 신규 용어 등록", expanded=False):
        if not all_ns_g:
            st.warning("먼저 **네임스페이스 탭**에서 Namespace를 추가하세요.")
        else:
            with st.form("glossary_form"):
                g_ns = st.selectbox("Namespace *", all_ns_g, key="gl_form_ns")
                g_term = st.text_input(
                    "표준 용어 *",
                    placeholder="예: 회수",
                    help="매핑 결과로 반환되는 레이블입니다. 직접 검색 기준이 아닙니다.",
                )
                g_desc = st.text_area(
                    "📌 설명 및 유의어 *  ←  🔍 벡터 검색 대상 (임베딩됨)",
                    placeholder="쿠폰 회수, 뺏어오기, 강제 반납, 강제 취소 등 이 표준 용어와 같은 의미의 다양한 표현을 나열하세요.",
                    height=100,
                    help="이 내용이 임베딩(벡터화)되어 사용자 질문과 유사도를 비교합니다. 유의어를 많이 넣을수록 정확도가 높아집니다.",
                )
                if st.form_submit_button("등록", type="primary"):
                    if not g_term or not g_desc:
                        st.error("용어와 설명은 필수입니다.")
                    else:
                        result = api_post(
                            "/api/knowledge/glossary",
                            {"namespace": g_ns, "term": g_term, "description": g_desc},
                        )
                        if result:
                            st.success(f"등록 완료! (ID: {result['id']})")

    st.divider()

    filter_g_ns = st.selectbox("Namespace 필터", ["(전체)"] + all_ns_g, key="g_filter")
    g_ns_param = None if filter_g_ns == "(전체)" else filter_g_ns
    glossary_list = api_get("/api/knowledge/glossary", {"namespace": g_ns_param} if g_ns_param else None) or []

    st.caption(f"총 {len(glossary_list)}건")
    for g in glossary_list:
        with st.expander(
            f"**[{g['namespace']}]** `{g['term']}` — {g['description'][:60]}{'...' if len(g['description']) > 60 else ''}",
            expanded=False,
        ):
            edit_key = f"gedit_{g['id']}"
            st.caption("표준 용어 (레이블 전용 — 검색 기준 아님)")
            st.markdown(f"`{g['term']}`")
            st.caption("📌 설명 및 유의어 ← 🔍 벡터 검색 대상 (임베딩됨)")
            st.write(g["description"])

            c_edit, c_del, _ = st.columns([1, 1, 4])
            with c_edit:
                if st.button("✏️ 수정", key=f"gedit_btn_{g['id']}", use_container_width=True):
                    st.session_state[edit_key] = not st.session_state.get(edit_key, False)
                    st.rerun()
            with c_del:
                if st.button("🗑️ 삭제", key=f"gdel_{g['id']}", type="secondary", use_container_width=True):
                    if api_delete(f"/api/knowledge/glossary/{g['id']}"):
                        st.rerun()

            if st.session_state.get(edit_key):
                with st.form(f"gedit_form_{g['id']}"):
                    new_term = st.text_input(
                        "표준 용어  (레이블 전용 — 검색 기준 아님)",
                        value=g["term"],
                    )
                    new_desc = st.text_area(
                        "📌 설명 및 유의어  ←  🔍 벡터 검색 대상 (저장 시 재임베딩)",
                        value=g["description"],
                        height=100,
                        help="이 내용이 임베딩됩니다. 유의어·다양한 표현을 풍부하게 작성하세요.",
                    )
                    c_save, c_cancel = st.columns([1, 1])
                    with c_save:
                        if st.form_submit_button("저장", type="primary"):
                            updated = api_put(
                                f"/api/knowledge/glossary/{g['id']}",
                                {"term": new_term, "description": new_desc},
                            )
                            if updated:
                                st.success("수정 완료")
                                st.session_state.pop(edit_key, None)
                                st.rerun()
                    with c_cancel:
                        if st.form_submit_button("취소"):
                            st.session_state.pop(edit_key, None)
                            st.rerun()


# ─── 탭 4: 통계 대시보드 ──────────────────────────────────────────────────────

with tab_stats:
    st.markdown("네임스페이스별로 **어떤 업무 질의가 많은지**, **어디서 미해결이 나오는지** 파악하는 화면입니다.")

    # ── Namespace 선택 ──
    all_ns_stats = api_get("/api/namespaces") or []

    col_ns_sel, col_ns_refresh = st.columns([4, 1])
    with col_ns_sel:
        if not all_ns_stats:
            st.info("등록된 Namespace가 없습니다.")
            st.stop()
        stats_ns = st.selectbox("📂 Namespace 선택", all_ns_stats, key="stats_ns_sel")
    with col_ns_refresh:
        st.write("")
        st.write("")
        if st.button("🔄", key="stats_refresh", help="새로고침"):
            st.rerun()

    # ── 선택된 NS 상세 통계 ──
    ns_detail = api_get(f"/api/stats/namespace/{stats_ns}")

    if not ns_detail:
        st.info("질의 이력이 없습니다. Chat 화면에서 질문을 입력하면 통계가 쌓입니다.")
        st.stop()

    total_q = ns_detail.get("total_queries", 0)
    resolved = ns_detail.get("resolved", 0)
    unresolved_cnt = ns_detail.get("unresolved", 0)
    resolve_rate = round(resolved / total_q * 100, 1) if total_q > 0 else 0

    # ── KPI 카드 ──
    kpi1, kpi2, kpi3 = st.columns(3)
    kpi1.metric("💬 전체 질의", f"{total_q:,}건")
    kpi2.metric("✅ 해결률", f"{resolve_rate}%", help=f"해결 {resolved}건 / 전체 {total_q}건")
    kpi3.metric(
        "🚨 미해결",
        f"{unresolved_cnt}건",
        delta=f"-{unresolved_cnt}" if unresolved_cnt > 0 else None,
        delta_color="inverse",
        help="지식 보완이 필요한 질의 수",
    )

    st.divider()

    # ── 업무 유형별 질의 분포 차트 ──
    term_dist = ns_detail.get("term_distribution", [])

    if term_dist:
        st.subheader("📊 업무 유형별 질의 비율")
        st.caption(
            "용어집 매핑으로 분류한 질의 유형 비율입니다. "
            "**미해결 비율이 높은 항목**의 지식 베이스를 먼저 보완하세요."
        )

        col_pie, col_table = st.columns([1, 1])

        with col_pie:
            pie_df = pd.DataFrame([
                {"유형": t["term"], "건수": t["total"]}
                for t in term_dist
            ])
            fig_pie = px.pie(
                pie_df,
                names="유형",
                values="건수",
                hole=0.45,
            )
            fig_pie.update_traces(
                textposition="outside",
                textinfo="percent+label",
                pull=[0.03] * len(pie_df),
            )
            fig_pie.update_layout(
                showlegend=False,
                margin=dict(t=20, b=20, l=20, r=20),
                height=360,
            )
            st.plotly_chart(fig_pie, use_container_width=True)

        with col_table:
            st.markdown("**유형별 상세**")
            total_all = sum(t["total"] for t in term_dist)
            for t in term_dist:
                pct = round(t["total"] / total_all * 100, 1) if total_all > 0 else 0
                unres_pct = round(t["unresolved"] / t["total"] * 100) if t["total"] > 0 else 0
                color = "🔴" if unres_pct >= 30 else ("🟡" if unres_pct >= 10 else "🟢")
                st.markdown(
                    f"{color} **{t['term']}**  \n"
                    f"총 {t['total']}건 ({pct}%)  |  미해결 {t['unresolved']}건 ({unres_pct}%)"
                )
                st.divider()
    else:
        st.info("아직 질의 기록이 없습니다.")

    st.divider()

    # ── 미해결 질문 목록 ──
    unresolved_cases = ns_detail.get("unresolved_cases", [])

    st.subheader(f"🚨 미해결 질문 ({len(unresolved_cases)}건)")
    if unresolved_cases:
        st.error(
            f"아래 **{len(unresolved_cases)}개 질문**에 AI가 답변을 찾지 못했습니다.  \n"
            "📝 버튼을 클릭하면 해당 질문을 기반으로 지식을 바로 등록할 수 있습니다."
        )
        for case in unresolved_cases:
            log_id = case.get("id")
            reg_key = f"unreg_{log_id}"
            term_badge = f"`{case['mapped_term']}`" if case.get("mapped_term") else "`(미분류)`"

            with st.container():
                c1, c2, c3 = st.columns([5, 1, 1])
                with c1:
                    st.markdown(f"**{case['question']}**  \n유형: {term_badge}")
                with c2:
                    st.caption(case["created_at"][:16] if case.get("created_at") else "")
                with c3:
                    if st.button("📝 등록", key=f"unreg_btn_{log_id}", type="primary"):
                        st.session_state[reg_key] = not st.session_state.get(reg_key, False)

            # 인라인 지식 등록 폼
            if st.session_state.get(reg_key):
                with st.form(f"unreg_form_{log_id}"):
                    st.markdown(f"**질문 기반 지식 등록** — `{case['question']}`")
                    col_a, col_b = st.columns(2)
                    with col_a:
                        uf_ns = st.selectbox("Namespace", all_ns_stats,
                                             index=all_ns_stats.index(stats_ns) if stats_ns in all_ns_stats else 0,
                                             key=f"uf_ns_{log_id}")
                        uf_container = st.text_input("컨테이너명", placeholder="예: coupon-api", key=f"uf_ct_{log_id}")
                    with col_b:
                        uf_tables = st.text_input("관련 테이블 (쉼표 구분)", key=f"uf_tb_{log_id}")
                        uf_weight = st.number_input("base_weight", min_value=0.0, value=1.0, step=0.1, key=f"uf_bw_{log_id}")
                    uf_content = st.text_area(
                        "가이드 내용 *",
                        value=case["question"],
                        height=120,
                        key=f"uf_co_{log_id}",
                        help="질문을 기반으로 운영 가이드 내용을 작성하세요.",
                    )
                    uf_query = st.text_area("SQL 쿼리 템플릿 (선택)", height=80, key=f"uf_sq_{log_id}")

                    col_ok, col_cancel, _ = st.columns([4, 2, 4])
                    with col_ok:
                        submitted = st.form_submit_button("✅ 등록 & 미해결 제거", type="primary", use_container_width=True)
                    with col_cancel:
                        cancelled = st.form_submit_button("취소", use_container_width=True)

                    if submitted:
                        if not uf_content:
                            st.error("내용은 필수입니다.")
                        else:
                            tables = [t.strip() for t in uf_tables.split(",") if t.strip()] if uf_tables else []
                            created = api_post("/api/knowledge", {
                                "namespace": uf_ns,
                                "container_name": uf_container or None,
                                "target_tables": tables or None,
                                "content": uf_content,
                                "query_template": uf_query or None,
                                "base_weight": uf_weight,
                            })
                            if created:
                                api_delete(f"/api/stats/query-log/{log_id}")
                                st.success(f"✅ 지식 등록 완료 (ID: {created['id']}) — 미해결 질문 삭제됨")
                                st.session_state.pop(reg_key, None)
                                st.rerun()
                    if cancelled:
                        st.session_state.pop(reg_key, None)
                        st.rerun()

            st.divider()
    else:
        st.success("✅ 미해결 케이스가 없습니다.")


# ─── 탭 5: 벡터 검색 테스트 ───────────────────────────────────────────────────

with tab_debug:
    st.markdown(
        "LLM 없이 검색 파이프라인 전 과정을 확인합니다. "
        "**임베딩 → 용어집 매핑 → 하이브리드 검색** 결과와 각 문서의 점수 내역을 볼 수 있습니다."
    )

    all_ns_debug = api_get("/api/namespaces") or []

    with st.form("debug_form"):
        col_ns, col_q = st.columns([1, 3])
        with col_ns:
            debug_ns = st.selectbox(
                "Namespace",
                all_ns_debug if all_ns_debug else ["(없음)"],
                key="debug_ns",
            )
        with col_q:
            debug_question = st.text_input(
                "검색 질문",
                placeholder="예: 쿠폰 뺏어오기 실패한 건 어떻게 확인해?",
            )

        col_w, col_k, col_topk = st.columns(3)
        with col_w:
            debug_w_vec = st.slider("벡터 비중", 0.0, 1.0, 0.7, 0.05, key="dbg_wv")
        with col_k:
            st.metric("키워드 비중", round(1.0 - debug_w_vec, 2))
        with col_topk:
            debug_top_k = st.slider("Top-K", 1, 20, 5, key="dbg_k")

        submitted = st.form_submit_button("🔍 검색 실행", type="primary")

    if submitted and debug_question and debug_ns != "(없음)":
        result = api_post(
            "/api/chat/debug",
            {
                "namespace": debug_ns,
                "question": debug_question,
                "w_vector": debug_w_vec,
                "w_keyword": round(1.0 - debug_w_vec, 2),
                "top_k": debug_top_k,
            },
        )

        if result:
            # ── 1. 용어집 매핑 결과 ─────────────────────────────────────
            st.divider()
            st.subheader("📖 Step 1 — 용어집 매핑")

            gm = result.get("glossary_match")
            enriched = result.get("enriched_query", debug_question)

            if gm:
                sim = gm["similarity"]
                sim_pct = int(sim * 100)
                col_gm1, col_gm2 = st.columns([1, 2])
                with col_gm1:
                    st.metric("매핑된 표준 용어", f'"{gm["term"]}"')
                    st.metric("유사도", f"{sim:.4f}  ({sim_pct}%)")
                with col_gm2:
                    st.markdown("**용어집 설명 (임베딩 원본)**")
                    st.info(gm["description"])

                if enriched != debug_question:
                    st.markdown("**강화된 검색어 (enriched_query)**")
                    st.code(enriched)
            else:
                st.warning("⚠️ 용어집 매핑 없음 — 등록된 용어집이 없거나 유사한 항목이 없습니다.")
                st.code(f"검색어 그대로 사용: {enriched}")

            # ── 2. 검색 파라미터 ─────────────────────────────────────────
            st.divider()
            st.subheader("⚙️ Step 2 — 검색 파라미터")
            c1, c2, c3 = st.columns(3)
            c1.metric("벡터 비중 (w_vector)", result["w_vector"])
            c2.metric("키워드 비중 (w_keyword)", result["w_keyword"])
            c3.metric("반환 문서 수", len(result.get("results", [])))

            # ── 3. 검색 결과 및 점수 내역 ────────────────────────────────
            st.divider()
            st.subheader("🔎 Step 3 — 하이브리드 검색 결과")

            results_list = result.get("results", [])
            if not results_list:
                st.error("검색 결과 없음 — 지식 베이스에 관련 문서가 없거나 벡터/키워드 모두 미매치")
            else:
                for i, r in enumerate(results_list, 1):
                    v = r["v_score"]
                    k = r["k_score"]
                    bw = r["base_weight"]
                    fs = r["final_score"]

                    formula = (
                        f"({result['w_vector']} × {v:.4f}) + "
                        f"({result['w_keyword']} × {k:.4f})"
                        f" × (1 + {bw:.1f}) = **{fs:.4f}**"
                    )

                    with st.expander(
                        f"#{i}  |  최종점수 `{fs:.4f}`  |  "
                        + (f"컨테이너: `{r['container_name']}`" if r.get("container_name") else f"ID: {r['id']}"),
                        expanded=(i == 1),
                    ):
                        score_col1, score_col2, score_col3, score_col4 = st.columns(4)
                        score_col1.metric("🔵 벡터 점수", f"{v:.4f}", help="코사인 유사도 (0~1)")
                        score_col2.metric("🟡 키워드 점수", f"{k:.4f}", help="ts_rank BM25 (0~∞)")
                        score_col3.metric("⚖️ base_weight", f"{bw:.1f}", help="문서 우선순위 가중치")
                        score_col4.metric("🏆 최종 점수", f"{fs:.4f}")

                        st.markdown(f"**점수 계산식:** {formula}")

                        bar_df = pd.DataFrame({
                            "항목": ["벡터 기여", "키워드 기여"],
                            "점수": [result["w_vector"] * v, result["w_keyword"] * k],
                        })
                        fig = px.bar(
                            bar_df, x="항목", y="점수",
                            color="항목",
                            color_discrete_map={"벡터 기여": "#4e9af1", "키워드 기여": "#f1c40f"},
                            height=200,
                        )
                        fig.update_layout(showlegend=False, margin=dict(t=10, b=10))
                        st.plotly_chart(fig, use_container_width=True)

                        st.markdown("**내용**")
                        st.write(r["content"])
                        if r.get("container_name"):
                            st.markdown(f"**컨테이너:** `{r['container_name']}`")
                        if r.get("target_tables"):
                            st.markdown("**테이블:** " + "  ".join(f"`{t}`" for t in r["target_tables"]))
                        if r.get("query_template"):
                            st.markdown("**SQL 쿼리**")
                            st.code(r["query_template"], language="sql")

            # ── 4. LLM에 전달될 컨텍스트 미리보기 ────────────────────────
            st.divider()
            st.subheader("📝 Step 4 — LLM 컨텍스트 미리보기")
            st.caption("실제 Chat 호출 시 이 내용이 LLM 프롬프트에 삽입됩니다.")
            ctx = result.get("context_preview", "")
            st.text_area("컨텍스트 (프롬프트 삽입 내용)", value=ctx, height=300, disabled=True)

    elif submitted:
        st.warning("Namespace와 질문을 모두 입력하세요.")
