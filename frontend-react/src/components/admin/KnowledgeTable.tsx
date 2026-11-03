import { useState, useRef, useEffect, useMemo } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { adminPath } from '../../pages/adminMenu';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Trash2, X, FileText, Upload, List, PenLine, CheckCircle, Clock, AlertCircle, Globe, ChevronDown, ChevronUp, Search } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { safeRehypePlugins } from '../../utils/markdown';
import {
  getKnowledge,
  createKnowledge,
  updateKnowledge,
  deleteKnowledge,
  bulkDeleteKnowledge,
  bulkUpdateKnowledge,
  vectorSearchKnowledge,
  bulkCreateKnowledge,
  checkBulkReplace,
  previewTextSplit,
  previewFileUpload,
  previewUrl,
  previewConfluenceTree,
  previewConfluenceBulk,
  getIngestionJobs,
  getIngestionJobStatus,
  cancelIngestionJob,
  getDuplicateMatches,
  resolveDuplicate,
  type IngestionJob,
  type IngestionJobStatus,
  type ConfluenceTreeResponse,
} from '../../api/knowledge';
import { updateConfluencePAT, deleteConfluencePAT } from '../../api/auth';
import { useNamespaceAccess } from '../../utils/useNamespaceAccess';
import { useAuthStore } from '../../store/useAuthStore';
import { TabGuide } from './TabGuide';
import { Button } from '../ui/Button';
import { Modal } from '../ui/Modal';
import { Badge } from '../ui/Badge';
import { PaginationInfo, PaginationNav, useClientPaging } from '../ui/Pagination';
import { diffLines, sameText, splitRows, wordDiff, type SplitRow } from '../../utils/textDiff';
import type { KnowledgeItem, DuplicateMatch, ChosenLocation } from '../../types';
import { LocationPicker, usePrefetchLocate } from './LocationPicker';
import { showAlert, showConfirm } from '../../store/useDialogStore';
import { KnowledgeStructurePanel } from './KnowledgeStructurePanel';
import { EvidenceButton } from './Evidence';
// 컨플 공간·하위 전체 넣기 — 시작은 등록 창구(하나, 10/29), 분류 검토는 [적재 검토] 탭(검토는 등록이 아니라서, 10/29 사용자)
import { SpaceImportStart } from './TeamImportPanel';

// ── 공통 타입 ─────────────────────────────────────────────────────────────────

