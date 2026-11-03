import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Pencil, Plus, Trash2 } from 'lucide-react';
import { createNamespace, getNamespacesDetail, setNamespaceSupports } from '../../api/namespaces';
import { isPickableDomain } from '../../utils/selectedDomain';
import { isSystemCollection } from '../../utils/collections';
import { canEditDomain, directOwnerParts, effectiveOwnerParts } from '../../utils/domainAccess';
import { useAuthStore } from '../../store/useAuthStore';
import { useAppStore } from '../../store/useAppStore';
import { Button } from '../ui/Button';
import { Badge } from '../ui/Badge';
import { FormModal } from '../ui/FormModal';
import type { NamespaceDetail } from '../../types';
import { CollectionLinks } from './CollectionLinks';
import { useCollectionEdit } from './CollectionEditModals';

/**
 * 컬렉션 › 시스템 컬렉션(11/02, docs/domains/knowledge/taxonomy-standard.md) — 옛 조직 컬렉션 자리.
 * 시스템 = IT 시스템(예: 홈페이지 시스템) 컬렉션, 최상위만. 담당 파트는 정책 컬렉션과 같은 연결(지정: 조직·권한 › 담당 컬렉션),
 * 관련 정책 = 이 시스템이 다루는 정책 컬렉션(예: 홈페이지 시스템 → 회원). 만들기·관련 정책 수정은 관리자만.
 * 줄을 누르면 보는 컬렉션을 이것으로(정책 컬렉션 표와 같게), [지식 등록 →]·[보기 →]도 같다.
 */
/** 시스템 키 이름 — 화면 이름 뒤에 " 시스템"(정책 컬렉션 "딜리버스"와 시스템 "딜리버스"가 겹치지 않게, 키 정리 전까지) */
const systemKey = (name: string) => (name.endsWith(' 시스템') ? name : `${name} 시스템`);

