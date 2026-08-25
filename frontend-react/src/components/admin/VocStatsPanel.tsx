import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { RefreshCw, CheckCircle2, AlertTriangle } from 'lucide-react';
import {
  getVocStats, getVocClusters, getVocClusterMembers,
  type VocCluster, type VocClusterMember,
} from '../../api/emailVoc';
import { createKnowledge } from '../../api/knowledge';
import { getCategories } from '../../api/namespaces';
import { Badge } from '../ui/Badge';
import { Modal } from '../ui/Modal';
import { Button } from '../ui/Button';
import { DonutChart, type DonutSegment } from '../ui/DonutChart';
import { CATEGORY_LABEL, SEVERITY_LABEL, formatRelative } from './VocEmailPanel';

// Teams 카드(teams_notify.py의 _SEVERITY_COLOR)와 동일한 4단계 팔레트 — 어디서
// 봐도 같은 심각도가 같은 색으로 읽히도록 색상 값 자체를 맞춘다.
const SEVERITY_HEX: Record<string, string> = {
  low: '#6B7280', medium: '#0891B2', high: '#D97706', urgent: '#DC2626',
};
const CATEGORY_HEX: Record<string, string> = {
  system_error: '#F43F5E', user_mistake: '#F59E0B', uncertain: '#6366F1', not_it_related: '#64748B',
};

// ── 지식 등록 모달 — 반복 유형에 대한 해결방안이 아직 없을 때 바로 등록 ──────

function ClusterKnowledgeRegisterModal({
  open, onClose, cluster, namespace, onSuccess,
}: {
  open: boolean; onClose: () => void; cluster: VocCluster | null; namespace: string; onSuccess: () => void;
}) {
  const [content, setContent] = useState('');
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
      setContent(`[반복 유형] ${cluster.representative_subject}\n\n해결 방안: `);
      setCategory(sortedCategories[0]?.name ?? '');
      setError(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, cluster, sortedCategories.length]);

  const handleSubmit = async () => {
    if (!cluster || !content.trim() || !category) return;
    setSubmitting(true);
    setError(null);
    try {
      const created = await createKnowledge({
        namespace, container_name: 'VOC 반복 유형', target_tables: [],
        content, category,
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

// ── 클러스터 멤버 모달 ────────────────────────────────────────────────────────

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

  const uncoveredCount = clusters.filter((c) => !c.has_knowledge_coverage).length;

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
            <h3 className="text-sm font-semibold text-slate-300 mb-4">유형 분포</h3>
            <div className="flex items-center gap-5">
              <DonutChart segments={categorySegments} centerTop={`${stats.total}`} centerBottom="전체" />
              <div className="space-y-1.5 flex-1 min-w-0">
                {categorySegments.map((seg) => (
                  <div key={seg.label} className="flex items-center gap-2">
                    <span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: seg.color }} />
                    <span className="text-xs text-slate-400 flex-1 truncate">{seg.label}</span>
                    <span className="text-xs font-semibold text-slate-200">{seg.value}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="bg-slate-800 border border-slate-700 rounded-xl p-5">
            <h3 className="text-sm font-semibold text-slate-300 mb-4">심각도 분포</h3>
            <div className="flex items-center gap-5">
              <DonutChart segments={severitySegments} centerTop={`${stats.total}`} centerBottom="전체" />
              <div className="space-y-1.5 flex-1 min-w-0">
                {severitySegments.map((seg) => (
                  <div key={seg.label} className="flex items-center gap-2">
                    <span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: seg.color }} />
                    <span className="text-xs text-slate-400 flex-1 truncate">{seg.label}</span>
                    <span className="text-xs font-semibold text-slate-200">{seg.value}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      <div>
        <div className="flex items-center gap-2 mb-2">
          <h3 className="text-sm font-semibold text-slate-300">반복 유형</h3>
          {uncoveredCount > 0 && (
            <Badge color="amber">해결방안 미등록 {uncoveredCount}건</Badge>
          )}
        </div>
        {clusters.length === 0 && !clustersLoading ? (
          <div className="text-sm text-slate-500 py-6 text-center border border-dashed border-slate-700 rounded-lg">
            아직 감지된 반복 유형이 없습니다.
          </div>
        ) : (
          <div className="border border-slate-700 rounded-lg overflow-hidden">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-slate-700 bg-slate-800/50">
                  <th className="text-left px-3 py-2 text-slate-400">대표 제목</th>
                  <th className="text-left px-3 py-2 text-slate-400">건수</th>
                  <th className="text-left px-3 py-2 text-slate-400">최근 발생</th>
                  <th className="text-left px-3 py-2 text-slate-400">해결방안</th>
                  <th className="text-left px-3 py-2 text-slate-400"></th>
                </tr>
              </thead>
              <tbody>
                {clusters.map((c) => (
                  <tr key={c.id} className="border-b border-slate-700/50 last:border-0 hover:bg-slate-700/30">
                    <td className="px-3 py-2">
                      <button className="text-slate-200 hover:text-indigo-400 text-left" onClick={() => setSelectedCluster(c)}>
                        {c.representative_subject || '(제목 없음)'}
                      </button>
                    </td>
                    <td className="px-3 py-2 text-slate-300">{c.member_count}건</td>
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