// 실제 마크다운 문법(제목/목록/굵게/코드블록/표)이 있는지 대략 판별 — 없으면 원본
// 그대로(공백/정렬 보존) 보여준다. 파이프로 열을 구분한 원본 데이터(엑셀 추출 등)를
// 마크다운으로 잘못 파싱하면 정렬이 깨져서 오히려 더 읽기 어려워지기 때문.
function looksLikeMarkdown(text: string): boolean {
  return (
    /^\s{0,3}#{1,6}\s+\S/m.test(text) ||
    /^\s{0,3}[-*+]\s+\S/m.test(text) ||
    /^\s{0,3}\d+\.\s+\S/m.test(text) ||
    /\*\*[^*\n]+\*\*/.test(text) ||
    /```/.test(text) ||
    /^\s*\|?(\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?\s*$/m.test(text)
  );
}

function KnowledgeContentView({ content }: { content: string }) {
  if (looksLikeMarkdown(content)) {
    return (
      <div className="prose prose-invert prose-sm max-w-none prose-pre:bg-slate-800 prose-pre:border prose-pre:border-slate-700 prose-code:text-indigo-300 prose-th:text-slate-300 prose-td:text-slate-400">
        <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={safeRehypePlugins}>
          {content}
        </ReactMarkdown>
      </div>
    );
  }
  return (
    <pre className="text-xs font-mono text-slate-300 whitespace-pre overflow-x-auto">{content}</pre>
  );
}

// 가중치(문서 우선순위) 입력·표시는 2026-10-02 제거 — 쓰인 적 없고 틀린 검색은 내용·용어집을 고친다(정정 흐름)
// 업무구분(선택·AI 자동분석·필터·배지·일괄 변경)은 10/22 업무구분 폐지로 삭제 — 운영 지식은 시스템 컬렉션에 바로 붙는다(11/02)
interface KnowledgeFormData {
  content: string;
}

const defaultForm: KnowledgeFormData = {
  content: '',
};

// 원문 원장(2026-10-08): 원문마다 미리보기 조각 중 일부만 골랐나 — 다시 분할이 사람이 뺀 조각을 되살리지 않게 서버에 알린다
function partialSourceDocs(selected: ReviewChunk[], all: ReviewChunk[]): Set<number> {
  const count = (list: ReviewChunk[]) => list.reduce((m, c) => {
    if (c.sourceDocumentId) m.set(c.sourceDocumentId, (m.get(c.sourceDocumentId) ?? 0) + 1);
    return m;
  }, new Map<number, number>());
  const sel = count(selected);
  const out = new Set<number>();
  count(all).forEach((n, id) => { if ((sel.get(id) ?? 0) < n) out.add(id); });
  return out;
}

type BulkItems = Parameters<typeof bulkCreateKnowledge>[1];

// 같은 원본 다시 등록(A안 2026-10-12): 교체될 기존 조각에 사람이 손댄 흔적(고친·뺀 조각 등)이 있으면 사유를 보여주고 확인받는다.
// 흔적이 없으면 묻지 않고 바로 교체. 취소하면 null(등록 안 함). 서버도 확인 없이 들어온 교체는 409로 막는다.
async function bulkCreateConfirmed(namespace: string, items: BulkItems, sourceFile: string, sourceType: string,
                                   loc?: ChosenLocation | null) {
  // E2: 넣을 곳 카드에서 고른 곳으로. 담당이 아닌 곳(제안)은 서버가 원문·페이지 연결을 떼고 승인 대기로 — 교체 확인이 필요 없다
  const target = loc?.namespace ?? namespace;
  const docIds = [...new Set(items.map(i => i.source_document_id).filter((x): x is number => !!x))];
  const pages = [...new Set(items.map(i => i.confluence_page_id).filter((x): x is string => !!x))];
  let confirmReplace = false;
  if ((docIds.length || pages.length) && (!loc || loc.writable)) {
    const c = await checkBulkReplace(target, docIds, pages);
    if (c.needs_confirm) {
      const why = [
        c.edited ? `· 내용을 고친 조각 ${c.edited}건` : '',
        c.removed ? `· 삭제·반려한 조각 ${c.removed}건` : '',
        c.partial ? '· 지난번에 일부 조각만 골라 등록함' : '',
      ].filter(Boolean).join('\n');
      const ok = await showConfirm(
        `이미 등록된 같은 원본이 있고, 그 조각에 사람이 손댄 흔적이 있습니다.\n${why}\n\n등록하면 기존 조각은 새 조각으로 교체되어 위 변경이 사라집니다.`,
        { title: '기존 조각 교체 확인', confirmLabel: '교체하고 등록' });
      if (!ok) return null;
      confirmReplace = true;
    }
  }
  const result = await bulkCreateKnowledge(target, items, sourceFile, sourceType, confirmReplace,
    loc ? { decisionId: loc.decisionId, choice: loc.choice, overrideReason: loc.overrideReason ?? null } : null);
  if (result.unlinked_sources?.length) {
    void showAlert('미리보기 뒤에 원문이 바뀌어(다른 등록으로 교체 등) 이번 조각은 원문에 연결하지 않았습니다.\n'
      + '등록은 진행되지만 "다시 분할"은 쓸 수 없습니다 — 필요하면 다시 미리보기 후 등록하세요.', { title: '원문 연결 안 됨' });
  }
  return result;
}

type IngestMethod = 'file' | 'text' | 'manual' | 'url' | null;

// ── KnowledgeTable (메인) ─────────────────────────────────────────────────────

export function KnowledgeTable() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { selectedNs, canModifyNs } = useNamespaceAccess();
  // 정책 컬렉션이 바뀌면(사이드바 포함) 이 화면의 필터·선택을 비운다 — 예전엔 화면 안 선택기로 바꿀 때만 했다(10/21)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { setPage(1); setSelectedIds(new Set()); }, [selectedNs]);   // 체크 남으면 앞 영역 것이 일괄 삭제됐다(리뷰 M1)

  // ?tab=ingest — 컬렉션 › 조직의 [지식 등록 →]이 그 컬렉션을 고른 뒤 등록 탭으로 바로 연다(10/22, collection-design §15-9)
  const [searchParams, setSearchParams] = useSearchParams();
  // ?into=(컬렉션 화면 [지식 등록 →]의 넣을 곳)는 그 등록 한 번만 — 등록했거나 보는 컬렉션을 바꾸면 지운다(리뷰 M4: 계속 남아 다음 등록까지 고정됐다)
  const into = searchParams.get('into');
  const dropInto = () => setSearchParams((p) => { const n = new URLSearchParams(p); n.delete('into'); return n; }, { replace: true });
  useEffect(() => { if (into && selectedNs && into !== selectedNs) dropInto(); },   // eslint-disable-line react-hooks/exhaustive-deps
    [selectedNs]);
  const [subTab, setSubTab] = useState<'list' | 'ingest' | 'review'>(() => {
    const t = searchParams.get('tab');   // ?tab=review — 컬렉션 관리 › 홈 [승인 대기] 카드(11/03)
    return t === 'ingest' || t === 'review' ? t : 'list';
  });   // 정정 요청은 품질 메뉴로(10/21)
  // 조회 탭 state
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editForm, setEditForm] = useState<KnowledgeFormData>(defaultForm);
  const [showEdit, setShowEdit] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<number | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchMode, setSearchMode] = useState<'text' | 'vector'>('text');
  const [vectorSearchInput, setVectorSearchInput] = useState('');
  const [vectorQuery, setVectorQuery] = useState('');
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [showBulkConfirm, setShowBulkConfirm] = useState(false);
  const [showBulkEdit, setShowBulkEdit] = useState(false);
  const [bulkEditSourceType, setBulkEditSourceType] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(30);

  const { data: jobs = [] } = useQuery({
    queryKey: ['ingestion-jobs', selectedNs],
    queryFn: () => getIngestionJobs(selectedNs),
    enabled: !!selectedNs,
    staleTime: 10_000,
    refetchInterval: (query) => (query.state.data?.some((j) => j.status === 'processing') ? 2000 : false),
  });

  const { data: items = [], isLoading, error } = useQuery({
    queryKey: ['knowledge', selectedNs],
    queryFn: () => getKnowledge(selectedNs),
    enabled: !!selectedNs,
    staleTime: 15_000,
    refetchOnMount: 'always',
  });

  const { data: pendingItems = [] } = useQuery({
    queryKey: ['knowledge', selectedNs, 'pending_review'],
    queryFn: () => getKnowledge(selectedNs, 'pending_review'),
    enabled: !!selectedNs,
    staleTime: 10_000,
    refetchOnMount: 'always',
  });


  const updateMutation = useMutation({
    mutationFn: (id: number) =>
      updateKnowledge(id, {
        content: editForm.content,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['knowledge', selectedNs] });
      setEditingId(null);
      setShowEdit(false);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteKnowledge(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['knowledge', selectedNs] });
      qc.invalidateQueries({ queryKey: ['stats-ns', selectedNs] });
      setDeleteTarget(null);
      setShowEdit(false);
    },
  });

  const vectorSearchQuery = useQuery({
    queryKey: ['knowledge-vector-search', selectedNs, vectorQuery],
    queryFn: () => vectorSearchKnowledge(selectedNs, vectorQuery),
    enabled: searchMode === 'vector' && !!vectorQuery && !!selectedNs,
    staleTime: 30_000,
  });

  const bulkDeleteMutation = useMutation({
    mutationFn: (ids: number[]) => bulkDeleteKnowledge(ids),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['knowledge', selectedNs] });
      qc.invalidateQueries({ queryKey: ['stats-ns', selectedNs] });
      setSelectedIds(new Set());
      setShowBulkConfirm(false);
    },
  });

  const bulkUpdateMutation = useMutation({
    mutationFn: (ids: number[]) => bulkUpdateKnowledge(ids, {
      ...(bulkEditSourceType ? { source_type: bulkEditSourceType } : {}),
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['knowledge', selectedNs] });
      qc.invalidateQueries({ queryKey: ['stats-ns', selectedNs] });
      setSelectedIds(new Set());
      setShowBulkEdit(false);
      setBulkEditSourceType('');
    },
  });

  const startEdit = (item: KnowledgeItem) => {
    setEditingId(item.id);
    setEditForm({ content: item.content });
    setShowEdit(true);
  };

  const textFilteredItems = items.filter((item) => {
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      const content = item.content.toLowerCase();
      const src = ((item as any).source_file ?? '').toLowerCase();
      if (!content.includes(q) && !src.includes(q)) return false;
    }
    return true;
  });
  const filteredItems = textFilteredItems; // for count ref
  const searchedItems = searchMode === 'vector' && vectorQuery
    ? (vectorSearchQuery.data ?? [])
    : textFilteredItems;
  const displayItems = searchedItems;
  const { totalPages, totalItems, slice } = useClientPaging(displayItems, pageSize);
  const pagedItems = slice(page);

  const allPageSelected = pagedItems.length > 0 && pagedItems.every((i) => selectedIds.has(i.id));
  const toggleSelectAll = () => {
    if (allPageSelected) {
      setSelectedIds((prev) => { const next = new Set(prev); pagedItems.forEach((i) => next.delete(i.id)); return next; });
    } else {
      setSelectedIds((prev) => { const next = new Set(prev); pagedItems.forEach((i) => next.add(i.id)); return next; });
    }
  };
  const toggleSelect = (id: number) => {
    setSelectedIds((prev) => { const next = new Set(prev); next.has(id) ? next.delete(id) : next.add(id); return next; });
  };

  const onIngestSuccess = () => {
    // 판정 캐시 비우기 — 같은 글을 다시 넣을 때 이미 확정된 판정 줄을 다시 쓰지 않게(리뷰 M1: 403·원장 누락)
    qc.removeQueries({ queryKey: ['locate'] });
    if (into) dropInto();
    qc.invalidateQueries({ queryKey: ['knowledge', selectedNs] });
    qc.invalidateQueries({ queryKey: ['ingestion-jobs', selectedNs] });
    qc.invalidateQueries({ queryKey: ['stats-ns', selectedNs] });
  };

  const onReviewResolved = () => {
    qc.invalidateQueries({ queryKey: ['knowledge', selectedNs] });
    qc.invalidateQueries({ queryKey: ['stats-ns', selectedNs] });
  };

  return (
    <div className="space-y-4">
      {/* 헤더 + 네임스페이스 */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-200">
          운영 지식
        </h2>
      </div>

      {/* 서브탭 */}
      <div className="flex gap-1 border-b border-slate-700 pb-0">
        <button
          onClick={() => setSubTab('list')}
          className={`flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-t-lg transition-colors border-b-2 -mb-px ${
            subTab === 'list'
              ? 'border-indigo-500 text-indigo-400 bg-slate-800/50'
              : 'border-transparent text-slate-400 hover:text-slate-200'
          }`}
        >
          <List className="w-4 h-4" />
          지식 조회
        </button>
        <button
          onClick={() => setSubTab('ingest')}
          className={`flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-t-lg transition-colors border-b-2 -mb-px ${
            subTab === 'ingest'
              ? 'border-indigo-500 text-indigo-400 bg-slate-800/50'
              : 'border-transparent text-slate-400 hover:text-slate-200'
          }`}
        >
          <Upload className="w-4 h-4" />
          지식 등록
        </button>
        <button
          onClick={() => setSubTab('review')}
          className={`flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-t-lg transition-colors border-b-2 -mb-px ${
            subTab === 'review'
              ? 'border-indigo-500 text-indigo-400 bg-slate-800/50'
              : 'border-transparent text-slate-400 hover:text-slate-200'
          }`}
          title="기존 지식과 유사도가 높아 자동 반영되지 않고 검토를 기다리는 항목"
        >
          <AlertCircle className="w-4 h-4" />
          승인 대기
          {pendingItems.length > 0 && (
            <span className="ml-1 text-[10px] bg-amber-100 text-amber-700 dark:bg-amber-900/60 dark:text-amber-400 px-1.5 py-0.5 rounded-full">{pendingItems.length}</span>
          )}
        </button>
      </div>

      {/* ── 서브탭 1: 조회 ── */}
      {subTab === 'list' && (
        <div className="space-y-3">
          {/* 필터 바 */}
          <div className="flex items-end gap-3 flex-wrap">
            {/* Search mode toggle */}
            <div className="flex rounded-lg border border-slate-600 overflow-hidden text-xs font-medium flex-shrink-0">
              <button
                onClick={() => { setSearchMode('text'); setVectorQuery(''); }}
                className={`px-3 py-2 transition-colors ${searchMode === 'text' ? 'bg-indigo-600 text-white' : 'bg-slate-800 text-slate-400 hover:text-slate-200'}`}
              >문자열</button>
              <button
                onClick={() => { setSearchMode('vector'); setSearchQuery(''); setPage(1); setSelectedIds(new Set()); }}
                className={`px-3 py-2 transition-colors ${searchMode === 'vector' ? 'bg-indigo-600 text-white' : 'bg-slate-800 text-slate-400 hover:text-slate-200'}`}
              >벡터</button>
            </div>

            {searchMode === 'text' ? (
              <div className="relative flex-1 min-w-48">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
                <input
                  type="text"
                  placeholder="내용 검색..."
                  value={searchQuery}
                  onChange={(e) => { setSearchQuery(e.target.value); setPage(1); setSelectedIds(new Set()); }}
                  className="w-full pl-9 pr-3 py-2 bg-slate-900 border border-slate-600 rounded-lg text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
                />
              </div>
            ) : (
              <div className="flex flex-1 gap-2 min-w-48">
                <div className="relative flex-1">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
                  <input
                    type="text"
                    placeholder="유사 지식 검색 (Enter로 실행)..."
                    value={vectorSearchInput}
                    onChange={(e) => setVectorSearchInput(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter') { setVectorQuery(vectorSearchInput); setPage(1); setSelectedIds(new Set()); } }}
                    className="w-full pl-9 pr-3 py-2 bg-slate-900 border border-slate-600 rounded-lg text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
                  />
                </div>
                <button
                  onClick={() => { setVectorQuery(vectorSearchInput); setPage(1); setSelectedIds(new Set()); }}
                  disabled={!vectorSearchInput.trim()}
                  className="px-3 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white text-sm rounded-lg transition-colors flex-shrink-0"
                >
                  {vectorSearchQuery.isFetching ? <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" /> : '검색'}
                </button>
              </div>
            )}

            {canModifyNs && (
              <label className="flex items-center gap-2 text-sm text-slate-400 cursor-pointer select-none pb-0.5">
                <input
                  type="checkbox"
                  checked={allPageSelected}
                  onChange={toggleSelectAll}
                  className="w-4 h-4 rounded accent-indigo-500"
                />
                전체 선택
              </label>
            )}
          </div>

          {/* Bulk action bar */}
          {selectedIds.size > 0 && canModifyNs && (
            <div className="flex items-center gap-3 px-4 py-2.5 bg-indigo-900/30 border border-indigo-700/40 rounded-xl">
              <span className="text-sm text-indigo-300 flex-1">{selectedIds.size}개 선택됨</span>
              <Button variant="ghost" size="sm" onClick={() => setSelectedIds(new Set())}>선택 해제</Button>
              <Button variant="secondary" size="sm" onClick={() => setShowBulkEdit(true)}>
                <PenLine className="w-3.5 h-3.5" />일괄 수정
              </Button>
              <Button variant="danger" size="sm" onClick={() => setShowBulkConfirm(true)}>
                <Trash2 className="w-3.5 h-3.5" />삭제
              </Button>
            </div>
          )}

          {!selectedNs && <div className="text-center py-16 text-slate-500">시스템 컬렉션을 선택하세요.</div>}
          {selectedNs && isLoading && <div className="text-center py-16 text-slate-500 animate-pulse">로딩 중...</div>}
          {selectedNs && error && <div className="text-center py-16 text-rose-400">오류가 발생했습니다.</div>}

          {selectedNs && !isLoading && (
            <div className="space-y-2">
              <PaginationInfo totalItems={totalItems} pageSize={pageSize} onPageSizeChange={setPageSize} />
              {pagedItems.map((item) => (
                <div key={item.id}
                  className="bg-slate-800 border border-slate-700 rounded-xl px-4 py-3 flex items-center gap-3 hover:bg-slate-700/50 transition-colors"
                >
                  {canModifyNs && (
                    <input
                      type="checkbox"
                      checked={selectedIds.has(item.id)}
                      onChange={() => toggleSelect(item.id)}
                      className="w-4 h-4 rounded accent-indigo-500 flex-shrink-0"
                    />
                  )}
                  <div className="flex flex-1 items-center gap-3 cursor-pointer min-w-0" onClick={() => startEdit(item)}>
                    <div className="flex-1 min-w-0">
                      <p className="text-xs text-slate-500 truncate">{item.content.slice(0, 100)}...</p>
                    </div>
                    <div className="flex items-center gap-2 flex-shrink-0 text-[10px] text-slate-500">
                      {searchMode === 'vector' && (item as KnowledgeItem & { similarity?: number }).similarity != null && (
                        <span className="px-1.5 py-0.5 rounded bg-violet-50 text-violet-700 border border-violet-200 dark:bg-violet-900/40 dark:text-violet-300 dark:border-violet-700/40 font-mono">
                          {((item as KnowledgeItem & { similarity?: number }).similarity! * 100).toFixed(1)}%
                        </span>
                      )}
                      <span>{new Date(item.updated_at !== item.created_at ? item.updated_at : item.created_at).toISOString().slice(0, 10)}</span>
                      {item.created_by_username && <span>{item.created_by_username}</span>}
                    </div>
                  </div>
                </div>
              ))}
              {displayItems.length === 0 && !vectorSearchQuery.isFetching && (
                <div className="text-center py-16 text-slate-500">
                  {searchQuery || vectorQuery ? '검색 결과가 없습니다.' : '지식 항목이 없습니다.'}
                </div>
              )}
              {vectorSearchQuery.isFetching && (
                <div className="flex items-center justify-center gap-2 py-16 text-slate-400 text-sm">
                  <div className="w-4 h-4 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
                  벡터 유사도 검색 중...
                </div>
              )}
              <PaginationNav page={page} totalPages={totalPages} onPageChange={setPage} />
            </div>
          )}
        </div>
      )}

      {/* ── 서브탭 2: 등록 ── */}
      {subTab === 'ingest' && (
        <IngestTab
          namespace={selectedNs}
          preset={into}
          jobs={jobs}
          onSuccess={onIngestSuccess}
          onGoToList={() => setSubTab('list')}
          onGoToReview={() => setSubTab('review')}
          onImportStarted={(id) => navigate(`${adminPath('k.sources')}?batch=${id}`)}   // 소스 연결에서 그 배치를 연다(10/30)
        />
      )}

      {/* ── 서브탭: 적재 검토(공간·하위 전체 넣기 배치) ── */}

      {/* ── 서브탭 3: 승인 대기 ── */}
      {subTab === 'review' && (
        <div className="space-y-3">
          <TabGuide
            what="새로 등록된 지식 중 기존 지식과 내용이 많이 겹쳐 아직 검색에 안 쓰이는 항목"
            when="지식 등록(파일·텍스트·직접 입력·URL) 시 기존 지식과 유사도가 높게 나올 때"
            todo="항목을 눌러 겹치는 기존 지식을 확인하고 승인 · 병합 · 반려"
            detail="중복 지식이 쌓이면 검색 결과가 같은 내용으로 채워져 다른 근거가 밀려납니다. 승인하면 그대로 검색에 쓰이고, 병합하면 기존 지식에 합쳐지며, 반려하면 쓰이지 않습니다."
          />
          <ReviewTab
            items={pendingItems}
            canModify={canModifyNs}
            onResolved={onReviewResolved}
          />
        </div>
      )}


      {/* Edit Modal */}
      <Modal isOpen={showEdit} onClose={() => { setShowEdit(false); setEditingId(null); }}
        title={canModifyNs ? '지식 수정' : '지식 상세'} maxWidth="max-w-2xl">
        <div className="space-y-3">
          {canModifyNs && (
            <div className="flex justify-end pb-3 border-b border-slate-700">
              <Button variant="danger" size="sm" onClick={() => editingId !== null && setDeleteTarget(editingId)}>
                <Trash2 className="w-3.5 h-3.5" />삭제
              </Button>
            </div>
          )}
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">내용</label>
            <textarea rows={10} value={editForm.content} readOnly={!canModifyNs}
              onChange={(e) => setEditForm((f) => ({ ...f, content: e.target.value }))}
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 resize-y min-h-[260px] read-only:border-slate-700 leading-relaxed" />
          </div>
          {/* 원문 위치는 부차 정보 — 내용 아래, 접힌 한 줄(사용자 피드백 2026-10-07) */}
          {editingId !== null && <KnowledgeStructurePanel knowledgeId={editingId} canModify={canModifyNs} />}
          {/* 이 지식이 검색에 잡히나 — 질문 하나로 AI가 보는 근거와 이 지식의 순위(10/28 AI가 본 근거) */}
          {editingId !== null && selectedNs && <EvidenceButton namespace={selectedNs} highlightId={editingId} label="이 지식이 검색에 잡히는지 보기" canFlag={canModifyNs} />}
          {updateMutation.error && <p className="text-xs text-rose-600 dark:text-rose-400">{String(updateMutation.error)}</p>}
          <div className="flex gap-2 justify-end pt-2">
            <Button variant="ghost" size="sm" onClick={() => { setShowEdit(false); setEditingId(null); }}>
              <X className="w-3.5 h-3.5" />{canModifyNs ? '취소' : '닫기'}
            </Button>
            {canModifyNs && (
              <Button variant="primary" size="sm" loading={updateMutation.isPending}
                onClick={() => editingId !== null && updateMutation.mutate(editingId)}
                disabled={!editForm.content.trim()}>
                저장
              </Button>
            )}
          </div>
        </div>
      </Modal>

      {/* Bulk Delete Confirm Modal */}
      <Modal isOpen={showBulkConfirm} onClose={() => setShowBulkConfirm(false)} title="지식 일괄 삭제">
        <div className="space-y-4">
          <p className="text-sm text-slate-300">선택한 <span className="text-rose-400 font-semibold">{selectedIds.size}개</span> 지식 항목을 삭제하시겠습니까? 되돌릴 수 없습니다.</p>
          {bulkDeleteMutation.error && <p className="text-xs text-rose-600 dark:text-rose-400">{String(bulkDeleteMutation.error)}</p>}
          <div className="flex gap-2 justify-end">
            <Button variant="secondary" size="sm" onClick={() => setShowBulkConfirm(false)}>취소</Button>
            <Button variant="danger" size="sm" loading={bulkDeleteMutation.isPending} onClick={() => bulkDeleteMutation.mutate(Array.from(selectedIds))}>삭제</Button>
          </div>
        </div>
      </Modal>

      {/* Bulk Edit Modal */}
      <Modal isOpen={showBulkEdit} onClose={() => setShowBulkEdit(false)} title="지식 일괄 수정">
        <div className="space-y-4">
          <p className="text-sm text-slate-300">선택한 <span className="text-indigo-400 font-semibold">{selectedIds.size}개</span> 지식 항목의 소스유형을 변경합니다.</p>
          <div>
            <label className="text-xs font-medium text-slate-400 block mb-1">소스유형</label>
            <select
              value={bulkEditSourceType}
              onChange={(e) => setBulkEditSourceType(e.target.value)}
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
            >
              <option value="">(변경 안 함)</option>
              <option value="manual">수동</option>
              <option value="csv_import">CSV</option>
              <option value="file_upload">파일</option>
              <option value="paste_split">텍스트</option>
              <option value="web">웹</option>
              <option value="confluence">Confluence</option>
              <option value="confluence_bulk">Confluence 일괄</option>
            </select>
          </div>
          {bulkUpdateMutation.error && <p className="text-xs text-rose-600 dark:text-rose-400">{String(bulkUpdateMutation.error)}</p>}
          <div className="flex gap-2 justify-end">
            <Button variant="secondary" size="sm" onClick={() => setShowBulkEdit(false)}>취소</Button>
            <Button
              variant="primary" size="sm"
              disabled={!bulkEditSourceType}
              loading={bulkUpdateMutation.isPending}
              onClick={() => bulkUpdateMutation.mutate(Array.from(selectedIds))}
            >
              적용
            </Button>
          </div>
        </div>
      </Modal>

      {/* Delete Confirm Modal */}
      <Modal isOpen={deleteTarget !== null} onClose={() => setDeleteTarget(null)} title="지식 삭제">
        <div className="space-y-4">
          <p className="text-sm text-slate-300">이 지식 항목을 삭제하시겠습니까? 되돌릴 수 없습니다.</p>
          {deleteMutation.error && <p className="text-xs text-rose-600 dark:text-rose-400">{String(deleteMutation.error)}</p>}
          <div className="flex gap-2 justify-end">
            <Button variant="secondary" size="sm" onClick={() => setDeleteTarget(null)}>취소</Button>
            <Button variant="danger" size="sm" loading={deleteMutation.isPending}
              onClick={() => deleteTarget !== null && deleteMutation.mutate(deleteTarget)}>
              삭제
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}


// ── 청크 검토 모달 ─────────────────────────────────────────────────────────────

interface ReviewChunk {
  idx: number;
  text: string;
  title: string | null;
  selected: boolean;
  /** 상위 맥락 — 컨플루언스 일괄은 상위·자기 페이지 제목 + 페이지 내 헤딩 조상, 파일·붙여넣기·단일 URL은 문서 안 제목 조상
   * (2026-09-22 일괄, 2026-10-07 전 경로). 확정(bulkCreateKnowledge)까지 실려가야 검색 컨텍스트에 반영된다. */
  headingPath?: string[] | null;
  /** 원문 원장 id(2026-10-08) — 미리보기가 저장한 원문. 확정 때 청크에 연결 */
  sourceDocumentId?: number | null;
  /** 컨플루언스 원본 페이지 식별(2026-09-28) — 확정까지 실려가야 같은 페이지 재등록 시 옛 버전이
   * 교체된다(예전엔 여기서 버려져 재등록마다 옛 버전과 새 버전이 함께 검색됐음). */
  confluencePageId?: string | null;
  confluenceVersion?: number | null;
}

// analyzer.py의 chunk_strategy 어휘(section/paragraph/fixed/auto) — 파일 업로드와
// 텍스트 붙여넣기 양쪽의 청킹 함수가 공통으로 지원하는 값만 노출한다.
const STRATEGY_OPTIONS: { value: string; label: string; desc: string }[] = [
  { value: 'auto', label: 'AI 자동 감지', desc: 'AI가 문서 구조를 분석해 최적 전략을 선택' },
  { value: 'section', label: '섹션 기준', desc: '제목/헤더 구조가 뚜렷한 문서에 적합' },
  { value: 'paragraph', label: '단락 기준', desc: '헤더 없이 문단으로만 나뉜 텍스트에 적합' },
  { value: 'fixed', label: '고정 길이', desc: '구조가 불규칙하거나 아주 긴 텍스트를 균등 분할' },
];

function ChunkReviewModal({ isOpen, onClose, chunks, onConfirm, loading, sourceName, currentStrategy, onRestrategize, unstructuredPages, preset }: {
  isOpen: boolean;
  onClose: () => void;
  chunks: ReviewChunk[];
  onConfirm: (selected: ReviewChunk[], loc: ChosenLocation | null) => Promise<void>;
  /** 컬렉션 화면에서 넘어온 넣을 곳(?into=) — 없으면 AI가 정한다(E2) */
  preset?: string | null;
  loading: boolean;
  sourceName: string;
  currentStrategy?: string | null;
  onRestrategize?: (strategy: string) => Promise<void>;
  /** 헤딩 없이 작성된 페이지 제목(2026-10-01) — 섹션 경계·부모 섹션 확장이 원본 헤딩에 의존해서
   * 등록 전에 알린다(정적 파싱이라 원본을 고치는 게 근본 해결). */
  unstructuredPages?: string[];
}) {
  const [rows, setRows] = useState<ReviewChunk[]>([]);
  const [expandedIdx, setExpandedIdx] = useState<Set<number>>(new Set());
  const [restrategizing, setRestrategizing] = useState(false);
  const [restrategizeError, setRestrategizeError] = useState('');
  const [loc, setLoc] = useState<ChosenLocation | null>(null);
  // 넣을 곳 판정에 보내는 글 = 조각 앞부분(문서 단위 — 실측과 같은 단위). 선택을 바꿔도 다시 묻지 않는다
  const locateText = useMemo(() => chunks.map(c => c.text).join('\n\n').slice(0, 2500), [chunks]);

  useEffect(() => {
    setRows(chunks.map(c => ({ ...c, selected: true })));
    setExpandedIdx(new Set());
  }, [chunks]);

  const handleStrategyChange = async (value: string) => {
    if (!onRestrategize || value === (currentStrategy ?? 'auto')) return;
    setRestrategizing(true); setRestrategizeError('');
    try {
      await onRestrategize(value);
    } catch (e: any) {
      setRestrategizeError(e.message || '재청킹 실패');
    } finally {
      setRestrategizing(false);
    }
  };

  const selectedCount = rows.filter(r => r.selected).length;
  const allSelected = rows.length > 0 && selectedCount === rows.length;

  const toggleAll = () => setRows(prev => prev.map(r => ({ ...r, selected: !allSelected })));
  const toggleRow = (idx: number) => setRows(prev => prev.map(r => r.idx === idx ? { ...r, selected: !r.selected } : r));
  const toggleExpand = (idx: number) => setExpandedIdx(prev => {
    const s = new Set(prev);
    s.has(idx) ? s.delete(idx) : s.add(idx);
    return s;
  });

  return (
    <Modal isOpen={isOpen} onClose={onClose} title={`청크 검토 — ${sourceName}`} maxWidth="max-w-3xl">
      <div className="space-y-3">
        {isOpen && <LocationPicker text={locateText} preset={preset} onChange={setLoc} />}
        {unstructuredPages && unstructuredPages.length > 0 && (
          <div
            className="rounded-lg border px-3 py-2 text-xs bg-amber-50 border-amber-200 text-amber-800 dark:bg-amber-900/30 dark:border-amber-700/40 dark:text-amber-300"
            title={`헤딩(제목 스타일) 없이 작성된 페이지라 섹션이 나뉘지 않습니다. 검색 정확도와 문맥 확장이 떨어질 수 있으니, 가능하면 원본에서 섹션 제목을 헤딩으로 지정해 주세요.\n\n${unstructuredPages.join('\n')}`}
          >
            헤딩 없는 페이지 {unstructuredPages.length}개 — 섹션 구분 안 됨
          </div>
        )}
        {/* 청킹 전략 재선택 */}
        {onRestrategize && (
          <div className="rounded-lg border border-slate-700/60 bg-slate-900/40 px-3 py-2.5 space-y-1.5">
            <div className="flex items-center gap-2 flex-wrap">
              <label className="text-xs font-medium text-slate-400 flex-shrink-0">청킹 전략</label>
              <select
                value={currentStrategy ?? 'auto'}
                onChange={(e) => handleStrategyChange(e.target.value)}
                disabled={restrategizing || loading}
                className="bg-slate-800 border border-slate-600 rounded px-2 py-1 text-xs text-slate-200 focus:outline-none focus:border-indigo-500 disabled:opacity-50"
              >
                {STRATEGY_OPTIONS.map(opt => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
              {restrategizing && <span className="text-[11px] text-indigo-400">재청킹 중...</span>}
            </div>
            <p className="text-[11px] text-slate-500">
              {STRATEGY_OPTIONS.find(o => o.value === (currentStrategy ?? 'auto'))?.desc}
            </p>
            {restrategizeError && <p className="text-[11px] text-rose-400">{restrategizeError}</p>}
          </div>
        )}

        {/* 전체 선택 / 카운터 */}
        <div className="flex items-center justify-between pb-2 border-b border-slate-700/60">
          <label className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer select-none">
            <input type="checkbox" checked={allSelected} onChange={toggleAll}
              className="w-4 h-4 rounded accent-indigo-500" />
            전체 선택
          </label>
          <span className="text-sm font-medium text-indigo-400">{selectedCount}/{rows.length} 선택됨</span>
        </div>

        {/* 청크 목록 (스크롤) */}
        <div className="max-h-[55vh] overflow-y-auto space-y-1.5 pr-1">
          {rows.map((chunk) => {
            const expanded = expandedIdx.has(chunk.idx);
            const preview = chunk.text.slice(0, 150);
            const hasMore = chunk.text.length > 150;
            return (
              <div key={chunk.idx}
                className={`rounded-lg border px-3 py-2 transition-colors ${
                  chunk.selected
                    ? 'border-indigo-300 bg-indigo-50 dark:border-indigo-600/60 dark:bg-indigo-950/30'
                    : 'border-slate-700/60 bg-slate-900/30 opacity-50'
                }`}
              >
                <div className="flex items-start gap-2">
                  <input type="checkbox" checked={chunk.selected} onChange={() => toggleRow(chunk.idx)}
                    className="w-4 h-4 rounded accent-indigo-500 mt-0.5 flex-shrink-0" />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap mb-1">
                      <span className="text-[11px] font-mono text-indigo-400 font-medium">#{chunk.idx + 1}</span>
                      {chunk.title && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-900/40 text-amber-300 border border-amber-700/30">
                          {chunk.title}
                        </span>
                      )}
                      <span className="text-[10px] text-slate-600">{chunk.text.length}자</span>
                    </div>
                    <p className="text-xs text-slate-300 leading-relaxed whitespace-pre-wrap break-words">
                      {expanded ? chunk.text : preview}{!expanded && hasMore ? '...' : ''}
                    </p>
                    {hasMore && (
                      <button onClick={() => toggleExpand(chunk.idx)}
                        className="flex items-center gap-0.5 text-[10px] text-slate-500 hover:text-slate-300 mt-1">
                        {expanded
                          ? <><ChevronUp className="w-3 h-3" />접기</>
                          : <><ChevronDown className="w-3 h-3" />더보기</>
                        }
                      </button>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
          {rows.length === 0 && (
            <p className="text-center py-8 text-slate-500 text-sm">파싱된 청크가 없습니다.</p>
          )}
        </div>

        <div className="flex gap-2 justify-end pt-2 border-t border-slate-700">
          <Button variant="ghost" size="sm" onClick={onClose} disabled={loading}>취소</Button>
          <Button variant="primary" size="sm" loading={loading}
            disabled={selectedCount === 0 || !loc}
            title={!loc ? '넣을 곳을 먼저 골라 주세요' : undefined}
            onClick={() => onConfirm(rows.filter(r => r.selected), loc)}>
            <CheckCircle className="w-3.5 h-3.5" />{loc && !loc.writable ? `승인 요청 (${selectedCount}건)` : `선택 항목 등록 (${selectedCount}건)`}
          </Button>
        </div>
      </div>
    </Modal>
  );
}


// ── 인제스천 진행률 모달 (처리 중인 작업 클릭 시) ─────────────────────────────

function IngestionProgressModal({ jobId, onClose, onSettled }: {
  jobId: number | null;
  onClose: () => void;
  onSettled: (job: IngestionJobStatus) => void;
}) {
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState('');

  const { data: job } = useQuery({
    queryKey: ['ingestion-job-status', jobId],
    queryFn: () => getIngestionJobStatus(jobId!),
    enabled: jobId !== null,
    refetchInterval: (query) => (query.state.data?.status === 'processing' ? 1500 : false),
  });

  const prevStatus = useRef<string | null>(null);
  useEffect(() => {
    if (job && prevStatus.current === 'processing' && job.status !== 'processing') {
      onSettled(job);
    }
    if (job) prevStatus.current = job.status;
  }, [job, onSettled]);

  const handleCancel = async () => {
    if (!jobId) return;
    setCancelling(true); setCancelError('');
    try {
      await cancelIngestionJob(jobId);
    } catch (e: any) {
      setCancelError(e.message || '중지 요청 실패');
    } finally {
      setCancelling(false);
    }
  };

  const pct = job && job.total_chunks > 0 ? Math.round((job.created_chunks / job.total_chunks) * 100) : 0;
  const isProcessing = job?.status === 'processing';

  return (
    <Modal isOpen={jobId !== null} onClose={onClose} title="등록 진행 상황">
      {!job ? (
        <div className="py-8 text-center text-sm text-slate-500">불러오는 중...</div>
      ) : (
        <div className="space-y-4">
          <div className="text-sm text-slate-300 truncate">{job.source_file || '-'}</div>

          <div>
            <div className="flex items-center justify-between text-xs text-slate-400 mb-1.5">
              <span>{job.created_chunks} / {job.total_chunks}건</span>
              <span className="font-medium text-slate-300">{pct}%</span>
            </div>
            <div className="w-full h-2.5 rounded-full bg-slate-700 overflow-hidden">
              <div
                className={`h-full rounded-full transition-all duration-300 ${
                  job.status === 'failed' ? 'bg-rose-500' : job.status === 'cancelled' ? 'bg-slate-500' : 'bg-indigo-500'
                }`}
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>

          <div className="flex items-center gap-2 text-xs">
            <span className={`px-1.5 py-0.5 rounded font-medium ${
              job.status === 'completed' ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400' :
              job.status === 'failed' ? 'bg-rose-100 text-rose-700 dark:bg-rose-900/30 dark:text-rose-400' :
              job.status === 'cancelled' ? 'bg-slate-700 text-slate-400' :
              'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400'
            }`}>
              {job.status}
            </span>
            {job.status === 'processing' && job.cancel_requested && (
              <span className="text-slate-500">중지 처리 중... (다음 배치 경계에서 중단됩니다)</span>
            )}
          </div>

          {job.error_message && (
            <div className="rounded-lg bg-rose-900/30 border border-rose-700/50 px-3 py-2 text-xs text-rose-300 whitespace-pre-wrap">
              {job.error_message}
            </div>
          )}

          {job.status === 'cancelled' && (
            <p className="text-xs text-slate-500">중지되어 이미 등록된 항목도 함께 롤백(삭제)되었습니다.</p>
          )}

          {job.status === 'completed' && job.quality && job.quality.warnings.length > 0 && (
            <div data-testid="ingestion-quality-warning"
              className="rounded-lg border border-amber-300 dark:border-amber-700/60 bg-amber-50 dark:bg-amber-950/20 px-3 py-2 text-xs space-y-1"
              title="저장된 청크를 구조로 검사한 결과 — 원문을 다시 확인하고 필요하면 지우고 다시 등록하세요">
              <p className="font-medium text-amber-800 dark:text-amber-200">분할 품질 확인 필요</p>
              <ul className="list-disc pl-4 text-amber-700 dark:text-amber-300">
                {job.quality.warnings.map((w) => <li key={w}>{w}</li>)}
              </ul>
            </div>
          )}

          {cancelError && <p className="text-xs text-rose-600 dark:text-rose-400">{cancelError}</p>}

          <div className="flex gap-2 justify-end pt-1">
            <Button variant="ghost" size="sm" onClick={onClose}>닫기 (백그라운드 계속 진행)</Button>
            {isProcessing && !job.cancel_requested && (
              <Button variant="danger" size="sm" loading={cancelling} onClick={handleCancel}>중지</Button>
            )}
          </div>
        </div>
      )}
    </Modal>
  );
}


// ── 서브탭 2: 지식 등록 ───────────────────────────────────────────────────────

/** 폼 → 등록 탭: 비동기 묶음(jobId)·단건 승인 대기 여부 + 실제로 넣은 곳(E2 — 지금 보는 컬렉션과 다를 수 있다) */
type IngestOutcome = { jobId?: number; pendingReview?: boolean; target?: ChosenLocation | null };

function doneText(n: number, loc: ChosenLocation | null | undefined): string {
  if (loc && !loc.writable) return `${n}건을 '${loc.label}' 담당의 승인 대기로 보냈어요 — 승인되면 검색에 나와요.`;
  return `${n}건 등록을 시작했습니다${loc ? ` — 넣는 곳 '${loc.label}'` : ''} · 진행 상황은 아래 등록 이력에서 확인하세요.`;
}

function IngestTab({ namespace, preset, jobs, onSuccess, onGoToList, onGoToReview, onImportStarted }: {
  namespace: string;          // 지금 보는 컬렉션(미리보기 원문 초안·등록 이력 기준)
  preset?: string | null;     // 컬렉션 화면의 [지식 등록 →]이 정한 넣을 곳(?into=) — 없으면 AI가 정한다(E2)
  jobs: IngestionJob[];
  onSuccess: () => void;
  onGoToList: () => void;
  onGoToReview: () => void;
  onImportStarted: (batchId: number) => void;   // 공간·하위 전체 넣기 시작 → [적재 검토] 탭에서 그 배치를 연다
}) {
  const isViewer = useAuthStore((s) => s.user?.role === 'viewer');
  const { setSelectedNs } = useNamespaceAccess();
  const [activeMethod, setActiveMethod] = useState<IngestMethod>(null);
  const [progressJobId, setProgressJobId] = useState<number | null>(null);
  const [autoNavigate, setAutoNavigate] = useState(false);
  const [suggestTarget, setSuggestTarget] = useState<ChosenLocation | null>(null);   // 담당 아닌 곳에 올린 묶음 — 끝나면 결과 안내
  const [jobPage, setJobPage] = useState(1);
  const [jobPageSize, setJobPageSize] = useState(30);
  const { totalPages: jobTotalPages, totalItems: jobTotalItems, slice: sliceJobs } = useClientPaging(jobs, jobPageSize);
  const pagedJobs = sliceJobs(jobPage);

  // 폼 제출 성공 시: 비동기 배치 작업(jobId)이면 진행 모달을 띄우고 완료될 때까지
  // 기다렸다가 결과(승인 대기 여부)에 따라 이동, 동기 단건 등록이면 즉시 이동.
  const handleFormOutcome = (outcome?: IngestOutcome) => {
    const t = outcome?.target;
    if (t && !t.writable) {
      // 담당이 아닌 곳에 넣은 제안 — 그 담당의 승인 대기. 지금 보는 컬렉션 목록엔 안 나오니 이동하지 않는다.
      // 묶음은 진행 창으로 끝까지 보여 주고 실제 결과로 알린다(리뷰 M2 — "보냈어요"만 보이고 실패가 숨었다)
      if (outcome?.jobId) {
        setSuggestTarget(t);
        setProgressJobId(outcome.jobId);
        return;
      }
      if (outcome?.pendingReview) {
        void showAlert(`'${t.label}' 담당의 승인 대기로 보냈어요. 담당이 승인하면 검색에 나와요.`, { title: '승인 요청을 보냈어요' });
      } else {
        void showAlert(`'${t.label}'에 바로 등록됐어요(담당 권한이 있어요).`, { title: '등록 완료' });   // 화면과 서버 판단이 어긋난 경우(리뷰 L3)
      }
      return;
    }
    if (t && t.namespace !== namespace) setSelectedNs(t.namespace);   // 다른 곳에 넣었으면 그 컬렉션으로 — 결과가 목록에 보이게
    if (outcome?.jobId) {
      setProgressJobId(outcome.jobId);
      setAutoNavigate(true);
    } else if (outcome?.pendingReview) {
      void showAlert('유사한 기존 지식이 있어 승인 대기 상태로 등록됐습니다.\n승인 대기 탭으로 이동합니다.', { title: '승인 대기로 등록' })
        .then(onGoToReview);
    } else {
      onGoToList();
    }
  };

  // E2: 넣을 곳은 등록할 때 정한다(AI 제안 + 내가 고름) — 지금 보는 컬렉션의 담당이 아니어도 넣을 수 있다(담당이 아닌 곳은 승인 대기)
  if (isViewer) {
    return <div className="text-center py-16 text-slate-500">조회 전용 계정은 지식을 등록할 수 없어요.</div>;
  }

  const methods: { id: IngestMethod; icon: React.ReactNode; title: string; desc: string; badge?: string }[] = [
    { id: 'file', icon: <Upload className="w-6 h-6" />, title: '파일 업로드', desc: 'PDF · Markdown · TXT · Excel(.xlsx) · CSV 파일을 드래그하거나 클릭하여 업로드. 자동 파싱·청킹.', badge: 'AI 분석 지원' },
    { id: 'text', icon: <FileText className="w-6 h-6" />, title: '대량 텍스트', desc: '텍스트를 붙여넣으면 헤더·단락 기준으로 자동 분할해 등록합니다.', badge: 'AI 분석 지원' },
    { id: 'manual', icon: <PenLine className="w-6 h-6" />, title: '직접 입력', desc: '단건 지식을 직접 작성하여 등록합니다.' },
    { id: 'url', icon: <Globe className="w-6 h-6" />, title: 'URL / Confluence', desc: '웹 페이지 또는 Confluence 페이지 URL을 입력하면 내용을 자동 수집합니다.', badge: 'AI 분석 지원' },
  ];

  return (
    <div className="space-y-5">
      {/* 지원 기능 안내 */}
      <div className="rounded-xl border border-slate-700/60 bg-slate-800/40 px-4 py-3 space-y-2.5 text-xs text-slate-400">
        <div className="flex items-center gap-2">
          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-violet-50 text-violet-700 border border-violet-200 dark:bg-violet-900/40 dark:text-violet-300 dark:border-violet-700/40 shrink-0">AI 분석 지원</span>
          <span className="text-slate-500">배지 기능 상세</span>
        </div>
        <div className="space-y-1.5 pl-1">
          <div className="flex items-start gap-2">
            <span className="w-1.5 h-1.5 rounded-full bg-violet-400 shrink-0 mt-1" />
            <span><span className="text-slate-300">AI 청크 전략</span><span className="text-slate-600 mx-1.5">—</span>텍스트 구조 분석 후 최적 분할 방식 자동 결정<span className="ml-2 text-slate-600">파일 업로드 · 대량 텍스트 · URL/Confluence</span></span>
          </div>
          <div className="flex items-start gap-2">
            <span className="w-1.5 h-1.5 rounded-full bg-slate-500 shrink-0 mt-1" />
            <span><span className="text-slate-300">청크 미리보기</span><span className="text-slate-600 mx-1.5">—</span>등록 전 청크 단위 확인 · 선택 등록<span className="ml-2 text-slate-600">공통</span></span>
          </div>
        </div>
      </div>

      {/* 방법 선택 카드 */}
      <div className="grid grid-cols-2 gap-3">
        {methods.map((m) => (
          <button key={m.id}
            onClick={() => setActiveMethod(activeMethod === m.id ? null : m.id)}
            className={`text-left p-4 rounded-xl border transition-all ${
              activeMethod === m.id
                ? 'border-indigo-500 bg-indigo-50 text-indigo-700 dark:bg-indigo-950/40 dark:text-indigo-300'
                : 'border-slate-700 bg-slate-800/60 text-slate-300 hover:border-slate-500 hover:bg-slate-800'
            }`}
          >
            <div className="flex items-start gap-3">
              <div className={`mt-0.5 ${activeMethod === m.id ? 'text-indigo-600 dark:text-indigo-400' : 'text-slate-400'}`}>{m.icon}</div>
              <div>
                <div className="flex items-center gap-2">
                  <span className="font-medium text-sm">{m.title}</span>
                  {m.badge && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-violet-50 text-violet-700 border border-violet-200 dark:bg-violet-900/40 dark:text-violet-300 dark:border-violet-700/40">{m.badge}</span>
                  )}
                </div>
                <p className="text-xs text-slate-500 mt-0.5 leading-relaxed">{m.desc}</p>
              </div>
            </div>
          </button>
        ))}
      </div>

      {/* 선택된 방법의 인라인 폼 */}
      {activeMethod === 'file' && (
        <FileUploadForm namespace={namespace} preset={preset}
          onSuccess={(outcome) => { onSuccess(); handleFormOutcome(outcome); }} onCancel={() => setActiveMethod(null)} />
      )}
      {activeMethod === 'text' && (
        <TextSplitForm namespace={namespace} preset={preset}
          onSuccess={(outcome) => { onSuccess(); handleFormOutcome(outcome); }} onCancel={() => setActiveMethod(null)} />
      )}
      {activeMethod === 'manual' && (
        <ManualForm namespace={namespace} preset={preset}
          onSuccess={(outcome) => { onSuccess(); handleFormOutcome(outcome); }} onCancel={() => setActiveMethod(null)} />
      )}
      {activeMethod === 'url' && (
        <UrlForm namespace={namespace} preset={preset} onImportStarted={onImportStarted}
          onSuccess={(outcome) => { onSuccess(); handleFormOutcome(outcome); }} onCancel={() => setActiveMethod(null)} />
      )}


      {/* 인제스천 작업 이력 */}
      {jobs.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-sm font-medium text-slate-400">등록 이력</h3>
          <PaginationInfo totalItems={jobTotalItems} pageSize={jobPageSize} onPageSizeChange={setJobPageSize} />
          <div className="rounded-xl border border-slate-700 overflow-hidden divide-y divide-slate-700/60">
            {pagedJobs.map((j: IngestionJob) => {
              const isProcessing = j.status === 'processing';
              return (
                <div key={j.id}
                  className={`flex items-center gap-3 px-4 py-2.5 text-xs ${isProcessing ? 'cursor-pointer hover:bg-slate-800/60' : ''}`}
                  onClick={() => isProcessing && setProgressJobId(j.id)}
                >
                  <span className={`flex items-center gap-1 px-1.5 py-0.5 rounded font-medium flex-shrink-0 ${
                    j.status === 'completed' ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400' :
                    j.status === 'failed' ? 'bg-rose-100 text-rose-700 dark:bg-rose-900/30 dark:text-rose-400' :
                    j.status === 'cancelled' ? 'bg-slate-700 text-slate-400' :
                    'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400'
                  }`}>
                    {j.status === 'completed' ? <CheckCircle className="w-3 h-3" /> : j.status === 'failed' ? <AlertCircle className="w-3 h-3" /> : <Clock className="w-3 h-3" />}
                    {j.status}
                  </span>
                  <span className="text-slate-400 flex-shrink-0 text-[10px] px-1.5 py-0.5 bg-slate-700/60 rounded">
                    {j.source_type === 'csv_import' ? 'CSV' : j.source_type === 'file_upload' ? '파일' : j.source_type === 'paste_split' ? '텍스트' : j.source_type === 'web' ? '웹' : j.source_type === 'confluence' ? 'Confluence' : '수동'}
                  </span>
                  <span className="text-slate-300 truncate flex-1">{j.source_file || '-'}</span>
                  <span className="text-slate-500 flex-shrink-0">{j.created_chunks}/{j.total_chunks}건</span>
                  {isProcessing && j.total_chunks > 0 && (
                    <span className="text-indigo-400 flex-shrink-0 font-medium">
                      {Math.round((j.created_chunks / j.total_chunks) * 100)}%
                    </span>
                  )}
                  {j.auto_glossary > 0 && <span className="text-violet-400 flex-shrink-0">용어 +{j.auto_glossary}</span>}
                  {j.pending_chunks > 0 && (
                    <button
                      onClick={(e) => { e.stopPropagation(); onGoToReview(); }}
                      className="flex-shrink-0 text-[10px] font-medium bg-amber-50 text-amber-700 border border-amber-200 dark:bg-amber-900/40 dark:text-amber-400 dark:border-amber-700/40 px-1.5 py-0.5 rounded hover:bg-amber-900/60"
                      title="기존 지식과 유사해 승인 대기 상태로 등록됨"
                    >
                      승인 대기 {j.pending_chunks}건
                    </button>
                  )}
                  {j.created_by_username && <span className="text-slate-500 flex-shrink-0">{j.created_by_username}</span>}
                  <span className="text-slate-600 flex-shrink-0">{new Date(j.created_at).toLocaleDateString('ko-KR')}</span>
                </div>
              );
            })}
          </div>
          <PaginationNav page={jobPage} totalPages={jobTotalPages} onPageChange={setJobPage} />
        </div>
      )}

      <IngestionProgressModal
        jobId={progressJobId}
        onClose={() => {
          // 제안 묶음이 끝나기 전에 닫으면 결과 안내를 못 한다 — 그 작업은 넣은 곳 등록 이력에만 있다(재리뷰 N1)
          if (suggestTarget) void showAlert(`'${suggestTarget.label}'에 올리는 작업은 뒤에서 계속돼요. 결과는 '${suggestTarget.label}'의 등록 이력·승인 대기에서 확인할 수 있어요.`,
            { title: '진행 창을 닫았어요' });
          setProgressJobId(null); setAutoNavigate(false); setSuggestTarget(null);
        }}
        onSettled={(job) => {
          onSuccess();
          if (suggestTarget) {
            const t = suggestTarget;
            setSuggestTarget(null);
            if (job.status === 'completed') {
              void showAlert(job.pending_chunks > 0
                ? `${job.pending_chunks}건을 '${t.label}' 담당의 승인 대기로 보냈어요. 담당이 승인하면 검색에 나와요.`
                : `'${t.label}'에 바로 등록됐어요(담당 권한이 있어요).`, { title: job.pending_chunks > 0 ? '승인 요청을 보냈어요' : '등록 완료' });
            } else {
              void showAlert(`'${t.label}'에 올리지 못했어요(${job.status === 'cancelled' ? '중지됨' : '실패'}) — 등록 이력의 사유를 확인하고 다시 시도해 주세요.`,
                { title: '승인 요청 실패' });
            }
            return;
          }
          if (!autoNavigate) return;
          setAutoNavigate(false);
          if (job.pending_chunks > 0) {
            void showAlert(`유사한 기존 지식이 있어 ${job.pending_chunks}건이 승인 대기 상태로 등록됐습니다.\n승인 대기 탭으로 이동합니다.`, { title: '승인 대기로 등록' })
              .then(onGoToReview);
          } else if (job.status === 'completed') {
            onGoToList();
          }
        }}
      />
    </div>
  );
}


/** 기존(왼쪽) ↔ 신규(오른쪽) 좌우 차이 — 같은 줄은 흐리게, 고친 줄은 바뀐 낱말 강조, 한쪽에만 있는 줄은 빨강·초록 */
function ReviewDiff({ before, after, loading }: { before: string | null; after: string; loading: boolean }) {
  const lines = useMemo(() => (before == null ? null : diffLines(before, after)), [before, after]);
  // 고친 줄의 낱말 차이도 여기서 한 번에(창의 다른 상태가 바뀔 때마다 다시 계산하지 않게 — 리뷰 L7)
  const rows = useMemo(() => (lines ? splitRows(lines).map((r) => ({ r, w: r.kind === 'change' ? wordDiff(r.left!, r.right!) : null })) : null), [lines]);
  if (loading) return <div className="text-sm text-slate-500 py-4 text-center">불러오는 중...</div>;
  if (before == null) return <p className="text-sm text-slate-500 py-4 text-center">비교할 기존 지식이 없어요 — 중복 매칭 없이 대기에 온 항목이에요(예: 읽기 제한 보류).</p>;
  if (rows == null || lines == null) return <p className="text-sm text-slate-500 py-4 text-center">너무 길어 줄 비교를 못 해요 — [원문]으로 보세요.</p>;
  const add = lines.filter((l) => l.kind === 'add').length;
  const del = lines.filter((l) => l.kind === 'del').length;
  return (
    <div className="space-y-1.5" data-testid="review-diff">
      <p className="text-xs text-slate-400" data-testid="review-diff-summary">
        {sameText(before, after)
          ? <span className="font-semibold text-emerald-700 dark:text-emerald-300">내용 같음(공백·줄바꿈만 다름)</span>
          : <><span className="text-rose-700 dark:text-rose-300">−{del}줄 기존에만</span> · <span className="text-emerald-700 dark:text-emerald-300">+{add}줄 신규에만</span> · 같은 줄 {lines.length - add - del}
            {rows.some((x) => x.w?.gaveUp) && <span className="ml-1 text-slate-500" title="한 줄이 너무 길어 낱말 단위 비교를 건너뛰고 줄 전체를 바뀜으로 표시">· 긴 줄은 낱말 비교 생략</span>}</>}
      </p>
      <div className="h-[28rem] overflow-auto rounded-lg border border-slate-700 bg-slate-900 font-mono text-xs leading-5">
        <div className="sticky top-0 z-10 grid grid-cols-2 border-b border-slate-700 bg-slate-800 text-[11px] font-sans font-medium">
          <span className="px-2 py-1 text-indigo-600 dark:text-indigo-400">기존 지식</span>
          <span className="border-l border-slate-700 px-2 py-1 text-amber-700 dark:text-amber-400">신규 등록</span>
        </div>
        {rows.map(({ r, w }, i) => <SplitLine key={i} r={r} w={w} />)}
      </div>
    </div>
  );
}

function SplitLine({ r, w }: { r: SplitRow; w: ReturnType<typeof wordDiff> | null }) {
  const cell = (text: string | null, side: 'left' | 'right') => {
    const changed = side === 'left' ? r.kind === 'del' || r.kind === 'change' : r.kind === 'add' || r.kind === 'change';
    const tone = text == null ? 'bg-slate-800/60'
      : !changed ? 'text-slate-400'
      : side === 'left' ? 'bg-rose-50 text-rose-900 dark:bg-rose-500/10 dark:text-rose-200' : 'bg-emerald-50 text-emerald-900 dark:bg-emerald-500/10 dark:text-emerald-200';
    const mark = side === 'left' ? 'bg-rose-200 dark:bg-rose-500/40' : 'bg-emerald-200 dark:bg-emerald-500/40';
    return (
      <div className={`flex min-w-0 ${side === 'right' ? 'border-l border-slate-700' : ''} ${tone}`} data-side={side} data-kind={text == null ? 'empty' : changed ? r.kind : 'same'}>
        <span className="inline-block w-5 flex-shrink-0 select-none text-center opacity-70">{text == null ? '' : changed ? (side === 'left' ? '−' : '+') : ' '}</span>
        <span className="min-w-0 whitespace-pre-wrap break-all pr-2">
          {text == null ? ' ' : w ? (side === 'left' ? w.left : w.right).map(([t, c], k) => c ? <mark key={k} className={`rounded-sm text-inherit ${mark}`}>{t}</mark> : <span key={k}>{t}</span>) : (text || ' ')}
        </span>
      </div>
    );
  };
  return <div className="grid grid-cols-2">{cell(r.left, 'left')}{cell(r.right, 'right')}</div>;
}

// ── 서브탭 3: 승인 대기 (중복 의심 리뷰) ───────────────────────────────────────

function ReviewTab({ items, canModify, onResolved }: {
  items: KnowledgeItem[];
  canModify: boolean;
  onResolved: () => void;
}) {
  const [selectedItem, setSelectedItem] = useState<KnowledgeItem | null>(null);
  const [mergeTargetId, setMergeTargetId] = useState<number | null>(null);
  const [actionError, setActionError] = useState('');
  const [pendingAction, setPendingAction] = useState<'approve' | 'reject' | 'merge' | null>(null);
  // 차이(git diff처럼 줄 단위) / 나란히 — 기본은 차이(11/03 태훈 "diff를 가시적으로")
  const [view, setView] = useState<'diff' | 'side'>('diff');
  // 가장 비슷한 것부터(서버도 그 순서로 주지만 화면에서 한 번 더 — 값 없는 건 맨 뒤)
  const sortedItems = useMemo(() => [...items].sort((a, b) => (b.max_similarity ?? -1) - (a.max_similarity ?? -1)), [items]);

  const { data: matches = [], isLoading: matchesLoading } = useQuery({
    queryKey: ['knowledge-duplicate-matches', selectedItem?.id],
    queryFn: () => getDuplicateMatches(selectedItem!.id),
    enabled: selectedItem !== null,
  });
  // 유사도 높은 순 정렬 — 백엔드가 이미 정렬해 오지만 방어적으로 한번 더 보장
  const sortedMatches = [...matches].sort((a, b) => b.similarity - a.similarity);

  useEffect(() => {
    setMergeTargetId(null);
  }, [selectedItem?.id]);

  useEffect(() => {
    if (mergeTargetId === null && sortedMatches.length > 0) {
      setMergeTargetId(sortedMatches[0].id);
    }
  }, [sortedMatches, mergeTargetId]);

  const resolveMutation = useMutation({
    mutationFn: ({ action, targetId }: { action: 'approve' | 'reject' | 'merge'; targetId?: number }) => {
      setPendingAction(action);
      return resolveDuplicate(selectedItem!.id, action, targetId);
    },
    onSuccess: () => {
      setSelectedItem(null);
      setActionError('');
      setPendingAction(null);
      onResolved();
    },
    onError: (e: any) => { setActionError(e.message || '처리에 실패했습니다.'); setPendingAction(null); },
  });

  if (items.length === 0) {
    return (
      <div className="text-center py-16 text-slate-500">
        <CheckCircle className="w-8 h-8 mx-auto mb-2 text-slate-600" />
        승인 대기 중인 지식이 없습니다.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="rounded-xl border border-slate-700 divide-y divide-slate-700/60 overflow-hidden">
        {sortedItems.map((item) => (
          <button
            key={item.id}
            onClick={() => setSelectedItem(item)}
            data-testid="review-row"
            className="w-full text-left px-4 py-3 flex items-center gap-3 hover:bg-slate-800/60 transition-colors"
          >
            <span className="w-14 flex-shrink-0 text-right text-xs font-mono tabular-nums text-indigo-600 dark:text-indigo-400"
              title="가장 비슷한 기존 지식의 유사도 — 높은 순" data-testid="review-row-sim">
              {item.max_similarity != null ? `${(item.max_similarity * 100).toFixed(1)}%` : '—'}
            </span>
            <Badge color="amber">승인 대기</Badge>
            <span className="text-sm text-slate-300 truncate flex-1">{item.content}</span>
            {item.created_by_username && (
              <span className="text-[11px] text-slate-500 flex-shrink-0">{item.created_by_username}</span>
            )}
            <span className="text-[11px] text-slate-600 flex-shrink-0">
              {new Date(item.created_at).toLocaleDateString('ko-KR')}
            </span>
          </button>
        ))}
      </div>

      <Modal
        isOpen={selectedItem !== null}
        onClose={() => { setSelectedItem(null); setActionError(''); }}
        title="유사 지식 검토"
        maxWidth="max-w-5xl"
      >
        {selectedItem && (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <div role="group" aria-label="보기" className="flex gap-1">
                {([['diff', '차이'], ['side', '원문']] as const).map(([k, label]) => (
                  <button key={k} aria-pressed={view === k} onClick={() => setView(k)}
                    className={`rounded-full border px-3 py-1 text-xs ${view === k ? 'border-indigo-600 bg-indigo-600 text-white dark:border-indigo-500 dark:bg-indigo-500' : 'border-slate-700 text-slate-300 hover:bg-slate-800'}`}>
                    {label}
                  </button>
                ))}
              </div>
              {view === 'diff' && !matchesLoading && sortedMatches.length > 0 && (
                <div className="flex flex-wrap items-center gap-1.5" data-testid="review-diff-targets">
                  <span className="text-[11px] text-slate-500">비교 대상(병합 대상)</span>
                  {sortedMatches.map((m) => (
                    <button key={m.id} type="button" onClick={() => setMergeTargetId(m.id)} aria-pressed={mergeTargetId === m.id}
                      className={`rounded-md border px-2 py-0.5 text-xs font-mono ${mergeTargetId === m.id ? 'border-indigo-500 text-indigo-700 dark:text-indigo-300' : 'border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
                      #{m.id} · {(m.similarity * 100).toFixed(1)}%
                    </button>
                  ))}
                </div>
              )}
            </div>
            {view === 'diff' ? (
              <ReviewDiff before={sortedMatches.find((m) => m.id === mergeTargetId)?.content ?? null} after={selectedItem.content} loading={matchesLoading} />
            ) : (
            <div className="grid grid-cols-2 gap-3">
              <div>
                <p className="text-xs font-medium text-amber-400 mb-1.5">신규 등록</p>
                <div className="rounded-lg bg-amber-900/10 border border-amber-700/30 px-3 py-2.5 h-[28rem] overflow-y-auto">
                  <KnowledgeContentView content={selectedItem.content} />
                </div>
              </div>
              <div>
                <p className="text-xs font-medium text-indigo-400 mb-1.5">
                  기존 지식{sortedMatches.length > 0 ? ` (${sortedMatches.length}건, 유사도순 — 클릭해서 펼치기/병합 대상 선택)` : ''}
                </p>
                {matchesLoading ? (
                  <div className="text-sm text-slate-500 py-4 text-center">불러오는 중...</div>
                ) : (
                  <div className="space-y-1.5 h-[28rem] overflow-y-auto">
                    {sortedMatches.map((m: DuplicateMatch) => {
                      const isSelected = mergeTargetId === m.id;
                      return (
                        <button
                          key={m.id}
                          type="button"
                          onClick={() => setMergeTargetId(isSelected ? null : m.id)}
                          className={`w-full text-left px-3 py-2 rounded-lg border text-sm transition-colors ${
                            isSelected
                              ? 'border-indigo-500 bg-indigo-50 dark:bg-indigo-900/20 text-slate-200'
                              : 'border-slate-700 bg-slate-900/40 text-slate-400 hover:bg-slate-800/60'
                          }`}
                        >
                          <div className="flex items-center gap-2">
                            <span className="text-xs font-mono text-indigo-600 dark:text-indigo-400 flex-shrink-0">
                              {(m.similarity * 100).toFixed(1)}%
                            </span>
                            {isSelected ? (
                              <span className="flex-1" />
                            ) : (
                              <span className="line-clamp-2 flex-1">{m.content}</span>
                            )}
                            {isSelected
                              ? <ChevronUp className="w-3.5 h-3.5 text-slate-500 flex-shrink-0" />
                              : <ChevronDown className="w-3.5 h-3.5 text-slate-500 flex-shrink-0" />}
                          </div>
                          {isSelected && (
                            <div className="mt-2 pt-2 border-t border-slate-700/60">
                              <KnowledgeContentView content={m.content} />
                            </div>
                          )}
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
            </div>
            )}

            {actionError && <p className="text-xs text-rose-600 dark:text-rose-400">{actionError}</p>}

            {canModify ? (
              <div className="pt-2 border-t border-slate-700 flex gap-2 justify-end">
                <Button
                  variant="ghost" size="sm"
                  loading={resolveMutation.isPending && pendingAction === 'reject'}
                  onClick={() => resolveMutation.mutate({ action: 'reject' })}
                >
                  반려
                </Button>
                <Button
                  variant="secondary" size="sm"
                  loading={resolveMutation.isPending && pendingAction === 'approve'}
                  onClick={() => resolveMutation.mutate({ action: 'approve' })}
                >
                  그대로 저장
                </Button>
                <Button
                  variant="primary" size="sm"
                  disabled={mergeTargetId === null}
                  loading={resolveMutation.isPending && pendingAction === 'merge'}
                  onClick={() => resolveMutation.mutate({ action: 'merge', targetId: mergeTargetId! })}
                >
                  덮어쓰기
                </Button>
              </div>
            ) : (
              <p className="text-xs text-slate-500 pt-2 border-t border-slate-700">이 컬렉션의 승인 권한이 없습니다.</p>
            )}
          </div>
        )}
      </Modal>
    </div>
  );
}


// ── 파일 업로드 인라인 폼 ────────────────────────────────────────────────────

function FileUploadForm({ namespace, preset, onSuccess, onCancel }: {
  namespace: string;      // 미리보기(원문 초안)를 둘 곳 = 지금 보는 컬렉션. 실제로 넣는 곳은 청크 검토의 넣을 곳 카드(E2)
  preset?: string | null;
  onSuccess: (outcome?: IngestOutcome) => void; onCancel: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [detectedStrategy, setDetectedStrategy] = useState<string | null>(null);
  const [reviewChunks, setReviewChunks] = useState<ReviewChunk[]>([]);
  const [showReview, setShowReview] = useState(false);
  const [loading, setLoading] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [error, setError] = useState('');
  const [done, setDone] = useState('');
  const [isDragging, setIsDragging] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const ACCEPT_EXTS = ['.pdf', '.md', '.markdown', '.txt', '.log', '.text', '.xlsx', '.xlsm', '.csv'];
  const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;   // 50MB (nginx client_max_body_size와 동기화)

  const acceptFile = (f: File) => {
    const name = f.name.toLowerCase();
    if (!ACCEPT_EXTS.some(ext => name.endsWith(ext))) {
      setError(`지원하지 않는 형식입니다. 지원: ${ACCEPT_EXTS.join(', ')}`);
      return;
    }
    if (f.size > MAX_UPLOAD_BYTES) {
      const mb = (f.size / 1024 / 1024).toFixed(1);
      setError(`파일이 너무 큽니다 (${mb}MB > 50MB 한도). PDF는 분할하거나, 텍스트만 추출(.txt)해서 업로드해주세요.`);
      return;
    }
    setFile(f); setReviewChunks([]); setDetectedStrategy(null); setDone(''); setError('');
  };

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault(); e.stopPropagation();
    setIsDragging(false);
    const dropped = e.dataTransfer.files?.[0];
    if (dropped) acceptFile(dropped);
  };

  const handleDragOver = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault(); e.stopPropagation();
    if (e.dataTransfer.types.includes('Files')) setIsDragging(true);
  };

  const handleDragLeave = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault(); e.stopPropagation();
    setIsDragging(false);
  };

  const handleOpenReview = async () => {
    if (!file) return;
    setPreviewing(true); setError('');
    try {
      const result = await previewFileUpload(file, undefined, namespace);
      setDetectedStrategy(result.detected_strategy ?? null);
      setReviewChunks(result.chunks.map(c => ({ ...c, headingPath: c.heading_path ?? null, sourceDocumentId: result.source_document_id ?? null, selected: true })));
      setShowReview(true);
    } catch (e: any) {
      const msg = e.message || '파일 분석 실패';
      // 사용자 친화적 메시지로 변환
      if (msg.includes('413') || msg.toLowerCase().includes('payload too large') || msg.toLowerCase().includes('request entity too large')) {
        setError(`파일이 서버 한도(50MB)를 초과합니다. 파일을 분할하거나 텍스트만 추출(.txt)해서 업로드해주세요.`);
      } else if (msg.includes('암호화') || msg.toLowerCase().includes('encrypted') || msg.toLowerCase().includes('password')) {
        setError(`암호화(비밀번호 보호)된 PDF는 등록할 수 없습니다. Acrobat → 도구 → 보호 → 암호화 → '보안 제거' 후 다시 업로드해주세요.`);
      } else if (msg.toLowerCase().includes('not a zip') || msg.includes('.xlsx 형식이 아닙니다')) {
        setError(`이 파일은 올바른 .xlsx 형식이 아닙니다. 구버전 .xls 파일은 Excel에서 '다른 이름으로 저장' → .xlsx로 변환한 뒤 다시 업로드해주세요.`);
      } else {
        setError(msg);
      }
    }
    finally { setPreviewing(false); }
  };

  const handleConfirm = async (selected: ReviewChunk[], loc: ChosenLocation | null) => {
    setLoading(true); setError('');
    try {
      const partial = partialSourceDocs(selected, reviewChunks);
      const items = selected.map(c => ({ content: c.text, heading_path: c.headingPath ?? null,
        source_document_id: c.sourceDocumentId ?? null, source_document_partial: !!c.sourceDocumentId && partial.has(c.sourceDocumentId) }));
      const result = await bulkCreateConfirmed(namespace, items, file!.name, 'file_upload', loc);
      if (!result) return;
      setDone(doneText(items.length, loc));
      setShowReview(false);
      onSuccess({ jobId: result.job_id, target: loc });
    } catch (e: any) { setError(e.message || '오류 발생'); }
    finally { setLoading(false); }
  };

  const handleRestrategize = async (strategy: string) => {
    if (!file) return;
    const result = await previewFileUpload(file, strategy, namespace);
    setDetectedStrategy(result.detected_strategy ?? strategy);
    setReviewChunks(result.chunks.map(c => ({ ...c, headingPath: c.heading_path ?? null, sourceDocumentId: result.source_document_id ?? null, selected: true })));
  };

  return (
    <div className="bg-slate-800/60 rounded-xl border border-indigo-200 dark:border-indigo-800/40 p-5 space-y-4">
      <h3 className="text-sm font-semibold text-slate-200 flex items-center gap-2"><Upload className="w-4 h-4 text-indigo-600 dark:text-indigo-400" />파일 업로드</h3>

      <div
        className={`border-2 border-dashed rounded-xl p-6 text-center cursor-pointer transition-colors ${
          isDragging
            ? 'border-indigo-400 bg-indigo-50 dark:bg-indigo-500/10'
            : 'border-slate-600 hover:border-indigo-500'
        }`}
        onClick={() => fileRef.current?.click()}
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragEnter={handleDragOver}
        onDragLeave={handleDragLeave}
      >
        <input ref={fileRef} type="file" accept={ACCEPT_EXTS.join(',')} className="hidden"
          onChange={(e) => { const f = e.target.files?.[0]; if (f) acceptFile(f); }} />
        {file ? (
          <div>
            <p className="text-sm text-slate-300 font-medium">{file.name}</p>
            <p className="text-xs text-slate-500">{(file.size / 1024).toFixed(1)} KB · 클릭/드롭하여 변경</p>
          </div>
        ) : (
          <div>
            <Upload className={`w-8 h-8 mx-auto mb-2 ${isDragging ? 'text-indigo-600 dark:text-indigo-400' : 'text-slate-500'}`} />
            <p className={`text-sm ${isDragging ? 'text-indigo-700 dark:text-indigo-300 font-medium' : 'text-slate-400'}`}>
              {isDragging ? '여기에 파일을 놓아주세요' : '파일을 드래그하거나 클릭하여 선택'}
            </p>
            <p className="text-[10px] text-slate-600 mt-1">.pdf .md .txt .xlsx .xlsm .csv</p>
          </div>
        )}
      </div>

      <div className="flex gap-3 items-end flex-wrap">
        <div className="flex items-center gap-2 text-xs text-slate-500">
          <span>청킹 전략:</span>
          <span className="px-2 py-0.5 rounded bg-indigo-900/40 text-indigo-300 border border-indigo-700/30">
            {detectedStrategy ? `AI 자동 감지 — ${detectedStrategy}` : 'AI가 파일 분석 후 자동 결정'}
          </span>
        </div>
      </div>

      {done && <p className="text-sm text-emerald-600 dark:text-emerald-400 font-medium">{done}</p>}
      {error && <p className="text-xs text-rose-600 dark:text-rose-400">{error}</p>}

      <div className="flex gap-2 justify-end pt-1">
        <Button variant="ghost" size="sm" onClick={onCancel}>취소</Button>
        <Button variant="primary" size="sm" onClick={handleOpenReview} disabled={!file || previewing}>
          {previewing ? '분석 중...' : '청크 검토 & 등록'}
        </Button>
      </div>

      <ChunkReviewModal
        isOpen={showReview}
        onClose={() => setShowReview(false)}
        chunks={reviewChunks}
        onConfirm={handleConfirm}
        loading={loading}
        sourceName={file?.name ?? ''}
        currentStrategy={detectedStrategy} onRestrategize={handleRestrategize} preset={preset}
      />
    </div>
  );
}


