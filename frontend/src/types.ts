export type Kind = "episode" | "special" | "movie";

export type Category = "video" | "subtitle" | "audio" | "image" | "archive" | "other" | "folder";

export const CATEGORY_LABELS: Record<Category, string> = {
  video: "视频",
  subtitle: "字幕",
  audio: "音频",
  image: "图片",
  archive: "压缩包",
  other: "其他",
  folder: "文件夹",
};

export interface IgnoredDir {
  group_id?: string;
  path: string;
  reason: "special" | "marker" | "ova";
}

export interface DirectoryGroup {
  id: string;
  name: string;
  source: string;
  target: string;
  created_at?: string;
  updated_at?: string;
}

export interface GroupCheck {
  groups: DirectoryGroup[];
  errors: string[];
  media_root: string;
  locked?: boolean;
}

export interface IdentifyCandidate {
  title: string;
  source: string;
  weight: number;
}

export interface FileIdentify {
  work_id?: string;
  title?: string;
  year?: number | null;
  confidence?: "high" | "medium" | "low" | "none" | string;
  source?: string;
  segment?: string;
  needs_confirm?: boolean;
  ignored?: boolean;
  candidates?: IdentifyCandidate[];
  reason?: string;
}

export interface ParsedFile {
  id: string;
  path: string;
  rel_path: string;
  group_id?: string;
  filename: string;
  parent: string;
  size: number;
  kind: Kind | "unknown" | "asset";
  category: Category;
  title: string | null;
  year: number | null;
  season: number | null;
  episode: number | null;
  special_type: string | null;
  special_no: number | null;
  group: string;
  resolution: string;
  codec: string;
  source: string;
  status: "ok" | "warning";
  warnings: string[];
  collection?: boolean;
  collection_range?: string;
  fields: Record<string, string | number>;
  identify?: FileIdentify;
  /** 命名规律分析留下的候选信息；只用于展示，不参与现有识别 */
  pattern_candidate?: {
    source?: string;
    source_label?: string;
    auto_applied?: boolean;
    confirmed?: boolean;
    values?: Record<string, unknown>;
  } | null;
}

export interface ScanPayload {
  scan_id: string;
  root: string;
  restored?: boolean;
  restored_at?: string;
  created_at?: string;
  selections?: Record<string, string[]>;
  groups?: DirectoryGroup[];
  merges?: {
    id: string;
    primary: string;
    primary_title?: string;
    members?: { group_id: string }[];
  }[];
  sources: string[];
  files: ParsedFile[];
  stats: {
    total: number;
    video: number;
    subtitle: number;
    audio: number;
    image: number;
    archive: number;
    other: number;
    episode: number;
    special: number;
    movie: number;
    unknown: number;
    collection: number;
  };
  truncated: boolean;
  max_files?: number;
  ignored_dirs?: IgnoredDir[];
  markers_written?: number;
  is_sample?: boolean;
  last_check?: RescanCheck | null;
}

export interface TemplateSpec {
  pattern: string;
  output_mode: "filename" | "path";
  /** 旧版“完整路径模板”拆出来的目录部分，界面只编辑文件名 */
  legacy_dir?: string;
}

/** 命名预设：3 条模板 + 13 个命名/落点选项。 */
export interface NamingPresetOptions {
  subtitle_mode: "follow" | "keep";
  auto_append_ext: boolean;
  max_filename_bytes: number;
  forbidden_chars: string;
  specials_dir: string;
  preserve_structure: boolean;
  movie_layout: "folder" | "special";
  special_folder_suffix: string;
  default_season: number;
  special_season_zero: boolean;
  ova_policy: "move" | "ignore";
  special_video_policy: "root" | "season";
  special_folder_conflict: "season" | "merge";
}

/** 工作流预设：其余识别与扫描、AI/提供方、执行、刮削选项。 */
export interface WorkflowPresetOptions {
  proxy_url: string;
  ai_enabled: boolean;
  ai_base_url: string;
  ai_model: string;
  ai_batch_limit: number;
  ai_special_enabled: boolean;
  ai_pattern_cache_auto_apply: boolean;
  scan_all_files: boolean;
  ignore_special_dirs: boolean;
  special_dir_names: string;
  ignore_marker_files: string;
  write_ignore_markers: boolean;
  generic_dir_names: string;
  min_title_length: number;
  max_files: number;
  conflict_policy: "skip" | "overwrite" | "suffix";
  keep_empty_dirs: boolean;
  asset_policy: "move" | "keep";
  batch_size: number;
  batch_delay_seconds: number;
  retry_times: number;
  retry_backoff_seconds: number;
  scraper_enabled: boolean;
}

