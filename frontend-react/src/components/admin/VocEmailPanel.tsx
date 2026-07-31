import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Mail, Plus, Trash2, Pencil, X, Save, Send, FlaskConical, AlertCircle, Inbox, Route, PlayCircle, KeyRound, History as HistoryIcon } from 'lucide-react';
import { clsx } from 'clsx';
import {
  getEmailCollectionSettings, updateEmailCollectionSettings,
  listVocRouting, createVocRouting, updateVocRouting, deleteVocRouting,
  testAnalyzeEmail, testNotifyTeams,
  getGraphCredentials, updateGraphCredentials,
  runManualCollection, getEmailHistory,
  getSchedulerStatus, getPollCycles,
  type VocRouting, type VocRoutingPayload, type EmailAnalysisResult,
  type ManualCollectionResult,
} from '../../api/emailVoc';
import { getNamespaces } from '../../api/namespaces';
import { Button } from '../ui/Button';
import { Badge } from '../ui/Badge';
import { ApiError } from '../../api/client';

const EMPTY_ROUTING_FORM: VocRoutingPayload = {
  part: '', mailbox_upn: '', teams_webhook_url: '', oncall_contact_name: '', oncall_contact_phone: '',
};

const SEVERITY_LABEL: Record<string, string> = { low: '낮음', medium: '보통', high: '높음', urgent: '긴급' };
const CATEGORY_LABEL: Record<string, string> = { system_error: '시스템 오류', user_mistake: '사용자 실수', uncertain: '판단 보류' };

type VocSubTab = 'collect' | 'analyze' | 'routing' | 'history';

const SUB_TABS: { id: VocSubTab; label: string; icon: React.ReactNode }[] = [
  { id: 'collect', label: '1단계 · 메일 수집', icon: <Inbox className="w-4 h-4" /> },
  { id: 'analyze', label: '2단계 · 분석 테스트', icon: <FlaskConical className="w-4 h-4" /> },
  { id: 'routing', label: '3단계 · 라우팅 · 알림 테스트', icon: <Route className="w-4 h-4" /> },
  { id: 'history', label: '이력', icon: <HistoryIcon className="w-4 h-4" /> },
];

const inputClass = 'bg-slate-700 border border-slate-600 rounded px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500';

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
};