// ── 대량 텍스트 인라인 폼 ────────────────────────────────────────────────────

function TextSplitForm({ namespace, preset, onSuccess, onCancel }: {
  namespace: string;      // 미리보기(원문 초안)를 둘 곳 = 지금 보는 컬렉션. 실제로 넣는 곳은 청크 검토의 넣을 곳 카드(E2)
  preset?: string | null;
  onSuccess: (outcome?: IngestOutcome) => void; onCancel: () => void;
}) {
  const [text, setText] = useState('');
  const [detectedStrategy, setDetectedStrategy] = useState<string | null>(null);
  const [reviewChunks, setReviewChunks] = useState<ReviewChunk[]>([]);
  const [showReview, setShowReview] = useState(false);
  const [loading, setLoading] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [error, setError] = useState('');
  const [done, setDone] = useState('');

  const handleOpenReview = async () => {
    if (!text.trim()) return;
    setPreviewing(true); setError('');
    try {
      const result = await previewTextSplit(text, 'auto', namespace);
      setDetectedStrategy(result.detected_strategy ?? null);
      setReviewChunks(result.chunks.map((c, i) => ({ idx: i, text: c, title: null, selected: true, headingPath: result.heading_paths?.[i] ?? null, sourceDocumentId: result.source_document_id ?? null })));
      setShowReview(true);
    } catch (e: any) { setError(e.message || '분할 미리보기 실패'); }
    finally { setPreviewing(false); }
  };

  const handleConfirm = async (selected: ReviewChunk[], loc: ChosenLocation | null) => {
    setLoading(true); setError('');
    try {
      const partial = partialSourceDocs(selected, reviewChunks);
      const items = selected.map(c => ({ content: c.text, heading_path: c.headingPath ?? null,
        source_document_id: c.sourceDocumentId ?? null, source_document_partial: !!c.sourceDocumentId && partial.has(c.sourceDocumentId) }));
      const result = await bulkCreateConfirmed(namespace, items, '텍스트 직접입력', 'paste_split', loc);
      if (!result) return;
      setDone(doneText(items.length, loc));
      setShowReview(false);
      onSuccess({ jobId: result.job_id, target: loc });
    } catch (e: any) { setError(e.message || '오류 발생'); }
    finally { setLoading(false); }
  };

  const handleRestrategize = async (strategy: string) => {
    const result = await previewTextSplit(text, strategy, namespace);
    setDetectedStrategy(result.detected_strategy ?? strategy);
    setReviewChunks(result.chunks.map((c, i) => ({ idx: i, text: c, title: null, selected: true, headingPath: result.heading_paths?.[i] ?? null, sourceDocumentId: result.source_document_id ?? null })));
  };

  return (
    <div className="bg-slate-800/60 rounded-xl border border-indigo-200 dark:border-indigo-800/40 p-5 space-y-4">
      <h3 className="text-sm font-semibold text-slate-200 flex items-center gap-2"><FileText className="w-4 h-4 text-indigo-600 dark:text-indigo-400" />대량 텍스트 등록</h3>

      <textarea rows={10} value={text} onChange={(e) => { setText(e.target.value); setDetectedStrategy(null); setDone(''); }}
        placeholder={"여기에 긴 텍스트를 붙여넣으세요...\n\nAI가 내용을 분석하여 최적의 분할 방식을 자동으로 결정합니다."}
        className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 resize-y min-h-[200px]" />

      <div className="flex gap-3 items-end flex-wrap">
        <div className="flex items-center gap-2 text-xs text-slate-500">
          <span>분할 전략:</span>
          <span className="px-2 py-0.5 rounded bg-indigo-900/40 text-indigo-300 border border-indigo-700/30">
            {detectedStrategy ? `AI 자동 감지 — ${detectedStrategy}` : 'AI가 텍스트 분석 후 자동 결정'}
          </span>
        </div>
      </div>

      {done && <p className="text-sm text-emerald-600 dark:text-emerald-400 font-medium">{done}</p>}
      {error && <p className="text-xs text-rose-600 dark:text-rose-400">{error}</p>}

      <div className="flex gap-2 justify-end pt-1">
        <Button variant="ghost" size="sm" onClick={onCancel}>취소</Button>
        <Button variant="primary" size="sm" onClick={handleOpenReview} disabled={!text.trim() || previewing}>
          {previewing ? '분석 중...' : '청크 검토 & 등록'}
        </Button>
      </div>

      <ChunkReviewModal
        isOpen={showReview}
        onClose={() => setShowReview(false)}
        chunks={reviewChunks}
        onConfirm={handleConfirm}
        loading={loading}
        sourceName="텍스트 직접입력"
        currentStrategy={detectedStrategy} onRestrategize={handleRestrategize} preset={preset}
      />
    </div>
  );
}


