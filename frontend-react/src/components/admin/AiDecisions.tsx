import { Fragment, useMemo, useState, type ReactNode } from 'react';
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { clsx } from 'clsx';
import {
  getDecisionLog, getDecisionsReport, listTechniques, setDecisionVerdict, type ChangedDecision, type DecisionLogItem, type DecisionsReport, type Technique,
} from '../../api/eval';
import { useAuthStore } from '../../store/useAuthStore';
import { showError } from '../../store/useDialogStore';
import { Modal } from '../ui/Modal';

/**
 * 품질 › AI 감사 로그 (v2.171 "AI 판단" → 10/27 이름·화면 바꿈, 사용자 "감사 로그 쪽이니까 — AI 업계 용어로").
 * 위: 종류마다 카드(큰 숫자 하나 + 한 줄 뜻). 카드를 누르면 아래 로그를 그 종류로 거르고, [기준·분포 ›]는 창(지금 기준 + 분포).
 * 아래: 감사 로그 — 날짜별 시간순 한 줄(언제 · AI가 한 것 · 사람·사용자가 한 것 · 기준), 사람이 바꾼 줄에 "AI가 틀렸나요?".
 * 감사 로그 관례(행위자·결과·기준을 한 줄에, 사람 변경이 핵심 증거, 같은 판단이 이어지면 접기, 종류·사람으로 거르기)를 따른다. 원문은 안 보낸다.
 */

const pct = (n: number, d: number) => (d ? `${Math.round((n / d) * 100)}%` : '-');
const fmtDate = (s: string) => new Date(s).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
const VERDICTS = [
  { key: 'wrong', label: 'AI가 틀림', cls: 'text-rose-600 dark:text-rose-400' },
  { key: 'context_changed', label: '상황이 바뀜', cls: 'text-amber-600 dark:text-amber-400' },
  { key: 'unknown', label: '모르겠음', cls: 'text-slate-400' },
] as const;
const PICK_NAME: Record<string, string> = { option: '선택지 고름', all: '전부 보기', typed: '직접 다시 물음', superseded: '다음 되묻기로 넘어감', other: '기타' };
const REASON_NAME: Record<string, string> = {
  resolved: '맞게 짚음(해결)', source_ok: '원문이 맞음', already_fixed: '이미 고쳐짐', duplicate: '중복', other: '기타',
};

type CardKey = 'location' | 'clarify' | 'source' | 'suggest' | 'candidates';

/** 카드마다 그 판단의 기준(답 기법 대장 항목) — 사용자 10/27 "어떤 기준으로 되묻는지, 판단 기준이 같이 있어야 개선 아이디어가 나온다" */
const CRITERIA: Record<CardKey, string[]> = {
  location: ['knowledge_location'], clarify: ['clarify', 'ref_clarify'], source: ['source_check'],
  suggest: ['collection_suggest'], candidates: ['improve_recommend'],
};

function Criteria({ items }: { items: Technique[] }) {
  if (!items.length) return null;
  return (
    <section className="rounded-lg border border-indigo-200 dark:border-indigo-500/30 bg-indigo-50/50 dark:bg-indigo-500/5 p-3 space-y-2" data-testid="decision-criteria">
      <h4 className="text-xs font-semibold text-indigo-700 dark:text-indigo-300">지금 기준</h4>
      {items.map((t) => (
        <div key={t.key} className="space-y-1">
          <p className="text-sm text-slate-100">{t.name}</p>
          <p className="text-xs text-slate-300 leading-relaxed">{t.does}</p>
          {t.values.length > 0 && (
            <p className="flex flex-wrap gap-1.5">
              {t.values.map((v) => (
                <span key={v.label} className="rounded px-1.5 py-0.5 text-xs bg-slate-800 border border-slate-600 text-slate-200"
                  title={v.setting ? `설정 ${v.setting}` : '코드에 정한 값'}>{v.label} <b>{v.value}</b></span>
              ))}
            </p>
          )}
          {t.evidence.length > 0 && <p className="text-xs text-slate-500">근거: {t.evidence[t.evidence.length - 1].date} · {t.evidence[t.evidence.length - 1].note}</p>}
        </div>
      ))}
      <p className="text-xs text-slate-500">값을 바꾸려면 평가 게이트 리포트의 고칠 방법에서 실험·측정 후 — 전체 목록은 품질 › 답 기법</p>
    </section>
  );
}

