import { useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  Divider,
  Empty,
  Input,
  Modal,
  Progress,
  Row,
  Segmented,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import {
  CopyOutlined,
  DeleteOutlined,
  DownloadOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  SaveOutlined,
  StarOutlined,
  UploadOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { api } from "../api";
import type {
  Category,
  FieldPayload,
  Kind,
  OperationProgress,
  OperationRecord,
  Preset,
  PreviewResponse,
  PreviewRow,
  ScanPayload,
  TemplateSpec,
} from "../types";
import { CATEGORY_LABELS } from "../types";

const { Text, Title } = Typography;

const CONFLICT_LABELS: Record<string, string> = {
  skip: "跳过",
  overwrite: "覆盖",
  suffix: "自动加序号",
};

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
    title: "操作",
    dataIndex: "id",
    width: 210,
    render: (value: string, record) => {
      if (record.kind !== "execute") return <Text type="secondary">-</Text>;
      const status = record.status;
      const running = status === "running";
      const queued = status === "queued";
      const paused = status === "paused";
      const resumable = status === "interrupted" || status === "partial" || status === "conflict";
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
  presets: Preset[];
  presetId: string;
  onPresetChange: (id: string) => void;
  reloadPresets: (selectId?: string) => Promise<Preset[]>;
  onGoScan: () => void;
}

export default function TemplatePage({
  scan,
  presets,
  presetId,
  onPresetChange,
  reloadPresets,
  onGoScan,
}: Props) {
  const [draft, setDraft] = useState<Preset | null>(null);
  const [kind, setKind] = useState<Kind>("episode");
  const [fields, setFields] = useState<FieldPayload | null>(null);
  const [validation, setValidation] = useState<{ errors: string[]; warnings: string[] }>({
    errors: [],
    warnings: [],
  });
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [configValues, setConfigValues] = useState<Record<string, unknown>>({});
  const [saveAsOpen, setSaveAsOpen] = useState(false);
  const [saveAsName, setSaveAsName] = useState("");
  const [executing, setExecuting] = useState(false);
  const [progress, setProgress] = useState<OperationProgress | null>(null);
  const [history, setHistory] = useState<OperationRecord[]>([]);
  const [undoing, setUndoing] = useState("");
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
  }, []);

  useEffect(() => {
    const current = presets.find((item) => item.id === presetId) ?? presets[0];
    if (current) {
      setDraft(JSON.parse(JSON.stringify(current)) as Preset);
    }
  }, [presetId, presets]);

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
            preset_id: draft.id,
            templates: draft.templates,
          }),
        ]);
        setValidation(checked);
        setPreview(result);
      } catch (error) {
        message.error(`预览失败：${(error as Error).message}`);
      } finally {
        setLoading(false);
      }
    }, 450);
    return () => window.clearTimeout(timer);
  }, [draft, kind, scan?.scan_id, configValues]);

  const updateConfig = async (patch: Record<string, unknown>) => {
    try {
      const result = await api.saveConfig({ ...configValues, ...patch });
      setConfigValues(result.values);
      message.success("目录结构设置已保存");
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
      await api.updatePreset(draft.id, {
        name: draft.name,
        library_profile: draft.library_profile,
        templates: draft.templates,
      });
      await reloadPresets(draft.id);
      message.success("预设已保存（当前工作流设置已一并记录）");
    } catch (error) {
      message.error(`保存失败：${(error as Error).message}`);
    } finally {
      setSaving(false);
    }
  };

  const handleSaveAs = async () => {
    if (!draft) return;
    try {
      const created = await api.createPreset({
        name: saveAsName.trim() || `${draft.name} 副本`,
        library_profile: draft.library_profile,
        templates: draft.templates,
      });
      await reloadPresets(created.id);
      setSaveAsOpen(false);
      setSaveAsName("");
      message.success("已另存为新预设");
    } catch (error) {
      message.error(`另存失败：${(error as Error).message}`);
    }
  };

  const handleDelete = () => {
    if (!draft) return;
    Modal.confirm({
      title: `删除预设「${draft.name}」？`,
      content: "删除后无法恢复，当前扫描结果不受影响。",
      okButtonProps: { danger: true },
      onOk: async () => {
        await api.deletePreset(draft.id);
        await reloadPresets();
        message.success("已删除");
      },
    });
  };

  const handleDuplicate = async () => {
    if (!draft) return;
    try {
      const created = await api.duplicatePreset(draft.id);
      await reloadPresets(created.id);
      message.success("已复制预设");
    } catch (error) {
      message.error((error as Error).message);
    }
  };

  const handleSetDefault = async () => {
    if (!draft) return;
    try {
      await api.setDefaultPreset(draft.id);
      await reloadPresets(draft.id);
      message.success("已设为默认预设");
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
      await reloadPresets();
      message.success(`已导入 ${result.created} 个预设`);
    } catch (error) {
      message.error(`导入失败：${(error as Error).message}`);
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
    const ready = preview?.rows.filter((row) => row.action === "rename" && row.status !== "error") ?? [];
    if (!ready.length) {
      message.warning("当前没有可执行的文件");
      return;
    }
    const policy = String(configValues["execution.conflict_policy"] ?? "skip");
    Modal.confirm({
      title: "确认执行改名 / 移动？",
      content: (
        <div>
          <div>
            将处理 {ready.length} 个文件，冲突策略：{CONFLICT_LABELS[policy] ?? policy}。
          </div>
          <div style={{ marginTop: 6, color: "#888" }}>
            任务会在后台分批跑完，关掉页面也会继续；执行后会写入记录，可在「执行记录」里撤销。
          </div>
        </div>
      ),
      okText: "开始执行",
      onOk: async () => {
        setExecuting(true);
        try {
          const result = await api.execute({
            scan_id: scan.scan_id,
            preset_id: draft.id,
            templates: draft.templates,
          });
          if (result.operation_id) {
            message.success(
              `任务已开始：共 ${result.total ?? 0} 个条目，分 ${result.batch_count ?? 1} 批执行`,
            );
            setProgress(await api.progress(result.operation_id));
          } else {
            message.info(result.message ?? "没有可执行的文件");
          }
          await loadHistory();
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

  if (!presets.length) {
    return <Empty description="还没有预设，请检查后端是否正常" />;
  }

  const columns: ColumnsType<PreviewRow> = [
    {
      title: "类别",
      dataIndex: "category",
      width: 84,
      filters: (Object.keys(CATEGORY_LABELS) as Category[]).map((key) => ({
        text: CATEGORY_LABELS[key],
        value: key,
      })),
      onFilter: (value, record) => record.category === value,
      render: (value: Category) => <Tag>{CATEGORY_LABELS[value] ?? value}</Tag>,
    },
    {
      title: "类型",
      dataIndex: "kind",
      width: 80,
      render: (value: string, record) =>
        record.category === "video" ? (
          <Tag color={KIND_COLORS[value]}>
            {KIND_TABS.find((item) => item.key === value)?.label ?? "未识别"}
          </Tag>
        ) : (
          <Text type="secondary">-</Text>
        ),
    },
    {
      title: "原文件",
      dataIndex: "original_path",
      render: (value: string) => <div className="preview-name">{basename(value)}</div>,
    },
    {
      title: "新名称 / 目标路径",
      dataIndex: "new_name",
      render: (value: string, record) => (
        <div>
          <div className="preview-name" style={{ opacity: record.action === "skip" ? 0.55 : 1 }}>
            {value || "-"}
            {record.action === "skip" && "（不改名）"}
          </div>
          {record.new_path && record.new_path !== record.original_path && (
            <div className="path-text">{record.new_path}</div>
          )}
        </div>
      ),
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 92,
      render: (value: string) => {
        const meta = STATUS_TAGS[value] ?? STATUS_TAGS.error;
        return <Tag color={meta.color}>{meta.label}</Tag>;
      },
    },
    {
      title: "提示",
      dataIndex: "messages",
      render: (values: string[]) =>
        values.length ? <Text type="secondary">{values.join("；")}</Text> : <Text type="secondary">-</Text>,
    },
  ];

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Card>
        <Row gutter={16} align="middle">
          <Col flex="auto">
            <Space wrap>
              <Text type="secondary">预设</Text>
              <Select
                style={{ minWidth: 240 }}
                value={draft?.id}
                onChange={onPresetChange}
                options={presets.map((item) => ({
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
                placeholder="预设名称"
              />
              {draft?.is_default && <Tag color="gold">默认预设</Tag>}
            </Space>
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
                目录结构
              </Text>
            </Divider>
            <Space direction="vertical" size={8} style={{ width: "100%" }}>
              <Checkbox
                checked={Boolean(configValues["naming.preserve_structure"])}
                onChange={(event) =>
                  updateConfig({ "naming.preserve_structure": event.target.checked })
                }
              >
                只重命名文件：不动文件夹、不改变相对位置
              </Checkbox>
              <Space wrap>
                <Text type="secondary">剧场版放在</Text>
                <Select
                  style={{ width: 190 }}
                  value={String(configValues["naming.movie_layout"] ?? "folder")}
                  onChange={(value) => updateConfig({ "naming.movie_layout": value })}
                  options={[
                    { value: "folder", label: "作品文件夹内" },
                    { value: "special", label: "特典季目录内" },
                  ]}
                />
                <Text type="secondary">
                  特典季目录：{String(configValues["naming.specials_dir"] ?? "Season 00")}（在设置页修改）
                </Text>
              </Space>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {Boolean(configValues["naming.preserve_structure"])
                  ? "当前：目标目录 / 源目录的相对结构 / 新文件名"
                  : `当前：目标目录 / 作品 (年份) / Season xx（特典用 ${String(
                      configValues["naming.specials_dir"] ?? "Season 00",
                    )}）`}
              </Text>
              <Text type="secondary" style={{ fontSize: 12 }}>
                字幕处理、冲突处理、空目录清理等设置统一在「设置」页管理。
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
            <Tooltip
              title={
                !scan
                  ? "内置示例数据不能执行，请先扫描真实文件"
                  : preview?.is_sample
                    ? "当前是示例数据"
                    : progress && ACTIVE_STATUSES.includes(progress.status)
                      ? "已有执行任务在进行"
                      : ""
              }
            >
              <Button
                type="primary"
                icon={<PlayCircleOutlined />}
                loading={executing}
                disabled={
                  !scan ||
                  Boolean(preview?.is_sample) ||
                  Boolean(progress && ACTIVE_STATUSES.includes(progress.status))
                }
                onClick={handleExecute}
              >
                执行改名
              </Button>
            </Tooltip>
          </Space>
        }
      >
        <Table
          rowKey="file_id"
          size="small"
          loading={loading}
          columns={columns}
          dataSource={preview?.rows ?? []}
          pagination={{ pageSize: 10, showSizeChanger: true, showTotal: (total) => `共 ${total} 条` }}
          scroll={{ x: 960 }}
          locale={{ emptyText: <Title level={5} type="secondary">输入模板后自动刷新预览</Title> }}
        />
      </Card>

      {progress && (
        <Card
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
              {["interrupted", "partial", "conflict"].includes(progress.status) && (
                <>
                  <Button
                    size="small"
                    loading={undoing === progress.operation_id}
                    onClick={() => handleResume(progress.operation_id)}
                  >
                    续跑
                  </Button>
                  <Button
                    size="small"
                    danger
                    loading={undoing === progress.operation_id}
                    onClick={() => handleUndo(progress.operation_id)}
                  >
                    撤销
                  </Button>
                </>
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
              </Text>
              <Text type="secondary">
                第 {progress.batch_index} / 共 {progress.batch_count} 批
              </Text>
            </Space>
            <Text type="secondary" ellipsis style={{ maxWidth: "100%" }}>
              当前文件：{progress.current_item || "—"}
            </Text>
          </Space>
        </Card>
      )}

      <Card
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
            )}
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
  );
}
