import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { ArrowRightCircle, Database, Check, X } from 'lucide-react';
import {
  promoteUnresolvedSegment, promoteUnresolvedSegmentToParam, suggestParamFields, type UnresolvedSegment,
} from '../../api/policy';

/**
 * 정책 항목 하나의 미분류 조각 처리(2026-10-02) — AI가 서술/파라미터 어디에도 넣지 못한 원문 조각을 그 자리에서 편입.
 * 원래 "미분류" 탭에만 있던 액션을 검토 큐(항목 브라우저)의 위험 높음 항목 안으로 옮겼다 — 같은 항목을 검토대기에서 보고
 * 고치려면 다른 탭에서 다시 찾아야 했던 중복을 없앰. 편입하면 parse_status가 바뀌어 위험도가 다시 계산된다.
 *
 * - 서술로 편입: 원문을 그대로 벡터 검색용 서술로(원클릭)
 * - 파라미터로 편입: 항목명/조건/값/단위 — 폼을 열면 AI 1차 추측으로 미리 채움(실패하면 빈 폼)
 * segment_index는 서버 배열 순서에 의존하므로 편입할 때마다 onChanged로 목록을 다시 받는다(로컬에서 자르지 않음).
 */
const EMPTY = { name: '', condition: '', value: '', unit: '' };

const INPUT = 'w-full bg-white dark:bg-slate-900 border border-slate-300 dark:border-slate-600 rounded-lg px-2.5 py-1.5 text-xs text-slate-800 dark:text-slate-200 focus:outline-none focus:border-cyan-500 disabled:opacity-60';
const LABEL = 'block text-[10px] font-medium text-cyan-700/80 dark:text-cyan-400/80 mb-0.5';

