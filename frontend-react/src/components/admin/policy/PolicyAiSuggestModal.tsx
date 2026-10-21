import { useEffect, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ApiError } from '../../../api/client';
import { createPolicyProposal } from '../../../api/corrections';
import type { PolicyDetail } from '../../../api/policy';
import { showAlert } from '../../../store/useDialogStore';
import { FormModal } from '../../ui/FormModal';

/** AI 수정 제안(10/21) — 한 줄 요청 → AI가 수정안을 써서 정정 요청에 올린다. 승인해야 반영. */
export function PolicyAiSuggestModal({ isOpen, namespace, policy, onClose, onDone }: {
  isOpen: boolean;
  namespace: string;
  policy: PolicyDetail;
  onClose: () => void;
  onDone: () => void;
}) {
  const [text, setText] = useState('');
  const [error, setError] = useState('');
  const qc = useQueryClient();
  useEffect(() => { if (isOpen) { setText(''); setError(''); } }, [isOpen, policy.item_id]);

  const save = useMutation({
    mutationFn: () => createPolicyProposal(namespace, policy.item_id, 'policy_ai', { user_input: text.trim() }),
    onSuccess: (r) => {
      onDone();
      if (r.ai_failed) {
        void showAlert(`정정 #${r.id}로 올렸지만 AI가 수정안을 만들지 못했어요 — 정정 요청에서 [AI 초안 만들기]나 [직접 수정]으로 이어 가세요.`,
          { tone: 'warning' });
      } else {
        void showAlert(`정정 #${r.id}로 올렸어요 — 품질 › 정정 요청에서 AI 수정안을 확인하고 승인하세요.`, { tone: 'success' });
      }
    },
    onError: (e: Error) => {
      setError(e.message || '올리지 못했어요.');
      // 그 사이 다른 정정이 생겼으면(409) 상세를 새로 받아 "정정 #N 검토 중" 안내가 보이게
      if (e instanceof ApiError && e.status === 409) qc.invalidateQueries({ queryKey: ['policy', namespace] });
    },
  });

  return (
    <FormModal
      isOpen={isOpen}
      title={`AI 수정 제안 — ${policy.policy_name}`}
      onClose={onClose}
      onSubmit={() => save.mutate()}
      submitLabel="AI에게 맡기기"
      submitting={save.isPending}
      submitDisabled={!text.trim()}
      error={error}
    >
      <label htmlFor="policy-ai-text" className="block text-xs text-slate-400"
        title="AI가 원문을 이 요청대로 고친 안을 써서 정정 요청에 올립니다. 승인 전엔 반영되지 않아요.">
        어떻게 고칠지 한 줄로 적어 주세요.
      </label>
      <input id="policy-ai-text" value={text} onChange={(e) => setText(e.target.value)} maxLength={1000} autoFocus
        placeholder="예: 반품 기한을 14일로 바꿔 주세요"
        className="w-full bg-slate-800 border border-slate-600 rounded-lg px-2.5 py-1.5 text-sm text-slate-200 focus:outline-none focus:border-indigo-500" />
      {save.isPending && (
        <p className="text-xs text-indigo-600 dark:text-indigo-400 animate-pulse">AI가 수정안을 쓰는 중… 30초~2분 걸려요. 창을 닫지 마세요.</p>
      )}
    </FormModal>
  );
}
