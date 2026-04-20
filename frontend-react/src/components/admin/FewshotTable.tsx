import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Trash2, X, Zap, Search } from 'lucide-react';
import { getFewshots, createFewshot, updateFewshot, deleteFewshot, updateFewshotStatus, bulkDeleteFewshot, vectorSearchFewshot } from '../../api/fewshots';
import { useNamespaceAccess } from '../../utils/useNamespaceAccess';
import { Button } from '../ui/Button';
import { Modal } from '../ui/Modal';
import { Badge } from '../ui/Badge';
import { PaginationInfo, PaginationNav, useClientPaging } from '../ui/Pagination';
import type { FewshotItem } from '../../types';

interface FewshotFormData {
  question: string;
  answer: string;
  knowledge_id: string;
}

const defaultForm: FewshotFormData = { question: '', answer: '', knowledge_id: '' };

type StatusFilter = 'all' | 'active' | 'candidate';

function StatusBadge({ status }: { status: string }) {
  if (status === 'active') {
    return <Badge color="emerald">활성</Badge>;
  }
  if (status === 'candidate') {
    return <Badge color="amber">후보</Badge>;
  }
  return null;
}

export function FewshotTable() {
  const qc = useQueryClient();
  const { selectedNs, setSelectedNs, canModifyNs, sortedNamespaces, user } = useNamespaceAccess();
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [searchMode, setSearchMode] = useState<'text' | 'vector'>('text');
  const [vectorSearchInput, setVectorSearchInput] = useState('');
  const [vectorQuery, setVectorQuery] = useState('');
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [showBulkConfirm, setShowBulkConfirm] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(30);

  const [editingId, setEditingId] = useState<number | null>(null);
  const [editingItem, setEditingItem] = useState<FewshotItem | null>(null);
  const [editForm, setEditForm] = useState<FewshotFormData>(defaultForm);
  const [showCreate, setShowCreate] = useState(false);
  const [showEdit, setShowEdit] = useState(false);
  const [createForm, setCreateForm] = useState<FewshotFormData>(defaultForm);
  const [deleteTarget, setDeleteTarget] = useState<number | null>(null);

  const { data: items = [], isLoading, error } = useQuery({
    queryKey: ['fewshots', selectedNs],
    queryFn: () => getFewshots(selectedNs),
    enabled: !!selectedNs,
    staleTime: 15_000,
    refetchOnMount: 'always',
  });

  const vectorSearchQuery = useQuery({
    queryKey: ['fewshot-vector-search', selectedNs, vectorQuery],
    queryFn: () => vectorSearchFewshot(selectedNs, vectorQuery),
    enabled: searchMode === 'vector' && !!vectorQuery && !!selectedNs,
    staleTime: 30_000,
  });

  // Client-side status filter + text search + sort: 활성 우선 → 최신순
  const STATUS_ORDER: Record<string, number> = { active: 0, candidate: 1 };
  const textFilteredItems = (statusFilter === 'all' ? items : items.filter((i) => i.status === statusFilter))
    .filter((i) => {
      if (!searchQuery.trim()) return true;
      const q = searchQuery.toLowerCase();
      return i.question.toLowerCase().includes(q) || i.answer.toLowerCase().includes(q);
    })
    .slice()
    .sort((a, b) => {
      const so = (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9);
      if (so !== 0) return so;
      return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
    });

  const displayItems = searchMode === 'vector' && vectorQuery
    ? (vectorSearchQuery.data ?? [])
    : textFilteredItems;
  const filteredItems = textFilteredItems; // for count badges

  const { totalPages, totalItems, slice } = useClientPaging(displayItems, pageSize);
  const pagedItems = slice(page);

  useEffect(() => { setPage(1); setSelectedIds(new Set()); }, [statusFilter, pageSize, searchQuery, vectorQuery]);

  const allPageSelected = pagedItems.length > 0 && pagedItems.every((i) => selectedIds.has(i.id));
  const toggleSelectAll = () => {
    if (allPageSelected) {
      setSelectedIds((prev) => { const next = new Set(prev); pagedItems.forEach((i) => next.delete(i.id)); return next; });
    } else {
      setSelectedIds((prev) => { const next = new Set(prev); pagedItems.forEach((i) => next.add(i.id)); return next; });
    }
  };
  const toggleSelect = (id: number) => {
    setSelectedIds((prev) => { const next = new Set(prev); next.has(id) ? next.delete(id) : next.add(id); return next; });
  };

  const createMutation = useMutation({
    mutationFn: () =>
      createFewshot({
        namespace: selectedNs,
        question: createForm.question,
        answer: createForm.answer,
        knowledge_id: createForm.knowledge_id ? parseInt(createForm.knowledge_id) : null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['fewshots', selectedNs] });
      setShowCreate(false);
      setCreateForm(defaultForm);
    },
  });

  const updateMutation = useMutation({
    mutationFn: (id: number) =>
      updateFewshot(id, {
        question: editForm.question,
        answer: editForm.answer,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['fewshots', selectedNs] });
      setEditingId(null);
      setShowEdit(false);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteFewshot(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['fewshots', selectedNs] });
      qc.invalidateQueries({ queryKey: ['stats-ns', selectedNs] });
      setDeleteTarget(null);
      setShowEdit(false);
    },
  });

  const bulkDeleteMutation = useMutation({
    mutationFn: (ids: number[]) => bulkDeleteFewshot(ids),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['fewshots', selectedNs] });
      qc.invalidateQueries({ queryKey: ['stats-ns', selectedNs] });
      setSelectedIds(new Set());
      setShowBulkConfirm(false);
    },
  });

  const statusMutation = useMutation({
    mutationFn: ({ id, status }: { id: number; status: string }) => updateFewshotStatus(id, status),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['fewshots', selectedNs] });
      setShowEdit(false);
      setEditingId(null);
      setEditingItem(null);
    },
  });

  const startEdit = (item: FewshotItem) => {
    setEditingId(item.id);
    setEditingItem(item);
    setEditForm({
      question: item.question,
      answer: item.answer,
      knowledge_id: item.knowledge_id ? String(item.knowledge_id) : '',
    });
    setShowEdit(true);
  };

  const statusTabs: { key: StatusFilter; label: string }[] = [
    { key: 'all', label: '전체' },
    { key: 'active', label: '활성' },
    { key: 'candidate', label: '후보' },
  ];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-slate-200">
            Q&A 베스트케이스
            {selectedNs && <span className="text-sm font-normal text-slate-500 ml-2">({selectedNs})</span>}
          </h2>
          <p className="text-xs text-slate-500 mt-0.5">
            👍 긍정 피드백 시 후보 등록 — 활성화 후 LLM 컨텍스트에 포함됩니다
          </p>
        </div>
        <Button variant="primary" size="sm" onClick={() => setShowCreate(true)} disabled={!selectedNs || !canModifyNs}>
          <Plus className="w-4 h-4" />
          추가
        </Button>
      </div>

      {/* 파트 selector */}
      <div>
        <label className="block text-xs font-medium text-slate-400 mb-1.5">파트</label>
        <select
          value={selectedNs}
          onChange={(e) => setSelectedNs(e.target.value)}
          className="w-64 bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
        >
          <option value="">선택...</option>
          {sortedNamespaces.map((ns) => <option key={ns} value={ns}>{ns}</option>)}
        </select>
      </div>

      {/* Status filter tabs */}
      {selectedNs && (
        <div className="flex gap-1">
          {statusTabs.map(({ key, label }) => (
            <button
              key={key}
              onClick={() => setStatusFilter(key)}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                statusFilter === key
                  ? 'bg-indigo-600 text-white'
                  : 'bg-slate-800 text-slate-400 hover:bg-slate-700 hover:text-slate-200'
              }`}
            >
              {label}
              {key !== 'all' && (
                <span className="ml-1.5 opacity-70">
                  ({items.filter((i) => i.status === key).length})
                </span>
              )}
              {key === 'all' && (
                <span className="ml-1.5 opacity-70">({items.length})</span>
              )}
            </button>
          ))}
        </div>
      )}

      {!selectedNs && (
        <div className="text-center py-10 text-slate-500">파트를 선택하세요.</div>
      )}
      {selectedNs && isLoading && (
        <div className="text-center py-10 text-slate-500 animate-pulse">로딩 중...</div>
      )}
      {selectedNs && error && (
        <div className="text-center py-10 text-rose-400">오류가 발생했습니다.</div>
      )}

      {selectedNs && !isLoading && (
        <div className="space-y-2">
          {/* Search bar */}
          <div className="flex items-center gap-2">
            <div className="flex rounded-lg border border-slate-600 overflow-hidden text-xs font-medium flex-shrink-0">
              <button
                onClick={() => { setSearchMode('text'); setVectorQuery(''); }}
                className={`px-3 py-2 transition-colors ${searchMode === 'text' ? 'bg-indigo-600 text-white' : 'bg-slate-800 text-slate-400 hover:text-slate-200'}`}
              >문자열</button>
              <button
                onClick={() => { setSearchMode('vector'); setSearchQuery(''); }}
                className={`px-3 py-2 transition-colors ${searchMode === 'vector' ? 'bg-indigo-600 text-white' : 'bg-slate-800 text-slate-400 hover:text-slate-200'}`}
              >벡터</button>
            </div>

            {searchMode === 'text' ? (
              <div className="relative flex-1">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
                <input
                  type="text"
                  placeholder="질문 또는 답변으로 검색..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="w-full pl-9 pr-3 py-2 bg-slate-900 border border-slate-600 rounded-lg text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
                />
              </div>
            ) : (
              <div className="flex flex-1 gap-2">
                <div className="relative flex-1">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
                  <input
                    type="text"
                    placeholder="유사 Q&A 검색 (Enter로 실행)..."
                    value={vectorSearchInput}
                    onChange={(e) => setVectorSearchInput(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && setVectorQuery(vectorSearchInput)}
                    className="w-full pl-9 pr-3 py-2 bg-slate-900 border border-slate-600 rounded-lg text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
                  />
                </div>
                <button
                  onClick={() => setVectorQuery(vectorSearchInput)}
                  disabled={!vectorSearchInput.trim()}
                  className="px-3 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white text-sm rounded-lg transition-colors flex-shrink-0"
                >
                  {vectorSearchQuery.isFetching ? <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" /> : '검색'}
                </button>
              </div>
            )}

            {canModifyNs && (
              <label className="flex items-center gap-2 text-sm text-slate-400 cursor-pointer select-none flex-shrink-0">
                <input
                  type="checkbox"
                  checked={allPageSelected}
                  onChange={toggleSelectAll}
                  className="w-4 h-4 rounded accent-indigo-500"
                />
                전체 선택
              </label>
            )}
          </div>

          {/* Bulk action bar */}
          {selectedIds.size > 0 && canModifyNs && (
            <div className="flex items-center gap-3 px-4 py-2.5 bg-indigo-900/30 border border-indigo-700/40 rounded-xl">
              <span className="text-sm text-indigo-300 flex-1">{selectedIds.size}개 선택됨</span>
              <Button variant="ghost" size="sm" onClick={() => setSelectedIds(new Set())}>선택 해제</Button>
              <Button variant="danger" size="sm" onClick={() => setShowBulkConfirm(true)}>
                <Trash2 className="w-3.5 h-3.5" />삭제
              </Button>
            </div>
          )}

          <PaginationInfo totalItems={totalItems} pageSize={pageSize} onPageSizeChange={setPageSize} />
          {pagedItems.map((item) => (
            <div
              key={item.id}
              className="bg-slate-800 border border-slate-700 rounded-xl px-4 py-3 flex items-center gap-3 hover:bg-slate-700/50 transition-colors"
            >
              {canModifyNs && (
                <input
                  type="checkbox"
                  checked={selectedIds.has(item.id)}
                  onChange={() => toggleSelect(item.id)}
                  className="w-4 h-4 rounded accent-indigo-500 flex-shrink-0"
                />
              )}
              <div
                className="flex flex-1 items-center gap-3 cursor-pointer min-w-0"
                onClick={() => startEdit(item)}
              >
                <Zap className="w-4 h-4 text-amber-400 flex-shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-slate-200 truncate">{item.question}</p>
                  <p className="text-xs text-slate-500 mt-0.5 truncate">{item.answer.slice(0, 100)}{item.answer.length > 100 ? '...' : ''}</p>
                </div>
                <div className="flex items-center gap-2 flex-shrink-0 text-xs text-slate-500">
                  <StatusBadge status={item.status} />
                  {searchMode === 'vector' && (item as FewshotItem & { similarity?: number }).similarity != null && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-violet-900/40 text-violet-300 border border-violet-700/40 font-mono">
                      {((item as FewshotItem & { similarity?: number }).similarity! * 100).toFixed(1)}%
                    </span>
                  )}
                  {item.created_by_username && <span>{item.created_by_username}</span>}
                  {item.created_by_part && (
                    <Badge color={canModifyNs ? 'emerald' : 'slate'}>{item.created_by_part}</Badge>
                  )}
                  <span>{new Date(item.created_at).toLocaleDateString('ko-KR')}</span>
                </div>
              </div>
            </div>
          ))}
          {displayItems.length === 0 && !vectorSearchQuery.isFetching && (
            <div className="text-center py-10 text-slate-500">
              <Zap className="w-8 h-8 mx-auto mb-2 text-slate-500" />
              {searchQuery || vectorQuery ? (
                <p>검색 결과가 없습니다.</p>
              ) : (
                <>
                  <p>Q&A 항목이 없습니다.</p>
                  <p className="text-xs mt-1">챗에서 긍정 피드백을 하면 후보로 자동 등록됩니다.</p>
                </>
              )}
            </div>
          )}
          {vectorSearchQuery.isFetching && (
            <div className="flex items-center justify-center gap-2 py-10 text-slate-400 text-sm">
              <div className="w-4 h-4 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
              벡터 유사도 검색 중...
            </div>
          )}
          <PaginationNav page={page} totalPages={totalPages} onPageChange={setPage} />
        </div>
      )}

      <Modal isOpen={showBulkConfirm} onClose={() => setShowBulkConfirm(false)} title="Q&A 일괄 삭제">
        <div className="space-y-4">
          <p className="text-sm text-slate-300">선택한 <span className="text-rose-400 font-semibold">{selectedIds.size}개</span> Q&A를 삭제하시겠습니까?</p>
          {bulkDeleteMutation.error && (
            <p className="text-xs text-rose-400">{String(bulkDeleteMutation.error)}</p>
          )}
          <div className="flex gap-2 justify-end">
            <Button variant="secondary" size="sm" onClick={() => setShowBulkConfirm(false)}>취소</Button>
            <Button variant="danger" size="sm" loading={bulkDeleteMutation.isPending} onClick={() => bulkDeleteMutation.mutate(Array.from(selectedIds))}>삭제</Button>
          </div>
        </div>
      </Modal>

      {/* Create Modal */}
      <Modal isOpen={showCreate} onClose={() => setShowCreate(false)} title="Q&A 추가" maxWidth="max-w-xl">
        <div className="space-y-3">
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">
              질문 <span className="text-rose-400">*</span>
            </label>
            <textarea
              rows={2}
              value={createForm.question}
              onChange={(e) => setCreateForm((f) => ({ ...f, question: e.target.value }))}
              placeholder="예시 질문을 입력하세요"
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500 resize-y"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">
              답변 <span className="text-rose-400">*</span>
            </label>
            <textarea
              rows={14}
              value={createForm.answer}
              onChange={(e) => setCreateForm((f) => ({ ...f, answer: e.target.value }))}
              placeholder="모범 답변을 입력하세요"
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500 resize-y min-h-[288px]"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">연결 지식 ID (선택)</label>
            <input
              type="number"
              value={createForm.knowledge_id}
              onChange={(e) => setCreateForm((f) => ({ ...f, knowledge_id: e.target.value }))}
              placeholder="지식 베이스 ID (없으면 비워두세요)"
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
            />
          </div>
          <div className="flex gap-2 justify-end pt-2">
            <Button variant="secondary" size="sm" onClick={() => { setShowCreate(false); setCreateForm(defaultForm); }}>취소</Button>
            <Button
              variant="primary" size="sm"
              loading={createMutation.isPending}
              onClick={() => createMutation.mutate()}
              disabled={!createForm.question.trim() || !createForm.answer.trim()}
            >
              추가
            </Button>
          </div>
          {createMutation.isError && (
            <p className="text-xs text-rose-400">{String(createMutation.error)}</p>
          )}
        </div>
      </Modal>

      {/* Edit / View Modal */}
      <Modal
        isOpen={showEdit}
        onClose={() => { setShowEdit(false); setEditingId(null); setEditingItem(null); }}
        title={canModifyNs ? 'Q&A 수정' : 'Q&A 상세'}
        maxWidth="max-w-xl"
      >
        <div className="space-y-3">
          {canModifyNs && (
            <div className="flex items-center justify-between pb-3 border-b border-slate-700">
              <div className="flex items-center gap-2">
                {editingItem && <StatusBadge status={editingItem.status} />}
                {/* Status transition buttons */}
                {editingItem?.status === 'candidate' && (
                  <Button
                    variant="primary" size="sm"
                    loading={statusMutation.isPending}
                    onClick={() => editingId !== null && statusMutation.mutate({ id: editingId, status: 'active' })}
                  >
                    활성화
                  </Button>
                )}
                {editingItem?.status === 'active' && (
                  <Button
                    variant="secondary" size="sm"
                    loading={statusMutation.isPending}
                    onClick={() => editingId !== null && statusMutation.mutate({ id: editingId, status: 'candidate' })}
                  >
                    후보로 내리기
                  </Button>
                )}
              </div>
              <Button
                variant="danger" size="sm"
                onClick={() => editingId !== null && setDeleteTarget(editingId)}
              >
                <Trash2 className="w-3.5 h-3.5" />삭제
              </Button>
            </div>
          )}
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">질문</label>
            <textarea
              rows={3}
              value={editForm.question}
              onChange={(e) => setEditForm((f) => ({ ...f, question: e.target.value }))}
              readOnly={!canModifyNs}
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 resize-y min-h-[80px] read-only:border-slate-700 read-only:outline-none leading-relaxed"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">답변</label>
            <textarea
              rows={14}
              value={editForm.answer}
              onChange={(e) => setEditForm((f) => ({ ...f, answer: e.target.value }))}
              readOnly={!canModifyNs}
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 resize-y min-h-[288px] read-only:border-slate-700 read-only:outline-none leading-relaxed"
            />
          </div>
          {editForm.knowledge_id && (
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">연결된 지식 ID</label>
              <div className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-indigo-400">
                #{editForm.knowledge_id}
              </div>
            </div>
          )}
          {updateMutation.error && (
            <p className="text-xs text-rose-400">{String(updateMutation.error)}</p>
          )}
          {statusMutation.error && (
            <p className="text-xs text-rose-400">{String(statusMutation.error)}</p>
          )}
          <div className="flex gap-2 justify-end">
            <Button variant="ghost" size="sm" onClick={() => { setShowEdit(false); setEditingId(null); setEditingItem(null); }}>
              <X className="w-3.5 h-3.5" />{canModifyNs ? '취소' : '닫기'}
            </Button>
            {canModifyNs && (
              <Button
                variant="primary" size="sm"
                loading={updateMutation.isPending}
                onClick={() => editingId !== null && updateMutation.mutate(editingId)}
                disabled={!editForm.question.trim() || !editForm.answer.trim()}
              >
                저장
              </Button>
            )}
          </div>
        </div>
      </Modal>

      {/* Delete Confirmation Modal */}
      <Modal isOpen={deleteTarget !== null} onClose={() => setDeleteTarget(null)} title="Q&A 삭제">
        <div className="space-y-4">
          <p className="text-sm text-slate-300">이 Q&A 항목을 삭제하시겠습니까? 이후 유사 질문에 더 이상 참조되지 않습니다.</p>
          {deleteMutation.error && (
            <p className="text-xs text-rose-400">{String(deleteMutation.error)}</p>
          )}
          <div className="flex gap-2 justify-end">
            <Button variant="secondary" size="sm" onClick={() => setDeleteTarget(null)}>취소</Button>
            <Button
              variant="danger" size="sm"
              loading={deleteMutation.isPending}
              onClick={() => deleteTarget !== null && deleteMutation.mutate(deleteTarget)}
            >
              삭제
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
