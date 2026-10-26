import { useQuery } from '@tanstack/react-query';
import { getDecisionsReport, type ChangedDecision, type DecisionKindSummary } from '../../api/eval';

/**
 * 품질 › AI 판단 (v2.171, 사용자 10/26 "이유도 보여야 관리자들이 관리할 수 있지") — AI가 정한 것을 사람이 얼마나 그대로 뒀나·바꿨나,
 * 바꾼 건마다 AI가 고른 곳과 이유 → 사람이 고른 곳 → "AI가 틀렸나요?" 답. 확신(자동 후보)인데 틀린 건 1건이라도 빨강.
 * 평가 게이트와 같은 엔진의 다른 입력(골든셋 대신 원장, eval-gate-redesign §9-2). 문서는 출처 이름만, 본문은 안 보낸다.
 */

const KIND_NAME: Record<string, { name: string; hint: string }> = {
  knowledge_location: { name: '지식 등록 위치', hint: '지식을 어느 컬렉션에 넣을지 AI가 제안 → 등록하는 사람이 확정하거나 바꿈' },
  chat_clarify: { name: '채팅 되묻기', hint: '목록이 크면 "어느 부분을 볼까요?" — 다음 질문에서 무엇을 골랐나' },
};
const CLARIFY_PICK: Record<string, string> = {
  option: '선택지 고름', all: '전부 보기', typed: '직접 다시 물음', superseded: '다음 되묻기로 넘어감', other: '기타',
};
const VERDICT: Record<string, { label: string; cls: string }> = {
  wrong: { label: 'AI가 틀림', cls: 'text-rose-600 dark:text-rose-400' },
  context_changed: { label: '상황이 바뀜(AI 잘못 아님)', cls: 'text-amber-600 dark:text-amber-400' },
  unknown: { label: '모르겠음', cls: 'text-slate-400' },
};

const pct = (n: number, d: number) => (d ? `${Math.round((n / d) * 100)}%` : '-');
const fmtDate = (s: string) => new Date(s).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });

/** 확정 중 AI 그대로 / 사람이 바꿈 — 막대 하나 */
function KeptBar({ s }: { s: DecisionKindSummary }) {
  const kept = s.decided - s.changed;
  return (
    <div className="space-y-1">
      <div className="flex h-2.5 rounded-full overflow-hidden bg-slate-700" role="img" aria-label={`AI 그대로 ${kept}, 사람이 바꿈 ${s.changed}`}>
        {s.decided > 0 && <div className="bg-emerald-500 dark:bg-emerald-400" style={{ width: `${(kept / s.decided) * 100}%` }} />}
        {s.decided > 0 && <div className="bg-amber-500 dark:bg-amber-400" style={{ width: `${(s.changed / s.decided) * 100}%` }} />}
      </div>
      <p className="text-xs text-slate-400">
        확정 {s.decided}건 중 <span className="text-emerald-600 dark:text-emerald-400">AI 그대로 {kept} ({pct(kept, s.decided)})</span> ·{' '}
        <span className="text-amber-600 dark:text-amber-400">사람이 바꿈 {s.changed}</span>
        {s.total > s.decided && <span className="text-slate-500"> · 아직 확정 전 {s.total - s.decided}</span>}
      </p>
    </div>
  );
}

function ChangedList({ rows }: { rows: ChangedDecision[] }) {
  if (!rows.length) return <p className="text-xs text-slate-500">사람이 바꾼 건이 없습니다.</p>;
  return (
    <ul className="divide-y divide-slate-800">
      {rows.map((r) => (
        <li key={r.id} className="py-2 text-sm space-y-0.5" data-testid={`changed-${r.id}`}>
          <p className="text-xs text-slate-500">{fmtDate(r.at)}{r.changed_by ? ` · ${r.changed_by}` : ''}{r.target ? ` · ${r.target}` : ''}</p>
          <p className="text-slate-200">
            AI <b>{r.ai.collection ?? '삭제된 컬렉션'}</b>
            <span className="text-slate-500"> → </span>
            사람 <b>{r.human.collection ?? (r.human.choice === 'other' ? '다른 곳' : '삭제된 컬렉션')}</b>
          </p>
          <p className="text-xs text-slate-400">AI 이유: {r.ai.reason ?? '(이 판단엔 이유가 남지 않음 — 10/23 이전 기록)'}</p>
          <p className="text-xs">
            {r.verdict
              ? <span className={VERDICT[r.verdict]?.cls}>{VERDICT[r.verdict]?.label ?? r.verdict}</span>
              : <span className="text-slate-500" title="바꾼 사람이 등록 창에서 'AI가 틀렸나요?'에 답하지 않음">AI가 틀렸나요? — 답 없음 ⓘ</span>}
            {r.verdict_reason && <span className="text-slate-400"> · {r.verdict_reason}</span>}
          </p>
        </li>
      ))}
    </ul>
  );
}

