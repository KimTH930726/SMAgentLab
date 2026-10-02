import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { CheckCircle, Sparkles } from 'lucide-react';
import {
  listCorrections, approveCorrection, rejectCorrection, retargetCorrection, type CorrectionItem,
} from '../../api/corrections';
import { Button } from '../ui/Button';
import { Badge } from '../ui/Badge';
import { TabGuide } from './TabGuide';
import { CorrectionAnalysis } from '../chat/CorrectionAnalysis';

/**
 * 정정 검토(2026-10-01, 근거 정정 흐름) — 채팅의 "답변 틀림"/"이 근거 틀림" 신고 받은편지함.
 * 사용자 입력은 승인 전엔 검색에 절대 반영되지 않는다(수정안은 개선 원장에만 있음).
 * 승인 = 새 버전으로 교체(원본은 deprecated로 보존), 반려 = 원본 유지 + 사유 기록(신고자에게 표시).
 * "답변 틀림"은 AI가 대상 근거를 고른다 — 틀렸으면 담당자가 대상을 바꾸고(수정안 재생성) 승인한다.
 */

type TargetType = CorrectionItem['target_type'];

const TYPE_LABEL: Record<TargetType, string> = {
  knowledge: '지식 정정',
  policy_param: '정책 값 정정',
  policy_narrative: '정책 서술 정정',
  missing: '빠진 내용',
  answer: '답변 오류',
};

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
};

