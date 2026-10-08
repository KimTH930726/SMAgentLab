import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { MessageSquare, CheckCircle, FileText, ChevronDown, ChevronUp, Trash2, AlertTriangle, Flag } from 'lucide-react';
import { useAppStore } from '../../store/useAppStore';
import { useAuthStore } from '../../store/useAuthStore';
import { sortNamespacesByUserPart } from '../../utils/sortNamespaces';
import { getNamespaceStats, deleteQueryLog, getQueryLogs, bulkDeleteQueryLogs, fillKnowledgeGap } from '../../api/stats';
import { createKnowledge } from '../../api/knowledge';
import { getNamespaces, getNamespacesDetail } from '../../api/namespaces';
import { Button } from '../ui/Button';
import { Modal } from '../ui/Modal';
import { Badge } from '../ui/Badge';
import { DonutChart, type DonutSegment } from '../ui/DonutChart';
import type { QueryLog, QueryFilter } from '../../types';
import { showAlert } from '../../store/useDialogStore';

const TERM_PALETTE = ['#6366f1', '#8b5cf6', '#06b6d4', '#f59e0b', '#10b981', '#f43f5e', '#ec4899', '#14b8a6'];

// 원본 용어(테이블명/코드 등 raw 값)는 관리자가 아니면 알아보기 어려우므로,
// 용어집에 등록된 한글 설명(rag_glossary.description)의 첫 문장으로 축약해 보여준다.
function friendlyTermLabel(term: string, description?: string | null): string {
  if (!description) return term;
  const dotIdx = description.indexOf('.');
  const firstSentence = (dotIdx >= 0 ? description.slice(0, dotIdx) : description).trim();
  if (!firstSentence) return term;
  return firstSentence.length > 26 ? `${firstSentence.slice(0, 25)}…` : firstSentence;
}

// ── Knowledge Register Modal (지식 등록 폼 모달) ──────────────────────────────

interface KnowledgeRegisterModalProps {
  open: boolean;
  onClose: () => void;
  log: QueryLog | null;
  namespace: string;
  onSuccess: () => void;
}

