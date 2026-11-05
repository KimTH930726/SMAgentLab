import { useMemo, useState } from 'react';
import { clsx } from 'clsx';
import type { SimilarMatch } from '../../api/knowledge';
import { Button } from '../ui/Button';
import { Modal } from '../ui/Modal';
import { showConfirm } from '../../store/useDialogStore';

/**
 * 등록 단계 "비슷한 지식이 이미 있어요"(11/04 태훈 — 시안 승인). 업계: Jira 만들 때 비슷한 이슈 · Stack Overflow 비슷한 질문.
 * [등록]을 누르면 저장 전에 같은 규칙(서버 _duplicate_matches)으로 확인 → 비슷한 게 있을 때만 이 창. 없으면 그대로 등록(사람 손 0).
 * 고르는 셋: 등록 안 함 · 기존 것에 병합(담당이면 기존 지식 수정) · 새로 등록(다른 내용 — AI 감사 '중복 의심' 번복으로 남음).
 * 11/05 태훈: 줄 하나 지웠는데 100%(뜻 유사도 반올림) → 글이 다르면 100% 안 보이게 + 줄 차이 수 · 차이 줄을 GitHub diff처럼(− 기존에만 / + 쓰신 내용에만)
 * · 병합 방향을 고르게(쓰신 내용으로 교체 / 기존 아래에 이어 붙이기, 저장되는 곳 = 기존 #id).
 */

const norm = (l: string) => l.trim();
const lineSet = (t: string) => new Set(t.split('\n').map(norm).filter(Boolean));

/** 뜻 유사도 — 소수 한 자리 내림, 글이 다르면 100% 안 보임(99.6%가 반올림돼 100%로 보이던 것) */
export function similarityLabel(sim: number, identical: boolean): string {
  const p = Math.floor(sim * 1000) / 10;
  return `${identical ? 100 : Math.min(p, 99.9).toFixed(1)}%`;
}

