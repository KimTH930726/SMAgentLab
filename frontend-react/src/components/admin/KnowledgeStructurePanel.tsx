import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ChevronRight } from 'lucide-react';
import { getKnowledgeStructure, type KnowledgeStructure, type KnowledgeOutlineItem } from '../../api/knowledge';

/**
 * 원문에서의 위치(2026-10-07) — 지식 상세·수정 창.
 * "앞·뒤·이웃" 같은 내부 용어 대신 원문 목차를 그대로 보여준다(사용자 피드백: 앞·뒤·이웃이 뭔지 모르겠다).
 *  - 경로: 원문 제목 › 상위 섹션 › 이 지식
 *  - 목차: 같은 원문의 조각 전체를 원문 순서대로, 상위 경로 깊이만큼 들여쓰기, 지금 보는 지식 강조
 *  - "함께 전달": 이 지식이 검색되면 AI에게 같이 전달되는 조각(검색 코드와 같은 규칙) — 설명은 툴팁
 *  - 직접 입력: 원문 목차 없음 한 줄
 * 청크 경계가 이상하면(같은 내용이 위아래에 또 있음, 제목이 엉뚱한 곳에 붙음) 목차에서 바로 보이게 하려는 것.
 */

const MAX_VISIBLE = 12;

function OutlineRow({ item }: { item: KnowledgeOutlineItem }) {
  const [open, setOpen] = useState(false);
  const pad = Math.min(item.depth, 4) * 12;
  return (
    <li>
      <button type="button" onClick={() => !item.is_self && setOpen((v) => !v)} disabled={item.is_self}
        aria-expanded={item.is_self ? undefined : open}
        title={item.is_self ? '지금 보는 지식' : '눌러서 내용 보기'}
        style={{ paddingLeft: 8 + pad }}
        className={`w-full text-left flex items-center gap-2 pr-2 py-1.5 rounded-md text-xs focus:outline-none focus-visible:ring-1 focus-visible:ring-indigo-500 dark:focus-visible:ring-indigo-400 ${
          item.is_self
            ? 'bg-indigo-50 text-indigo-800 font-medium dark:bg-indigo-950/40 dark:text-indigo-200 cursor-default'
            : 'text-slate-300 hover:bg-slate-700/50'}`}>
        <span className="truncate flex-1">{item.title || '(제목 없음)'}</span>
        {item.is_self && <span className="text-[11px] flex-shrink-0">지금 보는 지식</span>}
        {item.attached && (
          <span className="text-[10px] px-1.5 py-0.5 rounded flex-shrink-0 bg-emerald-100 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300"
            title="이 지식이 검색되면 이 조각도 AI에게 함께 전달됩니다(같은 상위 섹션, 원문에서 가까운 순, 글자 수 한도 안 — 같은 원문의 다른 지식이 함께 검색되면 조금 달라질 수 있음)">
            함께 전달
          </span>
        )}
      </button>
      {open && (
        <pre style={{ marginLeft: 8 + pad }}
          className="mr-2 mb-2 mt-0.5 whitespace-pre-wrap break-words text-[11px] leading-relaxed text-slate-400 bg-slate-900 border border-slate-700 rounded-md p-2 max-h-48 overflow-y-auto">
          {item.preview}{item.truncated ? '…' : ''}
        </pre>
      )}
    </li>
  );
}

export function KnowledgeStructurePanel({ knowledgeId }: { knowledgeId: number }) {
  const [showAll, setShowAll] = useState(false);
  const { data, isLoading, error } = useQuery<KnowledgeStructure>({
    queryKey: ['knowledge-structure', knowledgeId],
    queryFn: () => getKnowledgeStructure(knowledgeId),
    staleTime: 30_000,
  });

  if (isLoading) return <p className="text-xs text-slate-500">원문 위치 불러오는 중…</p>;
  if (error) return <p className="text-xs text-rose-600 dark:text-rose-400">원문 위치를 불러오지 못했습니다: {(error as Error).message}</p>;
  if (!data) return null;

  if (data.mode === 'single') {
    return (
      <p data-testid="knowledge-structure" className="text-[11px] text-slate-500">
        원문 위치: 직접 입력한 지식(원문 목차 없음)
      </p>
    );
  }

  const outline = data.outline ?? [];
  // 지금 보는 지식이 목차에 없을 수 있다(승인 대기 등 활성 아님 — 리뷰 2026-10-08) → 맨 앞부터
  const selfIdx = Math.max(0, outline.findIndex((o) => o.is_self));
  const where = data.position ? `${data.total}개 중 ${data.position}번째` : `조각 ${data.total}개`;
  // 긴 원문은 지금 보는 지식 주변만 먼저 보여준다
  const start = showAll || outline.length <= MAX_VISIBLE ? 0 : Math.max(0, Math.min(selfIdx - 4, outline.length - MAX_VISIBLE));
  const visible = showAll ? outline : outline.slice(start, start + MAX_VISIBLE);
  const attachedN = outline.filter((o) => o.attached).length;

  // 지식 내용이 주인공 — 원문 위치는 접힌 한 줄(경로·몇 번째), 펼치면 목차(사용자 피드백 2026-10-07)
  return (
    <details data-testid="knowledge-structure" className="group rounded-lg border border-slate-700 bg-slate-900/40">
      <summary className="cursor-pointer list-none [&::-webkit-details-marker]:hidden flex items-center gap-1.5 px-3 py-2 text-xs text-slate-400 hover:text-slate-200 min-w-0"
        title="같은 원문의 다른 조각과 이 지식이 검색될 때 함께 전달되는 조각을 봅니다">
        <ChevronRight className="w-3.5 h-3.5 flex-shrink-0 group-open:rotate-90 transition-transform" />
        <span className="flex-shrink-0 text-slate-500">원문 위치</span>
        <span className="truncate text-slate-300">{data.heading_path.length > 0 ? data.heading_path.join(' › ') : (data.source_file || '원문')}</span>
        <span className="flex-shrink-0 text-[11px] text-slate-500 tabular-nums">· {where}</span>
        {data.mode === 'section' && attachedN > 0 && (
          <span className="ml-auto flex-shrink-0 text-[10px] px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300"
            title="이 지식이 검색되면 AI에게 함께 전달되는 조각 수">함께 전달 {attachedN}</span>
        )}
      </summary>
      <div className="px-3 pb-3 space-y-1">
        {data.source_file && <p className="text-[11px] text-slate-500 truncate">원문: {data.source_file}</p>}
        <ol className="space-y-0.5 max-h-72 overflow-y-auto">
          {visible.map((o) => <OutlineRow key={o.id} item={o} />)}
        </ol>
        {outline.length > MAX_VISIBLE && (
          <button type="button" onClick={() => setShowAll((v) => !v)}
            className="mt-1 text-[11px] text-indigo-700 dark:text-indigo-300 hover:underline">
            {showAll ? '주변만 보기' : `전체 목차 보기 (${outline.length}개)`}
          </button>
        )}
      </div>
    </details>
  );
}