// ── 직접 입력 인라인 폼 ──────────────────────────────────────────────────────

function ManualForm({ namespace, preset, onSuccess, onCancel }: {
  namespace: string;
  preset?: string | null;
  onSuccess: (outcome?: IngestOutcome) => void; onCancel: () => void;
}) {
  const qc = useQueryClient();
  const [form, setForm] = useState<KnowledgeFormData>(defaultForm);
  const [done, setDone] = useState('');
  const [donePending, setDonePending] = useState(false);
  // 두 단계(E2): 내용을 쓰고 [넣을 곳 확인] → 카드에서 고르고 [등록]. 내용을 고치면 다시 1단계(판정은 앞부분이 같으면 다시 안 부름)
  const [locating, setLocating] = useState(false);
  const [loc, setLoc] = useState<ChosenLocation | null>(null);
  usePrefetchLocate(form.content, preset, !locating);   // 쓰다 멈추면 넣을 곳 판단을 미리(10/28 — 확인 누르면 바로)

  const createMutation = useMutation({
    mutationFn: () =>
      createKnowledge({
        namespace: loc?.namespace ?? namespace,
        content: form.content,
        decision_id: loc?.decisionId ?? null,
        location_choice: loc?.choice ?? null,
        override_reason: loc?.overrideReason ?? null,
      }),
    onSuccess: (created) => {
      const target = loc?.namespace ?? namespace;
      qc.invalidateQueries({ queryKey: ['knowledge', target] });
      qc.invalidateQueries({ queryKey: ['stats-ns', target] });
      setDone(
        loc && !loc.writable
          ? `'${loc.label}' 담당의 승인 대기로 보냈어요 — 승인되면 검색에 나와요.`
          : created.pending_review
            ? '유사한 기존 지식이 있어 승인 대기 상태로 등록됐습니다 — "승인 대기" 탭에서 확인하세요.'
            : `등록 완료${loc ? ` — '${loc.label}'` : ''}`
      );
      setDonePending(!!created.pending_review);
      setForm(defaultForm);
      setLocating(false);
      // 쓴·버린 판단은 이제 원장에서 확정·삭제됐다 — 캐시에 남기면 같은 내용을 다시 붙여넣을 때 지워진 판단을 다시 쓴다(10/28 리뷰 M1)
      qc.removeQueries({ queryKey: ['locate'] });
      onSuccess({ pendingReview: !!created.pending_review, target: loc });
    },
    // 실패(특히 403 — 판단이 지워졌거나 오래됨)면 판단을 버리고 다시 받게 — 안 그러면 내용을 바꿀 때까지 같은 판단으로 계속 막힌다(M1·L3)
    onError: () => { qc.removeQueries({ queryKey: ['locate'] }); setLocating(false); },
  });

  return (
    <div className="bg-slate-800/60 rounded-xl border border-indigo-200 dark:border-indigo-800/40 p-5 space-y-3">
      <h3 className="text-sm font-semibold text-slate-200 flex items-center gap-2"><PenLine className="w-4 h-4 text-indigo-600 dark:text-indigo-400" />직접 입력</h3>

      <div>
        <label className="block text-xs font-medium text-slate-400 mb-1">내용 <span className="text-rose-400">*</span></label>
        <textarea rows={8} value={form.content} onChange={(e) => { setForm((f) => ({ ...f, content: e.target.value })); setLocating(false); }}
          className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 resize-y min-h-[160px]" />
      </div>

      {locating && <LocationPicker text={form.content} preset={preset} onChange={setLoc} />}

      {done && <p className={`text-sm font-medium ${donePending ? 'text-amber-600 dark:text-amber-400' : 'text-emerald-600 dark:text-emerald-400'}`}>{done}</p>}
      {createMutation.isError && <p className="text-xs text-rose-600 dark:text-rose-400">{String(createMutation.error)}</p>}

      <div className="flex gap-2 justify-end pt-1">
        <Button variant="ghost" size="sm" onClick={onCancel}>취소</Button>
        {!locating ? (
          <Button variant="primary" size="sm" onClick={() => { setDone(''); setLocating(true); }} disabled={!form.content.trim()}>
            넣을 곳 확인
          </Button>
        ) : (
          <Button variant="primary" size="sm"
            loading={createMutation.isPending}
            onClick={() => createMutation.mutate()}
            disabled={createMutation.isPending || !form.content.trim() || !loc}>
            {loc && !loc.writable ? '승인 요청' : '등록'}
          </Button>
        )}
      </div>
    </div>
  );
}


