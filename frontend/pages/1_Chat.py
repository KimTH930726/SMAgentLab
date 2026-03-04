"""
Ops-Navigator — 운영 보조 챗
"""
import json
import os

import httpx
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

# ─── 전역 CSS ─────────────────────────────────────────────────────────────────

st.markdown("""
<style>
/* ── 사이드바 대화 목록: 버튼 → 플랫 텍스트 스타일 ── */
section[data-testid="stSidebar"] button[data-testid="stBaseButton-secondary"] {
    background: transparent !important;
    border: none !important;
    color: rgba(250,250,250,0.82) !important;
    text-align: left !important;
    justify-content: flex-start !important;
    padding: 5px 8px !important;
    border-radius: 8px !important;
    font-size: 14px !important;
    font-weight: 400 !important;
    line-height: 1.4 !important;
    box-shadow: none !important;
}
section[data-testid="stSidebar"] button[data-testid="stBaseButton-secondary"]:hover {
    background: rgba(255,255,255,0.08) !important;
    color: rgba(250,250,250,1) !important;
}
section[data-testid="stSidebar"] button[data-testid="stBaseButton-primary"] {
    border-radius: 8px !important;
    text-align: left !important;
    justify-content: flex-start !important;
    font-size: 14px !important;
    font-weight: 500 !important;
    padding: 5px 8px !important;
}
/* 사이드바 버튼 간격 */
section[data-testid="stSidebar"] .stButton {
    margin-bottom: 2px !important;
}
/* 사이드바 divider */
section[data-testid="stSidebar"] hr {
    margin: 8px 0 !important;
}
/* 삭제 확인 버튼 영역 */
.del-row button {
    font-size: 12px !important;
    padding: 2px 6px !important;
}
/* 👍👎 피드백 버튼 - 마커 기반 타겟팅 */
[data-testid="stMarkdownContainer"]:has(.fb-anchor)
    + [data-testid="stHorizontalBlock"] {
    gap: 4px !important;
}
[data-testid="stMarkdownContainer"]:has(.fb-anchor)
    + [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
    flex: 0 0 auto !important;
    min-width: 44px !important;
    max-width: 52px !important;
}
</style>
""", unsafe_allow_html=True)


# ─── API 헬퍼 ─────────────────────────────────────────────────────────────────

def api_get(path: str, params: dict = None):
    try:
        resp = httpx.get(f"{BACKEND_URL}{path}", params=params, timeout=10.0)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


def api_post(path: str, body: dict):
    try:
        resp = httpx.post(f"{BACKEND_URL}{path}", json=body, timeout=10.0)
        if resp.status_code in (200, 201):
            return resp.json()
    except Exception:
        pass
    return None


def api_delete(path: str) -> bool:
    try:
        resp = httpx.delete(f"{BACKEND_URL}{path}", timeout=10.0)
        return resp.status_code == 204
    except Exception:
        return False


# ─── DB 메시지 파싱 ───────────────────────────────────────────────────────────

def _parse_db_messages(db_msgs: list) -> list:
    result = []
    i = 0
    while i < len(db_msgs):
        msg = db_msgs[i]
        if msg["role"] == "user":
            result.append({"role": "user", "content": msg["content"]})
            if i + 1 < len(db_msgs) and db_msgs[i + 1]["role"] == "assistant":
                a = db_msgs[i + 1]
                result.append({
                    "role": "assistant",
                    "content": a["content"],
                    "mapped_term": a.get("mapped_term"),
                    "results": a.get("results") or [],
                    "_id": f"hist_{a['id']}",
                    "_from_history": True,
                })
                i += 2
                continue
        elif msg["role"] == "assistant":
            result.append({
                "role": "assistant",
                "content": msg["content"],
                "mapped_term": msg.get("mapped_term"),
                "results": msg.get("results") or [],
                "_id": f"hist_{msg['id']}",
                "_from_history": True,
            })
        i += 1
    return result


# ─── 세션 상태 초기화 ─────────────────────────────────────────────────────────

