import { useQuery } from '@tanstack/react-query';
import { Database, Layers, ClipboardCheck, TrendingUp, CheckCircle2, Circle, MinusCircle, ChevronRight } from 'lucide-react';
import { getPipelineStats, getTrack2History } from '../../api/policy';
import { useNamespaceAccess } from '../../utils/useNamespaceAccess';

// 실험실 게이트 작업3(2026-09-15, work-os 전달 프롬프트 "핵심 산출물") — 지식화 레이어
// 갭 이슈 6개의 현재 상태. docs/tech/data-storage-philosophy.md §9와 반드시 같이 유지한다
// (상태가 바뀌면 이 배열과 그 문서를 같이 갱신할 것) — 실제 스키마 조회로 재확인한 상태값:
// content_hash 컬럼은 있지만 LLM 생성물 미포함이라 재현 불완전(미착수), raw_structure/
// embedding_model 컬럼과 category_path GIN 인덱스는 전부 없음(미착수), DB스키마사전/
// 공통코드는 테이블+파서는 v2.73에 만들었지만 실제 재적재는 0건(부분 완료).
type GapStatus = 'done' | 'partial' | 'todo';

const GAP_ISSUES: { label: string; status: GapStatus; note: string }[] = [
  {
    label: 'content_hash 재현성', status: 'todo',
    note: '컬럼은 있으나 LLM 생성물 미포함 — 파서/프롬프트 개선을 기존 데이터에 반영할 경로가 없음. 다른 모든 재구조화 작업의 선행 조건.',
  },
  {
    label: 'raw_structure JSONB 컬럼', status: 'todo',
    note: '파서가 만든 정제 구조 트리를 보존할 자리 자체가 없음.',
  },
  {
    label: 'DB 스키마 사전 / 공통코드 RDB 재적재', status: 'partial',
    note: '테이블(ref_db_column/ref_common_code)+결정론 파서는 v2.73에 완료. 실제 데이터 재적재는 아직 0건(업로드 UI도 YAGNI로 보류 중).',
  },
  {
    label: '결정론 아웃라인 파서 (상태전이)', status: 'todo',
    note: 'unresolved 항목의 raw_body 마커 패턴 분석부터 필요.',
  },
  {
    label: 'embed_text render 함수', status: 'todo',
    note: '공통 자연어화 함수 + 토큰 한도 청크 분리가 아직 없음.',
  },
  {
    label: 'embedding_model 컬럼 + category_path GIN', status: 'todo',
    note: '재인덱싱 추적, 포함 필터 성능 — 둘 다 미착수.',
  },
];
const GAP_DONE_COUNT = GAP_ISSUES.filter((g) => g.status === 'done').length;
const GAP_PARTIAL_COUNT = GAP_ISSUES.filter((g) => g.status === 'partial').length;
const GAP_TODO_COUNT = GAP_ISSUES.filter((g) => g.status === 'todo').length;

const GAP_STATUS_ICON: Record<GapStatus, React.ReactNode> = {
  done: <CheckCircle2 className="w-4 h-4 text-emerald-400 flex-shrink-0" />,
  partial: <MinusCircle className="w-4 h-4 text-amber-400 flex-shrink-0" />,
  todo: <Circle className="w-4 h-4 text-slate-600 flex-shrink-0" />,
};
const GAP_STATUS_LABEL: Record<GapStatus, string> = { done: '완료', partial: '부분 완료', todo: '미착수' };

function StatTile({ label, value }: { label: string; value: number }) {
  return (
    <div className="bg-slate-900 border border-slate-700 rounded-lg px-4 py-3">
      <div className="text-2xl font-bold text-slate-200 font-mono tabular-nums">{value.toLocaleString()}</div>
      <div className="text-[11px] text-slate-500 mt-0.5">{label}</div>
    </div>
  );
}

type StepTone = 'good' | 'warn' | 'bad' | 'neutral';
const STEP_TONE_CLASS: Record<StepTone, string> = {
  good: 'border-emerald-600/50 bg-emerald-500/10 text-emerald-300',
  warn: 'border-amber-600/50 bg-amber-500/10 text-amber-300',
  bad: 'border-slate-700 bg-slate-800 text-slate-500',
  neutral: 'border-slate-700 bg-slate-800 text-slate-400',
};

function PipelineStep({ n, label, headline, tone }: { n: number; label: string; headline: string; tone: StepTone }) {
  return (
    <div className={`flex-1 min-w-[140px] rounded-lg border px-3 py-2.5 ${STEP_TONE_CLASS[tone]}`}>
      <div className="text-[10px] font-medium opacity-70">{n}단계 · {label}</div>
      <div className="text-xs font-semibold mt-0.5 leading-snug">{headline}</div>
    </div>
  );
}

