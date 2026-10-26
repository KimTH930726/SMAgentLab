import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { getQuestionDetail, type QuestionAnswer } from '../../api/eval';
import { layerOf } from '../../utils/evalLayers';
import { Modal } from '../ui/Modal';

/**
 * 문항 상세(v2.171, 사용자 10/26 "어떤 질문이었고 어떻게 답했고 왜 그랬고 어떤 후보가 됐고 어떻게 고치나") — 한 문항의 흐름을 위에서 아래로.
 * ⑥ 저장 구조(v2.172) — 정답 데이터의 저장 모양(원문만·파라미터 수·표)과 리포트와 같은 꼬리표(service/eval/report.py storage_for).
 * 질문·답·채점 이유는 서버가 결과 파일에서 읽어 준다(DB 저장 없음, 관리자만). [이전]/[다음]은 연 목록 안에서 이동.
 */

const LABEL_CLS: Record<string, string> = {
  정답: 'text-emerald-600 dark:text-emerald-400',
  부분: 'text-amber-600 dark:text-amber-400',
  오답: 'text-rose-600 dark:text-rose-400',
  거절: 'text-slate-400',
};
const MOVE: Record<string, { label: string; cls: string }> = {
  up: { label: '좋아짐', cls: 'text-emerald-600 dark:text-emerald-400' },
  down: { label: '나빠짐', cls: 'text-rose-600 dark:text-rose-400' },
  same: { label: '같음', cls: 'text-slate-400' },
};
const STATUS_NAME: Record<string, string> = { proposed: '제안', approved: '실험 중', held: '나중에', rejected: '안 하기', adopted: '운영 반영' };

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <section className="grid grid-cols-[1.75rem_1fr] gap-x-2">
      <span className="text-xs font-semibold text-indigo-600 dark:text-indigo-400 pt-0.5">{n}</span>
      <div className="space-y-1 min-w-0">
        <h4 className="text-xs font-medium text-slate-400">{title}</h4>
        {children}
      </div>
    </section>
  );
}

function Answers({ rows, title }: { rows: QuestionAnswer[]; title?: string }) {
  return (
    <div className="space-y-2">
      {title && <p className="text-xs text-slate-500">{title}</p>}
      {rows.map((a) => (
        <div key={a.rep} className="rounded-md border border-slate-700 bg-slate-900/40 p-2 space-y-1">
          <p className="text-xs">
            <span className="text-slate-500">{a.rep + 1}회 · </span>
            <span className={`font-medium ${LABEL_CLS[a.label ?? ''] ?? 'text-slate-400'}`}>{a.label ?? '채점 없음'}</span>
            {a.abstained && <span className="text-slate-500"> · 근거 없음으로 거절</span>}
            {a.clarified && <span className="text-slate-500" title="정책이 많아 되물었고, 측정은 '전부 보기'를 고른 답으로 채점"> · 되물음 ⓘ</span>}
            {a.error && <span className="text-rose-600 dark:text-rose-400"> · 오류 {a.error}</span>}
          </p>
          <p className="text-sm text-slate-200 whitespace-pre-wrap break-words max-h-48 overflow-y-auto">{a.answer || '(빈 답)'}</p>
          {a.reason && <p className="text-xs text-slate-400"><span className="text-slate-500">채점 이유 </span>{a.reason}</p>}
        </div>
      ))}
    </div>
  );
}

