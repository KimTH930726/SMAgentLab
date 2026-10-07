import { AlertTriangle, CheckCircle, Info, XCircle } from 'lucide-react';
import { Modal } from './Modal';
import { Button } from './Button';
import { useDialogStore, type DialogTone } from '../../store/useDialogStore';

// showAlert/showConfirm 요청을 앱 디자인 모달로 그린다. App 최상단에 한 번만 둔다.

const TONE: Record<DialogTone, { icon: typeof Info; cls: string; title: string }> = {
  info: { icon: Info, cls: 'text-indigo-600 dark:text-indigo-400', title: '안내' },
  success: { icon: CheckCircle, cls: 'text-emerald-600 dark:text-emerald-400', title: '완료' },
  warning: { icon: AlertTriangle, cls: 'text-amber-600 dark:text-amber-400', title: '확인' },
  danger: { icon: XCircle, cls: 'text-rose-600 dark:text-rose-400', title: '오류' },
};

export function DialogHost() {
  const current = useDialogStore((s) => s.queue[0]);
  const close = useDialogStore((s) => s.close);
  if (!current) return <Modal isOpen={false} onClose={() => {}}>{null}</Modal>;
  const tone = TONE[current.tone];
  const Icon = tone.icon;
  const isConfirm = current.kind === 'confirm';
  const danger = isConfirm && current.tone === 'danger';

  return (
    <Modal isOpen onClose={() => close(current.id, false)} maxWidth="max-w-md" zClass="z-[70]">
      <div data-testid="app-dialog" role={isConfirm ? 'alertdialog' : 'dialog'} aria-modal="true" className="flex flex-col gap-5">
        <div className="flex gap-3">
          <Icon className={`w-5 h-5 mt-0.5 flex-shrink-0 ${tone.cls}`} />
          <div className="flex flex-col gap-1.5 min-w-0">
            <h3 className="text-base font-semibold text-slate-100">{current.title ?? tone.title}</h3>
            <p data-testid="app-dialog-message" className="text-sm text-slate-300 whitespace-pre-line break-words leading-relaxed">
              {current.message}
            </p>
          </div>
        </div>
        <div className="flex justify-end gap-2">
          {isConfirm && (
            <Button variant="secondary" size="md" data-testid="app-dialog-cancel" onClick={() => close(current.id, false)}>
              {current.cancelLabel}
            </Button>
          )}
          <Button key={current.id} autoFocus variant={danger ? 'danger' : 'primary'} size="md" data-testid="app-dialog-ok"
            onClick={() => close(current.id, true)}>
            {current.confirmLabel}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
