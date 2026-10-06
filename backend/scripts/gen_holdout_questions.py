"""검증 전용(holdout) 질문 후보 생성 (2026-10-06, v2.128) — 골든셋 89문항 과적합 확인용.

57→88% 개선을 전부 같은 89문항으로 측정해 새 질문에서의 성능을 모른다. 튜닝에 안 쓴 정책 항목에서 LLM이 질문을 만들고 사람이
거른다(사용자 결정 2026-10-06). 원문 표현을 그대로 베끼면 검색 점수가 부풀려지므로 "현업이 상황을 말하듯, 원문 단어를 피해서"
만들게 하고, 원문과 겹치는 정도(가장 긴 공통 구간)를 함께 적어 사람이 거를 때 본다.

  출력(둘 다 tests/fixtures/golden_set/ — 원문 일부가 들어 있어 git 제외 폴더):
    holdout_v1_candidates.jsonl  골든셋과 같은 형식(type/query/source/expected_answer/qid) + overlap
    holdout_v1_review.md         사람이 거를 표 — 남길 행의 qid를 holdout_v1.jsonl로 옮기면 확정(튜닝 금지)

실행(컨테이너 안): python scripts/gen_holdout_questions.py [--per-ns 20] [--seed 7]
"""
import argparse
import asyncio
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, "/app")

from core.database import init_pool, get_conn  # noqa: E402
from shared.json_utils import parse_json_array  # noqa: E402
from service.policy import track2  # noqa: E402

OUT_DIR = Path("/app/tests/fixtures/golden_set")
BATCH = 5

SYSTEM = (
    "너는 사내 정책서를 처음 보는 현업 담당자다. 정책 항목마다, 실제로 채팅창에 물어볼 법한 질문 1개와 그 정책 기준의 짧은 정답을 쓴다.\n"
    "규칙: 정책명·본문의 표현을 그대로 베끼지 말고 상황을 말하듯 바꿔 묻는다(예: '반품 배송비 부담 주체' → '고객이 마음이 바뀌어 "
    "돌려보내면 택배비는 누가 내?'). 한 질문에 한 가지만. 정답은 본문에 있는 내용만으로 1~2문장. "
    '설명 없이 JSON 배열만: [{"id": 항목번호, "question": "...", "answer": "..."}]'
)


def _lcs_len(a: str, b: str) -> int:
    """가장 긴 공통 연속 구간 길이(공백 제외) — 질문이 원문을 얼마나 베꼈는지."""
    a, b = "".join(a.split()), "".join(b.split())
    best, prev = 0, [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-ns", type=int, default=20)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    await init_pool()
    from service.llm.factory import get_llm_provider
    llm = get_llm_provider()

    golden = [json.loads(line) for line in track2._GOLDEN_SET_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    used = {(g["source"].get("file"), g["source"].get("sheet"), g["source"].get("row")) for g in golden}
    rng = random.Random(args.seed)
    candidates, review = [], []
    async with get_conn() as conn:
        for needle, ns in track2._FILE_TO_NAMESPACE:
            rows = await conn.fetch(
                "SELECT i.id, i.source_file, i.source_sheet, i.source_row, i.policy_name, i.raw_body, i.category_path, "
                "EXISTS (SELECT 1 FROM policy_param p WHERE p.policy_item_id = i.id) AS has_param "
                "FROM policy_item i JOIN ops_namespace n ON n.id = i.namespace_id "
                "WHERE n.name = $1 AND i.status NOT IN ('deprecated','rejected') AND length(coalesce(i.raw_body,'')) >= 20",
                ns)
            pool = [r for r in rows if (r["source_file"], r["source_sheet"], r["source_row"]) not in used]
            # 시트 고르게 — 시트별로 섞어 돌아가며 뽑는다(한 시트 몰림 방지)
            by_sheet: dict[str, list] = {}
            for r in pool:
                by_sheet.setdefault(r["source_sheet"], []).append(r)
            for v in by_sheet.values():
                rng.shuffle(v)
            picked, sheets = [], sorted(by_sheet)
            while len(picked) < args.per_ns and any(by_sheet.values()):
                for s in sheets:
                    if by_sheet[s] and len(picked) < args.per_ns:
                        picked.append(by_sheet[s].pop())
            print(f"[{ns}] 후보 풀 {len(pool)} (골든 제외) → {len(picked)}개 생성 중...", flush=True)
            for i in range(0, len(picked), BATCH):
                batch = picked[i:i + BATCH]
                prompt = "\n\n".join(
                    f"[항목 {j + 1}] 분류: {' > '.join(r['category_path'] or [])} | 정책명: {r['policy_name']}\n본문: {r['raw_body'][:600]}"
                    for j, r in enumerate(batch))
                try:
                    arr = parse_json_array(await llm.generate_once(prompt, system=SYSTEM))
                except Exception as e:
                    print(f"  배치 실패(건너뜀): {e}", flush=True)
                    continue
                for obj in arr:
                    try:
                        r = batch[int(obj["id"]) - 1]
                    except (KeyError, ValueError, IndexError, TypeError):
                        continue
                    q, a = str(obj.get("question") or "").strip(), str(obj.get("answer") or "").strip()
                    if not q:
                        continue
                    qid = f"H{len(candidates) + 1:03d}"
                    ov = max(_lcs_len(q, r["policy_name"]), _lcs_len(q, r["raw_body"]))
                    candidates.append({
                        "qid": qid, "type": "param" if r["has_param"] else "narrative", "query": q,
                        "source": {"file": r["source_file"], "sheet": r["source_sheet"], "row": r["source_row"]},
                        "expected_answer": a, "overlap": ov,
                    })
                    review.append((qid, ns, r["source_sheet"], r["policy_name"], q, a, ov))
                print(f"  {min(i + BATCH, len(picked))}/{len(picked)}", flush=True)

    (OUT_DIR / "holdout_v1_candidates.jsonl").write_text(
        "\n".join(json.dumps(c, ensure_ascii=False) for c in candidates) + "\n", encoding="utf-8")
    lines = [
        "# 검증 전용 질문 후보 (holdout v1) — 사람이 거르기",
        "",
        "남길 행의 `남김` 칸에 O. 기준: ① 현업이 실제로 물을 법한가 ② 정답이 그 정책 하나로 정해지는가 ③ 원문을 베끼지 않았는가"
        "(`겹침` = 질문과 원문의 가장 긴 공통 구간 글자 수, 8 이상이면 베꼈을 가능성). 확정되면 이 문항으로는 튜닝하지 않는다.",
        "",
        "| 남김 | qid | 파트 | 시트 | 정책명 | 질문 | 정답(LLM) | 겹침 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for qid, ns, sheet, name, q, a, ov in review:
        esc = lambda s: str(s).replace("|", "/").replace("\n", " ")
        lines.append(f"|  | {qid} | {esc(ns)} | {esc(sheet)} | {esc(name)} | {esc(q)} | {esc(a)} | {ov} |")
    (OUT_DIR / "holdout_v1_review.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"후보 {len(candidates)}개 저장: {OUT_DIR}/holdout_v1_candidates.jsonl, holdout_v1_review.md")


if __name__ == "__main__":
    asyncio.run(main())
