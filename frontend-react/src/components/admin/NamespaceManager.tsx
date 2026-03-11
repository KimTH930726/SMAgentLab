import { useState, useRef, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Trash2, Database, ChevronDown, ChevronUp, Tag, Pencil, Check, X } from 'lucide-react';
import {
  getNamespacesDetail, createNamespace, deleteNamespace, renameNamespace,
  getCategories, createCategory, deleteCategory, renameCategory,
} from '../../api/namespaces';
import { useAuthStore } from '../../store/useAuthStore';
import { useAppStore } from '../../store/useAppStore';
import { Button } from '../ui/Button';
import { Modal } from '../ui/Modal';
import { Badge } from '../ui/Badge';
import type { KnowledgeCategory } from '../../types';

type AdminTabId = 'knowledge' | 'glossary';
interface NamespaceManagerProps {
  onNavigate?: (tab: AdminTabId) => void;
}

function CategorySection({ namespace, canModify }: { namespace: string; canModify: boolean }) {
  const qc = useQueryClient();
  const [newCat, setNewCat] = useState('');
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editingName, setEditingName] = useState('');
  const editRef = useRef<HTMLInputElement>(null);

  const { data: categories = [] } = useQuery<KnowledgeCategory[]>({
    queryKey: ['categories', namespace],
    queryFn: () => getCategories(namespace),
  });

  useEffect(() => {
    if (editingId !== null) editRef.current?.focus();
  }, [editingId]);

  const addMutation = useMutation({
    mutationFn: (name: string) => createCategory(namespace, name),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['categories', namespace] });
      setNewCat('');
    },
  });

  const renameMutation = useMutation({
    mutationFn: ({ oldName, newName }: { oldName: string; newName: string }) =>
      renameCategory(namespace, oldName, newName),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['categories', namespace] });
      setEditingId(null);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (name: string) => deleteCategory(namespace, name),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['categories', namespace] }),
  });

  const handleAdd = () => {
    const name = newCat.trim();
    if (!name) return;
    addMutation.mutate(name);
  };

  const startEdit = (cat: KnowledgeCategory) => {
    if (!canModify) return;
    setEditingId(cat.id);
    setEditingName(cat.name);
  };

  const commitEdit = (oldName: string) => {
    const name = editingName.trim();
    if (!name || name === oldName) { setEditingId(null); return; }
    renameMutation.mutate({ oldName, newName: name });
  };

  return (
    <div className="pt-3 border-t border-slate-700">
      <div className="flex items-center gap-1.5 mb-2">
        <Tag className="w-3.5 h-3.5 text-indigo-400" />
        <span className="text-xs font-medium text-slate-400">업무구분</span>
        {canModify && <span className="text-xs text-slate-600">(이름 클릭하면 수정)</span>}
      </div>
      <div className="flex flex-col gap-1.5 mb-2">
        {categories.map((cat) => (
          <div
            key={cat.id}
            className="bg-slate-900/60 border border-slate-700/50 rounded-lg px-3 py-2 flex items-center"
          >
            <Tag className="w-3 h-3 text-indigo-400 flex-shrink-0 mr-2" />
            <div className="flex-1 min-w-0">
              {editingId === cat.id ? (
                <input
                  ref={editRef}
                  value={editingName}
                  onChange={(e) => setEditingName(e.target.value)}
                  onBlur={() => commitEdit(cat.name)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.nativeEvent.isComposing) commitEdit(cat.name);
                    if (e.key === 'Escape') setEditingId(null);
                  }}
                  className="w-full bg-slate-800 border border-indigo-500 rounded px-1.5 py-0.5 text-xs text-slate-100 outline-none"
                />
              ) : (
                <button
                  onClick={() => startEdit(cat)}
                  className={`text-sm text-slate-200 ${canModify ? 'hover:text-indigo-300 transition-colors' : ''}`}
                  title={canModify ? '클릭하여 이름 수정' : undefined}
                >
                  {cat.name}
                </button>
              )}
            </div>
            {canModify && editingId !== cat.id && (
              <button
                onClick={() => deleteMutation.mutate(cat.name)}
                className="ml-2 p-1 rounded text-slate-500 hover:text-rose-400 hover:bg-rose-900/20 transition-colors flex-shrink-0"
                title="삭제"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
        ))}
        {categories.length === 0 && (
          <span className="text-xs text-slate-600">업무구분이 없습니다.</span>
        )}
      </div>
      {canModify && (
        <div className="flex gap-1.5">
          <input
            type="text"
            value={newCat}
            onChange={(e) => setNewCat(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && !e.nativeEvent.isComposing && handleAdd()}
            placeholder="새 업무구분 이름"
            className="flex-1 bg-slate-900 border border-slate-600 rounded-lg px-2 py-1 text-xs text-slate-200 placeholder-slate-600 focus:outline-none focus:border-indigo-500"
          />
          <Button
            variant="secondary"
            size="sm"
            onClick={handleAdd}
            loading={addMutation.isPending}
            disabled={!newCat.trim()}
          >
            <Plus className="w-3 h-3" />
          </Button>
        </div>
      )}
      {addMutation.isError && (
        <p className="text-xs text-rose-400 mt-1">{String(addMutation.error)}</p>
      )}
      {renameMutation.isError && (
        <p className="text-xs text-rose-400 mt-1">{String(renameMutation.error)}</p>
      )}
    </div>
  );
}

