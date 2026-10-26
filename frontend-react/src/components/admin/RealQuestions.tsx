import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { decideQuestionCandidate, listQuestionCandidates, type QuestionCandidate } from '../../api/eval';
import { useAuthStore } from '../../store/useAuthStore';
import { showError } from '../../store/useDialogStore';

/**
 * 평가 게이트 [실사용 문항] (v2.171, 사용자 10/26 "실사용 질문을 근거로 한 자가 개선 루프") — 정정 요청이 승인된 채팅 질문이 자동으로 여기 온다.
 * [시험지에 넣기]를 누르면 다음 측정부터 "실사용" 층(잠근 시험지)으로 잰다 — 고칠 때는 안 보고 판정 때만. 정답 = 정정으로 고친 정책.
 * 지식(컨플루언스) 정정은 쌓아만 둔다 — 지식 측정 연결은 다음(백로그 0-7). 관리자만(사용자 질문 원문).
 */

const VIEWS = [
  { key: 'proposed', label: '새로 온 것' },
  { key: 'added', label: '시험지에 넣음' },
  { key: 'dismissed', label: '뺌' },
] as const;
const fmtDate = (s: string) => new Date(s).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });

function Row({ q, isAdmin }: { q: QuestionCandidate; isAdmin: boolean }) {
  const qc = useQueryClient();
  const decide = useMutation({
    mutationFn: (status: QuestionCandidate['status']) => decideQuestionCandidate(q.id, status),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['eval-real-questions'] }),
    onError: (e) => showError(e, '저장하지 못했습니다'),
  });
  const target = q.gold.kind === 'policy' ? '정책' : q.gold.kind === 'knowledge' ? '지식' : q.gold.target_type ?? '-';
  return (
    <li className="rounded-lg border border-slate-700 bg-slate-800 p-3 space-y-1" data-testid={`real-q-${q.id}`}>
      <p className="text-xs text-slate-500">
        {q.qid} · {fmtDate(q.created_at)}{q.namespace ? ` · ${q.namespace}` : ''} · 정정 #{q.improvement_item_id} · 정답: 고친 {target}
        {q.decided_by && ` · ${q.decided_by}`}
      </p>
      <p className="text-sm text-slate-100 whitespace-pre-wrap">{q.question}</p>
      {q.report && <p className="text-xs text-slate-400"><span className="text-slate-500">신고 내용 </span>{q.report}</p>}
      {!q.measurable && (
        <p className="text-xs text-amber-600 dark:text-amber-400" title="지금 정책 골든만 잰다 — 지식 정답 문항은 지식 측정에 연결되면 들어간다">
          지금은 잴 수 없음(정답이 정책이 아님) — 넣어 두면 연결된 뒤 잽니다 ⓘ
        </p>
      )}
      {isAdmin && (
        <div className="flex gap-4 justify-end text-xs">
          {q.status !== 'added' && (
            <button className="text-indigo-600 dark:text-indigo-400 font-medium hover:underline disabled:opacity-50" disabled={decide.isPending}
              title="다음 측정부터 이 질문을 '실사용' 층(잠근 시험지)으로 잽니다" onClick={() => decide.mutate('added')}>시험지에 넣기</button>
          )}
          {q.status !== 'dismissed' && (
            <button className="text-slate-400 hover:underline disabled:opacity-50" disabled={decide.isPending}
              title="시험지에 쓰지 않습니다(질문이 모호함·중복 등)" onClick={() => decide.mutate('dismissed')}>빼기</button>
          )}
          {q.status !== 'proposed' && (
            <button className="text-slate-400 hover:underline disabled:opacity-50" disabled={decide.isPending} onClick={() => decide.mutate('proposed')}>
              결정 취소
            </button>
          )}
        </div>
      )}
    </li>
  );
}

export function RealQuestionsTab() {
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin');
  const [view, setView] = useState<QuestionCandidate['status']>('proposed');
  const { data = [], isLoading, error } = useQuery({
    queryKey: ['eval-real-questions', view], queryFn: () => listQuestionCandidates(view), enabled: isAdmin, staleTime: 30_000,
  });
  if (!isAdmin) return <p className="text-sm text-slate-500">관리자만 볼 수 있어요(사용자 질문 원문).</p>;
  return (
    <div className="space-y-3">
      <p className="text-xs text-slate-400" title="AI가 만든 질문만으로 고치면 시험지에만 강해진다 — 실제 질문으로 '정말 나아졌나'를 본다">
        채팅에서 신고 → 정정이 승인된 실제 질문이 자동으로 와요. 넣으면 다음 측정부터 잠근 시험지로 잽니다 ⓘ
      </p>
      <div className="flex gap-2 text-xs">
        {VIEWS.map((v) => (
          <button key={v.key} onClick={() => setView(v.key)}
            className={`px-2.5 py-1 rounded-full border ${view === v.key ? 'border-indigo-500 dark:border-indigo-400 text-indigo-600 dark:text-indigo-400' : 'border-slate-600 text-slate-400'}`}>
            {v.label}
          </button>
        ))}
      </div>
      {isLoading && <p className="text-sm text-slate-500">불러오는 중…</p>}
      {error && <p className="text-sm text-rose-600 dark:text-rose-400">불러오지 못했습니다: {(error as Error).message}</p>}
      {!isLoading && !error && data.length === 0 && (
        <p className="text-sm text-slate-500 py-8 text-center">
          {view === 'proposed' ? '아직 없어요 — 실사용이 열리고 채팅 신고가 정정 승인되면 여기에 쌓입니다.' : '없어요.'}
        </p>
      )}
      <ul className="space-y-2">{data.map((q) => <Row key={q.id} q={q} isAdmin={isAdmin} />)}</ul>
    </div>
  );
}
