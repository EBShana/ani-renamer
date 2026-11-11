import { useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  Collapse,
  Divider,
  Empty,
  Input,
  InputNumber,
  Modal,
  Progress,
  Row,
  Segmented,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Tree,
  Typography,
  message,
} from "antd";
import {
  CopyOutlined,
  DeleteOutlined,
  DownloadOutlined,
  FileOutlined,
  FolderOutlined,
  PauseCircleOutlined,
  SafetyCertificateOutlined,
  SaveOutlined,
  StarOutlined,
  UploadOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import type { DataNode } from "antd/es/tree";
import { api } from "../api";
import PageSections from "../components/SectionNav";
import type {
  Category,
  ConflictAction,
  ConflictItem,
  ConflictView,
  DuplicateGroup,
  ExecuteRow,
  FieldPayload,
  GateResult,
  Kind,
  OperationProgress,
  NamingPreset,
  OperationRecord,
  ParsedFile,
  PreviewResponse,
  PreviewRow,
  QueueEntry,
  QueueView,
  ScanPayload,
  TemplateSpec,
} from "../types";
import { CATEGORY_LABELS } from "../types";
import ListFilterBar, { EMPTY_FILTER, entryMatches, type ListFilterValue } from "../listFilters";

const { Text, Title } = Typography;

const DECISION_SOURCE_LABELS: Record<string, string> = {
  human: "人工确认",
  rule: "规则自动",
  ai: "AI 决策（预留）",
  "": "未知",
};

const RESOLVE_LABELS: Record<string, string> = {
  skip: "跳过",
  overwrite: "覆盖",
  suffix: "生成副本",
  "merge-skip": "合并（同名项跳过）",
  "merge-overwrite": "合并（同名项覆盖）",
  "merge-suffix": "合并（同名项生成副本）",
};

const FILE_ACTION_OPTIONS: { value: ConflictAction; label: string }[] = [
  { value: "skip", label: "跳过" },
  { value: "overwrite", label: "覆盖" },
  { value: "suffix", label: "生成副本" },
];

const FOLDER_ACTION_OPTIONS: { value: ConflictAction; label: string }[] = [
  { value: "skip", label: "整体跳过" },
  { value: "overwrite", label: "整体覆盖" },
  { value: "suffix", label: "整体生成副本" },
  { value: "merge-skip", label: "合并：同名项跳过" },
  { value: "merge-overwrite", label: "合并：同名项覆盖" },
  { value: "merge-suffix", label: "合并：同名项生成副本" },
];

const OPERATION_STATUS_TAGS: Record<string, { color: string; label: string }> = {
  queued: { color: "blue", label: "排队中" },
  running: { color: "processing", label: "执行中" },
  paused: { color: "gold", label: "已暂停" },
  interrupted: { color: "orange", label: "已中断" },
  cancelled: { color: "default", label: "已取消" },
  conflict: { color: "volcano", label: "待处理冲突" },
  done: { color: "green", label: "已完成" },
  partial: { color: "orange", label: "部分完成" },
  undone: { color: "default", label: "已撤销" },
};

const ACTIVE_STATUSES = ["queued", "running", "paused"];

const historyColumns = (
  busy: string,
  onUndo: (id: string) => void,
  onResume: (id: string) => void,
  onPause: (id: string) => void,
  onContinue: (id: string) => void,
  onCancel: (id: string) => void,
  onResolve: (id: string) => void,
): ColumnsType<OperationRecord> => [
  {
    title: "时间",
    dataIndex: "created_at",
    width: 180,
    render: (value: string) => new Date(value).toLocaleString("zh-CN", { hour12: false }),
  },
  {
    title: "类型",
    dataIndex: "kind",
    width: 80,
    render: (value: string) => (value === "undo" ? <Tag>撤销</Tag> : <Tag color="blue">执行</Tag>),
  },
  {
    title: "预设",
    dataIndex: "preset_name",
    width: 160,
    render: (value: string) => value || "-",
  },
  {
    title: "结果",
    dataIndex: "summary",
    render: (value: OperationRecord["summary"]) =>
      `成功 ${value.done ?? 0} · 跳过 ${value.skipped ?? 0} · 失败 ${value.failed ?? 0} · 冲突 ${
        value.conflict ?? 0
      }`,
  },
  {
    title: "状态",
    dataIndex: "status",
    width: 108,
    render: (value: string) => {
      const meta = OPERATION_STATUS_TAGS[value] ?? OPERATION_STATUS_TAGS.done;
      return <Tag color={meta.color}>{meta.label}</Tag>;
    },
  },
  {
    title: "决策来源",
    dataIndex: "decision_counts",
    width: 170,
    render: (value: OperationRecord["decision_counts"]) => {
      const data = value || { human: 0, rule: 0, ai: 0, unknown: 0 };
      return (
        <Space size={2} wrap>
          {data.human > 0 && <Tag color="blue">人工 {data.human}</Tag>}
          {data.rule > 0 && <Tag>规则 {data.rule}</Tag>}
          {data.ai > 0 && <Tag color="purple">AI {data.ai}</Tag>}
          {data.unknown > 0 && <Tag>未知 {data.unknown}</Tag>}
          {!data.human && !data.rule && !data.ai && !data.unknown && (
            <Text type="secondary">-</Text>
          )}
        </Space>
      );
    },
  },
  {
    title: "操作",
    dataIndex: "id",
    width: 210,
    render: (value: string, record) => {
      if (record.kind !== "execute") return <Text type="secondary">-</Text>;
      const status = record.status;
      const running = status === "running";
      const queued = status === "queued";
      const paused = status === "paused";
      const conflict = status === "conflict";
      const resumable = status === "interrupted" || status === "partial";
      const undoable =
        status === "done" || status === "partial" || status === "conflict" ||
        status === "cancelled" || status === "interrupted";
      return (
        <Space size={4} wrap>
          {running && (
            <Button size="small" icon={<PauseCircleOutlined />} loading={busy === value} onClick={() => onPause(value)}>
              暂停
            </Button>
          )}
          {queued && (
            <Button size="small" loading={busy === value} onClick={() => onCancel(value)}>
              取消
            </Button>
          )}
          {paused && (
            <Button size="small" loading={busy === value} onClick={() => onContinue(value)}>
              继续
            </Button>
          )}
          {(running || paused) && (
            <Button size="small" danger loading={busy === value} onClick={() => onCancel(value)}>
              取消
            </Button>
          )}
          {conflict && (
            <Button
              size="small"
              type="primary"
              loading={busy === value}
              onClick={() => onResolve(value)}
            >
              处理冲突
            </Button>
          )}
          {resumable && (
            <Button size="small" loading={busy === value} onClick={() => onResume(value)}>
              续跑
            </Button>
          )}
          {undoable && (
            <Button size="small" loading={busy === value} onClick={() => onUndo(value)}>
              撤销
            </Button>
          )}
          {!running && !queued && !paused && !resumable && !undoable && (
            <Text type="secondary">-</Text>
          )}
        </Space>
      );
    },
  },
];


const KIND_TABS: { key: Kind; label: string }[] = [
  { key: "episode", label: "剧集" },
  { key: "special", label: "特典" },
  { key: "movie", label: "剧场版" },
];

const KIND_COLORS: Record<string, string> = {
  episode: "blue",
  special: "purple",
  movie: "gold",
  unknown: "red",
};

const STATUS_TAGS: Record<string, { color: string; label: string }> = {
  ok: { color: "green", label: "可执行" },
  warning: { color: "orange", label: "需注意" },
  error: { color: "red", label: "冲突" },
  skip: { color: "default", label: "跳过" },
};

function basename(path: string): string {
  const parts = path.split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

interface Props {
  scan: ScanPayload | null;
  queue: QueueView | null;
  reloadQueue: () => Promise<void>;
  namingPresets: NamingPreset[];
  namingPresetId: string;
  onNamingPresetChange: (id: string) => void;
  reloadNamingPresets: (selectId?: string) => Promise<NamingPreset[]>;
  onPresetsImported: () => Promise<void>;
  onGoScan: () => void;
  onGoMatch: () => void;
  onGoSettings: () => void;
  onScan: (payload: ScanPayload) => void;
  onSettingsChanged: () => void;
  settingsVersion: number;
}

export default function TemplatePage({
  scan,
  queue,
  reloadQueue,
  namingPresets,
  namingPresetId,
  onNamingPresetChange,
  reloadNamingPresets,
  onPresetsImported,
  onGoScan,
  onGoMatch,
  onGoSettings,
  onScan,
  onSettingsChanged,
  settingsVersion,
}: Props) {
  const [checkedEntries, setCheckedEntries] = useState<string[]>([]);
  const [filters, setFilters] = useState<ListFilterValue>(EMPTY_FILTER);
  const [draft, setDraft] = useState<NamingPreset | null>(null);
  const [kind, setKind] = useState<Kind>("episode");
  const [fields, setFields] = useState<FieldPayload | null>(null);
  const [validation, setValidation] = useState<{ errors: string[]; warnings: string[] }>({
    errors: [],
    warnings: [],
  });
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  // 预览页默认看目录树，切到列表只是换展示，不重新请求、不改筛选
  const [viewMode, setViewMode] = useState<"tree" | "list">("tree");
  const [expandedKeys, setExpandedKeys] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [configValues, setConfigValues] = useState<Record<string, unknown>>({});
  const [saveAsOpen, setSaveAsOpen] = useState(false);
  const [saveAsName, setSaveAsName] = useState("");
  const [executing, setExecuting] = useState(false);
  // 自动执行授权：先看门槛，全绿才能填确认人并生成一次性授权
  const [autoOpen, setAutoOpen] = useState(false);
  const [autoLoading, setAutoLoading] = useState(false);
  const [autoSubmitting, setAutoSubmitting] = useState(false);
  const [autoRunId, setAutoRunId] = useState("");
  const [autoEnabled, setAutoEnabled] = useState(false);
  const [autoGate, setAutoGate] = useState<GateResult | null>(null);
  const [autoRisk, setAutoRisk] = useState<{ version: string; text: string } | null>(null);
  const [autoName, setAutoName] = useState("");
  const [previewNonce, setPreviewNonce] = useState(0);
  const [duplicateChecked, setDuplicateChecked] = useState<Record<string, string[]>>({});
  const [explainFiles, setExplainFiles] = useState<string[] | null>(null);
  const [progress, setProgress] = useState<OperationProgress | null>(null);
  const [history, setHistory] = useState<OperationRecord[]>([]);
  const [historyItems, setHistoryItems] = useState<
    Record<string, ExecuteRow[] | "loading">
  >({});
  const [undoing, setUndoing] = useState("");
  const [conflictView, setConflictView] = useState<ConflictView | null>(null);
  const [conflictChoices, setConflictChoices] = useState<Record<number, ConflictAction>>({});
  const [conflictChecked, setConflictChecked] = useState<number[]>([]);
  const [conflictLoading, setConflictLoading] = useState(false);
  const [overwriteConfirm, setOverwriteConfirm] = useState<{
    targets: ConflictItem[];
    folderOverwrite: boolean;
    choices: { seq: number; action: ConflictAction }[];
  } | null>(null);
  const openedConflictRef = useRef<string | null>(null);
  const editorRef = useRef<HTMLTextAreaElement | null>(null);
  const importRef = useRef<HTMLInputElement | null>(null);

  const loadHistory = async () => {
    try {
      const data = await api.history(10);
      setHistory(data.operations);
      if (data.active_operation_id) {
        // 关掉页面再回来：任务还在后台跑，继续显示进度
        const active = await api.progress(data.active_operation_id);
        setProgress(active);
      }
    } catch {
      // 历史记录读取失败不影响主流程
    }
  };

  useEffect(() => {
    loadHistory();
  }, []);

  const loadHistoryItems = async (operationId: string) => {
    if (historyItems[operationId]) return;
    setHistoryItems((current) => ({ ...current, [operationId]: "loading" }));
    try {
      const detail = await api.historyDetail(operationId);
      setHistoryItems((current) => ({ ...current, [operationId]: detail.items || [] }));
    } catch {
      setHistoryItems((current) => ({ ...current, [operationId]: [] }));
    }
  };

  useEffect(() => {
    setCheckedEntries(scan?.selections?.preview ?? []);
  }, [scan?.scan_id]);

  useEffect(() => {
    const id = progress?.operation_id;
    const status = progress?.status;
    if (!id || !status || !ACTIVE_STATUSES.includes(status)) return;
    const timer = window.setInterval(async () => {
      try {
        const next = await api.progress(id);
        setProgress(next);
        if (!ACTIVE_STATUSES.includes(next.status)) {
          await loadHistory();
          // 成功的条目已移出清单，刷新后剩余条目仍在原处
          await reloadQueue();
        }
      } catch {
        // 轮询失败下一次再试
      }
    }, 1200);
    return () => window.clearInterval(timer);
  }, [progress?.operation_id, progress?.status]);

  useEffect(() => {
    api
      .fields()
      .then(setFields)
      .catch((error) => message.error(`读取变量失败：${(error as Error).message}`));
  }, []);

  useEffect(() => {
    api
      .config()
      .then((result) => setConfigValues(result.values))
      .catch(() => {
        // 读取设置失败时使用后端默认策略，不阻塞预览
      });
  }, [settingsVersion]);

  useEffect(() => {
    const current =
      namingPresets.find((item) => item.id === namingPresetId) ?? namingPresets[0];
    if (current) {
      setDraft(JSON.parse(JSON.stringify(current)) as NamingPreset);
    }
  }, [namingPresetId, namingPresets]);

  // 命名这一层的“已修改”单独算：只比 13 个命名/落点选项 + 3 条模板，
  // 不把工作流设置混进来。
  const NAMING_OPTION_SETTINGS: [keyof NamingPreset["options"], string][] = [
    ["subtitle_mode", "naming.subtitle_mode"],
    ["auto_append_ext", "naming.auto_append_ext"],
    ["max_filename_bytes", "naming.max_filename_bytes"],
    ["forbidden_chars", "naming.forbidden_chars"],
    ["specials_dir", "naming.specials_dir"],
    ["preserve_structure", "naming.preserve_structure"],
    ["movie_layout", "naming.movie_layout"],
    ["special_folder_suffix", "naming.special_folder_suffix"],
    ["default_season", "parse.default_season"],
    ["special_season_zero", "parse.special_season_zero"],
    ["ova_policy", "parse.ova_policy"],
    ["special_video_policy", "execution.special_video_policy"],
    ["special_folder_conflict", "execution.special_folder_conflict"],
  ];

  const namingDirty = useMemo(() => {
    const current =
      namingPresets.find((item) => item.id === namingPresetId) ?? null;
    if (!current) return { options: 0, templates: 0 };
    const options = NAMING_OPTION_SETTINGS.filter(([option, setting]) => {
      const saved = current.options?.[option];
      const live = configValues[setting];
      if (saved === undefined || live === undefined) return false;
      return String(saved) !== String(live);
    }).length;
    const templates = (["episode", "special", "movie"] as Kind[]).filter(
      (kindKey) =>
        JSON.stringify(current.templates?.[kindKey] ?? {}) !==
        JSON.stringify(draft?.templates?.[kindKey] ?? {}),
    ).length;
    return { options, templates };
  }, [namingPresets, namingPresetId, configValues, draft]);

  const namingDirtyTotal = namingDirty.options + namingDirty.templates;

  const previewEntries = useMemo(
    () => (queue?.entries ?? []).filter((entry) => entry.stage === "preview"),
    [queue],
  );
  const previewEntryIds = previewEntries.map((entry) => entry.entry_id).join(",");

  useEffect(() => {
    if (!draft) return;
    const timer = window.setTimeout(async () => {
      const template = draft.templates[kind];
      setLoading(true);
      try {
        const [checked, result] = await Promise.all([
          api.validate(template.pattern, template.output_mode),
          api.preview({
            scan_id: scan?.scan_id,
            naming_preset_id: draft.id,
            templates: draft.templates,
            file_ids: queue
              ? previewEntries.flatMap((entry) => (entry.is_folder ? [] : entry.file_ids))
              : undefined,
            folder_ids: queue
              ? previewEntries.filter((entry) => entry.is_folder).map((entry) => entry.folder_key || "")
              : undefined,
          }),
        ]);
        setValidation(checked);
        setPreview(result);
        if (explainFiles && explainFiles.length) {
          const names = result.rows
            .filter((row) => row.file_id && explainFiles.includes(row.file_id) && row.new_name)
            .map((row) => row.new_name);
          if (names.length) {
            message.success(`模板已加入区分变量，将生成：${names.slice(0, 4).join(" / ")}`);
          }
          setExplainFiles(null);
        }
      } catch (error) {
        message.error(`预览失败：${(error as Error).message}`);
      } finally {
        setLoading(false);
      }
    }, 450);
    return () => window.clearTimeout(timer);
  }, [draft, kind, scan?.scan_id, configValues, queue, previewEntryIds, previewNonce]);

  const updateConfig = async (patch: Record<string, unknown>) => {
    try {
      const result = await api.saveConfig({ ...configValues, ...patch });
      setConfigValues(result.values);
      onSettingsChanged();
      message.success("命名选项已保存，点「保存」写回命名预设后就会显示「和预设一致」");
    } catch (error) {
      message.error(`保存失败：${(error as Error).message}`);
    }
  };

  const currentTemplate: TemplateSpec | null = draft ? draft.templates[kind] : null;

  const groupedFields = useMemo(() => {
    if (!fields) return [];
    return fields.categories
      .map((category) => ({
        ...category,
        items: fields.fields.filter((field) => field.category === category.key),
      }))
      .filter((group) => group.items.length > 0);
  }, [fields]);

  const patchTemplate = (target: Kind, patch: Partial<TemplateSpec>) => {
    setDraft((previous) =>
      previous
        ? {
            ...previous,
            templates: {
              ...previous.templates,
              [target]: { ...previous.templates[target], ...patch },
            },
          }
        : previous,
    );
  };

  const insertVariable = (key: string) => {
    if (!draft) return;
    const element = editorRef.current;
    const pattern = draft.templates[kind].pattern;
    const start = element?.selectionStart ?? pattern.length;
    const end = element?.selectionEnd ?? start;
    const token = `{${key}}`;
    const next = pattern.slice(0, start) + token + pattern.slice(end);
    patchTemplate(kind, { pattern: next });
    window.requestAnimationFrame(() => {
      element?.focus();
      element?.setSelectionRange(start + token.length, start + token.length);
    });
  };

  const handleSave = async () => {
    if (!draft) return;
    setSaving(true);
    try {
      // 不传 options：后端会把当前设置里的命名部分存进这份命名预设，
      // 保存后「已修改」自然回到「和预设一致」。
      await api.updateNamingPreset(draft.id, {
        name: draft.name,
        library_profile: draft.library_profile,
        templates: draft.templates,
      });
      await reloadNamingPresets(draft.id);
      onSettingsChanged();
      message.success("命名预设已保存（模板 + 当前命名选项）");
    } catch (error) {
      message.error(`保存失败：${(error as Error).message}`);
    } finally {
      setSaving(false);
    }
  };

  const handleSaveAs = async () => {
    if (!draft) return;
    try {
      const created = await api.createNamingPreset({
        name: saveAsName.trim() || `${draft.name} 副本`,
        library_profile: draft.library_profile,
        templates: draft.templates,
      });
      await reloadNamingPresets(created.id);
      setSaveAsOpen(false);
      setSaveAsName("");
      message.success("已另存为新命名预设");
    } catch (error) {
      message.error(`另存失败：${(error as Error).message}`);
    }
  };

  const handleDelete = () => {
    if (!draft) return;
    Modal.confirm({
      title: `删除命名预设「${draft.name}」？`,
      content:
        "删除后无法恢复，当前扫描结果不受影响。如果有工作流预设正在使用它，会提示你先换掉。",
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await api.deleteNamingPreset(draft.id);
          await reloadNamingPresets();
          message.success("已删除");
        } catch (error) {
          message.error((error as Error).message);
        }
      },
    });
  };

  const handleDuplicate = async () => {
    if (!draft) return;
    try {
      const created = await api.duplicateNamingPreset(draft.id);
      await reloadNamingPresets(created.id);
      message.success("已复制命名预设");
    } catch (error) {
      message.error((error as Error).message);
    }
  };

  const handleSetDefault = async () => {
    if (!draft) return;
    try {
      await api.setDefaultNamingPreset(draft.id);
      await reloadNamingPresets(draft.id);
      message.success("已设为默认命名预设");
    } catch (error) {
      message.error((error as Error).message);
    }
  };

  const handleExport = async () => {
    try {
      const payload = await api.exportPresets();
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "anime-renamer-presets.json";
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      message.error(`导出失败：${(error as Error).message}`);
    }
  };

  const handleImport = async (file: File) => {
    try {
      const text = await file.text();
      const payload = JSON.parse(text) as Record<string, unknown>;
      const result = await api.importPresets(payload);
      await reloadNamingPresets();
      await onPresetsImported();
      message.success(`已导入 ${result.created} 条预设（命名 + 工作流）`);
    } catch (error) {
      message.error(`导入失败：${(error as Error).message}`);
    }
  };

  const openConflicts = async (operationId: string) => {
    setConflictLoading(true);
    try {
      const view = await api.conflicts(operationId);
      const defaults: Record<number, ConflictAction> = {};
      for (const item of [...view.files, ...view.folders]) defaults[item.seq] = "skip";
      setConflictChoices(defaults);
      setConflictChecked([]);
      setConflictView(view);
    } catch (error) {
      message.error(`读取冲突失败：${(error as Error).message}`);
    } finally {
      setConflictLoading(false);
    }
  };

  useEffect(() => {
    const id = progress?.operation_id;
    if (!id || progress?.status !== "conflict") return;
    if (openedConflictRef.current === id) return;
    openedConflictRef.current = id;
    void openConflicts(id);
  }, [progress?.operation_id, progress?.status]);

  const toggleConflictCheck = (seq: number, on: boolean) => {
    setConflictChecked((prev) => (on ? [...prev, seq] : prev.filter((item) => item !== seq)));
  };

  const applyConflictAction = (items: ConflictItem[], action: ConflictAction) => {
    const ids = items.map((item) => item.seq);
    const picked = ids.filter((id) => conflictChecked.includes(id));
    const scope = picked.length ? picked : ids;
    setConflictChoices((prev) => {
      const next = { ...prev };
      for (const id of scope) next[id] = action;
      return next;
    });
    message.success(`已把 ${scope.length} 项设为「${RESOLVE_LABELS[action] ?? action}」`);
  };

  const statsText = (stats: {
    file_skip?: number;
    file_overwrite?: number;
    file_suffix?: number;
    folder_skip?: number;
    folder_overwrite?: number;
    folder_suffix?: number;
    merge_folders?: number;
    merge_skip?: number;
    merge_overwrite?: number;
    merge_suffix?: number;
    merged_files?: number;
  }) => {
    const overwrite = (stats.file_overwrite ?? 0) + (stats.folder_overwrite ?? 0);
    const skip = (stats.file_skip ?? 0) + (stats.folder_skip ?? 0) + (stats.merge_skip ?? 0);
    const suffix =
      (stats.file_suffix ?? 0) + (stats.folder_suffix ?? 0) + (stats.merge_suffix ?? 0);
    const merge = stats.merge_folders ?? 0;
    const moved = stats.merged_files ?? 0;
    return `覆盖 ${overwrite} · 跳过 ${skip} · 生成副本 ${suffix} · 合并 ${merge} 个文件夹（移入 ${moved} 个文件）`;
  };

  const doResolve = async (
    choices: { seq: number; action: ConflictAction }[],
    confirmOverwrite = false,
  ) => {
    if (!conflictView) return;
    setConflictLoading(true);
    try {
      const result = await api.resolve(conflictView.operation_id, choices, confirmOverwrite);
      message.success(`冲突处理已提交：${statsText(result.resolution)}`);
      openedConflictRef.current = null;
      setConflictView(null);
      setOverwriteConfirm(null);
      setProgress(await api.progress(result.operation_id));
      await loadHistory();
      await reloadQueue();
    } catch (error) {
      message.error(`处理冲突失败：${(error as Error).message}`);
    } finally {
      setConflictLoading(false);
    }
  };

  const submitConflicts = () => {
    if (!conflictView) return;
    const choices = [...conflictView.files, ...conflictView.folders].map((item) => ({
      seq: item.seq,
      action: (conflictChoices[item.seq] ?? "skip") as ConflictAction,
    }));
    const actionBySeq = new Map(choices.map((item) => [item.seq, item.action]));
    const risky = [...conflictView.files, ...conflictView.folders].filter((item) => {
      const action = actionBySeq.get(item.seq);
      return action === "overwrite" || action === "merge-overwrite";
    });
    const folderOverwrite = conflictView.folders.some(
      (item) => actionBySeq.get(item.seq) === "overwrite",
    );
    if (risky.length) {
      setOverwriteConfirm({ targets: risky, folderOverwrite, choices });
      return;
    }
    void doResolve(choices);
  };

  const conflictSections = (items: ConflictItem[]): { groupId: string; items: ConflictItem[] }[] => {
    const map = new Map<string, ConflictItem[]>();
    for (const item of items) {
      const key = String(item.group_id ?? "");
      const list = map.get(key) ?? [];
      list.push(item);
      map.set(key, list);
    }
    return Array.from(map.entries()).map(([groupId, groupItems]) => ({ groupId, items: groupItems }));
  };

  const decideDuplicate = async (
    key: string,
    action: "exclude" | "keep" | "keep_all" | "reset",
  ) => {
    if (!scan) return;
    const fileIds = duplicateChecked[key] ?? [];
    if ((action === "exclude" || action === "keep") && !fileIds.length) {
      message.warning(
        action === "exclude" ? "请先勾选要排除的文件" : "请先勾选要保留的文件（例如修正版 v2）",
      );
      return;
    }
    try {
      await api.duplicateDecision({
        scan_id: scan.scan_id,
        key,
        action,
        file_ids: fileIds,
      });
      message.success(
        action === "keep_all"
          ? "已选择全部保留，请在模板里加入能区分版本的变量"
          : action === "reset"
            ? "已撤销这组重复集的选择"
            : "重复集处理已保存",
      );
      setDuplicateChecked((prev) => ({ ...prev, [key]: [] }));
      setPreviewNonce((value) => value + 1);
    } catch (error) {
      message.error(`保存重复集选择失败：${(error as Error).message}`);
    }
  };

  const addTemplateVariable = (group: DuplicateGroup, key: "resolution" | "group") => {
    if (!draft) return;
    const token = `{${key}}`;
    const pattern = draft.templates.episode.pattern;
    setExplainFiles(group.files.map((file) => file.file_id));
    if (pattern.includes(token)) {
      setPreviewNonce((value) => value + 1);
      message.info(`正片模板里已经有 ${token}，重新生成预览看看名字`);
      return;
    }
    patchTemplate("episode", { pattern: `${pattern} - ${token}` });
    message.success(`已把 ${token} 加入正片模板（还没保存），正在重新生成预览`);
  };

  const recheckAfterExecute = async () => {
    if (!scan) return;
    try {
      const result = await api.recheck(scan.scan_id);
      message.success(
        `核对完成：还在 ${result.check.ok} 个，已移动 ${result.check.moved} 个，缺失 ${result.check.missing} 个`,
      );
      const fresh = await api.getScan(scan.scan_id);
      onScan(fresh);
      await reloadQueue();
    } catch (error) {
      message.error(`核对失败：${(error as Error).message}`);
    }
  };

  const handleExecute = () => {
    if (!draft) return;
    if (!scan) {
      message.warning("请先在「扫描」页扫描真实文件，内置示例不能执行");
      return;
    }
    if (progress && ACTIVE_STATUSES.includes(progress.status)) {
      message.warning("已有执行任务在进行，请等它完成或取消");
      return;
    }
    const selected = previewEntries.filter((entry) => checkedEntries.includes(entry.entry_id));
    const selectedIds = new Set(selected.map((entry) => entry.entry_id));
    const candidateRows = queue
      ? (preview?.rows ?? []).filter((row) => row.entry_id && selectedIds.has(row.entry_id))
      : (preview?.rows ?? []);
    const unresolved = candidateRows.filter(
      (row) =>
        row.status === "error" && (row.messages ?? []).some((text) => text.startsWith("重复集（")),
    );
    if (unresolved.length) {
      message.warning("勾选的条目里有还没处理的重复集，请先在上方的「重复集提示」里选择处理方式");
      return;
    }
    const ready = candidateRows.filter((row) => row.action === "rename" && row.status !== "error");
    if (!ready.length) {
      message.warning(queue ? "勾选的条目里没有可执行的文件" : "当前没有可执行的文件");
      return;
    }
    const selectedFileIds = selected.flatMap((entry) => (entry.is_folder ? [] : entry.file_ids));
    const selectedFolderIds = selected
      .filter((entry) => entry.is_folder)
      .map((entry) => entry.folder_key || "");
    const runExecute = async (confirmRisks: boolean) => {
      const result = await api.execute({
        scan_id: scan.scan_id,
        naming_preset_id: draft.id,
        templates: draft.templates,
        file_ids: queue ? selectedFileIds : undefined,
        folder_ids: queue ? selectedFolderIds : undefined,
        confirm_risks: confirmRisks,
      });
      if (result.needs_confirmation) {
        const risks = result.risks ?? [];
        Modal.confirm({
          title: "执行前检查发现风险",
          width: 640,
          okText: "仍然继续",
          cancelText: "取消",
          content: (
            <div>
              <div style={{ marginBottom: 8 }}>
                发现 {risks.length} 条需要留意的情况，不会自动中止：
              </div>
              <ul style={{ paddingLeft: 18, maxHeight: 260, overflow: "auto", color: "var(--ani-warning-text)" }}>
                {risks.map((risk, index) => (
                  <li key={`${risk.code}-${index}`} style={{ marginBottom: 4 }}>
                    {risk.message}
                  </li>
                ))}
              </ul>
              <div style={{ marginTop: 8, color: "var(--ani-text-tertiary)" }}>
                选择「仍然继续」会按原计划执行；「取消」不会改动任何文件。
              </div>
            </div>
          ),
          onOk: async () => {
            setExecuting(true);
            try {
              await runExecute(true);
            } catch (error) {
              message.error(`执行失败：${(error as Error).message}`);
            } finally {
              setExecuting(false);
            }
          },
        });
        return;
      }
      if (result.operation_id) {
        message.success(
          `任务已开始：共 ${result.total ?? 0} 个条目，分 ${result.batch_count ?? 1} 批执行`,
        );
        setProgress(await api.progress(result.operation_id));
      } else {
        message.info(result.message ?? "没有可执行的文件");
      }
      await loadHistory();
    };
    Modal.confirm({
      title: "确认执行改名 / 移动？",
      content: (
        <div>
          <div>
            将处理 {queue ? selected.length : ready.length} 个勾选条目（{ready.length} 个文件）。
          </div>
          <div style={{ marginTop: 6, color: "var(--ani-text-tertiary)" }}>
            目标已存在的文件不会打断任务，会先标记成「冲突，待处理」继续往下跑；
            全部结束后统一弹出冲突面板，由你决定跳过 / 覆盖 / 生成副本或合并。
            任务在后台分批执行，关掉页面也会继续，执行记录可撤销。
          </div>
        </div>
      ),
      okText: "开始执行",
      onOk: async () => {
        setExecuting(true);
        try {
          await runExecute(false);
        } catch (error) {
          message.error(`执行失败：${(error as Error).message}`);
          throw error;
        } finally {
          setExecuting(false);
        }
      },
    });
  };

  const handlePause = async (operationId: string) => {
    setUndoing(operationId);
    try {
      const result = await api.pause(operationId);
      message.info(result.message ?? "已请求暂停：当前文件处理完就会停下");
      setProgress(await api.progress(operationId));
      await loadHistory();
    } catch (error) {
      message.error(`暂停失败：${(error as Error).message}`);
    } finally {
      setUndoing("");
    }
  };

  const handleContinue = async (operationId: string) => {
    setUndoing(operationId);
    try {
      await api.continueRun(operationId);
      message.success("已继续执行");
      setProgress(await api.progress(operationId));
      await loadHistory();
    } catch (error) {
      message.error(`继续失败：${(error as Error).message}`);
    } finally {
      setUndoing("");
    }
  };

  const handleCancel = (operationId: string) => {
    Modal.confirm({
      title: "取消这次任务？",
      content:
        "已完成的文件不会自动回滚；取消后任务不能再续跑。记录会保留，之后可以在「执行记录」里手动撤销。",
      okText: "取消任务",
      okButtonProps: { danger: true },
      cancelText: "再想想",
      onOk: async () => {
        setUndoing(operationId);
        try {
          const result = await api.cancel(operationId);
          message.info(result.message ?? "已请求取消");
          setProgress(await api.progress(operationId));
          await loadHistory();
        } catch (error) {
          message.error(`取消失败：${(error as Error).message}`);
          throw error;
        } finally {
          setUndoing("");
        }
      },
    });
  };

  const handleUndo = (operationId: string) => {
    Modal.confirm({
      title: "撤销这次执行？",
      content: "会按记录把文件移回原路径；如果原路径已被占用则自动跳过。",
      okText: "撤销",
      onOk: async () => {
        setUndoing(operationId);
        try {
          const result = await api.undo(operationId);
          message.success(
            `撤销完成：还原 ${result.counts.done} 个，跳过 ${result.counts.skipped} 个，失败 ${result.counts.failed} 个`,
          );
          setProgress(null);
          await loadHistory();
        } catch (error) {
          message.error(`撤销失败：${(error as Error).message}`);
          throw error;
        } finally {
          setUndoing("");
        }
      },
    });
  };

  const handleResume = (operationId: string) => {
    Modal.confirm({
      title: "续跑剩下的文件？",
      content:
        "会先核对目录组有没有被删除或改动；只处理还没完成的条目，已经成功的不会重复移动。",
      okText: "续跑",
      onOk: async () => {
        setUndoing(operationId);
        try {
          const result = await api.resume(operationId);
          if (result.message) {
            message.info(result.message);
          } else {
            message.success("已开始续跑");
            setProgress(await api.progress(operationId));
          }
          await loadHistory();
        } catch (error) {
          message.error(`续跑失败：${(error as Error).message}`);
          throw error;
        } finally {
          setUndoing("");
        }
      },
    });
  };

  if (!namingPresets.length) {
    return <Empty description="还没有命名预设，请检查后端是否正常" />;
  }

  const rowsByEntry = useMemo(() => {
    const map = new Map<string, PreviewRow[]>();
    for (const row of preview?.rows ?? []) {
      const key = row.entry_id || "";
      if (!key) continue;
      const list = map.get(key) ?? [];
      list.push(row);
      map.set(key, list);
    }
    return map;
  }, [preview]);

  const fileById = useMemo(
    () => new Map<string, ParsedFile>((scan?.files ?? []).map((file) => [file.id, file])),
    [scan],
  );

  // 目录树：根节点就是扫描时设定的目标文件夹，多组并排；文件按目标路径挂到文件夹下
  const previewTree = useMemo(() => {
    interface FolderNode {
      key: string;
      name: string;
      folders: Map<string, FolderNode>;
      files: { key: string; name: string; row: PreviewRow }[];
    }
    const normalize = (value: string) => value.replace(/\\/g, "/").replace(/\/+$/, "");
    const make = (key: string, name: string): FolderNode => ({
      key,
      name,
      folders: new Map(),
      files: [],
    });
    const roots = new Map<string, FolderNode>();
    const targets = (scan?.groups ?? [])
      .map((group) => normalize(group.target || ""))
      .filter(Boolean);

    for (const row of preview?.rows ?? []) {
      const target = normalize(row.new_path || "");
      if (!target) continue;
      const owner = targets.find(
        (item) => target === item || target.startsWith(`${item}/`),
      );
      const rootKey = owner ?? (target.slice(0, target.lastIndexOf("/")) || target);
      let root = roots.get(rootKey);
      if (!root) {
        root = make(rootKey, rootKey);
        roots.set(rootKey, root);
      }
      const relative = target.slice(rootKey.length).replace(/^\/+/, "");
      const parts = relative.split("/").filter(Boolean);
      let cursor = root;
      for (let index = 0; index < Math.max(parts.length - 1, 0); index += 1) {
        const part = parts[index];
        let child = cursor.folders.get(part);
        if (!child) {
          child = make(`${cursor.key}/${part}`, part);
          cursor.folders.set(part, child);
        }
        cursor = child;
      }
      const fileName = parts[parts.length - 1] || row.new_name || "（未命名）";
      cursor.files.push({
        key: `${cursor.key}::${row.file_id}::${fileName}`,
        name: fileName,
        row,
      });
    }

    const countFiles = (node: FolderNode): number =>
      node.files.length +
      [...node.folders.values()].reduce((sum, child) => sum + countFiles(child), 0);

    const folderKeys: string[] = [];
    const leafTitle = (row: PreviewRow, name: string) => (
      <Space size={6}>
        <FileOutlined />
        <span className="preview-name">{name}</span>
        {row.status === "error" && <Tag color="red">需处理</Tag>}
        {row.status === "warning" && <Tag color="orange">注意</Tag>}
        {row.action === "skip" && row.status === "ok" && <Tag>跳过</Tag>}
        <Text type="secondary" style={{ fontSize: 12 }}>
          {CATEGORY_LABELS[row.category] ?? row.category}
        </Text>
      </Space>
    );
    const convert = (node: FolderNode): DataNode => {
      folderKeys.push(node.key);
      const folders = [...node.folders.values()].sort((a, b) =>
        a.name.localeCompare(b.name, "zh"),
      );
      const files = [...node.files].sort((a, b) => a.name.localeCompare(b.name, "zh"));
      const children: DataNode[] = [
        ...folders.map(convert),
        ...files.map((file) => ({
          key: file.key,
          isLeaf: true,
          title: leafTitle(file.row, file.name),
        })),
      ];
      return {
        key: node.key,
        title: (
          <Space size={6}>
            <FolderOutlined />
            <span>{node.name}</span>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {countFiles(node)} 个文件
            </Text>
          </Space>
        ),
        children,
      };
    };
    const rootNodes = [...roots.values()].sort((a, b) => a.key.localeCompare(b.key, "zh"));
    return {
      treeData: rootNodes.map(convert),
      folderKeys,
      total: preview?.rows?.length ?? 0,
    };
  }, [preview, scan]);

  useEffect(() => {
    // 新预览默认全部展开；切换视图不会重置用户的展开/收起
    setExpandedKeys(previewTree.folderKeys);
  }, [previewTree]);

  const filteredEntries = useMemo(
    () =>
      previewEntries.filter((entry) => {
        const rows = rowsByEntry.get(entry.entry_id) ?? [];
        const rowStatus = rows.some((row) => row.status === "error")
          ? "error"
          : rows.some((row) => row.status === "warning")
            ? "warning"
            : undefined;
        return entryMatches(entry, filters, {
          file: fileById.get(entry.file_ids[0] ?? ""),
          rowStatus,
          selected: checkedEntries.includes(entry.entry_id),
        });
      }),
    [previewEntries, rowsByEntry, filters, checkedEntries, fileById],
  );

  const groupOptions = useMemo(
    () =>
      (scan?.groups ?? []).map((group) => ({
        value: group.id,
        label: group.name,
      })),
    [scan],
  );

  const filteredSelected = filteredEntries.filter((entry) => checkedEntries.includes(entry.entry_id)).length;

  const previewGroups = useMemo(() => {
    const map = new Map<string, QueueEntry[]>();
    for (const entry of filteredEntries) {
      const key = entry.work_key || entry.group_id || "unknown";
      const list = map.get(key) ?? [];
      list.push(entry);
      map.set(key, list);
    }
    return Array.from(map.entries()).map(([key, entries]) => ({
      key,
      title: entries[0]?.title || "未命名作品",
      year: entries[0]?.year,
      entries: [...entries].sort(
        (left, right) =>
          (left.season ?? 0) - (right.season ?? 0) || (left.episode ?? 0) - (right.episode ?? 0),
      ),
    }));
  }, [filteredEntries]);

  const activeChecked = checkedEntries.filter((id) =>
    previewEntries.some((entry) => entry.entry_id === id),
  );

  const refreshAutoGate = async (runId: string) => {
    const result = await api.gateCheck(runId);
    setAutoGate(result.gate);
  };

  const openAutoExecute = async () => {
    if (!scan) {
      message.warning("请先在「扫描」页扫描真实文件，内置示例不能执行");
      return;
    }
    if (preview?.is_sample) {
      message.warning("当前是内置示例数据，不能自动执行");
      return;
    }
    if (progress && ACTIVE_STATUSES.includes(progress.status)) {
      message.warning("已有执行任务在进行，请等它完成或取消");
      return;
    }
    setAutoOpen(true);
    setAutoLoading(true);
    setAutoGate(null);
    setAutoName("");
    try {
      const current = await api.currentRun();
      if (!current.run) {
        message.error("还没有本次运行记录，请先扫描并生成预览");
        setAutoOpen(false);
        return;
      }
      setAutoRunId(current.run.id);
      const state = await api.autoExecuteState(current.run.id);
      setAutoEnabled(state.enabled);
      setAutoRisk(state.risk);
      await refreshAutoGate(current.run.id);
    } catch (error) {
      message.error(`读取门槛结果失败：${(error as Error).message}`);
      setAutoOpen(false);
    } finally {
      setAutoLoading(false);
    }
  };

  const gateFix = (checkId: string) => {
    setAutoOpen(false);
    if (checkId === "unassigned_clear" || checkId === "season_clear") {
      onGoMatch();
      return;
    }
    document.getElementById("sec-preview")?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const confirmAutoExecute = async () => {
    if (!autoRunId || !scan || !draft) return;
    const name = autoName.trim();
    if (!name) {
      message.warning("请填写确认人");
      return;
    }
    const selected = previewEntries.filter((entry) => checkedEntries.includes(entry.entry_id));
    if (!selected.length) {
      message.warning("请先勾选要执行的条目");
      return;
    }
    setAutoSubmitting(true);
    try {
      const auth = await api.authorizeAutoExecute(autoRunId, name);
      const authorizationId = auth.authorization?.id;
      if (!authorizationId) throw new Error("没有拿到授权编号");
      const selectedFileIds = selected.flatMap((entry) => (entry.is_folder ? [] : entry.file_ids));
      const selectedFolderIds = selected
        .filter((entry) => entry.is_folder)
        .map((entry) => entry.folder_key || "");
      const result = await api.autoExecute(autoRunId, {
        authorization_id: authorizationId,
        scan_id: scan.scan_id,
        naming_preset_id: draft.id,
        templates: draft.templates,
        file_ids: selectedFileIds,
        folder_ids: selectedFolderIds,
        confirm_risks: true,
      });
      if (result.auto_execute?.started) {
        message.success("已授权并开始自动执行；这条授权已经使用，不能再用。");
        setAutoOpen(false);
        setAutoName("");
        if (result.operation_id) setProgress(await api.progress(result.operation_id));
        await loadHistory();
      } else {
        message.warning(
          result.auto_execute?.message || result.message || "执行没有启动，授权未被使用",
        );
        await refreshAutoGate(autoRunId);
      }
    } catch (error) {
      message.error(`自动执行失败：${(error as Error).message}`);
      await refreshAutoGate(autoRunId).catch(() => undefined);
    } finally {
      setAutoSubmitting(false);
    }
  };

  const persistSelection = (ids: string[]) => {
    if (scan) void api.saveSelection(scan.scan_id, "preview", ids).catch(() => undefined);
  };

  const toggleEntry = (entryId: string, on: boolean) => {
    const next = on ? [...checkedEntries, entryId] : checkedEntries.filter((id) => id !== entryId);
    setCheckedEntries(next);
    persistSelection(next);
  };

  const toggleMany = (entryIds: string[], on: boolean) => {
    const rest = checkedEntries.filter((id) => !entryIds.includes(id));
    const next = on ? [...rest, ...entryIds] : rest;
    setCheckedEntries(next);
    persistSelection(next);
  };

  const previewColumns: ColumnsType<QueueEntry> = [
    {
      title: "",
      width: 40,
      render: (_, entry) => (
        <Checkbox
          checked={activeChecked.includes(entry.entry_id)}
          onChange={(event) => toggleEntry(entry.entry_id, event.target.checked)}
        />
      ),
    },
    {
      title: "条目",
      width: 260,
      render: (_, entry) => (
        <div>
          <div className="preview-name">
            {entry.is_folder ? "[素材文件夹] " : ""}
            {entry.filename}
          </div>
          <div className="path-text">{entry.rel_path}</div>
        </div>
      ),
    },
    {
      title: "新名称 / 目标路径",
      render: (_, entry) => {
        const rows = rowsByEntry.get(entry.entry_id) ?? [];
        const main = rows.find((row) => row.category === "video") ?? rows[0];
        if (!main) {
          return <Text type="warning">没有预览结果（可能缺少作品信息）</Text>;
        }
        return (
          <div>
            <div className="preview-name" style={{ opacity: main.action === "skip" ? 0.55 : 1 }}>
              {main.new_name || "-"}
              {main.action === "skip" && "（不改名）"}
            </div>
            {main.new_path && main.new_path !== main.original_path && (
              <div className="path-text">{main.new_path}</div>
            )}
          </div>
        );
      },
    },
    {
      title: "字幕",
      width: 92,
      render: (_, entry) => {
        const rows = rowsByEntry.get(entry.entry_id) ?? [];
        const count = rows.filter((row) => row.category === "subtitle").length;
        return count ? <Tag color="cyan">{count} 个（跟随）</Tag> : <Text type="secondary">-</Text>;
      },
    },
    {
      title: "状态",
      width: 100,
      render: (_, entry) => {
        const rows = rowsByEntry.get(entry.entry_id) ?? [];
        const main = rows.find((row) => row.category === "video") ?? rows[0];
        const meta = STATUS_TAGS[main?.status ?? "error"] ?? STATUS_TAGS.error;
        return <Tag color={meta.color}>{meta.label}</Tag>;
      },
    },
    {
      title: "提示",
      render: (_, entry) => {
        const rows = rowsByEntry.get(entry.entry_id) ?? [];
        const main = rows.find((row) => row.category === "video") ?? rows[0];
        const messages = [...(main?.messages ?? [])];
        if (entry.result?.message && entry.result.status !== "done") {
          messages.push(entry.result.message);
        }
        return messages.length ? (
          <Text type="secondary">{messages.join("；")}</Text>
        ) : (
          <Text type="secondary">-</Text>
        );
      },
    },
  ];

  return (
    <PageSections
      items={[
        { id: "sec-preset", label: "预设与模板" },
        { id: "sec-preview", label: "预览" },
        ...(progress ? [{ id: "sec-progress", label: "执行进度" }] : []),
        { id: "sec-history", label: "执行记录" },
      ]}
      primaryAction={{
        label: "执行",
        hint: `${activeChecked.length}/${previewEntries.length}`,
        disabled:
          !scan ||
          Boolean(preview?.is_sample) ||
          Boolean(progress && ACTIVE_STATUSES.includes(progress.status)) ||
          (queue ? activeChecked.length === 0 : false),
        loading: executing,
        onClick: handleExecute,
      }}
    >
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Card id="sec-preset" data-section>
        <Row gutter={16} align="middle">
          <Col flex="auto">
            <Space wrap>
              <Text type="secondary">命名预设</Text>
              <Select
                style={{ minWidth: 240 }}
                value={draft?.id}
                onChange={onNamingPresetChange}
                options={namingPresets.map((item) => ({
                  value: item.id,
                  label: `${item.name}${item.is_default ? "（默认）" : ""}`,
                }))}
              />
              <Input
                style={{ minWidth: 200 }}
                value={draft?.name ?? ""}
                onChange={(event) =>
                  setDraft((previous) => (previous ? { ...previous, name: event.target.value } : previous))
                }
                placeholder="命名预设名称"
              />
              {draft?.is_default && <Tag color="gold">默认命名预设</Tag>}
              {namingDirtyTotal > 0 ? (
                <Tag color="gold">已修改（命名，{namingDirtyTotal} 项）</Tag>
              ) : (
                <Tag color="green">和预设一致</Tag>
              )}
            </Space>
            <div style={{ marginTop: 4 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                命名预设只管模板和命名选项。识别、AI、执行这些设置在工作流预设里选，扫描页顶部可以切换。
              </Text>
            </div>
          </Col>
          <Col>
            <Space wrap>
              <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={handleSave}>
                保存
              </Button>
              <Button onClick={() => setSaveAsOpen(true)}>另存为</Button>
              <Button icon={<CopyOutlined />} onClick={handleDuplicate}>
                复制
              </Button>
              <Tooltip title="扫描后默认使用该预设">
                <Button icon={<StarOutlined />} onClick={handleSetDefault} disabled={draft?.is_default}>
                  设为默认
                </Button>
              </Tooltip>
              <Button icon={<DownloadOutlined />} onClick={handleExport}>
                导出
              </Button>
              <Button icon={<UploadOutlined />} onClick={() => importRef.current?.click()}>
                导入
              </Button>
              <Button danger icon={<DeleteOutlined />} onClick={handleDelete}>
                删除
              </Button>
            </Space>
          </Col>
        </Row>
      </Card>

      <Row gutter={16}>
        <Col xs={24} lg={15}>
          <Card
            title="模板编辑"
            extra={
              <Segmented
                value={kind}
                onChange={(value) => setKind(value as Kind)}
                options={KIND_TABS.map((item) => ({ label: item.label, value: item.key }))}
              />
            }
          >
            {currentTemplate?.legacy_dir && (
              <Alert
                type="info"
                showIcon
                style={{ marginBottom: 12 }}
                message="这个预设还带着旧版的目录写法"
                description={`旧目录部分：${currentTemplate.legacy_dir}。现在已经改为按规范自动生成作品文件夹和季目录，保存预设后旧目录部分会被丢弃。`}
              />
            )}
            <textarea
              ref={editorRef}
              className="template-editor"
              value={currentTemplate?.pattern ?? ""}
              onChange={(event) => patchTemplate(kind, { pattern: event.target.value })}
              spellCheck={false}
              placeholder="例如 {series} - S{season:02}E{episode:02}"
            />
            <div style={{ marginTop: 8 }}>
              <Text type="secondary">
                这里只编辑文件名；文件夹结构由系统自动生成。
                支持 {"{变量}"} 与 {"{变量:02}"} 补零，{"{{ }}"} 可转义；空值会自动折叠分隔符。
              </Text>
            </div>
            {validation.errors.length > 0 && (
              <Alert
                type="error"
                showIcon
                style={{ marginTop: 12 }}
                message="模板存在问题"
                description={validation.errors.join("；")}
              />
            )}
            {validation.errors.length === 0 && validation.warnings.length > 0 && (
              <Alert
                type="warning"
                showIcon
                style={{ marginTop: 12 }}
                message={validation.warnings.join("；")}
              />
            )}

            <Divider style={{ margin: "16px 0 4px" }}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                命名选项（随命名预设保存）
              </Text>
            </Divider>
            <Space direction="vertical" size={10} style={{ width: "100%" }}>
              <Space wrap>
                <Text type="secondary" style={{ width: 170 }}>
                  特典目录叫什么（Season 00）
                </Text>
                <Input
                  key={`specials-${String(configValues["naming.specials_dir"])}`}
                  style={{ width: 220 }}
                  defaultValue={String(configValues["naming.specials_dir"] ?? "Season 00")}
                  onBlur={(event) =>
                    updateConfig({ "naming.specials_dir": event.target.value })
                  }
                  onPressEnter={(event) => (event.target as HTMLInputElement).blur()}
                />
                <Text type="secondary" style={{ fontSize: 12 }}>
                  默认 Season 00，主流媒体库推荐
                </Text>
              </Space>
              <Checkbox
                checked={Boolean(configValues["naming.preserve_structure"])}
                onChange={(event) =>
                  updateConfig({ "naming.preserve_structure": event.target.checked })
                }
              >
                保留原目录结构（只重命名文件，不生成作品和季目录）
              </Checkbox>
              <Space wrap>
                <Text type="secondary" style={{ width: 170 }}>
                  电影目录布局
                </Text>
                <Select
                  style={{ width: 220 }}
                  value={String(configValues["naming.movie_layout"] ?? "folder")}
                  onChange={(value) => updateConfig({ "naming.movie_layout": value })}
                  options={[
                    { value: "folder", label: "放在作品文件夹内" },
                    { value: "special", label: "放进特典季目录" },
                  ]}
                />
              </Space>
              <Space wrap>
                <Text type="secondary" style={{ width: 170 }}>
                  特典视频
                </Text>
                <Select
                  style={{ width: 360 }}
                  value={String(configValues["execution.special_video_policy"] ?? "root")}
                  onChange={(value) =>
                    updateConfig({ "execution.special_video_policy": value })
                  }
                  options={[
                    { value: "root", label: "整目录带走（内部不改名）" },
                    { value: "season", label: "进 Season 00 按特典模板改名" },
                  ]}
                />
              </Space>

              <Collapse
                ghost
                size="small"
                items={[
                  {
                    key: "more-naming",
                    label: <Text type="secondary">更多命名选项 ▾</Text>,
                    children: (
                      <Space direction="vertical" size={8} style={{ width: "100%" }}>
                        <Space wrap>
                          <Text type="secondary" style={{ width: 170 }}>
                            字幕处理方式
                          </Text>
                          <Select
                            style={{ width: 220 }}
                            value={String(configValues["naming.subtitle_mode"] ?? "follow")}
                            onChange={(value) =>
                              updateConfig({ "naming.subtitle_mode": value })
                            }
                            options={[
                              { value: "follow", label: "跟随视频重命名（保留语言后缀）" },
                              { value: "keep", label: "保持原字幕文件名" },
                            ]}
                          />
                        </Space>
                        <Space wrap>
                          <Text type="secondary" style={{ width: 170 }}>
                            自动补全扩展名
                          </Text>
                          <Checkbox
                            checked={Boolean(configValues["naming.auto_append_ext"])}
                            onChange={(event) =>
                              updateConfig({ "naming.auto_append_ext": event.target.checked })
                            }
                          >
                            模板没写 {"{ext}"} 时自动补上原扩展名
                          </Checkbox>
                        </Space>
                        <Space wrap>
                          <Text type="secondary" style={{ width: 170 }}>
                            文件名最大字节数
                          </Text>
                          <InputNumber
                            key={`bytes-${String(configValues["naming.max_filename_bytes"])}`}
                            min={32}
                            max={255}
                            defaultValue={Number(configValues["naming.max_filename_bytes"] ?? 240)}
                            onBlur={(event) => {
                              const value = Number((event.target as HTMLInputElement).value);
                              if (Number.isFinite(value) && value > 0) {
                                updateConfig({ "naming.max_filename_bytes": value });
                              }
                            }}
                          />
                        </Space>
                        <Space wrap>
                          <Text type="secondary" style={{ width: 170 }}>
                            强制移除字符
                          </Text>
                          <Input
                            key={`forbidden-${String(configValues["naming.forbidden_chars"])}`}
                            style={{ width: 260 }}
                            defaultValue={String(configValues["naming.forbidden_chars"] ?? "")}
                            onBlur={(event) =>
                              updateConfig({ "naming.forbidden_chars": event.target.value })
                            }
                            onPressEnter={(event) => (event.target as HTMLInputElement).blur()}
                          />
                          <Text type="secondary" style={{ fontSize: 12 }}>
                            默认 &lt;&gt;:&quot;/\\|?*
                          </Text>
                        </Space>
                        <Space wrap>
                          <Text type="secondary" style={{ width: 170 }}>
                            撞名素材文件夹命名格式
                          </Text>
                          <Input
                            key={`suffix-${String(configValues["naming.special_folder_suffix"])}`}
                            style={{ width: 260 }}
                            defaultValue={String(
                              configValues["naming.special_folder_suffix"] ?? "{name} {source_season}",
                            )}
                            onBlur={(event) =>
                              updateConfig({ "naming.special_folder_suffix": event.target.value })
                            }
                            onPressEnter={(event) => (event.target as HTMLInputElement).blur()}
                          />
                        </Space>
                        <Space wrap>
                          <Text type="secondary" style={{ width: 170 }}>
                            默认季号
                          </Text>
                          <InputNumber
                            key={`season-${String(configValues["parse.default_season"])}`}
                            min={0}
                            max={99}
                            defaultValue={Number(configValues["parse.default_season"] ?? 1)}
                            onBlur={(event) => {
                              const value = Number((event.target as HTMLInputElement).value);
                              if (Number.isFinite(value) && value >= 0) {
                                updateConfig({ "parse.default_season": value });
                              }
                            }}
                          />
                          <Text type="secondary" style={{ fontSize: 12 }}>
                            认不出季信息时用这个
                          </Text>
                        </Space>
                        <Space wrap>
                          <Text type="secondary" style={{ width: 170 }}>
                            特典归入第 0 季
                          </Text>
                          <Checkbox
                            checked={Boolean(configValues["parse.special_season_zero"])}
                            onChange={(event) =>
                              updateConfig({ "parse.special_season_zero": event.target.checked })
                            }
                          >
                            特典按 Season 0 处理，便于媒体库识别
                          </Checkbox>
                        </Space>
                        <Space wrap>
                          <Text type="secondary" style={{ width: 170 }}>
                            OVA 处理方式
                          </Text>
                          <Select
                            style={{ width: 260 }}
                            value={String(configValues["parse.ova_policy"] ?? "move")}
                            onChange={(value) => updateConfig({ "parse.ova_policy": value })}
                            options={[
                              { value: "move", label: "归到 Season 00 随路径模板移动" },
                              { value: "ignore", label: "原地跳过并写忽略标记" },
                            ]}
                          />
                        </Space>
                        <Space wrap>
                          <Text type="secondary" style={{ width: 170 }}>
                            多季同名素材文件夹
                          </Text>
                          <Select
                            style={{ width: 260 }}
                            value={String(
                              configValues["execution.special_folder_conflict"] ?? "season",
                            )}
                            onChange={(value) =>
                              updateConfig({ "execution.special_folder_conflict": value })
                            }
                            options={[
                              { value: "season", label: "加季号区分（推荐）" },
                              { value: "merge", label: "合并进同一个文件夹" },
                            ]}
                          />
                        </Space>
                      </Space>
                    ),
                  },
                ]}
              />

              <Text type="secondary" style={{ fontSize: 12 }}>
                {Boolean(configValues["naming.preserve_structure"])
                  ? "当前结构：目标目录 / 源目录的相对结构 / 新文件名"
                  : `当前结构：目标目录 / 作品 (年份) / Season xx（特典用 ${String(
                      configValues["naming.specials_dir"] ?? "Season 00",
                    )}）`}
              </Text>
              <Text type="secondary" style={{ fontSize: 12 }}>
                这里的改动会立刻生效；点上方「保存」后会连同当前值写回这份命名预设，
                「和预设一致 / 已修改」只算模板和这些命名选项，不算工作流设置。
              </Text>
            </Space>
          </Card>
        </Col>
        <Col xs={24} lg={9}>
          <Card title="可用变量" extra={<Text type="secondary">点击插入光标位置</Text>}>
            {groupedFields.map((group) => (
              <div key={group.key} style={{ marginBottom: 12 }}>
                <Divider orientation="left" orientationMargin={0} style={{ margin: "8px 0" }}>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {group.label}
                  </Text>
                </Divider>
                <div className="variable-panel">
                  {group.items.map((field) => (
                    <Tooltip key={field.key} title={`${field.description}（示例：${field.example}）`}>
                      <button className="variable-chip" onClick={() => insertVariable(field.key)}>
                        {`{${field.key}}`}
                      </button>
                    </Tooltip>
                  ))}
                </div>
              </div>
            ))}
          </Card>
        </Col>
      </Row>

      <Card
        id="sec-preview"
        data-section
        title="预览"
        extra={
          <Space wrap>
            {preview?.is_sample && <Tag>内置示例数据</Tag>}
            {!preview?.is_sample && scan && <Tag color="blue">当前扫描</Tag>}
            {preview && (
              <Text type="secondary">
                可执行 {preview.summary.ok} · 需注意 {preview.summary.warning} · 冲突 {preview.summary.error} · 跳过{" "}
                {preview.summary.skip}
              </Text>
            )}
            {!scan && (
              <Button type="link" onClick={onGoScan}>
                去扫描真实文件
              </Button>
            )}
            <Tooltip title="先看四项门槛，全绿才能授权；每次授权只能用一次">
              <Button icon={<SafetyCertificateOutlined />} onClick={() => void openAutoExecute()}>
                自动执行（需授权）
              </Button>
            </Tooltip>
          </Space>
        }
      >
        <Space wrap style={{ marginBottom: 12 }} align="center">
          <Segmented
            value={viewMode}
            onChange={(value) => setViewMode(value as "tree" | "list")}
            options={[
              { label: "目录树", value: "tree" },
              { label: "文件对比", value: "list" },
            ]}
          />
          {viewMode === "list" ? (
            <>
              <Text type="secondary">
                本页只包含从「匹配作品」带出来的条目。已选（筛选后）{filteredSelected} / 筛选结果{" "}
                {filteredEntries.length} / 总已选 {activeChecked.length}
              </Text>
              <Button size="small" onClick={() => toggleMany(previewEntries.map((entry) => entry.entry_id), true)}>
                全选本页
              </Button>
              <Button size="small" onClick={() => toggleMany(previewEntries.map((entry) => entry.entry_id), false)}>
                全不选
              </Button>
            </>
          ) : (
            <Text type="secondary">
              共 {previewTree.total} 个文件；根节点是扫描时设定的目标文件夹，默认全部展开。
            </Text>
          )}
        </Space>

        {preview?.duplicates && preview.duplicates.length > 0 && (
          <Card
            size="small"
            title={`重复集提示（${preview.duplicates.length} 组）`}
            style={{ marginBottom: 12, borderColor: "#ff4d4f" }}
          >
            <Space direction="vertical" size={12} style={{ width: "100%" }}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                同一个作品、同一季、同一集出现多个文件。勾选后选择「排除勾选的」或「只保留勾选的」；
                如果只是同集多版本想分别保留，选「全部保留」并在模板里加入能区分的变量（例如 {"{version}、{resolution}"}）。
                未处理时这组会阻止执行，不会自动当成两集，也不会静默覆盖。
              </Text>
              {preview.duplicates.map((group) => {
                const checked = duplicateChecked[group.key] ?? [];
                return (
                  <Card
                    key={group.key}
                    size="small"
                    type="inner"
                    title={
                      <Space wrap>
                        <Text strong>
                          {group.title}
                          {group.year ? `（${group.year}）` : ""}
                        </Text>
                        <Tag color="red">
                          S{String(group.season).padStart(2, "0")}E
                          {String(group.episode).padStart(2, "0")}
                        </Tag>
                        <Tag color={group.kind === "parse_error" ? "orange" : "volcano"}>
                          {group.kind_label}
                        </Tag>
                        {group.decision ? (
                          <Tag color="green">
                            已选择：
                            {group.decision.action === "keep_all"
                              ? "全部保留"
                              : group.decision.action === "exclude"
                                ? "排除勾选项"
                                : "只保留勾选项"}
                          </Tag>
                        ) : (
                          <Tag color="red">未处理（会阻止执行）</Tag>
                        )}
                      </Space>
                    }
                    extra={
                      group.decision && (
                        <Button size="small" onClick={() => decideDuplicate(group.key, "reset")}>
                          重新选择
                        </Button>
                      )
                    }
                  >
                    <Space direction="vertical" size={8} style={{ width: "100%" }}>
                      <Text type="secondary">{group.reason}</Text>
                      {(group.kind === "multi_version" || group.kind === "multi_source") && (
                        <Alert
                          type="info"
                          showIcon
                          message={
                            group.kind === "multi_version"
                              ? "这可能是同一集的不同版本（例如画质不同），建议在正片模板里加入 {resolution} 或 {group}，让它们生成不同的名字。"
                              : "这些文件可能来自不同来源或不同发布组，建议在正片模板里加入 {group} 或 {resolution} 来区分。"
                          }
                          description="点下面的按钮会把变量加到正片模板末尾（只改当前草稿，不会自动保存）并立即重新生成预览。如果预览还提示「重复集」，再点这组里的「全部保留（我自己在模板里区分）」让它放行。"
                          action={
                            <Space direction="vertical">
                              <Button size="small" onClick={() => addTemplateVariable(group, "resolution")}>
                                加入 {"{resolution}"}
                              </Button>
                              <Button size="small" onClick={() => addTemplateVariable(group, "group")}>
                                加入 {"{group}"}
                              </Button>
                            </Space>
                          }
                        />
                      )}
                      {group.files.map((file) => (
                        <Checkbox
                          key={file.file_id}
                          checked={checked.includes(file.file_id) || file.excluded}
                          disabled={Boolean(group.decision) && file.excluded}
                          onChange={(event) => {
                            const next = event.target.checked
                              ? [...checked, file.file_id]
                              : checked.filter((id) => id !== file.file_id);
                            setDuplicateChecked((prev) => ({ ...prev, [group.key]: next }));
                          }}
                        >
                          <span className="preview-name">{file.filename}</span>
                          <span className="path-text" style={{ marginLeft: 8 }}>
                            {file.rel_path}
                          </span>
                          {file.version && <Tag style={{ marginLeft: 8 }}>v{file.version}</Tag>}
                          {file.resolution && <Tag style={{ marginLeft: 8 }}>{file.resolution}</Tag>}
                          {file.excluded && <Tag color="default" style={{ marginLeft: 8 }}>已排除</Tag>}
                        </Checkbox>
                      ))}
                      <Space wrap>
                        <Button
                          size="small"
                          disabled={Boolean(group.decision)}
                          onClick={() => decideDuplicate(group.key, "exclude")}
                        >
                          排除勾选的（其余保留）
                        </Button>
                        <Button
                          size="small"
                          disabled={Boolean(group.decision)}
                          onClick={() => decideDuplicate(group.key, "keep")}
                        >
                          只保留勾选的（其余排除，可用于保留修正版 v2）
                        </Button>
                        <Button
                          size="small"
                          disabled={Boolean(group.decision)}
                          onClick={() => decideDuplicate(group.key, "keep_all")}
                        >
                          全部保留（我自己在模板里区分）
                        </Button>
                      </Space>
                    </Space>
                  </Card>
                );
              })}
            </Space>
          </Card>
        )}

        {viewMode === "list" ? (
          <>
        <ListFilterBar
          value={filters}
          onChange={setFilters}
          groupOptions={groupOptions}
          selectedCount={filteredSelected}
          totalCount={filteredEntries.length}
        />
        {previewEntries.length === 0 ? (
          <Empty description="本页还没有条目，请先在「匹配作品」页勾选作品并点「下一步：带出」" />
        ) : filteredEntries.length === 0 ? (
          <Empty description="没有符合筛选条件的条目" />
        ) : (
          <Collapse
            defaultActiveKey={previewGroups.map((group) => group.key)}
            items={previewGroups.map((group) => {
              const ids = group.entries.map((entry) => entry.entry_id);
              const picked = ids.filter((id) => activeChecked.includes(id)).length;
              return {
                key: group.key,
                label: (
                  <Space>
                    <Checkbox
                      checked={picked === ids.length}
                      indeterminate={picked > 0 && picked < ids.length}
                      onClick={(event) => event.stopPropagation()}
                      onChange={(event) => toggleMany(ids, event.target.checked)}
                    />
                    <Text strong>{group.title}</Text>
                    {group.year && <Tag color="blue">{group.year}</Tag>}
                    <Tag>{ids.length} 个条目</Tag>
                    {picked > 0 && <Tag color="green">已选 {picked}</Tag>}
                  </Space>
                ),
                children: (
                  <Table
                    rowKey="entry_id"
                    size="small"
                    loading={loading}
                    columns={previewColumns}
                    dataSource={group.entries}
                    pagination={{ pageSize: 12, showSizeChanger: true, showTotal: (total) => `共 ${total} 条` }}
                    scroll={{ x: 900 }}
                    expandable={{
                      expandedRowRender: (entry) => {
                        const rows = rowsByEntry.get(entry.entry_id) ?? [];
                        if (!rows.length) {
                          return <Text type="secondary">没有预览明细。</Text>;
                        }
                        return (
                          <Space direction="vertical" size={2} style={{ width: "100%" }}>
                            {rows.map((row) => (
                              <Text
                                key={`${row.file_id}-${row.original_path}`}
                                type="secondary"
                                style={{ fontSize: 12 }}
                              >
                                [{CATEGORY_LABELS[row.category] ?? row.category}] {basename(row.original_path)} →{" "}
                                {row.new_name || "-"}
                                {row.messages.length ? `（${row.messages.join("；")}）` : ""}
                              </Text>
                            ))}
                          </Space>
                        );
                      },
                    }}
                    locale={{ emptyText: <Empty description="没有预览结果" /> }}
                  />
                ),
              };
            })}
          />
        )}
          </>
        ) : (
          <div>
            <Space wrap style={{ marginBottom: 10 }}>
              <Button size="small" onClick={() => setExpandedKeys(previewTree.folderKeys)}>
                全部展开
              </Button>
              <Button size="small" onClick={() => setExpandedKeys([])}>
                全部收起
              </Button>
            </Space>
            {previewEntries.length === 0 || previewTree.total === 0 ? (
              <Empty description="本页还没有条目，请先在「匹配作品」页勾选作品并点「下一步：带出」" />
            ) : (
              <Tree
                className="preview-tree"
                treeData={previewTree.treeData}
                expandedKeys={expandedKeys}
                onExpand={(keys) => setExpandedKeys(keys as string[])}
                selectable={false}
                blockNode
                showLine
              />
            )}
          </div>
        )}
        {queue && queue.counts.done > 0 && (
          <Alert
            type="success"
            showIcon
            style={{ marginTop: 12 }}
            message={`已完成 ${queue.counts.done} 个条目，已移出本页清单`}
            description="需要回退时，到下面的「执行记录」里点「撤销」。"
          />
        )}
      </Card>

      {progress && (
        <Card
          id="sec-progress"
          data-section
          title={
            <Space>
              <span>执行进度</span>
              <Tag color={OPERATION_STATUS_TAGS[progress.status]?.color}>
                {OPERATION_STATUS_TAGS[progress.status]?.label ?? progress.status}
              </Tag>
            </Space>
          }
          extra={
            <Space wrap>
              {progress.status === "running" && (
                <Button
                  size="small"
                  icon={<PauseCircleOutlined />}
                  loading={undoing === progress.operation_id}
                  onClick={() => handlePause(progress.operation_id)}
                >
                  暂停
                </Button>
              )}
              {progress.status === "paused" && (
                <Button
                  size="small"
                  loading={undoing === progress.operation_id}
                  onClick={() => handleContinue(progress.operation_id)}
                >
                  继续
                </Button>
              )}
              {(progress.status === "running" || progress.status === "paused") && (
                <Button
                  size="small"
                  danger
                  loading={undoing === progress.operation_id}
                  onClick={() => handleCancel(progress.operation_id)}
                >
                  取消
                </Button>
              )}
              {progress.status === "conflict" && (
                <Button
                  size="small"
                  type="primary"
                  loading={conflictLoading}
                  onClick={() => void openConflicts(progress.operation_id)}
                >
                  处理冲突（{progress.conflict}）
                </Button>
              )}
              {["interrupted", "partial", "conflict"].includes(progress.status) && (
                <Button
                  size="small"
                  danger
                  loading={undoing === progress.operation_id}
                  onClick={() => handleUndo(progress.operation_id)}
                >
                  撤销
                </Button>
              )}
              {["interrupted", "partial"].includes(progress.status) && (
                <Button
                  size="small"
                  loading={undoing === progress.operation_id}
                  onClick={() => handleResume(progress.operation_id)}
                >
                  续跑
                </Button>
              )}
              {!ACTIVE_STATUSES.includes(progress.status) && (
                <Button size="small" type="link" onClick={() => setProgress(null)}>
                  收起
                </Button>
              )}
            </Space>
          }
        >
          <Space direction="vertical" size={8} style={{ width: "100%" }}>
            <Progress
              percent={
                progress.total > 0 ? Math.round((progress.processed / progress.total) * 100) : 0
              }
              status={
                progress.failed > 0 || progress.conflict > 0
                  ? "exception"
                  : progress.status === "done"
                    ? "success"
                    : ACTIVE_STATUSES.includes(progress.status)
                      ? "active"
                      : "normal"
              }
            />
            <Space wrap size={16}>
              <Text>
                已完成 {progress.processed} / {progress.total}
              </Text>
              <Text type="secondary">
                成功 {progress.done} · 跳过 {progress.skipped} · 失败 {progress.failed} · 冲突{" "}
                {progress.conflict}
                {progress.merged > 0 ? ` · 合并 ${progress.merged}` : ""}
              </Text>
              <Text type="secondary">
                第 {progress.batch_index} / 共 {progress.batch_count} 批
              </Text>
            </Space>
            <Text type="secondary" ellipsis style={{ maxWidth: "100%" }}>
              当前文件：{progress.current_item || "—"}
            </Text>
            {progress.summary?.resolution && !ACTIVE_STATUSES.includes(progress.status) && (
              <Alert
                type={progress.status === "conflict" ? "warning" : "success"}
                showIcon
                message="冲突处理结果"
                description={`${statsText(progress.summary.resolution)} · 源目录还剩 ${
                  progress.summary.remaining_source ?? 0
                } 个文件`}
              />
            )}
            {!ACTIVE_STATUSES.includes(progress.status) &&
              ["done", "partial", "conflict", "cancelled", "interrupted"].includes(
                progress.status,
              ) && (
                <Alert
                  type={progress.status === "done" ? "success" : "warning"}
                  showIcon
                  message="执行结束，默认不会自动重扫"
                  description="需要确认文件现状时点「核对当前清单」；需要重新读取目录、发现新文件时点「完整重扫」。失败、合并 / 覆盖、取消后系统已经自动核对过一次。"
                  action={
                    <Space direction="vertical">
                      <Button size="small" onClick={() => void recheckAfterExecute()}>
                        核对当前清单
                      </Button>
                      <Button size="small" onClick={() => void onGoScan()}>
                        完整重扫（去扫描页）
                      </Button>
                    </Space>
                  }
                />
              )}
          </Space>
        </Card>
      )}

      <Card
        id="sec-history"
        data-section
        title="执行记录"
        extra={
          <Space>
            <Text type="secondary">每次执行都会留痕，可撤销</Text>
            <Button size="small" onClick={loadHistory}>
              刷新
            </Button>
          </Space>
        }
      >
        {history.length === 0 ? (
          <Empty description="还没有执行记录" />
        ) : (
          <Table
            rowKey="id"
            size="small"
            pagination={false}
            dataSource={history}
            columns={historyColumns(
              undoing,
              handleUndo,
              handleResume,
              handlePause,
              handleContinue,
              handleCancel,
              (id) => void openConflicts(id),
            )}
            expandable={{
              expandedRowRender: (record) => {
                const items = historyItems[record.id];
                if (!items || items === "loading") {
                  return <Text type="secondary">读取明细中…</Text>;
                }
                if (!items.length) {
                  return <Text type="secondary">这条记录没有明细</Text>;
                }
                return (
                  <Table<ExecuteRow>
                    size="small"
                    pagination={false}
                    rowKey={(row) => `${row.seq ?? ""}-${row.original_path}`}
                    dataSource={items}
                    columns={[
                      { title: "文件", dataIndex: "original_path", ellipsis: true },
                      {
                        title: "结果",
                        dataIndex: "status",
                        width: 80,
                        render: (value: string) => <Tag>{value}</Tag>,
                      },
                      {
                        title: "选择",
                        dataIndex: "choice",
                        width: 90,
                        render: (value?: string) => value || "-",
                      },
                      {
                        title: "决策来源",
                        dataIndex: "decision_source",
                        width: 140,
                        render: (value: string, row) => (
                          <Space size={2} wrap>
                            <Tag
                              color={
                                value === "human" ? "blue" : value === "ai" ? "purple" : undefined
                              }
                            >
                              {DECISION_SOURCE_LABELS[value || ""] || "未知"}
                            </Tag>
                            {row.rule_id ? (
                              <Text type="secondary" style={{ fontSize: 11 }}>
                                {row.rule_id}
                              </Text>
                            ) : null}
                          </Space>
                        ),
                      },
                      {
                        title: "决策时间",
                        dataIndex: "decided_at",
                        width: 170,
                        render: (value?: string) =>
                          value
                            ? new Date(value).toLocaleString("zh-CN", { hour12: false })
                            : "-",
                      },
                    ]}
                  />
                );
              },
              onExpand: (expanded, record) => {
                if (expanded) void loadHistoryItems(record.id);
              },
            }}
            scroll={{ x: 900 }}
          />
        )}
      </Card>

      <Modal
        title="另存为新预设"
        open={saveAsOpen}
        onCancel={() => setSaveAsOpen(false)}
        onOk={handleSaveAs}
        okText="保存"
      >
        <Input
          value={saveAsName}
          onChange={(event) => setSaveAsName(event.target.value)}
          placeholder="新预设名称"
          onPressEnter={handleSaveAs}
        />
      </Modal>

      <Modal
        title={conflictView ? `处理冲突（共 ${conflictView.total} 项）` : "处理冲突"}
        open={!!conflictView}
        width={1060}
        maskClosable={false}
        onCancel={() => setConflictView(null)}
        footer={[
          <Button key="later" onClick={() => setConflictView(null)}>
            稍后处理
          </Button>,
          <Button key="go" type="primary" loading={conflictLoading} onClick={submitConflicts}>
            开始处理
          </Button>,
        ]}
      >
        {conflictView && (
          <Space direction="vertical" size={16} style={{ width: "100%" }}>
            <Alert
              type="warning"
              showIcon
              message="先选好处理方式再点「开始处理」。直接关闭不会执行任何覆盖，任务会停在「待处理冲突」。"
              description="同名项指相对位置和名字都相同；文件名按 Windows 习惯不区分大小写。已有作品文件夹、Season 文件夹正常复用，不算冲突。"
            />
            {conflictView.groups.map((group) => {
              const groupFiles = conflictView.files.filter(
                (item) => String(item.group_id ?? "") === group.id,
              );
              const groupFolders = conflictView.folders.filter(
                (item) => String(item.group_id ?? "") === group.id,
              );
              if (!groupFiles.length && !groupFolders.length) return null;
              return (
                <Card
                  key={group.id || "root"}
                  size="small"
                  title={`目录组：${group.name || "未命名"}`}
                >
                  {groupFiles.length > 0 && (
                    <Space direction="vertical" size={8} style={{ width: "100%" }}>
                      <Space wrap>
                        <Text strong>文件冲突（{groupFiles.length}）</Text>
                        <Text type="secondary">
                          批量按钮只作用于本组勾选项；没勾选时作用于本组全部文件冲突
                        </Text>
                      </Space>
                      <Space wrap>
                        {FILE_ACTION_OPTIONS.map((option) => (
                          <Button
                            size="small"
                            key={option.value}
                            onClick={() => applyConflictAction(groupFiles, option.value)}
                          >
                            本组文件统一：{option.label}
                          </Button>
                        ))}
                      </Space>
                      <Table
                        size="small"
                        rowKey="seq"
                        pagination={groupFiles.length > 8 ? { pageSize: 8, size: "small" } : false}
                        dataSource={groupFiles}
                        columns={[
                          {
                            title: "",
                            width: 40,
                            render: (_, item) => (
                              <Checkbox
                                checked={conflictChecked.includes(item.seq)}
                                onChange={(event) =>
                                  toggleConflictCheck(item.seq, event.target.checked)
                                }
                              />
                            ),
                          },
                          {
                            title: "源文件",
                            render: (_, item) => (
                              <div>
                                <div className="preview-name">{basename(item.original_path)}</div>
                                <div className="path-text">{item.original_path}</div>
                              </div>
                            ),
                          },
                          {
                            title: "目标（已存在）",
                            render: (_, item) => <div className="path-text">{item.target_path}</div>,
                          },
                          {
                            title: "处理方式",
                            width: 170,
                            render: (_, item) => (
                              <Select
                                size="small"
                                style={{ width: 156 }}
                                value={conflictChoices[item.seq] ?? "skip"}
                                options={FILE_ACTION_OPTIONS}
                                onChange={(value: ConflictAction) =>
                                  setConflictChoices((prev) => ({ ...prev, [item.seq]: value }))
                                }
                              />
                            ),
                          },
                        ]}
                      />
                    </Space>
                  )}

                  {groupFolders.length > 0 && (
                    <Space
                      direction="vertical"
                      size={8}
                      style={{ width: "100%", marginTop: groupFiles.length ? 12 : 0 }}
                    >
                      <Space wrap>
                        <Text strong>文件夹冲突（{groupFolders.length}）</Text>
                        <Text type="secondary">
                          「整体」会替换整个目标文件夹；「合并」只把内容搬进去，目标文件夹保留
                        </Text>
                      </Space>
                      <Space wrap>
                        {FOLDER_ACTION_OPTIONS.map((option) => (
                          <Button
                            size="small"
                            key={option.value}
                            onClick={() => applyConflictAction(groupFolders, option.value)}
                          >
                            本组文件夹统一：{option.label}
                          </Button>
                        ))}
                      </Space>
                      <Table
                        size="small"
                        rowKey="seq"
                        pagination={
                          groupFolders.length > 8 ? { pageSize: 8, size: "small" } : false
                        }
                        dataSource={groupFolders}
                        columns={[
                          {
                            title: "",
                            width: 40,
                            render: (_, item) => (
                              <Checkbox
                                checked={conflictChecked.includes(item.seq)}
                                onChange={(event) =>
                                  toggleConflictCheck(item.seq, event.target.checked)
                                }
                              />
                            ),
                          },
                          {
                            title: "源文件夹",
                            render: (_, item) => (
                              <div>
                                <div className="preview-name">{basename(item.original_path)}</div>
                                <div className="path-text">{item.original_path}</div>
                              </div>
                            ),
                          },
                          {
                            title: "目标（已存在）",
                            render: (_, item) => <div className="path-text">{item.target_path}</div>,
                          },
                          {
                            title: "处理方式",
                            width: 220,
                            render: (_, item) => (
                              <Select
                                size="small"
                                style={{ width: 206 }}
                                value={conflictChoices[item.seq] ?? "skip"}
                                options={FOLDER_ACTION_OPTIONS}
                                onChange={(value: ConflictAction) =>
                                  setConflictChoices((prev) => ({ ...prev, [item.seq]: value }))
                                }
                              />
                            ),
                          },
                        ]}
                      />
                    </Space>
                  )}
                </Card>
              );
            })}
          </Space>
        )}
      </Modal>

      <Modal
        title="覆盖会永久删除目标内容，撤销无法恢复"
        open={!!overwriteConfirm}
        onCancel={() => setOverwriteConfirm(null)}
        okText="确认覆盖"
        okButtonProps={{ danger: true, loading: conflictLoading }}
        onOk={() => overwriteConfirm && void doResolve(overwriteConfirm.choices, true)}
      >
        {overwriteConfirm && (
          <Space direction="vertical" size={10} style={{ width: "100%" }}>
            <Alert
              type="error"
              showIcon
              message={`本次会永久删除 ${overwriteConfirm.targets.length} 处目标内容`}
              description="删除后即使点「撤销」，这些被删掉的原内容也无法恢复。请确认这些目标确实是不需要的旧文件。"
            />
            {overwriteConfirm.folderOverwrite && (
              <Alert
                type="error"
                showIcon
                message="其中包含「整个文件夹覆盖」"
                description="会删除整个目标文件夹以及它里面的全部内容，只保留你这次搬过去的新文件夹。"
              />
            )}
            <div style={{ maxHeight: 200, overflow: "auto" }}>
              {overwriteConfirm.targets.slice(0, 30).map((item) => (
                <div key={item.seq} className="path-text">
                  {item.target_path}
                </div>
              ))}
              {overwriteConfirm.targets.length > 30 && (
                <div className="path-text">……还有 {overwriteConfirm.targets.length - 30} 处</div>
              )}
            </div>
          </Space>
        )}
      </Modal>

      <Modal
        title="自动执行授权"
        open={autoOpen}
        width={680}
        onCancel={() => setAutoOpen(false)}
        styles={{ body: { maxHeight: "calc(100vh - 260px)", overflowY: "auto", paddingRight: 8 } }}
        footer={
          <Space wrap>
            <Button onClick={() => setAutoOpen(false)}>关闭</Button>
            {autoRunId && (
              <Button loading={autoLoading} onClick={() => void refreshAutoGate(autoRunId).catch(() => undefined)}>
                重新检查
              </Button>
            )}
            {!autoEnabled && (
              <Button
                type="primary"
                onClick={() => {
                  setAutoOpen(false);
                  onGoSettings();
                }}
              >
                去设置里打开开关
              </Button>
            )}
            {autoEnabled && autoGate?.passed && (
              <Button
                type="primary"
                loading={autoSubmitting}
                disabled={!autoName.trim() || !activeChecked.length}
                onClick={() => void confirmAutoExecute()}
              >
                确认授权并自动执行
              </Button>
            )}
          </Space>
        }
      >
        <Space direction="vertical" size={12} style={{ width: "100%" }}>
          {!autoEnabled && (
            <Alert
              type="warning"
              showIcon
              message="自动执行开关未开启（默认关闭）"
              description="请到「设置」页的「自动执行」卡片里打开开关并保存。打开后每次执行仍要单独授权。"
            />
          )}
          <Text type="secondary">
            门槛必须四项全绿才能授权；任何一项未通过都只显示问题，不提供授权按钮，也没有强制跳过。
          </Text>
          {autoLoading && <Text type="secondary">正在检查门槛...</Text>}
          {autoGate && (
            <>
              <Alert
                type={autoGate.passed ? "success" : "error"}
                showIcon
                message={autoGate.passed ? "四项检查全部通过" : autoGate.summary}
                description={`检查时间：${autoGate.checked_at.replace("T", " ").slice(0, 19)}`}
              />
              <Space direction="vertical" size={8} style={{ width: "100%" }}>
                {autoGate.checks.map((check) => (
                  <div
                    key={check.id}
                    style={{
                      border: "1px solid var(--ani-border, #f0f0f0)",
                      borderRadius: 8,
                      padding: "8px 12px",
                    }}
                  >
                    <Space wrap>
                      <Tag color={check.passed ? "green" : "red"}>
                        {check.passed ? "通过" : "未通过"}
                      </Tag>
                      <Text strong>{check.label}</Text>
                      {!check.passed && (
                        <Button size="small" type="link" onClick={() => gateFix(check.id)}>
                          去处理
                        </Button>
                      )}
                    </Space>
                    {!check.passed && (
                      <div style={{ marginTop: 4 }}>
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          {check.reason || "还有未处理的问题"}
                        </Text>
                        {check.issues.slice(0, 3).map((issue) => (
                          <div key={`${issue.kind}-${issue.ref_id}`} className="path-text">
                            {issue.title || issue.ref_id}
                            {issue.reason ? `：${issue.reason}` : ""}
                          </div>
                        ))}
                        {check.issues.length > 3 && (
                          <Text type="secondary" style={{ fontSize: 12 }}>
                            还有 {check.issues.length - 3} 条，去对应页面处理
                          </Text>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </Space>
            </>
          )}
          {autoRisk && (
            <Alert
              type="warning"
              showIcon
              message={`授权前请确认风险（文案版本 ${autoRisk.version}）`}
              description={autoRisk.text}
            />
          )}
          {autoEnabled && autoGate?.passed && (
            <>
              <div>
                <div style={{ fontWeight: 500, marginBottom: 4 }}>确认人（会写进授权记录）</div>
                <Input
                  value={autoName}
                  maxLength={40}
                  placeholder="例如：家里的 NAS 管理员"
                  onChange={(event) => setAutoName(event.target.value)}
                />
              </div>
              <Alert
                type="info"
                showIcon
                message="本次授权只能使用一次"
                description="点「确认授权并自动执行」后立即开始执行，这条授权马上失效；以后再执行必须重新授权。"
              />
              <Text type="secondary">
                当前已勾选 {activeChecked.length} 个条目。
              </Text>
            </>
          )}
        </Space>
      </Modal>

      <input
        ref={importRef}
        type="file"
        accept="application/json"
        style={{ display: "none" }}
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) handleImport(file);
          event.target.value = "";
        }}
      />
    </Space>
    </PageSections>
  );
}
