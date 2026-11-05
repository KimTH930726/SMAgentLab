import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { getSpotCheck, markSpotCheck, type DecisionsReport, type OverrideKind, type ReviewStatus } from '../../api/eval';
import { showError } from '../../store/useDialogStore';
import { Modal } from '../ui/Modal';

/**
 * 품질 › AI 감사 리포트(11/04 태훈 "감사 기록만 있으면 아무도 안 쓴다 — 어디서 뭘 틀리고 뭘 고칠지", "AI 판단에 대한 사람의 수용/번복이 보여야").
 * 업계 HITL 용어: 수용(Accepted) · 번복(Override) · 번복률 · 자동 적용(Auto-applied) · 규칙 적용 · 검토 대기(Pending review) · 표본 감사(Spot check).
 * 번복률은 사람이 본 판단(수용 + 번복)으로만 — 팀 적재 규칙 413건이 "수용"으로 섞여 1.1%로 보이던 것을 바로잡음(사람이 본 24건 중 21%).
 */

export const REVIEW_LABEL: Record<ReviewStatus, string> = {
  accept: '수용', override: '번복', auto: '자동 적용', rule: '규칙 적용', pending: '검토 대기',
};
export const REVIEW_HINT: Record<ReviewStatus, string> = {
  accept: '사람이 AI 판단을 그대로 받아들임',
  override: '사람이 AI 판단과 다르게 정함',
  auto: 'AI 판단을 사람 검토 없이 적용 — 표본 감사로 정확도 확인',
  rule: '사람이 정한 규칙(묶음 규칙·공간 기본값·미리 고른 곳)으로 정해져 AI 판단을 쓰지 않음 — 번복률 계산에서 제외',
  pending: '아직 사람이 확정하지 않음',
};
export const REVIEW_BADGE: Record<ReviewStatus, string> = {
  accept: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-500/15 dark:text-emerald-300',
  override: 'bg-amber-100 text-amber-900 dark:bg-amber-500/20 dark:text-amber-200',
  auto: 'bg-slate-700 text-slate-200',
  rule: 'bg-indigo-100 text-indigo-800 dark:bg-indigo-500/15 dark:text-indigo-300',
  pending: 'border border-dashed border-slate-500 text-slate-400',
};
export const BAR: Record<ReviewStatus, string> = {
  accept: 'bg-emerald-500 dark:bg-emerald-400', override: 'bg-amber-500 dark:bg-amber-400', auto: 'bg-slate-500',
  rule: 'bg-indigo-400 dark:bg-indigo-500', pending: 'bg-slate-700',
};
export const ORDER: ReviewStatus[] = ['accept', 'override', 'auto', 'rule', 'pending'];
const SPOT_REASON: { key: 'wrong' | 'both_ok' | 'context_changed'; label: string }[] = [
  { key: 'wrong', label: 'AI 오류' }, { key: 'both_ok', label: '선호 차이' }, { key: 'context_changed', label: '맥락 변화' },
];
/** 표본이 이보다 적으면 번복률을 "참고"로 — 업계 관례(형식적 승인 의심과 같은 20) */
const MIN_SAMPLE = 20;
const pct = (n: number, d: number) => (d ? `${Math.round((n / d) * 100)}%` : '—');
const total = (k: OverrideKind) => ORDER.reduce((a, s) => a + (k.review?.[s] ?? 0), 0);

function StackBar({ k }: { k: OverrideKind }) {
  const n = total(k);
  if (!n) return <span className="text-xs text-slate-500">기록 없음</span>;
  return (
    <div className="flex h-3 w-full overflow-hidden rounded-full bg-slate-900" role="img"
      aria-label={ORDER.map((s) => `${REVIEW_LABEL[s]} ${k.review[s]}`).join(', ')}>
      {ORDER.map((s) => k.review[s] ? <span key={s} className={BAR[s]} style={{ width: `${(k.review[s] / n) * 100}%` }} title={`${REVIEW_LABEL[s]} ${k.review[s]}`} /> : null)}
    </div>
  );
}

const NO_REASON = '사유 없음';   // 서버 override.NO_REASON

/**
 * 옮김 표(11/05 태훈 "줄글이라 수천 건이면 못 본다") — 업계 라우팅 품질 점검의 이동 짝(from → to) 표: 줄 = AI 추천, 칸 = 사람이 고른 곳,
 * 진할수록 자주 번복. 지운 컬렉션이 낀 짝은 표에서 빼고 한 줄 수로(정리 대상).
 */
