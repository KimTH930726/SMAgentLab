import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Trash2, X } from 'lucide-react';
import {
  getKnowledge,
  createKnowledge,
  updateKnowledge,
  deleteKnowledge,
} from '../../api/knowledge';
import { getCategories } from '../../api/namespaces';
import { useNamespaceAccess } from '../../utils/useNamespaceAccess';
import { Button } from '../ui/Button';
import { Modal } from '../ui/Modal';
import { Badge } from '../ui/Badge';
import { TagInput } from '../ui/TagInput';
import { Pagination, useClientPaging } from '../ui/Pagination';
import type { KnowledgeItem } from '../../types';

// ── KnowledgeTable ────────────────────────────────────────────────────────────

interface KnowledgeFormData {
  container_names: string[];
  target_tables: string[];
  content: string;
  query_template: string;
  base_weight: number;
  category: string;
}

const defaultForm: KnowledgeFormData = {
  container_names: [],
  target_tables: [],
  content: '',
  query_template: '',
  base_weight: 1.0,
  category: '',
};

function weightLabel(w: number) {
  return w >= 2 ? '높음' : w >= 1.5 ? '보통' : '기본';
}
function weightClass(w: number) {
  return w >= 2
    ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-300'
    : w >= 1.5
    ? 'bg-indigo-100 text-indigo-700 dark:bg-indigo-500/20 dark:text-indigo-300'
    : 'bg-zinc-100 text-zinc-600 dark:bg-zinc-600/40 dark:text-zinc-300';
}

