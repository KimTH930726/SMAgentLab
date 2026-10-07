"""웹 크롤러 + Confluence REST API 어댑터 — URL → ParsedDocument 변환."""
import logging
import re
import socket
from urllib.parse import urlparse, parse_qs, urljoin

import httpx

from agents.knowledge_rag.ingestion.adapters import ParsedDocument, parse_markdown

logger = logging.getLogger(__name__)

_CONFLUENCE_PATTERNS = re.compile(
    r"/(display/|pages/viewpage\.action|spaces/viewspace\.action|rest/api/content)",
    re.IGNORECASE,
)

FETCH_TIMEOUT = 30.0

# 트리 탐색 안전장치 (Confluence space 폭주 방지)
TREE_DEFAULT_DEPTH = 3
TREE_MAX_DEPTH = 10
TREE_DEFAULT_MAX_PAGES = 100
TREE_HARD_MAX_PAGES = 500


# ── 네트워크 예외 → 사용자 메시지 변환 ─────────────────────────────────────────

def _translate_httpx_error(e: Exception, service: str = "Confluence") -> ValueError:
    """httpx 예외의 실제 원인을 구분해 사용자에게 보여줄 한국어 메시지로 변환.

    502로 raw exception str(예: "[Errno -3] Temporary failure in name resolution")이
    그대로 노출되던 문제 — 인증 만료/DNS 실패/타임아웃을 구분해 원인을 명확히 알려준다.
    """
    if isinstance(e, httpx.HTTPStatusError):
        status = e.response.status_code
        content_type = e.response.headers.get("content-type", "")
        if status in (401, 403):
            return ValueError(
                f"{service} 인증 실패 (HTTP {status}): Personal Access Token이 만료되었거나 "
                "권한이 없습니다. 토큰을 새로 발급받아 다시 등록해주세요."
            )
        if "text/html" in content_type:
            # Confluence는 PAT 만료/무효 시 401/403 대신 로그인 페이지(HTML)를 404 등으로 반환하는 경우가 많음
            return ValueError(
                f"{service} 인증 실패로 추정됩니다 (HTTP {status}): API 요청이 JSON 대신 로그인 페이지로 "
                "리다이렉트되었습니다. Personal Access Token이 만료되었거나 무효할 수 있습니다. "
                "토큰을 새로 발급받아 다시 등록해주세요."
            )
        body = (e.response.text or "").strip()[:200]
        return ValueError(f"{service} 서버 오류 (HTTP {status})" + (f": {body}" if body else ""))

    cause = e.__cause__ or e.__context__
    if isinstance(cause, socket.gaierror):
        return ValueError(
            f"{service} 서버 주소를 찾을 수 없습니다 (DNS 조회 실패: {cause}). "
            "URL이 올바른지, 사내망/VPN 연결 상태를 확인해주세요."
        )
    if isinstance(e, httpx.ConnectError):
        return ValueError(f"{service} 서버에 연결할 수 없습니다: {e}")
    if isinstance(e, httpx.TimeoutException):
        return ValueError(
            f"{service} 서버 응답이 시간 초과되었습니다 ({FETCH_TIMEOUT:.0f}초). "
            "네트워크 상태를 확인 후 다시 시도해주세요."
        )
    return ValueError(f"{service} 요청 실패: {e}")


# ── 공개 진입점 ───────────────────────────────────────────────────────────────

async def fetch_url(url: str, confluence_token: str | None = None) -> ParsedDocument:
    """URL을 받아 ParsedDocument로 반환.

    - Confluence URL이면 REST API로 정확히 파싱
    - 일반 URL이면 httpx + BeautifulSoup으로 텍스트 추출
    """
    if _is_confluence(url):
        if not confluence_token:
            raise ValueError("Confluence 페이지를 가져오려면 Personal Access Token이 필요합니다.")
        return await _fetch_confluence(url, confluence_token)
    return await _fetch_web(url)


# ── 일반 웹 크롤러 ─────────────────────────────────────────────────────────────

