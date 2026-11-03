import { Fragment, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Trash2, Pencil } from 'lucide-react';
import {
  getNamespacesDetail, createNamespace,
} from '../../api/namespaces';
import { isBusinessCollection, isSystemCollection } from '../../utils/collections';
import { useAuthStore } from '../../store/useAuthStore';
import { useAppStore } from '../../store/useAppStore';
import { Button } from '../ui/Button';
import { Modal } from '../ui/Modal';
import { Badge } from '../ui/Badge';
import type { NamespaceDetail, OwnerPart } from '../../types';
import { showError } from '../../store/useDialogStore';
import { canEditDomain, directOwnerParts, effectiveOwnerParts, ownerNames } from '../../utils/domainAccess';
import { useCollectionEdit } from './CollectionEditModals';
import { CollectionLinks } from './CollectionLinks';

interface NamespaceManagerProps {
  onNavigate?: (to: 'policy' | 'knowledge' | 'glossary') => void;
  /** [지식 등록 →]·[보기 →] — 그 컬렉션을 고른 채 운영 지식(등록 탭 / 목록)으로. 넣는 곳은 [지식 등록] 한 곳(§15-9) */
  onRegister?: (tab: 'ingest' | 'list', into?: string) => void;
}

// 업무구분 관리(CategorySection)·업무구분 열은 10/22 업무구분 폐지로 삭제