function Card({ id, title, big, tone, line, active, onFilter, onOpen }: {
  id: CardKey; title: string; big: string; tone?: 'bad' | 'good' | 'muted'; line: string; active: boolean; onFilter?: () => void; onOpen: () => void;
}) {
  const cls = tone === 'bad' ? 'text-rose-600 dark:text-rose-400' : tone === 'good' ? 'text-emerald-600 dark:text-emerald-400' : tone === 'muted' ? 'text-slate-500' : 'text-slate-100';
  return (
    <div className={clsx('rounded-xl border p-3 space-y-1 min-w-0 flex flex-col',
      active ? 'border-indigo-500 bg-indigo-50 ring-1 ring-indigo-500 dark:border-indigo-400 dark:bg-indigo-500/10 dark:ring-indigo-400' : 'border-slate-700 bg-slate-800')}>
      <button onClick={onFilter ?? onOpen} data-testid={`decision-card-${id}`} aria-pressed={onFilter ? active : undefined}
        title={onFilter ? '아래 감사 로그를 이 종류로 거르기' : '기준·분포 보기'} className="text-left space-y-1 flex-1">
        <span className="block text-xs text-slate-400">{title}</span>
        <span className={`block text-2xl font-semibold tabular-nums ${cls}`}>{big}</span>
        <span className="block text-xs text-slate-400 leading-relaxed">{line}</span>
      </button>
      <button onClick={onOpen} data-testid={`decision-detail-${id}`} className="self-start text-xs text-indigo-600 dark:text-indigo-400 hover:underline">기준·분포 ›</button>
    </div>
  );
}

const LOG_KIND: Partial<Record<CardKey, string>> = { location: 'knowledge_location', clarify: 'chat_clarify', source: 'source_check', suggest: 'collection_suggest' };
const KIND_LABEL: Record<string, string> = { knowledge_location: '등록 위치', chat_clarify: '되묻기', source_check: '원문 확인', collection_suggest: '다른 컬렉션 제안' };
const dayOf = (s: string) => new Date(s).toLocaleDateString('ko-KR', { month: 'long', day: 'numeric', weekday: 'short' });
const timeOf = (s: string) => new Date(s).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false });

