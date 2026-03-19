import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { BookOpen, Wrench, Database, CheckCircle, XCircle, RefreshCw } from 'lucide-react';
import { listAgents, checkAgentHealth } from '../../api/agents';
import type { AgentInfo } from '../../types';

const ICON_MAP: Record<string, React.ReactNode> = {
  BookOpen: <BookOpen className="w-6 h-6" />,
  Wrench:   <Wrench className="w-6 h-6" />,
  Database: <Database className="w-6 h-6" />,
};

const COLOR_MAP: Record<string, string> = {
  indigo:  'bg-indigo-600',
  violet:  'bg-violet-600',
  emerald: 'bg-emerald-600',
  slate:   'bg-slate-600',
};

function AgentCard({ agent }: { agent: AgentInfo }) {
  const [checking, setChecking] = useState(false);
  const [health, setHealth] = useState<boolean | null>(null);

  const handleHealthCheck = async () => {
    setChecking(true);
    try {
      const result = await checkAgentHealth(agent.agent_id);
      setHealth(result.healthy);
    } catch {
      setHealth(false);
    } finally {
      setChecking(false);
    }
  };

  const iconBg = COLOR_MAP[agent.color] ?? 'bg-slate-600';

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-xl p-5 space-y-3">
      <div className="flex items-start gap-4">
        <div className={`w-12 h-12 rounded-xl ${iconBg} flex items-center justify-center text-white flex-shrink-0`}>
          {ICON_MAP[agent.icon] ?? <Database className="w-6 h-6" />}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h3 className="font-semibold text-slate-200">{agent.display_name}</h3>
            <span className="text-xs text-slate-500 font-mono bg-slate-700 px-2 py-0.5 rounded">
              {agent.agent_id}
            </span>
            <span className="text-xs text-slate-500 bg-slate-700/60 px-2 py-0.5 rounded">
              {agent.output_type}
            </span>
          </div>
          <p className="text-sm text-slate-400 mt-1">{agent.description}</p>
          <p className="text-xs text-slate-500 mt-1 italic">{agent.welcome_message}</p>
        </div>
      </div>

      <div className="flex items-center gap-3 pt-2 border-t border-slate-700">
        <button
          onClick={handleHealthCheck}
          disabled={checking}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-slate-700 hover:bg-slate-600 text-slate-300 rounded-lg transition-colors disabled:opacity-50"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${checking ? 'animate-spin' : ''}`} />
          헬스 체크
        </button>
        {health !== null && (
          health ? (
            <span className="flex items-center gap-1.5 text-xs text-emerald-400">
              <CheckCircle className="w-3.5 h-3.5" /> 정상
            </span>
          ) : (
            <span className="flex items-center gap-1.5 text-xs text-rose-400">
              <XCircle className="w-3.5 h-3.5" /> 비정상
            </span>
          )
        )}
        {agent.supports_debug && (
          <span className="ml-auto text-xs text-slate-500">디버그 지원</span>
        )}
      </div>
    </div>
  );
}

export function AgentDirectoryTab() {
  const { data: agents, isLoading, error, refetch } = useQuery({
    queryKey: ['agents'],
    queryFn: listAgents,
  });

  if (isLoading) {
    return <div className="text-slate-400 text-sm">에이전트 목록 로딩 중...</div>;
  }
  if (error) {
    return <div className="text-rose-400 text-sm">에이전트 목록을 불러올 수 없습니다.</div>;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-slate-200">에이전트 디렉토리</h2>
          <p className="text-sm text-slate-400">등록된 에이전트 목록 및 상태를 확인합니다.</p>
        </div>
        <button
          onClick={() => refetch()}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-slate-700 hover:bg-slate-600 text-slate-300 rounded-lg transition-colors"
        >
          <RefreshCw className="w-3.5 h-3.5" />
          새로고침
        </button>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        {(agents ?? []).map((agent) => (
          <AgentCard key={agent.agent_id} agent={agent} />
        ))}
        {agents?.length === 0 && (
          <p className="text-slate-500 text-sm col-span-2">등록된 에이전트가 없습니다.</p>
        )}
      </div>
    </div>
  );
}