/**
 * 실험실 게이트 작업3(2026-09-15) — "기준정보 축적 → 지식화 레이어 → 평가체계 축적 →
 * retrieval 평가 현황"을 한 화면에서 보는 모니터링 뷰. work-os 전달 프롬프트의 "핵심 산출물".
 *
 * PolicyLab(A/B 실험 도구)의 확장이 아니라 별도 컴포넌트로 분리했다 — 이건 실험 하나가
 * 아니라 파이프라인 전체 조망이라 성격이 다르고, project_common_platform_roadmap 메모리의
 * 방향("실험실이 나중에 독립 상위 메뉴로 승격되면 이 컴포넌트가 그대로 옮겨감")과도 맞다.
 *
 * 2026-09-15 재설계: 첫 버전은 숫자 카드 4개를 나열만 해서 "왜 있는지/뭘 보여주는지"가
 * 안 보인다는 실사용 피드백을 받음 — 4단계가 사실은 하나의 파이프라인(원본→구조화→평가
 * 실행→평가 결과)으로 이어진다는 걸 전달하도록, 맨 위에 단계 커넥터 + 동적 요약 문장을
 * 추가하고 각 섹션 첫 줄에 "그래서 뭘 알 수 있는지"를 먼저 쓰게 바꿈.
 */
