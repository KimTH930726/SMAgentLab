import { useState, useRef, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Trash2, Plus, Shield, User as UserIcon, Users, Building2, Pencil, Check, X } from 'lucide-react';
import { Button } from '../ui/Button';
import { Badge } from '../ui/Badge';
import { Modal } from '../ui/Modal';
import { getUsers, updateUser, deleteUser, getAllParts, createPart, deletePart, renamePart } from '../../api/auth';
import { useAuthStore } from '../../store/useAuthStore';
import type { User } from '../../types';

type SubTab = 'users' | 'parts';

export function UserManager() {
  const [subTab, setSubTab] = useState<SubTab>('parts');

  return (
    <div className="max-w-4xl">
      {/* Sub-tab bar */}
      <div className="flex gap-1 mb-6 border-b border-slate-700">
        <button
          onClick={() => setSubTab('parts')}
          className={`flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
            subTab === 'parts'
              ? 'text-indigo-400 border-indigo-500'
              : 'text-slate-400 border-transparent hover:text-slate-200 hover:border-slate-600'
          }`}
        >
          <Building2 className="w-3.5 h-3.5" />
          파트 목록
        </button>
        <button
          onClick={() => setSubTab('users')}
          className={`flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
            subTab === 'users'
              ? 'text-indigo-400 border-indigo-500'
              : 'text-slate-400 border-transparent hover:text-slate-200 hover:border-slate-600'
          }`}
        >
          <Users className="w-3.5 h-3.5" />
          사용자 목록
        </button>
      </div>

      {subTab === 'parts' && <PartSection />}
      {subTab === 'users' && <UserSection />}
    </div>
  );
}

// ── Part (부서) 관리 ────────────────────────────────────────────────────────

