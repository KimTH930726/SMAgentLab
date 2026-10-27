import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle } from 'lucide-react';
import { ApiError } from '../../../api/client';
import { createPolicyChange, getPolicyChangeUses, type PolicyChange } from '../../../api/corrections';
import { listPolicies } from '../../../api/policy';
import { showAlert } from '../../../store/useDialogStore';
import { FormModal } from '../../ui/FormModal';

/**
 * 정책 추가·폐기·분류 옮기기 제안 창(C3 2단계, 10/27 — docs/tech/policy-lifecycle-design.md §4 목업 F).
 * 모두 정정 요청에 제안으로 올라가고, 승인해야 반영된다. 분류는 기존 것에서 고르거나 대·중·소를 직접 써서 새로 만든다.
 */

const INPUT = 'w-full bg-slate-800 border border-slate-600 rounded-lg px-2.5 py-1.5 text-sm text-slate-200 focus:outline-none focus:border-indigo-500';
const AREA = `${INPUT} resize-y`;
const LEVELS = ['대분류', '중분류', '소분류'] as const;
const pathKey = (p: string[]) => p.join(' › ');

function useChange(namespace: string, onDone: () => void) {
  const qc = useQueryClient();
  const [error, setError] = useState('');
  const save = useMutation({
    mutationFn: (body: PolicyChange) => createPolicyChange(namespace, body),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ['policies', namespace] });
      qc.invalidateQueries({ queryKey: ['policy', namespace] });
      qc.invalidateQueries({ queryKey: ['corrections'] });
      qc.invalidateQueries({ queryKey: ['corrections-pending-count'] });
      onDone();
      void showAlert(`정정 #${r.id}로 올렸어요 — 품질 › 정정 요청에서 승인하면 반영돼요.`, { tone: 'success' });
    },
    onError: (e: Error) => {
      setError(e.message || '올리지 못했어요.');
      if (e instanceof ApiError && e.status === 409) qc.invalidateQueries({ queryKey: ['policy', namespace] });
    },
  });
  return { save, error, setError };
}

/** 분류 고르기 — 지금 컬렉션의 분류 목록에서 고르거나 대·중·소를 직접 쓴다(없는 경로면 "새 분류") */
function PathPicker({ namespace, value, onChange, idPrefix }: {
  namespace: string; value: string[]; onChange: (p: string[]) => void; idPrefix: string;
}) {
  const { data: rows = [] } = useQuery({ queryKey: ['policies', namespace, ''], queryFn: () => listPolicies(namespace, ''), staleTime: 15_000 });
  const paths = useMemo(() => {
    const m = new Map<string, string[]>();
    for (const r of rows) for (let d = 1; d <= r.category_path.length; d++) m.set(pathKey(r.category_path.slice(0, d)), r.category_path.slice(0, d));
    return [...m.values()].sort((a, b) => pathKey(a).localeCompare(pathKey(b), 'ko'));
  }, [rows]);
  const cur = value.map((v) => v.trim()).filter(Boolean);
  const known = paths.some((p) => pathKey(p) === pathKey(cur));
  return (
    <div className="space-y-2">
      <div className="space-y-1">
        <label htmlFor={`${idPrefix}-pick`} className="block text-xs text-slate-400">기존 분류에서 고르기</label>
        <select id={`${idPrefix}-pick`} className={INPUT} value={known ? pathKey(cur) : ''}
          onChange={(e) => { const p = paths.find((x) => pathKey(x) === e.target.value); if (p) onChange([...p]); }}>
          <option value="">— 고르거나 아래에 직접 쓰기 —</option>
          {paths.map((p) => <option key={pathKey(p)} value={pathKey(p)}>{pathKey(p)}</option>)}
        </select>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
        {LEVELS.map((lv, i) => (
          <div key={lv} className="space-y-1">
            <label htmlFor={`${idPrefix}-lv${i}`} className="block text-xs text-slate-400">{lv}{i === 0 ? ' (필수)' : ''}</label>
            <input id={`${idPrefix}-lv${i}`} className={INPUT} value={value[i] ?? ''}
              onChange={(e) => { const n = [...value]; n[i] = e.target.value; onChange(n.slice(0, 3)); }} />
          </div>
        ))}
      </div>
      {cur.length > 0 && !known && (
        <p className="text-xs text-amber-700 dark:text-amber-300" data-testid="new-category">새 분류: {pathKey(cur)} — 승인하면 생겨요</p>
      )}
    </div>
  );
}

const cleanPath = (p: string[]) => p.map((v) => v.trim()).filter(Boolean);

