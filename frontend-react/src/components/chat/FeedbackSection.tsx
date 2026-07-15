import { useState, useRef, useEffect } from 'react';
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
  const [submitError, setSubmitError] = useState<string | null>(null);
  const transitionTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (transitionTimer.current) clearTimeout(transitionTimer.current); }, []);
  const [form, setForm] = useState<KnowledgeFormData>({
    container_name: '',
    target_tables: '',
    content: answer,
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
  // 업무구분은 등록 시 필수값 — '공통지식'을 최상단 기본값으로 노출
  const sortedCategories = [...categories].sort((a, b) =>
    a.name === '공통지식' ? -1 : b.name === '공통지식' ? 1 : a.name.localeCompare(b.name)
  );
  useEffect(() => {
    if (state === 'showing_form' && !form.category && sortedCategories.length > 0) {
      setForm((f) => ({ ...f, category: sortedCategories[0].name }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state, sortedCategories.length]);

  const handlePositive = async () => {
    try {
      await postFeedback({ namespace, question, answer, knowledge_id: knowledgeId ?? null, is_positive: true, message_id: messageId ?? null });
      if (agentType === 'text2sql' && sqlResult?.sql) {
        // text2sql 긍정 피드백 → SQL Q&A 후보 등록
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
    if (transitionTimer.current) clearTimeout(transitionTimer.current);
    transitionTimer.current = setTimeout(() => setState('negative_sent'), 2000);
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
    setSubmitError(null);
    try {
      const created = await createKnowledge({
        namespace,
        container_name: form.container_name || '미분류',
        target_tables: form.target_tables.split(',').map((t) => t.trim()).filter(Boolean),
        content: form.content,
        query_template: form.query_template || null,
        base_weight: form.base_weight,
        category: form.category || null,
      });
      // 이 답변을 지적하고 새 지식을 등록한 것이므로, 기존 knowledgeId(오답의 근거였던
      // 지식)는 부정 피드백으로 가중치를 낮추고, query_log는 새로 등록한 지식과 연결해
      // 해결됨으로 처리한다 (is_positive=true로 보내면 오답을 만든 지식의 가중치가
      // 오히려 올라가는 문제가 있었음).
      await postFeedback({
        namespace, question, answer,
        knowledge_id: knowledgeId ?? null,
        is_positive: false,
        message_id: messageId ?? null,
        resolved_knowledge_id: created.id,
      });
      if (created.pending_review) {
        window.alert('등록하신 지식이 기존 지식과 유사도가 높아 승인 대기 상태로 등록되었습니다. 관리자 승인 후 검색에 반영됩니다.');
      }
      qc.invalidateQueries({ queryKey: ['knowledge'] });
      qc.invalidateQueries({ queryKey: ['stats-ns'] });
      setState('negative_sent');
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : '등록에 실패했습니다.');
    } finally {
      setSubmitting(false);
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

              {sortedCategories.length > 0 ? (
                <div>
                  <label className="block text-xs text-slate-500 mb-1">
                    업무구분 <span className="text-rose-400">*</span>
                  </label>
                  <select
                    value={form.category}
                    onChange={(e) => setForm((f) => ({ ...f, category: e.target.value }))}
                    className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
                  >
                    {sortedCategories.map((c) => (
                      <option key={c.id} value={c.name}>{c.name}</option>
                    ))}
                  </select>
                </div>
              ) : (
                <p className="text-xs text-amber-400">
                  이 파트에 등록된 업무구분이 없어 지식을 등록할 수 없습니다. 기준정보관리에서 업무구분을 먼저 추가해주세요.
                </p>
              )}

              {submitError && <p className="text-xs text-rose-400">{submitError}</p>}

              <div className="flex gap-2 justify-end pt-1">
                <Button variant="ghost" size="sm" onClick={handleSkip}>
                  건너뛰기
                </Button>
                <Button
                  variant="primary"
                  size="sm"
                  loading={submitting}
                  disabled={!form.content.trim() || !form.category}
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
