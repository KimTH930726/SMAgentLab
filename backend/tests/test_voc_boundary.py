"""VOC 레이어 경계(10/28) — VOC 창구(service/email_voc)는 지식을 코어 API(agents/knowledge_rag/knowledge/api.py)로만 쓴다.
정적 검사: ① 지식 에이전트 내부 모듈 import 금지(api만 허용) ② 지식·용어·정책 표(rag_*·policy_*)에 SQL 직접 금지(FROM·JOIN·INTO·UPDATE).
VOC를 별도 에이전트로 떼거나 코어를 바꿀 때 어디가 엮였는지 이 테스트가 바로 알려 준다(백로그 §0-3 완료 기준)."""
import ast
import re
from pathlib import Path

VOC = Path(__file__).resolve().parent.parent / "service" / "email_voc"
ALLOWED = {"agents.knowledge_rag.knowledge.api"}
# 스키마·따옴표가 붙은 이름도(public.rag_knowledge · "rag_knowledge") — 10/28 리뷰
SQL_TABLE = re.compile(r'\b(?:FROM|JOIN|INTO|UPDATE)\s+(?:\w+\.)?"?((?:rag|policy)_\w+)', re.IGNORECASE)


def _files():
    files = sorted(VOC.glob("*.py"))
    assert files, "VOC 모듈을 못 찾음"
    return files


def test_voc_imports_only_knowledge_api():
    bad = []
    for f in _files():
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):   # from agents.knowledge_rag.knowledge import api → …knowledge.api
                names = [node.module or ""] if (node.module or "") in ALLOWED else [f"{node.module}.{a.name}" for a in node.names]
            else:
                names = []
            for n in names:
                if n.startswith("agents.") and n not in ALLOWED:
                    bad.append(f"{f.name}:{node.lineno} {n}")
    assert not bad, f"VOC가 지식 코어 내부를 직접 import — knowledge.api로: {bad}"


def test_voc_has_no_direct_sql_on_core_tables():
    bad = []
    for f in _files():
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            # 문자열(SQL)만 본다 — 주석·docstring의 표 이름 언급은 괜찮다(docstring도 문자열이라 FROM/JOIN 같은 SQL 꼴일 때만 걸림)
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for m in SQL_TABLE.finditer(node.value):
                    bad.append(f"{f.name}:{node.lineno} {m.group(0)}")
    assert not bad, f"VOC가 코어 표에 SQL 직접 — knowledge.api로: {bad}"


def test_rule_catches_violations():
    """규칙 자체가 잡는지(빈 검사로 통과하지 않게) — 옛 VOC 코드 꼴."""
    assert SQL_TABLE.search("SELECT id FROM rag_knowledge k WHERE ...")
    assert SQL_TABLE.search("LEFT JOIN rag_knowledge k ON true")
    assert not SQL_TABLE.search("FROM ops_voc_cluster c")
    assert SQL_TABLE.search('SELECT 1 FROM public."rag_knowledge"') and SQL_TABLE.search("DELETE FROM policy_item")
    # import 규칙 — api는 되고 retrieval은 안 된다
    tree = ast.parse("from agents.knowledge_rag.knowledge import api\nfrom agents.knowledge_rag.knowledge import retrieval")
    mods = [f"{n.module}.{a.name}" for n in tree.body for a in n.names]
    assert [m in ALLOWED for m in mods] == [True, False]
