import { useState } from 'react';
import { clsx } from 'clsx';
import { FlaskConical, Search, ArrowRight } from 'lucide-react';
import { PolicyLab } from './PolicyLab';
import { UnifiedAdhocSearch } from './UnifiedAdhocSearch';

/**
 * "파이프라인 디버그"(즉석 질의 — 아무 질문이나 넣고 점수분해 확인)와 "정책 저장소
 * 실험실"(골든셋 실행 — 고정 문항셋 일괄 실행, 집계 지표)을 하나의 최상위 탭으로
 * 통합(2026-09-18) — 둘 다 "질의 → 검색결과 → 유사도 점수"를 보는 같은 축이라는 지적.
 *
 * 이름을 "실험실"에서 "평가 게이트"로 올림(2026-09-18) — 다만 지금은 여전히 수동
 * 측정 도구고 아무것도 자동으로 막지 않는다. "게이트"라는 이름은 향후 여기서 측정한
 * 지표를 기준으로 루프 엔지니어링(임계치 미달 시 자동 재시도/차단 등)을 붙일 계획이라는
 * 것에 근거한 선제적 명명 — 오늘 다른 곳(Track2 docstring, 발표자료)에서 "게이트/축
 * 완성" 같은 과장된 라벨을 실제 증거 수준으로 낮춘 것과 원칙적으로는 반대 방향이라,
 * 실제 차단 로직이 붙기 전까지는 이 docstring에 그 사실을 명시해 혼동을 막는다.
 *
 * 골든셋 실행 쪽 축 선택 UI는 안 만듦 — 일반지식 골든셋(query_log_review 라벨링 필요)이
 * 아직 없어서 선택지가 정책 하나뿐인 토글은 의미가 없음.
 *
 * 즉석 질의 쪽은 "일반지식/정책 중 하나 선택" 토글 → "두 패널을 나란히" → 최종적으로
 * **하나의 통합 리스트(UnifiedAdhocSearch)**로 3번 바뀜(2026-09-18). 두 패널로 나란히
 * 보여주는 것도 "정책이 뭔가 대단한 별도 축"처럼 보인다는 지적 — chat처럼 질문 하나
 * 기준으로 top-K 하나를 보는 구조를 원함. 점수 스케일이 안 맞는 문제(코사인 vs RDB
 * ts_rank)는 Reciprocal Rank Fusion(RRF)으로 해결 — OpenSearch/Azure AI Search 등이
 * 실제 쓰는 표준 기법, 정규화 없이 각 목록 안 순위만으로 합침. 상세는 UnifiedAdhocSearch.tsx.
 *
 * 골든셋 문항 → 즉석질의 드릴다운은 여전히 없음 — Track2가 문항별 결과가 아니라 유형별
 * 집계만 반환해서(백엔드 확장 필요) 이번 범위 밖.
 */
type GateMode = 'adhoc' | 'goldenset';

const MODE_HINT: Record<GateMode, string> = {
  adhoc: '질문 하나를 넣어 검색 방식(유사도 점수)을 직접 확인합니다 — 실제 채팅과 동일하게 일반지식·정책을 항상 함께 봅니다. 아래 "골든셋 실행"은 이 방식을 여러 문항에 한 번에 돌려 정확도로 집계한 것입니다.',
  goldenset: '위 "즉석 질의"와 같은 검색 방식을, 고정된 여러 문항에 한 번에 돌려 hit@K·정확도 등으로 집계합니다.',
};

export function EvaluationGate() {
  const [mode, setMode] = useState<GateMode>('adhoc');

  return (
    <div className="space-y-4">
      <div className="border-b border-slate-700">
        <div className="flex gap-1">
          <button
            onClick={() => setMode('adhoc')}
            className={clsx(
              'flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors',
              mode === 'adhoc'
                ? 'text-indigo-400 border-indigo-500'
                : 'text-slate-400 border-transparent hover:text-slate-200 hover:border-slate-600',
            )}
          >
            <Search className="w-4 h-4" />
            즉석 질의 <span className="text-[10px] text-slate-500 font-normal">(단건)</span>
          </button>
          <div className="flex items-center px-1 text-slate-600">
            <ArrowRight className="w-3.5 h-3.5" />
          </div>
          <button
            onClick={() => setMode('goldenset')}
            className={clsx(
              'flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors',
              mode === 'goldenset'
                ? 'text-indigo-400 border-indigo-500'
                : 'text-slate-400 border-transparent hover:text-slate-200 hover:border-slate-600',
            )}
          >
            <FlaskConical className="w-4 h-4" />
            골든셋 실행 <span className="text-[10px] text-slate-500 font-normal">(집계)</span>
          </button>
        </div>
      </div>

      <p className="text-xs text-slate-500 leading-relaxed -mt-2">{MODE_HINT[mode]}</p>

      {mode === 'adhoc' && <UnifiedAdhocSearch />}
      {mode === 'goldenset' && <PolicyLab />}
    </div>
  );
}
