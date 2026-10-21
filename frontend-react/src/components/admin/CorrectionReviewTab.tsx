import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { CheckCircle, Sparkles, RefreshCw, Pencil } from 'lucide-react';
import {
  listCorrections, approveCorrection, rejectCorrection, retargetCorrection, analyzeCorrection, redraftCorrection,
  updateCorrectionProposed, type CorrectionItem,
} from '../../api/corrections';
import { Button } from '../ui/Button';
import { Badge } from '../ui/Badge';
import { TabGuide } from './TabGuide';
import { CorrectionAnalysis } from '../chat/CorrectionAnalysis';
import { FormModal } from '../ui/FormModal';
import { showError } from '../../store/useDialogStore';
import { lineDiff, type DiffOp } from '../../utils/lineDiff';

/**
 * 정정 요청(2026-10-01, 10-02 리뷰 신호를 합침) — 지식·정책을 고치자는 신호·제안이 모두 모이는 곳. 승인해야만 반영된다.
 *  - 채팅 "답변 틀림" + 한 줄 → AI가 틀린 근거를 골라 수정안까지 만들어 둠 / 의견 없음 → 담당자가 대상을 고르면 수정안 생성
 *  - 평가 게이트 "이상해요" → 검색 노이즈(대상 지식), 수정안 없음
 *  - 정책서 화면의 [직접 수정]·[AI 수정 제안](10/21) — 정책 내용 변경은 전부 여기서 승인할 때 새 버전이 된다
 * 처리: [승인] / [직접 수정](10/21 되살림 — 고친 안을 저장한 뒤 승인) / [AI 다시 쓰기](요청 한 줄) / [반려]. 대기 건은 골라서 한꺼번에 승인할 수 있다
 * (건마다 같은 검사 — 그 사이 바뀐 안·【확인 필요】는 승인되지 않고 이유를 보인다).
 */

type TargetType = CorrectionItem['target_type'];
type Field = { key: string; label: string; rows: number };

