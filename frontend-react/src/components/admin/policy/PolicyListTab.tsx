import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ChevronDown, ChevronRight, Search, StickyNote } from 'lucide-react';
import { listPolicies, type PolicyRow } from '../../../api/policy';
import { useNamespaceAccess } from '../../../utils/useNamespaceAccess';
import { Badge } from '../../ui/Badge';
import { PolicyDetailModal } from './PolicyDetailModal';

/** 정책 목록 — 문서 순서 그대로, 첫 단계 절(category_path[0])로 접었다 펼친다. 검색 중엔 모두 펼침. */
export function PolicyListTab({ onOpenCorrection }: { onOpenCorrection: (id: number) => void }) {
  const { selectedNs } = useNamespaceAccess();
  const [text, setText] = useState('');
  const [q, setQ] = useState('');
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [current, setCurrent] = useState<number | null>(null);   // 상세를 연 정책 logical_id

  useEffect(() => {   // 입력이 멈추면 검색(타자마다 요청하지 않게)
    const t = setTimeout(() => setQ(text.trim()), 300);
    return () => clearTimeout(t);
  }, [text]);
  useEffect(() => { setCurrent(null); setOpen(new Set()); }, [selectedNs]);

  const { data: rows = [], isLoading, error } = useQuery({
    queryKey: ['policies', selectedNs, q],
    queryFn: () => listPolicies(selectedNs, q),
    enabled: !!selectedNs,
    staleTime: 15_000,
  });

  const groups = useMemo(() => {
    const m = new Map<string, PolicyRow[]>();
    for (const r of rows) {
      const g = r.category_path[0] ?? '(절 없음)';
      if (!m.has(g)) m.set(g, []);
      m.get(g)!.push(r);
    }
    return [...m.entries()];
  }, [rows]);
  const order = useMemo(() => rows.map((r) => r.logical_id), [rows]);
  const searching = q.length > 0;
  const toggle = (g: string) => setOpen((s) => { const n = new Set(s); if (n.has(g)) n.delete(g); else n.add(g); return n; });
  const pendingCount = rows.filter((r) => r.open_correction_id).length;

  if (!selectedNs) return <p className="text-sm text-slate-400 py-6 text-center">왼쪽에서 업무 영역을 고르세요.</p>;

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3 flex-wrap">
        <div className="relative flex-1 min-w-[240px] max-w-md">
          <Search className="w-4 h-4 text-slate-500 absolute left-2.5 top-1/2 -translate-y-1/2" />
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="정책명·원문 검색"
            aria-label="정책 검색"
            className="w-full bg-slate-800 border border-slate-600 rounded-lg pl-8 pr-3 py-1.5 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
          />
        </div>
        <span className="text-xs text-slate-500">
          {rows.length}건{pendingCount > 0 && ` · 검토 중 ${pendingCount}건`}
        </span>
      </div>

      {error && <p role="alert" className="text-xs text-rose-600 dark:text-rose-400">{(error as Error).message}</p>}
      {isLoading ? (
        <p className="text-xs text-slate-500">불러오는 중…</p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-slate-500 py-10 text-center">{searching ? '찾는 정책이 없어요.' : '이 업무 영역엔 정책서가 없어요.'}</p>
      ) : (
        <div className="space-y-2">
          {groups.map(([g, items]) => {
            const expanded = searching || open.has(g);
            const groupPending = items.filter((r) => r.open_correction_id).length;
            return (
              <div key={g} className="rounded-lg border border-slate-700">
                <button
                  onClick={() => toggle(g)}
                  aria-expanded={expanded}
                  className="w-full flex items-center gap-2 px-3 py-2 text-sm text-slate-200 hover:bg-slate-800/60 rounded-lg"
                >
                  {expanded ? <ChevronDown className="w-4 h-4 text-slate-500" /> : <ChevronRight className="w-4 h-4 text-slate-500" />}
                  <span className="font-medium text-left">{g}</span>
                  <span className="text-xs text-slate-500">{items.length}</span>
                  {groupPending > 0 && <Badge color="amber" className="ml-auto">검토 중 {groupPending}</Badge>}
                </button>
                {expanded && (
                  <ul className="border-t border-slate-700 divide-y divide-slate-700/60">
                    {items.map((r) => (
                      <li key={r.logical_id}>
                        <button
                          onClick={() => setCurrent(r.logical_id)}
                          className="w-full flex items-center gap-2 px-4 py-2 text-left hover:bg-slate-800/60"
                        >
                          <span className="text-sm text-slate-200 flex-1 min-w-0 truncate" title={r.policy_name}>{r.policy_name}</span>
                          {r.category_path.length > 1 && (
                            <span className="text-xs text-slate-500 truncate max-w-[40%]" title={r.category_path.join(' › ')}>
                              {r.category_path.slice(1).join(' › ')}
                            </span>
                          )}
                          {r.has_remark && <StickyNote className="w-3.5 h-3.5 text-slate-500 flex-shrink-0" aria-label="비고 있음" />}
                          {r.version > 1 && <span className="text-[11px] text-slate-500">v{r.version}</span>}
                          {r.open_correction_id && <Badge color="amber">정정 #{r.open_correction_id} 검토 중</Badge>}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            );
          })}
        </div>
      )}

      {current !== null && (
        <PolicyDetailModal
          namespace={selectedNs}
          logicalId={current}
          order={order}
          onNavigate={setCurrent}
          onClose={() => setCurrent(null)}
          onOpenCorrection={onOpenCorrection}
        />
      )}
    </div>
  );
}
