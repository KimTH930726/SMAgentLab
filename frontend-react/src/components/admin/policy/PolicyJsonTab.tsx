import { useEffect, useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Download, Upload } from 'lucide-react';
import {
  applyPolicyJson, exportPolicyJson, previewPolicyJson, type PolicyJsonApplyResult, type PolicyJsonPreview,
} from '../../../api/policy';
import { useNamespaceAccess } from '../../../utils/useNamespaceAccess';
import { showConfirm, showError } from '../../../store/useDialogStore';
import { Button } from '../../ui/Button';

/**
 * 정책서 JSON(관리자 전용, 10/21) — 지금 정책 내려받기 / 개정본 가져오기.
 * 가져오기는 바뀐 정책마다 정정 요청에 제안 1건(출처 "JSON 가져오기")만 만든다 — 정책은 승인할 때 바뀐다.
 * 받는 파일: 여기서 내려받은 JSON, 또는 팀원 v0 폴더의 policies.jsonl.
 */
export function PolicyJsonTab({ onOpenCorrections }: { onOpenCorrections: () => void }) {
  const { selectedNs } = useNamespaceAccess();
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  // ns = 그 파일을 비교한 업무 컬렉션 — 비교 중에 영역을 바꾸면 늦게 온 결과를 버린다(10/21 리뷰 L3)
  const [file, setFile] = useState<{ name: string; content: string; ns: string } | null>(null);
  const [preview, setPreview] = useState<PolicyJsonPreview | null>(null);
  const [result, setResult] = useState<PolicyJsonApplyResult | null>(null);
  const [error, setError] = useState('');
  useEffect(() => { setFile(null); setPreview(null); setResult(null); setError(''); }, [selectedNs]);

  const download = useMutation({
    mutationFn: () => exportPolicyJson(selectedNs),
    onSuccess: (data) => {
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `policy-v0-${selectedNs}-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
    },
    onError: (e: Error) => void showError(e),
  });

  const check = useMutation({
    mutationFn: (f: { name: string; content: string; ns: string }) => previewPolicyJson(f.ns, f.content, f.name),
    onSuccess: (p, f) => { if (p.domain === selectedNs && f.ns === selectedNs) { setPreview(p); setError(''); } },
    onError: (e: Error, f) => { if (f.ns === selectedNs) { setPreview(null); setError(e.message || '파일을 비교하지 못했어요.'); } },
  });

  const apply = useMutation({
    mutationFn: () => {
      if (!file || !preview || file.ns !== selectedNs || preview.domain !== selectedNs) throw new Error('파일을 다시 골라 비교해 주세요.');
      return applyPolicyJson(selectedNs, file.content, preview.doc_hash, preview.plan_hash, file.name);
    },
    onSuccess: (r) => {
      setResult(r);
      setPreview(null);
      qc.invalidateQueries({ queryKey: ['policies', selectedNs] });
      qc.invalidateQueries({ queryKey: ['policy', selectedNs] });
      qc.invalidateQueries({ queryKey: ['corrections'] });
      qc.invalidateQueries({ queryKey: ['corrections-pending-count'] });
    },
    onError: (e: Error) => setError(e.message || '올리지 못했어요.'),
  });

  const pick = async (f: File | undefined) => {
    if (!f) return;
    setResult(null);
    setPreview(null);
    setError('');
    if (fileRef.current) fileRef.current.value = '';
    let content: string;
    try {
      content = await f.text();
    } catch (e) {
      setError(`파일을 읽지 못했어요: ${(e as Error).message || f.name}`);   // 리뷰 L4 — 읽기 실패도 화면에
      return;
    }
    const picked = { name: f.name, content, ns: selectedNs };
    setFile(picked);
    check.mutate(picked);
  };

  const submit = async () => {
    if (!preview) return;
    if (await showConfirm(`바뀐 정책 ${preview.changed.length}건을 정정 요청에 올릴까요?\n승인 전엔 검색·답변에 반영되지 않아요.`, { tone: 'info' })) {
      apply.mutate();
    }
  };

  if (!selectedNs) return <p className="text-sm text-slate-400 py-6 text-center">왼쪽에서 업무 컬렉션을 고르세요.</p>;

  return (
    <div className="space-y-4 max-w-3xl">
      <div className="flex items-center gap-2 flex-wrap">
        <Button size="sm" variant="secondary" loading={download.isPending} onClick={() => download.mutate()}
          title="지금 쓰이는 정책(승인된 버전)을 JSON으로">
          <Download className="w-4 h-4 mr-1" />내려받기
        </Button>
        <Button size="sm" loading={check.isPending} onClick={() => fileRef.current?.click()}
          title="여기서 내려받은 JSON 또는 팀원 v0 폴더의 policies.jsonl — 비교만 하고 아직 아무것도 바꾸지 않아요">
          <Upload className="w-4 h-4 mr-1" />가져오기(비교)
        </Button>
        <input ref={fileRef} type="file" accept=".json,.jsonl,application/json" className="hidden" aria-label="가져올 파일"
          onChange={(e) => void pick(e.target.files?.[0])} />
        {file && <span className="text-xs text-slate-500 truncate max-w-[40%]">{file.name}</span>}
      </div>
      <p className="text-xs text-slate-500" title="정책명·절 경로 변경, 새 정책 추가, 정책 삭제는 가져오지 않아요(목록으로만 보여 줌).">
        바뀐 정책은 정정 요청에 올라가고, 승인해야 반영돼요.
      </p>

      {error && <p role="alert" className="text-xs text-rose-600 dark:text-rose-400">{error}</p>}

      {preview && (
        <div className="rounded-xl border border-slate-700 p-4 space-y-3" data-testid="policy-json-preview">
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
            <span className="text-slate-300">같음 <b>{preview.unchanged}</b></span>
            <span className="text-indigo-600 dark:text-indigo-400">바뀜 <b>{preview.changed.length}</b></span>
            <span className="text-amber-600 dark:text-amber-400" title="이미 검토 중인 정정이 있어 올리지 않아요">검토 중이라 제외 <b>{preview.blocked.length}</b></span>
            <span className="text-slate-400" title="파일에만 있음 — 새 정책 추가는 아직 지원하지 않아요">파일에만 <b>{preview.added.length}</b></span>
            <span className="text-slate-400" title="지금 정책엔 있는데 파일엔 없음 — 지우지 않아요">파일에 없음 <b>{preview.missing.length}</b></span>
            {preview.other_domain > 0 && <span className="text-slate-500">다른 컬렉션 {preview.other_domain}(비교 안 함)</span>}
          </div>
          {preview.changed.length > 0 && (
            <ul className="text-sm divide-y divide-slate-700/60 border border-slate-700 rounded-lg max-h-72 overflow-y-auto">
              {preview.changed.map((c) => (
                <li key={c.source_key} className="px-3 py-1.5 flex items-center gap-2">
                  <span className="text-slate-200 flex-1 min-w-0 truncate" title={c.source_key}>{c.policy_name}</span>
                  <span className="text-xs text-slate-500">{[c.body_changed && '원문', c.remark_changed && '비고'].filter(Boolean).join('·')}</span>
                </li>
              ))}
            </ul>
          )}
          {preview.blocked.length > 0 && (
            <p className="text-xs text-amber-600 dark:text-amber-400">
              검토 중: {preview.blocked.map((b) => `${b.policy_name}(정정 #${b.correction_id})`).join(', ')}
            </p>
          )}
          {preview.ignored_fields.length > 0 && (
            <p className="text-xs text-slate-500">정책명·절 경로가 다른 {preview.ignored_fields.length}건은 원문·비고만 비교했어요(이름·경로는 안 바꿈).</p>
          )}
          <div className="flex justify-end">
            <Button size="sm" disabled={preview.changed.length === 0} loading={apply.isPending} onClick={() => void submit()}>
              정정 요청에 올리기 ({preview.changed.length}건)
            </Button>
          </div>
        </div>
      )}

      {result && (
        <div role="status" className="rounded-xl border border-slate-700 p-4 space-y-2 text-sm">
          <p className="text-slate-200">정정 요청에 {result.created.length}건 올렸어요.</p>
          {result.blocked.length > 0 && <p className="text-xs text-amber-600 dark:text-amber-400">검토 중이라 제외 {result.blocked.length}건</p>}
          {result.failed.length > 0 && (
            <p className="text-xs text-rose-600 dark:text-rose-400">못 올림 {result.failed.length}건: {result.failed.map((f) => `${f.source_key} — ${f.reason}`).join('; ')}</p>
          )}
          {result.created.length > 0 && <Button size="sm" variant="secondary" onClick={onOpenCorrections}>정정 요청에서 보기 →</Button>}
        </div>
      )}
    </div>
  );
}