function PartSection() {
  const qc = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [createError, setCreateError] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<{ id: number; name: string } | null>(null);
  const [deleteError, setDeleteError] = useState('');
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editingName, setEditingName] = useState('');
  const [renameError, setRenameError] = useState('');
  const editRef = useRef<HTMLInputElement>(null);

  const { data: parts = [], isLoading } = useQuery({
    queryKey: ['parts-all'],
    queryFn: getAllParts,
    refetchOnMount: 'always',
  });

  useEffect(() => {
    if (editingId !== null) editRef.current?.focus();
  }, [editingId]);

  const createMutation = useMutation({
    mutationFn: (name: string) => createPart(name),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['parts-all'] });
      qc.invalidateQueries({ queryKey: ['namespaces'] });
      setShowCreate(false);
      setNewName('');
      setCreateError('');
    },
    onError: (err: Error) => {
      setCreateError(err.message || '파트 생성 실패');
    },
  });

  const renameMutation = useMutation({
    mutationFn: ({ id, name }: { id: number; name: string }) => renamePart(id, name),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['parts-all'] });
      qc.invalidateQueries({ queryKey: ['users'] });
      qc.invalidateQueries({ queryKey: ['namespaces'] });
      qc.invalidateQueries({ queryKey: ['namespaces-detail'] });
      setEditingId(null);
      setRenameError('');
    },
    onError: (err: Error) => {
      setRenameError(err.message || '파트 이름 변경 실패');
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (partId: number) => deletePart(partId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['parts-all'] });
      qc.invalidateQueries({ queryKey: ['users'] });
      setDeleteTarget(null);
      setDeleteError('');
    },
    onError: (err: Error) => {
      setDeleteError(err.message || '파트 삭제 실패');
    },
  });

  const commitRename = (partId: number, oldName: string) => {
    const trimmed = editingName.trim();
    if (!trimmed || trimmed === oldName) { setEditingId(null); return; }
    setRenameError('');
    renameMutation.mutate({ id: partId, name: trimmed });
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-200">파트 관리</h2>
        <Button variant="primary" size="sm" onClick={() => { setShowCreate(true); setNewName(''); setCreateError(''); }}>
          <Plus className="w-4 h-4" />
          새 파트
        </Button>
      </div>

      {isLoading && <div className="text-center py-10 text-slate-500 animate-pulse">로딩 중...</div>}

      <div className="grid gap-3">
        {parts.map((p) => (
          <div
            key={p.id}
            className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden hover:border-slate-600 transition-colors"
          >
            <div className="flex items-center gap-4 p-4">
              {/* 아이콘 */}
              <div className="w-10 h-10 rounded-xl bg-indigo-900/40 border border-indigo-700/40 flex items-center justify-center flex-shrink-0">
                <Building2 className="w-5 h-5 text-indigo-400" />
              </div>

              {/* 파트 이름 + 편집 */}
              <div className="flex-1 min-w-0">
                {editingId === p.id ? (
                  <span className="flex items-center gap-1.5">
                    <input
                      ref={editRef}
                      value={editingName}
                      onChange={(e) => setEditingName(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' && !e.nativeEvent.isComposing) commitRename(p.id, p.name);
                        if (e.key === 'Escape') { setEditingId(null); setRenameError(''); }
                      }}
                      onBlur={() => commitRename(p.id, p.name)}
                      className="bg-slate-900 border border-indigo-500 rounded px-2 py-0.5 text-sm text-slate-100 font-semibold outline-none w-40"
                    />
                    <button onClick={() => commitRename(p.id, p.name)} className="text-emerald-400 hover:text-emerald-300">
                      <Check className="w-3.5 h-3.5" />
                    </button>
                    <button onClick={() => { setEditingId(null); setRenameError(''); }} className="text-slate-500 hover:text-slate-300">
                      <X className="w-3.5 h-3.5" />
                    </button>
                    {renameError && <span className="text-xs text-rose-400">{renameError}</span>}
                  </span>
                ) : (
                  <span className="flex items-center gap-1.5 group">
                    <span className="font-semibold text-slate-200">{p.name}</span>
                    <button
                      onClick={() => { setEditingId(p.id); setEditingName(p.name); }}
                      className="opacity-0 group-hover:opacity-100 text-slate-600 hover:text-indigo-400 transition-all"
                      title="이름 수정"
                    >
                      <Pencil className="w-3 h-3" />
                    </button>
                  </span>
                )}
                <div className="flex items-center gap-2 mt-0.5">
                  <span className="text-xs text-slate-500">
                    생성: {new Date(p.created_at).toLocaleDateString('ko-KR')}
                  </span>
                </div>
              </div>

              {/* 소속 인원 수 + 삭제 */}
              <div className="flex items-center gap-3 flex-shrink-0">
                <div className="flex items-center gap-1.5 bg-slate-700/50 rounded-lg px-3 py-1.5">
                  <Users className="w-3.5 h-3.5 text-slate-400" />
                  <span className="text-sm font-medium text-slate-200">{p.user_count ?? 0}</span>
                  <span className="text-xs text-slate-500">명</span>
                </div>
                <Button
                  variant="danger"
                  size="sm"
                  onClick={() => { setDeleteTarget({ id: p.id, name: p.name }); setDeleteError(''); }}
                >
                  <Trash2 className="w-4 h-4" />
                </Button>
              </div>
            </div>
          </div>
        ))}
        {!isLoading && parts.length === 0 && (
          <div className="text-center py-10 text-slate-500">등록된 파트 없음</div>
        )}
      </div>

      {/* 파트 생성 모달 */}
      <Modal isOpen={showCreate} onClose={() => setShowCreate(false)} title="새 파트 생성">
        <div className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1.5">
              파트 이름 <span className="text-rose-400">*</span>
            </label>
            <input
              type="text"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && !e.nativeEvent.isComposing && createMutation.mutate(newName.trim())}
              placeholder="예: 딜리버스, 인프라, 지원"
              autoFocus
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
            />
          </div>
          {createError && <p className="text-xs text-rose-400">{createError}</p>}
          <div className="flex gap-2 justify-end">
            <Button variant="secondary" size="sm" onClick={() => setShowCreate(false)}>취소</Button>
            <Button
              variant="primary"
              size="sm"
              loading={createMutation.isPending}
              onClick={() => createMutation.mutate(newName.trim())}
              disabled={!newName.trim()}
            >
              생성
            </Button>
          </div>
        </div>
      </Modal>

      {/* 파트 삭제 확인 모달 */}
      <Modal isOpen={!!deleteTarget} onClose={() => { setDeleteTarget(null); setDeleteError(''); }} title="파트 삭제">
        <div className="space-y-4">
          {deleteTarget && (() => {
            const part = parts.find((p) => p.id === deleteTarget.id);
            const count = part?.user_count ?? 0;
            return count > 0 ? (
              <div className="space-y-3">
                <div className="flex items-start gap-3 bg-amber-900/20 border border-amber-700/40 rounded-lg p-3">
                  <Users className="w-5 h-5 text-amber-400 flex-shrink-0 mt-0.5" />
                  <p className="text-sm text-amber-300">
                    <span className="font-semibold">{deleteTarget.name}</span> 파트에 소속된 사용자가{' '}
                    <span className="font-semibold">{count}명</span> 있습니다.
                    <br />사용자 목록 탭에서 다른 파트로 이동한 후 삭제하세요.
                  </p>
                </div>
                <div className="flex justify-end">
                  <Button variant="secondary" size="sm" onClick={() => setDeleteTarget(null)}>확인</Button>
                </div>
              </div>
            ) : (
              <div className="space-y-3">
                <p className="text-sm text-slate-300">
                  <span className="font-semibold text-rose-400">{deleteTarget.name}</span> 파트를 삭제하시겠습니까?
                  <br />소속 사용자가 없으며, 이 작업은 되돌릴 수 없습니다.
                </p>
                {deleteError && <p className="text-xs text-rose-400">{deleteError}</p>}
                <div className="flex gap-2 justify-end">
                  <Button variant="secondary" size="sm" onClick={() => setDeleteTarget(null)}>취소</Button>
                  <Button
                    variant="danger"
                    size="sm"
                    loading={deleteMutation.isPending}
                    onClick={() => deleteMutation.mutate(deleteTarget.id)}
                  >
                    삭제
                  </Button>
                </div>
              </div>
            );
          })()}
        </div>
      </Modal>
    </div>
  );
}

