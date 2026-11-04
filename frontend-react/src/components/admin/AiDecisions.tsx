import { Fragment, useMemo, useState, type ReactNode } from 'react';
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { clsx } from 'clsx';
import {
  getDecisionLog, getDecisionsReport, getOverrides, listTechniques, type DecisionLogItem, type DecisionsReport, type ReviewStatus, type Technique,
} from '../../api/eval';
import { AuditReport, REVIEW_BADGE, REVIEW_HINT, REVIEW_LABEL } from './AuditReport';
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
const PICK_NAME: Record<string, string> = { option: '선택지 고름', all: '전부 보기', typed: '직접 재질문', superseded: '다음 되묻기로 대체', other: '기타' };
const REASON_NAME: Record<string, string> = {
  resolved: '정탐(해결)', source_ok: '오탐 · 원문 정상', already_fixed: '오탐 · 이미 수정됨', duplicate: '중복', other: '기타',
};

type CardKey = 'location' | 'clarify' | 'source' | 'suggest' | 'duplicate' | 'candidates';

/** 카드마다 그 판단의 기준(답 기법 대장 항목) — 사용자 10/27 "어떤 기준으로 되묻는지, 판단 기준이 같이 있어야 개선 아이디어가 나온다" */
const CRITERIA: Record<CardKey, string[]> = {
  location: ['knowledge_location'], clarify: ['clarify', 'ref_clarify'], source: ['source_check'],
  suggest: ['collection_suggest'], duplicate: ['duplicate_review'], candidates: ['improve_recommend'],
};

/** 판단 기준 — 가장 짧은 예시 한 장면 + "이렇게 판단해요" 규칙 3줄 + 근거 한 줄(10/30 사용자: 줄글·값 칩은 안 읽힘) */
function Criteria({ items }: { items: Technique[] }) {
  if (!items.length) return null;
  return (
    <section className="space-y-4" data-testid="decision-criteria">
      {items.map((t) => {
        const last = t.evidence[t.evidence.length - 1];
        return (
          <div key={t.key} className="rounded-lg border border-slate-700 bg-slate-800/60 p-4 space-y-3">
            <div className="flex items-center gap-2">
              <h4 className="text-sm font-semibold text-slate-100">{t.name}</h4>
              <span className="rounded bg-slate-700/60 px-1.5 py-0.5 text-[11px] text-slate-400">{t.stage}</span>
            </div>
            {t.example && <Example ex={t.example} />}
            {t.rules?.length ? (
              <div>
                <p className="text-[11px] text-slate-500 mb-1">이렇게 판단해요</p>
                <ol className="space-y-1">
                  {t.rules.map((r, i) => (
                    <li key={i} className="flex gap-2 text-sm text-slate-200">
                      <span className="shrink-0 w-5 h-5 rounded-full bg-indigo-100 text-indigo-700 dark:bg-indigo-500/20 dark:text-indigo-300 text-[11px] flex items-center justify-center">{i + 1}</span>
                      <span className="leading-relaxed">{r}</span>
                    </li>
                  ))}
                </ol>
              </div>
            ) : <p className="text-sm text-slate-300 leading-relaxed">{t.does}</p>}
            {last && <p className="text-xs text-slate-500" title={t.evidence.map((e) => `${e.date} ${e.note}`).join(' / ')}>근거 {last.date} · {last.note}</p>}
          </div>
        );
      })}
      <p className="text-xs text-slate-500">값을 바꾸려면 평가 게이트 › 개선할 곳에서 실험·측정 후 — 전체 목록은 평가 게이트 › 적용 이력</p>
    </section>
  );
}

