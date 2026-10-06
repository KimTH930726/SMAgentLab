import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { CheckCircle, Sparkles, PenLine } from 'lucide-react';
import {
  listCorrections, approveCorrection, rejectCorrection, retargetCorrection, analyzeCorrection, type CorrectionItem,
} from '../../api/corrections';
import { Button } from '../ui/Button';
import { Badge } from '../ui/Badge';
import { TabGuide } from './TabGuide';
import { CorrectionAnalysis } from '../chat/CorrectionAnalysis';

/**
 * 정정 검토(2026-10-01, 10-02 리뷰 신호를 합침) — 지식·정책을 고치자는 신호가 모두 모이는 곳.
 *  - 채팅 "답변 틀림" + 한 줄 → AI가 틀린 근거를 골라 수정안까지 만들어 둠
 *  - 채팅 "답변 틀림"만(의견 없음) → 근거 후보만 있음, 담당자가 대상을 고르면 그 기준으로 수정안 생성
 *  - (근거 카드 "이 근거 틀림"은 2026-10-06 제거 — 대상 지정은 담당자가 여기서)
 *  - 평가 게이트 "이상해요" → 검색 노이즈(대상 지식), 수정안 없음
 * 담당자는 [AI 수정안으로 대체] / [직접 수정](미리 채운 편집창) / 반려 중 고른다. 승인 전엔 검색에 반영되지 않는다.
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

// 백엔드 improvement/draft.py _REQUIRED와 같아야 한다(다르면 버튼은 눌리는데 400, 또는 반대로 못 누름)
const REQUIRED: Record<TargetType, string[]> = {
  knowledge: ['content'],
  missing: ['content'],
  policy_param: ['name', 'raw_body'],
  policy_narrative: ['chunk_text', 'raw_body'],
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

/** [직접 수정] 편집창 초기값 — AI 수정안이 있으면 그것, 없으면 지금 내용(원문)으로 채워 손만 대면 되게 */
function editSeed(item: CorrectionItem): Record<string, string> {
  const fields = FIELDS[item.target_type];
  const p = item.proposed;
  if (p) return Object.fromEntries(fields.map((f) => [f.key, String(p[f.key] ?? '')]));
  const o = (item.original ?? {}) as Record<string, unknown>;
  const param = (o.param ?? {}) as Record<string, unknown>;
  const src: Record<string, unknown> = item.target_type === 'policy_param' ? { ...param, raw_body: o.raw_body } : o;
  return Object.fromEntries(fields.map((f) => [f.key, f.key === 'content' && item.target_type === 'missing' ? '' : String(src[f.key] ?? '')]));
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
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState('');
  const [error, setError] = useState('');
  // 대상 변경·초안 재생성 후엔 같은 신고라도 수정안이 바뀐다 — 편집 상태를 초기화
  const proposedSig = JSON.stringify(item.proposed ?? null);
  useEffect(() => { setEditing(false); setDraft(editSeed(item)); },
    [item.id, item.target_type, item.target_id, item.target_sub_id, proposedSig]); // eslint-disable-line react-hooks/exhaustive-deps

  const approve = useMutation({
    mutationFn: (proposed?: Record<string, string>) => approveCorrection(item.id, proposed),
    onSuccess: () => { setError(''); onDone(); },
    onError: (e: Error) => setError(e.message || '반영에 실패했습니다.'),
  });
  const reject = useMutation({
    mutationFn: () => rejectCorrection(item.id, reason.trim()),
    onSuccess: () => { setError(''); onDone(); },
    onError: (e: Error) => setError(e.message || '반려에 실패했습니다.'),
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
    onSuccess: () => { setError(''); onDone(); },
    onError: (e: Error) => setError(e.message || '대상 변경에 실패했습니다.'),
  });
  const pending = item.status === 'pending';
  const isAnswer = item.target_type === 'answer';
  const noTarget = item.target_type === 'auto';
  const canFix = !isAnswer && !noTarget;
  const draftReady = REQUIRED[item.target_type].every((k) => (draft[k] ?? '').trim());
  const key = currentKey(item);
  const candidates = item.candidates ?? [];
  const busy = approve.isPending || retarget.isPending;

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
            {!editing ? (
              <>
                <p className="text-xs text-slate-500">
                  {item.user_input && item.kind !== 'search_noise' ? '사용자 입력으로 정리된 수정안' : item.proposed ? 'AI 수정안(추정 — 사용자 의견 없음)' : '수정안'}
                </p>
                {item.proposed ? (
                  <pre className="whitespace-pre-wrap text-xs text-slate-200 bg-slate-900/60 border border-indigo-200 dark:border-indigo-800/50 rounded-lg p-3 max-h-80 overflow-y-auto font-sans">
                    {fields.map((f) => (item.proposed?.[f.key] ? `${fields.length > 1 ? `[${f.label}] ` : ''}${item.proposed[f.key]}` : null)).filter(Boolean).join('\n\n')}
                  </pre>
                ) : (
                  <p className="text-xs text-amber-600 dark:text-amber-400 border border-dashed border-slate-700 rounded-lg p-3">
                    {item.kind === 'search_noise'
                      ? '검색 노이즈 표시라 AI 수정안이 없습니다 — 내용을 직접 고치거나, 문제 없으면 종료하세요.'
                      : item.kind === 'answer_signal' && !item.user_input
                        ? '사용자 의견이 없어 맞는 내용을 알 수 없어요 — [직접 수정]으로 지금 내용에서 고치거나, 문제 없으면 종료하세요.'
                        : 'AI 수정안이 없습니다 — 직접 수정하거나 AI 초안을 만드세요.'}
                  </p>
                )}
              </>
            ) : (
              <>
                <p className="text-xs text-slate-500">직접 수정 {item.proposed ? '(AI 수정안에서 시작)' : '(지금 내용에서 시작)'}</p>
                {fields.map((f) => (
                  <div key={f.key}>
                    <label className="block text-[11px] text-slate-500 mb-0.5">{f.label}</label>
                    <textarea
                      rows={f.rows}
                      value={draft[f.key] ?? ''}
                      onChange={(e) => setDraft((d) => ({ ...d, [f.key]: e.target.value }))}
                      className="w-full bg-slate-800 border border-slate-600 rounded-lg px-2.5 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-indigo-500 resize-y"
                    />
                  </div>
                ))}
              </>
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
          {noTarget && !rejecting && (
            <span className="text-xs text-slate-500 mr-auto">사용자가 의견 없이 '답변 틀림'만 눌렀어요 — 틀린 근거를 고르거나, 문제 없으면 종료하세요.</span>
          )}
          {isAnswer && !rejecting && (
            <span className="text-xs text-slate-500 mr-auto">지식은 정상 — 반영할 내용이 없어요. 확인했으면 종료하세요.</span>
          )}
          {rejecting ? (
            <>
              <input
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder={canFix && item.kind !== 'search_noise' ? '반려 사유 (신고자에게 보입니다)' : '종료 사유 (예: 확인 결과 문제 없음)'}
                className="flex-1 min-w-0 bg-slate-800 border border-slate-600 rounded-lg px-2.5 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-indigo-500"
              />
              <Button variant="ghost" size="sm" onClick={() => setRejecting(false)}>취소</Button>
              <Button variant="danger" size="sm" loading={reject.isPending} disabled={!reason.trim()} onClick={() => reject.mutate()}>
                {canFix && item.kind !== 'search_noise' ? '반려' : '종료'}
              </Button>
            </>
          ) : (
            <>
              <Button variant="ghost" size="sm" onClick={() => setRejecting(true)}>
                {canFix && item.kind !== 'search_noise' ? '반려…' : '종료…'}
              </Button>
              {canFix && !editing && !item.proposed && item.kind !== 'search_noise' && !(item.kind === 'answer_signal' && !item.user_input) && (
                <Button variant="ghost" size="sm" loading={retarget.isPending} onClick={() => retarget.mutate(key)}>
                  <Sparkles className="w-3.5 h-3.5" />AI 초안 만들기
                </Button>
              )}
              {canFix && !editing && (
                <Button variant="secondary" size="sm" disabled={busy} onClick={() => { setDraft(editSeed(item)); setEditing(true); }}
                  title="편집창이 AI 수정안(없으면 지금 내용)으로 미리 채워집니다">
                  <PenLine className="w-3.5 h-3.5" />직접 수정
                </Button>
              )}
              {canFix && !editing && item.proposed && (
                <Button variant="primary" size="sm" loading={approve.isPending} disabled={busy}
                  title={item.target_type === 'missing' ? '이 내용으로 새 지식을 등록합니다.' : '새 버전으로 교체되고, 기존 내용은 이력으로 남습니다.'}
                  onClick={() => approve.mutate(undefined)}>
                  AI 수정안으로 대체
                </Button>
              )}
              {canFix && editing && (
                <>
                  <Button variant="ghost" size="sm" onClick={() => setEditing(false)}>편집 취소</Button>
                  <Button variant="primary" size="sm" loading={approve.isPending} disabled={!draftReady || busy}
                    title={item.target_type === 'missing' ? '이 내용으로 새 지식을 등록합니다.' : '새 버전으로 교체되고, 기존 내용은 이력으로 남습니다.'}
                    onClick={() => approve.mutate(draft)}>
                    수정 내용으로 반영
                  </Button>
                </>
              )}
            </>
          )}
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
        todo="AI 수정안으로 대체 / 직접 수정 / 반려 중 선택. 의견 없는 '답변 틀림'은 틀린 근거를 고르면 AI가 수정안을 만듦"
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
