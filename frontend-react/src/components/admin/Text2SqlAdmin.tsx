import { useState, useEffect, useRef, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { clsx } from 'clsx';
import {
  Plus, Trash2, RefreshCw, Save, Eye, EyeOff, CheckCircle, XCircle,
  Search, X, Maximize2, Minimize2, Sparkles, Undo2, ZoomIn, ZoomOut,
  AlertTriangle, ArrowRight, Network,
} from 'lucide-react';
import { useAppStore } from '../../store/useAppStore';
import { getNamespaces } from '../../api/namespaces';
import {
  getTargetDb, upsertTargetDb, testTargetDb, listTargetSchemas, scanSchema,
  getFullSchema, updateSchemaTableDesc, toggleSchemaTable, updateSchemaColumnDesc, reindexSchema,
  saveSchemaPositions,
  listRelations, createRelation, deleteRelation, suggestRelationsAI,
  listSynonyms, createSynonym, deleteSynonym, reindexSynonyms, generateSynonymsAI,
  listSqlFewshots, createSqlFewshot, updateSqlFewshotStatus, deleteSqlFewshot, reindexFewshots, generateFewshotsAI,
  listPipelineStages, togglePipelineStage,
  listAuditLogs,
  listSqlCache, deleteSqlCacheEntry, clearSqlCache,
  type SchemaTableWithCols,
  type ScanReport,
} from '../../api/text2sql';
import { Button } from '../ui/Button';
import { Pagination, PaginationInfo, PaginationNav, useClientPaging } from '../ui/Pagination';
import { Modal } from '../ui/Modal';
import type {
  SqlTargetDb, SqlRelation, SqlSynonym, SqlFewshot, SqlPipelineStage, SqlAuditLog, SqlCacheEntry,
} from '../../types';

// ── Namespace selector ────────────────────────────────────────────────────────

function useNamespace() {
  const globalNs = useAppStore((s) => s.namespace);
  const [ns, setNs] = useState<string>(globalNs ?? '');
  useEffect(() => { if (globalNs) setNs(globalNs); }, [globalNs]);
  const { data: namespaces = [] } = useQuery({ queryKey: ['namespaces'], queryFn: getNamespaces });
  return { ns, setNs, namespaces };
}

function NsSelect({ ns, setNs, namespaces }: { ns: string; setNs: (v: string) => void; namespaces: string[] }) {
  return (
    <select value={ns} onChange={(e) => setNs(e.target.value)}
      className="bg-slate-800 border border-slate-600 rounded-lg px-3 py-1.5 text-sm text-slate-300 focus:outline-none focus:border-indigo-500">
      <option value="">네임스페이스 선택</option>
      {namespaces.map((n) => <option key={n} value={n}>{n}</option>)}
    </select>
  );
}

// ── TargetDbTab ───────────────────────────────────────────────────────────────

const _defaultForm: SqlTargetDb = { db_type: 'postgresql', host: '', port: 5432, db_name: '', username: '', password: '', schema_name: '', is_active: true };

function TargetDbTab() {
  const { ns, setNs, namespaces } = useNamespace();
  const qc = useQueryClient();
  const [showPwd, setShowPwd] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [scanReport, setScanReport] = useState<ScanReport | null>(null);
  const [scanning, setScanning] = useState(false);
  const [form, setForm] = useState<SqlTargetDb>(_defaultForm);
  const [schemaList, setSchemaList] = useState<string[]>([]);
  const [loadingSchemas, setLoadingSchemas] = useState(false);

  const { data: targetDbData } = useQuery({
    queryKey: ['sql_target_db', ns],
    queryFn: () => getTargetDb(ns),
    enabled: !!ns,
  });

  useEffect(() => {
    if (targetDbData) setForm({ ...targetDbData, password: '' });
  }, [targetDbData]);

  const saveMut = useMutation({
    mutationFn: () => upsertTargetDb(ns, form),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['sql_target_db', ns] }),
  });

  const handleTest = async () => {
    setTestResult(null);
    const r = await testTargetDb(ns, form).catch((e) => ({ ok: false, message: String(e) }));
    setTestResult(r);
  };

  const handleScan = async () => {
    setScanning(true);
    setScanReport(null);
    const r = await scanSchema(ns).catch((e: unknown) => { alert(String(e)); return null; });
    setScanning(false);
    if (r) {
      setScanReport(r);
      qc.invalidateQueries({ queryKey: ['sql_schema', ns] });
      qc.invalidateQueries({ queryKey: ['sql_synonyms', ns] });
      qc.invalidateQueries({ queryKey: ['sql_relations', ns] });
    }
  };

  const hasChanges = scanReport && (
    scanReport.tables_added > 0 || scanReport.tables_removed > 0 ||
    scanReport.columns_added > 0 || scanReport.columns_removed > 0 ||
    scanReport.columns_updated > 0
  );

  return (
    <div className="space-y-4 max-w-lg">
      <NsSelect ns={ns} setNs={setNs} namespaces={namespaces} />
      {ns && (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs text-slate-400">DB 종류</label>
              <select value={form.db_type} onChange={(e) => setForm({ ...form, db_type: e.target.value })}
                className="mt-1 w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-300">
                {['postgresql', 'mysql', 'oracle', 'sqlite'].map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs text-slate-400">포트</label>
              <input type="number" value={form.port} onChange={(e) => setForm({ ...form, port: Number(e.target.value) })}
                className="mt-1 w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-300" />
            </div>
          </div>
          {(['host', 'db_name', 'username'] as const).map((field) => (
            <div key={field}>
              <label className="text-xs text-slate-400">{field === 'db_name' ? (form.db_type === 'oracle' ? 'Service Name / SID' : 'DB 이름') : field === 'host' ? '호스트' : '사용자'}</label>
              <input value={form[field] as string} onChange={(e) => setForm({ ...form, [field]: e.target.value })}
                className="mt-1 w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-300" />
            </div>
          ))}
          <div>
            <label className="text-xs text-slate-400">
              스키마명 {form.db_type === 'oracle' ? '(Owner)' : form.db_type === 'postgresql' ? '(기본: public)' : '(선택)'}
            </label>
            <div className="flex gap-2 mt-1">
              {schemaList.length > 0 ? (
                <select value={form.schema_name ?? ''} onChange={(e) => setForm({ ...form, schema_name: e.target.value || null })}
                  className="flex-1 bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-300">
                  <option value="">{form.db_type === 'postgresql' ? 'public (기본)' : '선택하세요'}</option>
                  {schemaList.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              ) : (
                <input value={form.schema_name ?? ''} onChange={(e) => setForm({ ...form, schema_name: e.target.value || null })}
                  placeholder={form.db_type === 'postgresql' ? 'public' : form.db_type === 'oracle' ? 'OWNER명' : ''}
                  className="flex-1 bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-300 placeholder-slate-600" />
              )}
              <Button size="sm" variant="secondary" disabled={loadingSchemas}
                onClick={async () => {
                  setLoadingSchemas(true);
                  try {
                    const r = await listTargetSchemas(ns);
                    setSchemaList(r.schemas);
                  } catch (e) { alert(`스키마 조회 실패: ${e}`); }
                  setLoadingSchemas(false);
                }}>
                <RefreshCw className={clsx('w-3.5 h-3.5 mr-1', loadingSchemas && 'animate-spin')} />
                {loadingSchemas ? '조회 중' : '조회'}
              </Button>
            </div>
          </div>
          <div>
            <label className="text-xs text-slate-400">비밀번호 (변경 시만 입력)</label>
            <div className="relative mt-1">
              <input type={showPwd ? 'text' : 'password'} value={form.password ?? ''}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
                className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 pr-9 text-sm text-slate-300" />
              <button onClick={() => setShowPwd((v) => !v)} className="absolute right-2.5 top-2.5 text-slate-400">
                {showPwd ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
          </div>
          <div className="flex gap-2 flex-wrap">
            <Button size="sm" onClick={() => saveMut.mutate()} disabled={saveMut.isPending}>
              <Save className="w-3.5 h-3.5 mr-1" /> 저장
            </Button>
            <Button size="sm" variant="secondary" onClick={handleTest}>연결 테스트</Button>
            <Button size="sm" variant="secondary" onClick={handleScan} disabled={scanning}>
              <RefreshCw className={clsx('w-3.5 h-3.5 mr-1', scanning && 'animate-spin')} />
              {scanning ? '스캔 중...' : '스키마 스캔'}
            </Button>
          </div>
          {testResult && (
            <div className={clsx('flex items-center gap-2 text-sm px-3 py-2 rounded-lg', testResult.ok ? 'bg-emerald-900/30 text-emerald-400' : 'bg-rose-900/30 text-rose-400')}>
              {testResult.ok ? <CheckCircle className="w-4 h-4" /> : <XCircle className="w-4 h-4" />}
              {testResult.message}
            </div>
          )}
        </div>
      )}

      {/* Scan Report Modal */}
      <Modal isOpen={!!scanReport} onClose={() => setScanReport(null)} title="스키마 스캔 완료">
        {scanReport && (
          <div className="space-y-4">
            {/* Summary */}
            <div className="grid grid-cols-2 gap-3">
              <div className="bg-slate-800 rounded-lg p-3 text-center">
                <div className="text-lg font-bold text-indigo-400">
                  <span className="text-emerald-400">+{scanReport.tables_added}</span>
                  {scanReport.tables_removed > 0 && <span className="text-rose-400 ml-1">-{scanReport.tables_removed}</span>}
                </div>
                <div className="text-xs text-slate-500 mt-1">테이블</div>
              </div>
              <div className="bg-slate-800 rounded-lg p-3 text-center">
                <div className="text-lg font-bold">
                  <span className="text-emerald-400">+{scanReport.columns_added}</span>
                  {scanReport.columns_removed > 0 && <span className="text-rose-400 ml-1">-{scanReport.columns_removed}</span>}
                  {scanReport.columns_updated > 0 && <span className="text-amber-400 ml-1">~{scanReport.columns_updated}</span>}
                </div>
                <div className="text-xs text-slate-500 mt-1">컬럼</div>
              </div>
            </div>

            {/* Detail stats */}
            <div className="text-xs text-slate-400 space-y-1 bg-slate-800/60 rounded-lg px-3 py-2">
              <p>변경 없이 스킵: <span className="text-slate-300">{scanReport.columns_skipped}건</span></p>
              {scanReport.embeddings_created > 0 && (
                <p>임베딩 생성: <span className="text-indigo-300">{scanReport.embeddings_created}건</span></p>
              )}
              {scanReport.orphan_synonyms_deleted > 0 && (
                <p>용어 자동 삭제: <span className="text-rose-400">{scanReport.orphan_synonyms_deleted}건</span> (참조 컬럼 삭제됨)</p>
              )}
            </div>

            {!hasChanges && (
              <div className="flex items-center gap-2 text-sm text-slate-400 bg-slate-800/60 rounded-lg px-3 py-3">
                <CheckCircle className="w-4 h-4 text-emerald-400" />
                변경 사항이 없습니다. 스키마가 최신 상태입니다.
              </div>
            )}

            {/* Action buttons */}
            {hasChanges && (
              <div className="space-y-2">
                {scanReport.changed_tables.length > 0 && (
                  <>
                    <button
                      onClick={() => {
                        (window as any).__sqlSuggestTables = scanReport.changed_tables;
                        setScanReport(null);
                        window.dispatchEvent(new CustomEvent('sql-admin-navigate', { detail: { tab: 'erd' } }));
                      }}
                      className="w-full flex items-center justify-between gap-2 px-3 py-2.5 rounded-lg border border-slate-600 bg-slate-800 text-slate-200 hover:bg-slate-700 transition-colors text-sm"
                    >
                      <span className="flex items-center gap-2">
                        <Network className="w-4 h-4 text-indigo-400" />
                        관계 추천 대상 {scanReport.changed_tables.length}개 테이블
                      </span>
                      <ArrowRight className="w-3.5 h-3.5 text-slate-500" />
                    </button>
                    <button
                      onClick={() => {
                        (window as any).__sqlSuggestSynonyms = scanReport.changed_tables;
                        setScanReport(null);
                        window.dispatchEvent(new CustomEvent('sql-admin-navigate', { detail: { tab: 'synonym' } }));
                      }}
                      className="w-full flex items-center justify-between gap-2 px-3 py-2.5 rounded-lg border border-slate-600 bg-slate-800 text-slate-200 hover:bg-slate-700 transition-colors text-sm"
                    >
                      <span className="flex items-center gap-2">
                        <Sparkles className="w-4 h-4 text-amber-400" />
                        신규 테이블 용어 자동생성 추천
                      </span>
                      <ArrowRight className="w-3.5 h-3.5 text-slate-500" />
                    </button>
                  </>
                )}
                {scanReport.orphan_synonyms_warn.length > 0 && (
                  <button
                    onClick={() => {
                      setScanReport(null);
                      window.dispatchEvent(new CustomEvent('sql-admin-navigate', { detail: { tab: 'synonym' } }));
                    }}
                    className="w-full flex items-center justify-between gap-2 px-3 py-2.5 rounded-lg border border-amber-600/40 bg-amber-900/20 text-amber-300 hover:bg-amber-900/40 transition-colors text-sm"
                  >
                    <span>수정 필요 용어 {scanReport.orphan_synonyms_warn.length}건</span>
                    <span className="flex items-center gap-1 text-xs">용어 관리로 이동 <ArrowRight className="w-3.5 h-3.5" /></span>
                  </button>
                )}
              </div>
            )}

            <div className="flex justify-end">
              <Button variant="secondary" onClick={() => setScanReport(null)}>닫기</Button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}

// ── SchemaTab ─────────────────────────────────────────────────────────────────

function SchemaTab() {
  const { ns, setNs, namespaces } = useNamespace();
  const qc = useQueryClient();
  const [search, setSearch] = useState('');
  const [selectedTable, setSelectedTable] = useState<string | null>(null);

  const { data: schema = [], isLoading } = useQuery({
    queryKey: ['sql_schema', ns],
    queryFn: () => getFullSchema(ns),
    enabled: !!ns,
  });

  // Auto-select first table when data loads
  useEffect(() => {
    if (schema.length > 0 && !selectedTable) setSelectedTable(schema[0].table_name);
  }, [schema, selectedTable]);

  const reindexMut = useMutation({ mutationFn: () => reindexSchema(ns) });
  const toggleMut = useMutation({
    mutationFn: (t: SchemaTableWithCols) => toggleSchemaTable(ns, t.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['sql_schema', ns] }),
  });
  const updateColDesc = useMutation({
    mutationFn: ({ id, description }: { id: number; description: string }) => updateSchemaColumnDesc(ns, id, description),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['sql_schema', ns] }),
  });
  const updateTableDesc = useMutation({
    mutationFn: ({ id, description }: { id: number; description: string }) => updateSchemaTableDesc(ns, id, description),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['sql_schema', ns] }),
  });

  const lowSearch = search.toLowerCase();
  const filteredSchema = search
    ? schema.filter((t) =>
        t.table_name.toLowerCase().includes(lowSearch) ||
        t.columns.some((c) => c.name.toLowerCase().includes(lowSearch) || c.description.toLowerCase().includes(lowSearch))
      )
    : schema;

  const currentTable = filteredSchema.find((t) => t.table_name === selectedTable) ?? filteredSchema[0] ?? null;

  return (
    <div className="space-y-4">
      {/* Header bar */}
      <div className="flex items-center gap-3 flex-wrap justify-between">
        <div className="flex items-center gap-2">
          <NsSelect ns={ns} setNs={setNs} namespaces={namespaces} />
          {ns && (
            <div className="relative">
              <Search className="absolute left-2.5 top-2 w-3.5 h-3.5 text-slate-500 pointer-events-none" />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="테이블명, 컬럼명 검색..."
                className="pl-8 pr-3 py-1.5 bg-slate-800 border border-slate-600 rounded-lg text-sm text-slate-300 placeholder-slate-500 focus:outline-none focus:border-indigo-500 w-60"
              />
            </div>
          )}
        </div>
        {ns && (
          <div className="text-xs text-slate-500">
            {filteredSchema.length}건
            <Button size="sm" variant="secondary" className="ml-3" onClick={() => reindexMut.mutate()} disabled={reindexMut.isPending}>
              <RefreshCw className="w-3.5 h-3.5 mr-1" /> 재인덱싱
            </Button>
          </div>
        )}
      </div>

      {ns && isLoading && <p className="text-slate-400 text-sm">로딩 중...</p>}

      {ns && !isLoading && (
        <>
          {/* Table tabs */}
          <div className="flex gap-1 overflow-x-auto pb-1 border-b border-slate-700">
            {filteredSchema.map((t) => (
              <button
                key={t.table_name}
                onClick={() => setSelectedTable(t.table_name)}
                className={clsx(
                  'flex items-center gap-1.5 px-3 py-1.5 rounded-t-lg text-xs whitespace-nowrap transition-colors flex-shrink-0',
                  currentTable?.table_name === t.table_name
                    ? 'bg-indigo-600/20 text-indigo-300 border border-indigo-600/40 border-b-transparent'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800',
                )}
              >
                <input
                  type="checkbox"
                  checked={t.is_selected}
                  onChange={() => toggleMut.mutate(t)}
                  onClick={(e) => e.stopPropagation()}
                  className="w-3 h-3 rounded accent-indigo-500"
                />
                <span>{t.table_name}</span>
                <span className="text-slate-500">{t.columns.length}</span>
              </button>
            ))}
          </div>

          {/* Column table */}
          {currentTable && (
            <div>
              {/* Table description */}
              <div className="flex items-center gap-3 mb-3">
                <h3 className="text-sm font-semibold text-indigo-300">{currentTable.table_name}</h3>
                <input
                  key={`${currentTable.id}-desc`}
                  defaultValue={currentTable.description}
                  onBlur={(e) => { if (e.target.value !== currentTable.description) updateTableDesc.mutate({ id: currentTable.id, description: e.target.value }); }}
                  placeholder="테이블 설명..."
                  className="flex-1 bg-transparent border-b border-slate-700 focus:border-indigo-500 px-1 py-0.5 text-xs text-slate-400 placeholder-slate-600 outline-none"
                />
              </div>

              {/* Columns */}
              <div className="overflow-x-auto rounded-xl border border-slate-700">
                <table className="w-full text-sm border-collapse">
                  <thead>
                    <tr className="bg-slate-800 text-slate-400 text-xs">
                      <th className="px-4 py-2.5 text-left font-medium">컬럼</th>
                      <th className="px-4 py-2.5 text-left font-medium">타입</th>
                      <th className="px-4 py-2.5 text-left font-medium">설명</th>
                      <th className="px-4 py-2.5 text-center font-medium">키</th>
                    </tr>
                  </thead>
                  <tbody>
                    {currentTable.columns.map((col) => (
                      <tr key={col.id} className="border-t border-slate-700/60 hover:bg-slate-800/40">
                        <td className="px-4 py-2 font-mono text-indigo-300 text-xs whitespace-nowrap">{col.name}</td>
                        <td className="px-4 py-2 text-slate-400 text-xs whitespace-nowrap">{col.data_type}</td>
                        <td className="px-4 py-2">
                          <input
                            key={`${col.id}-${col.description}`}
                            defaultValue={col.description}
                            onBlur={(e) => { if (e.target.value !== col.description) updateColDesc.mutate({ id: col.id, description: e.target.value }); }}
                            placeholder="설명 추가..."
                            className="w-full bg-transparent border-b border-transparent focus:border-indigo-500 px-1 py-0.5 text-xs text-slate-300 placeholder-slate-600 outline-none"
                          />
                        </td>
                        <td className="px-4 py-2 text-center">
                          <div className="flex items-center justify-center gap-1">
                            {col.is_pk && (
                              <span className="px-1.5 py-0.5 bg-amber-500/20 text-amber-400 text-[10px] rounded font-medium">PK</span>
                            )}
                            {col.fk_reference && (
                              <span className="px-1.5 py-0.5 bg-blue-500/20 text-blue-400 text-[10px] rounded font-medium" title={col.fk_reference}>FK</span>
                            )}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {filteredSchema.length === 0 && (
            <p className="text-slate-500 text-sm text-center py-8">
              {search ? '검색 결과가 없습니다.' : '스키마가 없습니다. 대상 DB 탭에서 스캔하세요.'}
            </p>
          )}
        </>
      )}
    </div>
  );
}

// ── ErdTab (SVG-based ERD) ────────────────────────────────────────────────────

const ERD_W = 180;
const ERD_HDR = 32;
const ERD_ROW = 22;
const ERD_COL_W = 120;
const ERD_PAD = 8;

function erdNodeH(cols: number) { return ERD_HDR + cols * ERD_ROW + ERD_PAD; }

type ErdPos = Record<string, { x: number; y: number }>;

function autoLayout(tables: SchemaTableWithCols[], relations?: Array<{ from_table: string; to_table: string }>): ErdPos {
  const pos: ErdPos = {};
  const names = new Set(tables.map((t) => t.table_name));

  if (!relations || relations.length === 0) {
    // Fallback: simple grid
    const cols = 4;
    tables.forEach((t, i) => {
      pos[t.table_name] = { x: 40 + (i % cols) * (ERD_W + 60), y: 40 + Math.floor(i / cols) * 260 };
    });
    return pos;
  }

  // Build adjacency: to_table ← from_table (FK points from child to parent)
  const inDegree: Record<string, number> = {};
  const children: Record<string, string[]> = {};
  for (const n of names) { inDegree[n] = 0; children[n] = []; }
  for (const r of relations) {
    if (!names.has(r.from_table) || !names.has(r.to_table)) continue;
    // from_table references to_table → to_table is parent (level closer to 0)
    children[r.to_table].push(r.from_table);
    inDegree[r.from_table]++;
  }

  // Topological sort → assign levels (BFS from roots)
  const level: Record<string, number> = {};
  const queue: string[] = [];
  for (const n of names) {
    if (inDegree[n] === 0) { queue.push(n); level[n] = 0; }
  }
  let qi = 0;
  while (qi < queue.length) {
    const node = queue[qi++];
    for (const child of (children[node] || [])) {
      level[child] = Math.max(level[child] ?? 0, (level[node] ?? 0) + 1);
      inDegree[child]--;
      if (inDegree[child] === 0) queue.push(child);
    }
  }
  // Handle cycles: assign remaining to max level + 1
  const maxLevel = Math.max(0, ...Object.values(level));
  for (const n of names) {
    if (level[n] === undefined) level[n] = maxLevel + 1;
  }

  // Group by level
  const levels: Record<number, string[]> = {};
  for (const n of names) {
    const lv = level[n] ?? 0;
    if (!levels[lv]) levels[lv] = [];
    levels[lv].push(n);
  }

  // Assign positions: highest level on left (children), level 0 on right (parents)
  const GAP_X = ERD_W + 80;
  const GAP_Y = 30;
  const sortedLevels = Object.keys(levels).map(Number).sort((a, b) => b - a);
  let colIdx = 0;
  for (const lv of sortedLevels) {
    const group = levels[lv];
    // Sort by table name for consistency
    group.sort();
    let yOffset = 40;
    for (const tname of group) {
      pos[tname] = { x: 40 + colIdx * GAP_X, y: yOffset };
      const tbl = tables.find((t) => t.table_name === tname);
      yOffset += erdNodeH(tbl?.columns.length ?? 5) + GAP_Y;
    }
    colIdx++;
  }

  return pos;
}

// Relation type → color (dark-mode friendly)
const REL_COLORS: Record<string, string> = {
  'N:1': '#818cf8', '1:N': '#34d399', '1:1': '#fbbf24', 'N:M': '#f87171',
};

type Connecting = { fromTable: string; fromCol: string; colIdx: number; x1: number; y1: number; cx: number; cy: number };
type SuggestItem = { from_table: string; from_col: string; to_table: string; to_col: string; relation_type: string; reason: string };

function ErdTab() {
  const { ns, setNs, namespaces } = useNamespace();
  const qc = useQueryClient();

  const { data: schema = [] } = useQuery({ queryKey: ['sql_schema', ns], queryFn: () => getFullSchema(ns), enabled: !!ns });
  const { data: relations = [] } = useQuery({ queryKey: ['sql_relations', ns], queryFn: () => listRelations(ns), enabled: !!ns });

  const [positions, setPositions] = useState<ErdPos>({});
  const [posHistory, setPosHistory] = useState<ErdPos[]>([]);  // undo stack
  const [dragging, setDragging] = useState<{ table: string; ox: number; oy: number } | null>(null);
  const [panning, setPanning] = useState<{ startX: number; startY: number; startScrollLeft: number; startScrollTop: number } | null>(null);
  const [connecting, setConnecting] = useState<Connecting | null>(null);
  const [zoom, setZoom] = useState(1);
  const [selected, setSelected] = useState<number | null>(null);
  const [selectedErdTable, setSelectedErdTable] = useState<string | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [addForm, setAddForm] = useState<Omit<SqlRelation, 'id'>>({ from_table: '', from_col: '', to_table: '', to_col: '', relation_type: 'N:1', description: '' });
  const [suggestions, setSuggestions] = useState<SuggestItem[]>([]);
  const [showSuggest, setShowSuggest] = useState(false);
  const [pendingSuggestTables, setPendingSuggestTables] = useState<string[] | null>(null);
  const [erdSearch, setErdSearch] = useState('');
  const [focusedTable, setFocusedTable] = useState<string | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const relListRef = useRef<HTMLDivElement>(null);
  const posRef = useRef(positions);
  posRef.current = positions;

  // Check for pending suggest tables on mount (set by TargetDbTab scan report)
  useEffect(() => {
    const pending = (window as any).__sqlSuggestTables;
    if (pending && pending.length > 0) {
      setPendingSuggestTables(pending);
      (window as any).__sqlSuggestTables = null;
    }
  }, []);

  // Scroll right panel to selected relation
  const scrollRelIntoView = useCallback((relId: number) => {
    setTimeout(() => {
      const el = relListRef.current?.querySelector(`[data-rel-id="${relId}"]`);
      el?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }, 50);
  }, []);

  const [suggestHighlight, setSuggestHighlight] = useState(false);

  // When pending tables arrive: focus first new table + highlight suggest button
  useEffect(() => {
    if (pendingSuggestTables && pendingSuggestTables.length > 0 && ns && schema.length > 0) {
      // Focus on first changed table
      const firstTable = pendingSuggestTables[0];
      setFocusedTable(firstTable);
      const container = containerRef.current;
      const pos = posRef.current[firstTable];
      if (container && pos) {
        setTimeout(() => {
          container.scrollTo({
            left: pos.x * zoom - container.clientWidth / 2 + ERD_W * zoom / 2,
            top: pos.y * zoom - container.clientHeight / 2 + 100,
            behavior: 'smooth',
          });
        }, 300);
      }
      // Highlight suggest button
      setSuggestHighlight(true);
      setTimeout(() => setSuggestHighlight(false), 5000);
      setPendingSuggestTables(null);
    }
  }, [pendingSuggestTables, ns, schema]);

  // Load positions: prefer DB pos_x/pos_y, fallback to auto layout
  useEffect(() => {
    if (schema.length === 0) return;
    const dbPos: ErdPos = {};
    schema.forEach((t) => {
      if (t.pos_x !== 0 || t.pos_y !== 0) {
        dbPos[t.table_name] = { x: t.pos_x, y: t.pos_y };
      }
    });
    if (Object.keys(dbPos).length > 0) {
      setPositions({ ...autoLayout(schema, relations), ...dbPos });
    } else {
      setPositions(autoLayout(schema, relations));
    }
  }, [schema]);

  // Save positions to DB
  const savePosMut = useMutation({
    mutationFn: (pos: ErdPos) => saveSchemaPositions(ns, pos),
  });

  const savePositions = useCallback((pos: ErdPos) => {
    savePosMut.mutate(pos);
  }, [savePosMut]);

  const addMut = useMutation({
    mutationFn: (form: Omit<SqlRelation, 'id'>) => createRelation(ns, form),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['sql_relations', ns] }); setShowAdd(false); },
  });
  const delMut = useMutation({
    mutationFn: (id: number) => deleteRelation(ns, id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['sql_relations', ns] }); setSelected(null); },
  });
  const suggestMut = useMutation({
    mutationFn: (targetTables?: string[]) => suggestRelationsAI(ns, targetTables),
    onSuccess: (r) => { setSuggestions(r.suggestions); setShowSuggest(true); },
    onError: (e: Error) => alert(`관계 추천 실패: ${e.message}`),
  });

  const svgCoords = (e: React.MouseEvent) => {
    if (!svgRef.current) return { mx: 0, my: 0 };
    const r = svgRef.current.getBoundingClientRect();
    return { mx: (e.clientX - r.left) / zoom, my: (e.clientY - r.top) / zoom };
  };

  const dragStartRef = useRef<{ x: number; y: number } | null>(null);

  const handleTableMouseDown = (e: React.MouseEvent, table: string) => {
    e.stopPropagation();
    const { mx, my } = svgCoords(e);
    const p = posRef.current[table] ?? { x: 0, y: 0 };
    dragStartRef.current = { x: mx, y: my };
    // Push current positions to undo history
    setPosHistory((h) => [...h.slice(-19), { ...posRef.current }]);
    setDragging({ table, ox: mx - p.x, oy: my - p.y });
  };

  const handleDotMouseDown = (e: React.MouseEvent, table: string, col: string, colIdx: number) => {
    e.stopPropagation();
    const { mx, my } = svgCoords(e);
    setConnecting({ fromTable: table, fromCol: col, colIdx, x1: mx, y1: my, cx: mx, cy: my });
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    const { mx, my } = svgCoords(e);
    if (dragging) {
      setPositions((prev) => ({ ...prev, [dragging.table]: { x: mx - dragging.ox, y: my - dragging.oy } }));
    }
    if (connecting) {
      setConnecting((c) => c ? { ...c, cx: mx, cy: my } : null);
    }
    if (panning) {
      const container = containerRef.current;
      if (container) {
        container.scrollLeft = panning.startScrollLeft - (e.clientX - panning.startX);
        container.scrollTop = panning.startScrollTop - (e.clientY - panning.startY);
      }
    }
  };

  const handleMouseUp = (e: React.MouseEvent) => {
    if (panning) { setPanning(null); return; }
    if (dragging) {
      // Check if it was a click (no drag movement)
      const { mx, my } = svgCoords(e);
      const ds = dragStartRef.current;
      const wasDrag = ds && (Math.abs(mx - ds.x) > 3 || Math.abs(my - ds.y) > 3);
      if (!wasDrag) {
        // Click on table → select/deselect table
        const clickedTable = dragging.table;
        setSelectedErdTable((prev) => prev === clickedTable ? null : clickedTable);
        setSelected(null);
      }
      dragStartRef.current = null;
      savePositions(posRef.current);
      setDragging(null);
    }
    if (connecting) {
      const { mx, my } = svgCoords(e);
      let hit: { table: string; col: string } | null = null;
      for (const t of schema) {
        const p = posRef.current[t.table_name] ?? { x: 0, y: 0 };
        for (let i = 0; i < t.columns.length; i++) {
          const dotX = p.x;
          const dotY = p.y + ERD_HDR + i * ERD_ROW + ERD_ROW / 2;
          if (Math.abs(mx - dotX) < 10 && Math.abs(my - dotY) < 10) {
            hit = { table: t.table_name, col: t.columns[i].name };
            break;
          }
        }
        if (hit) break;
      }
      if (hit && hit.table !== connecting.fromTable) {
        setAddForm({ from_table: connecting.fromTable, from_col: connecting.fromCol, to_table: hit.table, to_col: hit.col, relation_type: 'N:1', description: '' });
        setShowAdd(true);
      }
      setConnecting(null);
    }
  };

  const handleUndo = () => {
    setPosHistory((h) => {
      if (h.length === 0) return h;
      const prev = h[h.length - 1];
      setPositions(prev);
      savePositions(prev);
      return h.slice(0, -1);
    });
  };

  const handleAutoLayout = () => {
    setPosHistory((h) => [...h.slice(-19), { ...posRef.current }]);
    const p = autoLayout(schema, relations);
    setPositions(p);
    savePositions(p);
  };

  // Ctrl+Z undo
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'z') { e.preventDefault(); handleUndo(); }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  });

  const getPath = (rel: SqlRelation) => {
    const fromTbl = schema.find((t) => t.table_name === rel.from_table);
    const toTbl = schema.find((t) => t.table_name === rel.to_table);
    if (!fromTbl || !toTbl) return null;
    const fp = positions[rel.from_table] ?? { x: 0, y: 0 };
    const tp = positions[rel.to_table] ?? { x: 0, y: 0 };
    const fi = fromTbl.columns.findIndex((c) => c.name === rel.from_col);
    const ti = toTbl.columns.findIndex((c) => c.name === rel.to_col);
    const fy = fp.y + ERD_HDR + (fi >= 0 ? fi * ERD_ROW + ERD_ROW / 2 : ERD_HDR / 2);
    const ty = tp.y + ERD_HDR + (ti >= 0 ? ti * ERD_ROW + ERD_ROW / 2 : ERD_HDR / 2);
    const x1 = fp.x + ERD_W; const x2 = tp.x;
    const mx2 = (x1 + x2) / 2;
    return { d: `M${x1},${fy} C${mx2},${fy} ${mx2},${ty} ${x2},${ty}`, fy, ty, x1, x2 };
  };

  const totalW = Math.max(800, ...schema.map((t) => (positions[t.table_name]?.x ?? 0) + ERD_W + 80));
  const totalH = Math.max(500, ...schema.map((t) => (positions[t.table_name]?.y ?? 0) + erdNodeH(t.columns.length) + 80));
  const tableNames = schema.map((t) => t.table_name);

  return (
    <div className="flex flex-col gap-3" style={{ height: 'calc(100vh - 220px)' }}>
      {/* Header toolbar */}
      <div className="flex items-center gap-2 flex-wrap justify-between flex-shrink-0">
        <div className="flex items-center gap-2">
          <NsSelect ns={ns} setNs={setNs} namespaces={namespaces} />
          {ns && schema.length > 0 && (
            <div className="relative">
              <Search className="absolute left-2.5 top-2 w-3.5 h-3.5 text-slate-500 pointer-events-none" />
              <input
                value={erdSearch}
                onChange={(e) => {
                  setErdSearch(e.target.value);
                  const q = e.target.value.toLowerCase();
                  if (q) {
                    const match = schema.find((t) => t.table_name.toLowerCase().includes(q));
                    if (match) {
                      setFocusedTable(match.table_name);
                      // Scroll to the table
                      const container = containerRef.current;
                      const pos = posRef.current[match.table_name];
                      if (container && pos) {
                        container.scrollTo({
                          left: pos.x * zoom - container.clientWidth / 2 + ERD_W * zoom / 2,
                          top: pos.y * zoom - container.clientHeight / 2 + 100,
                          behavior: 'smooth',
                        });
                      }
                    } else {
                      setFocusedTable(null);
                    }
                  } else {
                    setFocusedTable(null);
                  }
                }}
                placeholder="테이블 검색..."
                className="pl-8 pr-3 py-1.5 bg-slate-800 border border-slate-600 rounded-lg text-sm text-slate-300 placeholder-slate-500 focus:outline-none focus:border-indigo-500 w-48"
              />
            </div>
          )}
        </div>
        {ns && schema.length > 0 && (
          <div className="flex items-center gap-1.5">
            {/* Undo */}
            <button onClick={handleUndo} disabled={posHistory.length === 0}
              className="flex items-center gap-1 px-2 py-1.5 text-xs rounded-lg border border-slate-600 text-slate-400 hover:text-slate-200 hover:border-slate-500 disabled:opacity-30 disabled:cursor-not-allowed transition-colors">
              <Undo2 className="w-3.5 h-3.5" /> 되돌리기
            </button>
            {/* Auto layout */}
            <button onClick={handleAutoLayout}
              className="flex items-center gap-1 px-2 py-1.5 text-xs rounded-lg border border-slate-600 text-slate-400 hover:text-slate-200 hover:border-slate-500 transition-colors">
              <RefreshCw className="w-3.5 h-3.5" /> 자동 정리
            </button>
            {/* Zoom controls */}
            <div className="flex items-center gap-0.5 border border-slate-600 rounded-lg overflow-hidden">
              <button onClick={() => setZoom((z) => Math.max(0.3, z - 0.1))} className="px-1.5 py-1.5 text-slate-400 hover:text-slate-200 hover:bg-slate-700 transition-colors">
                <ZoomOut className="w-3.5 h-3.5" />
              </button>
              <span className="px-2 text-xs text-slate-400 bg-slate-800 select-none min-w-[44px] text-center">{Math.round(zoom * 100)}%</span>
              <button onClick={() => setZoom((z) => Math.min(2, z + 0.1))} className="px-1.5 py-1.5 text-slate-400 hover:text-slate-200 hover:bg-slate-700 transition-colors">
                <ZoomIn className="w-3.5 h-3.5" />
              </button>
            </div>
            {/* AI suggest */}
            <button onClick={() => { suggestMut.mutate(undefined); setSuggestHighlight(false); }} disabled={suggestMut.isPending}
              className={clsx(
                'flex items-center gap-1 px-2.5 py-1.5 text-xs rounded-lg bg-violet-700 hover:bg-violet-600 text-white disabled:opacity-50 transition-colors relative',
                suggestHighlight && 'ring-2 ring-violet-400 ring-offset-2 ring-offset-slate-900 animate-pulse',
              )}>
              <Sparkles className="w-3.5 h-3.5" /> {suggestMut.isPending ? '분석 중...' : '관계 추천'}
            </button>
            {/* Add relation */}
            <button
              onClick={() => { setAddForm({ from_table: '', from_col: '', to_table: '', to_col: '', relation_type: 'N:1', description: '' }); setShowAdd(true); }}
              className="flex items-center gap-1 px-2.5 py-1.5 text-xs rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white transition-colors">
              <Plus className="w-3.5 h-3.5" /> 관계 추가
            </button>
          </div>
        )}
      </div>

      {ns && schema.length > 0 && (
        <div className="flex gap-3 flex-1 min-h-0">
          {/* SVG Canvas */}
          <div
            ref={containerRef}
            className="flex-1 border border-slate-600 rounded-xl overflow-auto bg-slate-800/30"
            onWheel={(e) => {
              if (!e.altKey) return;
              e.preventDefault();
              setZoom((z) => Math.max(0.3, Math.min(2, z * (e.deltaY > 0 ? 0.9 : 1.1))));
            }}
          >
            <svg
              ref={svgRef}
              width={totalW * zoom}
              height={totalH * zoom}
              style={{ cursor: dragging || panning ? 'grabbing' : connecting ? 'crosshair' : 'grab', userSelect: 'none' }}
              onMouseDown={(e) => {
                if (dragging || connecting) return;
                const container = containerRef.current;
                if (!container) return;
                setPanning({ startX: e.clientX, startY: e.clientY, startScrollLeft: container.scrollLeft, startScrollTop: container.scrollTop });
              }}
              onMouseMove={handleMouseMove}
              onMouseUp={handleMouseUp}
              onMouseLeave={handleMouseUp}
            >
              {/* Grid dots background */}
              <defs>
                <pattern id="grid" width={20 * zoom} height={20 * zoom} patternUnits="userSpaceOnUse">
                  <circle cx={1} cy={1} r={0.5} fill="#334155" />
                </pattern>
                {/* Arrow markers — dynamic per relation color */}
                {Object.entries(REL_COLORS).map(([type, clr]) => (
                  <marker key={type} id={`arr-${type.replace(':', '')}`} markerWidth={8} markerHeight={8} refX={6} refY={3} orient="auto" markerUnits="strokeWidth">
                    <path d="M0,0 L0,6 L8,3 z" fill={clr} />
                  </marker>
                ))}
                <marker id="arr-sel" markerWidth={8} markerHeight={8} refX={6} refY={3} orient="auto" markerUnits="strokeWidth">
                  <path d="M0,0 L0,6 L8,3 z" fill="#f59e0b" />
                </marker>
              </defs>
              <rect width="100%" height="100%" fill="url(#grid)" />

              <g transform={`scale(${zoom})`}>
                {/* Relation lines — sorted: dimmed first, highlighted last */}
                {[...relations].sort((a, b) => {
                  const aH = (selected === a.id) || (selectedErdTable ? (a.from_table === selectedErdTable || a.to_table === selectedErdTable) : false);
                  const bH = (selected === b.id) || (selectedErdTable ? (b.from_table === selectedErdTable || b.to_table === selectedErdTable) : false);
                  return (aH ? 1 : 0) - (bH ? 1 : 0);
                }).map((rel) => {
                  const path = getPath(rel);
                  if (!path) return null;
                  const isSelected = selected === rel.id;
                  const isTableRelated = selectedErdTable ? (rel.from_table === selectedErdTable || rel.to_table === selectedErdTable) : false;
                  const highlighted = isSelected || isTableRelated;
                  const color = REL_COLORS[rel.relation_type] ?? '#64748b';
                  const lineColor = isSelected ? '#818cf8' : isTableRelated ? '#f59e0b' : color;
                  const markerId = isSelected ? 'arr-sel' : `arr-${rel.relation_type.replace(':', '')}`;
                  return (
                    <g key={rel.id} onClick={(e) => { e.stopPropagation(); const newId = isSelected ? null : rel.id; setSelected(newId); setSelectedErdTable(null); if (newId) scrollRelIntoView(newId); }} style={{ cursor: 'pointer' }}>
                      {/* Hit area */}
                      <path d={path.d} stroke="transparent" strokeWidth={14} fill="none" />
                      {/* Shadow for contrast */}
                      <path d={path.d} stroke="white" strokeWidth={highlighted ? 5 : 4}
                        fill="none" strokeDasharray={highlighted ? undefined : '8 5'} opacity={0.5} />
                      {/* Main line */}
                      <path d={path.d} stroke={lineColor}
                        strokeWidth={highlighted ? 2.5 : 2}
                        fill="none"
                        strokeDasharray={highlighted ? undefined : '8 5'}
                        opacity={highlighted ? 1 : ((selectedErdTable || selected) ? 0.15 : 1)}
                        markerEnd={`url(#${markerId})`} />
                      <text
                        x={(path.x1 + path.x2) / 2}
                        y={(path.fy + path.ty) / 2 - 6}
                        textAnchor="middle"
                        fill="white"
                        fontSize={9} fontWeight="800"
                        stroke="white" strokeWidth={3} paintOrder="stroke"
                        style={{ pointerEvents: 'none' }}
                      >{rel.relation_type}</text>
                      <text
                        x={(path.x1 + path.x2) / 2}
                        y={(path.fy + path.ty) / 2 - 6}
                        textAnchor="middle" fill={lineColor}
                        fontSize={9} fontWeight="800"
                        style={{ pointerEvents: 'none' }}
                      >{rel.relation_type}</text>
                    </g>
                  );
                })}

                {/* Connecting drag preview */}
                {connecting && (
                  <line
                    x1={connecting.x1} y1={connecting.y1}
                    x2={connecting.cx} y2={connecting.cy}
                    stroke="#f97316" strokeWidth={2} strokeDasharray="5 3" opacity={0.9}
                  />
                )}

                {/* Table nodes — sorted: dimmed first, highlighted last (SVG z-order) */}
                {[...schema].sort((a, b) => {
                  const selectedRel = selected ? relations.find((r) => r.id === selected) : null;
                  const aHighlighted = (selectedErdTable === a.table_name) ||
                    (selectedErdTable && relations.some((r) => (r.from_table === selectedErdTable || r.to_table === selectedErdTable) && (r.from_table === a.table_name || r.to_table === a.table_name))) ||
                    (selectedRel && (selectedRel.from_table === a.table_name || selectedRel.to_table === a.table_name));
                  const bHighlighted = (selectedErdTable === b.table_name) ||
                    (selectedErdTable && relations.some((r) => (r.from_table === selectedErdTable || r.to_table === selectedErdTable) && (r.from_table === b.table_name || r.to_table === b.table_name))) ||
                    (selectedRel && (selectedRel.from_table === b.table_name || selectedRel.to_table === b.table_name));
                  return (aHighlighted ? 1 : 0) - (bHighlighted ? 1 : 0);
                }).map((t) => {
                  const p = positions[t.table_name] ?? { x: 0, y: 0 };
                  const h = erdNodeH(t.columns.length);
                  const isFocused = focusedTable === t.table_name;
                  const isErdSelected = selectedErdTable === t.table_name;
                  const isRelatedToSelected = selectedErdTable ? relations.some((r) => (r.from_table === selectedErdTable || r.to_table === selectedErdTable) && (r.from_table === t.table_name || r.to_table === t.table_name)) : false;
                  // 관계 하나 선택 시 해당 두 테이블만 강조
                  const selectedRel = selected ? relations.find((r) => r.id === selected) : null;
                  const isRelEndpoint = selectedRel ? (selectedRel.from_table === t.table_name || selectedRel.to_table === t.table_name) : false;
                  const hasAnySelection = !!selectedErdTable || !!selected;
                  const isHighlighted = isErdSelected || isRelatedToSelected || isRelEndpoint;
                  const dimmed = hasAnySelection && !isHighlighted;
                  return (
                    <g key={t.table_name} transform={`translate(${p.x},${p.y})`}
                      onMouseDown={(e) => handleTableMouseDown(e, t.table_name)}
                      style={{ cursor: dragging?.table === t.table_name ? 'grabbing' : 'grab' }}>
                      {/* Focus glow */}
                      {isFocused && (
                        <rect x={-4} y={-4} width={ERD_W + 8} height={h + 8} rx={10}
                          fill="none" stroke="#6366f1" strokeWidth={3} opacity={0.8}>
                          <animate attributeName="opacity" values="0.8;0.3;0.8" dur="1.5s" repeatCount="indefinite" />
                        </rect>
                      )}
                      {/* Shadow */}
                      <rect x={3} y={3} width={ERD_W} height={h} rx={7} fill="rgba(0,0,0,0.4)" />
                      {/* Card body */}
                      <rect width={ERD_W} height={h} rx={7}
                        fill={isFocused ? '#eef2ff' : isErdSelected ? '#fffbeb' : '#f8fafc'}
                        stroke={isFocused ? '#6366f1' : isErdSelected ? '#f59e0b' : '#94a3b8'}
                        strokeWidth={isFocused || isErdSelected ? 2 : 1}
                        opacity={dimmed ? 0.25 : 1} />
                      {/* Header */}
                      <rect width={ERD_W} height={ERD_HDR} rx={7}
                        fill={isFocused ? '#c7d2fe' : isErdSelected ? '#fde68a' : '#e2e8f0'}
                        opacity={dimmed ? 0.25 : 1} />
                      <rect y={ERD_HDR - 7} width={ERD_W} height={7}
                        fill={isFocused ? '#c7d2fe' : isErdSelected ? '#fde68a' : '#e2e8f0'}
                        opacity={dimmed ? 0.25 : 1} />
                      <text x={10} y={ERD_HDR / 2 + 5} fill={isErdSelected ? '#92400e' : '#334155'} fontSize={11} fontWeight="700">{t.table_name}</text>
                      {/* Divider */}
                      <line x1={0} y1={ERD_HDR} x2={ERD_W} y2={ERD_HDR} stroke="#cbd5e1" strokeWidth={1} />
                      {t.columns.map((col, i) => (
                        <g key={col.id} transform={`translate(0,${ERD_HDR + i * ERD_ROW})`}>
                          <rect width={ERD_W} height={ERD_ROW} fill={i % 2 === 0 ? '#f8fafc' : '#f1f5f9'} opacity={dimmed ? 0.25 : 1} />
                          <text x={10} y={ERD_ROW / 2 + 4}
                            fill={col.is_pk ? '#d97706' : col.fk_reference ? '#4f46e5' : '#1e293b'}
                            fontSize={9} fontFamily="monospace" fontWeight={col.is_pk ? '700' : '400'}
                            opacity={dimmed ? 0.25 : 1}>
                            {col.is_pk ? '🔑 ' : ''}{col.name.length > 16 ? col.name.slice(0, 15) + '…' : col.name}
                          </text>
                          <text x={ERD_COL_W + 4} y={ERD_ROW / 2 + 4} fill="#64748b" fontSize={8} opacity={dimmed ? 0.25 : 1}>
                            {col.data_type.length > 10 ? col.data_type.slice(0, 9) : col.data_type}
                          </text>
                          {/* Left-side target dot */}
                          <circle cx={0} cy={ERD_ROW / 2} r={4} fill="#fff" stroke="#6366f1" strokeWidth={1.5} style={{ cursor: 'crosshair' }} />
                          {/* Right-side source dot */}
                          <circle cx={ERD_W} cy={ERD_ROW / 2} r={4} fill="#6366f1" stroke="#4f46e5" strokeWidth={1}
                            style={{ cursor: 'crosshair' }}
                            onMouseDown={(e) => { e.stopPropagation(); handleDotMouseDown(e, t.table_name, col.name, i); }}
                          />
                        </g>
                      ))}
                      {/* Bottom border */}
                      <rect width={ERD_W} height={h} rx={7} fill="none"
                        stroke={isFocused ? '#6366f1' : isErdSelected ? '#f59e0b' : '#94a3b8'}
                        strokeWidth={isFocused || isErdSelected ? 2 : 1}
                        opacity={dimmed ? 0.25 : 1} />
                    </g>
                  );
                })}

                {/* Highlighted relation lines — re-rendered ON TOP of tables */}
                {(selected || selectedErdTable) && relations.filter((rel) => {
                  const isSelected = selected === rel.id;
                  const isTableRelated = selectedErdTable ? (rel.from_table === selectedErdTable || rel.to_table === selectedErdTable) : false;
                  return isSelected || isTableRelated;
                }).map((rel) => {
                  const path = getPath(rel);
                  if (!path) return null;
                  const isSelected = selected === rel.id;
                  const color = REL_COLORS[rel.relation_type] ?? '#64748b';
                  const lineColor = isSelected ? '#f59e0b' : color;
                  const markerId = isSelected ? 'arr-sel' : `arr-${rel.relation_type.replace(':', '')}`;
                  return (
                    <g key={`top-${rel.id}`} style={{ pointerEvents: 'none' }}>
                      <path d={path.d} stroke="white" strokeWidth={5} fill="none" opacity={0.6} />
                      <path d={path.d} stroke={lineColor} strokeWidth={2.5} fill="none" markerEnd={`url(#${markerId})`} />
                      <text x={(path.x1 + path.x2) / 2} y={(path.fy + path.ty) / 2 - 6}
                        textAnchor="middle" fill="white" fontSize={9} fontWeight="800"
                        stroke="white" strokeWidth={3} paintOrder="stroke" />
                      <text x={(path.x1 + path.x2) / 2} y={(path.fy + path.ty) / 2 - 6}
                        textAnchor="middle" fill={lineColor} fontSize={9} fontWeight="800">{rel.relation_type}</text>
                    </g>
                  );
                })}
              </g>
            </svg>
          </div>

          {/* Right panel — relations list */}
          <div className="w-56 flex-shrink-0 border border-slate-700 rounded-xl bg-slate-900/60 flex flex-col overflow-hidden">
            <div className="px-3 py-2 border-b border-slate-700 flex items-center justify-between flex-shrink-0">
              <span className="text-xs font-semibold text-slate-300">
                관계 목록 {selectedErdTable ? (
                  <span className="text-amber-400 ml-1">
                    {selectedErdTable} ({relations.filter((r) => r.from_table === selectedErdTable || r.to_table === selectedErdTable).length})
                    <button onClick={() => setSelectedErdTable(null)} className="ml-1 text-slate-500 hover:text-slate-300">✕</button>
                  </span>
                ) : `(${relations.length})`}
              </span>
              <span className="text-[10px] text-slate-600">Ctrl+Z: 되돌리기</span>
            </div>
            <div ref={relListRef} className="flex-1 overflow-y-auto p-2 space-y-1">
              {(() => {
                const filteredRels = selectedErdTable
                  ? relations.filter((r) => r.from_table === selectedErdTable || r.to_table === selectedErdTable)
                  : relations;
                if (filteredRels.length === 0) return (
                  <p className="text-xs text-slate-600 text-center py-6">{selectedErdTable ? '관련 관계 없음' : '등록된 관계 없음'}</p>
                );
                return filteredRels.map((rel) => {
                const color = REL_COLORS[rel.relation_type] ?? '#64748b';
                const isSelected = selected === rel.id;
                return (
                  <div
                    key={rel.id}
                    data-rel-id={rel.id}
                    onClick={() => {
                      const newId = isSelected ? null : rel.id;
                      setSelected(newId);
                      setSelectedErdTable(null);
                      if (!isSelected) {
                        // Scroll SVG to midpoint between from/to tables
                        const fp = posRef.current[rel.from_table];
                        const tp = posRef.current[rel.to_table];
                        if (fp && tp && containerRef.current) {
                          const midX = (fp.x + tp.x) / 2;
                          const midY = (fp.y + tp.y) / 2;
                          containerRef.current.scrollTo({
                            left: midX * zoom - containerRef.current.clientWidth / 2 + ERD_W * zoom / 2,
                            top: midY * zoom - containerRef.current.clientHeight / 2,
                            behavior: 'smooth',
                          });
                        }
                        // Scroll right panel to this relation (after filter resets)
                        if (newId) scrollRelIntoView(newId);
                      }
                    }}
                    className={`flex items-center gap-1.5 px-2 py-1.5 rounded-lg cursor-pointer transition-colors group ${
                      isSelected ? 'bg-indigo-900/40 border border-indigo-700/50' : 'hover:bg-slate-800/60'
                    }`}
                  >
                    <span className="text-[10px] font-bold px-1.5 py-0.5 rounded flex-shrink-0" style={{ color: '#fff', backgroundColor: color }}>
                      {rel.relation_type}
                    </span>
                    <span className="text-[10px] text-slate-400 truncate flex-1 leading-tight">
                      {rel.from_table}<br /><span className="text-slate-600">→ {rel.to_table}</span>
                    </span>
                    <button
                      onClick={(e) => { e.stopPropagation(); if (confirm('관계를 삭제하시겠습니까?')) delMut.mutate(rel.id); }}
                      className="opacity-0 group-hover:opacity-100 text-rose-500 hover:text-rose-400 transition-opacity flex-shrink-0"
                    >
                      <Trash2 className="w-3 h-3" />
                    </button>
                  </div>
                );
              });
              })()}
            </div>
            {/* Usage guide */}
            <div className="border-t border-slate-700 p-3 flex-shrink-0 text-[10px] text-slate-500 space-y-1">
              <p>● 테이블 클릭 → 관련 관계 필터</p>
              <p>● 테이블 드래그 → 위치 이동</p>
              <p>● 점 드래그 → 다른 컬럼에 연결</p>
              <p>● 관계선 클릭 → 타입 변경/삭제</p>
              <p>● Alt+휠 → 확대/축소</p>
            </div>
            {/* Selected relation detail */}
            {selected !== null && (() => {
              const rel = relations.find((r) => r.id === selected);
              if (!rel) return null;
              return (
                <div className="border-t border-slate-700 p-2 flex-shrink-0 text-[10px] text-slate-400 space-y-0.5 bg-indigo-950/30">
                  <p className="font-semibold text-indigo-300">{rel.relation_type} 관계</p>
                  <p><span className="text-slate-500">from:</span> {rel.from_table}.{rel.from_col}</p>
                  <p><span className="text-slate-500">to:</span> {rel.to_table}.{rel.to_col}</p>
                  {rel.description && <p className="text-slate-500 truncate">{rel.description}</p>}
                  <button
                    onClick={() => { if (confirm('관계를 삭제하시겠습니까?')) delMut.mutate(selected); }}
                    className="w-full mt-1.5 text-rose-400 hover:text-rose-300 text-[10px] flex items-center justify-center gap-1 py-1 rounded hover:bg-rose-900/20 transition-colors border border-rose-800/30"
                  >
                    <Trash2 className="w-3 h-3" /> 관계 삭제
                  </button>
                </div>
              );
            })()}
          </div>
        </div>
      )}

      {/* Add relation modal */}
      <Modal isOpen={showAdd} onClose={() => setShowAdd(false)} title="관계 추가">
        <div className="space-y-3">
          {(['from_table', 'from_col', 'to_table', 'to_col'] as const).map((f) => (
            <div key={f}>
              <label className="text-xs text-slate-400">{f === 'from_table' ? '시작 테이블' : f === 'from_col' ? '시작 컬럼' : f === 'to_table' ? '대상 테이블' : '대상 컬럼'}</label>
              {f.endsWith('_table') ? (
                <select value={addForm[f]} onChange={(e) => setAddForm({ ...addForm, [f]: e.target.value })}
                  className="mt-1 w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-300">
                  <option value="">선택</option>
                  {tableNames.map((n) => <option key={n} value={n}>{n}</option>)}
                </select>
              ) : (
                <select value={addForm[f]} onChange={(e) => setAddForm({ ...addForm, [f]: e.target.value })}
                  className="mt-1 w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-300">
                  <option value="">선택</option>
                  {(f === 'from_col' ? schema.find((t) => t.table_name === addForm.from_table) : schema.find((t) => t.table_name === addForm.to_table))
                    ?.columns.map((c) => <option key={c.id} value={c.name}>{c.name}</option>)}
                </select>
              )}
            </div>
          ))}
          <div>
            <label className="text-xs text-slate-400">관계 유형</label>
            <select value={addForm.relation_type} onChange={(e) => setAddForm({ ...addForm, relation_type: e.target.value })}
              className="mt-1 w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-300">
              {['N:1', '1:N', '1:1', 'N:M'].map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
          <div>
            <label className="text-xs text-slate-400">설명</label>
            <input value={addForm.description} onChange={(e) => setAddForm({ ...addForm, description: e.target.value })}
              className="mt-1 w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-300" />
          </div>
          <div className="flex gap-2 justify-end">
            <Button variant="secondary" onClick={() => setShowAdd(false)}>취소</Button>
            <Button onClick={() => addMut.mutate(addForm)} disabled={addMut.isPending || !addForm.from_table || !addForm.to_table}>저장</Button>
          </div>
        </div>
      </Modal>

      {/* AI Suggest Relations Modal */}
      <Modal isOpen={showSuggest} onClose={() => setShowSuggest(false)} title={`AI 관계 추천 (${suggestions.length}건)`}>
        <div className="space-y-2 max-h-96 overflow-y-auto">
          {suggestions.length === 0 && <p className="text-sm text-slate-500 text-center py-4">추천할 관계가 없습니다 (이미 모두 등록됨)</p>}
          {suggestions.map((s, i) => (
            <div key={i} className="bg-slate-800/60 border border-slate-700 rounded-lg p-3">
              <div className="flex items-center gap-2 mb-1">
                <span className="text-[10px] font-bold px-1.5 py-0.5 rounded text-white"
                  style={{ backgroundColor: REL_COLORS[s.relation_type] ?? '#64748b' }}>{s.relation_type}</span>
                <span className="text-sm text-slate-200">{s.from_table}.{s.from_col} → {s.to_table}.{s.to_col}</span>
              </div>
              <p className="text-xs text-slate-400 mb-2">{s.reason}</p>
              <button
                onClick={() => {
                  addMut.mutate({ from_table: s.from_table, from_col: s.from_col, to_table: s.to_table, to_col: s.to_col, relation_type: s.relation_type, description: s.reason });
                  setSuggestions((prev) => prev.filter((_, j) => j !== i));
                }}
                className="text-xs text-indigo-400 hover:text-indigo-300 flex items-center gap-1 px-2 py-1 rounded hover:bg-indigo-900/20 transition-colors border border-indigo-800/30"
              >
                <Plus className="w-3 h-3" /> 추가
              </button>
            </div>
          ))}
        </div>
        <div className="flex justify-end mt-4">
          <Button variant="secondary" onClick={() => setShowSuggest(false)}>닫기</Button>
        </div>
      </Modal>
    </div>
  );
}

