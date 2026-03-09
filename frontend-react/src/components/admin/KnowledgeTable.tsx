import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Edit2, Trash2, ChevronDown, ChevronUp, X } from 'lucide-react';
import {
  getKnowledge,
  createKnowledge,
  updateKnowledge,
  deleteKnowledge,
} from '../../api/knowledge';
import { getNamespaces, getNamespacesDetail } from '../../api/namespaces';
import { useAppStore } from '../../store/useAppStore';
import { useAuthStore } from '../../store/useAuthStore';
import { Button } from '../ui/Button';
import { Modal } from '../ui/Modal';
import { Badge } from '../ui/Badge';
import type { KnowledgeItem } from '../../types';

interface KnowledgeFormData {
  container_name: string;
  target_tables: string;
  content: string;
  query_template: string;
  base_weight: number;
}

const defaultForm: KnowledgeFormData = {
  container_name: '',
  target_tables: '',
  content: '',
  query_template: '',
  base_weight: 1.0,
};

export function KnowledgeTable() {
  const qc = useQueryClient();
  const { namespace: storeNamespace } = useAppStore();
  const user = useAuthStore((s) => s.user);
  const [selectedNs, setSelectedNs] = useState(storeNamespace || '');

  const { data: nsDetails = [] } = useQuery({
    queryKey: ['namespaces-detail'],
    queryFn: getNamespacesDetail,
    staleTime: 30_000,
  });

  const nsOwnerPart = nsDetails.find((n) => n.name === selectedNs)?.owner_part;
  // owner_part 없으면 admin만, 있으면 같은 파트 or admin
  const canModifyNs = user?.role === 'admin' || (!!nsOwnerPart && nsOwnerPart === user?.part);

  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editForm, setEditForm] = useState<KnowledgeFormData>(defaultForm);
  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState<KnowledgeFormData>(defaultForm);
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
        container_name: createForm.container_name,
        target_tables: createForm.target_tables.split(',').map((t) => t.trim()).filter(Boolean),
        content: createForm.content,
        query_template: createForm.query_template || null,
        base_weight: createForm.base_weight,
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
        container_name: editForm.container_name,
        target_tables: editForm.target_tables.split(',').map((t) => t.trim()).filter(Boolean),
        content: editForm.content,
        query_template: editForm.query_template || null,
        base_weight: editForm.base_weight,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['knowledge', selectedNs] });
      setEditingId(null);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteKnowledge(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['knowledge', selectedNs] });
      qc.invalidateQueries({ queryKey: ['stats-ns', selectedNs] });
      setDeleteTarget(null);
    },
  });

  const startEdit = (item: KnowledgeItem) => {
    setEditingId(item.id);
    setEditForm({
      container_name: item.container_name,
      target_tables: (item.target_tables ?? []).join(', '),
      content: item.content,
      query_template: item.query_template ?? '',
      base_weight: item.base_weight,
    });
    setExpandedId(item.id);
  };

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

      {!selectedNs && (
        <div className="text-center py-10 text-slate-500">네임스페이스를 선택하세요.</div>
      )}
      {selectedNs && isLoading && (
        <div className="text-center py-10 text-slate-500 animate-pulse">로딩 중...</div>
      )}
      {selectedNs && error && (
        <div className="text-center py-10 text-rose-400">오류가 발생했습니다.</div>
      )}

      {selectedNs && !isLoading && (
        <div className="space-y-2">
          {items.map((item) => (
            <div key={item.id} className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
              {/* Card header */}
              <div
                className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-slate-700/50 transition-colors"
                onClick={() => setExpandedId(expandedId === item.id ? null : item.id)}
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    {item.container_name && (
                      <>
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-cyan-900/40 text-cyan-400 border border-cyan-800/40">컨테이너</span>
                        <span className="text-sm font-medium text-slate-200">{item.container_name}</span>
                      </>
                    )}
                    {(item.target_tables ?? []).length > 0 && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-900/40 text-amber-400 border border-amber-800/40 ml-1">테이블</span>
                    )}
                    {(item.target_tables ?? []).slice(0, 3).map((t) => (
                      <Badge key={t} color="indigo">{t}</Badge>
                    ))}
                    {(item.target_tables ?? []).length > 3 && (
                      <Badge color="slate">+{(item.target_tables ?? []).length - 3}</Badge>
                    )}
                    <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${
                      item.base_weight >= 2 ? 'bg-emerald-500/20 text-emerald-300' :
                      item.base_weight >= 1.5 ? 'bg-indigo-500/20 text-indigo-300' :
                      'bg-slate-600/40 text-slate-300'
                    }`}>
                      우선순위: {item.base_weight >= 2 ? '높음' : item.base_weight >= 1.5 ? '보통' : '기본'} ({item.base_weight})
                    </span>
                  </div>
                  <p className="text-xs text-slate-500 mt-0.5 truncate">{item.content.slice(0, 100)}...</p>
                </div>
                <div className="flex items-center gap-2 flex-shrink-0">
                  <span className="text-[10px] text-slate-500">
                    {item.updated_at !== item.created_at
                      ? new Date(item.updated_at).toISOString().slice(0, 10)
                      : new Date(item.created_at).toISOString().slice(0, 10)}
                  </span>
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
              </div>

              {/* Expanded content */}
              {expandedId === item.id && (
                <div className="border-t border-slate-700 px-4 py-4">
                  {editingId === item.id ? (
                    <div className="space-y-3">
                      <div>
                        <label className="block text-xs font-medium text-slate-400 mb-1">컨테이너명</label>
                        <input
                          type="text"
                          value={editForm.container_name}
                          onChange={(e) => setEditForm((f) => ({ ...f, container_name: e.target.value }))}
                          className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-slate-400 mb-1">대상 테이블 (쉼표로 구분)</label>
                        <input
                          type="text"
                          value={editForm.target_tables}
                          onChange={(e) => setEditForm((f) => ({ ...f, target_tables: e.target.value }))}
                          placeholder="table_a, table_b"
                          className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-slate-400 mb-1">내용</label>
                        <textarea
                          rows={4}
                          value={editForm.content}
                          onChange={(e) => setEditForm((f) => ({ ...f, content: e.target.value }))}
                          className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 resize-none"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-slate-400 mb-1">쿼리 템플릿 (선택)</label>
                        <textarea
                          rows={3}
                          value={editForm.query_template}
                          onChange={(e) => setEditForm((f) => ({ ...f, query_template: e.target.value }))}
                          className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm font-mono text-slate-200 focus:outline-none focus:border-indigo-500 resize-none"
                          placeholder="SELECT ..."
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-slate-400 mb-1">
                          문서 우선순위: <span className={`font-medium ${
                            editForm.base_weight >= 2 ? 'text-emerald-400' :
                            editForm.base_weight >= 1.5 ? 'text-indigo-400' :
                            'text-slate-300'
                          }`}>{editForm.base_weight.toFixed(1)} — {editForm.base_weight >= 2 ? '높음' : editForm.base_weight >= 1.5 ? '보통' : '기본'}</span>
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
                      {updateMutation.error && (
                        <p className="text-xs text-rose-400">{String(updateMutation.error)}</p>
                      )}
                      <div className="flex gap-2 justify-end pt-2">
                        <Button variant="ghost" size="sm" onClick={() => setEditingId(null)}>
                          <X className="w-3.5 h-3.5" />취소
                        </Button>
                        <Button variant="primary" size="sm" loading={updateMutation.isPending} onClick={() => updateMutation.mutate(item.id)}>
                          저장
                        </Button>
                      </div>
                    </div>
                  ) : (
                    <div className="space-y-3">
                      {canModifyNs && (
                        <div className="flex gap-2 justify-end pb-3 border-b border-slate-700">
                          <Button variant="secondary" size="sm" onClick={() => startEdit(item)}>
                            <Edit2 className="w-3.5 h-3.5" />수정
                          </Button>
                          <Button variant="danger" size="sm" onClick={() => setDeleteTarget(item.id)}>
                            <Trash2 className="w-3.5 h-3.5" />삭제
                          </Button>
                        </div>
                      )}
                      <div>
                        <p className="text-xs text-slate-500 mb-1">내용</p>
                        <p className="text-sm text-slate-300 whitespace-pre-wrap leading-relaxed">{item.content}</p>
                      </div>
                      {item.query_template && (
                        <div>
                          <p className="text-xs text-slate-500 mb-1">쿼리 템플릿</p>
                          <pre className="text-xs font-mono text-slate-300 bg-slate-900 p-3 rounded-lg overflow-x-auto">
                            {item.query_template}
                          </pre>
                        </div>
                      )}
                      <div className="flex items-center justify-between text-xs text-slate-500">
                        <div className="flex items-center gap-1.5">
                          <span>문서 우선순위:</span>
                          <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium ${
                            item.base_weight >= 2 ? 'bg-emerald-500/20 text-emerald-300' :
                            item.base_weight >= 1.5 ? 'bg-indigo-500/20 text-indigo-300' :
                            'bg-slate-600/40 text-slate-300'
                          }`}>
                            {item.base_weight >= 2 ? '높음' : item.base_weight >= 1.5 ? '보통' : '기본'} ({item.base_weight})
                          </span>
                        </div>
                        <span>수정: {new Date(item.updated_at).toLocaleDateString('ko-KR')}</span>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
          {items.length === 0 && (
            <div className="text-center py-10 text-slate-500">지식 항목이 없습니다.</div>
          )}
        </div>
      )}

      {/* Create Modal */}
      <Modal isOpen={showCreate} onClose={() => setShowCreate(false)} title="지식 추가" maxWidth="max-w-xl">
        <div className="space-y-3">
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">
              컨테이너명 <span className="text-rose-400">*</span>
            </label>
            <input
              type="text"
              value={createForm.container_name}
              onChange={(e) => setCreateForm((f) => ({ ...f, container_name: e.target.value }))}
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">대상 테이블 (쉼표로 구분)</label>
            <input
              type="text"
              value={createForm.target_tables}
              onChange={(e) => setCreateForm((f) => ({ ...f, target_tables: e.target.value }))}
              placeholder="table_a, table_b"
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">
              내용 <span className="text-rose-400">*</span>
            </label>
            <textarea
              rows={4}
              value={createForm.content}
              onChange={(e) => setCreateForm((f) => ({ ...f, content: e.target.value }))}
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 resize-none"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">쿼리 템플릿 (선택)</label>
            <textarea
              rows={3}
              value={createForm.query_template}
              onChange={(e) => setCreateForm((f) => ({ ...f, query_template: e.target.value }))}
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm font-mono text-slate-200 focus:outline-none focus:border-indigo-500 resize-none"
              placeholder="SELECT ..."
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">
              문서 우선순위: <span className={`font-medium ${
                createForm.base_weight >= 2 ? 'text-emerald-400' :
                createForm.base_weight >= 1.5 ? 'text-indigo-400' :
                'text-slate-300'
              }`}>{createForm.base_weight.toFixed(1)} — {createForm.base_weight >= 2 ? '높음' : createForm.base_weight >= 1.5 ? '보통' : '기본'}</span>
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
              disabled={!createForm.container_name.trim() || !createForm.content.trim()}
            >
              추가
            </Button>
          </div>
          {createMutation.isError && (
            <p className="text-xs text-rose-400">{String(createMutation.error)}</p>
          )}
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
