import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Upload, AlertTriangle, CheckCircle, ClipboardPaste, Plus, X } from 'lucide-react';
import {
  importPolicyFile, importPolicyPaste, fetchPolicySources, type PolicyImportProblem, type PolicyImportResult,
} from '../../api/policy';
import { ApiError } from '../../api/client';
import { useNamespaceAccess } from '../../utils/useNamespaceAccess';
import { useAuthStore } from '../../store/useAuthStore';
import { Button } from '../ui/Button';

/**
 * 정책서 올리기(2026-10-06, v2.124 → v2.127 붙여넣기).
 * 사내 문서 보안(DRM) 엑셀은 서버도 브라우저도 못 연다 — 예전 안내(담당자 PC에서 파이썬 변환기 실행)는 파이썬을
 * 제공하지 않아 담당자가 쓸 수 없었다(사용자 지적). 엑셀에서 복사는 되므로 바뀐 시트만 붙여넣는 게 기본 경로.
 * 처음 20여 개 시트 일괄 적재는 개발 쪽이 변환기로 하고, 평소 수정은 시트 1~2개라 붙여넣기로 충분하다.
 * 보안 없는 엑셀(·변환기 JSON)은 파일로 그대로 올린다. 같은 정책은 식별키로 찾아 바뀐 것만 새 버전(v2.123).
 */
const COLS: { key: 'created_items' | 'new_versions' | 'unchanged_skipped' | 'moved' | 'matched_by_body' | 'duplicate_keys'; label: string; tip: string }[] = [
  { key: 'created_items', label: '신규', tip: '처음 들어온 정책 — AI가 분해해 검토 대기로' },
  { key: 'new_versions', label: '새 버전', tip: '내용이 바뀐 정책 — AI가 다시 분해해 검토 대기로(이전 버전은 보관)' },
  { key: 'unchanged_skipped', label: '변경 없음', tip: '내용이 같아 건너뜀 — 승인 상태 그대로' },
  { key: 'moved', label: '위치만 갱신', tip: '내용은 같고 행·파일 위치만 바뀜 — 승인 상태 그대로' },
  { key: 'matched_by_body', label: '이름·분류 변경', tip: '분류 경로나 정책명이 바뀌었지만 본문이 같아 같은 정책으로 이음' },
  { key: 'duplicate_keys', label: '중복 키', tip: '파일 안에 같은 분류 경로+정책명이 또 나온 행 — 원본에서 중복인지 확인하세요' },
];

const NEW_SOURCE = '__new__';

function problemsOf(err: unknown): PolicyImportProblem[] {
  const d = err instanceof ApiError ? err.detail : undefined;
  return d && typeof d === 'object' && Array.isArray((d as { problems?: unknown }).problems)
    ? (d as { problems: PolicyImportProblem[] }).problems : [];
}

/** 붙여넣은 표의 행 수(내용 있는 행만) — 붙여넣어졌는지 확인용. 엑셀은 칸 안 줄바꿈이 있으면 그 칸을 큰따옴표로
 *  감싸 내보내서("" = 따옴표 문자) 줄 수로 세면 틀린다 — 칸 시작의 따옴표 안에서는 줄바꿈을 행 경계로 보지 않는다. */
function pastedRows(text: string): number {
  let rows = 0, inQuote = false, atCellStart = true, rowHasText = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQuote) {
      if (c === '"' && text[i + 1] === '"') i++;
      else if (c === '"') inQuote = false;
      continue;
    }
    if (c === '"' && atCellStart) { inQuote = true; rowHasText = true; atCellStart = false; continue; }
    if (c === '\t') { atCellStart = true; continue; }
    if (c === '\n' || c === '\r') {
      if (c === '\r' && text[i + 1] === '\n') i++;
      if (rowHasText) rows++;
      rowHasText = false; atCellStart = true;
      continue;
    }
    if (c.trim()) rowHasText = true;
    atCellStart = false;
  }
  return rows + (rowHasText ? 1 : 0);
}

type PastedSheet = { name: string; text: string };

