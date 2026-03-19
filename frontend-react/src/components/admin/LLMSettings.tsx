import React, { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Cpu, CheckCircle2, XCircle, AlertTriangle, RefreshCw, Save, FlaskConical, SlidersHorizontal, KeyRound, ChevronDown, MessageSquareText } from 'lucide-react';
import { getLLMConfig, updateLLMConfig, testLLMConnection, getSearchThresholds, updateSearchThresholds, getSearchDefaults, updateSearchDefaults } from '../../api/llm';
import { getCacheConfig, setCacheConfig } from '../../api/cache';
import type { CacheConfig } from '../../api/cache';
import type { LLMConfig, LLMConfigUpdate, SearchThresholds, SearchDefaults } from '../../api/llm';
import { Button } from '../ui/Button';
import { PromptManager } from './PromptManager';
import { useAuthStore } from '../../store/useAuthStore';
import { useAppStore } from '../../store/useAppStore';

type Provider = 'ollama' | 'inhouse';

type ResponseMode = 'streaming' | 'blocking';

type InhouseModel = 'gpt-5.2' | 'claude-sonnet-4.5' | 'gemini-3.0-pro' | '';

interface InhouseModelOption {
  id: InhouseModel;
  label: string;
  desc: string;
  icon: React.ReactNode;
  color: string;
}

/* ── 각 LLM 프로바이더 로고 SVG ── */
const OpenAILogo = ({ className }: { className?: string }) => (
  <svg viewBox="0 0 24 24" fill="currentColor" className={className}>
    <path d="M22.282 9.821a5.985 5.985 0 0 0-.516-4.91 6.046 6.046 0 0 0-6.51-2.9A6.065 6.065 0 0 0 4.981 4.18a5.985 5.985 0 0 0-3.998 2.9 6.046 6.046 0 0 0 .743 7.097 5.98 5.98 0 0 0 .51 4.911 6.051 6.051 0 0 0 6.515 2.9A5.985 5.985 0 0 0 13.26 24a6.056 6.056 0 0 0 5.772-4.206 5.99 5.99 0 0 0 3.997-2.9 6.056 6.056 0 0 0-.747-7.073zM13.26 22.43a4.476 4.476 0 0 1-2.876-1.04l.141-.081 4.779-2.758a.795.795 0 0 0 .392-.681v-6.737l2.02 1.168a.071.071 0 0 1 .038.052v5.583a4.504 4.504 0 0 1-4.494 4.494zM3.6 18.304a4.47 4.47 0 0 1-.535-3.014l.142.085 4.783 2.759a.771.771 0 0 0 .78 0l5.843-3.369v2.332a.08.08 0 0 1-.033.062L9.74 19.95a4.5 4.5 0 0 1-6.14-1.646zM2.34 7.896a4.485 4.485 0 0 1 2.366-1.973V11.6a.766.766 0 0 0 .388.676l5.815 3.355-2.02 1.168a.076.076 0 0 1-.071 0l-4.83-2.786A4.504 4.504 0 0 1 2.34 7.872zm16.597 3.855l-5.833-3.387L15.119 7.2a.076.076 0 0 1 .071 0l4.83 2.791a4.494 4.494 0 0 1-.676 8.105v-5.678a.79.79 0 0 0-.407-.667zm2.01-3.023l-.141-.085-4.774-2.782a.776.776 0 0 0-.785 0L9.409 9.23V6.897a.066.066 0 0 1 .028-.061l4.83-2.787a4.5 4.5 0 0 1 6.68 4.66zm-12.64 4.135l-2.02-1.164a.08.08 0 0 1-.038-.057V6.075a4.5 4.5 0 0 1 7.375-3.453l-.142.08L8.704 5.46a.795.795 0 0 0-.393.681zm1.097-2.365l2.602-1.5 2.607 1.5v2.999l-2.597 1.5-2.607-1.5z"/>
  </svg>
);

const AnthropicLogo = ({ className }: { className?: string }) => (
  <svg viewBox="0 0 100 100" className={className}>
    {/* Claude "sunburst" / starburst logo */}
    {[0, 45, 90, 135, 180, 225, 270, 315].map((angle) => (
      <ellipse
        key={angle}
        cx="50" cy="50" rx="6" ry="30"
        fill="#E07A5F"
        transform={`rotate(${angle} 50 50)`}
      />
    ))}
  </svg>
);

const GeminiLogo = ({ className }: { className?: string }) => (
  <svg viewBox="0 0 100 100" className={className}>
    {/* Google Gemini 4-color diamond star */}
    <polygon points="50,2 62,38 50,50" fill="#4285F4" />
    <polygon points="50,2 38,38 50,50" fill="#EA4335" />
    <polygon points="50,98 62,62 50,50" fill="#34A853" />
    <polygon points="50,98 38,62 50,50" fill="#FBBC05" />
    <polygon points="2,50 38,38 50,50" fill="#EA4335" />
    <polygon points="2,50 38,62 50,50" fill="#FBBC05" />
    <polygon points="98,50 62,38 50,50" fill="#4285F4" />
    <polygon points="98,50 62,62 50,50" fill="#34A853" />
  </svg>
);

