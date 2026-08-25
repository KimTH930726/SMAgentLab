import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Mail, Plus, Trash2, Pencil, X, Save, Send, FlaskConical, AlertCircle, Inbox, Route, PlayCircle, KeyRound, History as HistoryIcon, ChevronDown, ChevronRight, BarChart3 } from 'lucide-react';
import { clsx } from 'clsx';
import {
  getEmailCollectionSettings, updateEmailCollectionSettings,
  listVocRouting, createVocRouting, updateVocRouting, deleteVocRouting,
  testAnalyzeEmail, testNotifyTeams, getMailFolders,
  getGraphCredentials, updateGraphCredentials,
  getDelegatedAuthStatus, updateDelegatedAuthConfig, startDelegatedAuth,
  runManualCollection, getEmailHistory, getKnowledgeRefs,
  getSchedulerStatus, getPollCycles,
  type VocRouting, type VocRoutingPayload, type EmailAnalysisResult,
  type ManualCollectionResult, type DelegatedAuthStartResult, type MailFolder,
  type EmailAnalysisHistoryItem, type PollCycleItem,
} from '../../api/emailVoc';
import { getNamespaces } from '../../api/namespaces';
import { VocStatsPanel } from './VocStatsPanel';
import { getAllParts } from '../../api/auth';
import { Button } from '../ui/Button';
import { Badge } from '../ui/Badge';
import { Modal } from '../ui/Modal';
import { ApiError } from '../../api/client';

const EMPTY_ROUTING_FORM: VocRoutingPayload = {
  part: '', mailbox_upn: '', teams_webhook_url: '', oncall_contact_name: '', oncall_contact_phone: '',
  mail_folder_id: '', mail_folder_name: '',
};

export const SEVERITY_LABEL: Record<string, string> = { low: '낮음', medium: '보통', high: '높음', urgent: '긴급' };
// 4단계가 전부 구분되도록 — 기존엔 낮음/보통이 둘 다 slate, 높음/긴급이 둘 다 rose라
// 사실상 2단계로만 보였음. Teams 카드(teams_notify.py의 _SEVERITY_COLOR)와 동일한
// 단계(회색→청록→호박→빨강)로 맞춰서 어디서 봐도 같은 심각도가 같은 색으로 읽히게 한다.
const SEVERITY_COLOR: Record<string, 'slate' | 'cyan' | 'amber' | 'rose'> = {
  low: 'slate', medium: 'cyan', high: 'amber', urgent: 'rose',
};
export const CATEGORY_LABEL: Record<string, string> = {
  system_error: '시스템 오류', user_mistake: '사용자 실수', uncertain: '판단 보류', not_it_related: 'IT 무관',
};

type VocSubTab = 'auth' | 'routing' | 'collect' | 'history' | 'stats' | 'analyze';

const SUB_TABS: { id: VocSubTab; label: string; icon: React.ReactNode }[] = [
  { id: 'auth', label: '1단계 · 로그인', icon: <KeyRound className="w-4 h-4" /> },
  { id: 'routing', label: '2단계 · 라우팅', icon: <Route className="w-4 h-4" /> },
  { id: 'collect', label: '3단계 · 폴링', icon: <Inbox className="w-4 h-4" /> },
  { id: 'history', label: '분석 이력', icon: <HistoryIcon className="w-4 h-4" /> },
  { id: 'stats', label: 'VOC 통계', icon: <BarChart3 className="w-4 h-4" /> },
  { id: 'analyze', label: '분석·알림 테스트', icon: <FlaskConical className="w-4 h-4" /> },
];

const inputClass = 'bg-slate-700 border border-slate-600 rounded px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500';

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function isValidHttpUrl(value: string): boolean {
  try {
    const parsed = new URL(value);
    return parsed.protocol === 'http:' || parsed.protocol === 'https:';
  } catch {
    return false;
  }
}

// 백엔드 메시지는 대부분 이미 사람이 읽을 수 있는 한국어 문장이라 그대로 노출하되,
// 원인을 알 수 없는 경우(라우트 자체가 없는 404, 네트워크 단절 등)에는 다음에
// 뭘 해야 하는지 안내를 붙인다 — 원본 메시지만 던지면 사용자가 뭘 어떻게 해야
// 할지 알 수 없다(예: "Not Found"만 보고는 재시도해야 할지조차 판단 불가).
function toErrorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 404) return `${e.message} — 서버에 연결할 수 없거나 대상이 삭제됐을 수 있습니다. 새로고침 후 다시 시도해 주세요.`;
    if (e.status === 401) return '로그인이 만료됐습니다 — 다시 로그인해 주세요.';
    if (e.status >= 500) return `서버 오류(${e.status}) — 잠시 후 다시 시도해 주세요. 반복되면 관리자에게 문의하세요.`;
    return e.message;
  }
  return '네트워크 오류 — 연결 상태를 확인하고 다시 시도해 주세요.';
}

function toDateInputValue(d: Date): string {
  return d.toISOString().slice(0, 10);
}
function defaultDateFrom(): string {
  const d = new Date();
  d.setDate(d.getDate() - 7);
  return toDateInputValue(d);
}
function defaultDateTo(): string {
  return toDateInputValue(new Date());
}
const STATUS_LABEL: Record<string, string> = {
  analyzed: '분석됨(미발송)', notified: '발송 성공', notify_failed: '발송 실패',
  skipped_relevance: '관련지식 부족(스킵)',
};

// 폴링 탭 설정 카드들이 전부 "숫자 하나 입력받아 blur 시 저장"이라 반복되던
// 인라인 <label><input> 6벌을 하나로 통일 — 라벨을 입력창 위에 둬서 값이 뭘
// 의미하는지 스캔하기 쉽게 하고, 부연 설명은 title 툴팁으로만 노출한다.
function NumberSetting({
  label, hint, value, min, max, step, onCommit,
}: { label: string; hint?: string; value: number; min?: number; max?: number; step?: number; onCommit: (v: number) => void }) {
  return (
    <label className="block" title={hint}>
      <span className="block text-xs text-slate-400 mb-1">{label}</span>
      <input
        type="number" min={min} max={max} step={step}
        defaultValue={value}
        key={`${label}-${value}`}
        onBlur={(e) => {
          const v = Number(e.target.value);
          if ((min === undefined || v >= min) && (max === undefined || v <= max)) onCommit(v);
        }}
        className="w-24 bg-slate-700 border border-slate-600 rounded px-2 py-1.5 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
      />
    </label>
  );
}

export function formatRelative(iso: string | null): string {
  if (!iso) return '-';
  const diffMs = Date.now() - new Date(iso).getTime();
  const diffSec = Math.round(diffMs / 1000);
  if (diffSec < 0) {
    const future = Math.abs(diffSec);
    if (future < 60) return `${future}초 후`;
    return `${Math.round(future / 60)}분 후`;
  }
  if (diffSec < 60) return `${diffSec}초 전`;
  if (diffSec < 3600) return `${Math.round(diffSec / 60)}분 전`;
  return `${Math.round(diffSec / 3600)}시간 전`;
}

