import { Fragment, useMemo, useState, type ReactNode } from 'react';
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { clsx } from 'clsx';
import {
  getDecisionLog, getDecisionsReport, getOverrides, listTechniques, type DecisionLogItem, type DecisionsReport, type Technique,
} from '../../api/eval';
import { adminPath } from '../../pages/adminMenu';
import { Modal } from '../ui/Modal';

/**
 * 품질 › AI 감사 로그 (10/27 밤 개편, docs/domains/quality/self-improvement-loop-design.md) — 기능을 쓰다 사람이 AI 추천을 번복한 기록 = 자가 개선 루프의 입력.
 * 사용자 10/27: "그 위치에서 AI가 틀림 어쩌구 하는 게 아니라 … 번복했을 때의 로그를 쌓는 것, 그걸 기준으로 자가 개선". 그래서 이 화면은 기록만
 * ("AI가 틀렸나요?"는 번복하는 그 순간 기능 안에서 — 지식 등록 넣을 곳 카드).
 * 위: 요약 세 칸(번복 · AI가 틀렸다고 한 것 · 번복으로 만든 개선 후보) + 형식적 승인 의심. 아래: 종류 거르기([기준] = 지금 기준·분포 창) + 번복만 +
 * 한 줄 표(시간 · 종류 · AI 추천 → 사람이 한 것 · 번복 이유·이어진 개선 · 누가). 업계 override monitoring 관례(이유 코드·종류별 번복률·형식적 승인 경고). 원문 없음.
 */

const pct = (n: number, d: number) => (d ? `${Math.round((n / d) * 100)}%` : '-');
const fmtDate = (s: string) => new Date(s).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
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
      <p className="text-xs text-slate-500">값을 바꾸려면 평가 게이트 리포트의 고칠 방법에서 실험·측정 후 — 전체 목록은 평가 게이트 › 적용 이력</p>
    </section>
  );
}

const LOG_KIND: Partial<Record<CardKey, string>> = { location: 'knowledge_location', clarify: 'chat_clarify', source: 'source_check', suggest: 'collection_suggest' };
const OVERRIDE_REASON: Record<string, { label: string; cls: string }> = {
  wrong: { label: 'AI가 틀림', cls: 'text-rose-600 dark:text-rose-400' },
  both_ok: { label: '둘 다 맞음', cls: 'text-slate-300' },
  context_changed: { label: '상황이 바뀜', cls: 'text-amber-700 dark:text-amber-300' },
};
const ROW = 'grid grid-cols-[3.25rem_6rem_minmax(0,1.25fr)_1.25rem_minmax(0,1fr)_minmax(0,0.9fr)_4.5rem] items-center gap-x-2';
const KIND_LABEL: Record<string, string> = { knowledge_location: '등록 위치', chat_clarify: '되묻기', source_check: '원문 확인', collection_suggest: '다른 컬렉션 제안' };
const dayOf = (s: string) => new Date(s).toLocaleDateString('ko-KR', { month: 'long', day: 'numeric', weekday: 'short' });
const timeOf = (s: string) => new Date(s).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false });

