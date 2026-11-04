import { useMemo, useState } from 'react';
import { ChevronRight, MessageSquare } from 'lucide-react';
import type { ExploreItem } from '../../api/knowledge';

// 지식 검색 결과 카드(11/04 태훈 "검색해서 무슨 지식이 있나 보고 고치게, 카드형 + 필터·유사도") — 업계 패싯 검색 모양:
// 필터 옆에 건수, 카드엔 관련도·문서 위치·채널·수정일·채팅 근거 횟수·검색어 둘레 본문. 순서는 채팅 검색과 같다(서버 /knowledge/explore).

const CHANNEL: Record<string, string> = {
  confluence: '컨플루언스', confluence_bulk: '컨플루언스(일괄)', web: '웹', file_upload: '파일', paste_split: '붙여넣기', csv_import: 'CSV', manual: '직접 입력',
};
const channelOf = (it: ExploreItem) => CHANNEL[it.source_type ?? ''] ?? (it.source_type ? it.source_type : '직접 입력');

type Age = '7일' | '30일' | '그 전';
function ageOf(it: ExploreItem): Age {
  const t = Date.parse(it.updated_at ?? it.created_at ?? '');
  const days = Number.isFinite(t) ? (Date.now() - t) / 86_400_000 : Infinity;
  return days <= 7 ? '7일' : days <= 30 ? '30일' : '그 전';
}

// 관련도 색(11/04 태훈 "%에 따라 색을 다르게") — 35% 미만 = 채팅이 '근거 약함'으로 보는 선(settings.knowledge_min_score 0.35).
// 위 구간(50·70)은 눈으로 가르는 표시용 — 판정에 쓰지 않는다
const SCORE_BANDS: { min: number; label: string; cls: string }[] = [
  { min: 0.7, label: '높음', cls: 'bg-emerald-100 text-emerald-800 ring-emerald-300 dark:bg-emerald-500/15 dark:text-emerald-300 dark:ring-emerald-500/40' },
  { min: 0.5, label: '보통', cls: 'bg-indigo-100 text-indigo-800 ring-indigo-300 dark:bg-indigo-500/15 dark:text-indigo-300 dark:ring-indigo-500/40' },
  { min: 0.35, label: '낮음', cls: 'bg-amber-100 text-amber-900 ring-amber-300 dark:bg-amber-500/15 dark:text-amber-300 dark:ring-amber-500/40' },
  { min: -Infinity, label: '근거 약함', cls: 'bg-slate-700 text-slate-300 ring-slate-600' },
];
const bandOf = (s: number) => SCORE_BANDS.find((b) => s >= b.min)!;

function terms(q: string): string[] {
  return [...new Set(q.split(/[\s,.?!·()]+/).filter((w) => w.length >= 2))].sort((a, b) => b.length - a.length).slice(0, 6);
}

/** 검색어가 처음 나오는 곳 둘레 160자(없으면 맨 앞) — 제목 줄(#)은 위치 표시가 맡으니 본문만 */
function snippet(content: string, ts: string[]): string {
  const body = content.split('\n').filter((l) => !l.trim().startsWith('#')).join(' ').replace(/\s+/g, ' ').trim() || content;
  const low = body.toLowerCase();
  const at = ts.map((t) => low.indexOf(t.toLowerCase())).filter((i) => i >= 0).sort((a, b) => a - b)[0] ?? 0;
  const start = Math.max(0, at - 50);
  return (start > 0 ? '…' : '') + body.slice(start, start + 160) + (start + 160 < body.length ? '…' : '');
}

function Highlight({ text, ts }: { text: string; ts: string[] }) {
  if (!ts.length) return <>{text}</>;
  const re = new RegExp(`(${ts.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`, 'gi');
  return <>{text.split(re).map((p, i) => (i % 2 ? <mark key={i} className="rounded-sm bg-amber-200 px-0.5 text-inherit dark:bg-amber-500/30">{p}</mark> : <span key={i}>{p}</span>))}</>;
}