// 조회 실패 시 빈 상태("데이터 없음")처럼 보이지 않도록, 해당 섹션 안에 원인과
// "다시 시도" 버튼을 바로 붙여서 보여준다 — 데이터가 정말 없는 것과 불러오기에
// 실패한 것을 사용자가 구분할 수 있어야 한다.
function QueryErrorNotice({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  return (
    <div className="flex items-center justify-between gap-3 px-4 py-3 bg-rose-500/10 border border-rose-500/30 rounded-lg">
      <div className="flex items-center gap-2 min-w-0">
        <AlertCircle className="w-4 h-4 text-rose-400 flex-shrink-0" />
        <p className="text-sm text-rose-300 truncate">{toErrorMessage(error)}</p>
      </div>
      <button onClick={onRetry} className="text-xs text-rose-300 underline flex-shrink-0 hover:text-rose-200">
        다시 시도
      </button>
    </div>
  );
}

export function VocEmailPanel() {
  const queryClient = useQueryClient();
  const [namespace, setNamespace] = useState('');
  const [error, setError] = useState('');
  const [subTab, setSubTab] = useState<VocSubTab>('collect');

  const { data: namespaces = [] } = useQuery<string[]>({ queryKey: ['namespaces'], queryFn: getNamespaces });
  useEffect(() => {
    if (namespaces.length > 0 && !namespace) setNamespace(namespaces[0]);
  }, [namespaces, namespace]);
  const selectedNs = namespace || namespaces[0] || '';

  // ── §9 폴링 설정 ──────────────────────────────────────────────────────────
  const { data: settings, isError: settingsError, error: settingsErrorObj, refetch: refetchSettings } = useQuery({
    queryKey: ['email-voc-settings'],
    queryFn: getEmailCollectionSettings,
  });

  const settingsMutation = useMutation({
    mutationFn: updateEmailCollectionSettings,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['email-voc-settings'] });
      setError('');
    },
    onError: (e) => setError(toErrorMessage(e)),
  });

  // ── 실시간 상태 + 폴링 이력 ─────────────────────────────────────────────
  // 30초마다 다시 조회 — "지금 잘 돌고 있는지"를 화면 새로고침 없이 확인할 수 있게.
  // 폴링 주기가 보통 분 단위라 너무 짧게 잡으면 API만 자주 호출하고 실제로 바뀌는
  // 내용은 거의 없다. "3단계 · 폴링" 탭에서만 화면에 보이므로, 다른 탭을 보는
  // 동안은 멈춰 불필요한 호출을 없앤다.
  const { data: schedulerStatus, isError: schedulerError, error: schedulerErrorObj, refetch: refetchScheduler } = useQuery({
    queryKey: ['email-voc-scheduler-status'],
    queryFn: getSchedulerStatus,
    refetchInterval: 30_000,
    enabled: subTab === 'collect',
  });
  const { data: pollCycles = [] } = useQuery({
    queryKey: ['email-voc-poll-cycles'],
    queryFn: () => getPollCycles(3),
    refetchInterval: 30_000,
    enabled: subTab === 'collect',
  });
  // "실패 N" 배지만 봐서는 왜 실패했는지 알 수 없다는 피드백 — 클릭하면 사유(메일함별
  // 에러 메시지, error_summary에 이미 저장돼 있음)를 모달로 보여준다.
  const [selectedFailedCycle, setSelectedFailedCycle] = useState<PollCycleItem | null>(null);

  // ── §10 라우팅 매핑 ───────────────────────────────────────────────────────
  const {
    data: routing = [], isLoading: routingLoading,
    isError: routingError, error: routingErrorObj, refetch: refetchRouting,
  } = useQuery({
    queryKey: ['voc-routing', selectedNs],
    queryFn: () => listVocRouting(selectedNs),
    enabled: !!selectedNs,
  });
  // 담당 파트를 자유 입력 대신 사용자관리에 등록된 실제 파트 중에서 고르도록 —
  // 오타로 존재하지 않는 파트명이 들어가는 걸 막는다.
  const { data: parts = [] } = useQuery({ queryKey: ['all-parts'], queryFn: getAllParts });

  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [form, setForm] = useState<VocRoutingPayload>(EMPTY_ROUTING_FORM);

  const createMutation = useMutation({
    mutationFn: (payload: VocRoutingPayload) => createVocRouting(selectedNs, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['voc-routing', selectedNs] });
      setShowForm(false);
      setForm(EMPTY_ROUTING_FORM);
      setError('');
    },
    onError: (e) => setError(toErrorMessage(e)),
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: VocRoutingPayload }) =>
      updateVocRouting(id, selectedNs, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['voc-routing', selectedNs] });
      setEditingId(null);
      setShowForm(false);
      setForm(EMPTY_ROUTING_FORM);
      setError('');
    },
    onError: (e) => setError(toErrorMessage(e)),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteVocRouting(id, selectedNs),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['voc-routing', selectedNs] });
      setError('');
    },
    onError: (e) => setError(toErrorMessage(e)),
  });

  const [folderOptions, setFolderOptions] = useState<MailFolder[] | null>(null);
  const loadFoldersMutation = useMutation({
    mutationFn: () => getMailFolders(form.mailbox_upn),
    onSuccess: (folders) => { setFolderOptions(folders); setError(''); },
    onError: (e) => setError(toErrorMessage(e)),
  });

  const startEdit = (r: VocRouting) => {
    setEditingId(r.id);
    setForm({
      part: r.part, mailbox_upn: r.mailbox_upn,
      teams_webhook_url: r.teams_webhook_url ?? '',
      oncall_contact_name: r.oncall_contact_name ?? '',
      oncall_contact_phone: r.oncall_contact_phone ?? '',
      mail_folder_id: r.mail_folder_id ?? '',
      mail_folder_name: r.mail_folder_name ?? '',
    });
    setFolderOptions(null);
    setShowForm(true);
  };

  const startCreate = () => {
    setEditingId(null);
    setForm(EMPTY_ROUTING_FORM);
    setFolderOptions(null);
    setShowForm(true);
  };

  const handleSubmit = () => {
    if (!form.part.trim() || !form.mailbox_upn.trim()) {
      setError('담당 파트와 메일함 주소는 필수입니다.');
      return;
    }
    if (!EMAIL_RE.test(form.mailbox_upn.trim())) {
      setError('메일함 UPN 형식이 올바르지 않습니다 (예: name@company.com).');
      return;
    }
    if (form.teams_webhook_url?.trim() && !isValidHttpUrl(form.teams_webhook_url.trim())) {
      setError('Teams 웹훅 URL 형식이 올바르지 않습니다 (https://로 시작하는 전체 URL).');
      return;
    }
    if (editingId != null) {
      updateMutation.mutate({ id: editingId, payload: form });
    } else {
      createMutation.mutate(form);
    }
  };

  // ── Graph API 자격증명 (§7 Q10 승인 후 관리자가 수기 입력) ───────────────
  const { data: graphCreds } = useQuery({ queryKey: ['graph-credentials'], queryFn: getGraphCredentials });
  const [credTenantId, setCredTenantId] = useState('');
  const [credClientId, setCredClientId] = useState('');
  const [credClientSecret, setCredClientSecret] = useState('');

  const credsMutation = useMutation({
    mutationFn: () => updateGraphCredentials({ tenant_id: credTenantId, client_id: credClientId, client_secret: credClientSecret }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['graph-credentials'] });
      setCredClientSecret('');
      setError('');
    },
    onError: (e) => setError(toErrorMessage(e)),
  });

  // ── Delegated 권한 로그인 (Application 권한/Track B 승인 전 임시 경로) ─────
  // Authorization Code Flow(PKCE) — 로그인 링크를 열면 Microsoft가 이 서버의
  // 콜백으로 리다이렉트시키고, 그 콜백이 로그인을 완료시킨다. 관리자 화면은
  // 진행 중(pending)일 때만 짧은 주기로 상태를 폴링해 완료 여부를 감지한다.
  const delegatedRedirectUri = `${window.location.origin}/api/email-voc/delegated-auth/callback`;
  const { data: delegatedStatus } = useQuery({
    queryKey: ['delegated-auth-status'],
    queryFn: getDelegatedAuthStatus,
    refetchInterval: (query) => (query.state.data?.pending ? 3_000 : false),
  });
  const [delegatedTenantId, setDelegatedTenantId] = useState('');
  const [delegatedClientId, setDelegatedClientId] = useState('');
  const [delegatedClientSecret, setDelegatedClientSecret] = useState('');
  const [delegatedAuthUrl, setDelegatedAuthUrl] = useState<string | null>(null);

  const delegatedConfigMutation = useMutation({
    mutationFn: () => updateDelegatedAuthConfig({
      tenant_id: delegatedTenantId, client_id: delegatedClientId, redirect_uri: delegatedRedirectUri,
      client_secret: delegatedClientSecret || undefined,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['delegated-auth-status'] });
      setDelegatedClientSecret(''); // 저장 후 화면에 값이 남아있지 않게 즉시 비움
      setError('');
    },
    onError: (e) => setError(toErrorMessage(e)),
  });
  const delegatedStartMutation = useMutation({
    mutationFn: startDelegatedAuth,
    onSuccess: (result: DelegatedAuthStartResult) => {
      setDelegatedAuthUrl(result.auth_url);
      // 새 탭으로 바로 열어준다 — 팝업이 차단되면 아래 폴백 링크를 눌러야 함
      window.open(result.auth_url, '_blank', 'noopener,noreferrer');
      queryClient.invalidateQueries({ queryKey: ['delegated-auth-status'] });
      setError('');
    },
    onError: (e) => setError(toErrorMessage(e)),
  });

  // ── 폴링: 수동 1회성 실행 (§9, §5 Phase 1) ─────────────────────────────
  const [collectDateFrom, setCollectDateFrom] = useState(defaultDateFrom());
  const [collectDateTo, setCollectDateTo] = useState(defaultDateTo());
  const [collectResult, setCollectResult] = useState<ManualCollectionResult | null>(null);

  const dateRangeError = (() => {
    if (!collectDateFrom || !collectDateTo) return '시작일과 종료일을 모두 입력해주세요.';
    if (collectDateFrom > collectDateTo) return '시작일은 종료일보다 이후일 수 없습니다.';
    if (collectDateTo > defaultDateTo()) return '종료일은 오늘보다 미래일 수 없습니다.';
    const days = (new Date(collectDateTo).getTime() - new Date(collectDateFrom).getTime()) / 86_400_000;
    if (days > 90) return '조회 기간은 최대 90일까지만 가능합니다.';
    return null;
  })();

  const collectMutation = useMutation({
    mutationFn: () => runManualCollection(selectedNs, collectDateFrom, collectDateTo),
    onSuccess: (result) => {
      setCollectResult(result);
      setError('');
      queryClient.invalidateQueries({ queryKey: ['email-history', selectedNs] });
    },
    onError: (e) => setError(toErrorMessage(e)),
  });

  // ── 이력 ──────────────────────────────────────────────────────────────
  const [selectedHistoryItem, setSelectedHistoryItem] = useState<EmailAnalysisHistoryItem | null>(null);
  // 참조 지식 섹션은 기본 접힘 — 펼쳤을 때만 내용을 가져온다(불필요한 조회 방지).
  const [knowledgeRefsOpen, setKnowledgeRefsOpen] = useState(false);
  // 이력 상세 모달에서 "참조 지식 ID"가 실제로 뭘 가리키는지 바로 보여주기 위해
  // 펼쳤을 때 그 ID들의 실제 내용을 가져온다 — ID만 나열하면 관리자가 매번
  // 지식 관리 탭에서 따로 검색해야 해서 실사용 중 불편하다는 피드백으로 추가.
  const { data: selectedKnowledgeRefs = [], isLoading: knowledgeRefsLoading } = useQuery({
    queryKey: ['voc-knowledge-refs', selectedHistoryItem?.id, selectedNs],
    queryFn: () => getKnowledgeRefs(selectedNs, selectedHistoryItem!.knowledge_ref_ids),
    enabled: !!selectedHistoryItem && selectedHistoryItem.knowledge_ref_ids.length > 0 && knowledgeRefsOpen,
  });
  const [historyOffset, setHistoryOffset] = useState(0);
  const HISTORY_PAGE_SIZE = 30;
  const [historySeverity, setHistorySeverity] = useState('');
  const [historyStatus, setHistoryStatus] = useState('');
  const [historyMismatchOnly, setHistoryMismatchOnly] = useState(false);
  const [historyKeywordDraft, setHistoryKeywordDraft] = useState(''); // 입력 중(미확정)
  const [historyKeyword, setHistoryKeyword] = useState(''); // 검색 버튼/Enter로 확정된 값 — 이 값만 쿼리에 씀(타이핑마다 요청 안 나가게)
  // namespace/필터가 바뀌면 이력 페이지는 항상 1페이지로 리셋되어야 한다 —
  // 이전 offset이 남아있으면 필터링된 결과가 적을 때 빈 페이지가 나오는 버그가 생긴다.
  useEffect(() => {
    setHistoryOffset(0);
  }, [selectedNs, historySeverity, historyStatus, historyMismatchOnly, historyKeyword]);
  const {
    data: history = [], isLoading: historyLoading,
    isError: historyError, error: historyErrorObj, refetch: refetchHistory,
  } = useQuery({
    queryKey: ['email-history', selectedNs, historyOffset, historySeverity, historyStatus, historyMismatchOnly, historyKeyword],
    queryFn: () => getEmailHistory(selectedNs, HISTORY_PAGE_SIZE, historyOffset, {
      severity: historySeverity || undefined, status: historyStatus || undefined,
      mismatchOnly: historyMismatchOnly, keyword: historyKeyword || undefined,
    }),
    enabled: !!selectedNs && subTab === 'history',
  });
  const historyFiltersActive = !!(historySeverity || historyStatus || historyMismatchOnly || historyKeyword);
  const clearHistoryFilters = () => {
    setHistorySeverity(''); setHistoryStatus(''); setHistoryMismatchOnly(false);
    setHistoryKeywordDraft(''); setHistoryKeyword('');
  };

  // ── 번외: 분석 테스트 (§11 Track A #2) ─────────────────────────────────
  const [testSubject, setTestSubject] = useState('결제 오류 문의');
  const [testBody, setTestBody] = useState('고객이 결제 시도 시 500 에러가 계속 발생한다고 합니다.');
  const [testPart, setTestPart] = useState('');
  const [testResult, setTestResult] = useState<EmailAnalysisResult | null>(null);

  const analyzeMutation = useMutation({
    mutationFn: () => testAnalyzeEmail(selectedNs, testSubject, testBody, testPart || undefined),
    onSuccess: (result) => { setTestResult(result); setError(''); },
    onError: (e) => setError(toErrorMessage(e)),
  });

  // ── 번외: Teams 알림 테스트 (§11 Track A #3) — 분석 없이도 독립적으로 사용 가능 ──
  const [notifyWebhookUrl, setNotifyWebhookUrl] = useState('');
  const [notifySubject, setNotifySubject] = useState('(테스트) VOC 알림');
  const [notifySender, setNotifySender] = useState('user@example.com');
  const [notifyPart, setNotifyPart] = useState('');
  const [notifyCategory, setNotifyCategory] = useState<'system_error' | 'user_mistake' | 'uncertain' | 'not_it_related'>('system_error');
  const [notifySeverity, setNotifySeverity] = useState<'low' | 'medium' | 'high' | 'urgent'>('high');
  const [notifyMismatch, setNotifyMismatch] = useState(false);
  const [notifyResolutionDraft, setNotifyResolutionDraft] = useState('');
  const [notifyOncallName, setNotifyOncallName] = useState('');
  const [notifySent, setNotifySent] = useState<string | null>(null);

  const loadFromLastAnalysis = () => {
    if (!testResult) return;
    setNotifySubject(testSubject);
    setNotifyPart(testPart);
    setNotifyCategory(testResult.category as typeof notifyCategory);
    setNotifySeverity(testResult.severity as typeof notifySeverity);
    setNotifyMismatch(testResult.mismatch_flagged);
    setNotifyResolutionDraft(testResult.resolution_draft ?? '');
  };

  const notifyMutation = useMutation({
    mutationFn: () => testNotifyTeams({
      webhook_url: notifyWebhookUrl,
      subject: notifySubject,
      sender: notifySender,
      part: notifyPart,
      category: notifyCategory,
      severity: notifySeverity,
      mismatch_flagged: notifyMismatch,
      resolution_draft: notifyResolutionDraft || null,
      oncall_contact_name: notifyOncallName || null,
    }),
    onSuccess: () => { setNotifySent('발송 완료 — 등록한 Teams 채널에서 카드를 확인하세요.'); setError(''); },
    onError: (e) => { setNotifySent(null); setError(toErrorMessage(e)); },
  });

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-slate-100 flex items-center gap-2">
          <Mail className="w-5 h-5 text-indigo-400" />
          VOC 이메일 분석 채널
        </h2>
        <p className="text-sm text-slate-400 mt-0.5">메일 수신 → 분석 → Teams 알림 자동화</p>
      </div>

      {error && (
        <div className="flex items-center gap-3 px-4 py-3 bg-rose-500/10 border border-rose-500/30 rounded-lg">
          <AlertCircle className="w-4 h-4 text-rose-400 flex-shrink-0" />
          <p className="text-sm text-rose-300 flex-1">{error}</p>
          <button onClick={() => setError('')} className="text-rose-400 hover:text-rose-200 flex-shrink-0">
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* 서브 탭 */}
      <div className="border-b border-slate-700">
        <div className="flex gap-1">
          {SUB_TABS.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setSubTab(tab.id)}
              className={clsx(
                'flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors',
                subTab === tab.id
                  ? 'text-indigo-400 border-indigo-500'
                  : 'text-slate-400 border-transparent hover:text-slate-200 hover:border-slate-600',
              )}
            >
              {tab.icon}
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* ── 인증 · 자격증명 ── */}
      {subTab === 'auth' && (
        <section className="space-y-3">
          {/* Graph API 자격증명 */}
          <div>
            <h3 className="text-sm font-semibold text-slate-300 flex items-center gap-2 mb-2">
              <KeyRound className="w-4 h-4" /> Graph API 자격증명
              <Badge color={graphCreds?.configured ? 'emerald' : 'slate'}>
                {graphCreds?.configured ? '설정됨' : '미설정'}
              </Badge>
            </h3>
            <p className="text-xs text-slate-500 mb-2" title="승인 전이면 비워두세요 — 아래 개인 계정 로그인으로 대체 가능합니다">
              IT 승인 후 발급받는 값 (선택)
            </p>
            <div className="bg-slate-800 border border-slate-700 rounded-lg p-4 space-y-3">
              {graphCreds?.configured && (
                <p className="text-xs text-slate-400">
                  현재: tenant_id=<span className="font-mono">{graphCreds.tenant_id}</span>, client_id=<span className="font-mono">{graphCreds.client_id}</span> (client_secret은 다시 표시되지 않습니다)
                </p>
              )}
              <div className="grid grid-cols-3 gap-3">
                <label className="block text-xs text-slate-400">
                  Tenant ID (Directory ID)
                  <input placeholder="예: d4ffc887-..." value={credTenantId} onChange={(e) => setCredTenantId(e.target.value)} className={clsx('w-full mt-1', inputClass)} />
                </label>
                <label className="block text-xs text-slate-400">
                  Client ID (Application ID)
                  <input placeholder="예: bfdb9f4f-..." value={credClientId} onChange={(e) => setCredClientId(e.target.value)} className={clsx('w-full mt-1', inputClass)} />
                </label>
                <label className="block text-xs text-slate-400">
                  Client Secret
                  <input placeholder="IT에서 발급받은 값" type="password" value={credClientSecret} onChange={(e) => setCredClientSecret(e.target.value)} className={clsx('w-full mt-1', inputClass)} />
                </label>
              </div>
              <Button
                size="sm" variant="secondary"
                onClick={() => credsMutation.mutate()}
                loading={credsMutation.isPending}
                disabled={!credTenantId.trim() || !credClientId.trim() || !credClientSecret.trim()}
              >
                <Save className="w-3.5 h-3.5" /> 자격증명 저장
              </Button>
            </div>
          </div>

          {/* 개인 계정 로그인 (Delegated) — Application 권한 승인 전 임시 경로 */}
          <div className="pt-2">
            <h3 className="text-sm font-semibold text-slate-300 flex items-center gap-2 mb-2">
              <KeyRound className="w-4 h-4" /> 개인 계정 로그인 (Delegated, 임시)
              <Badge color={delegatedStatus?.logged_in ? 'emerald' : 'slate'}>
                {delegatedStatus?.logged_in ? `로그인됨 · ${delegatedStatus.account}` : '로그인 필요'}
              </Badge>
              {delegatedStatus?.client_secret_configured && (
                <Badge color="indigo">Confidential Client (secret 설정됨)</Badge>
              )}
            </h3>
            <p
              className="text-xs text-slate-500 mb-2"
              title="Application 권한이 설정돼 있으면 그쪽이 우선 사용됩니다. 라우팅 탭의 메일함 UPN을 아래 로그인 계정과 동일하게 등록하세요."
            >
              Application 권한 승인 전 임시 로그인 — 본인 메일함 기준으로 전체 플로우 테스트 가능
            </p>
            <div className="bg-slate-800 border border-slate-700 rounded-lg p-4 space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <label className="block text-xs text-slate-400">
                  Tenant ID (Directory ID)
                  <input placeholder="예: d4ffc887-..." value={delegatedTenantId} onChange={(e) => setDelegatedTenantId(e.target.value)} className={clsx('w-full mt-1', inputClass)} />
                </label>
                <label className="block text-xs text-slate-400">
                  Client ID (Application ID — Object ID 아님)
                  <input placeholder="예: bfdb9f4f-..." value={delegatedClientId} onChange={(e) => setDelegatedClientId(e.target.value)} className={clsx('w-full mt-1', inputClass)} />
                </label>
              </div>
              <label className="block text-xs text-slate-400" title="자동 계산됨 — Azure AD 앱 등록 시 이 값 그대로 등록하세요">
                리다이렉트 URL
                <input readOnly value={delegatedRedirectUri} className={clsx('w-full mt-1 font-mono text-xs', inputClass)} onFocus={(e) => e.target.select()} />
              </label>
              <label
                className="block text-xs text-slate-400"
                title="리다이렉트 URI가 Azure AD에 'Web' 플랫폼으로 등록돼 PKCE만으로 토큰 교환이 거부될 때만 입력하세요"
              >
                Client Secret (선택)
                <input
                  placeholder={delegatedStatus?.client_secret_configured ? '설정됨 — 변경하려면 새 값 입력' : 'Client Secret'}
                  type="password" value={delegatedClientSecret}
                  onChange={(e) => setDelegatedClientSecret(e.target.value)}
                  className={clsx('w-full mt-1', inputClass)}
                />
              </label>
              <div className="flex flex-wrap items-center gap-3">
                <Button
                  size="sm" variant="secondary"
                  onClick={() => delegatedConfigMutation.mutate()}
                  loading={delegatedConfigMutation.isPending}
                  disabled={!delegatedTenantId.trim() || !delegatedClientId.trim()}
                >
                  <Save className="w-3.5 h-3.5" /> 앱 정보 저장
                </Button>
                <Button
                  size="sm"
                  onClick={() => delegatedStartMutation.mutate()}
                  loading={delegatedStartMutation.isPending || !!delegatedStatus?.pending}
                  disabled={!delegatedStatus?.configured}
                >
                  {delegatedStatus?.logged_in ? '다시 로그인' : '로그인 시작'}
                </Button>
              </div>

              {(delegatedStatus?.pending || delegatedStatus?.login_error || (delegatedStatus?.logged_in && delegatedAuthUrl)) && (
                <div className={clsx(
                  'border rounded-lg p-3 text-sm',
                  delegatedStatus?.pending ? 'border-indigo-500/30 bg-indigo-500/10'
                    : delegatedStatus?.login_error ? 'border-rose-500/30 bg-rose-500/10'
                    : 'border-emerald-500/30 bg-emerald-500/10',
                )}>
                  {delegatedStatus?.pending ? (
                    <>
                      <p className="text-slate-200">새 탭에서 로그인 창이 열렸습니다. 안 열렸다면 아래 링크를 직접 클릭하세요(팝업 차단 가능성).</p>
                      {delegatedAuthUrl && (
                        <a
                          href={delegatedAuthUrl} target="_blank" rel="noopener noreferrer"
                          className="text-indigo-400 underline text-xs break-all"
                        >
                          로그인 페이지 열기
                        </a>
                      )}
                      <p className="text-xs text-slate-500 mt-1">로그인 완료되면 이 화면이 자동으로 갱신됩니다 (3초마다 확인 중).</p>
                    </>
                  ) : delegatedStatus?.login_error ? (
                    <p className="text-rose-400">로그인 실패 — {delegatedStatus.login_error} (다시 로그인을 눌러 재시도하세요)</p>
                  ) : delegatedStatus?.logged_in ? (
                    <p className="text-emerald-400">로그인 완료 — 이제 아래 "지금 실행" 또는 위 "폴링 자동화 ON"이 본인 메일함을 대상으로 동작합니다.</p>
                  ) : null}
                </div>
              )}
            </div>
          </div>
        </section>
      )}

      {/* ── 폴링 설정 ── */}
      {subTab === 'collect' && (
        <section className="space-y-3">
          {schedulerError && <QueryErrorNotice error={schedulerErrorObj} onRetry={() => refetchScheduler()} />}
          {settingsError && <QueryErrorNotice error={settingsErrorObj} onRetry={() => refetchSettings()} />}
          {/* 실시간 상태 */}
          <div className={clsx(
            'border rounded-xl p-4',
            schedulerStatus?.enabled
              ? (schedulerStatus?.is_running_now ? 'bg-indigo-500/10 border-indigo-500/30' : 'bg-emerald-500/5 border-emerald-500/20')
              : 'bg-slate-800 border-slate-700',
          )}>
            <div className="flex items-center justify-between gap-2 mb-3">
              <div className="flex items-center gap-2">
                <span className={clsx(
                  'w-2.5 h-2.5 rounded-full',
                  !schedulerStatus?.enabled ? 'bg-slate-500'
                    : schedulerStatus?.is_running_now ? 'bg-indigo-400 animate-pulse shadow-[0_0_8px_rgba(129,140,248,0.7)]' : 'bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.5)]',
                )} />
                <span className="text-sm font-semibold text-slate-100">
                  {!schedulerStatus?.enabled ? '폴링 꺼짐'
                    : schedulerStatus?.is_running_now ? '지금 수집 중'
                    : '정상 동작 중'}
                </span>
              </div>
              <span className="text-[11px] text-slate-500" title="아래 메일함 확인 주기와는 무관 — 이 화면이 최신 상태를 보여주는 주기입니다">
                30초마다 자동 새로고침
              </span>
            </div>
            {schedulerStatus?.enabled && (
              <div className="grid grid-cols-2 sm:grid-cols-4 divide-x divide-slate-700/60 rounded-lg overflow-hidden border border-slate-700/50 bg-slate-900/30">
                <div className="px-3 py-2.5">
                  <div className="text-[11px] text-slate-500">마지막 실행</div>
                  <div className="text-sm text-slate-200 font-medium mt-0.5">{formatRelative(schedulerStatus.last_cycle?.started_at ?? null)}</div>
                </div>
                <div className="px-3 py-2.5">
                  <div className="text-[11px] text-slate-500">다음 확인</div>
                  <div className="text-sm text-slate-200 font-medium mt-0.5">{formatRelative(schedulerStatus.next_estimated_at)}</div>
                </div>
                <div className="px-3 py-2.5">
                  <div className="text-[11px] text-slate-500">마지막 결과</div>
                  <div className="mt-0.5">
                    {schedulerStatus.last_cycle ? (
                      <Badge color={schedulerStatus.last_cycle.mailboxes_failed > 0 ? 'rose' : 'emerald'}>
                        성공 {schedulerStatus.last_cycle.mailboxes_ok} / 실패 {schedulerStatus.last_cycle.mailboxes_failed}
                      </Badge>
                    ) : (
                      <span className="text-sm text-slate-500">이력 없음</span>
                    )}
                  </div>
                </div>
                <div className="px-3 py-2.5">
                  <div className="text-[11px] text-slate-500">분석/발송</div>
                  <div className="text-sm text-slate-200 font-medium mt-0.5">
                    {schedulerStatus.last_cycle ? `${schedulerStatus.last_cycle.total_analyzed}건 / ${schedulerStatus.last_cycle.total_notified}건` : '-'}
                  </div>
                </div>
              </div>
            )}
            {schedulerStatus?.last_cycle?.error_summary && (
              <p className="flex items-center gap-1.5 text-xs text-amber-400 mt-3 px-3 py-2 bg-amber-500/10 border border-amber-500/20 rounded-lg">
                <AlertCircle className="w-3.5 h-3.5 flex-shrink-0" /> {schedulerStatus.last_cycle.error_summary}
              </p>
            )}
          </div>

          <div className="grid grid-cols-3 gap-3">
            <div className="bg-slate-800 border border-slate-700 rounded-lg p-4 space-y-3">
              <div>
                <h4 className="text-xs font-semibold text-slate-300">폴링 동작</h4>
                <p className="text-[11px] text-slate-500 mt-0.5">언제, 얼마나 자주 메일함을 확인할지</p>
              </div>
              <button
                onClick={() => settingsMutation.mutate({ email_collection_enabled: !(settings?.email_collection_enabled ?? false) })}
                disabled={settingsMutation.isPending}
                className={clsx(
                  'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors w-fit',
                  settings?.email_collection_enabled
                    ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400 hover:bg-emerald-500/20'
                    : 'bg-slate-700 border-slate-600 text-slate-400 hover:bg-slate-600',
                )}
              >
                <span className={clsx('w-2 h-2 rounded-full', settings?.email_collection_enabled ? 'bg-emerald-400' : 'bg-slate-500')} />
                폴링 자동화 {settings?.email_collection_enabled ? 'ON' : 'OFF'}
              </button>
              <NumberSetting
                label="확인 주기(분)" hint="실제로 몇 분마다 메일함에 접속해 새 메일을 확인하는지 — 위 '화면 자동 새로고침'과는 별개입니다"
                value={settings?.email_polling_interval_minutes ?? 5} min={1}
                onCommit={(v) => settingsMutation.mutate({ email_polling_interval_minutes: v })}
              />
              <NumberSetting
                label="재조회 기간(일)" hint="며칠 전 메일까지 다시 훑어 새 메일인지 확인할지"
                value={settings?.email_lookback_days ?? 7} min={1}
                onCommit={(v) => settingsMutation.mutate({ email_lookback_days: v })}
              />
            </div>

            <div className="bg-slate-800 border border-slate-700 rounded-lg p-4 space-y-3">
              <div>
                <h4 className="text-xs font-semibold text-slate-300">관련성 필터</h4>
                <p className="text-[11px] text-slate-500 mt-0.5">우리 지식과 무관한 메일을 LLM 호출 전에 걸러냄</p>
              </div>
              <NumberSetting
                label="관련지식 임계치(0~1)" hint="등록된 지식과의 최고 유사도가 이 값 미만이면 LLM 분석·Teams 발송 없이 건너뜁니다 — 높이면 노이즈↓ 놓치는 것↑"
                value={settings?.email_relevance_min_score ?? 0.35} min={0} max={1} step={0.05}
                onCommit={(v) => settingsMutation.mutate({ email_relevance_min_score: v })}
              />
            </div>

            <div className="bg-slate-800 border border-slate-700 rounded-lg p-4 space-y-3">
              <div>
                <h4 className="text-xs font-semibold text-slate-300">반복 패턴 탐지</h4>
                <p className="text-[11px] text-slate-500 mt-0.5">같은 유형 VOC가 몰리면 담당자에게 별도 알림</p>
              </div>
              <NumberSetting
                label="유사도(0~1)" hint="과거 VOC와 이 유사도 이상이면 같은 반복 유형으로 묶습니다 — 실 데이터로 검증한 기본값 0.85"
                value={settings?.email_pattern_similarity_threshold ?? 0.85} min={0} max={1} step={0.05}
                onCommit={(v) => settingsMutation.mutate({ email_pattern_similarity_threshold: v })}
              />
              <NumberSetting
                label="판정 기간(일)" hint="이 기간 안에 발생한 VOC끼리만 반복 여부를 비교합니다"
                value={settings?.email_pattern_window_days ?? 7} min={1}
                onCommit={(v) => settingsMutation.mutate({ email_pattern_window_days: v })}
              />
              <NumberSetting
                label="최소 건수" hint="이 건수 이상 반복돼야 '반복 패턴 감지' Teams 알림을 발송합니다(이후 늘어나도 재알림 없음)"
                value={settings?.email_pattern_min_count ?? 3} min={2}
                onCommit={(v) => settingsMutation.mutate({ email_pattern_min_count: v })}
              />
            </div>
          </div>

          {/* 수동 1회성 실행 */}
          <div className="pt-2">
            <h3 className="text-sm font-semibold text-slate-300 flex items-center gap-2 mb-2">
              <PlayCircle className="w-4 h-4" /> 수동 1회성 실행
            </h3>
            <p className="text-xs text-slate-500 mb-2">지정 기간을 즉시 1회 수집+분석+발송</p>
            <div className="bg-slate-800 border border-slate-700 rounded-lg p-4 space-y-3">
              <div className="flex flex-wrap items-center gap-3">
                <label className="flex items-center gap-2 text-sm text-slate-300">
                  From
                  <input
                    type="date" value={collectDateFrom} max={collectDateTo}
                    onChange={(e) => setCollectDateFrom(e.target.value)}
                    className="bg-slate-700 border border-slate-600 rounded px-2 py-1 text-slate-200 focus:outline-none focus:border-indigo-500"
                  />
                </label>
                <label className="flex items-center gap-2 text-sm text-slate-300">
                  To
                  <input
                    type="date" value={collectDateTo} max={defaultDateTo()}
                    onChange={(e) => setCollectDateTo(e.target.value)}
                    className="bg-slate-700 border border-slate-600 rounded px-2 py-1 text-slate-200 focus:outline-none focus:border-indigo-500"
                  />
                </label>
                <Button
                  size="sm" onClick={() => collectMutation.mutate()}
                  loading={collectMutation.isPending}
                  disabled={!selectedNs || !!dateRangeError}
                >
                  지금 실행
                </Button>
              </div>
              {dateRangeError && <p className="text-xs text-rose-400">{dateRangeError}</p>}

              {collectResult && (
                <div className="mt-2 space-y-2">
                  <p className="text-xs text-slate-400">
                    {collectResult.date_from} ~ {collectResult.date_to} 실행 결과 ({collectResult.mailboxes.length}개 메일함)
                  </p>
                  {collectResult.mailboxes.length === 0 ? (
                    <p className="text-xs text-slate-500">등록된 라우팅 매핑이 없습니다 — "2단계 · 라우팅" 탭에서 먼저 파트/메일함을 등록하세요.</p>
                  ) : (
                    <div className="border border-slate-700 rounded-lg overflow-hidden">
                      <table className="w-full text-xs">
                        <thead>
                          <tr className="border-b border-slate-700 bg-slate-800/50">
                            <th className="text-left px-3 py-2 text-slate-400">메일함</th>
                            <th className="text-left px-3 py-2 text-slate-400">결과</th>
                            <th className="text-left px-3 py-2 text-slate-400">수집/분석/중복/관련지식부족/IT무관/발송/발송실패</th>
                          </tr>
                        </thead>
                        <tbody>
                          {collectResult.mailboxes.map((m) => (
                            <tr key={m.mailbox_upn} className="border-b border-slate-700/50 last:border-0">
                              <td className="px-3 py-2 text-slate-300 font-mono">{m.mailbox_upn}</td>
                              <td className="px-3 py-2">
                                {m.ok
                                  ? <Badge color="emerald">정상</Badge>
                                  : <span className="text-rose-400">{m.error}</span>}
                              </td>
                              <td className="px-3 py-2 text-slate-400">
                                {m.fetched} / {m.analyzed} / {m.skipped_duplicate} / {m.skipped_low_relevance} / {m.skipped_not_it} / {m.notified} / {m.notify_failed}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>

          {/* 폴링 이력 — 사이클 단위 성공/실패 */}
          <div className="pt-2">
            <h3 className="text-sm font-semibold text-slate-300 mb-2">폴링 이력 (최근 3건)</h3>
            {pollCycles.length === 0 ? (
              <div className="text-sm text-slate-500 py-6 text-center border border-dashed border-slate-700 rounded-lg">
                아직 실행된 사이클이 없습니다.
              </div>
            ) : (
              <div className="border border-slate-700 rounded-lg overflow-hidden">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-slate-700 bg-slate-800/50">
                      <th className="text-left px-3 py-2 text-slate-400">실행 시각</th>
                      <th className="text-left px-3 py-2 text-slate-400">namespace 수</th>
                      <th className="text-left px-3 py-2 text-slate-400">성공/실패 메일함</th>
                      <th className="text-left px-3 py-2 text-slate-400">분석/발송/관련지식부족/IT무관</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pollCycles.map((c) => (
                      <tr key={c.id} className="border-b border-slate-700/50 last:border-0">
                        <td className="px-3 py-2 text-slate-300">{formatRelative(c.started_at)}</td>
                        <td className="px-3 py-2 text-slate-400">{c.namespaces_processed}</td>
                        <td className="px-3 py-2">
                          {c.mailboxes_failed > 0 ? (
                            <button
                              type="button"
                              onClick={() => setSelectedFailedCycle(c)}
                              className="cursor-pointer hover:opacity-80 transition-opacity"
                              title="클릭하면 실패 사유를 볼 수 있습니다"
                            >
                              <Badge color="rose">성공 {c.mailboxes_ok} / 실패 {c.mailboxes_failed}</Badge>
                            </button>
                          ) : (
                            <Badge color="emerald">성공 {c.mailboxes_ok} / 실패 {c.mailboxes_failed}</Badge>
                          )}
                        </td>
                        <td className="px-3 py-2 text-slate-400">{c.total_analyzed}건 / {c.total_notified}건 / {c.total_skipped_low_relevance}건 / {c.total_skipped_not_it}건</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </section>
      )}

      {/* ── 번외: 분석 테스트 ── */}
      {subTab === 'analyze' && (
        <section className="space-y-3">
          <p className="text-xs text-slate-500">텍스트 직접 입력으로 분류/심각도/오배치 판정 테스트</p>
          <div className="bg-slate-800 border border-slate-700 rounded-lg p-4 space-y-3">
            <label className="block text-xs text-slate-400">
              메일 제목
              <input
                placeholder="예: 결제 오류 문의" value={testSubject} onChange={(e) => setTestSubject(e.target.value)}
                className={clsx('w-full mt-1', inputClass)}
              />
            </label>
            <label className="block text-xs text-slate-400">
              메일 본문
              <textarea
                placeholder="분석할 메일 본문 텍스트" value={testBody} onChange={(e) => setTestBody(e.target.value)}
                rows={3}
                className={clsx('w-full mt-1', inputClass)}
              />
            </label>
            <label className="block text-xs text-slate-400" title="예: 결제팀이 받았는데 배송 문의면 '오배치 의심'으로 판정">
              수신 담당 파트 (선택 — 오배치 판정용)
              <input
                placeholder="예: 결제팀" value={testPart} onChange={(e) => setTestPart(e.target.value)}
                className={clsx('w-full mt-1', inputClass)}
              />
            </label>
            <Button size="sm" onClick={() => analyzeMutation.mutate()} loading={analyzeMutation.isPending} disabled={!selectedNs || !testBody.trim()}>
              분석 실행
            </Button>

            {testResult && (
              <div className="mt-3 bg-slate-900/50 border border-slate-700 rounded-lg p-4 space-y-2 text-sm">
                <div className="flex flex-wrap gap-2">
                  <Badge color="slate">{CATEGORY_LABEL[testResult.category] ?? testResult.category}</Badge>
                  <Badge color={SEVERITY_COLOR[testResult.severity] ?? 'slate'}>
                    심각도: {SEVERITY_LABEL[testResult.severity] ?? testResult.severity}
                  </Badge>
                  {testResult.mismatch_flagged && <Badge color="amber">오배치 의심</Badge>}
                </div>
                {testResult.resolution_draft ? (
                  <p className="text-slate-300">해결 방안: {testResult.resolution_draft}</p>
                ) : (
                  <p className="text-slate-500 text-xs italic">
                    해결 방안 없음 — {testResult.category === 'system_error'
                      ? '분석이 불완전했을 수 있습니다'
                      : `"${CATEGORY_LABEL[testResult.category] ?? testResult.category}"(으)로 판단되어 생성하지 않음`}
                  </p>
                )}
                {testResult.reasoning && <p className="text-slate-500 text-xs">판단 근거: {testResult.reasoning}</p>}
                <p className="text-slate-500 text-xs">참조 지식 ID: {testResult.knowledge_ref_ids.join(', ') || '없음'}</p>
                <p className="text-xs text-indigo-400 pt-1">
                  → 아래 "Teams 알림 발송 테스트"에서 "마지막 분석 결과 불러오기"로 이어서 발송까지 테스트할 수 있습니다.
                </p>
              </div>
            )}
          </div>
        </section>
      )}

      {/* ── 번외: Teams 알림 발송 테스트 (분석 테스트와 같은 탭) ── */}
      {subTab === 'analyze' && (
        <section className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-slate-300 flex items-center gap-2">
              <Send className="w-4 h-4" /> Teams 알림 발송 테스트
            </h3>
            {testResult && (
              <button onClick={loadFromLastAnalysis} className="text-xs text-indigo-400 hover:text-indigo-300">
                마지막 분석 결과 불러오기
              </button>
            )}
          </div>
          <p className="text-xs text-slate-500" title="채널 소유자면 누구나 발급 가능, IT 승인 불필요">
            Teams "Workflows" 웹훅 URL로 실제 카드 미리보기
          </p>
          <div className="bg-slate-800 border border-slate-700 rounded-lg p-4 space-y-3">
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">Teams 웹훅 URL (필수)</label>
              <input
                value={notifyWebhookUrl} onChange={(e) => setNotifyWebhookUrl(e.target.value)}
                placeholder="https://..."
                className={clsx('w-full', inputClass)}
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">제목</label>
                <input value={notifySubject} onChange={(e) => setNotifySubject(e.target.value)} className={clsx('w-full', inputClass)} />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">발신자</label>
                <input value={notifySender} onChange={(e) => setNotifySender(e.target.value)} className={clsx('w-full', inputClass)} />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">담당 파트</label>
                <input value={notifyPart} onChange={(e) => setNotifyPart(e.target.value)} className={clsx('w-full', inputClass)} />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1" title="심각도 높음/긴급일 때만 표시 — 자동발신 아님">온콜 담당자</label>
                <input value={notifyOncallName} onChange={(e) => setNotifyOncallName(e.target.value)} className={clsx('w-full', inputClass)} />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">분류</label>
                <select value={notifyCategory} onChange={(e) => setNotifyCategory(e.target.value as typeof notifyCategory)} className={clsx('w-full', inputClass)}>
                  {Object.entries(CATEGORY_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1" title="높음/긴급이면 카드가 빨간색 강조로 바뀜">심각도</label>
                <select value={notifySeverity} onChange={(e) => setNotifySeverity(e.target.value as typeof notifySeverity)} className={clsx('w-full', inputClass)}>
                  {Object.entries(SEVERITY_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>
              </div>
            </div>
            <label className="flex items-center gap-2 text-sm text-slate-300">
              <input type="checkbox" checked={notifyMismatch} onChange={(e) => setNotifyMismatch(e.target.checked)} />
              오배치 의심으로 표시
            </label>
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">해결 방안 초안 (선택)</label>
              <textarea
                value={notifyResolutionDraft} onChange={(e) => setNotifyResolutionDraft(e.target.value)}
                rows={2}
                className={clsx('w-full', inputClass)}
              />
            </div>
            <Button size="sm" onClick={() => notifyMutation.mutate()} loading={notifyMutation.isPending} disabled={!notifyWebhookUrl.trim()}>
              <Send className="w-3.5 h-3.5" /> Teams로 발송
            </Button>
            {notifySent && <p className="text-emerald-400 text-xs">{notifySent}</p>}
          </div>
        </section>
      )}

      {/* ── 2단계: 라우팅 매핑 ── */}
      {subTab === 'routing' && (
        <section className="space-y-3">
          {routingError && <QueryErrorNotice error={routingErrorObj} onRetry={() => refetchRouting()} />}
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-slate-300">담당자 라우팅 매핑</h3>
            <div className="flex items-center gap-2">
              <select
                value={selectedNs}
                onChange={(e) => setNamespace(e.target.value)}
                className="bg-slate-700 border border-slate-600 rounded-lg px-3 py-1.5 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
              >
                {namespaces.map((ns) => <option key={ns} value={ns}>{ns}</option>)}
              </select>
              <Button size="sm" onClick={startCreate}>
                <Plus className="w-3.5 h-3.5" /> 라우팅 추가
              </Button>
            </div>
          </div>
          <p className="text-xs text-slate-500">메일함 ↔ 담당 파트 ↔ Teams 웹훅 매핑</p>

          {showForm && (
            <div className="bg-slate-800 border border-indigo-500/30 rounded-lg p-4 space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <label className="block text-xs text-slate-400">
                  담당 파트
                  <select
                    value={form.part}
                    onChange={(e) => setForm({ ...form, part: e.target.value })}
                    className={clsx('w-full mt-1', inputClass)}
                  >
                    <option value="">선택하세요</option>
                    {parts.map((p) => <option key={p.id} value={p.name}>{p.name}</option>)}
                  </select>
                </label>
                <label className="block text-xs text-slate-400">
                  메일함 UPN
                  <input
                    type="email"
                    placeholder="예: voc-billing@example.com" value={form.mailbox_upn}
                    onChange={(e) => setForm({ ...form, mailbox_upn: e.target.value })}
                    className={clsx('w-full mt-1', inputClass)}
                  />
                </label>
                <label className="block text-xs text-slate-400 col-span-2" title="없으면 분석·이력 저장까지만 진행">
                  Teams 웹훅 URL (선택)
                  <input
                    type="url"
                    placeholder="https://..." value={form.teams_webhook_url ?? ''}
                    onChange={(e) => setForm({ ...form, teams_webhook_url: e.target.value })}
                    className={clsx('w-full mt-1', inputClass)}
                  />
                </label>
                <div className="col-span-2">
                  <label className="block text-xs text-slate-400 mb-1" title="비우면 메일함 전체 조회 — 특정 폴더로 좁히면 무관한 메일 유입 차단">
                    메일 폴더 (선택)
                  </label>
                  <div className="flex items-center gap-2">
                    <select
                      value={form.mail_folder_id ?? ''}
                      onChange={(e) => {
                        const picked = folderOptions?.find((f) => f.id === e.target.value);
                        setForm({
                          ...form,
                          mail_folder_id: e.target.value,
                          mail_folder_name: picked?.display_name ?? '',
                        });
                      }}
                      className={clsx('flex-1', inputClass)}
                    >
                      <option value="">전체 메일함{form.mail_folder_name ? ` (현재: ${form.mail_folder_name})` : ''}</option>
                      {folderOptions?.map((f) => (
                        <option key={f.id} value={f.id}>
                          {f.display_name} (안읽음 {f.unread_count} / 전체 {f.total_count})
                        </option>
                      ))}
                    </select>
                    <Button
                      size="sm" variant="secondary" type="button"
                      onClick={() => loadFoldersMutation.mutate()}
                      loading={loadFoldersMutation.isPending}
                      disabled={!form.mailbox_upn.trim()}
                    >
                      폴더 불러오기
                    </Button>
                  </div>
                </div>
                <label className="block text-xs text-slate-400" title="표시 전용 — 자동발신 아님">
                  온콜 담당자명 (선택)
                  <input
                    placeholder="예: 홍길동" value={form.oncall_contact_name ?? ''}
                    onChange={(e) => setForm({ ...form, oncall_contact_name: e.target.value })}
                    className={clsx('w-full mt-1', inputClass)}
                  />
                </label>
                <label className="block text-xs text-slate-400">
                  온콜 연락처 (선택)
                  <input
                    placeholder="예: 010-1234-5678" value={form.oncall_contact_phone ?? ''}
                    onChange={(e) => setForm({ ...form, oncall_contact_phone: e.target.value })}
                    className={clsx('w-full mt-1', inputClass)}
                  />
                </label>
              </div>
              <div className="flex items-center gap-2">
                <Button size="sm" onClick={handleSubmit} loading={createMutation.isPending || updateMutation.isPending}>
                  <Save className="w-3.5 h-3.5" /> 저장
                </Button>
                <Button size="sm" variant="ghost" onClick={() => { setShowForm(false); setEditingId(null); }}>
                  <X className="w-3.5 h-3.5" /> 취소
                </Button>
              </div>
            </div>
          )}

          {routingError ? null : routingLoading ? (
            <div className="text-sm text-slate-500 animate-pulse py-6 text-center">로딩 중...</div>
          ) : routing.length === 0 ? (
            <div className="text-sm text-slate-500 py-8 text-center border border-dashed border-slate-700 rounded-lg">
              등록된 라우팅 매핑이 없습니다. "라우팅 추가"로 메일함↔파트↔웹훅을 등록하세요.
            </div>
          ) : (
            <div className="border border-slate-700 rounded-lg overflow-hidden">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-700 bg-slate-800/50">
                    <th className="text-left px-4 py-2.5 text-xs font-medium text-slate-400">담당 파트</th>
                    <th className="text-left px-4 py-2.5 text-xs font-medium text-slate-400">메일함</th>
                    <th className="text-left px-4 py-2.5 text-xs font-medium text-slate-400">온콜 담당자</th>
                    <th className="w-20 px-4 py-2.5" />
                  </tr>
                </thead>
                <tbody>
                  {routing.map((r) => (
                    <tr key={r.id} className="border-b border-slate-700/50 last:border-0 hover:bg-slate-800/30">
                      <td className="px-4 py-3 text-slate-200">{r.part}</td>
                      <td className="px-4 py-3 text-slate-300 font-mono text-xs">{r.mailbox_upn}</td>
                      <td className="px-4 py-3 text-slate-400">{r.oncall_contact_name || '-'}</td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-1 justify-end">
                          <button onClick={() => startEdit(r)} className="p-1.5 rounded text-slate-500 hover:text-indigo-400 hover:bg-indigo-400/10">
                            <Pencil className="w-3.5 h-3.5" />
                          </button>
                          <button onClick={() => deleteMutation.mutate(r.id)} className="p-1.5 rounded text-slate-500 hover:text-rose-400 hover:bg-rose-400/10">
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {/* ── 이력 ── */}
      {subTab === 'history' && (
        <section className="space-y-3">
          {historyError && <QueryErrorNotice error={historyErrorObj} onRetry={() => refetchHistory()} />}
          <div className="flex items-center justify-between">
            <p className="text-xs text-slate-500">원본 메일 · AI 판단 · 발송 결과</p>
            <select
              value={selectedNs}
              onChange={(e) => setNamespace(e.target.value)}
              className="bg-slate-700 border border-slate-600 rounded-lg px-3 py-1.5 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
            >
              {namespaces.map((ns) => <option key={ns} value={ns}>{ns}</option>)}
            </select>
          </div>

          <div className="bg-slate-800 border border-slate-700 rounded-lg p-3 flex flex-wrap items-end gap-3">
            <label className="text-xs text-slate-400">
              심각도
              <select
                value={historySeverity} onChange={(e) => setHistorySeverity(e.target.value)}
                className={clsx('block mt-1', inputClass)}
              >
                <option value="">전체</option>
                {Object.entries(SEVERITY_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </label>
            <label className="text-xs text-slate-400">
              상태
              <select
                value={historyStatus} onChange={(e) => setHistoryStatus(e.target.value)}
                className={clsx('block mt-1', inputClass)}
              >
                <option value="">전체</option>
                {Object.entries(STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </label>
            <label className="flex items-center gap-1.5 text-xs text-slate-300 pb-2">
              <input
                type="checkbox" checked={historyMismatchOnly}
                onChange={(e) => setHistoryMismatchOnly(e.target.checked)}
              />
              오배치 의심만
            </label>
            <label className="text-xs text-slate-400 flex-1 min-w-[200px]">
              키워드 검색 (제목·발신자·본문)
              <input
                value={historyKeywordDraft}
                onChange={(e) => setHistoryKeywordDraft(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') setHistoryKeyword(historyKeywordDraft); }}
                placeholder="예: 결제 오류"
                className={clsx('w-full mt-1', inputClass)}
              />
            </label>
            <Button size="sm" variant="secondary" onClick={() => setHistoryKeyword(historyKeywordDraft)}>
              검색
            </Button>
            {historyFiltersActive && (
              <Button size="sm" variant="ghost" onClick={clearHistoryFilters}>
                <X className="w-3.5 h-3.5" /> 필터 초기화
              </Button>
            )}
          </div>

          {historyError ? null : historyLoading ? (
            <div className="text-sm text-slate-500 animate-pulse py-6 text-center">로딩 중...</div>
          ) : history.length === 0 ? (
            <div className="text-sm text-slate-500 py-8 text-center border border-dashed border-slate-700 rounded-lg">
              {historyFiltersActive
                ? '조건에 맞는 이력이 없습니다. 필터를 조정해보세요.'
                : '이력이 없습니다. "3단계 · 폴링"에서 수동 실행을 해보거나, 폴링 자동화가 켜지면 여기 쌓입니다.'}
            </div>
          ) : (
            <div className="space-y-2">
              {history.map((h) => (
                <div
                  key={h.id}
                  onClick={() => { setSelectedHistoryItem(h); setKnowledgeRefsOpen(false); }}
                  className="bg-slate-800 border border-slate-700 rounded-lg p-4 space-y-2 text-sm cursor-pointer hover:border-indigo-500/50 hover:bg-slate-800/70 transition-colors"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <span className="text-slate-200 font-medium">{h.subject || '(제목 없음)'}</span>
                      <span className="text-slate-500 ml-2 text-xs">{h.sender} → {h.mailbox_upn} ({h.part ?? '파트 미상'})</span>
                    </div>
                    <span className="text-xs text-slate-500">{h.received_at ?? h.created_at}</span>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Badge color="slate">{CATEGORY_LABEL[h.category ?? ''] ?? h.category ?? '-'}</Badge>
                    <Badge color={SEVERITY_COLOR[h.severity ?? ''] ?? 'slate'}>
                      심각도: {SEVERITY_LABEL[h.severity ?? ''] ?? h.severity ?? '-'}
                    </Badge>
                    {h.mismatch_flagged && <Badge color="amber">오배치 의심</Badge>}
                    <Badge color={
                      h.status === 'notified' ? 'emerald'
                        : h.status === 'notify_failed' ? 'rose'
                        : h.status === 'skipped_relevance' ? 'amber'
                        : 'slate'
                    }>
                      {STATUS_LABEL[h.status] ?? h.status}
                    </Badge>
                  </div>
                  {h.resolution_draft ? (
                    <p className="text-slate-300 text-xs">해결 방안: {h.resolution_draft}</p>
                  ) : h.status !== 'skipped_relevance' && (
                    <p className="text-slate-500 text-xs italic">
                      해결 방안 없음 — {h.category === 'system_error'
                        ? '분석이 불완전했을 수 있습니다'
                        : `"${CATEGORY_LABEL[h.category ?? ''] ?? h.category}"(으)로 판단되어 생성하지 않음`}
                    </p>
                  )}
                  {h.reasoning && <p className="text-slate-500 text-xs">판단 근거: {h.reasoning}</p>}
                  {h.notify_error && <p className="text-rose-400 text-xs">발송 실패 사유: {h.notify_error}</p>}
                </div>
              ))}
            </div>
          )}

          <div className="flex items-center justify-center gap-3 pt-2">
            <Button size="sm" variant="ghost" disabled={historyOffset === 0} onClick={() => setHistoryOffset(Math.max(0, historyOffset - HISTORY_PAGE_SIZE))}>
              이전
            </Button>
            {history.length > 0 && (
              <span className="text-xs text-slate-500">
                {historyOffset + 1}–{historyOffset + history.length}건
              </span>
            )}
            <Button size="sm" variant="ghost" disabled={history.length < HISTORY_PAGE_SIZE} onClick={() => setHistoryOffset(historyOffset + HISTORY_PAGE_SIZE)}>
              다음
            </Button>
          </div>

          <Modal isOpen={!!selectedHistoryItem} onClose={() => setSelectedHistoryItem(null)} title="분석 상세" maxWidth="max-w-2xl">
            {selectedHistoryItem && (
              <div className="space-y-4 text-sm">
                <div>
                  <p className="text-slate-100 font-medium">{selectedHistoryItem.subject || '(제목 없음)'}</p>
                  <p className="text-slate-500 text-xs mt-1">
                    {selectedHistoryItem.sender} → {selectedHistoryItem.mailbox_upn} ({selectedHistoryItem.part ?? '파트 미상'})
                  </p>
                </div>

                <div className="flex flex-wrap gap-2">
                  <Badge color="slate">{CATEGORY_LABEL[selectedHistoryItem.category ?? ''] ?? selectedHistoryItem.category ?? '-'}</Badge>
                  <Badge color={SEVERITY_COLOR[selectedHistoryItem.severity ?? ''] ?? 'slate'}>
                    심각도: {SEVERITY_LABEL[selectedHistoryItem.severity ?? ''] ?? selectedHistoryItem.severity ?? '-'}
                  </Badge>
                  {selectedHistoryItem.mismatch_flagged && <Badge color="amber">오배치 의심</Badge>}
                  <Badge color={
                    selectedHistoryItem.status === 'notified' ? 'emerald'
                      : selectedHistoryItem.status === 'notify_failed' ? 'rose'
                      : selectedHistoryItem.status === 'skipped_relevance' ? 'amber'
                      : 'slate'
                  }>
                    {STATUS_LABEL[selectedHistoryItem.status] ?? selectedHistoryItem.status}
                  </Badge>
                </div>

                <div>
                  <p className="text-xs font-medium text-slate-400 mb-1">해결 방안</p>
                  {selectedHistoryItem.resolution_draft ? (
                    <p className="text-slate-300 bg-slate-900/50 border border-slate-700 rounded-lg p-3">{selectedHistoryItem.resolution_draft}</p>
                  ) : (
                    <p className="text-slate-500 italic">
                      해당 없음 — {selectedHistoryItem.category === 'system_error'
                        ? '분석이 불완전했을 수 있습니다'
                        : `"${CATEGORY_LABEL[selectedHistoryItem.category ?? ''] ?? selectedHistoryItem.category}"(으)로 판단되어 생성하지 않음`}
                    </p>
                  )}
                </div>

                {selectedHistoryItem.reasoning && (
                  <div>
                    <p className="text-xs font-medium text-slate-400 mb-1">판단 근거</p>
                    <p className="text-slate-300 bg-slate-900/50 border border-slate-700 rounded-lg p-3">{selectedHistoryItem.reasoning}</p>
                  </div>
                )}

                {selectedHistoryItem.notify_error && (
                  <div>
                    <p className="text-xs font-medium text-slate-400 mb-1">발송 실패 사유</p>
                    <p className="text-rose-400 bg-rose-500/10 border border-rose-500/30 rounded-lg p-3">{selectedHistoryItem.notify_error}</p>
                  </div>
                )}

                {selectedHistoryItem.knowledge_ref_ids.length > 0 && (
                  <div>
                    <button
                      onClick={() => setKnowledgeRefsOpen((v) => !v)}
                      className="flex items-center gap-1 text-xs font-medium text-slate-400 hover:text-slate-200 mb-1"
                    >
                      {knowledgeRefsOpen ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
                      참조 지식 ({selectedHistoryItem.knowledge_ref_ids.length}건)
                    </button>
                    {knowledgeRefsOpen && (
                      knowledgeRefsLoading ? (
                        <p className="text-xs text-slate-500 animate-pulse">불러오는 중...</p>
                      ) : selectedKnowledgeRefs.length === 0 ? (
                        <p className="text-xs text-slate-500 italic">
                          내용을 찾을 수 없습니다 — 이후 삭제됐거나 수정됐을 수 있습니다 (ID: {selectedHistoryItem.knowledge_ref_ids.join(', ')})
                        </p>
                      ) : (
                        <div className="space-y-2">
                          {selectedKnowledgeRefs.map((k) => (
                            <div key={k.id} className="bg-slate-900/50 border border-slate-700 rounded-lg p-3">
                              <div className="flex items-center gap-2 mb-1 text-[11px] text-slate-500">
                                <span className="font-mono">#{k.id}</span>
                                {k.category && <Badge color="slate">{k.category}</Badge>}
                                {k.container_name && <span>{k.container_name}</span>}
                              </div>
                              <p className="text-slate-300 text-xs whitespace-pre-wrap">{k.content}</p>
                            </div>
                          ))}
                        </div>
                      )
                    )}
                  </div>
                )}

                <div className="grid grid-cols-2 gap-3 text-xs text-slate-500 pt-2 border-t border-slate-700">
                  <div>수신 시각: {selectedHistoryItem.received_at ?? '-'}</div>
                  <div>분석 시각: {selectedHistoryItem.created_at}</div>
                  <div>Teams 발송 시각: {selectedHistoryItem.teams_sent_at ?? '-'}</div>
                  <div>레코드 ID: {selectedHistoryItem.id}</div>
                </div>
              </div>
            )}
          </Modal>

          <Modal
            isOpen={!!selectedFailedCycle}
            onClose={() => setSelectedFailedCycle(null)}
            title="폴링 실패 사유"
            maxWidth="max-w-lg"
          >
            {selectedFailedCycle && (
              <div className="space-y-3">
                <p className="text-xs text-slate-500">
                  {formatRelative(selectedFailedCycle.started_at)} 사이클 — 메일함 {selectedFailedCycle.mailboxes_failed}개 실패
                </p>
                {selectedFailedCycle.error_summary ? (
                  <div className="space-y-2">
                    {selectedFailedCycle.error_summary.split('\n').map((line, i) => (
                      <p key={i} className="text-rose-400 text-xs bg-rose-500/10 border border-rose-500/30 rounded-lg p-3 whitespace-pre-wrap">
                        {line}
                      </p>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-slate-500">기록된 실패 사유가 없습니다.</p>
                )}
              </div>
            )}
          </Modal>
        </section>
      )}

      {subTab === 'stats' && <VocStatsPanel namespace={selectedNs} />}
    </div>
  );
}
