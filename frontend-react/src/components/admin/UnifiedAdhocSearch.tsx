import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { Search, Flag, FlagOff, Check, X, PenLine } from 'lucide-react';
import { debugSearch } from '../../api/chat';
import { searchPolicy, type ParamHit, type NarrativeHit } from '../../api/policy';
import { searchRefdata, type CommonCodeHit, type DbColumnHit } from '../../api/refdata';
import { flagKnowledgeForReview, updateKnowledge } from '../../api/knowledge';
import { getSearchThresholds } from '../../api/llm';
import { getCategories } from '../../api/namespaces';
import { useNamespaceAccess } from '../../utils/useNamespaceAccess';
import { Modal } from '../ui/Modal';
import type { DebugSearchResult } from '../../types';

/**
 * "즉석 질의" 통합 결과(2026-09-18, 카드/모달 리디자인) — "정책이 벡터검색/정책서
 * 검색으로 나뉘어 보이니 뭔가 대단한 별도 축처럼 보인다, chat처럼 질문 하나 기준으로
 * top-K 하나로 보고 싶다"는 지적으로 DebugPanel/PolicyAdhocSearch를 나란히 보여주던 걸
 * 교체. 이후 "화면이 안 예쁘다 — 카드형태로, 클릭하면 모달, 점수는 상태바처럼 카드
 * 맨 위에" 피드백으로 리스트 행을 카드+모달로 재설계.
 *
 * 문제: 일반지식(코사인 유사도, 0~1)·정책 파라미터(RDB ts_rank, 0~5+)·정책 서술(코사인
 * 유사도, 0~1)은 점수 스케일이 안 맞아 그냥 정렬하면 사과-오렌지 비교가 됨. 실제 chat도
 * 이 문제를 안 풀고 텍스트만 이어붙임(agent.py doc_context + policy_context).
 *
 * 해결: Reciprocal Rank Fusion(RRF) 적용 — OpenSearch/Azure AI Search/MongoDB/Redis 등이
 * 실제로 쓰는 표준 기법. 점수 정규화가 필요 없고 "각 목록 안에서 몇 번째 순위인가"만
 * 본다: rrf = 1/(k+rank), k=60(원 논문 기본값). 카드 상단 상태바도 같은 이유로 원점수
 * (ts_rank vs cosine)를 직접 쓰지 않고 "이번 검색 1위 대비 RRF 상대값"으로 그린다 —
 * 원점수를 그대로 막대 길이로 쓰면 ts_rank(보통 0.0~0.1대)가 cosine(0.5~0.9대)보다
 * 항상 짧게 보여 실제로는 상위인 RDB 결과가 "약한 결과"로 오인될 수 있음(RRF를 쓴
 * 이유와 정확히 같은 함정). 원점수는 배지 텍스트로 그대로 보여줘 투명성은 유지.
 *
 * 검색 오염 지식 관리(2026-09-18): 딜리버스 DB에서 무관한 질문에 공통코드 표(id=20)가
 * 1위로 튀는 실제 사례를 발견 — 카테고리를 "공통코드"로 재태깅해 키워드 전용 라우팅으로
 * 옮겨 해결했지만, 같은 클래스 문제(보일러플레이트 문서가 여러 무관한 질문에 반복
 * 노출)가 또 나올 수 있다는 지적으로 카드에 "이상해요" 플래그를 추가 — 기존
 * rag_knowledge_review_flag 큐(나빠요 피드백과 동일한 곳, 지식베이스 탭에서 처리)에
 * reason='search_noise'로 올린다. 정책 결과(params/narratives)는 rag_knowledge가 아니라
 * 별도 테이블이라 이 큐에 못 올림 — 플래그는 일반지식 카드에만 노출.
 *
 * 채택 여부 표시(2026-09-18): "순위/강도만 보여주지 말고 실제 chat 프롬프트에 이 지식이
 * 들어가는지 안 들어가는지도 같이 보여달라"는 요청 — RRF 순위는 이 화면 안에서의 상대
 * 비교일 뿐, 실제로 LLM 컨텍스트에 포함되는지는 별개 조건. 일반지식은 agent.py의
 * _build_rrf_context()/retrieval.build_context()가 final_score >= knowledge_min_score
 * (관리자 설정, /api/llm/thresholds)인 것만 포함시킨다 — 그 미만이면 아무리 순위가
 * 높아도 실제 chat 프롬프트엔 안 들어간다. 정책 파라미터/서술은 search_policy()에
 * 점수 임계치 자체가 없어(코드 확인 완료) top_k 이내면 항상 포함됨 — 그래서 정책
 * 카드는 항상 "채택됨"으로 표시하고 이유를 툴팁으로 알려준다.
 *
 * 인라인 수정(2026-09-18): "검색해서 나온 지식을 그 자리에서 고치면서 품질 관리를
 * 하고 싶다, 지금은 조회만 된다"는 요청 — 지식베이스 탭까지 왕복하지 않고 상세 모달
 * 안에서 바로 수정할 수 있게 KnowledgeTable의 편집 폼(content/category/base_weight)과
 * 동일한 필드를 넣었다. 정책 항목(params/narratives)은 rag_knowledge가 아니라 별도
 * 테이블이라 이 경로로 수정 불가 — 수정 버튼은 일반지식 카드에만 노출. 저장 후에는
 * 점수·채택 여부가 바뀔 수 있어(내용이 달라지면 재임베딩되고 유사도도 달라짐) 그냥
 * 로컬 텍스트만 바꾸지 않고 같은 질문으로 검색을 다시 돌려 최신 상태를 보여준다.
 *
 * 참조데이터 축 추가(2026-09-18): chat(agent.py)에 공통코드/DB스키마(ref_common_code/
 * ref_db_column) 축을 네 번째 RRF 축으로 새로 연결하면서("DS14가 뭐야?"가 rag_knowledge
 * 안의 벡터축과 스케일 경쟁하다 top_k 후보에도 못 들던 문제를 별도 테이블로 분리해 해결,
 * §table-definition.md #51) 이 화면도 "chat과 게이트가 똑같이 실행되고 투명하게 보여야
 * 한다"는 원칙에 따라 동일한 네 번째 축을 붙였다(`GET /api/refdata/search`, 신규). 이
 * 축도 정책과 마찬가지로 점수 임계치가 없어 top_k 이내면 항상 채택 — knowledgeId가 없어
 * 수정/플래그 버튼은 노출되지 않는다(그 데이터의 소유 테이블이 다름).
 */