const TYPE_LABEL: Record<TargetType, string> = {
  knowledge: '지식 정정',
  policy_param: '정책 값 정정',
  policy_narrative: '정책 정정',
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
const FIELDS: Record<TargetType, Field[]> = {
  knowledge: [{ key: 'content', label: '본문', rows: 10 }],
  missing: [{ key: 'content', label: '등록할 지식', rows: 10 }],
  policy_param: [
    { key: 'name', label: '항목명', rows: 1 },
    { key: 'condition', label: '조건', rows: 1 },
    { key: 'value', label: '값', rows: 1 },
    { key: 'unit', label: '단위', rows: 1 },
    { key: 'raw_body', label: '정책 원문', rows: 8 },
  ],
  policy_narrative: [
    { key: 'chunk_text', label: '서술 단락', rows: 5 },
    { key: 'raw_body', label: '정책 원문', rows: 12 },
  ],
  answer: [],
  auto: [],
};

const REVIEW_MARK = '【확인 필요';

const orig = (item: CorrectionItem) => (item.original ?? {}) as Record<string, unknown>;

/** v0(원문 통째) 정책은 서술 단락 = 원문이라 원문 칸만 보인다. 비고는 원문 스냅샷에 비고 칸이 있을 때만(10/21 이전 신고엔 없음). */
function shownFields(item: CorrectionItem): Field[] {
  const o = orig(item);
  let f = FIELDS[item.target_type];
  const p = item.proposed ?? {};
  // 수정안의 서술 단락이 원문과 다르면(채팅 정정·AI 다시 쓰기 초안) 숨기지 않는다 — 승인 때 바뀐 쪽이 새 원문이 되므로 보여야 한다(리뷰 M1)
  const proposalSplit = 'chunk_text' in p && String(p.chunk_text ?? '') !== String(p.raw_body ?? '');
  if (item.target_type === 'policy_narrative' && !proposalSplit && String(o.chunk_text ?? '') === String(o.raw_body ?? '')) {
    f = f.filter((x) => x.key !== 'chunk_text');
  }
  if ((item.target_type === 'policy_narrative' || item.target_type === 'policy_param') && 'remark' in o) {
    f = [...f, { key: 'remark', label: '비고', rows: 3 }];
  }
  return f;
}

/** 원문 스냅샷 → 수정안 칸 형식(백엔드 draft._original_fields + 비고) */
function originalValue(item: CorrectionItem, key: string): string {
  const o = orig(item);
  if (item.target_type === 'policy_param' && ['name', 'condition', 'value', 'unit'].includes(key)) {
    return String(((o.param ?? {}) as Record<string, unknown>)[key] ?? '');
  }
  return String(o[key] ?? '');
}

/** 수정안의 칸 — 비고가 없으면 "그대로"(원문 값) */
function proposedValue(item: CorrectionItem, key: string): string {
  const p = item.proposed ?? {};
  if (key in p) return String(p[key] ?? '');
  return key === 'remark' ? originalValue(item, key) : '';
}

function joinFields(fields: Field[], get: (key: string) => string): string {
  return fields.map((f) => (fields.length > 1 ? `[${f.label}]\n${get(f.key)}` : get(f.key))).join('\n\n');
}

function contextText(item: CorrectionItem): string {
  const o = orig(item);
  return `질문: ${o.question ?? ''}\n\n당시 답변:\n${o.answer ?? ''}`;
}

function policyHead(item: CorrectionItem): string {
  const o = orig(item);
  const path = Array.isArray(o.category_path) && o.category_path.length ? ` (${(o.category_path as string[]).join(' > ')})` : '';
  return o.policy_name ? `${o.policy_name}${path}` : '';
}

/** 백엔드 service._key와 같은 대상 식별자 */
function currentKey(item: CorrectionItem): string {
  if (item.target_type === 'knowledge') return `k-${item.target_id}`;
  if (item.target_type === 'policy_param') return `pp-${item.target_sub_id}`;
  if (item.target_type === 'policy_narrative') return `pn-${item.target_sub_id}`;
  return item.target_type;
}

function elapsed(from: string, to: string | null): string {
  const ms = (to ? new Date(to).getTime() : Date.now()) - new Date(from).getTime();
  const h = Math.floor(ms / 3_600_000);
  return h >= 24 ? `${Math.floor(h / 24)}일` : `${h}시간`;
}

type SourceGroup = 'all' | 'chat' | 'policy_direct' | 'policy_ai' | 'json_import' | 'eval_gate';
const SOURCE_FILTERS: { id: SourceGroup; label: string }[] = [
  { id: 'all', label: '전체' },
  { id: 'chat', label: '채팅' },
  { id: 'policy_direct', label: '정책 직접 수정' },
  { id: 'policy_ai', label: '정책 AI 제안' },
  { id: 'json_import', label: 'JSON 가져오기' },
  { id: 'eval_gate', label: '평가 게이트' },
];

function sourceGroup(item: CorrectionItem): Exclude<SourceGroup, 'all'> {
  if (item.source === 'policy_direct' || item.source === 'policy_ai' || item.source === 'json_import') return item.source;
  if (item.kind === 'search_noise' || item.source === 'eval_gate') return 'eval_gate';
  return 'chat';
}

function sourceLabel(item: CorrectionItem): string {
  if (item.kind === 'search_noise') return '평가 게이트 이상해요';
  if (item.source === 'policy_direct') return '정책 직접 수정';
  if (item.source === 'policy_ai') return '정책 AI 제안';
  if (item.source === 'json_import') return 'JSON 가져오기';
  if (item.source === 'chat_answer_auto') return '답변 틀림 + 한 줄(AI 판정)';
  if (item.source === 'chat_answer_wrong' || item.source === 'review_flag_migrated') return '답변 틀림(의견 없음)';
  return '근거 직접 지정';
}

const isMarked = (item: CorrectionItem) => !!item.proposed && Object.values(item.proposed).some((v) => String(v ?? '').includes(REVIEW_MARK));
const canFixItem = (item: CorrectionItem) => item.target_type !== 'answer' && item.target_type !== 'auto';
/** 일괄 승인 대상 — 카드의 [승인]이 보이는 건과 같은 조건 */
const approvable = (item: CorrectionItem) => item.status === 'pending' && canFixItem(item) && !!item.proposed && !isMarked(item);

function DiffPane({ ops, side }: { ops: DiffOp[]; side: 'before' | 'after' }) {
  const shown = ops.filter((o) => o.kind === 'same' || o.kind === (side === 'before' ? 'del' : 'add'));
  return (
    <pre className="whitespace-pre-wrap text-xs bg-slate-900/60 border border-slate-700 rounded-lg p-3 max-h-80 overflow-y-auto font-sans">
      {shown.map((o, i) => (
        <div key={i} className={o.kind === 'same' ? 'text-slate-300'
          : o.kind === 'del' ? 'bg-rose-100 text-rose-800 dark:bg-rose-950/50 dark:text-rose-200 rounded px-1 -mx-1'
            : 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/50 dark:text-emerald-200 rounded px-1 -mx-1'}>
          {o.text || ' '}
        </div>
      ))}
    </pre>
  );
}

function CorrectionCard({ item, onDone, selected, onSelect, focused }: {
  item: CorrectionItem;
  onDone: () => void;
  selected: boolean;
  onSelect: (on: boolean) => void;
  focused: boolean;
}) {
  const fields = shownFields(item);
  // 반려 사유 / 다시 쓰기 요청 / 직접 수정은 모달로 받는다(10/21 — 수정·입력은 모달)
  const [modal, setModal] = useState<'reject' | 'redraft' | 'edit' | null>(null);
  const [text, setText] = useState('');
  const [edit, setEdit] = useState<Record<string, string>>({});
  const [modalError, setModalError] = useState('');
  const [error, setError] = useState('');
  const openModal = (m: 'reject' | 'redraft') => { setModal(m); setText(''); setModalError(''); };
  const openEdit = () => {   // 지금 수정안으로 채운다(없으면 기존 내용)
    setEdit(Object.fromEntries(fields.map((f) => [f.key, item.proposed ? proposedValue(item, f.key) : originalValue(item, f.key)])));
    setModalError('');
    setModal('edit');
  };

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
  const saveEdit = useMutation({
    mutationFn: () => {
      const body: Record<string, string | null> = { ...edit };
      // v0 정책은 서술 단락 칸을 안 보여 준다 — 서버도 맞추지만, 옛 항목이어도 필수 칸이 비지 않게 원문을 같이 보낸다
      if (item.target_type === 'policy_narrative' && !fields.some((f) => f.key === 'chunk_text')) body.chunk_text = edit.raw_body ?? '';
      return updateCorrectionProposed(item.id, body);
    },
    onSuccess: () => { setModal(null); setError(''); onDone(); },
    onError: (e: Error) => setModalError(e.message || '저장에 실패했습니다.'),
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
        ? 'AI가 초안을 만들지 못했어요(사내 AI 응답 실패일 수 있음) — 잠시 후 다시 누르거나 [직접 수정]하세요.' : '');
      onDone();
    },
    onError: (e: Error) => setError(e.message || '대상 변경에 실패했습니다.'),
  });
  const pending = item.status === 'pending';
  const isAnswer = item.target_type === 'answer';
  const noTarget = item.target_type === 'auto';
  const canFix = canFixItem(item);
  const key = currentKey(item);
  const candidates = item.candidates ?? [];
  const busy = approve.isPending || retarget.isPending || redraft.isPending || saveEdit.isPending;
  // 의견 없는 신고의 검토 초안(2026-10-07): AI는 값을 안 바꾸고 의심 구간에 【확인 필요】만 단다 — 표시가 남으면 서버가 승인을 막는다
  const proposedMarked = isMarked(item);
  const proposedMarks = item.proposed ? Object.values(item.proposed).reduce((n, v) => n + (String(v ?? '').split(REVIEW_MARK).length - 1), 0) : 0;
  const edits = item.ai_verdict?.edits ?? [];
  const lastEdit = edits.length ? edits[edits.length - 1] : null;
  const isPolicySource = item.source === 'policy_direct' || item.source === 'policy_ai' || item.source === 'json_import';
  // 기존 vs 수정안 줄 비교 — 고칠 원문이 있는 대상만(빠진 내용·답변 오류는 질문·답변이 왼쪽)
  const hasOriginal = item.target_type === 'knowledge' || item.target_type === 'policy_param' || item.target_type === 'policy_narrative';
  const ops = useMemo(
    () => (hasOriginal && item.proposed ? lineDiff(joinFields(fields, (k) => originalValue(item, k)), joinFields(fields, (k) => proposedValue(item, k))) : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [item.id, item.proposed, item.original, item.target_type],
  );
  const changedLines = ops ? ops.filter((o) => o.kind !== 'same').length : 0;

  const proposalLabel = proposedMarked ? 'AI 검토 초안 — 의견이 없어 값은 그대로, 의심 부분에 【확인 필요】 표시'
    : lastEdit ? `직접 수정한 안${lastEdit.by_name ? ` (${lastEdit.by_name})` : ''}`
      : item.source === 'policy_direct' ? '정책 화면에서 직접 쓴 수정안'
        : item.source === 'policy_ai' ? 'AI 수정안(정책 화면 요청)'
        : item.source === 'json_import' ? 'JSON 가져오기의 원문·비고'
          : item.user_input && item.kind !== 'search_noise' ? '사용자 입력으로 정리된 수정안' : item.proposed ? 'AI 수정안(추정 — 사용자 의견 없음)' : '수정안';

  return (
    <div id={`correction-${item.id}`}
      className={`rounded-xl border p-4 space-y-3 ${focused ? 'border-indigo-500 ring-2 ring-indigo-500/40' : 'border-slate-700'}`}>
      <div className="flex items-center gap-2 flex-wrap text-xs">
        {pending && (
          <input type="checkbox" checked={selected} disabled={!approvable(item)} onChange={(e) => onSelect(e.target.checked)}
            aria-label={`정정 #${item.id} 선택`}
            title={approvable(item) ? '골라서 한꺼번에 승인' : proposedMarked ? '【확인 필요】 표시가 남아 있어 승인할 수 없어요' : '승인할 수정안이 없어요'}
            className="accent-indigo-600" />
        )}
        <span className="text-slate-500">#{item.id}</span>
        <Badge color={isAnswer ? 'cyan' : noTarget || item.kind === 'search_noise' ? 'amber' : item.target_type === 'missing' ? 'amber' : 'rose'}>
          {typeLabel(item)}
        </Badge>
        {item.status === 'approved' && <Badge color="emerald">반영됨</Badge>}
        {item.status === 'rejected' && <Badge color="slate">반려</Badge>}
        <span className="text-slate-500">{item.namespace}</span>
        <span className="text-slate-500">· {sourceLabel(item)}</span>
        {item.reporter && <span className="text-slate-500">· {isPolicySource ? '제안' : '신고'} {item.reporter}</span>}
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
          <span className="text-slate-500 text-xs mr-1">{item.kind === 'search_noise' ? '표시 내용' : item.source === 'policy_ai' ? 'AI에게 한 요청' : item.source === 'json_import' ? '가져온 파일' : '사용자 입력'}</span>“{item.user_input}”
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

      {pending && !isPolicySource && (candidates.length > 0 || !canFix) && (
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

      {hasOriginal && policyHead(item) && <p className="text-xs text-slate-400">{policyHead(item)}</p>}

      <div className={`grid grid-cols-1 gap-3 ${canFix ? 'lg:grid-cols-2' : ''}`}>
        <div className="space-y-1">
          <p className="text-xs text-slate-500">
            {hasOriginal ? '기존(현재 검색에 쓰이는 내용)' : '당시 질문·답변'}
          </p>
          {ops ? <DiffPane ops={ops} side="before" /> : (
            <pre className="whitespace-pre-wrap text-xs text-slate-300 bg-slate-900/60 border border-slate-700 rounded-lg p-3 max-h-80 overflow-y-auto font-sans">
              {hasOriginal ? joinFields(fields, (k) => originalValue(item, k)) : contextText(item)}
            </pre>
          )}
        </div>

        {canFix && (
          <div className="space-y-1">
            <p className="text-xs text-slate-500">
              {proposalLabel}
              {ops && <span className="ml-1" title="왼쪽 빨강 = 빠지는 줄, 오른쪽 초록 = 새 줄">· 바뀐 줄 {changedLines}</span>}
            </p>
            {/* 만드는 동안 "의견이 없어요" 안내가 그대로 남아 멈춘 것처럼 보였다(2026-10-07 리허설) — 진행 중 표시 */}
            {retarget.isPending ? (
              <p className="text-xs text-indigo-600 dark:text-indigo-400 border border-dashed border-slate-700 rounded-lg p-3 animate-pulse">
                AI가 {item.user_input ? '수정안' : '검토 초안'}을 만드는 중… 사내 AI라 30초~2분 걸려요. 창을 닫지 마세요.
              </p>
            ) : ops ? (
              <DiffPane ops={ops} side="after" />
            ) : item.proposed ? (
              <pre className="whitespace-pre-wrap text-xs text-slate-200 bg-slate-900/60 border border-indigo-200 dark:border-indigo-800/50 rounded-lg p-3 max-h-80 overflow-y-auto font-sans">
                {joinFields(fields, (k) => proposedValue(item, k))}
              </pre>
            ) : (
              <p className="text-xs text-amber-600 dark:text-amber-400 border border-dashed border-slate-700 rounded-lg p-3">
                {item.kind === 'search_noise'
                  ? '검색 노이즈 표시라 수정안이 없습니다 — [직접 수정]하거나 [AI 다시 쓰기]로 무엇이 이상한지 알려 주세요. 문제 없으면 종료하세요.'
                  : !item.user_input
                    ? '신고 의견이 없어요 — [AI 검토 초안]으로 의심 부분 표시를 받거나 [직접 수정]하세요. 문제 없으면 종료하세요.'
                    : '수정안이 없습니다 — [AI 초안 만들기]를 누르거나 [직접 수정]하세요.'}
              </p>
            )}
          </div>
        )}
      </div>

      {item.status === 'rejected' && item.reject_reason && (
        <p className="text-xs text-slate-400">반려 사유: {item.reject_reason}</p>
      )}
      {error && <p role="alert" className="text-xs text-rose-600 dark:text-rose-400">{error}</p>}

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
              <RefreshCw className="w-3.5 h-3.5" />AI 다시 쓰기
            </Button>
          )}
          {canFix && (
            <Button variant="secondary" size="sm" disabled={busy} onClick={openEdit}
              title="수정안을 직접 고쳐 저장합니다 — 반영은 [승인]에서">
              <Pencil className="w-3.5 h-3.5" />직접 수정
            </Button>
          )}
          {canFix && item.proposed && proposedMarks > 0 && (
            <span className="text-xs text-amber-600 dark:text-amber-400">【확인 필요】 {proposedMarks}곳 — [직접 수정]하거나 [AI 다시 쓰기]로 맞는 내용을 알려 주세요</span>
          )}
          {canFix && item.proposed && !proposedMarked && (
            <Button variant="primary" size="sm" loading={approve.isPending} disabled={busy}
              title={item.target_type === 'missing' ? '이 내용으로 새 지식을 등록합니다.' : '새 버전으로 교체되고, 기존 내용은 이력으로 남습니다.'}
              onClick={() => approve.mutate(item.proposed ?? undefined)}>
              승인
            </Button>
          )}

          <FormModal
            isOpen={modal === 'reject' || modal === 'redraft'}
            title={modal === 'redraft' ? 'AI 다시 쓰기 — 요청' : canFix && item.kind !== 'search_noise' ? '반려' : '종료'}
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
                : canFix && item.kind !== 'search_noise' ? (isPolicySource ? '반려 사유 (제안한 사람에게 보입니다)' : '반려 사유 (신고자에게 보입니다)') : '종료 사유'}
            </label>
            <textarea id={`corr-${item.id}-text`} rows={3} value={text} onChange={(e) => setText(e.target.value)} autoFocus
              placeholder={modal === 'redraft' ? '예: 기한은 7일이 맞아요 / 3번째 문단은 빼 주세요' : '예: 확인 결과 문제 없음'}
              className="w-full bg-slate-800 border border-slate-600 rounded-lg px-2.5 py-1.5 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 resize-y" />
            {modal === 'redraft' && redraft.isPending && (
              <p className="text-xs text-indigo-600 dark:text-indigo-400 animate-pulse">AI가 수정안을 다시 쓰는 중… 30초~2분 걸려요. 창을 닫지 마세요.</p>
            )}
          </FormModal>

          <FormModal
            isOpen={modal === 'edit'}
            title={`직접 수정 — 정정 #${item.id}`}
            onClose={() => setModal(null)}
            onSubmit={() => saveEdit.mutate()}
            submitLabel="수정안 저장"
            submitting={saveEdit.isPending}
            submitDisabled={fields.every((f) => (edit[f.key] ?? '') === (item.proposed ? proposedValue(item, f.key) : originalValue(item, f.key)))}
            error={modalError}
            maxWidth="max-w-3xl"
          >
            <p className="text-xs text-slate-400" title="저장한 안은 이 카드의 수정안이 되고, [승인]을 눌러야 검색·답변에 반영됩니다.">
              저장하면 수정안이 바뀌어요 — 반영은 [승인]에서.
            </p>
            {fields.map((f) => (
              <div key={f.key} className="space-y-1">
                <label htmlFor={`cedit-${item.id}-${f.key}`} className="block text-xs text-slate-400">{f.label}</label>
                <textarea id={`cedit-${item.id}-${f.key}`} rows={f.rows} value={edit[f.key] ?? ''}
                  onChange={(e) => setEdit((s) => ({ ...s, [f.key]: e.target.value }))}
                  className="w-full bg-slate-800 border border-slate-600 rounded-lg px-2.5 py-1.5 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 resize-y" />
              </div>
            ))}
          </FormModal>
        </div>
      )}
    </div>
  );
}

