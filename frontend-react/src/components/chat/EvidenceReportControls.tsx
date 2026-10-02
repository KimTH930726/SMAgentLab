/**
 * 근거 카드 헤더 오른쪽 — "정정 검토 중" 배지 + "이 근거 틀림" 버튼(2026-10-01, 근거 정정 흐름).
 * Accordion의 actions 자리에 들어간다(펼침 버튼 밖 — 버튼 안에 버튼을 넣으면 무효 HTML이고 클릭이 펼침으로 샌다).
 */
export function EvidenceReportControls({ underReview, onReport }: { underReview?: boolean; onReport?: () => void }) {
  if (underReview) {
    return (
      <span
        className="text-[10px] px-1.5 py-0.5 rounded border bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-900/40 dark:text-amber-300 dark:border-amber-700/40"
        title="이 근거가 틀렸다는 신고가 있어 담당자가 검토 중입니다. 검토가 끝날 때까지는 기존 내용이 그대로 쓰입니다."
      >
        정정 검토 중
      </span>
    );
  }
  if (!onReport) return null;
  return (
    <button
      type="button"
      onClick={onReport}
      className="text-[10px] px-1.5 py-0.5 rounded border border-slate-600 text-slate-400 hover:text-rose-600 hover:border-rose-300 dark:hover:text-rose-400 dark:hover:border-rose-700"
      title="이 근거의 내용이 틀렸다면 눌러서 맞는 내용을 한 줄로 알려주세요. 담당자 확인 후 반영됩니다."
    >
      이 근거 틀림
    </button>
  );
}
