import { create } from 'zustand';
import { streamChat } from '../api/chat';
import type { ChatMessage, KnowledgeResult, SSEToolRequestEvent, SSESqlEvent, SSETableEvent, SSEChartEvent } from '../types';

// Module-level (non-reactive, not in Zustand state)
let _controller: AbortController | null = null;

export interface PipelineStep {
  step: string;
  message: string;
  done: boolean;
}

interface StreamState {
  /** Conversation ID being streamed (null = new conv, not yet assigned) */
  convId: number | null;
  /** The conversationId at the time the stream was started (stable reference) */
  originConvId: number | null;
  /** Messages accumulated during streaming (user + assistant) */
  messages: ChatMessage[];
  /** Status line text */
  status: string;
  /** Accumulated pipeline steps for progress display */
  steps: PipelineStep[];
  /** Is a stream currently active */
  active: boolean;
  /** Backend message ID for the assistant message (set from meta event) */
  assistantMessageId: number | null;
  /** Tool request awaiting user approval */
  toolRequest: SSEToolRequestEvent | null;
  /** Tool execution result preview */
  toolResult: string | null;
  /** Tool error message */
  toolError: string | null;
  /** Text2SQL: generated SQL */
  sqlResult: SSESqlEvent | null;
  /** Text2SQL: query result table */
  tableResult: SSETableEvent | null;
}

export const useStreamStore = create<StreamState>(() => ({
  convId: null,
  originConvId: null,
  messages: [],
  status: '',
  steps: [],
  active: false,
  assistantMessageId: null,
  toolRequest: null,
  toolResult: null,
  toolError: null,
  sqlResult: null,
  tableResult: null,
}));

// ── Public actions ──────────────────────────────────────────────────────────

export function startChatStream(params: {
  namespace: string;
  question: string;
  agentType?: string;
  wVector: number;
  wKeyword: number;
  topK: number;
  conversationId: number | null;
  category?: string | null;
  approvedTool?: { tool_id: number; params: Record<string, string> } | null;
  selectedToolId?: number | null;
  onConversationCreated: (id: number) => void;
}) {
  // Abort any existing stream & detach controller so old _runStream can't mutate state
  if (_controller) {
    const old = _controller;
    _controller = null;
    old.abort();
  }

  _controller = new AbortController();
  const controller = _controller;

  const set = useStreamStore.setState;

  // Initialise store
  set({
    convId: params.conversationId,
    originConvId: params.conversationId,
    messages: [
      { role: 'user', content: params.question },
      { role: 'assistant', content: '', isStreaming: true, question: params.question },
    ],
    status: '',
    steps: [],
    active: true,
    assistantMessageId: null,
    toolRequest: null,
    toolResult: null,
    toolError: null,
    sqlResult: null,
    tableResult: null,
  });

  // Fire-and-forget — errors handled internally
  _runStream(params, controller).catch(() => {});
}

export function stopChatStream() {
  // Abort SSE connection — backend worker continues independently via asyncio.Task
  const c = _controller;
  _controller = null;
  c?.abort();
  if (c) {
    const state = useStreamStore.getState();
    const msgs = state.messages;
    const updated = msgs.length > 0
      ? msgs.map((m, i) => i === msgs.length - 1 ? { ...m, isStreaming: false } : m)
      : msgs;
    useStreamStore.setState({ active: false, messages: updated });
  }
}

export function clearStreamState() {
  useStreamStore.setState({
    convId: null,
    originConvId: null,
    messages: [],
    status: '',
    steps: [],
    active: false,
    assistantMessageId: null,
    toolRequest: null,
    toolResult: null,
    toolError: null,
    sqlResult: null,
    tableResult: null,
  });
}

// ── Internal stream runner ──────────────────────────────────────────────────