function Facet<T extends string>({ label, values: counted, value, onPick, testid }: {
  label: string; values: [T, number][]; value: T | null; onPick: (v: T | null) => void; testid: string;
}) {
  let values = counted;
  if (values.length < 2 && value === null) return null;   // 고를 게 하나뿐이면 필터가 의미 없다
  // 고른 값은 건수가 0이거나 상위 밖이어도 칩으로 — 안 그러면 풀 방법이 없다(리뷰)
  if (value !== null && !values.some(([v]) => v === value)) values = [[value, 0], ...values];
  return (
    <div role="group" aria-label={label} className="flex flex-wrap items-center gap-1.5" data-testid={testid}>
      <span className="text-[11px] text-slate-500 w-12 flex-shrink-0" aria-hidden>{label}</span>
      {values.map(([v, n]) => (
        <button key={v} type="button" aria-pressed={value === v} onClick={() => onPick(value === v ? null : v)}
          className={`rounded-full border px-2.5 py-0.5 text-xs ${value === v
            ? 'border-indigo-600 bg-indigo-600 text-white dark:border-indigo-500 dark:bg-indigo-500'
            : 'border-slate-700 text-slate-300 hover:bg-slate-800'}`}>
          {v} <span className="opacity-70 tabular-nums">{n}</span>
        </button>
      ))}
    </div>
  );
}

function count<T extends string>(items: ExploreItem[], f: (it: ExploreItem) => T, limit = 8): [T, number][] {
  const m = new Map<T, number>();
  items.forEach((it) => m.set(f(it), (m.get(f(it)) ?? 0) + 1));
  return [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, limit);
}

