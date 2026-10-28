import { useState } from 'react';
import { clsx } from 'clsx';
import { AnswerTab, RetrievalTab } from './GateRuns';
import type { Stage } from './GateFlow';

/**
 * 평가 게이트 › [성적](10/28, 옛 [검색]·[답] 탭을 합침 — 사용자 "위 단계와 아래 탭이 관계가 안 맞는다"). 단계 흐름에서 고른 단계의 성적을 보여 준다.
 * 검색 = 정답 정책을 AI에게 넘겼나(검색 측정), 생성 = 답 정답률(답 측정). 근거 판정·답 모양은 따로 잰 숫자가 없어 답 성적의 해당 층으로 안내,
 * 적재는 적재 검수(어긋남)로 잰다. 단계를 안 골랐으면 [검색 성적]·[답 성적] 중 하나(기본 = 답 — 목표 숫자인 사람이 쓴 질문이 여기).
 */

type Which = 'retrieval' | 'answers';
const ANSWER_LAYER_HINT: Partial<Record<Stage, string>> = {
  '근거 판정': '근거 판정만 따로 잰 숫자는 없어요 — 아래 층별 정답률의 "답 없음" 층(거절해야 맞는 질문)이 이 단계 성적입니다',
  '답 모양': '답 모양만 따로 잰 숫자는 없어요 — 아래 층별 정답률의 "목록"·"참고표 되묻기" 층이 이 단계 성적입니다',
};

export function GateScores({ stage }: { stage: Stage | null }) {
  const [pick, setPick] = useState<Which>('answers');
  if (stage === '적재') {
    return (
      <p className="rounded-lg border border-slate-700 bg-slate-800 p-4 text-sm text-slate-300" data-testid="scores-ingest">
        적재는 시험지로 재지 않고 <b>적재 검수</b>(설계 완료 기준과 다르게 저장된 곳)로 잽니다 — 위 단계 흐름의 "어긋남" 숫자. 그 검사에 걸린 문항은 [틀린 문항] 탭에서 봅니다.
      </p>
    );
  }
  const which: Which = stage === '검색' ? 'retrieval' : stage ? 'answers' : pick;
  const btn = (v: Which, label: string) => (
    <button onClick={() => setPick(v)} aria-pressed={which === v}
      className={clsx('rounded-md px-3 py-1 text-xs', which === v ? 'bg-indigo-600 text-white' : 'text-slate-300 hover:bg-slate-700')}>{label}</button>
  );
  return (
    <div className="space-y-3" data-testid="gate-scores" data-which={which}>
      {!stage && <div className="inline-flex rounded-lg border border-slate-700 p-0.5">{btn('answers', '답 성적')}{btn('retrieval', '검색 성적')}</div>}
      {stage && ANSWER_LAYER_HINT[stage] && <p className="text-xs text-slate-400">{ANSWER_LAYER_HINT[stage]}</p>}
      {which === 'retrieval' ? <RetrievalTab /> : <AnswerTab />}
    </div>
  );
}