// ── SynonymTab ────────────────────────────────────────────────────────────────

function SynonymTab() {
  const { ns, setNs, namespaces } = useNamespace();
  const qc = useQueryClient();
  const [search, setSearch] = useState('');
  const [form, setForm] = useState<Omit<SqlSynonym, 'id'>>({ term: '', target: '', description: '' });
  const [editing, setEditing] = useState<SqlSynonym | null>(null);

  const { data: synonyms = [] } = useQuery({ queryKey: ['sql_synonyms', ns], queryFn: () => listSynonyms(ns), enabled: !!ns });
  const addMut = useMutation({
    mutationFn: () => createSynonym(ns, form),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['sql_synonyms', ns] }); setForm({ term: '', target: '', description: '' }); },
  });
  const delMut = useMutation({
    mutationFn: (id: number) => deleteSynonym(ns, id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['sql_synonyms', ns] }),
  });
  const reindexMut = useMutation({ mutationFn: () => reindexSynonyms(ns) });
  const aiGenMut = useMutation({
    mutationFn: (targetTables?: string[]) => generateSynonymsAI(ns, targetTables),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ['sql_synonyms', ns] });
      alert(`AI 자동생성 완료: ${r.created}건 추가 (${r.skipped_invalid}건 필터링)`);
    },
    onError: (e: Error) => alert(`오류: ${e.message}`),
  });

  const [aiHighlight, setAiHighlight] = useState(false);

  // Check for pending suggest on mount
  useEffect(() => {
    const pending = (window as any).__sqlSuggestSynonyms;
    if (pending && pending.length > 0) {
      setAiHighlight(true);
      setTimeout(() => setAiHighlight(false), 5000);
      (window as any).__sqlSuggestSynonyms = null;
    }
  }, []);

  const [synPage, setSynPage] = useState(1);
  const [synPageSize, setSynPageSize] = useState(30);

  const filtered = search
    ? synonyms.filter((s) => s.term.includes(search) || s.target.includes(search) || s.description.includes(search))
    : synonyms;

  const synPaging = useClientPaging(filtered, synPageSize);
  const pagedFiltered = synPaging.slice(synPage);

  useEffect(() => { setSynPage(1); }, [search, synPageSize]);

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <NsSelect ns={ns} setNs={setNs} namespaces={namespaces} />
        {ns && (
          <div className="flex gap-2">
            <Button size="sm" variant="secondary" onClick={() => reindexMut.mutate()} disabled={reindexMut.isPending}>
              <RefreshCw className="w-3.5 h-3.5 mr-1" /> 재인덱싱
            </Button>
            <Button size="sm" onClick={() => { aiGenMut.mutate(undefined); setAiHighlight(false); }} disabled={aiGenMut.isPending}
              className={clsx(
                'bg-violet-700 hover:bg-violet-600 text-white',
                aiHighlight && 'ring-2 ring-violet-400 ring-offset-2 ring-offset-slate-900 animate-pulse',
              )}>
              <Sparkles className="w-3.5 h-3.5 mr-1" /> {aiGenMut.isPending ? 'AI 생성 중...' : 'AI 자동생성'}
            </Button>
          </div>
        )}
      </div>

      {ns && (
        <>
          {/* Inline add form */}
          <div className="bg-slate-800/60 border border-dashed border-slate-600 rounded-xl p-3">
            <div className="grid grid-cols-3 gap-2 mb-2">
              <div>
                <label className="text-[10px] text-slate-500 mb-1 block">용어 (예: 매출)</label>
                <input value={form.term} onChange={(e) => setForm({ ...form, term: e.target.value })}
                  placeholder="예: 매출"
                  className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-1.5 text-sm text-slate-300 placeholder-slate-600 focus:outline-none focus:border-indigo-500" />
              </div>
              <div>
                <label className="text-[10px] text-slate-500 mb-1 block">매핑 (예: SUM(orders.ord_amt))</label>
                <input value={form.target} onChange={(e) => setForm({ ...form, target: e.target.value })}
                  placeholder="예: SUM(orders.ord_amt)"
                  className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-1.5 text-sm text-slate-300 placeholder-slate-600 focus:outline-none focus:border-indigo-500" />
              </div>
              <div>
                <label className="text-[10px] text-slate-500 mb-1 block">설명</label>
                <input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })}
                  placeholder="선택"
                  className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-1.5 text-sm text-slate-300 placeholder-slate-600 focus:outline-none focus:border-indigo-500"
                  onKeyDown={(e) => e.key === 'Enter' && form.term && form.target && addMut.mutate()} />
              </div>
            </div>
            <Button size="sm" onClick={() => addMut.mutate()} disabled={addMut.isPending || !form.term || !form.target}>
              <Plus className="w-3.5 h-3.5 mr-1" /> 추가
            </Button>
          </div>

          {/* Search + count + page size */}
          <div className="flex items-center gap-3">
            <div className="relative flex-1">
              <Search className="absolute left-2.5 top-2 w-3.5 h-3.5 text-slate-500 pointer-events-none" />
              <input value={search} onChange={(e) => setSearch(e.target.value)}
                placeholder="검색..."
                className="pl-8 pr-3 py-1.5 w-full bg-slate-800 border border-slate-600 rounded-lg text-sm text-slate-300 placeholder-slate-500 focus:outline-none focus:border-indigo-500" />
            </div>
          </div>

          <PaginationInfo totalItems={synPaging.totalItems} pageSize={synPageSize} onPageSizeChange={setSynPageSize} />

          {/* List */}
          <div className="rounded-xl border border-slate-700 overflow-hidden">
            {pagedFiltered.length === 0 ? (
              <p className="text-slate-500 text-sm text-center py-8">{search ? '검색 결과가 없습니다.' : '등록된 용어가 없습니다.'}</p>
            ) : (
              <div className="divide-y divide-slate-700/60">
                {pagedFiltered.map((s) => (
                  <div key={s.id} className="flex items-center gap-4 px-4 py-3 hover:bg-slate-800/40 group">
                    <span className="text-sm text-slate-200 font-medium w-24 flex-shrink-0">{s.term}</span>
                    <code className="flex-1 text-xs text-orange-400 font-mono truncate">{s.target}</code>
                    <span className="text-xs text-slate-500 w-40 truncate">{s.description}</span>
                    <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                      <button onClick={() => setEditing(s)} className="px-2 py-1 text-xs rounded text-slate-400 hover:text-slate-200 hover:bg-slate-700">편집</button>
                      <button onClick={() => delMut.mutate(s.id)} className="px-2 py-1 text-xs rounded text-rose-400 hover:text-rose-300 hover:bg-rose-900/20">삭제</button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
          <PaginationNav page={synPage} totalPages={synPaging.totalPages} onPageChange={setSynPage} />
        </>
      )}

      {/* Edit modal */}
      <Modal isOpen={!!editing} onClose={() => setEditing(null)} title="용어 편집">
        {editing && (
          <div className="space-y-3">
            {(['term', 'target', 'description'] as const).map((f) => (
              <div key={f}>
                <label className="text-xs text-slate-400">{f === 'term' ? '용어' : f === 'target' ? '매핑' : '설명'}</label>
                <input value={editing[f]} onChange={(e) => setEditing({ ...editing, [f]: e.target.value })}
                  className="mt-1 w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-300" />
              </div>
            ))}
            <div className="flex gap-2 justify-end">
              <Button variant="secondary" onClick={() => setEditing(null)}>취소</Button>
              <Button onClick={() => {
                if (!editing) return;
                // 삭제 후 재추가로 편집 구현
                delMut.mutateAsync(editing.id).then(() => createSynonym(ns, { term: editing.term, target: editing.target, description: editing.description })).then(() => {
                  qc.invalidateQueries({ queryKey: ['sql_synonyms', ns] });
                  setEditing(null);
                });
              }}>저장</Button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}

// ── FewshotTab ────────────────────────────────────────────────────────────────

type FewshotStatusFilter = 'all' | 'pending' | 'approved' | 'rejected';

const STATUS_LABELS: Record<string, { label: string; color: string }> = {
  approved: { label: '승인됨', color: 'text-emerald-400 bg-emerald-900/30' },
  pending:  { label: '등록 후보', color: 'text-amber-400 bg-amber-900/30' },
  rejected: { label: '반려됨', color: 'text-rose-400 bg-rose-900/30' },
};

function FewshotTab() {
  const { ns, setNs, namespaces } = useNamespace();
  const qc = useQueryClient();
  const [showAdd, setShowAdd] = useState(false);
  const [form, setForm] = useState<Omit<SqlFewshot, 'id' | 'hits'>>({ question: '', sql: '', category: '', status: 'approved' });
  const [expanded, setExpanded] = useState<number | null>(null);
  const [statusFilter, setStatusFilter] = useState<FewshotStatusFilter>('all');

  const { data: fewshots = [] } = useQuery({
    queryKey: ['sql_fewshots', ns, statusFilter],
    queryFn: () => listSqlFewshots(ns, statusFilter),
    enabled: !!ns,
  });

  const addMut = useMutation({
    mutationFn: () => createSqlFewshot(ns, form),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['sql_fewshots', ns] }); setShowAdd(false); setForm({ question: '', sql: '', category: '', status: 'approved' }); },
  });
  const statusMut = useMutation({
    mutationFn: ({ id, status }: { id: number; status: 'approved' | 'pending' | 'rejected' }) => updateSqlFewshotStatus(ns, id, status),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['sql_fewshots', ns] }),
  });
  const delMut = useMutation({ mutationFn: (id: number) => deleteSqlFewshot(ns, id), onSuccess: () => qc.invalidateQueries({ queryKey: ['sql_fewshots', ns] }) });
  const reindexMut = useMutation({ mutationFn: () => reindexFewshots(ns) });
  const aiGenMut = useMutation({
    mutationFn: () => generateFewshotsAI(ns),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ['sql_fewshots', ns] });
      alert(`AI 자동생성 완료: ${r.created}건 추가 (중복 ${r.skipped_duplicates}건 제외)`);
    },
    onError: (e: Error) => alert(`오류: ${e.message}`),
  });

  const [fsPage, setFsPage] = useState(1);
  const [fsPageSize, setFsPageSize] = useState(30);
  const pendingCount = statusFilter === 'all' ? fewshots.filter(f => f.status === 'pending').length : (statusFilter === 'pending' ? fewshots.length : 0);
  const fsPaging = useClientPaging(fewshots, fsPageSize);
  const pagedFewshots = fsPaging.slice(fsPage);

  useEffect(() => { setFsPage(1); }, [statusFilter, fsPageSize]);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <NsSelect ns={ns} setNs={setNs} namespaces={namespaces} />
        {ns && (
          <div className="flex gap-2">
            <Button size="sm" variant="secondary" onClick={() => reindexMut.mutate()} disabled={reindexMut.isPending}>
              <RefreshCw className="w-3.5 h-3.5 mr-1" /> 재인덱싱
            </Button>
            <Button size="sm" onClick={() => aiGenMut.mutate()} disabled={aiGenMut.isPending}
              className="bg-violet-700 hover:bg-violet-600 text-white">
              <Sparkles className="w-3.5 h-3.5 mr-1" /> {aiGenMut.isPending ? 'AI 생성 중...' : 'AI 자동생성'}
            </Button>
            <Button size="sm" onClick={() => setShowAdd(true)}><Plus className="w-3.5 h-3.5 mr-1" /> 추가</Button>
          </div>
        )}
      </div>

      {ns && (
        <>
          {/* Status filter tabs + page size */}
          <div className="flex gap-1 border-b border-slate-700 items-center justify-between">
            <div className="flex gap-1">
              {(['all', 'pending', 'approved', 'rejected'] as FewshotStatusFilter[]).map((s) => (
                <button key={s} onClick={() => setStatusFilter(s)}
                  className={clsx(
                    'px-3 py-2 text-xs font-medium border-b-2 transition-colors whitespace-nowrap flex items-center gap-1.5',
                    statusFilter === s
                      ? 'text-indigo-400 border-indigo-500'
                      : 'text-slate-500 border-transparent hover:text-slate-300',
                  )}>
                  {s === 'all' ? '전체' : STATUS_LABELS[s].label}
                  {s === 'pending' && pendingCount > 0 && (
                    <span className="px-1.5 py-0.5 rounded-full text-[10px] bg-amber-500/20 text-amber-400 font-bold">{pendingCount}</span>
                  )}
                </button>
              ))}
            </div>
          </div>

          <PaginationInfo totalItems={fsPaging.totalItems} pageSize={fsPageSize} onPageSizeChange={setFsPageSize} />

          <div className="rounded-xl border border-slate-700 overflow-hidden divide-y divide-slate-700/60">
            {pagedFewshots.length === 0 && <p className="text-slate-500 text-sm text-center py-8">예제가 없습니다.</p>}
            {pagedFewshots.map((f) => {
              const st = STATUS_LABELS[f.status] ?? STATUS_LABELS.approved;
              const isExpanded = expanded === f.id;
              return (
                <div key={f.id} className="hover:bg-slate-800/30">
                  <div className="flex items-start gap-3 px-4 py-3">
                    {/* 좌: 질문 + SQL 미리보기 */}
                    <div className="flex-1 min-w-0 cursor-pointer" onClick={() => setExpanded(isExpanded ? null : f.id)}>
                      <div className="flex items-center gap-2 flex-wrap mb-1">
                        <span className={clsx('text-[10px] px-1.5 py-0.5 rounded font-medium flex-shrink-0', st.color)}>{st.label}</span>
                        {f.category && <span className="text-[10px] text-slate-500 bg-slate-700 px-1.5 py-0.5 rounded">{f.category}</span>}
                        {f.hits > 0 && <span className="text-[10px] text-amber-500">조회 {f.hits}회</span>}
                      </div>
                      <p className="text-sm text-slate-200 truncate">{f.question}</p>
                      {!isExpanded && (
                        <p className="text-xs text-emerald-600 font-mono truncate mt-0.5">{f.sql}</p>
                      )}
                    </div>
                    {/* 우: 액션 버튼 */}
                    <div className="flex items-center gap-1.5 flex-shrink-0 mt-0.5">
                      {f.status === 'pending' && (
                        <>
                          <button onClick={() => statusMut.mutate({ id: f.id, status: 'approved' })}
                            className="text-emerald-400 hover:text-emerald-300" title="승인">
                            <CheckCircle className="w-4 h-4" />
                          </button>
                          <button onClick={() => statusMut.mutate({ id: f.id, status: 'rejected' })}
                            className="text-rose-400 hover:text-rose-300" title="반려">
                            <XCircle className="w-4 h-4" />
                          </button>
                        </>
                      )}
                      {f.status === 'rejected' && (
                        <button onClick={() => statusMut.mutate({ id: f.id, status: 'approved' })}
                          className="text-emerald-400 hover:text-emerald-300" title="승인으로 변경">
                          <CheckCircle className="w-4 h-4" />
                        </button>
                      )}
                      <button onClick={() => setExpanded(isExpanded ? null : f.id)}
                        className="text-slate-500 hover:text-slate-300" title={isExpanded ? '접기' : '펼쳐서 SQL 보기'}>
                        {isExpanded ? <Minimize2 className="w-3.5 h-3.5" /> : <Maximize2 className="w-3.5 h-3.5" />}
                      </button>
                      <button onClick={() => delMut.mutate(f.id)} className="text-rose-400 hover:text-rose-300">
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>
                  {isExpanded && (
                    <pre className="mx-4 mb-3 text-xs text-emerald-400 bg-slate-900/60 rounded-lg px-3 py-2 overflow-x-auto font-mono">{f.sql}</pre>
                  )}
                </div>
              );
            })}
          </div>
          <PaginationNav page={fsPage} totalPages={fsPaging.totalPages} onPageChange={setFsPage} />
        </>
      )}

      <Modal isOpen={showAdd} onClose={() => setShowAdd(false)} title="SQL 예제 추가">
        <div className="space-y-3">
          <div>
            <label className="text-xs text-slate-400">질문</label>
            <input value={form.question} onChange={(e) => setForm({ ...form, question: e.target.value })}
              className="mt-1 w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-300" />
          </div>
          <div>
            <label className="text-xs text-slate-400">SQL</label>
            <textarea rows={4} value={form.sql} onChange={(e) => setForm({ ...form, sql: e.target.value })}
              className="mt-1 w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-300 font-mono resize-none" />
          </div>
          <div>
            <label className="text-xs text-slate-400">카테고리</label>
            <input value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}
              className="mt-1 w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-300" />
          </div>
          <div className="flex gap-2 justify-end">
            <Button variant="secondary" onClick={() => setShowAdd(false)}>취소</Button>
            <Button onClick={() => addMut.mutate()} disabled={addMut.isPending}>저장</Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}