export interface NamingPreset {
  id: string;
  name: string;
  is_default: boolean;
  library_profile: string;
  templates: Record<Kind, TemplateSpec>;
  options: NamingPresetOptions;
  created_at?: string;
  updated_at?: string;
}

export interface WorkflowPreset {
  id: string;
  name: string;
  is_default: boolean;
  library_profile: string;
  naming_preset_id: string;
  options: WorkflowPresetOptions;
  created_at?: string;
  updated_at?: string;
}

export interface NamingPresetInput {
  name: string;
  library_profile: string;
  templates: Record<Kind, TemplateSpec>;
  options?: NamingPresetOptions;
}

export interface WorkflowPresetInput {
  name: string;
  library_profile: string;
  naming_preset_id?: string;
  options?: WorkflowPresetOptions;
}

export type QueueStage = "scan" | "match" | "preview" | "done";

export interface QueueEntry {
  entry_id: string;
  stage: QueueStage;
  group_id?: string;
  work_id?: string | null;
  work_key: string;
  title: string;
  year: number | null;
  kind?: Kind | "unknown" | "asset";
  category: Category;
  file_ids: string[];
  filename: string;
  rel_path: string;
  parent: string;
  season: number | null;
  episode: number | null;
  is_folder: boolean;
  needs_assign: boolean;
  folder_key?: string | null;
  result: { status: string; message: string } | null;
}

export interface QueueView {
  entries: QueueEntry[];
  counts: Record<QueueStage, number>;
  groups: DirectoryGroup[];
  total: number;
  moved?: number;
}

export interface PreviewRow {
  file_id: string;
  entry_id?: string | null;
  group_id?: string;
  original_path: string;
  new_path: string;
  new_name: string;
  kind: Kind | "unknown" | "asset";
  category: Category;
  action: "rename" | "skip";
  status: "ok" | "warning" | "error" | "skip";
  messages: string[];
}

export interface DuplicateFile {
  file_id: string;
  filename: string;
  rel_path: string;
  group_id: string;
  version: string;
  resolution: string;
  excluded: boolean;
  reason: string;
  status?: string;
}

export interface DuplicateDecision {
  action: "exclude" | "keep" | "keep_all" | "replace" | "reset" | string;
  exclude: string[];
  updated_at?: string;
}

export interface DuplicateGroup {
  key: string;
  work_id: string;
  title: string;
  year: number | null;
  season: number;
  episode: number;
  kind: "multi_version" | "multi_source" | "parse_error" | string;
  kind_label: string;
  reason: string;
  files: DuplicateFile[];
  decision: DuplicateDecision | null;
}

export interface PreviewResponse {
  preset_id: string;
  preset_name: string;
  is_sample: boolean;
  subtitle_mode: string;
  rows: PreviewRow[];
  summary: { ok: number; warning: number; error: number; skip: number };
  duplicates?: DuplicateGroup[];
}

export interface ExecuteRow {
  seq?: number;
  file_id?: string | null;
  group_id?: string | null;
  category?: Category;
  original_path: string;
  target_path: string;
  status: "done" | "skipped" | "failed" | "conflict" | "planned" | "merged";
  message: string;
  choice?: string;
  decision_source?: string;
  rule_id?: string;
  decided_at?: string;
}

export interface ExecuteRisk {
  code: string;
  level: string;
  message: string;
  path: string;
}

export interface ExecuteResponse {
  preset_id: string;
  preset_name: string;
  dry_run: boolean;
  conflict_policy: string;
  keep_empty_dirs: boolean;
  operation_id: string | null;
  status?: string;
  total?: number;
  batch_count?: number;
  message?: string;
  needs_confirmation?: boolean;
  risks?: ExecuteRisk[];
  counts?: { done: number; skipped: number; failed: number; conflict?: number; planned: number };
  rows?: ExecuteRow[];
}

