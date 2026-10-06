import { create } from 'zustand';
import type { CorrectionRequestTarget, CorrectionCreated } from '../api/corrections';

/**
 * 근거 정정 입력 모드(2026-10-01) — 답변 아래 "답변 틀림"을 누르면 채팅 입력창이
 * "정정 입력 모드"로 바뀐다. 버튼(MessageItem·카드)과 입력창(ChatContainer)이 서로 다른 컴포넌트라
 * 공유 상태로 둔다. 사용자는 한 줄만 쓴다("답변 틀림"이면 대상도 AI가 찾음) — 가중치·업무구분은 묻지 않는다.
 */
export interface CorrectionTarget {
  key: string;
  targetType: CorrectionRequestTarget;
  targetId?: number;
  subId?: number;
  label: string;
}

/** "답변 틀림" — 대상을 고르지 않는다. 서버가 그 답변의 근거 중 틀린 것을 AI로 찾는다 */
export const AUTO_TARGET: CorrectionTarget = {
  key: 'auto', targetType: 'auto', label: 'AI가 틀린 근거를 찾아요',
};

export const MISSING_TARGET: CorrectionTarget = {
  key: 'missing', targetType: 'missing', label: '근거엔 없는 내용 · 빠진 내용',
};

interface ActiveCorrection {
  messageId?: number;
  namespace: string;
  /** 정정 대상 — AUTO_TARGET("답변 틀림", 서버가 판정) 또는 MISSING_TARGET(답변 id가 없을 때) */
  selected: CorrectionTarget;
}

interface CorrectionState {
  active: ActiveCorrection | null;
  /** 메시지별 접수 결과 — 그 답변 아래 "정정 신고 접수" 카드 표시용 */
  submitted: Record<number, { result: CorrectionCreated; target: CorrectionTarget }>;
  /** 이번 세션에 피드백을 보낸 답변 — 다음 질문 때 답변 목록이 다시 그려지면(스트림 사본은 has_feedback=false)
   *  피드백 버튼이 되살아나 같은 답변에 두 번 보낼 수 있었다. 컴포넌트 밖에 기억해 둔다. */
  feedbackSent: Record<number, 'negative'>;
  markFeedback: (messageId: number, kind: 'negative') => void;
  start: (ctx: ActiveCorrection) => void;
  cancel: () => void;
  markSubmitted: (messageId: number, result: CorrectionCreated, target: CorrectionTarget) => void;
}

export const useCorrectionStore = create<CorrectionState>((set) => ({
  active: null,
  submitted: {},
  feedbackSent: {},
  markFeedback: (messageId, kind) => set((s) => ({ feedbackSent: { ...s.feedbackSent, [messageId]: kind } })),
  start: (ctx) => set({ active: ctx }),
  cancel: () => set({ active: null }),
  markSubmitted: (messageId, result, target) =>
    set((s) => ({ submitted: { ...s.submitted, [messageId]: { result, target } } })),
}));