// ── PipelineTab ───────────────────────────────────────────────────────────────

const STAGE_DETAILS: Record<string, { summary: string; detail: string; llmCost: string; recommendation: string }> = {
  parse: {
    summary: '사용자 질문의 의도, 난이도, 엔티티를 분석합니다.',
    detail: '질문을 JSON으로 파싱하여 intent(simple_select, aggregation, join 등), difficulty(simple/moderate/complex), 언급된 테이블/컬럼 후보, 필터 조건을 추출합니다. 이 정보가 후속 스테이지의 정확도를 결정합니다.',
    llmCost: 'LLM 1회 호출',
    recommendation: '항상 ON 권장. 이 단계 없이는 SQL 생성 품질이 크게 떨어집니다.',
  },
  rag: {
    summary: '벡터 검색으로 관련 스키마/용어/예제를 찾습니다.',
    detail: '질문을 임베딩하여 pgvector에서 유사한 컬럼(스키마), 용어 사전(동의어), SQL 예제(few-shot)를 검색합니다. is_selected=TRUE인 테이블만 대상이며, 검색 결과는 SQL 생성 프롬프트에 컨텍스트로 전달됩니다.',
    llmCost: 'LLM 호출 없음 (벡터 검색만)',
    recommendation: '항상 ON 권장. 비용 0이면서 정확도에 핵심적입니다.',
  },
  schema_link: {
    summary: 'LLM이 질문에 필요한 테이블을 사전 선별합니다.',
    detail: 'RAG 결과와 전체 스키마를 LLM에 전달하여, 이 질문에 실제로 필요한 테이블/컬럼만 선별합니다. 테이블이 수백 개일 때 generate 스테이지에 전달되는 컨텍스트를 줄여 정확도를 높입니다.',
    llmCost: 'LLM 1회 호출',
    recommendation: '테이블 50개 이상일 때 ON 추천. 적은 테이블에서는 RAG만으로 충분합니다.',
  },
  schema_explore: {
    summary: '실제 DB에서 샘플 데이터를 조회합니다.',
    detail: '선별된 테이블의 주요 컬럼에 대해 SELECT DISTINCT col LIMIT 5 등을 실행하여 실제 데이터 값을 확인합니다. "VVIP 고객"이 grade_code=\'VVIP\'인지 grade=\'최우수\'인지 등 실제 값을 알아야 정확한 WHERE 조건을 생성할 수 있습니다.',
    llmCost: 'LLM 호출 없음 (DB 조회만)',
    recommendation: '코드값/상태값이 많은 DB에서 ON 추천. 대상 DB 읽기 권한 필요.',
  },
  generate: {
    summary: 'LLM이 SQL 쿼리를 생성합니다.',
    detail: 'parse 결과(의도/엔티티) + RAG 결과(스키마/용어/예제) + 관계 정보를 프롬프트에 조합하여 LLM이 SQL을 생성합니다. 프롬프트는 [시스템 설정 → 프롬프트 관리]에서 sql2_generate / sql2_generate_system으로 커스터마이징 가능합니다.',
    llmCost: 'LLM 1회 호출',
    recommendation: '항상 ON 권장. SQL 생성의 핵심 스테이지입니다.',
  },
  candidates: {
    summary: '복수의 SQL 후보를 생성하고 최적을 선택합니다.',
    detail: 'generate를 2~3회 반복하여 서로 다른 SQL 후보를 만들고, LLM이 각 후보를 평가하여 가장 정확한 것을 선택합니다. 복잡한 JOIN이나 서브쿼리에서 한 번에 정확한 SQL이 나오기 어려울 때 효과적입니다.',
    llmCost: 'LLM 3~4회 호출 (후보 생성 + 평가)',
    recommendation: '비용이 높아 정확도가 매우 중요한 경우에만 ON. 일반적으로는 OFF.',
  },
  validate: {
    summary: 'AST 파싱으로 SQL 문법과 안전성을 검증합니다.',
    detail: 'SQL을 AST(Abstract Syntax Tree)로 파싱하여 문법 오류, DROP/DELETE 등 위험 구문, 존재하지 않는 테이블/컬럼 참조를 검증합니다. LLM 호출 없이 코드 기반으로 동작합니다.',
    llmCost: 'LLM 호출 없음 (코드 검증만)',
    recommendation: 'ON 권장. 비용 0이면서 잘못된 SQL 실행을 방지합니다.',
  },
  fix: {
    summary: '검증 실패 시 LLM이 SQL을 자동 수정합니다.',
    detail: 'validate에서 오류가 발견되면 오류 메시지와 원본 SQL을 LLM에 전달하여 수정된 SQL을 받습니다. validate가 OFF이면 이 스테이지도 실행되지 않습니다.',
    llmCost: '조건부 LLM 1회 (검증 실패 시에만)',
    recommendation: 'validate와 함께 ON 권장. 검증 통과 시 비용 0입니다.',
  },
  execute: {
    summary: '대상 DB에 SQL을 실행하고 결과를 가져옵니다.',
    detail: '생성된 SQL을 대상 DB에 실행하여 결과를 가져옵니다. SELECT만 허용되며, 타임아웃(기본 30초)과 최대 행 수(1000행) 제한이 있습니다. OFF로 설정하면 SQL만 생성하고 실행하지 않습니다.',
    llmCost: 'LLM 호출 없음 (DB 실행만)',
    recommendation: '대상 DB 읽기 권한이 있을 때 ON. 권한 없으면 OFF로 SQL만 확인 가능.',
  },
  summarize: {
    summary: 'LLM이 쿼리 결과를 자연어로 요약합니다.',
    detail: '실행 결과(테이블 데이터)를 LLM에 전달하여 자연어 요약과 적합한 차트 유형(bar, line, pie 등)을 추천받습니다. execute가 OFF이면 이 스테이지도 실행되지 않습니다.',
    llmCost: 'LLM 1회 호출',
    recommendation: '사용자 경험 향상에 효과적. execute가 ON일 때 함께 사용 추천.',
  },
};