export type OperationStatus =
  | "queued"
  | "running"
  | "paused"
  | "interrupted"
  | "cancelled"
  | "conflict"
  | "done"
  | "partial"
  | "undone";

export interface OperationProgress {
  operation_id: string;
  status: OperationStatus;
  total: number;
  processed: number;
  done: number;
  skipped: number;
  failed: number;
  conflict: number;
  planned: number;
  merged: number;
  current_item: string;
  batch_index: number;
  batch_count: number;
  summary: OperationRecord["summary"];
}

export type ConflictAction =
  | "skip"
  | "overwrite"
  | "suffix"
  | "merge-skip"
  | "merge-overwrite"
  | "merge-suffix";

export interface ConflictItem {
  seq: number;
  group_id?: string | null;
  file_id?: string | null;
  category?: Category;
  original_path: string;
  target_path: string;
  status: string;
  message: string;
  choice?: string;
}

export interface ConflictView {
  operation_id: string;
  status: OperationStatus;
  groups: { id: string; name: string }[];
  files: ConflictItem[];
  folders: ConflictItem[];
  total: number;
}

export interface ResolutionStats {
  file_skip: number;
  file_overwrite: number;
  file_suffix: number;
  folder_skip: number;
  folder_overwrite: number;
  folder_suffix: number;
  merge_folders: number;
  merge_skip: number;
  merge_overwrite: number;
  merge_suffix: number;
  merged_files: number;
}

export interface ResolveResponse {
  operation_id: string;
  status: string;
  planned: number;
  resolution: ResolutionStats;
  skipped_unknown: string[];
}

export interface OperationRecord {
  id: string;
  scan_id: string | null;
  preset_id: string | null;
  preset_name: string;
  kind: "execute" | "undo";
  dry_run: boolean;
  status: OperationStatus;
  summary: {
    done?: number;
    skipped?: number;
    failed?: number;
    conflict?: number;
    conflict_policy?: string;
    source_operation?: string;
    merged?: number;
    remaining_source?: number;
    resolution?: ResolutionStats;
  };
  created_at: string;
  undone_at: string | null;
  item_count?: number;
  items?: ExecuteRow[];
  decision_counts?: { human: number; rule: number; ai: number; unknown: number };
}

export type RunStage = "scan" | "match" | "preview" | "execute" | "done";

export interface RunRecord {
  id: string;
  scan_id: string;
  stage: RunStage;
  stage_label: string;
  status: string;
  preset_id: string | null;
  operation_id: string | null;
  groups: DirectoryGroup[];
  snapshot: Record<string, unknown>;
  lock_owner: string;
  lock_expires_at: string;
  created_at: string;
  updated_at: string;
}

export interface PendingCounts {
  open: number;
  resolved: number;
  blocking: number;
  total?: number;
}

