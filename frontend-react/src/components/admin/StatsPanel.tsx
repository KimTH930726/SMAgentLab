import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { MessageSquare, CheckCircle, XCircle, Clock, FileText, ChevronDown, ChevronUp, Trash2 } from 'lucide-react';
import { useAppStore } from '../../store/useAppStore';
import { useAuthStore } from '../../store/useAuthStore';
import { getNamespaceStats, deleteQueryLog, resolveQueryLog, getQueryLogs, bulkDeleteQueryLogs, markQueryLogResolved } from '../../api/stats';
import { createKnowledge } from '../../api/knowledge';
import { getNamespaces, getNamespacesDetail } from '../../api/namespaces';
import { Button } from '../ui/Button';
import { Modal } from '../ui/Modal';
import { Badge } from '../ui/Badge';
import type { QueryLog, QueryStatus } from '../../types';

// ── SVG Donut Chart ──────────────────────────────────────────────────────────

interface DonutSegment { value: number; color: string; label: string; tooltip?: string; }

function DonutChart({ segments, size = 140, strokeWidth = 22, centerTop, centerBottom }: {
  segments: DonutSegment[]; size?: number; strokeWidth?: number;
  centerTop?: string; centerBottom?: string;
}) {
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
  const r = (size - strokeWidth) / 2;
  const cx = size / 2, cy = size / 2;
  const circumference = 2 * Math.PI * r;
  const total = segments.reduce((s, seg) => s + seg.value, 0);
  let cumAngle = -90;

  return (
    <div className="relative">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        {total === 0 ? (
          <circle cx={cx} cy={cy} r={r} fill="none" style={{ stroke: 'rgb(var(--slate-700))' }} strokeWidth={strokeWidth} />
        ) : (
          segments.map((seg, i) => {
            if (seg.value === 0) return null;
            const fraction = seg.value / total;
            const dash = Math.max(fraction * circumference - 2, 0);
            const startAngle = cumAngle;
            cumAngle += fraction * 360;
            const isHover = hoverIdx === i;
            return (
              <circle key={i} cx={cx} cy={cy} r={r} fill="none" stroke={seg.color}
                strokeWidth={isHover ? strokeWidth + 6 : strokeWidth} strokeLinecap="butt"
                strokeDasharray={`${dash} ${circumference - dash}`}
                transform={`rotate(${startAngle}, ${cx}, ${cy})`}
                className="transition-all duration-150 cursor-pointer"
                style={{ opacity: hoverIdx !== null && !isHover ? 0.4 : 1 }}
                onMouseEnter={() => setHoverIdx(i)}
                onMouseLeave={() => setHoverIdx(null)} />
            );
          })
        )}
        {centerTop && (
          <text x={cx} y={cy - 7} textAnchor="middle" dominantBaseline="middle"
            style={{ fill: 'rgb(var(--slate-200))' }} fontSize="22" fontWeight="700" fontFamily="system-ui">{centerTop}</text>
        )}
        {centerBottom && (
          <text x={cx} y={cy + 13} textAnchor="middle" dominantBaseline="middle"
            style={{ fill: 'rgb(var(--slate-400))' }} fontSize="11" fontFamily="system-ui">{centerBottom}</text>
        )}
      </svg>
      {hoverIdx !== null && segments[hoverIdx] && (
        <div className="absolute -top-10 left-1/2 -translate-x-1/2 bg-slate-900 border border-slate-600 rounded-lg px-3 py-1.5 shadow-lg pointer-events-none z-10 whitespace-nowrap">
          <p className="text-xs font-semibold text-slate-200">
            {segments[hoverIdx].label}: {segments[hoverIdx].value}건
            <span className="text-slate-400 ml-1">({Math.round((segments[hoverIdx].value / total) * 100)}%)</span>
          </p>
          {segments[hoverIdx].tooltip && (
            <p className="text-[10px] text-slate-400 mt-0.5">{segments[hoverIdx].tooltip}</p>
          )}
        </div>
      )}
    </div>
  );
}

