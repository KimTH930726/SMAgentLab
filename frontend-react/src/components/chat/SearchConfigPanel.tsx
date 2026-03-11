import { useQuery } from '@tanstack/react-query';
import { useAppStore } from '../../store/useAppStore';
import { getCategories } from '../../api/namespaces';

export function SearchConfigPanel() {
  const searchConfig = useAppStore((s) => s.searchConfig);
  const setSearchConfig = useAppStore((s) => s.setSearchConfig);
  const namespace = useAppStore((s) => s.namespace);
  const category = useAppStore((s) => s.category);
  const setCategory = useAppStore((s) => s.setCategory);

  const { data: categories = [] } = useQuery({
    queryKey: ['categories', namespace],
    queryFn: () => getCategories(namespace),
    enabled: !!namespace,
    staleTime: 0,
  });

  return (
    <div className="bg-slate-900 border border-slate-700 rounded-xl p-4 space-y-4">
      <h3 className="text-sm font-semibold text-slate-300">검색 설정</h3>

      {/* Vector weight */}
      <div>
        <div className="flex items-center justify-between mb-1.5">
          <label className="text-xs text-slate-400">의미 중심 (문맥 유사도)</label>
          <span className="text-xs font-mono text-indigo-400">{searchConfig.wVector.toFixed(1)}</span>
        </div>
        <input
          type="range"
          min={0}
          max={1}
          step={0.1}
          value={searchConfig.wVector}
          onChange={(e) => {
            const v = parseFloat(e.target.value);
            setSearchConfig({ wVector: v, wKeyword: parseFloat((1 - v).toFixed(1)) });
          }}
          className="w-full h-1.5 rounded-full accent-indigo-500 cursor-pointer"
        />
        <div className="flex justify-between text-xs text-slate-500 mt-0.5">
          <span>0.0</span>
          <span>1.0</span>
        </div>
      </div>

      {/* Keyword weight */}
      <div>
        <div className="flex items-center justify-between mb-1.5">
          <label className="text-xs text-slate-400">키워드 중심 (단어 일치)</label>
          <span className="text-xs font-mono text-indigo-400">{searchConfig.wKeyword.toFixed(1)}</span>
        </div>
        <input
          type="range"
          min={0}
          max={1}
          step={0.1}
          value={searchConfig.wKeyword}
          onChange={(e) => {
            const v = parseFloat(e.target.value);
            setSearchConfig({ wKeyword: v, wVector: parseFloat((1 - v).toFixed(1)) });
          }}
          className="w-full h-1.5 rounded-full accent-indigo-500 cursor-pointer"
        />
        <div className="flex justify-between text-xs text-slate-500 mt-0.5">
          <span>0.0</span>
          <span>1.0</span>
        </div>
      </div>

      {/* Top-K */}
      <div>
        <div className="flex items-center justify-between mb-1.5">
          <label className="text-xs text-slate-400">검색 결과 수</label>
          <span className="text-xs font-mono text-indigo-400">{searchConfig.topK}</span>
        </div>
        <input
          type="range"
          min={1}
          max={10}
          step={1}
          value={searchConfig.topK}
          onChange={(e) => setSearchConfig({ topK: parseInt(e.target.value, 10) })}
          className="w-full h-1.5 rounded-full accent-indigo-500 cursor-pointer"
        />
        <div className="flex justify-between text-xs text-slate-500 mt-0.5">
          <span>1</span>
          <span>10</span>
        </div>
      </div>

      {/* Category filter */}
      {categories.length > 0 && (
        <div>
          <label className="text-xs text-slate-400 block mb-1.5">업무구분 필터</label>
          <select
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            className="w-full bg-slate-800 border border-slate-600 rounded-lg px-2 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-indigo-500"
          >
            <option value="">전체</option>
            {categories.map((c) => (
              <option key={c.id} value={c.name}>{c.name}</option>
            ))}
          </select>
        </div>
      )}
    </div>
  );
}
