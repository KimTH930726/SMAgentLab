import { useState } from 'react';
import { Globe, Check, X, AlertCircle, ArrowRight, Edit3 } from 'lucide-react';
import type { SSEToolRequestEvent, HttpToolParam } from '../../types';

interface ToolRequestCardProps {
  event: SSEToolRequestEvent;
  onApprove: (toolId: number, params: Record<string, string>) => void;
  onReject: () => void;
  onFallback?: () => void;
}

export function ToolRequestCard({ event, onApprove, onReject, onFallback }: ToolRequestCardProps) {
  const [editParams, setEditParams] = useState<Record<string, string>>(event.params || {});
  const [missingFilled, setMissingFilled] = useState<Record<string, string>>({});

  // ── 활성 도구 없음 ──
  if (event.action === 'no_tools') {
    return (
      <div className="bg-slate-800 border border-slate-700 rounded-lg p-4 my-2 ml-9">
        <div className="flex items-center gap-2 text-amber-400 mb-2">
          <AlertCircle className="w-4 h-4 flex-shrink-0" />
          <span className="text-sm font-medium">{event.message}</span>
        </div>
        <p className="text-xs text-slate-500">관리자에게 HTTP 도구 등록을 요청하세요.</p>
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
                  onClick={() => onApprove(t.id, {})}
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

  // ── 파라미터 누락 / 실행 확인 ──
  const isMissing = event.action === 'missing_params';
  const missingParams = event.missing_params || [];

  const handleApprove = () => {
    if (!event.tool_id) return;
    const finalParams = { ...editParams, ...missingFilled };
    const stillMissing = missingParams.filter((p) => !finalParams[p]?.trim());
    if (stillMissing.length > 0) return;
    onApprove(event.tool_id, finalParams);
  };

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
          {isMissing ? '추가 정보를 입력해주세요' : '도구를 실행할까요?'}
        </span>
      </div>
      <p className="text-xs text-slate-400 mb-3 ml-6">
        {isMissing
          ? `${event.tool_name} 도구를 실행하려면 아래 항목을 입력해야 합니다.`
          : `${event.tool_name} 도구를 아래 파라미터로 실행합니다. 확인 후 승인해주세요.`}
      </p>

      {/* 선택된 도구 + 파라미터 */}
      <div className="bg-slate-900 rounded-lg p-3 mb-3 overflow-hidden">
        <div className="flex items-center gap-2 mb-1">
          <Globe className="w-3.5 h-3.5 text-emerald-400 flex-shrink-0" />
          <span className="text-sm font-medium text-emerald-400">{event.tool_name}</span>
        </div>
        <p className="text-[10px] text-slate-500 font-mono mb-3 break-all leading-relaxed">{event.tool_url}</p>

        {/* 파라미터 — 세로 레이아웃 (label 위, input 아래) */}
        <div className="space-y-2.5">
          {Object.entries(editParams).map(([key, value]) => {
            const isThisMissing = missingParams.includes(key);
            return (
              <div key={key}>
                <label className={`block text-xs font-mono mb-1 ${isThisMissing ? 'text-amber-400' : 'text-slate-400'}`}>
                  {key} {isThisMissing && <span className="text-amber-500 text-[10px]">(필수)</span>}
                </label>
                {isThisMissing ? (
                  <input
                    value={missingFilled[key] || ''}
                    onChange={(e) => setMissingFilled((f) => ({ ...f, [key]: e.target.value }))}
                    placeholder={_getParamHint(key, event.param_schema)}
                    className="w-full bg-slate-800 border border-amber-600/50 rounded px-2.5 py-1.5 text-sm text-white focus:border-emerald-500 focus:outline-none placeholder-slate-600"
                    autoFocus
                  />
                ) : (
                  <div className="flex items-center gap-1.5 px-2.5 py-1 bg-slate-800/50 rounded">
                    <span className="text-sm text-white font-mono break-all">{value}</span>
                    <span className="text-emerald-400 text-xs flex-shrink-0">&#10003;</span>
                  </div>
                )}
              </div>
            );
          })}

          {/* 누락 파라미터 중 editParams에 없는 것들 */}
          {missingParams.filter((p) => !(p in editParams)).map((key, idx) => (
            <div key={key}>
              <label className="block text-xs font-mono text-amber-400 mb-1">
                {key} <span className="text-amber-500 text-[10px]">(필수)</span>
              </label>
              <input
                value={missingFilled[key] || ''}
                onChange={(e) => setMissingFilled((f) => ({ ...f, [key]: e.target.value }))}
                placeholder={_getParamHint(key, event.param_schema)}
                className="w-full bg-slate-800 border border-amber-600/50 rounded px-2.5 py-1.5 text-sm text-white focus:border-emerald-500 focus:outline-none placeholder-slate-600"
                autoFocus={idx === 0}
              />
            </div>
          ))}
        </div>
      </div>

      {/* 전체 도구 목록 */}
      {event.tools && event.tools.length > 0 && (
        <div className="mb-3">
          <p className="text-[10px] text-slate-500 mb-1">전체 도구 목록:</p>
          <div className="flex flex-wrap gap-1">
            {event.tools.map((t) => (
              <span key={t.id} className={`text-[10px] px-1.5 py-0.5 rounded ${
                t.id === event.tool_id
                  ? 'bg-emerald-900/50 text-emerald-400 font-medium'
                  : 'bg-slate-700 text-slate-400'
              }`}>
                {t.id === event.tool_id ? '● ' : '○ '}{t.name}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* 버튼 */}
      <div className="flex gap-2">
        <button
          onClick={handleApprove}
          disabled={isMissing && missingParams.some((p) => {
            const val = missingFilled[p] || editParams[p];
            return !val?.trim();
          })}
          className="flex items-center gap-1.5 px-4 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-lg text-sm font-medium transition-colors"
        >
          <Check className="w-4 h-4" />
          {isMissing ? '입력 완료' : '승인'}
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

function _getParamHint(name: string, schema?: HttpToolParam[]): string {
  if (!schema) return `${name} 값을 입력하세요`;
  const param = schema.find((p) => p.name === name);
  if (!param) return `${name} 값을 입력하세요`;
  if (param.example) return `예: ${param.example}`;
  return param.description || `${name} 값을 입력하세요`;
}
