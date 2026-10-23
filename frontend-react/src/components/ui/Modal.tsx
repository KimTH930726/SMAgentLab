import { useEffect, useRef } from 'react';

let _openCount = 0;
// 열린 모달 차례(맨 위가 끝) — Esc는 맨 위 하나만 닫는다(10/23 리뷰 F3: 청크 검토 위 "다른 곳…" 창에서 Esc를 누르면 둘 다 닫혔다)
const _stack: number[] = [];
let _seq = 0;
import { createPortal } from 'react-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { X } from 'lucide-react';
import type { ReactNode } from 'react';

interface ModalProps {
  isOpen: boolean;
  onClose: () => void;
  title?: string;
  children: ReactNode;
  maxWidth?: string;
  /** 다른 모달 위에 떠야 할 때(앱 안내·확인 모달) */
  zClass?: string;
}

export function Modal({
  isOpen,
  onClose,
  title,
  children,
  maxWidth = 'max-w-md',
  zClass = 'z-50',
}: ModalProps) {
  const idRef = useRef(0);
  useEffect(() => {
    if (!isOpen) return;
    const id = ++_seq;
    idRef.current = id;
    _stack.push(id);
    return () => { const i = _stack.lastIndexOf(id); if (i >= 0) _stack.splice(i, 1); };
  }, [isOpen]);

  // Close on Escape key — 맨 위 모달만
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen && _stack[_stack.length - 1] === idRef.current) onClose();
    };
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [isOpen, onClose]);

  // Prevent body scroll when open — counter 방식으로 중첩 모달에서도 안전하게 처리
  useEffect(() => {
    if (!isOpen) return;
    _openCount++;
    document.body.style.overflow = 'hidden';
    return () => {
      _openCount = Math.max(0, _openCount - 1);
      if (_openCount === 0) document.body.style.overflow = '';
    };
  }, [isOpen]);

  return createPortal(
    <AnimatePresence>
      {isOpen && (
        <div className={`fixed inset-0 ${zClass} flex items-center justify-center p-4`}>
          {/* Backdrop */}
          <motion.div
            key="backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="absolute inset-0 bg-black/60 backdrop-blur-sm"
            onClick={onClose}
          />

          {/* Modal content */}
          <motion.div
            key="modal"
            initial={{ opacity: 0, scale: 0.95, y: 8 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: 8 }}
            transition={{ duration: 0.2 }}
            className={`relative bg-slate-800 rounded-xl shadow-2xl border border-slate-700 w-full ${maxWidth} z-10 flex flex-col max-h-[90vh]`}
          >
            {title && (
              <div className="flex items-center justify-between px-6 py-4 border-b border-slate-700 flex-shrink-0">
                <h3 className="text-lg font-semibold text-slate-100">{title}</h3>
                <button
                  onClick={onClose}
                  className="p-1.5 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-slate-700 transition-colors"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>
            )}
            <div className="p-6 overflow-y-auto flex-1">{children}</div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>,
    document.body,
  );
}
