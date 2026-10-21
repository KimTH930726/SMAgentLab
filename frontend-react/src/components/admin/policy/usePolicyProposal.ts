import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ApiError } from '../../../api/client';
import { createPolicyProposal, type PolicyProposalCreated, type PolicyProposalSource } from '../../../api/corrections';
import { showAlert } from '../../../store/useDialogStore';

type ProposalBody = Parameters<typeof createPolicyProposal>[3];

/** 정책 화면의 수정 제안 올리기 — 직접 수정·AI 제안 창이 같은 저장·409 처리·완료 안내를 쓴다(10/21 리뷰 C4, 두 창에 복사돼 있었다).
 *  done = 올린 결과에 맞는 완료 안내 문구와 색. 실패는 창 안의 error로(409면 상세를 새로 받아 "정정 #N 검토 중"이 보이게). */
export function usePolicyProposal({ namespace, itemId, source, onDone, done }: {
  namespace: string;
  itemId: number;
  source: PolicyProposalSource;
  onDone: () => void;
  done: (r: PolicyProposalCreated) => { message: string; tone: 'success' | 'warning' };
}) {
  const [error, setError] = useState('');
  const qc = useQueryClient();
  const save = useMutation({
    mutationFn: (body: ProposalBody) => createPolicyProposal(namespace, itemId, source, body),
    onSuccess: (r) => {
      onDone();
      const { message, tone } = done(r);
      void showAlert(message, { tone });
    },
    onError: (e: Error) => {
      setError(e.message || '올리지 못했어요.');
      // 그 사이 다른 정정이 생겼으면(409) 상세를 새로 받아 "정정 #N 검토 중" 안내가 보이게
      if (e instanceof ApiError && e.status === 409) qc.invalidateQueries({ queryKey: ['policy', namespace] });
    },
  });
  return { save, error, setError };
}
