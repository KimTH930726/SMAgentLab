import { useState } from 'react';
import { clsx } from 'clsx';
import { BarChart3, FileText, History } from 'lucide-react';
import { ReportTab } from './GateReport';
import { GateFlow, type Stage } from './GateFlow';
import { GateHistory } from './GateHistory';
import { GateScores } from './GateScores';
import type { GateAxis } from '../../api/eval';

/**
 * 품질 › 평가 게이트 — 자가 개선 루프의 측정 쪽(10/27 밤 재구성, docs/domains/quality/self-improvement-loop-design.md).
 * 두 축(10/28, 사용자 "위 단계를 누르면 계속 리포트로 돌아가고 아래 탭이랑 관계가 안 맞는다"):
 *   위 = 어느 단계(GateFlow: 적재 → 검색 → 근거 판정 → 답 모양 → 생성) — 고르면 아래 모든 탭이 그 단계로 걸러지는 공통 필터, 탭은 그대로.
 *   아래 = 무엇을 볼까 — [틀린 문항] [성적] [적용 이력].
 * 옛 [검색]·[답] 탭은 [성적] 하나로(같은 "검색"이 단계 이름과 탭 이름 두 뜻이던 것). 옛 품질 › RAG 파이프라인은 단계 흐름 + 적용 이력으로 흡수.
 * 옛 [검색 미리보기] 탭은 내림(10/28) — "AI가 본 근거"(Evidence.tsx)로 이름을 바꿔 정정 요청 신고 카드·지식 상세에 붙였다(쓸 일이 생기는 자리).
 * 맨 위 축(10/29, 운영 질의 감시 설계 §3-5): [정책서 | 업무지식] — 판정 기록·기준선이 축마다 따로(#96). 단계 흐름·틀린 문항·적용 이력은
 * 정책서 문항으로 진단한 자가 개선 루프라 정책서 축에만, 지식 축은 [성적]만(진단은 운영 질의 감시 다음 단계).
 */
type GateMode = 'report' | 'scores' | 'history';

const AXES: { axis: GateAxis; label: string; hint: string }[] = [
  { axis: 'policy', label: '정책서', hint: '정책서 골든셋 — 팀원 문항·실사용 문항 포함' },
  { axis: 'knowledge', label: '업무지식', hint: '업무지식 골든셋(컨플루언스 등) — 조각마다 로컬 LLM이 만든 질문' },
];

/** 지식 축은 [성적]만 — 그 탭 설명(정책서 문구를 글자 바꾸기로 돌려쓰지 않는다, 리뷰 L6) */
const KNOWLEDGE_SCORES_HINT = '지금 점수 — 검색 성적(정답 업무지식을 AI에게 넘겼나) · 답 성적(실제 채팅 경로로 답하고 업무지식 원문과 대조해 채점)';

const TABS: { mode: GateMode; label: string; icon: typeof FileText; hint: string; staged: boolean }[] = [
  { mode: 'report', label: '틀린 문항', icon: FileText, staged: true, hint: '틀린 문항마다 질문 · AI 답 · 왜 틀렸나 · 정답 데이터 저장 모양 · 고칠 기법과 실제 효과 — 사람이 보고 승인' },
  { mode: 'scores', label: '성적', icon: BarChart3, staged: true, hint: '지금 점수 — 검색 성적(정답 정책을 AI에게 넘겼나) · 답 성적(실제 채팅 경로로 답하고 정책 원문과 대조해 채점)' },
  { mode: 'history', label: '적용 이력', icon: History, staged: true, hint: '무엇을 언제 어떤 측정으로 바꿨나 · 결과(운영 반영·안 함·나중에) — 줄을 누르면 그 항목 설명' },
];

export function EvaluationGate() {
  const [axis, setAxis] = useState<GateAxis>('policy');
  const tabs = axis === 'policy' ? TABS : TABS.filter((t) => t.mode === 'scores');
  const [policyMode, setMode] = useState<GateMode>('report');
  const mode: GateMode = axis === 'policy' ? policyMode : 'scores';
  const [stage, setStage] = useState<Stage | null>(null);   // 탭을 바꿔도 유지 — 단계를 눌러도 탭은 그대로
  const cur = tabs.find((t) => t.mode === mode);

  return (
    <div className="space-y-4">
      <div className="inline-flex rounded-lg border border-slate-700 p-0.5" role="group" aria-label="평가 축">
        {AXES.map((a) => (
          <button key={a.axis} onClick={() => setAxis(a.axis)} aria-pressed={axis === a.axis} title={a.hint} data-testid={`axis-${a.axis}`}
            className={clsx('rounded-md px-4 py-1.5 text-sm font-medium', axis === a.axis ? 'bg-indigo-600 text-white' : 'text-slate-300 hover:bg-slate-700')}>
            {a.label}
          </button>
        ))}
      </div>
      {axis === 'policy' && <GateFlow stage={stage} onStage={setStage} />}
      <div className="border-b border-slate-700">
        <div className="flex gap-1">
          {tabs.map(({ mode: m, label, icon: Icon }) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={clsx(
                'flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors',
                mode === m
                  ? 'text-indigo-400 border-indigo-500'
                  : 'text-slate-400 border-transparent hover:text-slate-200 hover:border-slate-600',
              )}
            >
              <Icon className="w-4 h-4" />
              {label}
            </button>
          ))}
        </div>
      </div>

      <p className="text-xs text-slate-500 leading-relaxed -mt-2">
        {axis === 'knowledge' ? KNOWLEDGE_SCORES_HINT : cur?.hint}
        {axis === 'knowledge' && <span title="틀린 문항 진단·개선 후보·적용 이력은 정책서 축만 — 지식은 운영 질의 감시(실사용 질의) 다음 단계에서"> · 진단·개선은 정책서만 ⓘ</span>}
      </p>

      {axis === 'policy' && stage && cur?.staged && (
        <p className="text-xs text-indigo-600 dark:text-indigo-300" data-testid="stage-filter">
          {stage} 단계만 보는 중 · <button onClick={() => setStage(null)} className="underline">전체 단계</button>
        </p>
      )}
      {mode === 'report' && <ReportTab key={stage ?? 'all'} stage={stage} />}
      {mode === 'scores' && <GateScores key={axis} stage={axis === 'policy' ? stage : null} axis={axis} />}
      {mode === 'history' && <GateHistory stage={stage} />}
    </div>
  );
}