async def _fetch_web(url: str) -> ParsedDocument:
    """일반 웹 페이지 → BeautifulSoup 텍스트 추출."""
    from bs4 import BeautifulSoup

    async with httpx.AsyncClient(follow_redirects=True, timeout=FETCH_TIMEOUT) as client:
        resp = client.build_request("GET", url, headers={"User-Agent": "SMAgentLab/1.0"})
        try:
            r = await client.send(resp)
            r.raise_for_status()
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            raise _translate_httpx_error(e, service="웹") from e
        html = r.text

    soup = BeautifulSoup(html, "lxml")

    # 불필요한 태그 제거
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
        tag.decompose()

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    # 메인 콘텐츠 우선 추출 (article > main > body 순서)
    main = (
        soup.find("article")
        or soup.find("main")
        or soup.find(id=re.compile(r"content|main|body", re.I))
        or soup.find("body")
    )
    raw_text = _extract_text(main or soup)

    sections = _extract_heading_sections(main or soup)

    parsed = ParsedDocument(
        source_type="web",
        source_name=title or url,
        raw_text=raw_text,
        sections=sections,
        metadata={"url": url, "title": title},
    )
    logger.info("웹 크롤링 완료: %s (%d자)", url, len(raw_text))
    return parsed


# ── Confluence REST API ────────────────────────────────────────────────────────

async def _fetch_confluence(url: str, token: str) -> ParsedDocument:
    """Confluence URL → REST API → ParsedDocument."""
    from bs4 import BeautifulSoup

    base_url, page_id, space_key, title_hint = _parse_confluence_url(url)

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=FETCH_TIMEOUT, verify=False) as client:
            if page_id:
                api_url = f"{base_url}/rest/api/content/{page_id}?expand=body.storage,title,space,version"
                r = await client.get(api_url, headers=headers)
                r.raise_for_status()
                data = r.json()
                pages = [data]
            elif space_key and title_hint:
                # 공간 + 제목으로 검색
                api_url = f"{base_url}/rest/api/content"
                r = await client.get(api_url, headers=headers, params={
                    "spaceKey": space_key,
                    "title": title_hint,
                    "expand": "body.storage,title,version",
                    "limit": 1,
                })
                r.raise_for_status()
                pages = r.json().get("results", [])
                if not pages:
                    raise ValueError(f"Confluence 페이지를 찾을 수 없습니다: space={space_key}, title={title_hint}")
            else:
                raise ValueError(f"지원하지 않는 Confluence URL 형식: {url}")
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        raise _translate_httpx_error(e) from e

    page = pages[0]
    page_title = page.get("title", "Confluence Page")
    storage_html = page.get("body", {}).get("storage", {}).get("value", "")
    space_name = page.get("space", {}).get("name", "")

    soup = BeautifulSoup(prepare_storage_html(storage_html), "lxml")
    raw_text = _extract_text(soup)
    sections = _extract_heading_sections(soup)

    parsed = ParsedDocument(
        source_type="confluence",
        source_name=page_title,
        raw_text=raw_text,
        sections=sections,
        metadata={
            "url": url,
            # 제목 검색 경로는 URL에 page_id가 없어 응답의 id를 쓴다 — 재등록 시 같은 페이지
            # 판별(옛 버전 교체)의 키라 둘 다 채운다(2026-09-28)
            "page_id": page_id or page.get("id"),
            "space": space_name,
            "title": page_title,
            "version": page.get("version", {}).get("number"),
        },
    )
    logger.info("Confluence 페이지 수집 완료: %s (%d자)", page_title, len(raw_text))
    return parsed


# ── URL 파싱 헬퍼 ──────────────────────────────────────────────────────────────

def _is_confluence(url: str) -> bool:
    parsed = urlparse(url)
    return bool(_CONFLUENCE_PATTERNS.search(parsed.path)) or "atlassian.net" in parsed.netloc


def _parse_confluence_url(url: str) -> tuple[str, str | None, str | None, str | None]:
    """Confluence URL에서 (base_url, page_id, space_key, title) 추출."""
    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    qs = parse_qs(parsed.query)

    page_id: str | None = None
    space_key: str | None = None
    title_hint: str | None = None

    # /pages/viewpage.action?pageId=12345
    if "pageId" in qs:
        page_id = qs["pageId"][0]

    # /display/SPACEKEY/Page+Title
    elif "/display/" in parsed.path:
        parts = parsed.path.split("/display/", 1)[1].split("/", 1)
        space_key = parts[0]
        if len(parts) > 1:
            title_hint = parts[1].replace("+", " ").replace("-", " ")

    # /spaces/viewspace.action?key=SPACE → space overview (페이지 목록이므로 에러)
    elif "viewspace.action" in parsed.path and "key" in qs:
        raise ValueError(
            "Space 전체 URL은 지원하지 않습니다. 특정 페이지 URL을 입력해주세요.\n"
            "예: https://confl.sinc.co.kr/display/SPACE/페이지제목\n"
            "    https://confl.sinc.co.kr/pages/viewpage.action?pageId=12345"
        )

    # /rest/api/content/{id} 직접 입력
    elif "/rest/api/content/" in parsed.path:
        m = re.search(r"/rest/api/content/(\d+)", parsed.path)
        if m:
            page_id = m.group(1)

    return base_url, page_id, space_key, title_hint


