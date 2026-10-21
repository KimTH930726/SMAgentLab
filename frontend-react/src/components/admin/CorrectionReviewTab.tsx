import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { CheckCircle, Sparkles, RefreshCw } from 'lucide-react';
import {
  listCorrections, approveCorrection, rejectCorrection, retargetCorrection, analyzeCorrection, redraftCorrection, type CorrectionItem,
} from '../../api/corrections';
import { Button } from '../ui/Button';
import { Badge } from '../ui/Badge';
import { TabGuide } from './TabGuide';
import { CorrectionAnalysis } from '../chat/CorrectionAnalysis';
import { FormModal } from '../ui/FormModal';

/**
 * 정정 검토(2026-10-01, 10-02 리뷰 신호를 합침) — 지식·정책을 고치자는 신호가 모두 모이는 곳.
 *  - 채팅 "답변 틀림" + 한 줄 → AI가 틀린 근거를 골라 수정안까지 만들어 둠
 *  - 채팅 "답변 틀림"만(의견 없음) → 근거 후보만 있음, 담당자가 대상을 고르면 그 기준으로 수정안 생성
 *  - (근거 카드 "이 근거 틀림"은 2026-10-06 제거 — 대상 지정은 담당자가 여기서)
 *  - 평가 게이트 "이상해요" → 검색 노이즈(대상 지식), 수정안 없음
 * 담당자는 고치지 않는다(2026-10-21, 사람은 최종 승인만): [승인](AI 수정안 그대로) / [다시 쓰기](요청 한 줄 → AI가 다시 씀) / [반려].
 * 승인 전엔 검색에 반영되지 않는다.
 */

type TargetType = CorrectionItem['target_type'];

const TYPE_LABEL: Record<TargetType, string> = {
  knowledge: '지식 정정',
  policy_param: '정책 값 정정',
  policy_narrative: '정책 서술 정정',
  missing: '빠진 내용',
  answer: '답변 오류',
  auto: '답변 틀림 · 의견 없음',
};

function typeLabel(item: CorrectionItem): string {
  if (item.kind === 'search_noise') return '검색 노이즈';
  if (item.kind === 'answer_signal') return '답변 틀림 · 의견 없음';
  return TYPE_LABEL[item.target_type];
}

// 대상별 수정안 필드 — 백엔드 improvement/draft.py _FIELDS와 같은 순서
const FIELDS: Record<TargetType, { key: string; label: string; rows: number }[]> = {
  knowledge: [{ key: 'content', label: '본문', rows: 8 }],
  missing: [{ key: 'content', label: '등록할 지식', rows: 8 }],
  policy_param: [
    { key: 'name', label: '항목명', rows: 1 },
    { key: 'condition', label: '조건', rows: 1 },
    { key: 'value', label: '값', rows: 1 },
    { key: 'unit', label: '단위', rows: 1 },
    { key: 'raw_body', label: '정책 원문', rows: 6 },
  ],
  policy_narrative: [
    { key: 'chunk_text', label: '서술 단락', rows: 5 },
    { key: 'raw_body', label: '정책 원문', rows: 6 },
  ],
  answer: [],
  auto: [],
};

/** 백엔드 service._key와 같은 대상 식별자 */
function currentKey(item: CorrectionItem): string {
  if (item.target_type === 'knowledge') return `k-${item.target_id}`;
  if (item.target_type === 'policy_param') return `pp-${item.target_sub_id}`;
  if (item.target_type === 'policy_narrative') return `pn-${item.target_sub_id}`;
  return item.target_type;
}

