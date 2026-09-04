import { useState, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ChevronDown, ChevronUp, Database, Sparkles, Search, X } from 'lucide-react';
import { getPolicyItems } from '../../api/policy';
import { useNamespaceAccess } from '../../utils/useNamespaceAccess';
import { Badge } from '../ui/Badge';
import { PaginationInfo, PaginationNav, useClientPaging } from '../ui/Pagination';

/**
 * 정책 항목(policy_item) 브라우저 — 읽기 전용.
 *
 * `/api/policy/search`(질의 기반 검색)와는 목적이 다르다 — 이 화면은 쿼리 없이도 "지금 뭐가
 * 어떻게 저장돼 있는지" item 단위로 전체를 훑어보는 용도다. 각 항목을 펼치면 그 밑에 실제로
 * 어떤 param(RDB 정확조회)과 narrative(벡터 검색 청크)가 달려있는지 아이콘으로 구분해 보여준다
 * — docs/policy-doc-pipeline-plan.md §2 "왜 3층인가"를 화면에서 직접 확인할 수 있게.
 */
export function PolicyItemBrowser() {
  const { selectedNs, setSelectedNs, sortedNamespaces } = useNamespaceAccess();
  const [categoryFilter, setCategoryFilter] = useState('');
  const [qInput, setQInput] = useState('');
  const [q, setQ] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const { data: items = [], isLoading, error } = useQuery({
    queryKey: ['policy-items', selectedNs, categoryFilter, q],
    queryFn: () => getPolicyItems(selectedNs, categoryFilter || undefined, q || undefined),
    enabled: !!selectedNs,
    staleTime: 15_000,
    refetchOnMount: 'always',
  });

  // 대분류 드롭다운 선택지는 필터링된 items가 아니라 네임스페이스 전체 목록에서 뽑아야 한다 —
  // 안 그러면 카테고리를 고르는 순간 그 필터링된 결과에서 선택지를 다시 뽑아서 방금 고른
  // 카테고리 하나만 남고 나머지가 사라지는 자기잠식 버그가 생긴다(실사용 중 발견, 2026-09-04).
  const { data: allItems = [] } = useQuery({
    queryKey: ['policy-items-all', selectedNs],
    queryFn: () => getPolicyItems(selectedNs),
    enabled: !!selectedNs,
    staleTime: 30_000,
  });

  useEffect(() => { setPage(1); }, [selectedNs, categoryFilter, q, pageSize]);

  const categoryOptions = Array.from(new Set(allItems.map((i) => i.category_path[0]).filter(Boolean))).sort();

  const { totalPages, totalItems, slice } = useClientPaging(items, pageSize);
  const pagedItems = slice(page);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-800 dark:text-slate-200">
          정책 항목 브라우저
          {selectedNs && <span className="text-sm font-normal text-slate-500 ml-2">({selectedNs})</span>}
        </h2>
      </div>
      <p className="text-xs text-slate-500 -mt-2">
        지금까지 임포트된 정책 항목을 전체 목록으로 훑어봅니다. 항목을 펼치면 파라미터(RDB 정확
        조회)와 서술(벡터 검색)이 각각 어떻게 저장돼 있는지 확인할 수 있습니다.
      </p>

      <div className="flex flex-wrap items-end gap-3">
        <div>
          <label className="block text-xs font-medium text-slate-400 mb-1.5">파트</label>
          <select
            value={selectedNs}
            onChange={(e) => { setSelectedNs(e.target.value); setCategoryFilter(''); setQ(''); setQInput(''); setExpandedId(null); }}
            className="w-56 bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
          >
            <option value="">선택...</option>
            {sortedNamespaces.map((ns) => <option key={ns} value={ns}>{ns}</option>)}
          </select>
        </div>
        {categoryOptions.length > 0 && (
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1.5">대분류</label>
            <select
              value={categoryFilter}
              onChange={(e) => setCategoryFilter(e.target.value)}
              className="w-44 bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
            >
              <option value="">전체</option>
              {categoryOptions.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
        )}
        <div className="flex-1 min-w-[200px]">
          <label className="block text-xs font-medium text-slate-400 mb-1.5">정책명 검색</label>
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
            <input
              type="text"
              placeholder="정책명으로 필터링 (Enter)..."
              value={qInput}
              onChange={(e) => setQInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && setQ(qInput)}
              className="w-full pl-9 pr-8 py-2 bg-slate-900 border border-slate-600 rounded-lg text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
            />
            {q && (
              <button
                onClick={() => { setQ(''); setQInput(''); }}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
        </div>
      </div>

      {!selectedNs && <div className="text-center py-10 text-slate-500">파트를 선택하세요.</div>}
      {selectedNs && isLoading && <div className="text-center py-10 text-slate-500 animate-pulse">로딩 중...</div>}
      {selectedNs && error && <div className="text-center py-10 text-rose-400">오류가 발생했습니다.</div>}

      {selectedNs && !isLoading && (
        <div className="space-y-2">
          <PaginationInfo totalItems={totalItems} pageSize={pageSize} onPageSizeChange={setPageSize} />
          {pagedItems.length === 0 && (
            <div className="text-center py-10 text-slate-500">항목이 없습니다.</div>
          )}
          {pagedItems.map((item) => (
            <div key={item.item_id} className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
              <div
                className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-slate-700/50 transition-colors"
                onClick={() => setExpandedId(expandedId === item.item_id ? null : item.item_id)}
              >
                <div className="flex-1 min-w-0">
                  <span className="text-sm font-medium text-slate-200">{item.policy_name}</span>
                  {item.category_path.length > 0 && (
                    <span className="text-xs text-slate-500 ml-2">{item.category_path.join(' / ')}</span>
                  )}
                </div>
                {item.matched_via.includes('param') && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-cyan-900/40 text-cyan-300 border border-cyan-700/40" title="키워드(RDB) 매칭">키워드</span>
                )}
                {item.matched_via.includes('narrative') && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-violet-900/40 text-violet-300 border border-violet-700/40" title="벡터(의미) 매칭">벡터</span>
                )}
                {item.params.length > 0 && (
                  <span className="flex items-center gap-1 text-[11px] text-cyan-700 dark:text-cyan-400" title="RDB 정확조회 파라미터">
                    <Database className="w-3.5 h-3.5" />{item.params.length}
                  </span>
                )}
                {item.narratives.length > 0 && (
                  <span className="flex items-center gap-1 text-[11px] text-violet-700 dark:text-violet-400" title="벡터 검색 서술 청크">
                    <Sparkles className="w-3.5 h-3.5" />{item.narratives.length}
                  </span>
                )}
                {item.parse_status !== 'parsed' && (
                  <Badge color="amber">{item.parse_status}</Badge>
                )}
                <Badge color={item.status === 'pending_review' ? 'yellow' : 'slate'}>{item.status}</Badge>
                {expandedId === item.item_id ? (
                  <ChevronUp className="w-4 h-4 text-slate-400 flex-shrink-0" />
                ) : (
                  <ChevronDown className="w-4 h-4 text-slate-400 flex-shrink-0" />
                )}
              </div>

              {expandedId === item.item_id && (
                <div className="border-t border-slate-700 px-4 py-4 space-y-4">
                  <div>
                    <p className="text-xs font-medium text-slate-400 mb-2">원문</p>
                    <div className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300 leading-relaxed whitespace-pre-wrap">
                      {item.raw_body}
                    </div>
                  </div>
                  {item.params.length > 0 && (
                    <div>
                      <p className="flex items-center gap-1.5 text-xs font-medium text-cyan-700 dark:text-cyan-400 mb-2">
                        <Database className="w-3.5 h-3.5" />파라미터 (RDB 정확조회) — policy_param {item.params.length}건
                      </p>
                      <div className="space-y-1.5">
                        {item.params.map((p) => (
                          <div key={p.id} className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300">
                            <span className="font-medium text-slate-200">{p.name}</span>
                            {p.condition && <span className="text-slate-500"> ({p.condition})</span>}
                            <span className="text-cyan-600 dark:text-cyan-300"> = {p.value}{p.unit ? ` ${p.unit}` : ''}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  {item.narratives.length > 0 && (
                    <div>
                      <p className="flex items-center gap-1.5 text-xs font-medium text-violet-700 dark:text-violet-400 mb-2">
                        <Sparkles className="w-3.5 h-3.5" />서술 (벡터 검색) — policy_chunk {item.narratives.length}건
                      </p>
                      <div className="space-y-1.5">
                        {item.narratives.map((c) => (
                          <div key={c.id} className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300 leading-relaxed">
                            {c.chunk_text}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  {item.params.length === 0 && item.narratives.length === 0 && (
                    <p className="text-sm text-slate-500">이 항목엔 param/narrative가 없습니다(전부 unresolved로 처리됨 — "정책서 미분류" 탭 참고).</p>
                  )}
                </div>
              )}
            </div>
          ))}
          <PaginationNav page={page} totalPages={totalPages} onPageChange={setPage} />
        </div>
      )}
    </div>
  );
}
