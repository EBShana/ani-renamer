import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  Divider,
  Empty,
  Input,
  List,
  Modal,
  Popconfirm,
  Row,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import {
  DeleteOutlined,
  EditOutlined,
  FolderOpenOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  SearchOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { api } from "../api";
import type { Category, DirectoryGroup, ParsedFile, Preset, ScanPayload } from "../types";
import { CATEGORY_LABELS } from "../types";

const { Text, Title } = Typography;

const KIND_META: Record<string, { label: string; color: string }> = {
  episode: { label: "剧集", color: "blue" },
  special: { label: "特典", color: "purple" },
  movie: { label: "剧场版", color: "gold" },
  unknown: { label: "未识别", color: "red" },
  asset: { label: "-", color: "default" },
};

const CATEGORY_COLORS: Record<Category, string> = {
  video: "geekblue",
  subtitle: "cyan",
  audio: "green",
  image: "orange",
  archive: "magenta",
  other: "default",
  folder: "gold",
};

interface Props {
  scan: ScanPayload | null;
  onScan: (payload: ScanPayload | null) => void;
  onNext: () => void;
  presets: Preset[];
  presetId: string | null;
  onPresetChange: (presetId: string) => void;
}

function formatSize(bytes: number): string {
  if (!bytes) return "-";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let index = 0;
  while (value > 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(1)} ${units[index]}`;
}

export default function ScanPage({ scan, onScan, onNext, presets, presetId, onPresetChange }: Props) {
  const [groups, setGroups] = useState<DirectoryGroup[]>([]);
  const [mediaRoot, setMediaRoot] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [groupErrors, setGroupErrors] = useState<string[]>([]);
  const [loadError, setLoadError] = useState("");
  const [scanning, setScanning] = useState(false);
  const [keyword, setKeyword] = useState("");
  const [browseOpen, setBrowseOpen] = useState(false);
  const [browseTarget, setBrowseTarget] = useState<"source" | "target" | null>(null);
  const [browse, setBrowse] = useState<{
    path: string;
    parent: string | null;
    entries: { name: string; path: string }[];
    error?: string;
  } | null>(null);
  const [groupOpen, setGroupOpen] = useState(false);
  const [editing, setEditing] = useState<DirectoryGroup | null>(null);
  const [form, setForm] = useState({ name: "", source: "", target: "" });
  const [groupsLocked, setGroupsLocked] = useState(false);

  const loadGroups = async (keepSelection = true) => {
    try {
      const result = await api.checkGroups();
      setGroups(result.groups);
      setMediaRoot(result.media_root);
      setGroupErrors(result.errors);
      setGroupsLocked(Boolean(result.locked));
      setLoadError("");
      setSelected((current) => {
        const valid = result.groups.map((item) => item.id);
        if (!keepSelection || current.length === 0) return valid;
        const kept = current.filter((id) => valid.includes(id));
        return kept.length > 0 ? kept : valid;
      });
    } catch (error) {
      setLoadError((error as Error).message);
    }
  };

  useEffect(() => {
    void loadGroups(false);
  }, []);

  const openBrowse = async (path?: string, target: "source" | "target" = "source") => {
    setBrowseTarget(target);
    try {
      const result = await api.browse(path);
      setBrowse(result);
      setBrowseOpen(true);
      if (result.error) message.warning(result.error);
    } catch (error) {
      message.error((error as Error).message);
    }
  };

  const runScan = async () => {
    if (!selected.length) {
      message.warning("请先勾选至少一个目录组");
      return;
    }
    setScanning(true);
    try {
      const result = await api.scan(selected);
      onScan(result);
      const stats = result.stats;
      message.success(`扫描完成：${stats.total} 个文件，未识别 ${stats.unknown} 个`);
    } catch (error) {
      message.error(`扫描失败：${(error as Error).message}`);
    } finally {
      setScanning(false);
    }
  };

  const openGroup = (group?: DirectoryGroup) => {
    setEditing(group ?? null);
    setForm(
      group
        ? { name: group.name, source: group.source, target: group.target }
        : { name: "", source: "", target: "" },
    );
    setGroupOpen(true);
  };

  const saveGroup = async () => {
    if (!form.source.trim()) {
      message.warning("请填写源目录");
      return;
    }
    try {
      if (editing) {
        await api.updateGroup(editing.id, form);
      } else {
        await api.createGroup(form);
      }
      setGroupOpen(false);
      message.success("目录组已保存");
      await loadGroups(false);
    } catch (error) {
      message.error(`保存失败：${(error as Error).message}`);
    }
  };

  const removeGroup = async (group: DirectoryGroup) => {
    try {
      await api.deleteGroup(group.id);
      message.success(`已删除目录组「${group.name}」`);
      await loadGroups(false);
    } catch (error) {
      message.error(`删除失败：${(error as Error).message}`);
    }
  };

  const rows = useMemo(() => {
    if (!scan) return [];
    const text = keyword.trim().toLowerCase();
    if (!text) return scan.files;
    return scan.files.filter(
      (file) =>
        file.filename.toLowerCase().includes(text) ||
        (file.title ?? "").toLowerCase().includes(text) ||
        file.group.toLowerCase().includes(text),
    );
  }, [scan, keyword]);

  const groupNameOf = (groupId?: string) => {
    const fromScan = scan?.groups?.find((item) => item.id === groupId);
    const fromList = groups.find((item) => item.id === groupId);
    return fromScan?.name || fromList?.name || "";
  };

  const columns: ColumnsType<ParsedFile> = [
    {
      title: "文件",
      dataIndex: "filename",
      width: 360,
      render: (_, record) => (
        <Tooltip title={record.path}>
          <div className="preview-name">{record.filename}</div>
          <div className="path-text">{record.parent}</div>
        </Tooltip>
      ),
    },
    {
      title: "类别",
      dataIndex: "category",
      width: 84,
      filters: (Object.keys(CATEGORY_LABELS) as Category[]).map((key) => ({
        text: CATEGORY_LABELS[key],
        value: key,
      })),
      onFilter: (value, record) => record.category === value,
      render: (value: Category) => (
        <Tag color={CATEGORY_COLORS[value]}>{CATEGORY_LABELS[value] ?? value}</Tag>
      ),
    },
    {
      title: "类型",
      dataIndex: "kind",
      width: 88,
      filters: [
        { text: "剧集", value: "episode" },
        { text: "特典", value: "special" },
        { text: "剧场版", value: "movie" },
        { text: "未识别", value: "unknown" },
      ],
      onFilter: (value, record) => record.kind === value,
      render: (value: string) => {
        const meta = KIND_META[value] ?? KIND_META.unknown;
        return value === "asset" ? <Text type="secondary">-</Text> : <Tag color={meta.color}>{meta.label}</Tag>;
      },
    },
    {
      title: "作品",
      dataIndex: "title",
      width: 180,
      render: (value: string | null) => value || <Text type="danger">未提取</Text>,
    },
    {
      title: "季 / 集",
      width: 110,
      render: (_, record) =>
        record.kind === "special"
          ? `${record.special_type ?? "SP"}${record.special_no ?? ""}`
          : record.season != null && record.episode != null
            ? `S${String(record.season).padStart(2, "0")}E${String(record.episode).padStart(2, "0")}`
            : "-",
    },
    { title: "发布组", dataIndex: "group", width: 150, render: (value: string) => value || "-" },
    { title: "分辨率", dataIndex: "resolution", width: 92, render: (value: string) => value || "-" },
    { title: "编码", dataIndex: "codec", width: 82, render: (value: string) => value || "-" },
    { title: "大小", dataIndex: "size", width: 90, render: (value: number) => formatSize(value) },
    {
      title: "状态",
      dataIndex: "status",
      width: 96,
      render: (value: string, record) =>
        value === "ok" ? (
          <Tag color="green">已识别</Tag>
        ) : (
          <Tooltip title={record.warnings.join("；")}>
            <Tag color="orange">待确认</Tag>
          </Tooltip>
        ),
    },
  ];

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Card>
        <Title level={5} style={{ marginTop: 0 }}>
          1. 选择目录组（源目录 → 目标目录）
        </Title>
        <Text type="secondary">
          每个目录组是一对“从哪里扫描 + 输出到哪里”。多个组之间必须相互独立（不能嵌套或重叠），
          组内可以自由移动，禁止跨组移动。媒体根目录：<span className="mono">{mediaRoot || "-"}</span>
        </Text>
        {loadError && <Alert type="error" showIcon message={loadError} style={{ marginTop: 12 }} />}
        {groupsLocked && (
          <Alert
            type="info"
            showIcon
            style={{ marginTop: 12 }}
            message="执行任务进行中，目录组暂时锁定"
            description="有任务正在执行或已暂停，完成后才能增删改目录组，避免组快照和实际文件对不上。"
          />
        )}
        {groupErrors.length > 0 && (
          <Alert
            type="warning"
            showIcon
            style={{ marginTop: 12 }}
            message="现有目录组存在问题，个别组扫描会失败"
            description={
              <Space direction="vertical" size={2}>
                {groupErrors.map((item, index) => (
                  <Text key={index} type="secondary" style={{ fontSize: 12 }}>
                    {item}
                  </Text>
                ))}
              </Space>
            }
          />
        )}
        <Table
          rowKey="id"
          size="small"
          style={{ marginTop: 12 }}
          pagination={false}
          locale={{ emptyText: "还没有目录组，点右上角「添加目录组」" }}
          dataSource={groups}
          columns={[
            {
              title: "扫描",
              width: 72,
              render: (_, record) => (
                <Checkbox
                  checked={selected.includes(record.id)}
                  onChange={(event) =>
                    setSelected(
                      event.target.checked
                        ? [...selected, record.id]
                        : selected.filter((id) => id !== record.id),
                    )
                  }
                />
              ),
            },
            { title: "名称", dataIndex: "name", width: 160 },
            {
              title: "源目录",
              dataIndex: "source",
              render: (value: string) => <span className="mono">{value}</span>,
            },
            {
              title: "目标目录",
              dataIndex: "target",
              render: (value: string) => <span className="mono">{value}</span>,
            },
            {
              title: "操作",
              width: 150,
              render: (_, record) => (
                <Space size={4}>
                  <Button
                    size="small"
                    icon={<EditOutlined />}
                    disabled={groupsLocked}
                    onClick={() => openGroup(record)}
                  >
                    编辑
                  </Button>
                  <Popconfirm
                    title={`删除目录组「${record.name}」？`}
                    description="已有扫描结果会保留当时的组快照，不受影响；新扫描将不再包含这个组。"
                    okText="删除"
                    cancelText="取消"
                    disabled={groupsLocked}
                    onConfirm={() => removeGroup(record)}
                  >
                    <Button size="small" danger icon={<DeleteOutlined />} disabled={groupsLocked} />
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
          footer={() => (
            <Space>
              <Button icon={<PlusOutlined />} disabled={groupsLocked} onClick={() => openGroup()}>
                添加目录组
              </Button>
              {groupsLocked && <Text type="secondary">执行任务进行中，暂不能修改目录组</Text>}
            </Space>
          )}
        />

        <Divider />
        <Space wrap>
          <Text type="secondary">工作流预设</Text>
          <Select
            style={{ minWidth: 220 }}
            value={presetId ?? undefined}
            onChange={(value) => onPresetChange(value)}
            options={presets.map((item) => ({
              value: item.id,
              label: `${item.name}${item.is_default ? "（默认）" : ""}`,
            }))}
            placeholder="选择预设"
          />
          <Text type="secondary" style={{ fontSize: 12 }}>
            工作流设置（字幕、特典、冲突等）统一在「设置」页管理，预设只保存命名规则。
          </Text>
        </Space>
        <div>
          <Button
            type="primary"
            icon={<PlayCircleOutlined />}
            loading={scanning}
            onClick={runScan}
            style={{ marginTop: 12 }}
          >
            开始扫描选中的目录组
          </Button>
          {scan && (
            <Button onClick={() => onScan(null)} disabled={scanning} style={{ marginTop: 12 }}>
              清空扫描结果
            </Button>
          )}
        </div>
      </Card>

      {scan && (
        <Card>
          <Row gutter={16} className="stat-row" style={{ marginBottom: 8 }}>
            <Col span={6}>
              <Text type="secondary">文件总数</Text>
              <div style={{ fontSize: 20, fontWeight: 600 }}>{scan.stats.total}</div>
            </Col>
            <Col span={6}>
              <Text type="secondary">视频</Text>
              <div style={{ fontSize: 20, fontWeight: 600 }}>{scan.stats.video}</div>
            </Col>
            <Col span={6}>
              <Text type="secondary">字幕</Text>
              <div style={{ fontSize: 20, fontWeight: 600 }}>{scan.stats.subtitle}</div>
            </Col>
            <Col span={6}>
              <Text type="secondary">音频 / 图片 / 压缩包</Text>
              <div style={{ fontSize: 20, fontWeight: 600 }}>
                {scan.stats.audio + scan.stats.image + scan.stats.archive + scan.stats.other}
              </div>
            </Col>
          </Row>
          <Row gutter={16} className="stat-row">
            <Col span={6}>
              <Text type="secondary">剧集</Text>
              <div style={{ fontSize: 20, fontWeight: 600 }}>{scan.stats.episode}</div>
            </Col>
            <Col span={6}>
              <Text type="secondary">特典</Text>
              <div style={{ fontSize: 20, fontWeight: 600 }}>{scan.stats.special}</div>
            </Col>
            <Col span={6}>
              <Text type="secondary">剧场版</Text>
              <div style={{ fontSize: 20, fontWeight: 600 }}>{scan.stats.movie}</div>
            </Col>
            <Col span={6}>
              <Text type="secondary">未识别</Text>
              <div style={{ fontSize: 20, fontWeight: 600, color: scan.stats.unknown ? "#d97706" : undefined }}>
                {scan.stats.unknown}
              </div>
            </Col>
          </Row>
          {(scan.ignored_dirs?.length ?? 0) > 0 && (
            <Alert
              type="info"
              showIcon
              style={{ marginTop: 12, marginBottom: 12 }}
              message={`已跳过 ${scan.ignored_dirs?.length} 个目录（${scan.markers_written ?? 0} 个空标记文件已写入）`}
              description={
                <Space direction="vertical" size={2}>
                  {scan.ignored_dirs?.map((dir, index) => (
                    <Text key={`${dir.path}-${index}`} type="secondary" className="mono" style={{ fontSize: 12 }}>
                      {groupNameOf(dir.group_id) && `[${groupNameOf(dir.group_id)}] `}
                      {dir.path}{" "}
                      {dir.reason === "marker"
                        ? "（已有 .ignore/.nomedia）"
                        : dir.reason === "ova"
                          ? "（OVA 目录，按设置跳过）"
                          : ""}
                    </Text>
                  ))}
                </Space>
              }
            />
          )}
          {scan.truncated && (
            <Alert type="warning" showIcon message="文件数量达到上限（20000），仅处理了前 20000 个文件。" style={{ marginBottom: 12 }} />
          )}
          <Space style={{ marginBottom: 12, justifyContent: "space-between", width: "100%" }}>
            <Input
              prefix={<SearchOutlined />}
              placeholder="按文件名 / 作品 / 发布组过滤"
              style={{ width: 320 }}
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              allowClear
            />
            <Button type="primary" onClick={onNext}>
              下一步：匹配作品
            </Button>
          </Space>
          <Table
            rowKey="id"
            size="small"
            columns={columns}
            dataSource={rows}
            pagination={{ pageSize: 12, showSizeChanger: true, showTotal: (total) => `共 ${total} 条` }}
            scroll={{ x: 1200 }}
            expandable={{
              expandedRowRender: (record) => (
                <div className="mono" style={{ whiteSpace: "pre-wrap" }}>
                  {record.warnings.length > 0 && (
                    <Text type="warning">提示：{record.warnings.join("；")}</Text>
                  )}
                  <div style={{ marginTop: 4 }}>解析字段：</div>
                  {Object.entries(record.fields)
                    .filter(([, value]) => value !== "" && value !== 0)
                    .map(([key, value]) => (
                      <span key={key} style={{ marginRight: 16 }}>
                        {key}={String(value)}
                      </span>
                    ))}
                </div>
              ),
            }}
            locale={{ emptyText: <Empty description="没有匹配的文件" /> }}
          />
        </Card>
      )}

      <Modal
        title={editing ? "编辑目录组" : "添加目录组"}
        open={groupOpen}
        onCancel={() => setGroupOpen(false)}
        onOk={saveGroup}
        okText="保存"
        cancelText="取消"
      >
        <Space direction="vertical" style={{ width: "100%" }} size={12}>
          <div>
            <Text type="secondary">名称</Text>
            <Input
              value={form.name}
              placeholder="例如 动画库"
              onChange={(event) => setForm({ ...form, name: event.target.value })}
            />
          </div>
          <div>
            <Text type="secondary">源目录（从这里扫描）</Text>
            <Space.Compact style={{ width: "100%" }}>
              <Input
                className="mono"
                value={form.source}
                placeholder={`例如 ${mediaRoot || "/media"}/动画`}
                onChange={(event) => setForm({ ...form, source: event.target.value })}
              />
              <Button icon={<FolderOpenOutlined />} onClick={() => openBrowse(form.source || mediaRoot, "source")}>
                浏览
              </Button>
            </Space.Compact>
          </div>
          <div>
            <Text type="secondary">目标目录（输出到这里，留空 = 与源目录相同）</Text>
            <Space.Compact style={{ width: "100%" }}>
              <Input
                className="mono"
                value={form.target}
                placeholder={`留空表示和源目录相同`}
                onChange={(event) => setForm({ ...form, target: event.target.value })}
              />
              <Button icon={<FolderOpenOutlined />} onClick={() => openBrowse(form.target || mediaRoot, "target")}>
                浏览
              </Button>
            </Space.Compact>
          </div>
          <Alert
            type="info"
            showIcon
            message="组内会自动生成「作品 (年份)/Season xx」；组与组之间不允许目录嵌套或重叠。"
          />
        </Space>
      </Modal>

      <Modal
        title={browseTarget === "target" ? "选择目标文件夹" : "选择源文件夹"}
        open={browseOpen}
        onCancel={() => setBrowseOpen(false)}
        footer={
          <Space>
            <Button onClick={() => setBrowseOpen(false)}>取消</Button>
            <Button
              type="primary"
              disabled={!browse || !!browse.error}
              onClick={() => {
                if (browse) {
                  setForm((current) =>
                    browseTarget === "target"
                      ? { ...current, target: browse.path }
                      : { ...current, source: browse.path },
                  );
                  setBrowseOpen(false);
                }
              }}
            >
              使用当前目录
            </Button>
          </Space>
        }
      >
        {browse?.error ? (
          <Alert type="error" showIcon message={browse.error} />
        ) : (
          <>
            <Space style={{ marginBottom: 8 }}>
              <Button size="small" disabled={!browse?.parent} onClick={() => openBrowse(browse?.parent ?? undefined, browseTarget ?? "source")}>
                上一级
              </Button>
              <Text className="mono" style={{ fontSize: 12 }}>
                {browse?.path}
              </Text>
            </Space>
            <List
              size="small"
              bordered
              dataSource={browse?.entries ?? []}
              locale={{ emptyText: "没有子文件夹" }}
              renderItem={(entry) => (
                <List.Item style={{ cursor: "pointer" }} onClick={() => openBrowse(entry.path, browseTarget ?? "source")}>
                  <Space>
                    <FolderOpenOutlined />
                    {entry.name}
                  </Space>
                </List.Item>
              )}
            />
          </>
        )}
      </Modal>
    </Space>
  );
}
