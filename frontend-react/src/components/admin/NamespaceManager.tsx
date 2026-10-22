import { Fragment, useState, useRef, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Trash2, Tag, Pencil } from 'lucide-react';
import {
  getNamespacesDetail, createNamespace, deleteNamespace, renameNamespace, setNamespaceOwner,
  getCategories, createCategory, deleteCategory, renameCategory,
} from '../../api/namespaces';
import { getAllParts } from '../../api/auth';
import { useAuthStore } from '../../store/useAuthStore';
import { useAppStore } from '../../store/useAppStore';
import { Button } from '../ui/Button';
import { Modal } from '../ui/Modal';
import { Badge } from '../ui/Badge';
import type { KnowledgeCategory, NamespaceDetail } from '../../types';
import { showConfirm, showError } from '../../store/useDialogStore';
import { canEditDomain, effectiveOwnerPart } from '../../utils/domainAccess';
import { FormModal } from '../ui/FormModal';

/** 이름은 바뀌었는데 담당 저장만 실패 — 화면이 "이름 변경은 됨"을 알아야 다시 저장할 때 옛 이름으로 또 바꾸지 않는다(10/21 리뷰 L1) */
class RenamedButOwnerFailed extends Error {}

interface NamespaceManagerProps {
  onNavigate?: (to: 'policy' | 'knowledge' | 'glossary') => void;
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
      qc.invalidateQueries({ queryKey: ['categories', namespace] }); qc.invalidateQueries({ queryKey: ['namespaces-detail'] });
      setNewCat('');
    },
    onError: (err: Error) => { void showError(err, '업무구분 추가 실패'); },
  });

  const renameMutation = useMutation({
    mutationFn: ({ oldName, newName }: { oldName: string; newName: string }) =>
      renameCategory(namespace, oldName, newName),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['categories', namespace] }); qc.invalidateQueries({ queryKey: ['namespaces-detail'] });
      setEditingId(null);
    },
    onError: (err: Error) => { void showError(err, '업무구분 이름 변경 실패'); },
  });

  const deleteMutation = useMutation({
    mutationFn: (name: string) => deleteCategory(namespace, name),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['categories', namespace] }); qc.invalidateQueries({ queryKey: ['namespaces-detail'] }); },
    onError: (err: Error) => { void showError(err, '업무구분 삭제 실패'); },
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
        <Tag className="w-3.5 h-3.5 text-indigo-600 dark:text-indigo-400" />
        <span className="text-xs font-medium text-slate-400">업무구분</span>
        {canModify && <span className="text-xs text-slate-600">(이름 클릭하면 수정)</span>}
      </div>
      <div className="flex flex-col gap-1.5 mb-2">
        {categories.map((cat) => (
          <div
            key={cat.id}
            className="bg-slate-900/60 border border-slate-700/50 rounded-lg px-3 py-2 flex items-center"
          >
            <Tag className="w-3 h-3 text-indigo-600 dark:text-indigo-400 flex-shrink-0 mr-2" />
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
                  className={`text-sm text-slate-200 ${canModify ? 'hover:text-indigo-600 dark:hover:text-indigo-300 transition-colors' : ''}`}
                  title={canModify ? '클릭하여 이름 수정' : undefined}
                >
                  {cat.name}
                </button>
              )}
            </div>
            {canModify && editingId !== cat.id && (
              <button
                onClick={() => deleteMutation.mutate(cat.name)}
                className="ml-2 p-1 rounded text-slate-500 hover:text-rose-600 hover:bg-rose-50 dark:hover:text-rose-400 dark:hover:bg-rose-900/20 transition-colors flex-shrink-0"
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
      {/* 추가·이름 변경 실패는 onError의 오류 창 하나로만 알린다 — 아래 줄 표시와 두 번 보이던 것(10/21 리뷰 5e) */}
    </div>
  );
}

export function NamespaceManager({ onNavigate }: NamespaceManagerProps) {
  const qc = useQueryClient();
  const user = useAuthStore((s) => s.user);
  const { namespace: selectedNamespace, setNamespace } = useAppStore();
  // 영역 안 데이터(업무구분 등)는 담당 파트 사람 또는 관리자, 영역 자체(만들기·이름·삭제)는 관리자만(2026-10-20)
  const canModifyNs = (ownerPart?: string | null) => canEditDomain(user, ownerPart);
  const isAdmin = user?.role === 'admin';
  const [showCreate, setShowCreate] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<NamespaceDetail | null>(null);
  const [newName, setNewName] = useState('');
  const [newDescription, setNewDescription] = useState('');
  const [expandedCat, setExpandedCat] = useState<string | null>(null);
  // 업무 컬렉션 수정 모달 — 이름 + 담당 파트(10/21 사용자: 담당은 영역의 속성이라 이름과 같은 자리에서). 시스템·권한 › 담당 업무와 같은 칸(owner_part_id)
  const [editingNs, setEditingNs] = useState<NamespaceDetail | null>(null);
  const [editingNsName, setEditingNsName] = useState('');
  const [editingOwner, setEditingOwner] = useState('');   // '' = 담당 없음
  const { data: parts = [] } = useQuery({ queryKey: ['parts-all'], queryFn: getAllParts, enabled: isAdmin && editingNs !== null });

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

  const openEditNs = (ns: NamespaceDetail) => {
    editMutation.reset();
    setEditingNs(ns);
    setEditingNsName(ns.name);
    setEditingOwner(ns.owner_part ?? '');
  };

  // 바뀐 것만 보낸다: 이름 → (새 이름으로) 담당. 이름만 바뀌고 담당이 실패하면 오류 문구에 그렇게 적는다(조용히 반쯤 저장 안 되게)
  const editMutation = useMutation({
    mutationFn: async ({ oldName, newName: nn, owner }: { oldName: string; newName: string | null; owner: string | null | undefined }) => {
      if (nn) await renameNamespace(oldName, nn);
      if (owner !== undefined) {
        try {
          await setNamespaceOwner(nn ?? oldName, owner);
        } catch (e) {
          if (nn) throw new RenamedButOwnerFailed(`이름은 '${nn}'(으)로 바뀌었지만 담당 파트는 저장하지 못했어요 — ${(e as Error).message}`);
          throw e;
        }
      }
    },
    onSettled: (_data, err, { oldName, newName: nn }) => {
      // 이름이 실제로 바뀌었을 때만(성공, 또는 담당만 실패) 새 이름으로 — 이름 변경 자체가 실패했으면 옛 이름 그대로
      if (nn && (!err || err instanceof RenamedButOwnerFailed)) {
        qc.setQueryData<string[]>(['namespaces'], (old) => old?.map((ns) => (ns === oldName ? nn : ns)));
        // 고른 영역의 이름을 바꿨으면 새 이름으로 따라간다 — 안 그러면 사이드바가 "목록에 없음"으로 보고 첫 영역으로 넘어간다(재리뷰 L3)
        if (selectedNamespace === oldName) setNamespace(nn);
        // 모달은 열어 둔 채(오류 표시) 편집 대상을 새 이름으로 — 다시 저장하면 담당만 보낸다(옛 이름으로 또 바꾸면 404였다, 10/21 리뷰 L1)
        if (err) setEditingNs((cur) => (cur && cur.name === oldName ? { ...cur, name: nn } : cur));
      }
      qc.invalidateQueries({ queryKey: ['namespaces-detail'] });
      qc.invalidateQueries({ queryKey: ['namespaces'] });
    },
    onSuccess: () => setEditingNs(null),
    onError: () => { /* 모달 안에 표시 */ },
  });

  const editNameChanged = !!editingNs && !!editingNsName.trim() && editingNsName.trim() !== editingNs.name;
  const editOwnerChanged = !!editingNs && editingOwner !== (editingNs.owner_part ?? '');

  const commitNsEdit = async () => {
    if (!editingNs || editMutation.isPending || (!editNameChanged && !editOwnerChanged)) return;
    if (editOwnerChanged) {
      const msg = editingOwner
        ? `'${editingOwner}' 사람이 '${editingNsName.trim()}'의 지식·정책·용어를 수정하게 됩니다.` +
          (editingNs.owner_part ? `\n(지금 담당 '${editingNs.owner_part}'은 수정할 수 없게 돼요)` : '')
        : `'${editingNsName.trim()}'의 담당 파트를 없앱니다 — 관리자만 수정할 수 있게 돼요.`;
      if (!(await showConfirm(msg, { tone: 'warning', confirmLabel: '저장', title: '담당 파트 변경' }))) return;
    }
    editMutation.mutate({
      oldName: editingNs.name,
      newName: editNameChanged ? editingNsName.trim() : null,
      owner: editOwnerChanged ? (editingOwner || null) : undefined,
    });
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
      qc.removeQueries({ queryKey: ['stats-ns', deletedName] });
      qc.removeQueries({ queryKey: ['categories', deletedName] });
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
        <h2 className="text-lg font-semibold text-slate-200">업무 컬렉션</h2>
        {isAdmin && (
          <Button
            variant="primary"
            size="sm"
            onClick={() => setShowCreate(true)}
          >
            <Plus className="w-4 h-4" />
            새 업무 컬렉션
          </Button>
        )}
      </div>

      {isLoading && (
        <div className="text-center py-10 text-slate-500 animate-pulse">로딩 중...</div>
      )}
      {error && (
        <div className="text-center py-10 text-rose-600 dark:text-rose-400">오류가 발생했습니다.</div>
      )}

      {/* 업무 컬렉션 목록(③, 10/21) — 줄 = 그 영역 고르기(사이드바와 같은 값), 숫자 = 그 영역을 고른 채 해당 화면으로 */}
      {!isLoading && namespaces.length > 0 && (
        <div className="bg-slate-800 rounded-xl border border-slate-700 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-700 text-slate-400">
                <th className="text-left px-4 py-3 font-medium">업무 컬렉션</th>
                <th className="text-left px-4 py-3 font-medium">담당 파트</th>
                <th className="text-right px-3 py-3 font-medium" title="지금 쓰이는 정책(폐기·반려 제외)">정책</th>
                <th className="text-right px-3 py-3 font-medium" title="검색에 쓰이는(활성) 지식 — 승인 대기·반려·폐기·수집 중 제외">지식 문서</th>
                <th className="text-right px-3 py-3 font-medium">용어</th>
                <th className="text-left px-3 py-3 font-medium" title="지식 문서의 하위 분류 — 곧 없어질 예정(하위 업무는 업무 컬렉션으로, DB·공통코드는 시스템 정보로)">
                  업무구분 <span className="text-[10px] text-slate-500">곧 없어짐</span>
                </th>
                <th className="w-20"></th>
              </tr>
            </thead>
            <tbody>
              {namespaces.map((ns) => {
                const selected = ns.name === selectedNamespace;
                const count = (n: number | undefined, to: 'policy' | 'knowledge' | 'glossary', label: string) => (
                  <button onClick={(e) => { e.stopPropagation(); setNamespace(ns.name); onNavigate?.(to); }}
                    title={`${ns.name}의 ${label} 보기`}
                    className="tabular-nums text-slate-300 hover:text-indigo-600 dark:hover:text-indigo-300 hover:underline">
                    {n ?? 0}
                  </button>
                );
                const toggleCat = (e: React.MouseEvent) => { e.stopPropagation(); setExpandedCat(expandedCat === ns.name ? null : ns.name); };
                return (
                  <Fragment key={ns.name}>
                    <tr onClick={() => setNamespace(ns.name)} tabIndex={0} aria-current={selected ? 'true' : undefined}
                      onKeyDown={(e) => { if ((e.key === 'Enter' || e.key === ' ') && e.target === e.currentTarget) { e.preventDefault(); setNamespace(ns.name); } }}
                      className={`border-b border-slate-700/50 cursor-pointer transition-colors ${selected ? 'bg-indigo-50 dark:bg-indigo-900/20' : 'hover:bg-slate-700/30'}`}>
                      <td className="px-4 py-2.5">
                        <div>
                          <div className="font-medium text-slate-200">{ns.name}</div>
                          {ns.description && <div className="text-xs text-slate-500 truncate max-w-[16rem]" title={ns.description}>{ns.description}</div>}
                        </div>
                      </td>
                      <td className="px-4 py-2.5">
                        {ns.owner_part
                          ? <Badge color={canModifyNs(effectiveOwnerPart(ns)) ? 'emerald' : 'slate'}>{ns.owner_part}</Badge>
                          : <span className="text-xs text-slate-500" title="담당 지정: 시스템·권한 › 담당 업무">없음(관리자만)</span>}
                      </td>
                      <td className="px-3 py-2.5 text-right">{count(ns.policy_count, 'policy', '정책')}</td>
                      <td className="px-3 py-2.5 text-right">{count(ns.knowledge_count, 'knowledge', '지식 문서')}</td>
                      <td className="px-3 py-2.5 text-right">{count(ns.glossary_count, 'glossary', '용어')}</td>
                      <td className="px-3 py-2.5">
                        {(ns.category_count ?? 0) > 0 || canModifyNs(effectiveOwnerPart(ns)) ? (
                          <button onClick={toggleCat} aria-expanded={expandedCat === ns.name}
                            title={canModifyNs(effectiveOwnerPart(ns)) ? '업무구분 보기·추가·수정' : '업무구분 보기'}
                            className="text-xs text-slate-400 hover:text-slate-200">
                            {(ns.category_count ?? 0) > 0 ? `${ns.category_count}개` : '-'} {expandedCat === ns.name ? '▾' : '▸'}
                          </button>
                        ) : <span className="text-xs text-slate-500">-</span>}
                      </td>
                      <td className="px-3 py-2.5" onClick={(e) => e.stopPropagation()}>
                        {isAdmin && (
                          <div className="flex items-center justify-end gap-1">
                            <button onClick={() => openEditNs(ns)} title="수정(이름·담당 파트)"
                              className="p-1.5 rounded text-slate-500 hover:text-indigo-600 dark:hover:text-indigo-400 hover:bg-slate-700/60">
                              <Pencil className="w-3.5 h-3.5" />
                            </button>
                            <button onClick={() => setDeleteTarget(ns)} title="삭제"
                              className="p-1.5 rounded text-slate-500 hover:text-rose-600 dark:hover:text-rose-400 hover:bg-slate-700/60">
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        )}
                      </td>
                    </tr>
                    {expandedCat === ns.name && (
                      <tr className="border-b border-slate-700/50">
                        <td colSpan={7} className="px-4 pb-4 pt-1">
                          <CategorySection namespace={ns.name} canModify={canModifyNs(effectiveOwnerPart(ns))} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {!isLoading && namespaces.length === 0 && (
        <div className="text-center py-10 text-slate-500">업무 컬렉션이 없습니다.</div>
      )}

      {/* 업무 컬렉션 수정 — 이름 + 담당 파트(10/21). 모달: 그 자리에서 고치던 칸은 포커스가 빠지거나 취소를 눌러도 저장됐다 */}
      <FormModal
        isOpen={editingNs !== null}
        title="업무 컬렉션 수정"
        onClose={() => setEditingNs(null)}
        onSubmit={commitNsEdit}
        submitting={editMutation.isPending}
        submitDisabled={!editingNsName.trim() || (!editNameChanged && !editOwnerChanged)}
        error={editMutation.isError ? String((editMutation.error as Error)?.message ?? '저장 실패') : null}
      >
        <div>
          <label htmlFor="ns-rename" className="block text-xs text-slate-400 mb-1">이름</label>
          <input id="ns-rename" autoFocus value={editingNsName} onChange={(e) => setEditingNsName(e.target.value)}
            className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500" />
          <p className="text-xs text-slate-500 mt-1">안의 정책·지식·용어는 그대로 따라가요</p>
        </div>
        <div>
          <label htmlFor="ns-owner" className="block text-xs text-slate-400 mb-1">담당 파트</label>
          <select id="ns-owner" value={editingOwner} onChange={(e) => setEditingOwner(e.target.value)}
            className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500">
            <option value="">담당 없음</option>
            {editingOwner && !parts.some((p) => p.name === editingOwner) && <option value={editingOwner}>{editingOwner}</option>}
            {parts.map((p) => <option key={p.id} value={p.name}>{p.team ? `${p.team} · ${p.name}` : p.name}</option>)}
          </select>
          <p className="text-xs text-slate-500 mt-1">이 파트 사람이 이 컬렉션의 정책·지식·용어를 수정하게 돼요. 없으면 관리자만.</p>
        </div>
      </FormModal>

      {/* Create Modal */}
      <Modal
        isOpen={showCreate}
        onClose={() => setShowCreate(false)}
        title="새 업무 컬렉션"
      >
        <div className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1.5">
              이름 <span className="text-rose-600 dark:text-rose-400">*</span>
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
              placeholder="업무 컬렉션 설명"
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
            <p className="text-xs text-rose-600 dark:text-rose-400">{String(createMutation.error)}</p>
          )}
        </div>
      </Modal>

      {/* Delete Confirmation Modal */}
      <Modal
        isOpen={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        title="업무 컬렉션 삭제"
      >
        <div className="space-y-4">
          <p className="text-sm text-slate-300">
            <span className="font-semibold text-rose-600 dark:text-rose-400">{deleteTarget?.name}</span> 업무 컬렉션을 삭제하면 안의 데이터가 함께 지워지고 되돌릴 수 없어요.
          </p>
          {deleteTarget && (
            <p className="text-sm text-slate-400 tabular-nums">
              사용 중: 정책 {deleteTarget.policy_count ?? 0} · 지식 문서 {deleteTarget.knowledge_count} · 용어 {deleteTarget.glossary_count}
              {(deleteTarget.category_count ?? 0) > 0 && ` · 업무구분 ${deleteTarget.category_count}`}.
              검토 대기·반려·폐기된 정책과 지식, 질의 기록·대화도 모두 함께 지워져요.
            </p>
          )}
          <div className="flex gap-2 justify-end">
            <Button variant="secondary" size="sm" onClick={() => setDeleteTarget(null)}>
              취소
            </Button>
            <Button
              variant="danger"
              size="sm"
              loading={deleteMutation.isPending}
              onClick={() => deleteTarget && deleteMutation.mutate(deleteTarget.name)}
            >
              삭제
            </Button>
          </div>
          {deleteMutation.isError && (
            <p className="text-xs text-rose-600 dark:text-rose-400">{String(deleteMutation.error)}</p>
          )}
        </div>
      </Modal>
    </div>
  );
}
