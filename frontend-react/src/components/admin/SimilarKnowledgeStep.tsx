import { useMemo, useState } from 'react';
import { clsx } from 'clsx';
import type { SimilarMatch } from '../../api/knowledge';
import { Button } from '../ui/Button';
import { Modal } from '../ui/Modal';

/**
 * 등록 단계 "비슷한 지식이 이미 있어요"(11/04 태훈 — 시안 승인). 업계: Jira 만들 때 비슷한 이슈 · Stack Overflow 비슷한 질문.
 * [등록]을 누르면 저장 전에 같은 규칙(서버 _duplicate_matches)으로 확인 → 비슷한 게 있을 때만 이 창. 없으면 그대로 등록(사람 손 0).
 * 고르는 셋: 등록 안 함 · 기존 것에 합치기(담당이면 기존 지식 수정) · 새로 등록(다른 내용 — AI 감사 '중복 의심' 번복으로 남음).
 */

/** 줄 단위로 상대 쪽에 없는 줄만 강조 — 달라진 부분을 눈으로 찾게(공백은 무시) */
function Lines({ text, other }: { text: string; other: string }) {
  const theirs = useMemo(() => new Set(other.split('\n').map((l) => l.trim()).filter(Boolean)), [other]);
  return (
    <div className="space-y-0.5 text-[13px] leading-6">
      {text.split('\n').map((l, i) => {
        const diff = !!l.trim() && !theirs.has(l.trim());
        return <p key={i} className={clsx('whitespace-pre-wrap break-words rounded px-1', diff ? 'bg-amber-100 text-amber-950 dark:bg-amber-500/15 dark:text-amber-100' : 'text-slate-300')}>{l || ' '}</p>;
      })}
    </div>
  );
}

export function SimilarKnowledgeStep({ content, matches, n, canMerge, busy, onSkip, onMerge, onNew, onClose }: {
  content: string; matches: SimilarMatch[]; n: number;
  /** 넣을 곳 담당이면 합치기(기존 지식 바로 수정) — 아니면 [새로 등록]이 담당 승인 대기로 가고 거기서 합친다 */
  canMerge: boolean; busy: boolean;
  onSkip: (m: SimilarMatch) => void; onMerge: (m: SimilarMatch, merged: string) => void; onNew: (m: SimilarMatch) => void; onClose: () => void;
}) {
  const [pick, setPick] = useState(0);
  const [merging, setMerging] = useState<string | null>(null);
  const m = matches[Math.min(pick, matches.length - 1)];   // 다시 받아 후보가 줄어도 범위 안(리뷰 L4)
  return (
    <Modal isOpen onClose={onClose} title="비슷한 지식이 이미 있어요" maxWidth="max-w-5xl">
      <div className="space-y-3" data-testid="similar-step">
        <p className="text-xs text-slate-400" title="등록과 같은 기준(뜻이 비슷하고 글도 비슷함 — 양식 줄은 빼고 비교)으로 찾았어요. 노란 줄 = 상대 쪽에 없는 줄">
          같은 내용이면 <b className="text-slate-200">[등록 안 함]</b>, 기존 것을 보완하려면 <b className="text-slate-200">[기존 것에 합치기]</b>, 다른 내용이면 <b className="text-slate-200">[새로 등록]</b> ⓘ
        </p>
        {merging === null ? (
          <div className="grid gap-3 md:grid-cols-2">
            <section className="min-w-0 rounded-lg border border-slate-700 bg-slate-900/40 p-3 space-y-2">
              <h4 className="text-xs font-semibold text-slate-300">쓰신 내용</h4>
              <div className="max-h-80 overflow-y-auto"><Lines text={content} other={m.content} /></div>
            </section>
            <section className="min-w-0 rounded-lg border border-indigo-300 bg-slate-900/40 p-3 space-y-2 dark:border-indigo-500/50" data-testid="similar-existing">
              <h4 className="flex items-center gap-2 text-xs font-semibold text-slate-300">
                기존 지식 <span className="font-mono">#{m.id}</span>
                <span className="rounded px-1.5 py-0.5 text-[11px] bg-indigo-100 text-indigo-800 dark:bg-indigo-500/15 dark:text-indigo-300" title="뜻(임베딩) 유사도">유사도 {(m.similarity * 100).toFixed(0)}%</span>
              </h4>
              <div className="max-h-80 overflow-y-auto"><Lines text={m.content} other={content} /></div>
            </section>
          </div>
        ) : (
          <section className="space-y-2" data-testid="similar-merge">
            <h4 className="text-xs font-semibold text-slate-300">#{m.id}에 합칠 내용 — 기존 내용 아래에 쓰신 내용을 붙였어요. 고쳐서 저장하세요</h4>
            <textarea aria-label="합친 내용" rows={14} value={merging} onChange={(e) => setMerging(e.target.value)}
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 resize-y" />
          </section>
        )}
        {merging === null && matches.length > 1 && (
          <div className="flex flex-wrap items-center gap-1.5 text-xs" role="group" aria-label="다른 후보">
            <span className="text-slate-500">다른 후보</span>
            {matches.map((x, i) => (
              <button key={x.id} type="button" aria-pressed={i === pick} onClick={() => setPick(i)}
                className={clsx('rounded-full border px-2.5 py-0.5', i === pick ? 'border-indigo-600 bg-indigo-600 text-white dark:border-indigo-500 dark:bg-indigo-500' : 'border-slate-600 text-slate-300 hover:bg-slate-800')}>
                #{x.id} · {(x.similarity * 100).toFixed(0)}%</button>
            ))}
          </div>
        )}
        <div className="flex flex-wrap justify-end gap-2 border-t border-slate-700 pt-3">
          {merging === null ? (
            <>
              <Button variant="ghost" size="sm" disabled={busy} onClick={() => onSkip(m)} data-testid="similar-skip"
                title="이미 같은 내용이 있어요 — 저장하지 않아요(AI 감사에 '중복 맞음'으로 남아요)">등록 안 함</Button>
              <Button variant="secondary" size="sm" disabled={busy || !canMerge} onClick={() => setMerging(`${m.content.trimEnd()}\n\n${content.trim()}`)}
                data-testid="similar-merge-open"
                title={canMerge ? `#${m.id}를 보완해요 — 합친 내용을 고쳐 저장하면 기존 지식이 바뀌어요(새 조각은 안 생겨요)` : '넣을 곳 담당이 아니에요 — [새로 등록]하면 담당 승인 대기로 가고, 담당이 거기서 합쳐요'}>
                기존 것에 합치기 ▶</Button>
              <Button size="sm" disabled={busy} onClick={() => onNew(m)} data-testid="similar-new"
                title="다른 내용이에요 — 따로 등록해요(AI 감사에 '중복 아님'으로 남아 유사도 기준을 고칠 근거가 돼요)">새로 등록</Button>
            </>
          ) : (
            <>
              <Button variant="ghost" size="sm" disabled={busy} onClick={() => setMerging(null)}>뒤로</Button>
              <Button size="sm" loading={busy} disabled={busy || !merging.trim()} onClick={() => onMerge(m, merging)} data-testid="similar-merge-save">#{m.id}에 저장</Button>
            </>
          )}
        </div>
      </div>
    </Modal>
  );
}
