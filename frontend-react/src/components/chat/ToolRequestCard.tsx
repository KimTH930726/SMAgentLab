import { useState } from 'react';
import { Globe, Check, X, AlertCircle, ArrowRight, Edit3 } from 'lucide-react';
import type { SSEToolRequestEvent, McpToolParam } from '../../types';

interface ToolRequestCardProps {
  event: SSEToolRequestEvent;
  onApprove: (toolId: number, params: Record<string, string>) => void;
  onSelectTool: (toolId: number) => void;
  onReject: () => void;
  onFallback?: () => void;
}

function _buildInitialValues(event: SSEToolRequestEvent): Record<string, string> {
  // LLM이 사용자 메시지에서 실제로 추출한 값만 미리 채운다 — param_schema의
  // example은 입력 힌트(placeholder)일 뿐, 사용자가 말하지 않은 값을 여기서
  // 자동으로 채우면 "필수 입력 완료" 체크를 무력화해 사용자가 승인한 적 없는
  // 값으로 도구가 실행된다.
  return { ...(event.params || {}) };
}

export function ToolRequestCard({ event, onApprove, onSelectTool, onReject, onFallback }: ToolRequestCardProps) {
  const [paramValues, setParamValues] = useState<Record<string, string>>(() => _buildInitialValues(event));

  // ── 활성 도구 없음 ──
  if (event.action === 'no_tools') {
    return (
      <div className="bg-slate-800 border border-slate-700 rounded-lg p-4 my-2 ml-9">
        <div className="flex items-center gap-2 text-amber-400 mb-2">
          <AlertCircle className="w-4 h-4 flex-shrink-0" />
          <span className="text-sm font-medium">{event.message}</span>
        </div>
        <p className="text-xs text-slate-500">관리자에게 MCP 도구 등록을 요청하세요.</p>
      </div>
    );
  }

  // ── LLM이 도구 불필요 판단 → 도구 목록 + 선택/폴백 ──
  if (event.action === 'no_tool_needed') {
    return (
      <div className="bg-slate-800 border border-slate-700 rounded-lg p-4 my-2 ml-9">
        <div className="flex items-center gap-2 text-slate-300 mb-3">
          <Globe className="w-4 h-4 text-amber-400 flex-shrink-0" />
          <span className="text-sm font-medium">AI 판단: 도구가 필요하지 않습니다</span>
        </div>
        <p className="text-xs text-slate-400 mb-3">{event.message}</p>

        {event.tools && event.tools.length > 0 && (
          <div className="mb-3">
            <p className="text-xs text-slate-500 mb-2">도구를 직접 선택하거나, 도구 없이 진행할 수 있습니다.</p>
            <div className="space-y-1">
              {event.tools.map((t) => (
                <button
                  key={t.id}
                  onClick={() => onSelectTool(t.id)}
                  className="w-full flex items-center gap-2 text-left bg-slate-900 hover:bg-slate-700/60 border border-slate-700 hover:border-emerald-700/50 rounded-lg px-3 py-2 transition-colors"
                >
                  <Globe className="w-3.5 h-3.5 text-emerald-400 flex-shrink-0" />
                  <div className="flex-1 min-w-0">
                    <span className="text-xs font-medium text-slate-200">{t.name}</span>
                    {t.description && (
                      <p className="text-[10px] text-slate-500 truncate">{t.description}</p>
                    )}
                  </div>
                  <ArrowRight className="w-3 h-3 text-emerald-400 flex-shrink-0" />
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="flex gap-2">
          <button
            onClick={onFallback ?? onReject}
            className="flex items-center gap-1.5 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-sm font-medium transition-colors"
          >
            <Check className="w-4 h-4" />
            도구 없이 진행
          </button>
          <button
            onClick={onReject}
            className="flex items-center gap-1.5 px-4 py-2 bg-slate-700 hover:bg-slate-600 text-white rounded-lg text-sm font-medium transition-colors"
          >
            <X className="w-4 h-4" />
            취소
          </button>
        </div>
      </div>
    );
  }

  // ── 파라미터 입력 / 실행 확인 ──
  const isMissing = event.action === 'missing_params';
  const missingParams = event.missing_params || [];
  const paramSchema: McpToolParam[] = event.param_schema || [];

  // param_schema가 있으면 전체 파라미터 목록 사용, 없으면 기존 params 키 사용
  const allParamKeys: string[] = paramSchema.length > 0
    ? paramSchema.map((p) => p.name)
    : [...new Set([...Object.keys(event.params || {}), ...missingParams])];

  const requiredParamNames = paramSchema.length > 0
    ? paramSchema.filter((p) => p.required).map((p) => p.name)
    : missingParams;

  const handleApprove = () => {
    if (!event.tool_id) return;
    const stillMissing = requiredParamNames.filter((p) => !paramValues[p]?.trim());
    if (stillMissing.length > 0) return;
    // 빈 값 제외하고 전송
    const finalParams = Object.fromEntries(
      Object.entries(paramValues).filter(([, v]) => v?.trim())
    );
    onApprove(event.tool_id, finalParams);
  };

  const isRequiredMissing = requiredParamNames.some((p) => !paramValues[p]?.trim());

  return (
    <div className={`bg-slate-800 border ${isMissing ? 'border-amber-700/50' : 'border-emerald-800/50'} rounded-lg p-4 my-2 ml-9`}>
      {/* 헤더 */}
      <div className="flex items-center gap-2 mb-1">
        {isMissing ? (
          <Edit3 className="w-4 h-4 text-amber-400 flex-shrink-0" />
        ) : (
          <Globe className="w-4 h-4 text-emerald-400 flex-shrink-0" />
        )}
        <span className="text-sm font-medium text-white">
          {isMissing ? '파라미터를 입력해주세요' : '도구를 실행할까요?'}
        </span>
      </div>
      <p className="text-xs text-slate-400 mb-3 ml-6">
        {isMissing
          ? `${event.tool_name} 도구 실행에 필요한 값을 입력하세요. 선택 항목은 비워둘 수 있습니다.`
          : `${event.tool_name} 도구를 아래 파라미터로 실행합니다. 확인 후 승인해주세요.`}
      </p>

      {/* 선택된 도구 + 파라미터 */}
      <div className="bg-slate-900 rounded-lg p-3 mb-3 overflow-hidden">
        <div className="flex items-center gap-2 mb-1">
          <Globe className="w-3.5 h-3.5 text-emerald-400 flex-shrink-0" />
          <span className="text-sm font-medium text-emerald-400">{event.tool_name}</span>
        </div>
        <p className="text-[10px] text-slate-500 font-mono mb-3 break-all leading-relaxed">{event.tool_url}</p>

        {/* 전체 파라미터 — required/optional 모두 표시 */}
        <div className="space-y-2.5">
          {allParamKeys.map((key, idx) => {
            const isRequired = missingParams.includes(key);
            const schemaParam = paramSchema.find((p) => p.name === key);
            const isOptional = schemaParam ? !schemaParam.required : !isRequired;
            return (
              <div key={key}>
                <label className={`block text-xs font-mono mb-1 ${isRequired ? 'text-amber-400' : 'text-slate-400'}`}>
                  {key}{' '}
                  {isRequired
                    ? <span className="text-amber-500 text-[10px]">(필수)</span>
                    : isOptional
                      ? <span className="text-slate-500 text-[10px]">(선택)</span>
                      : null}
                </label>
                <input
                  value={paramValues[key] || ''}
                  onChange={(e) => setParamValues((p) => ({ ...p, [key]: e.target.value }))}
                  placeholder={_getParamHint(key, paramSchema)}
                  className={`w-full bg-slate-800 rounded px-2.5 py-1.5 text-sm text-white focus:outline-none placeholder-slate-600 border ${
                    isRequired
                      ? 'border-amber-600/50 focus:border-emerald-500'
                      : 'border-slate-600 focus:border-slate-400'
                  }`}
                  autoFocus={idx === 0}
                />
              </div>
            );
          })}
        </div>
      </div>

      {/* 전체 도구 목록 — 다른 도구 선택 가능 */}
      {event.tools && event.tools.length > 1 && (
        <div className="mb-3">
          <p className="text-[10px] text-slate-500 mb-1.5">다른 도구로 변경:</p>
          <div className="flex flex-wrap gap-1">
            {event.tools.filter((t) => t.id !== event.tool_id).map((t) => (
              <button
                key={t.id}
                onClick={() => onSelectTool(t.id)}
                title={t.description}
                className="text-[10px] px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600 text-slate-300 hover:text-white transition-colors"
              >
                {t.name}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* 버튼 */}
      <div className="flex gap-2">
        <button
          onClick={handleApprove}
          disabled={isRequiredMissing}
          className="flex items-center gap-1.5 px-4 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-lg text-sm font-medium transition-colors"
        >
          <Check className="w-4 h-4" />
          {isMissing ? '입력 완료' : '승인'}
        </button>
        <button
          onClick={onFallback ?? onReject}
          className="flex items-center gap-1.5 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-sm font-medium transition-colors"
        >
          <Check className="w-4 h-4" />
          도구없이 진행
        </button>
        <button
          onClick={onReject}
          className="flex items-center gap-1.5 px-4 py-2 bg-slate-700 hover:bg-slate-600 text-white rounded-lg text-sm font-medium transition-colors"
        >
          <X className="w-4 h-4" />
          취소
        </button>
      </div>
    </div>
  );
}

function _getParamHint(name: string, schema: McpToolParam[]): string {
  const param = schema.find((p) => p.name === name);
  if (!param) return `${name} 값을 입력하세요`;
  if (param.example) return `예: ${param.example}`;
  return param.description || `${name} 값을 입력하세요`;
}
