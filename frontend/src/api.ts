import type {
  AutoMatchResponse,
  BrowseResult,
  ConfigSchema,
  DirectoryGroup,
  ExecuteResponse,
  FieldPayload,
  GroupCheck,
  IdentifyOverview,
  Kind,
  MatchApplyResult,
  MatchCandidate,
  MatchGroup,
  OperationProgress,
  OperationRecord,
  Preset,
  PresetInput,
  PresetSettingsDiff,
  PreviewResponse,
  ProviderTestResult,
  ResumeResponse,
  RuleInfo,
  ScanPayload,
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

  matchSearch: (payload: {
    scan_id?: string;
    group_id?: string;
    query?: string;
    source?: "tmdb" | "bangumi";
  }) =>
    request<{ query: string; source: string; candidates: MatchCandidate[] }>("/match/search", {
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

  preview: (payload: {
    scan_id?: string;
    preset_id?: string;
    templates?: Partial<Record<Kind, TemplateSpec>>;
    file_ids?: string[];
    subtitle_mode?: "follow" | "keep";
    limit?: number;
  }) => request<PreviewResponse>("/preview", { method: "POST", body: JSON.stringify(payload) }),

  execute: (payload: {
    scan_id?: string;
    preset_id?: string;
    templates?: Partial<Record<Kind, TemplateSpec>>;
    file_ids?: string[];
    subtitle_mode?: "follow" | "keep";
    limit?: number;
    dry_run?: boolean;
    conflict_policy?: "skip" | "overwrite" | "suffix";
    keep_empty_dirs?: boolean;
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

  rules: () => request<{ rules: RuleInfo[] }>("/rules"),

  browse: (path?: string) =>
    request<BrowseResult>(`/browse${path ? `?path=${encodeURIComponent(path)}` : ""}`),
};