// 백엔드 improvement/draft.py _REQUIRED와 같아야 한다(다르면 버튼은 눌리는데 400, 또는 반대로 못 누름)
const REQUIRED: Record<TargetType, string[]> = {
  knowledge: ['content'],
  missing: ['content'],
  policy_param: ['name', 'raw_body'],
  policy_narrative: ['chunk_text', 'raw_body'],
  answer: [],
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
  if (item.target_type === 'missing' || item.target_type === 'answer') {
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

function CorrectionCard({ item, onDone }: { item: CorrectionItem; onDone: () => void }) {
  const fields = FIELDS[item.target_type];
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState('');
  const [error, setError] = useState('');
  // 대상 변경·초안 재생성 후엔 같은 신고라도 수정안이 바뀐다 — 대상과 수정안 기준으로 다시 채운다
  const proposedSig = JSON.stringify(item.proposed ?? null);
  useEffect(() => {
    const init: Record<string, string> = {};
    fields.forEach((f) => { init[f.key] = String(item.proposed?.[f.key] ?? ''); });
    setDraft(init);
  }, [item.id, item.target_type, item.target_id, item.target_sub_id, proposedSig]); // eslint-disable-line react-hooks/exhaustive-deps

  const approve = useMutation({
    mutationFn: () => approveCorrection(item.id, draft),
    onSuccess: () => { setError(''); onDone(); },
    onError: (e: Error) => setError(e.message || '승인에 실패했습니다.'),
  });
  const reject = useMutation({
    mutationFn: () => rejectCorrection(item.id, reason.trim()),
    onSuccess: () => { setError(''); onDone(); },
    onError: (e: Error) => setError(e.message || '반려에 실패했습니다.'),
  });
  const retarget = useMutation({
    mutationFn: (key: string) => retargetCorrection(item.id, key),
    onSuccess: () => { setError(''); onDone(); },
    onError: (e: Error) => setError(e.message || '대상 변경에 실패했습니다.'),
  });
  const pending = item.status === 'pending';
  const isAnswer = item.target_type === 'answer';
  const ready = !isAnswer && REQUIRED[item.target_type].every((k) => (draft[k] ?? '').trim());
  const key = currentKey(item);
  const candidates = item.candidates ?? [];

  return (
    <div className="rounded-xl border border-slate-700 p-4 space-y-3">
      <div className="flex items-center gap-2 flex-wrap text-xs">
        <Badge color={isAnswer ? 'cyan' : item.target_type === 'missing' ? 'amber' : 'rose'}>{TYPE_LABEL[item.target_type]}</Badge>
        {item.status === 'approved' && <Badge color="emerald">반영됨</Badge>}
        {item.status === 'rejected' && <Badge color="slate">반려</Badge>}
        <span className="text-slate-500">{item.namespace}</span>
        <span className="text-slate-500">· 신고 {item.reporter ?? '알 수 없음'}</span>
        <span className="text-slate-500" title={item.source === 'chat_answer_auto' ? 'AI가 대상 근거를 고른 신고' : '사용자가 근거 카드에서 직접 고른 신고'}>
          · {item.source === 'chat_answer_auto' ? '답변 틀림(AI 판정)' : '근거 직접 지정'}
        </span>
        <span className="text-slate-500" title={new Date(item.created_at).toLocaleString('ko-KR')}>
          · {pending ? `대기 ${elapsed(item.created_at, null)}` : `처리 ${elapsed(item.created_at, item.decided_at)} 소요${item.decided_by ? ` (${item.decided_by})` : ''}`}
        </span>
      </div>

      <p className="text-sm text-slate-200">
        <span className="text-slate-500 text-xs mr-1">사용자 의견</span>“{item.user_input}”
      </p>

      {item.ai_verdict?.verdict && (
        <div className="rounded-lg border border-indigo-200 bg-indigo-50/50 dark:border-indigo-800/50 dark:bg-indigo-950/20 p-3">
          <CorrectionAnalysis verdict={item.ai_verdict} audience="admin" />
          {item.ai_verdict.retargeted && (
            <p className="text-[11px] text-slate-500 mt-2">담당자가 대상을 바꿈 — 위 분석은 처음 AI 판단입니다.</p>
          )}
        </div>
      )}

      {pending && (
        <div className="flex items-center gap-2 flex-wrap text-xs">
          <span className="text-slate-500" title="AI가 고른 대상이 틀렸으면 바꾸세요. 바꾼 대상 기준으로 수정안을 다시 만듭니다.">정정 대상</span>
          <select
            value={key}
            disabled={retarget.isPending}
            onChange={(e) => retarget.mutate(e.target.value)}
            className="bg-slate-800 border border-slate-600 rounded-lg px-2 py-1 text-xs text-slate-200 focus:outline-none focus:border-indigo-500"
          >
            {candidates.map((c) => (
              <option key={c.key} value={c.key}>{c.label} — {c.preview.slice(0, 40)}</option>
            ))}
            <option value="missing">근거에 없는 내용(새 지식)</option>
            <option value="answer">답변 오류(지식은 정상)</option>
          </select>
          {!isAnswer && (
            <Button variant="ghost" size="sm" loading={retarget.isPending} onClick={() => retarget.mutate(key)}
              title="같은 대상으로 AI 수정안을 다시 만듭니다(초안이 없거나 마음에 안 들 때)">
              <Sparkles className="w-3.5 h-3.5" />초안 다시 만들기
            </Button>
          )}
          {retarget.isPending && <span className="text-indigo-600 dark:text-indigo-400 animate-pulse">AI가 수정안을 만드는 중…</span>}
        </div>
      )}

      <div className={`grid grid-cols-1 gap-3 ${isAnswer ? '' : 'lg:grid-cols-2'}`}>
        <div className="space-y-1">
          <p className="text-xs text-slate-500">
            {item.target_type === 'missing' || isAnswer ? '당시 질문·답변' : '기존(현재 검색에 쓰이는 내용)'}
          </p>
          <pre className="whitespace-pre-wrap text-xs text-slate-300 bg-slate-900/60 border border-slate-700 rounded-lg p-3 max-h-80 overflow-y-auto font-sans">
            {originalText(item)}
          </pre>
        </div>
        {!isAnswer && (
          <div className="space-y-2">
            <p className="text-xs text-slate-500" title="AI가 사용자 의견을 반영해 만든 초안입니다. 승인 전에 고칠 수 있고, 승인하면 이 내용이 새 버전으로 검색에 쓰입니다.">
              수정안 {pending ? '(편집 가능)' : ''}
              {!item.proposed && pending && <span className="text-amber-600 dark:text-amber-400"> — AI 초안 없음, 직접 작성하거나 다시 만들기</span>}
            </p>
            {fields.map((f) => (
              <div key={f.key}>
                <label className="block text-[11px] text-slate-500 mb-0.5">{f.label}</label>
                <textarea
                  rows={f.rows}
                  value={draft[f.key] ?? ''}
                  readOnly={!pending}
                  onChange={(e) => setDraft((d) => ({ ...d, [f.key]: e.target.value }))}
                  className="w-full bg-slate-800 border border-slate-600 rounded-lg px-2.5 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-indigo-500 resize-y"
                />
              </div>
            ))}
          </div>
        )}
      </div>

      {item.status === 'rejected' && item.reject_reason && (
        <p className="text-xs text-slate-400">반려 사유: {item.reject_reason}</p>
      )}
      {error && <p className="text-xs text-rose-600 dark:text-rose-400">{error}</p>}

      {pending && (
        <div className="flex items-center gap-2 justify-end flex-wrap">
          {isAnswer && !rejecting && (
            <span className="text-xs text-slate-500 mr-auto">지식은 정상 — 반영할 내용이 없어요. 확인했으면 종료하세요.</span>
          )}
          {rejecting ? (
            <>
              <input
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder={isAnswer ? '종료 사유 (예: 답변 오류 확인, 프롬프트 개선 과제로 기록)' : '반려 사유 (신고자에게 보입니다)'}
                className="flex-1 min-w-0 bg-slate-800 border border-slate-600 rounded-lg px-2.5 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-indigo-500"
              />
              <Button variant="ghost" size="sm" onClick={() => setRejecting(false)}>취소</Button>
              <Button variant="danger" size="sm" loading={reject.isPending} disabled={!reason.trim()} onClick={() => reject.mutate()}>
                {isAnswer ? '종료' : '반려'}
              </Button>
            </>
          ) : (
            <>
              <Button variant="ghost" size="sm" onClick={() => setRejecting(true)}>{isAnswer ? '종료…' : '반려…'}</Button>
              {!isAnswer && (
                <Button
                  variant="primary" size="sm" loading={approve.isPending} disabled={!ready || retarget.isPending}
                  title={item.target_type === 'missing' ? '새 지식으로 등록됩니다.' : '새 버전으로 교체되고, 기존 내용은 이력으로 남습니다.'}
                  onClick={() => approve.mutate()}
                >
                  승인 · 반영
                </Button>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

export function CorrectionReviewTab({ namespace, pendingByNamespace, onSelectNamespace }: {
  namespace: string;
  pendingByNamespace: Record<string, number>;
  onSelectNamespace: (ns: string) => void;
}) {
  const otherParts = Object.entries(pendingByNamespace).filter(([ns, n]) => ns !== namespace && n > 0);
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
        what="사용자가 채팅에서 '답변 틀림' · '이 근거 틀림'으로 신고한 정정 의견"
        when="채팅 답변에 틀린 근거나 빠진 내용이 있다고 사용자가 한 줄 의견을 남길 때"
        todo="AI가 고른 대상·수정안을 확인해 승인(새 버전으로 교체) 또는 반려(사유 필수). 대상이 틀리면 '정정 대상'에서 변경"
        detail="사용자 의견은 승인 전까지 검색·답변에 전혀 반영되지 않습니다. 승인하면 기존 내용은 이력으로 남고 새 버전이 쓰이며, 반려 사유는 신고자에게 보여집니다."
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
      {otherParts.length > 0 && (
        <p className="text-xs text-slate-500">
          다른 파트 대기:{' '}
          {otherParts.map(([ns, n], i) => (
            <span key={ns}>
              {i > 0 && ', '}
              <button onClick={() => onSelectNamespace(ns)} className="text-indigo-600 dark:text-indigo-400 hover:underline">{ns} {n}건</button>
            </span>
          ))}
        </p>
      )}
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