const INHOUSE_MODELS: InhouseModelOption[] = [
  { id: 'gpt-5.2', label: 'GPT 5.2', desc: 'OpenAI', icon: <OpenAILogo className="w-6 h-6" />, color: 'border-emerald-500 bg-emerald-500/10 text-emerald-300' },
  { id: 'claude-sonnet-4.5', label: 'Claude Sonnet 4.5', desc: 'Anthropic', icon: <AnthropicLogo className="w-6 h-6" />, color: 'border-orange-500 bg-orange-500/10 text-orange-300' },
  { id: 'gemini-3.0-pro', label: 'Gemini 3.0 Pro', desc: 'Google', icon: <GeminiLogo className="w-6 h-6" />, color: 'border-blue-500 bg-blue-500/10 text-blue-300' },
];

interface FormState {
  provider: Provider;
  ollama_base_url: string;
  ollama_model: string;
  ollama_timeout: number;
  inhouse_llm_url: string;
  inhouse_llm_agent_code: string;
  inhouse_llm_model: InhouseModel;
  inhouse_llm_response_mode: ResponseMode;
  inhouse_llm_timeout: number;
}

function configToForm(cfg: LLMConfig): FormState {
  return {
    provider: cfg.provider,
    ollama_base_url: cfg.ollama.base_url,
    ollama_model: cfg.ollama.model,
    ollama_timeout: cfg.ollama.timeout,
    inhouse_llm_url: cfg.inhouse.url,
    inhouse_llm_agent_code: cfg.inhouse.agent_code,
    inhouse_llm_model: (cfg.inhouse.model as InhouseModel) || '',
    inhouse_llm_response_mode: (cfg.inhouse.response_mode as ResponseMode) || 'streaming',
    inhouse_llm_timeout: cfg.inhouse.timeout,
  };
}

function ConnectionBadge({ ok, checking }: { ok: boolean | null; checking?: boolean }) {
  if (checking) return (
    <span className="flex items-center gap-1.5 text-xs text-slate-400">
      <RefreshCw className="w-3.5 h-3.5 animate-spin" /> 연결 확인 중...
    </span>
  );
  if (ok === null) return null;
  if (ok) return (
    <span className="flex items-center gap-1.5 text-xs text-emerald-400">
      <CheckCircle2 className="w-3.5 h-3.5" /> 연결됨
    </span>
  );
  return (
    <span className="flex items-center gap-1.5 text-xs text-rose-400">
      <XCircle className="w-3.5 h-3.5" /> 연결 실패
    </span>
  );
}

