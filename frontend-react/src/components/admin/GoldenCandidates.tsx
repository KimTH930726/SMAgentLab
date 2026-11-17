import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { decideGoldenCandidate, getGoldenCandidates, type GoldenAction, type GoldenCandidate, type GoldenSource } from '../../api/eval';
import { showAlert, showError } from '../../store/useDialogStore';
import { Modal } from '../ui/Modal';

/**
 * 골든셋 후보(11/16 시안 A 승인) — 채팅 신호 중 아직 정답을 안 고른 것. 답변 오류 판정(근거는 맞는데 답이 잘못 읽음) · 지식 없음 거절.
 * 사람이 정답을 골라야만 골든셋에 들어간다(업계 공통 — 검토 대기열 → 데이터셋). 자동 추가 없음.
 * 정답 지식은 "정답 닻"으로 저장(다시 적재해 조각 번호가 바뀌어도 찾음) · 모른다가 정답 = 단정 오답을 재는 문항(범위 밖).
 */

export const GOLDEN_KEY = ['eval-golden-candidates'];

const CHANNEL: Record<GoldenSource, { label: string; hint: string; cls: string }> = {
  answer_error: { label: '답변 오류', hint: '[답변 틀림] 뒤 AI가 "근거는 맞는데 답이 잘못 읽음"으로 본 건 — 정답은 그 답변의 근거 중에서',
    cls: 'bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300' },
  no_knowledge: { label: '지식 없음', hint: '"관련 지식을 찾지 못했습니다"로 끝난 질문 — 답이 있는 문서 번호를 넣거나, 정말 없으면 [모른다가 정답]',
    cls: 'bg-indigo-100 text-indigo-700 dark:bg-indigo-500/20 dark:text-indigo-300' },
};

export function useGoldenCandidates(enabled: boolean) {
  return useQuery({ queryKey: GOLDEN_KEY, queryFn: getGoldenCandidates, enabled, staleTime: 30_000 });
}

function Row({ c }: { c: GoldenCandidate }) {
  const qc = useQueryClient();
  const [docId, setDocId] = useState('');
  const m = useMutation({
    mutationFn: (v: { action: GoldenAction; target_id?: number }) => decideGoldenCandidate({ source: c.source, ref_id: c.ref_id, ...v }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: GOLDEN_KEY });
      if (!r.created) showAlert(`이미 정해진 질문이에요(${r.qid}) — 목록을 새로 읽었어요`);
    },
    onError: (e) => showError(e),
  });
  const ch = CHANNEL[c.source];
  const typed = Number(docId.replace(/^#/, '').trim());
  return (
    <li className="py-3 space-y-1.5" data-testid={`golden-row-${c.source}-${c.ref_id}`}>
      <div className="flex flex-wrap items-baseline gap-2">
        <span className={clsx('rounded px-1.5 py-0.5 text-xs whitespace-nowrap', ch.cls)} title={ch.hint}>{ch.label}</span>
        <p className="text-sm text-slate-100 min-w-0 break-words flex-1">{c.question}</p>
        <span className="text-xs text-slate-500 whitespace-nowrap">{c.namespace} · {c.created_at.slice(5, 10).replace('-', '/')}</span>
      </div>
      {c.ai_reason && <p className="text-xs text-slate-400 break-words">AI: {c.ai_reason}</p>}
      <div className="flex flex-wrap items-center gap-1.5">
        {c.source === 'answer_error' && c.evidence.map((e) => (
          <button key={`${e.target_type}-${e.target_id}`} disabled={m.isPending}
            onClick={() => m.mutate({ action: e.target_type === 'knowledge' ? 'knowledge' : 'policy', target_id: e.target_id })}
            className="rounded-md border border-slate-600 px-2 py-1 text-xs text-slate-200 hover:bg-slate-700 disabled:opacity-50"
            title="이 근거를 정답으로 골든셋에 넣기 — 다음 측정부터 이 질문이 맞는지 채점">
            정답 = {e.label ?? `#${e.target_id}`}
          </button>
        ))}
        {c.source === 'no_knowledge' && <>
          <input value={docId} onChange={(ev) => setDocId(ev.target.value)} placeholder="정답 문서 #번호" inputMode="numeric"
            className="w-32 rounded-md border border-slate-600 bg-slate-900 px-2 py-1 text-xs text-slate-100" aria-label="정답 문서 번호" />
          <button disabled={m.isPending || !Number.isInteger(typed) || typed <= 0} onClick={() => m.mutate({ action: 'knowledge', target_id: typed })}
            className="rounded-md border border-slate-600 px-2 py-1 text-xs text-slate-200 hover:bg-slate-700 disabled:opacity-50"
            title="답이 있는 지식 조각 번호(지식 검색의 #번호) — 그 조각이 정답인 문항으로 넣기">정답으로</button>
          <button disabled={m.isPending} onClick={() => m.mutate({ action: 'abstain' })}
            className="rounded-md border border-slate-600 px-2 py-1 text-xs text-slate-200 hover:bg-slate-700 disabled:opacity-50"
            title="우리 지식에 정말 답이 없는 질문 — 시스템이 모른다고 하면 정답, 지어내면 단정 오답으로 센다">모른다가 정답</button>
        </>}
        <button disabled={m.isPending} onClick={() => m.mutate({ action: 'dismiss' })}
          className="rounded-md px-2 py-1 text-xs text-slate-400 hover:text-slate-200 disabled:opacity-50"
          title="골든셋에 안 넣음(시험 질문·개인정보·중복 등) — 목록에서 사라짐">제외</button>
      </div>
    </li>
  );
}

export function GoldenCandidatesModal({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const q = useGoldenCandidates(isOpen);
  const [ch, setCh] = useState<GoldenSource | null>(null);
  const items = (q.data?.items ?? []).filter((x) => !ch || x.source === ch);
  const counts = q.data?.counts;
  return (
    <Modal isOpen={isOpen} onClose={onClose} title="골든셋 후보" maxWidth="max-w-4xl">
      <div className="space-y-3" data-testid="golden-candidates">
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="채널">
          {([null, 'answer_error', 'no_knowledge'] as (GoldenSource | null)[]).map((k) => (
            <button key={k ?? 'all'} onClick={() => setCh(k)} aria-pressed={ch === k} data-testid={`golden-chip-${k ?? 'all'}`}
              className={clsx('rounded-full px-3 py-1 text-xs', ch === k ? 'bg-indigo-600 text-white' : 'border border-slate-600 text-slate-300 hover:bg-slate-700')}
              title={k ? CHANNEL[k].hint : undefined}>
              {k ? `${CHANNEL[k].label} ${counts?.[k] ?? 0}` : `전체 ${(counts?.answer_error ?? 0) + (counts?.no_knowledge ?? 0)}`}
            </button>
          ))}
        </div>
        {q.isLoading ? <p className="text-sm text-slate-500">불러오는 중…</p>
          : q.error ? <p className="text-sm text-rose-600 dark:text-rose-400">후보를 불러오지 못했습니다: {(q.error as Error).message}</p>
            : items.length === 0 ? <p className="text-sm text-slate-500">정할 후보 없음</p>
              : <ul className="divide-y divide-slate-700">{items.map((c) => <Row key={`${c.source}-${c.ref_id}`} c={c} />)}</ul>}
        <p className="text-xs text-slate-500" title="정답 지식은 조각 번호가 아니라 그 조각에만 있는 줄(정답 닻)로 저장해 다시 적재해도 찾습니다">
          고르면 다음 측정부터 이 질문을 채점해요 ⓘ</p>
      </div>
    </Modal>
  );
}