export function KnowledgeSearchCards({ query, mappedTerm, items, onEdit, canEdit, currentNs }: {
  query: string; mappedTerm: string | null; items: ExploreItem[]; onEdit: (it: ExploreItem) => void; canEdit: boolean; currentNs: string;
}) {
  const [channel, setChannel] = useState<string | null>(null);
  const [doc, setDoc] = useState<string | null>(null);
  const [age, setAge] = useState<Age | null>(null);
  const ts = useMemo(() => terms(query), [query]);
  const docOf = (it: ExploreItem) => it.source_file || '(문서 없음)';
  // 패싯 건수는 다른 필터를 적용한 뒤의 수 — 누르면 몇 건이 남는지 미리 보이게
  const pass = (it: ExploreItem, skip?: 'c' | 'd' | 'a') =>
    (skip === 'c' || !channel || channelOf(it) === channel) && (skip === 'd' || !doc || docOf(it) === doc) && (skip === 'a' || !age || ageOf(it) === age);
  const shown = items.filter((it) => pass(it));

  return (
    <div className="space-y-3" data-testid="explore-results">
      <div className="rounded-xl border border-slate-700 bg-slate-800/60 px-4 py-3 space-y-2">
        <p className="text-xs text-slate-400" title="채팅이 이 질문으로 근거를 찾을 때와 같은 검색(용어집 글자 매칭 → 벡터·키워드 가중 합, 연결된 컬렉션 포함)이에요. 순서는 채팅 순위(피드백 가중치 포함), 관련도 = 채팅이 '근거 있음'을 판정할 때 보는 점수.">
          채팅과 같은 검색 · <b className="text-slate-200 tabular-nums">{shown.length}</b>/{items.length}건
          {mappedTerm && <> · 용어집 <span className="text-indigo-600 dark:text-indigo-400">{mappedTerm}</span></>}
        </p>
        <Facet label="채널" values={count(items.filter((it) => pass(it, 'c')), channelOf)} value={channel} onPick={setChannel} testid="facet-channel" />
        <Facet label="문서" values={count(items.filter((it) => pass(it, 'd')), docOf, 6)} value={doc} onPick={setDoc} testid="facet-doc" />
        <Facet label="수정일" values={count(items.filter((it) => pass(it, 'a')), ageOf)} value={age} onPick={setAge} testid="facet-age" />
      </div>

      {shown.length === 0 && <div className="text-center py-12 text-slate-500">검색 결과가 없습니다.</div>}
      <div className="grid gap-2 md:grid-cols-2">
        {shown.map((it) => {
          const band = bandOf(it.score);
          const title = (it.content.split('\n').find((l) => l.trim()) ?? '').replace(/^#+\s*/, '');
          return (
            // 카드 전체를 누르면 수정 창(11/04 태훈 — [수정]만 누를 수 있어 불편). 수정 권한이 없으면 누를 수 없는 카드
            <article key={it.id} data-testid={`explore-card-${it.id}`}
              role={canEdit ? 'button' : undefined} tabIndex={canEdit ? 0 : undefined} aria-label={canEdit ? `${title} 수정` : undefined}
              onClick={canEdit ? () => onEdit(it) : undefined}
              onKeyDown={canEdit ? (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onEdit(it); } } : undefined}
              className={`group min-w-0 rounded-xl border border-slate-700 bg-slate-800 overflow-hidden transition-colors ${canEdit
                ? 'cursor-pointer hover:border-indigo-400 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 dark:hover:border-indigo-500' : ''}`}>
              {/* 머리: 관련도(색) · 문서 위치 · 컬렉션 · 채널 · 날짜 */}
              <div className="flex items-center gap-2 border-b border-slate-700/70 bg-slate-900/40 px-4 py-2 text-[11px]">
                <span className={`inline-flex flex-shrink-0 items-center gap-1 rounded-md px-1.5 py-0.5 ring-1 ${band.cls}`}
                  title={`관련도(채팅 근거 판정 점수) ${band.label} · 뜻 ${(it.v_score * 100).toFixed(0)}% · 키워드 ${(it.k_score * 100).toFixed(0)}% — 35% 미만은 채팅이 근거 약함으로 봐요`}>
                  <span className="font-mono font-semibold tabular-nums" data-testid={`explore-card-${it.id}-score`}>{(it.score * 100).toFixed(0)}%</span>
                  <span className="text-[10px] opacity-80">{band.label}</span>
                </span>
                <span className="min-w-0 flex-1 truncate text-slate-400" title={it.heading_path.join(' › ')}>
                  {it.heading_path.length ? it.heading_path.join(' › ') : (it.source_file ?? '')}
                </span>
                {it.namespace !== currentNs && (
                  <span className="flex-shrink-0 rounded px-1.5 py-0.5 bg-indigo-100 text-indigo-700 dark:bg-indigo-500/15 dark:text-indigo-300" title="연결된 컬렉션의 지식 — 채팅도 함께 찾아요">{it.namespace}</span>
                )}
                <span className="flex-shrink-0 rounded border border-slate-600 px-1.5 py-0.5 text-slate-300">{channelOf(it)}</span>
                <span className="flex-shrink-0 tabular-nums text-slate-500">{(it.updated_at ?? it.created_at ?? '').slice(0, 10)}</span>
              </div>
              {/* 본문: 제목(굵게) · 검색어 둘레(왼쪽 선으로 구분) */}
              <div className="space-y-2 px-4 py-3">
                <p className="truncate text-[15px] font-semibold text-slate-100">{title}</p>
                <p className="border-l-2 border-slate-600 pl-3 text-[13px] leading-6 text-slate-300 break-all"><Highlight text={snippet(it.content, ts)} ts={ts} /></p>
              </div>
              {/* 발: 번호 · 채팅 노출 · 등록자 · (수정 권한) 수정 안내 */}
              <div className="flex items-center gap-3 px-4 pb-2.5 text-[11px] text-slate-500">
                <span className="font-mono">#{it.id}</span>
                <span className="inline-flex items-center gap-1" title="최근 90일 채팅 검색 결과에 이 지식이 든 횟수(답 근거로 채택됐는지와는 별개)" data-testid={`explore-card-${it.id}-used`}>
                  <MessageSquare className="w-3 h-3" />채팅 노출 <b className={`tabular-nums ${it.used_count ? 'text-slate-100' : 'text-slate-400'}`}>{it.used_count}</b>회
                </span>
                {it.created_by_username && <span className="truncate">{it.created_by_username}</span>}
                {canEdit && (
                  <span className="ml-auto inline-flex items-center gap-0.5 text-slate-500 group-hover:text-indigo-600 dark:group-hover:text-indigo-400" aria-hidden>
                    수정<ChevronRight className="w-3 h-3" />
                  </span>
                )}
              </div>
            </article>
          );
        })}
      </div>
    </div>
  );
}
