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
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import type {
  AssignmentRecord,
  IdentifyOverview,
  MatchGroup,
  ScanPayload,
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
}: Props) {
  const [suspectedKeys, setSuspectedKeys] = useState<React.Key[]>([]);
  const [fileIds, setFileIds] = useState<React.Key[]>([]);
  const [pending, setPending] = useState<PendingAssign | null>(null);
  const [mode, setMode] = useState<"existing" | "new">("new");
  const [existingWorkId, setExistingWorkId] = useState<string>();
  const [newTitle, setNewTitle] = useState("");
  const [newYear, setNewYear] = useState<number | null>(null);
  const [remember, setRemember] = useState(true);
  const [submitting, setSubmitting] = useState(false);

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
      setPending({ file_ids: fileIds.map(String), source, remember: false });
      setNewTitle("");
      setNewYear(null);
    }
    setMode("new");
    setExistingWorkId(undefined);
    setRemember(source === "suspected" && suspectedKeys.length === 1);
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
              : record.source === "manual"
                ? "手动指派"
                : "文件名 / 目录自动识别"}
          </Text>
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

  const suspectedSelected = suspectedKeys.length > 0;
  const fileSelected = fileIds.length > 0;
  const hasAny =
    overview.suspected.length > 0 ||
    overview.unassigned.length > 0 ||
    overview.pending.length > 0 ||
    overview.assignments.length > 0;

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
                  <Text type="secondary">
                    这些作品是系统按“文件名 + 目录”自动识别的。确认无误后点「确认」，之后就能正常预览和执行。
                  </Text>
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
          <Text type="secondary" style={{ fontSize: 12 }}>
            当前扫描：{scan.is_sample ? "内置示例" : scan.scan_id}
          </Text>
        </Space>
      </Modal>
    </Card>
  );
}
