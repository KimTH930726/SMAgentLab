import { useState } from 'react';
import { clsx } from 'clsx';
import { FlaskConical, Search, CheckCircle2, FileText, History } from 'lucide-react';
import { SearchPreview } from './SearchPreview';
import { AnswerTab, RetrievalTab } from './GateRuns';
import { ReportTab } from './GateReport';
import { GateFlow, type Stage } from './GateFlow';
import { GateHistory } from './GateHistory';
import { useAuthStore } from '../../store/useAuthStore';

/**
 * 품질 › 평가 게이트 — 자가 개선 루프의 측정 쪽(10/27 밤 재구성, docs/tech/self-improvement-loop-design.md).
 * 맨 위 = 단계 흐름 요약(GateFlow: 적재 → 검색 → 근거 판정 → 답 모양 → 생성, 단계별 틀림·번복) — 누르면 리포트가 그 단계로.
 * 탭 = [리포트] [검색] [답] [적용 이력] [검색 미리보기].
 * 검색 미리보기 = 옛 즉석 질의(지식·정책·참조데이터 따로 검색해 화면에서 합침 — 채팅과 달랐다)를 채팅과 같은 검색으로(Dify·RAGFlow 검색 테스트 자리).
 * 옛 품질 › RAG 파이프라인(답 기법) 메뉴는 여기로 흡수: 단계 흐름 = 흐름 그림, 적용 이력 = 무엇을 언제 어떤 측정으로 바꿨나 + 항목 전체(설명 창).
 */
type GateMode = 'report' | 'retrieval' | 'answers' | 'history' | 'adhoc';

// 탭 = [리포트] [검색] [답] [즉석 질의] (v2.172, 사용자 10/26 "틀린 문항 리포트 하나로") — 옛 [개선]·[적재 검수]·[판정 이력]·[실사용 문항]은
// 리포트 안으로 합쳤다(후보 결정 = 리포트의 고칠 방법, 적재 검수 = 저장 상태·측정도 안 되는 문항, 실사용 = 문항 라벨, 기준선 = 운영 반영 때 자동)
const TABS: { mode: GateMode; label: string; icon: typeof Search; hint: string }[] = [
  { mode: 'report', label: '리포트', icon: FileText, hint: '틀린 문항마다 질문 · AI 답 · 왜 틀렸나 · 정답 데이터 저장 모양 · 고칠 기법과 실제 효과 — 사람이 보고 승인' },
  { mode: 'retrieval', label: '검색', icon: FlaskConical, hint: '지금 점수의 검색 성적 — 정답 정책이 AI에게 넘기는 문맥에 들어갔나' },
  { mode: 'answers', label: '답', icon: CheckCircle2, hint: '지금 점수의 답 성적 — 실제 채팅 경로로 답하고 정책 원문과 대조해 채점' },
  { mode: 'history', label: '적용 이력', icon: History, hint: '무엇을 언제 어떤 측정으로 바꿨나 · 결과(운영 반영·안 함·나중에) — 줄을 누르면 그 항목 설명' },
  { mode: 'adhoc', label: '검색 미리보기', icon: Search, hint: '질문 하나를 채팅과 같은 검색으로(LLM 0) — 채팅이 할 일 · 찾은 근거 · 정답 정책을 골라 시험지에 추가' },
];

export function EvaluationGate() {
  const isAdmin = useAuthStore((st) => st.user?.role === 'admin');
  const tabs = TABS.filter((t) => isAdmin || t.mode !== 'adhoc');   // 검색 미리보기 = 관리자 전용 API
  const [mode, setMode] = useState<GateMode>('report');
  const [stage, setStage] = useState<Stage | null>(null);
  const onStage = (s: Stage | null) => { setStage(s); setMode('report'); };
  const hint = tabs.find((t) => t.mode === mode)?.hint;

  return (
    <div className="space-y-4">
      <GateFlow stage={stage} onStage={onStage} />
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

      <p className="text-xs text-slate-500 leading-relaxed -mt-2">{hint}</p>

      {mode === 'report' && stage && (
        <p className="text-xs text-indigo-600 dark:text-indigo-300" data-testid="report-stage">
          {stage} 단계 문항만 보는 중 · <button onClick={() => setStage(null)} className="underline">전체 보기</button>
        </p>
      )}
      {mode === 'report' && <ReportTab key={stage ?? 'all'} stage={stage} />}
      {mode === 'history' && <GateHistory />}
      {mode === 'retrieval' && <RetrievalTab />}
      {mode === 'answers' && <AnswerTab />}
      {mode === 'adhoc' && isAdmin && <SearchPreview />}
    </div>
  );
}
