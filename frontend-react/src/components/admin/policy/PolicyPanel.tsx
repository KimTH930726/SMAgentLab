import { useState } from 'react';
import { clsx } from 'clsx';
import { List, Table2, BookOpen } from 'lucide-react';
import { PolicyListTab } from './PolicyListTab';
import { RefTableTab } from './RefTableTab';
import { OverviewTab } from './OverviewTab';

/**
 * 지식 › 정책서(v0 재구축 2단계, 2026-10-21) — 정책 · 참고표 · 문서 개요. 고른 업무 영역 기준.
 * 옛 엑셀 경로 화면(항목 브라우저·미분류 집계·정책서 올리기·위험도 검토 큐)은 뺐다 — v0는 원문 통째라 그 화면들이 빈 칸만 보였다.
 * 내용 변경은 정책 상세의 [AI 수정 제안]·[직접 수정] → 품질 › 정정 요청에서 승인할 때만 새 버전이 된다.
 */
type Tab = 'policies' | 'tables' | 'overviews';

const TABS: { id: Tab; label: string; icon: React.ReactNode; hint: string }[] = [
  { id: 'policies', label: '정책', icon: <List className="w-4 h-4" />, hint: '지금 쓰이는 정책 — 눌러서 원문·버전 보기, 수정 제안' },
  { id: 'tables', label: '참고표', icon: <Table2 className="w-4 h-4" />, hint: '정책서의 조건·결과 표(원표 그대로)' },
  { id: 'overviews', label: '문서 개요', icon: <BookOpen className="w-4 h-4" />, hint: '정책서 서문 — 문서 목적·적용 대상' },
];

export function PolicyPanel({ onOpenCorrection }: { onOpenCorrection: (id: number) => void }) {
  const [tab, setTab] = useState<Tab>('policies');
  return (
    <div className="space-y-4">
      <div className="border-b border-slate-700">
        <div className="flex gap-1" role="tablist">
          {TABS.map((t) => (
            <button
              key={t.id}
              role="tab"
              aria-selected={tab === t.id}
              title={t.hint}
              onClick={() => setTab(t.id)}
              className={clsx(
                'flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors',
                tab === t.id
                  ? 'text-indigo-600 border-indigo-500 dark:text-indigo-400'
                  : 'text-slate-400 border-transparent hover:text-slate-200 hover:border-slate-600',
              )}
            >
              {t.icon}
              {t.label}
            </button>
          ))}
        </div>
      </div>
      {tab === 'policies' && <PolicyListTab onOpenCorrection={onOpenCorrection} />}
      {tab === 'tables' && <RefTableTab />}
      {tab === 'overviews' && <OverviewTab />}
    </div>
  );
}