/** 감사 로그 — 날짜별, 같은 날 같은 판단(종류·AI·결과가 같은 줄)이 이어지면 "같은 판단 n건"으로 접는다 */
function AuditLog({ kind, onClearKind }: { kind: CardKey | null; onClearKind: () => void }) {
  const [days, setDays] = useState(30);
  const [changedOnly, setChangedOnly] = useState(false);
  const [openRuns, setOpenRuns] = useState<Set<number>>(new Set());
  const logKind = kind ? LOG_KIND[kind] : undefined;
  const q = useInfiniteQuery({
    queryKey: ['ai-decision-log', days, logKind ?? '', changedOnly],
    queryFn: ({ pageParam }) => getDecisionLog({ days, kind: logKind, changedOnly, before: pageParam }),
    initialPageParam: null as number | null,
    getNextPageParam: (last) => last.next_before,
    staleTime: 30_000,
  });
  const items = useMemo(() => q.data?.pages.flatMap((p) => p.items) ?? [], [q.data]);
  // 접기: 바로 앞 줄과 날짜·종류·AI·결과가 같고 사람이 바꾸지 않은 줄이면 같은 묶음(바꾼 줄은 증거라 접지 않는다)
  const runs = useMemo(() => {
    const out: { head: DecisionLogItem; rest: DecisionLogItem[] }[] = [];
    for (const it of items) {
      const last = out[out.length - 1];
      const same = !!last && !it.changed && !last.head.changed && dayOf(last.head.at) === dayOf(it.at) && last.head.kind === it.kind
        && last.head.ai === it.ai && last.head.human === it.human && last.head.confident === it.confident
        && (last.head.changed_by ?? last.head.asked_by) === (it.changed_by ?? it.asked_by);   // 다른 사람 줄은 묶지 않는다(리뷰 L2)
      if (same) last.rest.push(it); else out.push({ head: it, rest: [] });
    }
    return out;
  }, [items]);
  const toggle = (id: number) => setOpenRuns((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const sel = 'bg-slate-800 border border-slate-600 rounded-md px-2 py-1 text-xs text-slate-200 focus:outline-none focus:border-indigo-500';
  return (
    <section className="space-y-3" data-testid="audit-log">
      <div className="flex flex-wrap items-center gap-3">
        <h3 className="text-sm font-semibold text-slate-100 mr-2">감사 로그</h3>
        {logKind && (
          <button onClick={onClearKind} className="rounded-full border border-indigo-400 px-2 py-0.5 text-xs text-indigo-600 dark:text-indigo-300">
            {KIND_LABEL[logKind] ?? logKind}만 ✕</button>
        )}
        <label className="flex items-center gap-1.5 text-xs text-slate-300">
          <input type="checkbox" checked={changedOnly} onChange={(e) => setChangedOnly(e.target.checked)} className="accent-indigo-600" />사람이 바꾼 것만
        </label>
        <select className={sel} value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="기간">
          {[7, 30, 90].map((d) => <option key={d} value={d}>최근 {d}일</option>)}
        </select>
      </div>
      {q.isLoading ? <p className="text-sm text-slate-500">불러오는 중…</p>
        : q.error ? <p className="text-sm text-rose-600 dark:text-rose-400">불러오지 못했습니다: {(q.error as Error).message}</p>
        : items.length === 0 ? <p className="text-sm text-slate-500">기록이 없어요.</p> : (
          <ol className="rounded-xl border border-slate-700 divide-y divide-slate-700/70">
            {runs.map((r, i) => {
              const newDay = i === 0 || dayOf(runs[i - 1].head.at) !== dayOf(r.head.at);
              const expanded = openRuns.has(r.head.id);
              return (
                <Fragment key={r.head.id}>
                  {newDay && <li className="px-4 py-1.5 text-xs font-medium text-slate-400 bg-slate-900/50">{dayOf(r.head.at)}</li>}
                  <LogRow it={r.head} />
                  {r.rest.length > 0 && (
                    <li className="px-4 py-1.5 pl-[5.25rem]">
                      <button className="text-xs text-slate-400 hover:text-slate-200" onClick={() => toggle(r.head.id)}>
                        {expanded ? '접기 ▾' : `같은 판단 ${r.rest.length}건 더 ▸`}
                      </button>
                    </li>
                  )}
                  {expanded && r.rest.map((x) => <LogRow key={x.id} it={x} />)}
                </Fragment>
              );
            })}
          </ol>
        )}
      {q.hasNextPage && (
        <button className="text-xs text-indigo-600 dark:text-indigo-400 hover:underline disabled:opacity-50" disabled={q.isFetchingNextPage}
          onClick={() => q.fetchNextPage()}>{q.isFetchingNextPage ? '불러오는 중…' : '더 보기'}</button>
      )}
    </section>
  );
}

function LogRow({ it }: { it: DecisionLogItem }) {
  const tip = [it.ai_reason, it.rule ? `기준: ${it.rule}` : null].filter(Boolean).join(' · ');
  return (
    <li className="px-4 py-2.5 grid grid-cols-[3.5rem_1fr] gap-x-3 gap-y-1 text-sm" data-testid={`log-${it.id}`}>
      <span className="text-xs text-slate-500 tabular-nums pt-0.5">{timeOf(it.at)}</span>
      <div className="min-w-0 space-y-1">
        <p className="flex flex-wrap items-center gap-1.5 min-w-0">
          <span className="rounded px-1.5 py-0.5 text-[11px] bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300">AI</span>
          <span className="rounded px-1.5 py-0.5 text-[11px] bg-slate-700 text-slate-300">{it.kind_name}</span>
          <span className="text-slate-100 break-words">{it.ai}</span>
          {it.confident && <span className="text-[11px] text-emerald-600 dark:text-emerald-400" title="AI 판단과 근거(이웃 등)가 맞아 확신 — 자동 후보">확신</span>}
          {tip && <span className="text-[11px] text-slate-500 cursor-help" title={tip}>ⓘ</span>}
        </p>
        <p className="flex flex-wrap items-center gap-1.5 text-xs">
          {it.open ? <span className="text-slate-500">아직 결과 없음</span> : (
            <>
              <span className={clsx('rounded px-1.5 py-0.5 text-[11px]', it.changed ? 'bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300' : 'bg-slate-700 text-slate-300')}>
                {it.changed ? '사람이 바꿈' : '사람'}</span>
              <span className="text-slate-300">{it.human}</span>
              {(it.changed_by || it.asked_by) && <span className="text-slate-500">· {it.changed_by ?? it.asked_by}</span>}
            </>
          )}
          {it.changed && <span className="ml-auto"><VerdictButtons r={it} /></span>}
        </p>
      </div>
    </li>
  );
}

/** 분포 막대 — 이름 · 막대 · 건수 */
function Bars({ rows }: { rows: { name: string; n: number; warn?: boolean }[] }) {
  const max = Math.max(1, ...rows.map((r) => r.n));
  if (!rows.some((r) => r.n)) return <p className="text-sm text-slate-500">아직 기록이 없어요.</p>;
  return (
    <div className="space-y-1.5">
      {rows.map((r) => (
        <div key={r.name} className="flex items-center gap-3 text-sm">
          <span className="w-36 shrink-0 text-slate-300">{r.name}</span>
          <span className="flex-1 h-2 rounded-full bg-slate-700 overflow-hidden">
            <span className={`block h-full ${r.warn ? 'bg-amber-500 dark:bg-amber-400' : 'bg-indigo-500 dark:bg-indigo-400'}`} style={{ width: `${(r.n / max) * 100}%` }} />
          </span>
          <span className="w-10 shrink-0 text-right tabular-nums text-slate-200">{r.n}</span>
        </div>
      ))}
    </div>
  );
}

function VerdictButtons({ r }: { r: Pick<ChangedDecision, 'id' | 'verdict' | 'verdict_reason' | 'changed_by'> }) {
  const qc = useQueryClient();
  const me = useAuthStore((s) => s.user?.username);
  const m = useMutation({
    mutationFn: (v: string) => setDecisionVerdict(r.id, v),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['ai-decisions'] }); qc.invalidateQueries({ queryKey: ['ai-decision-log'] }); },
    onError: (e) => showError(e, '답을 저장하지 못했어요'),
  });
  const mine = !!me && me === r.changed_by;
  if (r.verdict) {
    const v = VERDICTS.find((x) => x.key === r.verdict);
    return <span className={`text-xs ${v?.cls ?? ''}`}>{v?.label ?? r.verdict}{r.verdict_reason ? ` · ${r.verdict_reason}` : ''}</span>;
  }
  if (!mine) return <span className="text-xs text-slate-500" title="바꾼 사람만 답할 수 있어요">AI가 틀렸나요? — 답 없음 ⓘ</span>;
  return (
    <span className="flex flex-wrap items-center gap-2 text-xs">
      <span className="text-slate-400">AI가 틀렸나요?</span>
      {VERDICTS.map((v) => (
        <button key={v.key} disabled={m.isPending} onClick={() => m.mutate(v.key)}
          className="px-2 py-0.5 rounded border border-slate-600 text-slate-300 hover:border-indigo-500 dark:hover:border-indigo-400 disabled:opacity-50">{v.label}</button>
      ))}
    </span>
  );
}

