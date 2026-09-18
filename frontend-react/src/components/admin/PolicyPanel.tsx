import { useState } from 'react';
import { clsx } from 'clsx';
import { List, FileWarning } from 'lucide-react';
import { PolicyItemBrowser } from './PolicyItemBrowser';
import { PolicyUnresolvedReport } from './PolicyUnresolvedReport';

/**
 * 정책서 관련 화면들을 하나의 "정책" 대분류 탭 아래 서브탭으로 묶는다(2026-09-04) —
 * VocEmailPanel.tsx의 서브탭 패턴과 동일. 이전엔 Admin.tsx에 3개가 평평하게 나열돼 있었다.
 * 2026-09-15 실험실 게이트 작업3으로 "모니터" 서브탭을 추가했다가, 2026-09-17 제거함 —
 * ③④(평가체계/retrieval 현황)가 PolicyLab의 실행 이력 추이와 완전히 중복이었고, ②(갭
 * 이슈 체크리스트)는 살아있는 모니터링이 아니라 정적 문서(data-storage-philosophy.md
 * §9)에 가까웠음. 유일하게 고유했던 ①(기준정보 규모)은 PolicyLab 안으로 흡수(사용자
 * 지적: "사용자 기능 관점에서 별도 화면일 이유가 약하다").
 * 2026-09-18: "저장소 실험실"(PolicyLab)을 여기서 떼어내 "파이프라인 디버그"와 함께
 * 최상위 "실험실" 탭(ExperimentLab)으로 통합 — 둘 다 "질의→검색결과→유사도" 축이라는
 * 지적.
 */
type PolicySubTab = 'items' | 'unresolved';

const SUB_TABS: { id: PolicySubTab; label: string; icon: React.ReactNode }[] = [
  { id: 'items', label: '항목 브라우저', icon: <List className="w-4 h-4" /> },
  { id: 'unresolved', label: '미분류', icon: <FileWarning className="w-4 h-4" /> },
];

export function PolicyPanel() {
  const [subTab, setSubTab] = useState<PolicySubTab>('items');

  return (
    <div className="space-y-4">
      <div className="border-b border-slate-700">
        <div className="flex gap-1">
          {SUB_TABS.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setSubTab(tab.id)}
              className={clsx(
                'flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors',
                subTab === tab.id
                  ? 'text-indigo-400 border-indigo-500'
                  : 'text-slate-400 border-transparent hover:text-slate-200 hover:border-slate-600',
              )}
            >
              {tab.icon}
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {subTab === 'items' && <PolicyItemBrowser />}
      {subTab === 'unresolved' && <PolicyUnresolvedReport />}
    </div>
  );
}
