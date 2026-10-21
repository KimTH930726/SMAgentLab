/**
 * 지금 고른 업무 영역 표시 — 고르는 곳은 사이드바 한 곳(앱 공용 값, 10/21). 화면마다 있던 선택기를 이 표시로 바꿨다.
 */
export function CurrentDomain({ name }: { name: string }) {
  return (
    <div className="text-sm" title="업무 영역은 왼쪽 사이드바에서 바꿔요" data-testid="current-domain">
      <span className="text-xs text-slate-500 mr-1.5">업무 영역</span>
      <span className="font-medium text-slate-200">{name || '없음'}</span>
    </div>
  );
}
