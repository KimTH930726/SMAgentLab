import { BookOpen, Database, Wrench, ShieldAlert, Cloud } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { useAppStore, type AgentType } from '../store/useAppStore';

import logoSvg from '../assets/logo.svg';

interface AgentCard {
  id: AgentType;
  icon: React.ReactNode;
  title: string;
  description: string;
  features: string[];
  color: string;
  border: string;
  iconBg: string;
  disabled?: boolean;
  badge?: string;
}

const AGENTS: AgentCard[] = [
  {
    id: 'knowledge_rag',
    icon: <BookOpen className="w-8 h-8" />,
    title: '지식베이스 AI',
    description: '사내 문서·지식을 기반으로 질문에 답변합니다.',
    features: ['문서 RAG 검색', '하이브리드 벡터/키워드 검색', 'MCP 도구 연동 (선택)', '멀티턴 대화 메모리'],
    color: 'text-indigo-400',
    border: 'border-indigo-500/50 hover:border-indigo-400',
    iconBg: 'bg-indigo-500/10',
  },
  {
    id: 'text2sql',
    icon: <Database className="w-8 h-8" />,
    title: 'Text-to-SQL',
    description: '자연어로 데이터베이스에 질문하고 결과를 확인합니다.',
    features: ['자연어 → SQL 변환', 'CoT 단계별 추론', 'SQL 자동 검증 & 수정', '결과 시각화 (차트/테이블)'],
    color: 'text-emerald-400',
    border: 'border-emerald-500/50 hover:border-emerald-400',
    iconBg: 'bg-emerald-500/10',
  },
  {
    id: 'knowledge_rag' as AgentType,
    icon: <ShieldAlert className="w-8 h-8" />,
    title: '시스템 이상 탐지',
    description: '시스템 로그·메트릭을 분석하여 이상 징후를 탐지합니다.',
    features: ['로그 패턴 분석', '이상 탐지 알림', '장애 원인 추론', '대응 방안 추천'],
    color: 'text-orange-400',
    border: 'border-orange-500/30',
    iconBg: 'bg-orange-500/10',
    disabled: true,
    badge: '26Y 3Q 예정',
  },
  {
    id: 'knowledge_rag' as AgentType,
    icon: <Cloud className="w-8 h-8" />,
    title: '클라우드 자원 산정',
    description: '워크로드에 맞는 최적의 클라우드 자원을 산정합니다.',
    features: ['워크로드 분석', '비용 최적화 추천', '스케일링 시뮬레이션', '멀티 클라우드 비교'],
    color: 'text-sky-400',
    border: 'border-sky-500/30',
    iconBg: 'bg-sky-500/10',
    disabled: true,
    badge: '26Y 4Q 예정',
  },
];

function HealthBadge() {
  const { data, isLoading, isError } = useQuery<{ status: string; llm: string; llm_provider: string }>({
    queryKey: ['health'],
    queryFn: () => fetch('/health').then(r => r.json()),
    refetchInterval: 30000,
    staleTime: 15000,
  });

  if (isLoading) {
    return (
      <div className="flex items-center gap-1.5 text-xs text-slate-500">
        <span className="w-2 h-2 rounded-full bg-slate-600 animate-pulse" />
        상태 확인 중...
      </div>
    );
  }

  const ok = !isError && data?.status === 'ok';
  const llmOk = !isError && data?.llm === 'connected';

  return (
    <div className="flex items-center gap-4 text-xs">
      <div className="flex items-center gap-1.5">
        <span className={`w-2 h-2 rounded-full ${ok ? 'bg-emerald-400' : 'bg-rose-500'}`} />
        <span className={ok ? 'text-emerald-400' : 'text-rose-400'}>서버 {ok ? '정상' : '오류'}</span>
      </div>
      <div className="flex items-center gap-1.5">
        <span className={`w-2 h-2 rounded-full ${llmOk ? 'bg-emerald-400' : 'bg-amber-400'}`} />
        <span className={llmOk ? 'text-emerald-400' : 'text-amber-400'}>
          LLM {llmOk ? '연결됨' : '연결 안됨'}{data?.llm_provider ? ` (${data.llm_provider})` : ''}
        </span>
      </div>
    </div>
  );
}

export default function AgentSelect() {
  const setSelectedAgent = useAppStore((s) => s.setSelectedAgent);

  return (
    <div className="min-h-screen bg-slate-900 flex flex-col items-center justify-center px-6 py-12">
      {/* Header */}
      <div className="flex items-center gap-3 mb-10">
        <img src={logoSvg} alt="logo" className="w-10 h-10" />
        <span className="text-2xl font-bold text-slate-100">Ops-Navigator</span>
      </div>

      <h1 className="text-xl font-semibold text-slate-200 mb-2">에이전트 선택</h1>
      <p className="text-sm text-slate-500 mb-4">사용할 AI 에이전트를 선택하세요.</p>

      {/* Health check */}
      <div className="mb-8">
        <HealthBadge />
      </div>

      {/* Agent cards — horizontal scroll if overflow */}
      <div className="w-full max-w-5xl overflow-x-auto pb-4">
        <div className="flex gap-5 min-w-max px-1">
          {AGENTS.map((agent, i) => (
            <button
              key={`${agent.id}-${i}`}
              onClick={() => !agent.disabled && setSelectedAgent(agent.id)}
              disabled={agent.disabled}
              className={`w-64 flex-shrink-0 text-left bg-slate-800 border-2 ${agent.border} rounded-2xl p-6 transition-all duration-200 ${
                agent.disabled
                  ? 'opacity-50 cursor-not-allowed'
                  : 'hover:bg-slate-750 hover:scale-[1.02] focus:outline-none focus:ring-2 focus:ring-indigo-500'
              }`}
            >
              <div className={`inline-flex p-3 rounded-xl ${agent.iconBg} ${agent.color} mb-4`}>
                {agent.icon}
              </div>
              <div className="flex items-center gap-2 mb-1">
                <h2 className={`text-lg font-semibold ${agent.color}`}>{agent.title}</h2>
                {agent.badge && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-700 text-slate-400 whitespace-nowrap">{agent.badge}</span>
                )}
              </div>
              <p className="text-sm text-slate-400 mb-4">{agent.description}</p>
              <ul className="space-y-1.5">
                {agent.features.map((f) => (
                  <li key={f} className="flex items-center gap-2 text-xs text-slate-500">
                    <span className={`w-1 h-1 rounded-full flex-shrink-0`} style={{ background: 'currentColor' }} />
                    {f}
                  </li>
                ))}
              </ul>

              <div className={`mt-5 flex items-center gap-1.5 text-xs font-medium ${agent.disabled ? 'text-slate-600' : agent.color}`}>
                <Wrench className="w-3.5 h-3.5" />
                {agent.disabled ? '준비 중' : '선택하기'}
              </div>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
