import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { FlaskConical, ArrowRight, Info } from 'lucide-react';
import { runTrack2, type Track2Result } from '../../api/policy';
import { Button } from '../ui/Button';
import { Badge } from '../ui/Badge';

const TYPE_INFO: Record<string, { label: string; example: string }> = {
  param: { label: '숫자·조건 질문', example: '예: "배달비 얼마야?", "장바구니 몇 개까지?"' },
  narrative: { label: '설명 질문', example: '예: "재고 없으면 화면에 어떻게 표시돼?"' },
  navigation: { label: '분류 전체 보기', example: '예: "배송 관련 정책 다 보여줘"' },
  condition_filter: { label: '조건으로 걸러보기', example: '예: "특정 매장에서만 적용되는 규칙 알려줘"' },
};

/**
 * 저장소 전략 실험실(Track 2) — 버튼 하나로 A(rag_knowledge 지식-only) vs B(하이브리드 스키마)
 * 비교를 재실행하고 결과를 바로 본다. docs/policy-doc-pipeline-plan.md §4 실험을 매번 스크립트로
 * 짜는 대신 여기서 재실행 가능하게 만들었다(2026-09-04) — 처음 만든 HTML 목업에서 "이런 화면이면
 * 팀에 소개하기 좋겠다"는 반응을 받아 실제로 동작하는 최소 버전으로 승격.
 *
 * 실행에 몇 분 걸린다(전체 policy_item 규모만큼 임베딩 재계산) — 실시간 기능이 아니라 가끔
 * 재측정하는 용도라 동기 호출 + 로딩 상태로 충분하다고 판단(별도 잡 큐 없음, YAGNI).
 *
 * 2026-09-06 사용자 피드백: "A/B, 정답률 %" 같은 숫자만 있고 무슨 뜻인지 안 와닿는다 — 일반인이
 * 봐도 "그래서 뭐가 더 나은지" 바로 이해되게 각 용어를 풀어 설명하고 질문 유형마다 예시를 붙임.
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
          <h2 className="text-lg font-semibold text-slate-200 flex items-center gap-2">
            <FlaskConical className="w-5 h-5 text-indigo-500 dark:text-indigo-400" />
            저장소 전략 실험실
          </h2>
          <p className="text-xs text-slate-500 mt-1">
            정책서를 저장하는 두 가지 방식 중 어느 쪽이 질문에 더 정확히 답하는지 비교합니다.
          </p>
        </div>
        <Button variant="primary" size="sm" loading={runMutation.isPending} onClick={() => runMutation.mutate()}>
          {runMutation.isPending ? '실행 중... (몇 분 소요)' : '비교 실행'}
        </Button>
      </div>

      {/* 비교 대상 설명 — 결과가 없어도 항상 보여서 "A/B가 뭔지"부터 이해되게 */}
      <div className="flex gap-3 px-4 py-3 bg-indigo-900/20 border border-indigo-700/30 rounded-xl text-xs text-slate-300 leading-relaxed">
        <Info className="w-4 h-4 text-indigo-500 dark:text-indigo-400 flex-shrink-0 mt-0.5" />
        <div className="space-y-1.5">
          <p><b>A안(지식 그대로 저장)</b>: 정책서 내용을 통째로 문장으로 저장 — 다른 일반 지식 문서와 똑같이 취급</p>
          <p><b>B안(지금 우리가 쓰는 방식)</b>: 숫자·조건은 표(정확 조회)로, 설명글은 의미 검색(벡터)으로 나눠서 저장</p>
          <p className="text-slate-500">89개의 실제 질문을 두 방식에 똑같이 던져서, 각 방식이 정답을 찾아내는 비율을 비교합니다.</p>
          <p
            className="text-slate-500"
            title="표(RDB)는 한글 조사·어미까지 그대로 비교하기 때문에 '담을'과 '담기'처럼 같은 뜻이어도 표현이 다르면 못 찾는 경우가 많았습니다. 89문항 실측: 표 검색만 놓고 봤을 때 정답률 34.8%→73.9%(파라미터 질문), 지금 이 화면의 B안 전체로는 정답률 +4.5%p."
          >
            2026-09-10부터 B안의 표(RDB) 검색에 한국어 조사·어미 제거가 적용돼 있습니다 — 표현이
            달라도("배달비가"/"배달비는") 같은 항목으로 찾아냅니다.
          </p>
        </div>
      </div>

      {runMutation.isPending && (
        <div className="flex items-center gap-2 py-10 text-slate-400 text-sm justify-center">
          <div className="w-4 h-4 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
          전체 정책 항목을 임시 저장소에 옮겨 담고 89개 질문으로 비교 중입니다 — 몇 분 걸립니다.
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
                ? <>89개 질문 중 <b className="text-slate-100">B안(지금 방식)</b>이 더 많이 정답을 찾아냈습니다 — 지금처럼 저장하는 게 낫다는 뜻입니다.</>
                : <><b className="text-slate-100">A안(지식 그대로 저장)</b>이 지금 방식보다 정답을 더 많이 찾아냈습니다 — 지금 방식 보완이 필요합니다.</>}
            </p>
            <div className="flex items-center gap-4">
              <div>
                <div className="text-3xl font-bold text-slate-400 font-mono tabular-nums">{(lastResult.a_hit_rate * 100).toFixed(1)}%</div>
                <div className="text-xs text-slate-500 mt-1">A안 · 지식 그대로 저장</div>
              </div>
              <ArrowRight className="w-5 h-5 text-slate-600" />
              <div>
                <div className={`text-3xl font-bold font-mono tabular-nums ${lastResult.b_hit_rate >= lastResult.a_hit_rate ? 'text-emerald-400' : 'text-rose-400'}`}>
                  {(lastResult.b_hit_rate * 100).toFixed(1)}%
                </div>
                <div className="text-xs text-slate-500 mt-1">B안 · 지금 우리 방식</div>
              </div>
            </div>
            <p className="text-[11px] text-slate-500 mt-3">
              % = 89개 질문 중 정답이 검색 결과 상위 {lastResult.top_k}개 안에 들어온 비율
            </p>
            <div
              className="flex items-center gap-4 mt-4 pt-3 border-t border-slate-700/60 text-[11px] text-slate-500"
              title="검색 결과로 나온 후보들 중 실제로 정답인 항목의 비율입니다. 정답을 찾아낸 비율(위)이 같아도, 후보에 잡음(무관한 항목)이 많이 섞이면 이 값이 낮아집니다 — 낮을수록 AI가 답을 만들 때 참고하는 자료에 잡음이 많다는 뜻입니다."
            >
              <span>정답 집중도(precision)</span>
              <span className="font-mono tabular-nums text-slate-400">A {(lastResult.a_precision * 100).toFixed(0)}%</span>
              <span className="font-mono tabular-nums text-slate-400">B {(lastResult.b_precision * 100).toFixed(0)}%</span>
            </div>
            <div
              className="flex items-center gap-4 mt-2 text-[11px] text-slate-500"
              title="B안이 찾은 정답 중, 표(RDB) 조회로 찾았는지·의미검색(벡터)으로 찾았는지·둘 다에서 찾았는지를 나눈 비율입니다. 어느 한쪽이 0에 가까우면 그 방식은 굳이 안 써도 된다는 뜻이고, 둘 다 유의미하면 두 방식을 같이 쓰는 게 근거가 있다는 뜻입니다."
            >
              <span>B 근거</span>
              <span className="font-mono tabular-nums text-slate-400">표만 {(lastResult.b_hit_rdb_only * 100).toFixed(0)}%</span>
              <span className="font-mono tabular-nums text-slate-400">의미검색만 {(lastResult.b_hit_vector_only * 100).toFixed(0)}%</span>
              <span className="font-mono tabular-nums text-slate-400">둘 다 {(lastResult.b_hit_both * 100).toFixed(0)}%</span>
            </div>
          </div>

          <div className="bg-slate-800 border border-slate-700 rounded-xl px-5 py-5">
            <p className="text-xs font-medium text-slate-400 mb-1">질문 유형별로 뜯어보면</p>
            <p className="text-[11px] text-slate-500 mb-4">질문 성격에 따라 어느 저장 방식이 유리한지가 다를 수 있어 유형을 나눠서 봅니다.</p>
            <div className="space-y-5">
              {lastResult.by_type.map((t) => {
                const info = TYPE_INFO[t.type] ?? { label: t.type, example: '' };
                return (
                  <div key={t.type}>
                    <div className="flex items-baseline justify-between mb-1.5">
                      <div>
                        <span className="text-sm font-medium text-slate-200">{info.label}</span>
                        <span className="text-[11px] text-slate-500 ml-2">{info.example}</span>
                      </div>
                      <span className="text-[11px] text-slate-500 font-mono">질문 {t.n}개</span>
                    </div>
                    <div className="grid grid-cols-[1fr_110px] gap-3 items-center">
                      <div className="relative h-5 bg-slate-900 rounded overflow-hidden">
                        <div className="absolute inset-y-0 left-0 bg-slate-600" style={{ width: `${t.a_hit_rate * 100}%` }} />
                        <div className="absolute inset-y-0 left-0 bg-indigo-500 opacity-90" style={{ width: `${t.b_hit_rate * 100}%` }} />
                      </div>
                      <div className="text-xs font-mono tabular-nums text-right text-slate-400">
                        A {(t.a_hit_rate * 100).toFixed(0)} → B <span className={t.b_hit_rate >= t.a_hit_rate ? 'text-emerald-400' : 'text-rose-400'}>{(t.b_hit_rate * 100).toFixed(0)}</span>
                      </div>
                    </div>
                    <div
                      className="text-right text-[11px] font-mono tabular-nums text-slate-600 mt-0.5"
                      title="정답 집중도(precision) — 검색 후보 중 실제 정답 비율"
                    >
                      집중도 A {(t.a_precision * 100).toFixed(0)} · B {(t.b_precision * 100).toFixed(0)}
                    </div>
                    <div
                      className="text-right text-[11px] font-mono tabular-nums text-slate-600"
                      title="B안이 이 유형에서 정답을 찾은 경로 — 표(RDB)만/의미검색(벡터)만/둘 다"
                    >
                      B 근거 표 {(t.b_hit_rdb_only * 100).toFixed(0)} · 의미검색 {(t.b_hit_vector_only * 100).toFixed(0)} · 둘다 {(t.b_hit_both * 100).toFixed(0)}
                    </div>
                  </div>
                );
              })}
            </div>
            <div className="flex items-center gap-3 mt-5 pt-4 border-t border-slate-700 text-[11px] text-slate-500">
              <span className="flex items-center gap-1.5"><i className="inline-block w-2.5 h-2.5 rounded-sm bg-slate-600" />A안 · 지식 그대로 저장</span>
              <span className="flex items-center gap-1.5"><i className="inline-block w-2.5 h-2.5 rounded-sm bg-indigo-500" />B안 · 지금 우리 방식</span>
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
