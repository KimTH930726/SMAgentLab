import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { RefreshCw, CheckCircle2, AlertTriangle, X } from 'lucide-react';
import {
  getVocStats, getVocClusters, getVocClusterMembers,
  type VocCluster, type VocClusterMember,
} from '../../api/emailVoc';
import { createKnowledge } from '../../api/knowledge';
import { getCategories } from '../../api/namespaces';
import { Badge } from '../ui/Badge';
import { Modal } from '../ui/Modal';
import { Button } from '../ui/Button';
import { TagInput } from '../ui/TagInput';
import { DonutChart, type DonutSegment } from '../ui/DonutChart';
import { PaginationInfo, PaginationNav, useClientPaging } from '../ui/Pagination';
import { CATEGORY_LABEL, SEVERITY_LABEL, formatRelative } from './VocEmailPanel';

// Teams 카드(teams_notify.py의 _SEVERITY_COLOR)와 동일한 4단계 팔레트 — 어디서
// 봐도 같은 심각도가 같은 색으로 읽히도록 색상 값 자체를 맞춘다.
const SEVERITY_HEX: Record<string, string> = {
  low: '#6B7280', medium: '#0891B2', high: '#D97706', urgent: '#DC2626',
};
const CATEGORY_HEX: Record<string, string> = {
  system_error: '#F43F5E', user_mistake: '#F59E0B', uncertain: '#6366F1', not_it_related: '#64748B',
};
const CLUSTER_PAGE_SIZE = 10;

// ── 지식 등록 모달 — 기존 통계 화면(StatsPanel)의 지식 등록 폼과 동일한 필드 구성.
// 반복 유형에 대한 해결방안이 아직 없을 때 이 자리에서 바로 등록할 수 있게 한다.