/** 상대 쪽에 없는 줄만 강조 — mine = 쓰신 내용(+ 초록), theirs = 기존(− 빨강). 같은 줄은 흐리게(공백은 무시) */
function Lines({ text, other, side }: { text: string; other: string; side: 'mine' | 'theirs' }) {
  const theirs = useMemo(() => lineSet(other), [other]);
  return (
    <div className="text-[13px] leading-6 font-mono">
      {text.split('\n').map((l, i) => {
        const diff = !!norm(l) && !theirs.has(norm(l));
        return (
          <p key={i} data-diff={diff ? side : undefined}
            className={clsx('flex gap-2 whitespace-pre-wrap break-words border-l-4 pl-1.5 pr-1',
              !diff ? 'border-transparent text-slate-400'
                : side === 'mine' ? 'border-emerald-500 bg-emerald-100 text-emerald-950 dark:bg-emerald-500/20 dark:text-emerald-100'
                  : 'border-rose-500 bg-rose-100 text-rose-950 dark:bg-rose-500/20 dark:text-rose-100')}>
            <span aria-hidden className={clsx('w-3 shrink-0 select-none font-bold', side === 'mine' ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400')}>
              {diff ? (side === 'mine' ? '+' : '−') : ''}</span>
            <span className="min-w-0 flex-1">{l || ' '}</span>
          </p>
        );
      })}
    </div>
  );
}

export function SimilarKnowledgeStep({ content, matches, n, canMerge, busy, onSkip, onMerge, onNew, onClose, mergeMode = 'replace' }: {
  content: string; matches: SimilarMatch[]; n: number;
  /** 병합 기본 방식 — 등록 = 교체(새 판), 지식 공백 = 이어 붙이기(질문 표현을 보탬 — 교체면 기존 답이 지워진다) */
  mergeMode?: 'replace' | 'append';
  /** 넣을 곳 담당이면 병합(기존 지식 바로 수정) — 아니면 [새로 등록]이 담당 승인 대기로 가고 거기서 합친다 */
  canMerge: boolean; busy: boolean;
  onSkip: (m: SimilarMatch) => void; onMerge: (m: SimilarMatch, merged: string) => void; onNew: (m: SimilarMatch) => void; onClose: () => void;
}) {
  const [pick, setPick] = useState(0);
  const [merging, setMerging] = useState<string | null>(null);
  const [mode, setMode] = useState<'replace' | 'append'>(mergeMode);
  const m = matches[Math.min(pick, matches.length - 1)];   // 다시 받아 후보가 줄어도 범위 안(리뷰 L4)
  const stat = useMemo(() => {
    const a = lineSet(content), b = lineSet(m.content);
    return { added: [...a].filter((l) => !b.has(l)).length, removed: [...b].filter((l) => !a.has(l)).length };
  }, [content, m.content]);
  const identical = norm(content) === norm(m.content);
  const draft = (k: 'replace' | 'append') => k === 'replace' ? content.trim() : `${m.content.trimEnd()}\n\n${content.trim()}`;
  const openMerge = (k: 'replace' | 'append') => { setMode(k); setMerging(draft(k)); };
  // 방식을 바꾸면 초안을 새로 — 손본 게 있으면 묻고(리뷰 M4)
  const switchMode = async (k: 'replace' | 'append') => {
    if (mode === k) return;
    if (merging !== draft(mode) && !(await showConfirm('고친 내용이 사라져요. 병합 방식을 바꿀까요?'))) return;
    openMerge(k);
  };
  return (
    <Modal isOpen onClose={onClose} title="비슷한 지식이 이미 있어요" maxWidth="max-w-5xl">
      <div className="space-y-3" data-testid="similar-step">
        <p className="text-xs text-slate-400" title="등록과 같은 기준(뜻이 비슷하고 글도 비슷함 — 양식 줄은 빼고 비교)으로 찾았어요">
          같은 내용이면 <b className="text-slate-200">[등록 안 함]</b>, 기존 것을 고치려면 <b className="text-slate-200">[#{m.id}에 병합]</b>, 다른 내용이면 <b className="text-slate-200">[새로 등록]</b> ⓘ
        </p>
        {merging === null ? (
          <>
            <div className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-700 bg-slate-900/40 px-3 py-2 text-xs" data-testid="similar-diffstat">
              <span className="font-semibold text-slate-300">차이</span>
              {identical ? <span className="text-slate-300">글이 같아요</span>
                : stat.added === 0 && stat.removed === 0 ? (
                <span className="text-slate-300">줄 내용은 같아요 (띄어쓰기·줄 순서만 다름)</span>
              ) : (
                <>
                  <span className="rounded px-1.5 py-0.5 font-semibold tabular-nums bg-rose-100 text-rose-700 dark:bg-rose-500/20 dark:text-rose-300" title="기존 지식에만 있는 줄 — 쓰신 내용엔 없어요">− 기존에만 {stat.removed}줄</span>
                  <span className="rounded px-1.5 py-0.5 font-semibold tabular-nums bg-emerald-100 text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-300" title="쓰신 내용에만 있는 줄 — 기존 지식엔 없어요">+ 쓰신 내용에만 {stat.added}줄</span>
                </>
              )}
              <span className="ml-auto rounded px-1.5 py-0.5 bg-indigo-100 text-indigo-800 dark:bg-indigo-500/15 dark:text-indigo-300 tabular-nums"
                title="뜻(임베딩) 유사도 — 글이 조금 달라도 뜻이 같으면 높게 나와요. 몇 줄이 다른지는 왼쪽 숫자를 보세요" data-testid="similar-score">
                뜻 유사도 {similarityLabel(m.similarity, identical)}</span>
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              <section className="min-w-0 rounded-lg border-2 border-slate-600 bg-slate-900/40 p-3 space-y-2" data-testid="similar-existing">
                <h4 className="flex items-center gap-2 text-xs font-semibold text-slate-200">
                  <span className="rounded bg-slate-700 px-1.5 py-0.5 text-slate-200">기존 지식 <span className="font-mono">#{m.id}</span></span>
                  <span className="font-normal text-rose-600 dark:text-rose-400">− 쓰신 내용에 없는 줄</span>
                </h4>
                <div className="max-h-80 overflow-y-auto"><Lines text={m.content} other={content} side="theirs" /></div>
              </section>
              <section className="min-w-0 rounded-lg border-2 border-indigo-400 bg-slate-900/40 p-3 space-y-2 dark:border-indigo-500/60" data-testid="similar-mine">
                <h4 className="flex items-center gap-2 text-xs font-semibold text-slate-200">
                  <span className="rounded bg-indigo-600 px-1.5 py-0.5 text-white dark:bg-indigo-500">쓰신 내용 (새로)</span>
                  <span className="font-normal text-emerald-600 dark:text-emerald-400">+ 기존에 없는 줄</span>
                </h4>
                <div className="max-h-80 overflow-y-auto"><Lines text={content} other={m.content} side="mine" /></div>
              </section>
            </div>
          </>
        ) : (
          <section className="space-y-2" data-testid="similar-merge">
            <div className="flex flex-wrap items-center gap-2 text-xs" role="radiogroup" aria-label="병합 방식">
              <span className="font-semibold text-slate-300">병합 방식</span>
              {([['replace', '쓰신 내용으로 교체', `#${m.id} 내용을 쓰신 내용으로 바꿔요 — 빨간(− 기존에만) 줄 ${stat.removed}개가 빠져요`],
                 ['append', '기존 아래에 이어 붙이기', `#${m.id} 내용은 그대로 두고 그 아래에 쓰신 내용을 붙여요 — 겹치는 줄은 직접 지우세요`]] as const).map(([k, label, tip]) => (
                <button key={k} type="button" role="radio" aria-checked={mode === k} title={tip} data-testid={`similar-mode-${k}`}
                  onClick={() => void switchMode(k)}
                  className={clsx('rounded-full border px-2.5 py-0.5', mode === k ? 'border-indigo-600 bg-indigo-600 text-white dark:border-indigo-500 dark:bg-indigo-500' : 'border-slate-600 text-slate-300 hover:bg-slate-800')}>
                  {label}</button>
              ))}
            </div>
            <p className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200" data-testid="similar-merge-target">
              저장되는 곳: <b>기존 지식 #{m.id}</b> (아래 내용으로 바뀌어요) · 새 지식은 안 생겨요
              {mode === 'replace' && stat.removed > 0 && <> · 기존에만 있던 {stat.removed}줄이 빠져요</>}
            </p>
            <textarea aria-label="병합한 내용" rows={14} value={merging} onChange={(e) => setMerging(e.target.value)}
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 resize-y" />
          </section>
        )}
        {merging === null && matches.length > 1 && (
          <div className="flex flex-wrap items-center gap-1.5 text-xs" role="group" aria-label="다른 후보">
            <span className="text-slate-500">다른 후보</span>
            {matches.map((x, i) => (
              <button key={x.id} type="button" aria-pressed={i === pick} onClick={() => setPick(i)}
                className={clsx('rounded-full border px-2.5 py-0.5', i === pick ? 'border-indigo-600 bg-indigo-600 text-white dark:border-indigo-500 dark:bg-indigo-500' : 'border-slate-600 text-slate-300 hover:bg-slate-800')}>
                #{x.id} · {similarityLabel(x.similarity, norm(x.content) === norm(content))}</button>
            ))}
          </div>
        )}
        <div className="flex flex-wrap justify-end gap-2 border-t border-slate-700 pt-3">
          {merging === null ? (
            <>
              <Button variant="ghost" size="sm" disabled={busy} onClick={() => onSkip(m)} data-testid="similar-skip"
                title={`이미 같은 내용이 있어요 — 쓰신 내용은 저장하지 않고 #${m.id}는 그대로(AI 감사에 '중복 맞음'으로 남아요)`}>등록 안 함</Button>
              <Button variant="secondary" size="sm" disabled={busy || !canMerge} onClick={() => openMerge(mergeMode)}
                data-testid="similar-merge-open"
                title={canMerge ? `기존 #${m.id}를 고쳐요 — 다음 화면에서 교체 / 이어 붙이기를 고르고, 저장하면 #${m.id}가 바뀌어요(새 지식은 안 생겨요)` : '넣을 곳 담당이 아니에요 — [새로 등록]하면 담당 승인 대기로 가고, 담당이 거기서 합쳐요'}>
                #{m.id}에 병합 ▶</Button>
              <Button size="sm" disabled={busy} onClick={() => onNew(m)} data-testid="similar-new"
                title={`다른 내용이에요 — #${m.id}는 그대로 두고 따로 등록해요(AI 감사에 '중복 아님'으로 남아 유사도 기준을 고칠 근거가 돼요)`}>새로 등록</Button>
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
