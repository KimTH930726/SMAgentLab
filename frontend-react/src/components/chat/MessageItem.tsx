import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { Bot, User, ChevronDown, ChevronUp, CheckCircle, Database, BarChart2, Copy, Check } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeRaw from 'rehype-raw';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { vscDarkPlus, oneLight } from 'react-syntax-highlighter/dist/esm/styles/prism';
import { Badge } from '../ui/Badge';
import { SearchResultCard } from './SearchResultCard';
import { PolicyCitationCard } from './PolicyCitationCard';
import { FeedbackSection } from './FeedbackSection';
import { useThemeStore } from '../../store/useThemeStore';
import { useCorrectionStore } from '../../store/useCorrectionStore';
import { CorrectionAnalysis } from './CorrectionAnalysis';
import { getCorrectionStatus } from '../../api/corrections';
import type { ChatMessage } from '../../types';

interface MessageItemProps {
  message: ChatMessage;
  namespace: string;
}

// ── SQL Block ────────────────────────────────────────────────────────────────

function SqlBlock({ sql, reasoning, cached }: { sql: string; reasoning: string; cached: boolean }) {
  const [expanded, setExpanded] = useState(true);
  const [copied, setCopied] = useState(false);
  const isLight = useThemeStore((s) => s.theme === 'light');

  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard.writeText(sql).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  return (
    <div className="rounded-xl border border-emerald-200 dark:border-emerald-800/50 overflow-hidden text-xs">
      <div
        className="flex items-center justify-between px-3 py-2 bg-emerald-50 dark:bg-emerald-950/40 cursor-pointer select-none"
        onClick={() => setExpanded((v) => !v)}
      >
        <div className="flex items-center gap-2 text-emerald-700 dark:text-emerald-300 font-medium">
          <Database className="w-3.5 h-3.5" />
          <span>생성된 SQL</span>
          {cached && (
            <span className="flex items-center gap-1 text-amber-600 dark:text-amber-400 text-[10px]">
              <CheckCircle className="w-3 h-3" /> 캐시
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {expanded && (
            <button
              onClick={handleCopy}
              className="flex items-center gap-1 text-slate-400 hover:text-emerald-600 dark:hover:text-emerald-300 transition-colors"
              title="SQL 복사"
            >
              {copied ? <Check className="w-3.5 h-3.5 text-emerald-600 dark:text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
            </button>
          )}
          {expanded ? <ChevronUp className="w-3.5 h-3.5 text-slate-400" /> : <ChevronDown className="w-3.5 h-3.5 text-slate-400" />}
        </div>
      </div>
      {expanded && (
        <div>
          {reasoning && (
            <div className="px-3 py-2 bg-slate-900/60 border-t border-emerald-200 dark:border-emerald-900/30 text-slate-400 italic text-[11px] leading-relaxed">
              💭 {reasoning}
            </div>
          )}
          <SyntaxHighlighter
            language="sql"
            style={isLight ? oneLight : vscDarkPlus}
            customStyle={{ margin: 0, borderRadius: 0, fontSize: '12px', ...(isLight ? {} : { background: 'rgb(5,15,25)' }) }}
          >
            {sql}
          </SyntaxHighlighter>
        </div>
      )}
    </div>
  );
}

// ── Table Result ─────────────────────────────────────────────────────────────

function TableResult({ columns, rows, row_count, truncated }: {
  columns: string[];
  rows: Record<string, unknown>[];
  row_count: number;
  truncated: boolean;
}) {
  const [copiedCsv, setCopiedCsv] = useState(false);

  const exportCsv = () => {
    const header = columns.join(',');
    const body = rows.map((r) => columns.map((c) => {
      const v = r[c];
      if (v === null || v === undefined) return '';
      const s = String(v);
      return s.includes(',') || s.includes('"') || s.includes('\n') ? `"${s.replace(/"/g, '""')}"` : s;
    }).join(',')).join('\n');
    const blob = new Blob(['\uFEFF' + header + '\n' + body], { type: 'text/csv;charset=utf-8;' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'result.csv';
    a.click();
  };

  const copyCsv = () => {
    const header = columns.join('\t');
    const body = rows.map((r) => columns.map((c) => r[c] ?? '').join('\t')).join('\n');
    navigator.clipboard.writeText(header + '\n' + body).then(() => {
      setCopiedCsv(true);
      setTimeout(() => setCopiedCsv(false), 1500);
    });
  };

  return (
    <div className="rounded-xl border border-slate-700 overflow-hidden text-xs">
      <div className="flex items-center justify-between px-3 py-2 bg-slate-800/80">
        <div className="flex items-center gap-2 text-slate-300 font-medium">
          <BarChart2 className="w-3.5 h-3.5 text-blue-600 dark:text-blue-400" />
          <span className="text-slate-400">
            {row_count}행 × {columns.length}열
          </span>
          {truncated && <Badge color="amber">일부만 표시</Badge>}
        </div>
        <div className="flex items-center gap-2">
          <button onClick={copyCsv} className="flex items-center gap-1 px-2 py-0.5 rounded text-slate-400 hover:text-slate-200 hover:bg-slate-700 transition-colors" title="탭 구분 복사">
            {copiedCsv ? <Check className="w-3 h-3 text-emerald-600 dark:text-emerald-400" /> : <Copy className="w-3 h-3" />}
            <span>복사</span>
          </button>
          <button onClick={exportCsv} className="flex items-center gap-1 px-2 py-0.5 rounded text-slate-400 hover:text-slate-200 hover:bg-slate-700 transition-colors" title="CSV 다운로드">
            <span>CSV</span>
          </button>
        </div>
      </div>
      <div className="overflow-x-auto max-h-80">
        <table className="w-full border-collapse">
          <thead className="bg-slate-800 sticky top-0">
            <tr>
              {columns.map((col) => (
                <th key={col} className="px-3 py-2 text-left text-slate-400 font-medium whitespace-nowrap border-b border-slate-700">
                  {col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i} className={i % 2 === 0 ? 'bg-slate-900/30' : 'bg-slate-900/10'}>
                {columns.map((col) => (
                  <td key={col} className="px-3 py-1.5 text-slate-300 whitespace-nowrap border-b border-slate-800">
                    {row[col] === null || row[col] === undefined ? (
                      <span className="text-slate-600 italic">NULL</span>
                    ) : (
                      String(row[col])
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 && (
          <p className="text-center text-slate-500 py-4">결과가 없습니다.</p>
        )}
      </div>
    </div>
  );
}

// ── SVG Bar Chart ────────────────────────────────────────────────────────────

function SimpleBarChart({ chartResult, rows, columns }: {
  chartResult: { type: string; x: string; y: string; title: string };
  rows: Record<string, unknown>[];
  columns: string[];
}) {
  if (!columns.includes(chartResult.x) || !columns.includes(chartResult.y)) return null;
  if (chartResult.type !== 'bar' && chartResult.type !== 'line') {
    // For non-bar/line types, just show info
    return (
      <div className="rounded-xl border border-slate-700 px-3 py-2 text-xs text-slate-400 bg-slate-800/50">
        📊 {chartResult.title} — {chartResult.type} 차트 ({chartResult.x} × {chartResult.y})
      </div>
    );
  }

  const data = rows.slice(0, 20).map((r) => ({
    label: String(r[chartResult.x] ?? ''),
    value: Number(r[chartResult.y]) || 0,
  }));
  const max = Math.max(...data.map((d) => d.value), 1);
  const W = 400, H = 160, pad = { t: 24, r: 8, b: 40, l: 48 };
  const barW = Math.max(4, (W - pad.l - pad.r) / data.length - 2);

  return (
    <div className="rounded-xl border border-slate-700 overflow-hidden bg-slate-900/50">
      <div className="px-3 py-2 bg-slate-800/80 text-xs font-medium text-slate-300 flex items-center gap-2">
        <BarChart2 className="w-3.5 h-3.5 text-cyan-600 dark:text-cyan-400" />
        {chartResult.title}
      </div>
      <div className="overflow-x-auto px-2 pb-2">
        <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} className="text-xs">
          {/* y-axis ticks */}
          {[0, 0.25, 0.5, 0.75, 1].map((t) => {
            const y = pad.t + (H - pad.t - pad.b) * (1 - t);
            return (
              <g key={t}>
                <line x1={pad.l} x2={W - pad.r} y1={y} y2={y} stroke="#334155" strokeWidth={0.5} />
                <text x={pad.l - 4} y={y + 4} textAnchor="end" fill="#64748b" fontSize={9}>
                  {Math.round(max * t)}
                </text>
              </g>
            );
          })}
          {/* bars */}
          {data.map((d, i) => {
            const x = pad.l + i * ((W - pad.l - pad.r) / data.length) + 1;
            const bh = ((H - pad.t - pad.b) * d.value) / max;
            const y = pad.t + (H - pad.t - pad.b) - bh;
            return (
              <g key={i}>
                <rect x={x} y={y} width={barW} height={bh} fill="#6366f1" rx={2} opacity={0.8} />
                {data.length <= 12 && (
                  <text
                    x={x + barW / 2}
                    y={H - pad.b + 14}
                    textAnchor="middle"
                    fill="#94a3b8"
                    fontSize={8}
                    transform={data.length > 8 ? `rotate(-30 ${x + barW / 2} ${H - pad.b + 14})` : undefined}
                  >
                    {d.label.length > 8 ? d.label.slice(0, 7) + '…' : d.label}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}

// ── MessageItem ───────────────────────────────────────────────────────────────

// ── 정정 신고 접수 카드 ─────────────────────────────────────────────────────────

function CorrectionReceipt({ messageId }: { messageId: number }) {
  const entry = useCorrectionStore((s) => s.submitted[messageId]);
  const [open, setOpen] = useState(false);
  if (!entry) return null;
  const { result, target } = entry;
  const orig = result.original ?? {};
  const originalText = String(orig.content ?? orig.chunk_text ?? orig.raw_body ?? orig.question ?? '');
  const p = result.proposed;
  const proposedText = p ? String(p.content ?? p.chunk_text ?? p.raw_body ?? '') : '';
  const hasDiff = result.target_type !== 'answer';
  return (
    <div className="rounded-xl border px-3 py-2.5 text-xs space-y-2 bg-indigo-50/60 border-indigo-200 dark:bg-indigo-950/20 dark:border-indigo-800/50">
      <div className="flex items-center gap-2">
        <span className="font-medium text-indigo-700 dark:text-indigo-300">정정 신고 접수됨</span>
        {!result.ai_verdict?.verdict && <span className="text-slate-500 truncate">{target.label}</span>}
        {hasDiff && (
          <button onClick={() => setOpen((v) => !v)} className="ml-auto text-slate-500 hover:text-slate-300">
            {open ? '접기' : '기존 vs 수정안'}
          </button>
        )}
      </div>
      {result.ai_verdict?.verdict
        ? <CorrectionAnalysis verdict={result.ai_verdict} audience="reporter" />
        : <p className="text-[11px] text-slate-500">담당자가 확인한 뒤에만 반영돼요. 결과는 다음에 채팅을 열 때 알려드려요.</p>}
      {open && hasDiff && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
          <div>
            <p className="text-slate-500 mb-1">기존</p>
            <p className="whitespace-pre-wrap text-slate-300 max-h-48 overflow-y-auto">{originalText || '(없음)'}</p>
          </div>
          <div>
            <p className="text-slate-500 mb-1">수정안(AI 초안)</p>
            <p className="whitespace-pre-wrap text-slate-300 max-h-48 overflow-y-auto">
              {proposedText || '초안을 만들지 못했어요 — 담당자가 직접 작성해요.'}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

export function MessageItem({ message, namespace }: MessageItemProps) {
  const isUser = message.role === 'user';
  const knowledgeIds = (message.results ?? []).map((r) => r.id);
  const policyItemIds = [...new Set((message.policyCitations ?? []).map((c) => c.item_id).filter((v): v is number => v != null))];
  // "정정 검토 중" 배지 — 같은 근거가 다른 답변에 나와도 표시되도록 근거 id 기준으로 조회
  const { data: underReview } = useQuery({
    queryKey: ['correction-status', knowledgeIds.join(','), policyItemIds.join(',')],
    queryFn: () => getCorrectionStatus(knowledgeIds, policyItemIds),
    enabled: !isUser && !message.isStreaming && (knowledgeIds.length > 0 || policyItemIds.length > 0),
    staleTime: 30_000,
  });
  // 근거 카드의 "이 근거 틀림"은 2026-10-06 제거 — 신고는 "답변 틀림" 하나로(틀린 근거는 AI가 찾고 담당자가 바꿀 수 있음).
  // 실사용 0건이었고 버튼이 둘이라 차이를 헷갈렸다. 카드엔 "정정 검토 중" 배지만 남긴다.

  if (isUser) {
    return (
      <div className="flex justify-end">
        <div className="flex items-end gap-2 max-w-[80%]">
          <div className="bg-slate-700 rounded-2xl rounded-br-sm px-4 py-3">
            <p className="text-sm text-slate-200 whitespace-pre-wrap leading-relaxed">
              {message.content}
            </p>
          </div>
          <div className="w-7 h-7 rounded-full bg-slate-600 flex items-center justify-center flex-shrink-0 mb-0.5">
            <User className="w-4 h-4 text-slate-300" />
          </div>
        </div>
      </div>
    );
  }

  // Assistant message
  return (
    <div className="flex justify-start">
      <div className="flex items-start gap-2 max-w-[90%]">
        <div className="w-7 h-7 rounded-full bg-indigo-600 flex items-center justify-center flex-shrink-0 mt-0.5">
          <Bot className="w-4 h-4 text-white" />
        </div>
        <div className="flex-1 space-y-3">
          {/* Mapped term badge */}
          {message.mapped_term && (
            <div className="flex items-center gap-2">
              <span className="text-xs text-slate-500">용어 매핑:</span>
              <Badge color="indigo">{message.mapped_term}</Badge>
            </div>
          )}

          {/* Search results */}
          {message.results && message.results.length > 0 && (
            <div className="space-y-1.5">
              <p className="text-xs text-slate-500 font-medium">
                검색된 문서 {message.results.length}건
              </p>
              {message.results.map((result, idx) => (
                <SearchResultCard
                  key={result.id}
                  result={result}
                  defaultOpen={false}
                  index={idx}
                  underReview={underReview?.knowledge.includes(result.id)}
                />
              ))}
            </div>
          )}

          {/* Policy citations — rag_knowledge 검색결과(results)와 별개 출처이므로 분리된 섹션으로 표시 */}
          {message.policyCitations && message.policyCitations.length > 0 && (
            <div className="space-y-1.5">
              <p className="text-xs text-violet-600 dark:text-violet-400 font-medium">
                📋 정책 근거 {message.policyCitations.length}건
              </p>
              {message.policyCitations.map((citation, idx) => {
                return (
                  <PolicyCitationCard
                    key={idx}
                    citation={citation}
                    defaultOpen={false}
                    index={idx}
                    underReview={citation.kind === 'param'
                      ? citation.param_id != null && !!underReview?.policy_param.includes(citation.param_id)
                      : citation.chunk_id != null && !!underReview?.policy_narrative.includes(citation.chunk_id)}
                  />
                );
              })}
            </div>
          )}

          {/* SQL block */}
          {message.sqlResult && (
            <SqlBlock
              sql={message.sqlResult.sql}
              reasoning={message.sqlResult.reasoning}
              cached={message.sqlResult.cached}
            />
          )}

          {/* Table result */}
          {message.tableResult && (
            <TableResult
              columns={message.tableResult.columns}
              rows={message.tableResult.rows}
              row_count={message.tableResult.row_count}
              truncated={message.tableResult.truncated}
            />
          )}

          {/* Answer text (summary) — appears below table, above chart */}
          {(message.content || message.isStreaming) && (
            <div
              className={clsx(
                'bg-slate-800 rounded-2xl rounded-tl-sm px-4 py-3',
                'border border-slate-700',
              )}
            >
              {message.content ? (
                <div
                  className={clsx(
                    'prose-chat text-sm text-slate-200 leading-relaxed',
                    message.isStreaming && 'typing-cursor',
                  )}
                >
                  <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeRaw]}>
                    {message.content}
                  </ReactMarkdown>
                </div>
              ) : (
                message.isStreaming && (
                  <p className="typing-cursor text-slate-400 text-sm">&nbsp;</p>
                )
              )}
            </div>
          )}

          {/* Chart — after summary text */}
          {message.chartResult && message.tableResult && (
            <SimpleBarChart
              chartResult={message.chartResult}
              rows={message.tableResult.rows}
              columns={message.tableResult.columns}
            />
          )}

          {/* Feedback — 피드백을 이미 보낸 답변도 의견이 아직 없으면 "의견 덧붙이기"를 남긴다(의견까지 접수했으면 숨김) */}
          {!message.isStreaming && message.content && namespace && message.status !== 'failed' && (
            <FeedbackSection
              namespace={namespace}
              question={message.question ?? ''}
              answer={message.content}
              knowledgeId={message.results?.[0]?.id ?? null}
              messageId={message.messageId}
              alreadySent={!!message.has_feedback}
            />
          )}
          {message.messageId != null && <CorrectionReceipt messageId={message.messageId} />}
        </div>
      </div>
    </div>
  );
}
