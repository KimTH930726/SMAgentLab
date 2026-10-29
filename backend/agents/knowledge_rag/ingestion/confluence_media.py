"""컨플루언스 그림 처리 (2026-10-29, 정제 레이어 설계 §5-① — docs/domains/knowledge/sources/confluence/confluence-curation-layer-design.md).

예전엔 storage HTML의 그림(<ac:image>)·draw.io·gliffy 매크로가 표시 없이 버려졌다(글자 블록만 읽음). 파일럿 진단(10/29, 62페이지):
draw.io가 내용 페이지의 66%에 있고, 원본 XML의 도형·연결선 글이 본문보다 많았다(11,805자 vs 9,941자).
- draw.io: 같은 페이지 첨부의 원본(mxfile XML)에서 도형 이름과 연결(A → B)을 **코드로** 뽑아 매크로 자리에 글로 넣는다 — 그림을 해석하지
  않으니 지어내는 내용이 없다. 원본을 못 받으면 "[다이어그램: 이름 — 원문 페이지에서 확인]"(대체 처리를 보이게).
- 일반 그림: 자리에 "[이미지: 파일명 — 원문 페이지에서 확인]" — 답이 "그림에 있으니 원문을 보라"고 안내할 수 있게. 그림 내용은 읽지 않는다.
뽑은 글은 원장(rag_source_document.media)에 같이 남겨 다시 분할이 네트워크 없이 같은 결과를 낸다. 원문(raw_content)은 그대로.
"""
from __future__ import annotations

import base64
import html as _html
import logging
import re
import urllib.parse
import zlib
from typing import Optional

logger = logging.getLogger(__name__)

DIAGRAM_MACROS = ("drawio", "drawio-sketch", "inc-drawio")
DIAGRAM_TEXT_MAX = 4000   # 다이어그램 하나에서 넣는 글자 상한 — 넘으면 "…(이하 원문 페이지)"
# draw.io 매크로만 — 일반 매크로로 잡으면 접기(expand) 같은 바깥 매크로가 안쪽 draw.io 닫는 태그까지 삼켜 안쪽을 놓친다(draw.io 매크로 안엔 매크로가 없다)
_MACRO_RE = re.compile(r'<ac:structured-macro\b[^>]*ac:name="(' + "|".join(DIAGRAM_MACROS) + r')"[^>]*?(?:/>|>.*?</ac:structured-macro>)',
                       re.S | re.I)   # 스스로 닫힌 매크로(/>)는 거기서 끝 — 바깥 매크로 닫는 태그까지 삼키지 않게(리뷰 L7)
