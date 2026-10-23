import { useState } from 'react';
import { ArrowLeftRight } from 'lucide-react';
import type { NamespaceDetail, Part } from '../../types';
import { collectionLabel } from '../../utils/collections';
import { CollectionPickerModal } from './CollectionPickerModal';

/**
 * 보는 컬렉션 고르기(10/22, 컬렉션 설계 §15-11·§15-13) — 사이드바 칩 + 고르는 창(CollectionPickerModal, 지식 등록 넣을 곳 고르기와 같은 창).
 * 조직 컬렉션을 고르면 지식 문서·용어집·채팅이 그 컬렉션 하나(담을 수 없는 메뉴는 메뉴 노출 관리로 빠짐).
 */
export function DomainPicker({ value, onChange, details, parts, userPart }: {
  value: string;
  onChange: (name: string) => void;
  details: NamespaceDetail[];
  parts: Part[];
  userPart?: string | null;
}) {
  const [open, setOpen] = useState(false);
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
        <span className="truncate font-medium">{collectionLabel(value, details) || '컬렉션 없음'}</span>
        <ArrowLeftRight className="w-3.5 h-3.5 flex-shrink-0 text-indigo-500 dark:text-indigo-300" aria-hidden="true" />
      </button>
      <CollectionPickerModal isOpen={open} onClose={() => setOpen(false)} title="어떤 컬렉션을 볼까요?" value={value} onChoose={onChange}
        details={details} parts={parts} userPart={userPart} />
    </>
  );
}
