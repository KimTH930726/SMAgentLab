import { create } from 'zustand';
import type { FeedbackKind } from '../api/userFeedback';
import type { ErrorReport } from './useDialogStore';

// [피드백 보내기] 창을 어디서든 연다(11/02) — 사이드바 버튼, 오류 창 [오류 신고], 채팅 실패 답 [오류 신고].
// 창은 AppContent에 하나(FeedbackHost) — 오류 창(DialogHost)은 라우터 밖이라 여기로 열어 달라고만 한다.

interface FeedbackState {
  open: boolean;
  tab: 'send' | 'mine';
  kind: FeedbackKind;
  report: ErrorReport | null;
  /** 열 때마다 +1 — 창이 열린 채로 [오류 신고]가 다시 열어도 창이 처음부터 그려지게(리뷰 L2) */
  seq: number;
  openFeedback: (o?: { tab?: 'send' | 'mine'; kind?: FeedbackKind; report?: ErrorReport }) => void;
  closeFeedback: () => void;
}

export const useFeedbackStore = create<FeedbackState>((set) => ({
  open: false,
  tab: 'send',
  kind: 'bug',
  report: null,
  seq: 0,
  openFeedback: (o = {}) => set((s) => ({ seq: s.seq + 1, open: true, tab: o.tab ?? 'send', kind: o.report ? 'bug' : o.kind ?? 'bug', report: o.report ?? null })),
  closeFeedback: () => set({ open: false, report: null }),
}));

export const openFeedback = (o?: Parameters<FeedbackState['openFeedback']>[0]) => useFeedbackStore.getState().openFeedback(o);
