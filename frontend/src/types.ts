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
  fields: Record<string, string | number>;
  identify?: FileIdentify;
}

export interface ScanPayload {
  scan_id: string;
  root: string;
  groups?: DirectoryGroup[];
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
  };
  truncated: boolean;
  ignored_dirs?: IgnoredDir[];
  markers_written?: number;
  is_sample?: boolean;
}

export interface TemplateSpec {
  pattern: string;
  output_mode: "filename" | "path";
  /** 旧版“完整路径模板”拆出来的目录部分，界面只编辑文件名 */
  legacy_dir?: string;
}

export interface PresetOptions {
  subtitle_mode: "follow" | "keep";
  auto_append_ext: boolean;
  max_filename_bytes: number;
  forbidden_chars: string;
  specials_dir: string;
  preserve_structure: boolean;
  movie_layout: "folder" | "special";
  tmdb_lang: string;
  scan_all_files: boolean;
  ignore_special_dirs: boolean;
  ova_policy: "move" | "ignore";
  special_dir_names: string;
  write_ignore_markers: boolean;
  default_season: number;
  special_season_zero: boolean;
  conflict_policy: "skip" | "overwrite" | "suffix";
  keep_empty_dirs: boolean;
  asset_policy: "move" | "keep";
}

export interface Preset {
  id: string;
  name: string;
  is_default: boolean;
  library_profile: string;
  templates: Record<Kind, TemplateSpec>;
  options: PresetOptions;
  created_at?: string;
  updated_at?: string;
}

export interface PresetInput {
  name: string;
  library_profile: string;
  templates: Record<Kind, TemplateSpec>;
  options?: PresetOptions;
}

export interface PreviewRow {
  file_id: string;
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

export interface PreviewResponse {
  preset_id: string;
  preset_name: string;
  is_sample: boolean;
  subtitle_mode: string;
  rows: PreviewRow[];
  summary: { ok: number; warning: number; error: number; skip: number };
}

export interface ExecuteRow {
  seq?: number;
  file_id?: string | null;
  group_id?: string | null;
  category?: Category;
  original_path: string;
  target_path: string;
  status: "done" | "skipped" | "failed" | "conflict" | "planned";
  message: string;
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
  current_item: string;
  batch_index: number;
  batch_count: number;
  summary: OperationRecord["summary"];
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
  };
  created_at: string;
  undone_at: string | null;
  item_count?: number;
  items?: ExecuteRow[];
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
}

export interface ConfigSchema {
  version: number;
  groups: { key: string; label: string }[];
  fields: ConfigFieldSpec[];
  media_root: string;
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

export interface MatchGroup {
  group_id: string;
  title: string;
  year: number | null;
  seasons: number[];
  video_count: number;
  episode_count: number;
  special_count: number;
  movie_count: number;
  match: { tmdb_id: number | string; name: string } | null;
  mixed_match: boolean;
  confidence?: string;
  needs_confirm?: boolean;
  source?: string;
  file_ids?: string[];
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
}

export interface UnassignedFile {
  file_id: string;
  filename: string;
  path: string;
  rel_path: string;
  group_id: string;
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

export interface IdentifyOverview {
  works: IdentifyWork[];
  pending: IdentifyWork[];
  suspected: SuspectedGroup[];
  unassigned: UnassignedFile[];
  assignments: AssignmentRecord[];
  summary: {
    works: number;
    auto: number;
    pending: number;
    suspected: number;
    unassigned: number;
  };
}

export interface MatchApplyResult {
  updated: number;
  missing: string[];
  meta: {
    tmdb_id: number;
    media_type: "tv" | "movie";
    name: string;
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
  status: "auto" | "choose" | "missing";
  candidates: AutoMatchCandidate[];
  meta: MatchApplyResult["meta"] | null;
  updated: number;
  missing: string[];
  message: string;
}

export interface AutoMatchResponse {
  results: AutoMatchResult[];
  groups: MatchGroup[];
  summary: { auto: number; choose: number; missing: number };
  total: number;
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
}
