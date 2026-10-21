import { useQuery } from '@tanstack/react-query';
import { Building2, Layers, User as UserIcon } from 'lucide-react';
import { Modal } from '../ui/Modal';
import { Badge } from '../ui/Badge';
import { getUsers } from '../../api/auth';
import { getNamespacesDetail } from '../../api/namespaces';
import type { Part } from '../../types';

/**
 * 파트 현황 트리(10/21, 사용자 요청 — 조직은 트리가 직관적): 팀 › 파트 › 사람 · 담당 업무 영역을 한눈에. 보기 전용 —
 * 사람 이동·승인은 조직 › 사용자, 담당 변경은 조직 › 담당 업무 영역에서.
 */
export function PartTreeModal({ part, onClose }: { part: Part | null; onClose: () => void }) {
  const usersQ = useQuery({ queryKey: ['users'], queryFn: getUsers, enabled: !!part });
  const domainsQ = useQuery({ queryKey: ['namespaces-detail'], queryFn: getNamespacesDetail, enabled: !!part });
  const users = usersQ.data ?? [];
  const domains = domainsQ.data ?? [];
  if (!part) return <Modal isOpen={false} onClose={onClose}>{null}</Modal>;
  const members = users.filter((u) => u.part === part.name);
  const pending = members.filter((u) => u.role === 'viewer').length;
  const owned = domains.filter((d) => d.owner_part_id === part.id).map((d) => d.name);

  return (
    <Modal isOpen onClose={onClose} title={`${part.name} 현황`} maxWidth="max-w-lg">
      {(usersQ.isLoading || domainsQ.isLoading) ? (
        <p className="text-sm text-slate-500 animate-pulse py-4">불러오는 중…</p>
      ) : (usersQ.isError || domainsQ.isError) ? (
        <p role="alert" className="text-sm text-rose-600 dark:text-rose-400 py-4">현황을 불러오지 못했어요 — 창을 닫고 다시 열어 주세요.</p>
      ) : (
      <div className="text-sm space-y-1" data-testid="part-tree">
        <div className="flex items-center gap-2 text-slate-400">
          <Building2 className="w-4 h-4" />{part.team ?? '팀 없음'}
        </div>
        <div className="ml-5 border-l border-slate-700 pl-4 space-y-3 py-1">
          <div className="font-semibold text-slate-100">{part.name}</div>
          <div className="ml-3 border-l border-slate-700 pl-4 space-y-3">
            <div>
              <div className="flex items-center gap-2 text-xs text-slate-400 mb-1">
                <UserIcon className="w-3.5 h-3.5" />사람 {members.length}명{pending > 0 && ` · 승인 대기 ${pending}`}
              </div>
              {members.length ? (
                <ul className="space-y-1">
                  {members.map((u) => (
                    <li key={u.id} className="flex items-center gap-2">
                      <span className="text-slate-200 truncate max-w-[14rem]" title={u.username}>{u.username}</span>
                      {u.role === 'admin' && <Badge color="indigo">슈퍼어드민</Badge>}
                      {u.role === 'viewer' && <Badge color="slate">승인 대기</Badge>}
                      {!u.is_active && <Badge color="slate">비활성</Badge>}
                    </li>
                  ))}
                </ul>
              ) : <p className="text-xs text-slate-500">아직 없어요</p>}
            </div>
            <div>
              <div className="flex items-center gap-2 text-xs text-slate-400 mb-1">
                <Layers className="w-3.5 h-3.5" />담당 업무 영역 {owned.length}개
              </div>
              {owned.length ? (
                <div className="flex flex-wrap gap-1">{owned.map((d) => <Badge key={d} color="emerald">{d}</Badge>)}</div>
              ) : <p className="text-xs text-slate-500">없음 — 조직 › 담당 업무 영역에서 지정해요</p>}
            </div>
          </div>
        </div>
      </div>
      )}
    </Modal>
  );
}