# ── 텍스트 추출 헬퍼 ───────────────────────────────────────────────────────────

# HTML → 섹션 (2026-10-07 재작성 — 적재 파이프라인 감사). 예전 결함:
#  - 코드 매크로(<ac:plain-text-body><![CDATA[SQL…]]>)가 섹션·raw_text 양쪽에서 사라짐 — lxml이 CDATA를 버림
#  - h5/h6 제목이 사라지고 본문이 앞 섹션에 붙음, dt/dd·div 직속 텍스트가 섹션에서 빠짐
#  - 표가 칸마다 한 줄로 풀려 행·열 관계가 사라짐
#  - raw_text는 바깥 div가 전체를 한 줄로 먼저 삼키고 제목이 뒤에 붙어 순서가 깨짐
# 이제 문서 순서대로 한 번만 훑는다: 제목(h1~h6) / 표(행마다 "| a | b |") / 코드(``` 블록) / 단락성 태그 / div 직속 글자.
_HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")
_CONTENT_TAGS = ("p", "li", "pre", "blockquote", "dt", "dd")
_CDATA_RE = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.S)


def prepare_storage_html(html: str) -> str:
    """컨플루언스 storage HTML 전처리 — CDATA(코드 매크로 본문)를 글자로 살리고 코드 본문을 <pre>로."""
    import html as _html
    html = _CDATA_RE.sub(lambda m: _html.escape(m.group(1)), html or "")
    return (html.replace("<ac:plain-text-body>", "<pre>").replace("</ac:plain-text-body>", "</pre>"))


def _inside(element, names, stop) -> bool:
    return any(p.name in names for p in element.parents if p is not stop and p is not None)


def _is_data_table(table) -> bool:
    """제목(h1~h6)을 품은 표는 화면 배치용(레이아웃) 표 — 행으로 펼치지 않고 안쪽을 평소처럼 읽는다(리뷰 2026-10-07:
    옛 페이지·웹에서 표로 배치한 섹션의 제목이 표 한 행으로 뭉개졌다)."""
    return table.find(list(_HEADINGS)) is None


def _cell_text(cell) -> str:
    # 칸 안 줄바꿈(코드 등)과 "|"는 표 한 행을 깨므로 공백·이스케이프로
    return re.sub(r"\s*\n\s*", " ", cell.get_text(" ", strip=True)).replace("|", "\\|")


def _table_lines(table) -> list[str]:
    rows = []
    for tr in table.find_all("tr"):
        if tr.find_parent("table") is not table:
            continue   # 표 안의 표는 그 표에서
        cells = [_cell_text(c) for c in tr.find_all(["th", "td"]) if c.find_parent("tr") is tr]
        if any(cells):
            rows.append("| " + " | ".join(cells) + " |")
    return rows


def _wrap_div_text(tag) -> None:
    """div 직속 글자를 <p>로 감싸 문서 순서를 지킨다(리뷰: "<div><p>첫째</p>둘째</div>"가 "둘째, 첫째"로 뒤집혔다)."""
    from bs4 import BeautifulSoup, NavigableString
    root = tag if isinstance(tag, BeautifulSoup) else next((p for p in tag.parents if isinstance(p, BeautifulSoup)), None)
    if root is None:
        return
    for div in tag.find_all("div"):
        for child in list(div.children):
            if child.__class__ is NavigableString and str(child).strip():   # 주석·CDATA 등 하위 클래스는 제외
                p = root.new_tag("p")
                p.string = str(child).strip()
                child.replace_with(p)