function originalText(item: CorrectionItem): string {
  const o = (item.original ?? {}) as Record<string, unknown>;
  if (item.target_type === 'knowledge') return String(o.content ?? '');
  if (item.target_type === 'missing' || item.target_type === 'answer' || item.target_type === 'auto') {
    return `질문: ${o.question ?? ''}\n\n당시 답변:\n${o.answer ?? ''}`;
  }
  const head = `${o.policy_name ?? ''}${Array.isArray(o.category_path) && o.category_path.length ? ` (${(o.category_path as string[]).join(' > ')})` : ''}`;
  if (item.target_type === 'policy_param') {
    const p = (o.param ?? {}) as Record<string, unknown>;
    const val = [p.name, p.condition, `${p.value ?? ''}${p.unit ?? ''}`].filter(Boolean).join(' · ');
    return `${head}\n[값] ${val}\n\n${o.raw_body ?? ''}`;
  }
  return `${head}\n[단락] ${o.chunk_text ?? ''}\n\n${o.raw_body ?? ''}`;
}

function elapsed(from: string, to: string | null): string {
  const ms = (to ? new Date(to).getTime() : Date.now()) - new Date(from).getTime();
  const h = Math.floor(ms / 3_600_000);
  return h >= 24 ? `${Math.floor(h / 24)}일` : `${h}시간`;
}

function sourceLabel(item: CorrectionItem): string {
  if (item.kind === 'search_noise') return '평가 게이트 이상해요';
  if (item.source === 'chat_answer_auto') return '답변 틀림 + 한 줄(AI 판정)';
  if (item.source === 'chat_answer_wrong' || item.source === 'review_flag_migrated') return '답변 틀림(의견 없음)';
  return '근거 직접 지정';
}