export function NamespaceManager({ onNavigate, onRegister }: NamespaceManagerProps) {
  const qc = useQueryClient();
  const user = useAuthStore((s) => s.user);
  const { namespace: selectedNamespace, setNamespace } = useAppStore();
  // 영역 안 데이터(지식·용어 등)는 담당 파트 사람 또는 관리자, 영역 자체(만들기·이름·삭제)는 관리자만(2026-10-20)
  const canModifyNs = (owners: OwnerPart[]) => canEditDomain(user, owners);
  const isAdmin = user?.role === 'admin';
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [newDescription, setNewDescription] = useState('');
  // 이름 수정·삭제 창 — 시스템 컬렉션 화면과 같이 씀(11/02). 담당 파트는 플랫폼 관리 › 조직·권한 › 담당 컬렉션 한 곳에서
  const edit = useCollectionEdit('정책 컬렉션');

  const { data: allCollections = [], isLoading, error } = useQuery({
    queryKey: ['namespaces-detail'],
    queryFn: getNamespacesDetail,
    staleTime: 10_000,
    refetchOnMount: 'always',
  });
  // 정책 컬렉션만 — 시스템 컬렉션은 컬렉션 › 시스템 컬렉션 화면(11/02)
  const namespaces = allCollections.filter(isBusinessCollection);
  // 시스템의 관련 정책을 거꾸로 — 정책마다 연결된 시스템(없으면 경고)
  const systemsOf = new Map<string, string[]>();
  for (const s of allCollections.filter(isSystemCollection)) for (const p of s.supports ?? []) systemsOf.set(p, [...(systemsOf.get(p) ?? []), s.display_name || s.name]);

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

  const handleCreate = () => {
    if (!newName.trim()) return;
    createMutation.mutate({ name: newName.trim(), description: newDescription.trim() });
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-200">정책 컬렉션</h2>
        {isAdmin && (
          <Button
            variant="primary"
            size="sm"
            onClick={() => setShowCreate(true)}
          >
            <Plus className="w-4 h-4" />
            새 정책 컬렉션
          </Button>
        )}
      </div>

      {isLoading && (
        <div className="text-center py-10 text-slate-500 animate-pulse">로딩 중...</div>
      )}
      {error && (
        <div className="text-center py-10 text-rose-600 dark:text-rose-400">오류가 발생했습니다.</div>
      )}

      {/* 정책 컬렉션 목록(③, 10/21) — 줄 = 그 영역 고르기(사이드바와 같은 값), 숫자 = 그 영역을 고른 채 해당 화면으로 */}
      {!isLoading && namespaces.length > 0 && (
        <div className="bg-slate-800 rounded-xl border border-slate-700 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-700 text-slate-400">
                <th className="text-left px-4 py-3 font-medium">정책 컬렉션</th>
                <th className="text-left px-4 py-3 font-medium">담당 파트</th>
                <th className="text-right px-3 py-3 font-medium" title="지금 쓰이는 정책(폐기·반려 제외)">정책</th>
                <th className="text-right px-3 py-3 font-medium" title="검색에 쓰이는(활성) 지식 — 승인 대기·반려·폐기·수집 중 제외">운영 지식</th>
                <th className="text-right px-3 py-3 font-medium">용어</th>
                <th><span className="sr-only">이동</span></th>
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
                return (
                  <Fragment key={ns.name}>
                    <tr onClick={() => setNamespace(ns.name)} tabIndex={0} aria-current={selected ? 'true' : undefined}
                      onKeyDown={(e) => { if ((e.key === 'Enter' || e.key === ' ') && e.target === e.currentTarget) { e.preventDefault(); setNamespace(ns.name); } }}
                      className={`border-b border-slate-700/50 cursor-pointer transition-colors ${selected ? 'bg-indigo-50 dark:bg-indigo-900/20' : 'hover:bg-slate-700/30'}`}>
                      <td className="px-4 py-2.5">
                        <div>
                          <div className="font-medium text-slate-200">{ns.name}{ns.code && <span className="ml-2 text-[11px] font-mono text-slate-500">{ns.code}</span>}</div>
                          {ns.kind === 'domain' && ((systemsOf.get(ns.name) ?? (ns.parent ? systemsOf.get(ns.parent) : undefined))?.length
                            ? <div className="text-[11px] text-slate-500" title="관련 정책으로 연결된 시스템 — 이 정책 질문에서 그 시스템의 운영 지식까지 찾는다">시스템 · {(systemsOf.get(ns.name) ?? systemsOf.get(ns.parent ?? '') ?? []).join(' · ')}{!systemsOf.get(ns.name) && ' (상위)'}</div>
                            : <div className="text-[11px] text-amber-700 dark:text-amber-300" title="연결된 시스템이 없어 이 정책 질문에서 운영 지식을 찾을 길이 없어요 — 시스템 컬렉션에서 관련 정책으로 연결">관련 시스템 없음</div>)}
                          {ns.description && <div className="text-xs text-slate-500 truncate max-w-[16rem]" title={ns.description}>{ns.description}</div>}
                        </div>
                      </td>
                      <td className="px-4 py-2.5">
                        {directOwnerParts(ns).length > 0 ? (
                          <div className="flex flex-wrap gap-1">
                            {directOwnerParts(ns).map((o) => (
                              <Badge key={o.id} color={canModifyNs(effectiveOwnerParts(ns)) ? 'emerald' : 'slate'}>{o.name}</Badge>
                            ))}
                          </div>
                        ) : ns.owner_inherited ? (
                          <span className="text-xs text-slate-400" title="이 컬렉션엔 담당이 없어 상위 컬렉션 담당을 물려받아요">
                            상위: {ownerNames(effectiveOwnerParts(ns))}
                          </span>
                        ) : <span className="text-xs text-slate-500" title="담당 지정: 플랫폼 관리 › 조직·권한 › 담당 컬렉션">없음(관리자만)</span>}
                      </td>
                      <td className="px-3 py-2.5 text-right">{count(ns.policy_count, 'policy', '정책')}</td>
                      <td className="px-3 py-2.5 text-right">{count(ns.knowledge_count, 'knowledge', '운영 지식')}</td>
                      <td className="px-3 py-2.5 text-right">{count(ns.glossary_count, 'glossary', '용어')}</td>
                      <td className="px-3 py-2.5 whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
                        {onRegister && (
                          <CollectionLinks editable={canModifyNs(effectiveOwnerParts(ns))} ingest={false}   // 운영 지식은 시스템 컬렉션에만(11/02)
                            onGo={(tab) => { setNamespace(ns.name); onRegister(tab, ns.name); }} />
                        )}
                      </td>
                      <td className="px-3 py-2.5" onClick={(e) => e.stopPropagation()}>
                        {isAdmin && (
                          <div className="flex items-center justify-end gap-1">
                            <button onClick={() => edit.openRename(ns)} title="이름 수정"
                              className="p-1.5 rounded text-slate-500 hover:text-indigo-600 dark:hover:text-indigo-400 hover:bg-slate-700/60">
                              <Pencil className="w-3.5 h-3.5" />
                            </button>
                            <button onClick={() => edit.openDelete(ns)} title="삭제"
                              className="p-1.5 rounded text-slate-500 hover:text-rose-600 dark:hover:text-rose-400 hover:bg-slate-700/60">
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        )}
                      </td>
                    </tr>
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {!isLoading && namespaces.length === 0 && (
        <div className="text-center py-10 text-slate-500">정책 컬렉션이 없습니다.</div>
      )}

      {edit.modals}

      {/* Create Modal */}
      <Modal
        isOpen={showCreate}
        onClose={() => setShowCreate(false)}
        title="새 정책 컬렉션"
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
              placeholder="정책 컬렉션 설명"
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

    </div>
  );
}