function KindCard({ kind, s, picks, changed }: { kind: string; s: DecisionKindSummary; picks?: Record<string, number>; changed: ChangedDecision[] }) {
  const meta = KIND_NAME[kind] ?? { name: kind, hint: '' };
  const reasons = Object.entries(s.reasons ?? {});
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-800 p-4 space-y-3" data-testid={`decision-${kind}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-sm font-medium text-slate-100" title={meta.hint}>{meta.name} <span className="text-xs text-slate-500">판단 {s.total}</span></p>
        <p className="text-xs" title="확신 = AI 값이 비슷한 기존 항목 5개 중 4개와 같을 때(LLM 자기 평가 아님) — 나중에 자동 반영 후보. 이게 틀리면 자동 반영 기준을 다시 봐야 함">
          확신(자동 후보) {s.confident} ·{' '}
          <span className={s.auto_wrong ? 'text-rose-600 dark:text-rose-400 font-medium' : 'text-emerald-600 dark:text-emerald-400'}>
            확신인데 틀림 {s.auto_wrong}{s.auto_wrong ? ' — 자동 반영 기준 점검' : ' ✔'}
          </span> ⓘ
        </p>
      </div>
      {s.decided > 0 && <KeptBar s={s} />}
      {picks && Object.keys(picks).length > 0 && (
        <p className="text-xs text-slate-400" title="'전부 보기'가 많으면 괜히 물은 것, '직접 다시 물음'이 많으면 선택지가 안 맞은 것">
          사용자가 고른 것: {Object.entries(picks).map(([k, n]) => `${CLARIFY_PICK[k] ?? k} ${n}`).join(' · ')} ⓘ
        </p>
      )}
      {(s.changed > 0 || changed.length > 0) && (
        <div className="space-y-1">
          <p className="text-xs text-slate-400">
            사람이 바꾼 건 — AI가 틀림 {s.wrong} · 상황 바뀜 {s.context_changed} · 답 없음 {s.unknown}
            {reasons.length > 0 && <span> · 자주 나온 사유: {reasons.slice(0, 3).map(([r, n]) => `${r} ${n}`).join(', ')}</span>}
          </p>
          <ChangedList rows={changed} />
        </div>
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
      <p className="text-xs text-slate-500" title="AI 판단 원장 — AI가 정한 것 한 건 = 한 줄, 사람이 확정·변경하면 같은 줄에 남는다">
        최근 {data.days}일 · AI가 정한 것을 사람이 그대로 뒀나, 바꿨다면 왜 ⓘ
      </p>
      {kinds.length === 0 && <p className="text-sm text-slate-500">아직 판단 기록이 없습니다.</p>}
      {kinds.map(([k, s]) => (
        <KindCard key={k} kind={k} s={s} picks={k === 'chat_clarify' ? data.clarify_picks : undefined}
          changed={data.changed.filter((r) => r.kind === k)} />
      ))}
      <div className="rounded-lg border border-slate-700 bg-slate-800 p-4 space-y-2" data-testid="decision-candidates">
        <p className="text-sm font-medium text-slate-100" title="평가 게이트 [개선]의 후보 — AI 추천(해 볼 만함·안 하는 게 좋음)을 사람이 따랐나">
          개선 후보 — AI 추천과 사람 결정 <span className="text-xs text-slate-500">후보 {c.total}</span>
        </p>
        <p className="text-xs text-slate-400">
          추천대로 결정 <span className="text-emerald-600 dark:text-emerald-400">{c.agree}</span> ·
          추천과 반대 <span className={c.disagree ? 'text-amber-600 dark:text-amber-400' : ''}>{c.disagree}</span>
          <span title="반대가 많으면 AI 추천 규칙(다시 계산)이 사람 판단과 어긋난다는 신호"> ⓘ</span> ·
          아직 결정 전 {c.by_status.proposed ?? 0}
        </p>
      </div>
    </div>
  );
}