function CorrectionCard({ item, onDone }: { item: CorrectionItem; onDone: () => void }) {
  const fields = FIELDS[item.target_type];
  // 반려 사유 / 다시 쓰기 요청은 모달로 받는다(10/21 — 수정·입력은 모달)
  const [modal, setModal] = useState<'reject' | 'redraft' | null>(null);
  const [text, setText] = useState('');
  const [modalError, setModalError] = useState('');
  const [error, setError] = useState('');
  const openModal = (m: 'reject' | 'redraft') => { setModal(m); setText(''); setModalError(''); };

  const approve = useMutation({
    mutationFn: (shown?: Record<string, string | null>) => approveCorrection(item.id, shown),   // 화면에 보인 안(서버가 저장된 안과 같은지 확인)
    onSuccess: () => { setError(''); onDone(); },
    onError: (e: Error) => setError(e.message || '반영에 실패했습니다.'),
  });
  const reject = useMutation({
    mutationFn: () => rejectCorrection(item.id, text.trim()),
    onSuccess: () => { setModal(null); setError(''); onDone(); },
    onError: (e: Error) => setModalError(e.message || '반려에 실패했습니다.'),
  });
  const redraft = useMutation({
    mutationFn: () => redraftCorrection(item.id, text.trim()),
    onSuccess: () => { setModal(null); setError(''); onDone(); },
    onError: (e: Error) => setModalError(e.message || '다시 쓰기에 실패했습니다.'),
  });
  const analyze = useMutation({
    mutationFn: () => analyzeCorrection(item.id),
    onSuccess: (r) => {
      setError(r.analyzed ? '' : 'AI가 원인을 추정하지 못했어요(근거가 없거나 AI 응답 실패) — 직접 고르세요.');
      onDone();
    },
    onError: (e: Error) => setError(e.message || '추정에 실패했습니다.'),
  });
  const retarget = useMutation({
    mutationFn: (key: string) => retargetCorrection(item.id, key),
    // 서버는 초안을 못 만들어도 200(proposed=null)이라, 의견이 있는데 초안이 비면 AI 실패로 보고 알린다(2026-10-07 — 눌러도 아무 표시가
    // 없어 고장처럼 보였다). 의견 없음·답변 오류 대상은 원래 초안이 없는 경우라 알리지 않는다.
    onSuccess: (r) => {
      setError(r.proposed === null && item.user_input && r.target_type !== 'answer'
        ? 'AI가 초안을 만들지 못했어요(사내 AI 응답 실패일 수 있음) — 잠시 후 다시 눌러 주세요.' : '');
      onDone();
    },
    onError: (e: Error) => setError(e.message || '대상 변경에 실패했습니다.'),
  });
  const pending = item.status === 'pending';
  const isAnswer = item.target_type === 'answer';
  const noTarget = item.target_type === 'auto';
  const canFix = !isAnswer && !noTarget;
  const key = currentKey(item);
  const candidates = item.candidates ?? [];
  const busy = approve.isPending || retarget.isPending || redraft.isPending;
  // 의견 없는 신고의 검토 초안(2026-10-07): AI는 값을 안 바꾸고 의심 구간에 【확인 필요】만 단다 — 표시가 남으면 서버가 승인을 막는다
  const REVIEW_MARK = '【확인 필요';
  const proposedMarked = !!item.proposed && Object.values(item.proposed).some((v) => String(v ?? '').includes(REVIEW_MARK));
  const proposedMarks = item.proposed ? Object.values(item.proposed).reduce((n, v) => n + (String(v ?? '').split(REVIEW_MARK).length - 1), 0) : 0;

  return (
    <div className="rounded-xl border border-slate-700 p-4 space-y-3">
      <div className="flex items-center gap-2 flex-wrap text-xs">
        <Badge color={isAnswer ? 'cyan' : noTarget || item.kind === 'search_noise' ? 'amber' : item.target_type === 'missing' ? 'amber' : 'rose'}>
          {typeLabel(item)}
        </Badge>
        {item.status === 'approved' && <Badge color="emerald">반영됨</Badge>}
        {item.status === 'rejected' && <Badge color="slate">반려</Badge>}
        <span className="text-slate-500">{item.namespace}</span>
        <span className="text-slate-500">· {sourceLabel(item)}</span>
        {item.reporter && <span className="text-slate-500">· 신고 {item.reporter}</span>}
        <span className="text-slate-500" title={new Date(item.created_at).toLocaleString('ko-KR')}>
          · {pending ? `대기 ${elapsed(item.created_at, null)}` : `처리 ${elapsed(item.created_at, item.decided_at)} 소요${item.decided_by ? ` (${item.decided_by})` : ''}`}
        </span>
      </div>

      {item.question_text && canFix && (
        <div className="rounded-lg border border-slate-700 bg-slate-900/40 px-3 py-2 text-xs space-y-1">
          <p><span className="text-slate-500 mr-1">질문</span><span className="text-slate-200">{item.question_text}</span></p>
          {item.answer_text && (
            <p className="text-slate-400 line-clamp-3" title={item.answer_text}>
              <span className="text-slate-500 mr-1">당시 답변</span>{item.answer_text}
            </p>
          )}
        </div>
      )}

      {item.user_input ? (
        <p className="text-sm text-slate-200">
          <span className="text-slate-500 text-xs mr-1">{item.kind === 'search_noise' ? '표시 내용' : '사용자 입력'}</span>“{item.user_input}”
        </p>
      ) : item.kind === 'answer_signal' && (
        <p className="text-xs text-slate-500">
          사용자 입력 없음 — '답변 틀림'만 눌렀어요.{' '}
          {item.ai_verdict?.method === 'llm_no_opinion'
            ? (item.ai_verdict.verdict ? 'AI가 질문·답변·근거를 비교해 원인을 추정했어요(아래).' : `AI가 원인을 특정하지 못했어요 — ${item.ai_verdict.reason ?? ''}`)
            : '아직 AI 추정이 없어요.'}
          {pending && item.message_id != null && !item.ai_verdict && (
            <button onClick={() => analyze.mutate()} disabled={analyze.isPending}
              className="ml-2 inline-flex items-center gap-1 text-indigo-600 dark:text-indigo-400 hover:underline disabled:opacity-50"
              title="질문·답변·근거를 비교해 틀렸을 가능성이 높은 근거를 골라 둡니다(이관된 옛 신고처럼 분석을 못 받은 건)">
              <Sparkles className="w-3 h-3" />{analyze.isPending ? 'AI가 분석 중…' : 'AI로 원인 추정'}
            </button>
          )}
        </p>
      )}

      {item.ai_verdict?.verdict && (
        <div className="rounded-lg border border-indigo-200 bg-indigo-50/50 dark:border-indigo-800/50 dark:bg-indigo-950/20 p-3">
          <CorrectionAnalysis verdict={item.ai_verdict} audience="admin" />
          {item.ai_verdict.retargeted && (
            <p className="text-[11px] text-slate-500 mt-2">담당자가 대상을 바꿈 — 위 분석은 처음 AI 판단입니다.</p>
          )}
        </div>
      )}

      {pending && (candidates.length > 0 || !canFix) && (
        <div className="flex items-center gap-2 flex-wrap text-xs">
          <span className="text-slate-500" title="틀린 근거를 고르면 그 근거 기준으로 AI 수정안을 만듭니다.">
            {noTarget ? '틀린 근거 고르기' : '정정 대상'}
          </span>
          <select
            value={key}
            disabled={retarget.isPending}
            onChange={(e) => retarget.mutate(e.target.value)}
            className={`bg-slate-800 border rounded-lg px-2 py-1 text-xs text-slate-200 focus:outline-none focus:border-indigo-500 ${noTarget ? 'border-amber-400 dark:border-amber-600' : 'border-slate-600'}`}
          >
            {noTarget && <option value="auto" disabled>— 근거 후보 {candidates.length}개 중 선택 —</option>}
            {candidates.map((c) => (
              <option key={c.key} value={c.key}>{c.label} — {c.preview.slice(0, 40)}</option>
            ))}
            <option value="missing">근거에 없는 내용(새 지식)</option>
            <option value="answer">답변 오류(지식은 정상)</option>
          </select>
          {retarget.isPending && <span className="text-indigo-600 dark:text-indigo-400 animate-pulse">AI가 수정안을 만드는 중…</span>}
        </div>
      )}

      <div className={`grid grid-cols-1 gap-3 ${canFix ? 'lg:grid-cols-2' : ''}`}>
        <div className="space-y-1">
          <p className="text-xs text-slate-500">
            {item.target_type === 'missing' || isAnswer || noTarget ? '당시 질문·답변' : '기존(현재 검색에 쓰이는 내용)'}
          </p>
          <pre className="whitespace-pre-wrap text-xs text-slate-300 bg-slate-900/60 border border-slate-700 rounded-lg p-3 max-h-80 overflow-y-auto font-sans">
            {originalText(item)}
          </pre>
        </div>

        {canFix && (
          <div className="space-y-2">
            <p className="text-xs text-slate-500">
              {proposedMarked ? 'AI 검토 초안 — 의견이 없어 값은 그대로, 의심 부분에 【확인 필요】 표시'
                : item.user_input && item.kind !== 'search_noise' ? '사용자 입력으로 정리된 수정안' : item.proposed ? 'AI 수정안(추정 — 사용자 의견 없음)' : '수정안'}
            </p>
            {/* 만드는 동안 "의견이 없어요" 안내가 그대로 남아 멈춘 것처럼 보였다(2026-10-07 리허설) — 진행 중 표시 */}
            {retarget.isPending ? (
              <p className="text-xs text-indigo-600 dark:text-indigo-400 border border-dashed border-slate-700 rounded-lg p-3 animate-pulse">
                AI가 {item.user_input ? '수정안' : '검토 초안'}을 만드는 중… 사내 AI라 30초~2분 걸려요. 창을 닫지 마세요.
              </p>
            ) : item.proposed ? (
              <pre className="whitespace-pre-wrap text-xs text-slate-200 bg-slate-900/60 border border-indigo-200 dark:border-indigo-800/50 rounded-lg p-3 max-h-80 overflow-y-auto font-sans">
                {fields.map((f) => (item.proposed?.[f.key] ? `${fields.length > 1 ? `[${f.label}] ` : ''}${item.proposed[f.key]}` : null)).filter(Boolean).join('\n\n')}
              </pre>
            ) : (
              <p className="text-xs text-amber-600 dark:text-amber-400 border border-dashed border-slate-700 rounded-lg p-3">
                {item.kind === 'search_noise'
                  ? '검색 노이즈 표시라 AI 수정안이 없습니다 — [다시 쓰기]로 무엇이 이상한지 알려 주면 AI가 고친 안을 만들어요. 문제 없으면 종료하세요.'
                  : !item.user_input
                    ? '신고 의견이 없어요 — [AI 검토 초안]으로 의심 부분 표시를 받은 뒤 [다시 쓰기]로 맞는 내용을 알려 주세요. 문제 없으면 종료하세요.'
                    : 'AI 수정안이 없습니다 — [AI 초안 만들기]를 누르세요.'}
              </p>
            )}
          </div>
        )}
      </div>

      {item.status === 'rejected' && item.reject_reason && (
        <p className="text-xs text-slate-400">반려 사유: {item.reject_reason}</p>
      )}
      {error && <p className="text-xs text-rose-600 dark:text-rose-400">{error}</p>}

      {pending && (
        <div className="flex items-center gap-2 justify-end flex-wrap">
          {noTarget && (
            <span className="text-xs text-slate-500 mr-auto">사용자가 의견 없이 '답변 틀림'만 눌렀어요 — 틀린 근거를 고르거나, 문제 없으면 종료하세요.</span>
          )}
          {isAnswer && (
            <span className="text-xs text-slate-500 mr-auto">지식은 정상 — 반영할 내용이 없어요. 확인했으면 종료하세요.</span>
          )}
          <Button variant="ghost" size="sm" onClick={() => openModal('reject')} disabled={busy}>
            {canFix && item.kind !== 'search_noise' ? '반려…' : '종료…'}
          </Button>
          {/* 의견이 없으면 "검토 초안"(값은 그대로, 의심 부분 표시) — 예전엔 초안을 안 만들어 눌러도 아무 일이 없었다(2026-10-07, 99번) */}
          {canFix && !item.proposed && item.kind !== 'search_noise' && (
            <Button variant="ghost" size="sm" loading={retarget.isPending} disabled={busy} onClick={() => retarget.mutate(key)}
              title={item.user_input ? '신고 의견대로 고친 수정안을 AI가 만듭니다'
                : '의견이 없어 맞는 값은 모릅니다 — AI가 질문과 견줘 의심되는 부분에 【확인 필요】를 달아 둡니다(값은 안 바꿈)'}>
              <Sparkles className="w-3.5 h-3.5" />{item.user_input ? 'AI 초안 만들기' : 'AI 검토 초안'}
            </Button>
          )}
          {canFix && (item.proposed || item.kind === 'search_noise') && (
            <Button variant="secondary" size="sm" disabled={busy} onClick={() => openModal('redraft')}
              title="어떻게 고치면 될지 한 줄로 알려 주면 AI가 수정안을 다시 씁니다">
              <RefreshCw className="w-3.5 h-3.5" />다시 쓰기
            </Button>
          )}
          {canFix && item.proposed && proposedMarks > 0 && (
            <span className="text-xs text-amber-600 dark:text-amber-400">【확인 필요】 {proposedMarks}곳 — [다시 쓰기]로 맞는 내용을 알려 주세요</span>
          )}
          {canFix && item.proposed && !proposedMarked && (
            <Button variant="primary" size="sm" loading={approve.isPending} disabled={busy}
              title={item.target_type === 'missing' ? '이 내용으로 새 지식을 등록합니다.' : '새 버전으로 교체되고, 기존 내용은 이력으로 남습니다.'}
              onClick={() => approve.mutate(item.proposed ?? undefined)}>
              승인
            </Button>
          )}

          <FormModal
            isOpen={modal !== null}
            title={modal === 'redraft' ? '다시 쓰기 — AI에게 요청' : canFix && item.kind !== 'search_noise' ? '반려' : '종료'}
            onClose={() => setModal(null)}
            onSubmit={() => (modal === 'redraft' ? redraft.mutate() : reject.mutate())}
            submitLabel={modal === 'redraft' ? '다시 쓰기' : canFix && item.kind !== 'search_noise' ? '반려' : '종료'}
            submitting={redraft.isPending || reject.isPending}
            submitDisabled={!text.trim()}
            danger={modal === 'reject'}
            error={modalError}
          >
            <label htmlFor={`corr-${item.id}-text`} className="block text-xs text-slate-400">
              {modal === 'redraft' ? '어떻게 고치면 될지 한 줄로 적어 주세요. AI가 그대로 반영해 수정안을 다시 써요.'
                : canFix && item.kind !== 'search_noise' ? '반려 사유 (신고자에게 보입니다)' : '종료 사유'}
            </label>
            <textarea id={`corr-${item.id}-text`} rows={3} value={text} onChange={(e) => setText(e.target.value)} autoFocus
              placeholder={modal === 'redraft' ? '예: 기한은 7일이 맞아요 / 3번째 문단은 빼 주세요' : '예: 확인 결과 문제 없음'}
              className="w-full bg-slate-800 border border-slate-600 rounded-lg px-2.5 py-1.5 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 resize-y" />
            {modal === 'redraft' && redraft.isPending && (
              <p className="text-xs text-indigo-600 dark:text-indigo-400 animate-pulse">AI가 수정안을 다시 쓰는 중… 30초~2분 걸려요. 창을 닫지 마세요.</p>
            )}
          </FormModal>
        </div>
      )}
    </div>
  );
}