function Detail({ open, d, techs }: { open: CardKey; d: DecisionsReport; techs: Technique[] }) {
  return (
    <div className="space-y-4">
      <Criteria items={CRITERIA[open].map((k) => techs.find((t) => t.key === k)).filter((t): t is Technique => !!t)} />
      <DetailBody open={open} d={d} />
    </div>
  );
}

function DetailBody({ open, d }: { open: CardKey; d: DecisionsReport }) {
  if (open === 'location') {
    const rows = d.changed.filter((r) => r.kind === 'knowledge_location');
    const s = d.kinds.knowledge_location;
    return (
      <div className="space-y-3">
        {s && <p className="text-sm text-slate-300">확정 {s.decided}건 중 AI 그대로 {s.decided - s.changed} · 사람이 바꿈 {s.changed}
          {s.auto_wrong > 0 && <span className="text-rose-600 dark:text-rose-400"> · 확신(자동 후보)인데 틀림 {s.auto_wrong} — 자동 반영 기준 점검</span>}</p>}
        {rows.length === 0 ? <p className="text-sm text-slate-500">사람이 바꾼 건이 없어요.</p> : (
          <ul className="divide-y divide-slate-700">
            {rows.map((r) => (
              <li key={r.id} className="py-2.5 space-y-1" data-testid={`changed-${r.id}`}>
                <p className="text-sm text-slate-200">AI <b>{r.ai.collection ?? '삭제된 컬렉션'}</b> <span className="text-slate-500">→</span> 사람 <b>{r.human.collection ?? '다른 곳'}</b></p>
                <p className="text-xs text-slate-500">{fmtDate(r.at)}{r.changed_by ? ` · ${r.changed_by}` : ''}{r.target ? ` · ${r.target}` : ''}</p>
                <p className="text-xs text-slate-400">AI 이유: {r.ai.reason ?? '(이유가 남지 않은 옛 기록)'}</p>
                <VerdictButtons r={r} />
              </li>
            ))}
          </ul>
        )}
      </div>
    );
  }
  if (open === 'clarify') {
    const part = (src: 'category' | 'ref', title: string) => (
      <section className="space-y-2">
        <h4 className="text-xs font-medium text-slate-400">{title}</h4>
        <Bars rows={['option', 'all', 'typed', 'superseded'].map((k) => ({ name: PICK_NAME[k], n: d.clarify[src][k] ?? 0, warn: k === 'all' || k === 'typed' }))} />
      </section>
    );
    return (
      <div className="space-y-4">
        <p className="text-xs text-slate-400 leading-relaxed">"전부 보기"가 많으면 괜히 물은 것, "직접 다시 물음"이 많으면 선택지가 안 맞은 것(노랑). 아직 답을 안 고른 되묻기 {d.clarify.open}건.</p>
        {part('category', '정책 분류 되묻기 — 목록이 클 때 "어느 부분을 볼까요?"')}
        {part('ref', '참고표 되묻기 — 결과를 정하는 조건이 빠졌을 때(참고표 연결을 켰을 때만)')}
      </div>
    );
  }
  if (open === 'source') {
    const by = d.source_checks.by_reason;
    return (
      <div className="space-y-4">
        <p className="text-xs text-slate-400">원문 확인 카드(적재 점검·골든셋 작성이 짚은 원문 문제)를 사람이 어떻게 결론 냈나 — "문제 없음"이 검사가 괜히 짚은 것.</p>
        <Bars rows={['resolved', 'source_ok', 'already_fixed', 'duplicate', 'other'].map((k) => ({ name: REASON_NAME[k], n: by[k] ?? 0, warn: k !== 'resolved' }))} />
        <section className="space-y-1">
          <h4 className="text-xs font-medium text-slate-400">검사 출처별</h4>
          {Object.entries(d.source_checks.by_source).map(([src, v]) => (
            <p key={src} className="text-sm text-slate-300">{src === 'v0_load_check' ? '적재 점검' : src === 'golden_set' ? '골든셋 작성' : src} — 오탐 {pct(v.no_issue, v.no_issue + v.resolved)} ({v.no_issue}/{v.no_issue + v.resolved})</p>
          ))}
        </section>
      </div>
    );
  }
  if (open === 'suggest') {
    const s = d.collection_suggest;
    return (
      <div className="space-y-2 text-sm text-slate-300">
        <p>지금 {s.enabled ? '켜짐' : '꺼짐'}(설정 chat.collection_suggest) · 제안 {s.total}건 · 눌러서 다시 물음 {s.picked}건</p>
        <p className="text-xs text-slate-400 leading-relaxed">답 아래 "○○ 지식에 더 맞는 내용이 있어요"를 띄운 수와 실제로 누른 수. 누름이 너무 적으면 괜한 제안, 누른 답에 신고가 많으면 하한을 다시 봅니다.</p>
      </div>
    );
  }
  const c = d.candidates;
  return (
    <div className="space-y-3">
      <p className="text-xs text-slate-400">평가 게이트 리포트의 "고칠 방법" — AI 추천(해 볼 만함·안 하는 게 좋음)을 사람이 따랐나. 반대가 많으면 추천 규칙이 사람 판단과 어긋난다는 신호.</p>
      <Bars rows={[{ name: '추천대로 결정', n: c.agree }, { name: '추천과 반대', n: c.disagree, warn: true }, { name: '아직 결정 전', n: c.by_status.proposed ?? 0 }]} />
    </div>
  );
}