export function QuestionDetailModal({ runId, qids, start, onClose }: { runId: number; qids: string[]; start: number; onClose: () => void }) {
  const [i, setI] = useState(start);
  useEffect(() => setI(start), [start]);
  const qid = qids[i];
  const { data: d, isLoading, error } = useQuery({
    queryKey: ['eval-question', runId, qid], queryFn: () => getQuestionDetail(runId, qid), enabled: !!qid, staleTime: 60_000,
  });
  const layer = d?.type ? layerOf(d.type)?.name ?? d.type : '';

  return (
    <Modal isOpen={!!qid} onClose={onClose} title={`문항 ${qid ?? ''}`} maxWidth="max-w-3xl">
      <div className="space-y-4">
        <div className="flex items-center justify-between text-xs text-slate-400">
          <span>
            {layer}{d?.namespace ? ` · ${d.namespace}` : ''}{d?.cause ? ` · 원인: ${d.cause.name}` : ''}
            {d?.moved && <> · 이 판정에서 <span className={MOVE[d.moved].cls}>{MOVE[d.moved].label}</span></>}
          </span>
          {qids.length > 1 && (
            <span className="flex items-center gap-2">
              <button className="p-1 rounded hover:bg-slate-700 disabled:opacity-30" disabled={i === 0} onClick={() => setI(i - 1)} aria-label="이전 문항">
                <ChevronLeft className="w-4 h-4" />
              </button>
              {i + 1} / {qids.length}
              <button className="p-1 rounded hover:bg-slate-700 disabled:opacity-30" disabled={i >= qids.length - 1} onClick={() => setI(i + 1)} aria-label="다음 문항">
                <ChevronRight className="w-4 h-4" />
              </button>
            </span>
          )}
        </div>
        {isLoading && <p className="text-sm text-slate-500">불러오는 중…</p>}
        {error && <p className="text-sm text-rose-600 dark:text-rose-400">{(error as Error).message}</p>}
        {d && (
          <div className="space-y-4" data-testid="question-detail">
            <Step n={1} title="질문">
              <p className="text-sm text-slate-100 whitespace-pre-wrap">{d.question || '(질문 글은 답 측정 파일에만 있어요 — 이 판정은 검색만 쟀습니다)'}</p>
            </Step>
            <Step n={2} title="정답(기대 답 · 정답 정책)">
              <p className="text-sm text-slate-200 whitespace-pre-wrap">
                {d.expected || (d.answers.length ? (d.type === 'abstain' ? '(답 없는 질문 — 거절이 정답)' : '(기대 답 없음 — 정답 정책 원문으로 채점)') : '-')}
              </p>
              {d.gold_policies.length > 0 && <p className="text-xs text-slate-500">{d.gold_policies.join(' · ')}</p>}
            </Step>
            <Step n={3} title="검색 — 정답 정책을 AI에게 넘겼나">
              <p className="text-sm text-slate-200">
                {d.retrieval.gold_in_context
                  ? <span className="text-emerald-600 dark:text-emerald-400">넘김 ✔</span>
                  : <span className="text-rose-600 dark:text-rose-400">못 넘김 ✖</span>}
                {d.retrieval.top1 != null && <span className="text-slate-400"> · {d.retrieval.top1 ? '첫째로' : '첫째는 아님'}</span>}
                {d.retrieval.mapped_term && <span className="text-slate-400"> · 용어 "{d.retrieval.mapped_term}"</span>}
                {d.retrieval.clarify && <span className="text-slate-400"> · 되물음</span>}
              </p>
            </Step>
            <Step n={4} title="답 — AI가 이렇게 답했고 채점은 이렇게">
              {d.answers.length ? <Answers rows={d.answers} /> : <p className="text-sm text-slate-400">이 판정은 검색만 쟀어요 — 답 측정 없음</p>}
              {d.before && <details className="text-xs text-slate-400"><summary className="cursor-pointer">바꾸기 전 답 보기</summary><Answers rows={d.before} /></details>}
            </Step>
            <Step n={5} title="왜 — 처음 실패한 단계 · AI가 묶은 패턴">
              {d.cause ? (
                <p className="text-sm text-slate-200">{d.cause.name} <span className="text-xs text-slate-500">→ 볼 곳: {d.cause.where}</span></p>
              ) : <p className="text-sm text-slate-400">이 판정의 진단에서 틀린 문항이 아님</p>}
              {d.hypotheses.map((h) => (
                <p key={h.name} className="text-xs text-slate-400">AI 묶음: {h.name} ({h.n}문항){h.hint && <span className="text-slate-500"> — {h.hint}</span>}</p>
              ))}
            </Step>
            <Step n={6} title="저장 구조 — 정답 데이터가 어떤 모양으로 저장돼 있나">
              {!d.storage || d.storage.gold.length === 0 ? <p className="text-sm text-slate-400">정답 데이터 없음(답 없는 질문이거나 연결 안 됨)</p> : (
                <div className="space-y-1">
                  {d.storage.gold.map((g, k) => (
                    <p key={k} className="text-sm text-slate-200">
                      <span className="text-xs text-slate-500">{g.kind} </span>{g.name ?? '-'}
                      {g.path && <span className="text-xs text-slate-500"> · {g.path}</span>}
                      <span className="text-xs text-slate-400">
                        {g.missing ? ' · 현행에 없음' : ''}{g.params != null ? ` · 파라미터 ${g.params}개` : ''}{g.table ? ' · 표 있음' : ''}{g.rows != null ? ` · ${g.rows}행` : ''}
                      </span>
                    </p>
                  ))}
                  {d.storage.tags.length > 0 && (
                    <p className="flex flex-wrap gap-1">
                      {d.storage.tags.map((t) => <span key={t} className="rounded px-1.5 py-0.5 text-xs bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300">{t}</span>)}
                    </p>
                  )}
                </div>
              )}
            </Step>
            <Step n={7} title="개선 후보 · 고치는 법 · 측정 결과">
              {d.candidates.length === 0 ? <p className="text-sm text-slate-400">이 문항을 겨냥한 후보 없음</p> : d.candidates.map((c) => (
                <div key={c.id} className="space-y-1">
                  <p className="text-sm text-slate-100">{c.title} <span className="text-xs text-slate-500">· {STATUS_NAME[c.status] ?? c.status} · {c.technique_name}</span></p>
                  <p className="text-xs text-slate-400">바꿀 것: {c.action}</p>
                  {c.measurements.map((m) => (
                    <p key={m.id} className="text-xs">
                      <span className={m.verdict === 'pass' ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400'}>
                        {m.verdict === 'pass' ? '✔ 통과' : '✖ 실패'}
                      </span>
                      <span className="text-slate-400"> 판정 #{m.id} {m.label} — 이 문항 </span>
                      <span className={MOVE[m.this_question].cls}>{MOVE[m.this_question].label}</span>
                    </p>
                  ))}
                </div>
              ))}
            </Step>
          </div>
        )}
      </div>
    </Modal>
  );
}