/** 감사 로그 — 날짜별, 같은 날 같은 판단(종류·AI·결과가 같은 줄)이 이어지면 "같은 판단 n건"으로 접는다 */
function AuditLog({ kind, setKind, onCriteria, days, setDays }: {
  kind: CardKey | null; setKind: (k: CardKey | null) => void; onCriteria: (k: CardKey) => void; days: number; setDays: (d: number) => void;
}) {
  const [changedOnly, setChangedOnly] = useState(true);   // 기본 = 번복만(감사 로그의 핵심 증거, 그대로 둔 판단은 끄면 보임)
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
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold text-slate-100 mr-2">감사 로그</h3>
        <div className="flex flex-wrap gap-1" role="group" aria-label="종류">
          <button onClick={() => setKind(null)} aria-pressed={kind === null}
            className={clsx('rounded-full border px-2.5 py-0.5 text-xs', kind === null ? 'border-indigo-500 text-indigo-600 dark:border-indigo-400 dark:text-indigo-300' : 'border-slate-600 text-slate-300')}>전체</button>
          {(Object.keys(LOG_KIND) as CardKey[]).map((k) => (
            <span key={k} className={clsx('inline-flex items-center rounded-full border text-xs',
              kind === k ? 'border-indigo-500 text-indigo-600 dark:border-indigo-400 dark:text-indigo-300' : 'border-slate-600 text-slate-300')}>
              <button onClick={() => setKind(kind === k ? null : k)} aria-pressed={kind === k} data-testid={`decision-card-${k}`} className="pl-2.5 pr-1.5 py-0.5">
                {KIND_LABEL[LOG_KIND[k]!]}</button>
              <button onClick={() => onCriteria(k)} data-testid={`decision-detail-${k}`} title="이 판단의 지금 기준·분포" aria-label={`${KIND_LABEL[LOG_KIND[k]!]} 기준`}
                className="pr-2 py-0.5 text-slate-500 hover:text-slate-200">ⓘ</button>
            </span>
          ))}
        </div>
        <label className="ml-auto flex items-center gap-1.5 text-xs text-slate-300">
          <input type="checkbox" checked={changedOnly} onChange={(e) => setChangedOnly(e.target.checked)} className="accent-indigo-600" />번복만
        </label>
        <select className={sel} value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="기간">
          {[7, 30, 90].map((d) => <option key={d} value={d}>최근 {d}일</option>)}
        </select>
      </div>
      {q.isLoading ? <p className="text-sm text-slate-500">불러오는 중…</p>
        : q.error ? <p className="text-sm text-rose-600 dark:text-rose-400">불러오지 못했습니다: {(q.error as Error).message}</p>
        : items.length === 0 ? <p className="text-sm text-slate-500">기록이 없어요.</p> : (
          <ol className="rounded-xl border border-slate-700 divide-y divide-slate-700/70 overflow-x-auto">
            <li className={clsx(ROW, 'px-4 py-1.5 text-[11px] text-slate-500 bg-slate-900/60 min-w-[760px]')} aria-hidden>
              <span>시간</span><span>종류</span><span>AI 추천</span><span /><span>사람이 한 것</span><span>번복 이유 · 이어진 개선</span><span>누가</span>
            </li>
            {runs.map((r, i) => {
              const newDay = i === 0 || dayOf(runs[i - 1].head.at) !== dayOf(r.head.at);
              const expanded = openRuns.has(r.head.id);
              return (
                <Fragment key={r.head.id}>
                  {newDay && <li className="px-4 py-1.5 text-xs font-medium text-slate-400 bg-slate-900/50">{dayOf(r.head.at)}</li>}
                  <LogRow it={r.head} />
                  {r.rest.length > 0 && (
                    <li className="px-4 py-1.5 pl-[10rem] min-w-[760px]">
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
  const navigate = useNavigate();
  const tip = [it.ai_reason, it.rule ? `기준: ${it.rule}` : null].filter(Boolean).join(' · ');
  const why = it.override_reason ? OVERRIDE_REASON[it.override_reason] : null;
  return (
    <li data-testid={`log-${it.id}`}
      className={clsx(ROW, 'px-4 py-2.5 text-sm min-w-[760px]', it.changed && 'border-l-[3px] border-l-amber-500 pl-[13px] bg-amber-50/60 dark:border-l-amber-400 dark:bg-amber-500/5')}>
      <span className="text-xs text-slate-500 tabular-nums">{timeOf(it.at)}</span>
      <span><span className="rounded px-1.5 py-0.5 text-[11px] bg-slate-700 text-slate-300">{it.kind_name}</span></span>
      <span className="text-slate-100 min-w-0 break-words">
        {it.ai}
        {it.confident && <span className="ml-1 text-[11px] text-emerald-600 dark:text-emerald-400" title="AI 판단과 근거(이웃 등)가 맞아 확신 — 자동 후보">확신</span>}
        {tip && <span className="ml-1 text-[11px] text-slate-500 cursor-help" title={tip}>ⓘ</span>}
      </span>
      <span className="text-slate-500" aria-hidden>→</span>
      <span className={clsx('min-w-0 break-words text-xs', it.open ? 'text-slate-500' : it.changed ? 'text-amber-800 dark:text-amber-300' : 'text-slate-300')}>
        {it.open ? '아직 결과 없음' : it.human}{it.changed && <b className="ml-1 font-medium">· 번복</b>}
      </span>
      <span className="min-w-0 flex flex-col gap-0.5 text-xs">
        {why && <span className={why.cls}>{why.label}</span>}
        {it.changed && !why && it.kind === 'knowledge_location' && <span className="text-slate-500">이유 안 남김</span>}
        {it.candidate && (
          <button onClick={() => navigate(adminPath('q.eval'))} className="self-start text-indigo-600 dark:text-indigo-400 hover:underline"
            title={`${it.candidate.title} — 평가 게이트 리포트 › 고칠 방법`}>개선 후보 #{it.candidate.id} →</button>
        )}
      </span>
      <span className="text-xs text-slate-400 truncate">{it.changed_by ?? it.asked_by ?? ''}</span>
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
                {r.verdict && <p className="text-xs text-rose-600 dark:text-rose-400">번복 이유: {r.verdict === 'wrong' ? 'AI가 틀림' : r.verdict === 'context_changed' ? '상황이 바뀜' : '모름'}</p>}
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
  const navigate = useNavigate();
  const [days, setDays] = useState(30);
  const { data: d, isLoading, error } = useQuery({ queryKey: ['ai-decisions', 90], queryFn: () => getDecisionsReport(90), staleTime: 30_000 });
  const ov = useQuery({ queryKey: ['ai-overrides', days], queryFn: () => getOverrides(days), staleTime: 30_000 });
  const [open, setOpen] = useState<CardKey | null>(null);
  const [filter, setFilter] = useState<CardKey | null>(null);
  const { data: techs = [] } = useQuery({ queryKey: ['eval-techniques'], queryFn: listTechniques, staleTime: 60_000 });
  if (isLoading) return <p className="text-sm text-slate-500">불러오는 중…</p>;
  if (error) return <p className="text-sm text-rose-600 dark:text-rose-400">불러오지 못했습니다: {(error as Error).message}</p>;
  if (!d) return null;
  const titles: Record<CardKey, string> = {
    location: '지식 등록 위치', clarify: '채팅 되묻기', source: '원문 확인 검사', suggest: '다른 컬렉션 제안', candidates: '개선 후보 — AI 추천',
  };
  const kinds = ov.data?.kinds ?? [];
  const nOv = kinds.reduce((a, k) => a + k.overrides, 0);
  const nWrong = kinds.reduce((a, k) => a + (k.reasons['AI가 틀림'] ?? 0), 0);
  const nReason = kinds.filter((k) => k.kind === 'knowledge_location').reduce((a, k) => a + k.overrides - (k.reasons['이유 안 남김'] ?? 0), 0);
  const ovCands = ov.data?.candidates ?? [];
  const stamped = kinds.filter((k) => k.rubber_stamp);
  return (
    <div className="space-y-4">
      <p className="text-xs text-slate-500" title="AI 판단 원장(ops_ai_decision) — AI가 정한 것 한 건 = 한 줄, 사람이 확정·변경하면 같은 줄에 남는다. 번복 = 등록 위치는 AI 추천과 다른 곳, 되묻기는 전부 보기·직접 다시 물음, 원문 확인은 문제 없음">
        기능을 쓰다 사람이 AI 추천을 번복한 기록 — 자가 개선 루프의 입력 ⓘ
      </p>
      {ov.error && <p className="text-xs text-rose-600 dark:text-rose-400">번복 요약을 불러오지 못했어요: {(ov.error as Error).message}</p>}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2" data-testid="override-summary">
        <div className="rounded-xl border border-slate-700 bg-slate-800 p-3">
          <p className="text-xs text-slate-400">최근 {days}일 번복</p>
          <p className="text-2xl font-semibold tabular-nums text-slate-100">{nOv}<span className="text-sm font-normal text-slate-500">건</span></p>
          <p className="text-xs text-slate-400">{kinds.filter((k) => k.overrides).map((k) => `${k.kind_name} ${k.overrides}/${k.decided}`).join(' · ') || '없음'}</p>
        </div>
        <div className="rounded-xl border border-slate-700 bg-slate-800 p-3">
          <p className="text-xs text-slate-400" title="지식 등록에서 번복할 때 고른 이유(선택) — 되묻기·원문 확인은 고른 값이 곧 이유">AI가 틀렸다고 한 것</p>
          <p className="text-2xl font-semibold tabular-nums text-slate-100">{nWrong}<span className="text-sm font-normal text-slate-500">건</span></p>
          <p className="text-xs text-slate-400">등록 위치 번복 중 이유를 남긴 것 {nReason}건</p>
        </div>
        <button onClick={() => navigate(adminPath('q.eval'))} className="rounded-xl border border-slate-700 bg-slate-800 p-3 text-left hover:border-indigo-500 dark:hover:border-indigo-400"
          data-testid="override-candidates">
          <p className="text-xs text-slate-400">번복으로 만든 개선 후보</p>
          <p className="text-2xl font-semibold tabular-nums text-indigo-600 dark:text-indigo-400">{ovCands.length}<span className="text-sm font-normal text-slate-500">건 →</span></p>
          <p className="text-xs text-slate-400 truncate">{ovCands.map((c) => c.title).join(' · ') || '평가 게이트 리포트 › 고칠 방법'}</p>
        </button>
      </div>
      {stamped.length > 0 && (
        <p className="text-xs text-amber-700 dark:text-amber-300" title="업계 관례: 번복이 거의 없거나 수락이 95%를 넘으면 사람이 실제로 안 보고 있을 수 있다(표본 20건 이상일 때만)">
          형식적 승인 의심: {stamped.map((k) => `${k.kind_name} 수락 ${Math.round((1 - (k.rate ?? 0)) * 100)}%`).join(' · ')} ⓘ</p>
      )}
      <AuditLog kind={filter} setKind={setFilter} onCriteria={(k) => setOpen(k)} days={days} setDays={setDays} />
      <button onClick={() => setOpen('candidates')} className="text-xs text-slate-400 hover:text-slate-200">개선 후보 AI 추천을 사람이 따랐나 ›</button>
      <Modal isOpen={open !== null} onClose={() => setOpen(null)} title={open ? titles[open] : ''} maxWidth="max-w-2xl">
        {open && <Detail open={open} d={d} techs={techs} /> as ReactNode}
      </Modal>
    </div>
  );
}
