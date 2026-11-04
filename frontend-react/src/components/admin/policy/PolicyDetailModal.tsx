import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Archive, ChevronLeft, ChevronRight, FolderInput, MoreHorizontal, Pencil } from 'lucide-react';
import { getPolicy, type PolicyDetail, type PolicyParamRow } from '../../../api/policy';
import { correctionSourceLabel } from '../../../api/corrections';
import { useNamespaceAccess } from '../../../utils/useNamespaceAccess';
import { Modal } from '../../ui/Modal';
import { Button } from '../../ui/Button';
import { Badge } from '../../ui/Badge';
import { PolicyEditModal } from './PolicyEditModal';
import { PolicyAiSuggestModal } from './PolicyAiSuggestModal';
import { PolicyMoveModal, PolicyRetireModal } from './PolicyChangeModals';

const STATUS_LABEL: Record<string, string> = {
  active: '사용 중', deprecated: '이전 버전', rejected: '반려', pending_review: '검토 대기',
};

/** 원문에서 파라미터 값을 칠한다 — 값이 원문에 글자로 있어야 원문 파생이 맞다(설계 §7). */
function highlight(text: string, params: PolicyParamRow[]): ReactNode {
  const values = [...new Set(params.map((p) => (p.value ?? '').trim()).filter((v) => v && text.includes(v)))]
    .sort((a, b) => b.length - a.length);
  if (values.length === 0) return text;
  const out: ReactNode[] = [];
  let rest = text;
  let key = 0;
  while (rest) {
    let best: { i: number; v: string } | null = null;
    for (const v of values) {
      const i = rest.indexOf(v);
      if (i >= 0 && (best === null || i < best.i)) best = { i, v };
    }
    if (!best) { out.push(rest); break; }
    if (best.i > 0) out.push(rest.slice(0, best.i));
    out.push(<mark key={key++} className="bg-amber-100 text-amber-900 dark:bg-amber-900/40 dark:text-amber-100 rounded px-0.5">{best.v}</mark>);
    rest = rest.slice(best.i + best.v.length);
  }
  return out;
}

/**
 * 정책 상세(10/21) — 경로 · 이름 · 버전 · 원문 · 비고 · 용어 · (있으면) 값 · 버전 이력. [이전][다음]은 목록 순서대로.
 * 고치기는 [수정 제안](담당 파트·관리자, 창 안에서 [AI 초안 받기]) — 정정 요청에 제안으로 올라가고, 승인해야 새 버전이 된다(11/04 두 버튼 합침).
 * ⋯ 더보기의 [분류 옮기기 제안]·[폐기 제안](C3 2단계, 10/27)도 같은 정정 요청으로 — 승인해야 반영.
 * 이 정책에 검토 중 정정이 있으면 새 제안을 막고 그 정정으로 안내한다(한 정책에 안이 둘이면 승인 때 하나가 다른 하나를 덮는다).
 */