export function AiDecisions() {
  const { data: d, isLoading, error } = useQuery({ queryKey: ['ai-decisions', 90], queryFn: () => getDecisionsReport(90), staleTime: 30_000 });
  const [open, setOpen] = useState<CardKey | null>(null);
  const [filter, setFilter] = useState<CardKey | null>(null);
  const { data: techs = [] } = useQuery({ queryKey: ['eval-techniques'], queryFn: listTechniques, staleTime: 60_000 });
  if (isLoading) return <p className="text-sm text-slate-500">불러오는 중…</p>;
  if (error) return <p className="text-sm text-rose-600 dark:text-rose-400">불러오지 못했습니다: {(error as Error).message}</p>;
  if (!d) return null;
  const loc = d.kinds.knowledge_location;
  const cat = d.clarify.category;
  const asked = (cat.option ?? 0) + (cat.all ?? 0) + (cat.typed ?? 0);
  const waste = (cat.all ?? 0) + (cat.typed ?? 0);
  const sc = d.source_checks.by_reason;
  const resolved = sc.resolved ?? 0;
  const noIssue = Object.entries(sc).filter(([k]) => k !== 'resolved').reduce((a, [, n]) => a + n, 0);
  const cs = d.collection_suggest;
  const cand = d.candidates;
  const titles: Record<CardKey, string> = {
    location: '지식 등록 위치', clarify: '채팅 되묻기', source: '원문 확인 검사', suggest: '다른 컬렉션 제안', candidates: '개선 후보 — AI 추천',
  };
  const cards: { id: CardKey; big: string; tone?: 'bad' | 'good' | 'muted'; line: string }[] = [
    { id: 'location', big: loc && loc.decided ? pct(loc.decided - loc.changed, loc.decided) : '-', tone: loc?.auto_wrong ? 'bad' : undefined,
      line: loc ? `AI가 고른 곳 그대로 · 사람이 바꿈 ${loc.changed}건${loc.auto_wrong ? ` · 확신인데 틀림 ${loc.auto_wrong}` : ''}` : '아직 기록 없음' },
    { id: 'clarify', big: asked ? pct(waste, asked) : '-', tone: asked && waste / asked > 0.5 ? 'bad' : undefined,
      line: `괜히 물은 비율(전부 보기·직접 다시 물음) · 되물음 ${asked}건${d.clarify.open ? ` · 답 안 고름 ${d.clarify.open}` : ''}` },
    { id: 'source', big: resolved + noIssue ? pct(noIssue, resolved + noIssue) : '-',
      line: `검사가 괜히 짚은 비율(문제 없음) · 결론 ${resolved + noIssue}건` },
    { id: 'suggest', big: cs.enabled ? (cs.total ? pct(cs.picked, cs.total) : '0건') : '꺼짐', tone: cs.enabled ? undefined : 'muted',
      line: cs.enabled ? `눌러서 다시 물은 비율 · 제안 ${cs.total}건` : '설정에서 켜면 제안·누름이 쌓여요' },
    { id: 'candidates', big: cand.agree + cand.disagree ? pct(cand.agree, cand.agree + cand.disagree) : '-',
      line: `AI 추천대로 결정한 비율 · 반대 ${cand.disagree} · 결정 전 ${cand.by_status.proposed ?? 0}` },
  ];
  return (
    <div className="space-y-4">
      <p className="text-xs text-slate-500" title="AI 판단 원장(ops_ai_decision) — AI가 정한 것 한 건 = 한 줄, 사람이 확정·변경하면 같은 줄에 남는다">
        카드 숫자는 최근 {d.days}일 · 카드를 누르면 아래 감사 로그를 그 종류로 걸러요 ⓘ
      </p>
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-2">
        {cards.map((c) => (
          <Card key={c.id} id={c.id} title={titles[c.id]} big={c.big} tone={c.tone} line={c.line} active={filter === c.id}
            onFilter={LOG_KIND[c.id] ? () => setFilter(filter === c.id ? null : c.id) : undefined} onOpen={() => setOpen(c.id)} />
        ))}
      </div>
      <AuditLog kind={filter} onClearKind={() => setFilter(null)} />
      <Modal isOpen={open !== null} onClose={() => setOpen(null)} title={open ? titles[open] : ''} maxWidth="max-w-2xl">
        {open && <Detail open={open} d={d} techs={techs} /> as ReactNode}
      </Modal>
    </div>
  );
}
