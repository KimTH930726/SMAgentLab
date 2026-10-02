import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { postFeedback } from '../../api/feedback';
import { getMyCorrections } from '../../api/corrections';
import { useCorrectionStore, AUTO_TARGET, MISSING_TARGET } from '../../store/useCorrectionStore';

type FeedbackState = 'idle' | 'positive_sent' | 'negative_sent';

interface FeedbackSectionProps {
  namespace: string;
  question: string;
  answer: string;
  knowledgeId?: number | null;
  messageId?: number;
  /** 이미 피드백을 보낸 답변(재조회) — 신호는 다시 안 보내고, 아직 의견이 없으면 "의견 덧붙이기"만 */
  alreadySent?: boolean;
}

/**
 * 답변 피드백(2026-10-01 근거 정정 흐름) — "도움됐어요" / "답변 틀림".
 * "답변 틀림"은 예전처럼 여기서 지식 등록 폼을 열지 않는다(사용자가 바로 active 지식을 만들던 경로 제거).
 * 👎 신호(review_flag)만 보내고, 채팅 입력창을 정정 모드로 바꿔 한 줄 의견을 받는다 — 반영은 담당자 승인 후.
 */
export function FeedbackSection({ namespace, question, answer, knowledgeId, messageId, alreadySent }: FeedbackSectionProps) {
  const qc = useQueryClient();
  const startCorrection = useCorrectionStore((s) => s.start);
  // 이 답변의 정정 입력창이 열려 있는지 — 닫혀 있으면(취소·대화 이동·접수 완료) 다시 열 수 있게 버튼을 보인다
  const inputOpen = useCorrectionStore((s) => s.active != null && s.active.messageId === messageId);
  // 이 답변에 이미 의견(한 줄)까지 접수했으면 "의견 덧붙이기"를 숨긴다 — 이번 세션 접수는 스토어에서, 새로고침 뒤엔
  // 서버의 내 신고 기록으로 판단(접수했는데 또 신고하라는 듯 보이던 혼란, 2026-10-02 사용자 지적)
  const submittedHere = useCorrectionStore((s) => messageId != null && s.submitted[messageId] != null);
  const { data: mine = [] } = useQuery({
    queryKey: ['corrections-mine-all'],
    queryFn: () => getMyCorrections(false),
    enabled: messageId != null && !submittedHere,
    staleTime: 60_000,
  });
  const hasOpinion = submittedHere || mine.some((c) => c.message_id === messageId && !!c.user_input);
  const sentKind = useCorrectionStore((s) => (messageId != null ? s.feedbackSent[messageId] : undefined));
  const markFeedback = useCorrectionStore((s) => s.markFeedback);
  const [localState, setState] = useState<FeedbackState>(alreadySent ? 'negative_sent' : 'idle');
  // 다시 그려져도(remount) 이번 세션에 보낸 피드백은 유지
  const state: FeedbackState = sentKind === 'negative' ? 'negative_sent' : sentKind === 'positive' ? 'positive_sent' : localState;
  const [error, setError] = useState<string | null>(null);

  const send = async (isPositive: boolean) => {
    setError(null);
    try {
      await postFeedback({ namespace, question, answer, knowledge_id: knowledgeId ?? null, is_positive: isPositive, message_id: messageId ?? null });
      qc.invalidateQueries({ queryKey: ['knowledge'] });
      qc.invalidateQueries({ queryKey: ['stats-ns'] });
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : '피드백 전송에 실패했습니다.');
      return false;
    }
  };

  const handlePositive = async () => {
    if (await send(true)) {
      setState('positive_sent');
      if (messageId != null) markFeedback(messageId, 'positive');
    }
  };

  // 사용자는 근거를 고르지 않는다 — 한 줄만 쓰면 서버가 그 답변의 근거 중 틀린 것을 AI로 찾는다.
  // 답변 id가 없으면(저장 전) 어느 근거였는지 알 수 없어 "빠진 내용"으로 받는다.
  const openCorrection = () =>
    startCorrection({ messageId, namespace, selected: messageId != null ? AUTO_TARGET : MISSING_TARGET });

  const handleWrong = async () => {
    // 입력 모드를 먼저 연다 — 신호 전송을 기다렸다 열면 그 사이 친 글자가 질문 입력창으로 들어간다.
    openCorrection();
    setState('negative_sent');
    if (messageId != null) markFeedback(messageId, 'negative');
    await send(false);
  };

  if (state === 'positive_sent') {
    return <p className="mt-3 text-xs text-emerald-600 dark:text-emerald-400">피드백 감사합니다.</p>;
  }
  if (state === 'negative_sent') {
    return (
      <div className="mt-3 flex items-center gap-2 text-xs">
        {inputOpen ? (
          <span className="text-slate-500">아래 입력창에 맞는 내용을 한 줄로 적어주세요.</span>
        ) : hasOpinion ? null : (
          <button
            onClick={openCorrection}
            className="px-2.5 py-1 rounded-lg border border-slate-600 text-slate-400 hover:text-rose-600 hover:border-rose-300 dark:hover:text-rose-400 dark:hover:border-rose-700 transition-colors"
            title="'답변 틀림'은 이미 접수됐어요. 맞는 내용을 한 줄로 덧붙이면 AI가 어느 근거 문제인지 찾고 수정안까지 만들어 담당자에게 전달합니다."
          >
            의견 덧붙이기
          </button>
        )}
        {error && <span className="text-rose-600 dark:text-rose-400">신호 전송 실패: {error}</span>}
      </div>
    );
  }

  return (
    <div className="mt-3 flex items-center gap-2 flex-wrap">
      <button
        onClick={handlePositive}
        className="text-xs px-2.5 py-1 rounded-lg border border-slate-600 text-slate-400 hover:text-emerald-600 hover:border-emerald-300 dark:hover:text-emerald-400 dark:hover:border-emerald-700 transition-colors"
      >
        도움됐어요
      </button>
      <button
        onClick={handleWrong}
        className="text-xs px-2.5 py-1 rounded-lg border border-slate-600 text-slate-400 hover:text-rose-600 hover:border-rose-300 dark:hover:text-rose-400 dark:hover:border-rose-700 transition-colors"
        title="어디가 틀렸는지 한 줄로 알려주세요. AI가 어느 근거 문제인지 찾고, 담당자 확인 후 반영됩니다."
      >
        답변 틀림
      </button>
      {error && <span className="text-xs text-rose-600 dark:text-rose-400">{error}</span>}
    </div>
  );
}