export function PolicyDetailModal({
  namespace, logicalId, order, onNavigate, onClose, onOpenCorrection, sourceCheckId, initialEditing,
}: {
  namespace: string;
  logicalId: number;
  order: number[];
  onNavigate: (logicalId: number) => void;
  onClose: () => void;
  onOpenCorrection: (id: number) => void;
  /** 원문 확인 카드에서 열었으면 그 카드 번호 — 여기서 올린 제안이 승인되면 카드도 해결(C3) */
  sourceCheckId?: number | null;
  /** 'direct' = 상세 대신 수정 제안 창부터(원문 확인 카드 [정정 요청], 11/04) */
  initialEditing?: 'direct' | null;
}) {
  const qc = useQueryClient();
  const { canModifyNs } = useNamespaceAccess();
  const [editing, setEditing] = useState<'direct' | 'ai' | 'move' | 'retire' | null>(initialEditing ?? null);
  const [more, setMore] = useState(false);   // ⋯ 더보기(분류 옮기기·폐기 — 드문 작업)
  const { data, isLoading, error } = useQuery({
    queryKey: ['policy', namespace, logicalId],
    queryFn: () => getPolicy(namespace, logicalId),
    staleTime: 10_000,
  });
  // 바로 수정 창으로 열었는데 이 정책에 검토 중 정정이 이미 있으면 상세(그 정정 안내)로 — 다 쓰고 나서 409 나지 않게(리뷰 M2).
  // 처음 불러왔을 때 한 번만(쓰는 도중 다시 불러와 창이 닫히지 않게)
  const initialChecked = useRef(false);
  useEffect(() => {
    if (!data || initialChecked.current) return;
    initialChecked.current = true;
    if (initialEditing && data.open_correction) setEditing(null);
  }, [data, initialEditing]);
  const edit = (m: 'direct' | 'ai' | 'move' | 'retire' | null) => { setMore(false); setEditing(m); };
  const idx = order.indexOf(logicalId);
  const prev = idx > 0 ? order[idx - 1] : null;
  const next = idx >= 0 && idx < order.length - 1 ? order[idx + 1] : null;

  const afterSubmit = () => {
    setEditing(null);
    qc.invalidateQueries({ queryKey: ['policy', namespace, logicalId] });
    qc.invalidateQueries({ queryKey: ['policies', namespace] });
    qc.invalidateQueries({ queryKey: ['corrections'] });
    qc.invalidateQueries({ queryKey: ['corrections-pending-count'] });
  };

  const open = data?.open_correction ?? null;
  return (
    <>
      {/* 수정 모달이 떠 있는 동안 상세는 닫아 둔다 — 같이 열려 있으면 Esc 한 번에 둘 다 닫혔다.
          정책을 아직 못 불러왔으면(불러오는 중·오류) 수정 창이 뜰 수 없으니 상세에 그 상태를 보인다(리뷰 M3 — 빈 화면 금지) */}
      <Modal isOpen={editing === null || !data} onClose={onClose} title="정책" maxWidth="max-w-3xl">
        {isLoading ? (
          <p className="text-xs text-slate-500">불러오는 중…</p>
        ) : error || !data ? (
          <p role="alert" className="text-sm text-rose-600 dark:text-rose-400">{(error as Error)?.message ?? '정책을 불러오지 못했어요.'}</p>
        ) : (
          <DetailBody data={data} open={open} onOpenCorrection={onOpenCorrection} />
        )}
        <div className="flex items-center gap-2 pt-4 mt-4 border-t border-slate-700 flex-wrap">
          <Button variant="ghost" size="sm" disabled={prev === null} onClick={() => prev !== null && onNavigate(prev)} title="목록의 앞 정책">
            <ChevronLeft className="w-3.5 h-3.5" />이전
          </Button>
          <Button variant="ghost" size="sm" disabled={next === null} onClick={() => next !== null && onNavigate(next)} title="목록의 다음 정책">
            다음<ChevronRight className="w-3.5 h-3.5" />
          </Button>
          {idx >= 0 && <span className="text-xs text-slate-500">{idx + 1} / {order.length}</span>}
          {canModifyNs && data && (
            // 자주 쓰는 하나만 주 버튼, 드문 것(분류 옮기기·폐기)은 ⋯ 더보기(11/04 태훈 "버튼이 너무 많다" — 업계 관례)
            <div className="ml-auto flex items-center gap-2">
              <div className="relative" onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setMore(false); }}>
                <Button variant="ghost" size="sm" disabled={!!open} onClick={() => setMore((v) => !v)} aria-haspopup="menu" aria-expanded={more}
                  aria-label="더보기" title={open ? `정정 #${open.id} 검토가 끝나야 새 제안을 올릴 수 있어요` : '분류 옮기기 · 폐기 제안'}>
                  <MoreHorizontal className="w-4 h-4" />
                </Button>
                {more && !open && (
                  <div role="menu" className="absolute bottom-full right-0 mb-1 w-48 rounded-lg border border-slate-700 bg-slate-800 py-1 shadow-lg z-10">
                    <button role="menuitem" type="button" onClick={() => edit('move')}
                      title="다른 분류(대·중·소)로 옮기는 제안 — 새 분류도 쓸 수 있어요"
                      className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-slate-200 hover:bg-slate-700">
                      <FolderInput className="w-3.5 h-3.5" />분류 옮기기 제안</button>
                    <button role="menuitem" type="button" onClick={() => edit('retire')}
                      title="승인하면 검색·답변에서 빠져요(지우지 않음)"
                      className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-slate-200 hover:bg-slate-700">
                      <Archive className="w-3.5 h-3.5" />폐기 제안</button>
                  </div>
                )}
              </div>
              <Button size="sm" disabled={!!open} onClick={() => edit('direct')}
                title={open ? `정정 #${open.id} 검토가 끝나야 새 제안을 올릴 수 있어요` : '원문·비고를 고쳐 정정 요청에 올려요(창 안에서 AI 초안도 받을 수 있어요) — 승인하면 새 버전'}>
                <Pencil className="w-3.5 h-3.5" />수정 제안
              </Button>
            </div>
          )}
        </div>
      </Modal>
      {data && (
        <>
          {/* 카드의 [정정 요청 ▶]으로 바로 연 수정 창은 닫으면 그대로 닫는다(상세가 대신 뜨면 "왜 이게 떴지" — 리뷰 L6).
              [AI 초안 받기]로 간 창을 닫으면 수정 창으로 돌아온다 */}
          <PolicyEditModal isOpen={editing === 'direct'} namespace={namespace} policy={data}
            onClose={() => (initialEditing === 'direct' ? onClose() : setEditing(null))}
            onDone={afterSubmit} sourceCheckId={sourceCheckId} onAiDraft={() => setEditing('ai')} />
          <PolicyAiSuggestModal isOpen={editing === 'ai'} namespace={namespace} policy={data}
            onClose={() => setEditing('direct')} onDone={afterSubmit} sourceCheckId={sourceCheckId} />
          <PolicyMoveModal isOpen={editing === 'move'} namespace={namespace} itemId={data.item_id} name={data.policy_name}
            currentPath={data.category_path} onClose={() => setEditing(null)} />
          <PolicyRetireModal isOpen={editing === 'retire'} namespace={namespace} itemId={data.item_id} name={data.policy_name}
            onClose={() => setEditing(null)} />
        </>
      )}
    </>
  );
}

