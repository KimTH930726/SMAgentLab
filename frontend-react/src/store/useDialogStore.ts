import { create } from 'zustand';

// 브라우저 기본 alert/confirm 대신 앱 디자인의 모달(DialogHost)로 띄운다(2026-10-07).
// 컴포넌트 밖(useMutation 콜백 등)에서도 부를 수 있게 함수로 내보낸다. 여러 개가 겹치면 차례로.

export type DialogTone = 'info' | 'success' | 'warning' | 'danger';

export interface DialogRequest {
  id: number;
  kind: 'alert' | 'confirm';
  title?: string;
  message: string;
  tone: DialogTone;
  confirmLabel: string;
  cancelLabel: string;
  resolve: (ok: boolean) => void;
}

interface DialogState {
  queue: DialogRequest[];
  push: (req: DialogRequest) => void;
  close: (id: number, ok: boolean) => void;
}

export const useDialogStore = create<DialogState>((set, get) => ({
  queue: [],
  push: (req) => set({ queue: [...get().queue, req] }),
  close: (id, ok) => {
    const req = get().queue.find((r) => r.id === id);
    set({ queue: get().queue.filter((r) => r.id !== id) });
    req?.resolve(ok);
  },
}));

let _seq = 0;

interface DialogOptions {
  title?: string;
  tone?: DialogTone;
  confirmLabel?: string;
  cancelLabel?: string;
}

/** 안내 모달 — 확인을 누르면 resolve. 기다릴 필요 없으면 await 없이 호출. */
export function showAlert(message: string, opts: DialogOptions = {}): Promise<void> {
  return new Promise((resolve) => {
    useDialogStore.getState().push({
      id: ++_seq, kind: 'alert', message, title: opts.title, tone: opts.tone ?? 'info',
      confirmLabel: opts.confirmLabel ?? '확인', cancelLabel: '', resolve: () => resolve(),
    });
  });
}

/** 오류 안내 — useMutation onError 등에서. */
export function showError(err: unknown, fallback = '요청을 처리하지 못했습니다'): Promise<void> {
  const msg = err instanceof Error ? err.message : typeof err === 'string' ? err : '';
  return showAlert(msg || fallback, { title: '오류', tone: 'danger' });
}

/** 확인 모달 — 확인이면 true, 취소·바깥 클릭·Esc면 false. */
export function showConfirm(message: string, opts: DialogOptions = {}): Promise<boolean> {
  return new Promise((resolve) => {
    useDialogStore.getState().push({
      id: ++_seq, kind: 'confirm', message, title: opts.title, tone: opts.tone ?? 'warning',
      confirmLabel: opts.confirmLabel ?? '확인', cancelLabel: opts.cancelLabel ?? '취소', resolve,
    });
  });
}
