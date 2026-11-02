import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowRight } from 'lucide-react';
import { decideLink, decideSourceCheck, listSourceChecks, NO_ISSUE_REASONS, type NoIssueReason, type SourceCheck, type SuggestedLink } from '../../../api/policy';
import { useNamespaceAccess } from '../../../utils/useNamespaceAccess';
import { showConfirm, showError } from '../../../store/useDialogStore';
import { Badge } from '../../ui/Badge';
import { Button } from '../../ui/Button';
import { FormModal } from '../../ui/FormModal';
import { PolicyDetailModal } from './PolicyDetailModal';

/**
 * 원문 확인(v0 재구축 3단계, 2026-10-21) — 정책서 원문에 대한 질문·문제(담당자에게 묻는 것)와 규칙으로 추정한 제안 연결.
 * 내용 수정이 아니다: 고칠 게 정해지면 정책 상세의 수정 제안 → 품질 › 정정 요청에서 승인. 여기선 [해결](메모)·[문제 없음](사유)만.
 * C3(10/26): 카드의 정책 칩에서 연 정책으로 제안을 올리면 카드 = "정정 요청 중 #n" → 승인되면 카드 자동 해결, 반려되면 처리 전 그대로.
 * [닫기] → [문제 없음](사유 4개 — 원문이 맞음·이미 고쳐짐·중복·기타). 사유는 AI 판단 원장에도(검사기 오탐률).
 */
const TYPE_LABEL: Record<string, { label: string; color: 'amber' | 'rose' | 'cyan' }> = {
  question: { label: '담당자 질문', color: 'cyan' },
  conflict: { label: '정책끼리 어긋남', color: 'rose' },
  ref_error: { label: '참고표 오류', color: 'amber' },
};
const SOURCE_LABEL: Record<string, string> = { v0_load_check: '적재 점검', golden_set: '골든셋 작성' };
const STATUS_LABEL: Record<string, string> = { resolved: '해결', dismissed: '문제 없음', active: '확정', rejected: '거절' };
const AREA = 'w-full bg-slate-800 border border-slate-600 rounded-lg px-2.5 py-1.5 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 dark:focus:border-indigo-400 resize-y';

type Pending = { check: SourceCheck; action: 'resolve' | 'dismiss' } | null;

