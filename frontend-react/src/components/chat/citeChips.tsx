import type { ComponentPropsWithoutRef } from 'react';
import { clsx } from 'clsx';

/**
 * 답 본문의 근거 표식을 칩으로 그린다(11/12 시안 A안 — 번호 칩, Perplexity·Copilot 관례).
 * 서버가 만든 답 글자는 그대로 두고 화면에서만 바꾼다 — 옛 대화도 같은 규칙으로 그려진다.
 *  - `[#12]`·`[#12, #13]`·`[#12, 13]`·`[문서 #12]` → 번호 칩(숫자 = 아래 근거 카드 순서). 문맥에 없는 번호는 회색 "?"
 *  - `[정책]`·`[참고표]`·`[공통코드]`·`[DB]` → 종류 색 칩
 *  - `확인 안 됨` → 주황 배지(그 자리 그대로)
 * 표식 형식은 backend trustkit/answer/citation.py(CITE_IDS·UNVERIFIED)와 같다.
 */

export type CitePart =
  | { t: 'text'; v: string }
  | { t: 'doc'; ids: number[]; raw: string }
  | { t: 'kind'; kind: string; raw: string }
  | { t: 'unverified'; raw: string };

const MARK = /\[(?:문서\s*)?#\d+(?:\s*,\s*(?:문서\s*)?#?\d+)*\]|\[(정책|참고표|공통코드|DB)\]|확인 안 됨/g;

/** 글자를 표식과 일반 글로 나눈다 — 나눈 조각의 raw·v를 이으면 원래 글자와 같다(유실·추가 0). */
export function splitCites(text: string): CitePart[] {
  const out: CitePart[] = [];
  let last = 0;
  for (const m of text.matchAll(MARK)) {
    const at = m.index ?? 0;
    if (at > last) out.push({ t: 'text', v: text.slice(last, at) });
    const raw = m[0];
    if (raw.startsWith('[#') || raw.startsWith('[문서')) {
      out.push({ t: 'doc', ids: [...new Set([...raw.matchAll(/\d+/g)].map((d) => Number(d[0])))], raw });
    } else if (m[1]) {
      out.push({ t: 'kind', kind: m[1], raw });
    } else {
      out.push({ t: 'unverified', raw });
    }
    last = at + raw.length;
  }
  if (last < text.length) out.push({ t: 'text', v: text.slice(last) });
  return out;
}

// ── rehype 플러그인: sanitize "뒤"에 돌려 우리가 만든 span만 남긴다(본문 HTML은 이미 걸러진 뒤) ──
type HNode = { type: string; tagName?: string; value?: string; properties?: Record<string, unknown>; children?: HNode[] };
const SKIP = new Set(['code', 'pre', 'a']);

function partNode(p: CitePart): HNode {
  if (p.t === 'text') return { type: 'text', value: p.v };
  const data = p.t === 'doc' ? `doc:${p.ids.join(',')}` : p.t === 'kind' ? `kind:${p.kind}` : 'unverified';
  return { type: 'element', tagName: 'span', properties: { dataCite: data }, children: [{ type: 'text', value: p.raw }] };
}

function walk(node: HNode) {
  if (!node.children || (node.tagName && SKIP.has(node.tagName))) return;
  const next: HNode[] = [];
  for (const c of node.children) {
    if (c.type === 'text' && c.value) {
      const parts = splitCites(c.value);
      if (parts.length === 1 && parts[0].t === 'text') next.push(c);
      else next.push(...parts.map(partNode));
    } else {
      walk(c);
      next.push(c);
    }
  }
  node.children = next;
}

export function rehypeCiteChips() {
  return (tree: HNode) => walk(tree);
}

// ── 칩 그리기 ──
const CHIP = 'inline-flex items-center justify-center min-w-[18px] h-[18px] px-1.5 ml-0.5 rounded-full text-[11px] font-bold leading-none align-[2px] border border-transparent';
const KIND_STYLE: Record<string, string> = {
  정책: 'bg-violet-100 text-violet-700 dark:bg-violet-500/20 dark:text-violet-300',
  참고표: 'bg-teal-50 text-teal-700 dark:bg-teal-500/15 dark:text-teal-300',
};
/** 누를 수 있는 칩만 hover 테두리(리뷰 L2) */
const KIND_HOVER: Record<string, string> = {
  정책: 'hover:border-violet-500 dark:hover:border-violet-400',
  참고표: 'hover:border-teal-500 dark:hover:border-teal-400',
};
/** 공통코드·DB — "없는 번호 ?"(채운 회색)와 헷갈리지 않게 테두리만(리뷰 L3) */
const KIND_PLAIN = 'bg-transparent border-slate-500 text-slate-300';

export interface CiteContext {
  /** 근거 카드 순서대로의 운영 지식 id(카드 #1 = 0번) */
  docIds: number[];
  /** id → 마우스 올렸을 때 보일 한 줄 */
  docLabel: (id: number) => string;
  hasPolicy: boolean;
  onJump: (target: string) => void;
}

type SpanProps = ComponentPropsWithoutRef<'span'> & { node?: unknown; 'data-cite'?: string };

export function makeCiteSpan(ctx: CiteContext) {
  return function CiteSpan({ node: _node, ...props }: SpanProps) {
    const cite = props['data-cite'];
    if (!cite) return <span {...props} />;
    if (cite === 'unverified') {
      return (
        <span data-testid="cite-unverified" title="참고 문서에서 찾지 못한 부분이에요"
          className="inline-flex items-center gap-1 mx-0.5 px-1.5 h-[20px] rounded-md border text-[11.5px] font-bold align-[1px] border-amber-300 bg-amber-50 text-amber-700 dark:border-amber-500/40 dark:bg-amber-500/15 dark:text-amber-300">
          ⚠ 확인 안 됨
        </span>
      );
    }
    if (cite.startsWith('kind:')) {
      const kind = cite.slice(5);
      const jumpable = ctx.hasPolicy && (kind === '정책' || kind === '참고표');
      const cls = clsx(CHIP, KIND_STYLE[kind] ?? KIND_PLAIN);
      return jumpable ? (
        <button type="button" data-testid="cite-kind" className={clsx(cls, KIND_HOVER[kind], 'cursor-pointer')} title={`${kind} 근거 — 누르면 정책 근거로 이동`}
          onClick={() => ctx.onJump('p')}>{kind}</button>
      ) : (
        <span data-testid="cite-kind" className={cls} title={`${kind} 근거`}>{kind}</span>
      );
    }
    const ids = cite.slice(4).split(',').map(Number);
    return (
      <>
        {ids.map((id, i) => {
          const ix = ctx.docIds.indexOf(id);
          if (ix < 0) {
            return (
              <span key={i} data-testid="cite-unknown" title={`#${id} — 이 번호의 근거가 없어요`}
                aria-label={`근거 없는 번호 #${id}`} className={clsx(CHIP, 'bg-slate-700 text-slate-300')}>?</span>
            );
          }
          return (
            <button key={i} type="button" data-testid="cite-doc" title={ctx.docLabel(id)} aria-label={ctx.docLabel(id)} onClick={() => ctx.onJump(`k-${id}`)}
              className={clsx(CHIP, 'cursor-pointer bg-indigo-100 text-indigo-700 hover:border-indigo-500 dark:bg-indigo-500/20 dark:text-indigo-200 dark:hover:border-indigo-400')}>
              {ix + 1}
            </button>
          );
        })}
      </>
    );
  };
}

/** 이동한 카드를 1.5초 강조 — 클래스는 여기 글자로 있어야 Tailwind가 만든다 */
export const JUMP_HIGHLIGHT = ['ring-2', 'ring-indigo-500', 'dark:ring-indigo-400', 'rounded-xl'];
