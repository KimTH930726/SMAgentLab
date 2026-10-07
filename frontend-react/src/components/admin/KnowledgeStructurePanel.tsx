import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ChevronRight, Layers, ListOrdered, FileText } from 'lucide-react';
import { getKnowledgeStructure, type KnowledgeStructure, type KnowledgeBrief } from '../../api/knowledge';

/**
 * 지식의 문서 안 위치(2026-10-07) — 분할 방식에 따라 보여줄 게 다르다.
 *  - 섹션 구조: 상위 경로 + 검색 때 이 청크와 함께 AI에게 붙는 이웃 섹션(검색 코드와 같은 규칙) + 앞뒤
 *  - 문서 순서(단락·고정 길이): 같은 문서의 앞뒤 청크(검색 때 붙지 않음)
 *  - 단건(직접 입력): 문서 구조 없음
 * 청크 경계가 이상하면(같은 내용이 앞뒤에 또 있음, 제목이 엉뚱한 곳에 붙음) 여기서 바로 보이게 하려는 것.
 */

const MODE = {
  section: { icon: Layers, label: '섹션 구조', hint: '문서 제목 구조대로 잘린 지식 — 검색되면 같은 상위 섹션의 이웃이 함께 AI에게 전달됩니다' },
  sequence: { icon: ListOrdered, label: '문서 순서', hint: '단락·길이로 잘린 지식 — 같은 문서의 앞뒤 조각을 볼 수 있습니다(검색 때 함께 붙지는 않음)' },
  single: { icon: FileText, label: '단건', hint: '직접 입력한 지식 — 다른 조각과 연결되지 않습니다' },
} as const;

function BriefRow({ b, label }: { b: KnowledgeBrief; label?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <li>
      <button type="button" onClick={() => setOpen((v) => !v)}
        className="w-full text-left flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-slate-700/50 focus:outline-none focus-visible:ring-1 focus-visible:ring-indigo-500 dark:focus-visible:ring-indigo-400">
        {label && <span className="text-[11px] text-slate-500 w-8 flex-shrink-0">{label}</span>}
        <span className="text-xs text-slate-300 truncate flex-1">{b.title || '(제목 없음)'}</span>
        <span className="text-[11px] text-slate-500 tabular-nums">#{b.id}</span>
      </button>
      {open && (
        <pre className="mx-2 mb-2 mt-0.5 whitespace-pre-wrap break-words text-[11px] leading-relaxed text-slate-400 bg-slate-900 border border-slate-700 rounded-md p-2 max-h-48 overflow-y-auto">
          {b.preview}{b.truncated ? '…' : ''}
        </pre>
      )}
    </li>
  );
}

export function KnowledgeStructurePanel({ knowledgeId }: { knowledgeId: number }) {
  const { data, isLoading, error } = useQuery<KnowledgeStructure>({
    queryKey: ['knowledge-structure', knowledgeId],
    queryFn: () => getKnowledgeStructure(knowledgeId),
    staleTime: 30_000,
  });

  if (isLoading) return <p className="text-xs text-slate-500">문서 구조 불러오는 중…</p>;
  if (error) return <p className="text-xs text-rose-600 dark:text-rose-400">문서 구조를 불러오지 못했습니다: {(error as Error).message}</p>;
  if (!data) return null;
  const m = MODE[data.mode];
  const Icon = m.icon;

  return (
    <section data-testid="knowledge-structure" className="rounded-lg border border-slate-700 bg-slate-900/40 p-3 space-y-2.5">
      <div className="flex flex-wrap items-center gap-2" title={m.hint}>
        <Icon className="w-3.5 h-3.5 text-indigo-600 dark:text-indigo-400" />
        <span className="text-xs font-medium text-slate-300">문서 구조 · {m.label}</span>
        {data.position && data.total && (
          <span className="text-[11px] text-slate-500 tabular-nums">{data.total}개 조각 중 {data.position}번째</span>
        )}
        {data.source_file && <span className="text-[11px] text-slate-500 truncate max-w-[16rem]">· {data.source_file}</span>}
      </div>

      {data.heading_path.length > 0 && (
        <nav aria-label="상위 경로" className="flex flex-wrap items-center gap-1 text-xs">
          {data.heading_path.map((h, i) => (
            <span key={`${i}-${h}`} className="inline-flex items-center gap-1">
              {i > 0 && <ChevronRight className="w-3 h-3 text-slate-500" />}
              <span className="px-1.5 py-0.5 rounded bg-slate-800 border border-slate-700 text-slate-300">{h}</span>
            </span>
          ))}
          <ChevronRight className="w-3 h-3 text-slate-500" />
          <span className="px-1.5 py-0.5 rounded border border-indigo-300 dark:border-indigo-700/60 text-indigo-700 dark:text-indigo-300">이 지식</span>
        </nav>
      )}

      {data.mode !== 'single' && (data.prev || data.next) && (
        <ul className="space-y-0.5">
          {data.prev && <BriefRow b={data.prev} label="앞" />}
          {data.next && <BriefRow b={data.next} label="뒤" />}
        </ul>
      )}

      {data.mode === 'section' && (
        <details className="group">
          <summary className="cursor-pointer text-xs text-slate-400 hover:text-slate-200 flex items-center gap-1 list-none"
            title="검색에서 이 지식이 채택되면, 같은 상위 섹션의 이 지식들이 문서 순서상 가까운 것부터(글자 수 한도 안에서) 함께 AI에게 전달됩니다">
            <ChevronRight className="w-3 h-3 group-open:rotate-90 transition-transform" />
            검색 때 함께 붙는 이웃 섹션 {data.expansion.length}개
          </summary>
          {data.expansion.length > 0 ? (
            <ul className="mt-1 space-y-0.5 max-h-56 overflow-y-auto">
              {data.expansion.map((b) => <BriefRow key={b.id} b={b} />)}
            </ul>
          ) : <p className="mt-1 text-[11px] text-slate-500">같은 상위 섹션의 다른 지식이 없습니다.</p>}
        </details>
      )}
    </section>
  );
}
