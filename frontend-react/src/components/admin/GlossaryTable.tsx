import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Edit2, Trash2, X, ChevronDown, ChevronUp, BookOpen, Wand2 } from 'lucide-react';
import { getGlossary, createGlossaryItem, updateGlossaryItem, deleteGlossaryItem, suggestGlossaryTerms, applyGlossarySuggestion } from '../../api/knowledge';
import { useNamespaceAccess } from '../../utils/useNamespaceAccess';
import { Button } from '../ui/Button';
import { Modal } from '../ui/Modal';
import { Badge } from '../ui/Badge';
import { PaginationInfo, PaginationNav, useClientPaging } from '../ui/Pagination';
import type { GlossaryItem } from '../../types';

interface GlossaryFormData { term: string; description: string; }
const defaultForm: GlossaryFormData = { term: '', description: '' };

interface GlossarySuggestion { term: string; description: string; }

export function GlossaryTable() {
  const qc = useQueryClient();
  const { selectedNs, setSelectedNs, canModifyNs, sortedNamespaces, user } = useNamespaceAccess();
  const [expandedId, setExpandedId] = useState<number | null>(null);

  // AI 추천 관련 상태
  const [showSuggest, setShowSuggest] = useState(false);
  const [suggestions, setSuggestions] = useState<GlossarySuggestion[]>([]);
  const [suggestMessage, setSuggestMessage] = useState('');
  const [appliedTerms, setAppliedTerms] = useState<Set<string>>(new Set());
  const [suggestLimit, setSuggestLimit] = useState(50);

  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(30);

  const [editingId, setEditingId] = useState<number | null>(null);
  const [editForm, setEditForm] = useState<GlossaryFormData>(defaultForm);
  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState<GlossaryFormData>(defaultForm);
  const [deleteTarget, setDeleteTarget] = useState<number | null>(null);

  const { data: items = [], isLoading, error } = useQuery({
    queryKey: ['glossary', selectedNs],
    queryFn: () => getGlossary(selectedNs),
    enabled: !!selectedNs,
    staleTime: 15_000,
    refetchOnMount: 'always',
  });

  const createMutation = useMutation({
    mutationFn: () => createGlossaryItem({ namespace: selectedNs, term: createForm.term.trim(), description: createForm.description.trim() }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['glossary', selectedNs] }); qc.invalidateQueries({ queryKey: ['stats-ns', selectedNs] }); setShowCreate(false); setCreateForm(defaultForm); },
  });

  const updateMutation = useMutation({
    mutationFn: (id: number) => updateGlossaryItem(id, { term: editForm.term.trim(), description: editForm.description.trim() }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['glossary', selectedNs] }); setEditingId(null); },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteGlossaryItem(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['glossary', selectedNs] }); qc.invalidateQueries({ queryKey: ['stats-ns', selectedNs] }); setDeleteTarget(null); },
  });

  const suggestMutation = useMutation({
    mutationFn: () => suggestGlossaryTerms(selectedNs, suggestLimit),
    onSuccess: (data) => {
      setSuggestions(data.suggestions);
      setSuggestMessage(data.message);
      setAppliedTerms(new Set());
    },
  });

  const applyMutation = useMutation({
    mutationFn: ({ term, description }: { term: string; description: string }) =>
      applyGlossarySuggestion(selectedNs, term, description),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: ['glossary', selectedNs] });
      qc.invalidateQueries({ queryKey: ['stats-ns', selectedNs] });
      setAppliedTerms((prev) => new Set([...prev, variables.term]));
    },
  });

  const { totalPages, totalItems, slice } = useClientPaging(items, pageSize);
  const pagedItems = slice(page);

  useEffect(() => { setPage(1); }, [pageSize, selectedNs]);

  const startEdit = (item: GlossaryItem) => {
    setEditingId(item.id);
    setEditForm({ term: item.term, description: item.description });
    setExpandedId(item.id);
  };

  const handleOpenSuggest = () => {
    setSuggestions([]);
    setSuggestMessage('');
    setAppliedTerms(new Set());
    setShowSuggest(true);
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-200">
          용어집
          {selectedNs && <span className="text-sm font-normal text-slate-500 ml-2">({selectedNs})</span>}
        </h2>
        <div className="flex items-center gap-2">
          {selectedNs && canModifyNs && (
            <Button variant="secondary" size="sm" onClick={handleOpenSuggest}>
              <Wand2 className="w-4 h-4" />AI 용어 추천
            </Button>
          )}
          <Button variant="primary" size="sm" onClick={() => setShowCreate(true)} disabled={!selectedNs || !canModifyNs}>
            <Plus className="w-4 h-4" />용어 추가
          </Button>
        </div>
      </div>

      {/* Namespace selector */}
      <div>
        <label className="block text-xs font-medium text-slate-400 mb-1.5">파트</label>
        <select
          value={selectedNs}
          onChange={(e) => { setSelectedNs(e.target.value); setEditingId(null); setExpandedId(null); }}
          className="w-64 bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
        >
          <option value="">선택...</option>
          {sortedNamespaces.map((ns) => <option key={ns} value={ns}>{ns}</option>)}
        </select>
      </div>

      {!selectedNs && <div className="text-center py-10 text-slate-500">파트를 선택하세요.</div>}
      {selectedNs && isLoading && <div className="text-center py-10 text-slate-500 animate-pulse">로딩 중...</div>}
      {selectedNs && error && <div className="text-center py-10 text-rose-400">오류가 발생했습니다.</div>}

      {selectedNs && !isLoading && (
        <div className="space-y-2">
          <PaginationInfo totalItems={totalItems} pageSize={pageSize} onPageSizeChange={setPageSize} />
          {pagedItems.map((item) => (
            <div key={item.id} className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
              {/* Card header */}
              <div
                className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-slate-700/50 transition-colors"
                onClick={() => setExpandedId(expandedId === item.id ? null : item.id)}
              >
                <BookOpen className="w-4 h-4 text-indigo-400 flex-shrink-0" />
                <div className="flex-1 min-w-0">
                  <span className="text-sm font-medium text-slate-200">{item.term}</span>
                  <p className="text-xs text-slate-500 mt-0.5 truncate">{item.description}</p>
                </div>
                {item.created_by_username && (
                  <span className="text-xs text-slate-500">{item.created_by_username}</span>
                )}
                {item.created_by_part && (
                  <Badge color={canModifyNs ? 'emerald' : 'slate'}>{item.created_by_part}</Badge>
                )}
                {expandedId === item.id ? (
                  <ChevronUp className="w-4 h-4 text-slate-400" />
                ) : (
                  <ChevronDown className="w-4 h-4 text-slate-400" />
                )}
              </div>

              {/* Expanded content */}
              {expandedId === item.id && (
                <div className="border-t border-slate-700 px-4 py-4">
                  {editingId === item.id ? (
                    <div className="space-y-3">
                      <div>
                        <label className="block text-xs font-medium text-slate-400 mb-1">용어</label>
                        <input type="text" value={editForm.term} onChange={(e) => setEditForm((f) => ({ ...f, term: e.target.value }))}
                          className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500" />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-slate-400 mb-1">설명</label>
                        <textarea rows={3} value={editForm.description} onChange={(e) => setEditForm((f) => ({ ...f, description: e.target.value }))}
                          className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 resize-none" />
                      </div>
                      {updateMutation.error && (
                        <p className="text-xs text-rose-400">{String(updateMutation.error)}</p>
                      )}
                      <div className="flex gap-2 justify-end">
                        <Button variant="ghost" size="sm" onClick={() => setEditingId(null)}><X className="w-3.5 h-3.5" />취소</Button>
                        <Button variant="primary" size="sm" loading={updateMutation.isPending} onClick={() => updateMutation.mutate(item.id)}>저장</Button>
                      </div>
                    </div>
                  ) : (
                    <div className="space-y-3">
                      {canModifyNs && (
                        <div className="flex gap-2 justify-end pb-3 border-b border-slate-700">
                          <Button variant="secondary" size="sm" onClick={() => startEdit(item)}><Edit2 className="w-3.5 h-3.5" />수정</Button>
                          <Button variant="danger" size="sm" onClick={() => setDeleteTarget(item.id)}><Trash2 className="w-3.5 h-3.5" />삭제</Button>
                        </div>
                      )}
                      <div>
                        <p className="text-xs text-slate-500 mb-1">설명</p>
                        <p className="text-sm text-slate-300 leading-relaxed">{item.description}</p>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
          {items.length === 0 && <div className="text-center py-10 text-slate-500">용어 항목이 없습니다.</div>}
          <PaginationNav page={page} totalPages={totalPages} onPageChange={setPage} />
        </div>
      )}

      <Modal isOpen={showCreate} onClose={() => setShowCreate(false)} title="용어 추가">
        <div className="space-y-3">
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">용어 <span className="text-rose-400">*</span></label>
            <input type="text" value={createForm.term} onChange={(e) => setCreateForm((f) => ({ ...f, term: e.target.value }))}
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500" />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">설명 <span className="text-rose-400">*</span></label>
            <textarea rows={3} value={createForm.description} onChange={(e) => setCreateForm((f) => ({ ...f, description: e.target.value }))}
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 resize-none" />
          </div>
          {createMutation.error && (
            <p className="text-xs text-rose-400">{String(createMutation.error)}</p>
          )}
          <div className="flex gap-2 justify-end pt-2">
            <Button variant="secondary" size="sm" onClick={() => setShowCreate(false)}>취소</Button>
            <Button variant="primary" size="sm" loading={createMutation.isPending} onClick={() => createMutation.mutate()} disabled={!createForm.term.trim() || !createForm.description.trim()}>추가</Button>
          </div>
        </div>
      </Modal>

      <Modal isOpen={deleteTarget !== null} onClose={() => setDeleteTarget(null)} title="용어 삭제">
        <div className="space-y-4">
          <p className="text-sm text-slate-300">이 용어를 삭제하시겠습니까?</p>
          {deleteMutation.error && (
            <p className="text-xs text-rose-400">{String(deleteMutation.error)}</p>
          )}
          <div className="flex gap-2 justify-end">
            <Button variant="secondary" size="sm" onClick={() => setDeleteTarget(null)}>취소</Button>
            <Button variant="danger" size="sm" loading={deleteMutation.isPending} onClick={() => deleteTarget !== null && deleteMutation.mutate(deleteTarget)}>삭제</Button>
          </div>
        </div>
      </Modal>

      {/* AI 용어 추천 Modal */}
      <Modal isOpen={showSuggest} onClose={() => setShowSuggest(false)} title="AI 용어 추천" maxWidth="max-w-2xl">
        <div className="space-y-4">
          <div className="flex items-center justify-between gap-4">
            <p className="text-xs text-slate-400">미매핑 질문을 분석해 업무 용어를 자동 추출합니다.</p>
            <div className="flex items-center gap-2 flex-shrink-0">
              <label className="text-xs text-slate-400 whitespace-nowrap">최대 질문 수</label>
              <input
                type="number"
                min={5}
                max={200}
                value={suggestLimit}
                onChange={(e) => setSuggestLimit(Math.min(200, Math.max(5, Number(e.target.value))))}
                className="w-16 bg-slate-700 border border-slate-600 rounded px-2 py-1 text-xs text-slate-200 focus:outline-none focus:border-indigo-500"
              />
              <Button
                variant="primary" size="sm"
                loading={suggestMutation.isPending}
                onClick={() => suggestMutation.mutate()}
              >
                <Wand2 className="w-3.5 h-3.5" />분석 시작
              </Button>
            </div>
          </div>

          {suggestMutation.isPending && (
            <div className="flex items-center gap-2 py-4 text-slate-400 text-sm">
              <div className="w-4 h-4 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
              AI가 미매핑 질문을 분석 중...
            </div>
          )}

          {suggestMessage && !suggestMutation.isPending && (
            <p className="text-xs text-indigo-400 bg-indigo-900/20 border border-indigo-700/30 rounded-lg px-3 py-2">
              {suggestMessage}
            </p>
          )}

          {suggestMutation.isError && (
            <p className="text-xs text-rose-400">{String(suggestMutation.error)}</p>
          )}

          {suggestions.length > 0 && (
            <div className="space-y-2 max-h-96 overflow-y-auto">
              {suggestions.map((s) => {
                const isApplied = appliedTerms.has(s.term);
                return (
                  <div
                    key={s.term}
                    className={`flex items-start gap-3 bg-slate-800 border rounded-xl px-4 py-3 transition-colors ${
                      isApplied ? 'border-emerald-700/50 opacity-60' : 'border-slate-700'
                    }`}
                  >
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-slate-200">{s.term}</p>
                      <p className="text-xs text-slate-400 mt-0.5 leading-relaxed">{s.description}</p>
                    </div>
                    <Button
                      variant={isApplied ? 'secondary' : 'primary'}
                      size="sm"
                      disabled={isApplied || applyMutation.isPending}
                      onClick={() => !isApplied && applyMutation.mutate({ term: s.term, description: s.description })}
                    >
                      {isApplied ? '등록됨' : '등록'}
                    </Button>
                  </div>
                );
              })}
            </div>
          )}

          {suggestions.length === 0 && suggestMessage && !suggestMutation.isPending && (
            <div className="text-center py-6 text-slate-500 text-sm">
              추출된 용어가 없습니다.
            </div>
          )}

          <div className="flex justify-end pt-2">
            <Button variant="ghost" size="sm" onClick={() => setShowSuggest(false)}>닫기</Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
