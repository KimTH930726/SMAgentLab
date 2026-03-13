import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Save, XCircle } from 'lucide-react';
import { listPrompts, updatePrompt } from '../../api/prompts';
import type { Prompt, PromptUpdate } from '../../api/prompts';
import { Button } from '../ui/Button';
import { Modal } from '../ui/Modal';

export function PromptManager() {
  const qc = useQueryClient();
  const [selectedPrompt, setSelectedPrompt] = useState<Prompt | null>(null);
  const [editForm, setEditForm] = useState<PromptUpdate>({});

  const { data: prompts, isLoading } = useQuery({
    queryKey: ['prompts'],
    queryFn: listPrompts,
    staleTime: 10_000,
  });

  const saveMutation = useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: PromptUpdate }) => updatePrompt(id, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['prompts'] });
      setSelectedPrompt(null);
      setEditForm({});
    },
  });

  const openModal = (p: Prompt) => {
    setSelectedPrompt(p);
    setEditForm({ func_name: p.func_name, content: p.content, description: p.description });
    saveMutation.reset();
  };

  const closeModal = () => {
    setSelectedPrompt(null);
    setEditForm({});
  };

  const handleSave = () => {
    if (!selectedPrompt) return;
    saveMutation.mutate({ id: selectedPrompt.id, payload: editForm });
  };

  if (isLoading) {
    return <div className="text-center py-10 text-slate-500 animate-pulse text-sm">프롬프트 로딩 중...</div>;
  }

  return (
    <div className="space-y-4">
      <p className="text-xs text-slate-500">
        각 LLM 호출 기능에 사용되는 시스템 프롬프트를 관리합니다. 카드를 클릭하여 수정할 수 있습니다.
      </p>

      <div className="space-y-3">
        {prompts?.map((p) => (
          <button
            key={p.id}
            onClick={() => openModal(p)}
            className="w-full text-left bg-slate-800 border border-slate-700 rounded-xl overflow-hidden hover:border-indigo-500/50 transition-colors group"
          >
            {/* Header */}
            <div className="flex items-center justify-between px-5 py-3 border-b border-slate-700/50">
              <div className="flex items-center gap-3">
                <span className="text-xs font-mono text-slate-500 bg-slate-900 px-2 py-0.5 rounded">
                  #{p.id}
                </span>
                <span className="text-xs font-mono text-indigo-400 bg-indigo-500/10 px-2 py-0.5 rounded">
                  {p.func_key}
                </span>
                <span className="text-sm font-medium text-slate-200">{p.func_name}</span>
              </div>
              <span className="text-[10px] text-slate-500">
                {new Date(p.updated_at).toLocaleString('ko-KR')}
              </span>
            </div>

            {/* Body */}
            <div className="px-5 py-4 space-y-2">
              <p className="text-xs text-slate-500">{p.description}</p>
              <pre className="bg-slate-900/50 border border-slate-700/50 rounded-lg px-4 py-3 text-xs text-slate-300 font-mono whitespace-pre-wrap leading-relaxed max-h-32 overflow-hidden">
                {p.content}
              </pre>
            </div>
          </button>
        ))}
      </div>

      {/* Edit Modal */}
      {selectedPrompt && (
        <Modal
          isOpen
          onClose={closeModal}
          title={`프롬프트 수정 — ${selectedPrompt.func_key}`}
          maxWidth="max-w-3xl"
        >
          <div className="space-y-5">
            {saveMutation.isError && (
              <div className="flex items-center gap-2 px-4 py-3 rounded-lg text-sm border bg-rose-500/10 border-rose-500/30 text-rose-300">
                <XCircle className="w-4 h-4" /> 저장 실패: {String(saveMutation.error)}
              </div>
            )}

            {/* 기본 정보 */}
            <div className="flex items-center gap-3 bg-slate-900/60 rounded-lg px-4 py-3">
              <span className="text-xs font-mono text-slate-500 bg-slate-800 px-2 py-0.5 rounded">
                #{selectedPrompt.id}
              </span>
              <span className="text-xs font-mono text-indigo-400 bg-indigo-500/10 px-2 py-0.5 rounded">
                {selectedPrompt.func_key}
              </span>
              <span className="text-[10px] text-slate-500 ml-auto">
                최종 수정: {new Date(selectedPrompt.updated_at).toLocaleString('ko-KR')}
              </span>
            </div>

            {/* 기능명 */}
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">기능명</label>
              <input
                type="text"
                value={editForm.func_name ?? ''}
                onChange={(e) => setEditForm((f) => ({ ...f, func_name: e.target.value }))}
                className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
              />
            </div>

            {/* 설명 */}
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">설명</label>
              <input
                type="text"
                value={editForm.description ?? ''}
                onChange={(e) => setEditForm((f) => ({ ...f, description: e.target.value }))}
                className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
              />
            </div>

            {/* 프롬프트 내용 */}
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">프롬프트 내용</label>
              <textarea
                value={editForm.content ?? ''}
                onChange={(e) => setEditForm((f) => ({ ...f, content: e.target.value }))}
                rows={14}
                className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 font-mono leading-relaxed focus:outline-none focus:border-indigo-500 resize-y"
              />
            </div>

            {/* 액션 버튼 */}
            <div className="flex justify-end gap-3 pt-2 border-t border-slate-700/50">
              <Button variant="secondary" size="sm" onClick={closeModal}>
                취소
              </Button>
              <Button
                variant="primary"
                size="sm"
                onClick={handleSave}
                loading={saveMutation.isPending}
              >
                <Save className="w-3.5 h-3.5" /> 저장
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}
