/**
 * 줄 단위 비교(LCS) — 정정 요청 카드에서 "기존 vs 수정안"의 바뀐 줄을 칠하는 데 쓴다(10/21, 새 의존성 없이).
 * 결과는 두 쪽 정렬: same(양쪽 같음) / del(기존에만) / add(수정안에만). 두 글 다 수백 줄 이하라 O(n·m) 표로 충분하다
 * (정책 원문 최대 ~1,200자). 아주 긴 글은 표가 커지므로 상한을 넘으면 전체를 바뀐 줄로 본다(틀린 강조보다 과한 강조가 안전).
 */
export type DiffOp = { kind: 'same' | 'del' | 'add'; text: string };

const MAX_CELLS = 400_000;

export function lineDiff(before: string, after: string): DiffOp[] {
  const a = before.split('\n');
  const b = after.split('\n');
  if (a.length * b.length > MAX_CELLS) {
    return [...a.map((text) => ({ kind: 'del' as const, text })), ...b.map((text) => ({ kind: 'add' as const, text }))];
  }
  // lcs[i][j] = a[i..], b[j..]의 최장 공통 줄 수
  const lcs: number[][] = Array.from({ length: a.length + 1 }, () => new Array<number>(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }
  const out: DiffOp[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      out.push({ kind: 'same', text: a[i] });
      i++; j++;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      out.push({ kind: 'del', text: a[i++] });
    } else {
      out.push({ kind: 'add', text: b[j++] });
    }
  }
  while (i < a.length) out.push({ kind: 'del', text: a[i++] });
  while (j < b.length) out.push({ kind: 'add', text: b[j++] });
  return out;
}