_IMAGE_RE = re.compile(r"<ac:image\b[^>]*/>|<ac:image\b.*?</ac:image>", re.S)
_PARAM_RE = re.compile(r'<ac:parameter\b[^>]*ac:name="([^"]+)"[^>]*>(.*?)</ac:parameter>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def _params(macro: str) -> dict:
    return {k: _html.unescape(_TAG_RE.sub("", v)).strip() for k, v in _PARAM_RE.findall(macro)}


def find_diagrams(storage: str) -> list[str]:
    """페이지의 draw.io 다이어그램 이름(첨부 파일 이름) — 문서 순서, 중복 없이."""
    out: list[str] = []
    for m in _MACRO_RE.finditer(storage or ""):
        name = _params(m.group(0)).get("diagramName")
        if name and name not in out:
            out.append(name)
    return out


def _clean(v: Optional[str], html_label: bool = True) -> str:
    """라벨 글. HTML 라벨(style html=1)만 태그를 지우고 문자 참조를 푼다 — 순수 글 라벨의 "값 < 10, > 5"가 태그로 보여 지워지던 것(리뷰 L5)."""
    t = v or ""
    if html_label:
        t = _html.unescape(_TAG_RE.sub(" ", t.replace("<br>", " ")))
    return re.sub(r"\s+", " ", t).strip()


def _is_html(el) -> bool:
    return "html=1" in (el.get("style") or "")


def _models(xml: str) -> list:
    from lxml import etree
    root = etree.fromstring(xml.encode("utf-8"))
    if root.tag == "mxGraphModel":
        return [root]
    models = []
    for d in root.iter("diagram"):
        inner = list(d)
        if inner:
            models.append(inner[0])
            continue
        raw = (d.text or "").strip()
        if raw:   # 압축 저장(deflate + base64 + URL 인코딩) — draw.io 기본 형식
            data = zlib.decompress(base64.b64decode(raw), -15)
            models.append(etree.fromstring(urllib.parse.unquote(data.decode("utf-8")).encode("utf-8")))
    return models


def diagram_text(xml: str) -> str:
    """draw.io 원본 XML → 글. 도형 이름(문서 순서, 중복 없이) + 연결 "A → B(연결선 글)". 결정적 — 같은 XML이면 같은 글."""
    names: list[str] = []
    flows: list[str] = []
    for m in _models(xml):
        label: dict[str, str] = {}
        cells = []
        for el in m.iter():
            if el.tag in ("object", "UserObject"):   # 속성 붙은 도형은 바깥 object가 id·label을 가진다
                inner = el.find("mxCell")
                label[el.get("id")] = _clean(el.get("label") or el.get("value"), _is_html(inner) if inner is not None else True)
                if inner is not None:
                    cells.append((el.get("id"), inner))
            elif el.tag == "mxCell" and el.getparent() is not None and el.getparent().tag not in ("object", "UserObject"):
                label[el.get("id")] = _clean(el.get("value"), _is_html(el))
                cells.append((el.get("id"), el))
        edges = {cid for cid, c in cells if c.get("edge") == "1"}
        for cid, c in cells:   # 연결선 위 글 상자(부모가 연결선인 도형)는 그 연결선의 글로(리뷰 L6) — draw.io 기본 방식
            p = c.get("parent")
            if c.get("vertex") == "1" and p in edges and label.get(cid):
                label[p] = (label.get(p) + " " if label.get(p) else "") + label[cid]
                label[cid] = ""
        for cid, c in cells:
            if c.get("vertex") == "1" and label.get(cid) and label[cid] not in names:
                names.append(label[cid])
        for cid, c in cells:
            if c.get("edge") != "1":
                continue
            a, b = label.get(c.get("source") or ""), label.get(c.get("target") or "")
            if a and b:
                e = label.get(cid)
                line = f"{a} → {b}" + (f" ({e})" if e else "")
                if line not in flows:
                    flows.append(line)
            elif label.get(cid) and label[cid] not in names:   # 끝이 안 붙은 연결선 글은 도형 이름처럼
                names.append(label[cid])
    lines = (["구성: " + " · ".join(names)] if names else []) + ["흐름: " + f for f in flows]
    text = "\n".join(lines)
    return text if len(text) <= DIAGRAM_TEXT_MAX else text[:DIAGRAM_TEXT_MAX] + "…(이하 원문 페이지에서 확인)"


def _p(text: str) -> str:
    return "".join(f"<p>{_html.escape(line)}</p>" for line in text.split("\n") if line.strip())


def apply_media(storage: str, diagrams: Optional[dict], *, diagram_text_on: bool, image_marks: bool) -> str:
    """파싱 전 storage HTML에 그림 자리 글을 넣는다(원장 원문은 그대로 — 파싱용 사본만). 둘 다 끄면 원래 동작(그림 버림)."""
    if not storage or not (diagram_text_on or image_marks):
        return storage

    def macro(m: re.Match) -> str:
        name = _params(m.group(0)).get("diagramName") or "이름 없음"
        body = (diagrams or {}).get(name) if diagram_text_on else None
        if body:
            return _p(f"[다이어그램: {name}]\n{body}")
        return _p(f"[다이어그램: {name} — 원문 페이지에서 확인]") if image_marks else m.group(0)

    def image(m: re.Match) -> str:
        tag = m.group(0)
        f = re.search(r'ri:filename="([^"]+)"', tag) or re.search(r'ri:value="([^"]+)"', tag)
        alt = re.search(r'ac:alt="([^"]+)"', tag)
        name = _html.unescape((alt or f).group(1)) if (alt or f) else "그림"
        return f"<p>{_html.escape(f'[이미지: {name} — 원문 페이지에서 확인]')}</p>"

    out = _MACRO_RE.sub(macro, storage)
    return _IMAGE_RE.sub(image, out) if image_marks else out


async def fetch_diagrams(client, base: str, page_id: str, names: list[str], headers: dict) -> dict:
    """같은 페이지 첨부에서 다이어그램 원본을 받아 글로. 실패한 것은 빠진다(자리엔 "원문에서 확인" — 로그 남김)."""
    if not names:
        return {}
    out: dict[str, str] = {}
    try:
        r = await client.get(f"{base}/rest/api/content/{page_id}/child/attachment", headers=headers, params={"limit": 500})
        r.raise_for_status()
        atts = {a.get("title"): a for a in r.json().get("results", [])}
    except Exception:   # noqa: BLE001
        logger.warning("다이어그램 첨부 목록 실패(page %s) — 그림 자리는 '원문에서 확인'으로", page_id, exc_info=True)
        return {}
    for name in names:
        a = atts.get(name) or atts.get(f"{name}.drawio")
        dl = (a or {}).get("_links", {}).get("download")
        if not dl:
            logger.info("다이어그램 원본 첨부 없음(page %s, %s)", page_id, name)
            continue
        try:
            f = await client.get(base + dl, headers=headers)
            f.raise_for_status()
            text = diagram_text(f.text)
            if text:
                out[name] = text
        except Exception:   # noqa: BLE001
            logger.warning("다이어그램 원본 읽기 실패(page %s, %s)", page_id, name, exc_info=True)
    return out
