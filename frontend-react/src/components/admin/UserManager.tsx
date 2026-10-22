import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Trash2, Plus, Shield, User as UserIcon, Users, Building2, Pencil } from 'lucide-react';
import { Button } from '../ui/Button';
import { Badge } from '../ui/Badge';
import { Modal } from '../ui/Modal';
import { getUsers, updateUser, deleteUser, getAllParts, createPart, deletePart, renamePart, getTeams, createTeam, renameTeam, deleteTeam, setPartTeam } from '../../api/auth';
import { useAuthStore } from '../../store/useAuthStore';
import type { Part, Team, User } from '../../types';
import { showConfirm, showError } from '../../store/useDialogStore';
import { FormModal } from '../ui/FormModal';
import { PartTreeModal } from './PartTreeModal';

// ── Part (부서) 관리 ────────────────────────────────────────────────────────

/** 조직 > 팀·파트 */
export function PartSection() {
  const qc = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [createError, setCreateError] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<{ id: number; name: string } | null>(null);
  const [deleteError, setDeleteError] = useState('');
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editingName, setEditingName] = useState('');
  const [renameError, setRenameError] = useState('');
  const [treePart, setTreePart] = useState<Part | null>(null);   // 파트 현황 트리 모달
  const [createTeamId, setCreateTeamId] = useState<number | null>(null);   // 새 파트를 만들 팀(팀 아래에서만 만든다)
  const [teamModal, setTeamModal] = useState<{ mode: 'create' } | { mode: 'rename'; team: Team } | null>(null);
  const [teamName, setTeamName] = useState('');
  const [teamError, setTeamError] = useState('');
  const { data: teams = [] } = useQuery({ queryKey: ['teams'], queryFn: getTeams, refetchOnMount: 'always' });
  const invalidateOrg = () => {
    qc.invalidateQueries({ queryKey: ['teams'] });
    qc.invalidateQueries({ queryKey: ['parts-all'] });
    qc.invalidateQueries({ queryKey: ['parts-public'] });   // 사이드바 고르는 창의 "우리 팀" 묶음
    // 팀·파트마다 조직 컬렉션이 따라 생기고·지워지고·이름이 바뀐다(#83) — 고르는 창·컬렉션 목록·권한 판단이 옛 값으로 남지 않게(리뷰)
    qc.invalidateQueries({ queryKey: ['namespaces'] });
    qc.invalidateQueries({ queryKey: ['namespaces-detail'] });
  };
  const teamSaveMutation = useMutation({
    mutationFn: (v: { mode: 'create' } | { mode: 'rename'; team: Team }) =>
      v.mode === 'create' ? createTeam(teamName.trim()) : renameTeam(v.team.id, teamName.trim()),
    onSuccess: () => { invalidateOrg(); setTeamModal(null); setTeamError(''); },
    onError: (err: Error) => setTeamError(err.message || '팀 저장 실패'),
  });
  const teamDeleteMutation = useMutation({
    mutationFn: (t: Team) => deleteTeam(t.id),
    onSuccess: invalidateOrg,
    onError: (err: Error) => { void showError(err, '팀 삭제 실패'); },
  });
  const moveMutation = useMutation({
    mutationFn: ({ part, teamId }: { part: Part; teamId: number }) => setPartTeam(part.id, teamId),
    onSuccess: invalidateOrg,
    onError: (err: Error) => { void showError(err, '파트 옮기기 실패'); },
  });
  const askDeleteTeam = async (t: Team) => {
    if (teamDeleteMutation.isPending) return;
    if (!(await showConfirm(`'${t.name}' 팀을 삭제할까요?`, { tone: 'warning', confirmLabel: '삭제', title: '팀 삭제' }))) return;
    teamDeleteMutation.mutate(t);
  };
  const askMove = async (p: Part, teamId: number) => {
    const to = teams.find((t) => t.id === teamId);
    if (!to || teamId === p.team_id || moveMutation.isPending) return;
    if (!(await showConfirm(`'${p.name}'을(를) '${to.name}'(으)로 옮길까요? 사람·담당 업무는 그대로 따라가요.`, { confirmLabel: '옮기기', title: '파트 옮기기' }))) return;
    moveMutation.mutate({ part: p, teamId });
  };

  const { data: parts = [], isLoading } = useQuery({
    queryKey: ['parts-all'],
    queryFn: getAllParts,
    refetchOnMount: 'always',
  });


  const createMutation = useMutation({
    mutationFn: (name: string) => createPart(name, createTeamId ?? undefined),
    onSuccess: () => {
      invalidateOrg();
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
      qc.invalidateQueries({ queryKey: ['parts-public'] });   // 사이드바 고르는 창이 파트 이름으로 팀을 찾는다(리뷰 L3)
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
      invalidateOrg();   // 팀별 파트 수 · 지워진 파트의 조직 컬렉션(#83)
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
    if (renameMutation.isPending) return;   // 모달 저장 연타 방지
    setRenameError('');
    renameMutation.mutate({ id: partId, name: trimmed });
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-200" title="팀 아래에 파트 — 파트를 누르면 사람·담당 업무 현황">팀 › 파트</h2>
        <Button variant="primary" size="sm" onClick={() => { setTeamModal({ mode: 'create' }); setTeamName(''); setTeamError(''); }}>
          <Plus className="w-4 h-4" />
          팀
        </Button>
      </div>

      {isLoading && <div className="text-center py-10 text-slate-500 animate-pulse">로딩 중...</div>}

      {(() => {
        const partCard = (p: Part) => (
            <div
              key={p.id}
              className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden hover:border-slate-600 transition-colors"
            >
              <div className="flex items-center gap-4 p-4">
                {/* 아이콘 */}
                <div className="w-10 h-10 rounded-xl bg-indigo-100 border border-indigo-200 dark:bg-indigo-900/40 dark:border-indigo-700/40 flex items-center justify-center flex-shrink-0">
                  <Building2 className="w-5 h-5 text-indigo-600 dark:text-indigo-400" />
                </div>
  
                {/* 파트 이름 + 편집 */}
                <div className="flex-1 min-w-0">
                  {/* 이름 = 현황 트리 모달, 연필 = 이름 바꾸기 모달(10/21 — 그 자리에서 고치던 칸은 포커스가 빠지면 저장됐다) */}
                  <span className="flex items-center gap-1.5 group">
                    <button onClick={() => setTreePart(p)} title="사람·담당 업무 현황 보기"
                      className="font-semibold text-slate-200 hover:text-indigo-600 dark:hover:text-indigo-300 hover:underline">
                      {p.name}
                    </button>
                    <button
                      onClick={() => { setEditingId(p.id); setEditingName(p.name); setRenameError(''); }}
                      className="opacity-0 group-hover:opacity-100 focus:opacity-100 text-slate-600 hover:text-indigo-600 dark:hover:text-indigo-400 transition-all"
                      title="이름 바꾸기"
                    >
                      <Pencil className="w-3 h-3" />
                    </button>
                  </span>
                  <div className="flex items-center gap-2 mt-0.5">
                    <span className="text-xs text-slate-500">사람 {p.user_count ?? 0}명</span>
                    {(teams.length > 1 || !p.team_id) && (
                      <select aria-label={`${p.name} 팀 옮기기`} value={p.team_id ?? ''} onChange={(e) => void askMove(p, Number(e.target.value))}
                        className="text-xs bg-slate-900 border border-slate-600 rounded px-1.5 py-0.5 text-slate-300">
                        {!p.team_id && <option value="">팀 고르기</option>}
                        {teams.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                      </select>
                    )}
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
          );
        const noTeam = parts.filter((p) => !p.team_id);
        return (
          <div className="space-y-4">
            {teams.map((t) => {
              const tp = parts.filter((p) => p.team_id === t.id);
              return (
                <section key={t.id} className="space-y-2" aria-label={`${t.name} 팀`}>
                  <div className="flex items-center gap-2 px-1">
                    <Users className="w-4 h-4 text-indigo-600 dark:text-indigo-400" aria-hidden="true" />
                    <span className="font-semibold text-slate-100">{t.name}</span>
                    <span className="text-xs text-slate-500">파트 {tp.length}</span>
                    <button onClick={() => { setTeamModal({ mode: 'rename', team: t }); setTeamName(t.name); setTeamError(''); }}
                      className="text-slate-500 hover:text-indigo-600 dark:hover:text-indigo-400" title="팀 이름 바꾸기" aria-label={`${t.name} 이름 바꾸기`}>
                      <Pencil className="w-3 h-3" />
                    </button>
                    <span className="flex-1" />
                    <Button variant="secondary" size="sm" onClick={() => { setCreateTeamId(t.id); setShowCreate(true); setNewName(''); setCreateError(''); }}>
                      <Plus className="w-3.5 h-3.5" /> 파트
                    </Button>
                    <Button variant="danger" size="sm" onClick={() => void askDeleteTeam(t)} title={tp.length ? '파트를 먼저 다른 팀으로 옮겨야 삭제돼요' : '팀 삭제'}
                      aria-label={`${t.name} 팀 삭제`}>
                      <Trash2 className="w-4 h-4" />
                    </Button>
                  </div>
                  <div className="grid gap-2 pl-6 border-l border-slate-700 ml-2">
                    {tp.map(partCard)}
                    {tp.length === 0 && <div className="text-sm text-slate-500 py-2">파트 없음</div>}
                  </div>
                </section>
              );
            })}
            {noTeam.length > 0 && (
              <section className="space-y-2" aria-label="팀 없는 파트">
                <div className="px-1 text-sm text-amber-700 dark:text-amber-300" title="파트 카드의 팀 칸에서 팀을 고르면 옮겨져요">팀 없는 파트 — 팀으로 옮겨 주세요</div>
                <div className="grid gap-2 pl-6 border-l border-slate-700 ml-2">{noTeam.map(partCard)}</div>
              </section>
            )}
            {!isLoading && parts.length === 0 && teams.length === 0 && (
              <div className="text-center py-10 text-slate-500">팀을 먼저 만들어 주세요</div>
            )}
          </div>
        );
      })()}

      {/* 파트 생성 모달 */}
      <PartTreeModal part={treePart} onClose={() => setTreePart(null)} />
      <FormModal
        isOpen={teamModal !== null}
        title={teamModal?.mode === 'rename' ? '팀 이름 바꾸기' : '새 팀'}
        onClose={() => { setTeamModal(null); setTeamError(''); }}
        onSubmit={() => { if (teamModal && teamName.trim() && !teamSaveMutation.isPending) teamSaveMutation.mutate(teamModal); }}
        submitting={teamSaveMutation.isPending}
        submitDisabled={!teamName.trim() || (teamModal?.mode === 'rename' && teamName.trim() === teamModal.team.name)}
        error={teamError}
      >
        <label htmlFor="team-name" className="block text-xs text-slate-400">팀 이름 — 파트·사람 연결은 그대로 유지돼요</label>
        <input id="team-name" autoFocus value={teamName} onChange={(e) => setTeamName(e.target.value)}
          className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500" />
      </FormModal>
      <FormModal
        isOpen={editingId !== null}
        title="파트 이름 바꾸기"
        onClose={() => { setEditingId(null); setRenameError(''); }}
        onSubmit={() => {
          const old = parts.find((x) => x.id === editingId);
          if (old) commitRename(old.id, old.name);
        }}
        submitting={renameMutation.isPending}
        submitDisabled={!editingName.trim()}
        error={renameError}
      >
        <label htmlFor="part-rename" className="block text-xs text-slate-400">새 이름 — 사람·담당 업무 연결은 그대로 유지돼요</label>
        <input id="part-rename" autoFocus value={editingName} onChange={(e) => setEditingName(e.target.value)}
          className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500" />
      </FormModal>
      <Modal isOpen={showCreate} onClose={() => setShowCreate(false)} title={`새 파트 — ${teams.find((t) => t.id === createTeamId)?.name ?? ''}`}>
        <div className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1.5">
              파트 이름 <span className="text-rose-600 dark:text-rose-400">*</span>
            </label>
            <input
              type="text"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && !e.nativeEvent.isComposing && newName.trim() && !createMutation.isPending && createMutation.mutate(newName.trim())}
              placeholder="예: 딜리버스, 인프라, 지원"
              autoFocus
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
            />
          </div>
          {createError && <p className="text-xs text-rose-600 dark:text-rose-400">{createError}</p>}
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
                <div className="flex items-start gap-3 bg-amber-50 border border-amber-200 dark:bg-amber-900/20 dark:border-amber-700/40 rounded-lg p-3">
                  <Users className="w-5 h-5 text-amber-600 dark:text-amber-400 flex-shrink-0 mt-0.5" />
                  <p className="text-sm text-amber-700 dark:text-amber-300">
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
                  <span className="font-semibold text-rose-600 dark:text-rose-400">{deleteTarget.name}</span> 파트를 삭제하시겠습니까?
                  <br />소속 사용자가 없으며, 이 작업은 되돌릴 수 없습니다.
                </p>
                {deleteError && <p className="text-xs text-rose-600 dark:text-rose-400">{deleteError}</p>}
                <div className="flex gap-2 justify-end">
                  <Button variant="secondary" size="sm" onClick={() => setDeleteTarget(null)}>취소</Button>
                  <Button
                    variant="danger"
                    size="sm"
                    loading={deleteMutation.isPending}
                    disabled={deleteMutation.isPending}
                    onClick={() => !deleteMutation.isPending && deleteMutation.mutate(deleteTarget.id)}
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

/** 조직 > 사용자 */
export function UserSection() {
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
      void showError(err, '변경 실패');
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteUser(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['users'] });
    },
    onError: (err: Error) => {
      void showError(err, '사용자 삭제 실패');
    },
  });

  const handleToggleRole = async (u: User) => {
    const toAdmin = u.role !== 'admin';
    const msg = toAdmin
      ? `'${u.username}'을(를) 슈퍼어드민으로 바꿀까요? 모든 업무 컬렉션·사용자·시스템 설정을 바꿀 수 있게 됩니다.`
      : `'${u.username}'의 슈퍼어드민 권한을 해제하고 일반 사용자로 바꿀까요?`;
    if (!(await showConfirm(msg, { tone: toAdmin ? 'danger' : 'warning', confirmLabel: '변경' }))) return;
    updateMutation.mutate({ id: u.id, payload: { role: toAdmin ? 'admin' : 'user' } });
  };

  const handleApprove = async (u: User) => {
    if (!(await showConfirm(`'${u.username}'을(를) 일반 사용자로 승인할까요? 소속 파트가 담당하는 업무 컬렉션을 수정할 수 있게 됩니다.`, { confirmLabel: '승인' }))) return;
    updateMutation.mutate({ id: u.id, payload: { role: 'user' } });
  };

  // 사용자 수정(소속 파트·상태) — 모달(10/21). 예전엔 표의 선택칸을 바꾸는 순간·배지를 누르는 순간 저장됐다
  const [editUser, setEditUser] = useState<User | null>(null);
  const [editForm, setEditForm] = useState<{ part: string; is_active: boolean }>({ part: '', is_active: true });
  const openEdit = (u: User) => { editMutation.reset(); setEditUser(u); setEditForm({ part: u.part ?? '', is_active: u.is_active }); };
  const editMutation = useMutation({
    mutationFn: () => {
      if (!editUser) throw new Error('사용자를 고르세요.');
      const payload: Record<string, unknown> = {};
      if (editForm.part && editForm.part !== (editUser.part ?? '')) payload.part = editForm.part;
      if (editForm.is_active !== editUser.is_active) payload.is_active = editForm.is_active;
      return updateUser(editUser.id, payload);
    },
    onSuccess: (updated) => {
      // 내 소속을 바꿨으면 로그인 정보도 맞춘다 — 안 그러면 새로고침 전까지 사이드바가 옛 파트 기준(10/21 리뷰)
      const me = useAuthStore.getState().user;
      if (me && updated.id === me.id) useAuthStore.getState().updateUser({ ...me, part: updated.part });
      qc.invalidateQueries({ queryKey: ['users'] }); qc.invalidateQueries({ queryKey: ['parts-all'] }); setEditUser(null);
    },
    onError: () => { /* 모달 안에 표시 */ },
  });
  const editChanged = !!editUser && ((!!editForm.part && editForm.part !== (editUser.part ?? '')) || editForm.is_active !== editUser.is_active);

  const handleDelete = async (u: User) => {
    if (!(await showConfirm(`'${u.username}' 사용자를 삭제하시겠습니까?`, { tone: 'danger', confirmLabel: '삭제' }))) return;
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

  const NO_PART = '__none__';
  const filteredUsers = filterPart ? users.filter((u) => (filterPart === NO_PART ? !u.part : u.part === filterPart)) : users;

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
        {users.some((u) => !u.part) && (
          <button
            onClick={() => setFilterPart(filterPart === NO_PART ? '' : NO_PART)}
            className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
              filterPart === NO_PART ? 'bg-indigo-600 text-white' : 'bg-slate-700 text-slate-400 hover:bg-slate-600 hover:text-slate-200'
            }`}
          >
            소속 없음 <span className="opacity-70">({users.filter((u) => !u.part).length})</span>
          </button>
        )}
      </div>
      <div className="bg-slate-800 rounded-xl border border-slate-700 overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-700 text-slate-400">
              <th className="text-left px-4 py-3 font-medium">사용자</th>
              <th className="text-left px-4 py-3 font-medium">파트</th>
              <th className="text-left px-4 py-3 font-medium">역할</th>
              <th className="text-left px-4 py-3 font-medium">LLM 자격증명</th>
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
                      <Shield className="w-4 h-4 text-indigo-600 dark:text-indigo-400" />
                    ) : (
                      <UserIcon className="w-4 h-4 text-slate-500" />
                    )}
                    <span className="text-slate-200 truncate max-w-[14rem]" title={u.username}>{u.username}</span>
                  </div>
                </td>
                <td className="px-4 py-3">
                  <span className={u.part ? 'text-slate-300' : 'text-slate-500'}>{u.part ?? '소속 없음'}</span>
                </td>
                <td className="px-4 py-3">
                  {u.id === currentUser?.id ? (
                    <span
                      title="현재 로그인한 계정의 역할은 변경할 수 없습니다"
                      className="inline-block cursor-not-allowed opacity-80 hover:opacity-100 transition-opacity duration-150"
                    >
                      <Badge color="indigo">슈퍼어드민</Badge>
                    </span>
                  ) : u.role === 'viewer' ? (
                    // 가입하면 조회 전용으로 시작 → 관리자가 승인해 일반 사용자로(2026-10-20). 관리자 토글(admin↔user)과 분리 —
                    // 같은 토글을 쓰면 클릭 한 번에 관리자로 승격되는 사고가 날 수 있다.
                    <span className="inline-flex items-center gap-1.5">
                      <Badge color="slate">승인 대기</Badge>
                      <button onClick={() => void handleApprove(u)} title="일반 사용자로 승인 — 소속 파트 담당 업무를 수정할 수 있게 됨"
                        className="text-xs px-2 py-0.5 rounded border border-indigo-300 text-indigo-600 hover:bg-indigo-50 dark:border-indigo-500/50 dark:text-indigo-300 dark:hover:bg-indigo-900/30">
                        승인
                      </button>
                    </span>
                  ) : (
                    <span
                      role="button"
                      tabIndex={0}
                      onClick={() => void handleToggleRole(u)}
                      onKeyDown={(e) => e.key === 'Enter' && void handleToggleRole(u)}
                      title={`클릭하여 ${u.role === 'admin' ? '일반 사용자' : '슈퍼어드민'}로 변경`}
                      className="inline-block cursor-pointer transform hover:scale-110 active:scale-95 transition-transform duration-150"
                    >
                      <Badge color={u.role === 'admin' ? 'indigo' : 'slate'}>
                        {u.role === 'admin' ? '슈퍼어드민' : '일반 사용자'}
                      </Badge>
                    </span>
                  )}
                </td>
                <td className="px-4 py-3">
                  <Badge color={u.has_llm_credentials ? 'emerald' : 'slate'}>
                    {u.has_llm_credentials ? '본인 키' : '팀 공통'}
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
                    <Badge color={u.is_active ? 'emerald' : 'rose'}>
                      {u.is_active ? '활성' : '비활성'}
                    </Badge>
                  )}
                </td>
                <td className="px-4 py-3 text-right whitespace-nowrap">
                  {(
                    <button
                      onClick={() => openEdit(u)}
                      className="text-slate-500 hover:text-indigo-600 dark:hover:text-indigo-400 transition-colors p-1 mr-1"
                      title="소속 파트·상태 수정"
                    >
                      <Pencil className="w-4 h-4" />
                    </button>
                  )}
                  {u.id !== currentUser?.id && (
                    <button
                      onClick={() => handleDelete(u)}
                      className="text-slate-500 hover:text-rose-600 dark:hover:text-rose-400 hover:scale-110 active:scale-95 transition-all p-1 cursor-pointer"
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
      <FormModal
        isOpen={!!editUser}
        title={editUser ? `사용자 수정 — ${editUser.username}` : ''}
        onClose={() => setEditUser(null)}
        onSubmit={() => editMutation.mutate()}
        submitting={editMutation.isPending}
        submitDisabled={!editChanged}
        error={editMutation.error ? String((editMutation.error as Error).message) : null}
      >
        <div>
          <label htmlFor="user-edit-part" className="block text-xs font-medium text-slate-400 mb-1">소속 파트</label>
          <select id="user-edit-part" value={editForm.part} onChange={(e) => setEditForm((f) => ({ ...f, part: e.target.value }))}
            className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500">
            {!editForm.part && <option value="" disabled>소속 없음</option>}
            {parts.map((p) => <option key={p.id} value={p.name}>{p.team ? `${p.team} · ${p.name}` : p.name}</option>)}
          </select>
          <p className="text-xs text-slate-500 mt-1">소속 파트가 맡은 업무 컬렉션을 수정할 수 있게 돼요.</p>
        </div>
        {editUser?.id !== currentUser?.id && <label htmlFor="user-edit-active" className="flex items-center gap-2 text-sm text-slate-300">
          <input id="user-edit-active" type="checkbox" checked={editForm.is_active}
            onChange={(e) => setEditForm((f) => ({ ...f, is_active: e.target.checked }))} className="accent-indigo-500" />
          활성(로그인 가능)
        </label>}
      </FormModal>
    </div>
  );
}
