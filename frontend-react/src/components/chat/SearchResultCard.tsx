import { Accordion } from '../ui/Accordion';
import type { KnowledgeResult } from '../../types';
import type { Freshness, SourcePage } from '../../api/knowledge';
import { clsx } from 'clsx';
import { EvidenceReportControls } from './EvidenceReportControls';

interface SearchResultCardProps {
  result: KnowledgeResult;
  defaultOpen?: boolean;
  index: number;
  underReview?: boolean;
  /** 근거 최신성(11/04, Guru·Notion Verified 배지 관례) — 없으면(불러오는 중·정책 등) 표시 안 함 */
  freshness?: Freshness;
  /** 원문 페이지·앞뒤 문맥 그림 수(11/06) — 그림이 있을 때만 배지·링크 */
  sourcePage?: SourcePage;
}

// 신뢰도 판정은 서버 하나(retrieval.confidence_label — 채팅 문맥과 같은 값, 11/05). 화면은 색만 정한다.
// 옛 대화(라벨 없음)는 판정하지 않고 점수만 — 화면이 따로 0.7/0.4로 판정하던 것(가중치 곱한 점수라 부풀었다)을 없앴다
const TONE: Record<string, { barColor: string; textColor: string }> = {
  높음: { barColor: 'bg-emerald-500', textColor: 'text-emerald-600 dark:text-emerald-400' },
  보통: { barColor: 'bg-amber-500', textColor: 'text-amber-600 dark:text-amber-400' },
  낮음: { barColor: 'bg-slate-500', textColor: 'text-slate-400' },
};
function getScoreInfo(confidence?: string) {
  return { label: confidence ?? '', ...(TONE[confidence ?? ''] ?? TONE['낮음']) };
}

const fmtDay = (s: string) => new Date(s).toLocaleDateString('ko-KR', { year: 'numeric', month: 'numeric', day: 'numeric', timeZone: 'Asia/Seoul' });

export function SearchResultCard({ result, defaultOpen = false, index, underReview, freshness, sourcePage }: SearchResultCardProps) {
  const images = sourcePage && sourcePage.image_count > 0 ? sourcePage.image_count : 0;
  const scoreInfo = getScoreInfo(result.confidence);
  const displayName = `문서 #${result.id}`;
  const pct = Math.min(Math.round((result.relevance ?? result.final_score) * 100), 100);

  const header = (
    <div className="flex items-center gap-3 min-w-0 w-full">
      <span className="text-xs text-slate-500 flex-shrink-0">#{index + 1}</span>
      <span className="text-sm text-slate-200 truncate font-medium flex-1">{displayName}</span>
      {images > 0 && (
        <span data-testid={`images-${result.id}`} title={`원문의 이 내용 앞뒤에 그림·다이어그램 ${images}개 — 글로 옮기지 않았어요(원문에서 확인)`}
          className="flex-shrink-0 rounded border border-slate-600 bg-slate-800 px-1.5 py-0.5 text-[11px] font-medium text-slate-300">
          그림 {images}
        </span>
      )}
      {freshness && (
        <span data-testid={`freshness-${result.id}`}
          title={`${freshness.reviewed ? '마지막 검토' : '마지막 수정·등록'} ${fmtDay(freshness.last_at)}${freshness.interval_days ? ` · 검토 주기 ${freshness.interval_days}일` : ''} · 담당 ${freshness.owners.join(', ') || '없음(관리자)'}`}
          className={clsx('flex-shrink-0 rounded border px-1.5 py-0.5 text-[11px] font-medium', freshness.overdue
            ? 'border-amber-300 bg-amber-100 text-amber-800 dark:border-amber-500/40 dark:bg-amber-500/15 dark:text-amber-300'
            : freshness.reviewed ? 'border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-500/40 dark:bg-emerald-500/10 dark:text-emerald-300'
              : 'border-slate-600 bg-slate-800 text-slate-300')}>
          {freshness.overdue ? `⚠ 검토 기한 지남 · ${fmtDay(freshness.last_at)}` : `${freshness.reviewed ? '✓ 검토' : '수정'} ${fmtDay(freshness.last_at)}`}
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
        {images > 0 && sourcePage && (
          <p className="border-t border-dashed border-slate-700 pt-2 text-xs text-slate-400">
            원문 그림 {images}개 ·{' '}
            <a href={sourcePage.url} target="_blank" rel="noreferrer" className="text-indigo-600 hover:underline dark:text-indigo-400">
              원문 페이지에서 그림 보기 ↗
            </a>
          </p>
        )}
      </div>
    </Accordion>
  );
}
