import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ChevronDown, ChevronUp, FileWarning } from 'lucide-react';
import { getUnresolvedSummary } from '../../api/policy';
import { useNamespaceAccess } from '../../utils/useNamespaceAccess';
import { Badge } from '../ui/Badge';

/**
 * 정책서 unresolved 팀별 집계 리포트 — 읽기 전용.
 *
 * LLM 분해가 서술/파라미터 어디에도 못 넣은 내용을 팀(system_key)별로 보여준다. 승인/재분류
 * 등 쓰기 동작은 없다 — 그건 별도 "검토 UI"(docs/policy-doc-pipeline-plan.md §6, 미착수) 몫이고,
 * 이 화면의 목적은 표준화 요청 근거 자료를 사람이 눈으로 훑어볼 수 있게 하는 것뿐이다.
 */
export function PolicyUnresolvedReport() {
  const { selectedNs, setSelectedNs, sortedNamespaces } = useNamespaceAccess();
  const [systemFilter, setSystemFilter] = useState('');
  const [expandedItemId, setExpandedItemId] = useState<number | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ['policy-unresolved-summary', selectedNs],
    queryFn: () => getUnresolvedSummary(selectedNs),
    enabled: !!selectedNs,
    staleTime: 15_000,
    refetchOnMount: 'always',
  });

  const groups = systemFilter
    ? (data?.by_system ?? []).filter((g) => g.system_key === systemFilter)
    : (data?.by_system ?? []);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-200">
          정책서 미분류(unresolved) 리포트
          {selectedNs && <span className="text-sm font-normal text-slate-500 ml-2">({selectedNs})</span>}
        </h2>
      </div>
      <p className="text-xs text-slate-500 -mt-2">
        LLM이 서술/파라미터 어디로도 자동 분류하지 못한 내용을 팀별로 모았습니다. 표준화 요청이나
        분해 프롬프트 개선의 근거 자료로 사용하세요. 이 화면에서는 승인·수정은 할 수 없습니다.
      </p>

      {/* Namespace selector */}
      <div className="flex items-end gap-3">
        <div>
          <label className="block text-xs font-medium text-slate-400 mb-1.5">파트</label>
          <select
            value={selectedNs}
            onChange={(e) => { setSelectedNs(e.target.value); setSystemFilter(''); setExpandedItemId(null); }}
            className="w-56 bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
          >
            <option value="">선택...</option>
            {sortedNamespaces.map((ns) => <option key={ns} value={ns}>{ns}</option>)}
          </select>
        </div>
        {data && data.by_system.length > 0 && (
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1.5">시스템(팀)</label>
            <select
              value={systemFilter}
              onChange={(e) => setSystemFilter(e.target.value)}
              className="w-44 bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
            >
              <option value="">전체</option>
              {data.by_system.map((g) => (
                <option key={g.system_key} value={g.system_key}>{g.system_key}</option>
              ))}
            </select>
          </div>
        )}
      </div>

      {!selectedNs && <div className="text-center py-10 text-slate-500">파트를 선택하세요.</div>}
      {selectedNs && isLoading && <div className="text-center py-10 text-slate-500 animate-pulse">로딩 중...</div>}
      {selectedNs && error && <div className="text-center py-10 text-rose-400">오류가 발생했습니다.</div>}

      {selectedNs && data && (
        <>
          <div className="flex items-center gap-4 px-4 py-3 bg-slate-800 border border-slate-700 rounded-xl text-sm">
            <FileWarning className="w-4 h-4 text-amber-400 flex-shrink-0" />
            <span className="text-slate-300">전체 <span className="font-semibold text-slate-100">{data.total_items}</span>건 / segment <span className="font-semibold text-slate-100">{data.total_segments}</span>개</span>
          </div>

          {groups.length === 0 && (
            <div className="text-center py-10 text-slate-500">미분류 항목이 없습니다.</div>
          )}

          <div className="space-y-3">
            {groups.map((group) => (
              <div key={group.system_key} className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
                <div className="flex items-center gap-3 px-4 py-3 border-b border-slate-700">
                  <span className="text-sm font-medium text-slate-200">{group.system_key}</span>
                  <Badge color="amber">{group.item_count}건</Badge>
                  <Badge color="slate">segment {group.segment_count}개</Badge>
                </div>
                <div className="divide-y divide-slate-700/60">
                  {group.items.map((item) => (
                    <div key={item.item_id}>
                      <div
                        className="flex items-center gap-3 px-4 py-2.5 cursor-pointer hover:bg-slate-700/40 transition-colors"
                        onClick={() => setExpandedItemId(expandedItemId === item.item_id ? null : item.item_id)}
                      >
                        <div className="flex-1 min-w-0">
                          <span className="text-sm text-slate-200">{item.policy_name}</span>
                          {item.category_path.length > 0 && (
                            <span className="text-xs text-slate-500 ml-2">{item.category_path.join(' / ')}</span>
                          )}
                        </div>
                        <Badge color="slate">{item.segments.length}개</Badge>
                        {expandedItemId === item.item_id ? (
                          <ChevronUp className="w-4 h-4 text-slate-400 flex-shrink-0" />
                        ) : (
                          <ChevronDown className="w-4 h-4 text-slate-400 flex-shrink-0" />
                        )}
                      </div>
                      {expandedItemId === item.item_id && (
                        <div className="px-4 pb-3 space-y-2">
                          {item.segments.map((seg, idx) => (
                            <div key={idx} className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-2">
                              <p className="text-sm text-slate-300 leading-relaxed">{seg.text}</p>
                              {seg.reason && (
                                <p className="text-xs text-amber-400/80 mt-1">사유: {seg.reason}</p>
                              )}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
