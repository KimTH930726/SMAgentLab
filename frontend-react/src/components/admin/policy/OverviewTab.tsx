import { useQuery } from '@tanstack/react-query';
import { listOverviews } from '../../../api/policy';
import { useNamespaceAccess } from '../../../utils/useNamespaceAccess';

/** 문서 개요(10/21) — 정책서 서문(문서 목적·적용 대상·영역 구성). 정책·참고표 어디에도 안 들어간 문단을 문서당 1건으로 모은 것. 읽기 전용. */
export function OverviewTab() {
  const { selectedNs } = useNamespaceAccess();
  const { data = [], isLoading, error } = useQuery({
    queryKey: ['policy-overviews', selectedNs],
    queryFn: () => listOverviews(selectedNs),
    enabled: !!selectedNs,
    staleTime: 60_000,
  });
  if (!selectedNs) return <p className="text-sm text-slate-400 py-6 text-center">왼쪽에서 업무 영역을 고르세요.</p>;
  if (error) return <p role="alert" className="text-xs text-rose-600 dark:text-rose-400">{(error as Error).message}</p>;
  if (isLoading) return <p className="text-xs text-slate-500">불러오는 중…</p>;
  if (data.length === 0) return <p className="text-sm text-slate-500 py-10 text-center">이 업무 영역엔 문서 개요가 없어요.</p>;
  return (
    <div className="space-y-3">
      {data.map((o) => (
        <article key={o.id} className="rounded-lg border border-slate-700 p-4 space-y-2">
          <h4 className="text-sm font-semibold text-slate-100" title={o.source_file ?? undefined}>{o.title}</h4>
          <p className="whitespace-pre-wrap text-sm text-slate-300">{o.body}</p>
        </article>
      ))}
    </div>
  );
}