function formatRelative(iso: string | null): string {
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
  const { data: settings } = useQuery({
    queryKey: ['email-voc-settings'],
    queryFn: getEmailCollectionSettings,
  });

  const settingsMutation = useMutation({
    mutationFn: updateEmailCollectionSettings,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['email-voc-settings'] }),
    onError: (e) => setError(e instanceof ApiError ? e.message : String(e)),
  });

  // ── 실시간 상태 + 폴링 이력 ─────────────────────────────────────────────
  // 10초마다 다시 조회 — "지금 잘 돌고 있는지"를 화면 새로고침 없이 확인할 수 있게.
  // "1단계" 탭에서만 화면에 보이므로, 다른 탭을 보는 동안은 폴링을 멈춰 불필요한
  // API 호출을 없앤다(다른 탭에서도 계속 10초마다 쏘고 있었던 걸 발견해 수정).
  const { data: schedulerStatus } = useQuery({
    queryKey: ['email-voc-scheduler-status'],
    queryFn: getSchedulerStatus,
    refetchInterval: 10_000,
    enabled: subTab === 'collect',
  });
  const { data: pollCycles = [] } = useQuery({
    queryKey: ['email-voc-poll-cycles'],
    queryFn: () => getPollCycles(10),
    refetchInterval: 10_000,
    enabled: subTab === 'collect',
  });

  // ── §10 라우팅 매핑 ───────────────────────────────────────────────────────
  const { data: routing = [], isLoading: routingLoading } = useQuery({
    queryKey: ['voc-routing', selectedNs],
    queryFn: () => listVocRouting(selectedNs),
    enabled: !!selectedNs,
  });

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
    onError: (e) => setError(e instanceof ApiError ? e.message : String(e)),
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
    onError: (e) => setError(e instanceof ApiError ? e.message : String(e)),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteVocRouting(id, selectedNs),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['voc-routing', selectedNs] }),
  });

  const startEdit = (r: VocRouting) => {
    setEditingId(r.id);
    setForm({
      part: r.part, mailbox_upn: r.mailbox_upn,
      teams_webhook_url: r.teams_webhook_url ?? '',
      oncall_contact_name: r.oncall_contact_name ?? '',
      oncall_contact_phone: r.oncall_contact_phone ?? '',
    });
    setShowForm(true);
  };

  const startCreate = () => {
    setEditingId(null);
    setForm(EMPTY_ROUTING_FORM);
    setShowForm(true);
  };

  const handleSubmit = () => {
    if (!form.part.trim() || !form.mailbox_upn.trim()) {
      setError('담당 파트와 메일함 주소는 필수입니다.');
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
    onError: (e) => setError(e instanceof ApiError ? e.message : String(e)),
  });

  // ── 1단계: 수동 1회성 실행 (§9, §5 Phase 1) ─────────────────────────────
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
    onError: (e) => setError(e instanceof ApiError ? e.message : String(e)),
  });

  // ── 이력 ──────────────────────────────────────────────────────────────
  const [historyOffset, setHistoryOffset] = useState(0);
  const HISTORY_PAGE_SIZE = 30;
  // namespace는 "3단계" 탭 선택기와 공유되므로, 거기서 바뀌어도 이력 페이지는
  // 항상 1페이지로 리셋되어야 한다 — 이전 namespace의 offset이 남아있으면
  // 다른 namespace를 엉뚱한 offset으로 조회해 빈 페이지가 나오는 버그가 생긴다.
  useEffect(() => {
    setHistoryOffset(0);
  }, [selectedNs]);
  const { data: history = [], isLoading: historyLoading } = useQuery({
    queryKey: ['email-history', selectedNs, historyOffset],
    queryFn: () => getEmailHistory(selectedNs, HISTORY_PAGE_SIZE, historyOffset),
    enabled: !!selectedNs && subTab === 'history',
  });

  // ── 2단계: 분석 테스트 (§11 Track A #2) ─────────────────────────────────
  const [testSubject, setTestSubject] = useState('결제 오류 문의');
  const [testBody, setTestBody] = useState('고객이 결제 시도 시 500 에러가 계속 발생한다고 합니다.');
  const [testPart, setTestPart] = useState('');
  const [testResult, setTestResult] = useState<EmailAnalysisResult | null>(null);

  const analyzeMutation = useMutation({
    mutationFn: () => testAnalyzeEmail(selectedNs, testSubject, testBody, testPart || undefined),
    onSuccess: (result) => { setTestResult(result); setError(''); },
    onError: (e) => setError(e instanceof ApiError ? e.message : String(e)),
  });

  // ── 3단계: Teams 알림 테스트 (§11 Track A #3) — 분석 없이도 독립적으로 사용 가능 ──
  const [notifyWebhookUrl, setNotifyWebhookUrl] = useState('');
  const [notifySubject, setNotifySubject] = useState('(테스트) VOC 알림');
  const [notifySender, setNotifySender] = useState('user@example.com');
  const [notifyPart, setNotifyPart] = useState('');
  const [notifyCategory, setNotifyCategory] = useState<'system_error' | 'user_mistake' | 'uncertain'>('system_error');
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
    onError: (e) => { setNotifySent(null); setError(e instanceof ApiError ? e.message : String(e)); },
  });

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-slate-100 flex items-center gap-2">
          <Mail className="w-5 h-5 text-indigo-400" />
          VOC 이메일 분석 채널
        </h2>
        <p className="text-sm text-slate-400 mt-0.5">
          §9(수집 정책) · §10(담당자 라우팅) — Track A 구현. 실제 Graph API 연동은 Track B(조직 승인) 대기 중.
        </p>
      </div>

      {error && (
        <div className="flex items-center gap-3 px-4 py-3 bg-rose-500/10 border border-rose-500/30 rounded-lg">
          <AlertCircle className="w-4 h-4 text-rose-400 flex-shrink-0" />
          <p className="text-sm text-rose-300">{error}</p>
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

      {/* ── 1단계: 메일 수집 정책 ── */}
      {subTab === 'collect' && (
        <section className="space-y-3">
          {/* 실시간 상태 — 10초마다 자동 새로고침 */}
          <div className={clsx(
            'border rounded-lg p-4',
            schedulerStatus?.enabled
              ? (schedulerStatus?.is_running_now ? 'bg-indigo-500/10 border-indigo-500/30' : 'bg-emerald-500/5 border-emerald-500/20')
              : 'bg-slate-800 border-slate-700',
          )}>
            <div className="flex items-center gap-2 mb-2">
              <span className={clsx(
                'w-2 h-2 rounded-full',
                !schedulerStatus?.enabled ? 'bg-slate-500'
                  : schedulerStatus?.is_running_now ? 'bg-indigo-400 animate-pulse' : 'bg-emerald-400',
              )} />
              <span className="text-sm font-medium text-slate-200">
                {!schedulerStatus?.enabled ? '폴링 꺼짐'
                  : schedulerStatus?.is_running_now ? '지금 수집 중...'
                  : '대기 중 — 정상 동작'}
              </span>
              <span className="text-xs text-slate-500 ml-auto">10초마다 자동 갱신</span>
            </div>
            {schedulerStatus?.enabled && (
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs text-slate-400">
                <div>
                  <div className="text-slate-500">마지막 실행</div>
                  <div className="text-slate-200">{formatRelative(schedulerStatus.last_cycle?.started_at ?? null)}</div>
                </div>
                <div>
                  <div className="text-slate-500">다음 예상 실행</div>
                  <div className="text-slate-200">{formatRelative(schedulerStatus.next_estimated_at)}</div>
                </div>
                <div>
                  <div className="text-slate-500">마지막 결과</div>
                  <div className={schedulerStatus.last_cycle && schedulerStatus.last_cycle.mailboxes_failed > 0 ? 'text-rose-400' : 'text-emerald-400'}>
                    {schedulerStatus.last_cycle
                      ? `성공 ${schedulerStatus.last_cycle.mailboxes_ok} / 실패 ${schedulerStatus.last_cycle.mailboxes_failed}`
                      : '아직 실행 이력 없음'}
                  </div>
                </div>
                <div>
                  <div className="text-slate-500">분석/발송</div>
                  <div className="text-slate-200">
                    {schedulerStatus.last_cycle ? `${schedulerStatus.last_cycle.total_analyzed}건 / ${schedulerStatus.last_cycle.total_notified}건` : '-'}
                  </div>
                </div>
              </div>
            )}
            {schedulerStatus?.last_cycle?.error_summary && (
              <p className="text-xs text-amber-400 mt-2">⚠ {schedulerStatus.last_cycle.error_summary}</p>
            )}
          </div>

          <div className="bg-slate-800 border border-slate-700 rounded-lg p-4 flex flex-wrap items-center gap-6">
            <button
              onClick={() => settingsMutation.mutate({ email_collection_enabled: !(settings?.email_collection_enabled ?? false) })}
              disabled={settingsMutation.isPending}
              className={clsx(
                'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors',
                settings?.email_collection_enabled
                  ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400 hover:bg-emerald-500/20'
                  : 'bg-slate-700 border-slate-600 text-slate-400 hover:bg-slate-600',
              )}
            >
              <span className={clsx('w-2 h-2 rounded-full', settings?.email_collection_enabled ? 'bg-emerald-400' : 'bg-slate-500')} />
              폴링 자동화 {settings?.email_collection_enabled ? 'ON' : 'OFF'}
            </button>

            <label className="flex items-center gap-2 text-sm text-slate-300">
              폴링 주기(분, 최소 1)
              <input
                type="number" min={1}
                defaultValue={settings?.email_polling_interval_minutes ?? 5}
                key={`interval-${settings?.email_polling_interval_minutes}`}
                onBlur={(e) => {
                  const v = Number(e.target.value);
                  if (v >= 1) settingsMutation.mutate({ email_polling_interval_minutes: v });
                }}
                className="w-20 bg-slate-700 border border-slate-600 rounded px-2 py-1 text-slate-200 focus:outline-none focus:border-indigo-500"
              />
            </label>

            <label className="flex items-center gap-2 text-sm text-slate-300">
              재조회 기간(일)
              <input
                type="number" min={1}
                defaultValue={settings?.email_lookback_days ?? 7}
                key={`lookback-${settings?.email_lookback_days}`}
                onBlur={(e) => {
                  const v = Number(e.target.value);
                  if (v >= 1) settingsMutation.mutate({ email_lookback_days: v });
                }}
                className="w-20 bg-slate-700 border border-slate-600 rounded px-2 py-1 text-slate-200 focus:outline-none focus:border-indigo-500"
              />
            </label>
          </div>
          <p className="text-xs text-slate-500">
            실제 메일함 연결(Graph API)은 Track B(대상 메일함 확정·M365 여부·IT 승인) 대기 중이라, 이 설정은 저장은 되지만 아직 실제 폴링은 동작하지 않습니다.
          </p>

          {/* Graph API 자격증명 */}
          <div className="pt-2">
            <h3 className="text-sm font-semibold text-slate-300 flex items-center gap-2 mb-2">
              <KeyRound className="w-4 h-4" /> Graph API 자격증명
              <Badge color={graphCreds?.configured ? 'emerald' : 'slate'}>
                {graphCreds?.configured ? '설정됨' : '미설정'}
              </Badge>
            </h3>
            <p className="text-xs text-slate-500 mb-2">
              §7 Q10 IT 승인 후 발급받는 값입니다. 승인 전이라면 비워두면 됩니다 — 수동 실행 시 메일함별로 "자격증명 미설정" 에러만 표시됩니다.
            </p>
            <div className="bg-slate-800 border border-slate-700 rounded-lg p-4 space-y-3">
              {graphCreds?.configured && (
                <p className="text-xs text-slate-400">
                  현재: tenant_id=<span className="font-mono">{graphCreds.tenant_id}</span>, client_id=<span className="font-mono">{graphCreds.client_id}</span> (client_secret은 다시 표시되지 않습니다)
                </p>
              )}
              <div className="grid grid-cols-3 gap-3">
                <input placeholder="Tenant ID" value={credTenantId} onChange={(e) => setCredTenantId(e.target.value)} className={inputClass} />
                <input placeholder="Client ID" value={credClientId} onChange={(e) => setCredClientId(e.target.value)} className={inputClass} />
                <input placeholder="Client Secret" type="password" value={credClientSecret} onChange={(e) => setCredClientSecret(e.target.value)} className={inputClass} />
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

          {/* 수동 1회성 실행 */}
          <div className="pt-2">
            <h3 className="text-sm font-semibold text-slate-300 flex items-center gap-2 mb-2">
              <PlayCircle className="w-4 h-4" /> 수동 1회성 실행
            </h3>
            <p className="text-xs text-slate-500 mb-2">
              기간을 지정해 지금 바로 1회 수집+분석+발송을 실행합니다. 기본값은 오늘로부터 7일 전 ~ 오늘입니다.
            </p>
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
                    <p className="text-xs text-slate-500">등록된 라우팅 매핑이 없습니다 — "3단계" 탭에서 먼저 파트/메일함을 등록하세요.</p>
                  ) : (
                    <div className="border border-slate-700 rounded-lg overflow-hidden">
                      <table className="w-full text-xs">
                        <thead>
                          <tr className="border-b border-slate-700 bg-slate-800/50">
                            <th className="text-left px-3 py-2 text-slate-400">메일함</th>
                            <th className="text-left px-3 py-2 text-slate-400">결과</th>
                            <th className="text-left px-3 py-2 text-slate-400">수집/분석/중복/발송/발송실패</th>
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
                                {m.fetched} / {m.analyzed} / {m.skipped_duplicate} / {m.notified} / {m.notify_failed}
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

          {/* 최근 폴링 이력 — 사이클 단위 성공/실패 */}
          <div className="pt-2">
            <h3 className="text-sm font-semibold text-slate-300 mb-2">최근 폴링 이력</h3>
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
                      <th className="text-left px-3 py-2 text-slate-400">분석/발송</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pollCycles.map((c) => (
                      <tr key={c.id} className="border-b border-slate-700/50 last:border-0">
                        <td className="px-3 py-2 text-slate-300">{formatRelative(c.started_at)}</td>
                        <td className="px-3 py-2 text-slate-400">{c.namespaces_processed}</td>
                        <td className="px-3 py-2">
                          <Badge color={c.mailboxes_failed > 0 ? 'rose' : 'emerald'}>
                            성공 {c.mailboxes_ok} / 실패 {c.mailboxes_failed}
                          </Badge>
                        </td>
                        <td className="px-3 py-2 text-slate-400">{c.total_analyzed}건 / {c.total_notified}건</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </section>
      )}

      {/* ── 2단계: 분석 테스트 ── */}
      {subTab === 'analyze' && (
        <section className="space-y-3">
          <p className="text-xs text-slate-500">실제 메일 연동 없이, 텍스트를 직접 입력해 분류/심각도/오배치 판정 로직을 검증합니다.</p>
          <div className="bg-slate-800 border border-slate-700 rounded-lg p-4 space-y-3">
            <input
              placeholder="제목" value={testSubject} onChange={(e) => setTestSubject(e.target.value)}
              className={clsx('w-full', inputClass)}
            />
            <textarea
              placeholder="본문" value={testBody} onChange={(e) => setTestBody(e.target.value)}
              rows={3}
              className={clsx('w-full', inputClass)}
            />
            <input
              placeholder="수신 메일함 담당 파트 (선택 — 오배치 판정용)" value={testPart} onChange={(e) => setTestPart(e.target.value)}
              className={clsx('w-full', inputClass)}
            />
            <Button size="sm" onClick={() => analyzeMutation.mutate()} loading={analyzeMutation.isPending} disabled={!selectedNs || !testBody.trim()}>
              분석 실행
            </Button>

            {testResult && (
              <div className="mt-3 bg-slate-900/50 border border-slate-700 rounded-lg p-4 space-y-2 text-sm">
                <div className="flex flex-wrap gap-2">
                  <Badge color="slate">{CATEGORY_LABEL[testResult.category] ?? testResult.category}</Badge>
                  <Badge color={testResult.severity === 'urgent' || testResult.severity === 'high' ? 'rose' : 'slate'}>
                    심각도: {SEVERITY_LABEL[testResult.severity] ?? testResult.severity}
                  </Badge>
                  {testResult.mismatch_flagged && <Badge color="amber">오배치 의심</Badge>}
                </div>
                {testResult.resolution_draft && <p className="text-slate-300">해결 방안: {testResult.resolution_draft}</p>}
                {testResult.reasoning && <p className="text-slate-500 text-xs">판단 근거: {testResult.reasoning}</p>}
                <p className="text-slate-500 text-xs">참조 지식 ID: {testResult.knowledge_ref_ids.join(', ') || '없음'}</p>
                <p className="text-xs text-indigo-400 pt-1">
                  → "3단계 · 라우팅·알림 테스트" 탭에서 "마지막 분석 결과 불러오기"로 이어서 Teams 발송까지 테스트할 수 있습니다.
                </p>
              </div>
            )}
          </div>
        </section>
      )}

      {/* ── 3단계: 라우팅 매핑 + Teams 알림 테스트 ── */}
      {subTab === 'routing' && (
        <div className="space-y-8">
          <section className="space-y-3">
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
                  <Plus className="w-3.5 h-3.5" /> 파트 추가
                </Button>
              </div>
            </div>

            {showForm && (
              <div className="bg-slate-800 border border-indigo-500/30 rounded-lg p-4 space-y-3">
                <div className="grid grid-cols-2 gap-3">
                  <input
                    placeholder="담당 파트 (예: 결제팀)" value={form.part}
                    onChange={(e) => setForm({ ...form, part: e.target.value })}
                    className={inputClass}
                  />
                  <input
                    placeholder="메일함 UPN (예: voc-billing@example.com)" value={form.mailbox_upn}
                    onChange={(e) => setForm({ ...form, mailbox_upn: e.target.value })}
                    className={inputClass}
                  />
                  <input
                    placeholder="Teams Workflows 웹훅 URL" value={form.teams_webhook_url ?? ''}
                    onChange={(e) => setForm({ ...form, teams_webhook_url: e.target.value })}
                    className={clsx('col-span-2', inputClass)}
                  />
                  <input
                    placeholder="온콜 담당자명 (표시 전용, 자동발신 아님)" value={form.oncall_contact_name ?? ''}
                    onChange={(e) => setForm({ ...form, oncall_contact_name: e.target.value })}
                    className={inputClass}
                  />
                  <input
                    placeholder="온콜 연락처" value={form.oncall_contact_phone ?? ''}
                    onChange={(e) => setForm({ ...form, oncall_contact_phone: e.target.value })}
                    className={inputClass}
                  />
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

            {routingLoading ? (
              <div className="text-sm text-slate-500 animate-pulse py-6 text-center">로딩 중...</div>
            ) : routing.length === 0 ? (
              <div className="text-sm text-slate-500 py-8 text-center border border-dashed border-slate-700 rounded-lg">
                등록된 라우팅 매핑이 없습니다. "파트 추가"로 메일함↔파트↔웹훅을 등록하세요.
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

          {/* Teams 알림 테스트 — 분석 결과 없이도 독립적으로 사용 가능 */}
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
            <p className="text-xs text-slate-500">
              아무 Teams 채널에서 "Workflows" 앱으로 웹훅 URL을 발급받아(채널 소유자면 누구나 가능, IT 승인 불필요) 여기 입력하면 실제 카드가 어떻게 오는지 바로 확인할 수 있습니다.
            </p>
            <div className="bg-slate-800 border border-slate-700 rounded-lg p-4 space-y-3">
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">Teams Workflows 웹훅 URL (필수)</label>
                <input
                  value={notifyWebhookUrl} onChange={(e) => setNotifyWebhookUrl(e.target.value)}
                  placeholder="https://..."
                  className={clsx('w-full', inputClass)}
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-slate-400 mb-1">제목 — 카드 본문에 굵게 표시</label>
                  <input value={notifySubject} onChange={(e) => setNotifySubject(e.target.value)} className={clsx('w-full', inputClass)} />
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-400 mb-1">발신자 — "발신자" 항목에 표시</label>
                  <input value={notifySender} onChange={(e) => setNotifySender(e.target.value)} className={clsx('w-full', inputClass)} />
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-400 mb-1">담당 파트 — "담당 파트" 항목에 표시</label>
                  <input value={notifyPart} onChange={(e) => setNotifyPart(e.target.value)} className={clsx('w-full', inputClass)} />
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-400 mb-1">온콜 담당자 — 심각도 높음/긴급일 때만 표시(자동발신 아님)</label>
                  <input value={notifyOncallName} onChange={(e) => setNotifyOncallName(e.target.value)} className={clsx('w-full', inputClass)} />
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-400 mb-1">분류 — "분류" 항목에 표시</label>
                  <select value={notifyCategory} onChange={(e) => setNotifyCategory(e.target.value as typeof notifyCategory)} className={clsx('w-full', inputClass)}>
                    {Object.entries(CATEGORY_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-400 mb-1">심각도 — 높음/긴급이면 카드가 빨간색 강조로 바뀜</label>
                  <select value={notifySeverity} onChange={(e) => setNotifySeverity(e.target.value as typeof notifySeverity)} className={clsx('w-full', inputClass)}>
                    {Object.entries(SEVERITY_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </select>
                </div>
              </div>
              <label className="flex items-center gap-2 text-sm text-slate-300">
                <input type="checkbox" checked={notifyMismatch} onChange={(e) => setNotifyMismatch(e.target.checked)} />
                오배치 의심으로 표시 — 카드에 "오배치 의심" 경고 항목 추가
              </label>
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">해결 방안 초안 (선택) — 카드 하단에 표시</label>
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
        </div>
      )}

      {/* ── 이력 ── */}
      {subTab === 'history' && (
        <section className="space-y-3">
          <div className="flex items-center justify-between">
            <p className="text-xs text-slate-500">
              폴링/수동 실행으로 저장된 분석 결과 — 원본 메일, AI 판단, Teams 발송 성공/실패 여부를 확인합니다.
            </p>
            <select
              value={selectedNs}
              onChange={(e) => setNamespace(e.target.value)}
              className="bg-slate-700 border border-slate-600 rounded-lg px-3 py-1.5 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
            >
              {namespaces.map((ns) => <option key={ns} value={ns}>{ns}</option>)}
            </select>
          </div>

          {historyLoading ? (
            <div className="text-sm text-slate-500 animate-pulse py-6 text-center">로딩 중...</div>
          ) : history.length === 0 ? (
            <div className="text-sm text-slate-500 py-8 text-center border border-dashed border-slate-700 rounded-lg">
              이력이 없습니다. "1단계 · 메일 수집"에서 수동 실행을 해보거나, 폴링 자동화가 켜지면 여기 쌓입니다.
            </div>
          ) : (
            <div className="space-y-2">
              {history.map((h) => (
                <div key={h.id} className="bg-slate-800 border border-slate-700 rounded-lg p-4 space-y-2 text-sm">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <span className="text-slate-200 font-medium">{h.subject || '(제목 없음)'}</span>
                      <span className="text-slate-500 ml-2 text-xs">{h.sender} → {h.mailbox_upn} ({h.part ?? '파트 미상'})</span>
                    </div>
                    <span className="text-xs text-slate-500">{h.received_at ?? h.created_at}</span>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Badge color="slate">{CATEGORY_LABEL[h.category ?? ''] ?? h.category ?? '-'}</Badge>
                    <Badge color={h.severity === 'urgent' || h.severity === 'high' ? 'rose' : 'slate'}>
                      심각도: {SEVERITY_LABEL[h.severity ?? ''] ?? h.severity ?? '-'}
                    </Badge>
                    {h.mismatch_flagged && <Badge color="amber">오배치 의심</Badge>}
                    <Badge color={h.status === 'notified' ? 'emerald' : h.status === 'notify_failed' ? 'rose' : 'slate'}>
                      {STATUS_LABEL[h.status] ?? h.status}
                    </Badge>
                  </div>
                  {h.resolution_draft && <p className="text-slate-300 text-xs">해결 방안: {h.resolution_draft}</p>}
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
        </section>
      )}
    </div>
  );
}
