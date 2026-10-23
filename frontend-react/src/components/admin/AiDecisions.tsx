import { useQuery } from '@tanstack/react-query';
import { getDecisionsReport, type DecisionKindSummary } from '../../api/eval';

/**
 * 품질 › AI 판단 (v2.170, eval-gate-redesign §9-2·collection-design §16) — AI가 정한 것을 사람이 얼마나 바꿨나·틀렸다고 했나(읽기 전용).
 * 평가 게이트와 같은 엔진의 다른 입력: 골든셋 대신 원장의 판단(사람이 고친 값 = 정답). 자동 반영 후보(확신)인데 틀린 건 1건이라도 빨강 —
 * 자동 반영 기준을 다시 봐야 한다는 신호. 개선 후보 검수(추천과 사람 결정이 맞았나)도 같이.
 */

const KIND_NAME: Record<string, { name: string; hint: string }> = {
  knowledge_location: { name: '지식 등록 위치', hint: '지식을 어느 컬렉션에 넣을지 AI가 제안 → 등록하는 사람이 확정·변경' },
  chat_clarify: { name: '채팅 되묻기', hint: '목록이 크면 "어느 부분을 볼까요?" — 다음 질문에서 무엇을 골랐나' },
};

const CLARIFY_PICK: Record<string, string> = {
  option: '선택지 고름', all: '전부 보기', typed: '직접 다시 물음', superseded: '다음 되묻기로 넘어감', other: '기타',
};

const pct = (x: number | null | undefined) => (x == null ? '-' : `${Math.round(x * 100)}%`);

function Stat({ label, value, hint, cls = 'text-slate-100' }: { label: string; value: string | number; hint?: string; cls?: string }) {
  return (
    <div title={hint}>
      <p className="text-xs text-slate-400">{label}{hint ? ' ⓘ' : ''}</p>
      <p className={`text-xl font-semibold ${cls}`}>{value}</p>
    </div>
  );
}

function KindCard({ kind, s, picks }: { kind: string; s: DecisionKindSummary; picks?: Record<string, number> }) {
  const meta = KIND_NAME[kind] ?? { name: kind, hint: '' };
  const reasons = Object.entries(s.reasons ?? {});
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-800 p-4 space-y-3" data-testid={`decision-${kind}`}>
      <p className="text-sm font-medium text-slate-100" title={meta.hint}>{meta.name}</p>
      <div className="flex flex-wrap gap-8">
        <Stat label="판단" value={s.total} />
        <Stat label="확신(자동 후보)" value={s.confident} hint="AI 값 = 비슷한 기존 항목 5개 중 4개 다수결일 때만 — LLM 자기 평가가 아님" />
        <Stat label="사람 확정" value={s.decided} />
        <Stat label="사람이 바꿈" value={s.changed} />
        <Stat label="AI 틀림" value={`${s.wrong} (${pct(s.wrong_rate)})`} hint="바꾼 사람이 'AI가 틀렸다'고 답한 것 / 확정 수" />
        <Stat label="확신인데 틀림" value={s.auto_wrong} cls={s.auto_wrong ? 'text-rose-600 dark:text-rose-400' : 'text-slate-100'}
          hint="1건이라도 있으면 자동 반영 기준을 다시 봐야 한다" />
      </div>
      {(s.context_changed > 0 || s.unknown > 0) && (
        <p className="text-xs text-slate-500" title="틀림에 섞지 않는다 — 섞으면 오답률이 부풀거나 줄어든다">
          바꿨지만 틀림 아님: 상황 바뀜 {s.context_changed} · 답 없음 {s.unknown} ⓘ
        </p>
      )}
      {reasons.length > 0 && (
        <p className="text-xs text-slate-400">틀린 이유: {reasons.slice(0, 5).map(([r, n]) => `${r} ${n}`).join(' · ')}</p>
      )}
      {picks && Object.keys(picks).length > 0 && (
        <p className="text-xs text-slate-400" title="'전부 보기'가 많으면 괜히 물은 것, '직접 다시 물음'이 많으면 선택지가 안 맞은 것">
          고른 것: {Object.entries(picks).map(([k, n]) => `${CLARIFY_PICK[k] ?? k} ${n}`).join(' · ')} ⓘ
        </p>
      )}
    </div>
  );
}

export function AiDecisions() {
  const { data, isLoading, error } = useQuery({ queryKey: ['ai-decisions', 90], queryFn: () => getDecisionsReport(90), staleTime: 30_000 });
  if (isLoading) return <p className="text-sm text-slate-500">불러오는 중…</p>;
  if (error) return <p className="text-sm text-rose-600 dark:text-rose-400">불러오지 못했습니다: {(error as Error).message}</p>;
  if (!data) return null;
  const kinds = Object.entries(data.kinds);
  const c = data.candidates;
  return (
    <div className="space-y-4">
      <p className="text-xs text-slate-500">최근 {data.days}일 · AI가 정한 것을 사람이 확정·변경한 기록(AI 판단 원장)</p>
      {kinds.length === 0 && <p className="text-sm text-slate-500">아직 판단 기록이 없습니다.</p>}
      {kinds.map(([k, s]) => <KindCard key={k} kind={k} s={s} picks={k === 'chat_clarify' ? data.clarify_picks : undefined} />)}
      <div className="rounded-lg border border-slate-700 bg-slate-800 p-4 space-y-3" data-testid="decision-candidates">
        <p className="text-sm font-medium text-slate-100" title="평가 게이트 [개선]의 후보 — 시도 추천·기각 추천을 사람이 따랐나">개선 후보 검수</p>
        <div className="flex flex-wrap gap-8">
          <Stat label="후보" value={c.total} />
          <Stat label="추천대로 결정" value={c.agree} hint="시도 추천 → 승인·채택, 기각 추천 → 기각" />
          <Stat label="추천과 반대" value={c.disagree} cls={c.disagree ? 'text-amber-600 dark:text-amber-400' : 'text-slate-100'}
            hint="많으면 리플레이·추천 규칙이 사람 판단과 어긋난다는 신호" />
          <Stat label="아직 제안" value={c.by_status.proposed ?? 0} />
        </div>
      </div>
    </div>
  );
}