export function PolicyAddModal({ isOpen, namespace, initialPath, onClose }: {
  isOpen: boolean; namespace: string; initialPath: string[]; onClose: () => void;
}) {
  const [path, setPath] = useState<string[]>(initialPath);
  const [name, setName] = useState('');
  const [body, setBody] = useState('');
  const [remark, setRemark] = useState('');
  const { save, error, setError } = useChange(namespace, onClose);
  useEffect(() => {
    if (isOpen) { setPath(initialPath); setName(''); setBody(''); setRemark(''); setError(''); }
  }, [isOpen]);   // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <FormModal isOpen={isOpen} title="정책 추가 제안" onClose={onClose} maxWidth="max-w-3xl"
      onSubmit={() => save.mutate({ change: 'add', policy_name: name.trim(), category_path: cleanPath(path), raw_body: body, remark })}
      submitLabel="정정 요청에 올리기" submitting={save.isPending} error={error}
      submitDisabled={!name.trim() || !body.trim() || cleanPath(path).length === 0}>
      <p className="text-xs text-slate-400" title="승인 전엔 검색·답변에 나오지 않아요">저장하면 정정 요청에 올라가고, 승인해야 새 정책이 생겨요.</p>
      <PathPicker namespace={namespace} value={path} onChange={setPath} idPrefix="padd" />
      <div className="space-y-1">
        <label htmlFor="padd-name" className="block text-xs text-slate-400">정책 이름</label>
        <input id="padd-name" className={INPUT} value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="space-y-1">
        <label htmlFor="padd-body" className="block text-xs text-slate-400">원문</label>
        <textarea id="padd-body" rows={10} className={AREA} value={body} onChange={(e) => setBody(e.target.value)} />
      </div>
      <div className="space-y-1">
        <label htmlFor="padd-remark" className="block text-xs text-slate-400" title="답변에 쓰고 검색엔 안 씀">비고 (선택)</label>
        <textarea id="padd-remark" rows={2} className={AREA} value={remark} onChange={(e) => setRemark(e.target.value)} />
      </div>
    </FormModal>
  );
}

export function PolicyMoveModal({ isOpen, namespace, itemId, name, currentPath, onClose }: {
  isOpen: boolean; namespace: string; itemId: number; name: string; currentPath: string[]; onClose: () => void;
}) {
  const [path, setPath] = useState<string[]>(currentPath);
  const [reason, setReason] = useState('');
  const { save, error, setError } = useChange(namespace, onClose);
  useEffect(() => {
    if (isOpen) { setPath(currentPath); setReason(''); setError(''); }
  }, [isOpen, itemId]);   // eslint-disable-line react-hooks/exhaustive-deps
  const same = pathKey(cleanPath(path)) === pathKey(currentPath);
  return (
    <FormModal isOpen={isOpen} title={`분류 옮기기 제안 — ${name}`} onClose={onClose} maxWidth="max-w-2xl"
      onSubmit={() => save.mutate({ change: 'move', target_id: itemId, category_path: cleanPath(path), reason })}
      submitLabel="정정 요청에 올리기" submitting={save.isPending} error={error}
      submitDisabled={same || cleanPath(path).length === 0}>
      <p className="text-sm text-slate-300"><span className="text-xs text-slate-500 mr-2">지금</span>{pathKey(currentPath) || '(분류 없음)'}</p>
      <PathPicker namespace={namespace} value={path} onChange={setPath} idPrefix="pmove" />
      <div className="space-y-1">
        <label htmlFor="pmove-reason" className="block text-xs text-slate-400">이유 (선택)</label>
        <input id="pmove-reason" className={INPUT} value={reason} onChange={(e) => setReason(e.target.value)} />
      </div>
    </FormModal>
  );
}

export function PolicyRetireModal({ isOpen, namespace, itemId, name, onClose }: {
  isOpen: boolean; namespace: string; itemId: number; name: string; onClose: () => void;
}) {
  const [reason, setReason] = useState('');
  const { save, error, setError } = useChange(namespace, onClose);
  const uses = useQuery({ queryKey: ['policy-change-uses', namespace, itemId], queryFn: () => getPolicyChangeUses(namespace, itemId), enabled: isOpen });
  useEffect(() => { if (isOpen) { setReason(''); setError(''); } }, [isOpen, itemId]);   // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <FormModal isOpen={isOpen} title={`폐기 제안 — ${name}`} onClose={onClose} danger
      onSubmit={() => save.mutate({ change: 'retire', target_id: itemId, reason: reason.trim() })}
      submitLabel="정정 요청에 올리기" submitting={save.isPending} error={error} submitDisabled={!reason.trim()}>
      <p className="text-xs text-slate-400" title="지우지 않아요 — 이력·평가 문항이 계속 추적합니다. 되돌리려면 같은 정책을 다시 추가 제안">
        승인하면 이 정책은 검색·답변에서 빠지고 이전 버전으로 남아요.
      </p>
      {uses.data && uses.data.n > 0 && (
        <p className="flex items-start gap-1.5 text-xs text-amber-700 dark:text-amber-300" data-testid="retire-golden-warning"
          title={uses.data.qids.join(', ')}>
          <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          이 정책을 정답으로 쓰는 평가 문항 {uses.data.n}개 — 승인하면 측정에서 실패·판정불가로 세요(평가 문항 정리 필요)
        </p>
      )}
      {uses.data && !uses.data.checked && <p className="text-xs text-slate-500">평가 문항 파일이 서버에 없어 영향 문항은 세지 못했어요.</p>}
      {uses.error && <p className="text-xs text-rose-600 dark:text-rose-400">영향 문항을 세지 못했어요: {(uses.error as Error).message}</p>}
      <div className="space-y-1">
        <label htmlFor="pretire-reason" className="block text-xs text-slate-400">폐기 이유 (필수)</label>
        <textarea id="pretire-reason" rows={3} className={AREA} value={reason} onChange={(e) => setReason(e.target.value)} />
      </div>
    </FormModal>
  );
}
