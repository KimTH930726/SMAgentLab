import { useRef, useState } from 'react';
import { X } from 'lucide-react';

interface TagInputProps {
  tags: string[];
  onChange: (tags: string[]) => void;
  placeholder?: string;
  readOnly?: boolean;
  color?: 'cyan' | 'indigo';
}

export function TagInput({ tags, onChange, placeholder, readOnly = false, color = 'cyan' }: TagInputProps) {
  const [input, setInput] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  const addTag = (raw: string) => {
    const parts = raw.split(',').map((t) => t.trim()).filter(Boolean);
    const next = [...tags];
    for (const p of parts) {
      if (!next.includes(p)) next.push(p);
    }
    onChange(next);
    setInput('');
  };

  const removeTag = (idx: number) => onChange(tags.filter((_, i) => i !== idx));

  const chipClass = color === 'indigo'
    ? 'bg-indigo-100 text-indigo-700 border border-indigo-300 dark:bg-indigo-900/40 dark:text-indigo-300 dark:border-indigo-800/40'
    : 'bg-cyan-100 text-cyan-700 border border-cyan-300 dark:bg-cyan-900/40 dark:text-cyan-300 dark:border-cyan-800/40';

  if (readOnly) {
    return (
      <div className="flex flex-wrap gap-1.5 bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 min-h-[38px]">
        {tags.map((tag, i) => (
          <span key={i} className={`flex items-center gap-1 rounded px-2 py-0.5 text-xs font-mono ${chipClass}`}>
            {tag}
          </span>
        ))}
        {tags.length === 0 && <span className="text-slate-600 text-sm">-</span>}
      </div>
    );
  }

  return (
    <div
      className="flex flex-wrap gap-1.5 bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 min-h-[38px] focus-within:border-indigo-500 cursor-text"
      onClick={() => inputRef.current?.focus()}
    >
      {tags.map((tag, i) => (
        <span key={i} className={`flex items-center gap-1 rounded px-2 py-0.5 text-xs font-mono ${chipClass}`}>
          {tag}
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); removeTag(i); }}
            className="hover:text-rose-400 transition-colors leading-none"
          >
            <X className="w-3 h-3" />
          </button>
        </span>
      ))}
      <input
        ref={inputRef}
        type="text"
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={(e) => {
          if ((e.key === 'Enter' || e.key === ',') && !e.nativeEvent.isComposing) {
            e.preventDefault();
            if (input.trim()) addTag(input);
          } else if (e.key === 'Backspace' && !input && tags.length > 0) {
            removeTag(tags.length - 1);
          }
        }}
        onBlur={() => { if (input.trim()) addTag(input); }}
        placeholder={tags.length === 0 ? placeholder : ''}
        className="flex-1 min-w-[80px] bg-transparent text-sm text-slate-200 placeholder-slate-500 outline-none"
      />
    </div>
  );
}
