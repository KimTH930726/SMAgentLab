import { Accordion } from '../ui/Accordion';
import type { KnowledgeResult } from '../../types';
import type { Freshness } from '../../api/knowledge';
import { clsx } from 'clsx';
import { EvidenceReportControls } from './EvidenceReportControls';

interface SearchResultCardProps {
  result: KnowledgeResult;
  defaultOpen?: boolean;
  index: number;
  underReview?: boolean;
  /** 근거 최신성(11/04, Guru·Notion Verified 배지 관례) — 없으면(불러오는 중·정책 등) 표시 안 함 */
  freshness?: Freshness;
}

function getScoreInfo(score: number) {
  if (score >= 0.7) return { label: '높음', barColor: 'bg-emerald-500', textColor: 'text-emerald-600 dark:text-emerald-400' };
  if (score >= 0.4) return { label: '보통', barColor: 'bg-amber-500', textColor: 'text-amber-600 dark:text-amber-400' };
  return { label: '낮음', barColor: 'bg-slate-500', textColor: 'text-slate-400' };
}

const fmtDay = (s: string) => new Date(s).toLocaleDateString('ko-KR', { year: 'numeric', month: 'numeric', day: 'numeric', timeZone: 'Asia/Seoul' });

export function SearchResultCard({ result, defaultOpen = false, index, underReview, freshness }: SearchResultCardProps) {
  const scoreInfo = getScoreInfo(result.final_score);
  const displayName = `문서 #${result.id}`;
  const pct = Math.min(Math.round(result.final_score * 100), 100);

  const header = (
    <div className="flex items-center gap-3 min-w-0 w-full">
      <span className="text-xs text-slate-500 flex-shrink-0">#{index + 1}</span>
      <span className="text-sm text-slate-200 truncate font-medium flex-1">{displayName}</span>
      {freshness && (
        <span data-testid={`freshness-${result.id}`}
          title={`${freshness.reviewed ? '마지막 검토' : '마지막 수정·등록'} ${fmtDay(freshness.last_at)}${freshness.interval_days ? ` · 검토 주기 ${freshness.interval_days}일` : ''} · 담당 ${freshness.owners.join(', ') || '없음(관리자)'}`}
          className={clsx('flex-shrink-0 rounded px-1.5 py-0.5 text-[10px]', freshness.overdue
            ? 'bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300'
            : 'bg-slate-800 text-slate-400')}>
          {freshness.overdue ? '검토 기한 지남' : `${freshness.reviewed ? '검토' : '수정'} ${fmtDay(freshness.last_at)}`}
        </span>
      )}
      {/* 유사도 프로그레스 바 + 라벨 */}
      <div className="flex items-center gap-2 flex-shrink-0">
        <span className="text-[10px] text-slate-500">유사도</span>
        <div className="w-16 h-1.5 bg-slate-700 rounded-full overflow-hidden">
          <div className={clsx('h-full rounded-full', scoreInfo.barColor)} style={{ width: `${pct}%` }} />
        </div>
        <span className={clsx('text-xs font-medium whitespace-nowrap', scoreInfo.textColor)}>
          {scoreInfo.label} {pct}%
        </span>
      </div>
      <span className="text-[10px] text-slate-500 flex-shrink-0 hidden sm:inline">click</span>
    </div>
  );

  return (
    <Accordion
      title={header}
      defaultOpen={defaultOpen}
      className="bg-slate-900/60 border border-slate-700/50"
      headerClassName="hover:bg-slate-800/50"
      actions={underReview ? <EvidenceReportControls /> : undefined}
    >
      <div className="px-4 pb-4 space-y-3">
        {/* Content */}
        <p className="text-sm text-slate-300 leading-relaxed whitespace-pre-wrap">
          {result.content}
        </p>
      </div>
    </Accordion>
  );
}
