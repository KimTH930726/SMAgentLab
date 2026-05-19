import { useState, useRef, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Trash2, X, FileText, Upload, Database, List, PenLine, CheckCircle, Clock, AlertCircle, Globe, ChevronDown, ChevronUp, MessageSquare, RefreshCw, LogOut, Download, Check, Search } from 'lucide-react';
import {
  getKnowledge,
  createKnowledge,
  updateKnowledge,
  deleteKnowledge,
  bulkDeleteKnowledge,
  vectorSearchKnowledge,
  bulkCreateKnowledge,
  previewTextSplit,
  previewFileUpload,
  previewUrl,
  previewConfluenceTree,
  importConfluenceBulk,
  previewConfluenceBulk,
  getIngestionJobs,
  type IngestionJob,
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
import type { KnowledgeItem } from '../../types';

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

type IngestMethod = 'file' | 'csv' | 'text' | 'manual' | 'url' | 'teams' | null;

// ── KnowledgeTable (메인) ─────────────────────────────────────────────────────

export function KnowledgeTable() {
  const qc = useQueryClient();
  const { selectedNs, setSelectedNs, canModifyNs, sortedNamespaces } = useNamespaceAccess();

  const [subTab, setSubTab] = useState<'list' | 'ingest'>('list');

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
  });

  const { data: items = [], isLoading, error } = useQuery({
    queryKey: ['knowledge', selectedNs],
    queryFn: () => getKnowledge(selectedNs),
    enabled: !!selectedNs,
    staleTime: 15_000,
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
        category: editForm.category || '',
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
          {categoryNames.length > 0 && (
            <div>
              <label className="text-xs font-medium text-slate-400">업무구분</label>
              {canModifyNs ? (
                <select value={editForm.category} onChange={(e) => setEditForm((f) => ({ ...f, category: e.target.value }))}
                  className="w-full mt-1 bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500">
                  <option value="">없음 (파트 공통)</option>
                  {categoryNames.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              ) : (
                <div className="mt-1 bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300">
                  {editForm.category || <span className="text-slate-500">없음</span>}
                </div>
              )}
            </div>
          )}
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
          {bulkDeleteMutation.error && <p className="text-xs text-rose-400">{String(bulkDeleteMutation.error)}</p>}
          <div className="flex gap-2 justify-end">
            <Button variant="secondary" size="sm" onClick={() => setShowBulkConfirm(false)}>취소</Button>
            <Button variant="danger" size="sm" loading={bulkDeleteMutation.isPending} onClick={() => bulkDeleteMutation.mutate(Array.from(selectedIds))}>삭제</Button>
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

function ChunkReviewModal({ isOpen, onClose, chunks, onConfirm, loading, sourceName }: {
  isOpen: boolean;
  onClose: () => void;
  chunks: ReviewChunk[];
  onConfirm: (selected: ReviewChunk[]) => Promise<void>;
  loading: boolean;
  sourceName: string;
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
          <Button variant="primary" size="sm" loading={loading} disabled={selectedCount === 0}
            onClick={() => onConfirm(rows.filter(r => r.selected))}>
            <CheckCircle className="w-3.5 h-3.5" />선택 항목 등록 ({selectedCount}건)
          </Button>
        </div>
      </div>
    </Modal>
  );
}


// ── 서브탭 2: 지식 등록 ───────────────────────────────────────────────────────

function IngestTab({ namespace, categoryNames, canModify, jobs, onSuccess, onGoToList }: {
  namespace: string;
  categoryNames: string[];
  canModify: boolean;
  jobs: IngestionJob[];
  onSuccess: () => void;
  onGoToList: () => void;
}) {
  const [activeMethod, setActiveMethod] = useState<IngestMethod>(null);

  if (!namespace) {
    return <div className="text-center py-16 text-slate-500">파트를 선택하세요.</div>;
  }
  if (!canModify) {
    return <div className="text-center py-16 text-slate-500">이 파트의 지식 등록 권한이 없습니다.</div>;
  }

  const methods: { id: IngestMethod; icon: React.ReactNode; title: string; desc: string; badge?: string }[] = [
    { id: 'file', icon: <Upload className="w-6 h-6" />, title: '파일 업로드', desc: 'PDF · Markdown · TXT 파일을 업로드하면 자동으로 파싱·청킹합니다.', badge: 'AI 분석 지원' },
    { id: 'csv', icon: <Database className="w-6 h-6" />, title: 'CSV 임포트', desc: 'CSV 파일의 컬럼을 매핑하여 여러 건을 한 번에 등록합니다.' },
    { id: 'text', icon: <FileText className="w-6 h-6" />, title: '대량 텍스트', desc: '텍스트를 붙여넣으면 헤더·단락 기준으로 자동 분할해 등록합니다.' },
    { id: 'manual', icon: <PenLine className="w-6 h-6" />, title: '직접 입력', desc: '단건 지식을 직접 작성하여 등록합니다.' },
    { id: 'url', icon: <Globe className="w-6 h-6" />, title: 'URL / Confluence', desc: '웹 페이지 또는 Confluence 페이지 URL을 입력하면 내용을 자동 수집합니다.', badge: 'Confluence 지원' },
    { id: 'teams', icon: <MessageSquare className="w-6 h-6" />, title: 'Teams 메시지', desc: 'Teams 채팅방에 로그인하여 메시지를 선택적으로 수집합니다. 토큰은 서버 메모리에만 저장됩니다.' },
  ];

  return (
    <div className="space-y-5">
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
          onSuccess={() => { onSuccess(); onGoToList(); }} onCancel={() => setActiveMethod(null)} />
      )}
      {activeMethod === 'csv' && (
        <CsvImportForm namespace={namespace} categoryNames={categoryNames}
          onSuccess={() => { onSuccess(); onGoToList(); }} onCancel={() => setActiveMethod(null)} />
      )}
      {activeMethod === 'text' && (
        <TextSplitForm namespace={namespace} categoryNames={categoryNames}
          onSuccess={() => { onSuccess(); onGoToList(); }} onCancel={() => setActiveMethod(null)} />
      )}
      {activeMethod === 'manual' && (
        <ManualForm namespace={namespace} categoryNames={categoryNames}
          onSuccess={() => { onSuccess(); onGoToList(); }} onCancel={() => setActiveMethod(null)} />
      )}
      {activeMethod === 'url' && (
        <UrlForm namespace={namespace} categoryNames={categoryNames}
          onSuccess={() => { onSuccess(); onGoToList(); }} onCancel={() => setActiveMethod(null)} />
      )}
      {activeMethod === 'teams' && (
        <TeamsForm namespace={namespace} categoryNames={categoryNames}
          onSuccess={() => { onSuccess(); onGoToList(); }} onCancel={() => setActiveMethod(null)} />
      )}

      {/* 인제스천 작업 이력 */}
      {jobs.length > 0 && (
        <div>
          <h3 className="text-sm font-medium text-slate-400 mb-2">등록 이력</h3>
          <div className="rounded-xl border border-slate-700 overflow-hidden divide-y divide-slate-700/60">
            {jobs.slice(0, 10).map((j: IngestionJob) => (
              <div key={j.id} className="flex items-center gap-3 px-4 py-2.5 text-xs">
                <span className={`flex items-center gap-1 px-1.5 py-0.5 rounded font-medium flex-shrink-0 ${
                  j.status === 'completed' ? 'bg-emerald-900/30 text-emerald-400' :
                  j.status === 'failed' ? 'bg-rose-900/30 text-rose-400' :
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
                {j.auto_glossary > 0 && <span className="text-violet-400 flex-shrink-0">용어 +{j.auto_glossary}</span>}
                {j.auto_fewshot > 0 && <span className="text-emerald-400 flex-shrink-0">Q&A +{j.auto_fewshot}</span>}
                <span className="text-slate-600 flex-shrink-0">{new Date(j.created_at).toLocaleDateString('ko-KR')}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}


// ── 파일 업로드 인라인 폼 ────────────────────────────────────────────────────

function FileUploadForm({ namespace, categoryNames, onSuccess, onCancel }: {
  namespace: string; categoryNames: string[];
  onSuccess: () => void; onCancel: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [category, setCategory] = useState('');
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
      } else {
        setError(msg);
      }
    }
    finally { setPreviewing(false); }
  };

  const handleConfirm = async (selected: ReviewChunk[]) => {
    setLoading(true); setError('');
    try {
      const items = selected.map(c => ({ content: c.text, category: category || undefined }));
      const result = await bulkCreateKnowledge(namespace, items, file!.name, 'file_upload');
      setDone(`${result.created}건 등록 완료`);
      setShowReview(false);
      onSuccess();
    } catch (e: any) { setError(e.message || '오류 발생'); }
    finally { setLoading(false); }
  };

  return (
    <div className="bg-slate-800/60 rounded-xl border border-indigo-800/40 p-5 space-y-4">
      <h3 className="text-sm font-semibold text-slate-200 flex items-center gap-2"><Upload className="w-4 h-4 text-indigo-400" />파일 업로드</h3>

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
        {categoryNames.length > 0 && (
          <div>
            <label className="text-[10px] text-slate-500 mb-1 block">업무구분</label>
            <select value={category} onChange={(e) => setCategory(e.target.value)}
              className="bg-slate-900 border border-slate-600 rounded-lg px-2 py-1.5 text-xs text-slate-300">
              <option value="">자동 / 없음</option>
              {categoryNames.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
        )}
      </div>

      {done && <p className="text-sm text-emerald-400 font-medium">{done}</p>}
      {error && <p className="text-xs text-rose-400">{error}</p>}

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
      />
    </div>
  );
}


// ── CSV 임포트 인라인 폼 ─────────────────────────────────────────────────────

function CsvImportForm({ namespace, categoryNames, onSuccess, onCancel }: {
  namespace: string; categoryNames: string[];
  onSuccess: () => void; onCancel: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [headers, setHeaders] = useState<string[]>([]);
  const [previewRows, setPreviewRows] = useState<Record<string, string>[]>([]);
  const [allRows, setAllRows] = useState<Record<string, string>[]>([]);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [category, setCategory] = useState('');
  const [reviewChunks, setReviewChunks] = useState<ReviewChunk[]>([]);
  const [showReview, setShowReview] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [done, setDone] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);

  const handleFile = (f: File | null) => {
    if (!f) return;
    setFile(f); setError(''); setDone(''); setAllRows([]); setHeaders([]);
    const reader = new FileReader();
    reader.onload = (e) => {
      try {
        const text = e.target?.result as string;
        const lines = text.split('\n').filter(l => l.trim());
        if (lines.length < 2) { setError('데이터가 부족합니다.'); return; }
        const hdrs = lines[0].split(',').map(h => h.trim().replace(/^"|"$/g, ''));
        const rows = lines.slice(1).map(line => {
          const vals = line.split(',').map(v => v.trim().replace(/^"|"$/g, ''));
          const obj: Record<string, string> = {};
          hdrs.forEach((h, i) => { obj[h] = vals[i] || ''; });
          return obj;
        });
        setHeaders(hdrs);
        setAllRows(rows);
        setPreviewRows(rows.slice(0, 5));
        const contentCol = hdrs.find(h => /content|내용|설명|description|text/i.test(h));
        if (contentCol) setMapping(prev => ({ ...prev, content: contentCol }));
      } catch { setError('CSV 파싱 실패'); }
    };
    reader.readAsText(f);
  };

  const handleOpenReview = () => {
    if (!mapping.content) { setError('내용 컬럼을 선택해주세요.'); return; }
    setError('');
    const validRows = allRows.filter(row => row[mapping.content]?.trim());
    const chunks: ReviewChunk[] = validRows.map((row, i) => ({
      idx: i,
      text: row[mapping.content],
      title: mapping.category ? row[mapping.category] || null : null,
      selected: true,
    }));
    setReviewChunks(chunks);
    setShowReview(true);
  };

  const handleConfirm = async (selected: ReviewChunk[]) => {
    setLoading(true); setError('');
    try {
      const validRows = allRows.filter(row => row[mapping.content]?.trim());
      const items = selected.map(chunk => {
        const row = validRows[chunk.idx] ?? {};
        return {
          content: row[mapping.content] || chunk.text,
          category: mapping.category ? row[mapping.category] || category || undefined : category || undefined,
          container_name: mapping.container_name ? row[mapping.container_name] || undefined : undefined,
          query_template: mapping.query_template ? row[mapping.query_template] || undefined : undefined,
        };
      });
      const result = await bulkCreateKnowledge(namespace, items, file!.name, 'csv_import');
      setDone(`${result.created}건 등록 완료`);
      setShowReview(false);
      onSuccess();
    } catch (e: any) { setError(e.message || '오류 발생'); }
    finally { setLoading(false); }
  };

  return (
    <div className="bg-slate-800/60 rounded-xl border border-indigo-800/40 p-5 space-y-4">
      <h3 className="text-sm font-semibold text-slate-200 flex items-center gap-2"><Database className="w-4 h-4 text-indigo-400" />CSV 임포트</h3>

      <div className="border-2 border-dashed border-slate-600 rounded-xl p-6 text-center cursor-pointer hover:border-indigo-500 transition-colors"
        onClick={() => fileRef.current?.click()}>
        <input ref={fileRef} type="file" accept=".csv,.tsv" className="hidden"
          onChange={(e) => handleFile(e.target.files?.[0] ?? null)} />
        {file
          ? <p className="text-sm text-slate-300">{file.name} ({(file.size / 1024).toFixed(1)} KB) — {allRows.length}행</p>
          : <p className="text-sm text-slate-400">CSV 파일을 선택하세요</p>}
      </div>

      {headers.length > 0 && (
        <>
          <div className="grid grid-cols-2 gap-3">
            {['content', 'category', 'container_name', 'query_template'].map(field => (
              <div key={field}>
                <label className="text-[10px] text-slate-500 mb-1 block">
                  {field === 'content' ? '내용 컬럼 (필수)' : field === 'category' ? '업무구분 컬럼' : field === 'container_name' ? '컨테이너명 컬럼' : '쿼리 컬럼'}
                </label>
                <select value={mapping[field] || ''} onChange={(e) => setMapping(p => ({ ...p, [field]: e.target.value }))}
                  className="w-full bg-slate-900 border border-slate-600 rounded-lg px-2 py-1.5 text-xs text-slate-300">
                  <option value="">매핑 안함</option>
                  {headers.map(h => <option key={h} value={h}>{h}</option>)}
                </select>
              </div>
            ))}
          </div>
          {categoryNames.length > 0 && !mapping.category && (
            <div>
              <label className="text-[10px] text-slate-500 mb-1 block">기본 업무구분 (CSV에 없을 때)</label>
              <select value={category} onChange={(e) => setCategory(e.target.value)}
                className="w-full bg-slate-900 border border-slate-600 rounded-lg px-2 py-1.5 text-xs text-slate-300">
                <option value="">없음</option>
                {categoryNames.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
          )}
          <div>
            <p className="text-xs text-slate-500 mb-1">미리보기 (처음 5행)</p>
            <div className="overflow-x-auto rounded-lg border border-slate-700 max-h-40">
              <table className="w-full text-xs">
                <thead><tr className="bg-slate-800">
                  {headers.map(h => <th key={h} className="px-2 py-1 text-left text-slate-400 font-medium">{h}</th>)}
                </tr></thead>
                <tbody>{previewRows.map((r, i) => (
                  <tr key={i} className="border-t border-slate-700/60">
                    {headers.map(h => <td key={h} className="px-2 py-1 text-slate-300 truncate max-w-[150px]">{r[h]}</td>)}
                  </tr>
                ))}</tbody>
              </table>
            </div>
          </div>
        </>
      )}

      {done && <p className="text-sm text-emerald-400 font-medium">{done}</p>}
      {error && <p className="text-xs text-rose-400">{error}</p>}

      <div className="flex gap-2 justify-end pt-1">
        <Button variant="ghost" size="sm" onClick={onCancel}>취소</Button>
        <Button variant="primary" size="sm" onClick={handleOpenReview} disabled={loading || !file || !mapping.content}>
          청크 검토 & 등록 ({allRows.filter(r => r[mapping.content]?.trim()).length}행)
        </Button>
      </div>

      <ChunkReviewModal
        isOpen={showReview}
        onClose={() => setShowReview(false)}
        chunks={reviewChunks}
        onConfirm={handleConfirm}
        loading={loading}
        sourceName={file?.name ?? ''}
      />
    </div>
  );
}


// ── 대량 텍스트 인라인 폼 ────────────────────────────────────────────────────

function TextSplitForm({ namespace, categoryNames, onSuccess, onCancel }: {
  namespace: string; categoryNames: string[];
  onSuccess: () => void; onCancel: () => void;
}) {
  const [text, setText] = useState('');
  const [category, setCategory] = useState('');
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
      const items = selected.map(c => ({ content: c.text, category: category || undefined }));
      const result = await bulkCreateKnowledge(namespace, items, '텍스트 직접입력', 'paste_split');
      setDone(`${selected.length}개 청크 → ${result.created}건 등록 완료`);
      setShowReview(false);
      onSuccess();
    } catch (e: any) { setError(e.message || '오류 발생'); }
    finally { setLoading(false); }
  };

  return (
    <div className="bg-slate-800/60 rounded-xl border border-indigo-800/40 p-5 space-y-4">
      <h3 className="text-sm font-semibold text-slate-200 flex items-center gap-2"><FileText className="w-4 h-4 text-indigo-400" />대량 텍스트 등록</h3>

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
        {categoryNames.length > 0 && (
          <div>
            <label className="text-[10px] text-slate-500 mb-1 block">업무구분</label>
            <select value={category} onChange={(e) => setCategory(e.target.value)}
              className="bg-slate-900 border border-slate-600 rounded-lg px-2 py-1.5 text-xs text-slate-300">
              <option value="">없음</option>
              {categoryNames.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
        )}
      </div>

      {done && <p className="text-sm text-emerald-400 font-medium">{done}</p>}
      {error && <p className="text-xs text-rose-400">{error}</p>}

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
      />
    </div>
  );
}


// ── 직접 입력 인라인 폼 ──────────────────────────────────────────────────────

function ManualForm({ namespace, categoryNames, onSuccess, onCancel }: {
  namespace: string; categoryNames: string[];
  onSuccess: () => void; onCancel: () => void;
}) {
  const qc = useQueryClient();
  const [form, setForm] = useState<KnowledgeFormData>(defaultForm);
  const [done, setDone] = useState('');

  const createMutation = useMutation({
    mutationFn: () =>
      createKnowledge({
        namespace,
        container_name: form.container_names.join(', '),
        target_tables: form.target_tables,
        content: form.content,
        query_template: form.query_template || null,
        base_weight: form.base_weight,
        category: form.category || null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['knowledge', namespace] });
      qc.invalidateQueries({ queryKey: ['stats-ns', namespace] });
      setDone('등록 완료');
      setForm(defaultForm);
      onSuccess();
    },
  });

  return (
    <div className="bg-slate-800/60 rounded-xl border border-indigo-800/40 p-5 space-y-3">
      <h3 className="text-sm font-semibold text-slate-200 flex items-center gap-2"><PenLine className="w-4 h-4 text-indigo-400" />직접 입력</h3>

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
      {categoryNames.length > 0 && (
        <div>
          <label className="block text-xs font-medium text-slate-400 mb-1">업무구분</label>
          <select value={form.category} onChange={(e) => setForm((f) => ({ ...f, category: e.target.value }))}
            className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500">
            <option value="">없음 (파트 공통)</option>
            {categoryNames.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>
      )}
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

      {done && <p className="text-sm text-emerald-400 font-medium">{done}</p>}
      {createMutation.isError && <p className="text-xs text-rose-400">{String(createMutation.error)}</p>}

      <div className="flex gap-2 justify-end pt-1">
        <Button variant="ghost" size="sm" onClick={onCancel}>취소</Button>
        <Button variant="primary" size="sm"
          loading={createMutation.isPending}
          onClick={() => createMutation.mutate()}
          disabled={createMutation.isPending || form.container_names.length === 0 || !form.content.trim()}>
          추가
        </Button>
      </div>
    </div>
  );
}


// ── URL / Confluence 인라인 폼 ────────────────────────────────────────────────

function UrlForm({ namespace, categoryNames, onSuccess, onCancel }: {
  namespace: string; categoryNames: string[];
  onSuccess: () => void; onCancel: () => void;
}) {
  const { user, updateUser } = useAuthStore();
  const [url, setUrl] = useState('');
  const [category, setCategory] = useState('');
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
      const items = selected.map(c => ({ content: c.text, category: category || undefined }));
      const srcName = sourceMeta?.name ?? url;
      const srcType = sourceMeta?.type ?? (isConfluence ? 'confluence' : 'web');
      const result = await bulkCreateKnowledge(namespace, items, srcName, srcType);
      setDone(`"${srcName}" — ${result.created}건 등록 완료`);
      setShowReview(false);
      onSuccess();
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

      {categoryNames.length > 0 && (
        <div>
          <label className="block text-xs font-medium text-slate-400 mb-1">업무구분</label>
          <select value={category} onChange={(e) => setCategory(e.target.value)}
            className="w-full bg-slate-900 border border-slate-600 rounded-lg px-2 py-1.5 text-xs text-slate-300">
            <option value="">없음 (파트 공통)</option>
            {categoryNames.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>
      )}

      {done && <p className="text-sm text-emerald-400 font-medium">{done}</p>}
      {error && <p className="text-xs text-rose-400">{error}</p>}

      <div className="flex gap-2 justify-end pt-1">
        <Button variant="ghost" size="sm" onClick={onCancel}>취소</Button>
        <Button variant="primary" size="sm" onClick={handleOpenReview}
          disabled={!url.trim() || previewing || treeLoading}>
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
  onSuccess: () => void; onCancel: () => void;
}) {
  const { accessToken } = useAuthStore();
  const [authStatus, setAuthStatus] = useState<TeamsAuthStatus | null>(null);
  const [chats, setChats] = useState<TeamsChat[]>([]);
  const [selectedChat, setSelectedChat] = useState<TeamsChat | null>(null);
  const [messages, setMessages] = useState<TeamsMessage[]>([]);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [title, setTitle] = useState('');
  const [category, setCategory] = useState('');
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
          category: category || undefined,
        },
      );
      setDone(`"${threadTitle}" — ${result.created}건 등록 완료`);
      setSelectedIds(new Set());
      onSuccess();
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
          {categoryNames.length > 0 && (
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">업무구분</label>
              <select value={category} onChange={(e) => setCategory(e.target.value)}
                className="w-full bg-slate-900 border border-slate-600 rounded-lg px-2 py-1.5 text-xs text-slate-300">
                <option value="">없음</option>
                {categoryNames.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
          )}
        </div>
      )}

      {done && <p className="text-sm text-emerald-400 font-medium">{done}</p>}
      {error && <p className="text-xs text-rose-400">{error}</p>}

      <div className="flex gap-2 justify-end pt-1">
        <Button variant="ghost" size="sm" onClick={onCancel}>취소</Button>
        <Button variant="primary" size="sm" onClick={handleSubmit}
          disabled={!authenticated || selectedIds.size === 0 || submitting}>
          {submitting ? '등록 중...' : `${selectedIds.size}건 등록`}
        </Button>
      </div>
    </div>
  );
}
