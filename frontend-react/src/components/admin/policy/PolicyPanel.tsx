import { useState } from 'react';
import { clsx } from 'clsx';
import { List, Table2, BookOpen, FileQuestion, FileJson } from 'lucide-react';
import { useAuthStore } from '../../../store/useAuthStore';
import { PolicyListTab } from './PolicyListTab';
import { RefTableTab } from './RefTableTab';
import { OverviewTab } from './OverviewTab';
import { SourceCheckTab } from './SourceCheckTab';
import { PolicyJsonTab } from './PolicyJsonTab';

/**
 * 지식 › 정책서(v0 재구축 2단계, 2026-10-21) — 정책 · 참고표 · 문서 개요. 고른 업무 컬렉션 기준.
 * 옛 엑셀 경로 화면(항목 브라우저·미분류 집계·정책서 올리기·위험도 검토 큐)은 뺐다 — v0는 원문 통째라 그 화면들이 빈 칸만 보였다.
 * 내용 변경은 정책 상세의 [AI 수정 제안]·[직접 수정] → 품질 › 정정 요청에서 승인할 때만 새 버전이 된다.
 * 3단계(10/21): [원문 확인](원문에 대한 질문·문제 + 제안 연결 — 정정이 아님) · [JSON](관리자 — 내려받기, 가져오기는 정정 제안으로).
 */
type Tab = 'policies' | 'tables' | 'overviews' | 'checks' | 'json';

const TABS: { id: Tab; label: string; icon: React.ReactNode; hint: string }[] = [
  { id: 'policies', label: '정책', icon: <List className="w-4 h-4" />, hint: '지금 쓰이는 정책 — 눌러서 원문·버전 보기, 수정 제안' },
  { id: 'tables', label: '참고표', icon: <Table2 className="w-4 h-4" />, hint: '정책서의 조건·결과 표(원표 그대로)' },
  { id: 'overviews', label: '문서 개요', icon: <BookOpen className="w-4 h-4" />, hint: '정책서 서문 — 문서 목적·적용 대상' },
  { id: 'checks', label: '원문 확인', icon: <FileQuestion className="w-4 h-4" />, hint: '원문에 대한 질문·문제(담당자 확인) + 제안 연결' },
  { id: 'json', label: 'JSON', icon: <FileJson className="w-4 h-4" />, hint: '관리자 — 정책 내려받기, 개정본 가져오기(정정 요청에 올림)' },
];

export function PolicyPanel({ onOpenCorrection, onOpenCorrections }: {
  onOpenCorrection: (id: number) => void;
  onOpenCorrections: () => void;
}) {
  const [tab, setTab] = useState<Tab>('policies');
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin');
  const tabs = TABS.filter((t) => t.id !== 'json' || isAdmin);
  return (
    <div className="space-y-4">
      <div className="border-b border-slate-700">
        <div className="flex gap-1" role="tablist">
          {tabs.map((t) => (
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
      {tab === 'checks' && <SourceCheckTab onOpenCorrection={onOpenCorrection} />}
      {tab === 'json' && isAdmin && <PolicyJsonTab onOpenCorrections={onOpenCorrections} />}
    </div>
  );
}
