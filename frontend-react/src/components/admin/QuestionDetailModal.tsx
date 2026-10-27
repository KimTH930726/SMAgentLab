import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { getQuestionDetail, listTechniques, type QuestionAnswer, type Technique } from '../../api/eval';
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

/** 회차 라벨 → 한 줄 결론(다수결, 동률이면 낮은 쪽 — 채점 vote와 같은 방향) */
function verdictOf(rows: QuestionAnswer[]): string | null {
  const labs = rows.map((a) => a.label).filter((x): x is string => !!x);
  if (!labs.length) return null;
  const rank: Record<string, number> = { 정답: 3, 부분: 2, 거절: 1, 오답: 0 };
  const c = new Map<string, number>();
  labs.forEach((l) => c.set(l, (c.get(l) ?? 0) + 1));
  const top = Math.max(...c.values());
  return [...c.entries()].filter(([, n]) => n === top).map(([l]) => l).sort((a, b) => (rank[a] ?? 0) - (rank[b] ?? 0))[0];
}

const VERDICT_BOX: Record<string, string> = {
  정답: 'bg-emerald-50 text-emerald-800 border-emerald-200 dark:bg-emerald-500/10 dark:text-emerald-300 dark:border-emerald-500/30',
  부분: 'bg-amber-50 text-amber-800 border-amber-200 dark:bg-amber-500/10 dark:text-amber-300 dark:border-amber-500/30',
  오답: 'bg-rose-50 text-rose-800 border-rose-200 dark:bg-rose-500/10 dark:text-rose-300 dark:border-rose-500/30',
  거절: 'bg-slate-800 text-slate-300 border-slate-600',
};

function Section({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-slate-700 bg-slate-900/30 p-3 space-y-2 min-w-0">
      <h4 className="text-xs font-semibold text-slate-300"><span className="text-indigo-600 dark:text-indigo-400 mr-1.5">{CIRCLED[n - 1]}</span>{title}</h4>
      {children}
    </section>
  );
}

const CIRCLED = ['①', '②', '③', '④', '⑤', '⑥', '⑦'];

/** 저장 꼬리표 → 왜 문제인가(한 줄) + 해 볼 기법(답 기법 대장 키). 리포트 storage_tags가 붙이는 글자와 같아야 한다(service/eval/report.py) */
export const TAG_INFO: { match: (t: string) => boolean; why: string; techs: string[] }[] = [
  { match: (t) => t.startsWith('원문만'), why: '값(금액·기간·개수)이 원문 문장 속에만 있어 검색·답이 놓치기 쉬움', techs: ['param_derive'] },
  { match: (t) => t.startsWith('표·구간'), why: '조건별 값이 표·구간으로 적혀 AI가 줄을 잘못 읽기 쉬움', techs: ['ref_table_chat', 'condition_hint'] },
  { match: (t) => t.startsWith('질문 표현'), why: '질문에 쓴 말이 문서에 없어 검색이 못 찾음', techs: ['glossary_synonyms', 'keyword_rescue'] },
  { match: (t) => t.includes('흩어짐'), why: '정답이 정책 여러 개에 나뉘어 있어 다 넘겨야 답이 됨', techs: ['policy_top_k', 'category_list'] },
  { match: (t) => t.startsWith('정답 정책이 문맥'), why: '검색이 정답 정책을 AI에게 주는 근거 10개 안에 못 넣음', techs: ['keyword_rescue', 'policy_top_k'] },
  { match: (t) => t.startsWith('용어 매핑'), why: '질문 용어가 용어집에 없어 검색어가 보강되지 않음', techs: ['glossary_match', 'glossary_synonyms'] },
  { match: (t) => t.includes('채팅에 안 쓰임'), why: '정답이 참고표·개요에만 있는데 채팅이 그 데이터를 안 봄', techs: ['ref_table_chat'] },
  { match: (t) => t.includes('현행에 없음'), why: '정답 정책이 폐기·반려돼 지금은 검색 대상이 아님 — 골든셋 정리 대상', techs: [] },
  { match: (t) => t.startsWith('정답 데이터 없음'), why: '답 없는 질문 — 거절이 정답', techs: ['abstain_prefilter', 'chat_prompt'] },
];
const tagInfo = (t: string) => TAG_INFO.find((x) => x.match(t));

