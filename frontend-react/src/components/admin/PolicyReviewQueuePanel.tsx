import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { ShieldCheck, PauseCircle } from 'lucide-react';
import {
  getReviewSummary, runAutoReview, resumeAutoRule, revertAutoReview, type AutoReviewResult,
} from '../../api/policy';
import { useAuthStore } from '../../store/useAuthStore';
import { Button } from '../ui/Button';

const RULE = 'low_risk_v1';

/**
 * 정책 승인 대기 큐 요약(2026-10-01) — "위험한 것만 사람이 본다".
 * 위험도는 서버의 결정론적 규칙(service/policy/risk.py): 높음(구조화 실패·이전 반려) / 중간(서술이 여러 개로 나뉨) /
 * 낮음(구조화 완료 + 서술 1개). 낮음은 자동 통과하되 표본은 사람이 확인하고, 표본이 반려되면 규칙이 멈춘다.
 * 검색 결과는 바뀌지 않는다 — 채팅 검색은 반려·폐기만 빼므로 검토대기↔승인 이동은 영향이 없다.
 */
export function PolicyReviewQueuePanel({ namespace, onShowQueue }: { namespace: string; onShowQueue: () => void }) {
  const qc = useQueryClient();
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin');
  const [preview, setPreview] = useState<AutoReviewResult | null>(null);
  const [lastRun, setLastRun] = useState<AutoReviewResult | null>(null);

  const { data: s } = useQuery({
    queryKey: ['policy-review-summary', namespace],
    queryFn: () => getReviewSummary(namespace),
    enabled: !!namespace,
    staleTime: 10_000,
  });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['policy-review-summary', namespace] });
    qc.invalidateQueries({ queryKey: ['policy-items', namespace] });
    qc.invalidateQueries({ queryKey: ['policy-items-all', namespace] });
  };

  const previewRun = useMutation({
    mutationFn: () => runAutoReview(namespace, true),
    onSuccess: setPreview,
    onError: (e: Error) => alert(e.message),
  });
  const run = useMutation({
    mutationFn: () => runAutoReview(namespace, false),
    onSuccess: (r) => { setPreview(null); setLastRun(r); refresh(); },
    onError: (e: Error) => alert(e.message),
  });
  const resume = useMutation({
    mutationFn: () => resumeAutoRule(RULE),
    onSuccess: refresh,
    onError: (e: Error) => alert(e.message),
  });
  const revert = useMutation({
    mutationFn: (scope: { run_id?: string; rule_key?: string; namespace: string }) => revertAutoReview(scope),
    onSuccess: (r) => { alert(`${r.reverted}건을 검토대기로 되돌렸습니다.`); setLastRun(null); refresh(); },
    onError: (e: Error) => alert(e.message),
  });

  if (!namespace || !s) return null;
  const rule = s.rules[RULE];
  const autoCount = s.auto_approved[RULE] ?? 0;
  const lowWaiting = s.rule_active ? s.queue.low_waiting : 0;

  return (
    <div className="rounded-xl border border-slate-700 bg-slate-800/40 p-4 space-y-3">
      <div className="flex items-start gap-3 flex-wrap">
        <div className="flex-1 min-w-[240px] space-y-1">
          <p className="text-xs text-slate-500" title="위험도가 높거나 중간인 항목과, 자동 통과 대상 중 표본으로 남긴 항목만 사람이 확인합니다.">
            사람이 볼 검토 큐
          </p>
          <div className="flex items-baseline gap-2">
            <span className="text-2xl font-semibold text-slate-100 tabular-nums">{s.human_queue}</span>
            <span className="text-xs text-slate-500 tabular-nums">/ 검토대기 {s.pending}건</span>
          </div>
          <div className="flex flex-wrap gap-1.5 text-[11px]">
            <span className="px-1.5 py-0.5 rounded border bg-rose-50 text-rose-700 border-rose-200 dark:bg-rose-900/30 dark:text-rose-300 dark:border-rose-700/50"
              title="원문을 구조화하지 못했거나(미해결), 이전에 반려된 항목">높음 {s.queue.high}</span>
            <span className="px-1.5 py-0.5 rounded border bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-900/30 dark:text-amber-300 dark:border-amber-700/50"
              title="서술이 여러 개로 나뉘어 조건이 쪼개졌을 수 있는 항목">중간 {s.queue.medium}</span>
            <span className="px-1.5 py-0.5 rounded border bg-indigo-50 text-indigo-700 border-indigo-200 dark:bg-indigo-900/30 dark:text-indigo-300 dark:border-indigo-700/50"
              title={`자동 통과 대상 중 약 ${Math.round(s.sample_rate * 100)}%를 사람이 확인 — 반려가 나오면 자동 통과를 멈춥니다`}>표본 {s.queue.sample}</span>
            {!s.rule_active && s.queue.low_waiting > 0 && (
              <span className="px-1.5 py-0.5 rounded border border-slate-600 text-slate-400" title="자동 통과 규칙이 멈춰 낮음도 사람이 봅니다">
                낮음 {s.queue.low_waiting}
              </span>
            )}
          </div>
        </div>
        <div className="space-y-1 text-right">
          <p className="text-xs text-slate-500" title="규칙으로 자동 통과된 항목 — 사람 승인과 구분되고, 되돌릴 수 있습니다">자동 통과</p>
          <p className="text-2xl font-semibold text-cyan-600 dark:text-cyan-400 tabular-nums">{autoCount}</p>
          {lowWaiting > 0 && <p className="text-[11px] text-slate-500 tabular-nums">자동 통과 대기 {lowWaiting}건</p>}
        </div>
        <div className="flex flex-col gap-1.5 items-end">
          <Button variant="secondary" size="sm" onClick={onShowQueue}>검토 큐 보기(위험도순)</Button>
          {isAdmin && s.rule_active && lowWaiting > 0 && !preview && (
            <Button variant="ghost" size="sm" loading={previewRun.isPending} onClick={() => previewRun.mutate()}>
              <ShieldCheck className="w-3.5 h-3.5" />자동 통과 미리보기
            </Button>
          )}
        </div>
      </div>

      {preview && (
        <div className="rounded-lg border border-cyan-200 bg-cyan-50/60 dark:border-cyan-800/50 dark:bg-cyan-950/20 px-3 py-2 text-xs flex items-center gap-2 flex-wrap">
          <span className="text-slate-300">
            낮음 <b className="tabular-nums">{preview.auto_approved}</b>건 자동 통과, <b className="tabular-nums">{preview.sampled}</b>건은 표본으로 사람 확인 →
            사람 큐 <b className="tabular-nums">{preview.human_queue_after}</b>건
          </span>
          <span className="ml-auto flex gap-1.5">
            <Button variant="ghost" size="sm" onClick={() => setPreview(null)}>취소</Button>
            <Button variant="primary" size="sm" loading={run.isPending} onClick={() => run.mutate()}>실행</Button>
          </span>
        </div>
      )}

      {lastRun?.run_id && (
        <div className="text-xs text-slate-400 flex items-center gap-2">
          방금 {lastRun.auto_approved}건 자동 통과(표본 {lastRun.sampled}건).
          <button className="text-indigo-600 dark:text-indigo-400 hover:underline"
            onClick={() => window.confirm(`방금 자동 통과된 ${lastRun.auto_approved}건을 검토대기로 되돌릴까요?`) && revert.mutate({ run_id: lastRun.run_id!, namespace })}>
            되돌리기
          </button>
        </div>
      )}

      {!s.rule_active && rule?.paused_at && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 dark:border-rose-800/50 dark:bg-rose-950/30 px-3 py-2 text-xs flex items-center gap-2 flex-wrap">
          <PauseCircle className="w-4 h-4 text-rose-600 dark:text-rose-400" />
          <span className="text-rose-700 dark:text-rose-300 font-medium">자동 통과 멈춤</span>
          <span className="text-slate-400">{rule.paused_reason}</span>
          {isAdmin && (
            <span className="ml-auto flex gap-1.5">
              {autoCount > 0 && (
                <Button variant="ghost" size="sm" loading={revert.isPending}
                  onClick={() => window.confirm(`이 파트(${namespace})에서 이 규칙으로 자동 통과된 ${autoCount}건을 전부 검토대기로 되돌릴까요?`) && revert.mutate({ rule_key: RULE, namespace })}
                  title="이미 자동 통과된 항목은 그대로 둡니다 — 의심되면 되돌려 사람이 다시 봅니다">
                  자동 통과 {autoCount}건 되돌리기
                </Button>
              )}
              <Button variant="secondary" size="sm" loading={resume.isPending} onClick={() => resume.mutate()}
                title="반려된 표본을 확인했고 규칙에 문제가 없다고 판단되면 재개">
                재개
              </Button>
            </span>
          )}
        </div>
      )}
    </div>
  );
}
