import { useState, useEffect } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Zap, Eye, EyeOff } from 'lucide-react';
import { Button } from '../components/ui/Button';
import { useAuthStore } from '../store/useAuthStore';
import { register, login, getParts } from '../api/auth';
import type { Part } from '../types';

export default function Register() {
  const navigate = useNavigate();
  const authLogin = useAuthStore((s) => s.login);

  const [parts, setParts] = useState<Part[]>([]);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPw, setConfirmPw] = useState('');
  const [part, setPart] = useState('');
  const [llmApiKey, setLlmApiKey] = useState('');
  const [showPw, setShowPw] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    getParts()
      .then((data) => {
        const filtered = data.filter((p) => p.name !== '기본');
        setParts(filtered);
        if (filtered.length > 0) setPart(filtered[0].name);
      })
      .catch(console.error);
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password || !part) return;

    if (password !== confirmPw) {
      setError('비밀번호가 일치하지 않습니다.');
      return;
    }
    if (password.length < 4) {
      setError('비밀번호는 4자 이상이어야 합니다.');
      return;
    }

    setLoading(true);
    setError('');
    try {
      await register({
        username: username.trim(),
        password,
        part,
        llm_api_key: llmApiKey.trim() || undefined,
      });
      // Auto-login after registration
      const res = await login(username.trim(), password);
      authLogin(res.user, res.access_token, res.refresh_token);
      navigate('/', { replace: true });
    } catch (err) {
      if (err instanceof Error) {
        // 네트워크 에러 (fetch failed 등) → 사용자 친화적 메시지
        const msg = err.message;
        if (msg.includes('fetch') || msg.includes('network') || msg.includes('Failed')) {
          setError('서버에 연결할 수 없습니다. 잠시 후 다시 시도해주세요.');
        } else {
          setError(msg);
        }
      } else {
        setError('회원가입에 실패했습니다.');
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#0F172A] flex items-center justify-center px-4">
      <div className="w-full max-w-sm">
        {/* Logo */}
        <div className="flex items-center justify-center gap-3 mb-8">
          <div className="w-10 h-10 bg-indigo-600 rounded-xl flex items-center justify-center">
            <Zap className="w-6 h-6 text-white" />
          </div>
          <span className="text-2xl font-bold text-slate-100">Ops-Navigator</span>
        </div>

        <div className="bg-[#1E293B] rounded-2xl p-8 border border-slate-700">
          <h2 className="text-xl font-semibold text-slate-100 mb-6 text-center">회원가입</h2>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-slate-400 mb-1.5">아이디</label>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2.5 text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500 transition-colors"
                placeholder="사용자 아이디"
                autoFocus
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-slate-400 mb-1.5">파트 (부서)</label>
              <select
                value={part}
                onChange={(e) => setPart(e.target.value)}
                className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2.5 text-slate-200 focus:outline-none focus:border-indigo-500 cursor-pointer"
              >
                {parts.map((p) => (
                  <option key={p.id} value={p.name}>{p.name}</option>
                ))}
              </select>
            </div>

            <div>
              <label className="block text-sm font-medium text-slate-400 mb-1.5">비밀번호</label>
              <div className="relative">
                <input
                  type={showPw ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2.5 pr-10 text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500 transition-colors"
                  placeholder="비밀번호 (4자 이상)"
                />
                <button
                  type="button"
                  onClick={() => setShowPw((v) => !v)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300"
                >
                  {showPw ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium text-slate-400 mb-1.5">비밀번호 확인</label>
              <input
                type={showPw ? 'text' : 'password'}
                value={confirmPw}
                onChange={(e) => setConfirmPw(e.target.value)}
                className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2.5 text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500 transition-colors"
                placeholder="비밀번호 재입력"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-slate-400 mb-1.5">
                LLM API Key <span className="text-slate-600">(선택)</span>
              </label>
              <input
                type="password"
                value={llmApiKey}
                onChange={(e) => setLlmApiKey(e.target.value)}
                className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2.5 text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500 transition-colors"
                placeholder="사내 LLM API Key"
              />
              <p className="text-xs text-slate-600 mt-1">사내 LLM 사용 시에만 입력</p>
            </div>

            {error && (
              <p className="text-sm text-rose-400 bg-rose-900/20 border border-rose-800/30 rounded-lg px-3 py-2">
                {error}
              </p>
            )}

            <Button type="submit" loading={loading} className="w-full" size="lg">
              가입하기
            </Button>
          </form>

          <p className="mt-6 text-center text-sm text-slate-500">
            이미 계정이 있으신가요?{' '}
            <Link to="/login" className="text-indigo-400 hover:text-indigo-300 font-medium">
              로그인
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}
