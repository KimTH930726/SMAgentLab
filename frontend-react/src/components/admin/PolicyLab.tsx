import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { FlaskConical, Info, Check, ChevronDown, ChevronUp, TrendingUp, ArrowUpRight, ArrowDownRight } from 'lucide-react';
import { getTrack2Axes, getTrack2History } from '../../api/policy';
import { useTrack2Store, runTrack2Comparison } from '../../store/useTrack2Store';
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

const METRIC_INFO: Record<MetricKey, { label: string; meaning: string; recommendedFor: string }> = {
  hitK: {
    label: 'hit@K',
    meaning: '정답이 검색 결과 상위 K개 안에 들어왔는지(있다/없다)',
    recommendedFor: '풀컨텍스트 주입 상황 — 채팅 답변 생성 시 LLM이 상위 K개를 전부 참고하기 때문',
  },
  top1: {
    label: 'Top-1',
    meaning: '검색 결과 1위가 곧바로 정답인지',
    recommendedFor: '근거카드 1건 노출 상황 — 화면엔 1위 결과만 보여주는 지금 채팅 UI 방식이기 때문',
  },
  precision: {
    label: 'Precision@K',
    meaning: '검색 후보들 중 실제로 정답인 비율(후보에 잡음이 얼마나 섞였나)',
    recommendedFor: '후보 정제 작업 상황 — 검색 후보 자체의 잡음을 줄이려는 작업 중일 때 기준으로 삼기 좋음',
  },
  channel: {
    label: '채널기여도',
    meaning: 'B안(하이브리드)이 표(RDB)·의미검색(벡터) 중 어느 쪽으로 정답을 찾았는지',
    recommendedFor: 'B안 내부 진단 상황 — 표/의미검색 중 뭐가 실제로 일하는지 확인할 때',
  },
};
const ALL_METRICS: MetricKey[] = ['hitK', 'top1', 'precision', 'channel'];

// 저장 전략 현황판(2026-09-15) — "지금 뭘 쓰고 있는지"를 문단이 아니라 체크 배지로.
// active:false 항목(그래프 등)은 아직 안 쓰지만 향후 확장 후보를 미리 자리만 잡아둔 것.
type StorageStrategy = { key: string; label: string; active: boolean; detail: string };
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
 * 저장소 전략 실험실(Track 2) — **반복 실행 도구**. 버튼 하나로 A(rag_knowledge 지식-only)
 * vs B(하이브리드 스키마) 비교를 재실행하고, 매번 새로 쌓이는 실행 이력(policy_track2_run)
 * 위에서 추이까지 본다. docs/policy-doc-pipeline-plan.md §4.
 *
 * 2026-09-17 재설계(2번째) — "매번 실행할 때마다 A안/B안 설명, 지표 설명 같은 긴 문단이
 * 그대로 다시 렌더링돼서 도구가 아니라 매번 새로 읽는 보고서처럼 느껴진다"는 실사용
 * 지적. 설명은 기본적으로 접어두고(ⓘ 토글), 화면은 (1)실행 버튼 (2)이번 결과 숫자
 * (3)실행 이력 추이 3가지를 중심으로 — "본다"가 아니라 "쓴다"에 맞춘 레이아웃으로 교체.
 * 지표 카드형 설명(2026-09-17 1차 재설계, MetricCard)은 접힌 패널 안으로 이동해 유지.
 */
