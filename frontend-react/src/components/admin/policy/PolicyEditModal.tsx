import { useEffect, useState } from 'react';
import { Sparkles } from 'lucide-react';
import type { PolicyDetail } from '../../../api/policy';
import { FormModal } from '../../ui/FormModal';
import { usePolicyProposal } from './usePolicyProposal';

const AREA = 'w-full bg-slate-800 border border-slate-600 rounded-lg px-2.5 py-1.5 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 resize-y';

/** 정책 직접 수정(10/21) — 원문·비고를 고쳐 정정 요청에 올린다. 저장해도 바로 바뀌지 않고, 정정 요청에서 승인하면 새 버전. */
export function PolicyEditModal({ isOpen, namespace, policy, onClose, onDone, sourceCheckId, onAiDraft }: {
  isOpen: boolean;
  namespace: string;
  policy: PolicyDetail;
  onClose: () => void;
  onDone: () => void;
  sourceCheckId?: number | null;
  /** [AI 초안 받기] — 어떻게 고칠지 한 줄 → AI가 수정안을 쓴다(11/04: [AI 수정 제안]·[직접 수정] 두 버튼을 [수정 제안] 하나로 합침) */
  onAiDraft?: () => void;
}) {
  const [body, setBody] = useState(policy.raw_body);
  const [remark, setRemark] = useState(policy.remark ?? '');
  const { save, error, setError } = usePolicyProposal({
    namespace, itemId: policy.item_id, source: 'policy_direct', onDone, sourceCheckId,
    done: (r) => ({ message: `정정 #${r.id}로 올렸어요 — 컬렉션 관리 › 정정 요청에서 승인하면 새 버전으로 반영돼요.`, tone: 'success' }),
  });
  useEffect(() => {   // 열 때마다 지금 버전으로 채운다(다음 정책으로 넘긴 뒤 다시 열어도)
    if (isOpen) { setBody(policy.raw_body); setRemark(policy.remark ?? ''); setError(''); }
  }, [isOpen, policy.item_id, policy.raw_body, policy.remark, setError]);
  const unchanged = body === policy.raw_body && remark.trim() === (policy.remark ?? '').trim();

  return (
    <FormModal
      isOpen={isOpen}
      title={`수정 제안 — ${policy.policy_name}`}
      onClose={onClose}
      onSubmit={() => save.mutate({ proposed: { raw_body: body, remark } })}
      submitLabel="정정 요청에 올리기"
      submitting={save.isPending}
      submitDisabled={unchanged || !body.trim()}
      error={error}
      maxWidth="max-w-3xl"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-slate-400" title="한 정책엔 검토 중 정정이 하나만 있을 수 있어요. 승인 전엔 검색·답변에 반영되지 않습니다.">
          원문을 직접 고치거나 AI 초안을 받아 고쳐요 — 올리면 정정 요청에 가고, 승인해야 반영돼요.
        </p>
        {onAiDraft && (
          <button type="button" onClick={onAiDraft} data-testid="policy-ai-draft"
            title="어떻게 고칠지 한 줄 → AI가 수정안을 써서 정정 요청에 올려요"
            className="inline-flex items-center gap-1 rounded-md border border-indigo-300 px-2.5 py-1 text-xs text-indigo-700 hover:bg-indigo-50 dark:border-indigo-500/50 dark:text-indigo-300 dark:hover:bg-indigo-500/10">
            <Sparkles className="w-3.5 h-3.5" />AI 초안 받기
          </button>
        )}
      </div>
      <div className="space-y-1">
        <label htmlFor="policy-edit-body" className="block text-xs text-slate-400">원문</label>
        <textarea id="policy-edit-body" rows={14} value={body} onChange={(e) => setBody(e.target.value)} className={AREA} />
      </div>
      <div className="space-y-1">
        <label htmlFor="policy-edit-remark" className="block text-xs text-slate-400" title="답변에 쓰고 검색엔 안 씀 — 비우면 비고를 지웁니다">비고</label>
        <textarea id="policy-edit-remark" rows={3} value={remark} onChange={(e) => setRemark(e.target.value)} className={AREA} />
      </div>
    </FormModal>
  );
}
