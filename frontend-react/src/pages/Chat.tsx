import { useAppStore } from '../store/useAppStore';
import { ChatContainer } from '../components/chat/ChatContainer';

export default function Chat() {
  const { namespace } = useAppStore();

  if (!namespace) {
    return (
      <div className="flex items-center justify-center h-full text-slate-500">
        <div className="text-center">
          <p className="text-5xl mb-4">🗂️</p>
          <p className="text-slate-400 text-lg font-medium">파트를 선택해 주세요</p>
          <p className="text-slate-500 text-sm mt-2">좌측 사이드바에서 업무 도메인을 선택하세요</p>
        </div>
      </div>
    );
  }

  return <ChatContainer />;
}
