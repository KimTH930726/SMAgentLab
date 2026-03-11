import { clsx } from 'clsx';
import type { ReactNode } from 'react';

interface BadgeProps {
  children: ReactNode;
  color?: 'indigo' | 'emerald' | 'rose' | 'slate' | 'yellow';
  className?: string;
}

export function Badge({ children, color = 'slate', className }: BadgeProps) {
  return (
    <span
      className={clsx(
        'inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium',
        {
          'bg-indigo-100 text-indigo-700 border border-indigo-300 dark:bg-indigo-900/50 dark:text-indigo-300 dark:border-indigo-700/50': color === 'indigo',
          'bg-emerald-100 text-emerald-700 border border-emerald-300 dark:bg-emerald-900/50 dark:text-emerald-300 dark:border-emerald-700/50': color === 'emerald',
          'bg-rose-100 text-rose-700 border border-rose-300 dark:bg-rose-900/50 dark:text-rose-300 dark:border-rose-700/50': color === 'rose',
          'bg-zinc-100 text-zinc-600 border border-zinc-300 dark:bg-zinc-700/60 dark:text-zinc-300 dark:border-zinc-600/40': color === 'slate',
          'bg-yellow-100 text-yellow-700 border border-yellow-300 dark:bg-yellow-900/50 dark:text-yellow-300 dark:border-yellow-700/50': color === 'yellow',
        },
        className,
      )}
    >
      {children}
    </span>
  );
}