type SourceType = 'general' | 'policy-param' | 'policy-narrative' | 'refdata-code' | 'refdata-column';

interface MetaRow {
  key: string;
  value: string;
}

interface UnifiedHit {
  source: SourceType;
  label: string;
  snippet: string;
  fullContent: string;
  scoreLabel: string;
  rrf: number;
  strengthPct: number;
  knowledgeId?: number;
  meta: MetaRow[];
  adopted: boolean;
  adoptedReason: string;
  rawCategory?: string;
  rawBaseWeight?: number;
}

const RRF_K = 60;

function rrf(rank: number): number {
  return 1 / (RRF_K + rank + 1); // rank는 0-indexed
}

const SOURCE_META: Record<SourceType, { badge: string; badgeClass: string; dotClass: string }> = {
  'general': { badge: '일반지식', badgeClass: 'bg-indigo-50 text-indigo-700 border-indigo-200 dark:bg-indigo-900/40 dark:text-indigo-300 dark:border-indigo-700/40', dotClass: 'bg-indigo-500' },
  'policy-param': { badge: '정책 · RDB 파라미터', badgeClass: 'bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-900/40 dark:text-amber-300 dark:border-amber-700/40', dotClass: 'bg-amber-500' },
  'policy-narrative': { badge: '정책 · 벡터 서술', badgeClass: 'bg-violet-50 text-violet-700 border-violet-200 dark:bg-violet-900/40 dark:text-violet-300 dark:border-violet-700/40', dotClass: 'bg-violet-500' },
  'refdata-code': { badge: '참조데이터 · 공통코드', badgeClass: 'bg-teal-50 text-teal-700 border-teal-200 dark:bg-teal-900/40 dark:text-teal-300 dark:border-teal-700/40', dotClass: 'bg-teal-500' },
  'refdata-column': { badge: '참조데이터 · DB스키마', badgeClass: 'bg-cyan-50 text-cyan-700 border-cyan-200 dark:bg-cyan-900/40 dark:text-cyan-300 dark:border-cyan-700/40', dotClass: 'bg-cyan-500' },
};

