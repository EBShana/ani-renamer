import type {
  AiApplyItem,
  AiApplyResponse,
  AiIdentifyResponse,
  AiMatchResponse,
  AiSeasonSuggestion,
  AiSpecialResponse,
  AiStatus,
  AliasRecord,
  AutoMatchResponse,
  BrowseResult,
  ConfigSchema,
  ConflictAction,
  ConflictView,
  DirectoryGroup,
  ExecuteResponse,
  FieldPayload,
  GroupCheck,
  IdentifyOverview,
  Kind,
  MatchApplyResult,
  MatchCandidate,
  MatchGroup,
  MatchSearchResponse,
  OperationProgress,
  OperationRecord,
  PatternApplyResult,
  PatternCacheEntry,
  PatternCacheResponse,
  PatternDecisionRecord,
  PatternDecisionValue,
  PatternProposeResult,
  PatternStructure,
  Preset,
  PresetInput,
  PresetSettingsDiff,
  PreviewResponse,
  ProviderTestResult,
  QueueStage,
  QueueView,
  RescanCheck,
  ResolveResponse,
  ResumeResponse,
  RuleInfo,
  ScanPayload,
  ScanRecord,
  SeasonPlanResponse,
  SpecialMemory,
  RuleInput,
  TemplateSpec,
  UndoResponse,
} from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body && typeof body.detail === "string") detail = body.detail;
    } catch {
      // 保持原始错误信息
    }
    if (response.status === 401 && !path.startsWith("/auth/")) {
      // 登录失效：通知 App 回到登录页
      window.dispatchEvent(new Event("ani-unauthorized"));
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

export const api = {
  scan: (groupIds: string[]) =>
    request<ScanPayload>("/scans", {
      method: "POST",
      body: JSON.stringify({ group_ids: groupIds }),
    }),

  groups: () => request<{ groups: DirectoryGroup[]; media_root: string }>("/groups"),

  checkGroups: () => request<GroupCheck>("/groups/check"),

  createGroup: (payload: { name: string; source: string; target: string }) =>
    request<DirectoryGroup>("/groups", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  updateGroup: (id: string, payload: { name: string; source: string; target: string }) =>
    request<DirectoryGroup>(`/groups/${id}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),

  deleteGroup: (id: string) =>
    request<{ ok: boolean }>(`/groups/${id}`, { method: "DELETE" }),

  samples: () => request<ScanPayload>("/samples"),

  getScan: (scanId: string) => request<ScanPayload>(`/scans/${scanId}`),

  scans: () => request<{ scans: ScanRecord[] }>("/scans"),

  deleteScan: (scanId: string) =>
    request<{ ok: boolean }>(`/scans/${scanId}`, { method: "DELETE" }),

  saveSelection: (scanId: string, stage: string, ids: string[]) =>
    request<{ stage: string; count: number }>(`/scans/${scanId}/selections`, {
      method: "PUT",
      body: JSON.stringify({ stage, ids }),
    }),

  recheck: (scanId: string) =>
    request<{ check: RescanCheck; queue: QueueView }>(`/scans/${scanId}/recheck`, {
      method: "POST",
      body: JSON.stringify({}),
    }),

  rescan: (scanId: string, groupIds: string[] = []) =>
    request<ScanPayload>(`/scans/${scanId}/rescan`, {
      method: "POST",
      body: JSON.stringify({ group_ids: groupIds }),
    }),

  matchGroups: (scanId: string) =>
    request<{ groups: MatchGroup[]; total: number; matched: number }>("/match/groups", {
      method: "POST",
      body: JSON.stringify({ scan_id: scanId }),
    }),

  identify: (scanId: string) =>
    request<IdentifyOverview>("/identify", {
      method: "POST",
      body: JSON.stringify({ scan_id: scanId }),
    }),

  identifyAssign: (payload: {
    scan_id: string;
    file_ids: string[];
    work_id?: string;
    title?: string;
    year?: number | null;
    remember?: boolean;
    remember_alias?: boolean;
    group_id?: string;
    segment?: string;
  }) =>
    request<IdentifyOverview>("/identify/assign", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  identifyIgnore: (payload: { scan_id: string; group_id: string; segment: string }) =>
    request<IdentifyOverview>("/identify/ignore", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  identifyConfirm: (payload: { scan_id: string; work_id: string }) =>
    request<IdentifyOverview>("/identify/confirm", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  deleteAssignment: (assignmentId: string) =>
    request<{ assignments: IdentifyOverview["assignments"] }>(
      `/identify/assignments/${assignmentId}`,
      { method: "DELETE" },
    ),

  aliases: () => request<{ aliases: AliasRecord[] }>("/identify/aliases"),

  createAlias: (payload: { alias: string; title: string; year?: number | null }) =>
    request<{ alias: AliasRecord; aliases: AliasRecord[] }>("/identify/aliases", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  updateAlias: (
    aliasId: string,
    payload: { alias: string; title: string; year?: number | null },
  ) =>
    request<{ alias: AliasRecord; aliases: AliasRecord[] }>(`/identify/aliases/${aliasId}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),

  deleteAlias: (aliasId: string) =>
    request<{ aliases: AliasRecord[] }>(`/identify/aliases/${aliasId}`, {
      method: "DELETE",
    }),

  specialMemory: () => request<{ special_memory: SpecialMemory[] }>("/identify/special-memory"),

  deleteSpecialMemory: (memoryId: string) =>
    request<{ special_memory: SpecialMemory[] }>(`/identify/special-memory/${memoryId}`, {
      method: "DELETE",
    }),

  identifySpecial: (payload: {
    scan_id: string;
    folder_key: string;
    action: "assign" | "keep";
    work_id?: string;
  }) =>
    request<IdentifyOverview>("/identify/special", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  duplicateDecision: (payload: {
    scan_id: string;
    key: string;
    action: "exclude" | "keep" | "keep_all" | "replace" | "reset";
    file_ids?: string[];
    target_file_id?: string;
  }) =>
    request<{ decision: { action: string; exclude: string[] } }>("/identify/duplicates", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  identifyCollection: (payload: {
    scan_id: string;
    file_id: string;
    action: "keep" | "season" | "clear";
    season?: number;
  }) =>
    request<IdentifyOverview>("/identify/collection", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  authStatus: () =>
    request<{ enabled: boolean; authenticated: boolean }>("/auth/status"),

  authLogin: (password: string) =>
    request<{ ok: boolean; enabled: boolean }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ password }),
    }),

  authLogout: () => request<{ ok: boolean }>("/auth/logout", { method: "POST" }),

  authSetPassword: (password: string | null) =>
    request<{ enabled: boolean }>("/auth/password", {
      method: "POST",
      body: JSON.stringify({ password }),
    }),

  aiStatus: () => request<AiStatus>("/ai/status"),

  aiIdentifySuggest: (payload: { scan_id: string; group_id?: string; limit?: number }) =>
    request<AiIdentifyResponse>("/ai/identify/suggest", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  aiIdentifyApply: (payload: {
    scan_id: string;
    items: AiApplyItem[];
    remember_alias?: boolean;
  }) =>
    request<AiApplyResponse>("/ai/identify/apply", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  aiMatchSuggest: (payload: { scan_id: string; limit?: number; include_matched?: boolean }) =>
    request<AiMatchResponse>("/ai/match/suggest", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  aiSeasonSuggest: (payload: {
    scan_id: string;
    work_id: string;
    title?: string;
    year?: number | null;
    seasons: { season_number: number; name?: string; episode_count?: number }[];
  }) =>
    request<AiSeasonSuggestion>("/ai/season/suggest", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  aiSpecialSuggest: (payload: { scan_id: string; limit?: number }) =>
    request<AiSpecialResponse>("/ai/special/suggest", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  aiPatternPropose: (payload: {
    scan_id: string;
    group_id: string;
    dir_rel: string;
    preset_id?: string;
  }) =>
    request<PatternProposeResult>("/ai/patterns/propose", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  aiPatternApply: (payload: {
    scan_id: string;
    group_id: string;
    dir_rel: string;
    structure: PatternStructure;
    source?: "ai" | "manual";
    preset_id?: string;
  }) =>
    request<PatternApplyResult>("/ai/patterns/apply", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  aiPatternDecision: (payload: {
    group_id: string;
    dir_rel: string;
    decision: PatternDecisionValue;
    note?: string;
    structure?: PatternStructure;
  }) =>
    request<PatternDecisionRecord>("/ai/patterns/decision", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  aiPatterns: () => request<PatternCacheResponse>("/ai/patterns"),

  toggleAiPattern: (patternId: string, enabled: boolean) =>
    request<PatternCacheEntry>(`/ai/patterns/${patternId}`, {
      method: "PATCH",
      body: JSON.stringify({ enabled }),
    }),

  deleteAiPattern: (patternId: string) =>
    request<{ deleted: string; total: number }>(`/ai/patterns/${patternId}`, {
      method: "DELETE",
    }),

  clearAiPatterns: () =>
    request<{ cleared: number }>("/ai/patterns/clear", { method: "POST" }),

  identifyConfirmAll: (scanId: string) =>
    request<IdentifyOverview & { confirmed: number }>("/identify/confirm-all", {
      method: "POST",
      body: JSON.stringify({ scan_id: scanId }),
    }),

  matchSearch: (payload: {
    scan_id?: string;
    group_id?: string;
    query?: string;
    source?: "tmdb" | "bangumi";
  }) =>
    request<MatchSearchResponse>("/match/search", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  matchApply: (payload: {
    scan_id: string;
    group_id: string;
    tmdb_id: number;
    media_type: "tv" | "movie";
  }) =>
    request<MatchApplyResult>("/match/apply", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  matchRename: (payload: { scan_id: string; group_id: string; name: string }) =>
    request<{ updated: number; name: string; groups: MatchGroup[] }>("/match/rename", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  matchSeasonPlan: (scan_id: string) =>
    request<SeasonPlanResponse>("/match/season-plan", {
      method: "POST",
      body: JSON.stringify({ scan_id }),
    }),

  matchSeason: (payload: { scan_id: string; group_id: string; season: number }) =>
    request<{ season: number; updated: number; missing: string[]; groups: MatchGroup[] }>(
      "/match/season",
      { method: "POST", body: JSON.stringify(payload) },
    ),

  matchAuto: (payload: { scan_id: string; include_matched?: boolean; limit?: number }) =>
    request<AutoMatchResponse>("/match/auto", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  presets: () => request<{ presets: Preset[] }>("/presets"),

  createPreset: (payload: PresetInput) =>
    request<Preset>("/presets", { method: "POST", body: JSON.stringify(payload) }),

  updatePreset: (id: string, payload: PresetInput) =>
    request<Preset>(`/presets/${id}`, { method: "PUT", body: JSON.stringify(payload) }),

  deletePreset: (id: string) =>
    request<{ ok: boolean }>(`/presets/${id}`, { method: "DELETE" }),

  duplicatePreset: (id: string) =>
    request<Preset>(`/presets/${id}/duplicate`, { method: "POST" }),

  setDefaultPreset: (id: string) =>
    request<Preset>(`/presets/${id}/default`, { method: "POST" }),

  presetSettingsDiff: (id: string) =>
    request<PresetSettingsDiff>(`/presets/${id}/settings-diff`),

  applyPresetSettings: (id: string) =>
    request<{ preset_id: string; values: Record<string, unknown> }>(
      `/presets/${id}/apply-settings`,
      { method: "POST" },
    ),

  exportPresets: () => request<Record<string, unknown>>("/presets/export"),

  importPresets: (payload: Record<string, unknown>) =>
    request<{ created: number; presets: Preset[] }>("/presets/import", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  queue: (scanId: string) =>
    request<QueueView>("/queue", { method: "POST", body: JSON.stringify({ scan_id: scanId }) }),

  queueMove: (scanId: string, entryIds: string[], toStage: QueueStage) =>
    request<QueueView>("/queue/move", {
      method: "POST",
      body: JSON.stringify({ scan_id: scanId, entry_ids: entryIds, to_stage: toStage }),
    }),

  preview: (payload: {
    scan_id?: string;
    preset_id?: string;
    templates?: Partial<Record<Kind, TemplateSpec>>;
    file_ids?: string[];
    folder_ids?: string[];
    subtitle_mode?: "follow" | "keep";
    limit?: number;
  }) => request<PreviewResponse>("/preview", { method: "POST", body: JSON.stringify(payload) }),

  execute: (payload: {
    scan_id?: string;
    preset_id?: string;
    templates?: Partial<Record<Kind, TemplateSpec>>;
    file_ids?: string[];
    folder_ids?: string[];
    subtitle_mode?: "follow" | "keep";
    limit?: number;
    dry_run?: boolean;
    conflict_policy?: "skip" | "overwrite" | "suffix";
    keep_empty_dirs?: boolean;
    confirm_risks?: boolean;
  }) => request<ExecuteResponse>("/execute", { method: "POST", body: JSON.stringify(payload) }),

  history: (limit = 20) =>
    request<{ operations: OperationRecord[]; active_operation_id?: string | null }>(
      `/history?limit=${limit}`,
    ),

  progress: (operationId: string) =>
    request<OperationProgress>(`/history/${operationId}/progress`),

  pause: (operationId: string) =>
    request<{ operation_id: string; status: string; message?: string }>(
      `/history/${operationId}/pause`,
      { method: "POST" },
    ),

  continueRun: (operationId: string) =>
    request<{ operation_id: string; status: string }>(
      `/history/${operationId}/continue`,
      { method: "POST" },
    ),

  cancel: (operationId: string) =>
    request<{ operation_id: string; status: string; message?: string }>(
      `/history/${operationId}/cancel`,
      { method: "POST" },
    ),

  undo: (operationId: string) =>
    request<UndoResponse>(`/history/${operationId}/undo`, {
      method: "POST",
      body: JSON.stringify({}),
    }),

  resume: (operationId: string) =>
    request<ResumeResponse>(`/history/${operationId}/resume`, {
      method: "POST",
      body: JSON.stringify({}),
    }),

  conflicts: (operationId: string) =>
    request<ConflictView>(`/history/${operationId}/conflicts`),

  resolve: (
    operationId: string,
    choices: { seq: number; action: ConflictAction }[],
    confirmOverwrite = false,
  ) =>
    request<ResolveResponse>(`/history/${operationId}/resolve`, {
      method: "POST",
      body: JSON.stringify({ choices, confirm_overwrite: confirmOverwrite }),
    }),

  validate: (pattern: string, output_mode: string) =>
    request<{ errors: string[]; warnings: string[] }>("/templates/validate", {
      method: "POST",
      body: JSON.stringify({ pattern, output_mode }),
    }),

  fields: () => request<FieldPayload>("/fields"),

  config: () => request<{ values: Record<string, unknown>; media_root: string }>("/config"),

  configSchema: () => request<ConfigSchema>("/config/schema"),

  saveConfig: (values: Record<string, unknown>) =>
    request<{ values: Record<string, unknown> }>("/config", {
      method: "PUT",
      body: JSON.stringify({ values }),
    }),

  providerTest: (payload: { proxy_url?: string; tmdb_api_key?: string }) =>
    request<ProviderTestResult>("/providers/test", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  rules: () => request<{ rules: RuleInfo[]; note?: string }>("/rules"),

  createRule: (payload: RuleInput) =>
    request<{ rule: RuleInfo }>("/rules", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  updateRule: (id: string, payload: RuleInput) =>
    request<{ rule: RuleInfo }>(`/rules/${id}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),

  deleteRule: (id: string) =>
    request<{ ok: boolean }>(`/rules/${id}`, { method: "DELETE" }),

  resetRules: () =>
    request<{ rules: RuleInfo[] }>("/rules/reset", { method: "POST" }),

  browse: (path?: string) =>
    request<BrowseResult>(`/browse${path ? `?path=${encodeURIComponent(path)}` : ""}`),
};
