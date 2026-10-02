import { Sparkles, FileText } from 'lucide-react';
import type { AiVerdict } from '../../api/corrections';

/**
 * "답변 틀림" AI 분석 카드(2026-10-01) — 어느 근거를 왜 골랐고, 뭐가 틀렸고, 승인되면 어떻게 바뀌는지.
 * 사용자 접수 카드와 관리자 정정 검토가 같은 컴포넌트를 쓴다(같은 설명을 보고 판단하도록).
 * 판정은 신호일 뿐 — 반영은 담당자 승인 후에만이라는 걸 카드 안에서 항상 밝힌다.
 */

const VERDICT_TEXT: Record<NonNullable<AiVerdict['verdict']>, { title: string; tone: string }> = {
  evidence: {
    title: '근거 내용이 틀린 것 같아요',
    tone: 'bg-rose-50 text-rose-700 border-rose-200 dark:bg-rose-900/30 dark:text-rose-300 dark:border-rose-700/50',
  },
  missing: {
    title: '근거에 없는 내용이에요',
    tone: 'bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-900/30 dark:text-amber-300 dark:border-amber-700/50',
  },
  answer_error: {
    title: '근거는 맞는데 답변이 잘못 옮겼어요',
    tone: 'bg-sky-50 text-sky-700 border-sky-200 dark:bg-sky-900/30 dark:text-sky-300 dark:border-sky-700/50',
  },
};

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[7rem_1fr] gap-2 text-xs">
      <span className="text-slate-500">{label}</span>
      <span className="text-slate-300 leading-relaxed">{children}</span>
    </div>
  );
}

export function CorrectionAnalysis({ verdict, audience }: { verdict: AiVerdict; audience: 'reporter' | 'admin' }) {
  if (!verdict.verdict) return null;
  const v = VERDICT_TEXT[verdict.verdict];
  const guessed = verdict.method === 'similarity' || verdict.method === 'llm_no_opinion';
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <Sparkles className="w-3.5 h-3.5 text-indigo-500 dark:text-indigo-400" />
        <span className="text-xs font-medium text-indigo-600 dark:text-indigo-400">AI 분석</span>
        <span className={`text-[11px] px-2 py-0.5 rounded-full border ${v.tone}`}>{v.title}</span>
        {guessed && (
          <span
            className="text-[11px] text-amber-600 dark:text-amber-400"
            title={verdict.method === 'llm_no_opinion'
              ? '사용자가 맞는 내용을 알려주지 않아, 질문·답변·근거의 어긋남만 보고 추정했습니다. 담당자가 확인합니다.'
              : 'AI 판정이 원활하지 않아 의견과 가장 비슷한 근거로 추정했습니다. 담당자가 대상을 확인합니다.'}
          >
            추정
          </span>
        )}
      </div>

      {verdict.label && (
        <div className="flex items-start gap-2 rounded-lg border border-slate-700 bg-slate-900/40 px-2.5 py-2">
          <FileText className="w-3.5 h-3.5 text-slate-500 mt-0.5 flex-shrink-0" />
          <div className="min-w-0">
            <p className="text-xs text-slate-200 font-medium">{verdict.label}</p>
            {verdict.preview && <p className="text-[11px] text-slate-500 line-clamp-2">{verdict.preview}</p>}
          </div>
        </div>
      )}

      <div className="space-y-1">
        {verdict.user_claim && <Row label="이렇게 이해했어요">{verdict.user_claim}</Row>}
        {verdict.reason && <Row label="판단 이유">{verdict.reason}</Row>}
        {verdict.wrong_part && (
          <Row label={verdict.verdict === 'answer_error' ? '답변의 틀린 부분' : '틀린 부분'}>
            <mark className="bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-200 rounded px-1">
              {verdict.wrong_part}
            </mark>
          </Row>
        )}
        {verdict.fix_summary && <Row label="반영되면">{verdict.fix_summary}</Row>}
        {verdict.verdict === 'answer_error' && (
          <Row label="지식은">그대로 둬요 — 답변 품질 신호로 담당자에게 전달돼요.</Row>
        )}
      </div>

      {audience === 'reporter' && (
        <p className="text-[11px] text-slate-500">담당자가 확인한 뒤에만 반영돼요. 결과는 다음에 채팅을 열 때 알려드려요.</p>
      )}
    </div>
  );
}