const TERM_PALETTE = ['#6366f1', '#8b5cf6', '#06b6d4', '#f59e0b', '#10b981', '#f43f5e', '#ec4899', '#14b8a6'];

// ── QueryLog Modal ───────────────────────────────────────────────────────────

type ModalType = 'total' | 'resolved' | 'pending' | 'unresolved';

interface RegisterForm {
  logId: number | null;
  containerName: string;
  targetTables: string;
  content: string;
  queryTemplate: string;
}
const emptyRegister: RegisterForm = { logId: null, containerName: '', targetTables: '', content: '', queryTemplate: '' };

function QueryLogModal({
  open, onClose, modalType, namespace, qc, canModify,
}: {
  open: boolean; onClose: () => void; modalType: ModalType | null;
  namespace: string; qc: ReturnType<typeof useQueryClient>; canModify: boolean;
}) {
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [registerForm, setRegisterForm] = useState<RegisterForm>(emptyRegister);
  const [registering, setRegistering] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());

  const statusParam: QueryStatus | undefined = modalType === 'total' ? undefined : modalType as QueryStatus;
  const { data: logs = [], isLoading } = useQuery({
    queryKey: ['query-logs', namespace, modalType],
    queryFn: () => getQueryLogs(namespace, statusParam),
    enabled: open && !!namespace && !!modalType,
    staleTime: 10_000,
  });

  const invalidateAll = () => {
    qc.invalidateQueries({ queryKey: ['query-logs', namespace] });
    qc.invalidateQueries({ queryKey: ['stats-ns', namespace] });
  };

  const [actionError, setActionError] = useState<string | null>(null);

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteQueryLog(id),
    onSuccess: () => { invalidateAll(); setExpandedId(null); setActionError(null); },
    onError: (err: Error) => { setActionError(err.message); },
  });

  const resolveMutation = useMutation({
    mutationFn: (id: number) => resolveQueryLog(id),
    onSuccess: () => {
      invalidateAll();
      qc.invalidateQueries({ queryKey: ['knowledge', namespace] });
      setExpandedId(null);
      setActionError(null);
    },
    onError: (err: Error) => { setActionError(err.message); },
  });

  const bulkDeleteMutation = useMutation({
    mutationFn: (ids: number[]) => bulkDeleteQueryLogs(ids),
    onSuccess: () => {
      invalidateAll();
      setSelectedIds(new Set());
      setExpandedId(null);
      setActionError(null);
    },
    onError: (err: Error) => { setActionError(err.message); },
  });

  const toggleSelect = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const selectableLogs = logs.filter((l: QueryLog) => l.status !== 'resolved');

  const toggleSelectAll = () => {
    if (selectedIds.size === selectableLogs.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(selectableLogs.map((l: QueryLog) => l.id)));
    }
  };

  const handleRegister = async () => {
    if (!registerForm.logId) return;
    setRegistering(true);
    try {
      await createKnowledge({
        namespace,
        container_name: registerForm.containerName || '미분류',
        target_tables: registerForm.targetTables.split(',').map((t) => t.trim()).filter(Boolean),
        content: registerForm.content,
        query_template: registerForm.queryTemplate || null,
      });
      await markQueryLogResolved(registerForm.logId);
      invalidateAll();
      qc.invalidateQueries({ queryKey: ['knowledge', namespace] });
      qc.invalidateQueries({ queryKey: ['fewshots', namespace] });
      setRegisterForm(emptyRegister);
      setExpandedId(null);
    } catch (err) {
      console.error(err);
    } finally {
      setRegistering(false);
    }
  };

  const titleMap: Record<ModalType, string> = {
    total: '전체 질의', resolved: '해결된 질의', pending: '대기 중 질의', unresolved: '미해결 질의',
  };
  const title = modalType ? `${titleMap[modalType]} (${logs.length}건)` : '';

  return (
    <Modal isOpen={open} onClose={() => { onClose(); setExpandedId(null); setRegisterForm(emptyRegister); setSelectedIds(new Set()); }}
      title={title} maxWidth="max-w-2xl">
      {isLoading && <div className="text-center py-10 text-slate-500 animate-pulse">로딩 중...</div>}
      {!isLoading && logs.length === 0 && (
        <div className="text-center py-10 text-slate-500">질의 내역이 없습니다.</div>
      )}
      {/* Bulk action bar */}
      {canModify && selectableLogs.length > 0 && (
        <div className="flex items-center gap-3 mb-2">
          <label className="flex items-center gap-2 cursor-pointer text-xs text-slate-400 hover:text-slate-300">
            <input
              type="checkbox"
              checked={selectableLogs.length > 0 && selectedIds.size === selectableLogs.length}
              onChange={toggleSelectAll}
              className="w-3.5 h-3.5 rounded border-slate-600 bg-slate-800 text-indigo-500 focus:ring-indigo-500 focus:ring-offset-0 cursor-pointer"
            />
            전체 선택
          </label>
          {selectedIds.size > 0 && (
            <Button variant="danger" size="sm"
              loading={bulkDeleteMutation.isPending}
              onClick={() => bulkDeleteMutation.mutate([...selectedIds])}>
              <Trash2 className="w-3.5 h-3.5" />
              선택 삭제 ({selectedIds.size}건)
            </Button>
          )}
        </div>
      )}
      <div className="max-h-[60vh] overflow-y-auto space-y-2 pr-1">
        {logs.map((log: QueryLog) => (
          <div key={log.id} className="bg-slate-900/60 border border-slate-700 rounded-xl overflow-hidden">
            {/* Row header — clickable */}
            <div className="flex items-start">
              {canModify && log.status !== 'resolved' && (
                <label
                  className="flex items-center px-3 py-3.5 cursor-pointer"
                  onClick={(e) => e.stopPropagation()}
                >
                  <input
                    type="checkbox"
                    checked={selectedIds.has(log.id)}
                    onChange={() => toggleSelect(log.id)}
                    className="w-3.5 h-3.5 rounded border-slate-600 bg-slate-800 text-indigo-500 focus:ring-indigo-500 focus:ring-offset-0 cursor-pointer"
                  />
                </label>
              )}
            <button
              onClick={() => setExpandedId(expandedId === log.id ? null : log.id)}
              className="flex-1 text-left px-2 py-3 flex items-start gap-3 hover:bg-slate-700/40 transition-colors"
            >
              <span className="flex-shrink-0 mt-0.5">
                {log.status === 'resolved' && <CheckCircle className="w-4 h-4 text-emerald-400" />}
                {log.status === 'pending' && <Clock className="w-4 h-4 text-amber-400" />}
                {log.status === 'unresolved' && <XCircle className="w-4 h-4 text-rose-400" />}
              </span>
              <div className="flex-1 min-w-0">
                <p className="text-sm text-slate-200 truncate">{log.question}</p>
                <div className="flex items-center gap-2 mt-0.5 flex-wrap">
                  {log.mapped_term && <Badge color="indigo">{log.mapped_term}</Badge>}
                  <span className="text-xs text-slate-500">
                    {new Date(log.created_at).toLocaleString('ko-KR')}
                  </span>
                </div>
              </div>
              <span className="flex-shrink-0 text-slate-500 mt-0.5">
                {expandedId === log.id ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
              </span>
            </button>
            </div>

            {/* Expanded detail */}
            {expandedId === log.id && (
              <div className="border-t border-slate-700 px-4 py-4 bg-slate-800/50 space-y-3">
                <div>
                  <p className="text-xs text-slate-500 mb-1">질문 전체</p>
                  <p className="text-sm text-slate-200 leading-relaxed whitespace-pre-wrap">{log.question}</p>
                </div>
                <div className="flex items-center gap-4 text-xs text-slate-500">
                  <span>상태: <span className={
                    log.status === 'resolved' ? 'text-emerald-400'
                    : log.status === 'pending' ? 'text-amber-400'
                    : 'text-rose-400'
                  }>
                    {log.status === 'resolved' ? '해결됨' : log.status === 'pending' ? '대기 중' : '미해결'}
                  </span></span>
                  {log.mapped_term && <span>용어: <span className="text-indigo-400">{log.mapped_term}</span></span>}
                  <span>{new Date(log.created_at).toLocaleString('ko-KR')}</span>
                </div>

                {/* Resolved: 답변만 표시 */}
                {log.status === 'resolved' && log.answer && (
                  <div>
                    <p className="text-xs text-slate-500 mb-1">AI 답변</p>
                    <div className="bg-slate-900/80 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300 leading-relaxed whitespace-pre-wrap max-h-40 overflow-y-auto">
                      {log.answer}
                    </div>
                  </div>
                )}

                {/* Actions for pending/unresolved */}
                {log.status !== 'resolved' && (
                  canModify && registerForm.logId === log.id ? (
                    <div className="space-y-3 pt-1">
                      <p className="text-xs font-medium text-slate-400">지식으로 등록</p>
                      {/* 질문 (읽기 전용) */}
                      <div>
                        <label className="block text-xs text-slate-500 mb-1">질문</label>
                        <input type="text" value={log.question} disabled
                          className="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-400 cursor-not-allowed" />
                      </div>
                      {[
                        { label: '컨테이너명', key: 'containerName', placeholder: '예: 청구서 조회' },
                        { label: '대상 테이블 (쉼표 구분)', key: 'targetTables', placeholder: 'table_a, table_b' },
                      ].map(({ label, key, placeholder }) => (
                        <div key={key}>
                          <label className="block text-xs text-slate-500 mb-1">{label}</label>
                          <input
                            type="text"
                            value={registerForm[key as keyof RegisterForm] as string}
                            onChange={(e) => setRegisterForm((f) => ({ ...f, [key]: e.target.value }))}
                            placeholder={placeholder}
                            className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
                          />
                        </div>
                      ))}
                      <div>
                        <label className="block text-xs text-slate-500 mb-1">내용 <span className="text-rose-400">*</span></label>
                        <textarea rows={4} value={registerForm.content}
                          onChange={(e) => setRegisterForm((f) => ({ ...f, content: e.target.value }))}
                          placeholder="지식 베이스에 등록할 가이드 내용을 작성하세요"
                          className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500 resize-none" />
                      </div>
                      <div>
                        <label className="block text-xs text-slate-500 mb-1">쿼리 템플릿 (선택)</label>
                        <textarea rows={2} value={registerForm.queryTemplate}
                          onChange={(e) => setRegisterForm((f) => ({ ...f, queryTemplate: e.target.value }))}
                          placeholder="SELECT ..."
                          className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm font-mono text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500 resize-none" />
                      </div>
                      <div className="flex gap-2 justify-end">
                        <Button variant="ghost" size="sm" onClick={() => setRegisterForm(emptyRegister)}>취소</Button>
                        <Button variant="primary" size="sm" loading={registering}
                          disabled={!registerForm.content.trim()} onClick={handleRegister}>
                          지식 등록 + 해결 처리
                        </Button>
                      </div>
                    </div>
                  ) : (
                    <div className="space-y-3 pt-1">
                      {/* 답변 미리보기 */}
                      {log.answer ? (
                        <div>
                          <p className="text-xs text-slate-500 mb-1">
                            {log.status === 'pending' ? 'AI 답변 (검토 대상)' : 'AI 답변'}
                          </p>
                          <div className="bg-slate-900/80 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300 leading-relaxed whitespace-pre-wrap max-h-40 overflow-y-auto">
                            {log.answer}
                          </div>
                        </div>
                      ) : (
                        <p className="text-xs text-slate-500 italic">답변 없음 (마이그레이션 이전 데이터)</p>
                      )}
                      {actionError && (
                        <p className="text-xs text-rose-400">{actionError}</p>
                      )}
                      {canModify ? (
                        <div className="flex gap-2">
                          {log.status === 'pending' && (
                            <Button variant="primary" size="sm"
                              loading={resolveMutation.isPending && resolveMutation.variables === log.id}
                              disabled={!log.answer}
                              onClick={() => { setActionError(null); resolveMutation.mutate(log.id); }}>
                              <CheckCircle className="w-3.5 h-3.5" />승인 (지식 등록 + 해결)
                            </Button>
                          )}
                          <Button variant={log.status === 'pending' ? 'ghost' : 'primary'} size="sm"
                            onClick={() => setRegisterForm({
                              ...emptyRegister,
                              logId: log.id,
                              content: log.answer ?? '',
                            })}>
                            <FileText className="w-3.5 h-3.5" />
                            {log.status === 'pending' ? '수정 후 등록' : '지식 등록'}
                          </Button>
                          <Button variant="danger" size="sm"
                            loading={deleteMutation.isPending && deleteMutation.variables === log.id}
                            onClick={() => deleteMutation.mutate(log.id)}>
                            삭제
                          </Button>
                        </div>
                      ) : (
                        <p className="text-xs text-slate-500">이 네임스페이스에 대한 수정 권한이 없습니다.</p>
                      )}
                    </div>
                  )
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </Modal>
  );
}

// ── StatsPanel ───────────────────────────────────────────────────────────────

export function StatsPanel() {
  const { namespace: storeNamespace } = useAppStore();
  const user = useAuthStore((s) => s.user);
  const qc = useQueryClient();
  const [selectedNs, setSelectedNs] = useState(storeNamespace || '');
  const [modalType, setModalType] = useState<ModalType | null>(null);

  const { data: namespaces = [] } = useQuery({ queryKey: ['namespaces'], queryFn: getNamespaces, staleTime: 30_000 });
  const { data: nsDetails = [] } = useQuery({ queryKey: ['namespaces-detail'], queryFn: getNamespacesDetail, staleTime: 30_000 });
  const nsOwnerPart = nsDetails.find((n) => n.name === selectedNs)?.owner_part;
  // owner_part 없으면 admin만, 있으면 같은 파트 or admin
  const canModifyNs = user?.role === 'admin' || (!!nsOwnerPart && nsOwnerPart === user?.part);
  const { data: stats, isLoading, error, refetch } = useQuery({
    queryKey: ['stats-ns', selectedNs],
    queryFn: () => getNamespaceStats(selectedNs),
    enabled: !!selectedNs,
    staleTime: 10_000,
    refetchOnMount: 'always',
  });

  const resolveRate = stats && stats.total_queries > 0
    ? Math.round((stats.resolved / stats.total_queries) * 100) : 0;

  const resolveSegments: DonutSegment[] = [
    { value: stats?.resolved ?? 0, color: '#10b981', label: '해결됨', tooltip: '피드백 긍정 또는 관리자 승인' },
    { value: stats?.pending ?? 0, color: '#f59e0b', label: '대기 중', tooltip: '검색 결과 있음, 피드백 대기' },
    { value: stats?.unresolved ?? 0, color: '#f43f5e', label: '미해결', tooltip: '검색 결과 없음 또는 부정 피드백' },
  ];

  const topTerms = (stats?.term_distribution ?? []).slice(0, 8);
  const termSegments: DonutSegment[] = topTerms.map((t, i) => ({
    value: t.total, color: TERM_PALETTE[i % TERM_PALETTE.length], label: t.term,
    tooltip: t.term === '기타'
      ? '용어집에 매칭되지 않은 질의'
      : `용어집 매핑: "${t.term}" (대기 ${t.pending}, 미해결 ${t.unresolved})`,
  }));

  const kpiCards = [
    {
      label: '전체 질의', value: stats?.total_queries ?? 0, type: 'total' as ModalType,
      icon: <MessageSquare className="w-5 h-5 text-indigo-400" />, bg: 'bg-indigo-900/40',
    },
    {
      label: '해결됨', value: stats?.resolved ?? 0, type: 'resolved' as ModalType,
      icon: <CheckCircle className="w-5 h-5 text-emerald-400" />, bg: 'bg-emerald-900/40',
    },
    {
      label: '대기 중', value: stats?.pending ?? 0, type: 'pending' as ModalType,
      icon: <Clock className="w-5 h-5 text-amber-400" />, bg: 'bg-amber-900/40',
    },
    {
      label: '미해결', value: stats?.unresolved ?? 0, type: 'unresolved' as ModalType,
      icon: <XCircle className="w-5 h-5 text-rose-400" />, bg: 'bg-rose-900/40',
    },
  ];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-200">통계 대시보드</h2>
        <button onClick={() => refetch()} className="text-xs text-indigo-400 hover:text-indigo-300">새로고침</button>
      </div>

      <div>
        <label className="block text-xs font-medium text-slate-400 mb-1.5">네임스페이스</label>
        <select value={selectedNs} onChange={(e) => setSelectedNs(e.target.value)}
          className="w-64 bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500">
          <option value="">선택...</option>
          {namespaces.map((ns) => <option key={ns} value={ns}>{ns}</option>)}
        </select>
      </div>

      {isLoading && <div className="text-center py-10 text-slate-500 animate-pulse">로딩 중...</div>}
      {error && <div className="text-center py-10 text-rose-400">오류가 발생했습니다.</div>}

      {stats && (
        <>
          {/* KPI cards — clickable */}
          <div className="grid grid-cols-4 gap-3">
            {kpiCards.map(({ label, value, type, icon, bg }) => (
              <button
                key={label}
                onClick={() => setModalType(type)}
                className="bg-slate-800 border border-slate-700 rounded-xl p-4 flex items-center gap-3 hover:border-indigo-500/50 hover:bg-slate-700/60 transition-colors cursor-pointer text-left"
              >
                <div className={`w-10 h-10 ${bg} rounded-xl flex items-center justify-center flex-shrink-0`}>{icon}</div>
                <div>
                  <p className="text-xs text-slate-500">{label}</p>
                  <p className="text-2xl font-bold text-slate-100">{value}</p>
                </div>
              </button>
            ))}
          </div>

          {/* Donut charts */}
          <div className="grid grid-cols-2 gap-4">
            <div className="bg-slate-800 border border-slate-700 rounded-xl p-5">
              <h3 className="text-sm font-semibold text-slate-300 mb-4">해결률</h3>
              <div className="flex items-center gap-5">
                <DonutChart segments={resolveSegments} centerTop={`${resolveRate}%`} centerBottom="해결률" />
                <div className="space-y-2.5 flex-1">
                  {resolveSegments.map((seg) => (
                    <div key={seg.label} className="flex items-center gap-2">
                      <span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: seg.color }} />
                      <span className="text-xs text-slate-400 flex-1">{seg.label}</span>
                      <span className="text-xs font-semibold text-slate-200">{seg.value}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            <div className="bg-slate-800 border border-slate-700 rounded-xl p-5">
              <h3 className="text-sm font-semibold text-slate-300 mb-4">업무 유형별 분포</h3>
              {termSegments.length === 0 ? (
                <div className="flex items-center justify-center py-8">
                  <DonutChart segments={[]} centerTop="0" centerBottom="유형" />
                </div>
              ) : (
                <div className="flex items-center gap-5">
                  <DonutChart segments={termSegments} centerTop={`${topTerms.length}`} centerBottom="유형" />
                  <div className="space-y-1.5 flex-1 min-w-0">
                    {termSegments.map((seg) => (
                      <div key={seg.label} className="flex items-center gap-2">
                        <span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: seg.color }} />
                        <span className="text-xs text-slate-400 flex-1 truncate">{seg.label}</span>
                        <span className="text-xs font-semibold text-slate-200">{seg.value}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        </>
      )}

      {!selectedNs && !isLoading && (
        <div className="text-center py-10 text-slate-500">네임스페이스를 선택하세요.</div>
      )}

      {/* Query log modal */}
      <QueryLogModal
        open={modalType !== null}
        onClose={() => setModalType(null)}
        modalType={modalType}
        namespace={selectedNs}
        qc={qc}
        canModify={canModifyNs}
      />
    </div>
  );
}
