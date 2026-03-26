import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { motion, AnimatePresence } from 'framer-motion';
import { ThumbsUp, ThumbsDown } from 'lucide-react';
import { postFeedback } from '../../api/feedback';
import { createKnowledge } from '../../api/knowledge';
import { createSqlFewshotFromFeedback } from '../../api/text2sql';
import { getCategories } from '../../api/namespaces';
import { Button } from '../ui/Button';
import type { KnowledgeCategory } from '../../types';

type FeedbackState = 'idle' | 'positive_sent' | 'showing_form' | 'negative_sent';

interface KnowledgeFormData {
  container_name: string;
  target_tables: string;
  content: string;
  query_template: string;
  base_weight: number;
  category: string;
}

interface FeedbackSectionProps {
  namespace: string;
  question: string;
  answer: string;
  knowledgeId?: number | null;
  messageId?: number;
  agentType?: string;
  sqlResult?: { sql: string } | null;
}

export function FeedbackSection({
  namespace,
  question,
  answer,
  knowledgeId,
  messageId,
  agentType,
  sqlResult,
}: FeedbackSectionProps) {
  const qc = useQueryClient();
  const [state, setState] = useState<FeedbackState>('idle');
  const [submitting, setSubmitting] = useState(false);
  const [form, setForm] = useState<KnowledgeFormData>({
    container_name: '',
    target_tables: '',
    content: question,
    query_template: '',
    base_weight: 1.0,
    category: '',
  });

  const { data: categories = [] } = useQuery<KnowledgeCategory[]>({
    queryKey: ['categories', namespace],
    queryFn: () => getCategories(namespace),
    enabled: !!namespace,
    staleTime: 0,
  });

  const handlePositive = async () => {
    try {
      await postFeedback({ namespace, question, answer, knowledge_id: knowledgeId ?? null, is_positive: true, message_id: messageId ?? null });
      if (agentType === 'text2sql' && sqlResult?.sql) {
        // text2sql 긍정 피드백 → SQL few-shot 후보 등록
        await createSqlFewshotFromFeedback(namespace, question, sqlResult.sql).catch(() => {});
        qc.invalidateQueries({ queryKey: ['sql_fewshots', namespace] });
      } else {
        // 지식AI 긍정 피드백 → fewshot 자동 생성 + knowledge base_weight 변경
        qc.invalidateQueries({ queryKey: ['fewshots'] });
        qc.invalidateQueries({ queryKey: ['knowledge'] });
      }
      qc.invalidateQueries({ queryKey: ['stats-ns'] });
    } catch (err) {
      console.error(err);
    }
    setState('positive_sent');
    setTimeout(() => setState('negative_sent'), 2000);
  };

  const handleSkip = async () => {
    try {
      await postFeedback({ namespace, question, answer, knowledge_id: knowledgeId ?? null, is_positive: false, message_id: messageId ?? null });
      // 부정 피드백 → knowledge base_weight 변경 + 통계 갱신
      qc.invalidateQueries({ queryKey: ['knowledge'] });
      qc.invalidateQueries({ queryKey: ['stats-ns'] });
    } catch (err) {
      console.error(err);
    }
    setState('negative_sent');
  };

  const handleSubmit = async () => {
    setSubmitting(true);
    try {
      await createKnowledge({
        namespace,
        container_name: form.container_name || '미분류',
        target_tables: form.target_tables.split(',').map((t) => t.trim()).filter(Boolean),
        content: form.content,
        query_template: form.query_template || null,
        base_weight: form.base_weight,
        category: form.category || null,
      });
      // 지식 등록 완료 → 해결됨으로 처리 (query_log status = 'resolved')
      await postFeedback({ namespace, question, answer, knowledge_id: knowledgeId ?? null, is_positive: true, message_id: messageId ?? null });
      qc.invalidateQueries({ queryKey: ['knowledge'] });
      qc.invalidateQueries({ queryKey: ['stats-ns'] });
    } catch (err) {
      console.error(err);
    } finally {
      setSubmitting(false);
      setState('negative_sent');
    }
  };

  if (state === 'negative_sent') return null;

  return (
    <div className="mt-3">
      <AnimatePresence mode="wait">
        {state === 'idle' && (
          <motion.div
            key="buttons"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="flex items-center gap-2"
          >
            <span className="text-xs text-slate-500">이 답변이 도움이 되었나요?</span>
            <button
              onClick={handlePositive}
              className="p-1.5 rounded-lg text-slate-500 hover:text-emerald-400 hover:bg-emerald-900/20 transition-colors"
              title="도움됨"
            >
              <ThumbsUp className="w-4 h-4" />
            </button>
            <button
              onClick={() => agentType === 'text2sql' ? handleSkip() : setState('showing_form')}
              className="p-1.5 rounded-lg text-slate-500 hover:text-rose-400 hover:bg-rose-900/20 transition-colors"
              title="개선 필요"
            >
              <ThumbsDown className="w-4 h-4" />
            </button>
          </motion.div>
        )}

        {state === 'positive_sent' && (
          <motion.div
            key="positive"
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            className="text-xs text-emerald-400"
          >
            감사합니다! 피드백이 전송되었습니다.
          </motion.div>
        )}

        {state === 'showing_form' && (
          <motion.div
            key="form"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <div className="bg-slate-900/60 border border-slate-700 rounded-xl p-4 space-y-3 mt-2">
              <p className="text-xs font-medium text-slate-400">지식으로 등록 (선택사항)</p>

              <div>
                <label className="block text-xs text-slate-500 mb-1">컨테이너명</label>
                <input
                  type="text"
                  value={form.container_name}
                  onChange={(e) => setForm((f) => ({ ...f, container_name: e.target.value }))}
                  placeholder="예: 청구서 조회"
                  className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
                />
              </div>

              <div>
                <label className="block text-xs text-slate-500 mb-1">대상 테이블 (쉼표 구분)</label>
                <input
                  type="text"
                  value={form.target_tables}
                  onChange={(e) => setForm((f) => ({ ...f, target_tables: e.target.value }))}
                  placeholder="table_a, table_b"
                  className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
                />
              </div>

              <div>
                <label className="block text-xs text-slate-500 mb-1">내용 <span className="text-rose-400">*</span></label>
                <textarea
                  rows={6}
                  value={form.content}
                  onChange={(e) => setForm((f) => ({ ...f, content: e.target.value }))}
                  className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 resize-y"
                />
              </div>

              <div>
                <label className="block text-xs text-slate-500 mb-1">쿼리 템플릿 (선택)</label>
                <textarea
                  rows={3}
                  value={form.query_template}
                  onChange={(e) => setForm((f) => ({ ...f, query_template: e.target.value }))}
                  placeholder="SELECT ..."
                  className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm font-mono text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500 resize-y"
                />
              </div>

              <div>
                <label className="block text-xs text-slate-500 mb-1">
                  기본 가중치: <span className="text-indigo-400">{form.base_weight.toFixed(1)}</span>
                </label>
                <input
                  type="range"
                  min={0}
                  max={3}
                  step={0.1}
                  value={form.base_weight}
                  onChange={(e) => setForm((f) => ({ ...f, base_weight: parseFloat(e.target.value) }))}
                  className="w-full accent-indigo-500"
                />
              </div>

              {categories.length > 0 && (
                <div>
                  <label className="block text-xs text-slate-500 mb-1">업무구분 (선택)</label>
                  <select
                    value={form.category}
                    onChange={(e) => setForm((f) => ({ ...f, category: e.target.value }))}
                    className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
                  >
                    <option value="">없음 (파트 공통)</option>
                    {categories.map((c) => (
                      <option key={c.id} value={c.name}>{c.name}</option>
                    ))}
                  </select>
                  <p className="text-[10px] text-slate-600 mt-0.5">미설정 시 모든 업무구분 검색에 공통으로 포함됩니다</p>
                </div>
              )}

              <div className="flex gap-2 justify-end pt-1">
                <Button variant="ghost" size="sm" onClick={handleSkip}>
                  건너뛰기
                </Button>
                <Button
                  variant="primary"
                  size="sm"
                  loading={submitting}
                  disabled={!form.content.trim()}
                  onClick={handleSubmit}
                >
                  지식 등록 + 피드백 전송
                </Button>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
