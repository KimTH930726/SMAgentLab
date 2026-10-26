/**
 * 평가 게이트 층(문항 유형) 이름·합치기(v2.169) — 화면(GateRuns)과 테스트(e2e/eval-layers.spec.ts)가 같이 쓴다. React 없는 순수 함수.
 * 검색 파일은 목록을 list, 답 파일은 navigation으로 적는다(같은 18문항) — 화면에선 "목록" 하나로(사용자 10/23).
 */
export const LAYERS: { keys: string[]; name: string; hint: string }[] = [
  { keys: ['fact'], name: '사실', hint: '값·규칙 하나를 묻는 질문' },
  { keys: ['condition'], name: '조건', hint: '"3만 원 넘을 때"처럼 조건이 붙은 질문' },
  { keys: ['human'], name: '사람 문항', hint: '팀원이 직접 쓴 자연어 질문' },
  { keys: ['cross'], name: '영역 넘김', hint: '다른 업무 영역에서 물은 질문' },
  { keys: ['list', 'navigation'], name: '목록', hint: '한 분류를 다 보여 달라는 질문' },
  { keys: ['abstain'], name: '답 없음', hint: '정책서에 답이 없는 질문 — 거절이 정답' },
  { keys: ['real'], name: '실사용', hint: '정정 요청이 승인된 실제 채팅 질문 — 정답 = 고친 정책' },
];

/** 잠근 시험지(hold-out, 10/26) — 사람이 쓴 문항·실사용 문항. 고칠 때(진단·묶음·후보) 보지 않고 판정 때만 본다.
 *  서버 backend/service/eval/improve.py LOCKED_TYPES와 같은 값 */
export const LOCKED_TYPES = ['human', 'real'];

/** 잠근 층들의 정답률(문항 수 가중) — 층이 없으면 null */
export function lockedAccuracy(byType: Record<string, { n: number; accuracy: number }> | undefined): { n: number; accuracy: number } | null {
  const xs = LOCKED_TYPES.map((t) => byType?.[t]).filter((x): x is { n: number; accuracy: number } => !!x && x.n > 0);
  const n = xs.reduce((s, x) => s + x.n, 0);
  return n ? { n, accuracy: xs.reduce((s, x) => s + x.accuracy * x.n, 0) / n } : null;
}

/** 판정의 잠근 층 전/후(문항 수 가중) — 층별 차이 중 하나라도 내려가면 down */
export function lockedMove(groups: Record<string, { n: number; before: number; after: number; diff: number }> | null | undefined) {
  const xs = LOCKED_TYPES.map((t) => groups?.[t]).filter((x): x is { n: number; before: number; after: number; diff: number } => !!x && x.n > 0);
  const n = xs.reduce((s, x) => s + x.n, 0);
  if (!n) return null;
  const w = (k: 'before' | 'after') => xs.reduce((s, x) => s + x[k] * x.n, 0) / n;
  return { n, before: w('before'), after: w('after'), down: xs.some((x) => x.diff < 0) };
}

export const layerOf = (t: string) => LAYERS.find((l) => l.keys.includes(t));

/** 층별 행 — 같은 이름으로 합친다(목록 = list + navigation). 순서는 LAYERS, 모르는 층은 뒤에 그대로 */
export function mergeLayers<T>(byType: Record<string, T>, add: (a: T, b: T) => T): { name: string; hint: string; v: T }[] {
  const out: { name: string; hint: string; v: T }[] = [];
  for (const l of LAYERS) {
    const vs = l.keys.filter((k) => byType[k]).map((k) => byType[k]);
    if (vs.length) out.push({ name: l.name, hint: l.hint, v: vs.reduce(add) });
  }
  for (const [k, v] of Object.entries(byType)) if (!layerOf(k)) out.push({ name: k, hint: '', v });
  return out;
}

/** 전체 → 층 순서 → 모르는 층. DB(jsonb)는 키 순서를 지키지 않아 저장된 순서로 그리면 "전체"가 중간에 섞인다 */
export function groupOrder<T>(groups: Record<string, T>): [string, T][] {
  const rank = (g: string) => {
    if (g === '전체') return -1;
    const i = LAYERS.findIndex((l) => l.keys.includes(g));
    return i < 0 ? LAYERS.length : i;
  };
  return Object.entries(groups).sort(([a], [b]) => rank(a) - rank(b));
}