// ── 사용자 관리 ─────────────────────────────────────────────────────────────

function UserSection() {
  const qc = useQueryClient();
  const currentUser = useAuthStore((s) => s.user);
  const [filterPart, setFilterPart] = useState<string>('');

  const { data: users = [], isLoading } = useQuery({
    queryKey: ['users'],
    queryFn: getUsers,
  });

  const { data: parts = [] } = useQuery({
    queryKey: ['parts-all'],
    queryFn: getAllParts,
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: Record<string, unknown> }) => updateUser(id, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['users'] });
    },
    onError: (err: Error) => {
      alert(err.message || '변경 실패');
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteUser(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['users'] });
    },
    onError: (err: Error) => {
      alert(err.message || '사용자 삭제 실패');
    },
  });

  const handleToggleRole = (u: User) => {
    updateMutation.mutate({ id: u.id, payload: { role: u.role === 'admin' ? 'user' : 'admin' } });
  };

  const handleToggleActive = (u: User) => {
    updateMutation.mutate({ id: u.id, payload: { is_active: !u.is_active } });
  };

  const handleChangePart = (u: User, part: string) => {
    updateMutation.mutate({ id: u.id, payload: { part } });
  };

  const handleDelete = (u: User) => {
    if (!confirm(`'${u.username}' 사용자를 삭제하시겠습니까?`)) return;
    deleteMutation.mutate(u.id);
  };

  if (isLoading) {
    return (
      <div>
        <h3 className="text-lg font-semibold text-slate-100 mb-4">사용자 목록</h3>
        <p className="text-sm text-slate-500 animate-pulse">로딩 중...</p>
      </div>
    );
  }

  const filteredUsers = filterPart ? users.filter((u) => u.part === filterPart) : users;

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-lg font-semibold text-slate-100">
          사용자 목록 <span className="text-sm text-slate-500 font-normal">({filteredUsers.length}/{users.length}명)</span>
        </h3>
      </div>
      {/* 파트 필터 */}
      <div className="flex items-center gap-2 flex-wrap mb-3">
        <button
          onClick={() => setFilterPart('')}
          className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
            filterPart === '' ? 'bg-indigo-600 text-white' : 'bg-slate-700 text-slate-400 hover:bg-slate-600 hover:text-slate-200'
          }`}
        >
          전체
        </button>
        {parts.map((p) => (
          <button
            key={p.id}
            onClick={() => setFilterPart(filterPart === p.name ? '' : p.name)}
            className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
              filterPart === p.name ? 'bg-indigo-600 text-white' : 'bg-slate-700 text-slate-400 hover:bg-slate-600 hover:text-slate-200'
            }`}
          >
            {p.name} <span className="opacity-70">({users.filter((u) => u.part === p.name).length})</span>
          </button>
        ))}
      </div>
      <div className="bg-slate-800 rounded-xl border border-slate-700 overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-700 text-slate-400">
              <th className="text-left px-4 py-3 font-medium">사용자</th>
              <th className="text-left px-4 py-3 font-medium">파트</th>
              <th className="text-left px-4 py-3 font-medium">역할</th>
              <th className="text-left px-4 py-3 font-medium">API Key</th>
              <th className="text-left px-4 py-3 font-medium">상태</th>
              <th className="text-right px-4 py-3 font-medium">관리</th>
            </tr>
          </thead>
          <tbody>
            {filteredUsers.map((u) => (
              <tr key={u.id} className="border-b border-slate-700/50 hover:bg-slate-800/50">
                <td className="px-4 py-3">
                  <div className="flex items-center gap-2">
                    {u.role === 'admin' ? (
                      <Shield className="w-4 h-4 text-indigo-400" />
                    ) : (
                      <UserIcon className="w-4 h-4 text-slate-500" />
                    )}
                    <span className="text-slate-200">{u.username}</span>
                  </div>
                </td>
                <td className="px-4 py-3">
                  <select
                    value={u.part}
                    onChange={(e) => handleChangePart(u, e.target.value)}
                    title="파트를 변경하려면 선택하세요"
                    className="bg-slate-900 border border-slate-600 rounded px-2 py-1 text-xs text-slate-200 focus:outline-none focus:border-indigo-500 cursor-pointer hover:border-slate-400 transition-colors"
                  >
                    {parts.map((p) => (
                      <option key={p.id} value={p.name}>{p.name}</option>
                    ))}
                  </select>
                </td>
                <td className="px-4 py-3">
                  {u.id === currentUser?.id ? (
                    <span
                      title="현재 로그인한 계정의 역할은 변경할 수 없습니다"
                      className="inline-block cursor-not-allowed opacity-80 hover:opacity-100 transition-opacity duration-150"
                    >
                      <Badge color="indigo">슈퍼어드민</Badge>
                    </span>
                  ) : (
                    <span
                      role="button"
                      tabIndex={0}
                      onClick={() => handleToggleRole(u)}
                      onKeyDown={(e) => e.key === 'Enter' && handleToggleRole(u)}
                      title={`클릭하여 ${u.role === 'admin' ? '일반 사용자' : '슈퍼어드민'}로 변경`}
                      className="inline-block cursor-pointer transform hover:scale-110 active:scale-95 transition-transform duration-150"
                    >
                      <Badge color={u.role === 'admin' ? 'indigo' : 'slate'}>
                        {u.role === 'admin' ? '슈퍼어드민' : u.role}
                      </Badge>
                    </span>
                  )}
                </td>
                <td className="px-4 py-3">
                  <Badge color={u.has_api_key ? 'emerald' : 'slate'}>
                    {u.has_api_key ? '있음' : '없음'}
                  </Badge>
                </td>
                <td className="px-4 py-3">
                  {u.id === currentUser?.id ? (
                    <span
                      title="현재 로그인한 계정의 상태는 변경할 수 없습니다"
                      className="inline-block cursor-not-allowed opacity-80 hover:opacity-100 transition-opacity duration-150"
                    >
                      <Badge color="emerald">활성</Badge>
                    </span>
                  ) : (
                    <span
                      role="button"
                      tabIndex={0}
                      onClick={() => handleToggleActive(u)}
                      onKeyDown={(e) => e.key === 'Enter' && handleToggleActive(u)}
                      title={`클릭하여 ${u.is_active ? '비활성화' : '활성화'}`}
                      className="inline-block cursor-pointer transform hover:scale-110 active:scale-95 transition-transform duration-150"
                    >
                      <Badge color={u.is_active ? 'emerald' : 'rose'}>
                        {u.is_active ? '활성' : '비활성'}
                      </Badge>
                    </span>
                  )}
                </td>
                <td className="px-4 py-3 text-right">
                  {u.id !== currentUser?.id && (
                    <button
                      onClick={() => handleDelete(u)}
                      className="text-slate-500 hover:text-rose-400 hover:scale-110 active:scale-95 transition-all p-1 cursor-pointer"
                      title="사용자 삭제"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
