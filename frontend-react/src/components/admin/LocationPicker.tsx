import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { locateKnowledge } from '../../api/knowledge';
import { getParts } from '../../api/auth';
import { useNamespaceAccess } from '../../utils/useNamespaceAccess';
import { canEditDomain, effectiveOwnerParts } from '../../utils/domainAccess';
import { CollectionPickerModal } from '../layout/CollectionPickerModal';
import type { ChosenLocation, LocateCollection, LocateResult, LocationChoice } from '../../types';

/**
 * 지식 등록 — 넣을 곳 카드(E2, 10/23 목업 승인). 내용을 보고 AI가 넣을 곳을 제안하고(위치 판정 POST /knowledge/locate),
 * 애매할 때만 버튼으로 묻는다. 고른 결과는 onChange로 폼에 넘겨 등록 요청에 그대로 싣는다(decision_id → 원장 같은 줄에 확정).
 * AI가 추천해 미리 골라 두고 사람은 그대로 두거나 다른 걸 고른다 — "잘 모르겠어요"는 뺐다(10/23 사용자: 판단을 미루는 출구).
 * [다른 곳…] = 보는 컬렉션 고르기와 같은 창(CollectionPickerModal, 10/23 사용자 A안) — 담당이 아닌 곳엔 "승인 요청" 표시.
 * - preset(컬렉션 화면의 [지식 등록 →]) = 넣을 곳 확정, AI는 검사만 — 두 신호가 다른 곳으로 확신할 때만 "옮길까요?" 한 번
 * - 담당이 아닌 곳을 고르면 그 담당의 승인 대기로 간다(제안) — 미리 알린다
 * - AI 판단 실패·비교할 지식 없음 = 노란 줄(대체 처리를 보이게)
 */
export function LocationPicker({ text, preset, onChange }: {
  text: string;
  preset?: string | null;
  onChange: (loc: ChosenLocation | null) => void;
}) {
  const { nsDetails, user } = useNamespaceAccess();
  const { data: parts = [] } = useQuery({ queryKey: ['parts-public'], queryFn: getParts, staleTime: 300_000 });
  const [pickerOpen, setPickerOpen] = useState(false);
  const head = text.slice(0, 2500);   // 판정은 앞 2500자만 본다 — 같은 앞부분이면 다시 묻지 않는다
  const { data: r, isLoading, isError, error } = useQuery({
    queryKey: ['locate', head, preset ?? null],
    queryFn: () => locateKnowledge(head, preset),
    enabled: head.trim().length > 0,
    staleTime: Infinity,
    gcTime: 10 * 60_000,
    retry: false,
  });

  // 창에서 직접 고르기용 — 쓸 수 있는지는 지식 문서 화면과 같은 규칙
  const all: LocateCollection[] = useMemo(() => nsDetails
    .filter((d) => d.kind !== 'policy_test')
    .map((d) => ({
      name: d.name, label: d.display_name || d.name, kind: d.kind === 'org_space' ? 'org_space' : 'domain',
      writable: canEditDomain(user, effectiveOwnerParts(d)),
    })), [nsDetails, user]);
  const writableOf = useMemo(() => new Map(all.map((c) => [c.name, c.writable])), [all]);

  const [picked, setPicked] = useState<{ c: LocateCollection; choice: LocationChoice } | null>(null);
  // 판정이 오면 기본값 = preset 또는 AI 제안
  useEffect(() => {
    if (!r) { setPicked(null); return; }
    const base = r.preset ?? r.proposed;
    setPicked(base ? { c: base, choice: r.preset ? 'preset' : 'accept' } : null);
  }, [r]);
  const blocked = !r && !!picked && !picked.c.writable;   // 판정 없이 담당 아닌 곳 — 승인 요청에 판정 줄이 필요하다
  useEffect(() => {
    onChange(picked && !blocked ? { namespace: picked.c.name, label: picked.c.label, writable: picked.c.writable,
                                    decisionId: r?.decision_id ?? null, choice: picked.choice } : null);
  }, [picked, r, blocked, onChange]);

  if (!head.trim()) return null;
  if (isLoading) {
    return <Box><p className="text-xs text-slate-400 animate-pulse">AI가 넣을 곳을 보는 중…</p></Box>;
  }

  const select = (
    <>
      <button type="button" onClick={() => setPickerOpen(true)} aria-haspopup="dialog"
        className="text-xs px-2 py-0.5 rounded-md border border-dashed border-slate-600 text-slate-300 hover:border-indigo-500 dark:hover:border-indigo-400">
        다른 곳…
      </button>
      <CollectionPickerModal isOpen={pickerOpen} onClose={() => setPickerOpen(false)} title="어디에 넣을까요?" value={picked?.c.name ?? ''}
        details={nsDetails} parts={parts} userPart={user?.part}
        note={(d) => (writableOf.get(d.name) === false ? '승인 요청' : undefined)}
        onChoose={(name) => {
          const c = all.find((x) => x.name === name);
          if (c) setPicked({ c, choice: 'other' });
        }} />
    </>
  );

  if (isError || !r) {
    // 판정 자체가 안 됨 — 사람이 고르게 하고 노란 줄로 알린다(원장 줄 없음 → 담당인 곳만 넣을 수 있다)
    return (
      <Box>
        <Warn>넣을 곳을 AI가 보지 못했어요{error ? ` — ${(error as Error).message}` : ''}. 직접 골라 주세요.</Warn>
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs text-slate-400">넣을 곳</span>
          {picked ? <Chip c={picked.c} selected /> : <span className="text-xs text-slate-500">없음</span>}
          {select}
        </div>
        {blocked && <p className="text-xs text-amber-700 dark:text-amber-300">AI 판정 없이는 담당 파트인 곳에만 넣을 수 있어요.</p>}
      </Box>
    );
  }

  const isPicked = (c: LocateCollection) => picked?.c.name === c.name;

  return (
    <Box testid="location-picker">
      {r.fallback && <Warn>{r.fallback}</Warn>}
      {r.doc_type !== '지식 문서' && (
        <p className="text-xs text-slate-400" title="정책서는 정책서 메뉴의 가져오기, 시스템 정보는 아직 등록 경로가 없어요">
          {r.doc_type}처럼 보여요 — 그래도 지식 문서로 넣을 수 있어요.
        </p>
      )}

      {r.preset ? (
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs text-slate-400">넣을 곳</span>
          <Chip c={picked?.c ?? r.preset} selected />
          {r.suggest_move && r.ai && !isPicked(r.ai) && (
            <button type="button" onClick={() => setPicked({ c: r.ai!, choice: 'option' })}
              className="text-xs text-indigo-600 hover:text-indigo-500 dark:text-indigo-300 dark:hover:text-indigo-200">
              AI는 '{r.ai.label}' 지식 같다고 봐요 — 그쪽으로
            </button>
          )}
          {select}
        </div>
      ) : (
        <div className="space-y-1.5">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs text-slate-400">{r.ask ? '어디 지식인가요?' : '넣을 곳'}</span>
            {r.ask ? (
              <>
                {r.options.map((c) => (
                  <Chip key={c.name} c={c} selected={isPicked(c)}
                    onClick={() => setPicked({ c, choice: c.name === r.proposed?.name ? 'accept' : 'option' })} />
                ))}
                {picked && !r.options.some(isPicked) && <Chip c={picked.c} selected />}
              </>
            ) : (
              <>
                {picked && <Chip c={picked.c} selected />}
                {r.confident && picked?.choice === 'accept' && <span className="text-[11px] text-emerald-600 dark:text-emerald-400">AI 확신</span>}
              </>
            )}
            {select}
          </div>
        </div>
      )}
      <Reasons r={r} />

      {picked && !picked.c.writable && (
        <p className="text-xs text-amber-700 dark:text-amber-300">
          '{picked.c.label}'의 담당 파트가 아니라 그 담당의 승인 대기로 가요.
        </p>
      )}
    </Box>
  );
}

