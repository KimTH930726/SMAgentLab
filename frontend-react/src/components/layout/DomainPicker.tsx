import { useMemo, useState } from 'react';
import { ArrowLeftRight } from 'lucide-react';
import { clsx } from 'clsx';
import { Modal } from '../ui/Modal';
import type { NamespaceDetail, Part } from '../../types';
import { byContentThenName, isPickableDomain } from '../../utils/selectedDomain';
import { effectiveOwnerParts, ownerNames } from '../../utils/domainAccess';

type Tier = 'mine' | 'team' | 'other';

/**
 * 보는 컬렉션 고르기(10/22, 컬렉션 설계 §15-11) — 사이드바 칩 + 고르는 창. 타이핑 없이 누르기만(사용자 10/22 "주관식 별로").
 * 묶음 = 내 파트 담당 > 우리 팀(다른 파트) 담당 > 다른 팀·담당 없음(사용자 10/22). 묶음마다 모양을 달리하고, 마지막 묶음은 내용 있는 업무 먼저·빈 업무는 흐리게.
 * 담당 팀은 파트 목록(/auth/parts의 team)으로 안다. 고르는 건 업무 하나뿐 — 조직(팀·파트) 컬렉션은 고르지 않는다. 테스트용 컬렉션은 안 보인다.
 */
export function DomainPicker({ value, onChange, details, parts, userPart }: {
  value: string;
  onChange: (name: string) => void;
  details: NamespaceDetail[];
  parts: Part[];
  userPart?: string | null;
}) {
  const [open, setOpen] = useState(false);
  const { groups, myTeam } = useMemo(() => {
    const teamOf = new Map(parts.map((p) => [p.name, p.team ?? null]));
    const myTeam = userPart ? teamOf.get(userPart) ?? null : null;
    // 담당 N개(10/22): 내 파트가 담당 중 하나면 '내 담당', 담당 중 하나라도 우리 팀 파트면 '우리 팀'
    const tier = (d: NamespaceDetail): Tier => {
      const owners = effectiveOwnerParts(d).map((o) => o.name);
      if (userPart && owners.includes(userPart)) return 'mine';
      if (myTeam && owners.some((o) => teamOf.get(o) === myTeam)) return 'team';
      return 'other';
    };
    const pickable = details.filter(isPickableDomain);
    const by = (t: Tier) => pickable.filter((d) => tier(d) === t);
    return {
      myTeam,
      groups: {
        mine: by('mine').sort(byContentThenName),
        team: by('team').sort(byContentThenName),
        other: by('other').sort(byContentThenName),
      },
    };
  }, [details, parts, userPart]);

  const choose = (name: string) => {
    onChange(name);
    setOpen(false);
  };

  const card = (d: NamespaceDetail, t: Tier) => {
    const empty = (d.policy_count ?? 0) + d.knowledge_count === 0;
    const current = d.name === value;
    const ownerName = ownerNames(effectiveOwnerParts(d));
    return (
      <button
        key={d.name}
        type="button"
        onClick={() => choose(d.name)}
        aria-current={current ? 'true' : undefined}
        className={clsx(
          'text-left min-h-[56px] px-3 py-2 rounded-lg border transition-colors',
          t === 'mine' ? 'bg-indigo-50 hover:bg-indigo-100 dark:bg-indigo-950/40 dark:hover:bg-indigo-900/40' : 'bg-slate-900 hover:bg-slate-800',
          current ? 'border-indigo-500 dark:border-indigo-400 ring-1 ring-indigo-500 dark:ring-indigo-400'
            : t === 'mine' ? 'border-indigo-300 dark:border-indigo-700' : t === 'team' ? 'border-slate-600' : 'border-slate-700',
        )}
      >
        <span className={clsx('block text-sm font-medium', empty ? 'text-slate-500' : 'text-slate-100')}>
          {d.name}{current && <span className="ml-1 text-xs text-indigo-600 dark:text-indigo-300">· 지금</span>}
        </span>
        <span className="block text-xs text-slate-500">
          {empty ? '아직 비어 있음' : `정책 ${d.policy_count ?? 0} · 지식 ${d.knowledge_count}`}
          {t === 'team' && ownerName && ` · ${ownerName}`}
        </span>
      </button>
    );
  };

  const section = (t: Tier, title: string, items: NamespaceDetail[]) => items.length > 0 && (
    <section className="space-y-2">
      <h3 className={clsx('text-xs', t === 'mine' ? 'font-medium text-indigo-600 dark:text-indigo-300' : 'text-slate-500')}>{title}</h3>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">{items.map((d) => card(d, t))}</div>
    </section>
  );
  const onlyOthers = groups.mine.length === 0 && groups.team.length === 0;

  return (
    <>
      <span id="sidebar-domain-label" className="text-xs font-medium text-slate-500 block mb-1.5">보는 컬렉션</span>
      <button
        type="button"
        id="sidebar-domain"
        aria-labelledby="sidebar-domain-label sidebar-domain"
        aria-haspopup="dialog"
        onClick={() => setOpen(true)}
        title="다른 컬렉션으로 바꾸기"
        className="w-full flex items-center justify-between gap-2 bg-slate-900 border border-indigo-500 dark:border-indigo-400 rounded-lg px-3 py-2 text-sm text-slate-200 hover:bg-slate-800 transition-colors"
      >
        <span className="truncate font-medium">{value || '컬렉션 없음'}</span>
        <ArrowLeftRight className="w-3.5 h-3.5 flex-shrink-0 text-indigo-500 dark:text-indigo-300" aria-hidden="true" />
      </button>

      <Modal isOpen={open} onClose={() => setOpen(false)} title="어떤 컬렉션을 볼까요?" maxWidth="max-w-2xl">
        <div className="space-y-5">
          {section('mine', `내 파트 담당 · ${userPart ?? ''}`, groups.mine)}
          {section('team', `우리 팀 담당${myTeam ? ` · ${myTeam}` : ''}`, groups.team)}
          {section('other', onlyOthers ? `업무 컬렉션 ${groups.other.length}개` : '다른 팀 · 담당 없음', groups.other)}
        </div>
      </Modal>
    </>
  );
}
