import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { ChevronDown, ChevronUp, Database, Sparkles, Search, X, Check, Pencil, RotateCcw } from 'lucide-react';
import {
  getPolicyItems, approvePolicyItem, rejectPolicyItem, updatePolicyParam, updatePolicyNarrative, revertAutoPolicyItem,
  type PolicyItem, type RiskLevel,
} from '../../api/policy';
import { PolicyReviewQueuePanel } from './PolicyReviewQueuePanel';
import { UnresolvedSegmentActions } from './UnresolvedSegmentActions';
import { useNamespaceAccess } from '../../utils/useNamespaceAccess';
import { Badge } from '../ui/Badge';
import { PaginationInfo, PaginationNav, useClientPaging } from '../ui/Pagination';

const STATUS_LABEL: Record<string, string> = {
  pending_review: '검토대기',
  active: '승인됨',
  rejected: '반려됨',
};

const STATUS_BADGE_COLOR: Record<string, 'yellow' | 'emerald' | 'rose' | 'slate'> = {
  pending_review: 'yellow',
  active: 'emerald',
  rejected: 'rose',
};

// 위험도(서버 risk.py) — 높음/중간은 사람이 보고, 낮음은 자동 통과 후보
const RISK_LABEL: Record<RiskLevel, string> = { high: '위험 높음', medium: '위험 중간', low: '위험 낮음' };
const RISK_CLASS: Record<RiskLevel, string> = {
  high: 'bg-rose-50 text-rose-700 border-rose-200 dark:bg-rose-900/30 dark:text-rose-300 dark:border-rose-700/50',
  medium: 'bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-900/30 dark:text-amber-300 dark:border-amber-700/50',
  low: 'border-slate-600 text-slate-400',
};

function StatusBadge({ item }: { item: PolicyItem }) {
  // 자동 통과는 사람 승인과 다른 표시 — 되돌릴 수 있고, 나중에 기준 조정의 대상
  if (item.status === 'active' && item.review_source === 'auto_rule') {
    return (
      <span className="text-[10px] px-1.5 py-0.5 rounded border bg-cyan-50 text-cyan-700 border-cyan-200 dark:bg-cyan-900/40 dark:text-cyan-300 dark:border-cyan-700/50"
        title={`위험도 낮음 규칙(${item.review_rule})으로 자동 통과 — 사람이 승인한 것이 아닙니다`}>
        자동 통과
      </span>
    );
  }
  return <Badge color={STATUS_BADGE_COLOR[item.status] ?? 'slate'}>{STATUS_LABEL[item.status] ?? item.status}</Badge>;
}

/**
 * 정책 항목(policy_item) 브라우저.
 *
 * `/api/policy/search`(질의 기반 검색)와는 목적이 다르다 — 이 화면은 쿼리 없이도 "지금 뭐가
 * 어떻게 저장돼 있는지" item 단위로 전체를 훑어보는 용도다. 각 항목을 펼치면 그 밑에 실제로
 * 어떤 param(RDB 정확조회)과 narrative(벡터 검색 청크)가 달려있는지 아이콘으로 구분해 보여준다
 * — docs/policy-doc-pipeline-plan.md §2 "왜 3층인가"를 화면에서 직접 확인할 수 있게.
 *
 * 승인/반려(2026-09-23) — "pending 필터링이 없다"는 지적의 근본 원인은 필터 부재가 아니라
 * 모든 항목이 임포트 시점부터 영원히 `pending_review`로 남고 그걸 바꾸는 액션 자체가
 * 없었던 것이었다(검토 UI, docs/policy-doc-pipeline-plan.md 미착수 항목). 상태 필터보다
 * 승인/반려 액션을 먼저 추가하고, 상태 필터는 그 결과를 보기 위한 보조 수단으로 같이 둔다.
 */
