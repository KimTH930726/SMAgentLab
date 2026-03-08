import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Edit2, Trash2, ChevronDown, ChevronUp, X, Zap } from 'lucide-react';
import { getFewshots, createFewshot, updateFewshot, deleteFewshot } from '../../api/fewshots';
import { getNamespaces, getNamespacesDetail } from '../../api/namespaces';
import { useAppStore } from '../../store/useAppStore';
import { useAuthStore } from '../../store/useAuthStore';
import { Button } from '../ui/Button';
import { Modal } from '../ui/Modal';
import { Badge } from '../ui/Badge';
import type { FewshotItem } from '../../types';

interface FewshotFormData {
  question: string;
  answer: string;
  knowledge_id: string;
}

const defaultForm: FewshotFormData = { question: '', answer: '', knowledge_id: '' };

export function FewshotTable() {
  const qc = useQueryClient();
  const { namespace: storeNamespace } = useAppStore();
  const user = useAuthStore((s) => s.user);
  const [selectedNs, setSelectedNs] = useState(storeNamespace || '');

  const { data: nsDetails = [] } = useQuery({
    queryKey: ['namespaces-detail'],
    queryFn: getNamespacesDetail,
    staleTime: 30_000,
  });

  const nsOwnerPart = nsDetails.find((n) => n.name === selectedNs)?.owner_part;
  // owner_part 없으면 admin만, 있으면 같은 파트 or admin
  const canModifyNs = user?.role === 'admin' || (!!nsOwnerPart && nsOwnerPart === user?.part);

  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editForm, setEditForm] = useState<FewshotFormData>(defaultForm);
  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState<FewshotFormData>(defaultForm);
  const [deleteTarget, setDeleteTarget] = useState<number | null>(null);

  const { data: namespaces = [] } = useQuery({
    queryKey: ['namespaces'],
    queryFn: getNamespaces,
    staleTime: 30_000,
  });

  // 삭제된 네임스페이스 선택 상태 자동 리셋
  useEffect(() => {
    if (selectedNs && namespaces.length > 0 && !namespaces.includes(selectedNs)) {
      setSelectedNs('');
    }
  }, [namespaces, selectedNs]);

  const { data: items = [], isLoading, error } = useQuery({
    queryKey: ['fewshots', selectedNs],
    queryFn: () => getFewshots(selectedNs),
    enabled: !!selectedNs,
    staleTime: 15_000,
  });

  const createMutation = useMutation({
    mutationFn: () =>
      createFewshot({
        namespace: selectedNs,
        question: createForm.question,
        answer: createForm.answer,
        knowledge_id: createForm.knowledge_id ? parseInt(createForm.knowledge_id) : null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['fewshots', selectedNs] });
      setShowCreate(false);
      setCreateForm(defaultForm);
    },
  });

  const updateMutation = useMutation({
    mutationFn: (id: number) =>
      updateFewshot(id, {
        question: editForm.question,
        answer: editForm.answer,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['fewshots', selectedNs] });
      setEditingId(null);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteFewshot(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['fewshots', selectedNs] });
      setDeleteTarget(null);
    },
  });

  const startEdit = (item: FewshotItem) => {
    setEditingId(item.id);
    setEditForm({
      question: item.question,
      answer: item.answer,
      knowledge_id: item.knowledge_id ? String(item.knowledge_id) : '',
    });
    setExpandedId(item.id);
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-slate-200">
            Few-shot 베스트케이스
            {selectedNs && <span className="text-sm font-normal text-slate-500 ml-2">({selectedNs})</span>}
          </h2>
          <p className="text-xs text-slate-500 mt-0.5">
            👍 긍정 피드백 시 자동 등록 — 유사 질문에 LLM 컨텍스트로 삽입됩니다
          </p>
        </div>
        <Button variant="primary" size="sm" onClick={() => setShowCreate(true)} disabled={!selectedNs || !canModifyNs}>
          <Plus className="w-4 h-4" />
          추가
        </Button>
      </div>

      {/* Namespace selector */}
      <div>
        <label className="block text-xs font-medium text-slate-400 mb-1.5">네임스페이스</label>
        <select
          value={selectedNs}
          onChange={(e) => { setSelectedNs(e.target.value); setEditingId(null); setExpandedId(null); }}
          className="w-64 bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
        >
          <option value="">선택...</option>
          {namespaces.map((ns) => <option key={ns} value={ns}>{ns}</option>)}
        </select>
      </div>

      {!selectedNs && (
        <div className="text-center py-10 text-slate-500">네임스페이스를 선택하세요.</div>
      )}
      {selectedNs && isLoading && (
        <div className="text-center py-10 text-slate-500 animate-pulse">로딩 중...</div>
      )}
      {selectedNs && error && (
        <div className="text-center py-10 text-rose-400">오류가 발생했습니다.</div>
      )}

      {selectedNs && !isLoading && (
        <div className="space-y-2">
          {items.map((item) => (
            <div key={item.id} className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
              {/* Card header */}
              <div
                className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-slate-700/50 transition-colors"
                onClick={() => setExpandedId(expandedId === item.id ? null : item.id)}
              >
                <Zap className="w-4 h-4 text-amber-400 flex-shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-slate-200 truncate">{item.question}</p>
                  <p className="text-xs text-slate-500 mt-0.5 truncate">{item.answer.slice(0, 100)}{item.answer.length > 100 ? '...' : ''}</p>
                </div>
                <div className="flex items-center gap-2 flex-shrink-0">
                  {item.created_by_username && (
                    <span className="text-xs text-slate-500">{item.created_by_username}</span>
                  )}
                  {item.created_by_part && (
                    <Badge color={canModifyNs ? 'emerald' : 'slate'}>{item.created_by_part}</Badge>
                  )}
                  <span className="text-xs text-slate-600">
                    {new Date(item.created_at).toLocaleDateString('ko-KR')}
                  </span>
                  {expandedId === item.id ? (
                    <ChevronUp className="w-4 h-4 text-slate-400" />
                  ) : (
                    <ChevronDown className="w-4 h-4 text-slate-400" />
                  )}
                </div>
              </div>

              {/* Expanded content */}
              {expandedId === item.id && (
                <div className="border-t border-slate-700 px-4 py-4">
                  {editingId === item.id ? (
                    <div className="space-y-3">
                      <div>
                        <label className="block text-xs font-medium text-slate-400 mb-1">질문</label>
                        <textarea
                          rows={2}
                          value={editForm.question}
                          onChange={(e) => setEditForm((f) => ({ ...f, question: e.target.value }))}
                          className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 resize-none"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-slate-400 mb-1">답변</label>
                        <textarea
                          rows={5}
                          value={editForm.answer}
                          onChange={(e) => setEditForm((f) => ({ ...f, answer: e.target.value }))}
                          className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 resize-none"
                        />
                      </div>
                      <div className="flex gap-2 justify-end">
                        <Button variant="ghost" size="sm" onClick={() => setEditingId(null)}>
                          <X className="w-3.5 h-3.5" />취소
                        </Button>
                        <Button
                          variant="primary" size="sm"
                          loading={updateMutation.isPending}
                          onClick={() => updateMutation.mutate(item.id)}
                          disabled={!editForm.question.trim() || !editForm.answer.trim()}
                        >
                          저장
                        </Button>
                      </div>
                      {updateMutation.error && (
                        <p className="text-xs text-rose-400">{String(updateMutation.error)}</p>
                      )}
                    </div>
                  ) : (
                    <div className="space-y-3">
                      {canModifyNs && (
                        <div className="flex gap-2 justify-end pb-3 border-b border-slate-700">
                          <Button variant="secondary" size="sm" onClick={() => startEdit(item)}>
                            <Edit2 className="w-3.5 h-3.5" />수정
                          </Button>
                          <Button variant="danger" size="sm" onClick={() => setDeleteTarget(item.id)}>
                            <Trash2 className="w-3.5 h-3.5" />삭제
                          </Button>
                        </div>
                      )}
                      <div>
                        <p className="text-xs text-slate-500 mb-1">질문</p>
                        <p className="text-sm text-slate-300 whitespace-pre-wrap">{item.question}</p>
                      </div>
                      <div>
                        <p className="text-xs text-slate-500 mb-1">답변</p>
                        <p className="text-sm text-slate-300 whitespace-pre-wrap leading-relaxed">{item.answer}</p>
                      </div>
                      {item.knowledge_id && (
                        <div className="text-xs text-slate-500">
                          연결된 지식 ID: <span className="text-indigo-400">#{item.knowledge_id}</span>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
          {items.length === 0 && (
            <div className="text-center py-10 text-slate-500">
              <Zap className="w-8 h-8 mx-auto mb-2 text-slate-600" />
              <p>Few-shot 항목이 없습니다.</p>
              <p className="text-xs mt-1">챗에서 👍 긍정 피드백을 하면 자동으로 등록됩니다.</p>
            </div>
          )}
        </div>
      )}

      {/* Create Modal */}
      <Modal isOpen={showCreate} onClose={() => setShowCreate(false)} title="Few-shot 추가" maxWidth="max-w-xl">
        <div className="space-y-3">
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">
              질문 <span className="text-rose-400">*</span>
            </label>
            <textarea
              rows={2}
              value={createForm.question}
              onChange={(e) => setCreateForm((f) => ({ ...f, question: e.target.value }))}
              placeholder="예시 질문을 입력하세요"
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500 resize-none"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">
              답변 <span className="text-rose-400">*</span>
            </label>
            <textarea
              rows={6}
              value={createForm.answer}
              onChange={(e) => setCreateForm((f) => ({ ...f, answer: e.target.value }))}
              placeholder="모범 답변을 입력하세요"
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500 resize-none"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">연결 지식 ID (선택)</label>
            <input
              type="number"
              value={createForm.knowledge_id}
              onChange={(e) => setCreateForm((f) => ({ ...f, knowledge_id: e.target.value }))}
              placeholder="지식 베이스 ID (없으면 비워두세요)"
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
            />
          </div>
          <div className="flex gap-2 justify-end pt-2">
            <Button variant="secondary" size="sm" onClick={() => { setShowCreate(false); setCreateForm(defaultForm); }}>취소</Button>
            <Button
              variant="primary" size="sm"
              loading={createMutation.isPending}
              onClick={() => createMutation.mutate()}
              disabled={!createForm.question.trim() || !createForm.answer.trim()}
            >
              추가
            </Button>
          </div>
          {createMutation.isError && (
            <p className="text-xs text-rose-400">{String(createMutation.error)}</p>
          )}
        </div>
      </Modal>

      {/* Delete Confirmation Modal */}
      <Modal isOpen={deleteTarget !== null} onClose={() => setDeleteTarget(null)} title="Few-shot 삭제">
        <div className="space-y-4">
          <p className="text-sm text-slate-300">이 Few-shot 항목을 삭제하시겠습니까? 이후 유사 질문에 더 이상 참조되지 않습니다.</p>
          {deleteMutation.error && (
            <p className="text-xs text-rose-400">{String(deleteMutation.error)}</p>
          )}
          <div className="flex gap-2 justify-end">
            <Button variant="secondary" size="sm" onClick={() => setDeleteTarget(null)}>취소</Button>
            <Button
              variant="danger" size="sm"
              loading={deleteMutation.isPending}
              onClick={() => deleteTarget !== null && deleteMutation.mutate(deleteTarget)}
            >
              삭제
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