async function _runStream(
  params: {
    namespace: string;
    question: string;
    agentType?: string;
    wVector: number;
    wKeyword: number;
    topK: number;
    conversationId: number | null;
    category?: string | null;
    approvedTool?: { tool_id: number; params: Record<string, string> } | null;
    selectedToolId?: number | null;
    onConversationCreated: (id: number) => void;
  },
  controller: AbortController,
) {
  const set = useStreamStore.setState;
  const get = useStreamStore.getState;

  // All state mutations go through this guard — if this stream was detached, do nothing.
  const isOwner = () => _controller === controller;

  const updateLastMessage = (updater: (msg: ChatMessage) => ChatMessage) => {
    if (!isOwner()) return;
    const msgs = get().messages;
    if (msgs.length === 0) return;
    set({ messages: msgs.map((m, i) => (i === msgs.length - 1 ? updater(m) : m)) });
  };

  try {
    const stream = streamChat({
      namespace: params.namespace,
      question: params.question,
      agentType: params.agentType,
      wVector: params.wVector,
      wKeyword: params.wKeyword,
      topK: params.topK,
      conversationId: params.conversationId,
      category: params.category,
      approvedTool: params.approvedTool ?? null,
      selectedToolId: params.selectedToolId ?? null,
      signal: controller.signal,
    });

    for await (const event of stream) {
      // If aborted (stopChatStream), the AbortError will be caught below.
      // Backend worker continues independently via asyncio.Task.
      if (!isOwner()) return;

      if (event.type === 'status') {
        const stepId = (event as { step?: string }).step ?? '';
        const msg = event.message as string;
        const prev = get().steps.map((s) => ({ ...s, done: true }));
        set({ status: msg, steps: [...prev, { step: stepId, message: msg, done: false }] });
      } else if (event.type === 'meta') {
        const meta = event as {
          type: 'meta';
          conversation_id: number | null;
          mapped_term: string | null;
          results: KnowledgeResult[];
        };
        if (meta.conversation_id) {
          set({ convId: meta.conversation_id });
          params.onConversationCreated(meta.conversation_id);
        }
        const metaMsgId = (meta as { message_id?: number }).message_id;
        if (metaMsgId) {
          set({ assistantMessageId: metaMsgId });
        }
        updateLastMessage((m) => ({
          ...m,
          mapped_term: meta.mapped_term,
          results: meta.results ?? [],
        }));
      } else if (event.type === 'token') {
        const token = (event as { type: 'token'; data: string }).data;
        updateLastMessage((m) => ({ ...m, content: m.content + token }));
      } else if (event.type === 'tool_request') {
        set({ toolRequest: event as SSEToolRequestEvent });
        // 도구 요청 시 스트리밍 중단 상태로 전환 (사용자 입력 대기)
        updateLastMessage((m) => ({ ...m, isStreaming: false }));
      } else if (event.type === 'tool_result') {
        const data = (event as { data: string }).data;
        set({ toolResult: data });
      } else if (event.type === 'tool_error') {
        const msg = (event as { message: string }).message;
        set({ toolError: msg });
        updateLastMessage((m) => ({ ...m, toolError: msg }));
        // isStreaming 유지 — HTTP 실패 후 RAG 기반 LLM 답변이 이어짐
      } else if (event.type === 'sql') {
        const sqlEvent = event as SSESqlEvent;
        set({ sqlResult: sqlEvent });
        updateLastMessage((m) => ({ ...m, sqlResult: { sql: sqlEvent.sql, reasoning: sqlEvent.reasoning, cached: sqlEvent.cached } }));
      } else if (event.type === 'table') {
        const tableEvent = event as SSETableEvent;
        set({ tableResult: tableEvent });
        updateLastMessage((m) => ({
          ...m,
          tableResult: { columns: tableEvent.columns, rows: tableEvent.rows, row_count: tableEvent.row_count, truncated: tableEvent.truncated },
        }));
      } else if (event.type === 'chart') {
        const chartEvent = event as SSEChartEvent;
        updateLastMessage((m) => ({ ...m, chartResult: chartEvent.chart }));
      } else if (event.type === 'done') {
        const doneEvent = event as { type: 'done'; message_id?: number; status?: string; audit_id?: number };
        updateLastMessage((m) => ({ ...m, isStreaming: false, messageId: doneEvent.message_id, status: doneEvent.status, auditId: doneEvent.audit_id ?? null }));
      }
    }
  } catch (err) {
    if (!isOwner()) return; // stream was detached, don't touch state
    if (err instanceof Error && err.name === 'AbortError') {
      updateLastMessage((m) => ({ ...m, isStreaming: false }));
    } else {
      console.error(err);
      updateLastMessage((m) => ({
        ...m,
        content: '[오류가 발생했습니다. 다시 시도해 주세요.]',
        isStreaming: false,
      }));
    }
  } finally {
    if (isOwner()) {
      _controller = null;
      set({ active: false, status: '', steps: get().steps.map((s) => ({ ...s, done: true })) });
    }
  }
}
