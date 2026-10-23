import { useQuery } from '@tanstack/react-query';
import { listTechniques } from '../../api/eval';

/**
 * 품질 › 답 기법 (v2.170, eval-gate-redesign §9-4) — 검색·답을 다듬는 기법의 기준값·근거·코드 위치(읽기 전용).
 * 기준이 코드 상수에만 있어 까먹던 것(사용자 10/22)을 대장(backend/service/eval/techniques.py) 하나로. 기준값은 지금 서버의 운영값.
 * 값을 바꾸는 건 여기서 하지 않는다 — 평가 게이트 [개선]에서 후보로 재고 통과해야(설정은 설정 화면·환경 변수).
 */

const STAGE_ORDER = ['검색', '근거 판정', '답 모양', '생성'];

export function AnswerTechniques() {
  const { data = [], isLoading, error } = useQuery({ queryKey: ['eval-techniques'], queryFn: listTechniques, staleTime: 60_000 });
  if (isLoading) return <p className="text-sm text-slate-500">불러오는 중…</p>;
  if (error) return <p className="text-sm text-rose-600 dark:text-rose-400">불러오지 못했습니다: {(error as Error).message}</p>;
  return (
    <div className="space-y-5">
      <p className="text-xs text-slate-500" title="값 변경은 평가 게이트 [개선] 후보 → 측정 → 통과한 판정과 연결해 채택한 뒤 설정·환경 변수로">
        질문 → 검색 → 근거 판정 → 답 모양 → 생성 순서 · 기준값은 지금 서버 값 ⓘ
      </p>
      {STAGE_ORDER.map((stage) => {
        const items = data.filter((t) => t.stage === stage);
        if (!items.length) return null;
        return (
          <section key={stage} className="space-y-2">
            <h3 className="text-sm font-medium text-slate-300">{stage}</h3>
            {items.map((t) => (
              <div key={t.key} className="rounded-lg border border-slate-700 bg-slate-800 p-3 space-y-1" data-testid={`technique-${t.key}`}>
                <p className="text-sm text-slate-100">{t.name}</p>
                <p className="text-xs text-slate-400">{t.does}</p>
                {t.values.length > 0 && (
                  <p className="text-xs text-slate-300">
                    {t.values.map((v) => (
                      <span key={v.label} className="mr-3" title={v.setting ? `설정 키 ${v.setting}` : '코드 상수'}>
                        {v.label} <b>{v.value}</b>
                      </span>
                    ))}
                  </p>
                )}
                {t.evidence.map((e) => <p key={e.date + e.note} className="text-xs text-slate-500">{e.date} · {e.note}</p>)}
                {t.where && <p className="text-[11px] text-slate-600 font-mono">{t.where}</p>}
              </div>
            ))}
          </section>
        );
      })}
    </div>
  );
}