function strengthBand(pct: number): { barClass: string; label: string } {
  if (pct >= 70) return { barClass: 'bg-emerald-500', label: '강함' };
  if (pct >= 35) return { barClass: 'bg-sky-500', label: '보통' };
  return { barClass: 'bg-slate-400 dark:bg-slate-500', label: '약함' };
}

export function UnifiedAdhocSearch() {
  const qc = useQueryClient();
  const { selectedNs, setSelectedNs, sortedNamespaces, canModifyNs } = useNamespaceAccess();
  const [question, setQuestion] = useState('');
  const [topK, setTopK] = useState(10);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hits, setHits] = useState<UnifiedHit[] | null>(null);
  const [activeHit, setActiveHit] = useState<UnifiedHit | null>(null);
  const [flaggedIds, setFlaggedIds] = useState<Set<number>>(new Set());
  const [flaggingId, setFlaggingId] = useState<number | null>(null);
  const [isEditing, setIsEditing] = useState(false);
  const [editForm, setEditForm] = useState({ content: '', category: '', base_weight: 1.0 });

  const { data: categories = [] } = useQuery({
    queryKey: ['categories', selectedNs],
    queryFn: () => getCategories(selectedNs),
    enabled: !!selectedNs,
    staleTime: 30_000,
  });
  const categoryNames = categories.map((c) => c.name);

  const updateMutation = useMutation({
    mutationFn: (id: number) => updateKnowledge(id, editForm),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['knowledge', selectedNs] });
      setIsEditing(false);
      setActiveHit(null);
      handleSearch(); // 내용이 바뀌면 점수·채택 여부도 달라질 수 있어 다시 검색
    },
  });

  // 실제 chat 프롬프트 채택 기준(agent.py) — 일반지식만 이 임계치로 걸러짐
  const { data: thresholds } = useQuery({
    queryKey: ['search-thresholds'],
    queryFn: getSearchThresholds,
    staleTime: 60_000,
  });

  const handleSearch = async () => {
    if (!selectedNs || !question.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const [general, policy, refdata] = await Promise.all([
        debugSearch({ namespace: selectedNs, question: question.trim(), top_k: topK }),
        searchPolicy(selectedNs, question.trim(), { topK }),
        searchRefdata(selectedNs, question.trim(), { topK }),
      ]);
      const minScore = thresholds?.knowledge_min_score ?? 0;

      // "DB"/"공통코드"는 벡터 대신 ts_rank로만 순위가 매겨져(retrieval.py
      // _KEYWORD_ONLY_CATEGORIES) 코사인 스케일용 임계치를 그대로 대면 항상 걸러진다
      // (ts_rank는 보통 0.01~0.1대) — 백엔드 is_adopted()와 동일하게 이 카테고리는
      // "키워드 매칭이 됐는지"(final_score>0)만으로 채택 여부를 가른다.
      const KEYWORD_ONLY_CATEGORIES = ['DB', '공통코드'];
      const generalHits: UnifiedHit[] = general.results.map((r: DebugSearchResult, i) => {
        // base_weight를 걷어낸 원점수로 게이트 판단(2026-09-18) — final_score를 그대로
        // 쓰면 base_weight(기본 1.0, 최대 5.0)가 곱해져 있어 "관련성"이 아니라 "피드백을
        // 얼마나 받았는지"를 재게 됨(백엔드 retrieval.relevance_score()와 동일 공식).
        const rawScore = r.final_score / (1 + r.base_weight);
        const isKeywordOnly = r.category != null && KEYWORD_ONLY_CATEGORIES.includes(r.category);
        const adopted = isKeywordOnly ? r.final_score > 0 : rawScore >= minScore;
        return {
          source: 'general',
          label: `문서 #${r.id}${r.category ? ` · ${r.category}` : ''}`,
          snippet: r.content,
          fullContent: r.content,
          scoreLabel: `유사도(cosine) ${r.v_score.toFixed(4)}`,
          rrf: rrf(i),
          strengthPct: Math.max(4, Math.min(100, Math.round(r.v_score * 100))),
          knowledgeId: r.id,
          rawCategory: r.category ?? '',
          rawBaseWeight: r.base_weight,
          adopted,
          adoptedReason: isKeywordOnly
            ? (adopted
                ? '키워드 전용 카테고리(DB/공통코드) — 키워드 매칭이 돼 채택됩니다(ts_rank 크기는 무관).'
                : '키워드 전용 카테고리(DB/공통코드) — 키워드 매칭이 안 돼 제외됩니다.')
            : adopted
              ? `관련성 점수 ${rawScore.toFixed(4)}(base_weight 제외) ≥ 채택 임계치 ${minScore.toFixed(2)} — 실제 chat 프롬프트에 포함됩니다.`
              : `관련성 점수 ${rawScore.toFixed(4)}(base_weight 제외) < 채택 임계치 ${minScore.toFixed(2)} — 순위와 무관하게 실제 chat 프롬프트엔 포함되지 않습니다.`,
          meta: [
            { key: '분류', value: r.category ?? '-' },
            { key: '벡터 유사도', value: r.v_score.toFixed(4) },
            { key: '키워드 점수', value: r.k_score.toFixed(4) },
            { key: '관련성 점수(base_weight 제외)', value: rawScore.toFixed(4) },
            { key: '최종 점수(가중치 반영)', value: r.final_score.toFixed(4) },
            { key: 'base_weight', value: r.base_weight.toFixed(2) },
          ],
        };
      });
      const paramHits: UnifiedHit[] = policy.params.map((p: ParamHit, i) => ({
        source: 'policy-param',
        label: `${p.policy_name} · ${p.param_name}`,
        snippet: p.value ? `${p.value}${p.unit ?? ''}${p.condition ? ` (${p.condition})` : ''}` : p.raw_body,
        fullContent: p.raw_body,
        scoreLabel: `ts_rank ${p.score.toFixed(3)}`,
        rrf: rrf(i),
        strengthPct: 0, // rrf 상대값으로 아래에서 재계산
        adopted: true,
        adoptedReason: '정책 파라미터는 점수 임계치가 없어 top_k 이내면 항상 chat 프롬프트에 포함됩니다.',
        meta: [
          { key: '정책명', value: p.policy_name },
          { key: '분류', value: p.category_path.join(' > ') || '-' },
          { key: '상태', value: p.status },
          { key: '조건', value: p.condition ?? '조건없음' },
          { key: '값', value: p.value ? `${p.value}${p.unit ?? ''}` : '-' },
        ],
      }));
      const narrativeHits: UnifiedHit[] = policy.narratives.map((n: NarrativeHit, i) => ({
        source: 'policy-narrative',
        label: n.policy_name,
        snippet: n.chunk_text,
        fullContent: n.chunk_text,
        scoreLabel: `유사도(cosine) ${n.score.toFixed(4)}`,
        rrf: rrf(i),
        strengthPct: 0,
        adopted: true,
        adoptedReason: '정책 서술은 점수 임계치가 없어 top_k 이내면 항상 chat 프롬프트에 포함됩니다.',
        meta: [
          { key: '정책명', value: n.policy_name },
          { key: '분류', value: n.category_path.join(' > ') || '-' },
          { key: '상태', value: n.status },
        ],
      }));
      // 참조데이터(공통코드/DB스키마) 축(2026-09-18) — agent.py의 네 번째 RRF 축과 동일한
      // 데이터. ts_rank 기반이라 정책 파라미터와 마찬가지로 점수 임계치 없이 top_k
      // 이내면 항상 채택(코드 확인: _build_rrf_context가 이 축엔 is_adopted를 안 씀).
      const commonCodeHits: UnifiedHit[] = refdata.common_codes.map((c: CommonCodeHit, i) => ({
        source: 'refdata-code',
        label: `${c.group_code_name ?? '분류없음'} · ${c.code_id}`,
        snippet: c.code_name ?? '',
        fullContent: `${c.code_id} = ${c.code_name ?? ''}`,
        scoreLabel: `ts_rank ${c.rank.toFixed(3)}`,
        rrf: rrf(i),
        strengthPct: 0,
        adopted: true,
        adoptedReason: '참조데이터(공통코드)는 점수 임계치가 없어 top_k 이내면 항상 chat 프롬프트에 포함됩니다.',
        meta: [
          { key: '그룹', value: c.group_code_name ?? '-' },
          { key: '코드값', value: c.code_id },
          { key: '설명', value: c.code_name ?? '-' },
        ],
      }));
      const dbColumnHits: UnifiedHit[] = refdata.db_columns.map((d: DbColumnHit, i) => ({
        source: 'refdata-column',
        label: `${d.table_name}.${d.column_name}`,
        snippet: d.column_comment ?? '',
        fullContent: `${d.table_name}.${d.column_name} (${d.data_type ?? '-'}) — ${d.column_comment ?? ''}`,
        scoreLabel: `ts_rank ${d.rank.toFixed(3)}`,
        rrf: rrf(i),
        strengthPct: 0,
        adopted: true,
        adoptedReason: '참조데이터(DB스키마)는 점수 임계치가 없어 top_k 이내면 항상 chat 프롬프트에 포함됩니다.',
        meta: [
          { key: '테이블', value: d.table_name },
          { key: '컬럼', value: d.column_name },
          { key: '타입', value: d.data_type ?? '-' },
          { key: '설명', value: d.column_comment ?? '-' },
        ],
      }));

      const merged = [...generalHits, ...paramHits, ...narrativeHits, ...commonCodeHits, ...dbColumnHits].sort((a, b) => b.rrf - a.rrf);
      // 정책 축(ts_rank/cosine 혼재)은 원점수를 막대 길이로 못 쓰므로, 이번 검색 1위의
      // RRF를 기준으로 한 상대값을 강도로 사용 — 일반지식은 이미 자체 cosine을 씀
      const topRrf = merged[0]?.rrf ?? 1;
      for (const h of merged) {
        if (h.source !== 'general') {
          h.strengthPct = Math.max(4, Math.min(100, Math.round((h.rrf / topRrf) * 100)));
        }
      }
      // top_k는 "각 축에서 몇 개를 후보로 볼지"이자 "합친 뒤 최종 몇 개를 보여줄지"이기도
      // 하다 — 예전엔 후보 수로만 쓰여서 top_k=10이어도 축이 3개면 최대 30개가 그대로
      // 나열됐다("이게 top_k 맞냐"는 지적으로 수정, 2026-09-18). 합친 뒤 top_k개로 자른다.
      setHits(merged.slice(0, topK));
      setFlaggedIds(new Set());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setHits(null);
    } finally {
      setLoading(false);
    }
  };

  const handleFlag = async (hit: UnifiedHit) => {
    if (!selectedNs || hit.knowledgeId == null || flaggedIds.has(hit.knowledgeId)) return;
    setFlaggingId(hit.knowledgeId);
    try {
      await flagKnowledgeForReview(hit.knowledgeId, selectedNs, 'search_noise');
      setFlaggedIds((prev) => new Set(prev).add(hit.knowledgeId!));
    } catch (err) {
      alert(err instanceof Error ? err.message : String(err));
    } finally {
      setFlaggingId(null);
    }
  };

  const openDetail = (hit: UnifiedHit) => {
    setActiveHit(hit);
    setIsEditing(false);
  };

  const startEdit = (hit: UnifiedHit) => {
    setEditForm({
      content: hit.fullContent,
      category: hit.rawCategory ?? '',
      base_weight: hit.rawBaseWeight ?? 1.0,
    });
    setIsEditing(true);
  };

  const counts = hits
    ? {
        general: hits.filter((h) => h.source === 'general').length,
        param: hits.filter((h) => h.source === 'policy-param').length,
        narrative: hits.filter((h) => h.source === 'policy-narrative').length,
        code: hits.filter((h) => h.source === 'refdata-code').length,
        column: hits.filter((h) => h.source === 'refdata-column').length,
      }
    : null;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <label className="block text-xs font-medium text-slate-400 mb-1">파트</label>
          <select
            value={selectedNs}
            onChange={(e) => setSelectedNs(e.target.value)}
            className="w-48 bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
          >
            <option value="">선택...</option>
            {sortedNamespaces.map((ns) => <option key={ns} value={ns}>{ns}</option>)}
          </select>
        </div>
        <div className="flex-1 min-w-[240px]">
          <label className="block text-xs font-medium text-slate-400 mb-1">질문</label>
          <input
            type="text"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
            placeholder="예: 장바구니 최대 보관 수량이 몇 개야?"
            className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-slate-400 mb-1">top_k(최종 표시)</label>
          <input
            type="number" min={1} max={50} value={topK}
            onChange={(e) => setTopK(Number(e.target.value) || 10)}
            className="w-24 bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
          />
        </div>
        <button
          onClick={handleSearch}
          disabled={!selectedNs || !question.trim() || loading}
          className="flex items-center gap-1.5 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium rounded-lg transition-colors"
        >
          <Search className="w-4 h-4" />
          {loading ? '검색 중...' : '검색'}
        </button>
      </div>

      {!selectedNs && <p className="text-xs text-slate-500">파트를 선택하세요.</p>}
      {error && <p className="text-xs text-rose-400">검색 실패: {error}</p>}

      {hits && counts && (
        <div className="space-y-3">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1 text-xs text-slate-500 bg-slate-900/60 border border-slate-700/60 rounded-lg px-3 py-2">
            <p><b className="text-slate-300">검색</b> — 일반지식·정책(RDB/벡터)·참조데이터(공통코드/DB스키마)를 각각 검색 후 <b className="text-indigo-400">RRF</b>(k={RRF_K})로 순위 통합. 점수 스케일이 달라(코사인 0~1 vs ts_rank) 원점수 대신 순위만 씀.</p>
            <p><b className="text-slate-300">top_k</b> — 축별 후보 수 = 합친 뒤 최종 표시 개수.</p>
            <p><b className="text-slate-300">상단 막대</b> — 원점수 아님, 이번 검색 1위 대비 상대 강도.</p>
            <p>
              <b className="text-emerald-400">채택</b>/<b className="text-rose-400">제외</b> — 실제 chat 프롬프트 포함 여부.
              일반지식은 점수 ≥ {thresholds ? thresholds.knowledge_min_score.toFixed(2) : '…'} 필요(단, DB/공통코드 카테고리는 매칭 여부로만 판정), 정책·참조데이터는 항상 채택.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-3 text-xs">
            <span className="flex items-center gap-1.5 text-slate-400">
              <span className={clsx('w-2 h-2 rounded-full', SOURCE_META.general.dotClass)} />
              일반지식 {counts.general}건
            </span>
            <span className="flex items-center gap-1.5 text-slate-400">
              <span className={clsx('w-2 h-2 rounded-full', SOURCE_META['policy-param'].dotClass)} />
              정책·파라미터 {counts.param}건
            </span>
            <span className="flex items-center gap-1.5 text-slate-400">
              <span className={clsx('w-2 h-2 rounded-full', SOURCE_META['policy-narrative'].dotClass)} />
              정책·서술 {counts.narrative}건
            </span>
            <span className="flex items-center gap-1.5 text-slate-400">
              <span className={clsx('w-2 h-2 rounded-full', SOURCE_META['refdata-code'].dotClass)} />
              참조데이터·공통코드 {counts.code}건
            </span>
            <span className="flex items-center gap-1.5 text-slate-400">
              <span className={clsx('w-2 h-2 rounded-full', SOURCE_META['refdata-column'].dotClass)} />
              참조데이터·DB스키마 {counts.column}건
            </span>
          </div>

          {hits.length === 0 && <p className="text-xs text-slate-500 py-6 text-center">결과 없음</p>}

          {/* 좌상단부터 순위 순으로: grid의 기본 흐름(행 우선)이 정렬 순서와 그대로 맞음 */}
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
            {hits.map((h, i) => {
              const band = strengthBand(h.strengthPct);
              const isFlaggable = h.source === 'general' && h.knowledgeId != null;
              const isFlagged = isFlaggable && flaggedIds.has(h.knowledgeId!);
              return (
                <div
                  key={i}
                  onClick={() => openDetail(h)}
                  title={h.adoptedReason}
                  className={clsx(
                    'group relative flex flex-col bg-slate-800 border rounded-lg overflow-hidden cursor-pointer transition-colors',
                    h.adopted
                      ? 'border-slate-700 hover:border-slate-600 hover:bg-slate-800/80'
                      : 'border-rose-900/50 opacity-60 hover:opacity-80',
                  )}
                >
                  {/* 상태바: 카드 맨 위, 이번 검색에서의 상대 강도 */}
                  <div className="h-1.5 w-full bg-slate-700/60 flex-shrink-0">
                    <div className={clsx('h-full transition-all', band.barClass)} style={{ width: `${h.strengthPct}%` }} />
                  </div>

                  <div className="px-3 py-2.5 flex flex-col gap-1 flex-1">
                    <div className="flex items-center justify-between gap-1">
                      <span className="text-[10px] text-slate-500 font-mono">#{i + 1}</span>
                      <div className="flex items-center gap-1.5">
                        <span
                          className={clsx(
                            'flex items-center gap-0.5 text-[9px] font-medium px-1 py-0.5 rounded',
                            h.adopted ? 'text-emerald-400 bg-emerald-950/40' : 'text-rose-400 bg-rose-950/40',
                          )}
                        >
                          {h.adopted ? <Check className="w-2.5 h-2.5" /> : <X className="w-2.5 h-2.5" />}
                          {h.adopted ? '채택' : '제외'}
                        </span>
                        <span className={clsx('text-[10px] font-medium', {
                          'text-emerald-400': band.label === '강함',
                          'text-sky-400': band.label === '보통',
                          'text-slate-500': band.label === '약함',
                        })}>
                          {band.label}
                        </span>
                        {isFlaggable && (
                          <button
                            onClick={(e) => { e.stopPropagation(); handleFlag(h); }}
                            disabled={isFlagged || flaggingId === h.knowledgeId}
                            title={isFlagged ? '리뷰 신호로 등록됨' : '이 결과가 이상해요 — 리뷰 큐로 보내기'}
                            className={clsx(
                              'p-0.5 rounded transition-colors',
                              isFlagged
                                ? 'text-rose-400 cursor-default'
                                : 'text-slate-500 hover:text-rose-400 hover:bg-rose-950/40',
                            )}
                          >
                            {isFlagged ? <FlagOff className="w-3 h-3" /> : <Flag className="w-3 h-3" />}
                          </button>
                        )}
                      </div>
                    </div>
                    <span className={clsx('self-start text-[9px] px-1.5 py-0.5 rounded border', SOURCE_META[h.source].badgeClass)}>
                      {SOURCE_META[h.source].badge}
                    </span>
                    <span className="text-xs text-slate-200 font-medium line-clamp-2">{h.label}</span>
                    <p className="text-[11px] text-slate-400 line-clamp-3 flex-1">{h.snippet}</p>
                    <span className="text-[10px] font-mono text-slate-500 truncate">{h.scoreLabel}</span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      <Modal
        isOpen={!!activeHit}
        onClose={() => { setActiveHit(null); setIsEditing(false); }}
        title={activeHit?.label}
        maxWidth="max-w-2xl"
      >
        {activeHit && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <span className={clsx('text-[11px] px-2 py-0.5 rounded border', SOURCE_META[activeHit.source].badgeClass)}>
                {SOURCE_META[activeHit.source].badge}
              </span>
              <span className="text-xs font-mono text-slate-400">{activeHit.scoreLabel}</span>
              <span className="text-xs text-slate-600">rrf {activeHit.rrf.toFixed(4)}</span>
              <span
                className={clsx(
                  'flex items-center gap-1 text-[11px] font-medium px-1.5 py-0.5 rounded',
                  activeHit.adopted ? 'text-emerald-400 bg-emerald-950/40' : 'text-rose-400 bg-rose-950/40',
                )}
              >
                {activeHit.adopted ? <Check className="w-3 h-3" /> : <X className="w-3 h-3" />}
                {activeHit.adopted ? '채택됨' : '제외됨'}
              </span>
            </div>
            <p className="text-xs text-slate-500">{activeHit.adoptedReason}</p>

            {!isEditing ? (
              <>
                <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs bg-slate-900/60 border border-slate-700/60 rounded-lg px-3 py-2.5">
                  {activeHit.meta.map((m) => (
                    <div key={m.key} className="flex flex-col">
                      <span className="text-slate-500">{m.key}</span>
                      <span className="text-slate-300 truncate">{m.value}</span>
                    </div>
                  ))}
                </div>

                <p className="text-sm text-slate-200 leading-relaxed whitespace-pre-wrap">
                  {activeHit.fullContent}
                </p>

                <div className="flex flex-wrap gap-2">
                  {activeHit.source === 'general' && activeHit.knowledgeId != null && canModifyNs && (
                    <button
                      onClick={() => startEdit(activeHit)}
                      className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg border border-slate-700 text-slate-400 hover:text-indigo-400 hover:border-indigo-800/60 transition-colors"
                    >
                      <PenLine className="w-3.5 h-3.5" />수정
                    </button>
                  )}
                  {activeHit.source === 'general' && activeHit.knowledgeId != null && (
                    <button
                      onClick={() => handleFlag(activeHit)}
                      disabled={flaggedIds.has(activeHit.knowledgeId) || flaggingId === activeHit.knowledgeId}
                      className={clsx(
                        'flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg border transition-colors',
                        flaggedIds.has(activeHit.knowledgeId)
                          ? 'text-rose-400 border-rose-800/60 bg-rose-950/30 cursor-default'
                          : 'text-slate-400 border-slate-700 hover:text-rose-400 hover:border-rose-800/60',
                      )}
                    >
                      {flaggedIds.has(activeHit.knowledgeId) ? <FlagOff className="w-3.5 h-3.5" /> : <Flag className="w-3.5 h-3.5" />}
                      {flaggedIds.has(activeHit.knowledgeId) ? '리뷰 신호로 등록됨' : '이 결과가 이상해요 — 리뷰 큐로 보내기'}
                    </button>
                  )}
                </div>
              </>
            ) : (
              <div className="space-y-3">
                <div>
                  <label className="block text-xs font-medium text-slate-400 mb-1">업무구분</label>
                  <select
                    value={editForm.category}
                    onChange={(e) => setEditForm((f) => ({ ...f, category: e.target.value }))}
                    className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
                  >
                    <option value="" disabled>선택하세요</option>
                    {categoryNames.map((c) => <option key={c} value={c}>{c}</option>)}
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-400 mb-1">내용</label>
                  <textarea
                    rows={10}
                    value={editForm.content}
                    onChange={(e) => setEditForm((f) => ({ ...f, content: e.target.value }))}
                    className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 resize-y min-h-[220px] leading-relaxed"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-400 mb-1">
                    문서 우선순위: <span className="font-medium text-slate-300">{editForm.base_weight.toFixed(1)}</span>
                  </label>
                  <input
                    type="range" min={0} max={3} step={0.1} value={editForm.base_weight}
                    onChange={(e) => setEditForm((f) => ({ ...f, base_weight: parseFloat(e.target.value) }))}
                    className="w-full accent-indigo-500"
                  />
                </div>
                {updateMutation.error && (
                  <p className="text-xs text-rose-400">{String(updateMutation.error)}</p>
                )}
                <p className="text-[11px] text-slate-500">저장하면 내용이 재임베딩되어 점수·채택 여부가 바뀔 수 있어, 같은 질문으로 검색을 다시 실행합니다.</p>
                <div className="flex gap-2 justify-end pt-1">
                  <button
                    onClick={() => setIsEditing(false)}
                    className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg border border-slate-700 text-slate-400 hover:text-slate-200 transition-colors"
                  >
                    <X className="w-3.5 h-3.5" />취소
                  </button>
                  <button
                    onClick={() => activeHit.knowledgeId != null && updateMutation.mutate(activeHit.knowledgeId)}
                    disabled={!editForm.content.trim() || !editForm.category || updateMutation.isPending}
                    className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed text-white font-medium transition-colors"
                  >
                    <Check className="w-3.5 h-3.5" />{updateMutation.isPending ? '저장 중...' : '저장'}
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </Modal>
    </div>
  );
}