export function KnowledgeTable() {
  const qc = useQueryClient();
  const { selectedNs, setSelectedNs, canModifyNs, sortedNamespaces, user } = useNamespaceAccess();

  const [editingId, setEditingId] = useState<number | null>(null);
  const [editForm, setEditForm] = useState<KnowledgeFormData>(defaultForm);
  const [showCreate, setShowCreate] = useState(false);
  const [showEdit, setShowEdit] = useState(false);
  const [createForm, setCreateForm] = useState<KnowledgeFormData>(defaultForm);
  const [deleteTarget, setDeleteTarget] = useState<number | null>(null);
  const [categoryFilter, setCategoryFilter] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(30);

  const { data: categories = [] } = useQuery({
    queryKey: ['categories', selectedNs],
    queryFn: () => getCategories(selectedNs),
    enabled: !!selectedNs,
    staleTime: 30_000,
  });

  const categoryNames = categories.map((c) => c.name);

  const { data: items = [], isLoading, error } = useQuery({
    queryKey: ['knowledge', selectedNs],
    queryFn: () => getKnowledge(selectedNs),
    enabled: !!selectedNs,
    staleTime: 15_000,
    refetchOnMount: 'always',
  });

  const createMutation = useMutation({
    mutationFn: () =>
      createKnowledge({
        namespace: selectedNs,
        container_name: createForm.container_names.join(', '),
        target_tables: createForm.target_tables,
        content: createForm.content,
        query_template: createForm.query_template || null,
        base_weight: createForm.base_weight,
        category: createForm.category || null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['knowledge', selectedNs] });
      qc.invalidateQueries({ queryKey: ['stats-ns', selectedNs] });
      setShowCreate(false);
      setCreateForm(defaultForm);
    },
  });

  const updateMutation = useMutation({
    mutationFn: (id: number) =>
      updateKnowledge(id, {
        container_name: editForm.container_names.join(', '),
        target_tables: editForm.target_tables,
        content: editForm.content,
        query_template: editForm.query_template || null,
        base_weight: editForm.base_weight,
        category: editForm.category || '',
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['knowledge', selectedNs] });
      setEditingId(null);
      setShowEdit(false);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteKnowledge(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['knowledge', selectedNs] });
      qc.invalidateQueries({ queryKey: ['stats-ns', selectedNs] });
      setDeleteTarget(null);
      setShowEdit(false);
    },
  });

  const startEdit = (item: KnowledgeItem) => {
    setEditingId(item.id);
    setEditForm({
      container_names: item.container_name
        ? item.container_name.split(',').map((t) => t.trim()).filter(Boolean)
        : [],
      target_tables: item.target_tables ?? [],
      content: item.content,
      query_template: item.query_template ?? '',
      base_weight: item.base_weight,
      category: item.category ?? '',
    });
    setShowEdit(true);
  };

  const filteredItems = items.filter((item) => !categoryFilter || item.category === categoryFilter);
  const { totalPages, totalItems, slice } = useClientPaging(filteredItems, pageSize);
  const pagedItems = slice(page);

  useEffect(() => { setPage(1); }, [categoryFilter, pageSize]);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-200">
          지식 베이스
          {selectedNs && <span className="text-sm font-normal text-slate-500 ml-2">({selectedNs})</span>}
        </h2>
        <Button variant="primary" size="sm" onClick={() => setShowCreate(true)} disabled={!selectedNs || !canModifyNs}>
          <Plus className="w-4 h-4" />
          지식 추가
        </Button>
      </div>

      {/* 파트 selector + Category filter */}
      <div className="flex items-end gap-3 flex-wrap">
        <div>
          <label className="block text-xs font-medium text-slate-400 mb-1.5">파트</label>
          <select
            value={selectedNs}
            onChange={(e) => { setSelectedNs(e.target.value); setCategoryFilter(''); }}
            className="w-48 bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
          >
            <option value="">선택...</option>
            {sortedNamespaces.map((ns) => <option key={ns} value={ns}>{ns}</option>)}
          </select>
        </div>
        {categoryNames.length > 0 && (
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1.5">업무구분</label>
            <select
              value={categoryFilter}
              onChange={(e) => setCategoryFilter(e.target.value)}
              className="w-40 bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
            >
              <option value="">전체</option>
              {categoryNames.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
        )}
      </div>

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
          {pagedItems.map((item) => (
            <div
              key={item.id}
              className="bg-slate-800 border border-slate-700 rounded-xl px-4 py-3 flex items-center gap-3 cursor-pointer hover:bg-slate-700/50 transition-colors"
              onClick={() => startEdit(item)}
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-3 flex-wrap">
                  {item.category && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-violet-100 text-violet-700 border border-violet-300 dark:bg-violet-900/40 dark:text-violet-300 dark:border-violet-700/40 font-medium">{item.category}</span>
                  )}
                  {item.container_name && (
                    <span className="flex items-center gap-1 flex-wrap">
                      <span className="text-[10px] text-slate-500 font-medium flex-shrink-0">컨테이너</span>
                      {item.container_name.split(',').map((c) => c.trim()).filter(Boolean).map((c) => (
                        <Badge key={c} color="cyan">{c}</Badge>
                      ))}
                    </span>
                  )}
                  {(item.target_tables ?? []).length > 0 && (
                    <span className="flex items-center gap-1 flex-wrap">
                      <span className="text-[10px] text-slate-500 font-medium flex-shrink-0">테이블</span>
                      {(item.target_tables ?? []).slice(0, 3).map((t) => (
                        <Badge key={t} color="amber">{t}</Badge>
                      ))}
                      {(item.target_tables ?? []).length > 3 && (
                        <Badge color="slate">+{(item.target_tables ?? []).length - 3}</Badge>
                      )}
                    </span>
                  )}
                  <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${weightClass(item.base_weight)}`}>
                    우선순위: {weightLabel(item.base_weight)} ({item.base_weight})
                  </span>
                </div>
                <p className="text-xs text-slate-500 mt-0.5 truncate">{item.content.slice(0, 100)}...</p>
              </div>
              <div className="flex items-center gap-2 flex-shrink-0 text-[10px] text-slate-500">
                <span>
                  {item.updated_at !== item.created_at
                    ? new Date(item.updated_at).toISOString().slice(0, 10)
                    : new Date(item.created_at).toISOString().slice(0, 10)}
                </span>
                {item.created_by_username && <span>{item.created_by_username}</span>}
                {item.created_by_part && (
                  <Badge color={canModifyNs ? 'emerald' : 'slate'}>{item.created_by_part}</Badge>
                )}
              </div>
            </div>
          ))}
          {filteredItems.length === 0 && (
            <div className="text-center py-10 text-slate-500">지식 항목이 없습니다.</div>
          )}
          <Pagination
            page={page} totalPages={totalPages} onPageChange={setPage}
            pageSize={pageSize} onPageSizeChange={setPageSize}
            totalItems={totalItems}
          />
        </div>
      )}

      {/* Create Modal */}
      <Modal isOpen={showCreate} onClose={() => setShowCreate(false)} title="지식 추가" maxWidth="max-w-xl">
        <div className="space-y-3">
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">
              컨테이너명 <span className="text-rose-400">*</span>
              <span className="text-slate-600 font-normal ml-1">(Enter 또는 쉼표로 추가)</span>
            </label>
            <TagInput
              tags={createForm.container_names}
              onChange={(tags) => setCreateForm((f) => ({ ...f, container_names: tags }))}
              placeholder="컨테이너명 입력..."
              color="cyan"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">
              대상 테이블
              <span className="text-slate-600 font-normal ml-1">(Enter 또는 쉼표로 추가)</span>
            </label>
            <TagInput
              tags={createForm.target_tables}
              onChange={(tags) => setCreateForm((f) => ({ ...f, target_tables: tags }))}
              placeholder="테이블명 입력..."
              color="indigo"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">
              내용 <span className="text-rose-400">*</span>
            </label>
            <textarea
              rows={10}
              value={createForm.content}
              onChange={(e) => setCreateForm((f) => ({ ...f, content: e.target.value }))}
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 resize-y min-h-[260px]"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">쿼리 템플릿 (선택)</label>
            <textarea
              rows={4}
              value={createForm.query_template}
              onChange={(e) => setCreateForm((f) => ({ ...f, query_template: e.target.value }))}
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm font-mono text-slate-200 focus:outline-none focus:border-indigo-500 resize-y min-h-[100px]"
              placeholder="SELECT ..."
            />
          </div>
          {categoryNames.length > 0 && (
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">업무구분</label>
              <select
                value={createForm.category}
                onChange={(e) => setCreateForm((f) => ({ ...f, category: e.target.value }))}
                className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
              >
                <option value="">없음 (파트 공통)</option>
                {categoryNames.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
              <p className="text-[10px] text-slate-600 mt-0.5">미설정 시 모든 업무구분 검색에 공통으로 포함됩니다</p>
            </div>
          )}
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">
              문서 우선순위: <span className={`font-medium ${
                createForm.base_weight >= 2 ? 'text-emerald-400' :
                createForm.base_weight >= 1.5 ? 'text-indigo-400' :
                'text-slate-300'
              }`}>{createForm.base_weight.toFixed(1)} — {weightLabel(createForm.base_weight)}</span>
            </label>
            <input
              type="range" min={0} max={3} step={0.1} value={createForm.base_weight}
              onChange={(e) => setCreateForm((f) => ({ ...f, base_weight: parseFloat(e.target.value) }))}
              className="w-full accent-indigo-500"
            />
            <p className="text-[11px] text-slate-400 mt-1">
              1.0=기본 · 1.5+=보통(검색 시 우선 노출) · 2.0+=높음(핵심 문서, 항상 상위 노출) · 피드백 시 자동 상승
            </p>
          </div>
          <div className="flex gap-2 justify-end pt-2">
            <Button variant="secondary" size="sm" onClick={() => setShowCreate(false)}>취소</Button>
            <Button
              variant="primary" size="sm"
              loading={createMutation.isPending}
              onClick={() => createMutation.mutate()}
              disabled={createForm.container_names.length === 0 || !createForm.content.trim()}
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
        onClose={() => { setShowEdit(false); setEditingId(null); }}
        title={canModifyNs ? '지식 수정' : '지식 상세'}
        maxWidth="max-w-2xl"
      >
        <div className="space-y-3">
          {canModifyNs && (
            <div className="flex justify-end pb-3 border-b border-slate-700">
              <Button
                variant="danger" size="sm"
                onClick={() => editingId !== null && setDeleteTarget(editingId)}
              >
                <Trash2 className="w-3.5 h-3.5" />삭제
              </Button>
            </div>
          )}
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">
              컨테이너명
              {canModifyNs && <span className="text-slate-600 font-normal ml-1">(Enter 또는 쉼표로 추가)</span>}
            </label>
            <TagInput
              tags={editForm.container_names}
              onChange={(tags) => setEditForm((f) => ({ ...f, container_names: tags }))}
              placeholder="컨테이너명 입력..."
              readOnly={!canModifyNs}
              color="cyan"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">
              대상 테이블
              {canModifyNs && <span className="text-slate-600 font-normal ml-1">(Enter 또는 쉼표로 추가)</span>}
            </label>
            <TagInput
              tags={editForm.target_tables}
              onChange={(tags) => setEditForm((f) => ({ ...f, target_tables: tags }))}
              placeholder="테이블명 입력..."
              readOnly={!canModifyNs}
              color="indigo"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">내용</label>
            <textarea
              rows={10}
              value={editForm.content}
              onChange={(e) => setEditForm((f) => ({ ...f, content: e.target.value }))}
              readOnly={!canModifyNs}
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 resize-y min-h-[260px] read-only:border-slate-700 read-only:outline-none leading-relaxed"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">쿼리 템플릿 (선택)</label>
            <textarea
              rows={4}
              value={editForm.query_template}
              onChange={(e) => setEditForm((f) => ({ ...f, query_template: e.target.value }))}
              readOnly={!canModifyNs}
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm font-mono text-slate-200 focus:outline-none focus:border-indigo-500 resize-y min-h-[100px] read-only:border-slate-700"
              placeholder="SELECT ..."
            />
          </div>
          {categoryNames.length > 0 && (
            <div>
              <label className="text-xs font-medium text-slate-400">업무구분</label>
              {canModifyNs ? (
                <select
                  value={editForm.category}
                  onChange={(e) => setEditForm((f) => ({ ...f, category: e.target.value }))}
                  className="w-full mt-1 bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
                >
                  <option value="">없음 (파트 공통)</option>
                  {categoryNames.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              ) : (
                <div className="mt-1 bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300">
                  {editForm.category || <span className="text-slate-500">없음 (파트 공통)</span>}
                </div>
              )}
              {canModifyNs && <p className="text-[10px] text-slate-600 mt-0.5">미설정 시 모든 업무구분 검색에 공통으로 포함됩니다</p>}
            </div>
          )}
          {canModifyNs && (
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">
                문서 우선순위: <span className={`font-medium ${
                  editForm.base_weight >= 2 ? 'text-emerald-400' :
                  editForm.base_weight >= 1.5 ? 'text-indigo-400' :
                  'text-slate-300'
                }`}>{editForm.base_weight.toFixed(1)} — {weightLabel(editForm.base_weight)}</span>
              </label>
              <input
                type="range" min={0} max={3} step={0.1} value={editForm.base_weight}
                onChange={(e) => setEditForm((f) => ({ ...f, base_weight: parseFloat(e.target.value) }))}
                className="w-full accent-indigo-500"
              />
              <p className="text-[11px] text-slate-400 mt-1">
                1.0=기본 · 1.5+=보통(검색 시 우선 노출) · 2.0+=높음(핵심 문서, 항상 상위 노출) · 피드백 시 자동 상승
              </p>
            </div>
          )}
          {!canModifyNs && (
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">문서 우선순위</label>
              <div className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-2">
                <span className={`text-xs font-medium px-2 py-0.5 rounded ${weightClass(editForm.base_weight)}`}>
                  {editForm.base_weight.toFixed(1)} — {weightLabel(editForm.base_weight)}
                </span>
              </div>
            </div>
          )}
          {updateMutation.error && (
            <p className="text-xs text-rose-400">{String(updateMutation.error)}</p>
          )}
          <div className="flex gap-2 justify-end pt-2">
            <Button variant="ghost" size="sm" onClick={() => { setShowEdit(false); setEditingId(null); }}>
              <X className="w-3.5 h-3.5" />{canModifyNs ? '취소' : '닫기'}
            </Button>
            {canModifyNs && (
              <Button
                variant="primary" size="sm"
                loading={updateMutation.isPending}
                onClick={() => editingId !== null && updateMutation.mutate(editingId)}
                disabled={editForm.container_names.length === 0 || !editForm.content.trim()}
              >
                저장
              </Button>
            )}
          </div>
        </div>
      </Modal>

      {/* Delete Confirmation Modal */}
      <Modal isOpen={deleteTarget !== null} onClose={() => setDeleteTarget(null)} title="지식 삭제">
        <div className="space-y-4">
          <p className="text-sm text-slate-300">이 지식 항목을 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.</p>
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
