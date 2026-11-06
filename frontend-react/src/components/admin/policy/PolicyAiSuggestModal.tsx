import { useEffect, useState } from 'react';
import type { PolicyDetail } from '../../../api/policy';
import { FormModal } from '../../ui/FormModal';
import { usePolicyProposal } from './usePolicyProposal';

/** AI 수정 제안(10/21) — 한 줄 요청 → AI가 수정안을 써서 정정 요청에 올린다. 승인해야 반영. */
export function PolicyAiSuggestModal({ isOpen, namespace, policy, onClose, onDone, sourceCheckId }: {
  isOpen: boolean;
  namespace: string;
  policy: PolicyDetail;
  onClose: () => void;
  onDone: () => void;
  sourceCheckId?: number | null;
}) {
  const [text, setText] = useState('');
  // 원문 확인 카드에서 열면 두 갈래(11/05): 표시만 달기(기본 — 글자는 코드가 그대로 보장) / 이렇게 고쳐(사람이 고칠 내용을 정할 때만)
  const fromCard = sourceCheckId != null;
  const [markOnly, setMarkOnly] = useState(true);
  const { save, error, setError } = usePolicyProposal({
    namespace, itemId: policy.item_id, source: 'policy_ai', onDone, sourceCheckId,
    done: (r) => (r.ai_failed
      ? { message: `정정 #${r.id}로 올렸지만 AI가 수정안을 만들지 못했어요 — 정정 요청에서 [AI 초안 만들기]나 [직접 수정]으로 이어 가세요.`, tone: 'warning' }
      : { message: `정정 #${r.id}로 올렸어요 — 컬렉션 관리 › 정정 요청에서 AI 수정안을 확인하고 승인하세요.`, tone: 'success' }),
  });
  useEffect(() => { if (isOpen) { setText(''); setError(''); setMarkOnly(true); } }, [isOpen, policy.item_id, setError]);
  const blank = fromCard && markOnly;

  return (
    <FormModal
      isOpen={isOpen}
      title={`AI 수정 제안 — ${policy.policy_name}`}
      onClose={onClose}
      onSubmit={() => save.mutate({ user_input: blank ? '' : text.trim() })}
      submitLabel={blank ? '표시만 달기' : 'AI에게 맡기기'}
      submitting={save.isPending}
      submitDisabled={!blank && !text.trim()}
      error={error}
    >
      {fromCard && (
        <div className="grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label="초안 방식">
          {([[true, '표시만 달기', '맞는 값을 아직 몰라요 — AI가 카드 문제에 해당하는 구간에 【확인 필요】만 달고, 원문 글자는 그대로예요'],
             [false, '이렇게 고쳐 주세요', '고칠 내용을 알아요 — 적은 대로 AI가 고친 안을 써요(원문·요청에 없는 값이 생기면 표시 초안으로 바꿔요)']] as const).map(([v, label, tip]) => (
            <button key={label} type="button" role="radio" aria-checked={markOnly === v} onClick={() => setMarkOnly(v)} data-testid={`policy-ai-mode-${v ? 'mark' : 'fix'}`}
              className={`rounded-lg border px-3 py-2 text-left text-xs ${markOnly === v ? 'border-indigo-500 bg-indigo-50 dark:border-indigo-400 dark:bg-indigo-500/15' : 'border-slate-600 hover:bg-slate-800'}`}>
              <b className="block text-sm text-slate-100">{label}</b><span className="text-slate-400">{tip}</span>
            </button>
          ))}
        </div>
      )}
      {!blank && (
        <>
          <label htmlFor="policy-ai-text" className="block text-xs text-slate-400"
            title="AI가 원문을 이 요청대로 고친 안을 써서 정정 요청에 올립니다. 승인 전엔 반영되지 않아요.">
            {fromCard ? '무엇을 어떻게 고칠지 — 맞는 값까지 적어 주세요' : '어떻게 고칠지 한 줄로 적어 주세요.'}
          </label>
          <input id="policy-ai-text" value={text} onChange={(e) => setText(e.target.value)} maxLength={1000} autoFocus
            placeholder="예: 반품 기한을 14일로 바꿔 주세요"
            className="w-full bg-slate-800 border border-slate-600 rounded-lg px-2.5 py-1.5 text-sm text-slate-200 focus:outline-none focus:border-indigo-500" />
        </>
      )}
      {save.isPending && (
        <p className="text-xs text-indigo-600 dark:text-indigo-400 animate-pulse">AI가 수정안을 쓰는 중… 30초~2분 걸려요. 창을 닫지 마세요.</p>
      )}
    </FormModal>
  );
}
