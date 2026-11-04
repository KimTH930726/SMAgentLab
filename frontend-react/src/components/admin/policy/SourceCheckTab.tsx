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
 * 내용 수정이 아니다: 고칠 게 정해지면 정책 상세의 수정 제안 → 품질 › 정정 요청에서 승인. 여기선 [처리 완료로 닫기](메모)·[이슈 아님으로 닫기](사유) + [정정 요청](수정 제안 창 바로 열기, 11/04).
 * C3(10/26): 카드의 정책 칩에서 연 정책으로 제안을 올리면 카드 = "정정 요청 중 #n" → 승인되면 카드 자동 해결, 반려되면 처리 전 그대로.
 * [이슈 아님으로 닫기](사유 4개 — 원문이 맞음·이미 고쳐짐·중복·기타). 사유는 AI 판단 원장에도(검사기 오탐률).
 */
const TYPE_LABEL: Record<string, { label: string; color: 'amber' | 'rose' | 'cyan' }> = {
  question: { label: '담당자 질문', color: 'cyan' },
  conflict: { label: '정책끼리 어긋남', color: 'rose' },
  ref_error: { label: '참고표 오류', color: 'amber' },
};
const SOURCE_LABEL: Record<string, string> = { v0_load_check: '적재 점검', golden_set: '골든셋 작성' };
// 처리 결과(11/04 업계 용어 — 이슈 아님 · 처리 완료)
const STATUS_LABEL: Record<string, string> = { resolved: '처리 완료', dismissed: '이슈 아님', active: '확정', rejected: '거절' };
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
  const [editFirst, setEditFirst] = useState(false);   // [정정 요청] — 상세 대신 수정 제안 창부터(11/04)
  useEffect(() => { setPolicyOpen(null); }, [selectedNs]);

  const key = ['source-checks', selectedNs, view];
  const { data, isLoading, error: loadError } = useQuery({
    queryKey: key,
    queryFn: () => listSourceChecks(selectedNs, view),
    enabled: !!selectedNs,
    staleTime: 15_000,
  });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['source-checks', selectedNs] });
    qc.invalidateQueries({ queryKey: ['namespaces-detail'] });   // 탭·메뉴 숫자(pending_source_checks)도 같이
  };

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
  const openPolicy = (logicalId: number, checkId: number | null, edit = false) => { setPolicyOpen(logicalId); setFromCheck(checkId); setEditFirst(edit); };
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
        <span className="text-xs text-slate-500" title="원문 자체에 대한 질문·문제예요. 고칠 내용이 정해지면 [정정 요청] → 승인하면 새 버전이 되고 카드도 닫혀요.">
          질문 {checks.length} · 제안 연결 {links.length}
        </span>
      </div>
      {view === 'open' && checks.length > 0 && (
        <p className="text-xs text-slate-400" data-testid="source-check-guide">
          원문을 고쳐야 하면 <b className="text-slate-200">[정정 요청]</b> · 원문이 맞으면 <b className="text-slate-200">[이슈 아님으로 닫기]</b> · 다른 방법으로 이미 처리했으면 <b className="text-slate-200">[처리 완료로 닫기]</b>. 닫은 카드는 '처리됨'에서 다시 볼 수 있어요.
        </p>
      )}

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
                    // 버튼이 아니라 "관련 정책" 링크 — 칩 모양이라 "그 정책으로 대체?"로 읽혔다(11/04 태훈)
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
                      <span className="text-slate-500">관련 정책</span>
                      {c.policies.map((p) => (
                        <button key={p.logical_id} type="button" onClick={() => openPolicy(p.logical_id, c.status === 'open' ? c.id : null)} disabled={!p.policy_name}
                          title={`${p.category_path.join(' › ')} — 정책 상세 보기`} data-testid={`source-check-policy-${c.id}-${p.logical_id}`}
                          className="text-indigo-600 hover:underline dark:text-indigo-400 disabled:text-slate-500 disabled:no-underline">
                          {p.policy_name ?? `폐기된 정책 #${p.logical_id}`} ↗
                        </button>
                      ))}
                      {c.ref_tables.map((t) => (
                        <span key={t.logical_id} className="text-xs px-2 py-0.5 rounded-md border border-slate-600 text-slate-400" title="참고표 탭에서 원표 보기">
                          참고표 · {t.title ?? t.logical_id}
                        </span>
                      ))}
                    </div>
                  )}
                  {c.note && <p className="text-xs text-slate-400"><span className="text-slate-500 mr-1">{c.status === 'resolved' ? '처리 메모' : '이슈 아닌 이유'}</span>{c.note}</p>}
                  {c.status === 'open' && c.correction && (
                    c.correction.status === 'pending' ? (
                      <button onClick={() => onOpenCorrection(c.correction!.id)} data-testid={`source-check-correction-${c.id}`}
                        title="이 카드에서 올린 정정 제안 — 승인되면 이 카드도 처리 완료로 닫혀요"
                        className="text-xs text-indigo-600 dark:text-indigo-400 hover:underline">정정 요청 중 #{c.correction.id} ↗</button>
                    ) : c.correction.status === 'rejected' ? (
                      <p className="text-xs text-amber-600 dark:text-amber-400" title="올린 정정 제안이 반려돼 카드는 처리 전 그대로예요 — 다시 정정 요청하거나 [이슈 아님으로 닫기]">
                        정정 #{c.correction.id} 반려됨 — 다시 확인 ⓘ</p>
                    ) : null
                  )}
                  {c.status === 'open' && canModifyNs && (
                    <div className="flex flex-wrap gap-2 justify-end">
                      <Button size="sm" variant="ghost" onClick={() => open(c, 'dismiss')}
                        title="원문이 맞아 고칠 게 없어요 — 사유를 골라 닫으면 '처리됨'으로 가요(AI 감사에 원문 확인 오탐으로 쌓여요)">이슈 아님으로 닫기</Button>
                      <Button size="sm" variant="secondary" onClick={() => open(c, 'resolve')}
                        title="원문 수정 말고 다른 방법으로 이미 처리했어요 — 메모를 남기고 '처리됨'으로">처리 완료로 닫기</Button>
                      {c.policies.some((p) => p.policy_name) && !(c.correction?.status === 'pending') && (
                        <Button size="sm" onClick={() => openPolicy(c.policies.find((p) => p.policy_name)!.logical_id, c.id, true)}
                          data-testid={`source-check-fix-${c.id}`}
                          title={`원문을 고쳐요 — ${c.policies.find((p) => p.policy_name)!.policy_name} 수정 제안 창이 바로 열리고, 승인되면 새 버전이 되며 이 카드도 저절로 닫혀요${c.policies.filter((p) => p.policy_name).length > 1 ? ' (다른 관련 정책은 이름을 눌러 상세에서 [수정 제안])' : ''}`}>정정 요청 ▶</Button>
                      )}
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
          title={pending.action === 'resolve' ? `처리 완료로 닫기 — #${pending.check.id}` : `이슈 아님으로 닫기 — #${pending.check.id}`}
          onClose={() => setPending(null)}
          onSubmit={() => decide.mutate()}
          submitLabel={pending.action === 'resolve' ? '처리 완료로 닫기' : '이슈 아님으로 닫기'}
          submitting={decide.isPending}
          submitDisabled={!canSubmit}
          error={error}
        >
          {pending.action === 'dismiss' && (
            <fieldset className="space-y-1" aria-label="이슈 아닌 이유">
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
          <textarea aria-label={pending.action === 'resolve' ? '처리 메모' : '이슈 아닌 이유 메모'} rows={3} value={note}
            onChange={(e) => setNote(e.target.value)} maxLength={1000} className={AREA} />
        </FormModal>
      )}

      {policyOpen !== null && (
        <PolicyDetailModal
          key={`${policyOpen}-${editFirst}`}   // 열 때마다 새로(수정 창부터 연 상태가 다음 [관련 정책] 열기에 남지 않게, 리뷰 M3)
          namespace={selectedNs}
          logicalId={policyOpen}
          order={[policyOpen]}
          onNavigate={setPolicyOpen}
          onClose={() => setPolicyOpen(null)}
          onOpenCorrection={onOpenCorrection}
          sourceCheckId={fromCheck}
          initialEditing={editFirst ? 'direct' : null}
        />
      )}
    </div>
  );
}
