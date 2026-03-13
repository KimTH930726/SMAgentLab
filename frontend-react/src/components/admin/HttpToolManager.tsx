import { useState, useEffect, useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Globe, Plus, Trash2, Sparkles, Save, X, Search } from 'lucide-react';
import { useAppStore } from '../../store/useAppStore';
import { useAuthStore } from '../../store/useAuthStore';
import { getNamespaces, getNamespacesDetail } from '../../api/namespaces';
import { sortNamespacesByUserPart } from '../../utils/sortNamespaces';
import { listHttpTools, createHttpTool, updateHttpTool, deleteHttpTool, toggleHttpTool, autocompleteHttpTool, testHttpTool } from '../../api/httpTools';
import type { HttpToolTestResult } from '../../api/httpTools';
import { Modal } from '../ui/Modal';
import { Button } from '../ui/Button';
import { CodeBlock } from '../ui/CodeBlock';
import { Badge } from '../ui/Badge';
import type { HttpTool, HttpToolParam, HttpToolCreatePayload } from '../../types';

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
  url: string;
  headers: string;
  param_schema: HttpToolParam[];
  response_example: string;
  timeout_sec: number;
  max_response_kb: number;
}

const EMPTY_FORM: FormState = {
  name: '', description: '', method: 'GET', url: '',
  headers: '{}', param_schema: [], response_example: '',
  timeout_sec: 10, max_response_kb: 50,
};

const EMPTY_PARAM: HttpToolParam = { name: '', type: 'string', required: true, description: '', example: '' };

export function HttpToolManager() {
  const storeNamespace = useAppStore((s) => s.namespace);
  const user = useAuthStore((s) => s.user);
  const [namespace, setNamespace] = useState(storeNamespace || '');

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

  const [tools, setTools] = useState<HttpTool[]>([]);
  const [loading, setLoading] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [rawText, setRawText] = useState('');
  const [autoCompleting, setAutoCompleting] = useState(false);
  const [error, setError] = useState('');
  const [selectedTool, setSelectedTool] = useState<HttpTool | null>(null);
  const [testParams, setTestParams] = useState<Record<string, string>>({});
  const [testResult, setTestResult] = useState<HttpToolTestResult | null>(null);
  const [testLoading, setTestLoading] = useState(false);

  const fetchTools = useCallback(async () => {
    if (!namespace) return;
    setLoading(true);
    try {
      setTools(await listHttpTools(namespace));
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
      const result = await autocompleteHttpTool(namespace, rawText);
      if (result.status === 'ok' && result.tool) {
        const t = result.tool as Record<string, unknown>;
        setForm({
          name: (t.name as string) || '',
          description: (t.description as string) || '',
          method: (t.method as string) || 'GET',
          url: (t.url as string) || '',
          headers: JSON.stringify(t.headers || {}, null, 2),
          param_schema: (t.param_schema as HttpToolParam[]) || [],
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
        await updateHttpTool(editingId, {
          name: form.name, description: form.description, method: form.method,
          url: form.url, headers: parsedHeaders, param_schema: form.param_schema,
          response_example: parsedResponse ?? null, timeout_sec: form.timeout_sec,
          max_response_kb: form.max_response_kb,
        });
      } else {
        const payload: HttpToolCreatePayload = {
          namespace, name: form.name, description: form.description,
          method: form.method, url: form.url, headers: parsedHeaders,
          param_schema: form.param_schema, response_example: parsedResponse ?? null,
          timeout_sec: form.timeout_sec, max_response_kb: form.max_response_kb,
        };
        await createHttpTool(payload);
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

  const openEdit = (tool: HttpTool) => {
    setEditingId(tool.id);
    setForm({
      name: tool.name,
      description: tool.description,
      method: tool.method,
      url: tool.url,
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
    await deleteHttpTool(id);
    setSelectedTool(null);
    fetchTools();
  };

  const handleToggle = async (tool: HttpTool) => {
    await toggleHttpTool(tool.id, !tool.is_active);
    fetchTools();
  };

  const addParam = () => setForm((f) => ({ ...f, param_schema: [...f.param_schema, { ...EMPTY_PARAM }] }));
  const removeParam = (idx: number) => setForm((f) => ({
    ...f, param_schema: f.param_schema.filter((_, i) => i !== idx),
  }));
  const updateParam = (idx: number, key: keyof HttpToolParam, value: unknown) => {
    setForm((f) => ({
      ...f,
      param_schema: f.param_schema.map((p, i) => i === idx ? { ...p, [key]: value } : p),
    }));
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-200 flex items-center gap-2">
          <Globe className="w-5 h-5 text-emerald-400" />
          HTTP 도구 관리
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

      {!namespace && (
        <div className="text-center py-10 text-slate-500">파트를 선택하세요.</div>
      )}

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
            <label className="text-xs text-slate-400">URL</label>
            <input value={form.url} onChange={(e) => setForm((f) => ({ ...f, url: e.target.value }))}
              className="w-full bg-slate-900 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-200 focus:border-emerald-500 focus:outline-none" />
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
      {loading ? (
        <p className="text-slate-400 text-center py-8">불러오는 중...</p>
      ) : tools.length === 0 ? (
        <p className="text-slate-500 text-center py-8">등록된 HTTP 도구가 없습니다.</p>
      ) : (
        <div className="space-y-3">
          {tools.map((tool) => (
            <button
              key={tool.id}
              type="button"
              onClick={() => setSelectedTool(tool)}
              className="w-full text-left bg-slate-800 border border-slate-700 rounded-lg p-4 hover:bg-slate-700/50 transition-colors"
            >
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-3">
                  <Globe className="w-4 h-4 text-emerald-400" />
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
              <p className="text-xs text-slate-500 font-mono truncate">{tool.url}</p>
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
      )}

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
            const result = await testHttpTool(tool.id, params);
            setTestResult(result);
          } catch (e) {
            setTestResult({
              status: 'error',
              request: { method: tool.method, url: tool.url, headers: tool.headers, params: rawParams },
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
                <p className="text-sm text-slate-300 font-mono bg-slate-900/40 rounded-lg p-3 break-all">{tool.url}</p>
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
                    <Globe className="w-4 h-4" /> 테스트 호출
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
    </div>
  );
}