export function SourceCheckTab({ onOpenCorrection }: { onOpenCorrection: (id: number) => void }) {
  const { selectedNs, canModifyNs } = useNamespaceAccess();
  const qc = useQueryClient();
  const [view, setView] = useState<'open' | 'done'>('open');
  const [pending, setPending] = useState<Pending>(null);
  const [note, setNote] = useState('');
  const [reason, setReason] = useState<NoIssueReason | null>(null);
  const [error, setError] = useState('');
  const [policyOpen, setPolicyOpen] = useState<number | null>(null);
  const [fromCheck, setFromCheck] = useState<number | null>(null);   // 카드에서 연 정책이면 그 카드 — 여기서 올린 제안이 승인되면 카드도 해결
  useEffect(() => { setPolicyOpen(null); }, [selectedNs]);

  const key = ['source-checks', selectedNs, view];
  const { data, isLoading, error: loadError } = useQuery({
    queryKey: key,
    queryFn: () => listSourceChecks(selectedNs, view),
    enabled: !!selectedNs,
    staleTime: 15_000,
  });
  const refresh = () => qc.invalidateQueries({ queryKey: ['source-checks', selectedNs] });

  const decide = useMutation({
    mutationFn: () => decideSourceCheck(pending!.check.id, pending!.action, note.trim(), pending!.action === 'dismiss' ? reason : null),
    onSuccess: () => { setPending(null); refresh(); },
    onError: (e: Error) => setError(e.message || '처리하지 못했어요.'),
  });
  const link = useMutation({
    mutationFn: ({ id, action }: { id: number; action: 'confirm' | 'reject' }) => decideLink(id, action),
    onSuccess: refresh,
    onError: (e: Error) => { void showError(e); refresh(); },
  });

  const open = (check: SourceCheck, action: 'resolve' | 'dismiss') => { setPending({ check, action }); setNote(''); setReason(null); setError(''); };
  const openPolicy = (logicalId: number, checkId: number | null) => { setPolicyOpen(logicalId); setFromCheck(checkId); };
  const canSubmit = pending?.action === 'dismiss' ? !!reason && (reason !== 'other' || !!note.trim()) : !!note.trim();
  const rejectLink = async (l: SuggestedLink) => {
    if (await showConfirm(`이 제안 연결을 거절할까요?\n${l.policy.policy_name} → ${l.target.path.join(' › ')}`, { tone: 'warning' })) {
      link.mutate({ id: l.id, action: 'reject' });
    }
  };

  if (!selectedNs) return <p className="text-sm text-slate-400 py-6 text-center">왼쪽에서 정책 컬렉션을 고르세요.</p>;
  const checks = data?.checks ?? [];
  const links = data?.links ?? [];

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex rounded-lg border border-slate-700 overflow-hidden text-sm" role="group" aria-label="보기">
          {(['open', 'done'] as const).map((v) => (
            <button key={v} onClick={() => setView(v)} aria-pressed={view === v}
              className={view === v ? 'px-3 py-1 bg-slate-700 text-slate-100' : 'px-3 py-1 text-slate-400 hover:text-slate-200'}>
              {v === 'open' ? '확인할 것' : '처리됨'}
            </button>
          ))}
        </div>
        <span className="text-xs text-slate-500" title="원문 자체에 대한 질문·문제예요. 고칠 내용이 정해지면 정책 상세의 수정 제안 → 정정 요청에서 승인합니다.">
          질문 {checks.length} · 제안 연결 {links.length}
        </span>
      </div>

      {loadError && <p role="alert" className="text-xs text-rose-600 dark:text-rose-400">{(loadError as Error).message}</p>}
      {isLoading ? <p className="text-xs text-slate-500">불러오는 중…</p> : (
        <>
          {checks.length === 0 && links.length === 0 && (
            <p className="text-sm text-slate-500 py-10 text-center">{view === 'open' ? '확인할 원문 문제가 없어요.' : '처리한 항목이 없어요.'}</p>
          )}
          {checks.length > 0 && (
            <ul className="space-y-2">
              {checks.map((c) => (
                <li key={c.id} data-testid={`source-check-${c.id}`} className="rounded-xl border border-slate-700 p-3 space-y-2">
                  <div className="flex items-center gap-2 flex-wrap text-xs">
                    <Badge color={TYPE_LABEL[c.type]?.color ?? 'slate'}>{TYPE_LABEL[c.type]?.label ?? c.type}</Badge>
                    <span className="text-slate-500">#{c.id} · {SOURCE_LABEL[c.source] ?? c.source}</span>
                    {c.status !== 'open' && <Badge color={c.status === 'resolved' ? 'emerald' : 'slate'}>{STATUS_LABEL[c.status]}</Badge>}
                    {c.decided_by && <span className="text-slate-500">· {c.decided_by}</span>}
                  </div>
                  <p className="text-sm text-slate-200">{c.description}</p>
                  {(c.policies.length > 0 || c.ref_tables.length > 0) && (
                    <div className="flex flex-wrap gap-1.5">
                      {c.policies.map((p) => (
                        <button key={p.logical_id} onClick={() => openPolicy(p.logical_id, c.status === 'open' ? c.id : null)} disabled={!p.policy_name}
                          title={p.category_path.join(' › ')}
                          className="text-xs px-2 py-0.5 rounded-md border border-slate-600 text-slate-300 hover:border-indigo-500 dark:hover:border-indigo-400 disabled:opacity-50">
                          {p.policy_name ?? `폐기된 정책 #${p.logical_id}`}
                        </button>
                      ))}
                      {c.ref_tables.map((t) => (
                        <span key={t.logical_id} className="text-xs px-2 py-0.5 rounded-md border border-slate-600 text-slate-400" title="참고표 탭에서 원표 보기">
                          참고표 · {t.title ?? t.logical_id}
                        </span>
                      ))}
                    </div>
                  )}
                  {c.note && <p className="text-xs text-slate-400"><span className="text-slate-500 mr-1">{c.status === 'resolved' ? '메모' : '문제 없는 이유'}</span>{c.note}</p>}
                  {c.status === 'open' && c.correction && (
                    c.correction.status === 'pending' ? (
                      <button onClick={() => onOpenCorrection(c.correction!.id)} data-testid={`source-check-correction-${c.id}`}
                        title="이 카드에서 올린 정정 제안 — 승인되면 이 카드도 해결로 바뀌어요"
                        className="text-xs text-indigo-600 dark:text-indigo-400 hover:underline">정정 요청 중 #{c.correction.id} ↗</button>
                    ) : c.correction.status === 'rejected' ? (
                      <p className="text-xs text-amber-600 dark:text-amber-400" title="올린 정정 제안이 반려돼 카드는 처리 전 그대로예요 — 다시 보거나 [문제 없음]으로 닫으세요">
                        정정 #{c.correction.id} 반려됨 — 다시 확인 ⓘ</p>
                    ) : null
                  )}
                  {c.status === 'open' && canModifyNs && (
                    <div className="flex gap-2 justify-end">
                      <Button size="sm" variant="secondary" onClick={() => open(c, 'dismiss')}
                        title="원문에 문제가 없어 닫아요 — 이유를 골라 주세요(검사가 괜히 짚은 비율로 쌓여요)">문제 없음</Button>
                      <Button size="sm" onClick={() => open(c, 'resolve')}
                        title="정책 칩을 눌러 수정 제안을 올리면 승인될 때 저절로 해결돼요 — 이 버튼은 다른 방법으로 정리했을 때">해결</Button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}

          {links.length > 0 && (
            <div className="space-y-2">
              <p className="text-xs text-slate-500" title="원문의 절 번호가 틀려 적재기가 규칙으로 추정한 참조 연결이에요. 확정하면 연결로 씁니다.">제안 연결</p>
              <ul className="rounded-xl border border-slate-700 divide-y divide-slate-700/60">
                {links.map((l) => (
                  <li key={l.id} data-testid={`suggested-link-${l.id}`} className="flex items-center gap-2 px-3 py-2 text-sm flex-wrap">
                    <button onClick={() => openPolicy(l.policy.logical_id, null)} className="text-slate-200 hover:text-indigo-600 dark:hover:text-indigo-400 truncate max-w-[45%]"
                      title={l.policy.category_path.join(' › ')}>{l.policy.policy_name}</button>
                    <ArrowRight className="w-3.5 h-3.5 text-slate-500 flex-shrink-0" />
                    <span className="text-slate-400 truncate" title={l.target.namespace ?? undefined}>{l.target.path.join(' › ')}</span>
                    {l.status === 'suggested' ? (canModifyNs && (
                      <span className="ml-auto flex gap-2">
                        <Button size="sm" variant="secondary" disabled={link.isPending} onClick={() => void rejectLink(l)}>거절</Button>
                        <Button size="sm" disabled={link.isPending} onClick={() => link.mutate({ id: l.id, action: 'confirm' })}>확정</Button>
                      </span>
                    )) : <Badge color={l.status === 'active' ? 'emerald' : 'slate'} className="ml-auto">{STATUS_LABEL[l.status] ?? l.status}</Badge>}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}

      {pending && (
        <FormModal
          isOpen
          title={pending.action === 'resolve' ? `해결 — #${pending.check.id}` : `문제 없음 — #${pending.check.id}`}
          onClose={() => setPending(null)}
          onSubmit={() => decide.mutate()}
          submitLabel={pending.action === 'resolve' ? '해결로 표시' : '문제 없음으로 닫기'}
          submitting={decide.isPending}
          submitDisabled={!canSubmit}
          error={error}
        >
          {pending.action === 'dismiss' && (
            <fieldset className="space-y-1" aria-label="문제 없는 이유">
              {NO_ISSUE_REASONS.map((r) => (
                <label key={r.key} className="flex items-center gap-2 text-sm text-slate-200">
                  <input type="radio" name={`no-issue-${pending.check.id}`} checked={reason === r.key} onChange={() => setReason(r.key)} />
                  {r.label}
                </label>
              ))}
            </fieldset>
          )}
          <p className="text-xs text-slate-400" title="원문을 고쳐야 하면 정책 칩을 눌러 수정 제안으로 올리세요 — 여기선 정책이 바뀌지 않습니다.">
            {pending.action === 'resolve' ? '어떻게 정리됐는지 적어 주세요.' : reason === 'other' ? '이유를 적어 주세요.' : '덧붙일 말(선택)'}
          </p>
          <textarea aria-label={pending.action === 'resolve' ? '해결 메모' : '문제 없는 이유 메모'} rows={3} value={note}
            onChange={(e) => setNote(e.target.value)} maxLength={1000} className={AREA} />
        </FormModal>
      )}

      {policyOpen !== null && (
        <PolicyDetailModal
          namespace={selectedNs}
          logicalId={policyOpen}
          order={[policyOpen]}
          onNavigate={setPolicyOpen}
          onClose={() => setPolicyOpen(null)}
          onOpenCorrection={onOpenCorrection}
          sourceCheckId={fromCheck}
        />
      )}
    </div>
  );
}
