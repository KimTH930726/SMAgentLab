import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { FlaskConical, ArrowRight } from 'lucide-react';
import { runTrack2, type Track2Result } from '../../api/policy';
import { Button } from '../ui/Button';
import { Badge } from '../ui/Badge';

const TYPE_LABEL: Record<string, string> = {
  param: '파라미터 조회',
  narrative: '서술 Q&A',
  navigation: '카테고리 네비',
  condition_filter: '조건 필터',
};

/**
 * 저장소 전략 실험실(Track 2) — 버튼 하나로 A(rag_knowledge 지식-only) vs B(하이브리드 스키마)
 * 비교를 재실행하고 결과를 바로 본다. docs/policy-doc-pipeline-plan.md §4 실험을 매번 스크립트로
 * 짜는 대신 여기서 재실행 가능하게 만들었다(2026-09-04) — 처음 만든 HTML 목업에서 "이런 화면이면
 * 팀에 소개하기 좋겠다"는 반응을 받아 실제로 동작하는 최소 버전으로 승격.
 *
 * 실행에 몇 분 걸린다(전체 policy_item 규모만큼 임베딩 재계산) — 실시간 기능이 아니라 가끔
 * 재측정하는 용도라 동기 호출 + 로딩 상태로 충분하다고 판단(별도 잡 큐 없음, YAGNI).
 */
export function PolicyLab() {
  const [lastResult, setLastResult] = useState<Track2Result | null>(null);

  const runMutation = useMutation({
    mutationFn: () => runTrack2(),
    onSuccess: (data) => setLastResult(data),
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h2 className="text-lg font-semibold text-slate-800 dark:text-slate-200 flex items-center gap-2">
            <FlaskConical className="w-5 h-5 text-indigo-500 dark:text-indigo-400" />
            저장소 전략 실험실
          </h2>
          <p className="text-xs text-slate-500 mt-1">
            정책서를 지식-only(A)로 저장할지 지금의 하이브리드 스키마(B, RDB 파라미터+벡터 서술)로
            저장할지, 골든셋으로 실측 비교합니다.
          </p>
        </div>
        <Button variant="primary" size="sm" loading={runMutation.isPending} onClick={() => runMutation.mutate()}>
          {runMutation.isPending ? '실행 중... (몇 분 소요)' : '비교 실행'}
        </Button>
      </div>

      {runMutation.isPending && (
        <div className="flex items-center gap-2 py-10 text-slate-400 text-sm justify-center">
          <div className="w-4 h-4 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
          전체 정책 항목을 임시 네임스페이스에 색인하고 골든셋으로 비교 중입니다 — 몇 분 걸립니다.
        </div>
      )}

      {runMutation.isError && (
        <div className="bg-rose-900/20 border border-rose-700/40 rounded-xl px-4 py-3 text-sm text-rose-300">
          {String(runMutation.error)}
        </div>
      )}

      {!runMutation.isPending && lastResult && (
        <>
          <div className="bg-gradient-to-b from-indigo-900/20 to-slate-800 border border-indigo-700/30 rounded-xl px-5 py-5">
            <p className="text-[11px] font-semibold tracking-wide uppercase text-indigo-400 mb-2">결론</p>
            <p className="text-sm text-slate-300 leading-relaxed mb-4">
              {lastResult.b_hit_rate >= lastResult.a_hit_rate
                ? <>지금 <b className="text-slate-100">하이브리드 스키마(B)</b>가 지식-only(A) 대비 정답률이 높습니다.</>
                : <>지금은 <b className="text-slate-100">지식-only(A)</b>가 하이브리드(B)보다 정답률이 높습니다 — 하이브리드 쪽 보완이 필요합니다.</>}
            </p>
            <div className="flex items-center gap-4">
              <div>
                <div className="text-3xl font-bold text-slate-400 font-mono tabular-nums">{(lastResult.a_hit_rate * 100).toFixed(1)}%</div>
                <div className="text-xs text-slate-500 mt-1">A · 지식-only</div>
              </div>
              <ArrowRight className="w-5 h-5 text-slate-600" />
              <div>
                <div className={`text-3xl font-bold font-mono tabular-nums ${lastResult.b_hit_rate >= lastResult.a_hit_rate ? 'text-emerald-400' : 'text-rose-400'}`}>
                  {(lastResult.b_hit_rate * 100).toFixed(1)}%
                </div>
                <div className="text-xs text-slate-500 mt-1">B · 하이브리드</div>
              </div>
            </div>
          </div>

          <div className="bg-slate-800 border border-slate-700 rounded-xl px-5 py-5">
            <p className="text-xs font-medium text-slate-400 mb-4">유형별 정답률 (hit@{lastResult.top_k})</p>
            <div className="space-y-4">
              {lastResult.by_type.map((t) => (
                <div key={t.type} className="grid grid-cols-[120px_1fr_110px] gap-3 items-center">
                  <div>
                    <div className="text-sm font-medium text-slate-200">{TYPE_LABEL[t.type] ?? t.type}</div>
                    <div className="text-[11px] text-slate-500 font-mono">n={t.n}</div>
                  </div>
                  <div className="relative h-5 bg-slate-900 rounded overflow-hidden">
                    <div className="absolute inset-y-0 left-0 bg-slate-600" style={{ width: `${t.a_hit_rate * 100}%` }} />
                    <div className="absolute inset-y-0 left-0 bg-indigo-500 opacity-90" style={{ width: `${t.b_hit_rate * 100}%` }} />
                  </div>
                  <div className="text-xs font-mono tabular-nums text-right text-slate-400">
                    {(t.a_hit_rate * 100).toFixed(1)} → <span className={t.b_hit_rate >= t.a_hit_rate ? 'text-emerald-400' : 'text-rose-400'}>{(t.b_hit_rate * 100).toFixed(1)}</span>
                  </div>
                </div>
              ))}
            </div>
            <div className="flex items-center gap-3 mt-4 text-[11px] text-slate-500">
              <span className="flex items-center gap-1.5"><i className="inline-block w-2.5 h-2.5 rounded-sm bg-slate-600" />A · 지식-only</span>
              <span className="flex items-center gap-1.5"><i className="inline-block w-2.5 h-2.5 rounded-sm bg-indigo-500" />B · 하이브리드</span>
            </div>
          </div>

          <div className="flex items-center gap-2 text-[11px] text-slate-500 font-mono">
            <Badge color="slate">{lastResult.golden_set_file}</Badge>
            <span>전체 {lastResult.total_n}문항 · 소요 {lastResult.duration_seconds}초</span>
          </div>
        </>
      )}

      {!runMutation.isPending && !lastResult && !runMutation.isError && (
        <div className="text-center py-14 text-slate-500 text-sm">
          아직 이번 세션에서 실행한 결과가 없습니다. "비교 실행"을 눌러 시작하세요.
        </div>
      )}
    </div>
  );
}