function PipelineTab() {
  const qc = useQueryClient();
  const { data: stages = [], isLoading } = useQuery({ queryKey: ['sql_pipeline_stages'], queryFn: listPipelineStages });
  const toggleMut = useMutation({
    mutationFn: (s: SqlPipelineStage) => togglePipelineStage(s.id, !s.is_enabled),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['sql_pipeline_stages'] }),
  });
  const [detailStage, setDetailStage] = useState<SqlPipelineStage | null>(null);

  if (isLoading) return <p className="text-slate-400 text-sm">로딩 중...</p>;

  const detailInfo = detailStage ? STAGE_DETAILS[detailStage.id] : null;

  return (
    <div className="space-y-3">
      <p className="text-xs text-slate-500">파이프라인 프롬프트는 [시스템 설정 → 프롬프트 관리]에서 편집하세요. 카드를 클릭하면 상세 설명을 볼 수 있습니다.</p>
      {stages.map((s) => {
        const isUnimplemented = (s.description || '').startsWith('[미구현]');
        return (
          <div key={s.id}
            onClick={() => setDetailStage(s)}
            className={clsx('border rounded-xl p-4 cursor-pointer transition-colors', isUnimplemented ? 'bg-slate-800/50 border-dashed border-slate-700/50 hover:bg-slate-800/70' : 'bg-slate-800 border-slate-700 hover:bg-slate-750 hover:border-slate-600')}>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <span className="w-6 h-6 flex items-center justify-center text-xs bg-slate-700 rounded font-mono text-slate-400">{s.order_num}</span>
                <div>
                  <span className={clsx('text-sm font-medium', isUnimplemented ? 'text-slate-400' : 'text-slate-200')}>{s.name}</span>
                  <span className="ml-2 text-xs text-slate-500 font-mono">{s.id}</span>
                </div>
                {s.is_required && <span className="text-xs text-amber-500">필수</span>}
                {isUnimplemented && <span className="text-[10px] text-slate-500 bg-slate-700/60 px-1.5 py-0.5 rounded">미구현</span>}
              </div>
              <button onClick={(e) => { e.stopPropagation(); !s.is_required && !isUnimplemented && toggleMut.mutate(s); }} disabled={s.is_required || isUnimplemented}
                className={clsx('w-10 h-5 rounded-full transition-colors relative', s.is_enabled ? 'bg-emerald-600' : 'bg-slate-600', (s.is_required || isUnimplemented) && 'opacity-50 cursor-not-allowed')}>
                <span className={clsx('absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform', s.is_enabled ? 'translate-x-5' : 'translate-x-0.5')} />
              </button>
            </div>
            <p className="text-xs text-slate-500 mt-1 ml-9">{isUnimplemented ? (s.description || '').replace('[미구현] ', '') : s.description}</p>
          </div>
        );
      })}

      {/* Stage Detail Modal */}
      <Modal isOpen={!!detailStage} onClose={() => setDetailStage(null)} title={detailStage ? `${detailStage.order_num}. ${detailStage.name}` : ''}>
        {detailStage && detailInfo && (
          <div className="space-y-4">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-xs font-mono text-slate-500 bg-slate-800 px-2 py-1 rounded">{detailStage.id}</span>
              {detailStage.is_required && <span className="text-xs text-amber-500 bg-amber-900/20 px-2 py-1 rounded">필수</span>}
              {(detailStage.description || '').startsWith('[미구현]') && <span className="text-xs text-slate-500 bg-slate-700 px-2 py-1 rounded">미구현</span>}
              <span className={clsx('text-xs px-2 py-1 rounded', detailStage.is_enabled ? 'text-emerald-400 bg-emerald-900/20' : 'text-slate-500 bg-slate-700')}>
                {detailStage.is_enabled ? 'ON' : 'OFF'}
              </span>
            </div>

            <div>
              <h4 className="text-xs font-semibold text-slate-400 mb-1">요약</h4>
              <p className="text-sm text-slate-200">{detailInfo.summary}</p>
            </div>

            <div>
              <h4 className="text-xs font-semibold text-slate-400 mb-1">상세 동작</h4>
              <p className="text-sm text-slate-300 leading-relaxed">{detailInfo.detail}</p>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="bg-slate-800 rounded-lg px-3 py-2">
                <h4 className="text-[10px] font-semibold text-slate-500 mb-1">비용</h4>
                <p className="text-xs text-slate-300">{detailInfo.llmCost}</p>
              </div>
              <div className="bg-slate-800 rounded-lg px-3 py-2">
                <h4 className="text-[10px] font-semibold text-slate-500 mb-1">추천</h4>
                <p className="text-xs text-slate-300">{detailInfo.recommendation}</p>
              </div>
            </div>

            <div className="flex justify-end">
              <Button variant="secondary" onClick={() => setDetailStage(null)}>닫기</Button>
            </div>
          </div>
        )}
        {detailStage && !detailInfo && (
          <div className="text-sm text-slate-400 py-4 text-center">상세 설명이 준비되지 않았습니다.</div>
        )}
      </Modal>
    </div>
  );
}

