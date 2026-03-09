import { clsx } from 'clsx';
import { Bot, User } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Badge } from '../ui/Badge';
import { SearchResultCard } from './SearchResultCard';
import { FeedbackSection } from './FeedbackSection';
import type { ChatMessage } from '../../types';

interface MessageItemProps {
  message: ChatMessage;
  namespace: string;
}

export function MessageItem({ message, namespace }: MessageItemProps) {
  const isUser = message.role === 'user';

  if (isUser) {
    return (
      <div className="flex justify-end">
        <div className="flex items-end gap-2 max-w-[80%]">
          <div className="bg-slate-700 rounded-2xl rounded-br-sm px-4 py-3">
            <p className="text-sm text-slate-200 whitespace-pre-wrap leading-relaxed">
              {message.content}
            </p>
          </div>
          <div className="w-7 h-7 rounded-full bg-slate-600 flex items-center justify-center flex-shrink-0 mb-0.5">
            <User className="w-4 h-4 text-slate-300" />
          </div>
        </div>
      </div>
    );
  }

  // Assistant message
  return (
    <div className="flex justify-start">
      <div className="flex items-start gap-2 max-w-[90%]">
        <div className="w-7 h-7 rounded-full bg-indigo-600 flex items-center justify-center flex-shrink-0 mt-0.5">
          <Bot className="w-4 h-4 text-white" />
        </div>
        <div className="flex-1 space-y-3">
          {/* Mapped term badge */}
          {message.mapped_term && (
            <div className="flex items-center gap-2">
              <span className="text-xs text-slate-500">용어 매핑:</span>
              <Badge color="indigo">{message.mapped_term}</Badge>
            </div>
          )}

          {/* Search results */}
          {message.results && message.results.length > 0 && (
            <div className="space-y-1.5">
              <p className="text-xs text-slate-500 font-medium">
                검색된 문서 {message.results.length}건
              </p>
              {message.results.map((result, idx) => (
                <SearchResultCard
                  key={result.id}
                  result={result}
                  defaultOpen={false}
                  index={idx}
                />
              ))}
            </div>
          )}

          {/* Answer text */}
          {(message.content || message.isStreaming) && (
            <div
              className={clsx(
                'bg-slate-800 rounded-2xl rounded-tl-sm px-4 py-3',
                'border border-slate-700',
              )}
            >
              {message.content ? (
                <div
                  className={clsx(
                    'prose-chat text-sm text-slate-200 leading-relaxed',
                    message.isStreaming && 'typing-cursor',
                  )}
                >
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {message.content}
                  </ReactMarkdown>
                </div>
              ) : (
                message.isStreaming && (
                  <p className="typing-cursor text-slate-400 text-sm">&nbsp;</p>
                )
              )}
            </div>
          )}

          {/* Feedback - hide while streaming or if already feedbacked */}
          {!message.isStreaming && message.content && namespace && !message.has_feedback && (
            <FeedbackSection
              namespace={namespace}
              question={message.question ?? ''}
              answer={message.content}
              knowledgeId={message.results?.[0]?.id ?? null}
              messageId={message.messageId}
            />
          )}
        </div>
      </div>
    </div>
  );
}
