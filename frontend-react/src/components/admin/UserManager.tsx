import { useState, useEffect, useCallback } from 'react';
import { Trash2, Plus, Shield, User as UserIcon } from 'lucide-react';
import { Button } from '../ui/Button';
import { Badge } from '../ui/Badge';
import { getUsers, updateUser, deleteUser, getParts, createPart, deletePart } from '../../api/auth';
import { useAuthStore } from '../../store/useAuthStore';
import type { User, Part } from '../../types';

export function UserManager() {
  return (
    <div className="space-y-8">
      <PartSection />
      <UserSection />
    </div>
  );
}

// ── Part (부서) 관리 ────────────────────────────────────────────────────────

function PartSection() {
  const [parts, setParts] = useState<Part[]>([]);
  const [newName, setNewName] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      const data = await getParts();
      setParts(data);
    } catch (err) {
      console.error(err);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleCreate = async () => {
    if (!newName.trim() || loading) return;
    setLoading(true);
    setError('');
    const name = newName.trim();
    setNewName('');
    try {
      await createPart(name);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : '파트 생성 실패');
      setNewName(name);
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async (partId: number) => {
    if (!confirm('이 파트를 삭제하시겠습니까?')) return;
    try {
      await deletePart(partId);
      await load();
    } catch (err) {
      alert(err instanceof Error ? err.message : '파트 삭제 실패');
    }
  };

  return (
    <div>
      <h3 className="text-lg font-semibold text-slate-100 mb-4">파트 (부서) 관리</h3>
      <div className="bg-[#1E293B] rounded-xl border border-slate-700 p-4">
        <div className="flex gap-2 mb-4">
          <input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && !e.nativeEvent.isComposing && handleCreate()}
            placeholder="새 파트 이름"
            className="flex-1 bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
          />
          <Button onClick={handleCreate} loading={loading} size="sm">
            <Plus className="w-4 h-4" />
            추가
          </Button>
        </div>
        {error && <p className="text-sm text-rose-400 mb-3">{error}</p>}

        <div className="flex flex-wrap gap-2">
          {parts.map((p) => (
            <div
              key={p.id}
              className="flex items-center gap-2 bg-slate-800 border border-slate-600 rounded-lg px-3 py-1.5"
            >
              <span className="text-sm text-slate-200">{p.name}</span>
              <button
                onClick={() => handleDelete(p.id)}
                className="text-slate-500 hover:text-rose-400 transition-colors"
                title="파트 삭제"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
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
  const currentUser = useAuthStore((s) => s.user);
  const [users, setUsers] = useState<User[]>([]);
  const [parts, setParts] = useState<Part[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [u, p] = await Promise.all([getUsers(), getParts()]);
      setUsers(u);
      setParts(p);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleToggleRole = async (u: User) => {
    const newRole = u.role === 'admin' ? 'user' : 'admin';
    try {
      await updateUser(u.id, { role: newRole });
      await load();
    } catch (err) {
      alert(err instanceof Error ? err.message : '역할 변경 실패');
    }
  };

  const handleToggleActive = async (u: User) => {
    try {
      await updateUser(u.id, { is_active: !u.is_active });
      await load();
    } catch (err) {
      alert(err instanceof Error ? err.message : '상태 변경 실패');
    }
  };

  const handleChangePart = async (u: User, part: string) => {
    try {
      await updateUser(u.id, { part });
      await load();
    } catch (err) {
      alert(err instanceof Error ? err.message : '파트 변경 실패');
    }
  };

  const handleDelete = async (u: User) => {
    if (!confirm(`'${u.username}' 사용자를 삭제하시겠습니까?`)) return;
    try {
      await deleteUser(u.id);
      await load();
    } catch (err) {
      alert(err instanceof Error ? err.message : '사용자 삭제 실패');
    }
  };

  if (loading) {
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
      <div className="bg-[#1E293B] rounded-xl border border-slate-700 overflow-hidden">
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
                    className="bg-slate-900 border border-slate-600 rounded px-2 py-1 text-xs text-slate-200 focus:outline-none focus:border-indigo-500"
                  >
                    {parts.map((p) => (
                      <option key={p.id} value={p.name}>{p.name}</option>
                    ))}
                  </select>
                </td>
                <td className="px-4 py-3">
                  {u.id === currentUser?.id ? (
                    <Badge color="indigo">admin</Badge>
                  ) : (
                    <button onClick={() => handleToggleRole(u)}>
                      <Badge color={u.role === 'admin' ? 'indigo' : 'slate'}>
                        {u.role}
                      </Badge>
                    </button>
                  )}
                </td>
                <td className="px-4 py-3">
                  <Badge color={u.has_api_key ? 'emerald' : 'slate'}>
                    {u.has_api_key ? '있음' : '없음'}
                  </Badge>
                </td>
                <td className="px-4 py-3">
                  {u.id === currentUser?.id ? (
                    <Badge color="emerald">활성</Badge>
                  ) : (
                    <button onClick={() => handleToggleActive(u)}>
                      <Badge color={u.is_active ? 'emerald' : 'rose'}>
                        {u.is_active ? '활성' : '비활성'}
                      </Badge>
                    </button>
                  )}
                </td>
                <td className="px-4 py-3 text-right">
                  {u.id !== currentUser?.id && (
                    <button
                      onClick={() => handleDelete(u)}
                      className="text-slate-500 hover:text-rose-400 transition-colors p-1"
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