// ── AuditLogTab ───────────────────────────────────────────────────────────────

function AuditLogTab() {
  const { ns, setNs, namespaces } = useNamespace();
  const [page, setPage] = useState(1);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const { data, isLoading } = useQuery({ queryKey: ['sql_audit_logs', ns, page], queryFn: () => listAuditLogs(ns, page, 50), enabled: !!ns });
  const statusColor = (s: string) => ({ success: 'text-emerald-400 bg-emerald-900/20', error: 'text-rose-400 bg-rose-900/20', blocked: 'text-amber-400 bg-amber-900/20' }[s] ?? 'text-slate-400 bg-slate-700/20');

  return (
    <div className="space-y-4">
      <NsSelect ns={ns} setNs={setNs} namespaces={namespaces} />
      {ns && (
        <>
          {isLoading ? <p className="text-slate-400 text-sm">로딩 중...</p> : (
            <div className="rounded-xl border border-slate-700 overflow-hidden">
              <div className="divide-y divide-slate-700/60">
                {(data?.items ?? []).length === 0 && <p className="text-slate-500 text-sm text-center py-8">감사 로그가 없습니다.</p>}
                {(data?.items ?? []).map((log: SqlAuditLog) => (
                  <div key={log.id} className="hover:bg-slate-800/30">
                    <div className="flex items-center gap-3 px-4 py-3 cursor-pointer" onClick={() => setExpandedId(expandedId === log.id ? null : log.id)}>
                      <span className="text-xs text-slate-500 whitespace-nowrap w-28 flex-shrink-0">
                        {new Date(log.created_at).toLocaleString('ko-KR', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}
                      </span>
                      <span className="flex-1 text-sm text-slate-300 truncate">{log.question}</span>
                      <span className={clsx('text-xs px-2 py-0.5 rounded-full font-medium flex-shrink-0', statusColor(log.status))}>{log.status}</span>
                      <span className="text-xs text-slate-500 flex-shrink-0">{log.duration_ms}ms</span>
                      {log.cached && <CheckCircle className="w-3.5 h-3.5 text-amber-400 flex-shrink-0" />}
                    </div>
                    {expandedId === log.id && log.sql && (
                      <pre className="mx-4 mb-3 text-xs text-emerald-400 bg-slate-900/60 rounded-lg px-3 py-2 overflow-x-auto font-mono">{log.sql}</pre>
                    )}
                    {expandedId === log.id && log.error && (
                      <p className="mx-4 mb-3 text-xs text-rose-400 bg-rose-900/20 rounded-lg px-3 py-2">{log.error}</p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
          {(data?.total ?? 0) > 50 && (
            <div className="flex gap-2 justify-center">
              <Button size="sm" variant="secondary" onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page === 1}>이전</Button>
              <span className="text-xs text-slate-400 self-center">{page} / {Math.ceil((data?.total ?? 0) / 50)}</span>
              <Button size="sm" variant="secondary" onClick={() => setPage((p) => p + 1)} disabled={page * 50 >= (data?.total ?? 0)}>다음</Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ── CacheTab ──────────────────────────────────────────────────────────────────

function CacheTab() {
  const { ns, setNs, namespaces } = useNamespace();
  const qc = useQueryClient();
  const { data: entries = [], isLoading } = useQuery({ queryKey: ['sql_cache', ns], queryFn: () => listSqlCache(ns), enabled: !!ns });
  const delMut = useMutation({ mutationFn: (id: number) => deleteSqlCacheEntry(ns, id), onSuccess: () => qc.invalidateQueries({ queryKey: ['sql_cache', ns] }) });
  const clearMut = useMutation({ mutationFn: () => clearSqlCache(ns), onSuccess: () => qc.invalidateQueries({ queryKey: ['sql_cache', ns] }) });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <NsSelect ns={ns} setNs={setNs} namespaces={namespaces} />
        {ns && entries.length > 0 && (
          <Button size="sm" variant="secondary" onClick={() => { if (confirm('전체 캐시를 삭제하시겠습니까?')) clearMut.mutate(); }}>
            <X className="w-3.5 h-3.5 mr-1" /> 전체 삭제
          </Button>
        )}
      </div>
      {ns && (
        <>
          {isLoading ? <p className="text-slate-400 text-sm">로딩 중...</p> : (
            <div className="rounded-xl border border-slate-700 overflow-hidden divide-y divide-slate-700/60">
              {entries.length === 0 && <p className="text-slate-500 text-sm text-center py-8">캐시가 없습니다.</p>}
              {entries.map((e: SqlCacheEntry) => (
                <div key={e.id} className="flex items-start gap-3 px-4 py-3 hover:bg-slate-800/30 group">
                  <div className="flex-1 min-w-0 space-y-1">
                    <p className="text-sm text-slate-300 truncate">{e.question}</p>
                    <p className="text-xs text-emerald-400 font-mono truncate">{e.sql}</p>
                    <div className="flex gap-3 text-xs text-slate-500">
                      <span>조회 {e.hits}회</span>
                      <span>{new Date(e.created_at).toLocaleDateString('ko-KR')}</span>
                      {e.expires_at && <span>만료 {new Date(e.expires_at).toLocaleDateString('ko-KR')}</span>}
                    </div>
                  </div>
                  <button onClick={() => delMut.mutate(e.id)} className="text-rose-400 hover:text-rose-300 opacity-0 group-hover:opacity-100 transition-opacity">
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ── Named exports (for Admin.tsx main tabs) ───────────────────────────────────

export { TargetDbTab as SqlTargetDbTab };
export { SchemaTab as SqlSchemaTab };
export { ErdTab as SqlErdTab };
export { SynonymTab as SqlSynonymTab };
export { FewshotTab as SqlFewshotTab };
export { PipelineTab as SqlPipelineTab };
export { AuditLogTab as SqlAuditLogTab };
export { CacheTab as SqlCacheTab };

// Keep Text2SqlAdmin as default fallback (unused after refactor)
export function Text2SqlAdmin() {
  return <TargetDbTab />;
}
