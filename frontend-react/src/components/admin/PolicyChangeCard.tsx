import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { AlertTriangle } from 'lucide-react';
import { approveCorrection, rejectCorrection, type CorrectionItem, type PolicyChangeTarget } from '../../api/corrections';
import { useCollectionLabel } from '../../utils/useCollectionLabel';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { FormModal } from '../ui/FormModal';

/**
 * 정정 요청의 정책 추가·폐기·분류 옮기기 제안 카드(C3 2단계, 10/27). 내용 정정과 달리 수정안 칸·AI 다시 쓰기가 없다 —
 * 무엇이 어떻게 바뀌는지만 보이고 [승인]/[반려]. 폐기는 그 정책을 정답으로 쓰는 평가 문항 수(제안 때 센 값)를 경고로.
 */

const LABEL: Record<PolicyChangeTarget, string> = { policy_add: '정책 추가', policy_retire: '정책 폐기', policy_move: '분류 옮기기' };
const path = (v: unknown) => (Array.isArray(v) && v.length ? (v as string[]).join(' › ') : '(분류 없음)');

export function PolicyChangeCard({ item, onDone, selected, onSelect, focused }: {
  item: CorrectionItem;
  onDone: (effect?: 'applied' | 'slot') => void;
  selected: boolean;
  onSelect: (on: boolean) => void;
  focused: boolean;
}) {
  const label = useCollectionLabel();
  const t = item.target_type as PolicyChangeTarget;
  const o = (item.original ?? {}) as Record<string, unknown>;
  const p = (item.proposed ?? {}) as Record<string, unknown>;
  const golden = o.golden as { n: number; qids: string[]; checked: boolean } | undefined;
  const pending = item.status === 'pending';
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState('');
  const [error, setError] = useState('');
  const [modalError, setModalError] = useState('');
  const approve = useMutation({
    mutationFn: () => approveCorrection(item.id, item.proposed ?? {}),
    onSuccess: () => { setError(''); onDone('applied'); },
    onError: (e: Error) => setError(e.message || '반영하지 못했어요.'),
  });
  const reject = useMutation({
    mutationFn: () => rejectCorrection(item.id, reason.trim()),
    onSuccess: () => { setRejecting(false); onDone('slot'); },
    onError: (e: Error) => setModalError(e.message || '반려하지 못했어요.'),
  });

  return (
    <div id={`correction-${item.id}`} data-testid={`policy-change-${item.id}`}
      className={`rounded-xl border p-4 space-y-3 ${focused ? 'border-indigo-500 ring-2 ring-indigo-500/40' : 'border-slate-700'}`}>
      <div className="flex items-center gap-2 flex-wrap text-xs">
        {pending && (
          <input type="checkbox" checked={selected} onChange={(e) => onSelect(e.target.checked)} aria-label={`정정 #${item.id} 선택`}
            title="골라서 한꺼번에 승인" className="accent-indigo-600" />
        )}
        <span className="text-slate-500">#{item.id}</span>
        <Badge color={t === 'policy_retire' ? 'rose' : 'indigo'}>{LABEL[t]}</Badge>
        {item.status === 'approved' && <Badge color="emerald">반영됨</Badge>}
        {item.status === 'rejected' && <Badge color="slate">반려</Badge>}
        <span className="text-slate-500">{label(item.namespace)}</span>
        {item.reporter && <span className="text-slate-500">· 제안 {item.reporter}</span>}
      </div>

      {t === 'policy_add' ? (
        <div className="space-y-2">
          <p className="text-sm text-slate-100">
            <span className="text-xs text-slate-500 mr-2">분류</span>{path(p.category_path)}
            {o.new_category === true && <Badge color="amber" className="ml-2">새 분류</Badge>}
          </p>
          <p className="text-sm text-slate-100"><span className="text-xs text-slate-500 mr-2">이름</span>{String(p.policy_name ?? '')}</p>
          <pre className="whitespace-pre-wrap text-xs text-slate-200 bg-slate-900/60 border border-slate-700 rounded-lg p-3 max-h-80 overflow-y-auto font-sans">{String(p.raw_body ?? '')}</pre>
          {p.remark ? <p className="text-xs text-slate-400"><span className="text-slate-500 mr-1">비고</span>{String(p.remark)}</p> : null}
        </div>
      ) : (
        <div className="space-y-2">
          <p className="text-sm text-slate-100">{String(o.policy_name ?? '')} <span className="text-xs text-slate-500">v{String(o.version ?? '')}</span></p>
          {t === 'policy_move' ? (
            <p className="text-sm text-slate-200 flex flex-wrap items-center gap-2" data-testid="move-paths">
              <span className="text-slate-400 line-through decoration-slate-500">{path(o.category_path)}</span>
              <span aria-hidden>→</span>
              <span className="font-medium">{path(p.category_path)}</span>
              {o.new_category === true && <Badge color="amber">새 분류</Badge>}
            </p>
          ) : (
            <p className="text-xs text-slate-400">{path(o.category_path)} · 승인하면 검색·답변에서 빠지고 이전 버전으로 남아요</p>
          )}
          {t === 'policy_retire' && golden && golden.n > 0 && (
            <p className="flex items-start gap-1.5 text-xs text-amber-700 dark:text-amber-300" title={golden.qids.join(', ')}>
              <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
              이 정책을 정답으로 쓰는 평가 문항 {golden.n}개(제안 때) — 승인하면 측정에서 실패·판정불가로 세요
            </p>
          )}
        </div>
      )}
      {item.user_input && <p className="text-sm text-slate-300"><span className="text-xs text-slate-500 mr-1">이유</span>“{item.user_input}”</p>}
      {item.status === 'rejected' && item.reject_reason && <p className="text-xs text-slate-400">반려 사유: {item.reject_reason}</p>}
      {error && <p role="alert" className="text-xs text-rose-600 dark:text-rose-400">{error}</p>}

      {pending && (
        <div className="flex items-center gap-2 justify-end">
          <Button variant="ghost" size="sm" disabled={approve.isPending} onClick={() => { setRejecting(true); setReason(''); setModalError(''); }}>반려…</Button>
          <Button variant="primary" size="sm" loading={approve.isPending} onClick={() => approve.mutate()}
            title={t === 'policy_add' ? '새 정책을 만들어 바로 검색에 씁니다' : t === 'policy_move' ? '새 버전(분류만 다름)으로 바뀝니다' : '현행에서 내립니다(지우지 않음)'}>
            승인
          </Button>
        </div>
      )}
      <FormModal isOpen={rejecting} title="반려" onClose={() => setRejecting(false)} onSubmit={() => reject.mutate()} submitLabel="반려"
        submitting={reject.isPending} submitDisabled={!reason.trim()} danger error={modalError}>
        <label htmlFor={`pchg-${item.id}-reason`} className="block text-xs text-slate-400">반려 사유 (제안한 사람에게 보입니다)</label>
        <textarea id={`pchg-${item.id}-reason`} rows={3} value={reason} onChange={(e) => setReason(e.target.value)} autoFocus
          className="w-full bg-slate-800 border border-slate-600 rounded-lg px-2.5 py-1.5 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 resize-y" />
      </FormModal>
    </div>
  );
}
