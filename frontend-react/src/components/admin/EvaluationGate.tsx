import { useState } from 'react';
import { clsx } from 'clsx';
import { BarChart3, Search, FileText, History } from 'lucide-react';
import { SearchPreview } from './SearchPreview';
import { ReportTab } from './GateReport';
import { GateFlow, type Stage } from './GateFlow';
import { GateHistory } from './GateHistory';
import { GateScores } from './GateScores';
import { useAuthStore } from '../../store/useAuthStore';

/**
 * 품질 › 평가 게이트 — 자가 개선 루프의 측정 쪽(10/27 밤 재구성, docs/tech/self-improvement-loop-design.md).
 * 두 축(10/28, 사용자 "위 단계를 누르면 계속 리포트로 돌아가고 아래 탭이랑 관계가 안 맞는다"):
 *   위 = 어느 단계(GateFlow: 적재 → 검색 → 근거 판정 → 답 모양 → 생성) — 고르면 아래 모든 탭이 그 단계로 걸러지는 공통 필터, 탭은 그대로.
 *   아래 = 무엇을 볼까 — [틀린 문항] [성적] [적용 이력] [검색 미리보기](검색 미리보기는 단계와 무관).
 * 옛 [검색]·[답] 탭은 [성적] 하나로(같은 "검색"이 단계 이름과 탭 이름 두 뜻이던 것). 옛 품질 › RAG 파이프라인은 단계 흐름 + 적용 이력으로 흡수.
 * 검색 미리보기 = 옛 즉석 질의를 채팅과 같은 검색으로(Dify·RAGFlow 검색 테스트 자리).
 */
type GateMode = 'report' | 'scores' | 'history' | 'adhoc';

const TABS: { mode: GateMode; label: string; icon: typeof Search; hint: string; staged: boolean }[] = [
  { mode: 'report', label: '틀린 문항', icon: FileText, staged: true, hint: '틀린 문항마다 질문 · AI 답 · 왜 틀렸나 · 정답 데이터 저장 모양 · 고칠 기법과 실제 효과 — 사람이 보고 승인' },
  { mode: 'scores', label: '성적', icon: BarChart3, staged: true, hint: '지금 점수 — 검색 성적(정답 정책을 AI에게 넘겼나) · 답 성적(실제 채팅 경로로 답하고 정책 원문과 대조해 채점)' },
  { mode: 'history', label: '적용 이력', icon: History, staged: true, hint: '무엇을 언제 어떤 측정으로 바꿨나 · 결과(운영 반영·안 함·나중에) — 줄을 누르면 그 항목 설명' },
  { mode: 'adhoc', label: '검색 미리보기', icon: Search, staged: false, hint: '질문 하나를 채팅과 같은 검색으로(LLM 0) — 채팅이 할 일 · 찾은 근거 · 정답 정책을 골라 시험지에 추가' },
];

export function EvaluationGate() {
  const isAdmin = useAuthStore((st) => st.user?.role === 'admin');
  const tabs = TABS.filter((t) => isAdmin || t.mode !== 'adhoc');   // 검색 미리보기 = 관리자 전용 API
  const [mode, setMode] = useState<GateMode>('report');
  const [stage, setStage] = useState<Stage | null>(null);   // 탭을 바꿔도 유지 — 단계를 눌러도 탭은 그대로
  const cur = tabs.find((t) => t.mode === mode);

  return (
    <div className="space-y-4">
      <GateFlow stage={stage} onStage={setStage} />
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

      <p className="text-xs text-slate-500 leading-relaxed -mt-2">{cur?.hint}</p>

      {stage && cur?.staged && (
        <p className="text-xs text-indigo-600 dark:text-indigo-300" data-testid="stage-filter">
          {stage} 단계만 보는 중 · <button onClick={() => setStage(null)} className="underline">전체 단계</button>
        </p>
      )}
      {mode === 'report' && <ReportTab key={stage ?? 'all'} stage={stage} />}
      {mode === 'scores' && <GateScores stage={stage} />}
      {mode === 'history' && <GateHistory stage={stage} />}
      {mode === 'adhoc' && isAdmin && <SearchPreview />}
    </div>
  );
}