export function PolicyLab() {
  const [visibleMetrics, setVisibleMetrics] = useState<Set<MetricKey>>(new Set(ALL_METRICS));
  const [axis, setAxis] = useState('policy');
  const [showInfo, setShowInfo] = useState(false);
  // 비교 실행 상태는 컴포넌트 밖(zustand)에 있다 — 이 화면을 나갔다 들어와도 실행 중
  // 표시/결과가 안 끊기게(useTrack2Store.ts 참고, "실행했다가 다른 화면 갔다오면
  // 초기화된다"는 지적으로 2026-09-23 수정).
  const running = useTrack2Store((s) => s.running);
  const runError = useTrack2Store((s) => s.error);
  const lastResult = useTrack2Store((s) => s.lastResult);

  const { data: axes = [{ key: 'policy', label: '정책서 (A/B)' }] } = useQuery({
    queryKey: ['track2-axes'],
    queryFn: getTrack2Axes,
    staleTime: 5 * 60_000,
  });

  const { data: history = [] } = useQuery({
    queryKey: ['track2-history-lab'],
    queryFn: () => getTrack2History(10),
    staleTime: 30_000,
  });
  const trendAsc = [...history].reverse(); // 오래된 순으로

  const displayResult = lastResult ?? history[0] ?? null; // 아직 이번 세션에 실행 안 했어도 최근 이력을 바로 보여줌

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
      {/* 툴바 — 실행 도구라는 정체성을 첫 줄부터: 축 선택 + 지표 선택 + 실행 버튼이 전부 한 줄에 */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h2 className="text-lg font-semibold text-slate-200 flex items-center gap-2">
            <FlaskConical className="w-5 h-5 text-indigo-500 dark:text-indigo-400" />
            저장소 전략 실험실
          </h2>
          <button
            type="button"
            onClick={() => setShowInfo((v) => !v)}
            className="flex items-center gap-1 text-xs text-slate-500 hover:text-slate-300 mt-0.5"
          >
            <Info className="w-3.5 h-3.5" />
            A안·B안·지표가 뭔지 설명 보기
            {showInfo ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
          </button>
        </div>
        <div className="flex items-end gap-2">
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
          <Button variant="primary" size="sm" loading={running} onClick={() => runTrack2Comparison(10, axis)}>
            {running ? '실행 중... (몇 분 소요)' : '▶ 비교 실행'}
          </Button>
        </div>
      </div>

      {/* 지표 선택 칩 — 상시 노출(자주 쓰는 컨트롤이라 접지 않음) */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[11px] text-slate-500 mr-1">표시할 지표</span>
        {ALL_METRICS.map((key) => {
          const on = visibleMetrics.has(key);
          return (
            <button
              key={key}
              type="button"
              onClick={() => toggleMetric(key)}
              title={`${METRIC_INFO[key].meaning} — 추천: ${METRIC_INFO[key].recommendedFor}`}
              className={`flex items-center gap-1 px-2.5 py-1 rounded-full text-[11px] font-medium border transition-colors ${
                on
                  ? 'bg-indigo-50 border-indigo-300 text-indigo-700 dark:bg-indigo-500/20 dark:border-indigo-500/50 dark:text-indigo-300'
                  : 'bg-transparent border-slate-700 text-slate-500 hover:text-slate-400'
              }`}
            >
              {on && <Check className="w-3 h-3" />}
              {METRIC_INFO[key].label}
            </button>
          );
        })}
      </div>

      {/* 설명 패널 — 기본 접힘. A안/B안/저장전략/지표 정의가 전부 여기 하나로 모임 */}
      {showInfo && (
        <div className="px-4 py-3 bg-indigo-50 border border-indigo-200 dark:bg-indigo-900/20 dark:border-indigo-700/30 rounded-xl text-xs text-slate-300 leading-relaxed space-y-3">
          <div className="space-y-1.5">
            <p><b>A안(지식 그대로 저장)</b>: 정책서 내용을 통째로 문장으로 저장 — 다른 일반 지식 문서와 똑같이 취급</p>
            <p><b>B안(지금 우리가 쓰는 방식)</b>: 숫자·조건은 표(정확 조회)로, 설명글은 의미 검색(벡터)으로 나눠서 저장</p>
            <p className="text-slate-500">89개의 실제 질문을 두 방식에 똑같이 던져서, 각 방식이 정답을 찾아내는 비율을 비교합니다.</p>
          </div>
          <div>
            <p className="text-slate-500 mb-1.5">B안이 지금 쓰는 저장 전략</p>
            <div className="flex flex-wrap gap-2">
              {STORAGE_STRATEGIES.map((s) => (
                <span
                  key={s.key}
                  title={s.detail}
                  className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-medium border ${
                    s.active
                      ? 'bg-emerald-50 border-emerald-300 text-emerald-700 dark:bg-emerald-500/10 dark:border-emerald-600/40 dark:text-emerald-300'
                      : 'bg-transparent border-slate-700 text-slate-600 border-dashed'
                  }`}
                >
                  {s.active ? <Check className="w-3 h-3" /> : <span className="w-3 h-3 rounded-full border border-slate-600 flex-shrink-0" />}
                  {s.label}
                  {!s.active && <span className="text-slate-700">· 미도입</span>}
                </span>
              ))}
            </div>
          </div>
          <div>
            <p className="text-slate-500 mb-1.5">지표 정의 · 추천 상황</p>
            <div className="space-y-1.5">
              {ALL_METRICS.map((key) => (
                <p key={key}>
                  <b className="text-slate-200">{METRIC_INFO[key].label}</b> — {METRIC_INFO[key].meaning}.{' '}
                  <span className="text-indigo-700 dark:text-indigo-300">추천: {METRIC_INFO[key].recommendedFor}</span>
                </p>
              ))}
            </div>
          </div>
        </div>
      )}

      {running && (
        <div className="flex items-center gap-2 py-10 text-slate-400 text-sm justify-center">
          <div className="w-4 h-4 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
          전체 정책 항목을 임시 저장소에 옮겨 담고 89개 질문으로 비교 중입니다 — 몇 분 걸립니다.
          다른 화면으로 이동해도 계속 진행되고, 돌아오면 결과가 반영돼 있습니다.
        </div>
      )}

      {runError && (
        <div className="bg-rose-50 border border-rose-200 dark:bg-rose-900/20 dark:border-rose-700/40 rounded-xl px-4 py-3 text-sm text-rose-700 dark:text-rose-300">
          {runError}
        </div>
      )}

      {!running && displayResult && (
        <>
          {/* 결과 표 — 지표별 "뭔지"를 표 안에 같이 넣어서 숫자만 보고 헷갈리지 않게.
              hit@K/집중도(A vs B, 깔끔한 쌍)와 Top-1/채널기여도(B 내부 채널별 세부)는
              모양이 달라서 한 표에 억지로 안 합치고 표 2개로 분리. */}
          {!lastResult && (
            <p className="text-[11px] text-amber-600 dark:text-amber-400">⚠ 이번 세션엔 아직 재실행 안 함 — 가장 최근 저장된 결과(추이 그래프의 마지막 점)를 보여주는 중</p>
          )}

          {(visibleMetrics.has('hitK') || visibleMetrics.has('precision')) && (
            <div className="bg-slate-800 border border-slate-700 rounded-xl px-5 py-4">
              <p className="text-xs font-medium text-slate-400 mb-3">A안 vs B안 — 전체 비교</p>
              <div className="overflow-x-auto">
                <table className="w-full text-xs border-collapse">
                  <thead>
                    <tr className="border-b border-slate-700 text-slate-500">
                      <th className="text-left font-medium py-2 pr-3">지표</th>
                      <th className="text-left font-medium py-2 px-3">뜻</th>
                      <th className="text-right font-medium py-2 px-3">A안</th>
                      <th className="text-right font-medium py-2 pl-3">B안(지금 방식)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleMetrics.has('hitK') && (
                      <tr className="border-b border-slate-700/50">
                        <td className="py-2.5 pr-3 text-slate-200 font-medium">hit@K</td>
                        <td className="py-2.5 px-3 text-slate-500">정답이 검색 상위 K개 안에 있었는지</td>
                        <td className="py-2.5 px-3 text-right font-mono tabular-nums text-slate-300">{(displayResult.a_hit_rate * 100).toFixed(1)}%</td>
                        <td className="py-2.5 pl-3 text-right font-mono tabular-nums">
                          <span className={`inline-flex items-center gap-0.5 font-semibold ${displayResult.b_hit_rate >= displayResult.a_hit_rate ? 'text-emerald-500 dark:text-emerald-400' : 'text-rose-500 dark:text-rose-400'}`}>
                            {(displayResult.b_hit_rate * 100).toFixed(1)}%
                            {displayResult.b_hit_rate >= displayResult.a_hit_rate ? <ArrowUpRight className="w-3.5 h-3.5" /> : <ArrowDownRight className="w-3.5 h-3.5" />}
                          </span>
                        </td>
                      </tr>
                    )}
                    {visibleMetrics.has('precision') && (
                      <tr className="last:border-0">
                        <td className="py-2.5 pr-3 text-slate-200 font-medium">Precision@K (집중도)</td>
                        <td className="py-2.5 px-3 text-slate-500">검색 후보 중 실제 정답 비율(낮을수록 잡음 많음)</td>
                        <td className="py-2.5 px-3 text-right font-mono tabular-nums text-slate-300">{(displayResult.a_precision * 100).toFixed(0)}%</td>
                        <td className="py-2.5 pl-3 text-right font-mono tabular-nums text-slate-300">{(displayResult.b_precision * 100).toFixed(0)}%</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
              <p className="text-[11px] text-slate-500 mt-3">
                {displayResult.b_hit_rate >= displayResult.a_hit_rate
                  ? <>89개 질문 중 <b className="text-slate-300">B안(지금 방식)</b>이 더 많이 정답을 찾아냈습니다 — 지금처럼 저장하는 게 낫다는 뜻입니다.</>
                  : <><b className="text-slate-300">A안</b>이 지금 방식보다 정답을 더 많이 찾아냈습니다 — 보완이 필요합니다.</>}
              </p>
            </div>
          )}

          {(visibleMetrics.has('top1') || visibleMetrics.has('channel')) && (
            <div className="bg-slate-800 border border-slate-700 rounded-xl px-5 py-4">
              <p className="text-xs font-medium text-slate-400 mb-3">B안 세부 — 표(RDB) vs 의미검색(벡터) 채널별</p>
              <div className="overflow-x-auto">
                <table className="w-full text-xs border-collapse">
                  <thead>
                    <tr className="border-b border-slate-700 text-slate-500">
                      <th className="text-left font-medium py-2 pr-3">지표</th>
                      <th className="text-left font-medium py-2 px-3">뜻</th>
                      <th className="text-right font-medium py-2 px-3">표(RDB)</th>
                      <th className="text-right font-medium py-2 pl-3">의미검색(벡터)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleMetrics.has('top1') && (
                      <tr className="border-b border-slate-700/50">
                        <td className="py-2.5 pr-3 text-slate-200 font-medium">Top-1</td>
                        <td className="py-2.5 px-3 text-slate-500">
                          검색 결과 1위가 곧바로 정답인지 <span className="text-slate-600">(A안 참고값 {(displayResult.a_top1_accuracy * 100).toFixed(0)}%)</span>
                        </td>
                        <td className="py-2.5 px-3 text-right font-mono tabular-nums text-slate-300">{(displayResult.b_top1_param_accuracy * 100).toFixed(0)}%</td>
                        <td className="py-2.5 pl-3 text-right font-mono tabular-nums text-slate-300">{(displayResult.b_top1_narrative_accuracy * 100).toFixed(0)}%</td>
                      </tr>
                    )}
                    {visibleMetrics.has('channel') && (
                      <tr className="last:border-0">
                        <td className="py-2.5 pr-3 text-slate-200 font-medium">채널기여도(B근거)</td>
                        <td className="py-2.5 px-3 text-slate-500">
                          정답을 그 채널 "단독"으로 찾은 비율 <span className="text-slate-600">(둘 다에서 찾음 {(displayResult.b_hit_both * 100).toFixed(0)}%)</span>
                        </td>
                        <td className="py-2.5 px-3 text-right font-mono tabular-nums text-slate-300">{(displayResult.b_hit_rdb_only * 100).toFixed(0)}%</td>
                        <td className="py-2.5 pl-3 text-right font-mono tabular-nums text-slate-300">{(displayResult.b_hit_vector_only * 100).toFixed(0)}%</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
              <p className="text-[11px] text-slate-500 mt-3">B안은 표(RDB)/의미검색(벡터) 두 채널이 독립적으로 동작해서 "진짜 하나의 순위"가 없습니다 — 그래서 A안처럼 단일 값이 아니라 채널별로 따로 봅니다.</p>
            </div>
          )}

          {visibleMetrics.size === 0 && (
            <p className="text-xs text-slate-500 text-center py-4 bg-slate-800 border border-slate-700 rounded-xl">위에서 지표를 하나 이상 선택하세요.</p>
          )}

          {/* 실행 이력 추이 — "도구"라는 정체성의 핵심: 한 번 보고 끝나는 게 아니라 반복 실행 결과가 쌓인다는 걸 바로 보여줌 */}
          <div className="bg-slate-800 border border-slate-700 rounded-xl px-5 py-4">
            <div className="flex items-center gap-1.5 mb-2">
              <TrendingUp className="w-3.5 h-3.5 text-indigo-600 dark:text-indigo-400" />
              <span className="text-xs font-medium text-slate-300">실행 이력 추이 (B안 hit@K, 최근 {trendAsc.length}회)</span>
            </div>
            {trendAsc.length >= 2 ? (
              <div className="flex items-end gap-1.5 h-16">
                {trendAsc.map((h) => (
                  <div
                    key={h.id}
                    title={`${new Date(h.run_at).toLocaleString('ko-KR')} — hit@K ${(h.b_hit_rate * 100).toFixed(1)}%`}
                    className="flex-1 bg-indigo-500/70 hover:bg-indigo-400 rounded-t min-w-[6px] transition-colors"
                    style={{ height: `${Math.max(h.b_hit_rate * 100, 3)}%` }}
                  />
                ))}
              </div>
            ) : (
              <p className="text-[11px] text-slate-600">실행이 {trendAsc.length}회뿐이라 추이를 그릴 수 없습니다 — 2회 이상부터 막대그래프가 나타납니다.</p>
            )}
          </div>

          {/* 유형별 breakdown — 표로. A→B가 나아졌는지는 화살표 아이콘으로 즉시 구분되게 */}
          <div className="bg-slate-800 border border-slate-700 rounded-xl px-5 py-5">
            <p className="text-xs font-medium text-slate-400 mb-3">질문 유형별로 뜯어보면</p>
            <div className="overflow-x-auto">
              <table className="w-full text-xs border-collapse">
                <thead>
                  <tr className="border-b border-slate-700 text-slate-500">
                    <th className="text-left font-medium py-2 pr-3">유형</th>
                    <th className="text-right font-medium py-2 px-3">질문수</th>
                    {visibleMetrics.has('hitK') && <th className="text-right font-medium py-2 px-3">hit@K (A→B)</th>}
                    {visibleMetrics.has('top1') && <th className="text-right font-medium py-2 px-3">Top-1 (A/B표/B의미)</th>}
                    {visibleMetrics.has('precision') && <th className="text-right font-medium py-2 px-3">집중도 (A/B)</th>}
                    {visibleMetrics.has('channel') && <th className="text-right font-medium py-2 pl-3">B근거 (표/의미/둘다)</th>}
                  </tr>
                </thead>
                <tbody>
                  {displayResult.by_type.map((t) => {
                    const info = TYPE_INFO[t.type] ?? { label: t.type, example: '' };
                    const improved = t.b_hit_rate >= t.a_hit_rate;
                    return (
                      <tr key={t.type} className="border-b border-slate-700/50 last:border-0">
                        <td className="py-2.5 pr-3 text-slate-200 font-medium" title={info.example}>{info.label}</td>
                        <td className="py-2.5 px-3 text-right font-mono tabular-nums text-slate-500">{t.n}</td>
                        {visibleMetrics.has('hitK') && (
                          <td className="py-2.5 px-3 text-right font-mono tabular-nums">
                            <span className="text-slate-400">{(t.a_hit_rate * 100).toFixed(0)}%</span>
                            {' → '}
                            <span className={`inline-flex items-center gap-0.5 font-semibold ${improved ? 'text-emerald-500 dark:text-emerald-400' : 'text-rose-500 dark:text-rose-400'}`}>
                              {(t.b_hit_rate * 100).toFixed(0)}%
                              {improved ? <ArrowUpRight className="w-3.5 h-3.5" /> : <ArrowDownRight className="w-3.5 h-3.5" />}
                            </span>
                          </td>
                        )}
                        {visibleMetrics.has('top1') && (
                          <td className="py-2.5 px-3 text-right font-mono tabular-nums text-slate-300">
                            {(t.a_top1_accuracy * 100).toFixed(0)}% / {(t.b_top1_param_accuracy * 100).toFixed(0)}% / {(t.b_top1_narrative_accuracy * 100).toFixed(0)}%
                          </td>
                        )}
                        {visibleMetrics.has('precision') && (
                          <td className="py-2.5 px-3 text-right font-mono tabular-nums text-slate-300">
                            {(t.a_precision * 100).toFixed(0)}% / {(t.b_precision * 100).toFixed(0)}%
                          </td>
                        )}
                        {visibleMetrics.has('channel') && (
                          <td className="py-2.5 pl-3 text-right font-mono tabular-nums text-slate-300">
                            {(t.b_hit_rdb_only * 100).toFixed(0)}% / {(t.b_hit_vector_only * 100).toFixed(0)}% / {(t.b_hit_both * 100).toFixed(0)}%
                          </td>
                        )}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="flex items-center gap-4 mt-4 pt-3 border-t border-slate-700 text-[11px] text-slate-500">
              <span className="flex items-center gap-1.5"><ArrowUpRight className="w-3.5 h-3.5 text-emerald-500 dark:text-emerald-400" /> B안이 A안보다 나음</span>
              <span className="flex items-center gap-1.5"><ArrowDownRight className="w-3.5 h-3.5 text-rose-500 dark:text-rose-400" /> B안이 A안보다 못함</span>
            </div>
          </div>

          <div className="flex items-center gap-2 text-[11px] text-slate-500 font-mono">
            <Badge color="slate">{displayResult.golden_set_file}</Badge>
            <span>전체 {displayResult.total_n}문항 · 소요 {displayResult.duration_seconds}초</span>
          </div>
        </>
      )}

      {!running && !displayResult && !runError && (
        <div className="text-center py-14 text-slate-500 text-sm">
          아직 실행 이력이 없습니다. "비교 실행"을 눌러 시작하세요.
        </div>
      )}
    </div>
  );
}