export function CorrectionReviewTab({ namespace }: { namespace: string }) {
  const qc = useQueryClient();
  const [status, setStatus] = useState<'pending' | 'approved' | 'rejected'>('pending');
  const { data: items = [], isLoading, error } = useQuery({
    queryKey: ['corrections', namespace, status],
    queryFn: () => listCorrections(namespace || null, status),
    staleTime: 10_000,
  });
  const onDone = () => {
    qc.invalidateQueries({ queryKey: ['corrections'] });
    qc.invalidateQueries({ queryKey: ['corrections-pending-count'] });
    qc.invalidateQueries({ queryKey: ['knowledge'] });
  };

  return (
    <div className="space-y-3">
      <TabGuide
        what="지식·정책을 고치자는 신호가 모두 모이는 곳 — 신고 1번 = 1건"
        when="채팅 '답변 틀림'(한 줄 의견은 선택, 틀린 근거는 AI가 찾음) · 평가 게이트 '이상해요'"
        todo="승인 / 다시 쓰기(요청 한 줄 → AI가 다시 씀) / 반려 중 선택. 의견 없는 '답변 틀림'은 틀린 근거를 고르면 AI가 수정안을 만듦"
        detail="사용자 의견은 승인 전까지 검색·답변에 전혀 반영되지 않습니다. 승인하면 기존 내용은 이력으로 남고 새 버전이 쓰이며, 반려 사유는 신고자에게 보여집니다. 처리는 그 파트 담당자와 관리자가 할 수 있습니다."
      />
      <div className="flex gap-1">
        {(['pending', 'approved', 'rejected'] as const).map((s) => (
          <button
            key={s}
            onClick={() => setStatus(s)}
            className={`px-3 py-1 text-xs rounded-lg border ${status === s ? 'border-indigo-500 text-indigo-600 dark:text-indigo-400' : 'border-slate-700 text-slate-400 hover:text-slate-200'}`}
          >
            {s === 'pending' ? '대기' : s === 'approved' ? '반영됨' : '반려'}
          </button>
        ))}
      </div>
      {error && <p className="text-xs text-rose-600 dark:text-rose-400">{(error as Error).message}</p>}
      {isLoading ? (
        <p className="text-xs text-slate-500">불러오는 중…</p>
      ) : items.length === 0 ? (
        <div className="text-center py-12 text-slate-500 text-sm">
          <CheckCircle className="w-8 h-8 mx-auto mb-2 text-slate-600" />
          {status === 'pending' ? '검토할 정정 신고가 없습니다.' : '해당 상태의 신고가 없습니다.'}
        </div>
      ) : (
        items.map((item) => <CorrectionCard key={item.id} item={item} onDone={onDone} />)
      )}
    </div>
  );
}
