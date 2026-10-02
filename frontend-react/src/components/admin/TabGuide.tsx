/**
 * 검토 탭 상단 안내 — "뭐 하는 화면인지 모르겠다"는 피드백(2026-10-01)으로 추가.
 * 항목이 0건일 때도 항상 보인다(빈 화면일수록 무슨 화면인지 알아야 하므로).
 * 문구는 짧게 3줄(무엇·언제 쌓이나·할 일), 배경 설명은 title 툴팁으로.
 */
export function TabGuide({ what, when, todo, detail }: { what: string; when: string; todo: string; detail?: string }) {
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-800/40 px-4 py-2.5 text-xs space-y-0.5" title={detail}>
      <p className="text-slate-200 font-medium">{what}</p>
      <p className="text-slate-400"><span className="text-slate-500">언제 쌓이나 · </span>{when}</p>
      <p className="text-slate-400"><span className="text-slate-500">할 일 · </span>{todo}</p>
    </div>
  );
}