function KnowledgeRegisterModal({ open, onClose, log, namespace, onSuccess }: KnowledgeRegisterModalProps) {
  const [content, setContent] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 열 때 빈 칸으로 — 공백 질의의 AI 답변은 "관련 지식을 찾지 못했습니다"뿐이라 채워 두면 지우는 수고만 생겼다(2026-10-06).
  // 업무구분도 묻지 않는다 — 서버가 내용으로 추천, 없으면 "분류 확인 필요"(다른 등록 경로와 같음, 업무구분 없는 파트도 등록 가능)
  useEffect(() => {
    if (open && log) {
      setContent('');
      setError(null);
    }
  }, [open, log]);

  const handleSubmit = async () => {
    if (!log || !content.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      const created = await createKnowledge({
        namespace,
        content,
      });
      await fillKnowledgeGap(log.id, created.id);
      if (created.pending_review) {
        void showAlert('등록하신 지식이 기존 지식과 유사도가 높아 승인 대기 상태로 등록되었습니다.\n관리자 승인 후 검색에 반영됩니다.', { title: '승인 대기로 등록' });
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
    <Modal
      isOpen={open}
      onClose={onClose}
      title="지식 등록"
      maxWidth="max-w-xl"
    >
      <div className="space-y-3">
        {/* 원본 질문 (읽기 전용) */}
        <div>
          <label className="block text-xs font-medium text-slate-400 mb-1">원본 질문</label>
          <div className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-400 leading-relaxed">
            {log?.question}
          </div>
        </div>

        {/* 내용 */}
        <div>
          <label className="block text-xs font-medium text-slate-400 mb-1">
            내용 <span className="text-rose-600 dark:text-rose-400">*</span>
          </label>
          <textarea
            rows={8}
            value={content}
            onChange={(e) => setContent(e.target.value)}
            className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 resize-y min-h-[200px] leading-relaxed"
            placeholder="이 질문에 답이 될 내용을 적어 주세요"
          />
        </div>

        <p className="text-[11px] text-slate-500" title="기존 업무구분 중 내용에 맞는 것을 추천하고, 없으면 '분류 확인 필요'로 등록합니다">
          업무구분은 내용으로 자동 지정됩니다.
        </p>

        {error && <p className="text-xs text-rose-600 dark:text-rose-400">{error}</p>}

        <div className="flex gap-2 justify-end pt-1">
          <Button variant="ghost" size="sm" onClick={onClose}>취소</Button>
          <Button
            variant="primary" size="sm"
            loading={submitting}
            disabled={!content.trim()}
            onClick={handleSubmit}
          >
            지식 등록
          </Button>
        </div>
      </div>
    </Modal>
  );
}

// ── QueryLog Modal ───────────────────────────────────────────────────────────

// 질의 상태는 답변 / 지식 공백 둘뿐(2026-10-02) — 좋아요/싫어요 기반 해결·미해결은 없앴고(신고 없는 답변은 맞은 것으로 봄),
// 틀린 답은 정정 요청(개선 원장)으로 센다. 공백 = 답변에 "관련 지식을 찾지 못했습니다"가 뜬 것. 지식을 등록해 메우면 "메움".
type ModalType = 'total' | QueryFilter;

const isFilled = (log: QueryLog) => log.status === 'no_knowledge' && log.resolved_knowledge_id != null;

function QueryLogModal({
  open, onClose, modalType, namespace, qc, canModify,
}: {
  open: boolean; onClose: () => void; modalType: ModalType | null;
  namespace: string; qc: ReturnType<typeof useQueryClient>; canModify: boolean;
}) {
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [registerLog, setRegisterLog] = useState<QueryLog | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());

  const statusParam: QueryFilter | undefined = modalType === 'total' || modalType === null ? undefined : modalType;
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

  const selectableLogs = logs.filter((l: QueryLog) => !isFilled(l));

  const toggleSelectAll = () => {
    if (selectedIds.size === selectableLogs.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(selectableLogs.map((l: QueryLog) => l.id)));
    }
  };

  const titleMap: Record<ModalType, string> = {
    total: '전체 질의', pending: '답변한 질의', no_knowledge: '지식 공백 질의', filled: '해결된 공백 질의',
  };
  const title = modalType ? `${titleMap[modalType]} (${logs.length}건)` : '';

  return (
    <>
      <Modal isOpen={open} onClose={() => { onClose(); setExpandedId(null); setRegisterLog(null); setSelectedIds(new Set()); }}
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
              {/* Row header */}
              <div className="flex items-start">
                {canModify && !isFilled(log) && (
                  <label className="flex items-center px-3 py-3.5 cursor-pointer" onClick={(e) => e.stopPropagation()}>
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
                    {isFilled(log) ? <FileText className="w-4 h-4 text-indigo-600 dark:text-indigo-400" />
                      : log.status === 'no_knowledge' ? <AlertTriangle className="w-4 h-4 text-orange-600 dark:text-orange-400" />
                      : <CheckCircle className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />}
                  </span>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-slate-200 truncate">{log.question}</p>
                    <div className="flex items-center gap-2 mt-0.5 flex-wrap">
                      {log.mapped_term && <Badge color="indigo">{log.mapped_term}</Badge>}
                      {isFilled(log) && log.resolved_at ? (
                        <span className="text-xs text-slate-500">해결 {new Date(log.resolved_at).toLocaleString('ko-KR')}</span>
                      ) : (
                        <span className="text-xs text-slate-500">{new Date(log.created_at).toLocaleString('ko-KR')}</span>
                      )}
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
                      isFilled(log) ? 'text-indigo-600 dark:text-indigo-400'
                      : log.status === 'no_knowledge' ? 'text-orange-600 dark:text-orange-400'
                      : 'text-emerald-600 dark:text-emerald-400'
                    }>
                      {isFilled(log) ? '공백 해결' : log.status === 'no_knowledge' ? '지식 공백' : '답변'}
                    </span></span>
                    {log.mapped_term && <span>용어: <span className="text-indigo-600 dark:text-indigo-400">{log.mapped_term}</span></span>}
                    <span>질문 {new Date(log.created_at).toLocaleString('ko-KR')}</span>
                    {isFilled(log) && log.resolved_at && (
                      <span>해결 {new Date(log.resolved_at).toLocaleString('ko-KR')}</span>
                    )}
                  </div>

                  {/* AI 답변 또는 등록된 지식 미리보기 */}
                  {log.answer && (
                    <div>
                      <p className="text-xs text-slate-500 mb-1">
                        {log.knowledge_active ? '등록한 지식' : isFilled(log) ? 'AI 답변 (등록한 지식은 검토 대기)' : 'AI 답변'}
                      </p>
                      <div className="bg-slate-900/80 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300 leading-relaxed whitespace-pre-wrap max-h-40 overflow-y-auto">
                        {log.answer}
                      </div>
                    </div>
                  )}
                  {!log.answer && !isFilled(log) && (
                    <p className="text-xs text-slate-500 italic">답변 없음 (마이그레이션 이전 데이터)</p>
                  )}

                  {/* 조치 — 공백은 지식 등록으로 메우고, 답변·공백 모두 기록 삭제 가능(메운 공백은 실적이라 그대로) */}
                  {!isFilled(log) && (
                    <div className="space-y-2 pt-1">
                      {actionError && <p className="text-xs text-rose-600 dark:text-rose-400">{actionError}</p>}
                      {canModify ? (
                        <div className="flex gap-2">
                          {log.status === 'no_knowledge' && (
                            <Button variant="primary" size="sm" onClick={() => setRegisterLog(log)}>
                              <FileText className="w-3.5 h-3.5" />지식 등록
                            </Button>
                          )}
                          <Button variant="danger" size="sm"
                            loading={deleteMutation.isPending && deleteMutation.variables === log.id}
                            onClick={() => deleteMutation.mutate(log.id)}>
                            삭제
                          </Button>
                        </div>
                      ) : (
                        <p className="text-xs text-slate-500">이 파트에 대한 수정 권한이 없습니다.</p>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      </Modal>

      {/* 지식 등록 모달 */}
      <KnowledgeRegisterModal
        open={registerLog !== null}
        onClose={() => setRegisterLog(null)}
        log={registerLog}
        namespace={namespace}
        onSuccess={() => {
          invalidateAll();
          qc.invalidateQueries({ queryKey: ['knowledge', namespace] });
          setExpandedId(null);
        }}
      />
    </>
  );
}

// ── StatsPanel ───────────────────────────────────────────────────────────────

export function StatsPanel({ onOpenCorrections }: { onOpenCorrections?: () => void } = {}) {
  const { namespace: storeNamespace, setNamespace } = useAppStore();
  const user = useAuthStore((s) => s.user);
  const qc = useQueryClient();
  const [selectedNs, setSelectedNs] = useState(storeNamespace || '');
  const [modalType, setModalType] = useState<ModalType | null>(null);

  const { data: namespaces = [] } = useQuery({ queryKey: ['namespaces'], queryFn: getNamespaces, staleTime: 30_000 });
  const { data: nsDetails = [] } = useQuery({ queryKey: ['namespaces-detail'], queryFn: getNamespacesDetail, staleTime: 30_000 });
  const sortedNamespaces = sortNamespacesByUserPart(namespaces, user?.part, nsDetails);

  // 삭제 등으로 selectedNs가 유효하지 않으면 리셋
  useEffect(() => {
    if (selectedNs && namespaces.length > 0 && !namespaces.includes(selectedNs)) {
      setSelectedNs('');
    }
  }, [namespaces, selectedNs]);
  const nsOwnerPart = nsDetails.find((n) => n.name === selectedNs)?.owner_part;
  // owner_part 없으면 공통(모두 가능), 있으면 같은 파트 or admin
  const canModifyNs = user?.role === 'admin' || !nsOwnerPart || nsOwnerPart === user?.part;
  const { data: stats, isLoading, error, refetch } = useQuery({
    queryKey: ['stats-ns', selectedNs],
    queryFn: () => getNamespaceStats(selectedNs),
    enabled: !!selectedNs,
    staleTime: 10_000,
    refetchOnMount: 'always',
  });

  // 답변률 = 답변한 질의 비율(신고 없는 답변은 맞은 것으로 봄). 공백은 메웠어도 그때는 답 못 한 질의라 분모에만.
  const answerRate = stats && stats.total_queries > 0
    ? Math.round((stats.answered / stats.total_queries) * 100) : 0;

  const answerSegments: DonutSegment[] = [
    { value: stats?.answered ?? 0, color: '#10b981', label: '답변', tooltip: '근거를 찾아 답변함 — 틀렸으면 사용자가 "답변 틀림"으로 신고(정정 요청)' },
    { value: stats?.no_knowledge ?? 0, color: '#f97316', label: '지식 공백', tooltip: '"관련 지식을 찾지 못했습니다"가 뜬 질의 — 지식 등록 필요' },
    { value: stats?.filled ?? 0, color: '#6366f1', label: '공백 해결', tooltip: '지식 공백이었다가 지식을 등록해 해결한 질의' },
  ];

  const topTerms = (stats?.term_distribution ?? []).slice(0, 8);
  const termSegments: DonutSegment[] = topTerms.map((t, i) => ({
    value: t.total, color: TERM_PALETTE[i % TERM_PALETTE.length], label: friendlyTermLabel(t.term, t.description),
    tooltip: t.term === '기타'
      ? '용어집에 매칭되지 않은 질의'
      : `${t.description || t.term} — 원본 용어: "${t.term}" (지식 공백 ${t.no_knowledge})`,
  }));

  const kpiCards = [
    {
      label: '전체 질의', value: stats?.total_queries ?? 0, type: 'total' as ModalType,
      icon: <MessageSquare className="w-5 h-5 text-indigo-600 dark:text-indigo-400" />, bg: 'bg-indigo-100 dark:bg-indigo-900/40',
      highlight: false, tip: '이 파트에 들어온 모든 질의',
    },
    {
      label: '답변', value: stats?.answered ?? 0, type: 'pending' as ModalType,
      icon: <CheckCircle className="w-5 h-5 text-emerald-600 dark:text-emerald-400" />, bg: 'bg-emerald-100 dark:bg-emerald-900/40',
      highlight: false, tip: '근거를 찾아 답변한 질의 — 정정 신고가 없으면 맞은 것으로 봅니다',
    },
    {
      // 질의 목록이 아니라 개선 원장 건수 — 누르면 이 파트의 지식 베이스 › 정정 검토로 이동
      label: '정정 요청', value: stats?.corrections_open ?? 0, type: null, go: onOpenCorrections,
      icon: <Flag className="w-5 h-5 text-rose-600 dark:text-rose-400" />, bg: 'bg-rose-100 dark:bg-rose-900/40',
      highlight: false, tip: '"답변 틀림" 신고 중 아직 처리 안 된 건 — 눌러서 정정 검토로 이동',
    },
    {
      label: '지식 공백', value: stats?.no_knowledge ?? 0, type: 'no_knowledge' as ModalType,
      icon: <AlertTriangle className="w-5 h-5 text-orange-600 dark:text-orange-400" />, bg: 'bg-orange-100 dark:bg-orange-900/40',
      highlight: (stats?.no_knowledge ?? 0) > 0, tip: '"관련 지식을 찾지 못했습니다"가 뜬 질의 — 눌러서 지식 등록',
    },
    {
      label: '공백 해결', value: stats?.filled ?? 0, type: 'filled' as ModalType,
      icon: <FileText className="w-5 h-5 text-indigo-600 dark:text-indigo-400" />, bg: 'bg-indigo-100 dark:bg-indigo-900/40',
      highlight: false, tip: '지식 공백이었다가 지식을 등록해 해결한 질의',
    },
  ];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-200">통계 대시보드</h2>
        <button onClick={() => refetch()} className="text-xs text-indigo-600 hover:text-indigo-500 dark:text-indigo-400 dark:hover:text-indigo-300">새로고침</button>
      </div>

      <div>
        <label className="block text-xs font-medium text-slate-400 mb-1.5">파트</label>
        <select value={selectedNs} onChange={(e) => setSelectedNs(e.target.value)}
          className="w-64 bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500">
          <option value="">선택...</option>
          {sortedNamespaces.map((ns) => <option key={ns} value={ns}>{ns}</option>)}
        </select>
      </div>

      {isLoading && <div className="text-center py-10 text-slate-500 animate-pulse">로딩 중...</div>}
      {error && <div className="text-center py-10 text-rose-600 dark:text-rose-400">오류가 발생했습니다.</div>}

      {stats && (
        <>
          {/* KPI cards — clickable */}
          <div className="grid grid-cols-5 gap-3">
            {kpiCards.map(({ label, value, type, icon, bg, highlight, tip, go }) => (
              <button
                key={label}
                title={tip}
                onClick={() => {
                  if (type) setModalType(type);
                  else if (go) { setNamespace(selectedNs); go(); }
                }}
                className={`bg-slate-800 border rounded-xl p-4 flex items-center gap-3 transition-colors text-left ${type || go ? 'hover:bg-slate-700/60 cursor-pointer' : 'cursor-default'} ${
                  highlight
                    ? 'border-orange-400 ring-1 ring-orange-300 hover:border-orange-500 dark:border-orange-500/60 dark:ring-orange-500/30 dark:hover:border-orange-400/80'
                    : 'border-slate-700 hover:border-indigo-500/50'
                }`}
              >
                <div className={`w-10 h-10 ${bg} rounded-xl flex items-center justify-center flex-shrink-0`}>{icon}</div>
                <div>
                  <p className="text-xs text-slate-500">{label}</p>
                  <p className={`text-2xl font-bold ${highlight ? 'text-orange-600 dark:text-orange-400' : 'text-slate-100'}`}>{value}</p>
                  {highlight && <p className="text-[10px] text-orange-600 dark:text-orange-500 mt-0.5">등록 필요</p>}
                </div>
              </button>
            ))}
          </div>

          {/* Donut charts */}
          <div className="grid grid-cols-2 gap-4">
            <div className="bg-slate-800 border border-slate-700 rounded-xl p-5">
              <h3 className="text-sm font-semibold text-slate-300 mb-4">
                답변 현황
                {(stats.system_errors ?? 0) > 0 && (
                  <span className="ml-2 text-[11px] font-normal text-slate-500"
                    title="LLM 서버 연결 실패로 답하지 못한 질의 — 장애라 답변·공백 통계(전체 포함)에서 뺐습니다">
                    · LLM 연결 실패 {stats.system_errors}건(통계 제외)
                  </span>
                )}
              </h3>
              <div className="flex items-center gap-5">
                <DonutChart segments={answerSegments} centerTop={`${answerRate}%`} centerBottom="답변률" />
                <div className="space-y-2.5 flex-1">
                  {answerSegments.map((seg) => (
                    <div key={seg.label} className="flex items-center gap-2" title={seg.tooltip}>
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
                    {termSegments.map((seg, i) => (
                      <div key={i} className="flex items-center gap-2" title={seg.tooltip}>
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
        <div className="text-center py-10 text-slate-500">파트를 선택하세요.</div>
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