type BulkResult = { id: number; ok: boolean; message?: string };

export function CorrectionReviewTab({ namespace, focusId = null }: { namespace: string; focusId?: number | null }) {
  const qc = useQueryClient();
  const [status, setStatus] = useState<'pending' | 'approved' | 'rejected'>('pending');
  const [source, setSource] = useState<SourceGroup>('all');
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [results, setResults] = useState<BulkResult[] | null>(null);
  const { data: all = [], isLoading, error } = useQuery({
    queryKey: ['corrections', namespace, status],
    queryFn: () => listCorrections(namespace || null, status),
    staleTime: 10_000,
  });
  const items = useMemo(() => (source === 'all' ? all : all.filter((i) => sourceGroup(i) === source)), [all, source]);
  const onDone = () => {
    qc.invalidateQueries({ queryKey: ['corrections'] });
    qc.invalidateQueries({ queryKey: ['corrections-pending-count'] });
    qc.invalidateQueries({ queryKey: ['knowledge'] });
    qc.invalidateQueries({ queryKey: ['policies'] });
    qc.invalidateQueries({ queryKey: ['policy'] });
  };
  // 목록이 바뀌면(승인·필터) 더는 승인할 수 없는 건은 선택에서 뺀다
  useEffect(() => {
    setSelected((s) => {
      const ok = new Set(items.filter(approvable).map((i) => i.id));
      const next = new Set([...s].filter((id) => ok.has(id)));
      return next.size === s.size ? s : next;
    });
  }, [items]);
  useEffect(() => { setSelected(new Set()); setResults(null); }, [namespace, status]);
  // 정책서 화면에서 "정정 요청에서 보기"로 왔으면 그 카드로
  const scrolledFor = useRef<number | null>(null);   // 한 번만 — 다른 카드를 처리할 때마다 되돌아가지 않게(리뷰 L3)
  useEffect(() => {
    if (focusId == null || isLoading || scrolledFor.current === focusId) return;
    const el = document.getElementById(`correction-${focusId}`);
    if (el) { el.scrollIntoView({ block: 'center' }); scrolledFor.current = focusId; }
  }, [focusId, isLoading, items]);

  const approvableItems = items.filter(approvable);
  const allChecked = approvableItems.length > 0 && approvableItems.every((i) => selected.has(i.id));

  // 일괄 승인 = 건마다 같은 승인 요청을 차례로(새 API 없음) — 화면에 보인 안을 보내 그 사이 바뀐 건은 409로 빠지고 나머지는 승인된다
  const bulk = useMutation({
    mutationFn: async (ids: number[]) => {
      const out: BulkResult[] = [];
      for (const id of ids) {
        const item = items.find((i) => i.id === id);
        if (!item || !approvable(item)) {
          out.push({ id, ok: false, message: '승인할 수 없는 상태예요(수정안 없음·【확인 필요】).' });
          continue;
        }
        try {
          await approveCorrection(id, item.proposed ?? undefined);
          out.push({ id, ok: true });
        } catch (e) {
          out.push({ id, ok: false, message: e instanceof Error ? e.message : String(e) });
        }
      }
      return out;
    },
    onSuccess: (out) => {
      setResults(out);
      setSelected(new Set(out.filter((r) => !r.ok).map((r) => r.id)));
      onDone();
    },
    onError: (e) => { void showError(e, '일괄 승인 중 오류가 났어요'); onDone(); },
  });

  return (
    <div className="space-y-3">
      <TabGuide
        what="지식·정책을 고치자는 신호·제안이 모두 모이는 곳 — 승인해야 반영돼요"
        when="채팅 '답변 틀림' · 평가 게이트 '이상해요' · 정책서 화면의 직접 수정·AI 수정 제안"
        todo="승인 / 직접 수정 / AI 다시 쓰기(요청 한 줄) / 반려. 대기 건은 골라서 한꺼번에 승인할 수 있어요"
        detail="승인 전엔 검색·답변에 전혀 반영되지 않습니다. 승인하면 기존 내용은 이력으로 남고 새 버전이 쓰이며, 반려 사유는 신고자(제안자)에게 보여집니다. 처리는 그 업무 영역 담당 파트와 관리자가 할 수 있습니다."
      />
      <div className="flex items-center gap-3 flex-wrap">
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
        <label className="flex items-center gap-1.5 text-xs text-slate-500">
          출처
          <select value={source} onChange={(e) => setSource(e.target.value as SourceGroup)} aria-label="출처"
            className="bg-slate-800 border border-slate-600 rounded-lg px-2 py-1 text-xs text-slate-200 focus:outline-none focus:border-indigo-500">
            {SOURCE_FILTERS.map((f) => <option key={f.id} value={f.id}>{f.label}</option>)}
          </select>
        </label>
        {status === 'pending' && approvableItems.length > 0 && (
          <div className="flex items-center gap-2 ml-auto">
            <label className="flex items-center gap-1.5 text-xs text-slate-400">
              <input type="checkbox" checked={allChecked} className="accent-indigo-600"
                onChange={(e) => setSelected(e.target.checked ? new Set(approvableItems.map((i) => i.id)) : new Set())} />
              승인 가능한 {approvableItems.length}건 모두
            </label>
            <Button variant="primary" size="sm" disabled={selected.size === 0 || bulk.isPending} loading={bulk.isPending}
              onClick={() => { setResults(null); bulk.mutate([...selected]); }}
              title="고른 건을 하나씩 차례로 승인해요 — 그 사이 바뀐 건·【확인 필요】는 빠지고 이유가 보여요">
              선택한 {selected.size}건 승인
            </Button>
          </div>
        )}
      </div>

      {results && (
        <div role="status" className="rounded-lg border border-slate-700 bg-slate-800/40 px-3 py-2 text-xs space-y-1">
          <p className="text-slate-200">
            {results.filter((r) => r.ok).length}건 승인
            {results.some((r) => !r.ok) && <span className="text-rose-600 dark:text-rose-400"> · {results.filter((r) => !r.ok).length}건 실패(선택 유지)</span>}
          </p>
          {results.filter((r) => !r.ok).map((r) => (
            <p key={r.id} className="text-rose-600 dark:text-rose-400">#{r.id} — {r.message}</p>
          ))}
          <button className="text-slate-500 hover:text-slate-300" onClick={() => setResults(null)}>닫기</button>
        </div>
      )}

      {error && <p role="alert" className="text-xs text-rose-600 dark:text-rose-400">{(error as Error).message}</p>}
      {isLoading ? (
        <p className="text-xs text-slate-500">불러오는 중…</p>
      ) : items.length === 0 ? (
        <div className="text-center py-12 text-slate-500 text-sm">
          <CheckCircle className="w-8 h-8 mx-auto mb-2 text-slate-600" />
          {status === 'pending' ? '검토할 정정 요청이 없습니다.' : '해당 상태의 요청이 없습니다.'}
        </div>
      ) : (
        items.map((item) => (
          <CorrectionCard key={item.id} item={item} onDone={onDone} focused={item.id === focusId}
            selected={selected.has(item.id)}
            onSelect={(on) => setSelected((s) => { const n = new Set(s); if (on) n.add(item.id); else n.delete(item.id); return n; })} />
        ))
      )}
    </div>
  );
}
