import { useState } from 'react';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';
import { Clipboard, Check } from 'lucide-react';

interface CodeBlockProps {
  code: string;
  language?: string;
}

export function CodeBlock({ code, language = 'sql' }: CodeBlockProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(code).catch(console.error);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="relative">
      <button
        onClick={handleCopy}
        className="absolute top-2 right-2 p-1.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-slate-400 hover:text-slate-200 transition-colors z-10"
        title="코드 복사"
      >
        {copied ? (
          <Check className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
        ) : (
          <Clipboard className="w-4 h-4" />
        )}
      </button>
      <SyntaxHighlighter
        language={language}
        style={oneDark}
        customStyle={{
          margin: 0,
          borderRadius: '8px',
          fontSize: '0.8rem',
          background: '#0F172A',
        }}
      >
        {code}
      </SyntaxHighlighter>
    </div>
  );
}
