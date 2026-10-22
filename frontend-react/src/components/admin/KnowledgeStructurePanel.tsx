import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { showAlert, showConfirm, showError } from '../../store/useDialogStore';
import { ChevronRight } from 'lucide-react';
import { getKnowledgeStructure, rechunkSourceDocument, type KnowledgeStructure, type KnowledgeOutlineItem } from '../../api/knowledge';

/**
 * 원문에서의 위치(2026-10-07) — 지식 상세·수정 창.
 * "앞·뒤·이웃" 같은 내부 용어 대신 원문 목차를 그대로 보여준다(사용자 피드백: 앞·뒤·이웃이 뭔지 모르겠다).
 *  - 경로: 원문 제목 › 상위 섹션 › 이 지식
 *  - 목차: 같은 원문의 조각 전체를 원문 순서대로, 상위 경로 깊이만큼 들여쓰기, 지금 보는 지식 강조
 *  - "함께 전달": 이 지식이 검색되면 AI에게 같이 전달되는 조각(검색 코드와 같은 규칙) — 설명은 툴팁
 *  - 직접 입력: 원문 목차 없음 한 줄
 * 청크 경계가 이상하면(같은 내용이 위아래에 또 있음, 제목이 엉뚱한 곳에 붙음) 목차에서 바로 보이게 하려는 것.
 */

const MAX_VISIBLE = 12;

function OutlineRow({ item }: { item: KnowledgeOutlineItem }) {
  const [open, setOpen] = useState(false);
  const pad = Math.min(item.depth, 4) * 12;
  return (
    <li>
      <button type="button" onClick={() => !item.is_self && setOpen((v) => !v)} disabled={item.is_self}
        aria-expanded={item.is_self ? undefined : open}
        title={item.is_self ? '지금 보는 지식' : '눌러서 내용 보기'}
        style={{ paddingLeft: 8 + pad }}
        className={`w-full text-left flex items-center gap-2 pr-2 py-1.5 rounded-md text-xs focus:outline-none focus-visible:ring-1 focus-visible:ring-indigo-500 dark:focus-visible:ring-indigo-400 ${
          item.is_self
            ? 'bg-indigo-50 text-indigo-800 font-medium dark:bg-indigo-950/40 dark:text-indigo-200 cursor-default'
            : 'text-slate-300 hover:bg-slate-700/50'}`}>
        <span className="truncate flex-1">{item.title || '(제목 없음)'}</span>
        {item.is_self && <span className="text-[11px] flex-shrink-0">지금 보는 지식</span>}
        {item.attached && (
          <span className="text-[10px] px-1.5 py-0.5 rounded flex-shrink-0 bg-emerald-100 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300"
            title="이 지식이 검색되면 이 조각도 AI에게 함께 전달됩니다(같은 상위 섹션, 원문에서 가까운 순, 글자 수 한도 안 — 같은 원문의 다른 지식이 함께 검색되면 조금 달라질 수 있음)">
            함께 전달
          </span>
        )}
      </button>
      {open && (
        <pre style={{ marginLeft: 8 + pad }}
          className="mr-2 mb-2 mt-0.5 whitespace-pre-wrap break-words text-[11px] leading-relaxed text-slate-400 bg-slate-900 border border-slate-700 rounded-md p-2 max-h-48 overflow-y-auto">
          {item.preview}{item.truncated ? '…' : ''}
        </pre>
      )}
    </li>
  );
}

// 원문에서 다시 분할(2026-10-08 원문 원장) — 결과를 앱 모달로 알리고, 사람이 고친 조각이 있으면 한 번 더 확인
function RechunkButton({ docId, namespace }: { docId: number; namespace: string }) {
  const qc = useQueryClient();
  const run = useMutation({
    mutationFn: (force: boolean) => rechunkSourceDocument(docId, force),
    onSuccess: async (r) => {
      if (r.blocked) {
        // 사람의 판단이 들어간 흔적 — 다시 나누면 원문 기준으로 되돌아간다(옛 조각은 폐기 상태로 남음)
        const why = [
          r.edited ? `내용을 고친 조각 ${r.edited}개` : '',
          r.removed ? `삭제·반려한 조각 ${r.removed}개(다시 생김)` : '',
          r.partial ? '등록 때 일부 조각만 고름(뺀 조각이 다시 생김)' : '',
        ].filter(Boolean).join('\n· ');
        const ok = await showConfirm(
          `사람이 손댄 부분이 있습니다:\n· ${why}\n\n다시 나누면 원문 기준으로 바뀝니다(옛 조각은 폐기 상태로 남음). 진행할까요?`,
          { tone: 'danger', confirmLabel: '다시 나누기' });
        if (ok) run.mutate(true);
        return;
      }
      if (!r.changed) {
        void showAlert('지금 코드로 다시 나눠도 결과가 같습니다 — 바뀐 것이 없습니다.', { title: '원문에서 다시 분할' });
        return;
      }
      const warn = r.quality?.warnings?.length ? `\n품질 확인 필요: ${r.quality.warnings.join(', ')}` : '';
      void showAlert(`조각 ${r.old}개 → ${r.new}개로 다시 나눴습니다.${warn}`, { title: '원문에서 다시 분할', tone: 'success' });
      qc.invalidateQueries({ queryKey: ['knowledge', namespace] });
      qc.invalidateQueries({ queryKey: ['knowledge-structure'] });
    },
    onError: (e: Error) => void showError(e),
  });
  return (
    <button type="button" onClick={() => run.mutate(false)} disabled={run.isPending}
      title="저장된 원문을 지금 코드로 다시 나눕니다 — 나누는 규칙이 고쳐졌을 때 사용. 결과가 같으면 아무것도 바뀌지 않습니다"
      className="text-[11px] px-2 py-0.5 rounded border border-indigo-300 text-indigo-700 hover:bg-indigo-50 dark:border-indigo-700/60 dark:text-indigo-300 dark:hover:bg-indigo-950/40 disabled:opacity-50">
      {run.isPending ? '다시 나누는 중…' : '원문에서 다시 분할'}
    </button>
  );
}