export function PolicyPipelineMonitor() {
  const { selectedNs, setSelectedNs, sortedNamespaces } = useNamespaceAccess();

  const { data: stats, isLoading: statsLoading } = useQuery({
    queryKey: ['policy-pipeline-stats', selectedNs],
    queryFn: () => getPipelineStats(selectedNs),
    enabled: !!selectedNs,
    staleTime: 30_000,
  });

  const { data: history = [], isLoading: historyLoading } = useQuery({
    queryKey: ['track2-history'],
    queryFn: () => getTrack2History(20),
    staleTime: 30_000,
  });

  const latest = history[0];
  // history는 run_at 내림차순(최신 먼저) — 추이 차트는 시간순이 자연스러워 뒤집는다.
  const trendAsc = [...history].reverse();

  const ready = !!selectedNs && !!stats;

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-slate-200">정책 지식 파이프라인 모니터</h2>
        <p className="text-xs text-slate-500 mt-1 leading-relaxed">
          정책 지식은 <b className="text-slate-400">원본 수집 → 구조화(지식화) → 평가 실행 → 평가 결과</b> 4단계를 거칩니다.
          이 화면은 지금 어느 단계까지 얼마나 진행됐고, 어디가 비어있는지를 한 번에 보여줍니다 — 숫자 자체보다
          "다음에 뭘 채워야 하는지"를 찾는 용도입니다.
        </p>
      </div>

      <div>
        <label className="block text-xs font-medium text-slate-400 mb-1.5">파트</label>
        <select
          value={selectedNs}
          onChange={(e) => setSelectedNs(e.target.value)}
          className="w-56 bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
        >
          <option value="">선택...</option>
          {sortedNamespaces.map((ns) => <option key={ns} value={ns}>{ns}</option>)}
        </select>
      </div>

      {!selectedNs && (
        <div className="text-center py-14 text-slate-500 text-sm bg-slate-800 border border-slate-700 rounded-xl">
          위에서 파트를 선택하면 단계별 현황이 표시됩니다.
        </div>
      )}

      {selectedNs && (statsLoading || historyLoading) && (
        <div className="text-center py-14 text-slate-500 text-sm bg-slate-800 border border-slate-700 rounded-xl">불러오는 중...</div>
      )}

      {ready && (
        <>
          {/* 단계 커넥터 — 4단계가 하나로 이어진다는 걸 시각적으로 먼저 보여줌 */}
          <div className="flex items-center gap-1.5 flex-wrap">
            <PipelineStep
              n={1} label="기준정보"
              headline={`정책 ${stats.policy_item.toLocaleString()}건 쌓임`}
              tone={stats.policy_item > 0 ? 'good' : 'bad'}
            />
            <ChevronRight className="w-4 h-4 text-slate-600 flex-shrink-0" />
            <PipelineStep
              n={2} label="지식화 레이어"
              headline={`갭 이슈 ${GAP_TODO_COUNT}/${GAP_ISSUES.length}건 미착수`}
              tone={GAP_TODO_COUNT === 0 ? 'good' : GAP_TODO_COUNT <= GAP_ISSUES.length / 2 ? 'warn' : 'bad'}
            />
            <ChevronRight className="w-4 h-4 text-slate-600 flex-shrink-0" />
            <PipelineStep
              n={3} label="평가체계"
              headline={history.length > 0 ? `${history.length}회 실행됨` : '실행 이력 없음'}
              tone={history.length > 0 ? 'good' : 'bad'}
            />
            <ChevronRight className="w-4 h-4 text-slate-600 flex-shrink-0" />
            <PipelineStep
              n={4} label="평가 결과"
              headline={latest ? `hit@K ${(latest.b_hit_rate * 100).toFixed(0)}%` : '결과 없음'}
              tone={latest ? (latest.b_hit_rate >= 0.7 ? 'good' : 'warn') : 'bad'}
            />
          </div>

          {/* 동적 요약 문장 — 4단계를 숫자가 아니라 하나의 이야기로 */}
          <div className="px-4 py-3 bg-indigo-900/20 border border-indigo-700/30 rounded-xl text-xs text-slate-300 leading-relaxed">
            기준정보는 <b className="text-slate-100">{stats.policy_item.toLocaleString()}건</b> 쌓였{stats.ref_db_column === 0 && stats.ref_common_code === 0 ? '지만, DB 스키마 사전·공통코드는 아직 재적재된 게 없고' : '고'} 지식화 레이어 갭 이슈는 {GAP_ISSUES.length}건 중{' '}
            <b className="text-slate-100">{GAP_TODO_COUNT}건이 미착수</b>{GAP_PARTIAL_COUNT > 0 ? `(${GAP_PARTIAL_COUNT}건은 부분 완료)` : ''}라 원본을
            신뢰할 구조로 다듬는 단계는 아직 부족합니다.{' '}
            {history.length > 0 && latest
              ? <>그럼에도 평가는 <b className="text-slate-100">{history.length}회</b> 실행됐고, 최근 결과는 하이브리드 방식(B안) 기준 hit@K <b className="text-slate-100">{(latest.b_hit_rate * 100).toFixed(1)}%</b>입니다.</>
              : <>평가는 아직 한 번도 실행되지 않았습니다 — "저장소 실험실" 탭에서 비교를 실행하면 이 요약이 채워집니다.</>}
          </div>

          {/* ① 기준정보 축적 */}
          <div className="bg-slate-800 border border-slate-700 rounded-xl px-5 py-5">
            <p className="text-xs font-medium text-slate-400 mb-1 flex items-center gap-1.5">
              <Database className="w-3.5 h-3.5" /> 1단계 · 기준정보 축적
            </p>
            <p className="text-sm text-slate-300 mb-3">
              원본 정책서가 얼마나 시스템에 들어와 있는지입니다. RDB(정확 조회)·벡터(의미검색)·참조 테이블 각각의 건수를 봅니다.
            </p>
            <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
              <StatTile label="정책 항목" value={stats.policy_item} />
              <StatTile label="파라미터(RDB)" value={stats.policy_param} />
              <StatTile label="서술(벡터)" value={stats.policy_chunk} />
              <StatTile label="DB 스키마 사전" value={stats.ref_db_column} />
              <StatTile label="공통코드" value={stats.ref_common_code} />
            </div>
            {stats.trend.length > 0 ? (
              <p className="text-[11px] text-slate-600 mt-3">
                최근 30일 적재 이력: {stats.trend.map((t) => `${t.day} +${t.count}`).join(' · ')}
                {' '}— 정책서는 일괄 임포트 방식이라 특정일에 몰려 찍히는 게 정상입니다.
              </p>
            ) : (
              <p className="text-[11px] text-slate-600 mt-3">최근 30일 안에 새로 적재된 항목이 없습니다.</p>
            )}
          </div>

          {/* ② 지식화 레이어 갭 이슈 */}
          <div className="bg-slate-800 border border-slate-700 rounded-xl px-5 py-5">
            <p className="text-xs font-medium text-slate-400 mb-1 flex items-center gap-1.5">
              <Layers className="w-3.5 h-3.5" /> 2단계 · 지식화 레이어
            </p>
            <p className="text-sm text-slate-300 mb-3">
              1단계에서 쌓인 원본을 "믿고 검색에 쓸 수 있는 구조"로 다듬는 작업이 얼마나 됐는지입니다.
              아래 6개가 다 채워져야 재구조화(예: 다른 데이터 축 추가)가 안전해집니다 — 지금은{' '}
              <b className="text-slate-100">{GAP_TODO_COUNT}건 미착수 · {GAP_PARTIAL_COUNT}건 부분 완료 · {GAP_DONE_COUNT}건 완료</b>입니다.
            </p>
            <div className="space-y-2.5">
              {GAP_ISSUES.map((g) => (
                <div key={g.label} className="flex items-start gap-2.5">
                  {GAP_STATUS_ICON[g.status]}
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm text-slate-200">{g.label}</span>
                      <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${
                        g.status === 'done' ? 'bg-emerald-500/15 text-emerald-400'
                          : g.status === 'partial' ? 'bg-amber-500/15 text-amber-400'
                          : 'bg-slate-700 text-slate-500'
                      }`}>
                        {GAP_STATUS_LABEL[g.status]}
                      </span>
                    </div>
                    <p className="text-[11px] text-slate-500 mt-0.5">{g.note}</p>
                  </div>
                </div>
              ))}
            </div>
            <p className="text-[11px] text-slate-600 mt-3">근거: docs/tech/data-storage-philosophy.md §9 (우선순위 순서 그대로)</p>
          </div>

          {/* ③ 평가체계 축적 */}
          <div className="bg-slate-800 border border-slate-700 rounded-xl px-5 py-5">
            <p className="text-xs font-medium text-slate-400 mb-1 flex items-center gap-1.5">
              <ClipboardCheck className="w-3.5 h-3.5" /> 3단계 · 평가체계 축적
            </p>
            <p className="text-sm text-slate-300 mb-3">
              1·2단계 결과가 실제로 "정답을 잘 찾아내는지" 확인한 횟수입니다 — 측정 자체가 없으면 4단계(품질 숫자)도 근거가 없습니다.
            </p>
            {history.length === 0 ? (
              <p className="text-xs text-slate-500 py-4 text-center bg-slate-900 rounded-lg">
                아직 저장된 실행 이력이 없습니다 — "저장소 실험실" 탭에서 비교를 한 번 실행하면 여기 쌓입니다.
              </p>
            ) : (
              <p className="text-xs text-slate-400">
                지금까지 <b className="text-slate-200">{history.length}회</b> 실행 · 최근 실행{' '}
                <b className="text-slate-200">{latest ? new Date(latest.run_at).toLocaleString('ko-KR') : '-'}</b>
              </p>
            )}
          </div>

          {/* ④ retrieval 평가 현황 */}
          {latest && (
            <div className="bg-slate-800 border border-slate-700 rounded-xl px-5 py-5">
              <p className="text-xs font-medium text-slate-400 mb-1 flex items-center gap-1.5">
                <TrendingUp className="w-3.5 h-3.5" /> 4단계 · 평가 결과(retrieval 품질)
              </p>
              <p className="text-sm text-slate-300 mb-3">
                가장 최근 측정에서, 질문에 정답을 찾아내는 비율(hit@K)과 화면에 1건만 보여줄 때 그게 정답일 확률(Top-1)입니다.
              </p>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-4">
                <StatTile label="최근 B안 hit@K" value={Math.round(latest.b_hit_rate * 100)} />
                <StatTile label="최근 Top-1(A)" value={Math.round(latest.a_top1_accuracy * 100)} />
                <StatTile label="최근 Top-1(B·표)" value={Math.round(latest.b_top1_param_accuracy * 100)} />
                <StatTile label="최근 Top-1(B·의미검색)" value={Math.round(latest.b_top1_narrative_accuracy * 100)} />
              </div>
              {trendAsc.length >= 2 ? (
                <>
                  <p className="text-[11px] text-slate-500 mb-1.5">B안 hit@K 추이(오래된 순, {trendAsc.length}회)</p>
                  <div className="flex items-end gap-1 h-16">
                    {trendAsc.map((h) => (
                      <div
                        key={h.id}
                        title={`${new Date(h.run_at).toLocaleDateString('ko-KR')} — hit@K ${(h.b_hit_rate * 100).toFixed(1)}%`}
                        className="flex-1 bg-indigo-500/70 hover:bg-indigo-400 rounded-t min-w-[4px] transition-colors"
                        style={{ height: `${Math.max(h.b_hit_rate * 100, 3)}%` }}
                      />
                    ))}
                  </div>
                </>
              ) : (
                <p className="text-[11px] text-slate-600">
                  아직 실행이 {trendAsc.length}회뿐이라 추이를 그릴 수 없습니다 — 2회 이상부터 막대그래프가 나타납니다.
                </p>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