def _walk_blocks(tag):
    """문서 순서대로 ("heading", level, text) / ("text", 0, text) 를 낸다."""
    _wrap_div_text(tag)
    in_data_table = lambda el: any(p.name == "table" and _is_data_table(p) for p in el.parents if p is not tag and p is not None)
    for el in tag.find_all(list(_HEADINGS) + list(_CONTENT_TAGS) + ["table", "div"]):
        if el.name == "table":
            if not _is_data_table(el) or in_data_table(el) or _inside(el, _CONTENT_TAGS, tag):
                continue   # 레이아웃 표는 안쪽을 평소처럼, 데이터 표 안의 표·단락 안의 표는 바깥에서 한 번에
            lines = _table_lines(el)
            if lines:
                yield ("text", 0, "\n".join(lines))
            continue
        if in_data_table(el):
            continue   # 데이터 표 안 요소는 표에서 한 번에
        if el.name in _HEADINGS:
            t = el.get_text(" ", strip=True)
            if t:
                yield ("heading", int(el.name[1]), t)
            continue
        if el.name == "div":
            # 다른 태그로 감싸지 않은 div 직속 글자만(나머지는 안쪽 태그가 낸다)
            own = " ".join(str(x).strip() for x in el.find_all(string=True, recursive=False) if str(x).strip())
            if own and not _inside(el, _CONTENT_TAGS, tag):
                yield ("text", 0, own)
            continue
        if _inside(el, _CONTENT_TAGS, tag):
            continue   # 같은 계열 부모가 이미 포함
        if el.name == "pre":
            code = el.get_text().strip("\n")
            if code.strip():
                yield ("text", 0, "```\n" + code + "\n```")
            continue
        t = el.get_text(" ", strip=True)
        if t:
            yield ("text", 0, t)


def _extract_heading_sections(tag) -> list[dict]:
    """헤딩(h1~h6) 기반 섹션 — 표·코드·dt/dd·div 직속 글자 포함, 문서 순서 유지."""
    sections: list[dict] = []
    title, level, lines = "", 0, []
    for kind, lv, text in _walk_blocks(tag):
        if kind == "heading":
            if lines or title:
                sections.append({"title": title, "content": "\n".join(lines).strip(), "level": level})
            title, level, lines = text, lv, []
        else:
            lines.append(text)
    if lines or title:
        sections.append({"title": title, "content": "\n".join(lines).strip(), "level": level})
    return sections


def _extract_text(tag) -> str:
    """BS4 태그 → 문서 순서 그대로의 텍스트(제목은 "## 제목"). 섹션 추출과 같은 순회를 써서 둘이 어긋나지 않는다."""
    out = []
    for kind, _lv, text in _walk_blocks(tag):
        out.append(f"\n## {text}\n" if kind == "heading" else text)
    raw = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", raw).strip()


# ── Confluence 자손 페이지 트리 ───────────────────────────────────────────────

async def fetch_confluence_tree(
    url: str,
    token: str,
    *,
    max_depth: int = TREE_DEFAULT_DEPTH,
    max_pages: int = TREE_DEFAULT_MAX_PAGES,
) -> dict:
    """입력 URL을 root로 하여 하위 페이지 트리 메타데이터 반환 (본문 fetch 안 함).

    Returns:
        {
            "root": {"page_id": "...", "title": "...", "url": "..."},
            "tree": [
                {"page_id": "...", "title": "...", "url": "...", "depth": 0, "parent_id": None},
                {"page_id": "...", "title": "...", "url": "...", "depth": 1, "parent_id": "root_id"},
                ...
            ],
            "truncated": False,         # max_pages 초과 시 True
            "max_depth_reached": False, # 깊이 도달로 일부 자손이 잘렸을 때 True
        }
    """
    max_depth = max(1, min(max_depth, TREE_MAX_DEPTH))
    max_pages = max(1, min(max_pages, TREE_HARD_MAX_PAGES))

    base_url, page_id, space_key, title_hint = _parse_confluence_url(url)
    if not page_id and not (space_key and title_hint):
        raise ValueError(f"지원하지 않는 Confluence URL 형식: {url}")

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=FETCH_TIMEOUT, verify=False) as client:
            # 1) root page_id 확정
            if not page_id:
                r = await client.get(
                    f"{base_url}/rest/api/content",
                    headers=headers,
                    params={"spaceKey": space_key, "title": title_hint, "limit": 1},
                )
                r.raise_for_status()
                results = r.json().get("results", [])
                if not results:
                    raise ValueError(f"Confluence 페이지를 찾을 수 없습니다: space={space_key}, title={title_hint}")
                root_data = results[0]
                page_id = root_data["id"]
            else:
                r = await client.get(
                    f"{base_url}/rest/api/content/{page_id}",
                    headers=headers,
                    params={"expand": "title"},
                )
                r.raise_for_status()
                root_data = r.json()

            root_title = root_data.get("title", f"Page {page_id}")
            root_url = _build_page_url(base_url, page_id, root_data)

            # 2) BFS 자손 탐색
            tree: list[dict] = [
                {"page_id": page_id, "title": root_title, "url": root_url, "depth": 0, "parent_id": None},
            ]
            seen: set[str] = {page_id}
            truncated = False
            max_depth_reached = False

            # (page_id, depth) 큐
            queue: list[tuple[str, int]] = [(page_id, 0)]
            while queue:
                parent_id, depth = queue.pop(0)
                if depth >= max_depth:
                    # 더 깊이 탐색 안 함 — 다만 자손이 있는지는 알 수 없으니 일단 표시
                    continue
                children = await _fetch_confluence_children(client, base_url, headers, parent_id)
                for child in children:
                    cid = str(child["id"])
                    if cid in seen:
                        continue
                    seen.add(cid)
                    if len(tree) >= max_pages:
                        truncated = True
                        break
                    tree.append({
                        "page_id": cid,
                        "title": child.get("title", f"Page {cid}"),
                        "url": _build_page_url(base_url, cid, child),
                        "depth": depth + 1,
                        "parent_id": parent_id,
                    })
                    queue.append((cid, depth + 1))
                if truncated:
                    break
                if depth + 1 >= max_depth and children:
                    max_depth_reached = True
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        raise _translate_httpx_error(e) from e

    logger.info(
        "Confluence 트리 수집: root=%s, total=%d, depth_limit=%d, truncated=%s",
        page_id, len(tree), max_depth, truncated,
    )
    return {
        "root": tree[0],
        "tree": tree,
        "truncated": truncated,
        "max_depth_reached": max_depth_reached,
        "max_depth": max_depth,
        "max_pages": max_pages,
    }


