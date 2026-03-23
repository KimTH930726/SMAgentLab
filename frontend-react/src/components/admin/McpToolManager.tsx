import { useState, useEffect, useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Wrench, Plus, Trash2, Sparkles, Save, X, Search, History, RefreshCw } from 'lucide-react';
import { useAppStore } from '../../store/useAppStore';
import { useAuthStore } from '../../store/useAuthStore';
import { getNamespaces, getNamespacesDetail } from '../../api/namespaces';
import { sortNamespacesByUserPart } from '../../utils/sortNamespaces';
import { listMcpTools, createMcpTool, updateMcpTool, deleteMcpTool, toggleMcpTool, autocompleteMcpTool, testMcpTool, listMcpToolLogs, getMcpToolLogStats } from '../../api/mcpTools';
import type { McpToolTestResult, McpToolLog, McpToolLogStats, McpToolLogsResponse } from '../../api/mcpTools';
import { Modal } from '../ui/Modal';
import { Button } from '../ui/Button';
import { CodeBlock } from '../ui/CodeBlock';
import { Badge } from '../ui/Badge';
import { PaginationInfo, PaginationNav, useClientPaging } from '../ui/Pagination';
import type { McpTool, McpToolParam, McpToolCreatePayload } from '../../types';

const PLACEHOLDER_TEXT = `API 설명을 자유롭게 입력하세요. LLM이 자동으로 구조화합니다.

예) 큐레이션 섹션 리스트를 조회하는 POST API
URL: http://display.example.com/api/v2/section-list
Headers: Authorization: Bearer eyJhbG...
파라미터:
 - sectionChannelDiv (필수, string): 채널구분 (예: 40)
 - lastId (선택, number): 마지막 ID (예: 0)
 - sectionIdList (선택, array): 섹션 ID 목록 (예: [])
응답: { "sectionList": [...], "lastId": 0 }`;

interface FormState {
  name: string;
  description: string;
  method: string;
  hub_base_url: string;
  tool_path: string;
  headers: string;
  param_schema: McpToolParam[];
  response_example: string;
  timeout_sec: number;
  max_response_kb: number;
}

const EMPTY_FORM: FormState = {
  name: '', description: '', method: 'GET', hub_base_url: '', tool_path: '',
  headers: '{}', param_schema: [], response_example: '',
  timeout_sec: 10, max_response_kb: 50,
};

const EMPTY_PARAM: McpToolParam = { name: '', type: 'string', required: true, description: '', example: '' };

type McpSubTab = 'tools' | 'logs';

