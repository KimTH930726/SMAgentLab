import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { RefreshCw, Trash2, Database, BarChart2, AlertCircle } from 'lucide-react';
import { clsx } from 'clsx';
import { getCacheStats, getCacheEntries, invalidateCache, deleteCacheEntry, getCacheConfig, setCacheConfig } from '../../api/cache';
import { getNamespaces } from '../../api/namespaces';
import { Button } from '../ui/Button';
import { PaginationInfo, PaginationNav, useClientPaging } from '../ui/Pagination';

function formatTtl(seconds: number): string {
  if (seconds <= 0) return '만료됨';
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return m > 0 ? `${m}분 ${s}초` : `${s}초`;
}

export function CachePanel() {
  const queryClient = useQueryClient();
  const [namespace, setNamespace] = useState('');
  const [confirmClear, setConfirmClear] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(30);

  const { data: namespaces = [] } = useQuery<string[]>({
    queryKey: ['namespaces'],
    queryFn: getNamespaces,
  });

  useEffect(() => {
    if (namespaces.length > 0 && !namespace) setNamespace(namespaces[0]);
  }, [namespaces, namespace]);

  const selectedNs = namespace || namespaces[0] || '';

  const { data: stats, isLoading: statsLoading, refetch: refetchStats } = useQuery({
    queryKey: ['cache-stats', selectedNs],
    queryFn: () => getCacheStats(selectedNs),
    enabled: !!selectedNs,
    refetchInterval: 30_000,
  });

  const { data: entries = [], isLoading: entriesLoading, refetch: refetchEntries } = useQuery({
    queryKey: ['cache-entries', selectedNs],
    queryFn: () => getCacheEntries(selectedNs),
    enabled: !!selectedNs,
    refetchInterval: 10_000,
    refetchOnMount: 'always',
  });

  const invalidateMutation = useMutation({
    mutationFn: () => invalidateCache(selectedNs),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['cache-stats', selectedNs] });
      queryClient.invalidateQueries({ queryKey: ['cache-entries', selectedNs] });
      setConfirmClear(false);
      alert(`캐시 ${data.deleted}건 삭제 완료`);
    },
  });

  const deleteEntryMutation = useMutation({
    mutationFn: (key: string) => deleteCacheEntry(key),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['cache-stats', selectedNs] });
      queryClient.invalidateQueries({ queryKey: ['cache-entries', selectedNs] });
    },
  });

  const { data: cacheConfig } = useQuery({
    queryKey: ['cache-config'],
    queryFn: getCacheConfig,
  });

  const toggleMutation = useMutation({
    mutationFn: (enabled: boolean) => setCacheConfig({ enabled }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['cache-config'] });
    },
  });

  const { totalPages, totalItems, slice } = useClientPaging(entries, pageSize);
  const pagedEntries = slice(page);

  useEffect(() => { setPage(1); }, [selectedNs, pageSize]);

  const handleRefresh = () => {
    refetchStats();
    refetchEntries();
  };

  return (
    <div className="space-y-6">
      {/* 헤더 */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-slate-100">Semantic Cache 현황</h2>
          <p className="text-sm text-slate-400 mt-0.5">
            유사도 임계값 이상의 질문은 LLM 없이 즉시 응답됩니다. (기본 0.92 / TTL 30분)
          </p>
        </div>
        <div className="flex items-center gap-2">
          {/* 캐시 ON/OFF 토글 */}
          <button
            onClick={() => toggleMutation.mutate(!(cacheConfig?.enabled ?? true))}
            disabled={toggleMutation.isPending}
            className={clsx(
              'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors',
              cacheConfig?.enabled !== false
                ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400 hover:bg-emerald-500/20'
                : 'bg-slate-700 border-slate-600 text-slate-400 hover:bg-slate-600',
            )}
            title={cacheConfig?.enabled !== false ? '캐시 비활성화' : '캐시 활성화'}
          >
            <span className={clsx(
              'w-2 h-2 rounded-full',
              cacheConfig?.enabled !== false ? 'bg-emerald-400' : 'bg-slate-500',
            )} />
            {cacheConfig?.enabled !== false ? 'ON' : 'OFF'}
          </button>
          <select
            value={selectedNs}
            onChange={(e) => setNamespace(e.target.value)}
            className="bg-slate-700 border border-slate-600 rounded-lg px-3 py-1.5 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
          >
            {namespaces.map((ns) => (
              <option key={ns} value={ns}>{ns}</option>
            ))}
          </select>
          <button
            onClick={handleRefresh}
            className="p-2 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-slate-700 transition-colors"
            title="새로고침"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* 캐시 비활성화 경고 */}
      {cacheConfig?.enabled === false && (
        <div className="flex items-center gap-3 px-4 py-3 bg-slate-700/50 border border-slate-600 rounded-lg">
          <AlertCircle className="w-4 h-4 text-slate-400 flex-shrink-0" />
          <p className="text-sm text-slate-300">
            Semantic Cache가 비활성화 상태입니다. 모든 질문이 LLM을 통해 처리됩니다.
          </p>
        </div>
      )}

      {/* Redis 미연결 경고 */}
      {stats && !stats.connected && (
        <div className="flex items-center gap-3 px-4 py-3 bg-amber-500/10 border border-amber-500/30 rounded-lg">
          <AlertCircle className="w-4 h-4 text-amber-400 flex-shrink-0" />
          <p className="text-sm text-amber-300">
            Redis 미연결 상태입니다. .env의 REDIS_URL을 확인하거나 ops-redis 컨테이너 상태를 점검하세요.
          </p>
        </div>
      )}

      {/* 통계 카드 */}
      <div className="grid grid-cols-2 gap-4">
        <StatCard
          icon={<Database className="w-5 h-5 text-indigo-400" />}
          label="저장된 캐시"
          value={statsLoading ? '...' : `${stats?.total_entries ?? 0}건`}
          color="indigo"
        />
        <StatCard
          icon={<BarChart2 className="w-5 h-5 text-emerald-400" />}
          label="누적 히트 수"
          value={statsLoading ? '...' : `${stats?.total_hits ?? 0}회`}
          color="emerald"
        />
      </div>

      {/* 전체 초기화 버튼 */}
      <div className="flex items-center gap-3">
        {!confirmClear ? (
          <button
            onClick={() => setConfirmClear(true)}
            disabled={!stats?.connected || (stats?.total_entries ?? 0) === 0}
            className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium text-rose-400 border border-rose-400/30 hover:bg-rose-400/10 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            <Trash2 className="w-4 h-4" />
            전체 캐시 초기화
          </button>
        ) : (
          <div className="flex items-center gap-2">
            <span className="text-sm text-slate-300">
              {selectedNs}의 캐시 {stats?.total_entries}건을 모두 삭제합니다.
            </span>
            <Button
              size="sm"
              variant="danger"
              onClick={() => invalidateMutation.mutate()}
              loading={invalidateMutation.isPending}
            >
              확인
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirmClear(false)}>
              취소
            </Button>
          </div>
        )}
        <p className="text-xs text-slate-500">지식베이스 대량 업데이트 후 캐시를 초기화하면 최신 문서 기반으로 재생성됩니다.</p>
      </div>

      {/* 캐시 목록 */}
      <div>
        <h3 className="text-sm font-semibold text-slate-300 mb-3">
          캐시된 질문 목록
          <span className="ml-2 text-xs font-normal text-slate-500">(히트 수 많은 순)</span>
        </h3>
        <PaginationInfo totalItems={totalItems} pageSize={pageSize} onPageSizeChange={setPageSize} />
        {entriesLoading ? (
          <div className="text-sm text-slate-500 animate-pulse py-6 text-center">로딩 중...</div>
        ) : entries.length === 0 ? (
          <div className="text-sm text-slate-500 py-8 text-center border border-dashed border-slate-700 rounded-lg">
            저장된 캐시 없음 — 채팅에서 검색 결과가 있는 질문에 답변하면 자동으로 캐시됩니다.
          </div>
        ) : (
          <div className="border border-slate-700 rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-700 bg-slate-800/50">
                  <th className="text-left px-4 py-2.5 text-xs font-medium text-slate-400">질문</th>
                  <th className="text-left px-4 py-2.5 text-xs font-medium text-slate-400 w-24">히트</th>
                  <th className="text-left px-4 py-2.5 text-xs font-medium text-slate-400 w-28">남은 시간</th>
                  <th className="w-12 px-4 py-2.5" />
                </tr>
              </thead>
              <tbody>
                {pagedEntries.map((entry) => (
                  <tr key={entry.key} className="border-b border-slate-700/50 last:border-0 hover:bg-slate-800/30">
                    <td className="px-4 py-3">
                      <p className="text-slate-200 truncate max-w-md">{entry.query || '(질문 없음)'}</p>
                      {entry.mapped_term && (
                        <span className="text-xs text-indigo-400 mt-0.5 block">용어: {entry.mapped_term}</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <span className={clsx(
                        'text-sm font-medium',
                        entry.hits > 10 ? 'text-emerald-400' : entry.hits > 0 ? 'text-slate-300' : 'text-slate-500',
                      )}>
                        {entry.hits}회
                      </span>
                    </td>
                    <td className="px-4 py-3 text-slate-400 text-xs font-mono">
                      {formatTtl(entry.ttl_seconds)}
                    </td>
                    <td className="px-4 py-3">
                      <button
                        onClick={() => deleteEntryMutation.mutate(entry.key)}
                        disabled={deleteEntryMutation.isPending}
                        className="p-1.5 rounded text-slate-500 hover:text-rose-400 hover:bg-rose-400/10 transition-colors"
                        title="이 캐시 삭제"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <PaginationNav page={page} totalPages={totalPages} onPageChange={setPage} />
      </div>
    </div>
  );
}

interface StatCardProps {
  icon: React.ReactNode;
  label: string;
  value: string;
  color: 'indigo' | 'emerald' | 'amber' | 'slate';
  hint?: string;
}

function StatCard({ icon, label, value, color, hint }: StatCardProps) {
  const borderColor = {
    indigo: 'border-indigo-500/30',
    emerald: 'border-emerald-500/30',
    amber: 'border-amber-500/30',
    slate: 'border-slate-600',
  }[color];

  return (
    <div className={clsx('bg-slate-800 border rounded-lg p-4', borderColor)}>
      <div className="flex items-center gap-2 mb-2">
        {icon}
        <span className="text-xs text-slate-400">{label}</span>
      </div>
      <p className="text-2xl font-bold text-slate-100">{value}</p>
      {hint && <p className="text-[11px] text-slate-500 mt-1">{hint}</p>}
    </div>
  );
}
