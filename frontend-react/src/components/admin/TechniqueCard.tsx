import { clsx } from 'clsx';
import type { Technique } from '../../api/eval';

/**
 * 기법 한 장(v2.171 품질 › 답 기법에서 옮김) — 리포트의 고칠 방법·적용 이력에서 "이 기법은 무엇인가".
 * 10/27 가독성(목업 ③): 이름 + 상태 배지 · 하는 일 · 지금 값 상자(값 크게, 설정 키 작게) · 측정 기록 타임라인 · 걸린 후보 배지 · 바꾸려면(코드 위치 작게).
 * 대장 하나가 기준(backend/service/eval/techniques.py).
 */

const CAND_STATUS: Record<string, { label: string; cls: string }> = {
  proposed: { label: '제안', cls: 'bg-slate-700 text-slate-300' },
  approved: { label: '실험 중', cls: 'bg-indigo-100 text-indigo-700 dark:bg-indigo-500/20 dark:text-indigo-300' },
  held: { label: '보류', cls: 'bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300' },   // 개선 후보 상태 이름과 같게(10/30)
  rejected: { label: '기각', cls: 'bg-slate-700 text-slate-400' },
  adopted: { label: '반영', cls: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300' },
  failed: { label: '실패', cls: 'bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300' },
};
// 꺼짐·안 함이 값에 적힌 항목만 — 나머지는 '쓰는 중'이라 단정하지 않는다(적용 이력 항목 전체와 같은 규칙)
const offOf = (t: Technique) => t.values.some((v) => v.value === '꺼짐' || v.value.includes('기본 꺼짐') || v.value.startsWith('안 함'));

/** showName = 이름 줄을 보일까 — 창 제목에 이미 이름이 있으면 false(적용 이력의 설명 창) */
export function TechniqueCard({ t, showName = true }: { t: Technique; showName?: boolean }) {
  const off = offOf(t);
  const settings = t.values.filter((v) => v.setting).map((v) => v.setting);
  return (
    <div className="space-y-4" id={`tech-${t.key}`} data-testid={`technique-${t.key}`}>
      <div className="flex flex-wrap items-center gap-2">
        {showName && <p className="text-base font-semibold text-slate-100">{t.name}</p>}
        {off && <span className="rounded-full px-2 py-0.5 text-[11px] bg-slate-700 text-slate-300">꺼짐</span>}
        <span className="text-xs text-slate-500">{t.stage} 단계</span>
      </div>

      <div>
        <p className="text-[11px] text-slate-500 mb-0.5">하는 일</p>
        <p className="text-sm text-slate-200 leading-relaxed">{t.does}</p>
      </div>

      {t.values.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          {t.values.map((v) => (
            <div key={v.label} className="rounded-lg border border-slate-700 bg-slate-900/40 px-3 py-2 min-w-0"
              title={v.setting ? `설정 키 ${v.setting} — 서버 운영값` : '코드에 정한 값'}>
              <p className="text-[11px] text-slate-500">{v.label}</p>
              <p className="text-lg font-semibold text-slate-100 break-words">{v.value}</p>
              {v.setting && <p className="text-[11px] font-mono text-slate-500 truncate">{v.setting}</p>}
            </div>
          ))}
        </div>
      )}

      <div>
        <p className="text-[11px] text-slate-500 mb-1">측정 기록 · 왜 있나</p>
        {t.evidence.length === 0 ? <p className="text-xs text-slate-500">기록된 근거 없음 — 측정해서 남길 대상</p> : (
          <ol className="border-l border-slate-700 ml-1.5 space-y-2">
            {t.evidence.map((e) => (
              <li key={e.date + e.note} className="relative pl-4 text-xs">
                <span className="absolute -left-[5px] top-1 h-2.5 w-2.5 rounded-full bg-indigo-500 dark:bg-indigo-400" aria-hidden />
                <span className="text-slate-500 tabular-nums mr-2">{e.date.slice(5).replace('-', '/')}</span>
                <span className="text-slate-200">{e.note}</span>
                {e.gate_run_id != null && <span className="ml-1 text-indigo-600 dark:text-indigo-400" title="eval_gate.py --save로 남긴 측정 번호">#{e.gate_run_id}</span>}
              </li>
            ))}
          </ol>
        )}
      </div>

      {t.candidates.length > 0 && (
        <div className="space-y-1">
          <p className="text-[11px] text-slate-500">걸린 개선 후보</p>
          {t.candidates.map((c) => {
            const st = CAND_STATUS[c.status] ?? { label: c.status, cls: 'bg-slate-700 text-slate-300' };
            return (
              <p key={c.id} className="flex items-center gap-2 text-xs text-slate-200">
                <span className={clsx('shrink-0 rounded-full px-2 py-0.5 text-[11px]', st.cls)}>{st.label}</span>
                <span className="min-w-0">{c.title}</span><span className="shrink-0 text-slate-500">· {c.n_target}문항</span>
              </p>
            );
          })}
        </div>
      )}

      <p className="text-[11px] text-slate-500 border-t border-slate-700 pt-2" title="값을 바로 바꾸지 않는다 — 리포트의 고칠 방법에서 실험·측정(통과)한 뒤 설정·코드에 반영">
        바꾸려면 리포트 고칠 방법 → 실험해 보기 → 측정 통과 → {settings.length ? `설정 ${settings.join(', ')}` : '코드'}
        {t.where && <span className="block font-mono text-slate-600 truncate">{t.where}</span>}
      </p>
    </div>
  );
}