export function NamespaceManager({ onNavigate }: NamespaceManagerProps) {
  const qc = useQueryClient();
  const user = useAuthStore((s) => s.user);
  const { setNamespace } = useAppStore();
  // owner_part 없으면 공통(모두 가능), 있으면 같은 파트 or admin
  const canModifyNs = (ownerPart?: string | null) =>
    user?.role === 'admin' || !ownerPart || ownerPart === user?.part;
  // 공통 파트(owner_part=null) 삭제는 admin 전용
  const canDeleteNs = (ownerPart?: string | null) =>
    user?.role === 'admin' || (!!ownerPart && ownerPart === user?.part);
  const [showCreate, setShowCreate] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [newName, setNewName] = useState('');
  const [newDescription, setNewDescription] = useState('');
  const [expandedCat, setExpandedCat] = useState<string | null>(null);
  const [editingNs, setEditingNs] = useState<string | null>(null);
  const [editingNsName, setEditingNsName] = useState('');
  const nsEditRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editingNs !== null) nsEditRef.current?.focus();
  }, [editingNs]);

  const { data: namespaces = [], isLoading, error } = useQuery({
    queryKey: ['namespaces-detail'],
    queryFn: getNamespacesDetail,
    staleTime: 10_000,
    refetchOnMount: 'always',
  });

  const createMutation = useMutation({
    mutationFn: (payload: { name: string; description: string }) => createNamespace(payload),
    onSuccess: (_data, payload) => {
      // 캐시 즉시 업데이트 (사이드바 싱크)
      qc.setQueryData<string[]>(['namespaces'], (old) =>
        old ? [...old, payload.name] : [payload.name],
      );
      qc.invalidateQueries({ queryKey: ['namespaces-detail'] });
      qc.invalidateQueries({ queryKey: ['namespaces'] });
      setShowCreate(false);
      setNewName('');
      setNewDescription('');
    },
  });

  const renameMutation = useMutation({
    mutationFn: ({ oldName, newName: nn }: { oldName: string; newName: string }) =>
      renameNamespace(oldName, nn),
    onSuccess: (_data, { oldName, newName: nn }) => {
      qc.setQueryData<string[]>(['namespaces'], (old) =>
        old?.map((ns) => (ns === oldName ? nn : ns)),
      );
      qc.invalidateQueries({ queryKey: ['namespaces-detail'] });
      qc.invalidateQueries({ queryKey: ['namespaces'] });
      setEditingNs(null);
    },
    onError: (err: Error) => {
      alert(err.message || '이름 변경 실패');
    },
  });

  const commitNsRename = (oldName: string) => {
    const trimmed = editingNsName.trim();
    if (!trimmed || trimmed === oldName) { setEditingNs(null); return; }
    renameMutation.mutate({ oldName, newName: trimmed });
  };

  const deleteMutation = useMutation({
    mutationFn: (name: string) => deleteNamespace(name),
    onSuccess: (_data, deletedName) => {
      // 캐시에서 삭제된 NS 즉시 제거 (다른 탭 전환 시 stale 데이터 방지)
      qc.setQueryData<string[]>(['namespaces'], (old) =>
        old?.filter((ns) => ns !== deletedName),
      );
      qc.setQueryData<Array<{ name: string }>>(
        ['namespaces-detail'],
        (old) => old?.filter((ns) => ns.name !== deletedName),
      );
      // 삭제된 NS 관련 하위 데이터 캐시도 제거
      qc.removeQueries({ queryKey: ['knowledge', deletedName] });
      qc.removeQueries({ queryKey: ['glossary', deletedName] });
      qc.removeQueries({ queryKey: ['fewshots', deletedName] });
      // 전체 목록 리프레시
      qc.invalidateQueries({ queryKey: ['namespaces-detail'] });
      qc.invalidateQueries({ queryKey: ['namespaces'] });
      setDeleteTarget(null);
    },
  });

  const handleCreate = () => {
    if (!newName.trim()) return;
    createMutation.mutate({ name: newName.trim(), description: newDescription.trim() });
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-200">파트 DB 관리</h2>
        <Button
          variant="primary"
          size="sm"
          onClick={() => setShowCreate(true)}
        >
          <Plus className="w-4 h-4" />
          새 파트
        </Button>
      </div>

      {isLoading && (
        <div className="text-center py-10 text-slate-500 animate-pulse">로딩 중...</div>
      )}
      {error && (
        <div className="text-center py-10 text-rose-400">오류가 발생했습니다.</div>
      )}

      <div className="grid gap-3">
        {namespaces.map((ns) => (
          <div
            key={ns.name}
            className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden cursor-pointer hover:border-slate-600 transition-colors"
            onClick={() => setExpandedCat(expandedCat === ns.name ? null : ns.name)}
          >
            <div className="flex items-center gap-4 p-4">
              <div className="w-10 h-10 rounded-xl bg-indigo-900/40 border border-indigo-700/40 flex items-center justify-center flex-shrink-0">
                <Database className="w-5 h-5 text-indigo-400" />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  {editingNs === ns.name ? (
                    <span className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                      <input
                        ref={nsEditRef}
                        value={editingNsName}
                        onChange={(e) => setEditingNsName(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' && !e.nativeEvent.isComposing) commitNsRename(ns.name);
                          if (e.key === 'Escape') setEditingNs(null);
                        }}
                        onBlur={() => commitNsRename(ns.name)}
                        className="bg-slate-900 border border-indigo-500 rounded px-2 py-0.5 text-sm text-slate-100 font-semibold outline-none w-36"
                      />
                      <button onClick={() => commitNsRename(ns.name)} className="text-emerald-400 hover:text-emerald-300"><Check className="w-3.5 h-3.5" /></button>
                      <button onClick={() => setEditingNs(null)} className="text-slate-500 hover:text-slate-300"><X className="w-3.5 h-3.5" /></button>
                    </span>
                  ) : (
                    <span className="flex items-center gap-1 group">
                      <span className="font-semibold text-slate-200">{ns.name}</span>
                      {canModifyNs(ns.owner_part) && (
                        <button
                          onClick={(e) => { e.stopPropagation(); setEditingNs(ns.name); setEditingNsName(ns.name); }}
                          className="opacity-0 group-hover:opacity-100 text-slate-600 hover:text-indigo-400 transition-all"
                          title="이름 수정"
                        >
                          <Pencil className="w-3 h-3" />
                        </button>
                      )}
                    </span>
                  )}
                  <button
                    onClick={(e) => { e.stopPropagation(); setNamespace(ns.name); onNavigate?.('knowledge'); }}
                    className="text-xs text-slate-500 bg-slate-700 px-2 py-0.5 rounded-full hover:bg-indigo-900/40 hover:text-indigo-300 transition-colors"
                  >
                    지식 {ns.knowledge_count}건
                  </button>
                  <button
                    onClick={(e) => { e.stopPropagation(); setNamespace(ns.name); onNavigate?.('glossary'); }}
                    className="text-xs text-slate-500 bg-slate-700 px-2 py-0.5 rounded-full hover:bg-indigo-900/40 hover:text-indigo-300 transition-colors"
                  >
                    용어 {ns.glossary_count}건
                  </button>
                </div>
                {ns.description && (
                  <p className="text-sm text-slate-400 mt-0.5 truncate">{ns.description}</p>
                )}
                <div className="flex items-center gap-2 mt-0.5">
                  {ns.created_by_username && (
                    <span className="text-xs text-slate-500">{ns.created_by_username}</span>
                  )}
                  {ns.owner_part && (
                    <Badge color={canModifyNs(ns.owner_part) ? 'emerald' : 'slate'}>{ns.owner_part}</Badge>
                  )}
                  <span className="text-xs text-slate-500">
                    생성: {new Date(ns.created_at).toLocaleDateString('ko-KR')}
                  </span>
                </div>
              </div>
              <div className="flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
                <Tag className="w-3.5 h-3.5 text-slate-500" />
                {expandedCat === ns.name
                  ? <ChevronUp className="w-4 h-4 text-slate-400" />
                  : <ChevronDown className="w-4 h-4 text-slate-400" />
                }
                {canDeleteNs(ns.owner_part) && (
                  <Button
                    variant="danger"
                    size="sm"
                    onClick={() => setDeleteTarget(ns.name)}
                  >
                    <Trash2 className="w-4 h-4" />
                  </Button>
                )}
              </div>
            </div>
            {expandedCat === ns.name && (
              <div className="px-4 pb-4" onClick={(e) => e.stopPropagation()}>
                <CategorySection namespace={ns.name} canModify={canModifyNs(ns.owner_part)} />
              </div>
            )}
          </div>
        ))}
        {!isLoading && namespaces.length === 0 && (
          <div className="text-center py-10 text-slate-500">파트가 없습니다.</div>
        )}
      </div>

      {/* Create Modal */}
      <Modal
        isOpen={showCreate}
        onClose={() => setShowCreate(false)}
        title="새 파트 생성"
      >
        <div className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1.5">
              이름 <span className="text-rose-400">*</span>
            </label>
            <input
              type="text"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="예: billing, infra, support"
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
              onKeyDown={(e) => e.key === 'Enter' && !e.nativeEvent.isComposing && handleCreate()}
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1.5">설명</label>
            <input
              type="text"
              value={newDescription}
              onChange={(e) => setNewDescription(e.target.value)}
              placeholder="파트 설명"
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
            />
          </div>
          <div className="flex gap-2 justify-end">
            <Button variant="secondary" size="sm" onClick={() => setShowCreate(false)}>
              취소
            </Button>
            <Button
              variant="primary"
              size="sm"
              loading={createMutation.isPending}
              onClick={handleCreate}
              disabled={!newName.trim()}
            >
              생성
            </Button>
          </div>
          {createMutation.isError && (
            <p className="text-xs text-rose-400">{String(createMutation.error)}</p>
          )}
        </div>
      </Modal>

      {/* Delete Confirmation Modal */}
      <Modal
        isOpen={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        title="파트 삭제"
      >
        <div className="space-y-4">
          <p className="text-sm text-slate-300">
            <span className="font-semibold text-rose-400">{deleteTarget}</span> 파트를
            삭제하면 모든 지식과 용어도 함께 삭제됩니다. 계속하시겠습니까?
          </p>
          <div className="flex gap-2 justify-end">
            <Button variant="secondary" size="sm" onClick={() => setDeleteTarget(null)}>
              취소
            </Button>
            <Button
              variant="danger"
              size="sm"
              loading={deleteMutation.isPending}
              onClick={() => deleteTarget && deleteMutation.mutate(deleteTarget)}
            >
              삭제
            </Button>
          </div>
          {deleteMutation.isError && (
            <p className="text-xs text-rose-400">{String(deleteMutation.error)}</p>
          )}
        </div>
      </Modal>
    </div>
  );
}
