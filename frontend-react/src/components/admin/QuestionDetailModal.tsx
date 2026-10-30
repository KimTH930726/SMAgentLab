import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { getLatestImprovement, getQuestionDetail, listTechniques, type GateAxis, type QuestionAnswer, type Technique } from '../../api/eval';
import { useAuthStore } from '../../store/useAuthStore';
import { CandidateModal as CandidateView } from './GateImprove';
import { layerOf } from '../../utils/evalLayers';
import { Modal } from '../ui/Modal';
import { CAUSE_STAGE } from './GateFlow';

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

/** 회차 요약 — "2회 모두 오답" / "1회 정답 · 2회 오답" */
function repsLine(rows: QuestionAnswer[]): string {
  const labs = rows.map((a) => a.label ?? '채점 없음');
  return new Set(labs).size === 1 ? `${rows.length}회 모두` : labs.map((l, i) => `${i + 1}회 ${l}`).join(' · ');
}

/** AI 답 — 대표(1회)만 펼치고 나머지는 한 줄(같은 답이면 "같은 답"), 눌러서 보기. 긴 답은 6줄까지 + [더 보기] */
function MainAnswer({ rows }: { rows: QuestionAnswer[] }) {
  const [more, setMore] = useState(false);
  const [rest, setRest] = useState(false);
  const [a, ...others] = rows;
  // [더 보기]는 실제로 잘렸을 때만 — 글자 수로 짐작하면 한 문단 200~300자 답이 잘린 채 버튼이 없었다(재리뷰 10/30 M1)
  const body = useRef<HTMLParagraphElement>(null);
  const [clamped, setClamped] = useState(false);
  useLayoutEffect(() => {
    const el = body.current;
    if (!el || more) return;
    const check = () => setClamped(el.scrollHeight > el.clientHeight + 1);
    check();
    const ro = new ResizeObserver(check);
    ro.observe(el);
    return () => ro.disconnect();
  }, [a.answer, more]);
  return (
    <div className="space-y-2">
      <div className="rounded-md border border-slate-700 bg-slate-900/40 p-2 space-y-1">
        <p className="text-xs">
          <span className="text-slate-500">AI 답 {rows.length > 1 ? '(1회) ' : ''}· </span>
          <span className={`font-medium ${LABEL_CLS[a.label ?? ''] ?? 'text-slate-400'}`}>{a.label ?? '채점 없음'}</span>
          {a.abstained && <span className="text-slate-500"> · 근거 없음으로 거절</span>}
          {a.clarified && <span className="text-slate-500" title="정책이 많아 되물었고, 측정은 '전부 보기'를 고른 답으로 채점"> · 되물음 ⓘ</span>}
          {a.error && <span className="text-rose-600 dark:text-rose-400"> · 오류 {a.error}</span>}
        </p>
        <p ref={body} className={`text-sm text-slate-200 whitespace-pre-wrap break-words ${more ? '' : 'line-clamp-6'}`} data-testid="main-answer">{a.answer || '(빈 답)'}</p>
        {(clamped || more) && <button className="text-xs text-indigo-600 dark:text-indigo-400 hover:underline" onClick={() => setMore(!more)}>{more ? '접기' : '더 보기'}</button>}
        {a.reason && <p className="text-xs rounded bg-slate-800/80 px-2 py-1 text-slate-300"><span className="text-slate-500">채점 이유 </span>{a.reason}</p>}
      </div>
      {others.length > 0 && (
        <div className="text-xs text-slate-400">
          <button className="hover:text-slate-200" onClick={() => setRest(!rest)} aria-expanded={rest}>
            {rest ? '▾' : '▸'} {others.map((o) => `${o.rep + 1}회 ${o.label ?? '채점 없음'}${o.answer === a.answer ? '(같은 답)' : ''}`).join(' · ')} — 보기
          </button>
          {rest && <div className="mt-1"><Answers rows={others} /></div>}
        </div>
      )}
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

/** 저장 구조(10/27 가독성 목업 ⑤) — 한 줄 요약(종류별 수 · 원문 글자 · 파라미터 · 표) + 꼬리표 배지, 정답 데이터 이름·경로, 꼬리표마다 왜 문제 → 해 볼 기법 */
function StorageView({ storage, techs }: { storage: NonNullable<Awaited<ReturnType<typeof getQuestionDetail>>['storage']>; techs: Technique[] }) {
  const name = (k: string) => techs.find((t) => t.key === k)?.name ?? k;
  const kinds = [...new Set(storage.gold.map((g) => g.kind))].map((k) => `${KIND_CHIP[k] ?? k} ${storage.gold.filter((g) => g.kind === k).length}`);
  const sum = (f: (g: typeof storage.gold[number]) => number | null | undefined) => {
    const v = storage.gold.map(f).filter((x): x is number => x != null);
    return v.length ? v.reduce((a, b) => a + b, 0) : null;
  };
  const body = sum((g) => g.body_len), params = sum((g) => g.params);
  const table = storage.gold.some((g) => g.table) ? '표 있음' : storage.gold.some((g) => g.table === false) ? '표 없음' : null;
  const facts = [...kinds, body != null ? `원문 ${body.toLocaleString()}자` : null, params != null ? `파라미터 ${params}` : null, table].filter(Boolean);
  return (
    <div className="space-y-2">
      <p className="flex flex-wrap items-center gap-2 text-sm text-slate-200" data-testid="storage-line">
        <span>{facts.join(' · ')}</span>
        {storage.gold.some((g) => g.missing) && <span className="rounded px-1.5 py-0.5 text-xs bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300">현행에 없음</span>}
        {storage.tags.map((t) => <span key={t} className="rounded px-1.5 py-0.5 text-xs bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300">⚠ {t}</span>)}
      </p>
      <ul className="space-y-0.5">
        {storage.gold.map((g, k) => (
          <li key={k} className="text-xs min-w-0">
            <span className="text-slate-100">{g.name ?? '-'}</span>
            {g.path && <span className="text-slate-500"> · {g.path}</span>}
            {g.rows != null && <span className="text-slate-500"> · {g.rows}행</span>}
          </li>
        ))}
      </ul>
      {storage.tags.map((t) => {
        const info = tagInfo(t);
        if (!info) return null;
        return (
          <p key={t} className="flex flex-wrap items-center gap-1.5 rounded-md border-l-4 border-amber-400 dark:border-amber-500 bg-amber-50/60 dark:bg-amber-500/5 px-3 py-1.5 text-xs">
            <span className="text-slate-300">{info.why}</span>
            {info.techs.length > 0 && <><span className="text-slate-500">→ 해 볼 기법</span>
              {info.techs.map((k) => <span key={k} className="rounded px-1.5 py-0.5 bg-slate-800 border border-slate-600 text-slate-200">{name(k)}</span>)}</>}
          </p>
        );
      })}
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
  const [candId, setCandId] = useState<number | null>(null);   // 고칠 방법 줄을 누르면 그 후보 카드(10/30 사용자: 제안에서 바로 가게)

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
            {/* 4칸(10/30 사용자 "꼭 필요한 정보만, 가시성"): 결론 한 줄 → ① 질문 → ② 정답 vs AI 답(대표 1회) → ③ 왜 틀렸나(검색·단계·패턴·저장 한 칸) → ④ 고칠 방법.
                원인·저장 꼬리표를 맨 위와 칸 안에 두 번 쓰던 것, 거의 같은 답 2회를 다 펼치던 것을 없앴다 */}
            <div className={`rounded-lg border px-3 py-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm ${VERDICT_BOX[verdict ?? ''] ?? 'border-slate-700 text-slate-300'}`}
              data-testid="question-verdict">
              <b>{verdict ? (verdict === '정답' ? '✔ 정답' : verdict === '오답' ? '✖ 오답' : verdict) : '답 측정 없음'}</b>
              {d.answers.length > 1 && <span className="text-xs opacity-80">{repsLine(d.answers)}</span>}
              {d.cause && <span>· 처음 실패: {CAUSE_STAGE[d.cause.key] ?? d.cause.name}</span>}
            </div>

            <Section n={1} title="질문">
              <p className="text-sm text-slate-100 whitespace-pre-wrap">{d.question || '(질문 글은 답 측정 파일에만 있어요 — 이 판정은 검색만 쟀습니다)'}</p>
            </Section>

            <Section n={2} title="정답 / AI 답">
              <div className="grid grid-cols-1 md:grid-cols-[minmax(0,2fr)_minmax(0,3fr)] gap-3">
                <div className="space-y-1 min-w-0">
                  <p className="text-xs text-slate-500">정답(기대 답 · 정답 정책)</p>
                  <p className="text-sm text-slate-200 whitespace-pre-wrap">
                    {d.expected || (d.answers.length ? (d.type === 'abstain' ? '(답 없는 질문 — 거절이 정답)' : '(기대 답 없음 — 정답 정책 원문으로 채점)') : '-')}
                  </p>
                  {d.gold_policies.length > 0 && <p className="text-xs text-slate-500">{d.gold_policies.join(' · ')}</p>}
                </div>
                <div className="min-w-0">
                  {d.answers.length ? <MainAnswer key={qid} rows={d.answers} /> : <p className="text-sm text-slate-400">이 판정은 검색만 쟀어요 — 답 측정 없음</p>}
                  {d.before && <details className="text-xs text-slate-400 mt-1"><summary className="cursor-pointer">바꾸기 전 답 보기</summary><Answers rows={d.before} /></details>}
                </div>
              </div>
            </Section>

            <Section n={3} title="왜 틀렸나">
              <dl className="grid grid-cols-[3.5rem_minmax(0,1fr)] gap-x-3 gap-y-2 text-sm" data-testid="question-why">
                <dt className="text-xs text-slate-500 pt-0.5">검색</dt>
                <dd className="text-slate-200">
                  {d.retrieval.gold_in_context
                    ? <span className="text-emerald-600 dark:text-emerald-400">✔ 정답 정책을 AI에게 넘김</span>
                    : <span className="text-rose-600 dark:text-rose-400">✖ 정답 정책을 못 넘김</span>}
                  {d.retrieval.top1 != null && <span className="text-slate-400"> · {d.retrieval.top1 ? '1순위' : '1순위는 아님'}</span>}
                  {d.retrieval.mapped_term && <span className="text-slate-400"> · 용어 "{d.retrieval.mapped_term}"</span>}
                  {d.retrieval.clarify && <span className="text-slate-400"> · 되물음</span>}
                </dd>
                {d.cause && <>
                  <dt className="text-xs text-slate-500 pt-0.5">원인</dt>
                  <dd className="text-slate-200" data-testid="why-stage">{d.cause.name} <span className="text-xs text-slate-500" title="볼 곳">· {d.cause.where}</span></dd>
                </>}
                {d.hypotheses.length > 0 && <>
                  <dt className="text-xs text-slate-500 pt-0.5" title="틀린 문항의 채점 이유를 로컬 LLM이 패턴별로 묶은 것">패턴</dt>
                  <dd className="space-y-0.5">{d.hypotheses.map((h) => (
                    <p key={h.name} className="text-slate-200" title={h.hint || undefined}>{h.name} <span className="text-xs text-slate-500">· {h.n}문항</span></p>
                  ))}</dd>
                </>}
                <dt className="text-xs text-slate-500 pt-0.5">저장</dt>
                <dd className="min-w-0">{!d.storage ? <span className="text-slate-400">정답 데이터 없음(답 없는 질문이거나 연결 안 됨)</span>
                  : <StorageView storage={d.storage} techs={techs} />}</dd>
              </dl>
            </Section>

            <Section n={4} title="고칠 방법 — 누르면 후보 카드">
              {d.candidates.length === 0 ? (
                <p className="text-sm text-slate-400">이 문항을 겨냥한 후보 없음{d.storage?.tags.length ? ' — 위 저장 구조의 "해 볼 기법"을 보세요' : ''}</p>
              ) : (
                <div className="rounded-lg border border-slate-700 overflow-x-auto" data-testid="question-candidates">
                  <table className="w-full min-w-[560px] text-xs">
                    <thead className="bg-slate-900/60 text-slate-500">
                      <tr><th className="px-3 py-1.5 text-left font-medium">상태</th><th className="px-3 py-1.5 text-left font-medium">방법</th>
                        <th className="px-3 py-1.5 text-left font-medium">기법</th><th className="px-3 py-1.5 text-left font-medium">최근 측정 · 이 문항</th></tr>
                    </thead>
                    <tbody>
                      {d.candidates.map((c) => {
                        const last = c.measurements[c.measurements.length - 1];
                        return (
                          <tr key={c.id} className="border-t border-slate-700/70 align-top">
                            <td className="px-3 py-2"><span className={`rounded px-1.5 py-0.5 whitespace-nowrap ${CAND_TONE[c.status] ?? CAND_TONE.proposed}`}>{STATUS_NAME[c.status] ?? c.status}</span></td>
                            <td className="px-3 py-2">
                              <button className="text-left text-indigo-600 dark:text-indigo-400 hover:underline" title={`${c.action} — 눌러서 후보 카드(실험·결정)`}
                                onClick={() => setCandId(c.id)} data-testid={`question-cand-${c.id}`}>{c.title} ›</button>
                            </td>
                            <td className="px-3 py-2 text-slate-400">{c.technique_name}</td>
                            <td className="px-3 py-2 whitespace-nowrap">
                              {last ? <>
                                <span className={last.verdict === 'pass' ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400'}>#{last.id} {last.verdict === 'pass' ? '통과' : '실패'}</span>
                                <span className="text-slate-500"> · </span><span className={MOVE[last.this_question].cls}>{MOVE[last.this_question].label}</span>
                                {c.measurements.length > 1 && <span className="text-slate-500"> ({c.measurements.length}번)</span>}
                              </> : <span className="text-slate-500">안 재 봄</span>}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </Section>
          </div>
        )}
      </div>
      {candId != null && <CandidateModal id={candId} onClose={() => setCandId(null)} />}
    </Modal>
  );
}

/** 문항 상세 위에 뜨는 후보 카드 — 두 축의 최근 진단에서 찾는다(평가 게이트 ②·①과 같은 카드). 옛 진단 후보면 안내만 */
function CandidateModal({ id, onClose }: { id: number; onClose: () => void }) {
  const isAdmin = useAuthStore((st) => st.user?.role === 'admin');
  const axes: GateAxis[] = ['policy', 'knowledge'];
  const q = useQuery({
    queryKey: ['eval-improvement', 'by-candidate', id],
    queryFn: async () => {   // 두 축을 함께 — 한쪽이 실패해도 다른 쪽에 있으면 보인다(재리뷰 L4). 둘 다 실패면 오류
      const got = await Promise.allSettled(axes.map((axis) => getLatestImprovement(undefined, axis)));
      for (const [k, r] of got.entries()) {
        const c = r.status === 'fulfilled' ? r.value?.candidates.find((x) => x.id === id) : undefined;
        if (c && r.status === 'fulfilled' && r.value) return { imp: r.value, c, axis: axes[k] };
      }
      if (got.every((r) => r.status === 'rejected')) throw (got[0] as PromiseRejectedResult).reason;
      return null;
    },
  });
  const hit = q.data;
  if (hit) return <CandidateView isOpen onClose={onClose} c={hit.c} improvementId={hit.imp.run.id} isAdmin={isAdmin} axis={hit.axis} zClass="z-[55]" />;
  return (
    <Modal isOpen onClose={onClose} title={`후보 #${id}`} maxWidth="max-w-3xl" zClass="z-[55]">
      {q.isLoading ? <p className="text-sm text-slate-400">불러오는 중…</p>
        : q.error ? <p className="text-sm text-rose-600 dark:text-rose-400">후보를 불러오지 못했어요: {(q.error as Error).message}</p>
          : <p className="text-sm text-slate-400">예전 진단의 후보예요 — 평가 게이트의 최근 진단에서 같은 이름의 후보를 보세요</p>}
    </Modal>
  );
}