async def _fetch_confluence_children(
    client: httpx.AsyncClient, base_url: str, headers: dict, parent_id: str,
) -> list[dict]:
    """단일 페이지의 직접 자식 페이지 목록 반환 (페이지네이션 처리)."""
    results: list[dict] = []
    start = 0
    limit = 100
    while True:
        r = await client.get(
            f"{base_url}/rest/api/content/{parent_id}/child/page",
            headers=headers,
            params={"limit": limit, "start": start, "expand": "title"},
        )
        if r.status_code == 404:
            return []
        r.raise_for_status()
        data = r.json()
        batch = data.get("results", [])
        results.extend(batch)
        if len(batch) < limit:
            break
        start += limit
        if start >= TREE_HARD_MAX_PAGES:
            break
    return results


def _build_page_url(base_url: str, page_id: str, page_data: dict) -> str:
    """페이지 메타데이터에서 사용자 친화적 URL 생성. tinyui/webui 우선."""
    links = page_data.get("_links", {}) if isinstance(page_data, dict) else {}
    webui = links.get("webui")
    if webui:
        return urljoin(base_url + "/", webui.lstrip("/"))
    return f"{base_url}/pages/viewpage.action?pageId={page_id}"


# ── Confluence 단일 페이지 (page_id 기반, bulk 인제스천용) ──────────────────

async def fetch_confluence_by_id(base_url: str, page_id: str, token: str) -> ParsedDocument:
    """page_id 기반 단일 페이지 fetch — 트리 선택 후 bulk 인제스천에서 호출."""
    from bs4 import BeautifulSoup

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    base = base_url.rstrip("/")

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=FETCH_TIMEOUT, verify=False) as client:
            r = await client.get(
                f"{base}/rest/api/content/{page_id}",
                headers=headers,
                params={"expand": "body.storage,title,space,version,_links,ancestors"},
            )
            r.raise_for_status()
            page = r.json()
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        raise _translate_httpx_error(e) from e

    page_title = page.get("title", f"Page {page_id}")
    storage_html = page.get("body", {}).get("storage", {}).get("value", "")
    space_name = page.get("space", {}).get("name", "")
    page_url = _build_page_url(base, page_id, page)
    page_version = page.get("version", {}).get("number")
    # ancestors: REST API가 루트→직계상위 순으로 반환 — 마지막 원소가 직계 상위 페이지.
    # 카테고리 자동화(2026-09-22, docs/tech/knowledge-category-automation.md)의 1순위
    # 신호 — space명은 팀 전체가 스페이스 하나에 몰려있어 너무 굵다는 게 실측 확인됨,
    # 직계 상위 페이지 제목이 실제 업무구분과 훨씬 가깝다(예: "외부서비스" > "배달의민족").
    ancestors = page.get("ancestors") or []
    parent_title = ancestors[-1].get("title") if ancestors else None

    soup = BeautifulSoup(prepare_storage_html(storage_html), "lxml")
    raw_text = _extract_text(soup)
    sections = _extract_heading_sections(soup)

    return ParsedDocument(
        source_type="confluence",
        source_name=page_title,
        raw_text=raw_text,
        sections=sections,
        metadata={
            "url": page_url,
            "page_id": page_id,
            "space": space_name,
            "title": page_title,
            "version": page_version,
            "parent_title": parent_title,
        },
    )