export function KnowledgeStructurePanel({ knowledgeId, canModify = false }: { knowledgeId: number; canModify?: boolean }) {
  const [showAll, setShowAll] = useState(false);
  const { data, isLoading, error } = useQuery<KnowledgeStructure>({
    queryKey: ['knowledge-structure', knowledgeId],
    queryFn: () => getKnowledgeStructure(knowledgeId),
    staleTime: 30_000,
  });

  if (isLoading) return <p className="text-xs text-slate-500">원문 위치 불러오는 중…</p>;
  if (error) return <p className="text-xs text-rose-600 dark:text-rose-400">원문 위치를 불러오지 못했습니다: {(error as Error).message}</p>;
  if (!data) return null;

  if (data.mode === 'single') {
    return (
      <p data-testid="knowledge-structure" className="text-[11px] text-slate-500">
        원문 위치: 직접 입력한 지식(원문 목차 없음)
      </p>
    );
  }

  const outline = data.outline ?? [];
  // 지금 보는 지식이 목차에 없을 수 있다(승인 대기 등 활성 아님 — 리뷰 2026-10-08) → 맨 앞부터
  const selfIdx = Math.max(0, outline.findIndex((o) => o.is_self));
  const where = data.position ? `${data.total}개 중 ${data.position}번째` : `조각 ${data.total}개`;
  // 긴 원문은 지금 보는 지식 주변만 먼저 보여준다
  const start = showAll || outline.length <= MAX_VISIBLE ? 0 : Math.max(0, Math.min(selfIdx - 4, outline.length - MAX_VISIBLE));
  const visible = showAll ? outline : outline.slice(start, start + MAX_VISIBLE);
  const attachedN = outline.filter((o) => o.attached).length;

  // 지식 내용이 주인공 — 원문 위치는 접힌 한 줄(경로·몇 번째), 펼치면 목차(사용자 피드백 2026-10-07)
  return (
    <details data-testid="knowledge-structure" className="group rounded-lg border border-slate-700 bg-slate-900/40">
      <summary className="cursor-pointer list-none [&::-webkit-details-marker]:hidden flex items-center gap-1.5 px-3 py-2 text-xs text-slate-400 hover:text-slate-200 min-w-0"
        title="같은 원문의 다른 조각과 이 지식이 검색될 때 함께 전달되는 조각을 봅니다">
        <ChevronRight className="w-3.5 h-3.5 flex-shrink-0 group-open:rotate-90 transition-transform" />
        <span className="flex-shrink-0 text-slate-500">원문 위치</span>
        <span className="truncate text-slate-300">{data.heading_path.length > 0 ? data.heading_path.join(' › ') : (data.source_file || '원문')}</span>
        <span className="flex-shrink-0 text-[11px] text-slate-500 tabular-nums">· {where}</span>
        {data.mode === 'section' && attachedN > 0 && (
          <span className="ml-auto flex-shrink-0 text-[10px] px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300"
            title="이 지식이 검색되면 AI에게 함께 전달되는 조각 수">함께 전달 {attachedN}</span>
        )}
      </summary>
      <div className="px-3 pb-3 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          {data.source_file && <p className="text-[11px] text-slate-500 truncate">원문: {data.source_file}</p>}
          {data.source_document
            ? <span className="text-[11px] text-slate-500" title={`원문 원장 #${data.source_document.id} · ${data.source_document.chars.toLocaleString()}자 · 분할 코드 ${data.source_document.parser_version ?? '-'}`}>원문 보관됨</span>
            : <span className="text-[11px] text-slate-500" title="원문 원장 도입(2026-10-08) 전에 등록돼 원문이 저장돼 있지 않습니다 — 다시 등록하면 보관됩니다">원문 보관 안 됨</span>}
          {canModify && data.source_document?.status === 'active' && (
            <span className="ml-auto"><RechunkButton docId={data.source_document.id} namespace={data.namespace} /></span>
          )}
        </div>
        <ol className="space-y-0.5 max-h-72 overflow-y-auto">
          {visible.map((o) => <OutlineRow key={o.id} item={o} />)}
        </ol>
        {outline.length > MAX_VISIBLE && (
          <button type="button" onClick={() => setShowAll((v) => !v)}
            className="mt-1 text-[11px] text-indigo-700 dark:text-indigo-300 hover:underline">
            {showAll ? '주변만 보기' : `전체 목차 보기 (${outline.length}개)`}
          </button>
        )}
      </div>
    </details>
  );
}
