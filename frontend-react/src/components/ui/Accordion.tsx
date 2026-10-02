import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { ChevronDown } from 'lucide-react';
import type { ReactNode } from 'react';
import { clsx } from 'clsx';

interface AccordionProps {
  title: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
  className?: string;
  headerClassName?: string;
  /** 헤더 오른쪽 별도 버튼 자리 — 펼침 버튼 안에 넣으면 버튼 중첩(무효 HTML)이라 밖에 둔다 */
  actions?: ReactNode;
}

export function Accordion({
  title,
  children,
  defaultOpen = false,
  className,
  headerClassName,
  actions,
}: AccordionProps) {
  const [isOpen, setIsOpen] = useState(defaultOpen);

  return (
    <div className={clsx('rounded-xl overflow-hidden', className)}>
      <div className={clsx('flex items-center', actions != null && headerClassName)}>
      <button
        type="button"
        onClick={() => setIsOpen((prev) => !prev)}
        className={clsx(
          'w-full flex items-center justify-between px-4 py-3 text-left transition-colors',
          actions == null && headerClassName,
        )}
      >
        <span className="flex-1">{title}</span>
        <motion.span
          animate={{ rotate: isOpen ? 180 : 0 }}
          transition={{ duration: 0.2 }}
          className="flex-shrink-0 ml-2"
        >
          <ChevronDown className="w-4 h-4 text-slate-400" />
        </motion.span>
      </button>
      {actions != null && <div className="flex items-center gap-1.5 pr-3 flex-shrink-0">{actions}</div>}
      </div>

      <AnimatePresence initial={false}>
        {isOpen && (
          <motion.div
            key="content"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.25, ease: 'easeInOut' }}
            style={{ overflow: 'hidden' }}
          >
            {children}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
