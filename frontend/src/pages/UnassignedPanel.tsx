import { useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Empty,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Tooltip,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from "antd";
import { RobotOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import type {
  AiSpecialResponse,
  AliasRecord,
  AssignmentRecord,
  IdentifyOverview,
  MatchGroup,
  ScanPayload,
  SpecialMemory,
  SpecialSuggestion,
  SuspectedGroup,
  UnassignedFile,
} from "../types";

const { Text } = Typography;

export interface AssignPayload {
  file_ids: string[];
  work_id?: string;
  title?: string;
  year?: number | null;
  remember?: boolean;
  remember_alias?: boolean;
  group_id?: string;
  segment?: string;
}

interface Props {
  scan: ScanPayload;
  overview: IdentifyOverview;
  works: MatchGroup[];
  busy: boolean;
  onAssign: (payload: AssignPayload) => Promise<void>;
  onIgnore: (groupId: string, segment: string) => Promise<void>;
  onConfirm: (workId: string) => Promise<void>;
  onDeleteAssignment: (id: string) => Promise<void>;
  onCreateAlias: (payload: { alias: string; title: string; year?: number | null }) => Promise<void>;
  onUpdateAlias: (
    id: string,
    payload: { alias: string; title: string; year?: number | null },
  ) => Promise<void>;
  onDeleteAlias: (id: string) => Promise<void>;
  onDeleteSpecialMemory: (id: string) => Promise<void>;
  onSpecial: (folderKey: string, action: "assign" | "keep", workId?: string) => Promise<void>;
  onConfirmAll: () => Promise<void>;
  onAiSpecial: () => Promise<AiSpecialResponse>;
  aiSpecialEnabled: boolean;
  aiSpecialHint: string;
}

interface PendingAssign {
  file_ids: string[];
  source: "suspected" | "file";
  remember: boolean;
  group_id?: string;
  segment?: string;
}

export default function UnassignedPanel({
  scan,
  overview,
  works,
  busy,
  onAssign,
  onIgnore,
  onConfirm,
  onDeleteAssignment,
  onCreateAlias,
  onUpdateAlias,
  onDeleteAlias,
  onDeleteSpecialMemory,
  onSpecial,
  onConfirmAll,
  onAiSpecial,
  aiSpecialEnabled,
  aiSpecialHint,
}: Props) {
  const [suspectedKeys, setSuspectedKeys] = useState<React.Key[]>([]);
  const [fileIds, setFileIds] = useState<React.Key[]>([]);
  const [pending, setPending] = useState<PendingAssign | null>(null);
  const [mode, setMode] = useState<"existing" | "new">("new");
  const [existingWorkId, setExistingWorkId] = useState<string>();
  const [newTitle, setNewTitle] = useState("");
  const [newYear, setNewYear] = useState<number | null>(null);
  const [remember, setRemember] = useState(true);
  const [rememberAlias, setRememberAlias] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [aliasOpen, setAliasOpen] = useState(false);
  const [editingAlias, setEditingAlias] = useState<AliasRecord | null>(null);
  const [aliasText, setAliasText] = useState("");
  const [aliasTitle, setAliasTitle] = useState("");
  const [aliasYear, setAliasYear] = useState<number | null>(null);
  const [specialChoice, setSpecialChoice] = useState<Record<string, string>>({});
  const [aiSpecial, setAiSpecial] = useState<AiSpecialResponse | null>(null);
  const [aiSpecialLoading, setAiSpecialLoading] = useState(false);

  const suspectedMap = useMemo(
    () => Object.fromEntries(overview.suspected.map((item) => [item.key, item])),
    [overview.suspected],
  );

  const openAssign = (source: "suspected" | "file") => {
    if (source === "suspected") {
      const selected = suspectedKeys
        .map((key) => suspectedMap[String(key)])
        .filter(Boolean) as SuspectedGroup[];
      if (!selected.length) {
        message.warning("请先勾选疑似作品");
        return;
      }
      const ids = selected.flatMap((item) => item.file_ids);
      setPending({
        file_ids: ids,
        source,
        remember: selected.length === 1,
        group_id: selected.length === 1 ? selected[0].group_id : undefined,
        segment: selected.length === 1 ? selected[0].segment : undefined,
      });
      setNewTitle(selected.length === 1 ? selected[0].title : "");
      setNewYear(selected.length === 1 ? selected[0].year : null);
    } else {
      if (!fileIds.length) {
        message.warning("请先勾选文件");
        return;
      }
      const selected = fileIds
        .map((key) => overview.unassigned.find((item) => item.file_id === String(key)))
        .filter(Boolean) as UnassignedFile[];
      const sameSegment =
        selected.length > 0 &&
        selected.every(
          (item) => item.segment === selected[0].segment && item.group_id === selected[0].group_id,
        );
      setPending({
        file_ids: fileIds.map(String),
        source,
        remember: false,
        group_id: sameSegment ? selected[0].group_id : undefined,
        segment: sameSegment ? selected[0].segment : undefined,
      });
      setNewTitle(selected.length === 1 ? (selected[0].segment || "") : "");
      setNewYear(null);
    }
    setMode("new");
    setExistingWorkId(undefined);
    setRemember(source === "suspected" && suspectedKeys.length === 1);
    setRememberAlias(source === "suspected" && suspectedKeys.length === 1);
  };

  const closeAssign = () => {
    setPending(null);
    setSubmitting(false);
  };

  const submitAssign = async () => {
    if (!pending) return;
    if (mode === "existing" && !existingWorkId) {
      message.warning("请选择要指派到的作品");
      return;
    }
    if (mode === "new" && !newTitle.trim()) {
      message.warning("请填写作品名称");
      return;
    }
    setSubmitting(true);
    try {
      await onAssign({
        file_ids: pending.file_ids,
        work_id: mode === "existing" ? existingWorkId : undefined,
        title: mode === "new" ? newTitle.trim() : undefined,
        year: mode === "new" ? newYear : undefined,
        remember: remember && Boolean(pending.segment),
        remember_alias: rememberAlias && Boolean(pending.segment),
        group_id: pending.group_id,
        segment: pending.segment,
      });
      setSuspectedKeys([]);
      setFileIds([]);
      closeAssign();
      message.success(`已指派 ${pending.file_ids.length} 个文件`);
    } catch (error) {
      message.error(`指派失败：${(error as Error).message}`);
      setSubmitting(false);
    }
  };

  const suspectedColumns: ColumnsType<SuspectedGroup> = [
    {
      title: "建议名称",
      dataIndex: "title",
      render: (value: string, record) => (
        <div>
          <div className="preview-name">{value}</div>
          <Text type="secondary" style={{ fontSize: 12 }}>
            名称段：{record.segment}
          </Text>
          {record.reason && (
            <div>
              <Text type="secondary" style={{ fontSize: 12 }}>
                判断依据：{record.reason}
              </Text>
            </div>
          )}
        </div>
      ),
    },
    {
      title: "文件数",
      dataIndex: "count",
      width: 90,
      render: (value: number) => <Tag color="orange">{value} 个</Tag>,
    },
    {
      title: "示例文件",
      dataIndex: "files",
      render: (value: string[]) => (
        <div>
          {value.slice(0, 3).map((name) => (
            <div key={name}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {name}
              </Text>
            </div>
          ))}
          {value.length > 3 && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              等 {value.length} 个
            </Text>
          )}
        </div>
      ),
    },
    {
      title: "操作",
      width: 100,
      render: (_, record) => (
        <Button
          size="small"
          type="link"
          disabled={busy}
          onClick={() => onIgnore(record.group_id, record.segment)}
        >
          忽略
        </Button>
      ),
    },
  ];

  const fileColumns: ColumnsType<UnassignedFile> = [
    {
      title: "文件名",
      dataIndex: "filename",
      render: (value: string, record) => (
        <div>
          <div className="preview-name">{value}</div>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {record.rel_path}
          </Text>
        </div>
      ),
    },
    {
      title: "原因",
      dataIndex: "reason",
      width: 260,
      render: (value: string) => <Tag>{value}</Tag>,
    },
  ];

  const pendingColumns: ColumnsType<IdentifyOverview["pending"][number]> = [
    {
      title: "作品",
      dataIndex: "title",
      render: (value: string, record) => (
        <div>
          <div className="preview-name">{value}</div>
          <Text type="secondary" style={{ fontSize: 12 }}>
            识别来源：
            {record.source === "assignment"
              ? "记住的规则"
              : record.source === "alias"
                ? "记住的别名"
                : record.source === "manual"
                  ? "手动指派"
                  : "文件名 / 目录自动识别"}
          </Text>
          {record.reason && (
            <div>
              <Text type="secondary" style={{ fontSize: 12 }}>
                判断依据：{record.reason}
              </Text>
            </div>
          )}
        </div>
      ),
    },
    {
      title: "年份",
      dataIndex: "year",
      width: 80,
      render: (value: number | null) => value ?? "-",
    },
    {
      title: "文件",
      dataIndex: "video_count",
      width: 90,
      render: (value: number) => `${value} 个`,
    },
    {
      title: "操作",
      width: 110,
      render: (_, record) => (
        <Button
          size="small"
          type="primary"
          disabled={busy}
          onClick={() => onConfirm(record.work_id)}
        >
          确认无误
        </Button>
      ),
    },
  ];

  const assignmentColumns: ColumnsType<AssignmentRecord> = [
    {
      title: "目录组",
      dataIndex: "group_id",
      width: 180,
      render: (value: string) => <Text code>{value}</Text>,
    },
    {
      title: "名称段",
      dataIndex: "segment",
    },
    {
      title: "作品名",
      dataIndex: "title",
      render: (value: string, record) => (
        <span>
          {value}
          {record.year ? `（${record.year}）` : ""}
        </span>
      ),
    },
    {
      title: "操作",
      width: 90,
      render: (_, record) => (
        <Popconfirm
          title="删除这条记住的规则？"
          description="以后扫描到这个名称段时不再自动套用。"
          okText="删除"
          cancelText="取消"
          onConfirm={() => onDeleteAssignment(record.id)}
        >
          <Button size="small" type="link" danger disabled={busy}>
            删除
          </Button>
        </Popconfirm>
      ),
    },
  ];

  const openAlias = (record?: AliasRecord) => {
    setEditingAlias(record ?? null);
    setAliasText(record?.alias ?? "");
    setAliasTitle(record?.title ?? "");
    setAliasYear(record?.year ?? null);
    setAliasOpen(true);
  };

  const submitAlias = async () => {
    if (!aliasText.trim() || !aliasTitle.trim()) {
      message.warning("写法和作品名都不能为空");
      return;
    }
    setSubmitting(true);
    try {
      const payload = {
        alias: aliasText.trim(),
        title: aliasTitle.trim(),
        year: aliasYear,
      };
      if (editingAlias) await onUpdateAlias(editingAlias.id, payload);
      else await onCreateAlias(payload);
      message.success(editingAlias ? "别名已修改" : "别名已保存，下次扫描遇到相同写法会直接套用");
      setAliasOpen(false);
    } catch (error) {
      message.error(`保存别名失败：${(error as Error).message}`);
    } finally {
      setSubmitting(false);
    }
  };

  const aiPickOf = (folderKey: string) =>
    aiSpecial?.suggestions.find((item) => item.folder_key === folderKey && item.work_id);

  const handleConfirmAll = async () => {
    try {
      await onConfirmAll();
    } catch (error) {
      message.error(`一键确认失败：${(error as Error).message}`);
    }
  };

  const runAiSpecial = async () => {
    if (!aiSpecialEnabled) {
      message.warning(aiSpecialHint || "AI 特典归属判断未启用");
      return;
    }
    setAiSpecialLoading(true);
    try {
      const result = await onAiSpecial();
      setAiSpecial(result);
      const picked = result.suggestions.filter((item) => item.work_id);
      if (picked.length) {
        setSpecialChoice((prev) => {
          const next = { ...prev };
          picked.forEach((item) => {
            next[item.folder_key] = item.work_id;
          });
          return next;
        });
        message.info(`AI 给出 ${picked.length} 条归属建议，已预选；点「确认归属」后才会生效`);
      } else {
        message.warning(result.message || "AI 没能判断出归属，请手动选择");
      }
    } catch (error) {
      message.error(`AI 判断失败：${(error as Error).message}`);
    } finally {
      setAiSpecialLoading(false);
    }
  };

  const confirmSpecial = async (record: SpecialSuggestion) => {
    const workId =
      specialChoice[record.folder_key] ||
      aiPickOf(record.folder_key)?.work_id ||
      record.candidates[0]?.work_id;
    if (!workId) {
      message.warning("这组特典目录没有可用的作品候选");
      return;
    }
    try {
      await onSpecial(record.folder_key, "assign", workId);
      message.success("已确认并记住这个特典目录的归属，下次扫描会自动复用");
    } catch (error) {
      message.error(`确认失败：${(error as Error).message}`);
    }
  };

  const keepSpecial = async (record: SpecialSuggestion) => {
    try {
      await onSpecial(record.folder_key, "keep");
      message.success("已取消归属，特典目录保持原地");
    } catch (error) {
      message.error(`操作失败：${(error as Error).message}`);
    }
  };

  const aliasColumns: ColumnsType<AliasRecord> = [
    {
      title: "别名（另一种写法）",
      dataIndex: "alias",
      render: (value: string) => <span className="preview-name">{value}</span>,
    },
    {
      title: "对应作品",
      dataIndex: "title",
      render: (value: string, record) => (
        <span>
          {value}
          {record.year ? `（${record.year}）` : ""}
        </span>
      ),
    },
    {
      title: "操作",
      width: 130,
      render: (_, record) => (
        <Space size={4}>
          <Button size="small" type="link" disabled={busy} onClick={() => openAlias(record)}>
            修改
          </Button>
          <Popconfirm
            title="删除这条别名？"
            description="以后扫描到这种写法时不再自动套用。"
            okText="删除"
            cancelText="取消"
            onConfirm={() => onDeleteAlias(record.id)}
          >
            <Button size="small" type="link" danger disabled={busy}>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const specialColumns: ColumnsType<SpecialSuggestion> = [
    {
      title: "特典目录",
      dataIndex: "rel_path",
      render: (value: string, record) => (
        <div>
          <div className="preview-name">{record.name}</div>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {value}
          </Text>
        </div>
      ),
    },
    {
      title: "情况说明",
      dataIndex: "reason",
      render: (value: string, record) => (
        <div>
          <div>{value}</div>
          {record.confirmed && (
            <Tag color="green" style={{ marginTop: 4 }}>
              已归属：{record.confirmed.title}
            </Tag>
          )}
        </div>
      ),
    },
    {
      title: "操作",
      width: 300,
      render: (_, record) => {
        if (record.confirmed) {
          return (
            <Button size="small" disabled={busy} onClick={() => keepSpecial(record)}>
              取消归属（保持原地）
            </Button>
          );
        }
        const aiPick = aiPickOf(record.folder_key);
        if (!record.candidates.length && !aiPick) {
          return <Text type="secondary">没有对上的作品，默认原地不动</Text>;
        }
        const options = record.candidates.map((item) => ({
          value: item.work_id,
          label: `${item.title}${item.year ? `（${item.year}）` : ""}`,
        }));
        if (aiPick && !record.candidates.some((item) => item.work_id === aiPick.work_id)) {
          options.push({
            value: aiPick.work_id,
            label: `${aiPick.title || aiPick.work_id}（AI 建议）`,
          });
        }
        return (
          <Space direction="vertical" size={4}>
            <Space wrap>
              <Select
                size="small"
                style={{ width: 200 }}
                value={
                  specialChoice[record.folder_key] ||
                  aiPick?.work_id ||
                  record.candidates[0]?.work_id
                }
                onChange={(value) =>
                  setSpecialChoice((prev) => ({ ...prev, [record.folder_key]: value }))
                }
                options={options}
              />
              <Button size="small" type="primary" disabled={busy} onClick={() => confirmSpecial(record)}>
                确认归属
              </Button>
            </Space>
            {aiPick && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                AI 建议：{aiPick.title || aiPick.work_id}
                {aiPick.reason ? `（${aiPick.reason}）` : ""}
              </Text>
            )}
          </Space>
        );
      },
    },
  ];

  const memoryColumns: ColumnsType<SpecialMemory> = [
    {
      title: "特典目录",
      dataIndex: "rel_path",
      render: (value: string) => <span className="preview-name">{value}</span>,
    },
    {
      title: "记住了归属",
      dataIndex: "title",
      render: (value: string, record) => (
        <span>
          {value}
          {record.year ? `（${record.year}）` : ""}
        </span>
      ),
    },
    {
      title: "操作",
      width: 110,
      render: (_, record) => (
        <Popconfirm
          title="删除这条记录？"
          description="删除后下次扫描会重新询问这个特典目录的归属。"
          okText="删除"
          cancelText="取消"
          onConfirm={() => onDeleteSpecialMemory(record.id)}
        >
          <Button size="small" type="link" danger disabled={busy}>
            删除
          </Button>
        </Popconfirm>
      ),
    },
  ];

  const suspectedSelected = suspectedKeys.length > 0;
  const fileSelected = fileIds.length > 0;
  const hasAny =
    overview.suspected.length > 0 ||
    overview.unassigned.length > 0 ||
    overview.pending.length > 0 ||
    overview.assignments.length > 0 ||
    overview.aliases.length > 0 ||
    overview.special_suggestions.length > 0 ||
    overview.special_memory.length > 0;

  return (
    <Card
      title="未归属区"
      extra={
        <Text type="secondary">
          疑似作品 {overview.summary.suspected} · 单个未归属 {overview.summary.unassigned} · 待确认{" "}
          {overview.summary.pending}
        </Text>
      }
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message="未归属的文件不会被改名或移动。先在这里确认作品归属，再去「模板与预览」执行。"
      />
      {!hasAny ? (
        <Empty description="没有未归属的文件" />
      ) : (
        <Tabs
          items={[
            {
              key: "suspected",
              label: `疑似作品（${overview.suspected.length}）`,
              children: (
                <Space direction="vertical" size={12} style={{ width: "100%" }}>
                  <Space wrap>
                    <Button
                      type="primary"
                      disabled={!suspectedSelected || busy}
                      onClick={() => openAssign("suspected")}
                    >
                      指派选中（{suspectedKeys.length}）
                    </Button>
                    <Text type="secondary">
                      同一目录组里多个文件共用同一段名称，先确认再整组套用。
                    </Text>
                  </Space>
                  <Table
                    rowKey="key"
                    size="small"
                    dataSource={overview.suspected}
                    columns={suspectedColumns}
                    rowSelection={{
                      selectedRowKeys: suspectedKeys,
                      onChange: setSuspectedKeys,
                    }}
                    pagination={false}
                    locale={{ emptyText: "没有疑似作品" }}
                  />
                </Space>
              ),
            },
            {
              key: "files",
              label: `单个未归属文件（${overview.unassigned.length}）`,
              children: (
                <Space direction="vertical" size={12} style={{ width: "100%" }}>
                  <Space wrap>
                    <Button
                      type="primary"
                      disabled={!fileSelected || busy}
                      onClick={() => openAssign("file")}
                    >
                      指派选中（{fileIds.length}）
                    </Button>
                    <Text type="secondary">
                      单文件指派优先于疑似作品归属；多选后可以一次指派给同一个作品。
                    </Text>
                  </Space>
                  <Table
                    rowKey="file_id"
                    size="small"
                    dataSource={overview.unassigned}
                    columns={fileColumns}
                    rowSelection={{ selectedRowKeys: fileIds, onChange: setFileIds }}
                    pagination={{ pageSize: 10 }}
                    locale={{ emptyText: "没有单个未归属文件" }}
                  />
                </Space>
              ),
            },
            {
              key: "pending",
              label: `已指派待确认（${overview.pending.length}）`,
              children: (
                <Space direction="vertical" size={12} style={{ width: "100%" }}>
                  <Space wrap>
                    <Text type="secondary">
                      这些作品是系统按“文件名 + 目录”自动识别的。确认无误后点「确认」，之后就能正常预览和执行。
                    </Text>
                    <Popconfirm
                      title={`确认全部 ${overview.pending.length} 个作品？`}
                      description="确认后这些作品会直接参与预览和执行，请先核对列表。"
                      okText={`全部确认（${overview.pending.length}）`}
                      cancelText="取消"
                      disabled={busy || !overview.pending.length}
                      onConfirm={() => void handleConfirmAll()}
                    >
                      <Button
                        size="small"
                        type="primary"
                        disabled={busy || !overview.pending.length}
                      >
                        全部确认（{overview.pending.length}）
                      </Button>
                    </Popconfirm>
                  </Space>
                  <Table
                    rowKey="work_id"
                    size="small"
                    dataSource={overview.pending}
                    columns={pendingColumns}
                    pagination={false}
                    locale={{ emptyText: "没有待确认的作品" }}
                  />
                </Space>
              ),
            },
            {
              key: "assignments",
              label: `记住的规则（${overview.assignments.length}）`,
              children: (
                <Space direction="vertical" size={12} style={{ width: "100%" }}>
                  <Text type="secondary">
                    按「目录组 + 名称段」保存，下次扫描到相同名称会直接套用。可以随时删除。
                  </Text>
                  <Table
                    rowKey="id"
                    size="small"
                    dataSource={overview.assignments}
                    columns={assignmentColumns}
                    pagination={false}
                    locale={{ emptyText: "还没有记住任何指派规则" }}
                  />
                </Space>
              ),
            },
            {
              key: "aliases",
              label: `别名（${overview.aliases.length}）`,
              children: (
                <Space direction="vertical" size={12} style={{ width: "100%" }}>
                  <Space wrap>
                    <Button type="primary" disabled={busy} onClick={() => openAlias()}>
                      添加别名
                    </Button>
                    <Text type="secondary">
                      同一部作品的其他写法（日文名、罗马音、简称、繁简差异），下次遇到完全相同的写法会自动套用。
                      别名只做精确匹配，不会把两部作品自动合并。
                    </Text>
                  </Space>
                  <Table
                    rowKey="id"
                    size="small"
                    dataSource={overview.aliases}
                    columns={aliasColumns}
                    pagination={false}
                    locale={{ emptyText: "还没有记住任何别名" }}
                  />
                </Space>
              ),
            },
            {
              key: "special",
              label: `特典候选（${overview.special_suggestions.length}）`,
              children: (
                <Space direction="vertical" size={12} style={{ width: "100%" }}>
                  <Space wrap>
                    <Text type="secondary">
                      扫描时跳过的特典目录会在这里给出作品候选。确认后才会移动到对应作品文件夹；
                      没确认的一律保持原地，仍然不能跨目录组移动。
                    </Text>
                    <Tooltip title={aiSpecialHint}>
                      <Button
                        size="small"
                        icon={<RobotOutlined />}
                        loading={aiSpecialLoading}
                        disabled={busy || !aiSpecialEnabled}
                        onClick={() => void runAiSpecial()}
                      >
                        AI 判断归属
                      </Button>
                    </Tooltip>
                  </Space>
                  {aiSpecial && (
                    <Alert
                      type={aiSpecial.suggestions.some((item) => item.work_id) ? "info" : "warning"}
                      showIcon
                      message={
                        aiSpecial.message ||
                        `AI 检查了 ${aiSpecial.considered} 个特典目录，建议仍然需要你确认`
                      }
                    />
                  )}
                  <Table
                    rowKey="folder_key"
                    size="small"
                    dataSource={overview.special_suggestions}
                    columns={specialColumns}
                    pagination={{ pageSize: 10 }}
                    locale={{ emptyText: "没有需要确认的特典目录" }}
                  />
                  <Text type="secondary">
                    已记住的特典归属（{overview.special_memory.length}）：确认过的目录下次扫描 /
                    重扫会直接复用，不用再点一次。
                  </Text>
                  <Table
                    rowKey="id"
                    size="small"
                    dataSource={overview.special_memory}
                    columns={memoryColumns}
                    pagination={{ pageSize: 5 }}
                    locale={{ emptyText: "还没有记住任何特典目录" }}
                  />
                </Space>
              ),
            },
          ]}
        />
      )}

      <Modal
        title="指派作品"
        open={pending !== null}
        onCancel={closeAssign}
        onOk={submitAssign}
        okText="确定指派"
        cancelText="取消"
        confirmLoading={submitting}
        destroyOnClose
      >
        <Space direction="vertical" size={12} style={{ width: "100%" }}>
          <Text type="secondary">共 {pending?.file_ids.length ?? 0} 个文件</Text>
          <Select
            value={mode}
            onChange={(value) => setMode(value)}
            options={[
              { label: "新建作品", value: "new" },
              { label: "指派给已有作品", value: "existing" },
            ]}
            style={{ width: 200 }}
          />
          {mode === "new" ? (
            <Space.Compact style={{ width: "100%" }}>
              <Input
                value={newTitle}
                onChange={(event) => setNewTitle(event.target.value)}
                placeholder="作品名称，例如：葬送的芙莉莲"
              />
              <InputNumber
                value={newYear}
                onChange={(value) => setNewYear(value)}
                placeholder="年份（可不填）"
                min={1900}
                max={2100}
                style={{ width: 150 }}
              />
            </Space.Compact>
          ) : (
            <Select
              showSearch
              value={existingWorkId}
              onChange={setExistingWorkId}
              placeholder="选择已有作品"
              style={{ width: "100%" }}
              optionFilterProp="label"
              options={works.map((item) => ({
                value: item.group_id,
                label: `${item.title}${item.year ? `（${item.year}）` : ""}`,
              }))}
            />
          )}
          {pending?.source === "suspected" && pending.segment && (
            <Checkbox checked={remember} onChange={(event) => setRemember(event.target.checked)}>
              记住这条规则（目录组 + 名称段），下次扫描直接套用
            </Checkbox>
          )}
          {pending?.segment && (
            <Checkbox
              checked={rememberAlias}
              onChange={(event) => setRememberAlias(event.target.checked)}
            >
              记住为别名：把「{pending.segment}」记成这部作品的另一种写法（下次扫描直接套用）
            </Checkbox>
          )}
          <Text type="secondary" style={{ fontSize: 12 }}>
            当前扫描：{scan.is_sample ? "内置示例" : scan.scan_id}
          </Text>
        </Space>
      </Modal>

      <Modal
        title={editingAlias ? "修改别名" : "添加别名"}
        open={aliasOpen}
        onCancel={() => setAliasOpen(false)}
        onOk={submitAlias}
        okText="保存"
        cancelText="取消"
        confirmLoading={submitting}
        destroyOnClose
      >
        <Space direction="vertical" size={12} style={{ width: "100%" }}>
          <Input
            value={aliasText}
            onChange={(event) => setAliasText(event.target.value)}
            placeholder="另一种写法，例如：そうさく、Sousaku、搜作"
          />
          <Space.Compact style={{ width: "100%" }}>
            <Input
              value={aliasTitle}
              onChange={(event) => setAliasTitle(event.target.value)}
              placeholder="它对应的正式作品名"
            />
            <InputNumber
              value={aliasYear}
              onChange={(value) => setAliasYear(value)}
              placeholder="年份（可不填）"
              min={1900}
              max={2100}
              style={{ width: 150 }}
            />
          </Space.Compact>
          <Text type="secondary" style={{ fontSize: 12 }}>
            别名只做完全相同的写法匹配；不会因为名字相似就自动把两部作品合并。
          </Text>
        </Space>
      </Modal>
    </Card>
  );
}