// ── URL / Confluence 인라인 폼 ────────────────────────────────────────────────

function UrlForm({ namespace, preset, onSuccess, onCancel, onImportStarted }: {
  namespace: string;      // 미리보기(원문 초안)를 둘 곳 = 지금 보는 컬렉션. 실제로 넣는 곳은 청크 검토의 넣을 곳 카드(E2)
  preset?: string | null;
  onSuccess: (outcome?: IngestOutcome) => void; onCancel: () => void;
  onImportStarted: (batchId: number) => void;   // 공간·하위 전체 넣기 시작 → [적재 검토] 탭에서 연다
}) {
  const { user, updateUser } = useAuthStore();
  const [url, setUrl] = useState('');
  const [reviewChunks, setReviewChunks] = useState<ReviewChunk[]>([]);
  const [sourceMeta, setSourceMeta] = useState<{ name: string; type: string } | null>(null);
  const [unstructuredPages, setUnstructuredPages] = useState<string[]>([]);
  const [showReview, setShowReview] = useState(false);
  const [loading, setLoading] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [error, setError] = useState('');
  const [done, setDone] = useState('');

  // 트리 선택 (Confluence 하위 페이지)
  // 컨플 넣는 방식 — 이 페이지만 / 하위 골라 넣기(트리 체크, 한 곳) / 공간·하위 전체(분류 미리보기 → 페이지마다 분류, 배치 롤백)
  const [confMode, setConfMode] = useState<'page' | 'pick' | 'space'>('page');
  const includeChildren = confMode === 'pick';
  const [showTreeModal, setShowTreeModal] = useState(false);
  const [tree, setTree] = useState<ConfluenceTreeResponse | null>(null);
  const [selectedPageIds, setSelectedPageIds] = useState<Set<string>>(new Set());
  const [treeLoading, setTreeLoading] = useState(false);

  // PAT 등록 모달
  const [showPatModal, setShowPatModal] = useState(false);
  const [patInput, setPatInput] = useState('');
  const [patLoading, setPatLoading] = useState(false);
  const [patError, setPatError] = useState('');

  const isConfluence = url.includes('confluence') || url.includes('confl.');
  const hasPat = !!user?.has_confluence_pat;

  const handleOpenReview = async () => {
    if (!url.trim()) return;
    if (isConfluence && !hasPat) { setShowPatModal(true); return; }

    // Confluence + 하위 페이지 포함 → 트리 선택 모달
    if (isConfluence && includeChildren) {
      setTreeLoading(true); setError('');
      try {
        const result = await previewConfluenceTree(url.trim(), { maxDepth: 3, maxPages: 100 });
        setTree(result);
        // 기본 선택: 모두 체크
        setSelectedPageIds(new Set(result.tree.map(n => n.page_id)));
        setShowTreeModal(true);
      } catch (e: any) { setError(e.message || '트리 조회 실패'); }
      finally { setTreeLoading(false); }
      return;
    }

    setPreviewing(true); setError('');
    try {
      const result = await previewUrl(namespace, url.trim());
      setSourceMeta({ name: result.source_name, type: result.source_type });
      setUnstructuredPages(result.structure && !result.structure.structured ? [result.source_name] : []);
      setReviewChunks(result.chunks.map(c => ({
        ...c, selected: true, headingPath: c.heading_path ?? null, sourceDocumentId: result.source_document_id ?? null,
        confluencePageId: result.confluence_page_id, confluenceVersion: result.confluence_version,
      })));
      setShowReview(true);
    } catch (e: any) { setError(e.message || '수집 실패'); }
    finally { setPreviewing(false); }
  };

  // 트리 선택 → 청크 미리보기 단계
  const handleProceedToReview = async () => {
    if (!tree || selectedPageIds.size === 0) return;
    setLoading(true); setError('');
    try {
      const parsed = new URL(url.trim());
      const baseUrl = `${parsed.protocol}//${parsed.host}`;
      const pages = tree.tree
        .filter(n => selectedPageIds.has(n.page_id))
        .map(n => ({ page_id: n.page_id, title: n.title, url: n.url }));
      const result = await previewConfluenceBulk(namespace, baseUrl, pages, { chunkStrategy: 'auto' });

      if (result.chunks.length === 0) {
        const r = result.failed_pages.filter(p => p.restricted);
        const f = result.failed_pages.filter(p => !p.restricted);
        const parts = [
          f.length > 0 ? `실패: ${f.map(p => p.title).join(', ')}` : '',
          r.length > 0 ? `읽기 제한이라 건너뜀(기록만): ${r.map(p => p.title).join(', ')}` : '',
        ].filter(Boolean);
        setError(`청크가 생성되지 않았습니다.${parts.length ? ` (${parts.join(' · ')})` : ''}`);
        return;
      }

      // ChunkReviewModal로 전환 (페이지 제목을 청크 타이틀에 prefix)
      setSourceMeta({
        name: `Confluence ${pages.length}개 페이지`,
        type: 'confluence_bulk',
      });
      setUnstructuredPages(result.pages.filter(p => !p.structured).map(p => p.title));
      setReviewChunks(result.chunks.map(c => ({
        idx: c.idx,
        text: c.text,
        title: c.title ? `${c.page_title} — ${c.title}` : c.page_title,
        selected: true,
        headingPath: c.heading_path,
        sourceDocumentId: c.source_document_id ?? null,
        confluencePageId: c.confluence_page_id,
        confluenceVersion: c.confluence_version,
      })));
      setShowTreeModal(false);
      setShowReview(true);

      // 읽기 제한 페이지는 실패가 아니라 일부러 건너뜀(기록만, 10/29) — 같은 문구로 섞으면 다시 시도할 일로 보인다
      const restricted = result.failed_pages.filter(p => p.restricted);
      const failed = result.failed_pages.filter(p => !p.restricted);
      const msgs = [
        failed.length > 0 ? `일부 페이지를 못 가져왔어요: ${failed.map(p => p.title).join(', ')}` : '',
        restricted.length > 0 ? `읽기 제한이라 건너뜀(기록만): ${restricted.map(p => p.title).join(', ')}` : '',
      ].filter(Boolean);
      if (msgs.length > 0) setError(msgs.join(' · '));
    } catch (e: any) { setError(e.message || '청크 미리보기 실패'); }
    finally { setLoading(false); }
  };

  const togglePage = (pageId: string) => {
    setSelectedPageIds(prev => {
      const next = new Set(prev);
      if (next.has(pageId)) next.delete(pageId); else next.add(pageId);
      return next;
    });
  };

  const toggleAllSelection = (select: boolean) => {
    if (!tree) return;
    setSelectedPageIds(select ? new Set(tree.tree.map(n => n.page_id)) : new Set());
  };

  const handleConfirm = async (selected: ReviewChunk[], loc: ChosenLocation | null) => {
    setLoading(true); setError('');
    try {
      // confluence_bulk는 조상 헤딩(c.headingPath)을 그대로 쓴다(2026-09-22). heading_path는 이전엔 이 확정 경로 자체에
      // 필드가 없어서 v2.98 이후로도 계속 NULL로 저장되고 있던 것까지 같이 고침. 업무구분은 10/22 폐지.
      // 컨플루언스 원본 식별자(2026-09-28)는 단일 URL/트리 일괄 둘 다 실어 보낸다 — 없으면 null
      const partialUrl = partialSourceDocs(selected, reviewChunks);
      const items = selected.map(c => ({
        ...(sourceMeta?.type === 'confluence_bulk'
          ? { content: c.text, heading_path: c.headingPath }
          : { content: c.text, heading_path: c.headingPath ?? null }),
        confluence_page_id: c.confluencePageId ?? null,
        confluence_version: c.confluenceVersion ?? null,
        source_document_id: c.sourceDocumentId ?? null,
        source_document_partial: !!c.sourceDocumentId && partialUrl.has(c.sourceDocumentId),
      }));
      const srcName = sourceMeta?.name ?? url;
      const srcType = sourceMeta?.type ?? (isConfluence ? 'confluence' : 'web');
      const result = await bulkCreateConfirmed(namespace, items, srcName, srcType, loc);
      if (!result) return;
      setDone(`"${srcName}" — ${doneText(items.length, loc)}`);
      setShowReview(false);
      onSuccess({ jobId: result.job_id, target: loc });
    } catch (e: any) { setError(e.message || '오류 발생'); }
    finally { setLoading(false); }
  };

  const handleSavePat = async () => {
    if (!patInput.trim()) return;
    setPatLoading(true); setPatError('');
    try {
      await updateConfluencePAT(patInput.trim());
      if (user) updateUser({ ...user, has_confluence_pat: true });
      setShowPatModal(false); setPatInput('');
    } catch (e: any) { setPatError(e.message || 'PAT 저장 실패'); }
    finally { setPatLoading(false); }
  };

  const handleDeletePat = async () => {
    try {
      await deleteConfluencePAT();
      if (user) updateUser({ ...user, has_confluence_pat: false });
    } catch { /* ignore */ }
  };

  return (
    <div className="bg-slate-800/60 rounded-xl border border-indigo-200 dark:border-indigo-800/40 p-5 space-y-4">
      <h3 className="text-sm font-semibold text-slate-200 flex items-center gap-2">
        <Globe className="w-4 h-4 text-indigo-600 dark:text-indigo-400" />URL / Confluence 수집
      </h3>

      <div>
        <label className="block text-xs font-medium text-slate-400 mb-1">URL <span className="text-rose-400">*</span></label>
        <input
          type="url"
          value={url}
          onChange={(e) => { setUrl(e.target.value); setSourceMeta(null); setDone(''); }}
          placeholder="https://confl.sinc.co.kr/display/SPACE/페이지제목"
          className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 placeholder:text-slate-600"
        />
        <p className="text-[11px] text-slate-500 mt-1">
          Confluence: display/SPACE/제목 또는 pages/viewpage.action?pageId=... 형식
        </p>
      </div>

      {isConfluence && (
        <div className="flex items-center gap-3 p-3 bg-slate-900/60 rounded-lg border border-slate-700">
          {hasPat ? (
            <>
              <CheckCircle className="w-4 h-4 text-emerald-400 shrink-0" />
              <span className="text-xs text-slate-300 flex-1">Confluence PAT 등록됨</span>
              <button onClick={() => { setPatInput(''); setShowPatModal(true); }}
                className="text-xs text-indigo-400 hover:text-indigo-300">변경</button>
              <button onClick={handleDeletePat}
                className="text-xs text-rose-600 hover:text-rose-500 dark:text-rose-400 dark:hover:text-rose-300">삭제</button>
            </>
          ) : (
            <>
              <AlertCircle className="w-4 h-4 text-amber-400 shrink-0" />
              <span className="text-xs text-slate-400 flex-1">Confluence PAT 미등록 — 수집 시 등록 필요</span>
              <button onClick={() => setShowPatModal(true)}
                className="text-xs text-indigo-400 hover:text-indigo-300">등록</button>
            </>
          )}
        </div>
      )}

      {isConfluence && hasPat && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-2" role="radiogroup" aria-label="컨플 넣는 방식">
          {([
            ['page', '이 페이지만', '입력한 페이지 하나 — 청크 검토 후 등록'],
            ['pick', '하위 골라 넣기', '트리에서 체크(최대 200) — 한 곳에 등록'],
            ['space', '공간·하위 전체 넣기', '페이지마다 분류 미리보기 → 표본 → 적재 · 배치 롤백'],
          ] as const).map(([v, title, desc]) => (
            <button key={v} role="radio" aria-checked={confMode === v} onClick={() => setConfMode(v)}
              className={`text-left p-2.5 rounded-lg border ${confMode === v ? 'border-indigo-500 bg-indigo-50 dark:bg-indigo-950/40' : 'border-slate-700 bg-slate-900/60 hover:border-slate-500'}`}>
              <div className="text-xs font-semibold text-slate-200">{title}</div>
              <div className="text-[11px] text-slate-500">{desc}</div>
            </button>
          ))}
        </div>
      )}

      {done && <p className="text-sm text-emerald-600 dark:text-emerald-400 font-medium">{done}</p>}
      {error && <p className="text-xs text-rose-600 dark:text-rose-400">{error}</p>}

      {isConfluence && hasPat && confMode === 'space' ? (
        <SpaceImportStart url={url} onStarted={onImportStarted} onCancel={onCancel} />
      ) : (
        <div className="flex gap-2 justify-end pt-1">
          <Button variant="ghost" size="sm" onClick={onCancel}>취소</Button>
          <Button variant="primary" size="sm" onClick={handleOpenReview}
            disabled={!url.trim() || previewing || treeLoading}>
            {treeLoading ? '트리 조회 중...' : previewing ? '수집 중...' : (isConfluence && includeChildren ? '하위 페이지 선택' : '수집 & 청크 검토')}
          </Button>
        </div>
      )}

      <ChunkReviewModal
        isOpen={showReview}
        onClose={() => setShowReview(false)}
        chunks={reviewChunks}
        onConfirm={handleConfirm}
        loading={loading}
        sourceName={sourceMeta?.name ?? url}
        unstructuredPages={unstructuredPages}
        preset={preset}
      />

      {/* Confluence 하위 페이지 트리 선택 모달 */}
      {showTreeModal && tree && (
        <Modal isOpen onClose={() => setShowTreeModal(false)} title="Confluence 하위 페이지 선택">
          <div className="space-y-3">
            <div className="flex items-center justify-between text-xs">
              <div className="text-slate-400">
                총 <span className="text-slate-200 font-semibold">{tree.tree.length}개</span> 페이지 (최대 깊이 {tree.max_depth})
                {tree.truncated && <span className="ml-2 text-amber-400">⚠ {tree.max_pages}개 한도 초과 — 일부 누락</span>}
                {tree.max_depth_reached && !tree.truncated && <span className="ml-2 text-amber-400">⚠ 깊이 한도 도달</span>}
              </div>
              <div className="flex gap-2">
                <button onClick={() => toggleAllSelection(true)} className="text-indigo-400 hover:text-indigo-300">전체 선택</button>
                <span className="text-slate-600">/</span>
                <button onClick={() => toggleAllSelection(false)} className="text-slate-400 hover:text-slate-300">전체 해제</button>
              </div>
            </div>

            <div className="max-h-96 overflow-y-auto bg-slate-900/60 rounded-lg border border-slate-700 divide-y divide-slate-800">
              {tree.tree.map(node => (
                <label
                  key={node.page_id}
                  className="flex items-start gap-2 px-3 py-2 hover:bg-slate-800/40 cursor-pointer"
                  style={{ paddingLeft: `${12 + node.depth * 20}px` }}
                >
                  <input
                    type="checkbox"
                    checked={selectedPageIds.has(node.page_id)}
                    onChange={() => togglePage(node.page_id)}
                    className="mt-1 w-3.5 h-3.5 accent-indigo-500 shrink-0"
                  />
                  <div className="flex-1 min-w-0">
                    <div className="text-xs text-slate-200 truncate">
                      {node.depth === 0 && <span className="text-amber-400 mr-1">📌</span>}
                      {node.title}
                    </div>
                    <div className="text-[10px] text-slate-500 font-mono truncate">{node.url}</div>
                  </div>
                </label>
              ))}
            </div>

            <div className="flex items-center justify-between pt-1">
              <div className="text-xs text-slate-400">
                선택: <span className="text-indigo-400 font-semibold">{selectedPageIds.size}</span>개 페이지
              </div>
              <div className="flex gap-2">
                <Button variant="ghost" size="sm" onClick={() => setShowTreeModal(false)}>취소</Button>
                <Button
                  variant="primary"
                  size="sm"
                  onClick={handleProceedToReview}
                  disabled={selectedPageIds.size === 0 || loading}
                >
                  {loading ? '청크 분석 중...' : `${selectedPageIds.size}개 페이지 · 청크 검토`}
                </Button>
              </div>
            </div>
          </div>
        </Modal>
      )}

      {/* Confluence PAT 등록 모달 */}
      {showPatModal && (
        <Modal isOpen onClose={() => setShowPatModal(false)} title="Confluence PAT 등록">
          <div className="space-y-4">
            <p className="text-xs text-slate-400">
              Confluence 계정 → 프로필 → Personal Access Token 에서 발급하세요.
              PAT는 암호화하여 개인 계정에 저장됩니다.
            </p>
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">
                Personal Access Token <span className="text-rose-400">*</span>
              </label>
              <input
                type="password"
                value={patInput}
                onChange={(e) => setPatInput(e.target.value)}
                placeholder="PAT를 입력하세요"
                className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
                autoFocus
              />
            </div>
            {patError && <p className="text-xs text-rose-600 dark:text-rose-400">{patError}</p>}
            <div className="flex gap-2 justify-end">
              <Button variant="ghost" size="sm" onClick={() => setShowPatModal(false)}>취소</Button>
              <Button variant="primary" size="sm" onClick={handleSavePat}
                disabled={!patInput.trim() || patLoading}>
                {patLoading ? '저장 중...' : '저장'}
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