function MoveMatrix({ pairs }: { pairs: NonNullable<OverrideKind['pairs']> }) {
  const live = pairs.filter((p) => !p.deleted);
  const gone = pairs.filter((p) => p.deleted).reduce((a, p) => a + p.n, 0);
  const label = (v: string | null, side: 'from' | 'to') => v ?? (side === 'from' ? '추천 없음' : '미지정');
  const rows = [...new Set(live.map((p) => label(p.from, 'from')))];
  const cols = [...new Set(live.map((p) => label(p.to, 'to')))];
  const max = Math.max(1, ...live.map((p) => p.n));
  const cell = (r: string, c: string) => live.find((p) => label(p.from, 'from') === r && label(p.to, 'to') === c)?.n ?? 0;
  return (
    <div className="space-y-1.5" data-testid="move-matrix">
      {live.length > 0 && (
        <div className="overflow-x-auto">
          <table className="text-xs border-collapse">
            <thead>
              <tr>
                <th className="px-1.5 py-1 text-left font-normal text-slate-500">AI 추천 ↓ · 사람 →</th>
                {cols.map((c) => <th key={c} className="px-1.5 py-1 text-left font-medium text-slate-300 whitespace-nowrap">{c}</th>)}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r} className="border-t border-slate-700/60">
                  <th className="px-1.5 py-1 text-left font-medium text-slate-300 whitespace-nowrap">{r}</th>
                  {cols.map((c) => {
                    const n = cell(r, c);
                    return (
                      <td key={c} className="p-0.5">
                        <span className={clsx('flex h-7 min-w-[2.5rem] items-center justify-center rounded tabular-nums',
                          n ? 'font-semibold text-amber-950 dark:text-amber-50' : 'text-slate-600')}
                          style={n ? { backgroundColor: `rgb(245 158 11 / ${0.2 + 0.7 * (n / max)})` } : undefined}
                          title={n ? `AI는 ${r}, 사람은 ${c} — ${n}건` : undefined}>{n || '·'}</span>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {gone > 0 && <p className="text-[11px] text-slate-500" title="지운 컬렉션(시험용 등)이 추천이나 결정에 낀 번복 — 개선 근거로 쓰지 않아요">삭제된 컬렉션 관련 {gone}건 — 표에서 뺌</p>}
    </div>
  );
}

/** 번복 있는 판단 하나 — 큰 번복률(높을수록 진하게) · 사유 막대 · 사유 없는 번복 경고 · 잦은 번복 · [번복 기록 보기 ›] */
function FindingCard({ k, onShow }: { k: OverrideKind; onShow: () => void }) {
  const rate = k.decided ? k.overrides / k.decided : 0;
  const reasons = Object.entries(k.reasons).sort((a, b) => b[1] - a[1]);
  const max = reasons[0]?.[1] ?? 1;
  const noReason = k.reasons[NO_REASON] ?? 0;
  const tone = rate >= 0.3 ? 'text-rose-600 dark:text-rose-400' : rate >= 0.15 ? 'text-amber-700 dark:text-amber-300' : 'text-emerald-700 dark:text-emerald-300';
  return (
    <article className="min-w-0 rounded-xl border border-slate-700 bg-slate-900/40 p-3 space-y-2.5" data-testid={`finding-${k.kind}`}>
      <div className="flex items-baseline justify-between gap-2">
        <h4 className="min-w-0 break-words text-sm font-semibold text-slate-100">{k.kind_name}</h4>
        <span className="text-[11px] text-slate-500 tabular-nums" title="번복 ÷ 사람 검토(수용 + 번복)">번복 {k.overrides} / 사람 검토 {k.decided}</span>
      </div>
      <p className={clsx('text-3xl font-semibold tabular-nums leading-none', tone)}>
        {pct(k.overrides, k.decided)}<span className="ml-1.5 text-xs font-normal text-slate-500">번복률{k.decided < MIN_SAMPLE ? ' · 표본 부족(참고)' : ''}</span>
      </p>
      <ul className="space-y-1" aria-label="번복 사유">
        {reasons.map(([r, n]) => (
          <li key={r} className="grid grid-cols-[minmax(0,7rem)_minmax(0,1fr)_auto] items-center gap-2 text-xs">
            <span className={clsx('truncate', r === NO_REASON ? 'text-slate-500' : 'text-slate-300')} title={r}>{r}</span>
            <span className="h-2 overflow-hidden rounded-full bg-slate-800">
              <span className={clsx('block h-full rounded-full', r === NO_REASON ? 'bg-slate-500' : 'bg-amber-500 dark:bg-amber-400')} style={{ width: `${(n / max) * 100}%` }} />
            </span>
            <span className="tabular-nums text-slate-300">{n}</span>
          </li>
        ))}
      </ul>
      {noReason > 0 && noReason * 2 >= k.overrides && (
        <p className="rounded-md bg-amber-50 px-2 py-1 text-[11px] text-amber-900 dark:bg-amber-500/10 dark:text-amber-200" data-testid={`finding-noreason-${k.kind}`}
          title="번복할 때 사유를 고르면 AI가 무엇을 틀리는지 알 수 있어요 — 자가 개선 후보의 근거">
          ⚠ 사유 없는 번복 {pct(noReason, k.overrides)} — 무엇을 고칠지 알 수 없어요</p>
      )}
      {(() => {   // 잦은 번복 = 지운 컬렉션이 안 낀 짝 중 가장 많은 것(지운 시험 컬렉션이 1등으로 보이던 것)
        const top = k.pairs?.find((p) => !p.deleted);
        return top ? <p className="text-[11px] text-slate-400">잦은 번복 <b className="text-slate-200">{top.from ?? '추천 없음'} → {top.to ?? '미지정'}</b> {top.n}건</p> : null;
      })()}
      <button type="button" onClick={onShow} className="text-xs text-indigo-600 hover:underline dark:text-indigo-400">번복 기록 보기 ›</button>
    </article>
  );
}

export function AuditReport({ kinds, days, candidates, onShow, onCriteria, canCriteria }: {
  kinds: OverrideKind[]; days: number; candidates: DecisionsReport['candidates'];
  /** 아래 감사 로그를 이 판단(·검토 결과)으로 — 날짜 거르기는 풀고 스크롤(11/04 "눌러도 뭔지 모르겠다": 이름 클릭 → [기록 보기 ›] 버튼) */
  onShow: (kind: string | null, review: ReviewStatus | null) => void; onCriteria: (kind: string) => void; canCriteria: (kind: string) => boolean;
}) {
  const [spotOpen, setSpotOpen] = useState(false);
  const sum = (s: ReviewStatus) => kinds.reduce((a, k) => a + (k.review?.[s] ?? 0), 0);
  // 요약 번복률·검토 대기는 관리자가 검토하는 종류만 — 되묻기·제안은 사용자 선택이라 섞으면 번복률이 낮아 보인다(리뷰 Q1·Q2). 종류별 표는 전부
  const admin = kinds.filter((k) => k.admin_reviewed);
  const asum = (s: ReviewStatus) => admin.reduce((a, k) => a + (k.review?.[s] ?? 0), 0);
  const all = ORDER.reduce((a, s) => a + sum(s), 0);
  const reviewed = asum('accept') + asum('override');
  const aiErr = admin.reduce((a, k) => a + (k.reasons['AI 오류'] ?? 0), 0);
  const spotN = kinds.reduce((a, k) => a + (k.spot?.n ?? 0), 0);
  const spotOv = kinds.reduce((a, k) => a + (k.spot?.overridden ?? 0), 0);
  const autoSpotN = kinds.reduce((a, k) => a + (k.spot?.auto_n ?? 0), 0);
  const autoSpotOv = kinds.reduce((a, k) => a + (k.spot?.auto_overridden ?? 0), 0);
  const withOv = [...kinds].filter((k) => k.overrides > 0).sort((a, b) => (b.rate ?? 0) - (a.rate ?? 0));
  // 주요 발견(11/05 태훈 "가시성 있게") — 번복 있는 종류는 카드(FindingCard), 그 밖의 사실은 흐린 줄
  const findings: string[] = [];
  if (sum('rule')) findings.push(`규칙 적용 ${sum('rule')}건 — 사람이 정한 규칙으로 들어가 AI 판단은 쓰지 않음(번복률 계산에서 제외)`);
  if (sum('auto')) findings.push(`자동 적용 ${sum('auto')}건 — 사람 검토 없음${autoSpotN ? ` · 표본 감사 ${autoSpotN}건 중 번복 ${autoSpotOv}건` : ' · 표본 감사로 정확도를 확인하세요'}`);
  for (const k of kinds.filter((x) => x.rubber_stamp)) findings.push(`형식적 승인 의심 — ${k.kind_name} 수용률 ${pct(k.decided - k.overrides, k.decided)}(사람이 실제로 안 보고 누를 수 있음, 표본 감사 권장)`);
  if (!withOv.length && !findings.length) findings.push('이 기간 번복 없음');
  const card = 'rounded-xl border border-slate-700 bg-slate-800 p-4 flex flex-col gap-1';
  const candDecided = candidates.agree + candidates.disagree;
  return (
    <div className="space-y-4" data-testid="audit-report">
      <section className="grid grid-cols-2 lg:grid-cols-4 gap-3" aria-label="요약">
        <div className={card}>
          <span className="text-xs text-slate-400">AI 판단 · 최근 {days}일</span>
          <span className="text-3xl font-semibold tabular-nums text-slate-100" data-testid="report-total">{all}</span>
          <span className="text-xs text-slate-500">{kinds.filter((k) => total(k)).length}종류</span>
        </div>
        <div className={card} title={`관리자 검토 종류(${admin.map((k) => k.kind_name).join('·')}) — ${REVIEW_HINT.accept} / ${REVIEW_HINT.override}`}>
          <span className="text-xs text-slate-400">사람 검토 <span className="text-slate-500">· 관리자 검토 종류</span></span>
          <span className="text-3xl font-semibold tabular-nums text-slate-100" data-testid="report-reviewed">{reviewed}</span>
          <span className="text-xs text-slate-500">수용 {asum('accept')} · 번복 {asum('override')}</span>
        </div>
        <div className={clsx(card, asum('override') ? 'border-amber-500 dark:border-amber-400' : '')}
          title="번복률 = 번복 ÷ 사람 검토(수용 + 번복) — 관리자 검토 종류만, 규칙·자동 적용·검토 대기는 분모에서 뺌. 되묻기·제안(사용자 선택)은 아래 표에서 종류별로">
          <span className="text-xs text-amber-700 dark:text-amber-300">번복률</span>
          <span className="text-3xl font-semibold tabular-nums text-amber-700 dark:text-amber-300" data-testid="report-rate">{pct(asum('override'), reviewed)}</span>
          <span className="text-xs text-slate-500">사람 검토 {reviewed}건 기준 · AI 오류 {aiErr}</span>
        </div>
        <div className={card} title="사람이 AI 판단을 검토하지 않은 판단(관리자 검토 종류)">
          <span className="text-xs text-slate-400">미검토</span>
          <span className="text-3xl font-semibold tabular-nums text-slate-100">{asum('rule') + asum('auto') + asum('pending')}</span>
          <span className="text-xs text-slate-500">규칙 적용 {asum('rule')} · 자동 적용 {asum('auto')} · 검토 대기 {asum('pending')}</span>
        </div>
      </section>

      <section className="rounded-xl border border-slate-700 bg-slate-800 p-4 space-y-2" aria-label="주요 발견" data-testid="report-findings">
        <h3 className="text-sm font-semibold text-slate-100">주요 발견</h3>
        {withOv.length > 0 && (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {withOv.slice(0, 3).map((k) => <FindingCard key={k.kind} k={k} onShow={() => onShow(k.kind, 'override')} />)}
          </div>
        )}
        {findings.length > 0 && <ul className="space-y-0.5 text-xs text-slate-400">{findings.map((f) => <li key={f}>· {f}</li>)}</ul>}
      </section>

      <section className="rounded-xl border border-slate-700 bg-slate-800 p-4 space-y-3" aria-label="AI 판단 ↔ 사람 검토">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-semibold text-slate-100">AI 판단 ↔ 사람 검토</h3>
          <div className="flex flex-wrap gap-3 text-[11px] text-slate-400" aria-label="범례">
            {ORDER.map((s) => <span key={s} className="flex items-center gap-1" title={REVIEW_HINT[s]}><span className={clsx('h-2.5 w-2.5 rounded-sm', BAR[s])} />{REVIEW_LABEL[s]}</span>)}
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[820px] text-sm" data-testid="report-table">
            <thead>
              <tr className="text-left text-[11px] text-slate-500">
                <th className="py-1.5 pr-2 font-medium">AI 판단</th>
                <th className="py-1.5 px-2 font-medium w-[30%]">검토 결과 분포</th>
                <th className="py-1.5 px-2 font-medium text-right">수용</th>
                <th className="py-1.5 px-2 font-medium text-right">번복</th>
                <th className="py-1.5 px-2 font-medium text-right" title="번복 ÷ (수용 + 번복)">번복률</th>
                <th className="py-1.5 px-2 font-medium text-right" title="규칙 적용 · 자동 적용 · 검토 대기">미검토</th>
                <th className="py-1.5 pl-2 font-medium">표본 수</th>
                <th className="py-1.5 pl-2 font-medium"><span className="sr-only">감사 로그</span></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-700/70">
              {kinds.map((k) => (
                <tr key={k.kind} data-testid={`report-row-${k.kind}`}>
                  <td className="py-2.5 pr-2">
                    <span className="font-medium text-slate-100">{k.kind_name}</span>
                    {canCriteria(k.kind) && <button type="button" onClick={() => onCriteria(k.kind)} className="ml-1 text-slate-500 hover:text-slate-200" aria-label={`${k.kind_name} 판단 기준`}>ⓘ</button>}
                  </td>
                  <td className="py-2.5 px-2"><StackBar k={k} /></td>
                  <td className="py-2.5 px-2 text-right tabular-nums text-slate-200">{k.review.accept}</td>
                  <td className={clsx('py-2.5 px-2 text-right tabular-nums', k.overrides ? 'text-amber-700 dark:text-amber-300' : 'text-slate-400')}>{k.overrides}</td>
                  <td className={clsx('py-2.5 px-2 text-right tabular-nums font-semibold', k.overrides ? 'text-amber-700 dark:text-amber-300' : 'text-slate-400')} data-testid={`report-rate-${k.kind}`}>{pct(k.overrides, k.decided)}</td>
                  <td className="py-2.5 px-2 text-right tabular-nums text-slate-300">{k.review.rule + k.review.auto + k.review.pending}</td>
                  <td className={clsx('py-2.5 pl-2 text-xs', k.decided < MIN_SAMPLE ? 'text-amber-700 dark:text-amber-300' : 'text-slate-400')}>
                    사람 검토 {k.decided}{k.decided < MIN_SAMPLE ? ' — 부족(참고)' : ''}{k.spot?.n ? ` · 표본 감사 ${k.spot.n}` : ''}
                  </td>
                  <td className="py-2.5 pl-2 text-right">
                    <button type="button" onClick={() => onShow(k.kind, null)} data-testid={`report-show-${k.kind}`}
                      title={`아래 감사 로그에 ${k.kind_name} 판단만 — 검토 결과 전체 · 날짜 전체`}
                      className="whitespace-nowrap rounded-md border border-slate-600 px-2 py-0.5 text-xs text-slate-200 hover:bg-slate-700">기록 보기 ›</button>
                  </td>
                </tr>
              ))}
              <tr data-testid="report-row-candidates" title="평가 게이트 › 고칠 방법 — AI가 '해 볼 만함·안 하는 게 좋음'으로 추천한 개선 후보를 사람이 따랐나(최근 90일)">
                <td className="py-2.5 pr-2">
                  <span className="font-medium text-slate-100">개선 후보 추천</span>
                  {canCriteria('candidates') && <button type="button" onClick={() => onCriteria('candidates')} className="ml-1 text-slate-500 hover:text-slate-200" aria-label="개선 후보 추천 판단 기준">ⓘ</button>}
                </td>
                <td className="py-2.5 px-2">
                  {candDecided + (candidates.by_status.proposed ?? 0) ? (
                    <div className="flex h-3 w-full overflow-hidden rounded-full bg-slate-900" role="img" aria-label={`수용 ${candidates.agree}, 번복 ${candidates.disagree}`}>
                      {[['accept', candidates.agree], ['override', candidates.disagree], ['pending', candidates.by_status.proposed ?? 0]].map(([s, n]) =>
                        n ? <span key={s as string} className={BAR[s as ReviewStatus]} style={{ width: `${((n as number) / (candDecided + (candidates.by_status.proposed ?? 0))) * 100}%` }} /> : null)}
                    </div>
                  ) : <span className="text-xs text-slate-500">기록 없음</span>}
                </td>
                <td className="py-2.5 px-2 text-right tabular-nums text-slate-200">{candidates.agree}</td>
                <td className="py-2.5 px-2 text-right tabular-nums text-slate-400">{candidates.disagree}</td>
                <td className="py-2.5 px-2 text-right tabular-nums text-slate-400">{pct(candidates.disagree, candDecided)}</td>
                <td className="py-2.5 px-2 text-right tabular-nums text-slate-300">{candidates.by_status.proposed ?? 0}</td>
                <td className="py-2.5 pl-2 text-xs text-slate-400">사람 검토 {candDecided}</td>
                <td className="py-2.5 pl-2" title="개선 후보 결정 기록은 평가 게이트 › 적용 이력에서" />
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <div className="grid gap-3 lg:grid-cols-2">
        <section className="rounded-xl border border-slate-700 bg-slate-800 p-4 space-y-3 min-w-0" aria-label="번복 패턴과 조치" data-testid="report-patterns">
          <h3 className="text-sm font-semibold text-slate-100">번복 패턴 → 조치</h3>
          {withOv.length === 0 ? <p className="text-sm text-slate-500">번복이 없어요.</p> : withOv.map((k) => (
            <div key={k.kind} className="rounded-lg border border-slate-700 p-3 space-y-1.5">
              <div className="flex justify-between gap-2 text-sm"><b className="text-slate-100">{k.kind_name}</b><span className="tabular-nums text-amber-700 dark:text-amber-300">번복 {k.overrides}</span></div>
              <p className="text-xs text-slate-300">{Object.entries(k.reasons).map(([r, n]) => `${r} ${n}`).join(' · ') || '사유 기록 없음'}</p>
              {k.pairs && k.pairs.length > 0 && <MoveMatrix pairs={k.pairs} />}
              <div className="flex justify-end">
                <button type="button" onClick={() => onShow(k.kind, 'override')} className="rounded-md border border-slate-600 px-2.5 py-1 text-xs text-slate-200 hover:bg-slate-700">번복 기록 보기</button>
              </div>
            </div>
          ))}
          <p className="text-[11px] text-slate-500">조치(개선 후보)는 평가 게이트 › 리포트 › 고칠 방법에서 — 번복이 쌓이면 진단이 후보로 만듭니다.</p>
        </section>

        <section className="rounded-xl border border-slate-700 bg-slate-800 p-4 space-y-3 min-w-0" aria-label="미검토 판단" data-testid="report-unreviewed">
          <h3 className="text-sm font-semibold text-slate-100">미검토 판단 · 표본 감사</h3>
          <div className="rounded-lg border border-slate-700 p-3 space-y-1.5">
            <div className="flex justify-between gap-2 text-sm"><b className="text-slate-100">자동 적용 · 수용 다시 보기</b><span className="tabular-nums text-slate-300">감사 {spotN} · 번복 {spotOv}</span></div>
            <p className="text-xs text-slate-400">사람 손 없이 적용됐거나 수용된 판단에서 무작위로 뽑아 수용·번복을 매겨요 — 정확도 추정과 형식적 승인 확인</p>
            <div className="flex justify-end">
              <button type="button" onClick={() => setSpotOpen(true)} data-testid="spot-open"
                className="rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-500">표본 감사 10건</button>
            </div>
          </div>
          <div className="rounded-lg border border-slate-700 p-3 flex flex-wrap items-center justify-between gap-2">
            <span className="text-sm text-slate-200">규칙 적용 <b className="tabular-nums">{asum('rule')}</b> · 검토 대기 <b className="tabular-nums">{asum('pending')}</b></span>
            <span className="flex gap-1.5">
              <button type="button" onClick={() => onShow(null, 'rule')} className="rounded-md border border-slate-600 px-2.5 py-1 text-xs text-slate-200 hover:bg-slate-700">규칙 적용 보기</button>
              <button type="button" onClick={() => onShow(null, 'pending')} className="rounded-md border border-slate-600 px-2.5 py-1 text-xs text-slate-200 hover:bg-slate-700">검토 대기 보기</button>
            </span>
          </div>
        </section>
      </div>
      {/* 열 때마다 새로 마운트 — 새 표본(닫으면 쿼리도 사라짐, 리뷰 M1) */}
      {spotOpen && <SpotCheckModal open onClose={() => setSpotOpen(false)} days={days} />}
    </div>
  );
}

/** 표본 감사 창 — 무작위 10건, 줄마다 [수용] / [번복: 사유]. 매긴 줄은 결과 + [변경](다시 매기면 마지막 검토자로 덮어씀) */
function SpotCheckModal({ open, onClose, days }: { open: boolean; onClose: () => void; days: number }) {
  const qc = useQueryClient();
  const [marked, setMarked] = useState<Record<number, string>>({});
  // 창이 열려 있는 동안은 같은 표본(재조회로 줄이 바뀌지 않게), 닫으면 버림(gcTime 0 + 조건부 마운트)
  const q = useQuery({ queryKey: ['spot-check', days], queryFn: () => getSpotCheck({ days, n: 10 }), enabled: open, staleTime: Infinity, gcTime: 0 });
  const mark = useMutation({
    mutationFn: (v: { id: number; result: 'accepted' | 'overridden'; reason?: 'wrong' | 'both_ok' | 'context_changed' }) => markSpotCheck(v.id, v.result, v.reason),
    onSuccess: (_r, v) => {
      setMarked((m) => ({ ...m, [v.id]: v.result === 'accepted' ? '수용' : `번복 · ${SPOT_REASON.find((x) => x.key === v.reason)?.label ?? ''}` }));
      qc.invalidateQueries({ queryKey: ['ai-overrides'] });
      qc.invalidateQueries({ queryKey: ['ai-decision-log'] });
    },
    onError: (e) => showError(e, '표본 감사를 저장하지 못했어요'),
  });
  const close = () => { setMarked({}); onClose(); };
  return (
    <Modal isOpen={open} onClose={close} title="표본 감사" maxWidth="max-w-3xl">
      <div className="space-y-3" data-testid="spot-modal">
        <p className="text-xs text-slate-400">AI 판단이 맞았는지 사람이 다시 봐요 — 맞으면 수용, 아니면 번복과 사유. 원래 기록(최종 값)은 바뀌지 않아요.</p>
        {q.isLoading ? <p className="text-sm text-slate-500">불러오는 중…</p>
          : q.error ? <p className="text-sm text-rose-600 dark:text-rose-400">불러오지 못했어요: {(q.error as Error).message}</p>
          : !q.data?.items.length ? <p className="text-sm text-slate-500">감사할 판단이 없어요(자동 적용·수용 중 아직 안 본 것).</p> : (
            <>
              <p className="text-xs text-slate-500">남은 감사 대상 {q.data.left}건 중 {q.data.items.length}건</p>
              <ul className="divide-y divide-slate-700 rounded-lg border border-slate-700">
                {q.data.items.map((it) => (
                  <li key={it.id} className="p-3 space-y-2" data-testid={`spot-${it.id}`}>
                    <div className="flex flex-wrap items-center gap-2 text-sm">
                      <span className="rounded bg-slate-700 px-1.5 py-0.5 text-[11px] text-slate-300">{it.kind_name}</span>
                      <span className={clsx('rounded-full px-2 py-0.5 text-[11px]', REVIEW_BADGE[it.review])}>{REVIEW_LABEL[it.review]}</span>
                      <span className="text-slate-100">AI 판단: <b>{it.ai}</b></span>
                      <span className="text-slate-400">→ 최종 값: {it.human ?? '—'}</span>
                    </div>
                    {it.ai_reason && <p className="text-xs text-slate-400">AI 근거: {it.ai_reason}</p>}
                    {marked[it.id] ? (
                      <p className="text-xs text-emerald-700 dark:text-emerald-300" data-testid={`spot-${it.id}-done`}>감사 완료 — {marked[it.id]}
                        <button type="button" onClick={() => setMarked((m) => { const n = { ...m }; delete n[it.id]; return n; })}
                          className="ml-2 text-slate-400 underline hover:text-slate-200">변경</button></p>
                    ) : (
                      <div className="flex flex-wrap gap-1.5" role="group" aria-label="표본 감사 결과">
                        <button type="button" disabled={mark.isPending} onClick={() => mark.mutate({ id: it.id, result: 'accepted' })}
                          className="rounded-md bg-emerald-600 px-3 py-1 text-xs font-semibold text-white hover:bg-emerald-500 disabled:opacity-50">수용</button>
                        {SPOT_REASON.map((r) => (
                          <button key={r.key} type="button" disabled={mark.isPending} onClick={() => mark.mutate({ id: it.id, result: 'overridden', reason: r.key })}
                            className="rounded-md border border-amber-500 px-3 py-1 text-xs text-amber-800 hover:bg-amber-50 dark:border-amber-400 dark:text-amber-200 dark:hover:bg-amber-500/10 disabled:opacity-50">
                            번복 · {r.label}</button>
                        ))}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </>
          )}
      </div>
    </Modal>
  );
}
