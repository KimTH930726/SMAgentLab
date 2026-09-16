import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { FlaskConical, ArrowRight, Info, Check } from 'lucide-react';
import { runTrack2, getTrack2Axes, type Track2Result } from '../../api/policy';
import { Button } from '../ui/Button';
import { Badge } from '../ui/Badge';

const TYPE_INFO: Record<string, { label: string; example: string }> = {
  param: { label: '숫자·조건 질문', example: '예: "배달비 얼마야?", "장바구니 몇 개까지?"' },
  narrative: { label: '설명 질문', example: '예: "재고 없으면 화면에 어떻게 표시돼?"' },
  navigation: { label: '분류 전체 보기', example: '예: "배송 관련 정책 다 보여줘"' },
  condition_filter: { label: '조건으로 걸러보기', example: '예: "특정 매장에서만 적용되는 규칙 알려줘"' },
};

// 실험실 게이트 작업2(2026-09-15) — "이 지표가 왜 필요한가"를 소비 패턴에 매핑한 결정론
// 규칙표. LLM 판단이 필요 없는 고정 매핑(work-os 전달 프롬프트 §작업2) — 지금 이 시스템에
// 실제로 존재하는 3가지 소비 경로에 그대로 대응된다: ① 채팅 답변 생성은 top_k 전체를 LLM
// 프롬프트에 넣음(풀컨텍스트 주입) → hit@K, ② 화면 근거 카드는 1건으로 압축해서 보여줌
// (project_policy_pipeline 참고) → Top-1 Accuracy, ③ 리랭커/후보 정제 작업 시엔 후보군
// 자체의 잡음 비율이 중요 → precision@K. 채널기여도(RDB/벡터)는 소비 패턴이 아니라 "B안
// 내부에서 뭐가 일하는지" 진단용이라 규칙표엔 안 넣고 별도 지표로 둠.
type MetricKey = 'hitK' | 'top1' | 'precision' | 'channel';

const METRIC_INFO: Record<MetricKey, { label: string; recommendedFor: string }> = {
  hitK: { label: 'hit@K', recommendedFor: '풀컨텍스트 주입 — 답변 생성 시 LLM이 상위 K개를 전부 참고' },
  top1: { label: 'Top-1 Accuracy', recommendedFor: '근거카드 1건 노출 — 화면엔 1위 결과만 보여줄 때(지금 채팅 UI 방식)' },
  precision: { label: 'Precision@K', recommendedFor: '후보 정제 필요 — 검색 후보 자체의 잡음을 줄이는 작업 중일 때' },
  channel: { label: '채널기여도', recommendedFor: 'B안 내부에서 표(RDB)·의미검색(벡터) 중 뭐가 일하는지 진단할 때' },
};
const ALL_METRICS: MetricKey[] = ['hitK', 'top1', 'precision', 'channel'];

