"""웹 크롤러 + Confluence REST API 어댑터 — URL → ParsedDocument 변환."""
import logging
import re
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
        r = await client.send(resp)
        r.raise_for_status()
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

    async with httpx.AsyncClient(follow_redirects=True, timeout=FETCH_TIMEOUT, verify=False) as client:
        if page_id:
            api_url = f"{base_url}/rest/api/content/{page_id}?expand=body.storage,title,space"
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
                "expand": "body.storage,title",
                "limit": 1,
            })
            r.raise_for_status()
            pages = r.json().get("results", [])
            if not pages:
                raise ValueError(f"Confluence 페이지를 찾을 수 없습니다: space={space_key}, title={title_hint}")
        else:
            raise ValueError(f"지원하지 않는 Confluence URL 형식: {url}")

    page = pages[0]
    page_title = page.get("title", "Confluence Page")
    storage_html = page.get("body", {}).get("storage", {}).get("value", "")
    space_name = page.get("space", {}).get("name", "")

    soup = BeautifulSoup(storage_html, "lxml")
    raw_text = _extract_text(soup)
    sections = _extract_heading_sections(soup)

    parsed = ParsedDocument(
        source_type="confluence",
        source_name=page_title,
        raw_text=raw_text,
        sections=sections,
        metadata={
            "url": url,
            "page_id": page_id,
            "space": space_name,
            "title": page_title,
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

def _extract_text(tag) -> str:
    """BS4 태그 → 줄바꿈 정리된 순수 텍스트."""
    lines = []
    for element in tag.descendants:
        if element.name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            text = element.get_text(" ", strip=True)
            if text:
                lines.append(f"\n## {text}\n")
        elif element.name in ("p", "li", "td", "th", "div") and not any(
            p.name in ("p", "li", "td", "th") for p in element.parents if p != tag
        ):
            text = element.get_text(" ", strip=True)
            if text:
                lines.append(text)
        elif element.name == "br":
            lines.append("")

    raw = "\n".join(lines)
    # 연속 공백줄 정리
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()


def _extract_heading_sections(tag) -> list[dict]:
    """헤딩 태그 기반 섹션 분리."""
    sections: list[dict] = []
    current_title = ""
    current_level = 0
    current_lines: list[str] = []

    for element in tag.find_all(["h1", "h2", "h3", "h4", "p", "li", "td"]):
        if element.name in ("h1", "h2", "h3", "h4"):
            if current_lines or current_title:
                sections.append({
                    "title": current_title,
                    "content": "\n".join(current_lines).strip(),
                    "level": current_level,
                })
            current_title = element.get_text(" ", strip=True)
            current_level = int(element.name[1])
            current_lines = []
        else:
            text = element.get_text(" ", strip=True)
            if text:
                current_lines.append(text)

    if current_lines or current_title:
        sections.append({
            "title": current_title,
            "content": "\n".join(current_lines).strip(),
            "level": current_level,
        })

    return sections


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

    async with httpx.AsyncClient(follow_redirects=True, timeout=FETCH_TIMEOUT, verify=False) as client:
        r = await client.get(
            f"{base}/rest/api/content/{page_id}",
            headers=headers,
            params={"expand": "body.storage,title,space,_links"},
        )
        r.raise_for_status()
        page = r.json()

    page_title = page.get("title", f"Page {page_id}")
    storage_html = page.get("body", {}).get("storage", {}).get("value", "")
    space_name = page.get("space", {}).get("name", "")
    page_url = _build_page_url(base, page_id, page)

    soup = BeautifulSoup(storage_html, "lxml")
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
        },
    )