function ClusterKnowledgeRegisterModal({
  open, onClose, cluster, namespace, onSuccess,
}: {
  open: boolean; onClose: () => void; cluster: VocCluster | null; namespace: string; onSuccess: () => void;
}) {
  const [containerNames, setContainerNames] = useState<string[]>([]);
  const [targetTables, setTargetTables] = useState<string[]>([]);
  const [content, setContent] = useState('');
  const [queryTemplate, setQueryTemplate] = useState('');
  const [baseWeight, setBaseWeight] = useState(1.0);
  const [category, setCategory] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { data: categories = [] } = useQuery({
    queryKey: ['categories', namespace],
    queryFn: () => getCategories(namespace),
    enabled: !!namespace,
    staleTime: 0,
  });
  const sortedCategories = [...categories].sort((a, b) =>
    a.name === '공통지식' ? -1 : b.name === '공통지식' ? 1 : a.name.localeCompare(b.name)
  );

  useEffect(() => {
    if (open && cluster) {
      setContainerNames(['VOC 반복 유형']);
      setTargetTables([]);
      setContent(`[반복 유형] ${cluster.representative_subject}\n\n해결 방안: `);
      setQueryTemplate('');
      setBaseWeight(1.0);
      setCategory(sortedCategories[0]?.name ?? '');
      setError(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, cluster, sortedCategories.length]);

  const weightLabel = (w: number) => w >= 2 ? '높음' : w >= 1.5 ? '보통' : '기본';

  const handleSubmit = async () => {
    if (!cluster || !content.trim() || !category) return;
    setSubmitting(true);
    setError(null);
    try {
      const created = await createKnowledge({
        namespace, container_name: containerNames.join(', ') || 'VOC 반복 유형',
        target_tables: targetTables, content, query_template: queryTemplate || null,
        base_weight: baseWeight, category,
      });
      if (created.pending_review) {
        window.alert('등록하신 지식이 기존 지식과 유사도가 높아 승인 대기 상태로 등록되었습니다. 관리자 승인 후 검색에 반영됩니다.');
      }
      onSuccess();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : '등록 실패');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal isOpen={open} onClose={onClose} title="반복 유형 해결방안 등록" maxWidth="max-w-xl">
      <div className="space-y-3">
        <div>
          <label className="block text-xs font-medium text-slate-400 mb-1">
            컨테이너명 <span className="text-slate-500 font-normal ml-1">(Enter 또는 쉼표로 추가)</span>
          </label>
          <TagInput tags={containerNames} onChange={setContainerNames} placeholder="컨테이너명 입력..." color="cyan" />
        </div>

        <div>
          <label className="block text-xs font-medium text-slate-400 mb-1">
            대상 테이블 <span className="text-slate-500 font-normal ml-1">(Enter 또는 쉼표로 추가)</span>
          </label>
          <TagInput tags={targetTables} onChange={setTargetTables} placeholder="테이블명 입력..." color="indigo" />
        </div>

        <div>
          <label className="block text-xs font-medium text-slate-400 mb-1">
            내용 <span className="text-rose-400">*</span>
          </label>
          <textarea
            rows={8}
            value={content}
            onChange={(e) => setContent(e.target.value)}
            className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 resize-y min-h-[180px] leading-relaxed"
            placeholder="이 반복 유형에 대한 해결 방안을 작성하세요"
          />
        </div>

        <div>
          <label className="block text-xs font-medium text-slate-400 mb-1">쿼리 템플릿 (선택)</label>
          <textarea
            rows={3}
            value={queryTemplate}
            onChange={(e) => setQueryTemplate(e.target.value)}
            className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm font-mono text-slate-200 focus:outline-none focus:border-indigo-500 resize-y min-h-[80px]"
            placeholder="SELECT ..."
          />
        </div>

        {sortedCategories.length > 0 ? (
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">
              업무구분 <span className="text-rose-400">*</span>
            </label>
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
            >
              {sortedCategories.map((c) => <option key={c.id} value={c.name}>{c.name}</option>)}
            </select>
          </div>
        ) : (
          <p className="text-xs text-amber-400">
            이 파트에 등록된 업무구분이 없어 지식을 등록할 수 없습니다. 기준정보관리에서 업무구분을 먼저 추가해주세요.
          </p>
        )}

        <div>
          <label className="block text-xs font-medium text-slate-400 mb-1">
            문서 우선순위:{' '}
            <span className={`font-medium ${
              baseWeight >= 2 ? 'text-emerald-400' : baseWeight >= 1.5 ? 'text-indigo-400' : 'text-slate-300'
            }`}>
              {baseWeight.toFixed(1)} — {weightLabel(baseWeight)}
            </span>
          </label>
          <input
            type="range" min={0} max={3} step={0.1} value={baseWeight}
            onChange={(e) => setBaseWeight(parseFloat(e.target.value))}
            className="w-full accent-indigo-500"
          />
          <p className="text-[11px] text-slate-400 mt-1">1.0=기본 · 1.5+=보통 · 2.0+=높음(핵심 문서, 항상 상위 노출)</p>
        </div>

        {error && <p className="text-xs text-rose-400">{error}</p>}

        <div className="flex gap-2 justify-end pt-1">
          <Button variant="ghost" size="sm" onClick={onClose}>취소</Button>
          <Button
            variant="primary" size="sm" loading={submitting}
            disabled={!content.trim() || !category}
            onClick={handleSubmit}
          >
            지식 등록
          </Button>
        </div>
      </div>
    </Modal>
  );
}

// ── 클러스터 멤버 모달 — 개별 VOC의 실제 분류 결과를 보여줘 LLM 판단을 확인할 수 있게 한다 ──

function ClusterMembersModal({
  open, onClose, cluster, namespace,
}: {
  open: boolean; onClose: () => void; cluster: VocCluster | null; namespace: string;
}) {
  const { data: members = [], isLoading } = useQuery({
    queryKey: ['voc-cluster-members', namespace, cluster?.id],
    queryFn: () => getVocClusterMembers(namespace, cluster!.id),
    enabled: open && !!namespace && !!cluster,
  });

  return (
    <Modal isOpen={open} onClose={onClose} title={cluster ? `반복 유형 상세 (${cluster.member_count}건)` : ''} maxWidth="max-w-2xl">
      {cluster && cluster.category_breakdown.length > 1 && (
        <div className="mb-3 bg-amber-500/10 border border-amber-500/30 rounded-lg px-3 py-2">
          <p className="text-xs text-amber-400">
            ⚠️ 같은 반복 유형인데 LLM이 서로 다른 분류를 내렸습니다 —{' '}
            {cluster.category_breakdown.map((c) => `${CATEGORY_LABEL[c.category ?? ''] ?? c.category} ${c.count}건`).join(', ')}
          </p>
        </div>
      )}
      {isLoading && <div className="text-center py-10 text-slate-500 animate-pulse">로딩 중...</div>}
      <div className="space-y-2 max-h-[60vh] overflow-y-auto pr-1">
        {members.map((m: VocClusterMember) => (
          <div key={m.id} className="bg-slate-900/60 border border-slate-700 rounded-xl px-4 py-3">
            <p className="text-sm text-slate-200">{m.subject || '(제목 없음)'}</p>
            <div className="flex items-center gap-2 mt-1.5 flex-wrap">
              {m.category && <Badge color="slate">{CATEGORY_LABEL[m.category] ?? m.category}</Badge>}
              {m.severity && <Badge color="rose">{SEVERITY_LABEL[m.severity] ?? m.severity}</Badge>}
              <span className="text-xs text-slate-500">{m.sender}</span>
              <span className="text-xs text-slate-500">{new Date(m.created_at).toLocaleString('ko-KR')}</span>
            </div>
            {m.reasoning && (
              <p className="text-xs text-slate-400 mt-2 leading-relaxed">{m.reasoning}</p>
            )}
          </div>
        ))}
      </div>
    </Modal>
  );
}

// ── VocStatsPanel ─────────────────────────────────────────────────────────────

export function VocStatsPanel({ namespace }: { namespace: string }) {
  const qc = useQueryClient();
  const [selectedCluster, setSelectedCluster] = useState<VocCluster | null>(null);
  const [registerCluster, setRegisterCluster] = useState<VocCluster | null>(null);
  const [categoryFilter, setCategoryFilter] = useState<string | null>(null);
  const [severityFilter, setSeverityFilter] = useState<string | null>(null);
  const [page, setPage] = useState(1);

  const { data: stats, isLoading: statsLoading, refetch: refetchStats } = useQuery({
    queryKey: ['voc-stats', namespace],
    queryFn: () => getVocStats(namespace),
    enabled: !!namespace,
    staleTime: 10_000,
  });
  const { data: clusters = [], isLoading: clustersLoading, refetch: refetchClusters } = useQuery({
    queryKey: ['voc-clusters', namespace],
    queryFn: () => getVocClusters(namespace),
    enabled: !!namespace,
    staleTime: 10_000,
  });

  const refetchAll = () => { refetchStats(); refetchClusters(); };

  const categorySegments: DonutSegment[] = (stats?.category_distribution ?? [])
    .filter((c) => c.category)
    .map((c) => ({
      value: c.count, color: CATEGORY_HEX[c.category!] ?? '#64748B',
      label: CATEGORY_LABEL[c.category!] ?? c.category!,
    }));
  const severitySegments: DonutSegment[] = (stats?.severity_distribution ?? [])
    .filter((s) => s.severity)
    .map((s) => ({
      value: s.count, color: SEVERITY_HEX[s.severity!] ?? '#64748B',
      label: SEVERITY_LABEL[s.severity!] ?? s.severity!,
    }));
  const categoryKeys = (stats?.category_distribution ?? []).filter((c) => c.category).map((c) => c.category!);
  const severityKeys = (stats?.severity_distribution ?? []).filter((s) => s.severity).map((s) => s.severity!);
  const selectedCategoryIdx = categoryFilter ? categoryKeys.indexOf(categoryFilter) : null;
  const selectedSeverityIdx = severityFilter ? severityKeys.indexOf(severityFilter) : null;

  // IT와 무관한 CS성 불만(not_it_related)은 애초에 "시스템 해결방안"을 등록할
  // 대상이 아니다 — 반복 유형 목록은 기본적으로 이걸 빼고 "진짜 시스템적으로
  // 의미 있는 VOC"만 보여준다. 단, 관리자가 유형 분포 도넛에서 "IT 무관"을 직접
  // 클릭해 들여다보고 싶어하면 그때는 예외적으로 보여준다(명시적 선택 우선).
  const meaningfulClusters = clusters.filter((c) => c.primary_category !== 'not_it_related');
  const excludedNotItCount = clusters.length - meaningfulClusters.length;
  const baseClusters = categoryFilter ? clusters : meaningfulClusters;

  // 도넛 세그먼트 클릭 → 반복 유형 목록을 그 유형만 보이도록 필터링. 어떤 VOC들이
  // 어떤 유형으로 반복되고 있는지를 통계(전체)에서 상세(반복 유형)로 바로 이어보게 한다.
  const filteredClusters = baseClusters.filter((c) => {
    if (categoryFilter && c.primary_category !== categoryFilter) return false;
    if (severityFilter && c.primary_severity !== severityFilter) return false;
    return true;
  });
  const { totalPages, totalItems, slice } = useClientPaging(filteredClusters, CLUSTER_PAGE_SIZE);
  const pagedClusters = slice(page);

  useEffect(() => { setPage(1); }, [categoryFilter, severityFilter, namespace]);

  const uncoveredCount = meaningfulClusters.filter((c) => !c.has_knowledge_coverage).length;
  const inconsistentCount = meaningfulClusters.filter((c) => c.category_breakdown.length > 1).length;

  if (!namespace) {
    return <div className="text-center py-10 text-slate-500">파트를 선택하세요.</div>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-200">VOC 통계</h2>
        <button onClick={refetchAll} className="text-xs text-indigo-400 hover:text-indigo-300 flex items-center gap-1">
          <RefreshCw className="w-3.5 h-3.5" />새로고침
        </button>
      </div>

      {(statsLoading || clustersLoading) && <div className="text-center py-6 text-slate-500 animate-pulse">로딩 중...</div>}

      {stats && (
        <div className="grid grid-cols-2 gap-4">
          <div className="bg-slate-800 border border-slate-700 rounded-xl p-5">
            <h3 className="text-sm font-semibold text-slate-300 mb-1">유형 분포</h3>
            <p className="text-[11px] text-slate-500 mb-3">클릭하면 아래 반복 유형을 그 분류만 필터링합니다</p>
            <div className="flex items-center gap-5">
              <DonutChart
                segments={categorySegments} centerTop={`${stats.total}`} centerBottom="전체"
                selectedIndex={selectedCategoryIdx}
                onSegmentClick={(i) => setCategoryFilter((prev) => prev === categoryKeys[i] ? null : categoryKeys[i])}
              />
              <div className="space-y-1.5 flex-1 min-w-0">
                {categorySegments.map((seg, i) => (
                  <button
                    key={seg.label}
                    onClick={() => setCategoryFilter((prev) => prev === categoryKeys[i] ? null : categoryKeys[i])}
                    className={`flex items-center gap-2 w-full text-left rounded px-1 -mx-1 ${categoryFilter === categoryKeys[i] ? 'bg-slate-700/60' : 'hover:bg-slate-700/30'}`}
                  >
                    <span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: seg.color }} />
                    <span className="text-xs text-slate-400 flex-1 truncate">{seg.label}</span>
                    <span className="text-xs font-semibold text-slate-200">{seg.value}</span>
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className="bg-slate-800 border border-slate-700 rounded-xl p-5">
            <h3 className="text-sm font-semibold text-slate-300 mb-1">심각도 분포</h3>
            <p className="text-[11px] text-slate-500 mb-3">클릭하면 아래 반복 유형을 그 심각도만 필터링합니다</p>
            <div className="flex items-center gap-5">
              <DonutChart
                segments={severitySegments} centerTop={`${stats.total}`} centerBottom="전체"
                selectedIndex={selectedSeverityIdx}
                onSegmentClick={(i) => setSeverityFilter((prev) => prev === severityKeys[i] ? null : severityKeys[i])}
              />
              <div className="space-y-1.5 flex-1 min-w-0">
                {severitySegments.map((seg, i) => (
                  <button
                    key={seg.label}
                    onClick={() => setSeverityFilter((prev) => prev === severityKeys[i] ? null : severityKeys[i])}
                    className={`flex items-center gap-2 w-full text-left rounded px-1 -mx-1 ${severityFilter === severityKeys[i] ? 'bg-slate-700/60' : 'hover:bg-slate-700/30'}`}
                  >
                    <span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: seg.color }} />
                    <span className="text-xs text-slate-400 flex-1 truncate">{seg.label}</span>
                    <span className="text-xs font-semibold text-slate-200">{seg.value}</span>
                  </button>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      <div>
        <div className="flex items-center gap-2 mb-1 flex-wrap">
          <h3 className="text-sm font-semibold text-slate-300">반복 유형</h3>
          {uncoveredCount > 0 && <Badge color="amber">해결방안 미등록 {uncoveredCount}건</Badge>}
          {inconsistentCount > 0 && (
            <Badge color="rose" className="flex items-center gap-1">
              <AlertTriangle className="w-3 h-3" />LLM 분류 불일치 {inconsistentCount}건
            </Badge>
          )}
          {(categoryFilter || severityFilter) && (
            <button
              onClick={() => { setCategoryFilter(null); setSeverityFilter(null); }}
              className="text-xs text-slate-400 hover:text-slate-200 flex items-center gap-1 ml-auto"
            >
              <X className="w-3 h-3" />
              필터 해제 ({categoryFilter ? CATEGORY_LABEL[categoryFilter] ?? categoryFilter : ''}{categoryFilter && severityFilter ? ' · ' : ''}{severityFilter ? SEVERITY_LABEL[severityFilter] ?? severityFilter : ''})
            </button>
          )}
        </div>
        <p className="text-[11px] text-slate-500 mb-2">
          시스템적으로 해결방안이 필요한 유형만 표시 — IT 무관 CS성 불만 {excludedNotItCount}건은 기본 제외{' '}
          {excludedNotItCount > 0 && !categoryFilter && '(위 유형 분포에서 "IT 무관"을 클릭하면 볼 수 있습니다)'}
        </p>

        {filteredClusters.length === 0 && !clustersLoading ? (
          <div className="text-sm text-slate-500 py-6 text-center border border-dashed border-slate-700 rounded-lg">
            {clusters.length === 0 ? '아직 감지된 반복 유형이 없습니다.' : '이 필터에 해당하는 반복 유형이 없습니다.'}
          </div>
        ) : (
          <>
            <PaginationInfo totalItems={totalItems} className="mb-2" />
            <div className="border border-slate-700 rounded-lg overflow-hidden">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-slate-700 bg-slate-800/50">
                    <th className="text-left px-3 py-2 text-slate-400">대표 제목</th>
                    <th className="text-left px-3 py-2 text-slate-400">건수</th>
                    <th className="text-left px-3 py-2 text-slate-400">주요 분류</th>
                    <th className="text-left px-3 py-2 text-slate-400">최근 발생</th>
                    <th className="text-left px-3 py-2 text-slate-400">해결방안</th>
                    <th className="text-left px-3 py-2 text-slate-400"></th>
                  </tr>
                </thead>
                <tbody>
                  {pagedClusters.map((c) => (
                    <tr key={c.id} className="border-b border-slate-700/50 last:border-0 hover:bg-slate-700/30">
                      <td className="px-3 py-2">
                        <button className="text-slate-200 hover:text-indigo-400 text-left" onClick={() => setSelectedCluster(c)}>
                          {c.representative_subject || '(제목 없음)'}
                        </button>
                      </td>
                      <td className="px-3 py-2 text-slate-300">{c.member_count}건</td>
                      <td className="px-3 py-2">
                        <div className="flex items-center gap-1">
                          {c.primary_category && <Badge color="slate">{CATEGORY_LABEL[c.primary_category] ?? c.primary_category}</Badge>}
                          {c.category_breakdown.length > 1 && (
                            <span title="이 반복 유형에서 LLM이 서로 다른 분류를 내린 적이 있습니다">
                              <AlertTriangle className="w-3.5 h-3.5 text-amber-400" />
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="px-3 py-2 text-slate-400">{formatRelative(c.last_seen_at)}</td>
                      <td className="px-3 py-2">
                        {c.has_knowledge_coverage ? (
                          <Badge color="emerald">
                            <span className="flex items-center gap-1"><CheckCircle2 className="w-3 h-3" />등록됨 (유사도 {c.matched_knowledge_similarity})</span>
                          </Badge>
                        ) : (
                          <Badge color="amber">
                            <span className="flex items-center gap-1"><AlertTriangle className="w-3 h-3" />등록 필요</span>
                          </Badge>
                        )}
                      </td>
                      <td className="px-3 py-2">
                        {!c.has_knowledge_coverage && (
                          <Button variant="ghost" size="sm" onClick={() => setRegisterCluster(c)}>지식 등록</Button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {totalPages > 1 && <PaginationNav page={page} totalPages={totalPages} onPageChange={setPage} className="mt-3" />}
          </>
        )}
      </div>

      <ClusterMembersModal
        open={!!selectedCluster} onClose={() => setSelectedCluster(null)}
        cluster={selectedCluster} namespace={namespace}
      />
      <ClusterKnowledgeRegisterModal
        open={!!registerCluster} onClose={() => setRegisterCluster(null)}
        cluster={registerCluster} namespace={namespace}
        onSuccess={() => {
          qc.invalidateQueries({ queryKey: ['voc-clusters', namespace] });
          qc.invalidateQueries({ queryKey: ['knowledge', namespace] });
        }}
      />
    </div>
  );
}
