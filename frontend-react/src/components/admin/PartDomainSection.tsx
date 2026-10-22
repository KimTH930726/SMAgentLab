import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Pencil } from 'lucide-react';
import { Button } from '../ui/Button';
import { Badge } from '../ui/Badge';
import { Modal } from '../ui/Modal';
import { getAllParts, setPartDomains } from '../../api/auth';
import { getNamespacesDetail } from '../../api/namespaces';
import { showConfirm, showError } from '../../store/useDialogStore';
import type { Part } from '../../types';

/**
 * 담당 업무 — 파트(사람 조직)가 어느 업무 컬렉션을 맡는지(2026-10-21, 관리자 화면 설계안 docs/tech/admin-ia-design.md ①).
 * 파트 하나가 여러 영역(1:N), 영역 하나엔 담당 파트 하나. 담당 파트 사람이 그 영역의 지식·정책·용어를 수정하고, 담당 없는 영역은 관리자만.
 * 저장 전에 무엇이 바뀌는지 확인받는다(고르는 순간 저장하던 칸으로 사고 — 10/20).
 */
export function PartDomainSection() {
  const qc = useQueryClient();
  // 파트 이름·팀은 파트 목록에서, 누가 무엇을 맡았는지는 업무 컬렉션 목록(owner_part_id) 하나에서 — 출처가 둘이면 이름 바꾼 직후 어긋났다(리뷰 M1)
  const { data: parts = [], isLoading: partsLoading } = useQuery({ queryKey: ['parts-all'], queryFn: getAllParts });
  const { data: domains = [], isLoading: nsLoading } = useQuery({ queryKey: ['namespaces-detail'], queryFn: getNamespacesDetail });
  const [editing, setEditing] = useState<Part | null>(null);
  const [checked, setChecked] = useState<Set<string>>(new Set());

  const partName = useMemo(() => new Map(parts.map((p) => [p.id, p.name])), [parts]);
  const domainsOf = (partId: number, list = domains) => list.filter((d) => d.owner_part_id === partId).map((d) => d.name);
  const unowned = domains.filter((d) => !d.owner_part_id).map((d) => d.name);

  const saveMutation = useMutation({
    mutationFn: ({ part, names }: { part: Part; names: string[] }) => setPartDomains(part.id, names),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['parts-all'] });
      qc.invalidateQueries({ queryKey: ['namespaces-detail'] });   // 수정 권한 표시(canEditDomain)가 이 값을 본다
      setEditing(null);
    },
    onError: (err: Error) => { void showError(err, '담당 업무 저장 실패'); },
  });

  const openEdit = (p: Part) => {
    setEditing(p);
    setChecked(new Set(domainsOf(p.id)));
  };

  const toggle = (name: string) => setChecked((prev) => {
    const next = new Set(prev);
    if (next.has(name)) next.delete(name); else next.add(name);
    return next;
  });

  const save = async () => {
    if (!editing || saveMutation.isPending) return;
    // 저장 직전 최신 담당을 다시 읽어 확인 창을 만든다 — 창을 연 뒤 다른 관리자가 바꾼 것이 조용히 되돌려지지 않게(리뷰 L1)
    const fresh = await qc.fetchQuery({ queryKey: ['namespaces-detail'], queryFn: getNamespacesDetail, staleTime: 0 });
    const before = new Set(domainsOf(editing.id, fresh));
    const added = [...checked].filter((n) => !before.has(n));
    const removed = [...before].filter((n) => !checked.has(n));
    if (added.length === 0 && removed.length === 0) { setEditing(null); return; }
    const freshOwner = new Map(fresh.map((d) => [d.name, d.owner_part_id ?? null]));
    const moved = added.filter((n) => freshOwner.get(n) && freshOwner.get(n) !== editing.id);
    const lines = [
      added.length ? `맡게 됨: ${added.join(', ')}` : '',
      moved.length ? `  └ 다른 파트에서 옮겨 옴: ${moved.map((n) => `${n}(${partName.get(freshOwner.get(n) as number) ?? '다른 파트'})`).join(', ')}` : '',
      removed.length ? `맡지 않음: ${removed.join(', ')} — 담당이 없어지면 관리자만 수정할 수 있어요` : '',
      '',
      `'${editing.name}' 사람이 맡은 업무 컬렉션의 지식·정책·용어를 수정하게 됩니다.`,
    ].filter((l, i) => l || i === 3);
    if (!(await showConfirm(lines.join('\n'), { tone: 'warning', confirmLabel: '저장', title: `${editing.name} 담당 업무` }))) return;
    saveMutation.mutate({ part: editing, names: [...checked] });
  };

  if (partsLoading || nsLoading) return <div className="text-center py-10 text-slate-500 animate-pulse">로딩 중...</div>;

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-slate-200">담당 업무</h2>
        <p className="text-sm text-slate-400 mt-1" title="파트 하나가 여러 업무 컬렉션을 맡을 수 있고, 업무 컬렉션 하나의 담당 파트는 하나입니다.">
          파트가 맡은 업무 컬렉션은 그 파트 사람이 수정할 수 있어요. 담당이 없으면 관리자만 수정해요.
        </p>
      </div>

      <div className="bg-slate-800 rounded-xl border border-slate-700 overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-700 text-slate-400">
              <th className="text-left px-4 py-3 font-medium w-48">파트</th>
              <th className="text-left px-4 py-3 font-medium">담당 업무</th>
              <th className="text-right px-4 py-3 font-medium w-24"></th>
            </tr>
          </thead>
          <tbody>
            {/* 팀 › 파트 › 담당 업무 트리(10/22) — 파트 목록이 팀 이름 순이라, 팀이 바뀌는 줄 앞에 팀 줄을 끼운다 */}
            {parts.map((p, i) => [
              (i === 0 || parts[i - 1].team !== p.team) && (
                <tr key={`team-${p.team ?? 'none'}`} className="bg-slate-900/40 border-b border-slate-700/50">
                  <td colSpan={3} className="px-4 py-2 text-xs font-semibold text-slate-300">{p.team ?? '팀 없음'}</td>
                </tr>
              ),
              <tr key={p.id} className="border-b border-slate-700/50 align-top">
                <td className="px-4 py-3 pl-8">
                  <div className="text-slate-200">{p.name}</div>
                </td>
                <td className="px-4 py-3">
                  {domainsOf(p.id).length ? (
                    <div className="flex flex-wrap gap-1">{domainsOf(p.id).map((d) => <Badge key={d} color="emerald">{d}</Badge>)}</div>
                  ) : <span className="text-xs text-slate-500">없음</span>}
                </td>
                <td className="px-4 py-3 text-right">
                  <Button variant="secondary" size="sm" onClick={() => openEdit(p)} data-testid={`part-domains-edit-${p.id}`}>
                    <Pencil className="w-3.5 h-3.5" /> 변경
                  </Button>
                </td>
              </tr>,
            ])}
            {/* 마지막 팀 소속처럼 읽히지 않게 팀 줄과 같은 모양의 구분 줄(리뷰) */}
            <tr className="bg-slate-900/40 border-b border-slate-700/50">
              <td colSpan={3} className="px-4 py-2 text-xs font-semibold text-slate-300">담당 파트 없음</td>
            </tr>
            <tr>
              <td className="px-4 py-3 pl-8 text-slate-400">—</td>
              <td className="px-4 py-3" colSpan={2}>
                {unowned.length ? (
                  <div className="flex flex-wrap gap-1 items-center">
                    {unowned.map((d) => <Badge key={d} color="slate">{d}</Badge>)}
                    <span className="text-xs text-slate-500 ml-1">— 관리자만 수정</span>
                  </div>
                ) : <span className="text-xs text-slate-500">없음</span>}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <Modal isOpen={!!editing} onClose={() => setEditing(null)} title={editing ? `${editing.name} — 담당 업무` : ''} maxWidth="max-w-lg">
        {editing && (
          <div className="space-y-4">
            <p className="text-xs text-slate-400">체크한 업무 컬렉션을 이 파트가 맡아요. 다른 파트가 맡던 컬렉션을 체크하면 이 파트로 옮겨져요.</p>
            <div className="max-h-80 overflow-y-auto space-y-1 pr-1">
              {domains.map((d) => {
                const other = d.owner_part_id && d.owner_part_id !== editing.id ? (partName.get(d.owner_part_id) ?? d.owner_part) : null;
                return (
                  <label key={d.name} htmlFor={`pd-${editing.id}-${d.id}`}
                    className="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-slate-700/40 cursor-pointer">
                    <input id={`pd-${editing.id}-${d.id}`} type="checkbox" checked={checked.has(d.name)} onChange={() => toggle(d.name)}
                      className="accent-indigo-500" />
                    <span className="text-sm text-slate-200">{d.name}</span>
                    {other && <span className="text-xs text-amber-700 dark:text-amber-300">{other} 담당</span>}
                  </label>
                );
              })}
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="secondary" size="sm" onClick={() => setEditing(null)}>취소</Button>
              <Button variant="primary" size="sm" onClick={() => void save()} loading={saveMutation.isPending} disabled={saveMutation.isPending}>
                저장
              </Button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