/** 추천 이유 — 왜 그곳인지 보이게(10/23 사용자: 다른 곳을 고를 때 판단 근거 · 틀렸을 때 자체 개선 재료). 두 신호를 따로 보여 준다. */
function Reasons({ r }: { r: LocateResult }) {
  const top = r.neighbors[0];
  const nb = r.neighbors.length ? r.neighbors.map((n) => `${n.kind === 'org_space' ? `조직 · ${n.label}` : n.label} ${n.n}`).join(' · ') : '없음';
  const verdict = r.preset
    ? (r.suggest_move ? 'AI와 비슷한 지식이 모두 다른 곳을 가리켜요 — 옮길지 골라 주세요' : '컬렉션 화면에서 정한 곳이에요 — AI는 검사만 했어요')
    : r.confident ? 'AI 판단과 비슷한 지식이 같은 곳을 가리켜 확신해요'
      : r.ai && top ? 'AI 판단과 비슷한 지식이 달라요 — 맞는 곳을 골라 주세요'
        : '근거가 약해요 — 맞는 곳을 골라 주세요';
  return (
    <div data-testid="location-reasons" className="rounded-md bg-slate-800/60 px-2.5 py-1.5 text-[11px] text-slate-400 space-y-0.5">
      <p><span className="text-slate-500">AI 판단</span> · {r.ai ? <b className="font-medium text-slate-300">{r.ai.kind === 'org_space' ? `조직 · ${r.ai.label}` : r.ai.label}</b> : '고르지 못함'}{r.reason && ` — ${r.reason}`}</p>
      <p><span className="text-slate-500">비슷한 지식 {r.neighbor_total}건</span> · {nb}</p>
      <p className={r.confident ? 'text-emerald-600 dark:text-emerald-400' : 'text-slate-300'}>{verdict}</p>
    </div>
  );
}

function Box({ children, testid }: { children: React.ReactNode; testid?: string }) {
  return <div data-testid={testid} className="rounded-lg border border-slate-700/60 bg-slate-900/40 px-3 py-2.5 space-y-1.5">{children}</div>;
}

function Warn({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-xs rounded px-2 py-1 bg-amber-50 text-amber-800 border border-amber-200 dark:bg-amber-900/30 dark:text-amber-300 dark:border-amber-700/40">
      {children}
    </p>
  );
}

function Chip({ c, selected, onClick }: { c: LocateCollection; selected?: boolean; onClick?: () => void }) {
  const cls = clsx('text-xs px-2 py-0.5 rounded-md border',
    selected ? 'border-indigo-500 bg-indigo-50 text-indigo-700 dark:border-indigo-400 dark:bg-indigo-950/40 dark:text-indigo-300'
      : 'border-slate-600 text-slate-300 hover:border-indigo-500 dark:hover:border-indigo-400');
  const label = c.kind === 'org_space' ? `조직 · ${c.label}` : c.label;
  if (!onClick) return <span className={cls}>{label}</span>;
  return <button type="button" onClick={onClick} aria-pressed={!!selected} className={cls}>{label}</button>;
}
