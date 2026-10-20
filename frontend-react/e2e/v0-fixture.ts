import { execFileSync } from 'node:child_process';

// v0 방식(정책서 파트, 원문 통째 조각 1개 — 2026-10-20) 임시 정책을 만드는 E2E 공용 도구.
// 예전 테스트는 업무 파트에 엑셀로 정책을 올렸는데, v0에선 정책서 파트에 엑셀 업로드가 막혀 있고(service.py _ensure_excel_part)
// 업무 파트에서 질문하면 정책서 파트 14개를 함께 찾아 실데이터가 섞인다. 정책서 파트로 만들면 채팅 범위가 그 파트뿐이라 격리된다.

export function psql(sql: string): string {
  return execFileSync('docker', ['exec', 'ops-postgres', 'psql', '-U', 'ops', '-d', 'opsdb', '-tA', '-c', sql],
    { encoding: 'utf-8' }).trim();
}

export interface FixturePolicy { name: string; path: string[]; body: string; remark?: string }

// 적재기(scripts/load_policy_v0.py)와 같은 규칙으로 넣는다 — 머리 붙인 임베딩(chunk_embed_text), pipeline_version v0_whole
const INSERT_PY = `
import asyncio, json, os, sys
sys.path.insert(0, "/app")
async def main():
    from core.database import init_pool, get_conn
    from service.policy.service import chunk_embed_text
    from shared.embedding import embedding_service
    await init_pool(); embedding_service.load()
    async with get_conn() as c:
        nid = await c.fetchval("SELECT id FROM ops_namespace WHERE name = $1", os.environ["NS"])
        for it in json.loads(os.environ["ITEMS"]):
            e = await embedding_service.embed(chunk_embed_text(it["name"], it["path"], it["body"]))
            iid = await c.fetchval(
                "INSERT INTO policy_item (namespace_id, category_path, policy_name, raw_body, remark, content_hash, status, "
                "parse_status, pipeline_version) VALUES ($1,$2,$3,$4,$5,md5($4),$6,'parsed','v0_whole') RETURNING id",
                nid, it["path"], it["name"], it["body"], it.get("remark"), os.environ["STATUS"])
            await c.execute("INSERT INTO policy_chunk (policy_item_id, chunk_text, embedding, chunk_idx) VALUES ($1,$2,$3::vector,0)",
                            iid, it["body"], str(e))
asyncio.run(main())
`;

/** 임시 정책서 파트를 (다시) 만들고 정책을 넣는다. 지울 땐 dropPolicyPart. */
export function createPolicyPart(ns: string, items: FixturePolicy[], status: 'active' | 'pending_review' = 'active') {
  dropPolicyPart(ns);
  psql(`INSERT INTO ops_namespace (name, description, kind) VALUES ('${ns}', 'e2e', 'policy_test')`);   // 테스트 파트 — 다른 파트 채팅의 "정책 파트 전부"에 안 섞임(scope.py)
  execFileSync('docker', ['exec', '-i', '-e', `NS=${ns}`, '-e', `ITEMS=${JSON.stringify(items)}`, '-e', `STATUS=${status}`,
    'ops-backend', 'python', '-'], { input: INSERT_PY, encoding: 'utf-8' });
}

export function dropPolicyPart(ns: string) {
  // 한 트랜잭션으로 — 참조 테이블은 모두 ON DELETE CASCADE/SET NULL이라 파트 삭제로 충분하다(supersedes 자기참조만 먼저 끊음)
  const id = `(SELECT id FROM ops_namespace WHERE name='${ns}')`;
  psql(`BEGIN; UPDATE policy_item SET supersedes_id=NULL WHERE namespace_id=${id}; `
    + `UPDATE rag_knowledge SET supersedes_id=NULL WHERE namespace_id=${id}; DELETE FROM ops_namespace WHERE name='${ns}'; COMMIT;`);
}
