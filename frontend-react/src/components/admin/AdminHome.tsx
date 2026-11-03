import type { ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { getCorrectionPendingCount } from '../../api/corrections';
import { getKnowledge } from '../../api/knowledge';
import { getNamespaceStats } from '../../api/stats';
import { getBaselineRun, getDecisionsReport, type GateAxis, type GateRunDetail } from '../../api/eval';
import { getLlmUsage } from '../../api/llm';
import { listFeedback } from '../../api/userFeedback';
import { useNamespaceAccess } from '../../utils/useNamespaceAccess';
import { useAdminMenu } from '../../utils/useAdminMenu';
import { collectionLabel, isSystemCollection } from '../../utils/collections';
import { effectiveOwnerParts, ownerNames } from '../../utils/domainAccess';
import { adminPath, type AdminMenuId } from '../../pages/adminMenu';
import { useImportTodoState } from './TeamImportPanel';
import { useAuthStore } from '../../store/useAuthStore';

/**
 * 두 홈(11/03 admin-audience-split-design §3) — 카드 = 큰 숫자 1개 + [자세히](그 화면으로). 숫자는 지금 API를 그대로 쓴다(새 집계 없음).
 * 컬렉션 관리 › 홈: 보는 컬렉션의 할 일. 플랫폼 관리 › 홈: OpsLens 상태(관리자).
 * "중복 의심" 카드는 컬렉션별 집계 API가 없어 1단계에서 뺐다(설계 §3 — check_ingest 중복 검사를 컬렉션별로 만들 때 추가).
 */

type Tone = 'bad' | 'warn' | 'ok' | 'none';
const TONE: Record<Tone, string> = {
  bad: 'border-l-rose-500 dark:border-l-rose-400',
  warn: 'border-l-amber-500 dark:border-l-amber-400',
  ok: 'border-l-emerald-500 dark:border-l-emerald-400',
  none: 'border-l-slate-700',
};

interface CardProps {
  title: string;
  value: string | number | null | undefined;   // null/undefined = 아직 못 읽음
  unit?: string;
  sub?: string;
  tip: string;                // 배경 설명은 툴팁으로
  tone?: Tone;
  to: string;
  loading?: boolean;
  error?: boolean;
  onGo: (to: string) => void;
  testid: string;
}

function Card({ title, value, unit, sub, tip, tone = 'none', to, loading, error, onGo, testid }: CardProps) {
  return (
    <div data-testid={testid} title={tip}
      className={clsx('min-w-0 rounded-xl border border-slate-700 border-l-4 bg-slate-800 p-4 flex flex-col gap-1.5', TONE[error ? 'none' : tone])}>
      <h3 className="text-xs font-medium text-slate-400">{title}</h3>
      <div className="font-mono text-3xl font-semibold leading-tight tabular-nums text-slate-100" data-testid={`${testid}-value`}>
        {loading ? <span className="text-slate-500">…</span> : error || value == null ? <span className="text-slate-500">—</span> : value}
        {!loading && !error && value != null && unit && <small className="ml-1 text-sm font-medium text-slate-400">{unit}</small>}
      </div>
      <p className={clsx('text-xs', error ? 'text-rose-600 dark:text-rose-400' : 'text-slate-400')}>
        {error ? '불러오지 못함' : sub ?? ' '}
      </p>
      <button onClick={() => onGo(to)} aria-label={`${title} 자세히`} className="self-start text-xs font-semibold text-indigo-600 hover:underline dark:text-indigo-300">
        [자세히]
      </button>
    </div>
  );
}

const Grid = ({ children, testid }: { children: ReactNode; testid: string }) => (
  <div data-testid={testid} className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">{children}</div>
);

const pct1 = (x: number) => (x * 100).toFixed(1);

// ── 컬렉션 관리 › 홈 ─────────────────────────────────────────────────────────

export function CollectionHome({ onGo }: { onGo: (to: string) => void }) {
  const { selectedNs, nsDetails, nsLoading, nsError, canModifyNs } = useNamespaceAccess();
  const { groups } = useAdminMenu();
  const visible = new Set(groups.flatMap((g) => g.items.map((it) => it.id)));
  const shown = (id: AdminMenuId) => visible.has(id);   // 메뉴 노출에서 끈 화면의 카드는 안 보인다
  const detail = nsDetails.find((d) => d.name === selectedNs);
  const isSystem = isSystemCollection(detail);
  const owner = ownerNames(effectiveOwnerParts(detail));

  // 정정 대기 — 사이드바 배지와 같은 쿼리(처리 권한이 있을 때). 권한이 없으면 컬렉션 목록의 대기 수(조회만)
  const corr = useQuery({
    queryKey: ['corrections-pending-count', selectedNs],
    queryFn: () => getCorrectionPendingCount(selectedNs),
    enabled: !!selectedNs && canModifyNs,
    staleTime: 30_000,
  });
  // 권한이 없으면 컬렉션 목록 값 — 목록을 읽는 중·실패면 0이 아니라 "…"·"불러오지 못함"(리뷰 11/03)
  const corrN = canModifyNs ? corr.data?.by_namespace[selectedNs] ?? (corr.data ? 0 : undefined) : detail ? detail.pending_corrections ?? 0 : undefined;
  const corrLoading = canModifyNs ? corr.isLoading : nsLoading;
  const corrError = canModifyNs ? corr.isError : nsError;
  const pending = useQuery({   // 운영 지식 › 승인 대기와 같은 쿼리
    queryKey: ['knowledge', selectedNs, 'pending_review'],
    queryFn: () => getKnowledge(selectedNs, 'pending_review'),
    enabled: !!selectedNs,
    staleTime: 10_000,
  });
  const stats = useQuery({ queryKey: ['stats-ns', selectedNs], queryFn: () => getNamespaceStats(selectedNs), enabled: !!selectedNs, staleTime: 10_000 });
  // 서버 no_knowledge = 미해결 공백, filled = 해결된 공백 — 겹치지 않는다(service/admin/router.py _OPEN_GAP·_FILLED_GAP)
  const gap = stats.data ? stats.data.no_knowledge : null;
  const imp = useImportTodoState();
  const checks = detail ? detail.pending_source_checks ?? 0 : undefined;

  if (!selectedNs) return <p className="text-sm text-slate-400 py-10 text-center">보는 컬렉션을 먼저 골라 주세요.</p>;

  return (
    <div className="space-y-4 max-w-5xl" data-testid="collection-home">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold text-slate-100">{collectionLabel(selectedNs, nsDetails)} · 데이터 품질</h2>
        <span className={clsx('rounded-full px-2 py-0.5 text-xs',
          canModifyNs ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300' : 'bg-slate-700 text-slate-300')}
          title="이 컬렉션을 수정할 수 있는 파트(없으면 관리자만)">
          {canModifyNs ? '처리 가능' : '조회만'} · 담당 {owner || '없음(관리자만)'}
        </span>
      </div>
      <Grid testid="collection-home-cards">
        {shown('q.corrections') && (
          <Card testid="card-corrections" title="정정 요청" value={corrN} unit="건 대기" tone={(corrN ?? 0) > 0 ? 'bad' : 'ok'}
            loading={corrLoading} error={corrError}
            tip="채팅 [답변 틀림]·평가 게이트·정책서 수정 제안 중 처리 전인 것 — 승인해야 검색에 반영"
            to={adminPath('q.corrections')} onGo={onGo} />
        )}
        <Card testid="card-pending" title="승인 대기" value={pending.data?.length} tone={(pending.data?.length ?? 0) > 0 ? 'warn' : 'ok'}
          loading={pending.isLoading} error={pending.isError && !pending.data} sub="유사 지식 · 담당 아닌 사람이 등록"
          tip="기존 지식과 많이 겹치거나 담당이 아닌 사람이 넣어 아직 검색에 안 쓰이는 지식 — 승인 · 병합 · 반려"
          to={`${adminPath('k.docs')}?tab=review`} onGo={onGo} />
        {shown('k.sources') && (
          <Card testid="card-import" title="적재 검토" value={imp.n} tone={imp.n > 0 ? 'warn' : 'ok'} sub="팀 단위 · 연결할 묶음"
            loading={imp.isLoading} error={imp.isError}
            tip="컨플 공간 적재 미리보기에서 사람이 연결할 묶음 수 — 팀 단위(보는 컬렉션과 무관)"
            to={adminPath('k.sources')} onGo={onGo} />
        )}
        {shown('q.stats') && (
          <Card testid="card-gap" title="지식 공백" value={gap} tone={(gap ?? 0) > 0 ? 'warn' : 'none'}
            loading={stats.isLoading} error={stats.isError && !stats.data}
            sub={stats.data ? `미해결 ${stats.data.no_knowledge} · 해결 ${stats.data.filled}` : undefined}
            tip={'"관련 지식을 찾지 못했습니다"가 뜬 질의 중 아직 지식·정정으로 채우지 않은 것(전체 기간)'}
            to={adminPath('q.stats')} onGo={onGo} />
        )}
        {!isSystem && shown('k.policy') && (
          <Card testid="card-checks" title="검토 대기 · 정책서" value={checks}
            tone={(checks ?? 0) > 0 ? 'warn' : 'ok'} sub="원문 확인" loading={nsLoading} error={nsError}
            tip="처리 전 정책서 원문 확인 — 원문에 대한 질문·문제(담당자 확인)"
            to={`${adminPath('k.policy')}?tab=checks`} onGo={onGo} />
        )}
      </Grid>
    </div>
  );
}

// ── 플랫폼 관리 › 홈 ─────────────────────────────────────────────────────────

const FLOOR: Record<GateAxis, number> = { knowledge: 0.8, policy: 0.85 };   // 정확도 게이트 하한(CLAUDE.md 10/30)
const CW_MAX = 0.1;                                                          // 오픈 기준 단정 오답 ≤10%(10/30)
const confidentWrong = (b: GateRunDetail | null | undefined) => {
  const a = b?.summary.answers;
  return a && a.n > 0 ? (a.labels['오답'] ?? 0) / a.n : null;
};

export function PlatformHome({ onGo }: { onGo: (to: string) => void }) {
  const base = {
    knowledge: useQuery({ queryKey: ['eval-gate-baseline', 'knowledge'], queryFn: () => getBaselineRun('knowledge'), staleTime: 30_000 }),
    policy: useQuery({ queryKey: ['eval-gate-baseline', 'policy'], queryFn: () => getBaselineRun('policy'), staleTime: 30_000 }),
  };
  const dec = useQuery({ queryKey: ['home-decisions', 7], queryFn: () => getDecisionsReport(7), staleTime: 60_000 });
  const llm = useQuery({ queryKey: ['llm-usage', 7], queryFn: () => getLlmUsage(7), staleTime: 60_000 });
  const fb = useQuery({ queryKey: ['user-feedback', 'new'], queryFn: () => listFeedback('new'), staleTime: 60_000 });
  const { nsDetails, nsLoading, nsError } = useNamespaceAccess();

  const acc = (axis: GateAxis) => base[axis].data?.summary.answers?.accuracy ?? null;
  const cwK = confidentWrong(base.knowledge.data);
  const cwP = confidentWrong(base.policy.data);
  const kinds = Object.values(dec.data?.kinds ?? {});
  const decided = kinds.reduce((a, k) => a + k.decided, 0);
  const changed = kinds.reduce((a, k) => a + k.changed, 0);
  const calls = (llm.data?.features ?? []).reduce((a, f) => a + f.calls, 0);
  const failed = (llm.data?.features ?? []).reduce((a, f) => a + f.failed, 0);
  const collections = nsDetails.filter((d) => (d.kind ?? 'domain') === 'domain');
  const unowned = collections.filter((d) => effectiveOwnerParts(d).length === 0);
  const unownedSys = unowned.filter((d) => isSystemCollection(d)).length;

  const accCard = (axis: GateAxis, label: string) => {
    const a = acc(axis);
    const b = base[axis].data;
    return (
      <Card testid={`card-acc-${axis}`} title={`정답률 · ${label}`} value={a == null ? null : pct1(a)} unit="%"
        tone={a == null ? 'none' : a >= FLOOR[axis] ? 'ok' : 'warn'} loading={base[axis].isLoading} error={base[axis].isError && !base[axis].data}
        sub={b ? `기준선 #${b.id} · 하한 ${FLOOR[axis] * 100}% ${a != null && a >= FLOOR[axis] ? '통과' : '미달'}` : '기준선 없음'}
        tip={`평가 게이트 ${label} 축의 현재 기준선 측정 — 골든셋 답 정답률(거절·부분은 정답 아님)`}
        to={adminPath('q.eval')} onGo={onGo} />
    );
  };

  return (
    <div className="space-y-4 max-w-5xl" data-testid="platform-home">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold text-slate-100">OpsLens 상태</h2>
        <span className="rounded-full bg-slate-700 px-2 py-0.5 text-xs text-slate-300" title="정답률·단정 오답은 축마다 현재 기준선 측정">
          기준선 {base.knowledge.data ? `#${base.knowledge.data.id}` : '—'} · {base.policy.data ? `#${base.policy.data.id}` : '—'}
        </span>
      </div>
      <Grid testid="platform-home-cards">
        {accCard('knowledge', '업무지식')}
        {accCard('policy', '정책서')}
        <Card testid="card-confident-wrong" title="단정 오답 · 업무지식" value={cwK == null ? null : pct1(cwK)} unit="%"
          tone={cwK == null ? 'none' : cwK <= CW_MAX ? 'ok' : 'bad'} loading={base.knowledge.isLoading} error={base.knowledge.isError && !base.knowledge.data}
          sub={`오픈 기준 ${CW_MAX * 100}% 이하${cwP != null ? ` · 정책서 ${pct1(cwP)}%` : ''}`}
          tip="틀린 답을 단정한 비율(채점 '오답' / 전체) — 모른다고 한 것(거절)은 빼지 않음. 기준선 측정"
          to={adminPath('q.eval')} onGo={onGo} />
        <Card testid="card-overrides" title="AI 판단 변경률" value={dec.data ? (decided > 0 ? pct1(changed / decided) : 0) : null}
          unit="% / 7일" loading={dec.isLoading} error={dec.isError && !dec.data}
          sub={dec.data ? `사람이 확정 ${decided}건 중 바꿈 ${changed}건` : undefined}
          tip="AI가 정한 것(등록 위치·되묻기·원문 확인·컬렉션 제안 등)을 사람이 확정할 때 바꾼 비율 — 최근 7일"
          to={adminPath('q.decisions')} onGo={onGo} />
        <Card testid="card-llm" title="LLM 호출" value={llm.data ? calls.toLocaleString() : null} unit="/ 7일"
          tone={failed > 0 ? 'warn' : 'none'} loading={llm.isLoading} error={llm.isError && !llm.data}
          sub={llm.data ? `실패 ${failed.toLocaleString()}건 · 기능 ${llm.data.features.length}개` : undefined}
          tip="사내 LLM 호출 수(기능별 합계) — 최근 7일. 자세히는 설정 › LLM 프로바이더 › 사용량"
          to={`${adminPath('sys.settings')}?tab=provider`} onGo={onGo} />
        <Card testid="card-feedback" title="피드백 미처리" value={fb.data?.counts.new} tone={(fb.data?.counts.new ?? 0) > 0 ? 'warn' : 'ok'}
          loading={fb.isLoading} error={fb.isError && !fb.data}
          sub={fb.data ? `선정 ${fb.data.counts.planned ?? 0} · 진행 중 ${fb.data.counts.in_progress ?? 0}` : undefined}
          tip="사용자 피드백(오류 신고·기능 제안) 중 아직 선정·기각 등을 정하지 않은 신규 건"
          to={adminPath('q.feedback')} onGo={onGo} />
        <Card testid="card-unowned" title="담당 없는 컬렉션" value={unowned.length} tone={unowned.length > 0 ? 'warn' : 'ok'}
          sub={`정책 ${unowned.length - unownedSys} · 시스템 ${unownedSys} / 전체 ${collections.length}`} loading={nsLoading} error={nsError}
          tip="담당 파트가 없는 컬렉션 — 관리자만 수정·승인할 수 있어 정정·승인 대기가 쌓인다. 조직·권한 › 담당 컬렉션에서 지정"
          to={`${adminPath('org')}?tab=domains`} onGo={onGo} />
      </Grid>
    </div>
  );
}

// ── 컬렉션 관리 › 컬렉션 정보 ─────────────────────────────────────────────────
// 보는 컬렉션 한 줄만 읽기 전용(리뷰 11/03) — 만들기·이름 수정·삭제는 플랫폼 관리 › 컬렉션(관리자)

export function CollectionInfo({ onGo }: { onGo: (to: string) => void }) {
  const { selectedNs, nsDetails, nsLoading, nsError } = useNamespaceAccess();
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin');
  if (nsLoading) return <p className="text-sm text-slate-500 py-6">불러오는 중…</p>;
  if (nsError) return <p className="text-sm text-rose-600 dark:text-rose-400 py-6">컬렉션 정보를 불러오지 못함</p>;
  const d = nsDetails.find((n) => n.name === selectedNs);
  if (!d) return <p className="text-sm text-slate-400 py-10 text-center">보는 컬렉션을 먼저 골라 주세요.</p>;
  const isSystem = isSystemCollection(d);
  const owners = effectiveOwnerParts(d);
  // 관련 — 시스템은 지원하는 정책(supports), 정책은 그 정책을 지원하는 시스템들
  const related = isSystem ? d.supports ?? []
    : nsDetails.filter((s) => isSystemCollection(s) && (s.supports ?? []).includes(d.name)).map((s) => collectionLabel(s.name, nsDetails));
  const manage = isSystem ? 'systems' : 'domains';
  const rows: [string, ReactNode, string?][] = [
    ['종류', isSystem ? '시스템 컬렉션' : '정책 컬렉션'],
    ['코드', d.code ?? '—', '업무 코드 — 바뀌지 않는 키(이름은 화면용)'],
    ['담당 파트', owners.length ? `${ownerNames(owners)}${d.owner_inherited ? ' (상위에서 물려받음)' : ''}` : '없음(관리자만 수정)',
      '이 컬렉션의 지식·정정을 처리할 수 있는 파트'],
    [isSystem ? '관련 정책' : '관련 시스템', related.length ? related.join(' · ') : '—'],
    ['운영 지식', d.knowledge_count.toLocaleString()],
    ['용어', d.glossary_count.toLocaleString()],
    ...(!isSystem ? [['정책', (d.policy_count ?? 0).toLocaleString(), '지금 쓰이는 정책'] as [string, ReactNode, string]] : []),
  ];
  return (
    <div className="space-y-4 max-w-3xl" data-testid="collection-info">
      <h2 className="text-lg font-semibold text-slate-100">{collectionLabel(d.name, nsDetails)}</h2>
      <dl className="rounded-xl border border-slate-700 bg-slate-800 divide-y divide-slate-700">
        {rows.map(([k, v, tip]) => (
          <div key={k} className="grid grid-cols-[8rem_minmax(0,1fr)] gap-3 px-4 py-2.5 text-sm" title={tip}>
            <dt className="text-slate-400">{k}</dt>
            <dd className="text-slate-200 break-words" data-testid={`info-${k}`}>{v}</dd>
          </div>
        ))}
      </dl>
      <p className="text-xs text-slate-400" data-testid="collection-info-manage">
        수정은 플랫폼 관리 › 컬렉션{isAdmin ? <> — <button onClick={() => onGo(adminPath(manage))}
          className="font-semibold text-indigo-600 hover:underline dark:text-indigo-300">{isSystem ? '시스템 컬렉션' : '정책 컬렉션'}에서 수정 →</button></> : '(관리자)'}
      </p>
    </div>
  );
}
