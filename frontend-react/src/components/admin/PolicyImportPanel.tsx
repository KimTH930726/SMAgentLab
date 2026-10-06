import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Upload, AlertTriangle, CheckCircle } from 'lucide-react';
import { importPolicyFile, type PolicyImportProblem, type PolicyImportResult } from '../../api/policy';
import { ApiError } from '../../api/client';
import { useNamespaceAccess } from '../../utils/useNamespaceAccess';
import { useAuthStore } from '../../store/useAuthStore';
import { Button } from '../ui/Button';

/**
 * 정책서 올리기(2026-10-06, v2.124) — 이전엔 임포트 화면이 없어 API·스크립트로만 넣었다.
 * 표준 입력은 JSON(사내 문서 보안 암호화로 서버가 엑셀을 못 여는 경우가 있어서) — 담당자 PC에서
 * `scripts/excel_to_policy_json.py`로 변환해 올린다. 엑셀도 그대로 받는다(암호화 안 된 파일).
 * 같은 정책은 식별키(분류 경로+정책명)로 찾아 바뀐 것만 새 버전, 사라진 정책은 검토 큐로(v2.123).
 * 사용자가 "됐는지 / 안 됐으면 왜·뭘 하면 되는지 / 다음에 뭘 하는지"를 바로 알게 — 오류는 "어디·문제·할 일" 표로,
 * 성공은 반영 결과 + 읽은 칸 + 경고 + 다음 할 일로.
 */
const COLS: { key: 'created_items' | 'new_versions' | 'unchanged_skipped' | 'moved' | 'matched_by_body' | 'duplicate_keys'; label: string; tip: string }[] = [
  { key: 'created_items', label: '신규', tip: '처음 들어온 정책 — AI가 분해해 검토 대기로' },
  { key: 'new_versions', label: '새 버전', tip: '내용이 바뀐 정책 — AI가 다시 분해해 검토 대기로(이전 버전은 보관)' },
  { key: 'unchanged_skipped', label: '변경 없음', tip: '내용이 같아 건너뜀 — 승인 상태 그대로' },
  { key: 'moved', label: '위치만 갱신', tip: '내용은 같고 행·파일 위치만 바뀜 — 승인 상태 그대로' },
  { key: 'matched_by_body', label: '이름·분류 변경', tip: '분류 경로나 정책명이 바뀌었지만 본문이 같아 같은 정책으로 이음' },
  { key: 'duplicate_keys', label: '중복 키', tip: '파일 안에 같은 분류 경로+정책명이 또 나온 행 — 원본에서 중복인지 확인하세요' },
];

function problemsOf(err: unknown): PolicyImportProblem[] {
  const d = err instanceof ApiError ? err.detail : undefined;
  return d && typeof d === 'object' && Array.isArray((d as { problems?: unknown }).problems)
    ? (d as { problems: PolicyImportProblem[] }).problems : [];
}

export function PolicyImportPanel({ onGoReview }: { onGoReview?: () => void } = {}) {
  const qc = useQueryClient();
  const { selectedNs, setSelectedNs, sortedNamespaces, canModifyNs } = useNamespaceAccess();
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin');
  const [file, setFile] = useState<File | null>(null);
  const [reprocessAll, setReprocessAll] = useState(false);
  const [error, setError] = useState<{ message: string; problems: PolicyImportProblem[] } | null>(null);

  const mutation = useMutation({
    mutationFn: () => importPolicyFile(file!, selectedNs, isAdmin && reprocessAll),
    onSuccess: () => {
      setError(null);
      qc.invalidateQueries({ queryKey: ['policy-items'] });
      qc.invalidateQueries({ queryKey: ['policy-items-all'] });
      qc.invalidateQueries({ queryKey: ['policy-review-summary'] });
    },
    onError: (err: Error) => setError({ message: err.message, problems: problemsOf(err) }),
  });
  const result: PolicyImportResult | undefined = mutation.data;
  const toReview = result ? result.sheets.reduce((n, s) => n + s.created_items + s.new_versions, 0) : 0;
  const warnings = result ? [...result.warnings, ...result.sheets.flatMap((s) => s.warnings.map((w) => `'${s.sheet_name}' 시트: ${w}`))] : [];

  return (
    <div className="space-y-4 max-w-4xl">
      {/* 사용자 지적(2026-10-06): 단계 카드 3개만 두니 "기능 3개를 준다는 건가"로 읽힘 — 이 화면에서 하는 건 ②뿐이고
          ①은 그 전에 담당자 PC에서, ③은 그 뒤에 다른 화면에서 한다는 걸 순서 있는 안내문으로 */}
      <div className="rounded-xl border border-slate-700 bg-slate-800 px-4 py-3 text-xs text-slate-300 space-y-1.5">
        <p className="text-sm font-medium text-slate-200">정책서를 반영하는 순서</p>
        <p>
          <span className="font-medium text-slate-200">① 올리기 전(담당자 PC)</span> — 정책 엑셀을 JSON 파일로 바꿉니다:{' '}
          <code className="px-1 rounded bg-slate-900 text-slate-200">python excel_to_policy_json.py 정책서.xlsx</code>
          <span className="text-slate-500" title="보안(DRM) 문서도 Excel 프로그램으로 자동으로 읽습니다. 헤더 이름이 다르면 --map 본문=&quot;헤더 이름&quot;. 실행하면 엑셀의 어느 칸을 읽었는지 보여 줍니다">
            {' '}(같은 이름의 .json이 생깁니다 · 보안 문서도 됩니다)
          </span>
        </p>
        <p>
          <span className="font-medium text-slate-200">② 이 화면</span> — 파트를 고르고 그 JSON 파일을 올립니다. 바뀐 정책만 AI가 분해하고,
          내용이 같은 정책은 그대로 둡니다. (보안이 없는 엑셀은 그대로 올려도 됩니다)
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
          <select value={selectedNs} onChange={(e) => setSelectedNs(e.target.value)}
            className="w-56 bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500">
            <option value="">선택...</option>
            {sortedNamespaces.map((ns) => <option key={ns} value={ns}>{ns}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-xs font-medium text-slate-400 mb-1.5" title="변환기로 만든 JSON(권장) 또는 보안 암호화가 없는 엑셀">파일</label>
          <input type="file" accept=".json,.xlsx"
            onChange={(e) => { setFile(e.target.files?.[0] ?? null); setError(null); mutation.reset(); }}
            className="text-sm text-slate-300 file:mr-3 file:rounded-lg file:border-0 file:bg-slate-700 file:px-3 file:py-2 file:text-sm file:text-slate-200" />
        </div>
        {isAdmin && (
          <label className="flex items-center gap-2 text-xs text-slate-400 pb-2"
            title="내용이 같은 정책도 전부 AI로 다시 분해해 새 버전(검토 대기)으로 넣습니다. 분해 방식이 바뀌면 시스템이 알아서 다시 분해하므로, 그 감지에 안 걸리는 경우(예: 사내 LLM 모델 교체)에만 씁니다">
            <input type="checkbox" checked={reprocessAll} onChange={(e) => setReprocessAll(e.target.checked)} />
            전체 다시 분해(관리자)
          </label>
        )}
        <Button variant="primary" size="sm" loading={mutation.isPending}
          disabled={!file || !selectedNs || !canModifyNs || mutation.isPending}
          onClick={() => mutation.mutate()}>
          <Upload className="w-3.5 h-3.5" />올리기
        </Button>
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
                          title="엑셀의 어느 칸을 어디로 읽었는지 — 틀렸으면 변환기 --map으로 지정해 다시 변환하세요">
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