// 저장 전략 현황판(2026-09-15, 사용자 요청) — "지금 뭘 쓰고 있는지"를 문단이 아니라 체크
// 배지로 한눈에. active:false 항목(그래프 등)은 아직 안 쓰지만 향후 확장 후보를 미리
// 자리만 잡아둔 것 — 실제로 도입되면 active만 true로 바꾸면 된다(목록 구조 변경 불필요).
type StorageStrategy = {
  key: string;
  label: string;
  active: boolean;
  detail: string;
};
const STORAGE_STRATEGIES: StorageStrategy[] = [
  {
    key: 'rdb', label: 'RDB (정확 조회)', active: true,
    detail: '숫자·조건 항목을 표로 저장해 정확히 조회. 2026-09-10부터 한국어 조사·어미 제거 적용 — "배달비가"/"배달비는"처럼 표현이 달라도 같은 항목으로 찾음.',
  },
  {
    key: 'vector', label: '벡터 (의미검색)', active: true,
    detail: '설명글을 임베딩으로 저장해 의미 기반으로 검색. 2026-09-11부터 nlpai-lab/KURE-v1(1024차원) 사용.',
  },
  {
    key: 'graph', label: '그래프', active: false,
    detail: '아직 도입 안 함 — 정책 간 참조·의존 관계가 늘어나면 검토할 확장 후보.',
  },
];

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
  const [visibleMetrics, setVisibleMetrics] = useState<Set<MetricKey>>(new Set(ALL_METRICS));
  const [axis, setAxis] = useState('policy');

  // 비교 가능한 데이터 축 목록(엔진 파라미터화, 2026-09-16) — 지금은 "정책서" 하나뿐이라
  // 드롭다운도 사실상 고정값이지만, 새 축(CMDB 등)이 백엔드에 등록되면 이 목록이 그대로
  // 늘어나서 선택지가 생긴다 — 미리 화면에 자리를 잡아두는 것.
  const { data: axes = [{ key: 'policy', label: '정책서 (A/B)' }] } = useQuery({
    queryKey: ['track2-axes'],
    queryFn: getTrack2Axes,
    staleTime: 5 * 60_000,
  });

  const runMutation = useMutation({
    mutationFn: () => runTrack2(10, axis),
    onSuccess: (data) => setLastResult(data),
  });

  const toggleMetric = (key: MetricKey) => {
    setVisibleMetrics((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

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
        <div className="flex items-center gap-2">
          <div>
            <label className="block text-[10px] text-slate-500 mb-1">데이터 축</label>
            <select
              value={axis}
              onChange={(e) => setAxis(e.target.value)}
              disabled={axes.length <= 1}
              title={axes.length <= 1 ? '아직 정책서 축 하나뿐 — 새 축이 추가되면 여기서 고를 수 있습니다' : undefined}
              className="w-40 bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 disabled:opacity-60 disabled:cursor-not-allowed"
            >
              {axes.map((a) => <option key={a.key} value={a.key}>{a.label}</option>)}
            </select>
          </div>
          <Button variant="primary" size="sm" loading={runMutation.isPending} onClick={() => runMutation.mutate()}>
            {runMutation.isPending ? '실행 중... (몇 분 소요)' : '비교 실행'}
          </Button>
        </div>
      </div>

      {/* 저장 전략 현황판 — B안이 지금 실제로 쓰는 저장 방식을 체크 배지로. 그래프처럼 아직
          안 쓰는 확장 후보도 자리만 비활성 배지로 잡아둬서, 나중에 도입되면 active만
          뒤집으면 되게(목록 구조를 다시 안 짜도 됨). */}
      <div className="px-4 py-3 bg-slate-800 border border-slate-700 rounded-xl">
        <p className="text-[11px] text-slate-500 mb-2">B안(지금 방식)이 쓰는 저장 전략</p>
        <div className="flex flex-wrap gap-2">
          {STORAGE_STRATEGIES.map((s) => (
            <span
              key={s.key}
              title={s.detail}
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-medium border ${
                s.active
                  ? 'bg-emerald-500/10 border-emerald-600/40 text-emerald-300'
                  : 'bg-transparent border-slate-700 text-slate-600 border-dashed'
              }`}
            >
              {s.active
                ? <Check className="w-3 h-3" />
                : <span className="w-3 h-3 rounded-full border border-slate-600 flex-shrink-0" />}
              {s.label}
              {!s.active && <span className="text-slate-700">· 미도입</span>}
            </span>
          ))}
        </div>
      </div>

      {/* 비교 대상 설명 — 결과가 없어도 항상 보여서 "A/B가 뭔지"부터 이해되게 */}
      <div className="flex gap-3 px-4 py-3 bg-indigo-900/20 border border-indigo-700/30 rounded-xl text-xs text-slate-300 leading-relaxed">
        <Info className="w-4 h-4 text-indigo-500 dark:text-indigo-400 flex-shrink-0 mt-0.5" />
        <div className="space-y-1.5">
          <p><b>A안(지식 그대로 저장)</b>: 정책서 내용을 통째로 문장으로 저장 — 다른 일반 지식 문서와 똑같이 취급</p>
          <p><b>B안(지금 우리가 쓰는 방식)</b>: 숫자·조건은 표(정확 조회)로, 설명글은 의미 검색(벡터)으로 나눠서 저장 — 위 배지의 상세는 마우스를 올려 확인</p>
          <p className="text-slate-500">89개의 실제 질문을 두 방식에 똑같이 던져서, 각 방식이 정답을 찾아내는 비율을 비교합니다.</p>
        </div>
      </div>

      {/* 지표 선택 — 실험실 게이트 작업2. 결과가 없어도 항상 보여서 미리 고를 수 있게 */}
      <div className="flex flex-wrap items-center gap-2 px-4 py-3 bg-slate-800 border border-slate-700 rounded-xl">
        <span className="text-[11px] text-slate-500 mr-1">표시할 지표</span>
        {ALL_METRICS.map((key) => {
          const on = visibleMetrics.has(key);
          return (
            <button
              key={key}
              type="button"
              onClick={() => toggleMetric(key)}
              title={`추천: ${METRIC_INFO[key].recommendedFor}`}
              className={`flex items-center gap-1 px-2.5 py-1 rounded-full text-[11px] font-medium border transition-colors ${
                on
                  ? 'bg-indigo-500/20 border-indigo-500/50 text-indigo-300'
                  : 'bg-transparent border-slate-700 text-slate-500 hover:text-slate-400'
              }`}
            >
              {on && <Check className="w-3 h-3" />}
              {METRIC_INFO[key].label}
            </button>
          );
        })}
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
            {visibleMetrics.has('hitK') && (
              <>
                <p className="text-[11px] font-semibold tracking-wide uppercase text-indigo-400 mb-2">
                  결론 <span className="normal-case font-normal text-slate-500">— hit@K, 추천: {METRIC_INFO.hitK.recommendedFor}</span>
                </p>
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
              </>
            )}
            {visibleMetrics.has('top1') && (
              <div
                className={`flex items-center gap-4 text-[11px] text-slate-500 ${visibleMetrics.has('hitK') ? 'mt-4 pt-3 border-t border-slate-700/60' : ''}`}
                title="검색 결과 1위가 실제 정답인 비율입니다. B안은 표(RDB)/의미검색(벡터)이 분리된 채널이라 '진짜 하나의 1위'가 없어 채널별로 따로 잽니다."
              >
                <span>Top-1 Accuracy <span className="text-slate-600">— {METRIC_INFO.top1.recommendedFor}</span></span>
                <span className="font-mono tabular-nums text-slate-400">A {(lastResult.a_top1_accuracy * 100).toFixed(0)}%</span>
                <span className="font-mono tabular-nums text-slate-400">B(표) {(lastResult.b_top1_param_accuracy * 100).toFixed(0)}%</span>
                <span className="font-mono tabular-nums text-slate-400">B(의미검색) {(lastResult.b_top1_narrative_accuracy * 100).toFixed(0)}%</span>
              </div>
            )}
            {visibleMetrics.has('precision') && (
              <div
                className={`flex items-center gap-4 text-[11px] text-slate-500 ${visibleMetrics.has('hitK') || visibleMetrics.has('top1') ? 'mt-2' : ''}`}
                title="검색 결과로 나온 후보들 중 실제로 정답인 항목의 비율입니다. 정답을 찾아낸 비율(hit@K)이 같아도, 후보에 잡음(무관한 항목)이 많이 섞이면 이 값이 낮아집니다 — 낮을수록 AI가 답을 만들 때 참고하는 자료에 잡음이 많다는 뜻입니다."
              >
                <span>정답 집중도(precision) <span className="text-slate-600">— {METRIC_INFO.precision.recommendedFor}</span></span>
                <span className="font-mono tabular-nums text-slate-400">A {(lastResult.a_precision * 100).toFixed(0)}%</span>
                <span className="font-mono tabular-nums text-slate-400">B {(lastResult.b_precision * 100).toFixed(0)}%</span>
              </div>
            )}
            {visibleMetrics.has('channel') && (
              <div
                className="flex items-center gap-4 mt-2 text-[11px] text-slate-500"
                title="B안이 찾은 정답 중, 표(RDB) 조회로 찾았는지·의미검색(벡터)으로 찾았는지·둘 다에서 찾았는지를 나눈 비율입니다. 어느 한쪽이 0에 가까우면 그 방식은 굳이 안 써도 된다는 뜻이고, 둘 다 유의미하면 두 방식을 같이 쓰는 게 근거가 있다는 뜻입니다."
              >
                <span>B 근거 <span className="text-slate-600">— {METRIC_INFO.channel.recommendedFor}</span></span>
                <span className="font-mono tabular-nums text-slate-400">표만 {(lastResult.b_hit_rdb_only * 100).toFixed(0)}%</span>
                <span className="font-mono tabular-nums text-slate-400">의미검색만 {(lastResult.b_hit_vector_only * 100).toFixed(0)}%</span>
                <span className="font-mono tabular-nums text-slate-400">둘 다 {(lastResult.b_hit_both * 100).toFixed(0)}%</span>
              </div>
            )}
            {visibleMetrics.size === 0 && (
              <p className="text-xs text-slate-500 text-center py-4">위에서 지표를 하나 이상 선택하세요.</p>
            )}
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
                    {visibleMetrics.has('hitK') && (
                      <div className="grid grid-cols-[1fr_110px] gap-3 items-center">
                        <div className="relative h-5 bg-slate-900 rounded overflow-hidden">
                          <div className="absolute inset-y-0 left-0 bg-slate-600" style={{ width: `${t.a_hit_rate * 100}%` }} />
                          <div className="absolute inset-y-0 left-0 bg-indigo-500 opacity-90" style={{ width: `${t.b_hit_rate * 100}%` }} />
                        </div>
                        <div className="text-xs font-mono tabular-nums text-right text-slate-400">
                          A {(t.a_hit_rate * 100).toFixed(0)} → B <span className={t.b_hit_rate >= t.a_hit_rate ? 'text-emerald-400' : 'text-rose-400'}>{(t.b_hit_rate * 100).toFixed(0)}</span>
                        </div>
                      </div>
                    )}
                    {visibleMetrics.has('top1') && (
                      <div
                        className="text-right text-[11px] font-mono tabular-nums text-slate-600 mt-0.5"
                        title="검색 결과 1위가 실제 정답인 비율 — 화면 근거카드는 1건만 노출될 때 이 지표가 맞습니다."
                      >
                        Top-1 A {(t.a_top1_accuracy * 100).toFixed(0)} · B(표) {(t.b_top1_param_accuracy * 100).toFixed(0)} · B(의미검색) {(t.b_top1_narrative_accuracy * 100).toFixed(0)}
                      </div>
                    )}
                    {visibleMetrics.has('precision') && (
                      <div
                        className="text-right text-[11px] font-mono tabular-nums text-slate-600 mt-0.5"
                        title="정답 집중도(precision) — 검색 후보 중 실제 정답 비율"
                      >
                        집중도 A {(t.a_precision * 100).toFixed(0)} · B {(t.b_precision * 100).toFixed(0)}
                      </div>
                    )}
                    {visibleMetrics.has('channel') && (
                      <div
                        className="text-right text-[11px] font-mono tabular-nums text-slate-600"
                        title="B안이 이 유형에서 정답을 찾은 경로 — 표(RDB)만/의미검색(벡터)만/둘 다"
                      >
                        B 근거 표 {(t.b_hit_rdb_only * 100).toFixed(0)} · 의미검색 {(t.b_hit_vector_only * 100).toFixed(0)} · 둘다 {(t.b_hit_both * 100).toFixed(0)}
                      </div>
                    )}
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