export function SystemCollectionManager({ onNavigate, onRegister }: {
  onNavigate?: (to: 'knowledge' | 'glossary') => void;
  onRegister: (tab: 'ingest' | 'list', into?: string) => void;
}) {
  const qc = useQueryClient();
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === 'admin';
  const edit = useCollectionEdit('시스템 컬렉션', ' 시스템');   // 이름 수정·삭제 — 정책 컬렉션 화면과 같은 창(키 끝 " 시스템"은 화면에서 뗌)
  const selected = useAppStore((s) => s.namespace);
  const setNamespace = useAppStore((s) => s.setNamespace);
  const { data: all = [], isLoading, error } = useQuery({
    queryKey: ['namespaces-detail'], queryFn: getNamespacesDetail, staleTime: 10_000, refetchOnMount: 'always',
  });
  const systems = all.filter(isSystemCollection);
  // 관련 정책 후보 = 고르는 창에 보이는 정책 컬렉션(테스트용·시스템 제외)
  const policies = all.filter((d) => isPickableDomain(d) && !isSystemCollection(d));

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['namespaces-detail'] });
    qc.invalidateQueries({ queryKey: ['namespaces'] });
  };

  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [newCode, setNewCode] = useState('');
  const [newDesc, setNewDesc] = useState('');
  const create = useMutation({
    mutationFn: () => createNamespace({ name: systemKey(newName.trim()), description: newDesc.trim(), area: 'system', ...(newCode.trim() ? { code: newCode.trim().toUpperCase() } : {}) }),
    onSuccess: () => { refresh(); setShowCreate(false); setNewName(''); setNewDesc(''); setNewCode(''); },
    onError: () => { /* 모달 안에 표시 */ },
  });

  const [editing, setEditing] = useState<NamespaceDetail | null>(null);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const openSupports = (d: NamespaceDetail) => {
    saveSupports.reset();
    setEditing(d);
    setChecked(new Set(d.supports ?? []));
  };
  const toggle = (name: string) => setChecked((prev) => {
    const next = new Set(prev);
    if (next.has(name)) next.delete(name); else next.add(name);
    return next;
  });
  const saveSupports = useMutation({
    mutationFn: ({ name, list }: { name: string; list: string[] }) => setNamespaceSupports(name, list),
    onSuccess: () => { refresh(); setEditing(null); },
    onError: () => { /* 모달 안에 표시 */ },
  });
  const supportsChanged = !!editing && [...checked].sort().join('\n') !== [...(editing.supports ?? [])].sort().join('\n');

  const go = (d: NamespaceDetail, tab: 'ingest' | 'list') => { setNamespace(d.name); onRegister(tab, d.name); };

  return (
    <div className="space-y-4" data-testid="system-collections">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-200">시스템 컬렉션</h2>
        {isAdmin && (
          <Button variant="primary" size="sm" onClick={() => { create.reset(); setShowCreate(true); }}>
            <Plus className="w-4 h-4" /> 시스템 추가
          </Button>
        )}
      </div>

      {isLoading && <div className="text-center py-10 text-slate-500 animate-pulse">로딩 중...</div>}
      {error && <div className="text-center py-10 text-rose-600 dark:text-rose-400">오류가 발생했습니다.</div>}

      {!isLoading && systems.length > 0 && (
        <div className="bg-slate-800 rounded-xl border border-slate-700 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-700 text-slate-400">
                <th className="text-left px-4 py-3 font-medium">시스템 컬렉션</th>
                <th className="text-left px-4 py-3 font-medium">담당 파트</th>
                <th className="text-left px-4 py-3 font-medium" title="이 시스템이 다루는 정책 컬렉션 — 그 정책 질문에서 이 시스템의 운영 지식까지 찾는다">관련 정책</th>
                <th className="text-right px-3 py-3 font-medium" title="검색에 쓰이는(활성) 지식">운영 지식</th>
                <th className="text-right px-3 py-3 font-medium">용어</th>
                <th><span className="sr-only">이동</span></th>
                <th className="w-20"><span className="sr-only">수정·삭제</span></th>
              </tr>
            </thead>
            <tbody>
              {systems.map((d) => {
                const current = d.name === selected;
                const owners = directOwnerParts(d);
                const editable = canEditDomain(user, effectiveOwnerParts(d));
                const count = (n: number, to: 'knowledge' | 'glossary', label: string) => (
                  <button onClick={(e) => { e.stopPropagation(); setNamespace(d.name); onNavigate?.(to); }} title={`${d.name}의 ${label} 보기`}
                    className="tabular-nums text-slate-300 hover:text-indigo-600 dark:hover:text-indigo-300 hover:underline">{n}</button>
                );
                return (
                  <tr key={d.name} onClick={() => setNamespace(d.name)} tabIndex={0} aria-current={current ? 'true' : undefined}
                    onKeyDown={(e) => { if ((e.key === 'Enter' || e.key === ' ') && e.target === e.currentTarget) { e.preventDefault(); setNamespace(d.name); } }}
                    className={`border-b border-slate-700/50 cursor-pointer transition-colors ${current ? 'bg-indigo-50 dark:bg-indigo-900/20' : 'hover:bg-slate-700/30'}`}>
                    <td className="px-4 py-2.5">
                      <div className="font-medium text-slate-200">{d.display_name || d.name}{d.code && <span className="ml-2 text-[11px] font-mono text-slate-500">{d.code}</span>}</div>
                      {d.description && <div className="text-xs text-slate-500 truncate max-w-[16rem]" title={d.description}>{d.description}</div>}
                    </td>
                    <td className="px-4 py-2.5">
                      {owners.length > 0 ? (
                        <div className="flex flex-wrap gap-1">
                          {owners.map((o) => <Badge key={o.id} color={editable ? 'emerald' : 'slate'}>{o.name}</Badge>)}
                        </div>
                      ) : <span className="text-xs text-slate-500" title="담당 지정: 플랫폼 관리 › 조직·권한 › 담당 컬렉션">없음(관리자만)</span>}
                    </td>
                    <td className="px-4 py-2.5" onClick={(e) => e.stopPropagation()}>
                      <div className="flex flex-wrap items-center gap-1">
                        {(d.supports ?? []).length
                          ? (d.supports ?? []).map((b) => <Badge key={b} color="indigo">{b}</Badge>)
                          : <span className="text-xs text-slate-500">없음</span>}
                        {isAdmin && (
                          <button onClick={() => openSupports(d)} title="관련 정책 수정" aria-label={`${d.display_name || d.name} 관련 정책 수정`}
                            className="p-1 rounded text-slate-500 hover:text-indigo-600 dark:hover:text-indigo-400 hover:bg-slate-700/60">
                            <Pencil className="w-3.5 h-3.5" />
                          </button>
                        )}
                      </div>
                    </td>
                    <td className="px-3 py-2.5 text-right">{count(d.knowledge_count, 'knowledge', '운영 지식')}</td>
                    <td className="px-3 py-2.5 text-right">{count(d.glossary_count, 'glossary', '용어')}</td>
                    <td className="px-3 py-2.5 whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
                      <CollectionLinks editable={editable} onGo={(tab) => go(d, tab)} />
                    </td>
                    <td className="px-3 py-2.5" onClick={(e) => e.stopPropagation()}>
                      {isAdmin && (
                        <div className="flex items-center justify-end gap-1">
                          <button onClick={() => edit.openRename(d)} title="이름 수정" aria-label={`${d.display_name || d.name} 이름 수정`}
                            className="p-1.5 rounded text-slate-500 hover:text-indigo-600 dark:hover:text-indigo-400 hover:bg-slate-700/60">
                            <Pencil className="w-3.5 h-3.5" />
                          </button>
                          <button onClick={() => edit.openDelete(d)} title="삭제" aria-label={`${d.display_name || d.name} 삭제`}
                            className="p-1.5 rounded text-slate-500 hover:text-rose-600 dark:hover:text-rose-400 hover:bg-slate-700/60">
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {!isLoading && !error && systems.length === 0 && (
        <div className="text-center py-10 text-slate-500">시스템 컬렉션이 없습니다.</div>
      )}

      {edit.modals}

      <FormModal isOpen={showCreate} title="시스템 추가" onClose={() => setShowCreate(false)} onSubmit={() => create.mutate()}
        submitLabel="추가" submitting={create.isPending} submitDisabled={!newName.trim()}
        error={create.isError ? String((create.error as Error)?.message ?? '추가 실패') : null}>
        <div>
          <label htmlFor="sys-name" className="block text-xs text-slate-400 mb-1">이름 <span className="text-rose-600 dark:text-rose-400">*</span></label>
          <input id="sys-name" autoFocus value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="예: 홈페이지"
            className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500" />
        </div>
        <div>
          <label htmlFor="sys-code" className="block text-xs text-slate-400 mb-1" title="바뀌지 않는 키 — 외부 연계·골든셋이 이 값을 가리킨다">코드</label>
          <input id="sys-code" value={newCode} onChange={(e) => setNewCode(e.target.value)} placeholder="예: SYS-HP"
            className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm font-mono text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500" />
        </div>
        <div>
          <label htmlFor="sys-desc" className="block text-xs text-slate-400 mb-1">설명</label>
          <input id="sys-desc" value={newDesc} onChange={(e) => setNewDesc(e.target.value)}
            className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500" />
          <p className="text-xs text-slate-500 mt-1">담당 파트: 플랫폼 관리 › 조직·권한 › 담당 컬렉션</p>
        </div>
      </FormModal>

      <FormModal isOpen={!!editing} title={editing ? `${editing.display_name || editing.name} — 관련 정책` : ''} onClose={() => setEditing(null)}
        onSubmit={() => editing && saveSupports.mutate({ name: editing.name, list: [...checked] })}
        submitting={saveSupports.isPending} submitDisabled={!supportsChanged}
        error={saveSupports.isError ? String((saveSupports.error as Error)?.message ?? '저장 실패') : null}>
        <div className="max-h-80 overflow-y-auto space-y-1 pr-1" data-testid="supports-list">
          {policies.length === 0 && <p className="text-xs text-slate-500">정책 컬렉션이 없어요.</p>}
          {policies.map((b) => (
            <label key={b.name} htmlFor={`sup-${b.id}`} className="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-slate-700/40 cursor-pointer">
              <input id={`sup-${b.id}`} type="checkbox" checked={checked.has(b.name)} onChange={() => toggle(b.name)} className="accent-indigo-500" />
              <span className="text-sm text-slate-200">{b.name}</span>
            </label>
          ))}
        </div>
      </FormModal>
    </div>
  );
}
