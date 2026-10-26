import type { Technique } from '../../api/eval';

/**
 * 답 기법 한 장(v2.171 품질 › 답 기법에서 옮김, v2.172) — 틀린 문항 리포트의 기법을 펼치면 "이 기법은 무엇인가"로 보인다.
 * 기법마다 하는 일 · 지금 값 · 왜 있나(도입·보류 근거 숫자, 측정 번호) · 지금 걸린 개선 후보 · 바꾸려면. 대장 하나가 기준(backend/service/eval/techniques.py).
 */

const CAND_STATUS: Record<string, string> = { proposed: '제안', approved: '실험 중', held: '나중에', rejected: '안 하기', adopted: '운영 반영' };

export function TechniqueCard({ t }: { t: Technique }) {
  const off = t.values.some((v) => v.value === '꺼짐');
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-800 p-4 space-y-2" id={`tech-${t.key}`} data-testid={`technique-${t.key}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-sm font-medium text-slate-100">{t.name}{off && <span className="ml-2 text-xs text-slate-500">(꺼짐)</span>}</p>
        {t.values.length > 0 && (
          <p className="text-xs text-slate-300">
            {t.values.map((v) => (
              <span key={v.label} className="ml-3" title={v.setting ? `설정 키 ${v.setting} — 서버 운영값` : '코드에 정한 값'}>
                {v.label} <b>{v.value}</b>
              </span>
            ))}
          </p>
        )}
      </div>
      <p className="text-xs text-slate-300"><span className="text-slate-500">하는 일 </span>{t.does}</p>
      <div className="text-xs space-y-0.5">
        <p className="text-slate-500">왜 있나 · 효과</p>
        {t.evidence.length === 0 ? <p className="text-slate-500 pl-3">기록된 근거 없음 — 측정해서 남길 대상</p> : t.evidence.map((e) => (
          <p key={e.date + e.note} className="text-slate-300 pl-3">
            {e.date} · {e.note}
            {e.gate_run_id != null && <span className="text-indigo-600 dark:text-indigo-400" title="eval_gate.py --save로 남긴 측정 번호"> (측정 #{e.gate_run_id})</span>}
          </p>
        ))}
      </div>
      {t.candidates.length > 0 && (
        <div className="text-xs space-y-0.5">
          <p className="text-slate-500">지금 걸린 개선 후보</p>
          {t.candidates.map((c) => (
            <p key={c.id} className="text-slate-300 pl-3">{c.title} <span className="text-slate-500">· {CAND_STATUS[c.status] ?? c.status} · 대상 {c.n_target}문항</span></p>
          ))}
        </div>
      )}
      <p className="text-xs text-slate-500" title="값을 바로 바꾸지 않는다 — 리포트의 고칠 방법에서 실험·측정(통과)한 뒤 설정·코드에 반영">
        바꾸려면: 리포트 고칠 방법 → 실험해 보기 → 측정 통과 → {t.values.some((v) => v.setting) ? `설정 ${t.values.filter((v) => v.setting).map((v) => v.setting).join(', ')}` : '코드'}
        {t.where && <span className="font-mono text-slate-600"> · {t.where}</span>}
      </p>
    </div>
  );
}