export function McpToolManager() {
  const storeNamespace = useAppStore((s) => s.namespace);
  const selectedAgent = useAppStore((s) => s.selectedAgent ?? 'knowledge_rag');
  const user = useAuthStore((s) => s.user);
  const [namespace, setNamespace] = useState(storeNamespace || '');
  const [subTab, setSubTab] = useState<McpSubTab>('tools');

  useEffect(() => {
    if (storeNamespace) setNamespace(storeNamespace);
  }, [storeNamespace]);


  const { data: namespaces = [] } = useQuery({
    queryKey: ['namespaces'],
    queryFn: getNamespaces,
    staleTime: 30_000,
  });
  const { data: nsDetails = [] } = useQuery({
    queryKey: ['namespaces-detail'],
    queryFn: getNamespacesDetail,
    staleTime: 30_000,
  });
  const sortedNamespaces = sortNamespacesByUserPart(namespaces, user?.part, nsDetails);

  // 로드된 목록에 현재 namespace가 없으면 초기화 (store 잔존값 방지)
  useEffect(() => {
    if (sortedNamespaces.length > 0 && namespace && !sortedNamespaces.includes(namespace)) {
      setNamespace('');
    }
  }, [sortedNamespaces, namespace]);

  const [tools, setTools] = useState<McpTool[]>([]);
  const [loading, setLoading] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [rawText, setRawText] = useState('');
  const [autoCompleting, setAutoCompleting] = useState(false);
  const [error, setError] = useState('');
  const [selectedTool, setSelectedTool] = useState<McpTool | null>(null);
  const [toolPage, setToolPage] = useState(1);
  const [toolPageSize, setToolPageSize] = useState(30);
  const [testParams, setTestParams] = useState<Record<string, string>>({});
  const [testResult, setTestResult] = useState<McpToolTestResult | null>(null);
  const [testLoading, setTestLoading] = useState(false);

  const fetchTools = useCallback(async () => {
    if (!namespace) return;
    setLoading(true);
    try {
      setTools(await listMcpTools(namespace, selectedAgent));
    } finally {
      setLoading(false);
    }
  }, [namespace]);

  useEffect(() => { fetchTools(); }, [fetchTools]);

  const handleAutoComplete = async () => {
    if (!namespace || rawText.trim().length < 10) return;
    setAutoCompleting(true);
    setError('');
    try {
      const result = await autocompleteMcpTool(namespace, rawText);
      if (result.status === 'ok' && result.tool) {
        const t = result.tool as Record<string, unknown>;
        setForm({
          name: (t.name as string) || '',
          description: (t.description as string) || '',
          method: (t.method as string) || 'GET',
          hub_base_url: (t.hub_base_url as string) || (t.url as string) || '',
          tool_path: (t.tool_path as string) || '',
          headers: JSON.stringify(t.headers || {}, null, 2),
          param_schema: (t.param_schema as McpToolParam[]) || [],
          response_example: t.response_example ? JSON.stringify(t.response_example, null, 2) : '',
          timeout_sec: 10,
          max_response_kb: 50,
        });
        setShowForm(true);
      } else {
        setError(result.message || 'LLM 자동완성 실패');
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setAutoCompleting(false);
    }
  };

  const handleSubmit = async () => {
    if (!namespace) return;
    setError('');
    try {
      let parsedHeaders: Record<string, string> = {};
      try { parsedHeaders = JSON.parse(form.headers); } catch { /* ignore */ }

      let parsedResponse: Record<string, unknown> | undefined;
      if (form.response_example.trim()) {
        try { parsedResponse = JSON.parse(form.response_example); } catch { /* ignore */ }
      }

      if (editingId) {
        await updateMcpTool(editingId, {
          name: form.name, description: form.description, method: form.method,
          hub_base_url: form.hub_base_url, tool_path: form.tool_path, headers: parsedHeaders, param_schema: form.param_schema,
          response_example: parsedResponse ?? null, timeout_sec: form.timeout_sec,
          max_response_kb: form.max_response_kb,
        });
      } else {
        const payload: McpToolCreatePayload = {
          namespace, name: form.name, description: form.description,
          method: form.method, hub_base_url: form.hub_base_url, tool_path: form.tool_path, headers: parsedHeaders,
          param_schema: form.param_schema, response_example: parsedResponse ?? null,
          timeout_sec: form.timeout_sec, max_response_kb: form.max_response_kb,
          agent_type: selectedAgent,
        };
        await createMcpTool(payload);
      }
      setForm(EMPTY_FORM);
      setShowForm(false);
      setEditingId(null);
      setRawText('');
      setSelectedTool(null);
      fetchTools();
    } catch (e) {
      setError(String(e));
    }
  };

  const openEdit = (tool: McpTool) => {
    setEditingId(tool.id);
    setForm({
      name: tool.name,
      description: tool.description,
      method: tool.method,
      hub_base_url: tool.hub_base_url || '',
      tool_path: tool.tool_path || '',
      headers: JSON.stringify(tool.headers, null, 2),
      param_schema: tool.param_schema,
      response_example: tool.response_example ? JSON.stringify(tool.response_example, null, 2) : '',
      timeout_sec: tool.timeout_sec,
      max_response_kb: tool.max_response_kb,
    });
    setShowForm(true);
    setSelectedTool(null);
  };

  const handleDelete = async (id: number) => {
    if (!confirm('이 도구를 삭제하시겠습니까?')) return;
    await deleteMcpTool(id);
    setSelectedTool(null);
    fetchTools();
  };

  const handleToggle = async (tool: McpTool) => {
    await toggleMcpTool(tool.id, !tool.is_active);
    fetchTools();
  };

  const addParam = () => setForm((f) => ({ ...f, param_schema: [...f.param_schema, { ...EMPTY_PARAM }] }));
  const removeParam = (idx: number) => setForm((f) => ({
    ...f, param_schema: f.param_schema.filter((_, i) => i !== idx),
  }));
  const updateParam = (idx: number, key: keyof McpToolParam, value: unknown) => {
    setForm((f) => ({
      ...f,
      param_schema: f.param_schema.map((p, i) => i === idx ? { ...p, [key]: value } : p),
    }));
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-200 flex items-center gap-2">
          <Wrench className="w-5 h-5 text-emerald-400" />
          MCP 도구 관리
          {namespace && <span className="text-sm font-normal text-slate-500 ml-2">({namespace})</span>}
        </h2>
        <button
          onClick={() => { setShowForm(true); setEditingId(null); setForm(EMPTY_FORM); setRawText(''); }}
          disabled={!namespace}
          className="flex items-center gap-2 px-4 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white rounded-lg text-sm font-medium transition-colors"
        >
          <Plus className="w-4 h-4" /> 새 도구
        </button>
      </div>

      {/* 파트 선택 */}
      <div>
        <label className="block text-xs font-medium text-slate-400 mb-1.5">파트</label>
        <select
          value={namespace}
          onChange={(e) => setNamespace(e.target.value)}
          className="w-48 bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
        >
          <option value="">선택...</option>
          {sortedNamespaces.map((ns) => <option key={ns} value={ns}>{ns}</option>)}
        </select>
      </div>

      {!namespace ? (
        <div className="text-center py-10 text-slate-500">파트를 선택하세요.</div>
      ) : (
        <>
      {/* 서브탭 바 */}
      <div className="flex gap-1 mb-4 border-b border-slate-700">
        <button
          onClick={() => setSubTab('tools')}
          className={`flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
            subTab === 'tools'
              ? 'text-indigo-400 border-indigo-500'
              : 'text-slate-400 border-transparent hover:text-slate-200'
          }`}
        >
          <Wrench className="w-3.5 h-3.5" />
          도구 관리
        </button>
        <button
          onClick={() => setSubTab('logs')}
          className={`flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
            subTab === 'logs'
              ? 'text-indigo-400 border-indigo-500'
              : 'text-slate-400 border-transparent hover:text-slate-200'
          }`}
        >
          <History className="w-3.5 h-3.5" />
          호출 로그
        </button>
      </div>

      {subTab === 'tools' && (
        <>
          {/* LLM 자동완성 입력 */}
          {showForm && !editingId && (
            <div className="bg-slate-800 border border-slate-700 rounded-lg p-4 space-y-3">
              <label className="text-sm font-medium text-slate-300">자연어로 API 정보 입력</label>
              <textarea
                value={rawText}
                onChange={(e) => setRawText(e.target.value)}
                placeholder={PLACEHOLDER_TEXT}
                rows={8}
                className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:border-emerald-500 focus:outline-none"
              />
              <button
                onClick={handleAutoComplete}
                disabled={autoCompleting || rawText.trim().length < 10}
                className="flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white rounded-lg text-sm font-medium transition-colors"
              >
                <Sparkles className="w-4 h-4" />
                {autoCompleting ? 'LLM 분석 중...' : 'LLM 자동완성'}
              </button>
            </div>
          )}

          {/* 구조화된 폼 */}
          {showForm && (form.name || editingId) && (
            <div className="bg-slate-800 border border-slate-700 rounded-lg p-4 space-y-4">
              <h3 className="text-sm font-medium text-slate-300">
                {editingId ? '도구 수정' : '자동완성 결과 확인'}
              </h3>
              {error && <p className="text-red-400 text-sm">{error}</p>}

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="text-xs text-slate-400">이름</label>
                  <input value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                    className="w-full bg-slate-900 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-200 focus:border-emerald-500 focus:outline-none" />
                </div>
                <div>
                  <label className="text-xs text-slate-400">Method</label>
                  <select value={form.method} onChange={(e) => setForm((f) => ({ ...f, method: e.target.value }))}
                    className="w-full bg-slate-900 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-200 focus:border-emerald-500 focus:outline-none">
                    {['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map((m) => <option key={m}>{m}</option>)}
                  </select>
                </div>
              </div>

              <div>
                <label className="block text-xs text-slate-400 mb-1">MCP Hub Base URL</label>
                <input
                  value={form.hub_base_url}
                  onChange={(e) => setForm((f) => ({ ...f, hub_base_url: e.target.value }))}
                  placeholder="https://api-gateway.internal/mcp"
                  className="w-full bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500 font-mono"
                />
                <p className="text-xs text-slate-500 mt-0.5">MCP Hub API Gateway 기본 URL</p>
              </div>
              <div>
                <label className="block text-xs text-slate-400 mb-1">Tool Path</label>
                <input
                  value={form.tool_path}
                  onChange={(e) => setForm((f) => ({ ...f, tool_path: e.target.value }))}
                  placeholder="/tools/deploy"
                  className="w-full bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500 font-mono"
                />
                <p className="text-xs text-slate-500 mt-0.5">
                  호출 URL: <span className="text-indigo-400 font-mono">{form.hub_base_url || 'https://...'}{form.tool_path || '/path'}</span>
                </p>
              </div>

              <div>
                <label className="text-xs text-slate-400">설명</label>
                <input value={form.description} onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
                  className="w-full bg-slate-900 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-200 focus:border-emerald-500 focus:outline-none" />
              </div>

              <div>
                <div className="flex items-center justify-between mb-2">
                  <label className="text-xs text-slate-400">Headers</label>
                  <button
                    onClick={() => {
                      try {
                        const h = JSON.parse(form.headers) as Record<string, string>;
                        h[''] = '';
                        setForm((f) => ({ ...f, headers: JSON.stringify(h, null, 2) }));
                      } catch {
                        setForm((f) => ({ ...f, headers: JSON.stringify({ '': '' }, null, 2) }));
                      }
                    }}
                    className="text-xs text-emerald-400 hover:text-emerald-300"
                  >
                    + 추가
                  </button>
                </div>
                {(() => {
                  let entries: [string, string][] = [];
                  try { entries = Object.entries(JSON.parse(form.headers) as Record<string, string>); } catch { /* */ }
                  if (entries.length === 0) {
                    return (
                      <button
                        type="button"
                        onClick={() => setForm((f) => ({ ...f, headers: JSON.stringify({ '': '' }, null, 2) }))}
                        className="w-full border border-dashed border-slate-600 rounded-lg py-3 text-xs text-slate-500 hover:text-emerald-400 hover:border-emerald-500/50 transition-colors"
                      >
                        + 헤더 추가 (예: Authorization)
                      </button>
                    );
                  }
                  return entries.map(([k, v], idx) => (
                    <div key={idx} className="flex gap-2 mb-2 items-center">
                      <input
                        value={k}
                        onChange={(e) => {
                          const newEntries = [...entries];
                          newEntries[idx] = [e.target.value, newEntries[idx][1]];
                          setForm((f) => ({ ...f, headers: JSON.stringify(Object.fromEntries(newEntries), null, 2) }));
                        }}
                        placeholder="Key (예: Authorization)"
                        className="w-40 shrink-0 bg-slate-900 border border-slate-600 rounded px-2 py-1 text-xs text-slate-200 font-mono focus:outline-none focus:border-emerald-500"
                      />
                      <input
                        value={v}
                        onChange={(e) => {
                          const newEntries = [...entries];
                          newEntries[idx] = [newEntries[idx][0], e.target.value];
                          setForm((f) => ({ ...f, headers: JSON.stringify(Object.fromEntries(newEntries), null, 2) }));
                        }}
                        placeholder="Value"
                        className="flex-1 bg-slate-900 border border-slate-600 rounded px-2 py-1 text-xs text-slate-200 font-mono focus:outline-none focus:border-emerald-500"
                      />
                      <button
                        onClick={() => {
                          const newEntries = entries.filter((_, i) => i !== idx);
                          setForm((f) => ({ ...f, headers: JSON.stringify(Object.fromEntries(newEntries), null, 2) }));
                        }}
                        className="text-red-400 hover:text-red-300 shrink-0"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  ));
                })()}
              </div>

              {/* 파라미터 스키마 */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <label className="text-xs text-slate-400">파라미터</label>
                  <button onClick={addParam} className="text-xs text-emerald-400 hover:text-emerald-300">+ 추가</button>
                </div>
                {form.param_schema.map((p, idx) => (
                  <div key={idx} className="flex gap-2 mb-2 items-center">
                    <input value={p.name} onChange={(e) => updateParam(idx, 'name', e.target.value)}
                      placeholder="이름" className="w-28 shrink-0 bg-slate-900 border border-slate-600 rounded px-2 py-1 text-xs text-slate-200 focus:outline-none" />
                    <select value={p.type} onChange={(e) => updateParam(idx, 'type', e.target.value)}
                      className="w-20 shrink-0 bg-slate-900 border border-slate-600 rounded px-2 py-1 text-xs text-slate-200 focus:outline-none">
                      <option>string</option><option>number</option><option>boolean</option>
                    </select>
                    <label className="flex items-center gap-1 text-xs text-slate-400 shrink-0">
                      <input type="checkbox" checked={p.required} onChange={(e) => updateParam(idx, 'required', e.target.checked)} />
                      필수
                    </label>
                    <input value={p.description} onChange={(e) => updateParam(idx, 'description', e.target.value)}
                      placeholder="설명" className="w-48 shrink-0 bg-slate-900 border border-slate-600 rounded px-2 py-1 text-xs text-slate-200 focus:outline-none" />
                    <input value={p.example ?? ''} onChange={(e) => updateParam(idx, 'example', e.target.value)}
                      placeholder="예시" className="w-28 shrink-0 bg-slate-900 border border-slate-600 rounded px-2 py-1 text-xs text-slate-200 focus:outline-none" />
                    <button onClick={() => removeParam(idx)} className="text-red-400 hover:text-red-300 shrink-0">
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                ))}
              </div>

              <div>
                <label className="text-xs text-slate-400">Response 예시 (JSON)</label>
                <textarea value={form.response_example} onChange={(e) => setForm((f) => ({ ...f, response_example: e.target.value }))}
                  rows={3}
                  className="w-full bg-slate-900 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-200 font-mono focus:border-emerald-500 focus:outline-none" />
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="text-xs text-slate-400">타임아웃 (초)</label>
                  <input type="number" value={form.timeout_sec} onChange={(e) => setForm((f) => ({ ...f, timeout_sec: +e.target.value }))}
                    min={1} max={60}
                    className="w-full bg-slate-900 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-200 focus:border-emerald-500 focus:outline-none" />
                </div>
                <div>
                  <label className="text-xs text-slate-400">최대 응답 (KB)</label>
                  <input type="number" value={form.max_response_kb} onChange={(e) => setForm((f) => ({ ...f, max_response_kb: +e.target.value }))}
                    min={1} max={500}
                    className="w-full bg-slate-900 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-200 focus:border-emerald-500 focus:outline-none" />
                </div>
              </div>

              <div className="flex gap-2">
                <button onClick={handleSubmit}
                  className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-sm font-medium transition-colors">
                  {editingId ? '수정' : '저장'}
                </button>
                <button onClick={() => { setShowForm(false); setEditingId(null); setForm(EMPTY_FORM); setError(''); }}
                  className="px-4 py-2 bg-slate-700 hover:bg-slate-600 text-white rounded-lg text-sm font-medium transition-colors">
                  취소
                </button>
              </div>
            </div>
          )}

          {/* 도구 목록 */}
          {(() => {
            const toolPaging = useClientPaging(tools, toolPageSize);
            const pagedTools = toolPaging.slice(toolPage);
            return loading ? (
              <p className="text-slate-400 text-center py-8">불러오는 중...</p>
            ) : tools.length === 0 ? (
              <p className="text-slate-500 text-center py-8">등록된 MCP 도구가 없습니다.</p>
            ) : (
              <>
              <PaginationInfo totalItems={toolPaging.totalItems} pageSize={toolPageSize} onPageSizeChange={setToolPageSize} />
              <div className="space-y-3">
              {pagedTools.map((tool) => (
                <button
                  key={tool.id}
                  type="button"
                  onClick={() => setSelectedTool(tool)}
                  className="w-full text-left bg-slate-800 border border-slate-700 rounded-lg p-4 hover:bg-slate-700/50 transition-colors"
                >
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-3">
                      <Wrench className="w-4 h-4 text-emerald-400" />
                      <span className="text-slate-200 font-medium">{tool.name}</span>
                      <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded ${
                        tool.is_active ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30' : 'bg-slate-600/30 text-slate-500 border border-slate-600/30'
                      }`}>
                        {tool.is_active ? '활성' : '비활성'}
                      </span>
                      <span className="text-xs text-slate-500 font-mono">{tool.method}</span>
                    </div>
                    {/* Toggle switch — stop propagation to not open modal */}
                    <div
                      onClick={(e) => { e.stopPropagation(); handleToggle(tool); }}
                      className="cursor-pointer"
                      title={tool.is_active ? '비활성화' : '활성화'}
                    >
                      <div className={`relative w-9 h-5 rounded-full transition-colors ${
                        tool.is_active ? 'bg-emerald-600' : 'bg-slate-600'
                      }`}>
                        <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
                          tool.is_active ? 'translate-x-4' : 'translate-x-0.5'
                        }`} />
                      </div>
                    </div>
                  </div>
                  <p className="text-sm text-slate-400 mb-1">{tool.description}</p>
                  <p className="text-xs text-slate-500 font-mono truncate">{tool.hub_base_url}{tool.tool_path}</p>
                  {tool.param_schema.length > 0 && (
                    <div className="flex gap-1 mt-2 flex-wrap">
                      {tool.param_schema.map((p) => (
                        <span key={p.name} className={`text-[10px] px-1.5 py-0.5 rounded ${
                          p.required ? 'bg-amber-900/30 text-amber-400' : 'bg-slate-700 text-slate-400'
                        }`}>
                          {p.name}{p.required ? '*' : ''}
                        </span>
                      ))}
                    </div>
                  )}
                </button>
              ))}
            </div>
              <PaginationNav page={toolPage} totalPages={toolPaging.totalPages} onPageChange={setToolPage} />
              </>
            );
          })()}

          {/* 도구 상세 모달 — 테스트 호출 + 수정/삭제 */}
          {selectedTool && (() => {
            const tool = selectedTool;

            const initParams = () => {
              const init: Record<string, string> = {};
              for (const p of tool.param_schema) {
                init[p.name] = p.example ?? '';
              }
              return init;
            };

            // init params on first open
            if (Object.keys(testParams).length === 0 && tool.param_schema.length > 0) {
              setTestParams(initParams());
            }

            const handleTest = async () => {
              setTestLoading(true);
              setTestResult(null);
              const rawParams = { ...testParams };
              if (Object.keys(rawParams).length === 0) {
                for (const p of tool.param_schema) {
                  rawParams[p.name] = p.example ?? '';
                }
              }
              // param_schema 타입에 맞게 값 변환 (string → number/boolean/array 등)
              const params: Record<string, unknown> = {};
              for (const [key, val] of Object.entries(rawParams)) {
                const schema = tool.param_schema.find((p) => p.name === key);
                if (!val && val !== '0') {
                  params[key] = val;
                  continue;
                }
                if (schema?.type === 'number') {
                  params[key] = Number(val) || 0;
                } else if (schema?.type === 'boolean') {
                  params[key] = val === 'true';
                } else {
                  // string이지만 JSON 배열/객체처럼 생긴 값은 파싱 시도
                  const trimmed = val.trim();
                  if ((trimmed.startsWith('[') && trimmed.endsWith(']')) || (trimmed.startsWith('{') && trimmed.endsWith('}'))) {
                    try { params[key] = JSON.parse(trimmed); } catch { params[key] = val; }
                  } else {
                    params[key] = val;
                  }
                }
              }
              try {
                const result = await testMcpTool(tool.id, params);
                setTestResult(result);
              } catch (e) {
                setTestResult({
                  status: 'error',
                  request: { method: tool.method, url: `${tool.hub_base_url}${tool.tool_path}`, headers: tool.headers, params: rawParams },
                  error: String(e),
                  elapsed_ms: 0,
                });
              } finally {
                setTestLoading(false);
              }
            };

            const statusColor = (code?: number) => {
              if (!code) return 'text-slate-400';
              if (code >= 200 && code < 300) return 'text-emerald-400';
              if (code >= 300 && code < 400) return 'text-amber-400';
              if (code >= 400 && code < 500) return 'text-rose-400';
              return 'text-red-500';
            };

            return (
              <Modal
                isOpen
                onClose={() => { setSelectedTool(null); setTestResult(null); setTestParams({}); }}
                title={tool.name}
                maxWidth="max-w-3xl"
              >
                <div className="space-y-5 overflow-y-auto max-h-[70vh]">
                  {/* 기본 정보 */}
                  <div className="flex items-center justify-between bg-slate-900/60 rounded-lg px-4 py-3">
                    <div className="flex items-center gap-2">
                      <Badge color={tool.is_active ? 'emerald' : 'slate'}>{tool.is_active ? '활성' : '비활성'}</Badge>
                      <span className="text-xs font-mono text-slate-400">{tool.method}</span>
                    </div>
                    <div className="flex items-center gap-3">
                      <span className="text-xs text-slate-500">타임아웃: {tool.timeout_sec}초</span>
                      <span className="text-xs text-slate-500">최대: {tool.max_response_kb}KB</span>
                      <span className="text-xs text-slate-500">ID: {tool.id}</span>
                    </div>
                  </div>

                  {/* URL + 설명 */}
                  <div>
                    <p className="text-xs text-slate-500 mb-1">URL</p>
                    <p className="text-sm text-slate-300 font-mono bg-slate-900/40 rounded-lg p-3 break-all">{tool.hub_base_url}{tool.tool_path}</p>
                  </div>
                  <p className="text-xs text-slate-400">{tool.description}</p>

                  {/* Headers */}
                  {Object.keys(tool.headers).length > 0 && (
                    <div>
                      <p className="text-xs text-slate-500 mb-1.5">Headers</p>
                      <CodeBlock code={JSON.stringify(tool.headers, null, 2)} language="json" />
                    </div>
                  )}

                  {/* 파라미터 스키마 */}
                  {tool.param_schema.length > 0 && (
                    <div>
                      <p className="text-xs text-slate-500 mb-1.5">파라미터 ({tool.param_schema.length}개)</p>
                      <div className="bg-slate-900/40 rounded-lg overflow-hidden">
                        <table className="w-full text-xs">
                          <thead>
                            <tr className="border-b border-slate-700">
                              <th className="text-left px-3 py-2 text-slate-500 font-medium">이름</th>
                              <th className="text-left px-3 py-2 text-slate-500 font-medium">타입</th>
                              <th className="text-left px-3 py-2 text-slate-500 font-medium">필수</th>
                              <th className="text-left px-3 py-2 text-slate-500 font-medium">설명</th>
                              <th className="text-left px-3 py-2 text-slate-500 font-medium">예시</th>
                            </tr>
                          </thead>
                          <tbody>
                            {tool.param_schema.map((p) => (
                              <tr key={p.name} className="border-b border-slate-700/50">
                                <td className="px-3 py-2 text-slate-200 font-mono">{p.name}</td>
                                <td className="px-3 py-2 text-slate-400">{p.type}</td>
                                <td className="px-3 py-2">
                                  <span className={p.required ? 'text-amber-400' : 'text-slate-500'}>{p.required ? '필수' : '선택'}</span>
                                </td>
                                <td className="px-3 py-2 text-slate-400">{p.description}</td>
                                <td className="px-3 py-2 text-slate-500 font-mono">{p.example ?? '-'}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}

                  {/* Response 예시 */}
                  {tool.response_example && (
                    <div>
                      <p className="text-xs text-slate-500 mb-1.5">Response 예시</p>
                      <CodeBlock code={JSON.stringify(tool.response_example, null, 2)} language="json" />
                    </div>
                  )}

                  {/* 테스트 호출 섹션 */}
                  <div className="border border-cyan-500/30 rounded-xl p-4 space-y-3 bg-cyan-500/5">
                    <div className="flex items-center justify-between">
                      <h4 className="text-sm font-semibold text-cyan-400 flex items-center gap-2">
                        <Wrench className="w-4 h-4" /> 테스트 호출
                      </h4>
                      <Button
                        variant="primary"
                        size="sm"
                        onClick={handleTest}
                        loading={testLoading}
                      >
                        <Search className="w-3.5 h-3.5" /> 실행
                      </Button>
                    </div>

                    {/* 파라미터 입력 */}
                    {tool.param_schema.length > 0 && (
                      <div className="space-y-2">
                        {tool.param_schema.map((p) => (
                          <div key={p.name} className="flex items-center gap-2">
                            <span className={`text-xs font-mono w-28 shrink-0 ${p.required ? 'text-amber-400' : 'text-slate-500'}`}>
                              {p.name}{p.required ? '*' : ''}
                            </span>
                            <input
                              type="text"
                              value={testParams[p.name] ?? ''}
                              onChange={(e) => setTestParams((prev) => ({ ...prev, [p.name]: e.target.value }))}
                              placeholder={p.example ?? p.description}
                              className="flex-1 bg-slate-900 border border-slate-600 rounded px-2 py-1 text-xs text-slate-200 font-mono focus:outline-none focus:border-cyan-500"
                            />
                            <span className="text-[10px] text-slate-500 w-16 shrink-0">{p.type}</span>
                          </div>
                        ))}
                      </div>
                    )}

                    {/* 테스트 결과 */}
                    {testResult && (
                      <div className="space-y-3 pt-2 border-t border-slate-700/50">
                        {/* 요청 정보 */}
                        <div>
                          <p className="text-xs text-slate-500 mb-1">Request</p>
                          <div className="bg-slate-900/60 rounded-lg p-3 text-xs font-mono space-y-1">
                            <p className="text-slate-300">{testResult.request.method} {testResult.request.url}</p>
                            {Object.keys(testResult.request.params).length > 0 && (
                              <p className="text-slate-500">params: {JSON.stringify(testResult.request.params)}</p>
                            )}
                          </div>
                        </div>

                        {/* 응답 요약 */}
                        <div className="flex items-center gap-4 bg-slate-900/60 rounded-lg px-4 py-3">
                          {testResult.status === 'ok' && testResult.response ? (
                            <>
                              <div className="text-center">
                                <p className="text-[10px] text-slate-500">Status</p>
                                <p className={`text-lg font-bold ${statusColor(testResult.response.status_code)}`}>
                                  {testResult.response.status_code}
                                </p>
                              </div>
                              <div className="text-center">
                                <p className="text-[10px] text-slate-500">응답시간</p>
                                <p className={`text-lg font-bold ${testResult.response.elapsed_ms > 3000 ? 'text-rose-400' : testResult.response.elapsed_ms > 1000 ? 'text-amber-400' : 'text-emerald-400'}`}>
                                  {testResult.response.elapsed_ms}ms
                                </p>
                              </div>
                              <div className="text-center">
                                <p className="text-[10px] text-slate-500">크기</p>
                                <p className="text-sm font-mono text-slate-300">
                                  {testResult.response.size_bytes > 1024 ? `${(testResult.response.size_bytes / 1024).toFixed(1)}KB` : `${testResult.response.size_bytes}B`}
                                </p>
                              </div>
                              {testResult.response.truncated && (
                                <Badge color="amber">응답 잘림</Badge>
                              )}
                            </>
                          ) : (
                            <>
                              <div className="text-center">
                                <p className="text-[10px] text-slate-500">Status</p>
                                <p className="text-lg font-bold text-rose-400">ERROR</p>
                              </div>
                              <div className="text-center">
                                <p className="text-[10px] text-slate-500">응답시간</p>
                                <p className="text-sm font-mono text-slate-300">{testResult.elapsed_ms ?? 0}ms</p>
                              </div>
                              <p className="text-xs text-rose-400 flex-1">{testResult.error}</p>
                            </>
                          )}
                        </div>

                        {/* 응답 헤더 */}
                        {testResult.response && (
                          <div>
                            <p className="text-xs text-slate-500 mb-1">Response Headers</p>
                            <CodeBlock
                              code={Object.entries(testResult.response.headers)
                                .map(([k, v]) => `${k}: ${v}`)
                                .join('\n')}
                              language="http"
                            />
                          </div>
                        )}

                        {/* 응답 본문 */}
                        {testResult.response && (
                          <div>
                            <p className="text-xs text-slate-500 mb-1">Response Body</p>
                            {testResult.response.body.trim() ? (
                              <CodeBlock
                                code={(() => {
                                  try {
                                    return JSON.stringify(JSON.parse(testResult.response!.body), null, 2);
                                  } catch {
                                    return testResult.response!.body;
                                  }
                                })()}
                                language="json"
                              />
                            ) : (
                              <div className="bg-slate-900/60 rounded-lg p-3 text-xs text-slate-500 italic">
                                응답 본문 없음 (content-length: 0) — 응답 헤더를 확인하세요.
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    )}
                  </div>

                  {/* 액션 버튼 */}
                  <div className="flex gap-2 pt-2 border-t border-slate-700">
                    <Button variant="primary" size="sm" onClick={() => openEdit(tool)}>
                      <Save className="w-3.5 h-3.5" /> 수정
                    </Button>
                    <Button variant="secondary" size="sm" onClick={() => handleDelete(tool.id)}>
                      <Trash2 className="w-3.5 h-3.5" /> 삭제
                    </Button>
                    <Button variant="secondary" size="sm" onClick={() => { setSelectedTool(null); setTestResult(null); setTestParams({}); }} className="ml-auto">
                      <X className="w-3.5 h-3.5" /> 닫기
                    </Button>
                  </div>
                </div>
              </Modal>
            );
          })()}
        </>
      )}

      {subTab === 'logs' && <McpToolLogs namespace={namespace} />}
        </>
      )}
    </div>
  );
}

// ── 도넛 차트 ─────────────────────────────────────────────────────────────────
function DonutChart({ dist, total }: { dist: Record<string, number>; total: number }) {
  if (total === 0) return null;
  const cx = 60, cy = 60, r = 45, sw = 18;
  const C = 2 * Math.PI * r;
  const COLORS: Record<string, string> = { '2xx': '#10b981', '4xx': '#f59e0b', '5xx': '#ef4444', 'error': '#6b7280' };

  const grouped: Record<string, number> = {};
  for (const [key, cnt] of Object.entries(dist)) {
    const code = parseInt(key);
    const bucket = isNaN(code) ? 'error' : code < 300 ? '2xx' : code < 500 ? '4xx' : '5xx';
    grouped[bucket] = (grouped[bucket] || 0) + cnt;
  }

  const segs: { key: string; cnt: number; segLen: number; offset: number; color: string }[] = [];
  let acc = 0;
  for (const [key, cnt] of Object.entries(grouped)) {
    const segLen = (cnt / total) * C;
    segs.push({ key, cnt, segLen, offset: -acc, color: COLORS[key] ?? '#6366f1' });
    acc += segLen;
  }

  return (
    <div className="flex items-center gap-6">
      <svg width="120" height="120" viewBox="0 0 120 120">
        {segs.map(s => (
          <circle key={s.key} cx={cx} cy={cy} r={r} fill="none" stroke={s.color}
            strokeWidth={sw} strokeDasharray={`${s.segLen} ${C}`} strokeDashoffset={s.offset}
            style={{ transform: 'rotate(-90deg)', transformOrigin: `${cx}px ${cy}px` }} />
        ))}
        <text x={cx} y={cy - 4} textAnchor="middle" fill="white" fontSize="18" fontWeight="bold">{total}</text>
        <text x={cx} y={cy + 14} textAnchor="middle" fill="#94a3b8" fontSize="9">총 호출</text>
      </svg>
      <div className="space-y-2">
        {segs.map(s => (
          <div key={s.key} className="flex items-center gap-2 text-xs">
            <div className="w-3 h-3 rounded-sm flex-shrink-0" style={{ backgroundColor: s.color }} />
            <span className="text-slate-300 font-mono w-8">{s.key}</span>
            <span className="text-slate-400">{s.cnt}회</span>
            <span className="text-slate-500">({Math.round(s.cnt / total * 100)}%)</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── MCP 호출 로그 메인 ────────────────────────────────────────────────────────
function McpToolLogs({ namespace }: { namespace: string }) {
  type Preset = '1h' | '24h' | '7d' | '30d' | 'custom';
  const PRESET_LABELS: Record<Preset, string> = { '1h': '1시간', '24h': '24시간', '7d': '7일', '30d': '30일', 'custom': '직접 입력' };
  const STATS_PAGE = 10;
  const LOG_PAGE = 20;

  const [preset, setPreset] = useState<Preset>('24h');
  const [customFrom, setCustomFrom] = useState('');
  const [customTo, setCustomTo] = useState('');

  const [stats, setStats] = useState<McpToolLogStats[]>([]);
  const [loadingStats, setLoadingStats] = useState(false);
  const [statsError, setStatsError] = useState<string | null>(null);
  const [statsPage, setStatsPage] = useState(1);

  const [modalTool, setModalTool] = useState<McpToolLogStats | null>(null);
  const [logResp, setLogResp] = useState<McpToolLogsResponse>({ items: [], total: 0, page: 1, page_size: LOG_PAGE });
  const [loadingLogs, setLoadingLogs] = useState(false);
  const [logsError, setLogsError] = useState<string | null>(null);
  const [logPage, setLogPage] = useState(1);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const getRange = useCallback((): { from?: string; to?: string } => {
    if (preset === 'custom') return { from: customFrom || undefined, to: customTo || undefined };
    const now = new Date();
    const h = { '1h': 1, '24h': 24, '7d': 168, '30d': 720 }[preset];
    return { from: new Date(now.getTime() - h * 3_600_000).toISOString(), to: now.toISOString() };
  }, [preset, customFrom, customTo]);

  const loadStats = useCallback(async () => {
    if (!namespace) return;
    setLoadingStats(true);
    setStatsError(null);
    const { from, to } = getRange();
    try {
      setStats(await getMcpToolLogStats(namespace, from, to));
      setStatsPage(1);
    } catch (e) {
      setStatsError(e instanceof Error ? e.message : '통계 조회 실패');
    } finally {
      setLoadingStats(false);
    }
  }, [namespace, getRange]);

  useEffect(() => { loadStats(); }, [loadStats]);

  const loadLogs = useCallback(async (tool: McpToolLogStats, p: number) => {
    setLoadingLogs(true);
    setLogsError(null);
    const { from, to } = getRange();
    try {
      setLogResp(await listMcpToolLogs(namespace, tool.tool_id ?? undefined, from, to, p, LOG_PAGE));
    } catch (e) {
      setLogsError(e instanceof Error ? e.message : '로그 조회 실패');
    } finally {
      setLoadingLogs(false);
    }
  }, [namespace, getRange]);

  const openModal = (stat: McpToolLogStats) => {
    setModalTool(stat);
    setLogPage(1);
    setExpandedId(null);
    setLogResp({ items: [], total: 0, page: 1, page_size: LOG_PAGE });
    loadLogs(stat, 1);
  };

  const handleLogPage = (p: number) => { setLogPage(p); if (modalTool) loadLogs(modalTool, p); };

  const statsSlice = stats.slice((statsPage - 1) * STATS_PAGE, statsPage * STATS_PAGE);
  const statsTotalPages = Math.max(1, Math.ceil(stats.length / STATS_PAGE));
  const logTotalPages = Math.max(1, Math.ceil(logResp.total / LOG_PAGE));

  if (!namespace) return <div className="text-sm text-slate-500 py-8 text-center">파트를 선택하세요.</div>;

  return (
    <div className="space-y-4">
      {/* 시간 필터 */}
      <div className="flex items-center gap-2 flex-wrap">
        {(Object.keys(PRESET_LABELS) as Preset[]).map(p => (
          <button key={p} onClick={() => setPreset(p)}
            className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors ${preset === p ? 'bg-indigo-600 text-white' : 'bg-slate-700 text-slate-300 hover:bg-slate-600'}`}>
            {PRESET_LABELS[p]}
          </button>
        ))}
        {preset === 'custom' && (
          <>
            <input type="datetime-local" value={customFrom} onChange={e => setCustomFrom(e.target.value)}
              className="bg-slate-700 border border-slate-600 rounded px-2 py-1 text-xs text-slate-200 focus:outline-none focus:border-indigo-500" />
            <span className="text-slate-500 text-xs">~</span>
            <input type="datetime-local" value={customTo} onChange={e => setCustomTo(e.target.value)}
              className="bg-slate-700 border border-slate-600 rounded px-2 py-1 text-xs text-slate-200 focus:outline-none focus:border-indigo-500" />
          </>
        )}
        <button onClick={loadStats} title="새로고침"
          className={`p-1.5 rounded text-slate-400 hover:text-slate-200 hover:bg-slate-700 ml-auto transition-colors ${loadingStats ? 'animate-spin' : ''}`}>
          <RefreshCw className="w-4 h-4" />
        </button>
      </div>

      {/* 도구별 집계 테이블 */}
      {loadingStats ? (
        <div className="text-sm text-slate-500 animate-pulse py-8 text-center">로딩 중...</div>
      ) : statsError ? (
        <div className="text-sm text-rose-400 py-8 text-center border border-dashed border-rose-500/30 rounded-lg">{statsError}</div>
      ) : stats.length === 0 ? (
        <div className="text-sm text-slate-500 py-8 text-center border border-dashed border-slate-700 rounded-lg">
          선택한 기간에 호출 로그가 없습니다.
        </div>
      ) : (
        <>
          <div className="flex justify-end">
            <PaginationNav page={statsPage} totalPages={statsTotalPages} onPageChange={setStatsPage} />
          </div>
          <div className="border border-slate-700 rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-700 bg-slate-800/50">
                  <th className="text-left px-4 py-2.5 text-xs font-medium text-slate-400">도구</th>
                  <th className="text-right px-4 py-2.5 text-xs font-medium text-slate-400 w-20">호출 수</th>
                  <th className="text-right px-4 py-2.5 text-xs font-medium text-slate-400 w-24">성공률</th>
                  <th className="text-right px-4 py-2.5 text-xs font-medium text-slate-400 w-24">평균 응답</th>
                  <th className="text-left px-4 py-2.5 text-xs font-medium text-slate-400 w-36">마지막 호출</th>
                </tr>
              </thead>
              <tbody>
                {statsSlice.map(stat => {
                  const pct = stat.total_calls > 0 ? Math.round(stat.success_calls / stat.total_calls * 100) : 0;
                  return (
                    <tr key={stat.tool_id ?? stat.tool_name} onClick={() => openModal(stat)}
                      className="border-b border-slate-700/50 last:border-0 hover:bg-slate-700/30 cursor-pointer">
                      <td className="px-4 py-3 text-slate-200 font-medium">{stat.tool_name}</td>
                      <td className="px-4 py-3 text-right text-slate-300">{stat.total_calls}</td>
                      <td className="px-4 py-3 text-right">
                        <span className={`text-sm font-medium ${pct >= 95 ? 'text-emerald-400' : pct >= 80 ? 'text-amber-400' : 'text-rose-400'}`}>
                          {pct}%
                        </span>
                      </td>
                      <td className="px-4 py-3 text-right text-slate-400 font-mono text-xs">
                        {stat.avg_duration_ms != null ? `${stat.avg_duration_ms}ms` : '-'}
                      </td>
                      <td className="px-4 py-3 text-slate-500 text-xs">
                        {stat.last_called_at ? new Date(stat.last_called_at).toLocaleString('ko-KR') : '-'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}

      {/* 상세 모달 */}
      <Modal isOpen={!!modalTool} onClose={() => setModalTool(null)}
        title={modalTool ? `${modalTool.tool_name} — 호출 상세` : ''} maxWidth="max-w-4xl">
        {modalTool && (
          <div className="space-y-5">
            <DonutChart dist={modalTool.status_dist} total={modalTool.total_calls} />

            <div className="border border-slate-700 rounded-lg overflow-hidden">
              {loadingLogs ? (
                <div className="text-sm text-slate-500 animate-pulse py-6 text-center">로딩 중...</div>
              ) : logsError ? (
                <div className="text-sm text-rose-400 py-6 text-center px-4">{logsError}</div>
              ) : logResp.items.length === 0 ? (
                <div className="text-sm text-slate-500 py-6 text-center">로그가 없습니다.</div>
              ) : (
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-slate-700 bg-slate-800/50">
                      <th className="text-left px-4 py-2.5 text-xs font-medium text-slate-400">사용자</th>
                      <th className="text-left px-4 py-2.5 text-xs font-medium text-slate-400">호출 시각</th>
                      <th className="text-left px-4 py-2.5 text-xs font-medium text-slate-400 w-16">상태</th>
                      <th className="text-left px-4 py-2.5 text-xs font-medium text-slate-400 w-20">응답시간</th>
                      <th className="text-left px-4 py-2.5 text-xs font-medium text-slate-400 w-16">크기</th>
                    </tr>
                  </thead>
                  <tbody>
                    {logResp.items.flatMap(log => {
                      const isExpanded = expandedId === log.id;
                      const rows = [
                        <tr key={log.id} onClick={() => setExpandedId(isExpanded ? null : log.id)}
                          className={`border-b border-slate-700/50 hover:bg-slate-800/30 cursor-pointer ${isExpanded ? 'bg-slate-800/40' : ''}`}>
                          <td className="px-4 py-2.5 text-slate-300">{log.username ?? '-'}</td>
                          <td className="px-4 py-2.5 text-slate-400 text-xs">{new Date(log.called_at).toLocaleString('ko-KR')}</td>
                          <td className="px-4 py-2.5">
                            {log.response_status
                              ? <span className={`text-xs font-mono font-medium ${log.response_status < 300 ? 'text-emerald-400' : 'text-rose-400'}`}>{log.response_status}</span>
                              : <span className="text-xs text-rose-400">오류</span>}
                          </td>
                          <td className="px-4 py-2.5 text-slate-400 text-xs font-mono">{log.duration_ms != null ? `${log.duration_ms}ms` : '-'}</td>
                          <td className="px-4 py-2.5 text-slate-500 text-xs">{log.response_kb != null ? `${log.response_kb.toFixed(1)}KB` : '-'}</td>
                        </tr>,
                      ];
                      if (isExpanded) {
                        rows.push(
                          <tr key={`${log.id}-detail`} className="bg-slate-900/60 border-b border-slate-700/50">
                            <td colSpan={5} className="px-4 py-3 space-y-2.5">
                              {log.error && <p className="text-xs text-rose-400">{log.error}</p>}
                              {log.request_url && (
                                <div className="flex items-center gap-2">
                                  {log.http_method && (
                                    <span className="text-[10px] font-mono font-bold text-indigo-400 bg-indigo-500/10 px-1.5 py-0.5 rounded">
                                      {log.http_method}
                                    </span>
                                  )}
                                  <span className="text-xs text-slate-400 font-mono truncate">{log.request_url}</span>
                                </div>
                              )}
                              <div>
                                <p className="text-xs text-slate-500 mb-1">전송 파라미터</p>
                                <pre className="text-xs text-slate-300 bg-slate-800 rounded p-2 overflow-x-auto max-h-40 font-mono">
                                  {Object.keys(log.params).length === 0 ? '(없음)' : JSON.stringify(log.params, null, 2)}
                                </pre>
                              </div>
                            </td>
                          </tr>
                        );
                      }
                      return rows;
                    })}
                  </tbody>
                </table>
              )}
            </div>
            <div className="flex items-center justify-between">
              <p className="text-xs text-slate-500">전체 {logResp.total}건</p>
              <PaginationNav page={logPage} totalPages={logTotalPages} onPageChange={handleLogPage} />
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
