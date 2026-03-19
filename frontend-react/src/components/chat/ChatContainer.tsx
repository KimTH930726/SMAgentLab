import { useState, useEffect, useRef, useCallback } from 'react';
import { Send, Square, AlertCircle, ChevronDown, ChevronUp, Wrench } from 'lucide-react';
import { useAppStore } from '../../store/useAppStore';
import {
  useStreamStore,
  startChatStream,
  stopChatStream,
  clearStreamState,
} from '../../store/useStreamStore';
import { getMessages } from '../../api/conversations';
import { suggestCategory } from '../../api/namespaces';
import { MessageItem } from './MessageItem';
import { ToolRequestCard } from './ToolRequestCard';
import type { ChatMessage } from '../../types';
import type { PipelineStep } from '../../store/useStreamStore';

function PipelineStepsToggle({ steps }: { steps: PipelineStep[] }) {
  const [expanded, setExpanded] = useState(false);
  const currentStep = [...steps].reverse().find((s) => !s.done) ?? steps[steps.length - 1];

  return (
    <div className="ml-9">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 text-xs text-slate-400 hover:text-slate-300 transition-colors py-1"
      >
        {currentStep && !currentStep.done ? (
          <span className="text-indigo-400 animate-pulse">●</span>
        ) : (
          <span className="text-emerald-400">✓</span>
        )}
        <span>{currentStep?.message}</span>
        {expanded ? (
          <ChevronUp className="w-3 h-3" />
        ) : (
          <ChevronDown className="w-3 h-3" />
        )}
      </button>
      {expanded && (
        <div className="mt-1 space-y-0.5 pl-1 border-l border-slate-700/50 ml-1">
          {steps.map((s, i) => (
            <div key={i} className="flex items-center gap-2 text-xs pl-2">
              {s.done ? (
                <span className="text-emerald-400 text-[10px]">✓</span>
              ) : (
                <span className="text-indigo-400 animate-pulse text-[10px]">●</span>
              )}
              <span className={s.done ? 'text-slate-500' : 'text-slate-300'}>{s.message}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// 백엔드에서 도구 대기 상태로 저장한 placeholder 메시지 패턴
const _TOOL_PENDING_PATTERNS = [
  '[추가 정보 입력 대기 중]',
  '[도구 선택 대기 중]',
  '[도구 실행 승인 대기 중]',
];

function convertMessages(msgs: { id: number; role: string; content: string; mapped_term?: string | null; results?: unknown[] | null; status?: string; has_feedback?: boolean; metadata?: { sql_result?: { sql: string; reasoning: string; cached: boolean } | null; table_result?: { columns: string[]; rows: Record<string, unknown>[]; row_count: number; truncated: boolean } | null; chart_result?: { type: string; x: string; y: string; title: string } | null } | null }[]): ChatMessage[] {
  // Fix out-of-order pairs from old data (parallel saves could put assistant before user)
  // Check pairs at step=2: (0,1), (2,3), ... and swap reversed pairs
  const ordered = [...msgs];
  for (let i = 0; i < ordered.length - 1; i += 2) {
    if (ordered[i].role === 'assistant' && ordered[i + 1].role === 'user') {
      [ordered[i], ordered[i + 1]] = [ordered[i + 1], ordered[i]];
    }
  }

  const converted: ChatMessage[] = [];
  let lastQuestion = '';
  for (let i = 0; i < ordered.length; i++) {
    const m = ordered[i];
    if (m.role === 'user') {
      lastQuestion = m.content;
      converted.push({ role: 'user', content: m.content });
    } else {
      const isGenerating = m.status === 'generating';
      // 빈 assistant 메시지: 생성 중이면 placeholder 표시, 아니면 스킵
      if (!m.content || m.content.trim().length <= 1) {
        if (!isGenerating) continue;
      }
      // 도구 대기 placeholder 메시지는 스킵 (ToolRequestCard가 대신 표시)
      const isToolPending = _TOOL_PENDING_PATTERNS.includes(m.content?.trim());
      if (isToolPending) continue;

      converted.push({
        role: 'assistant',
        content: isGenerating && (!m.content || m.content.trim().length <= 1)
          ? '답변 생성 중...'
          : m.content,
        mapped_term: m.mapped_term,
        results: (m.results ?? []) as ChatMessage['results'],
        question: lastQuestion,
        has_feedback: m.has_feedback ?? false,
        messageId: m.id,
        isStreaming: isGenerating,
        status: m.status,
        sqlResult: m.metadata?.sql_result ?? null,
        tableResult: m.metadata?.table_result ?? null,
        chartResult: m.metadata?.chart_result ?? null,
      });
    }
  }
  return converted;
}

export function ChatContainer() {
  const namespace = useAppStore((s) => s.namespace);
  const conversationId = useAppStore((s) => s.conversationId);
  const setConversationId = useAppStore((s) => s.setConversationId);
  const searchConfig = useAppStore((s) => s.searchConfig);
  const category = useAppStore((s) => s.category);
  const conversations = useAppStore((s) => s.conversations);
  const chatRefreshKey = useAppStore((s) => s.chatRefreshKey);

  // History messages loaded from API — tagged with the conversationId they belong to
  const [historyMessages, setHistoryMessages] = useState<ChatMessage[]>([]);
  const historyConvIdRef = useRef<number | null>(null);
  const selectedAgent = useAppStore((s) => s.selectedAgent);
  const [input, setInput] = useState('');
  const [useHttpTool, setUseHttpTool] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const userScrolledUpRef = useRef(false);

  // Epoch counter: prevents stale async getMessages from overwriting historyMessages.
  // Incremented on conversationId change and new stream start.
  const loadEpochRef = useRef(0);

  // Stream state from Zustand store
  const streamActive = useStreamStore((s) => s.active);
  const streamConvId = useStreamStore((s) => s.convId);
  const streamOriginConvId = useStreamStore((s) => s.originConvId);
  const streamMessages = useStreamStore((s) => s.messages);
  const streamStatus = useStreamStore((s) => s.status);
  const streamSteps = useStreamStore((s) => s.steps);
  const toolRequest = useStreamStore((s) => s.toolRequest);

  // Is this conversation the one being streamed (or just completed)?
  const isStreamHere =
    streamMessages.length > 0 &&
    (streamConvId === conversationId ||
      streamOriginConvId === conversationId ||
      (streamOriginConvId === null && conversationId === null && streamActive));

  const isLoading = streamActive && isStreamHere;
  const statusMessage = isLoading ? streamStatus : '';

  // Check if current conversation has been trimmed
  const currentConv = conversationId
    ? conversations.find((c) => c.id === conversationId)
    : null;
  const isTrimmed = currentConv?.trimmed ?? false;

  // What to display: history + stream messages (if applicable)
  // Guard: only use historyMessages if they belong to the current conversation
  const safeHistory = historyConvIdRef.current === conversationId ? historyMessages : [];

  const displayMessages = (() => {
    if (!isStreamHere) return safeHistory;
    let hist = safeHistory;
    const firstStream = streamMessages[0];
    if (!firstStream || firstStream.role !== 'user') return [...hist, ...streamMessages];

    // Case 1: history ends with [user, assistant(pre-created)] matching stream's user
    if (hist.length >= 2) {
      const secondLast = hist[hist.length - 2];
      const last = hist[hist.length - 1];
      if (secondLast.role === 'user' && secondLast.content === firstStream.content
          && last.role === 'assistant') {
        return [...hist.slice(0, -2), ...streamMessages];
      }
    }
    // Case 2: history ends with [user] matching stream's user
    if (hist.length >= 1) {
      const last = hist[hist.length - 1];
      if (last.role === 'user' && last.content === firstStream.content) {
        return [...hist.slice(0, -1), ...streamMessages];
      }
    }
    return [...hist, ...streamMessages];
  })();

  // Epoch-guarded history loader with retry for recently-aborted streams
  const loadHistory = useCallback((convId: number, retry = true) => {
    const epoch = loadEpochRef.current;
    getMessages(convId)
      .then((msgs) => {
        if (epoch !== loadEpochRef.current) return;
        const converted = convertMessages(msgs);
        historyConvIdRef.current = convId;
        setHistoryMessages(converted);

        // If last message is user with no assistant response, backend may still be saving.
        // Retry once after 500ms.
        if (retry && converted.length > 0 && converted[converted.length - 1].role === 'user') {
          setTimeout(() => {
            if (epoch !== loadEpochRef.current) return;
            getMessages(convId)
              .then((msgs2) => {
                if (epoch === loadEpochRef.current) {
                  historyConvIdRef.current = convId;
                  setHistoryMessages(convertMessages(msgs2));
                }
              })
              .catch(console.error);
          }, 500);
        }
      })
      .catch(console.error);
  }, []);

  // Clear stale stream state on mount (e.g. after page refresh)
  useEffect(() => {
    const state = useStreamStore.getState();
    if (state.active && state.messages.length > 0) {
      // Stream was active before page refresh — force clear
      clearStreamState();
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Load conversation history when conversationId changes
  useEffect(() => {
    loadEpochRef.current++;
    userScrolledUpRef.current = false;

    if (!conversationId) {
      historyConvIdRef.current = null;
      setHistoryMessages([]);
      return;
    }

    // If actively streaming to this conversation:
    // - For NEW conversations (originConvId was null), stream messages are sufficient — skip DB load
    // - For existing conversations (user navigated back), load previous messages
    if (streamActive && (streamConvId === conversationId || streamOriginConvId === conversationId)) {
      if (streamOriginConvId !== null) {
        loadHistory(conversationId);
      }
      return;
    }

    // Clear stale stream state if it belongs to a different conversation
    if (!streamActive && streamMessages.length > 0) {
      clearStreamState();
    }

    // Clear for fresh load
    historyConvIdRef.current = null;
    setHistoryMessages([]);
    loadHistory(conversationId);
  }, [conversationId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Poll for background-generating messages (backend asyncio.Task still running)
  useEffect(() => {
    if (!conversationId || streamActive) return;
    const lastMsg = safeHistory[safeHistory.length - 1];
    if (!lastMsg || !lastMsg.isStreaming) return;

    const interval = setInterval(() => {
      const epoch = loadEpochRef.current;
      getMessages(conversationId)
        .then((msgs) => {
          if (epoch !== loadEpochRef.current) return;
          const converted = convertMessages(msgs);
          historyConvIdRef.current = conversationId;
          setHistoryMessages(converted);
          // Stop polling if generation completed
          const last = converted[converted.length - 1];
          if (!last || !last.isStreaming) {
            clearInterval(interval);
          }
        })
        .catch(console.error);
    }, 3000);

    return () => clearInterval(interval);
  }, [conversationId, streamActive, safeHistory]); // eslint-disable-line react-hooks/exhaustive-deps

  // Refetch messages when returning from admin (feedback state may have changed)
  const prevRefreshRef = useRef(chatRefreshKey);
  useEffect(() => {
    if (prevRefreshRef.current === chatRefreshKey) return;
    prevRefreshRef.current = chatRefreshKey;
    if (conversationId && !streamActive) {
      loadHistory(conversationId);
    }
  }, [chatRefreshKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // When stream finishes naturally (not by clearStreamState), reload messages from DB
  const prevActiveRef = useRef(streamActive);
  useEffect(() => {
    const wasActive = prevActiveRef.current;
    prevActiveRef.current = streamActive;

    // Only act on natural stream completion (active→false with convId still set).
    // If clearStreamState was called first, convId is already null → skip.
    if (wasActive && !streamActive && streamConvId !== null) {
      // Tool request pending — keep stream state so ToolRequestCard stays visible
      if (useStreamStore.getState().toolRequest) return;

      const finishedConvId = streamConvId;
      const currentConvId = useAppStore.getState().conversationId;

      if (currentConvId === finishedConvId) {
        // User is still viewing this conversation — reload from DB
        const epoch = loadEpochRef.current;
        getMessages(finishedConvId)
          .then((msgs) => {
            if (epoch === loadEpochRef.current &&
                useAppStore.getState().conversationId === finishedConvId) {
              historyConvIdRef.current = finishedConvId;
              const converted = convertMessages(msgs);
              // toolError는 DB에 저장 안 됨 — 스트림 메시지에서 마지막 assistant 기준으로 복원
              const streamMsgs = useStreamStore.getState().messages;
              const lastStreamErr = [...streamMsgs].reverse().find(m => m.role === 'assistant' && m.toolError);
              if (lastStreamErr?.toolError && converted.length > 0 && converted[converted.length - 1].role === 'assistant') {
                converted[converted.length - 1] = { ...converted[converted.length - 1], toolError: lastStreamErr.toolError };
              }
              setHistoryMessages(converted);
            }
          })
          .catch(console.error)
          .finally(() => {
            if (!useStreamStore.getState().active) {
              clearStreamState();
            }
          });
      } else {
        // User navigated away — clear stream state immediately
        clearStreamState();
      }
    }
  }, [streamActive, streamConvId]);

  // Smart auto-scroll
  useEffect(() => {
    if (!userScrolledUpRef.current) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [displayMessages, statusMessage]);

  const handleScroll = () => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 100;
    userScrolledUpRef.current = !atBottom;
  };

  const handleStop = () => {
    stopChatStream();
  };

  const handleSubmit = async () => {
    if (!input.trim() || streamActive || !namespace) return;
    const question = input.trim();
    setInput('');
    userScrolledUpRef.current = false;

    // Increment epoch to invalidate any in-flight getMessages from previous stream completion
    loadEpochRef.current++;

    // 자동 감지: 질문 내용 기반으로 업무구분 자동 탐지
    let resolvedCategory: string | null = category || null;
    if (category === '__auto__') {
      resolvedCategory = await suggestCategory(namespace, question).catch(() => null);
    }

    const startConvId = conversationId;
    startChatStream({
      namespace,
      question,
      wVector: searchConfig.wVector,
      wKeyword: searchConfig.wKeyword,
      topK: searchConfig.topK,
      conversationId,
      category: resolvedCategory,
      agentType: useHttpTool ? 'mcp_tool' : (selectedAgent ?? 'knowledge_rag'),
      onConversationCreated: (id) => {
        // Guard: if stream was already stopped/cleared, don't navigate
        if (!useStreamStore.getState().active) return;
        const currentConvId = useAppStore.getState().conversationId;
        if (currentConvId === startConvId && currentConvId !== id) {
          setConversationId(id);
        }
      },
    });
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <div className="flex flex-col h-full">
      {/* Messages area */}
      <div
        ref={scrollContainerRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto p-6 space-y-6"
      >
        {isTrimmed && displayMessages.length > 0 && (
          <div className="flex items-center gap-2 px-4 py-2.5 bg-slate-800/60 border border-slate-700 rounded-lg text-slate-400 text-xs">
            <AlertCircle className="w-3.5 h-3.5 flex-shrink-0" />
            <span>이전 대화는 보관 정책에 따라 삭제되었습니다.</span>
          </div>
        )}

        {displayMessages.length === 0 && (
          <div className="flex items-center justify-center h-full text-slate-500">
            <div className="text-center">
              <p className="text-4xl mb-4">⚡</p>
              <p className="text-lg font-medium text-slate-400">Ops-Navigator</p>
              <p className="text-sm mt-2 text-slate-500">운영 관련 질문을 입력하세요</p>
              <p className="text-xs mt-1 text-slate-500">Ctrl+Enter로 전송</p>
            </div>
          </div>
        )}

        {displayMessages.map((msg, i) => {
          // Tool request 대기 중 빈 assistant 메시지 숨김
          if (msg.role === 'assistant' && !msg.content && toolRequest && isStreamHere) return null;
          return (
            <MessageItem key={`${conversationId ?? 'new'}-${i}`} message={msg} namespace={namespace} />
          );
        })}

        {/* Pipeline steps — inline below last message */}
        {isLoading && streamSteps.length > 0 && (
          <PipelineStepsToggle steps={streamSteps} />
        )}

        {/* Tool request card — awaiting user approval */}
        {toolRequest && isStreamHere && (
          <ToolRequestCard
            event={toolRequest}
            onApprove={(toolId, params) => {
              useStreamStore.setState({ toolRequest: null });
              // 승인된 도구로 재요청
              loadEpochRef.current++;
              startChatStream({
                namespace: namespace!,
                question: [...displayMessages].reverse().find((m) => m.role === 'user')?.content || '',
                agentType: 'mcp_tool',
                wVector: searchConfig.wVector,
                wKeyword: searchConfig.wKeyword,
                topK: searchConfig.topK,
                conversationId,
                category: category || null,
                approvedTool: { tool_id: toolId, params },
                onConversationCreated: (id) => {
                  if (!useStreamStore.getState().active) return;
                  const currentConvId = useAppStore.getState().conversationId;
                  if (currentConvId !== id) setConversationId(id);
                },
              });
            }}
            onSelectTool={(toolId) => {
              useStreamStore.setState({ toolRequest: null });
              // 사용자가 직접 선택한 도구로 파라미터 추출 재요청
              loadEpochRef.current++;
              startChatStream({
                namespace: namespace!,
                question: [...displayMessages].reverse().find((m) => m.role === 'user')?.content || '',
                agentType: 'mcp_tool',
                wVector: searchConfig.wVector,
                wKeyword: searchConfig.wKeyword,
                topK: searchConfig.topK,
                conversationId,
                category: category || null,
                selectedToolId: toolId,
                onConversationCreated: (id) => {
                  if (!useStreamStore.getState().active) return;
                  const currentConvId = useAppStore.getState().conversationId;
                  if (currentConvId !== id) setConversationId(id);
                },
              });
            }}
            onReject={() => {
              clearStreamState();
              if (conversationId) loadHistory(conversationId);
            }}
            onFallback={() => {
              useStreamStore.setState({ toolRequest: null });
              // 도구 없이 knowledge_rag로 재요청
              loadEpochRef.current++;
              startChatStream({
                namespace: namespace!,
                question: [...displayMessages].reverse().find((m) => m.role === 'user')?.content || '',
                wVector: searchConfig.wVector,
                wKeyword: searchConfig.wKeyword,
                topK: searchConfig.topK,
                conversationId,
                category: category || null,
                onConversationCreated: (id) => {
                  if (!useStreamStore.getState().active) return;
                  const currentConvId = useAppStore.getState().conversationId;
                  if (currentConvId !== id) setConversationId(id);
                },
              });
            }}
          />
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input area */}
      <div className="border-t border-slate-700 p-4 bg-slate-800">
        {/* MCP tool toggle */}
        <div className="flex items-center gap-2 mb-2">
          <button
            onClick={() => setUseHttpTool((v) => !v)}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium border transition-colors ${
              useHttpTool
                ? 'bg-emerald-900/50 text-emerald-400 border-emerald-700/50'
                : 'bg-slate-700/50 text-slate-500 border-slate-600/50 hover:text-slate-400'
            }`}
            title="MCP 도구를 사용하여 외부 API를 호출합니다"
          >
            <Wrench className="w-3.5 h-3.5" />
            MCP 도구 {useHttpTool ? 'ON' : 'OFF'}
          </button>
        </div>
        <div className="flex gap-3 items-end">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={selectedAgent === 'text2sql' ? 'DB에 대해 자연어로 질문하세요... (Ctrl+Enter로 전송)' : useHttpTool ? 'MCP 도구를 활용한 질문을 입력하세요... (Ctrl+Enter로 전송)' : '질문을 입력하세요... (Ctrl+Enter로 전송)'}
            rows={2}
            disabled={!namespace || isLoading}
            className="flex-1 bg-slate-900 border border-slate-700 rounded-xl px-4 py-3 text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500 resize-none disabled:opacity-50 text-sm"
          />
          {isLoading ? (
            <button
              onClick={handleStop}
              className="p-3 bg-rose-600 hover:bg-rose-500 rounded-xl transition-colors flex-shrink-0"
              title="생성 중단"
            >
              <Square className="w-5 h-5 text-white fill-white" />
            </button>
          ) : (
            <button
              onClick={handleSubmit}
              disabled={!input.trim() || !namespace || streamActive}
              className="p-3 bg-indigo-600 hover:bg-indigo-500 disabled:bg-slate-700 disabled:opacity-50 rounded-xl transition-colors flex-shrink-0"
              title="전송 (Ctrl+Enter)"
            >
              <Send className="w-5 h-5 text-white" />
            </button>
          )}
        </div>
        {!namespace && (
          <p className="text-xs text-slate-500 mt-2">
            왼쪽 사이드바에서 파트를 선택하세요.
          </p>
        )}
      </div>
    </div>
  );
}