function CollapsibleSection({ title, defaultOpen = true, children }: { title: string; defaultOpen?: boolean; children: React.ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border border-slate-700 rounded-xl overflow-hidden">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-5 py-3 bg-slate-800/60 hover:bg-slate-800 transition-colors"
      >
        <span className="flex items-center gap-2 text-sm font-semibold text-slate-200">
          <SlidersHorizontal className="w-4 h-4 text-indigo-400" />
          {title}
        </span>
        <ChevronDown className={`w-4 h-4 text-slate-400 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && <div className="px-5 py-4">{children}</div>}
    </div>
  );
}

type SubTab = 'provider' | 'thresholds' | 'prompts';

export function LLMSettings() {
  const [subTab, setSubTab] = useState<SubTab>('thresholds');
  const selectedAgent = useAppStore((s) => s.selectedAgent);

  return (
    <div className={subTab === 'prompts' ? 'max-w-5xl' : 'max-w-2xl'}>
      {/* Sub-tab bar */}
      <div className="flex gap-1 mb-6 border-b border-slate-700">
        <button
          onClick={() => setSubTab('thresholds')}
          className={`flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
            subTab === 'thresholds'
              ? 'text-indigo-400 border-indigo-500'
              : 'text-slate-400 border-transparent hover:text-slate-200 hover:border-slate-600'
          }`}
        >
          <SlidersHorizontal className="w-3.5 h-3.5" />
          검색 설정
        </button>
        <button
          onClick={() => setSubTab('prompts')}
          className={`flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
            subTab === 'prompts'
              ? 'text-indigo-400 border-indigo-500'
              : 'text-slate-400 border-transparent hover:text-slate-200 hover:border-slate-600'
          }`}
        >
          <MessageSquareText className="w-3.5 h-3.5" />
          프롬프트 관리
        </button>
        <button
          onClick={() => setSubTab('provider')}
          className={`flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
            subTab === 'provider'
              ? 'text-indigo-400 border-indigo-500'
              : 'text-slate-400 border-transparent hover:text-slate-200 hover:border-slate-600'
          }`}
        >
          <Cpu className="w-3.5 h-3.5" />
          LLM 프로바이더
        </button>
      </div>

      {subTab === 'provider' && <ProviderSettings />}
      {subTab === 'prompts' && <PromptManager agentType={selectedAgent} />}
      {subTab === 'thresholds' && (
        <div className="space-y-4">
          <CollapsibleSection title="검색 기본값 설정" defaultOpen={false}>
            <SearchDefaultsSettings />
          </CollapsibleSection>
          <CollapsibleSection title="검색 임계값 설정" defaultOpen={false}>
            <ThresholdSettings />
          </CollapsibleSection>
          <CollapsibleSection title="Semantic Cache 설정" defaultOpen={false}>
            <CacheSettings />
          </CollapsibleSection>
        </div>
      )}
    </div>
  );
}


// ── LLM 프로바이더 설정 컴포넌트 ──

function ProviderSettings() {
  const qc = useQueryClient();
  const user = useAuthStore((s) => s.user);
  const [form, setForm] = useState<FormState | null>(null);
  const [testResult, setTestResult] = useState<{ ok: boolean; error?: string } | null>(null);
  const [testing, setTesting] = useState(false);
  const [dirty, setDirty] = useState(false);

  const { data: config, isLoading } = useQuery({
    queryKey: ['llm-config'],
    queryFn: getLLMConfig,
    staleTime: 10_000,
  });

  // config 로드 시 폼 초기화 (한 번만)
  useEffect(() => {
    if (config && !form) {
      setForm(configToForm(config));
    }
  }, [config, form]);

  const saveMutation = useMutation({
    mutationFn: (payload: LLMConfigUpdate) => updateLLMConfig(payload),
    onSuccess: (data: LLMConfig) => {
      qc.setQueryData(['llm-config'], data);
      setForm(configToForm(data));
      setTestResult({ ok: data.is_connected });
      setDirty(false);
    },
  });

  const handleChange = <K extends keyof FormState>(key: K, value: FormState[K]) => {
    setForm((f) => f ? { ...f, [key]: value } : f);
    setDirty(true);
    setTestResult(null);
  };

  const buildPayload = (): LLMConfigUpdate => {
    if (!form) throw new Error('no form');
    const payload: LLMConfigUpdate = { provider: form.provider };
    if (form.provider === 'ollama') {
      payload.ollama_base_url = form.ollama_base_url;
      payload.ollama_model = form.ollama_model;
      payload.ollama_timeout = form.ollama_timeout;
    } else {
      payload.inhouse_llm_url = form.inhouse_llm_url;
      payload.inhouse_llm_agent_code = form.inhouse_llm_agent_code;
      payload.inhouse_llm_model = form.inhouse_llm_model || undefined;
      payload.inhouse_llm_response_mode = form.inhouse_llm_response_mode;
      payload.inhouse_llm_timeout = form.inhouse_llm_timeout;
    }
    return payload;
  };

  const handleTest = async () => {
    if (!form) return;
    setTesting(true);
    setTestResult(null);
    try {
      const payload = buildPayload();
      const result = await testLLMConnection(payload);
      setTestResult({ ok: result.is_connected, error: result.error });
    } catch {
      setTestResult({ ok: false, error: '요청 실패' });
    } finally {
      setTesting(false);
    }
  };

  const handleSave = () => {
    if (!form) return;
    saveMutation.mutate(buildPayload());
  };

  if (isLoading || !form) {
    return <div className="text-center py-10 text-slate-500 animate-pulse">설정 로딩 중...</div>;
  }

  const currentProvider = config?.provider;
  const isConnected = config?.is_connected;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-slate-200">
            LLM 프로바이더 설정
          </h2>
          <p className="text-xs text-slate-500 mt-0.5">
            런타임 전환 — 컨테이너 재시작 시 .env 설정으로 복귀됩니다
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <ConnectionBadge ok={isConnected ?? null} />
          {config?.is_runtime_override && (
            <span className="text-xs text-amber-400 flex items-center gap-1">
              <AlertTriangle className="w-3 h-3" /> 런타임 오버라이드 중
            </span>
          )}
        </div>
      </div>

      {/* Provider selector */}
      <div className="bg-slate-800 border border-slate-700 rounded-xl p-5 space-y-4">
        <div>
          <label className="block text-xs font-medium text-slate-400 mb-2">프로바이더 선택</label>
          <div className="flex gap-3">
            {(['inhouse', 'ollama'] as Provider[]).map((p) => (
              <button
                key={p}
                onClick={() => handleChange('provider', p)}
                className={`flex-1 py-3 px-4 rounded-lg border text-sm font-medium transition-all ${
                  form.provider === p
                    ? 'border-indigo-500 bg-indigo-500/10 text-indigo-300'
                    : 'border-slate-600 bg-slate-900 text-slate-400 hover:border-slate-500'
                }`}
              >
                <div className="text-base mb-0.5">{p === 'ollama' ? '🦙' : '🏢'}</div>
                <div>{p === 'ollama' ? 'Ollama' : '사내 LLM'}</div>
                <div className="text-xs opacity-70 mt-0.5">
                  {p === 'ollama' ? '로컬 / 내부망' : 'DevX MCP API'}
                </div>
                {currentProvider === p && (
                  <div className="text-xs text-emerald-400 mt-1">현재 사용 중</div>
                )}
              </button>
            ))}
          </div>
        </div>

        {/* InHouse LLM model selector */}
        {form.provider === 'inhouse' && (
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-2">모델 선택</label>
            <div className="grid grid-cols-3 gap-2">
              {INHOUSE_MODELS.map((m) => (
                <button
                  key={m.id}
                  onClick={() => handleChange('inhouse_llm_model', form.inhouse_llm_model === m.id ? '' as InhouseModel : m.id)}
                  className={`py-2.5 px-3 rounded-lg border text-sm font-medium transition-all text-center ${
                    form.inhouse_llm_model === m.id
                      ? m.color
                      : 'border-slate-600 bg-slate-900 text-slate-500 hover:border-slate-400 hover:text-slate-300'
                  }`}
                >
                  <div className={`text-lg mb-0.5 transition-all ${form.inhouse_llm_model === m.id ? '' : 'grayscale opacity-40'}`}>{m.icon}</div>
                  <div className="text-xs font-semibold">{m.label}</div>
                  <div className="text-[10px] opacity-60 mt-0.5">{m.desc}</div>
                </button>
              ))}
            </div>
            <p className="text-xs text-slate-500 mt-1.5">inputs.model 파라미터로 전달됩니다. 미선택 시 Agent 기본 모델 사용.</p>
          </div>
        )}

        {/* InHouse LLM settings */}
        {form.provider === 'inhouse' && (
          <div className="space-y-3 pt-1">
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">
                API 엔드포인트 URL <span className="text-rose-400">*</span>
              </label>
              <input
                type="text"
                value={form.inhouse_llm_url}
                onChange={(e) => handleChange('inhouse_llm_url', e.target.value)}
                placeholder="https://devx-mcp-api.shinsegae-inc.com/api/v1/mcp-command/chat"
                className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500 font-mono"
              />
              <p className="text-xs text-slate-500 mt-0.5">DevX MCP API 엔드포인트 (전체 URL)</p>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">Agent Code <span className="text-rose-400">*</span></label>
              <input
                type="text"
                value={form.inhouse_llm_agent_code}
                onChange={(e) => handleChange('inhouse_llm_agent_code', e.target.value)}
                placeholder="playground"
                className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500 font-mono"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">응답 방식</label>
              <div className="flex gap-2">
                {(['streaming', 'blocking'] as ResponseMode[]).map((mode) => (
                  <button
                    key={mode}
                    onClick={() => handleChange('inhouse_llm_response_mode', mode)}
                    className={`flex-1 py-2 px-3 rounded-lg border text-xs font-medium transition-all ${
                      form.inhouse_llm_response_mode === mode
                        ? 'border-indigo-500 bg-indigo-500/10 text-indigo-300'
                        : 'border-slate-600 bg-slate-900 text-slate-400 hover:border-slate-500'
                    }`}
                  >
                    {mode === 'streaming' ? 'Streaming (실시간)' : 'Blocking (일괄)'}
                  </button>
                ))}
              </div>
              <p className="text-xs text-slate-500 mt-0.5">
                {form.inhouse_llm_response_mode === 'streaming'
                  ? 'SSE 스트리밍 — 토큰 단위로 실시간 표시'
                  : 'Blocking — 전체 응답을 한번에 수신'}
              </p>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">
                내 API Key
              </label>
              <div className={`w-full bg-slate-900/50 border rounded-lg px-3 py-2 text-sm font-mono flex items-center gap-2 ${
                user?.has_api_key ? 'border-emerald-500/30 text-emerald-400' : 'border-slate-600 text-slate-500'
              }`}>
                <KeyRound className="w-3.5 h-3.5 flex-shrink-0" />
                {user?.has_api_key ? '••••••••••••••••  (등록됨)' : '미등록 — 프로필에서 API Key를 등록해주세요'}
              </div>
              <p className="text-xs text-slate-500 mt-0.5">
                API Key는 프로필 설정에서 등록/변경할 수 있습니다. 각 사용자별로 개별 관리됩니다.
              </p>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">
                타임아웃 (초): <span className="text-indigo-400">{form.inhouse_llm_timeout}s</span>
              </label>
              <input
                type="range" min={10} max={600} step={10}
                value={form.inhouse_llm_timeout}
                onChange={(e) => handleChange('inhouse_llm_timeout', parseInt(e.target.value))}
                className="w-full accent-indigo-500"
              />
              <div className="flex justify-between text-xs text-slate-500 mt-0.5">
                <span>10s</span><span>10분</span>
              </div>
            </div>
          </div>
        )}

        {/* Ollama settings */}
        {form.provider === 'ollama' && (
          <div className="space-y-3 pt-1">
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">Ollama URL</label>
              <input
                type="text"
                value={form.ollama_base_url}
                onChange={(e) => handleChange('ollama_base_url', e.target.value)}
                placeholder="http://host.docker.internal:11434"
                className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500 font-mono"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">모델명</label>
              <input
                type="text"
                value={form.ollama_model}
                onChange={(e) => handleChange('ollama_model', e.target.value)}
                placeholder="exaone3.5:7.8b"
                className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500 font-mono"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">
                타임아웃 (초): <span className="text-indigo-400">{form.ollama_timeout}s</span>
              </label>
              <input
                type="range" min={30} max={1800} step={30}
                value={form.ollama_timeout}
                onChange={(e) => handleChange('ollama_timeout', parseInt(e.target.value))}
                className="w-full accent-indigo-500"
              />
              <div className="flex justify-between text-xs text-slate-500 mt-0.5">
                <span>30s</span><span>30분</span>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Test result */}
      {testResult && (
        <div className={`flex items-center gap-2 px-4 py-3 rounded-lg text-sm border ${
          testResult.ok
            ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300'
            : 'bg-rose-500/10 border-rose-500/30 text-rose-300'
        }`}>
          {testResult.ok
            ? <><CheckCircle2 className="w-4 h-4" /> 연결 성공 — LLM 서버가 응답합니다</>
            : <><XCircle className="w-4 h-4" /> 연결 실패{testResult.error ? ` — ${testResult.error}` : ''}</>
          }
        </div>
      )}

      {saveMutation.isError && (
        <div className="flex items-center gap-2 px-4 py-3 rounded-lg text-sm border bg-rose-500/10 border-rose-500/30 text-rose-300">
          <XCircle className="w-4 h-4" /> 저장 실패: {String(saveMutation.error)}
        </div>
      )}

      {/* Action buttons */}
      <div className="flex gap-2">
        <Button variant="secondary" size="sm" onClick={handleTest} loading={testing} disabled={!dirty && testResult !== null}>
          <FlaskConical className="w-4 h-4" /> 연결 테스트
        </Button>
        <Button
          variant="primary" size="sm"
          onClick={handleSave}
          loading={saveMutation.isPending}
          disabled={!dirty}
        >
          <Save className="w-4 h-4" /> 저장 및 적용
        </Button>
      </div>

      {/* Info box */}
      <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4 text-xs text-slate-500 space-y-1">
        <p className="font-medium text-slate-400">참고사항</p>
        <p>• <strong className="text-slate-300">저장 및 적용</strong>은 즉시 반영되며 컨테이너 재시작 전까지 유지됩니다.</p>
        <p>• 영구 적용하려면 <code className="bg-slate-900 px-1 rounded">.env</code> 파일의 <code className="bg-slate-900 px-1 rounded">LLM_PROVIDER</code> 등을 직접 수정하세요.</p>
        <p>• API Key는 각 사용자가 프로필 설정에서 개별 등록합니다. 이 화면에서는 변경할 수 없습니다.</p>
      </div>

    </div>
  );
}


// ── 검색 임계값 설정 컴포넌트 ──

const THRESHOLD_FIELDS: { key: keyof SearchThresholds; label: string; desc: string; min: number; max: number; step: number; color: string }[] = [
  { key: 'glossary_min_similarity', label: '용어 매핑 최소 유사도', desc: '이 값 이상이어야 용어 매핑이 활성화됩니다', min: 0, max: 1, step: 0.05, color: 'text-indigo-400' },
  { key: 'fewshot_min_similarity', label: 'Few-shot 최소 유사도', desc: '이 값 이상이어야 Few-shot 예시에 포함됩니다', min: 0, max: 1, step: 0.05, color: 'text-amber-400' },
  { key: 'knowledge_min_score', label: '검색결과 최소 점수', desc: '이 점수 미만의 결과는 LLM 컨텍스트에서 제외됩니다', min: 0, max: 1, step: 0.05, color: 'text-rose-400' },
  { key: 'knowledge_high_score', label: '검색결과 높은 신뢰 기준', desc: '이 점수 이상이면 "높음" 신뢰도로 분류됩니다', min: 0, max: 1, step: 0.05, color: 'text-emerald-400' },
  { key: 'knowledge_mid_score', label: '검색결과 보통 신뢰 기준', desc: '이 점수 이상이면 "보통", 미만이면 "낮음" 신뢰도', min: 0, max: 1, step: 0.05, color: 'text-sky-400' },
];

function ThresholdSettings() {
  const qc = useQueryClient();
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin') ?? false;
  const [values, setValues] = useState<SearchThresholds | null>(null);
  const [dirty, setDirty] = useState(false);

  const { data: thresholds, isLoading } = useQuery({
    queryKey: ['search-thresholds'],
    queryFn: getSearchThresholds,
    staleTime: 10_000,
  });

  useEffect(() => {
    if (thresholds && !values) {
      setValues(thresholds);
    }
  }, [thresholds, values]);

  const saveMutation = useMutation({
    mutationFn: (payload: Partial<SearchThresholds>) => updateSearchThresholds(payload),
    onSuccess: (data: SearchThresholds) => {
      qc.setQueryData(['search-thresholds'], data);
      setValues(data);
      setDirty(false);
    },
  });

  const handleChange = (key: keyof SearchThresholds, val: number) => {
    setValues((v) => v ? { ...v, [key]: val } : v);
    setDirty(true);
  };

  const handleSave = () => {
    if (!values) return;
    saveMutation.mutate(values);
  };

  const handleReset = () => {
    if (thresholds) {
      setValues(thresholds);
      setDirty(false);
    }
  };

  if (isLoading || !values) {
    return <div className="text-center py-6 text-slate-500 animate-pulse text-sm">임계값 로딩 중...</div>;
  }

  return (
    <div className="space-y-4">
      <p className="text-xs text-slate-500">
        유사도 및 점수 임계값을 조정하여 검색 민감도를 제어합니다. 낮추면 더 많은 결과, 높이면 더 정확한 결과.
      </p>

      <div className="bg-slate-800 border border-slate-700 rounded-xl p-5 space-y-5">
        {THRESHOLD_FIELDS.map(({ key, label, desc, min, max, step, color }) => (
          <div key={key}>
            <div className="flex justify-between text-xs mb-1">
              <span className="font-medium text-slate-400">{label}</span>
              <span className={`font-mono ${color}`}>{values[key].toFixed(2)}</span>
            </div>
            <input
              type="range"
              min={min} max={max} step={step}
              value={values[key]}
              onChange={(e) => handleChange(key, parseFloat(e.target.value))}
              className="w-full accent-indigo-500"
            />
            <p className="text-[10px] text-slate-500 mt-0.5">{desc}</p>
          </div>
        ))}
      </div>

      {saveMutation.isError && (
        <div className="flex items-center gap-2 px-4 py-3 rounded-lg text-sm border bg-rose-500/10 border-rose-500/30 text-rose-300">
          <XCircle className="w-4 h-4" /> 저장 실패: {String(saveMutation.error)}
        </div>
      )}

      {saveMutation.isSuccess && !dirty && (
        <div className="flex items-center gap-2 px-4 py-3 rounded-lg text-sm border bg-emerald-500/10 border-emerald-500/30 text-emerald-300">
          <CheckCircle2 className="w-4 h-4" /> 임계값이 적용되었습니다
        </div>
      )}

      {isAdmin ? (
        <div className="flex gap-2">
          <Button variant="secondary" size="sm" onClick={handleReset} disabled={!dirty}>
            초기화
          </Button>
          <Button
            variant="primary" size="sm"
            onClick={handleSave}
            loading={saveMutation.isPending}
            disabled={!dirty}
          >
            <Save className="w-4 h-4" /> 저장 및 적용
          </Button>
        </div>
      ) : (
        <p className="text-xs text-slate-500">* 이 설정은 관리자만 변경할 수 있습니다.</p>
      )}
    </div>
  );
}


// ── 검색 기본값 설정 컴포넌트 ──

const SEARCH_DEFAULT_FIELDS: { key: keyof SearchDefaults; label: string; desc: string; min: number; max: number; step: number; color: string; format: (v: number) => string }[] = [
  { key: 'default_w_vector', label: '의미 중심 (문맥 유사도) 가중치', desc: '벡터 검색의 가중치입니다. 키워드 가중치와 자동으로 합이 1이 됩니다.', min: 0, max: 1, step: 0.05, color: 'text-indigo-400', format: (v) => v.toFixed(2) },
  { key: 'default_w_keyword', label: '키워드 중심 (단어 일치) 가중치', desc: 'BM25 키워드 검색의 가중치입니다. 의미 가중치와 자동으로 합이 1이 됩니다.', min: 0, max: 1, step: 0.05, color: 'text-amber-400', format: (v) => v.toFixed(2) },
  { key: 'default_top_k', label: '검색 결과 수 (Top-K)', desc: 'LLM에 전달할 최대 검색 결과 수입니다. (1~20)', min: 1, max: 20, step: 1, color: 'text-emerald-400', format: (v) => String(v) },
];

function SearchDefaultsSettings() {
  const qc = useQueryClient();
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin') ?? false;
  const [values, setValues] = useState<SearchDefaults | null>(null);
  const [dirty, setDirty] = useState(false);

  const { data: defaults, isLoading } = useQuery({
    queryKey: ['search-defaults'],
    queryFn: getSearchDefaults,
    staleTime: 10_000,
  });

  useEffect(() => {
    if (defaults && !values) {
      setValues(defaults);
    }
  }, [defaults, values]);

  const saveMutation = useMutation({
    mutationFn: (payload: Partial<SearchDefaults>) => updateSearchDefaults(payload),
    onSuccess: (data: SearchDefaults) => {
      qc.setQueryData(['search-defaults'], data);
      setValues(data);
      setDirty(false);
      // 채팅 패널의 검색 설정도 즉시 반영
      useAppStore.getState().initSearchConfig({
        wVector: data.default_w_vector,
        wKeyword: data.default_w_keyword,
        topK: data.default_top_k,
      });
    },
  });

  const handleChange = (key: keyof SearchDefaults, val: number) => {
    setValues((v) => {
      if (!v) return v;
      if (key === 'default_w_vector') {
        return { ...v, default_w_vector: val, default_w_keyword: Math.round((1 - val) * 100) / 100 };
      }
      if (key === 'default_w_keyword') {
        return { ...v, default_w_keyword: val, default_w_vector: Math.round((1 - val) * 100) / 100 };
      }
      return { ...v, [key]: val };
    });
    setDirty(true);
  };

  const handleSave = () => {
    if (!values) return;
    saveMutation.mutate(values);
  };

  const handleReset = () => {
    if (defaults) {
      setValues(defaults);
      setDirty(false);
    }
  };

  if (isLoading || !values) {
    return <div className="text-center py-6 text-slate-500 animate-pulse text-sm">검색 기본값 로딩 중...</div>;
  }

  return (
    <div className="space-y-4">
      <p className="text-xs text-slate-500">
        채팅 검색 시 사용되는 기본 가중치와 결과 수를 설정합니다. 사용자가 개별 조정하지 않으면 이 값이 적용됩니다.
      </p>

      <div className="bg-slate-800 border border-slate-700 rounded-xl p-5 space-y-5">
        {SEARCH_DEFAULT_FIELDS.map(({ key, label, desc, min, max, step, color, format }) => (
          <div key={key}>
            <div className="flex justify-between text-xs mb-1">
              <span className="font-medium text-slate-400">{label}</span>
              <span className={`font-mono ${color}`}>{format(values[key])}</span>
            </div>
            <input
              type="range"
              min={min} max={max} step={step}
              value={values[key]}
              onChange={(e) => handleChange(key, key === 'default_top_k' ? parseInt(e.target.value) : parseFloat(e.target.value))}
              className="w-full accent-indigo-500"
            />
            <p className="text-[10px] text-slate-500 mt-0.5">{desc}</p>
          </div>
        ))}
      </div>

      {saveMutation.isError && (
        <div className="flex items-center gap-2 px-4 py-3 rounded-lg text-sm border bg-rose-500/10 border-rose-500/30 text-rose-300">
          <XCircle className="w-4 h-4" /> 저장 실패: {String(saveMutation.error)}
        </div>
      )}

      {saveMutation.isSuccess && !dirty && (
        <div className="flex items-center gap-2 px-4 py-3 rounded-lg text-sm border bg-emerald-500/10 border-emerald-500/30 text-emerald-300">
          <CheckCircle2 className="w-4 h-4" /> 검색 기본값이 적용되었습니다
        </div>
      )}

      {isAdmin ? (
        <div className="flex gap-2">
          <Button variant="secondary" size="sm" onClick={handleReset} disabled={!dirty}>
            초기화
          </Button>
          <Button
            variant="primary" size="sm"
            onClick={handleSave}
            loading={saveMutation.isPending}
            disabled={!dirty}
          >
            <Save className="w-4 h-4" /> 저장 및 적용 (전체)
          </Button>
        </div>
      ) : (
        <div className="flex items-center gap-3">
          <Button
            variant="primary" size="sm"
            onClick={() => {
              if (!values) return;
              useAppStore.getState().setSearchConfig({
                wVector: values.default_w_vector,
                wKeyword: values.default_w_keyword,
                topK: values.default_top_k,
              });
              setDirty(false);
            }}
            disabled={!dirty}
          >
            <Save className="w-4 h-4" /> 내 설정으로 저장
          </Button>
          <span className="text-xs text-slate-500">내 브라우저에만 저장됩니다.</span>
        </div>
      )}
    </div>
  );
}


// ── Semantic Cache 설정 컴포넌트 ──

function CacheSettings() {
  const qc = useQueryClient();
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin') ?? false;
  const [values, setValues] = useState<CacheConfig | null>(null);
  const [dirty, setDirty] = useState(false);

  const { data: config, isLoading } = useQuery({
    queryKey: ['cache-config'],
    queryFn: getCacheConfig,
    staleTime: 10_000,
  });

  useEffect(() => {
    if (config && !values) setValues(config);
  }, [config, values]);

  const saveMutation = useMutation({
    mutationFn: (patch: Partial<CacheConfig>) => setCacheConfig(patch),
    onSuccess: (data) => {
      qc.setQueryData(['cache-config'], data);
      setValues(data);
      setDirty(false);
    },
  });

  const handleChange = (key: keyof CacheConfig, val: number | boolean) => {
    setValues((v) => v ? { ...v, [key]: val } : v);
    setDirty(true);
  };

  if (isLoading || !values) {
    return <div className="text-center py-6 text-slate-500 animate-pulse text-sm">캐시 설정 로딩 중...</div>;
  }

  const ttlMinutes = Math.round(values.cache_ttl / 60);

  return (
    <div className="space-y-4">
      <p className="text-xs text-slate-500">
        Semantic Cache의 유사도 임계값과 TTL을 조정합니다. 임계값을 낮추면 더 많은 질문이 캐시 히트됩니다.
      </p>

      <div className="bg-slate-800 border border-slate-700 rounded-xl p-5 space-y-5">
        {/* 유사도 임계값 */}
        <div>
          <div className="flex justify-between text-xs mb-1">
            <span className="font-medium text-slate-400">캐시 히트 유사도 임계값</span>
            <span className="font-mono text-indigo-400">{values.similarity_threshold.toFixed(2)}</span>
          </div>
          <input
            type="range" min={0.5} max={0.99} step={0.01}
            value={values.similarity_threshold}
            onChange={(e) => handleChange('similarity_threshold', parseFloat(e.target.value))}
            className="w-full accent-indigo-500"
          />
          <div className="flex justify-between text-[10px] text-slate-500 mt-0.5">
            <span>0.50 (느슨)</span>
            <span>현재: {values.similarity_threshold.toFixed(2)}</span>
            <span>0.99 (엄격)</span>
          </div>
          <p className="text-[10px] text-slate-500 mt-1">
            이 유사도 이상이면 캐시 히트. 한국어 단문 권장값: 0.90~0.93. 기본값: 0.92
          </p>
        </div>

        {/* TTL */}
        <div>
          <div className="flex justify-between text-xs mb-1">
            <span className="font-medium text-slate-400">캐시 유효 시간 (TTL)</span>
            <span className="font-mono text-emerald-400">
              {ttlMinutes >= 60 ? `${Math.floor(ttlMinutes / 60)}시간 ${ttlMinutes % 60}분` : `${ttlMinutes}분`}
            </span>
          </div>
          <input
            type="range" min={5} max={1440} step={5}
            value={ttlMinutes}
            onChange={(e) => handleChange('cache_ttl', parseInt(e.target.value) * 60)}
            className="w-full accent-indigo-500"
          />
          <div className="flex justify-between text-[10px] text-slate-500 mt-0.5">
            <span>5분</span>
            <span>24시간</span>
          </div>
          <p className="text-[10px] text-slate-500 mt-1">
            캐시 항목이 자동 만료되는 시간. 새로 저장되는 항목에만 적용됩니다. 기본값: 30분
          </p>
        </div>
      </div>

      {saveMutation.isError && (
        <div className="flex items-center gap-2 px-4 py-3 rounded-lg text-sm border bg-rose-500/10 border-rose-500/30 text-rose-300">
          <XCircle className="w-4 h-4" /> 저장 실패: {String(saveMutation.error)}
        </div>
      )}

      {saveMutation.isSuccess && !dirty && (
        <div className="flex items-center gap-2 px-4 py-3 rounded-lg text-sm border bg-emerald-500/10 border-emerald-500/30 text-emerald-300">
          <CheckCircle2 className="w-4 h-4" /> 캐시 설정이 적용되었습니다
        </div>
      )}

      {isAdmin ? (
        <div className="flex gap-2">
          <Button variant="secondary" size="sm" onClick={() => { if (config) { setValues(config); setDirty(false); } }} disabled={!dirty}>
            초기화
          </Button>
          <Button variant="primary" size="sm" onClick={() => values && saveMutation.mutate(values)} loading={saveMutation.isPending} disabled={!dirty}>
            <Save className="w-4 h-4" /> 저장 및 적용
          </Button>
        </div>
      ) : (
        <p className="text-xs text-slate-500">* 이 설정은 관리자만 변경할 수 있습니다.</p>
      )}
    </div>
  );
}
