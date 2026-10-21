import type { ReactNode } from 'react';
import { Modal } from './Modal';
import { Button } from './Button';

/**
 * 수정·입력 모달 공통 모양(10/21 — "수정 기능은 어지간하면 모달로"). 제목 · 입력 칸 · [취소][저장].
 * 저장이 실패하면 모달 안에 오류를 보이고 닫지 않는다(사용자가 실패를 놓치지 않게). Esc·바깥 클릭은 저장하지 않고 닫는다.
 * 그 자리에서 바로 고치던 칸(포커스가 빠지면 저장)은 실수로 저장되는 일이 잦아 쓰지 않는다.
 */
export function FormModal({
  isOpen, title, onClose, onSubmit, submitLabel = '저장', submitting = false, submitDisabled = false,
  error, danger = false, maxWidth = 'max-w-md', children,
}: {
  isOpen: boolean;
  title: string;
  onClose: () => void;
  onSubmit: () => void;
  submitLabel?: string;
  submitting?: boolean;
  submitDisabled?: boolean;
  error?: string | null;
  danger?: boolean;
  maxWidth?: string;
  children: ReactNode;
}) {
  return (
    <Modal isOpen={isOpen} onClose={() => { if (!submitting) onClose(); }} title={title} maxWidth={maxWidth}>
      <form
        className="space-y-4"
        onSubmit={(e) => { e.preventDefault(); if (!submitting && !submitDisabled) onSubmit(); }}
      >
        {children}
        {error && <p role="alert" className="text-xs text-rose-600 dark:text-rose-400">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="secondary" size="sm" onClick={onClose} disabled={submitting}>취소</Button>
          <Button type="submit" variant={danger ? 'danger' : 'primary'} size="sm" loading={submitting} disabled={submitDisabled || submitting}>
            {submitLabel}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