export function PolicyItemBrowser() {
  const { selectedNs, setSelectedNs, sortedNamespaces, canModifyNs } = useNamespaceAccess();
  const [categoryFilter, setCategoryFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [qInput, setQInput] = useState('');
  const [q, setQ] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const queryClient = useQueryClient();

  const { data: items = [], isLoading, error } = useQuery({
    queryKey: ['policy-items', selectedNs, categoryFilter, q, statusFilter],
    // 검토대기는 위험도 순(높음 → 중간 → 표본 → 낮음) — 사람이 위에서부터 보면 되게
    queryFn: () => getPolicyItems(selectedNs, categoryFilter || undefined, q || undefined, statusFilter || undefined,
      statusFilter === 'pending_review' ? 'risk' : undefined),
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

  const invalidateItems = () => {
    queryClient.invalidateQueries({ queryKey: ['policy-items', selectedNs] });
    queryClient.invalidateQueries({ queryKey: ['policy-items-all', selectedNs] });
    queryClient.invalidateQueries({ queryKey: ['policy-review-summary', selectedNs] });
  };

  const approveMutation = useMutation({
    mutationFn: (itemId: number) => approvePolicyItem(itemId, selectedNs),
    onSuccess: invalidateItems,
    onError: (err: Error) => alert(err.message),
  });
  const rejectMutation = useMutation({
    mutationFn: (itemId: number) => rejectPolicyItem(itemId, selectedNs),
    onSuccess: (res) => {
      invalidateItems();
      if (res.rule_paused) {
        alert('자동 통과 표본이 반려되어, 같은 규칙의 자동 통과를 멈췄습니다.\n상단 요약에서 이미 자동 통과된 항목을 되돌리거나 확인 후 재개할 수 있습니다.');
      }
    },
    onError: (err: Error) => alert(err.message),
  });
  const revertMutation = useMutation({
    mutationFn: (itemId: number) => revertAutoPolicyItem(itemId, selectedNs),
    onSuccess: invalidateItems,
    onError: (err: Error) => alert(err.message),
  });

  // 반려된 항목의 파라미터/서술 수정(2026-09-23) — "반려하면 그냥 데이터를 버리는데?"라는
  // 지적으로 추가. 저장하면 서버가 자동으로 검토대기로 되돌린다(edit.py 참고) — 그래서
  // 성공 시 목록만 invalidate하면 배지가 알아서 갱신된다.
  const [editingParamId, setEditingParamId] = useState<number | null>(null);
  const [paramEditForm, setParamEditForm] = useState({ name: '', condition: '', value: '', unit: '' });
  const [editingChunkId, setEditingChunkId] = useState<number | null>(null);
  const [chunkEditForm, setChunkEditForm] = useState('');

  const updateParamMutation = useMutation({
    mutationFn: (paramId: number) => updatePolicyParam(paramId, selectedNs, {
      name: paramEditForm.name.trim(),
      condition: paramEditForm.condition.trim() || null,
      value: paramEditForm.value.trim() || null,
      unit: paramEditForm.unit.trim() || null,
    }),
    onSuccess: () => { invalidateItems(); setEditingParamId(null); },
    onError: (err: Error) => alert(err.message),
  });
  const updateNarrativeMutation = useMutation({
    mutationFn: (chunkId: number) => updatePolicyNarrative(chunkId, selectedNs, chunkEditForm.trim()),
    onSuccess: () => { invalidateItems(); setEditingChunkId(null); },
    onError: (err: Error) => alert(err.message),
  });

  useEffect(() => { setPage(1); }, [selectedNs, categoryFilter, q, statusFilter, pageSize]);

  const categoryOptions = Array.from(new Set(allItems.map((i) => i.category_path[0]).filter(Boolean))).sort();

  const { totalPages, totalItems, slice } = useClientPaging(items, pageSize);
  const pagedItems = slice(page);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-200">
          정책 항목 브라우저
          {selectedNs && <span className="text-sm font-normal text-slate-500 ml-2">({selectedNs})</span>}
        </h2>
      </div>
      <p className="text-xs text-slate-500 -mt-2">
        지금까지 임포트된 정책 항목을 전체 목록으로 훑어봅니다. 항목을 펼치면 파라미터(RDB 정확
        조회)와 서술(벡터 검색)이 각각 어떻게 저장돼 있는지 확인할 수 있습니다.
      </p>
      {selectedNs && (
        <PolicyReviewQueuePanel namespace={selectedNs} onShowQueue={() => { setStatusFilter('pending_review'); setQ(''); setQInput(''); }} />
      )}

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
        <div>
          <label className="block text-xs font-medium text-slate-400 mb-1.5">검토 상태</label>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="w-36 bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
          >
            <option value="">전체</option>
            <option value="pending_review">검토대기</option>
            <option value="active">승인됨</option>
            <option value="rejected">반려됨</option>
          </select>
        </div>
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
      {selectedNs && error && <div className="text-center py-10 text-rose-600 dark:text-rose-400">오류가 발생했습니다.</div>}

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
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-cyan-50 text-cyan-700 border border-cyan-200 dark:bg-cyan-900/40 dark:text-cyan-300 dark:border-cyan-700/40" title="키워드(RDB) 매칭">키워드</span>
                )}
                {item.matched_via.includes('narrative') && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-violet-50 text-violet-700 border border-violet-200 dark:bg-violet-900/40 dark:text-violet-300 dark:border-violet-700/40" title="벡터(의미) 매칭">벡터</span>
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
                {item.status === 'pending_review' && item.review_sample && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded border bg-indigo-50 text-indigo-700 border-indigo-200 dark:bg-indigo-900/30 dark:text-indigo-300 dark:border-indigo-700/50"
                    title="자동 통과 대상 중 사람이 확인하도록 남긴 표본 — 반려하면 같은 규칙의 자동 통과가 멈춥니다">
                    표본 확인
                  </span>
                )}
                {item.status === 'pending_review' && item.risk_level && (
                  <span className={`text-[10px] px-1.5 py-0.5 rounded border ${RISK_CLASS[item.risk_level]}`}
                    title={item.risk_reasons.join('\n')}>
                    {RISK_LABEL[item.risk_level]}
                  </span>
                )}
                <StatusBadge item={item} />
                {canModifyNs && item.status === 'active' && item.review_source === 'auto_rule' && (
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      if (window.confirm(`"${item.policy_name}"의 자동 통과를 되돌려 검토대기로 보낼까요?`)) revertMutation.mutate(item.item_id);
                    }}
                    disabled={revertMutation.isPending && revertMutation.variables === item.item_id}
                    title="자동 통과 되돌리기 — 검토대기로 보내 사람이 확인(검색 결과는 바뀌지 않음)"
                    className="p-1 rounded text-slate-400 hover:text-slate-200 hover:bg-slate-700 disabled:opacity-50"
                  >
                    <RotateCcw className="w-3.5 h-3.5" />
                  </button>
                )}
                {canModifyNs && item.status === 'pending_review' && (
                  <div className="flex items-center gap-1 flex-shrink-0" onClick={(e) => e.stopPropagation()}>
                    <button
                      type="button"
                      disabled={approveMutation.isPending && approveMutation.variables === item.item_id}
                      onClick={() => approveMutation.mutate(item.item_id)}
                      title="승인 — 검토 완료로 표시합니다(검토대기 상태에서도 이미 검색에 포함되고 있었습니다)"
                      className="p-1 rounded text-emerald-600 hover:bg-emerald-50 dark:text-emerald-400 dark:hover:bg-emerald-950/40 disabled:opacity-50"
                    >
                      <Check className="w-3.5 h-3.5" />
                    </button>
                    <button
                      type="button"
                      disabled={rejectMutation.isPending && rejectMutation.variables === item.item_id}
                      onClick={() => {
                        // 승인과 달리 반려는 검색/채팅에서 실제로 빠져서, 확인 없이 바로
                        // 누르면 위험하다(아이콘이 붙어 있어 오클릭 가능성도 있음) —
                        // confirm으로 한 번 막는다. 반려 후에는 이 화면에서 항목을 펼쳐
                        // 파라미터/서술을 고치면 다시 검토대기로 되돌릴 수 있다(2026-09-23
                        // 추가 — 예전엔 되돌릴 방법이 없었지만 지금은 있음, 문구도 갱신).
                        if (window.confirm(`"${item.policy_name}" 항목을 반려할까요?\n검색/채팅에서 제외됩니다. 반려 후 이 화면에서 항목을 펼쳐 파라미터/서술을 고치면 다시 검토대기로 되돌릴 수 있습니다.`)) {
                          rejectMutation.mutate(item.item_id);
                        }
                      }}
                      title="반려 — 검색/채팅에서 제외됩니다(펼쳐서 수정하면 재검토 가능)"
                      className="p-1 rounded text-rose-600 hover:bg-rose-50 dark:text-rose-400 dark:hover:bg-rose-950/40 disabled:opacity-50"
                    >
                      <X className="w-3.5 h-3.5" />
                    </button>
                  </div>
                )}
                {expandedId === item.item_id ? (
                  <ChevronUp className="w-4 h-4 text-slate-400 flex-shrink-0" />
                ) : (
                  <ChevronDown className="w-4 h-4 text-slate-400 flex-shrink-0" />
                )}
              </div>

              {expandedId === item.item_id && (
                <div className="border-t border-slate-700 px-4 py-4 space-y-4">
                  {item.status === 'pending_review' && item.risk_reasons.length > 0 && (
                    <div>
                      <p className="text-xs font-medium text-slate-400 mb-1">
                        왜 {item.risk_level ? RISK_LABEL[item.risk_level] : ''}인가
                      </p>
                      <ul className="text-xs text-slate-400 list-disc pl-4 space-y-0.5">
                        {item.risk_reasons.map((r) => <li key={r}>{r}</li>)}
                      </ul>
                    </div>
                  )}
                  {item.unresolved_segments.length > 0 && (
                    <div className="space-y-2">
                      <p className="text-xs font-medium text-amber-700 dark:text-amber-400"
                        title="AI가 서술·파라미터 어디에도 넣지 못한 원문 조각입니다. 편입하면 구조화 상태와 위험도가 바로 다시 계산됩니다.">
                        미분류 조각 {item.unresolved_segments.length}개 — 편입하면 위험도가 다시 계산돼요
                      </p>
                      <UnresolvedSegmentActions
                        namespace={selectedNs}
                        itemId={item.item_id}
                        segments={item.unresolved_segments}
                        canModify={canModifyNs}
                        onChanged={() => Promise.all([
                          queryClient.invalidateQueries({ queryKey: ['policy-items', selectedNs] }),
                          queryClient.invalidateQueries({ queryKey: ['policy-items-all', selectedNs] }),
                          queryClient.invalidateQueries({ queryKey: ['policy-review-summary', selectedNs] }),
                          queryClient.invalidateQueries({ queryKey: ['policy-unresolved-summary', selectedNs] }),
                        ])}
                      />
                    </div>
                  )}
                  <div>
                    <p className="text-xs font-medium text-slate-400 mb-2">원문</p>
                    <div className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300 leading-relaxed whitespace-pre-wrap">
                      {item.raw_body}
                    </div>
                  </div>
                  {item.status === 'rejected' && canModifyNs && (
                    <p className="text-xs text-amber-700 dark:text-amber-400/90 bg-amber-50 dark:bg-amber-950/20 border border-amber-200 dark:border-amber-800/40 rounded-lg px-3 py-2">
                      반려된 항목입니다 — 아래 파라미터/서술을 고쳐 저장하면 다시 검토대기 상태로
                      전환되어 재승인을 받을 수 있습니다.
                    </p>
                  )}
                  {item.params.length > 0 && (
                    <div>
                      <p className="flex items-center gap-1.5 text-xs font-medium text-cyan-700 dark:text-cyan-400 mb-2">
                        <Database className="w-3.5 h-3.5" />파라미터 (RDB 정확조회) — policy_param {item.params.length}건
                      </p>
                      <div className="space-y-1.5">
                        {item.params.map((p) => {
                          const canEdit = item.status === 'rejected' && canModifyNs;
                          if (editingParamId === p.id) {
                            return (
                              <div key={p.id} className="bg-slate-900 border border-cyan-500/50 rounded-lg px-3 py-2 space-y-1.5">
                                <div className="grid grid-cols-2 gap-1.5">
                                  <div className="col-span-2">
                                    <label className="block text-[10px] font-medium text-cyan-700/80 dark:text-cyan-400/80 mb-0.5">항목명 *</label>
                                    <input type="text" value={paramEditForm.name}
                                      onChange={(e) => setParamEditForm((f) => ({ ...f, name: e.target.value }))}
                                      className="w-full bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-600 rounded px-2 py-1 text-xs text-slate-800 dark:text-slate-200 focus:outline-none focus:border-cyan-500" />
                                  </div>
                                  <div>
                                    <label className="block text-[10px] font-medium text-cyan-700/80 dark:text-cyan-400/80 mb-0.5">조건</label>
                                    <input type="text" value={paramEditForm.condition}
                                      onChange={(e) => setParamEditForm((f) => ({ ...f, condition: e.target.value }))}
                                      className="w-full bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-600 rounded px-2 py-1 text-xs text-slate-800 dark:text-slate-200 focus:outline-none focus:border-cyan-500" />
                                  </div>
                                  <div>
                                    <label className="block text-[10px] font-medium text-cyan-700/80 dark:text-cyan-400/80 mb-0.5">값</label>
                                    <input type="text" value={paramEditForm.value}
                                      onChange={(e) => setParamEditForm((f) => ({ ...f, value: e.target.value }))}
                                      className="w-full bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-600 rounded px-2 py-1 text-xs text-slate-800 dark:text-slate-200 focus:outline-none focus:border-cyan-500" />
                                  </div>
                                  <div className="col-span-2">
                                    <label className="block text-[10px] font-medium text-cyan-700/80 dark:text-cyan-400/80 mb-0.5">단위</label>
                                    <input type="text" value={paramEditForm.unit}
                                      onChange={(e) => setParamEditForm((f) => ({ ...f, unit: e.target.value }))}
                                      className="w-full bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-600 rounded px-2 py-1 text-xs text-slate-800 dark:text-slate-200 focus:outline-none focus:border-cyan-500" />
                                  </div>
                                </div>
                                <div className="flex justify-end gap-2">
                                  <button type="button" onClick={() => setEditingParamId(null)}
                                    className="flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] text-slate-500 hover:text-slate-700 dark:hover:text-slate-300">
                                    <X className="w-3 h-3" />취소
                                  </button>
                                  <button type="button"
                                    disabled={!paramEditForm.name.trim() || updateParamMutation.isPending}
                                    onClick={() => updateParamMutation.mutate(p.id)}
                                    className="flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium border border-cyan-300 text-cyan-700 bg-cyan-50 hover:bg-cyan-100 dark:border-cyan-600/40 dark:text-cyan-300 dark:bg-cyan-500/10 disabled:opacity-50">
                                    <Check className="w-3 h-3" />{updateParamMutation.isPending ? '저장 중...' : '저장'}
                                  </button>
                                </div>
                              </div>
                            );
                          }
                          return (
                            <div key={p.id} className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300 flex items-center justify-between gap-2">
                              <span>
                                <span className="font-medium text-slate-200">{p.name}</span>
                                {p.condition && <span className="text-slate-500"> ({p.condition})</span>}
                                <span className="text-cyan-600 dark:text-cyan-300"> = {p.value}{p.unit ? ` ${p.unit}` : ''}</span>
                              </span>
                              {canEdit && (
                                <button type="button"
                                  onClick={() => { setEditingParamId(p.id); setParamEditForm({ name: p.name, condition: p.condition ?? '', value: p.value ?? '', unit: p.unit ?? '' }); }}
                                  className="p-1 rounded text-slate-500 hover:text-cyan-600 hover:bg-cyan-50 dark:hover:text-cyan-400 dark:hover:bg-cyan-950/40 flex-shrink-0">
                                  <Pencil className="w-3.5 h-3.5" />
                                </button>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}
                  {item.narratives.length > 0 && (
                    <div>
                      <p className="flex items-center gap-1.5 text-xs font-medium text-violet-700 dark:text-violet-400 mb-2">
                        <Sparkles className="w-3.5 h-3.5" />서술 (벡터 검색) — policy_chunk {item.narratives.length}건
                      </p>
                      <div className="space-y-1.5">
                        {item.narratives.map((c) => {
                          const canEdit = item.status === 'rejected' && canModifyNs;
                          if (editingChunkId === c.id) {
                            return (
                              <div key={c.id} className="bg-slate-900 border border-violet-500/50 rounded-lg px-3 py-2 space-y-1.5">
                                <textarea rows={3} value={chunkEditForm}
                                  onChange={(e) => setChunkEditForm(e.target.value)}
                                  className="w-full bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-600 rounded px-2 py-1 text-xs text-slate-800 dark:text-slate-200 focus:outline-none focus:border-violet-500 resize-y" />
                                <div className="flex justify-end gap-2">
                                  <button type="button" onClick={() => setEditingChunkId(null)}
                                    className="flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] text-slate-500 hover:text-slate-700 dark:hover:text-slate-300">
                                    <X className="w-3 h-3" />취소
                                  </button>
                                  <button type="button"
                                    disabled={!chunkEditForm.trim() || updateNarrativeMutation.isPending}
                                    onClick={() => updateNarrativeMutation.mutate(c.id)}
                                    className="flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium border border-violet-300 text-violet-700 bg-violet-50 hover:bg-violet-100 dark:border-violet-600/40 dark:text-violet-300 dark:bg-violet-500/10 disabled:opacity-50">
                                    <Check className="w-3 h-3" />{updateNarrativeMutation.isPending ? '저장 중...' : '저장'}
                                  </button>
                                </div>
                              </div>
                            );
                          }
                          return (
                            <div key={c.id} className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300 leading-relaxed flex items-start justify-between gap-2">
                              <span>{c.chunk_text}</span>
                              {canEdit && (
                                <button type="button"
                                  onClick={() => { setEditingChunkId(c.id); setChunkEditForm(c.chunk_text); }}
                                  className="p-1 rounded text-slate-500 hover:text-violet-600 hover:bg-violet-50 dark:hover:text-violet-400 dark:hover:bg-violet-950/40 flex-shrink-0">
                                  <Pencil className="w-3.5 h-3.5" />
                                </button>
                              )}
                            </div>
                          );
                        })}
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
