import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { ChevronDown, ChevronUp, FileWarning, Info, ArrowRightCircle } from 'lucide-react';
import { getUnresolvedSummary, promoteUnresolvedSegment } from '../../api/policy';
import { useNamespaceAccess } from '../../utils/useNamespaceAccess';
import { Badge } from '../ui/Badge';

/**
 * 정책서 unresolved 팀별 집계 리포트.
 *
 * LLM 분해가 서술/파라미터 어디에도 못 넣은 내용을 팀(system_key)별로 보여준다. 정밀
 * 재분류(param 필드 추출) 같은 승인 화면은 여전히 없다 — 그건 별도 "검토 UI"
 * (docs/policy-doc-pipeline-plan.md §6, 미착수) 몫이다. 다만 2026-09-16, "조회만 있고
 * 아무 액션도 없다"는 지적을 받고 **딱 하나의 액션(서술로 편입)만 추가** — 원문을 그대로
 * policy_chunk에 넣어 최소한 검색은 되게 만드는 원클릭 액션. 완전한 검토 화면의 대체가
 * 아니라 "완전 방치"를 벗어나는 최소선.
 *
 * 2026-09-04 사용자 피드백 반영: (1) 라이트모드에서 amber 텍스트 대비가 낮아 안 읽힘 —
 * slate 팔레트는 CSS 변수로 테마에 따라 자동 전환되지만 amber 등 강조색은 그렇지 않아 dark:
 * 변형을 명시해야 함(Badge.tsx가 이미 하던 패턴을 여기서 놓쳤었음). (2) "표준화 요청 근거로
 * 쓰라"는 설명이 추상적이라 사용자가 뭘 해야 할지 안 와닿음 — 원문/사유/다음 액션을 명시적으로
 * 분리해 보여주도록 재구성.
 */
export function PolicyUnresolvedReport() {
  const { selectedNs, setSelectedNs, sortedNamespaces } = useNamespaceAccess();
  const [systemFilter, setSystemFilter] = useState('');
  const [expandedItemId, setExpandedItemId] = useState<number | null>(null);
  const queryClient = useQueryClient();

  const { data, isLoading, error } = useQuery({
    queryKey: ['policy-unresolved-summary', selectedNs],
    queryFn: () => getUnresolvedSummary(selectedNs),
    enabled: !!selectedNs,
    staleTime: 15_000,
    refetchOnMount: 'always',
  });

  // segment_index는 서버의 unresolved_segments 배열 순서에 의존한다 — 편입 성공 후 배열이
  // 한 칸씩 당겨지므로, 같은 item에서 연달아 편입할 때 인덱스가 어긋나지 않도록 매번
  // summary를 다시 받아온다(로컬에서 배열을 직접 잘라내지 않음).
  const promoteMutation = useMutation({
    mutationFn: ({ itemId, segmentIndex }: { itemId: number; segmentIndex: number }) =>
      promoteUnresolvedSegment(itemId, segmentIndex, selectedNs),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['policy-unresolved-summary', selectedNs] }),
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

      {/* 사용법 안내 — "왜 여기 있고 뭘 해야 하는지"를 명시적으로 */}
      <div className="flex gap-3 px-4 py-3 bg-indigo-50 border border-indigo-200 dark:bg-indigo-900/20 dark:border-indigo-700/30 rounded-xl text-xs text-slate-300 leading-relaxed">
        <Info className="w-4 h-4 text-indigo-500 dark:text-indigo-400 flex-shrink-0 mt-0.5" />
        <div className="space-y-1">
          <p>
            AI가 정책 원문을 "서술 설명"이나 "값 하나짜리 파라미터" 어느 쪽으로도 자동 분류하지
            못한 부분입니다. <b>데이터가 사라진 건 아니고</b> 원문 그대로 보존된 상태입니다.
          </p>
          <p>
            펼쳐서 <b>"서술로 편입"</b>을 누르면 원문을 그대로 검색 가능한 서술 지식으로 바로
            등록합니다(값·조건을 정밀하게 구조화하는 건 아니고, 최소한 검색은 되게 만드는
            원클릭 액션입니다). 정밀 재분류가 필요하거나 팀에 원문 수정을 요청할 근거로 쓰고
            싶으면 편입하지 않고 그대로 둬도 됩니다.
          </p>
        </div>
      </div>

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
      {selectedNs && error && <div className="text-center py-10 text-rose-600 dark:text-rose-400">오류가 발생했습니다.</div>}

      {selectedNs && data && (
        <>
          <div className="flex items-center gap-4 px-4 py-3 bg-slate-800 border border-slate-700 rounded-xl text-sm">
            <FileWarning className="w-4 h-4 text-amber-600 dark:text-amber-400 flex-shrink-0" />
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
                          {item.segments.map((seg, idx) => {
                            const isThisPending = promoteMutation.isPending
                              && promoteMutation.variables?.itemId === item.item_id
                              && promoteMutation.variables?.segmentIndex === idx;
                            return (
                              <div key={idx} className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 space-y-1.5">
                                <div>
                                  <p className="text-[10px] font-medium uppercase tracking-wide text-slate-500">원문</p>
                                  <p className="text-sm text-slate-300 leading-relaxed">{seg.text}</p>
                                </div>
                                {seg.reason && (
                                  <div>
                                    <p className="text-[10px] font-medium uppercase tracking-wide text-slate-500">왜 자동 분류가 안 됐나요</p>
                                    <p className="text-xs text-amber-700 dark:text-amber-400/90">{seg.reason}</p>
                                  </div>
                                )}
                                <div className="flex justify-end pt-1">
                                  <button
                                    type="button"
                                    disabled={promoteMutation.isPending}
                                    onClick={() => promoteMutation.mutate({ itemId: item.item_id, segmentIndex: idx })}
                                    title="원문을 그대로 검색 가능한 서술 지식으로 등록합니다(정밀 재분류는 아님)"
                                    className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-medium border border-indigo-300 text-indigo-700 bg-indigo-50 hover:bg-indigo-100 dark:border-indigo-600/40 dark:text-indigo-300 dark:bg-indigo-500/10 dark:hover:bg-indigo-500/20 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                                  >
                                    {isThisPending ? (
                                      <>편입 중...</>
                                    ) : (
                                      <><ArrowRightCircle className="w-3.5 h-3.5" /> 서술로 편입</>
                                    )}
                                  </button>
                                </div>
                              </div>
                            );
                          })}
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
