import { useState, useRef, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Trash2, X, FileText, Upload, Database, List, PenLine, CheckCircle, Clock, AlertCircle, Globe, ChevronDown, ChevronUp, MessageSquare, RefreshCw, LogOut, Download, Check, Search } from 'lucide-react';
import {
  getKnowledge,
  createKnowledge,
  updateKnowledge,
  deleteKnowledge,
  bulkDeleteKnowledge,
  bulkUpdateKnowledge,
  vectorSearchKnowledge,
  bulkCreateKnowledge,
  previewTextSplit,
  previewFileUpload,
  previewUrl,
  previewConfluenceTree,
  importConfluenceBulk,
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
import { getCategories } from '../../api/namespaces';
import { updateConfluencePAT, deleteConfluencePAT } from '../../api/auth';
import {
  getTeamsAuthStatus,
  logoutTeams,
  listTeamsChats,
  fetchTeamsMessages,
  importTeamsThreads,
  downloadHelperExe,
  type TeamsChat,
  type TeamsMessage,
  type TeamsAuthStatus,
} from '../../api/teams';
import { useNamespaceAccess } from '../../utils/useNamespaceAccess';
import { useAuthStore } from '../../store/useAuthStore';
import { Button } from '../ui/Button';
import { Modal } from '../ui/Modal';
import { Badge } from '../ui/Badge';
import { TagInput } from '../ui/TagInput';
import { PaginationInfo, PaginationNav, useClientPaging } from '../ui/Pagination';
import type { KnowledgeItem, DuplicateMatch } from '../../types';

// ── 공통 타입 ─────────────────────────────────────────────────────────────────

interface KnowledgeFormData {
  container_names: string[];
  target_tables: string[];
  content: string;
  query_template: string;
  base_weight: number;
  category: string;
}

const defaultForm: KnowledgeFormData = {
  container_names: [],
  target_tables: [],
  content: '',
  query_template: '',
  base_weight: 1.0,
  category: '',
};

function weightLabel(w: number) {
  return w >= 2 ? '높음' : w >= 1.5 ? '보통' : '기본';
}
function weightClass(w: number) {
  return w >= 2
    ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-300'
    : w >= 1.5
    ? 'bg-indigo-100 text-indigo-700 dark:bg-indigo-500/20 dark:text-indigo-300'
    : 'bg-zinc-100 text-zinc-600 dark:bg-zinc-600/40 dark:text-zinc-300';
}

// 업무구분은 필수값 — 카테고리가 없는 네임스페이스는 "네임스페이스 관리"에서 먼저 추가해야 함
function RequiredCategoryField({ categoryNames, value, onChange }: {
  categoryNames: string[]; value: string; onChange: (v: string) => void;
}) {
  if (categoryNames.length === 0) {
    return (
      <div className="rounded-lg bg-amber-900/20 border border-amber-700/40 px-3 py-2 text-xs text-amber-300">
        이 네임스페이스에 업무구분이 없습니다. "네임스페이스 관리"에서 먼저 업무구분을 추가해주세요.
      </div>
    );
  }
  return (
    <div>
      <label className="block text-xs font-medium text-slate-400 mb-1">업무구분 <span className="text-rose-400">*</span></label>
      <select value={value} onChange={(e) => onChange(e.target.value)}
        className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500">
        <option value="" disabled>선택하세요</option>
        {categoryNames.map((c) => <option key={c} value={c}>{c}</option>)}
      </select>
    </div>
  );
}

type IngestMethod = 'file' | 'text' | 'manual' | 'url' | 'teams' | null;

// ── KnowledgeTable (메인) ─────────────────────────────────────────────────────

export function KnowledgeTable() {
  const qc = useQueryClient();
  const { selectedNs, setSelectedNs, canModifyNs, sortedNamespaces } = useNamespaceAccess();

  const [subTab, setSubTab] = useState<'list' | 'ingest' | 'review'>('list');

  // 조회 탭 state
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editForm, setEditForm] = useState<KnowledgeFormData>(defaultForm);
  const [showEdit, setShowEdit] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<number | null>(null);
  const [categoryFilter, setCategoryFilter] = useState('');
  const [sourceFilter, setSourceFilter] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [searchMode, setSearchMode] = useState<'text' | 'vector'>('text');
  const [vectorSearchInput, setVectorSearchInput] = useState('');
  const [vectorQuery, setVectorQuery] = useState('');
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [showBulkConfirm, setShowBulkConfirm] = useState(false);
  const [showBulkEdit, setShowBulkEdit] = useState(false);
  const [bulkEditCategory, setBulkEditCategory] = useState('');
  const [bulkEditSourceType, setBulkEditSourceType] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(30);

  const { data: categories = [] } = useQuery({
    queryKey: ['categories', selectedNs],
    queryFn: () => getCategories(selectedNs),
    enabled: !!selectedNs,
    staleTime: 30_000,
  });
  const categoryNames = categories.map((c) => c.name);

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
        container_name: editForm.container_names.join(', '),
        target_tables: editForm.target_tables,
        content: editForm.content,
        query_template: editForm.query_template || null,
        base_weight: editForm.base_weight,
        category: editForm.category,
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
      ...(bulkEditCategory ? { category: bulkEditCategory } : {}),
      ...(bulkEditSourceType ? { source_type: bulkEditSourceType } : {}),
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['knowledge', selectedNs] });
      qc.invalidateQueries({ queryKey: ['stats-ns', selectedNs] });
      setSelectedIds(new Set());
      setShowBulkEdit(false);
      setBulkEditCategory('');
      setBulkEditSourceType('');
    },
  });

  const startEdit = (item: KnowledgeItem) => {
    setEditingId(item.id);
    setEditForm({
      container_names: item.container_name
        ? item.container_name.split(',').map((t) => t.trim()).filter(Boolean)
        : [],
      target_tables: item.target_tables ?? [],
      content: item.content,
      query_template: item.query_template ?? '',
      base_weight: item.base_weight,
      category: item.category ?? '',
    });
    setShowEdit(true);
  };

  const textFilteredItems = items.filter((item) => {
    if (categoryFilter && item.category !== categoryFilter) return false;
    if (sourceFilter) {
      const st = (item as any).source_type || 'manual';
      if (sourceFilter !== st) return false;
    }
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      const content = item.content.toLowerCase();
      const src = ((item as any).source_file ?? '').toLowerCase();
      if (!content.includes(q) && !src.includes(q)) return false;
    }
    return true;
  });
  const filteredItems = textFilteredItems; // for count ref
  const displayItems = searchMode === 'vector' && vectorQuery
    ? (vectorSearchQuery.data ?? [])
    : textFilteredItems;
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
          지식 베이스
          {selectedNs && <span className="text-sm font-normal text-slate-500 ml-2">({selectedNs})</span>}
        </h2>
        <select
          value={selectedNs}
          onChange={(e) => { setSelectedNs(e.target.value); setCategoryFilter(''); setPage(1); }}
          className="w-44 bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
        >
          <option value="">파트 선택...</option>
          {sortedNamespaces.map((ns) => <option key={ns} value={ns}>{ns}</option>)}
        </select>
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
          {items.length > 0 && (
            <span className="ml-1 text-[10px] bg-slate-700 text-slate-400 px-1.5 py-0.5 rounded-full">{items.length}</span>
          )}
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
          {jobs.length > 0 && (
            <span className="ml-1 text-[10px] bg-indigo-900/60 text-indigo-400 px-1.5 py-0.5 rounded-full">{jobs.length}</span>
          )}
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
            <span className="ml-1 text-[10px] bg-amber-900/60 text-amber-400 px-1.5 py-0.5 rounded-full">{pendingItems.length}</span>
          )}
        </button>
      </div>

      {/* ── 서브탭 1: 조회 ── */}
      {subTab === 'list' && (
        <div className="space-y-3">
          {/* 필터 바 */}
          <div className="flex items-end gap-3 flex-wrap">
            {categoryNames.length > 0 && (
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1.5">업무구분</label>
                <select value={categoryFilter} onChange={(e) => { setCategoryFilter(e.target.value); setPage(1); setSelectedIds(new Set()); }}
                  className="w-36 bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500">
                  <option value="">전체</option>
                  {categoryNames.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              </div>
            )}
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1.5">소스</label>
              <select value={sourceFilter} onChange={(e) => { setSourceFilter(e.target.value); setPage(1); setSelectedIds(new Set()); }}
                className="w-36 bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500">
                <option value="">전체</option>
                <option value="manual">수동 등록</option>
                <option value="csv_import">CSV 임포트</option>
                <option value="paste_split">텍스트 분할</option>
                <option value="file_upload">파일 업로드</option>
                <option value="web">웹 크롤링</option>
                <option value="confluence">Confluence</option>
                <option value="teams">Teams</option>
              </select>
            </div>
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

          {!selectedNs && <div className="text-center py-16 text-slate-500">파트를 선택하세요.</div>}
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
                      <div className="flex items-center gap-2 flex-wrap">
                        {(item as any).source_file && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-sky-900/40 text-sky-300 font-mono">
                            {(item as any).source_type === 'csv_import' ? '📊' : (item as any).source_type === 'paste_split' ? '📋' : (item as any).source_type === 'teams' ? '💬' : '📄'}{' '}
                            {(item as any).source_file}{(item as any).source_chunk_idx != null && ` #${(item as any).source_chunk_idx}`}
                          </span>
                        )}
                        {item.category && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-violet-900/40 text-violet-300 border border-violet-700/40 font-medium">{item.category}</span>
                        )}
                        {item.container_name && (
                          <>
                            <span className="text-[10px] text-slate-500 font-medium">컨테이너</span>
                            {item.container_name.split(',').map((c) => c.trim()).filter(Boolean).map((c) => (
                              <Badge key={c} color="cyan">{c}</Badge>
                            ))}
                          </>
                        )}
                        {(item.target_tables ?? []).length > 0 && (
                          <>
                            <span className="text-[10px] text-slate-500 font-medium">테이블</span>
                            {(item.target_tables ?? []).slice(0, 3).map((t) => <Badge key={t} color="amber">{t}</Badge>)}
                            {(item.target_tables ?? []).length > 3 && <Badge color="slate">+{(item.target_tables ?? []).length - 3}</Badge>}
                          </>
                        )}
                        <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${weightClass(item.base_weight)}`}>
                          우선순위: {weightLabel(item.base_weight)} ({item.base_weight})
                        </span>
                      </div>
                      <p className="text-xs text-slate-500 mt-0.5 truncate">{item.content.slice(0, 100)}...</p>
                    </div>
                    <div className="flex items-center gap-2 flex-shrink-0 text-[10px] text-slate-500">
                      {searchMode === 'vector' && (item as KnowledgeItem & { similarity?: number }).similarity != null && (
                        <span className="px-1.5 py-0.5 rounded bg-violet-900/40 text-violet-300 border border-violet-700/40 font-mono">
                          {((item as KnowledgeItem & { similarity?: number }).similarity! * 100).toFixed(1)}%
                        </span>
                      )}
                      <span>{new Date(item.updated_at !== item.created_at ? item.updated_at : item.created_at).toISOString().slice(0, 10)}</span>
                      {item.created_by_username && <span>{item.created_by_username}</span>}
                      {item.created_by_part && <Badge color={canModifyNs ? 'emerald' : 'slate'}>{item.created_by_part}</Badge>}
                    </div>
                  </div>
                </div>
              ))}
              {displayItems.length === 0 && !vectorSearchQuery.isFetching && (
                <div className="text-center py-16 text-slate-500">
                  {searchQuery || categoryFilter || sourceFilter || vectorQuery ? '검색 결과가 없습니다.' : '지식 항목이 없습니다.'}
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
          categoryNames={categoryNames}
          canModify={canModifyNs}
          jobs={jobs}
          onSuccess={onIngestSuccess}
          onGoToList={() => setSubTab('list')}
          onGoToReview={() => setSubTab('review')}
        />
      )}

      {/* ── 서브탭 3: 승인 대기 ── */}
      {subTab === 'review' && (
        <ReviewTab
          items={pendingItems}
          canModify={canModifyNs}
          onResolved={onReviewResolved}
        />
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
          {canModifyNs ? (
            <RequiredCategoryField categoryNames={categoryNames} value={editForm.category}
              onChange={(v) => setEditForm((f) => ({ ...f, category: v }))} />
          ) : categoryNames.length > 0 && (
            <div>
              <label className="text-xs font-medium text-slate-400">업무구분</label>
              <div className="mt-1 bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300">
                {editForm.category || <span className="text-slate-500">미분류</span>}
              </div>
            </div>
          )}
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">컨테이너명</label>
            <TagInput tags={editForm.container_names} onChange={(tags) => setEditForm((f) => ({ ...f, container_names: tags }))}
              placeholder="컨테이너명 입력..." readOnly={!canModifyNs} color="cyan" />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">대상 테이블</label>
            <TagInput tags={editForm.target_tables} onChange={(tags) => setEditForm((f) => ({ ...f, target_tables: tags }))}
              placeholder="테이블명 입력..." readOnly={!canModifyNs} color="indigo" />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">내용</label>
            <textarea rows={10} value={editForm.content} readOnly={!canModifyNs}
              onChange={(e) => setEditForm((f) => ({ ...f, content: e.target.value }))}
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 resize-y min-h-[260px] read-only:border-slate-700 leading-relaxed" />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">쿼리 템플릿 (선택)</label>
            <textarea rows={4} value={editForm.query_template} readOnly={!canModifyNs}
              onChange={(e) => setEditForm((f) => ({ ...f, query_template: e.target.value }))}
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm font-mono text-slate-200 focus:outline-none focus:border-indigo-500 resize-y min-h-[100px] read-only:border-slate-700"
              placeholder="SELECT ..." />
          </div>
          {canModifyNs && (
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">
                문서 우선순위: <span className={`font-medium ${editForm.base_weight >= 2 ? 'text-emerald-400' : editForm.base_weight >= 1.5 ? 'text-indigo-400' : 'text-slate-300'}`}>
                  {editForm.base_weight.toFixed(1)} — {weightLabel(editForm.base_weight)}
                </span>
              </label>
              <input type="range" min={0} max={3} step={0.1} value={editForm.base_weight}
                onChange={(e) => setEditForm((f) => ({ ...f, base_weight: parseFloat(e.target.value) }))}
                className="w-full accent-indigo-500" />
            </div>
          )}
          {updateMutation.error && <p className="text-xs text-rose-400">{String(updateMutation.error)}</p>}
          <div className="flex gap-2 justify-end pt-2">
            <Button variant="ghost" size="sm" onClick={() => { setShowEdit(false); setEditingId(null); }}>
              <X className="w-3.5 h-3.5" />{canModifyNs ? '취소' : '닫기'}
            </Button>
            {canModifyNs && (
              <Button variant="primary" size="sm" loading={updateMutation.isPending}
                onClick={() => editingId !== null && updateMutation.mutate(editingId)}
                disabled={!editForm.content.trim() || !editForm.category || categoryNames.length === 0}>
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
          {bulkDeleteMutation.error && <p className="text-xs text-rose-400">{String(bulkDeleteMutation.error)}</p>}
          <div className="flex gap-2 justify-end">
            <Button variant="secondary" size="sm" onClick={() => setShowBulkConfirm(false)}>취소</Button>
            <Button variant="danger" size="sm" loading={bulkDeleteMutation.isPending} onClick={() => bulkDeleteMutation.mutate(Array.from(selectedIds))}>삭제</Button>
          </div>
        </div>
      </Modal>

      {/* Bulk Edit Modal */}
      <Modal isOpen={showBulkEdit} onClose={() => setShowBulkEdit(false)} title="지식 일괄 수정">
        <div className="space-y-4">
          <p className="text-sm text-slate-300">선택한 <span className="text-indigo-400 font-semibold">{selectedIds.size}개</span> 지식 항목의 업무구분·소스유형을 변경합니다. 값을 지정한 필드만 바뀌고, 비워두면 그대로 유지됩니다.</p>
          <div>
            <label className="text-xs font-medium text-slate-400 block mb-1">업무구분</label>
            <select
              value={bulkEditCategory}
              onChange={(e) => setBulkEditCategory(e.target.value)}
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
            >
              <option value="">(변경 안 함)</option>
              {categoryNames.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
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
              <option value="teams">Teams</option>
            </select>
          </div>
          {bulkUpdateMutation.error && <p className="text-xs text-rose-400">{String(bulkUpdateMutation.error)}</p>}
          <div className="flex gap-2 justify-end">
            <Button variant="secondary" size="sm" onClick={() => setShowBulkEdit(false)}>취소</Button>
            <Button
              variant="primary" size="sm"
              disabled={!bulkEditCategory && !bulkEditSourceType}
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
          {deleteMutation.error && <p className="text-xs text-rose-400">{String(deleteMutation.error)}</p>}
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
}

function ChunkReviewModal({ isOpen, onClose, chunks, onConfirm, loading, sourceName, category, categoryNames, onCategoryChange }: {
  isOpen: boolean;
  onClose: () => void;
  chunks: ReviewChunk[];
  onConfirm: (selected: ReviewChunk[]) => Promise<void>;
  loading: boolean;
  sourceName: string;
  category: string;
  categoryNames: string[];
  onCategoryChange: (v: string) => void;
}) {
  const [rows, setRows] = useState<ReviewChunk[]>([]);
  const [expandedIdx, setExpandedIdx] = useState<Set<number>>(new Set());

  useEffect(() => {
    setRows(chunks.map(c => ({ ...c, selected: true })));
    setExpandedIdx(new Set());
  }, [chunks]);

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
        <RequiredCategoryField categoryNames={categoryNames} value={category} onChange={onCategoryChange} />

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
                    ? 'border-indigo-600/60 bg-indigo-950/30'
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
          <Button variant="primary" size="sm" loading={loading} disabled={selectedCount === 0 || !category || categoryNames.length === 0}
            onClick={() => onConfirm(rows.filter(r => r.selected))}>
            <CheckCircle className="w-3.5 h-3.5" />선택 항목 등록 ({selectedCount}건)
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
              job.status === 'completed' ? 'bg-emerald-900/30 text-emerald-400' :
              job.status === 'failed' ? 'bg-rose-900/30 text-rose-400' :
              job.status === 'cancelled' ? 'bg-slate-700 text-slate-400' :
              'bg-amber-900/30 text-amber-400'
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

          {cancelError && <p className="text-xs text-rose-400">{cancelError}</p>}

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

function IngestTab({ namespace, categoryNames, canModify, jobs, onSuccess, onGoToList, onGoToReview }: {
  namespace: string;
  categoryNames: string[];
  canModify: boolean;
  jobs: IngestionJob[];
  onSuccess: () => void;
  onGoToList: () => void;
  onGoToReview: () => void;
}) {
  const [activeMethod, setActiveMethod] = useState<IngestMethod>(null);
  const [progressJobId, setProgressJobId] = useState<number | null>(null);
  const [autoNavigate, setAutoNavigate] = useState(false);

  // 폼 제출 성공 시: 비동기 배치 작업(jobId)이면 진행 모달을 띄우고 완료될 때까지
  // 기다렸다가 결과(승인 대기 여부)에 따라 이동, 동기 단건 등록이면 즉시 이동.
  const handleFormOutcome = (outcome?: { jobId?: number; pendingReview?: boolean }) => {
    if (outcome?.jobId) {
      setProgressJobId(outcome.jobId);
      setAutoNavigate(true);
    } else if (outcome?.pendingReview) {
      alert('유사한 기존 지식이 있어 승인 대기 상태로 등록됐습니다. 승인 대기 탭으로 이동합니다.');
      onGoToReview();
    } else {
      onGoToList();
    }
  };

  if (!namespace) {
    return <div className="text-center py-16 text-slate-500">파트를 선택하세요.</div>;
  }
  if (!canModify) {
    return <div className="text-center py-16 text-slate-500">이 파트의 지식 등록 권한이 없습니다.</div>;
  }

  const methods: { id: IngestMethod; icon: React.ReactNode; title: string; desc: string; badge?: string }[] = [
    { id: 'file', icon: <Upload className="w-6 h-6" />, title: '파일 업로드', desc: 'PDF · Markdown · TXT · Excel(.xlsx) · CSV 파일을 드래그하거나 클릭하여 업로드. 자동 파싱·청킹.', badge: 'AI 분석 지원' },
    { id: 'text', icon: <FileText className="w-6 h-6" />, title: '대량 텍스트', desc: '텍스트를 붙여넣으면 헤더·단락 기준으로 자동 분할해 등록합니다.', badge: 'AI 분석 지원' },
    { id: 'manual', icon: <PenLine className="w-6 h-6" />, title: '직접 입력', desc: '단건 지식을 직접 작성하여 등록합니다.' },
    { id: 'url', icon: <Globe className="w-6 h-6" />, title: 'URL / Confluence', desc: '웹 페이지 또는 Confluence 페이지 URL을 입력하면 내용을 자동 수집합니다.', badge: 'AI 분석 지원' },
    { id: 'teams', icon: <MessageSquare className="w-6 h-6" />, title: 'Teams 메시지', desc: 'Teams 채팅방에 로그인하여 메시지를 선택적으로 수집합니다. 토큰은 서버 메모리에만 저장됩니다.' },
  ];

  return (
    <div className="space-y-5">
      {/* 지원 기능 안내 */}
      <div className="rounded-xl border border-slate-700/60 bg-slate-800/40 px-4 py-3 space-y-2.5 text-xs text-slate-400">
        <div className="flex items-center gap-2">
          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-violet-900/40 text-violet-300 border border-violet-700/40 shrink-0">AI 분석 지원</span>
          <span className="text-slate-500">배지 기능 상세</span>
        </div>
        <div className="space-y-1.5 pl-1">
          <div className="flex items-start gap-2">
            <span className="w-1.5 h-1.5 rounded-full bg-violet-400 shrink-0 mt-1" />
            <span><span className="text-slate-300">AI 자동 태깅</span><span className="text-slate-600 mx-1.5">—</span>카테고리 · 시스템명 · 중요도 자동 분류<span className="ml-2 text-slate-600">파일 업로드 · URL/Confluence</span></span>
          </div>
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
                ? 'border-indigo-500 bg-indigo-950/40 text-indigo-300'
                : 'border-slate-700 bg-slate-800/60 text-slate-300 hover:border-slate-500 hover:bg-slate-800'
            }`}
          >
            <div className="flex items-start gap-3">
              <div className={`mt-0.5 ${activeMethod === m.id ? 'text-indigo-400' : 'text-slate-400'}`}>{m.icon}</div>
              <div>
                <div className="flex items-center gap-2">
                  <span className="font-medium text-sm">{m.title}</span>
                  {m.badge && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-violet-900/40 text-violet-300 border border-violet-700/40">{m.badge}</span>
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
        <FileUploadForm namespace={namespace} categoryNames={categoryNames}
          onSuccess={(outcome) => { onSuccess(); handleFormOutcome(outcome); }} onCancel={() => setActiveMethod(null)} />
      )}
      {activeMethod === 'text' && (
        <TextSplitForm namespace={namespace} categoryNames={categoryNames}
          onSuccess={(outcome) => { onSuccess(); handleFormOutcome(outcome); }} onCancel={() => setActiveMethod(null)} />
      )}
      {activeMethod === 'manual' && (
        <ManualForm namespace={namespace} categoryNames={categoryNames}
          onSuccess={(outcome) => { onSuccess(); handleFormOutcome(outcome); }} onCancel={() => setActiveMethod(null)} />
      )}
      {activeMethod === 'url' && (
        <UrlForm namespace={namespace} categoryNames={categoryNames}
          onSuccess={(outcome) => { onSuccess(); handleFormOutcome(outcome); }} onCancel={() => setActiveMethod(null)} />
      )}
      {activeMethod === 'teams' && (
        <TeamsForm namespace={namespace} categoryNames={categoryNames}
          onSuccess={(outcome) => { onSuccess(); handleFormOutcome(outcome); }} onCancel={() => setActiveMethod(null)} />
      )}

      {/* 인제스천 작업 이력 */}
      {jobs.length > 0 && (
        <div>
          <h3 className="text-sm font-medium text-slate-400 mb-2">등록 이력</h3>
          <div className="rounded-xl border border-slate-700 overflow-hidden divide-y divide-slate-700/60">
            {jobs.slice(0, 10).map((j: IngestionJob) => {
              const isProcessing = j.status === 'processing';
              return (
                <div key={j.id}
                  className={`flex items-center gap-3 px-4 py-2.5 text-xs ${isProcessing ? 'cursor-pointer hover:bg-slate-800/60' : ''}`}
                  onClick={() => isProcessing && setProgressJobId(j.id)}
                >
                  <span className={`flex items-center gap-1 px-1.5 py-0.5 rounded font-medium flex-shrink-0 ${
                    j.status === 'completed' ? 'bg-emerald-900/30 text-emerald-400' :
                    j.status === 'failed' ? 'bg-rose-900/30 text-rose-400' :
                    j.status === 'cancelled' ? 'bg-slate-700 text-slate-400' :
                    'bg-amber-900/30 text-amber-400'
                  }`}>
                    {j.status === 'completed' ? <CheckCircle className="w-3 h-3" /> : j.status === 'failed' ? <AlertCircle className="w-3 h-3" /> : <Clock className="w-3 h-3" />}
                    {j.status}
                  </span>
                  <span className="text-slate-400 flex-shrink-0 text-[10px] px-1.5 py-0.5 bg-slate-700/60 rounded">
                    {j.source_type === 'csv_import' ? 'CSV' : j.source_type === 'file_upload' ? '파일' : j.source_type === 'paste_split' ? '텍스트' : j.source_type === 'web' ? '웹' : j.source_type === 'confluence' ? 'Confluence' : j.source_type === 'teams' ? 'Teams' : '수동'}
                  </span>
                  <span className="text-slate-300 truncate flex-1">{j.source_file || '-'}</span>
                  <span className="text-slate-500 flex-shrink-0">{j.created_chunks}/{j.total_chunks}건</span>
                  {isProcessing && j.total_chunks > 0 && (
                    <span className="text-indigo-400 flex-shrink-0 font-medium">
                      {Math.round((j.created_chunks / j.total_chunks) * 100)}%
                    </span>
                  )}
                  {j.auto_glossary > 0 && <span className="text-violet-400 flex-shrink-0">용어 +{j.auto_glossary}</span>}
                  {j.auto_fewshot > 0 && <span className="text-emerald-400 flex-shrink-0">Q&A +{j.auto_fewshot}</span>}
                  {j.pending_chunks > 0 && (
                    <button
                      onClick={(e) => { e.stopPropagation(); onGoToReview(); }}
                      className="flex-shrink-0 text-[10px] font-medium bg-amber-900/40 text-amber-400 border border-amber-700/40 px-1.5 py-0.5 rounded hover:bg-amber-900/60"
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
        </div>
      )}

      <IngestionProgressModal
        jobId={progressJobId}
        onClose={() => { setProgressJobId(null); setAutoNavigate(false); }}
        onSettled={(job) => {
          onSuccess();
          if (!autoNavigate) return;
          setAutoNavigate(false);
          if (job.pending_chunks > 0) {
            alert(`유사한 기존 지식이 있어 ${job.pending_chunks}건이 승인 대기 상태로 등록됐습니다. 승인 대기 탭으로 이동합니다.`);
            onGoToReview();
          } else if (job.status === 'completed') {
            onGoToList();
          }
        }}
      />
    </div>
  );
}


// ── 서브탭 3: 승인 대기 (중복 의심 리뷰) ───────────────────────────────────────

function ReviewTab({ items, canModify, onResolved }: {
  items: KnowledgeItem[];
  canModify: boolean;
  onResolved: () => void;
}) {
  const [selectedItem, setSelectedItem] = useState<KnowledgeItem | null>(null);
  const [expandedMatchId, setExpandedMatchId] = useState<number | null>(null);
  const [actionError, setActionError] = useState('');
  const [pendingAction, setPendingAction] = useState<'approve' | 'reject' | 'merge' | null>(null);

  const { data: matches = [], isLoading: matchesLoading } = useQuery({
    queryKey: ['knowledge-duplicate-matches', selectedItem?.id],
    queryFn: () => getDuplicateMatches(selectedItem!.id),
    enabled: selectedItem !== null,
  });
  // 유사도 높은 순 정렬 — 백엔드가 이미 정렬해 오지만 방어적으로 한번 더 보장
  const sortedMatches = [...matches].sort((a, b) => b.similarity - a.similarity);

  useEffect(() => {
    setExpandedMatchId(sortedMatches[0]?.id ?? null);
  }, [selectedItem?.id]);

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
      <p className="text-xs text-slate-500">
        기존 지식과 유사도가 높아 자동 반영되지 않고 검토를 기다리는 항목입니다. 클릭해서 어떤
        기존 지식과 겹치는지 확인 후 처리하세요.
      </p>
      <div className="rounded-xl border border-slate-700 divide-y divide-slate-700/60 overflow-hidden">
        {items.map((item) => (
          <button
            key={item.id}
            onClick={() => setSelectedItem(item)}
            className="w-full text-left px-4 py-3 flex items-center gap-3 hover:bg-slate-800/60 transition-colors"
          >
            <Badge color="amber">승인 대기</Badge>
            {item.category && <Badge color="cyan">{item.category}</Badge>}
            <span className="text-sm text-slate-300 truncate flex-1">{item.content}</span>
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
        maxWidth="max-w-3xl"
      >
        {selectedItem && (
          <div className="space-y-4">
            <div>
              <p className="text-xs font-medium text-amber-400 mb-1.5">신규 등록 (승인 대기)</p>
              <div className="rounded-lg bg-amber-900/10 border border-amber-700/30 px-3 py-2.5 text-sm text-slate-200 whitespace-pre-wrap max-h-40 overflow-y-auto">
                {selectedItem.content}
              </div>
            </div>

            <div>
              <p className="text-xs font-medium text-indigo-400 mb-1.5">
                유사한 기존 지식 {sortedMatches.length > 0 ? `${sortedMatches.length}건 (유사도순)` : ''}
              </p>
              {matchesLoading ? (
                <div className="text-sm text-slate-500 py-4 text-center">불러오는 중...</div>
              ) : (
                <div className="space-y-1.5 max-h-72 overflow-y-auto">
                  {sortedMatches.map((m: DuplicateMatch) => {
                    const isExpanded = expandedMatchId === m.id;
                    return (
                      <div key={m.id} className="rounded-lg border border-slate-700 bg-slate-900/40 overflow-hidden">
                        <button
                          type="button"
                          onClick={() => setExpandedMatchId(isExpanded ? null : m.id)}
                          className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-slate-800/60 transition-colors"
                        >
                          <span className="text-xs font-mono text-indigo-400 flex-shrink-0 w-14">
                            {(m.similarity * 100).toFixed(1)}%
                          </span>
                          {!isExpanded && (
                            <span className="text-xs text-slate-400 truncate flex-1">{m.content}</span>
                          )}
                          {isExpanded && <span className="flex-1" />}
                          {isExpanded
                            ? <ChevronUp className="w-3.5 h-3.5 text-slate-500 flex-shrink-0" />
                            : <ChevronDown className="w-3.5 h-3.5 text-slate-500 flex-shrink-0" />}
                        </button>
                        {isExpanded && (
                          <div className="px-3 pb-3 pt-1 border-t border-slate-700/60 space-y-2.5">
                            <p className="text-sm text-slate-300 whitespace-pre-wrap">{m.content}</p>
                            {canModify && (
                              <div>
                                <Button
                                  variant="secondary" size="sm"
                                  loading={resolveMutation.isPending && pendingAction === 'merge'}
                                  onClick={() => resolveMutation.mutate({ action: 'merge', targetId: m.id })}
                                >
                                  이 기존 지식에 신규 등록 내용 덮어쓰기
                                </Button>
                                <p className="text-[11px] text-slate-500 mt-1">
                                  → 바로 위에 보이는 이 기존 지식의 내용이 "신규 등록" 내용으로 바뀝니다. 신규 등록 항목 자체는 삭제됩니다.
                                </p>
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>

            {actionError && <p className="text-xs text-rose-400">{actionError}</p>}

            {canModify ? (
              <div className="pt-2 border-t border-slate-700 space-y-2">
                <p className="text-[11px] text-slate-500">
                  위에서 특정 기존 지식을 덮어쓰지 않을 경우, 아래 둘 중 하나를 선택하세요.
                </p>
                <div className="flex gap-2 justify-end">
                  <div className="text-right">
                    <Button
                      variant="ghost" size="sm"
                      loading={resolveMutation.isPending && pendingAction === 'reject'}
                      onClick={() => resolveMutation.mutate({ action: 'reject' })}
                    >
                      신규 등록 내용 폐기
                    </Button>
                    <p className="text-[11px] text-slate-500 mt-1">→ 신규 등록 내용을 삭제. 기존 지식들은 전부 그대로 유지.</p>
                  </div>
                  <div className="text-right">
                    <Button
                      variant="primary" size="sm"
                      loading={resolveMutation.isPending && pendingAction === 'approve'}
                      onClick={() => resolveMutation.mutate({ action: 'approve' })}
                    >
                      신규 등록 내용 그대로 저장
                    </Button>
                    <p className="text-[11px] text-slate-500 mt-1">→ 중복 아님 — 신규 내용을 별도 지식으로 추가. 기존 지식들도 그대로 유지(둘 다 남음).</p>
                  </div>
                </div>
              </div>
            ) : (
              <p className="text-xs text-slate-500 pt-2 border-t border-slate-700">이 파트의 승인 권한이 없습니다.</p>
            )}
          </div>
        )}
      </Modal>
    </div>
  );
}


// ── 파일 업로드 인라인 폼 ────────────────────────────────────────────────────

function FileUploadForm({ namespace, categoryNames, onSuccess, onCancel }: {
  namespace: string; categoryNames: string[];
  onSuccess: (outcome?: { jobId?: number }) => void; onCancel: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [category, setCategory] = useState(categoryNames.includes('공통지식') ? '공통지식' : '');
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
      const result = await previewFileUpload(file);
      setDetectedStrategy((result as any).detected_strategy ?? null);
      setReviewChunks(result.chunks.map(c => ({ ...c, selected: true })));
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

  const handleConfirm = async (selected: ReviewChunk[]) => {
    setLoading(true); setError('');
    try {
      const items = selected.map(c => ({ content: c.text, category }));
      const result = await bulkCreateKnowledge(namespace, items, file!.name, 'file_upload');
      setDone(`${items.length}건 등록을 시작했습니다 — 진행 상황은 아래 등록 이력에서 확인하세요.`);
      setShowReview(false);
      onSuccess({ jobId: result.job_id });
    } catch (e: any) { setError(e.message || '오류 발생'); }
    finally { setLoading(false); }
  };

  return (
    <div className="bg-slate-800/60 rounded-xl border border-indigo-800/40 p-5 space-y-4">
      <h3 className="text-sm font-semibold text-slate-200 flex items-center gap-2"><Upload className="w-4 h-4 text-indigo-400" />파일 업로드</h3>

      <RequiredCategoryField categoryNames={categoryNames} value={category} onChange={setCategory} />

      <div
        className={`border-2 border-dashed rounded-xl p-6 text-center cursor-pointer transition-colors ${
          isDragging
            ? 'border-indigo-400 bg-indigo-500/10'
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
            <Upload className={`w-8 h-8 mx-auto mb-2 ${isDragging ? 'text-indigo-400' : 'text-slate-500'}`} />
            <p className={`text-sm ${isDragging ? 'text-indigo-300 font-medium' : 'text-slate-400'}`}>
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

      {done && <p className="text-sm text-emerald-400 font-medium">{done}</p>}
      {error && <p className="text-xs text-rose-400">{error}</p>}

      <div className="flex gap-2 justify-end pt-1">
        <Button variant="ghost" size="sm" onClick={onCancel}>취소</Button>
        <Button variant="primary" size="sm" onClick={handleOpenReview} disabled={!file || previewing || !category || categoryNames.length === 0}>
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
        category={category} categoryNames={categoryNames} onCategoryChange={setCategory}
      />
    </div>
  );
}


// ── 대량 텍스트 인라인 폼 ────────────────────────────────────────────────────

function TextSplitForm({ namespace, categoryNames, onSuccess, onCancel }: {
  namespace: string; categoryNames: string[];
  onSuccess: (outcome?: { jobId?: number }) => void; onCancel: () => void;
}) {
  const [text, setText] = useState('');
  const [category, setCategory] = useState(categoryNames.includes('공통지식') ? '공통지식' : '');
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
      const result = await previewTextSplit(text);
      setDetectedStrategy((result as any).detected_strategy ?? null);
      setReviewChunks(result.chunks.map((c, i) => ({ idx: i, text: c, title: null, selected: true })));
      setShowReview(true);
    } catch (e: any) { setError(e.message || '분할 미리보기 실패'); }
    finally { setPreviewing(false); }
  };

  const handleConfirm = async (selected: ReviewChunk[]) => {
    setLoading(true); setError('');
    try {
      const items = selected.map(c => ({ content: c.text, category }));
      const result = await bulkCreateKnowledge(namespace, items, '텍스트 직접입력', 'paste_split');
      setDone(`${items.length}건 등록을 시작했습니다 — 진행 상황은 아래 등록 이력에서 확인하세요.`);
      setShowReview(false);
      onSuccess({ jobId: result.job_id });
    } catch (e: any) { setError(e.message || '오류 발생'); }
    finally { setLoading(false); }
  };

  return (
    <div className="bg-slate-800/60 rounded-xl border border-indigo-800/40 p-5 space-y-4">
      <h3 className="text-sm font-semibold text-slate-200 flex items-center gap-2"><FileText className="w-4 h-4 text-indigo-400" />대량 텍스트 등록</h3>

      <RequiredCategoryField categoryNames={categoryNames} value={category} onChange={setCategory} />

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

      {done && <p className="text-sm text-emerald-400 font-medium">{done}</p>}
      {error && <p className="text-xs text-rose-400">{error}</p>}

      <div className="flex gap-2 justify-end pt-1">
        <Button variant="ghost" size="sm" onClick={onCancel}>취소</Button>
        <Button variant="primary" size="sm" onClick={handleOpenReview} disabled={!text.trim() || previewing || !category || categoryNames.length === 0}>
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
        category={category} categoryNames={categoryNames} onCategoryChange={setCategory}
      />
    </div>
  );
}


// ── 직접 입력 인라인 폼 ──────────────────────────────────────────────────────

function ManualForm({ namespace, categoryNames, onSuccess, onCancel }: {
  namespace: string; categoryNames: string[];
  onSuccess: (outcome?: { pendingReview?: boolean }) => void; onCancel: () => void;
}) {
  const qc = useQueryClient();
  const [form, setForm] = useState<KnowledgeFormData>({
    ...defaultForm,
    category: categoryNames.includes('공통지식') ? '공통지식' : '',
  });
  const [done, setDone] = useState('');
  const [donePending, setDonePending] = useState(false);

  const createMutation = useMutation({
    mutationFn: () =>
      createKnowledge({
        namespace,
        container_name: form.container_names.join(', '),
        target_tables: form.target_tables,
        content: form.content,
        query_template: form.query_template || null,
        base_weight: form.base_weight,
        category: form.category,
      }),
    onSuccess: (created) => {
      qc.invalidateQueries({ queryKey: ['knowledge', namespace] });
      qc.invalidateQueries({ queryKey: ['stats-ns', namespace] });
      setDone(
        created.pending_review
          ? '유사한 기존 지식이 있어 승인 대기 상태로 등록됐습니다 — "승인 대기" 탭에서 확인하세요.'
          : '등록 완료'
      );
      setDonePending(!!created.pending_review);
      setForm({ ...defaultForm, category: categoryNames.includes('공통지식') ? '공통지식' : '' });
      onSuccess({ pendingReview: !!created.pending_review });
    },
  });

  return (
    <div className="bg-slate-800/60 rounded-xl border border-indigo-800/40 p-5 space-y-3">
      <h3 className="text-sm font-semibold text-slate-200 flex items-center gap-2"><PenLine className="w-4 h-4 text-indigo-400" />직접 입력</h3>

      <RequiredCategoryField categoryNames={categoryNames} value={form.category}
        onChange={(v) => setForm((f) => ({ ...f, category: v }))} />
      <div>
        <label className="block text-xs font-medium text-slate-400 mb-1">컨테이너명 <span className="text-slate-600">(Enter 또는 쉼표로 추가)</span></label>
        <TagInput tags={form.container_names} onChange={(tags) => setForm((f) => ({ ...f, container_names: tags }))}
          placeholder="컨테이너명 입력..." color="cyan" />
      </div>
      <div>
        <label className="block text-xs font-medium text-slate-400 mb-1">대상 테이블 <span className="text-slate-600">(Enter 또는 쉼표로 추가)</span></label>
        <TagInput tags={form.target_tables} onChange={(tags) => setForm((f) => ({ ...f, target_tables: tags }))}
          placeholder="테이블명 입력..." color="indigo" />
      </div>
      <div>
        <label className="block text-xs font-medium text-slate-400 mb-1">내용 <span className="text-rose-400">*</span></label>
        <textarea rows={8} value={form.content} onChange={(e) => setForm((f) => ({ ...f, content: e.target.value }))}
          className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 resize-y min-h-[160px]" />
      </div>
      <div>
        <label className="block text-xs font-medium text-slate-400 mb-1">쿼리 템플릿 (선택)</label>
        <textarea rows={3} value={form.query_template} onChange={(e) => setForm((f) => ({ ...f, query_template: e.target.value }))}
          className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm font-mono text-slate-200 focus:outline-none focus:border-indigo-500 resize-y"
          placeholder="SELECT ..." />
      </div>
      <div>
        <label className="block text-xs font-medium text-slate-400 mb-1">
          문서 우선순위: <span className={`font-medium ${form.base_weight >= 2 ? 'text-emerald-400' : form.base_weight >= 1.5 ? 'text-indigo-400' : 'text-slate-300'}`}>
            {form.base_weight.toFixed(1)} — {weightLabel(form.base_weight)}
          </span>
        </label>
        <input type="range" min={0} max={3} step={0.1} value={form.base_weight}
          onChange={(e) => setForm((f) => ({ ...f, base_weight: parseFloat(e.target.value) }))}
          className="w-full accent-indigo-500" />
        <p className="text-[11px] text-slate-500 mt-1">1.0=기본 · 1.5+=보통 · 2.0+=높음(핵심 문서)</p>
      </div>

      {done && <p className={`text-sm font-medium ${donePending ? 'text-amber-400' : 'text-emerald-400'}`}>{done}</p>}
      {createMutation.isError && <p className="text-xs text-rose-400">{String(createMutation.error)}</p>}

      <div className="flex gap-2 justify-end pt-1">
        <Button variant="ghost" size="sm" onClick={onCancel}>취소</Button>
        <Button variant="primary" size="sm"
          loading={createMutation.isPending}
          onClick={() => createMutation.mutate()}
          disabled={createMutation.isPending || form.container_names.length === 0 || !form.content.trim() || !form.category || categoryNames.length === 0}>
          추가
        </Button>
      </div>
    </div>
  );
}


// ── URL / Confluence 인라인 폼 ────────────────────────────────────────────────

function UrlForm({ namespace, categoryNames, onSuccess, onCancel }: {
  namespace: string; categoryNames: string[];
  onSuccess: (outcome?: { jobId?: number }) => void; onCancel: () => void;
}) {
  const { user, updateUser } = useAuthStore();
  const [url, setUrl] = useState('');
  const [category, setCategory] = useState(categoryNames.includes('공통지식') ? '공통지식' : '');
  const [reviewChunks, setReviewChunks] = useState<ReviewChunk[]>([]);
  const [sourceMeta, setSourceMeta] = useState<{ name: string; type: string } | null>(null);
  const [showReview, setShowReview] = useState(false);
  const [loading, setLoading] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [error, setError] = useState('');
  const [done, setDone] = useState('');

  // 트리 선택 (Confluence 하위 페이지)
  const [includeChildren, setIncludeChildren] = useState(false);
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
      setReviewChunks(result.chunks.map(c => ({ ...c, selected: true })));
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
        const failMsg = result.failed_pages.length > 0
          ? ` (실패: ${result.failed_pages.map(p => p.title).join(', ')})`
          : '';
        setError(`청크가 생성되지 않았습니다.${failMsg}`);
        return;
      }

      // ChunkReviewModal로 전환 (페이지 제목을 청크 타이틀에 prefix)
      setSourceMeta({
        name: `Confluence ${pages.length}개 페이지`,
        type: 'confluence_bulk',
      });
      setReviewChunks(result.chunks.map(c => ({
        idx: c.idx,
        text: c.text,
        title: c.title ? `${c.page_title} — ${c.title}` : c.page_title,
        selected: true,
      })));
      setShowTreeModal(false);
      setShowReview(true);

      if (result.failed_pages.length > 0) {
        setError(`일부 페이지 fetch 실패: ${result.failed_pages.map(p => p.title).join(', ')}`);
      }
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

  const handleConfirm = async (selected: ReviewChunk[]) => {
    setLoading(true); setError('');
    try {
      const items = selected.map(c => ({ content: c.text, category }));
      const srcName = sourceMeta?.name ?? url;
      const srcType = sourceMeta?.type ?? (isConfluence ? 'confluence' : 'web');
      const result = await bulkCreateKnowledge(namespace, items, srcName, srcType);
      setDone(`"${srcName}" — ${items.length}건 등록을 시작했습니다 — 진행 상황은 아래 등록 이력에서 확인하세요.`);
      setShowReview(false);
      onSuccess({ jobId: result.job_id });
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
    <div className="bg-slate-800/60 rounded-xl border border-indigo-800/40 p-5 space-y-4">
      <h3 className="text-sm font-semibold text-slate-200 flex items-center gap-2">
        <Globe className="w-4 h-4 text-indigo-400" />URL / Confluence 수집
      </h3>

      <RequiredCategoryField categoryNames={categoryNames} value={category} onChange={setCategory} />

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
                className="text-xs text-rose-400 hover:text-rose-300">삭제</button>
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
        <div className="flex items-center gap-2 p-2.5 bg-slate-900/60 rounded-lg border border-slate-700">
          <input
            type="checkbox"
            id="includeChildren"
            checked={includeChildren}
            onChange={(e) => setIncludeChildren(e.target.checked)}
            className="w-3.5 h-3.5 accent-indigo-500"
          />
          <label htmlFor="includeChildren" className="text-xs text-slate-300 cursor-pointer flex-1">
            하위 페이지 포함 — 입력 URL을 최상단으로 자손 페이지 트리에서 선택 등록
          </label>
        </div>
      )}

      {done && <p className="text-sm text-emerald-400 font-medium">{done}</p>}
      {error && <p className="text-xs text-rose-400">{error}</p>}

      <div className="flex gap-2 justify-end pt-1">
        <Button variant="ghost" size="sm" onClick={onCancel}>취소</Button>
        <Button variant="primary" size="sm" onClick={handleOpenReview}
          disabled={!url.trim() || previewing || treeLoading || !category || categoryNames.length === 0}>
          {treeLoading ? '트리 조회 중...' : previewing ? '수집 중...' : (isConfluence && includeChildren ? '하위 페이지 선택' : '수집 & 청크 검토')}
        </Button>
      </div>

      <ChunkReviewModal
        isOpen={showReview}
        onClose={() => setShowReview(false)}
        chunks={reviewChunks}
        onConfirm={handleConfirm}
        loading={loading}
        sourceName={sourceMeta?.name ?? url}
        category={category} categoryNames={categoryNames} onCategoryChange={setCategory}
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
                  disabled={selectedPageIds.size === 0 || loading || !category}
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
            {patError && <p className="text-xs text-rose-400">{patError}</p>}
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


// ── Teams 인라인 폼 ───────────────────────────────────────────────────────────

function TeamsForm({ namespace, categoryNames, onSuccess, onCancel }: {
  namespace: string; categoryNames: string[];
  onSuccess: (outcome?: { jobId?: number }) => void; onCancel: () => void;
}) {
  const { accessToken } = useAuthStore();
  const [authStatus, setAuthStatus] = useState<TeamsAuthStatus | null>(null);
  const [chats, setChats] = useState<TeamsChat[]>([]);
  const [selectedChat, setSelectedChat] = useState<TeamsChat | null>(null);
  const [messages, setMessages] = useState<TeamsMessage[]>([]);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [title, setTitle] = useState('');
  const [category, setCategory] = useState(categoryNames.includes('공통지식') ? '공통지식' : '');
  const [hasMore, setHasMore] = useState(false);
  const [loadingChats, setLoadingChats] = useState(false);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [done, setDone] = useState('');

  const authenticated = authStatus?.authenticated === true && authStatus?.token_expired !== true;
  const tokenExpired = authStatus?.token_expired === true;

  // 헬퍼 실행에 쓸 URL 구성
  // nginx가 /api/* 를 백엔드로 프록시하므로 window.location.origin 을 api_url 로 사용.
  const apiUrl = typeof window !== 'undefined' ? window.location.origin : 'http://localhost:8501';
  // opsnav:// URL — 브라우저 클릭으로 헬퍼 자동 실행 (install_url_handler.py 등록 필요)
  const opsnavUrl = accessToken
    ? `opsnav://teams-login?api_url=${encodeURIComponent(apiUrl)}&jwt=${encodeURIComponent(accessToken)}`
    : '';

  const loadChats = async () => {
    setLoadingChats(true);
    try {
      const result = await listTeamsChats();
      setChats(result.chats);
    } catch (e: any) {
      setError(e.message || '채팅방 목록 조회 실패');
    } finally {
      setLoadingChats(false);
    }
  };

  // 최초 진입 시 인증 상태 조회
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const status = await getTeamsAuthStatus();
        if (!cancelled) setAuthStatus(status);
        if (status.authenticated && !status.token_expired && !cancelled) {
          await loadChats();
        }
      } catch (e: any) {
        if (!cancelled) setError(e.message || '인증 상태 조회 실패');
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 인증 안 됐을 때 2초마다 상태 폴링 — 헬퍼가 토큰을 POST하면 자동 감지
  useEffect(() => {
    if (authenticated) return;
    let cancelled = false;
    const timer = setInterval(async () => {
      try {
        const status = await getTeamsAuthStatus();
        if (cancelled) return;
        const flippedToAuthed = status.authenticated && !status.token_expired && !authenticated;
        setAuthStatus(status);
        if (flippedToAuthed) {
          await loadChats();
        }
      } catch {
        // 폴링 중 일시 오류는 무시
      }
    }, 2000);
    return () => { cancelled = true; clearInterval(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authenticated]);

  const handleLogout = async () => {
    try {
      await logoutTeams();
      setAuthStatus({ authenticated: false, chat_count: 0, token_expired: null });
      setChats([]);
      setSelectedChat(null);
      setMessages([]);
      setSelectedIds(new Set());
    } catch { /* ignore */ }
  };

  const [downloading, setDownloading] = useState(false);
  const handleDownloadHelper = async () => {
    setDownloading(true);
    setError('');
    try {
      await downloadHelperExe(accessToken);
    } catch (e: any) {
      setError(`헬퍼 다운로드 실패: ${e.message || '알 수 없는 오류'}`);
    } finally {
      setDownloading(false);
    }
  };

  const handleSelectChat = async (chat: TeamsChat) => {
    setSelectedChat(chat);
    setMessages([]);
    setSelectedIds(new Set());
    setLoadingMessages(true);
    setError('');
    try {
      const result = await fetchTeamsMessages(chat.id, { pageSize: 50 });
      setMessages(result.messages);
      setHasMore(result.has_more);
    } catch (e: any) {
      setError(e.message || '메시지 조회 실패');
    } finally {
      setLoadingMessages(false);
    }
  };

  const handleLoadMore = async () => {
    if (!selectedChat || messages.length === 0) return;
    const oldest = messages[0];
    if (!oldest?.time) return;
    setLoadingMessages(true);
    try {
      const result = await fetchTeamsMessages(selectedChat.id, {
        pageSize: 50,
        before: oldest.time,
      });
      // 오래된 메시지를 앞쪽에 추가 (시간순 유지)
      setMessages((prev) => [...result.messages, ...prev]);
      setHasMore(result.has_more);
    } catch (e: any) {
      setError(e.message || '메시지 추가 조회 실패');
    } finally {
      setLoadingMessages(false);
    }
  };

  const toggleMessage = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAll = () => {
    if (selectedIds.size === messages.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(messages.map((m) => m.id)));
    }
  };

  const handleSubmit = async () => {
    if (!selectedChat) return;
    if (selectedIds.size === 0) {
      setError('등록할 메시지를 1개 이상 선택하세요.');
      return;
    }
    const selected = messages.filter((m) => selectedIds.has(m.id));
    const threadTitle = title.trim() || `${selectedChat.label} — ${selected.length}건`;

    setSubmitting(true);
    setError('');
    try {
      const result = await importTeamsThreads(
        namespace,
        [{ title: threadTitle, messages: selected }],
        {
          chatId: selectedChat.id,
          chatLabel: selectedChat.label,
          category,
        },
      );
      setDone(`"${threadTitle}" — 등록을 시작했습니다. 진행 상황은 아래 등록 이력에서 확인하세요.`);
      setSelectedIds(new Set());
      onSuccess({ jobId: result.job_id ?? undefined });
    } catch (e: any) {
      setError(e.message || '등록 실패');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="bg-slate-800/60 rounded-xl border border-indigo-800/40 p-5 space-y-4">
      <h3 className="text-sm font-semibold text-slate-200 flex items-center gap-2">
        <MessageSquare className="w-4 h-4 text-indigo-400" />Teams 메시지 수집
      </h3>

      {/* 인증 섹션 */}
      {authenticated ? (
        <div className="flex items-center gap-3 p-3 bg-slate-900/60 rounded-lg border border-slate-700">
          <CheckCircle className="w-4 h-4 text-emerald-400 shrink-0" />
          <span className="text-xs text-slate-300 flex-1">
            Teams 로그인됨 · 채팅방 {authStatus?.chat_count ?? 0}개 캡처됨
          </span>
          <button onClick={loadChats} disabled={loadingChats}
            className="text-xs text-indigo-400 hover:text-indigo-300 flex items-center gap-1">
            <RefreshCw className="w-3 h-3" /> 새로고침
          </button>
          <button onClick={handleLogout}
            className="text-xs text-rose-400 hover:text-rose-300 flex items-center gap-1">
            <LogOut className="w-3 h-3" /> 로그아웃
          </button>
        </div>
      ) : (
        <div className="p-3 bg-slate-900/60 rounded-lg border border-slate-700 space-y-3">
          <div className="flex items-start gap-2">
            <AlertCircle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
            <div className="text-xs text-slate-300 flex-1">
              {tokenExpired ? 'Teams 세션이 만료되었습니다. ' : 'Teams 토큰이 없습니다. '}
              아래 버튼으로 로그인하세요.
            </div>
          </div>

          {/* 주 액션 1: opsnav:// 링크 → 헬퍼 이미 설치된 경우 */}
          {opsnavUrl ? (
            <a
              href={opsnavUrl}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-medium transition-colors"
            >
              <MessageSquare className="w-4 h-4" />
              Teams 로그인
            </a>
          ) : (
            <div className="text-[11px] text-slate-500">로그인 세션이 없어 실행할 수 없습니다.</div>
          )}

          {/* 주 액션 2: 헬퍼 EXE 다운로드 — 최초 사용자용 */}
          <div className="p-2.5 rounded-lg bg-slate-800/50 border border-slate-700 space-y-2">
            <p className="text-[11px] text-slate-300">
              <span className="font-medium text-slate-200">처음 사용이신가요?</span>{' '}
              아래 버튼으로 헬퍼를 받아 실행하세요. 한 번만 설치하면 됩니다.
            </p>
            <button
              onClick={handleDownloadHelper}
              disabled={downloading}
              className="inline-flex items-center gap-2 px-3 py-1.5 rounded-md bg-slate-700 hover:bg-slate-600 disabled:opacity-50 text-slate-100 text-xs font-medium transition-colors"
            >
              <Download className="w-3.5 h-3.5" />
              {downloading ? '다운로드 중...' : '헬퍼 다운로드 (OpsNavHelper.exe)'}
            </button>
            <p className="text-[10px] text-slate-500 leading-relaxed">
              다운로드한 <code className="bg-slate-900 px-1 rounded">OpsNavHelper.exe</code> 를 더블클릭하면 설치됩니다.
              Python 설치 불필요. Chrome 이 미리 설치돼 있어야 합니다.
            </p>
            <p className="text-[10px] text-slate-500 leading-relaxed">
              <code className="bg-slate-900 px-1 rounded">OpsNavHelper.exe</code> 위치를 옮기거나 이름을 바꾼 경우, 해당 파일을 한 번 더 더블클릭해 주세요.
            </p>
          </div>
        </div>
      )}

      {/* 채팅방 선택 */}
      {authenticated && (
        <div>
          <label className="block text-xs font-medium text-slate-400 mb-1">채팅방</label>
          {loadingChats ? (
            <div className="text-xs text-slate-500 py-3 text-center">채팅방 목록 로딩 중...</div>
          ) : chats.length === 0 ? (
            <div className="text-xs text-slate-500 py-3 text-center">채팅방이 없습니다. 새로고침을 시도하세요.</div>
          ) : (
            <select
              value={selectedChat?.id ?? ''}
              onChange={(e) => {
                const chat = chats.find((c) => c.id === e.target.value);
                if (chat) handleSelectChat(chat);
              }}
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
            >
              <option value="">채팅방을 선택하세요...</option>
              {chats.map((c) => (
                <option key={c.id} value={c.id}>{c.label} ({c.members}명)</option>
              ))}
            </select>
          )}
        </div>
      )}

      {/* 메시지 선택 */}
      {selectedChat && (
        <div className="space-y-2">
          <div className="flex items-center justify-between text-xs">
            <span className="text-slate-400">
              메시지 <span className="text-slate-300 font-medium">{messages.length}</span>건
              {selectedIds.size > 0 && <span className="text-indigo-300"> · {selectedIds.size}건 선택됨</span>}
            </span>
            <div className="flex items-center gap-2">
              {hasMore && (
                <button onClick={handleLoadMore} disabled={loadingMessages}
                  className="text-xs text-indigo-400 hover:text-indigo-300">
                  {loadingMessages ? '로딩...' : '이전 메시지 더보기'}
                </button>
              )}
              <button onClick={toggleAll} className="text-xs text-indigo-400 hover:text-indigo-300">
                {selectedIds.size === messages.length && messages.length > 0 ? '전체 해제' : '전체 선택'}
              </button>
            </div>
          </div>
          <div className="max-h-[28rem] overflow-y-auto rounded-lg border border-slate-700 bg-slate-900/40 px-2 py-2">
            {loadingMessages && messages.length === 0 ? (
              <div className="p-4 text-xs text-slate-500 text-center">메시지 로딩 중...</div>
            ) : messages.length === 0 ? (
              <div className="p-4 text-xs text-slate-500 text-center">메시지가 없습니다.</div>
            ) : (
              messages.map((m, idx) => {
                const selected = selectedIds.has(m.id);
                const curDate = m.date?.slice(0, 10) ?? '';
                const prevDate = idx > 0 ? messages[idx - 1].date?.slice(0, 10) : '';
                const showDateSep = curDate && curDate !== prevDate;
                const timeOnly = m.date?.slice(11, 16) ?? '';
                return (
                  <div key={m.id}>
                    {showDateSep && (
                      <div className="flex items-center gap-3 py-2">
                        <div className="flex-1 h-px bg-slate-700/60" />
                        <span className="text-[10px] font-medium text-slate-500">{curDate}</span>
                        <div className="flex-1 h-px bg-slate-700/60" />
                      </div>
                    )}
                    <div
                      onClick={() => toggleMessage(m.id)}
                      className={`flex gap-3 px-2 py-1.5 rounded-lg cursor-pointer transition-colors ${
                        selected
                          ? 'bg-indigo-600/10 border border-indigo-500/40'
                          : 'border border-transparent hover:bg-slate-800/60'
                      }`}
                    >
                      <div
                        className={`shrink-0 mt-0.5 w-5 h-5 rounded-full flex items-center justify-center transition-colors ${
                          selected
                            ? 'bg-indigo-500 border-0'
                            : 'bg-slate-800 border border-slate-600'
                        }`}
                      >
                        {selected && <Check className="w-3 h-3 text-white" />}
                      </div>
                      <div className="flex-1 min-w-0 space-y-0.5">
                        <div className="flex items-baseline gap-2">
                          <span className="text-[13px] font-semibold text-slate-200 truncate">{m.from}</span>
                          <span className="text-[11px] text-slate-500">{timeOnly}</span>
                          {m.reply_to && (
                            <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                              m.reply_to.type === 'forward'
                                ? 'bg-indigo-500/15 text-indigo-300'
                                : 'bg-slate-700/60 text-slate-400'
                            }`}>
                              {m.reply_to.type === 'forward' ? '전달' : '회신'}
                            </span>
                          )}
                        </div>
                        {m.reply_to?.preview && (
                          <div
                            className={`px-2.5 py-1.5 rounded border-l-2 bg-slate-800/60 ${
                              m.reply_to.type === 'forward' ? 'border-indigo-400' : 'border-slate-500'
                            }`}
                          >
                            {m.reply_to.from && (
                              <div className="text-[11px] font-medium text-slate-400 mb-0.5">
                                {m.reply_to.type === 'forward' ? '전달된 메시지' : m.reply_to.from}
                              </div>
                            )}
                            <div className="text-[12px] text-slate-500 whitespace-pre-wrap break-words line-clamp-2">
                              {m.reply_to.preview}
                            </div>
                          </div>
                        )}
                        <div className="text-[13px] leading-snug text-slate-300 whitespace-pre-wrap break-words">
                          {m.content}
                        </div>
                      </div>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      )}

      {/* 메타 입력 */}
      {selectedChat && selectedIds.size > 0 && (
        <div className="grid grid-cols-2 gap-3">
          <RequiredCategoryField categoryNames={categoryNames} value={category} onChange={setCategory} />
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">문서 제목 (선택)</label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder={`${selectedChat.label} — ${selectedIds.size}건`}
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-indigo-500 placeholder:text-slate-600"
            />
          </div>
        </div>
      )}

      {done && <p className="text-sm text-emerald-400 font-medium">{done}</p>}
      {error && <p className="text-xs text-rose-400">{error}</p>}

      <div className="flex gap-2 justify-end pt-1">
        <Button variant="ghost" size="sm" onClick={onCancel}>취소</Button>
        <Button variant="primary" size="sm" onClick={handleSubmit}
          disabled={!authenticated || selectedIds.size === 0 || submitting || !category || categoryNames.length === 0}>
          {submitting ? '등록 중...' : `${selectedIds.size}건 등록`}
        </Button>
      </div>
    </div>
  );
}