export function PolicyImportPanel({ onGoReview }: { onGoReview?: () => void } = {}) {
  const qc = useQueryClient();
  const { selectedNs, setSelectedNs, sortedNamespaces, canModifyNs } = useNamespaceAccess();
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin');
  const [mode, setMode] = useState<'paste' | 'file'>('paste');
  const [file, setFile] = useState<File | null>(null);
  const [source, setSource] = useState('');
  const [newSource, setNewSource] = useState('');
  const [sheets, setSheets] = useState<PastedSheet[]>([{ name: '', text: '' }]);
  const [reprocessAll, setReprocessAll] = useState(false);
  const [error, setError] = useState<{ message: string; problems: PolicyImportProblem[] } | null>(null);

  const sourcesQuery = useQuery({
    queryKey: ['policy-sources', selectedNs],
    queryFn: () => fetchPolicySources(selectedNs),
    enabled: !!selectedNs,
  });
  const sources = sourcesQuery.data ?? [];
  const sourceFile = source === NEW_SOURCE ? newSource.trim() : source;
  const knownSheets = sources.find((s) => s.source_file === source)?.sheets ?? [];
  const filledSheets = sheets.filter((s) => s.name.trim() || s.text.trim());
  const pasteReady = !!sourceFile && filledSheets.length > 0 && filledSheets.every((s) => s.name.trim() && s.text.trim());
  // 반영 버튼이 왜 안 눌리는지 바로 알게(사용자 관점 — 막힌 이유와 할 일)
  const pasteBlocker = !selectedNs ? '' : !sourceFile ? '정책서를 고르세요.'
    : filledSheets.length === 0 ? '시트 이름을 넣고 엑셀에서 복사한 내용을 붙여넣으세요.'
      : filledSheets.some((s) => !s.name.trim()) ? '시트 이름이 빈 칸이 있어요 — 엑셀 아래쪽 탭 이름을 넣으세요.'
        : filledSheets.some((s) => !s.text.trim()) ? '내용이 빈 시트가 있어요 — 붙여넣거나 x로 빼세요.' : '';

  const clearResult = () => { setError(null); mutation.reset(); };
  const updateSheet = (i: number, patch: Partial<PastedSheet>) => {
    setSheets((prev) => prev.map((s, j) => (j === i ? { ...s, ...patch } : s)));
    clearResult();
  };

  const mutation = useMutation({
    mutationFn: () => (mode === 'paste'
      ? importPolicyPaste(selectedNs, sourceFile,
        filledSheets.map((s) => ({ sheet_name: s.name.trim(), text: s.text })), isAdmin && reprocessAll)
      : importPolicyFile(file!, selectedNs, isAdmin && reprocessAll)),
    onSuccess: () => {
      setError(null);
      qc.invalidateQueries({ queryKey: ['policy-items'] });
      qc.invalidateQueries({ queryKey: ['policy-items-all'] });
      qc.invalidateQueries({ queryKey: ['policy-review-summary'] });
      qc.invalidateQueries({ queryKey: ['policy-sources'] });
    },
    onError: (err: Error) => setError({ message: err.message, problems: problemsOf(err) }),
  });
  const result: PolicyImportResult | undefined = mutation.data;
  const toReview = result ? result.sheets.reduce((n, s) => n + s.created_items + s.new_versions, 0) : 0;
  const warnings = result ? [...result.warnings, ...result.sheets.flatMap((s) => s.warnings.map((w) => `'${s.sheet_name}' 시트: ${w}`))] : [];
  const ready = mode === 'paste' ? pasteReady : !!file;

  return (
    <div className="space-y-4 max-w-4xl">
      <div className="rounded-xl border border-slate-700 bg-slate-800 px-4 py-3 text-xs text-slate-300 space-y-1.5">
        <p className="text-sm font-medium text-slate-200">정책서를 반영하는 순서</p>
        <p>
          <span className="font-medium text-slate-200">① 엑셀에서</span> — 바뀐 시트를 열고 <kbd className="px-1 rounded bg-slate-900">Ctrl+A</kbd>{' '}
          → <kbd className="px-1 rounded bg-slate-900">Ctrl+C</kbd>로 시트 전체를 복사합니다. 바뀐 시트만 올리면 되고, 안 올린 시트는 그대로 둡니다.
        </p>
        <p>
          <span className="font-medium text-slate-200">② 이 화면</span> — 파트·정책서·시트 이름을 고르고 붙여넣은 뒤 반영합니다. 바뀐 정책만 AI가
          분해하고, 내용이 같은 정책은 그대로 둡니다.
        </p>
        <p>
          <span className="font-medium text-slate-200">③ 올린 뒤</span> — 위험 낮음은 자동 통과되고, 나머지는{' '}
          {onGoReview ? (
            <button type="button" onClick={onGoReview} className="underline text-indigo-600 dark:text-indigo-400">항목 브라우저 › 검토대기</button>
          ) : '항목 브라우저 › 검토대기'}
          에서 위험 높음·중간부터 승인/반려합니다.
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <div>
          <label className="block text-xs font-medium text-slate-400 mb-1.5">파트</label>
          <select value={selectedNs} onChange={(e) => { setSelectedNs(e.target.value); setSource(''); clearResult(); }}
            className="w-56 bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500">
            <option value="">선택...</option>
            {sortedNamespaces.map((ns) => <option key={ns} value={ns}>{ns}</option>)}
          </select>
        </div>
        <div className="flex rounded-lg border border-slate-600 overflow-hidden text-xs">
          {([['paste', '시트 붙여넣기', '보안(DRM) 엑셀도 됩니다 — 엑셀에서 복사해 붙여넣기'],
            ['file', '파일 올리기', '보안이 걸리지 않은 엑셀(.xlsx) 또는 변환기로 만든 JSON']] as const).map(([m, label, tip]) => (
            <button key={m} type="button" title={tip} onClick={() => { setMode(m); clearResult(); }}
              className={`px-3 py-2 ${mode === m ? 'bg-slate-700 text-slate-100' : 'text-slate-400 hover:text-slate-200'}`}>
              {label}
            </button>
          ))}
        </div>
      </div>

      {mode === 'paste' ? (
        <div className="space-y-3">
          <div className="flex flex-wrap items-end gap-3">
            <div>
              <label htmlFor="policy-paste-source" className="block text-xs font-medium text-slate-400 mb-1.5"
                title="같은 정책서의 정책을 찾는 기준입니다 — 이미 올린 정책서면 목록에서 고르세요">정책서</label>
              <select id="policy-paste-source" value={source} onChange={(e) => { setSource(e.target.value); clearResult(); }} disabled={!selectedNs}
                className="w-full max-w-md bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 disabled:opacity-50">
                <option value="">선택...</option>
                {sources.map((s) => (
                  <option key={s.source_file} value={s.source_file}>{s.source_file} (시트 {s.sheets.length}개)</option>
                ))}
                <option value={NEW_SOURCE}>+ 새 정책서</option>
              </select>
            </div>
            {source === NEW_SOURCE && (
              <div>
                <label htmlFor="policy-paste-new-source" className="block text-xs font-medium text-slate-400 mb-1.5">새 정책서 이름</label>
                <input id="policy-paste-new-source" value={newSource} onChange={(e) => { setNewSource(e.target.value); clearResult(); }}
                  placeholder="예) 온라인스토어_정책서.xlsx"
                  className="w-72 bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500" />
              </div>
            )}
          </div>

          <datalist id="policy-paste-sheets">
            {knownSheets.map((s) => <option key={s.name} value={s.name}>{`정책 ${s.items}개`}</option>)}
          </datalist>
          {sheets.map((s, i) => {
            const rows = pastedRows(s.text);
            const isNewSheet = !!s.name.trim() && source !== NEW_SOURCE && !!source
              && !knownSheets.some((k) => k.name === s.name.trim());
            return (
              <div key={i} className="rounded-xl border border-slate-700 bg-slate-800 p-3 space-y-2">
                <div className="flex items-center gap-2">
                  <input value={s.name} onChange={(e) => updateSheet(i, { name: e.target.value })} list="policy-paste-sheets"
                    placeholder="시트 이름 (엑셀 아래쪽 탭 이름)" aria-label={`시트 ${i + 1} 이름`}
                    className="w-64 bg-slate-900 border border-slate-600 rounded-lg px-3 py-1.5 text-sm text-slate-200 focus:outline-none focus:border-indigo-500" />
                  {isNewSheet && (
                    <span className="text-[11px] text-amber-600 dark:text-amber-400"
                      title="이 정책서에 없던 시트 이름이라 정책이 전부 신규로 들어갑니다. 기존 시트면 목록에서 고르세요">새 시트로 들어갑니다</span>
                  )}
                  {rows > 0 && <span className="text-[11px] text-slate-500">{rows}행 붙여넣음</span>}
                  {sheets.length > 1 && (
                    <button type="button" onClick={() => { setSheets((prev) => prev.filter((_, j) => j !== i)); clearResult(); }}
                      className="ml-auto text-slate-500 hover:text-slate-300" title="이 시트 빼기">
                      <X className="w-4 h-4" />
                    </button>
                  )}
                </div>
                <textarea value={s.text} onChange={(e) => updateSheet(i, { text: e.target.value })} rows={4} spellCheck={false}
                  aria-label={`시트 ${i + 1} 내용`}
                  placeholder="엑셀에서 시트 전체를 복사(Ctrl+A → Ctrl+C)해 여기에 붙여넣기(Ctrl+V)"
                  className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-xs font-mono text-slate-300 focus:outline-none focus:border-indigo-500 whitespace-pre overflow-x-auto" />
              </div>
            );
          })}
          <button type="button" onClick={() => setSheets((prev) => [...prev, { name: '', text: '' }])}
            className="flex items-center gap-1 text-xs text-slate-400 hover:text-slate-200">
            <Plus className="w-3.5 h-3.5" />시트 추가
          </button>
        </div>
      ) : (
        <div>
          <label className="block text-xs font-medium text-slate-400 mb-1.5"
            title="보안이 걸리지 않은 엑셀(.xlsx) 또는 변환기로 만든 JSON. 보안 엑셀은 '시트 붙여넣기'로">파일</label>
          <input type="file" accept=".xlsx,.json"
            onChange={(e) => { setFile(e.target.files?.[0] ?? null); clearResult(); }}
            className="text-sm text-slate-300 file:mr-3 file:rounded-lg file:border-0 file:bg-slate-700 file:px-3 file:py-2 file:text-sm file:text-slate-200" />
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <Button variant="primary" size="sm" loading={mutation.isPending}
          disabled={!ready || !selectedNs || !canModifyNs || mutation.isPending}
          onClick={() => mutation.mutate()}>
          {mode === 'paste' ? <ClipboardPaste className="w-3.5 h-3.5" /> : <Upload className="w-3.5 h-3.5" />}
          {mode === 'paste' ? '반영' : '올리기'}
        </Button>
        {mode === 'paste' && pasteBlocker && <span className="text-xs text-slate-500">{pasteBlocker}</span>}
        {isAdmin && (
          <label className="flex items-center gap-2 text-xs text-slate-400"
            title="내용이 같은 정책도 전부 AI로 다시 분해해 새 버전(검토 대기)으로 넣습니다. 분해 방식이 바뀌면 시스템이 알아서 다시 분해하므로, 그 감지에 안 걸리는 경우(예: 사내 LLM 모델 교체)에만 씁니다">
            <input type="checkbox" checked={reprocessAll} onChange={(e) => setReprocessAll(e.target.checked)} />
            전체 다시 분해(관리자)
          </label>
        )}
      </div>
      {isAdmin && (
        <p className={`text-[11px] ${reprocessAll ? 'text-amber-600 dark:text-amber-400' : 'text-slate-500'}`}>
          {reprocessAll
            ? '전체 다시 분해 켜짐 — 바뀌지 않은 정책도 전부 AI로 다시 분해해 새 버전으로 넣습니다. 수십 분 걸리고 검토 큐가 다시 차요. 평소엔 끄세요.'
            : '전체 다시 분해: 평소엔 끄세요. 바뀐 정책만 처리하는 게 기본이고, 분해 방식이 바뀌면 시스템이 알아서 다시 분해합니다(사내 LLM 모델만 바꿨을 때 등 예외용).'}
        </p>
      )}
      {!selectedNs && <p className="text-xs text-slate-500">파트를 먼저 고르세요.</p>}
      {selectedNs && !canModifyNs && <p className="text-xs text-slate-500">이 파트에 올릴 권한이 없습니다 — 파트 담당자나 관리자만 올릴 수 있어요.</p>}
      {mutation.isPending && <p className="text-xs text-slate-500">바뀐 정책을 AI가 분해하는 중입니다 — 수십 건이면 몇 분 걸릴 수 있어요. 창을 닫지 마세요.</p>}

      {error && (
        <div className="rounded-xl border border-rose-200 bg-rose-50 dark:border-rose-700/50 dark:bg-rose-900/20 p-4 space-y-2">
          <p className="flex items-center gap-1.5 text-sm font-medium text-rose-700 dark:text-rose-300">
            <AlertTriangle className="w-4 h-4" />{error.message}
          </p>
          {error.problems.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-left text-slate-500">
                    <th className="py-1 pr-3 font-medium">어디</th>
                    <th className="py-1 pr-3 font-medium">문제</th>
                    <th className="py-1 font-medium">이렇게 고치세요</th>
                  </tr>
                </thead>
                <tbody>
                  {error.problems.map((p, i) => (
                    <tr key={i} className="border-t border-rose-200 dark:border-rose-800/40 align-top text-slate-300">
                      <td className="py-1.5 pr-3 whitespace-nowrap">{p.where}</td>
                      <td className="py-1.5 pr-3">{p.problem}</td>
                      <td className="py-1.5">{p.fix}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {result && (
        <div className="bg-slate-800 border border-slate-700 rounded-xl p-4 space-y-3">
          <p className="flex items-center gap-1.5 text-sm text-slate-200">
            <CheckCircle className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
            <span className="font-medium">{result.source_file}</span> 반영 완료 — 채팅 답변에 바로 쓰입니다
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-xs tabular-nums">
              <thead>
                <tr className="text-slate-500 text-left">
                  <th className="py-1 pr-3 font-medium">시트</th>
                  {COLS.map((c) => <th key={c.key} className="py-1 px-2 font-medium text-right" title={c.tip}>{c.label}</th>)}
                </tr>
              </thead>
              <tbody>
                {result.sheets.map((s) => (
                  <tr key={s.sheet_name} className="border-t border-slate-700 text-slate-300 align-top">
                    <td className="py-1.5 pr-3">
                      {s.sheet_name}
                      <span className="ml-1 text-slate-500">{s.kind === 'glossary' ? '(용어집)' : s.kind === 'unknown' ? '(건너뜀)' : ''}</span>
                      {s.skip_reason && <span className="block text-[11px] text-slate-500">{s.skip_reason}</span>}
                      {s.columns && (
                        <span className="block text-[11px] text-slate-500"
                          title="엑셀의 어느 칸을 어디로 읽었는지 — 틀렸으면 엑셀 헤더 이름(정책명·조건/상세 등)을 확인해 주세요">
                          읽은 칸: 정책명 ← {s.columns.정책명} · 본문 ← {s.columns.본문 ?? '(못 찾음)'} · 분류 ← {s.columns.분류.join(' > ') || '(없음)'}
                        </span>
                      )}
                    </td>
                    {s.kind === 'glossary' ? (
                      <td colSpan={COLS.length} className="py-1.5 px-2 text-right text-slate-400">
                        용어 추가 {s.glossary_added} · 이미 있음 {s.glossary_duplicate_skipped}
                      </td>
                    ) : s.kind === 'unknown' ? (
                      <td colSpan={COLS.length} />
                    ) : COLS.map((c) => <td key={c.key} className="py-1.5 px-2 text-right">{s[c.key]}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {warnings.length > 0 && (
            <div className="rounded-lg border border-amber-200 bg-amber-50 dark:border-amber-700/50 dark:bg-amber-900/20 px-3 py-2">
              <p className="text-xs font-medium text-amber-700 dark:text-amber-300">반영은 됐지만 확인할 것</p>
              <ul className="list-disc pl-4 text-xs text-slate-300 space-y-0.5 mt-1">
                {warnings.map((w, i) => <li key={i}>{w}</li>)}
              </ul>
            </div>
          )}

          <div className="text-xs text-slate-400 space-y-0.5">
            <p className="font-medium text-slate-300">다음에 할 일</p>
            {toReview > 0 && (() => {
              const auto = result.auto_review?.auto_approved ?? 0;
              const sampled = result.auto_review?.sampled ?? 0;
              return auto >= toReview ? (
                <p>· 신규·바뀐 정책 {toReview}건이 모두 위험 낮음이라 자동 통과했어요 — 따로 볼 건 없어요.</p>
              ) : (
                <p>· 신규·바뀐 정책 {toReview}건 중 {auto}건은 자동 통과했어요. 나머지 {toReview - auto}건
                  {sampled > 0 ? `(표본 ${sampled}건 포함)` : ''}은
                  <span className="text-slate-200"> 항목 브라우저 › 검토대기</span>에서 위험 높음·중간부터 확인하세요.</p>
              );
            })()}
            {result.missing_marked > 0 && (
              <p className="text-amber-600 dark:text-amber-400"
                title="자동으로 지우지 않았습니다 — 일부러 뺀 정책이면 반려(검색에서 제외), 실수로 빠졌으면 승인(유지)">
                · 원본에서 사라진 정책 {result.missing_marked}건이 검토 큐에 있어요 — 반려하면 폐기, 승인하면 유지됩니다.
              </p>
            )}
            {toReview === 0 && result.missing_marked === 0 && <p>· 바뀐 정책이 없어 할 일이 없어요.</p>}
          </div>
        </div>
      )}
    </div>
  );
}