export interface PendingItem {
  id: string;
  run_id: string;
  scan_id: string;
  kind: string;
  kind_label: string;
  ref_type: string;
  ref_id: string;
  title: string;
  reason: string;
  blocking: boolean;
  status: "open" | "resolved";
  resolved_by: string;
  resolved_at: string;
  decision_source: string;
  decision_source_label: string;
  meta: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface RunStatus {
  run: RunRecord | null;
  pending: PendingCounts;
  locked?: boolean;
}

export interface GateIssue {
  kind: string;
  kind_label: string;
  ref_type: string;
  ref_id: string;
  title: string;
  reason: string;
  blocking: boolean;
}

export interface GateCheck {
  id: string;
  label: string;
  passed: boolean;
  reason: string;
  issues: GateIssue[];
}

export interface GateResult {
  checked_at: string;
  run_id: string;
  scan_id: string;
  passed: boolean;
  blocked: boolean;
  summary: string;
  failed: { id: string; label: string }[];
  checks: GateCheck[];
}

export interface AutoAuthorization {
  id: string;
  run_id: string;
  scan_id: string;
  operation_id: string;
  confirmed_by: string;
  risk_version: string;
  risk_text: string;
  gate_checked_at: string;
  status: "granted" | "consumed" | string;
  created_at: string;
  consumed_at: string;
}

export interface AutoExecuteState {
  run: RunRecord;
  enabled: boolean;
  gate: GateResult | null;
  risk: { version: string; text: string };
  authorizations: AutoAuthorization[];
  latest_granted: AutoAuthorization | null;
}

export interface PendingResponse {
  run: RunRecord | null;
  items: PendingItem[];
  counts: PendingCounts;
}

export interface UndoResponse {
  operation_id: string;
  undo_id: string | null;
  counts: { done: number; skipped: number; failed: number; conflict?: number; planned: number };
  rows: ExecuteRow[];
}

export interface ResumeResponse {
  operation_id: string;
  status?: string;
  reset?: number;
  message?: string;
  summary?: OperationRecord["summary"];
}

export interface FieldSpec {
  key: string;
  label: string;
  category: string;
  example: string;
  description: string;
}

export interface FieldPayload {
  categories: { key: string; label: string }[];
  fields: FieldSpec[];
}

export interface ConfigFieldSpec {
  key: string;
  label: string;
  type: "string" | "int" | "bool" | "select" | "secret";
  default: unknown;
  group: string;
  description: string;
  options: string[];
  option_labels?: string[];
}

export interface ConfigSchema {
  version: number;
  groups: { key: string; label: string }[];
  fields: ConfigFieldSpec[];
  media_root: string;
}

export interface ConfigRepairNotice {
  key: string;
  before: string;
  after: string;
}

export interface ProviderTestResult {
  ok: boolean;
  message: string;
  detail: string;
  proxy: boolean;
}

export interface SettingChange {
  key: string;
  label: string;
  current: unknown;
  next: unknown;
}

export interface PresetSettingsDiff {
  preset_id: string;
  preset_name: string;
  changes: SettingChange[];
}

export interface BrowseResult {
  root: string;
  path: string;
  parent: string | null;
  entries: { name: string; path: string }[];
  error?: string;
}

export interface MatchCandidate {
  source: "tmdb" | "bangumi";
  media_type: "tv" | "movie";
  id: number;
  name: string;
  original_name: string;
  year: number | null;
  overview: string;
  poster: string;
  popularity: number;
}

export interface SeasonOption {
  season_number: number;
  name: string;
  episode_count: number;
}

export interface MatchGroup {
  group_id: string;
  title: string;
  year: number | null;
  seasons: number[];
  video_count: number;
  episode_count: number;
  special_count: number;
  movie_count: number;
  match: {
    tmdb_id: number | string;
    name: string;
    media_type?: "tv" | "movie" | "";
    name_source?: string;
  } | null;
  mixed_match: boolean;
  confidence?: string;
  needs_confirm?: boolean;
  source?: string;
  file_ids?: string[];
  season_options?: SeasonOption[];
  season_choice?: number | null;
  season_mode?: "auto" | "manual" | "pending" | "default" | "unknown";
  season_pending?: boolean;
  season_recommended?: number | null;
}

export interface IdentifyWork {
  work_id: string;
  title: string;
  year: number | null;
  confidence: string;
  source: string;
  needs_confirm: boolean;
  file_ids: string[];
  files: string[];
  seasons: number[];
  video_count: number;
  episode_count: number;
  special_count: number;
  movie_count: number;
  matched: { tmdb_id: number | string; name: string } | null;
  reason?: string;
}

export interface SuspectedGroup {
  key: string;
  group_id: string;
  segment: string;
  title: string;
  year: number | null;
  file_ids: string[];
  files: string[];
  count: number;
  reason?: string;
}

export interface UnassignedFile {
  file_id: string;
  filename: string;
  path: string;
  rel_path: string;
  group_id: string;
  segment?: string;
  reason: string;
}

export interface AssignmentRecord {
  id: string;
  group_id: string;
  segment: string;
  title: string;
  year: number | null;
  created_at?: string;
  updated_at?: string;
}

export interface AliasRecord {
  id: string;
  alias: string;
  title: string;
  year: number | null;
  created_at?: string;
  updated_at?: string;
}

export interface SpecialCandidate {
  work_id: string;
  title: string;
  year: number | null;
  score: number;
  reason: string;
}

export interface SpecialSuggestion {
  folder_key: string;
  group_id: string;
  rel_path: string;
  name: string;
  candidates: SpecialCandidate[];
  confirmed: { work_id: string; title: string; year: number | null } | null;
  reason: string;
}

export interface SpecialMemory {
  id: string;
  group_id: string;
  rel_path: string;
  work_id: string;
  title: string;
  year: number | null;
  created_at: string;
  updated_at: string;
}

export interface RescanCheck {
  checked_at: string;
  total: number;
  ok: number;
  moved: number;
  missing: number;
  entries_done: number;
  entries_problem: number;
}

export interface CollectionItem {
  file_id: string;
  filename: string;
  rel_path: string;
  group_id: string;
  collection_range: string;
  work_id: string;
  title: string;
  season: number;
  decision: { action: "keep" | "season"; season?: number } | null;
}

export interface IdentifyOverview {
  works: IdentifyWork[];
  pending: IdentifyWork[];
  suspected: SuspectedGroup[];
  unassigned: UnassignedFile[];
  assignments: AssignmentRecord[];
  aliases: AliasRecord[];
  special_suggestions: SpecialSuggestion[];
  special_memory: SpecialMemory[];
  collections: CollectionItem[];
  summary: {
    works: number;
    auto: number;
    pending: number;
    suspected: number;
    unassigned: number;
  };
}

export interface MergeInfo {
  id: string;
  primary: string;
  title: string;
  merged_group_ids: string[];
  count: number;
}

export interface BatchApplyResult {
  results: {
    group_id: string;
    title: string;
    ok: boolean;
    updated?: number;
    missing?: string[];
    error?: string;
    meta?: MatchApplyResult["meta"];
  }[];
  merged: MergeInfo[];
  groups: MatchGroup[];
}

export interface MatchApplyResult {
  updated: number;
  missing: string[];
  merged?: MergeInfo[];
  meta: {
    tmdb_id: number;
    media_type: "tv" | "movie";
    name: string;
    name_source?: string;
    name_source_label?: string;
    original_name: string;
    year: number | null;
    overview: string;
    poster: string;
  };
  groups: MatchGroup[];
}

export interface AutoMatchCandidate extends MatchCandidate {
  score: number;
  reasons: string[];
}

export interface AutoMatchResult {
  group_id: string;
  title: string;
  query: string;
  status: "auto" | "ai_pick" | "choose" | "missing";
  candidates: AutoMatchCandidate[];
  meta: MatchApplyResult["meta"] | null;
  updated: number;
  missing: string[];
  message: string;
}

export interface AutoMatchResponse {
  results: AutoMatchResult[];
  groups: MatchGroup[];
  summary: { auto: number; ai_pick?: number; choose: number; missing: number };
  total: number;
  merged?: MergeInfo[];
}

export interface RuleInfo {
  id: string;
  name: string;
  scope: string;
  kind: string;
  priority: number;
  regex: string;
  enabled: boolean;
  description: string;
  sample: string;
  builtin?: boolean;
}

export interface RuleInput {
  name: string;
  scope: string;
  kind: string;
  priority: number;
  regex: string;
  enabled: boolean;
  description: string;
  sample: string;
}

export interface ScanRecord {
  scan_id: string;
  created_at: string;
  updated_at: string;
  file_count: number;
}

export interface MatchSearchResponse {
  query: string;
  source: string;
  candidates: MatchCandidate[];
  fallback?: MatchCandidate[];
}

export interface AiStatus {
  enabled: boolean;
  configured: boolean;
  special_enabled?: boolean;
  message: string;
  batch_limit: number;
}

export interface AiSeasonSuggestion {
  work_id: string;
  season: number | null;
  candidates: number[];
  reason: string;
  message: string;
}

export interface SeasonPlanGroup {
  group_id: string;
  title: string;
  year: number | null;
  current_season: number;
  season_choice: number | null;
  manual: boolean;
  action: "assign" | "keep_manual" | "unresolved" | "invalid_clue" | "conflict";
  proposed_season: number | null;
  clue_text: string;
  reason: string;
  message: string;
  episodes: number[];
}

export interface SeasonPlanCluster {
  tmdb_id: string;
  name: string;
  seasons: { season_number: number; name: string; episode_count: number }[];
  groups: SeasonPlanGroup[];
  assignments: { group_id: string; title: string; season: number; reason: string }[];
  manual: { group_id: string; title: string; season: number; message: string }[];
  unresolved: { group_id: string; title: string; message: string }[];
  looks_like: number[];
  before_collisions: number;
  collisions: { season: number; episode: number; groups: string[] }[];
}

export interface SeasonPlanResponse {
  clusters: SeasonPlanCluster[];
}

export interface AiSpecialSuggestion {
  folder_key: string;
  rel_path: string;
  work_id: string;
  title: string;
  year: number | null;
  confidence: string;
  reason: string;
}

export interface AiSpecialResponse {
  suggestions: AiSpecialSuggestion[];
  total: number;
  considered: number;
  message: string;
}

export interface AiIdentifySuggestion {
  file_id: string;
  filename: string;
  folder: string;
  segment: string;
  title: string;
  year: number | null;
  season: number | null;
  episode: number | null;
  kind: string;
  confidence: string;
  reason: string;
  tags: Record<string, string>;
}

export interface AiIdentifyResponse {
  suggestions: AiIdentifySuggestion[];
  total: number;
  considered: number;
  message: string;
}

export interface AiMatchSuggestion {
  group_id: string;
  current_title: string;
  query: string;
  title: string;
  year: number | null;
  media_type: "tv" | "movie";
  confidence: string;
  reason: string;
}

export interface AiMatchResponse {
  suggestions: AiMatchSuggestion[];
  total: number;
  considered: number;
  message: string;
}

export interface AiApplyItem {
  file_ids: string[];
  title: string;
  year: number | null;
  season: number | null;
  episode: number | null;
  kind: string;
  segment: string;
  remember_alias: boolean;
}

export interface AiApplyResponse {
  applied: number;
  remembered: number;
  aliases: AliasRecord[];
}

// ---- 命名规律分析（一次只处理「目录组 + 子目录」） ----

export interface PatternRule {
  role: string;
  container?: string;
  match?: string;
  take?: string;
}

export interface PatternStructure {
  rules?: PatternRule[];
  [key: string]: unknown;
}

export interface PatternCheckItem {
  key: string;
  label: string;
  ok: boolean;
  detail: string;
  examples: string[];
}

export interface PatternFileValues {
  kind?: string | null;
  episode?: number | string | null;
  season?: number | null;
  special_no?: number | null;
  version?: string | number | null;
}

export interface PatternSampleRow {
  position: string;
  file_id: string;
  original: string;
  new_name: string;
  new_path: string;
  status: string;
  messages: string[];
  evidence: string[];
  suspects: string[];
  builtin: PatternFileValues;
  ai: PatternFileValues;
  changed: boolean;
}

export type PatternDecisionValue = "adopted" | "rejected" | "later";

export interface PatternDecisionRecord {
  id: string;
  group_id: string;
  dir_rel: string;
  decision: PatternDecisionValue;
  decision_label: string;
  note: string;
  structure: PatternStructure;
  created_at: string;
  updated_at: string;
}

export interface PatternConflictValue {
  builtin?: unknown;
  ai?: unknown;
}

export interface PatternProposeResult {
  status: "proposal" | "candidate_only" | "abandoned";
  applied: boolean;
  source_label: string;
  target_count: number;
  message: string;
  attempts?: number;
  limit?: number;
  structure?: PatternStructure;
  checks: PatternCheckItem[];
  sample: PatternSampleRow[];
  mismatched: string[];
  conflicts?: Record<string, Record<string, PatternConflictValue>>;
  decision?: PatternDecisionRecord | null;
  remind?: boolean;
}

export interface PatternApplyResult {
  status: string;
  applied: boolean;
  written: number;
  skipped: number;
  skipped_files?: string[];
  cached: number;
  source?: string;
  source_label?: string;
  message: string;
  checks?: PatternCheckItem[];
  conflicts?: Record<string, Record<string, PatternConflictValue>>;
}

export interface PatternCacheEntry {
  id: string;
  fingerprint: string;
  group_id: string;
  dir_rel: string;
  structure: PatternStructure;
  source: string;
  source_label: string;
  enabled: boolean;
  hits: number;
  created_at: string;
  updated_at: string;
  last_used_at: string;
}

export interface PatternCacheResponse {
  auto_apply: boolean;
  total: number;
  entries: PatternCacheEntry[];
  decisions: PatternDecisionRecord[];
}