function DetailBody({ data, open, onOpenCorrection }: {
  data: PolicyDetail; open: PolicyDetail['open_correction']; onOpenCorrection: (id: number) => void;
}) {
  return (
    <div className="space-y-4">
      <div className="space-y-1">
        {data.category_path.length > 0 && <p className="text-xs text-slate-500">{data.category_path.join(' › ')}</p>}
        <div className="flex items-center gap-2 flex-wrap">
          <h4 className="text-base font-semibold text-slate-100">{data.policy_name}</h4>
          <Badge color="slate">v{data.version}</Badge>
          <Badge color="emerald">{STATUS_LABEL[data.status] ?? data.status}</Badge>
        </div>
      </div>

      {open && (
        <div className="flex items-center gap-2 flex-wrap rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-700/50 dark:bg-amber-900/20 dark:text-amber-200">
          <span>정정 #{open.id} 검토 중 · {correctionSourceLabel(open.source)}</span>
          <button onClick={() => onOpenCorrection(open.id)} className="font-medium underline hover:no-underline">
            정정 요청에서 보기 →
          </button>
        </div>
      )}

      <section className="space-y-1">
        <p className="text-xs text-slate-500">원문</p>
        <pre className="whitespace-pre-wrap font-sans text-sm text-slate-200 bg-slate-900/60 border border-slate-700 rounded-lg p-3 max-h-80 overflow-y-auto">
          {highlight(data.raw_body, data.params)}
        </pre>
      </section>

      {data.remark && (
        <section className="space-y-1">
          <p className="text-xs text-slate-500" title="답변에 쓰고 검색엔 안 씀">비고</p>
          <p className="whitespace-pre-wrap text-sm text-slate-300 bg-slate-900/40 border border-slate-700 rounded-lg p-3">{data.remark}</p>
        </section>
      )}

      {data.terms.length > 0 && (
        <section className="space-y-1">
          <p className="text-xs text-slate-500" title="답변 때 이 정책과 같이 붙는 용어 설명">용어</p>
          <div className="flex flex-wrap gap-1.5">
            {data.terms.map((t) => (
              <span key={t.id} title={t.description} className="px-2 py-0.5 rounded-full text-xs border border-slate-600 text-slate-300 bg-slate-800">
                {t.term}
              </span>
            ))}
          </div>
        </section>
      )}

      {data.params.length > 0 && (
        <section className="space-y-1">
          <p className="text-xs text-slate-500" title="원문에서 뽑은 값 — 원문에 칠해진 부분. 원문에 없는 값은 빨강(확인 필요)">값</p>
          <table className="w-full text-xs">
            <thead>
              <tr className="text-slate-500 text-left"><th className="py-1 pr-2 font-normal">항목</th><th className="py-1 pr-2 font-normal">조건</th><th className="py-1 font-normal">값</th></tr>
            </thead>
            <tbody>
              {data.params.map((p) => {
                const inSource = !!p.value && data.raw_body.includes(p.value.trim());
                return (
                  <tr key={p.id} className="border-t border-slate-700/60">
                    <td className="py-1 pr-2 text-slate-300">{p.name}</td>
                    <td className="py-1 pr-2 text-slate-400">{p.condition ?? '-'}</td>
                    <td className={`py-1 ${inSource ? 'text-slate-200' : 'text-rose-600 dark:text-rose-400'}`}
                      title={inSource ? undefined : '원문에 이 값이 글자로 없어요 — 확인 필요'}>
                      {p.value ?? '-'}{p.unit ?? ''}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>
      )}

      <section className="space-y-1">
        <p className="text-xs text-slate-500">버전</p>
        <ul className="text-xs space-y-0.5">
          {data.versions.map((v) => (
            <li key={v.item_id} className="flex items-center gap-2 text-slate-400">
              <span className={v.status === 'active' ? 'text-slate-200 font-medium' : ''}>v{v.version}</span>
              <span>{STATUS_LABEL[v.status] ?? v.status}</span>
              <span className="text-slate-500">· {v.origin}</span>
              {v.created_at && <span className="text-slate-500">· {new Date(v.created_at).toLocaleString('ko-KR')}</span>}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
