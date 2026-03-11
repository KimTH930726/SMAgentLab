import { useState, useRef, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Trash2, Plus, Shield, User as UserIcon, Users, Building2, Pencil, Check, X } from 'lucide-react';
import { Button } from '../ui/Button';
import { Badge } from '../ui/Badge';
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
          파트 관리
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
  const [newName, setNewName] = useState('');
  const [error, setError] = useState('');
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editingName, setEditingName] = useState('');
  const editRef = useRef<HTMLInputElement>(null);

  const { data: parts = [] } = useQuery({
    queryKey: ['parts-all'],
    queryFn: getAllParts,
  });

  useEffect(() => {
    if (editingId !== null) editRef.current?.focus();
  }, [editingId]);

  const createMutation = useMutation({
    mutationFn: (name: string) => createPart(name),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['parts-all'] });
      qc.invalidateQueries({ queryKey: ['namespaces'] });
      setNewName('');
      setError('');
    },
    onError: (err: Error) => {
      setError(err.message || '파트 생성 실패');
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
    },
    onError: (err: Error) => {
      alert(err.message || '파트 이름 변경 실패');
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (partId: number) => deletePart(partId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['parts-all'] });
      qc.invalidateQueries({ queryKey: ['users'] });
    },
    onError: (err: Error) => {
      alert(err.message || '파트 삭제 실패');
    },
  });

  const handleCreate = () => {
    if (!newName.trim() || createMutation.isPending) return;
    setError('');
    createMutation.mutate(newName.trim());
  };

  const commitRename = (partId: number, oldName: string) => {
    const trimmed = editingName.trim();
    if (!trimmed || trimmed === oldName) { setEditingId(null); return; }
    renameMutation.mutate({ id: partId, name: trimmed });
  };

  const handleDelete = (partId: number) => {
    if (!confirm('이 파트를 삭제하시겠습니까?')) return;
    deleteMutation.mutate(partId);
  };

  return (
    <div>
      <h3 className="text-lg font-semibold text-slate-100 mb-4">파트 (부서) 관리</h3>
      <div className="bg-slate-800 rounded-xl border border-slate-700 p-4">
        <div className="flex gap-2 mb-4">
          <input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && !e.nativeEvent.isComposing && handleCreate()}
            placeholder="새 파트 이름"
            className="flex-1 bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
          />
          <Button onClick={handleCreate} loading={createMutation.isPending} size="sm">
            <Plus className="w-4 h-4" />
            추가
          </Button>
        </div>
        {error && <p className="text-sm text-rose-400 mb-3">{error}</p>}

        <div className="flex flex-wrap gap-2">
          {parts.map((p) => (
            <div
              key={p.id}
              className="flex items-center gap-1.5 bg-slate-900 border border-slate-600 rounded-lg px-3 py-1.5"
            >
              {editingId === p.id ? (
                <>
                  <input
                    ref={editRef}
                    value={editingName}
                    onChange={(e) => setEditingName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && !e.nativeEvent.isComposing) commitRename(p.id, p.name);
                      if (e.key === 'Escape') setEditingId(null);
                    }}
                    onBlur={() => commitRename(p.id, p.name)}
                    className="bg-slate-800 border border-indigo-500 rounded px-1.5 py-0.5 text-xs text-slate-100 outline-none w-24"
                  />
                  <button onClick={() => commitRename(p.id, p.name)} className="text-emerald-400 hover:text-emerald-300">
                    <Check className="w-3.5 h-3.5" />
                  </button>
                  <button onClick={() => setEditingId(null)} className="text-slate-500 hover:text-slate-300">
                    <X className="w-3.5 h-3.5" />
                  </button>
                </>
              ) : (
                <>
                  <button
                    onClick={() => { setEditingId(p.id); setEditingName(p.name); }}
                    className="text-sm text-slate-200 hover:text-indigo-300 transition-colors"
                    title="클릭하여 이름 수정"
                  >
                    {p.name}
                  </button>
                  <button
                    onClick={() => { setEditingId(p.id); setEditingName(p.name); }}
                    className="text-slate-600 hover:text-indigo-400 transition-colors"
                    title="이름 수정"
                  >
                    <Pencil className="w-3 h-3" />
                  </button>
                  <button
                    onClick={() => handleDelete(p.id)}
                    className="text-slate-500 hover:text-rose-400 transition-colors"
                    title="파트 삭제"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </>
              )}
            </div>
          ))}
          {parts.length === 0 && <p className="text-sm text-slate-500">등록된 파트 없음</p>}
        </div>
      </div>
    </div>
  );
}

// ── 사용자 관리 ─────────────────────────────────────────────────────────────

function UserSection() {
  const qc = useQueryClient();
  const currentUser = useAuthStore((s) => s.user);

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

  return (
    <div>
      <h3 className="text-lg font-semibold text-slate-100 mb-4">
        사용자 목록 <span className="text-sm text-slate-500 font-normal">({users.length}명)</span>
      </h3>
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
            {users.map((u) => (
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
