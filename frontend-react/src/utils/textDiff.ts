// 두 글의 줄 단위 차이(git diff처럼) — 승인 대기 검토에서 "신규 vs 기존"을 한눈에(11/03 태훈).
// 최장 공통 부분열(LCS). 조각은 수십~수백 줄이라 O(n·m)로 충분 — 너무 길면 비교를 포기하고 알린다.

export type DiffLine = { kind: 'same' | 'add' | 'del'; text: string };

export const DIFF_MAX_LINES = 1500;

/** before → after 의 줄 차이. 줄 수가 DIFF_MAX_LINES를 넘으면 null(화면에서 "너무 길어 비교 못 함") */
export function diffLines(before: string, after: string): DiffLine[] | null {
  const a = before.replace(/\r\n/g, '\n').split('\n');
  const b = after.replace(/\r\n/g, '\n').split('\n');
  if (a.length > DIFF_MAX_LINES || b.length > DIFF_MAX_LINES) return null;
  const n = a.length, m = b.length;
  // lcs[i][j] = a[i..], b[j..]의 공통 부분열 길이
  const lcs: Uint16Array[] = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }
  const out: DiffLine[] = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) { out.push({ kind: 'same', text: a[i] }); i++; j++; }
    else if (lcs[i + 1][j] >= lcs[i][j + 1]) out.push({ kind: 'del', text: a[i++] });
    else out.push({ kind: 'add', text: b[j++] });
  }
  while (i < n) out.push({ kind: 'del', text: a[i++] });
  while (j < m) out.push({ kind: 'add', text: b[j++] });
  return out;
}

/** 공백·줄바꿈만 다른지(정리하면 완전히 같은 글) */
export const sameText = (x: string, y: string) => x.replace(/\s+/g, ' ').trim() === y.replace(/\s+/g, ' ').trim();

// ── 좌우 나눠 보기(11/03 태훈 "위아래 말고 좌우로, diff 보이게") ──────────────────
// 왼쪽 = 기존, 오른쪽 = 신규. 연달아 지운 줄·추가된 줄은 한 줄씩 짝지어 같은 행에(고친 줄), 남는 쪽은 빈칸.

export type SplitRow = { left: string | null; right: string | null; kind: 'same' | 'change' | 'del' | 'add' };

export function splitRows(lines: DiffLine[]): SplitRow[] {
  const rows: SplitRow[] = [];
  let i = 0;
  while (i < lines.length) {
    if (lines[i].kind === 'same') { rows.push({ left: lines[i].text, right: lines[i].text, kind: 'same' }); i++; continue; }
    const dels: string[] = [], adds: string[] = [];
    while (i < lines.length && lines[i].kind !== 'same') {
      (lines[i].kind === 'del' ? dels : adds).push(lines[i].text); i++;
    }
    for (let k = 0; k < Math.max(dels.length, adds.length); k++) {
      const l = dels[k] ?? null, r = adds[k] ?? null;
      rows.push({ left: l, right: r, kind: l != null && r != null ? 'change' : l != null ? 'del' : 'add' });
    }
  }
  return rows;
}

/** 고친 줄 안에서 바뀐 낱말 — [글, 바뀜?] 조각들(공백 포함 토큰 단위 LCS) */
export function wordDiff(before: string, after: string): { left: [string, boolean][]; right: [string, boolean][]; gaveUp?: boolean } {
  const a = before.split(/(\s+)/).filter(Boolean), b = after.split(/(\s+)/).filter(Boolean);
  // 너무 긴 한 줄은 낱말 비교를 포기하고 줄 전체를 바뀜으로 — 화면에 "낱말 비교 생략"으로 알린다(리뷰 L6)
  if (a.length * b.length > 250_000) return { left: [[before, true]], right: [[after, true]], gaveUp: true };
  const n = a.length, m = b.length;
  const lcs: Uint16Array[] = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--)
    lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
  const left: [string, boolean][] = [], right: [string, boolean][] = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) { left.push([a[i], false]); right.push([b[j], false]); i++; j++; }
    else if (lcs[i + 1][j] >= lcs[i][j + 1]) left.push([a[i++], true]);
    else right.push([b[j++], true]);
  }
  while (i < n) left.push([a[i++], true]);
  while (j < m) right.push([b[j++], true]);
  return { left, right };
}
