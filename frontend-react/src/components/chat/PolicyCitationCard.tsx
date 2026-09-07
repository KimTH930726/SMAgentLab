import { Accordion } from '../ui/Accordion';
import type { PolicyCitation } from '../../types';
import { clsx } from 'clsx';

interface PolicyCitationCardProps {
  citation: PolicyCitation;
  defaultOpen?: boolean;
  index: number;
}

// 정책 검색 편입 2단계(2026-09-06) — "정책에서 온 답인지 기준정보에서 온 답인지 구분이 안
// 되고 원문도 안 보인다"는 피드백에 대응하는 카드. SearchResultCard(rag_knowledge)와 스타일
// 계열은 맞추되 보라색 계열로 색만 바꿔서 "이건 다른 출처다"가 한눈에 보이게 한다.
export function PolicyCitationCard({ citation, defaultOpen = false, index }: PolicyCitationCardProps) {
  const isParam = citation.kind === 'param';
  const kindLabel = isParam ? '파라미터 · 정확 일치' : '서술';

  const header = (
    <div className="flex items-center gap-3 min-w-0 w-full">
      <span className="text-xs text-slate-500 flex-shrink-0">#{index + 1}</span>
      <span
        className={clsx(
          'text-[10px] px-1.5 py-0.5 rounded border flex-shrink-0',
          isParam
            ? 'bg-violet-900/40 text-violet-300 border-violet-700/40'
            : 'bg-fuchsia-900/40 text-fuchsia-300 border-fuchsia-700/40',
        )}
      >
        {kindLabel}
      </span>
      <span className="text-sm text-slate-200 truncate font-medium flex-1">{citation.policy_name}</span>
      {citation.category_path.length > 0 && (
        <span className="text-[10px] text-slate-500 truncate hidden sm:inline max-w-[40%]">
          {citation.category_path.join(' > ')}
        </span>
      )}
    </div>
  );

  return (
    <Accordion
      title={header}
      defaultOpen={defaultOpen}
      className="bg-violet-950/20 border border-violet-800/30"
      headerClassName="hover:bg-violet-900/20"
    >
      <div className="px-4 pb-4 space-y-3">
        <p className="text-sm text-slate-300 leading-relaxed whitespace-pre-wrap">
          {citation.detail}
        </p>

        {citation.raw_body && (
          <div>
            <p className="text-xs text-slate-500 mb-1.5">원문 정책</p>
            <div className="rounded-lg bg-slate-900/60 border border-slate-700/50 px-3 py-2 text-xs text-slate-400 whitespace-pre-wrap leading-relaxed">
              {citation.raw_body}
            </div>
          </div>
        )}
      </div>
    </Accordion>
  );
}