export function UnresolvedSegmentActions({ namespace, itemId, segments, canModify, onChanged }: {
  namespace: string;
  itemId: number;
  segments: UnresolvedSegment[];
  canModify: boolean;
  /** 목록 새로고침 — Promise를 돌려줘야 그동안 버튼이 잠긴다(옛 순번으로 엉뚱한 조각을 편입하지 않게) */
  onChanged: () => Promise<unknown>;
}) {
  const [formIdx, setFormIdx] = useState<number | null>(null);
  const [form, setForm] = useState(EMPTY);
  // 어떤 조각의 AI 프리필이 로딩 중인지 — 단순 boolean이면 A 요청 중에 B로 폼을 옮겼을 때 A의 완료가 B 로딩 표시를 끔
  const [suggesting, setSuggesting] = useState<number | null>(null);

  const openForm = async (idx: number) => {
    setFormIdx(idx);
    setForm(EMPTY);
    setSuggesting(idx);
    try {
      const s = await suggestParamFields(itemId, idx, namespace);
      setFormIdx((cur) => {
        if (cur === idx) setForm({ name: s.name ?? '', condition: s.condition ?? '', value: s.value ?? '', unit: s.unit ?? '' });
        return cur;
      });
    } catch {
      // 제안은 best-effort — 실패하면 빈 폼 그대로
    } finally {
      setSuggesting((cur) => (cur === idx ? null : cur));
    }
  };

  const toNarrative = useMutation({
    mutationFn: (idx: number) => promoteUnresolvedSegment(itemId, idx, namespace),
    // 조각이 하나 빠지면 뒤 순번이 당겨진다 — 열린 파라미터 폼은 다른 조각 아래로 밀려가므로 닫고, 새 목록을 받을 때까지 잠근다
    onSuccess: () => { setFormIdx(null); setForm(EMPTY); setSuggesting(null); return onChanged(); },
    onError: (e: Error) => alert(e.message),
  });
  const toParam = useMutation({
    mutationFn: (idx: number) => promoteUnresolvedSegmentToParam(itemId, idx, namespace, {
      name: form.name.trim(), condition: form.condition.trim() || null,
      value: form.value.trim() || null, unit: form.unit.trim() || null,
    }),
    onSuccess: () => { setFormIdx(null); setForm(EMPTY); return onChanged(); },
    onError: (e: Error) => alert(e.message),
  });
  const busy = toNarrative.isPending || toParam.isPending;

  return (
    <div className="space-y-2">
      {segments.map((seg, idx) => (
        <div key={idx} className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 space-y-1.5">
          <div>
            <p className="text-[10px] font-medium uppercase tracking-wide text-slate-500">원문</p>
            <p className="text-sm text-slate-300 leading-relaxed">{seg.text}</p>
          </div>
          {seg.reason && (
            <div>
              <p className="text-[10px] font-medium uppercase tracking-wide text-slate-500">왜 자동 분류가 안 됐나요</p>
              <p className="text-xs text-amber-700 dark:text-amber-400/90">{seg.reason}</p>
            </div>
          )}
          {canModify && formIdx === idx && (
            <div className="border border-cyan-200 dark:border-cyan-700/40 bg-cyan-50/60 dark:bg-cyan-950/20 rounded-lg px-3 py-2.5 space-y-2">
              {suggesting === idx && <p className="text-[11px] text-cyan-700 dark:text-cyan-400">AI 제안 확인 중...</p>}
              <div className="grid grid-cols-2 gap-2">
                {([['name', '항목명 *', 'col-span-2'], ['condition', '조건(선택)', ''], ['value', '값(선택)', ''], ['unit', '단위(선택)', 'col-span-2']] as const).map(([k, label, span]) => (
                  <div key={k} className={span}>
                    <label className={LABEL}>{label}</label>
                    <input type="text" value={form[k]} disabled={suggesting === idx}
                      onChange={(e) => setForm((f) => ({ ...f, [k]: e.target.value }))} className={INPUT} />
                  </div>
                ))}
              </div>
              <div className="flex justify-end gap-2">
                <button type="button" onClick={() => { setFormIdx(null); setForm(EMPTY); }}
                  className="flex items-center gap-1 px-2.5 py-1 rounded-full text-[11px] font-medium text-slate-500 hover:text-slate-700 dark:hover:text-slate-300">
                  <X className="w-3.5 h-3.5" />취소
                </button>
                <button type="button" disabled={!form.name.trim() || busy || suggesting === idx} onClick={() => toParam.mutate(idx)}
                  className="flex items-center gap-1 px-2.5 py-1 rounded-full text-[11px] font-medium border border-cyan-300 text-cyan-700 bg-cyan-50 hover:bg-cyan-100 dark:border-cyan-600/40 dark:text-cyan-300 dark:bg-cyan-500/10 dark:hover:bg-cyan-500/20 disabled:opacity-50 disabled:cursor-not-allowed transition-colors">
                  <Check className="w-3.5 h-3.5" />{toParam.isPending && toParam.variables === idx ? '저장 중...' : '저장'}
                </button>
              </div>
            </div>
          )}
          {canModify && (
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" disabled={busy} onClick={() => toNarrative.mutate(idx)}
                title="원문을 그대로 검색 가능한 서술로 등록합니다(값·조건의 정밀 구조화는 아님)"
                className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-medium border border-indigo-300 text-indigo-700 bg-indigo-50 hover:bg-indigo-100 dark:border-indigo-600/40 dark:text-indigo-300 dark:bg-indigo-500/10 dark:hover:bg-indigo-500/20 disabled:opacity-50 disabled:cursor-not-allowed transition-colors">
                {toNarrative.isPending && toNarrative.variables === idx ? <>편입 중...</> : <><ArrowRightCircle className="w-3.5 h-3.5" /> 서술로 편입</>}
              </button>
              {formIdx !== idx && (
                <button type="button" disabled={busy} onClick={() => openForm(idx)}
                  title="항목명/조건/값/단위로 파라미터(정확 조회)로 등록합니다 — AI가 값을 미리 채웁니다"
                  className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-medium border border-cyan-300 text-cyan-700 bg-cyan-50 hover:bg-cyan-100 dark:border-cyan-600/40 dark:text-cyan-300 dark:bg-cyan-500/10 dark:hover:bg-cyan-500/20 disabled:opacity-50 transition-colors">
                  <Database className="w-3.5 h-3.5" /> 파라미터로 편입
                </button>
              )}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
