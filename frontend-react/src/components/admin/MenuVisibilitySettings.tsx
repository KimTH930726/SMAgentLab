import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Save } from 'lucide-react';
import { getMenuVisibility, setMenuVisibility } from '../../api/menu';
import { ADMIN_MENU, COLLECTION_KINDS, kindScope, supportsKind } from '../../pages/adminMenu';
import { Button } from '../ui/Button';

/**
 * 시스템·권한 › 설정 › 메뉴 노출(10/23 사용자 "영역별로 필요 없는 메뉴는 미노출 — 나중엔 권한별도").
 * 보는 컬렉션에 따라 바뀌는 메뉴(scoped)만 — 종류마다 켜고 끈다. 그 종류가 담을 수 없는 칸은 "—"(설정으로 켤 수 없음).
 * 저장 값은 숨길 것만(범위 kind:<종류>) — 관리자만 바꾼다.
 */
export function MenuVisibilitySettings() {
  const qc = useQueryClient();
  const { data, isLoading, isError } = useQuery({ queryKey: ['menu-visibility'], queryFn: getMenuVisibility });
  const [hidden, setHidden] = useState<Record<string, string[]>>({});
  useEffect(() => { if (data) setHidden(data.hidden); }, [data]);

  const items = ADMIN_MENU.flatMap((g) => g.items.filter((it) => it.scoped).map((it) => ({ group: g.label, it })));
  const isShown = (scope: string, id: string) => !(hidden[scope] ?? []).includes(id);
  const toggle = (scope: string, id: string) => setHidden((prev) => {
    const cur = new Set(prev[scope] ?? []);
    if (cur.has(id)) cur.delete(id); else cur.add(id);
    return { ...prev, [scope]: [...cur].sort() };
  });
  const same = (a: Record<string, string[]>, b: Record<string, string[]>) => {
    const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
    return [...keys].every((k) => (a[k] ?? []).join() === (b[k] ?? []).join());
  };
  const dirty = !!data && !same(hidden, data.hidden);

  const save = useMutation({
    mutationFn: () => setMenuVisibility(hidden),
    onSuccess: (v) => { qc.setQueryData(['menu-visibility'], v); },
    onError: () => { /* 아래 줄에 표시 */ },
  });

  if (isLoading) return <p className="text-sm text-slate-500 py-6">불러오는 중…</p>;
  if (isError) return <p className="text-sm text-rose-600 dark:text-rose-400 py-6">메뉴 노출 설정을 불러오지 못했어요.</p>;

  return (
    <div className="space-y-4" data-testid="menu-visibility">
      <p className="text-xs text-slate-500"
        title="보는 컬렉션 종류에 따라 왼쪽 메뉴에서 빠진다. 끈 메뉴로 가는 화면 안 링크(예: 통계 → 정정 요청)도 운영 지식으로 간다. '—'는 그 종류엔 그 데이터를 둘 수 없어 늘 숨김, 운영 지식은 늘 보임">
        보는 컬렉션 종류마다 왼쪽 메뉴에 보일지 정해요.
      </p>
      <div className="bg-slate-800 rounded-xl border border-slate-700 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-700 text-slate-400">
              <th className="text-left px-4 py-3 font-medium">메뉴</th>
              {COLLECTION_KINDS.map((k) => <th key={k.kind} className="px-4 py-3 font-medium text-center">{k.label}</th>)}
            </tr>
          </thead>
          <tbody>
            {items.map(({ group, it }) => (
              <tr key={it.id} className="border-b border-slate-700/50">
                <td className="px-4 py-2.5 text-slate-200">{group === it.label ? it.label : `${group} › ${it.label}`}</td>
                {COLLECTION_KINDS.map((k) => {
                  const scope = kindScope(k.kind);
                  const id = `mv-${k.kind}-${it.id}`;
                  return (
                    <td key={k.kind} className="px-4 py-2.5 text-center">
                      {supportsKind(it, k.kind) ? (
                        <input id={id} type="checkbox" aria-label={`${k.label}에서 ${it.label} 보이기`} className="accent-indigo-500"
                          title={it.required ? '늘 보임 — 숨긴 메뉴와 등록·보기 링크가 가는 곳' : undefined}
                          checked={it.required || isShown(scope, it.id)} disabled={it.required || save.isPending}
                          onChange={() => toggle(scope, it.id)} />
                      ) : (
                        <span className="text-slate-500" title={`${k.label}엔 ${it.label} 데이터를 둘 수 없어 늘 숨겨요`}>—</span>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex items-center justify-end gap-3">
        {save.isError && <span className="text-xs text-rose-600 dark:text-rose-400">저장 실패 — {(save.error as Error)?.message}</span>}
        {save.isSuccess && !dirty && <span className="text-xs text-emerald-600 dark:text-emerald-400">저장했어요</span>}
        <Button variant="primary" size="sm" onClick={() => save.mutate()} disabled={!dirty || save.isPending}>
          <Save className="w-3.5 h-3.5" /> 저장
        </Button>
      </div>
    </div>
  );
}