/** 예시 한 장면 — 가상 문장(실제 정책 문구 아님). 첫 줄이 사람(사용자·새 지식·후보), 나머지는 시스템 쪽 */
function Example({ ex }: { ex: NonNullable<Technique['example']> }) {
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900/60 p-3 space-y-2" data-testid="criteria-example">
      <p className="text-[11px] text-slate-500">예시 — {ex.where}</p>
      {ex.lines.map(([who, text], i) => (
        <div key={i} className={clsx('flex', i === 0 && ex.where === '채팅' ? 'justify-end' : 'justify-start')}>
          <div className={clsx('max-w-[85%] rounded-xl px-3 py-1.5 text-sm',
            i === 0 ? 'bg-indigo-600 text-white' : 'bg-slate-700/70 text-slate-100')}>
            <span className={clsx('block text-[10px]', i === 0 ? 'text-indigo-200' : 'text-slate-400')}>{who}</span>{text}
          </div>
        </div>
      ))}
      {ex.chips.length > 0 && (
        <div className="flex flex-wrap gap-1.5 pl-1">
          {ex.chips.map((c) => <span key={c} className="rounded-full border border-indigo-300 dark:border-indigo-500/50 px-2.5 py-0.5 text-xs text-indigo-700 dark:text-indigo-300">{c}</span>)}
        </div>
      )}
    </div>
  );
}

const LOG_KIND: Partial<Record<CardKey, string>> = {
  location: 'knowledge_location', clarify: 'chat_clarify', source: 'source_check', suggest: 'collection_suggest', duplicate: 'duplicate_check',
};
// 번복 사유(업계 override reason code, 11/04 태훈 "업계 용어로")
const OVERRIDE_REASON: Record<string, { label: string; cls: string }> = {
  wrong: { label: 'AI 오류', cls: 'text-rose-600 dark:text-rose-400' },
  both_ok: { label: '선호 차이', cls: 'text-slate-300' },
  context_changed: { label: '맥락 변화', cls: 'text-amber-700 dark:text-amber-300' },
};
const ROW = 'grid grid-cols-[3.25rem_6rem_minmax(0,1.2fr)_1.25rem_minmax(0,1fr)_4.75rem_minmax(0,0.9fr)_4.5rem] items-center gap-x-2';
const REVIEW_FILTERS: (ReviewStatus | null)[] = [null, 'override', 'accept', 'auto', 'rule', 'pending'];
const KIND_LABEL: Record<string, string> = {
  knowledge_location: '등록 위치', chat_clarify: '되묻기', source_check: '원문 확인', collection_suggest: '다른 컬렉션 제안', duplicate_check: '중복 의심',
};
const dayOf = (s: string) => new Date(s).toLocaleDateString('ko-KR', { month: 'long', day: 'numeric', weekday: 'short' });
const timeOf = (s: string) => new Date(s).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false });

