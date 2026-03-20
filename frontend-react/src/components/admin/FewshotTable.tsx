import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Trash2, X, Zap } from 'lucide-react';
import { getFewshots, createFewshot, updateFewshot, deleteFewshot, updateFewshotStatus } from '../../api/fewshots';
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

  // Client-side status filter
  const filteredItems = statusFilter === 'all'
    ? items
    : items.filter((item) => item.status === statusFilter);

  const { totalPages, totalItems, slice } = useClientPaging(filteredItems, pageSize);
  const pagedItems = slice(page);

  useEffect(() => { setPage(1); }, [statusFilter, pageSize]);

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
            Few-shot 베스트케이스
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
          <PaginationInfo totalItems={totalItems} pageSize={pageSize} onPageSizeChange={setPageSize} />
          {pagedItems.map((item) => (
            <div
              key={item.id}
              className="bg-slate-800 border border-slate-700 rounded-xl px-4 py-3 flex items-center gap-3 cursor-pointer hover:bg-slate-700/50 transition-colors"
              onClick={() => startEdit(item)}
            >
              <Zap className="w-4 h-4 text-amber-400 flex-shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-slate-200 truncate">{item.question}</p>
                <p className="text-xs text-slate-500 mt-0.5 truncate">{item.answer.slice(0, 100)}{item.answer.length > 100 ? '...' : ''}</p>
              </div>
              <div className="flex items-center gap-2 flex-shrink-0 text-xs text-slate-500">
                <StatusBadge status={item.status} />
                {item.created_by_username && <span>{item.created_by_username}</span>}
                {item.created_by_part && (
                  <Badge color={canModifyNs ? 'emerald' : 'slate'}>{item.created_by_part}</Badge>
                )}
                <span>{new Date(item.created_at).toLocaleDateString('ko-KR')}</span>
              </div>
            </div>
          ))}
          {filteredItems.length === 0 && (
            <div className="text-center py-10 text-slate-500">
              <Zap className="w-8 h-8 mx-auto mb-2 text-slate-500" />
              <p>Few-shot 항목이 없습니다.</p>
              <p className="text-xs mt-1">챗에서 긍정 피드백을 하면 후보로 자동 등록됩니다.</p>
            </div>
          )}
          <PaginationNav page={page} totalPages={totalPages} onPageChange={setPage} />
        </div>
      )}

      {/* Create Modal */}
      <Modal isOpen={showCreate} onClose={() => setShowCreate(false)} title="Few-shot 추가" maxWidth="max-w-xl">
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
        title={canModifyNs ? 'Few-shot 수정' : 'Few-shot 상세'}
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
      <Modal isOpen={deleteTarget !== null} onClose={() => setDeleteTarget(null)} title="Few-shot 삭제">
        <div className="space-y-4">
          <p className="text-sm text-slate-300">이 Few-shot 항목을 삭제하시겠습니까? 이후 유사 질문에 더 이상 참조되지 않습니다.</p>
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
