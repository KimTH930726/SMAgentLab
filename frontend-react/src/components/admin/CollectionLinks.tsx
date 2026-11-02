/**
 * 컬렉션 현황 화면(업무·시스템)의 이동 링크 — [지식 등록 →](쓸 수 있을 때) + [보기 →](항상). 10/23 사용자: 두 화면 같은 모양.
 * 넣는 곳은 [지식 등록] 한 곳(§15-9) — 여기선 그 컬렉션을 고른 채 운영 지식 › 등록 탭 / 목록으로 보낼 뿐.
 */
export function CollectionLinks({ editable, onGo, ingest = true }: { editable: boolean; onGo: (tab: 'ingest' | 'list') => void; ingest?: boolean }) {
  const link = 'text-xs font-medium text-indigo-600 hover:text-indigo-500 dark:text-indigo-300 dark:hover:text-indigo-200';
  return (
    <div className="flex items-center justify-end gap-3">
      {editable && ingest && (
        <button type="button" onClick={() => onGo('ingest')} title="이 컬렉션을 고르고 운영 지식 › 등록으로" className={link}>
          지식 등록 →
        </button>
      )}
      <button type="button" onClick={() => onGo('list')}
        title={editable ? '이 컬렉션을 고르고 운영 지식 목록으로' : '이 컬렉션을 고르고 운영 지식 목록으로(쓰기는 담당 파트 사람과 관리자만)'}
        className={link}>
        보기 →
      </button>
    </div>
  );
}
