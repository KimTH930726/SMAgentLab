import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Edit2, Trash2, X, ChevronDown, ChevronUp, BookOpen } from 'lucide-react';
import { getNamespaces, getNamespacesDetail } from '../../api/namespaces';
import { getGlossary, createGlossaryItem, updateGlossaryItem, deleteGlossaryItem } from '../../api/knowledge';
import { useAppStore } from '../../store/useAppStore';
import { useAuthStore } from '../../store/useAuthStore';
import { Button } from '../ui/Button';
import { Modal } from '../ui/Modal';
import { Badge } from '../ui/Badge';
import type { GlossaryItem } from '../../types';

interface GlossaryFormData { term: string; description: string; }
const defaultForm: GlossaryFormData = { term: '', description: '' };

export function GlossaryTable() {
  const qc = useQueryClient();
  const { namespace: storeNamespace } = useAppStore();
  const user = useAuthStore((s) => s.user);
  const [selectedNs, setSelectedNs] = useState(storeNamespace || '');
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const { data: nsDetails = [] } = useQuery({
    queryKey: ['namespaces-detail'],
    queryFn: getNamespacesDetail,
    staleTime: 30_000,
  });

  const nsOwnerPart = nsDetails.find((n) => n.name === selectedNs)?.owner_part;
  // owner_part 없으면 admin만, 있으면 같은 파트 or admin
  const canModifyNs = user?.role === 'admin' || (!!nsOwnerPart && nsOwnerPart === user?.part);

  const [editingId, setEditingId] = useState<number | null>(null);
  const [editForm, setEditForm] = useState<GlossaryFormData>(defaultForm);
  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState<GlossaryFormData>(defaultForm);
  const [deleteTarget, setDeleteTarget] = useState<number | null>(null);

  const { data: namespaces = [] } = useQuery({
    queryKey: ['namespaces'],
    queryFn: getNamespaces,
    staleTime: 30_000,
  });

  // 삭제된 네임스페이스 선택 상태 자동 리셋
  useEffect(() => {
    if (selectedNs && namespaces.length > 0 && !namespaces.includes(selectedNs)) {
      setSelectedNs('');
    }
  }, [namespaces, selectedNs]);

  const { data: items = [], isLoading, error } = useQuery({
    queryKey: ['glossary', selectedNs],
    queryFn: () => getGlossary(selectedNs),
    enabled: !!selectedNs,
    staleTime: 15_000,
  });

  const createMutation = useMutation({
    mutationFn: () => createGlossaryItem({ namespace: selectedNs, term: createForm.term.trim(), description: createForm.description.trim() }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['glossary', selectedNs] }); setShowCreate(false); setCreateForm(defaultForm); },
  });

  const updateMutation = useMutation({
    mutationFn: (id: number) => updateGlossaryItem(id, { term: editForm.term.trim(), description: editForm.description.trim() }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['glossary', selectedNs] }); setEditingId(null); },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteGlossaryItem(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['glossary', selectedNs] }); setDeleteTarget(null); },
  });

  const startEdit = (item: GlossaryItem) => {
    setEditingId(item.id);
    setEditForm({ term: item.term, description: item.description });
    setExpandedId(item.id);
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-200">
          용어집
          {selectedNs && <span className="text-sm font-normal text-slate-500 ml-2">({selectedNs})</span>}
        </h2>
        <Button variant="primary" size="sm" onClick={() => setShowCreate(true)} disabled={!selectedNs || !canModifyNs}>
          <Plus className="w-4 h-4" />용어 추가
        </Button>
      </div>

      {/* Namespace selector */}
      <div>
        <label className="block text-xs font-medium text-slate-400 mb-1.5">네임스페이스</label>
        <select
          value={selectedNs}
          onChange={(e) => { setSelectedNs(e.target.value); setEditingId(null); setExpandedId(null); }}
          className="w-64 bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
        >
          <option value="">선택...</option>
          {namespaces.map((ns) => <option key={ns} value={ns}>{ns}</option>)}
        </select>
      </div>

      {!selectedNs && <div className="text-center py-10 text-slate-500">네임스페이스를 선택하세요.</div>}
      {selectedNs && isLoading && <div className="text-center py-10 text-slate-500 animate-pulse">로딩 중...</div>}
      {selectedNs && error && <div className="text-center py-10 text-rose-400">오류가 발생했습니다.</div>}

      {selectedNs && !isLoading && (
        <div className="space-y-2">
          {items.map((item) => (
            <div key={item.id} className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
              {/* Card header */}
              <div
                className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-slate-700/50 transition-colors"
                onClick={() => setExpandedId(expandedId === item.id ? null : item.id)}
              >
                <BookOpen className="w-4 h-4 text-indigo-400 flex-shrink-0" />
                <div className="flex-1 min-w-0">
                  <span className="text-sm font-medium text-slate-200">{item.term}</span>
                  <p className="text-xs text-slate-500 mt-0.5 truncate">{item.description}</p>
                </div>
                {item.created_by_username && (
                  <span className="text-xs text-slate-500">{item.created_by_username}</span>
                )}
                {item.created_by_part && (
                  <Badge color={canModifyNs ? 'emerald' : 'slate'}>{item.created_by_part}</Badge>
                )}
                {expandedId === item.id ? (
                  <ChevronUp className="w-4 h-4 text-slate-400" />
                ) : (
                  <ChevronDown className="w-4 h-4 text-slate-400" />
                )}
              </div>

              {/* Expanded content */}
              {expandedId === item.id && (
                <div className="border-t border-slate-700 px-4 py-4">
                  {editingId === item.id ? (
                    <div className="space-y-3">
                      <div>
                        <label className="block text-xs font-medium text-slate-400 mb-1">용어</label>
                        <input type="text" value={editForm.term} onChange={(e) => setEditForm((f) => ({ ...f, term: e.target.value }))}
                          className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500" />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-slate-400 mb-1">설명</label>
                        <textarea rows={3} value={editForm.description} onChange={(e) => setEditForm((f) => ({ ...f, description: e.target.value }))}
                          className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 resize-none" />
                      </div>
                      {updateMutation.error && (
                        <p className="text-xs text-rose-400">{String(updateMutation.error)}</p>
                      )}
                      <div className="flex gap-2 justify-end">
                        <Button variant="ghost" size="sm" onClick={() => setEditingId(null)}><X className="w-3.5 h-3.5" />취소</Button>
                        <Button variant="primary" size="sm" loading={updateMutation.isPending} onClick={() => updateMutation.mutate(item.id)}>저장</Button>
                      </div>
                    </div>
                  ) : (
                    <div className="space-y-3">
                      {canModifyNs && (
                        <div className="flex gap-2 justify-end pb-3 border-b border-slate-700">
                          <Button variant="secondary" size="sm" onClick={() => startEdit(item)}><Edit2 className="w-3.5 h-3.5" />수정</Button>
                          <Button variant="danger" size="sm" onClick={() => setDeleteTarget(item.id)}><Trash2 className="w-3.5 h-3.5" />삭제</Button>
                        </div>
                      )}
                      <div>
                        <p className="text-xs text-slate-500 mb-1">설명</p>
                        <p className="text-sm text-slate-300 leading-relaxed">{item.description}</p>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
          {items.length === 0 && <div className="text-center py-10 text-slate-500">용어 항목이 없습니다.</div>}
        </div>
      )}

      <Modal isOpen={showCreate} onClose={() => setShowCreate(false)} title="용어 추가">
        <div className="space-y-3">
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">용어 <span className="text-rose-400">*</span></label>
            <input type="text" value={createForm.term} onChange={(e) => setCreateForm((f) => ({ ...f, term: e.target.value }))}
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500" />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">설명 <span className="text-rose-400">*</span></label>
            <textarea rows={3} value={createForm.description} onChange={(e) => setCreateForm((f) => ({ ...f, description: e.target.value }))}
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 resize-none" />
          </div>
          {createMutation.error && (
            <p className="text-xs text-rose-400">{String(createMutation.error)}</p>
          )}
          <div className="flex gap-2 justify-end pt-2">
            <Button variant="secondary" size="sm" onClick={() => setShowCreate(false)}>취소</Button>
            <Button variant="primary" size="sm" loading={createMutation.isPending} onClick={() => createMutation.mutate()} disabled={!createForm.term.trim() || !createForm.description.trim()}>추가</Button>
          </div>
        </div>
      </Modal>

      <Modal isOpen={deleteTarget !== null} onClose={() => setDeleteTarget(null)} title="용어 삭제">
        <div className="space-y-4">
          <p className="text-sm text-slate-300">이 용어를 삭제하시겠습니까?</p>
          {deleteMutation.error && (
            <p className="text-xs text-rose-400">{String(deleteMutation.error)}</p>
          )}
          <div className="flex gap-2 justify-end">
            <Button variant="secondary" size="sm" onClick={() => setDeleteTarget(null)}>취소</Button>
            <Button variant="danger" size="sm" loading={deleteMutation.isPending} onClick={() => deleteTarget !== null && deleteMutation.mutate(deleteTarget)}>삭제</Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
