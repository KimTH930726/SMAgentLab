import { useState, type ReactNode } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { deleteNamespace, renameNamespace } from '../../api/namespaces';
import { useAppStore } from '../../store/useAppStore';
import { Button } from '../ui/Button';
import { Modal } from '../ui/Modal';
import { FormModal } from '../ui/FormModal';
import type { NamespaceDetail } from '../../types';

/**
 * 컬렉션 이름 수정·삭제 창 — 정책 컬렉션·시스템 컬렉션 화면이 같이 쓴다(11/02, 두 화면에 같은 코드를 두지 않게).
 * suffix: 키 이름 끝에 붙는 글자(시스템 = " 시스템") — 화면 이름(뗀 것)으로 고치고 저장할 땐 다시 붙인다(정책 컬렉션과 이름이 겹치지 않게).
 * 이름을 바꾸면 고른 컬렉션도 새 이름으로 따라가고, 지우면 그 컬렉션 캐시를 비운다.
 */
export function useCollectionEdit(kindLabel: string, suffix = ''): {
  openRename: (ns: NamespaceDetail) => void;
  openDelete: (ns: NamespaceDetail) => void;
  modals: ReactNode;
} {
  const qc = useQueryClient();
  const selected = useAppStore((s) => s.namespace);
  const setNamespace = useAppStore((s) => s.setNamespace);
  const [editing, setEditing] = useState<NamespaceDetail | null>(null);
  const [draft, setDraft] = useState('');
  const [deleting, setDeleting] = useState<NamespaceDetail | null>(null);
  const shown = (name: string) => (suffix && name.endsWith(suffix) ? name.slice(0, -suffix.length) : name);
  const keyOf = (text: string) => (suffix && editing?.name.endsWith(suffix) && !text.endsWith(suffix) ? `${text}${suffix}` : text);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['namespaces-detail'] });
    qc.invalidateQueries({ queryKey: ['namespaces'] });
  };
  const rename = useMutation({
    mutationFn: ({ oldName, newName }: { oldName: string; newName: string }) => renameNamespace(oldName, newName),
    onSuccess: (_d, { oldName, newName }) => {
      qc.setQueryData<string[]>(['namespaces'], (old) => old?.map((n) => (n === oldName ? newName : n)));
      // 고른 컬렉션의 이름을 바꿨으면 새 이름으로 따라간다 — 안 그러면 사이드바가 "목록에 없음"으로 보고 첫 컬렉션으로 넘어간다
      if (selected === oldName) setNamespace(newName);
      setEditing(null);
    },
    onSettled: refresh,
    onError: () => { /* 창 안에 표시 */ },
  });
  const remove = useMutation({
    mutationFn: (name: string) => deleteNamespace(name),
    onSuccess: (_d, name) => {
      qc.setQueryData<string[]>(['namespaces'], (old) => old?.filter((n) => n !== name));
      qc.setQueryData<Array<{ name: string }>>(['namespaces-detail'], (old) => old?.filter((n) => n.name !== name));
      for (const k of ['knowledge', 'glossary', 'stats-ns']) qc.removeQueries({ queryKey: [k, name] });
      refresh();
      setDeleting(null);
    },
    onError: () => { /* 창 안에 표시 */ },
  });

  const changed = !!editing && !!draft.trim() && keyOf(draft.trim()) !== editing.name;
  const commit = () => {
    if (!editing || rename.isPending || !changed) return;
    rename.mutate({ oldName: editing.name, newName: keyOf(draft.trim()) });
  };

  const modals = (
    <>
      {/* 이름 수정. 모달: 그 자리에서 고치던 칸은 포커스가 빠지거나 취소를 눌러도 저장됐다 */}
      <FormModal isOpen={editing !== null} title={`${kindLabel} 수정`} onClose={() => setEditing(null)} onSubmit={commit}
        submitting={rename.isPending} submitDisabled={!changed}
        error={rename.isError ? String((rename.error as Error)?.message ?? '저장 실패') : null}>
        <div>
          <label htmlFor="ns-rename" className="block text-xs text-slate-400 mb-1">이름</label>
          <input id="ns-rename" autoFocus value={draft} onChange={(e) => setDraft(e.target.value)}
            className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500" />
          <p className="text-xs text-slate-500 mt-1">안의 정책·지식·용어는 그대로 따라가요{editing?.code ? ` · 코드 ${editing.code}는 바뀌지 않아요` : ''}</p>
          <p className="text-xs text-slate-500 mt-1">담당 파트: 시스템·권한 › 조직·권한 › 담당 컬렉션</p>
        </div>
      </FormModal>

      <Modal isOpen={!!deleting} onClose={() => setDeleting(null)} title={`${kindLabel} 삭제`}>
        <div className="space-y-4">
          <p className="text-sm text-slate-300">
            <span className="font-semibold text-rose-600 dark:text-rose-400">{deleting && shown(deleting.name)}</span> {kindLabel}을 삭제하면 안의 데이터가 함께 지워지고 되돌릴 수 없어요.
          </p>
          {deleting && (
            <p className="text-sm text-slate-400 tabular-nums">
              사용 중: 정책 {deleting.policy_count ?? 0} · 운영 지식 {deleting.knowledge_count} · 용어 {deleting.glossary_count}.
              검토 대기·반려·폐기된 정책과 지식, 질의 기록·대화도 모두 함께 지워져요.
            </p>
          )}
          <div className="flex gap-2 justify-end">
            <Button variant="secondary" size="sm" onClick={() => setDeleting(null)}>취소</Button>
            <Button variant="danger" size="sm" loading={remove.isPending} onClick={() => deleting && remove.mutate(deleting.name)}>삭제</Button>
          </div>
          {remove.isError && <p className="text-xs text-rose-600 dark:text-rose-400">{String(remove.error)}</p>}
        </div>
      </Modal>
    </>
  );

  return {
    openRename: (ns) => { rename.reset(); setEditing(ns); setDraft(shown(ns.name)); },
    openDelete: (ns) => { remove.reset(); setDeleting(ns); },
    modals,
  };
}