def _init_state():
    defaults = {
        "current_conv_id": None,
        "current_ns": None,
        "messages": [],
        "conv_list": [],
        "w_vector": 0.7,
        "top_k": 5,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_state()


# ─── 사이드바 ─────────────────────────────────────────────────────────────────

with st.sidebar:
    # Namespace 선택
    namespaces = api_get("/api/namespaces") or []
    if namespaces:
        ns_idx = 0
        if st.session_state.current_ns in namespaces:
            ns_idx = namespaces.index(st.session_state.current_ns)
        selected_ns = st.selectbox("Namespace", namespaces, index=ns_idx, label_visibility="collapsed")
    else:
        selected_ns = st.text_input("Namespace", value="default", label_visibility="collapsed")
        st.caption("등록된 네임스페이스가 없습니다.")

    if selected_ns != st.session_state.current_ns:
        st.session_state.current_ns = selected_ns
        st.session_state.current_conv_id = None
        st.session_state.messages = []
        st.session_state.conv_list = []

    st.divider()

    # 새 대화 버튼
    if st.button("✏️  새 대화", use_container_width=True, type="secondary"):
        st.session_state.current_conv_id = None
        st.session_state.messages = []
        st.rerun()

    # 최근 대화 목록
    st.markdown(
        "<p style='font-size:11px;color:rgba(250,250,250,0.4);margin:10px 0 4px 4px;"
        "text-transform:uppercase;letter-spacing:0.07em;'>최근</p>",
        unsafe_allow_html=True,
    )

    if not st.session_state.conv_list and selected_ns:
        st.session_state.conv_list = api_get("/api/conversations", {"namespace": selected_ns}) or []
    conv_list = st.session_state.conv_list

    if conv_list:
        for conv in conv_list:
            is_active = (conv["id"] == st.session_state.current_conv_id)
            label = conv["title"][:28] + ("…" if len(conv["title"]) > 28 else "")
            show_del_key = f"show_del_{conv['id']}"

            c_btn, c_dots = st.columns([9, 1])
            with c_btn:
                if st.button(
                    label,
                    key=f"conv_{conv['id']}",
                    use_container_width=True,
                    type="primary" if is_active else "secondary",
                    help=conv["created_at"][:16],
                ):
                    if not is_active:
                        msgs = api_get(f"/api/conversations/{conv['id']}/messages") or []
                        st.session_state.current_conv_id = conv["id"]
                        st.session_state.messages = _parse_db_messages(msgs)
                        # 다른 대화의 삭제 패널 닫기
                        for k in list(st.session_state.keys()):
                            if k.startswith("show_del_"):
                                del st.session_state[k]
                        st.rerun()
            with c_dots:
                if st.button("⋯", key=f"dots_{conv['id']}", help="옵션"):
                    st.session_state[show_del_key] = not st.session_state.get(show_del_key, False)
                    st.rerun()

            if st.session_state.get(show_del_key):
                c_del, c_cancel = st.columns([1, 1])
                with c_del:
                    if st.button(
                        "🗑️ 삭제",
                        key=f"del_yes_{conv['id']}",
                        use_container_width=True,
                        type="secondary",
                    ):
                        if api_delete(f"/api/conversations/{conv['id']}"):
                            if st.session_state.current_conv_id == conv["id"]:
                                st.session_state.current_conv_id = None
                                st.session_state.messages = []
                            st.session_state.conv_list = []
                            st.session_state.pop(show_del_key, None)
                            st.rerun()
                with c_cancel:
                    if st.button("취소", key=f"del_no_{conv['id']}", use_container_width=True):
                        st.session_state.pop(show_del_key, None)
                        st.rerun()
    else:
        st.markdown(
            "<p style='font-size:13px;color:rgba(250,250,250,0.35);padding:4px 8px;'>"
            "대화 이력이 없습니다.</p>",
            unsafe_allow_html=True,
        )

    st.divider()

    # 검색 설정 — 네이티브 expander
    with st.expander("⚙️ 검색 설정"):
        st.session_state.w_vector = st.slider(
            "벡터 비중", 0.0, 1.0, st.session_state.w_vector, 0.05,
        )
        st.caption(f"벡터 {st.session_state.w_vector:.0%}  |  키워드 {1 - st.session_state.w_vector:.0%}")
        st.session_state.top_k = st.slider("Top-K", 1, 10, st.session_state.top_k)
        st.caption(f"검색 결과 최대 {st.session_state.top_k}건")

w_vector = st.session_state.w_vector
w_keyword = round(1.0 - w_vector, 2)
top_k = st.session_state.top_k


# ─── 렌더링 헬퍼 ──────────────────────────────────────────────────────────────

def _render_result_card(mapped_term, results):
    if mapped_term:
        st.caption(f"🔤 용어 매핑: **{mapped_term}**")
    if not results:
        st.info("관련 문서를 찾지 못했습니다.")
        return
    top = results[0]
    col1, col2 = st.columns([1, 2])
    with col1:
        if top.get("container_name"):
            st.markdown(f"**🐳 컨테이너**\n\n`{top['container_name']}`")
    with col2:
        tables = top.get("target_tables") or []
        if tables:
            st.markdown("**🗂️ 관련 테이블**\n\n" + "  ".join(f"`{t}`" for t in tables))
    if top.get("query_template"):
        st.markdown("**📋 SQL 쿼리**")
        st.code(top["query_template"], language="sql")
    with st.expander(f"📄 검색된 문서 ({len(results)}건)", expanded=False):
        for r in results:
            st.markdown(
                f"**#{r['id']}** | 점수: `{r['final_score']:.4f}` | "
                + (f"컨테이너: `{r['container_name']}`" if r.get("container_name") else "")
            )
            content = r.get("content", "")
            st.write(content[:300] + ("..." if len(content) > 300 else ""))
            st.divider()


def _send_feedback(msg: dict, is_positive: bool):
    results = msg.get("results", [])
    knowledge_id = results[0]["id"] if results else None
    try:
        httpx.post(
            f"{BACKEND_URL}/api/feedback",
            json={
                "knowledge_id": knowledge_id,
                "namespace": st.session_state.current_ns or "",
                "question": msg.get("question", ""),
                "answer": msg.get("content"),
                "is_positive": is_positive,
            },
            timeout=5.0,
        )
    except Exception:
        pass


def _render_kb_improve_form(msg: dict):
    results = msg.get("results", [])
    top = results[0] if results else {}
    mid = msg["_id"]
    done_key = f"kb_done_{mid}"

    with st.container(border=True):
        st.markdown(
            "**💡 지식 베이스 개선** — 올바른 가이드를 등록하면 다음 질문에 더 잘 답변합니다.\n\n"
            "건너뛰기를 누르면 이 과정을 생략할 수 있습니다."
        )
        with st.form(f"kb_improve_{mid}"):
            col_a, col_b = st.columns(2)
            with col_a:
                st.text_input(
                    "Namespace", value=st.session_state.current_ns or "",
                    disabled=True, key=f"kb_ns_{mid}",
                )
                kb_container = st.text_input(
                    "컨테이너명", value=top.get("container_name") or "",
                    key=f"kb_ct_{mid}",
                )
            with col_b:
                kb_tables = st.text_input(
                    "관련 테이블 (쉼표 구분)",
                    value=", ".join(top.get("target_tables") or []),
                    key=f"kb_tb_{mid}",
                )
                kb_weight = st.number_input(
                    "base_weight", min_value=0.0, value=1.5, step=0.1,
                    key=f"kb_bw_{mid}",
                )
            kb_content = st.text_area(
                "개선된 가이드 내용 *", height=120,
                placeholder="올바른 운영 처리 방법, 원인 및 해결 방법을 상세히 작성하세요.",
                key=f"kb_co_{mid}",
            )
            kb_query = st.text_area(
                "SQL 쿼리 템플릿 (선택)",
                value=top.get("query_template") or "", height=80,
                key=f"kb_sq_{mid}",
            )
            col_ok, col_skip, _ = st.columns([3, 2, 5])
            with col_ok:
                submitted = st.form_submit_button("✅ 지식 등록", type="primary", use_container_width=True)
            with col_skip:
                skipped = st.form_submit_button("건너뛰기", use_container_width=True)

            if submitted:
                if not kb_content:
                    st.error("내용은 필수입니다.")
                else:
                    ns = st.session_state.current_ns or ""
                    tables = [t.strip() for t in kb_tables.split(",") if t.strip()] if kb_tables else []
                    created = api_post("/api/knowledge", {
                        "namespace": ns,
                        "container_name": kb_container or None,
                        "target_tables": tables or None,
                        "content": kb_content,
                        "query_template": kb_query or None,
                        "base_weight": kb_weight,
                    })
                    if created:
                        st.success(f"✅ 지식 등록 완료 (ID: {created['id']}) — 감사합니다!")
                        st.session_state[done_key] = True
                        st.rerun()
            if skipped:
                st.session_state[done_key] = True
                st.rerun()


def _render_feedback(msg: dict):
    if msg.get("_from_history"):
        return  # 이력에서 불러온 메시지는 피드백 버튼 생략

    fb_key = f"fb_{msg['_id']}"
    done_key = f"kb_done_{msg['_id']}"

    if st.session_state.get(fb_key):
        fb_val = st.session_state[fb_key]
        st.caption(f"✅ 피드백: {'👍' if fb_val == 'pos' else '👎'}")
        if fb_val == "neg" and not st.session_state.get(done_key, False):
            _render_kb_improve_form(msg)
        return

    st.markdown('<span class="fb-anchor"></span>', unsafe_allow_html=True)
    col_pos, col_neg, _ = st.columns([1, 1, 8], gap="small")
    with col_pos:
        if st.button("👍", key=f"pos_{msg['_id']}"):
            _send_feedback(msg, True)
            st.session_state[fb_key] = "pos"
            st.rerun()
    with col_neg:
        if st.button("👎", key=f"neg_{msg['_id']}"):
            _send_feedback(msg, False)
            st.session_state[fb_key] = "neg"
            st.rerun()


def _render_assistant_msg(msg: dict):
    _render_result_card(msg.get("mapped_term"), msg.get("results", []))
    st.markdown("**🤖 AI 답변**")
    st.write(msg.get("content", ""))
    _render_feedback(msg)


# ─── 채팅 메인 영역 ───────────────────────────────────────────────────────────

if st.session_state.current_conv_id:
    conv_title = next(
        (c["title"] for c in conv_list if c["id"] == st.session_state.current_conv_id),
        f"대화방 #{st.session_state.current_conv_id}",
    )
    st.markdown(f"<h4 style='margin-bottom:0.3rem'>{conv_title}</h4>", unsafe_allow_html=True)
else:
    st.markdown(
        "<h4 style='margin-bottom:0.3rem;color:rgba(250,250,250,0.4)'>새 대화를 시작하세요</h4>",
        unsafe_allow_html=True,
    )

st.markdown("---")

if st.session_state.current_conv_id and not st.session_state.messages:
    st.info("이 대화방에는 저장된 메시지가 없습니다.")
else:
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            if msg["role"] == "user":
                st.write(msg["content"])
            else:
                _render_assistant_msg(msg)

question = st.chat_input("운영 질문을 입력하세요...")

if question:
    with st.chat_message("user"):
        st.write(question)
    st.session_state.messages.append({"role": "user", "content": question})

    with st.chat_message("assistant"):
        status_box = st.status("처리 중...", expanded=True)
        status_msg = status_box.empty()
        result_area = st.empty()
        answer_area = st.empty()

        full_answer = ""
        results_data = []
        mapped_term_val = None
        conv_id_received = None
        error_msg = None

        payload = {
            "namespace": selected_ns,
            "question": question,
            "w_vector": w_vector,
            "w_keyword": w_keyword,
            "top_k": top_k,
            "conversation_id": st.session_state.current_conv_id,
        }

        try:
            with httpx.Client(timeout=960.0) as client:
                with client.stream(
                    "POST",
                    f"{BACKEND_URL}/api/chat/stream",
                    json=payload,
                ) as resp:
                    resp.raise_for_status()
                    for raw_line in resp.iter_lines():
                        if not raw_line.startswith("data: "):
                            continue
                        event = json.loads(raw_line[6:])
                        etype = event.get("type")

                        if etype == "status":
                            status_msg.markdown(f"**{event['message']}**")
                        elif etype == "meta":
                            conv_id_received = event.get("conversation_id")
                            mapped_term_val = event.get("mapped_term")
                            results_data = event.get("results", [])
                            with result_area.container():
                                _render_result_card(mapped_term_val, results_data)
                        elif etype == "token":
                            full_answer += event.get("data", "")
                            answer_area.markdown(f"**🤖 AI 답변**\n\n{full_answer}▌")
                        elif etype == "done":
                            answer_area.markdown(f"**🤖 AI 답변**\n\n{full_answer}")
                            status_box.update(
                                label=f"✅ 완료 — {len(results_data)}건 검색됨",
                                state="complete",
                                expanded=False,
                            )
        except httpx.HTTPStatusError as e:
            error_msg = f"API 오류: {e.response.status_code}"
        except Exception as e:
            error_msg = f"연결 오류: {e}"

        if error_msg:
            status_box.update(label="❌ 오류", state="error", expanded=False)
            st.error(error_msg)
        else:
            if conv_id_received and conv_id_received != st.session_state.current_conv_id:
                st.session_state.current_conv_id = conv_id_received
                st.session_state.conv_list = []

            turn_id = f"new_{len(st.session_state.messages)}"
            assistant_msg = {
                "role": "assistant",
                "content": full_answer,
                "mapped_term": mapped_term_val,
                "results": results_data,
                "_id": turn_id,
                "question": question,
            }
            st.session_state.messages.append(assistant_msg)
            _render_feedback(assistant_msg)