/** 감사 로그 — 날짜별, 같은 날 같은 판단(종류·AI·결과가 같은 줄)이 이어지면 "같은 판단 n건"으로 접는다 */
function AuditLog({ kind, setKind, days, day, setDay, review, setReview, counts }: {
  kind: CardKey | null; setKind: (k: CardKey | null) => void; days: number;   // 기간은 위 리포트 드롭다운 하나로
  day: string | null; setDay: (d: string | null) => void;
  /** 칩 건수(기간·종류 기준) — 날짜를 고르면 null(그날 건수는 날짜 카드에 — 목록과 다른 숫자가 나오지 않게, 리뷰 M2) */
  review: ReviewStatus | null; setReview: (r: ReviewStatus | null) => void; counts: Record<ReviewStatus, number> | null;
}) {
  const [openRuns, setOpenRuns] = useState<Set<number>>(new Set());
  const logKind = kind ? LOG_KIND[kind] : undefined;
  const q = useInfiniteQuery({
    queryKey: ['ai-decision-log', days, logKind ?? '', review ?? '', day ?? ''],
    queryFn: ({ pageParam }) => getDecisionLog({ days, kind: logKind, review, before: pageParam, day }),
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
        <h3 className="text-sm font-semibold text-slate-100 mr-2">감사 로그 <span className="text-xs font-normal text-slate-500">근거 확인용</span></h3>
        {/* 종류는 드롭다운 — 판단 종류가 늘어도 한 줄(11/03 태훈). 기준(ⓘ)은 위 종류 카드에서 */}
        <select className={sel} value={kind ?? ''} onChange={(e) => setKind((e.target.value || null) as CardKey | null)} aria-label="종류" data-testid="audit-kind">
          <option value="">종류 전체</option>
          {(Object.keys(LOG_KIND) as CardKey[]).map((k) => <option key={k} value={k}>{KIND_LABEL[LOG_KIND[k]!]}</option>)}
        </select>
        {day && (
          <button onClick={() => setDay(null)} data-testid="audit-day-chip" title="날짜 거르기 풀기"
            className="rounded-full border border-indigo-500 px-2.5 py-0.5 text-xs text-indigo-600 dark:border-indigo-400 dark:text-indigo-300">
            {dayOf(day)} ✕</button>
        )}
        {/* 검토 결과 거르기(11/04) — 기본 = 번복(감사의 핵심 증거). 건수는 위 리포트와 같은 기간·기준 */}
        <div className="ml-auto flex flex-wrap gap-1" role="group" aria-label="검토 결과" data-testid="audit-review">
          {REVIEW_FILTERS.map((r) => (
            <button key={r ?? 'all'} type="button" aria-pressed={review === r} onClick={() => setReview(r)} title={r ? REVIEW_HINT[r] : '모든 판단'}
              className={clsx('rounded-full border px-2.5 py-0.5 text-xs', review === r
                ? 'border-indigo-600 bg-indigo-600 text-white dark:border-indigo-500 dark:bg-indigo-500' : 'border-slate-600 text-slate-300 hover:bg-slate-800')}>
              {r ? REVIEW_LABEL[r] : '전체'}{counts && <span className="tabular-nums opacity-75"> {r ? counts[r] : Object.values(counts).reduce((a, n) => a + n, 0)}</span>}
            </button>
          ))}
        </div>
      </div>
      {q.isLoading ? <p className="text-sm text-slate-500">불러오는 중…</p>
        : q.error ? <p className="text-sm text-rose-600 dark:text-rose-400">불러오지 못했습니다: {(q.error as Error).message}</p>
        : items.length === 0 ? <p className="text-sm text-slate-500">기록이 없어요.</p> : (
          <ol className="rounded-xl border border-slate-700 divide-y divide-slate-700/70 overflow-x-auto">
            <li className={clsx(ROW, 'px-4 py-1.5 text-[11px] text-slate-500 bg-slate-900/60 min-w-[760px]')} aria-hidden>
              <span>시간</span><span>종류</span><span>AI 판단</span><span /><span>최종 값</span><span>검토 결과</span><span>번복 사유 · 개선 후보</span><span>검토자</span>
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
        {it.open ? '—' : it.human}
      </span>
      <span className="flex flex-col items-start gap-0.5">
        <span className={clsx('rounded-full px-2 py-0.5 text-[11px]', REVIEW_BADGE[it.review])} title={REVIEW_HINT[it.review]} data-testid={`log-${it.id}-review`}>{REVIEW_LABEL[it.review]}</span>
        {it.spot && <span className="text-[10px] text-slate-400" title={`표본 감사${it.spot_by ? ` · ${it.spot_by}` : ''}`}>감사: {it.spot === 'accepted' ? '수용' : `번복${it.spot_reason ? ` · ${OVERRIDE_REASON[it.spot_reason]?.label ?? ''}` : ''}`}</span>}
      </span>
      <span className="min-w-0 flex flex-col gap-0.5 text-xs">
        {why && <span className={why.cls}>{why.label}</span>}
        {it.changed && !why && it.kind === 'knowledge_location' && <span className="text-slate-500">사유 없음</span>}
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
        {s && <p className="text-sm text-slate-300">확정 {s.decided}건 중 수용 {s.decided - s.changed} · 번복 {s.changed}
          {s.auto_wrong > 0 && <span className="text-rose-600 dark:text-rose-400"> · 확신(자동 후보)인데 틀림 {s.auto_wrong} — 자동 반영 기준 점검</span>}</p>}
        {rows.length === 0 ? <p className="text-sm text-slate-500">번복이 없어요.</p> : (
          <ul className="divide-y divide-slate-700">
            {rows.map((r) => (
              <li key={r.id} className="py-2.5 space-y-1" data-testid={`changed-${r.id}`}>
                <p className="text-sm text-slate-200">AI <b>{r.ai.collection ?? '삭제된 컬렉션'}</b> <span className="text-slate-500">→</span> 사람 <b>{r.human.collection ?? '다른 곳'}</b></p>
                <p className="text-xs text-slate-500">{fmtDate(r.at)}{r.changed_by ? ` · ${r.changed_by}` : ''}{r.target ? ` · ${r.target}` : ''}</p>
                <p className="text-xs text-slate-400">AI 근거: {r.ai.reason ?? '(근거가 남지 않은 옛 기록)'}</p>
                {r.verdict && <p className="text-xs text-rose-600 dark:text-rose-400">번복 사유: {r.verdict === 'wrong' ? 'AI 오류' : r.verdict === 'context_changed' ? '맥락 변화' : '모름'}</p>}
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
        <p className="text-xs text-slate-400 leading-relaxed">"전부 보기"가 많으면 괜히 물은 것, "직접 재질문"이 많으면 선택지가 안 맞은 것(노랑). 검토 대기 되묻기 {d.clarify.open}건.</p>
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
            <p key={src} className="text-sm text-slate-300">{src === 'v0_load_check' ? '적재 점검' : src === 'golden_set' ? '골든셋 작성' : src} — 오탐률 {pct(v.no_issue, v.no_issue + v.resolved)} ({v.no_issue}/{v.no_issue + v.resolved})</p>
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
  if (open === 'duplicate') {
    return (
      <p className="text-xs text-slate-400 leading-relaxed">운영 지식 › 승인 대기에서 사람이 처리한 것 — 덮어쓰기·반려는 AI 판단(중복)대로, [그대로 저장]은 "중복 아님" = 번복.
        번복이 많으면 유사도 하한이 낮은 것, 0.98 넘는 건이 많으면 등록 때 완전 일치 거부·청크 구조화 대상(11/03 설계).</p>
    );
  }
  const c = d.candidates;
  return (
    <div className="space-y-3">
      <p className="text-xs text-slate-400">평가 게이트 리포트의 "고칠 방법" — AI 추천(해 볼 만함·안 하는 게 좋음)을 사람이 따랐나. 반대가 많으면 추천 규칙이 사람 판단과 어긋난다는 신호.</p>
      <Bars rows={[{ name: '수용(추천대로)', n: c.agree }, { name: '번복(추천과 반대)', n: c.disagree, warn: true }, { name: '검토 대기', n: c.by_status.proposed ?? 0 }]} />
    </div>
  );
}

/** 날짜별 카드 — 그날 AI 판단 · 번복. 누르면 그날만(다시 누르면 풀림). 많아지면 가로로 넘긴다 */
function DayCards({ daily, day, onDay, loading }: { daily: { day: string; n: number; overrides: number }[]; day: string | null; onDay: (d: string) => void; loading: boolean }) {
  if (loading || daily.length === 0) return null;
  const max = Math.max(1, ...daily.map((x) => x.n));
  return (
    <section className="space-y-1.5" aria-label="날짜별">
      <h3 className="text-xs font-medium text-slate-400" title="그날(한국 시간) AI가 낸 판단 수 — 아직 사람이 확정하지 않은 판단도 포함, 종류 카드는 확정된 것만 센다">
        날짜별 <span className="text-slate-500">(누르면 그날만 · 확정 전 포함 ⓘ)</span></h3>
      <div className="flex gap-2 overflow-x-auto pb-1" data-testid="day-cards">
        {daily.map((x) => (
          <button key={x.day} onClick={() => onDay(x.day)} aria-pressed={day === x.day} data-testid={`day-card-${x.day}`}
            className={clsx('w-28 shrink-0 rounded-xl border bg-slate-800 p-2.5 text-left', day === x.day ? 'border-indigo-500 ring-1 ring-indigo-500 dark:border-indigo-400 dark:ring-indigo-400' : 'border-slate-700 hover:border-slate-500')}>
            <p className="text-[11px] text-slate-400">{dayOf(x.day)}</p>
            <p className="text-lg font-semibold tabular-nums text-slate-100">{x.n}<span className="text-xs font-normal text-slate-500">건</span></p>
            <span className="mt-1 block h-1.5 rounded-full bg-slate-700 overflow-hidden" aria-hidden>
              <span className="block h-full bg-indigo-500 dark:bg-indigo-400" style={{ width: `${(x.n / max) * 100}%` }} />
            </span>
            <p className={clsx('mt-1 text-[11px]', x.overrides ? 'text-amber-700 dark:text-amber-300' : 'text-slate-500')}>번복 {x.overrides}</p>
          </button>
        ))}
      </div>
    </section>
  );
}

export function AiDecisions() {
  const [days, setDays] = useState(30);
  const { data: d, isLoading, error } = useQuery({ queryKey: ['ai-decisions', 90], queryFn: () => getDecisionsReport(90), staleTime: 30_000 });
  const ov = useQuery({ queryKey: ['ai-overrides', days], queryFn: () => getOverrides(days), staleTime: 30_000 });
  const [open, setOpen] = useState<CardKey | null>(null);
  const [filter, setFilter] = useState<CardKey | null>(null);
  const [day, setDay] = useState<string | null>(null);
  const [review, setReview] = useState<ReviewStatus | null>('override');   // 기본 = 번복(감사의 핵심 증거)
  const { data: techs = [] } = useQuery({ queryKey: ['eval-techniques'], queryFn: listTechniques, staleTime: 60_000 });
  if (isLoading) return <p className="text-sm text-slate-500">불러오는 중…</p>;
  if (error) return <p className="text-sm text-rose-600 dark:text-rose-400">불러오지 못했습니다: {(error as Error).message}</p>;
  if (!d) return null;
  const titles: Record<CardKey, string> = {
    location: '지식 등록 위치', clarify: '채팅 되묻기', source: '원문 확인 검사', suggest: '다른 컬렉션 제안', duplicate: '등록 중복 의심',
    candidates: '개선 후보 — AI 추천',
  };
  const kinds = ov.data?.kinds ?? [];
  const cardOf = (kind: string): CardKey | null => kind === 'candidates' ? 'candidates' : (Object.keys(LOG_KIND) as CardKey[]).find((k) => LOG_KIND[k] === kind) ?? null;
  const counts = Object.fromEntries((['accept', 'override', 'auto', 'rule', 'pending'] as ReviewStatus[]).map((r) =>
    [r, kinds.filter((k) => !filter || k.kind === LOG_KIND[filter]).reduce((a, k) => a + (k.review?.[r] ?? 0), 0)])) as Record<ReviewStatus, number>;
  const toLog = () => document.querySelector('[data-testid="audit-log"]')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-slate-500" title="AI 판단 원장(ops_ai_decision) — AI가 정한 것 한 건 = 한 줄, 사람이 확정·변경하면 같은 줄에 남는다">
          AI 판단마다 사람 검토 결과(수용·번복) — 번복률은 사람이 검토한 판단으로만 계산 ⓘ</p>
        <select className="bg-slate-800 border border-slate-600 rounded-md px-2 py-1 text-xs text-slate-200 focus:outline-none focus:border-indigo-500"
          value={days} onChange={(e) => { setDays(Number(e.target.value)); setDay(null); }} aria-label="기간" data-testid="report-days">
          {[7, 30, 90].map((x) => <option key={x} value={x}>최근 {x}일</option>)}
        </select>
      </div>
      {ov.error && <p className="text-xs text-rose-600 dark:text-rose-400">리포트를 불러오지 못했어요: {(ov.error as Error).message}</p>}
      {ov.isLoading ? <p className="text-sm text-slate-500">불러오는 중…</p> : (
        <AuditReport kinds={kinds} days={days} candidates={d.candidates}
          onKind={(k) => { const c = cardOf(k); if (c && c !== 'candidates') setFilter(c); toLog(); }}
          onReview={(r) => { setReview(r); toLog(); }}
          canCriteria={(k) => !!cardOf(k)} onCriteria={(k) => { const c = cardOf(k); if (c) setOpen(c); }} />
      )}
      <DayCards daily={ov.data?.daily ?? []} day={day} onDay={(x) => setDay(day === x ? null : x)} loading={ov.isLoading} />
      <AuditLog kind={filter} setKind={setFilter} days={days} day={day} setDay={setDay}
        review={review} setReview={setReview} counts={day ? null : counts} />
      <Modal isOpen={open !== null} onClose={() => setOpen(null)} title={open ? titles[open] : ''} maxWidth="max-w-2xl">
        {open && <Detail open={open} d={d} techs={techs} /> as ReactNode}
      </Modal>
    </div>
  );
}
