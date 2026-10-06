/**
 * 근거 카드 헤더 오른쪽 — "정정 검토 중" 배지(2026-10-01, 근거 정정 흐름).
 * 같은 근거가 다른 답변에 나와도 신고가 접수된 근거임을 보여준다. 카드의 "이 근거 틀림" 버튼은 2026-10-06 제거 —
 * 신고는 답변 아래 "답변 틀림" 하나로(틀린 근거는 AI가 찾고 담당자가 정정 검토에서 바꿀 수 있음).
 * Accordion의 actions 자리에 들어간다(펼침 버튼 밖 — 버튼 안에 버튼을 넣으면 무효 HTML이고 클릭이 펼침으로 샌다).
 */
export function EvidenceReportControls() {
  return (
    <span
      className="text-[10px] px-1.5 py-0.5 rounded border bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-900/40 dark:text-amber-300 dark:border-amber-700/40"
      title="이 근거가 틀렸다는 신고가 있어 담당자가 검토 중입니다. 검토가 끝날 때까지는 기존 내용이 그대로 쓰입니다."
    >
      정정 검토 중
    </span>
  );
}