const KIND_CHIP: Record<string, string> = { 정책: '정책', 참고표: '참고표', 개요: '개요' };

/** 저장 구조 — 정답 데이터마다 카드(종류·이름·경로 + 저장 사실 칩), 문제 꼬리표마다 왜 문제인가 + 해 볼 기법 */
function StorageView({ storage, techs }: { storage: NonNullable<Awaited<ReturnType<typeof getQuestionDetail>>['storage']>; techs: Technique[] }) {
  const name = (k: string) => techs.find((t) => t.key === k)?.name ?? k;
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
        {storage.gold.map((g, k) => (
          <div key={k} className="rounded-lg border border-slate-700 bg-slate-800 p-3 space-y-1.5 min-w-0">
            <p className="text-sm text-slate-100 min-w-0">
              <span className="mr-1.5 rounded px-1.5 py-0.5 text-xs bg-slate-700 text-slate-300">{KIND_CHIP[g.kind] ?? g.kind}</span>
              {g.name ?? '-'}
            </p>
            {g.path && <p className="text-xs text-slate-500 break-words">{g.path}</p>}
            <p className="flex flex-wrap gap-1.5 text-xs">
              {g.missing && <span className="rounded px-1.5 py-0.5 bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300">현행에 없음</span>}
              {g.body_len != null && <span className="rounded px-1.5 py-0.5 border border-slate-600 text-slate-300">원문 {g.body_len}자</span>}
              {g.params != null && (
                <span className={`rounded px-1.5 py-0.5 border ${g.params ? 'border-slate-600 text-slate-300' : 'border-amber-300 text-amber-700 dark:border-amber-500/40 dark:text-amber-300'}`}>파라미터 {g.params}개</span>
              )}
              {g.table != null && <span className="rounded px-1.5 py-0.5 border border-slate-600 text-slate-300">{g.table ? '표 있음' : '표 없음'}</span>}
              {g.rows != null && <span className="rounded px-1.5 py-0.5 border border-slate-600 text-slate-300">{g.rows}행</span>}
            </p>
          </div>
        ))}
      </div>
      {storage.tags.length > 0 && (
        <ul className="space-y-1.5">
          {storage.tags.map((t) => {
            const info = tagInfo(t);
            return (
              <li key={t} className="rounded-md border-l-4 border-amber-400 dark:border-amber-500 bg-amber-50/60 dark:bg-amber-500/5 px-3 py-2 space-y-0.5">
                <p className="text-sm text-amber-800 dark:text-amber-300 font-medium">{t}</p>
                {info && <p className="text-xs text-slate-300">{info.why}</p>}
                {info && info.techs.length > 0 && (
                  <p className="text-xs text-slate-400">해 볼 기법: {info.techs.map((k) => <span key={k} className="ml-1 rounded px-1.5 py-0.5 bg-slate-800 border border-slate-600 text-slate-200">{name(k)}</span>)}</p>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

const CAND_TONE: Record<string, string> = {
  proposed: 'bg-slate-700 text-slate-300', approved: 'bg-indigo-100 text-indigo-700 dark:bg-indigo-500/20 dark:text-indigo-300',
  held: 'bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300', rejected: 'bg-slate-800 text-slate-500',
  adopted: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300',
};

/**
 * 문항 상세(v2.171 → 10/27 가시성 개편, 사용자 "모달도 항목별로 명확하게") — 맨 위 결론 한 줄(정답·오답 색 · 원인 · 저장 꼬리표),
 * 정답과 AI 답은 나란히, 나머지는 칸마다 상자로. listTitle = 무엇을 보는 목록인가("사실 층 — 정답을 못 넘긴 문항").
 * 질문·답·채점 이유는 서버가 결과 파일에서 읽어 준다(DB 저장 없음, 관리자만). [이전]/[다음]은 연 목록 안에서 이동.
 */
export function QuestionDetailModal({ runId, qids, start, onClose, listTitle }: {
  runId: number; qids: string[]; start: number; onClose: () => void; listTitle?: string;
}) {
  const [i, setI] = useState(start);
  useEffect(() => setI(start), [start]);
  const qid = qids[i];
  const { data: d, isLoading, error } = useQuery({
    queryKey: ['eval-question', runId, qid], queryFn: () => getQuestionDetail(runId, qid), enabled: !!qid, staleTime: 60_000,
  });
  const layer = d?.type ? layerOf(d.type)?.name ?? d.type : '';
  const verdict = d ? verdictOf(d.answers) : null;
  const { data: techs = [] } = useQuery({ queryKey: ['eval-techniques'], queryFn: listTechniques, staleTime: 60_000 });

  return (
    <Modal isOpen={!!qid} onClose={onClose} title={`문항 ${qid ?? ''}`} maxWidth="max-w-4xl">
      <div className="space-y-3">
        <div className="flex items-center justify-between gap-3 text-xs text-slate-400">
          <span className="min-w-0">
            {listTitle && <span className="text-slate-300">{listTitle} · </span>}
            {layer}{d?.namespace ? ` · ${d.namespace}` : ''}
            {d?.moved && <> · 이 판정에서 <span className={MOVE[d.moved].cls}>{MOVE[d.moved].label}</span></>}
          </span>
          {qids.length > 1 && (
            <span className="flex items-center gap-2 shrink-0">
              <button className="p-1 rounded hover:bg-slate-700 disabled:opacity-30" disabled={i === 0} onClick={() => setI(i - 1)} aria-label="이전 문항">
                <ChevronLeft className="w-4 h-4" />
              </button>
              <span className="tabular-nums">{i + 1} / {qids.length}</span>
              <button className="p-1 rounded hover:bg-slate-700 disabled:opacity-30" disabled={i >= qids.length - 1} onClick={() => setI(i + 1)} aria-label="다음 문항">
                <ChevronRight className="w-4 h-4" />
              </button>
            </span>
          )}
        </div>
        {isLoading && <p className="text-sm text-slate-500">불러오는 중…</p>}
        {error && <p className="text-sm text-rose-600 dark:text-rose-400">{(error as Error).message}</p>}
        {d && (
          <div className="space-y-3" data-testid="question-detail">
            {/* 결론 한 줄 — 무엇이 틀렸고 왜·어떻게 저장돼 있나를 맨 위에 */}
            <div className={`rounded-lg border px-3 py-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm ${VERDICT_BOX[verdict ?? ''] ?? 'border-slate-700 text-slate-300'}`}
              data-testid="question-verdict">
              <b>{verdict ? (verdict === '정답' ? '✔ 정답' : verdict === '오답' ? '✖ 오답' : verdict) : '답 측정 없음'}</b>
              {d.answers.length > 1 && <span className="text-xs opacity-80">{d.answers.map((a) => a.label ?? '-').join(' · ')}</span>}
              {d.cause && <span>원인: {d.cause.name}</span>}
              {d.storage?.tags.map((t) => <span key={t} className="rounded px-1.5 py-0.5 text-xs bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300">{t}</span>)}
            </div>

            <Section n={1} title="질문">
              <p className="text-sm text-slate-100 whitespace-pre-wrap">{d.question || '(질문 글은 답 측정 파일에만 있어요 — 이 판정은 검색만 쟀습니다)'}</p>
            </Section>

            <Section n={2} title="정답 / AI 답">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div className="space-y-1 min-w-0">
                  <p className="text-xs text-slate-500">정답(기대 답 · 정답 정책)</p>
                  <p className="text-sm text-slate-200 whitespace-pre-wrap">
                    {d.expected || (d.answers.length ? (d.type === 'abstain' ? '(답 없는 질문 — 거절이 정답)' : '(기대 답 없음 — 정답 정책 원문으로 채점)') : '-')}
                  </p>
                  {d.gold_policies.length > 0 && <p className="text-xs text-slate-500">{d.gold_policies.join(' · ')}</p>}
                </div>
                <div className="min-w-0">
                  {d.answers.length ? <Answers rows={d.answers} /> : <p className="text-sm text-slate-400">이 판정은 검색만 쟀어요 — 답 측정 없음</p>}
                  {d.before && <details className="text-xs text-slate-400 mt-1"><summary className="cursor-pointer">바꾸기 전 답 보기</summary><Answers rows={d.before} /></details>}
                </div>
              </div>
            </Section>

            <Section n={3} title="검색 — 정답 정책을 AI에게 넘겼나">
              <p className="text-sm text-slate-200">
                {d.retrieval.gold_in_context
                  ? <span className="text-emerald-600 dark:text-emerald-400">✔ 넘김</span>
                  : <span className="text-rose-600 dark:text-rose-400">✖ 못 넘김</span>}
                {d.retrieval.top1 != null && <span className="text-slate-400"> · {d.retrieval.top1 ? '1순위' : '1순위는 아님'}</span>}
                {d.retrieval.mapped_term && <span className="text-slate-400"> · 용어 "{d.retrieval.mapped_term}"</span>}
                {d.retrieval.clarify && <span className="text-slate-400"> · 되물음</span>}
              </p>
            </Section>

            <Section n={4} title="왜 — 처음 실패한 단계 · AI가 묶은 패턴">
              {d.cause ? (
                <p className="text-sm text-slate-200">{d.cause.name} <span className="text-xs text-slate-500">→ 볼 곳: {d.cause.where}</span></p>
              ) : <p className="text-sm text-slate-400">이 판정의 진단에서 틀린 문항이 아님</p>}
              {d.hypotheses.map((h) => (
                <p key={h.name} className="text-xs text-slate-400">AI 묶음: {h.name} ({h.n}문항){h.hint && <span className="text-slate-500"> — {h.hint}</span>}</p>
              ))}
            </Section>

            <Section n={5} title="저장 구조 — 정답 데이터가 어떤 모양으로 저장돼 있나">
              {!d.storage || d.storage.gold.length === 0
                ? <p className="text-sm text-slate-400">정답 데이터 없음(답 없는 질문이거나 연결 안 됨)</p>
                : <StorageView storage={d.storage} techs={techs} />}
            </Section>

            <Section n={6} title="고칠 방법 · 측정 결과">
              {d.candidates.length === 0 ? (
                <p className="text-sm text-slate-400">이 문항을 겨냥한 후보 없음{d.storage?.tags.length ? ' — 위 저장 구조의 "해 볼 기법"을 보세요' : ''}</p>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                  {d.candidates.map((c) => {
                    const last = c.measurements[c.measurements.length - 1];
                    return (
                      <div key={c.id} className="rounded-lg border border-slate-700 bg-slate-800 p-3 space-y-1.5 min-w-0">
                        <p className="flex items-center gap-2 text-sm text-slate-100 min-w-0">
                          <span className={`shrink-0 rounded px-1.5 py-0.5 text-xs ${CAND_TONE[c.status] ?? CAND_TONE.proposed}`}>{STATUS_NAME[c.status] ?? c.status}</span>
                          <span className="truncate" title={c.title}>{c.title}</span>
                        </p>
                        <p className="text-xs text-slate-400"><span className="text-slate-500">기법 </span>{c.technique_name}</p>
                        <p className="text-xs text-slate-300"><span className="text-slate-500">바꿀 것 </span>{c.action}</p>
                        {last ? (
                          <p className="text-xs">
                            <span className={last.verdict === 'pass' ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400'}>{last.verdict === 'pass' ? '✔ 통과' : '✖ 실패'}</span>
                            <span className="text-slate-400"> 측정 #{last.id} — 이 문항 </span>
                            <span className={MOVE[last.this_question].cls}>{MOVE[last.this_question].label}</span>
                            {c.measurements.length > 1 && <span className="text-slate-500"> (측정 {c.measurements.length}번)</span>}
                          </p>
                        ) : <p className="text-xs text-slate-500">아직 안 재 봄</p>}
                      </div>
                    );
                  })}
                </div>
              )}
            </Section>
          </div>
        )}
      </div>
    </Modal>
  );
}
