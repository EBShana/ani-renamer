import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  Input,
  List,
  Modal,
  Row,
  Segmented,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import {
  ArrowRightOutlined,
  LinkOutlined,
  SearchOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { api } from "../api";
import type {
  AutoMatchCandidate,
  AutoMatchResult,
  IdentifyOverview,
  MatchCandidate,
  MatchGroup,
  ScanPayload,
} from "../types";
import UnassignedPanel from "./UnassignedPanel";
import type { AssignPayload } from "./UnassignedPanel";

const { Text, Title } = Typography;

interface Props {
  scan: ScanPayload | null;
  onScanUpdate: (payload: ScanPayload) => void;
  onGoScan: () => void;
  onNext: () => void;
}

export default function MatchPage({ scan, onScanUpdate, onGoScan, onNext }: Props) {
  const [groups, setGroups] = useState<MatchGroup[]>([]);
  const [loading, setLoading] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [activeGroup, setActiveGroup] = useState<MatchGroup | null>(null);
  const [query, setQuery] = useState("");
  const [source, setSource] = useState<"tmdb" | "bangumi">("tmdb");
  const [candidates, setCandidates] = useState<MatchCandidate[]>([]);
  const [searching, setSearching] = useState(false);
  const [applying, setApplying] = useState(false);
  const [autoLoading, setAutoLoading] = useState(false);
  const [autoResults, setAutoResults] = useState<Record<string, AutoMatchResult>>({});
  const [autoSummary, setAutoSummary] = useState<{ auto: number; choose: number; missing: number } | null>(null);
  const [applyingGroup, setApplyingGroup] = useState("");
  const [overview, setOverview] = useState<IdentifyOverview | null>(null);
  const [identifyBusy, setIdentifyBusy] = useState(false);

  const loadGroups = async (scanId: string) => {
    setLoading(true);
    try {
      const result = await api.matchGroups(scanId);
      setGroups(result.groups);
    } catch (error) {
      message.error(`读取作品列表失败：${(error as Error).message}`);
    } finally {
      setLoading(false);
    }
  };

  const loadIdentify = async (scanId: string) => {
    try {
      const result = await api.identify(scanId);
      setOverview(result);
    } catch (error) {
      message.error(`读取未归属信息失败：${(error as Error).message}`);
    }
  };

  const refreshAfterIdentify = async (scanId: string) => {
    const [groupResult, fresh] = await Promise.all([
      api.matchGroups(scanId),
      api.getScan(scanId),
    ]);
    setGroups(groupResult.groups);
    onScanUpdate(fresh);
  };

  useEffect(() => {
    if (scan) {
      loadGroups(scan.scan_id);
      loadIdentify(scan.scan_id);
    } else {
      setGroups([]);
      setOverview(null);
    }
  }, [scan?.scan_id]);

  const handleAssign = async (payload: AssignPayload) => {
    if (!scan) return;
    setIdentifyBusy(true);
    try {
      const result = await api.identifyAssign({ scan_id: scan.scan_id, ...payload });
      setOverview(result);
      await refreshAfterIdentify(scan.scan_id);
    } finally {
      setIdentifyBusy(false);
    }
  };

  const handleIgnore = async (groupId: string, segment: string) => {
    if (!scan) return;
    setIdentifyBusy(true);
    try {
      const result = await api.identifyIgnore({
        scan_id: scan.scan_id,
        group_id: groupId,
        segment,
      });
      setOverview(result);
      await refreshAfterIdentify(scan.scan_id);
      message.success("已忽略这组疑似作品");
    } catch (error) {
      message.error(`忽略失败：${(error as Error).message}`);
    } finally {
      setIdentifyBusy(false);
    }
  };

  const handleConfirm = async (workId: string) => {
    if (!scan) return;
    setIdentifyBusy(true);
    try {
      const result = await api.identifyConfirm({ scan_id: scan.scan_id, work_id: workId });
      setOverview(result);
      await refreshAfterIdentify(scan.scan_id);
      message.success("已确认作品归属");
    } catch (error) {
      message.error(`确认失败：${(error as Error).message}`);
    } finally {
      setIdentifyBusy(false);
    }
  };

  const handleDeleteAssignment = async (assignmentId: string) => {
    if (!scan) return;
    setIdentifyBusy(true);
    try {
      await api.deleteAssignment(assignmentId);
      const result = await api.identify(scan.scan_id);
      setOverview(result);
      message.success("已删除这条规则");
    } catch (error) {
      message.error(`删除失败：${(error as Error).message}`);
    } finally {
      setIdentifyBusy(false);
    }
  };

  const openSearch = (group: MatchGroup) => {
    setActiveGroup(group);
    setQuery(group.title);
    setCandidates([]);
    setSource("tmdb");
    setSearchOpen(true);
  };

  const runSearch = async (text?: string, target: "tmdb" | "bangumi" = source) => {
    if (!scan) return;
    const keyword = (text ?? query).trim();
    if (!keyword) {
      message.warning("请填写关键词");
      return;
    }
    setSearching(true);
    try {
      const result = await api.matchSearch({
        scan_id: scan.scan_id,
        group_id: activeGroup?.group_id,
        query: keyword,
        source: target,
      });
      setCandidates(result.candidates);
      if (!result.candidates.length) message.info("没有找到候选，可换个关键词或改用其他数据源");
    } catch (error) {
      message.error((error as Error).message);
    } finally {
      setSearching(false);
    }
  };

  const applyFor = async (
    group: MatchGroup,
    candidate: MatchCandidate | AutoMatchCandidate,
    inline = false,
  ) => {
    if (!scan) return;
    if (inline) setApplyingGroup(group.group_id);
    else setApplying(true);
    try {
      const result = await api.matchApply({
        scan_id: scan.scan_id,
        group_id: group.group_id,
        tmdb_id: candidate.id,
        media_type: candidate.media_type,
      });
      setGroups(result.groups);
      const fresh = await api.getScan(scan.scan_id);
      onScanUpdate(fresh);
      setSearchOpen(false);
      setAutoResults((prev) => {
        if (!prev[group.group_id]) return prev;
        const next = { ...prev };
        delete next[group.group_id];
        return next;
      });
      message.success(`已匹配「${result.meta.name}」，更新 ${result.updated} 个文件`);
      if (result.missing.length) {
        message.warning(
          `有 ${result.missing.length} 集未在 TMDB 中找到（${result.missing.slice(0, 6).join("、")}）`,
        );
      }
    } catch (error) {
      message.error(`匹配失败：${(error as Error).message}`);
    } finally {
      if (inline) setApplyingGroup("");
      else setApplying(false);
    }
  };

  const applyCandidate = (candidate: MatchCandidate) => {
    if (activeGroup) applyFor(activeGroup, candidate);
  };

  const runAuto = async () => {
    if (!scan) return;
    if (scan.is_sample) {
      message.warning("内置示例数据不需要匹配");
      return;
    }
    setAutoLoading(true);
    try {
      const result = await api.matchAuto({ scan_id: scan.scan_id });
      setGroups(result.groups);
      setAutoResults(
        Object.fromEntries(result.results.map((item) => [item.group_id, item])),
      );
      setAutoSummary(result.summary);
      const fresh = await api.getScan(scan.scan_id);
      onScanUpdate(fresh);
      const parts = [
        `自动匹配 ${result.summary.auto} 个`,
        `待选择 ${result.summary.choose} 个`,
        `需手动 ${result.summary.missing} 个`,
      ];
      if (result.summary.auto > 0) message.success(parts.join("，"));
      else message.info(parts.join("，"));
    } catch (error) {
      message.error(`自动匹配失败：${(error as Error).message}`);
    } finally {
      setAutoLoading(false);
    }
  };

  const pickInline = (group: MatchGroup, result: AutoMatchResult, value: string) => {
    const candidate = result.candidates.find(
      (item) => `${item.media_type}:${item.id}` === value,
    );
    if (candidate) applyFor(group, candidate, true);
  };

  const columns: ColumnsType<MatchGroup> = [
    {
      title: "作品",
      dataIndex: "title",
      render: (_, record) => (
        <div>
          <div className="preview-name">{record.title}</div>
          <Space size={4} wrap style={{ marginTop: 2 }}>
            {record.source === "manual" && <Tag color="blue">手动指派</Tag>}
            {record.source === "assignment" && <Tag color="cyan">记住的规则</Tag>}
            {record.needs_confirm && <Tag color="orange">待确认</Tag>}
          </Space>
          {record.match?.name && record.match.name !== record.title && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              元数据名称：{record.match.name}
            </Text>
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
      title: "季",
      dataIndex: "seasons",
      width: 120,
      render: (value: number[]) =>
        value.length ? value.map((season) => <Tag key={season}>S{String(season).padStart(2, "0")}</Tag>) : "-",
    },
    {
      title: "文件",
      dataIndex: "video_count",
      width: 190,
      render: (value: number, record) => (
        <Text type="secondary">
          {value} 个（剧集 {record.episode_count} / 特典 {record.special_count} / 剧场版 {record.movie_count}）
        </Text>
      ),
    },
    {
      title: "匹配状态",
      dataIndex: "match",
      width: 300,
      render: (_, record) => {
        const info = autoResults[record.group_id];
        if (record.match) {
          return (
            <Space size={4} wrap>
              <Tag color="green">TMDB #{record.match.tmdb_id}</Tag>
              {record.match.name && record.match.name !== record.title && (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {record.match.name}
                </Text>
              )}
              {record.mixed_match && <Tag color="orange">组内不一致</Tag>}
              {info?.status === "auto" && <Tag color="blue">自动</Tag>}
            </Space>
          );
        }
        if (info?.status === "choose" && info.candidates.length > 0) {
          return (
            <div>
              <Select
                size="small"
                style={{ width: 250 }}
                placeholder="请选择正确的作品"
                loading={applyingGroup === record.group_id}
                onChange={(value) => pickInline(record, info, value)}
                options={info.candidates.map((item) => ({
                  value: `${item.media_type}:${item.id}`,
                  label: `${item.name}${item.year ? `（${item.year}）` : ""} · ${
                    item.media_type === "movie" ? "电影" : "剧集"
                  }`,
                }))}
              />
              {info.message && (
                <div style={{ marginTop: 4 }}>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {info.message}
                  </Text>
                </div>
              )}
            </div>
          );
        }
        if (info?.status === "missing") {
          return (
            <div>
              <Space size={4}>
                <Tag color="orange">需手动</Tag>
                <Button size="small" type="link" onClick={() => openSearch(record)}>
                  搜索
                </Button>
              </Space>
              {info.message && (
                <div style={{ marginTop: 4 }}>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {info.message}
                  </Text>
                </div>
              )}
            </div>
          );
        }
        return <Tag>未匹配</Tag>;
      },
    },
    {
      title: "操作",
      width: 140,
      render: (_, record) => (
        <Button
          type={record.match ? "default" : "primary"}
          icon={<SearchOutlined />}
          onClick={() => openSearch(record)}
        >
          {record.match ? "重新匹配" : "搜索匹配"}
        </Button>
      ),
    },
  ];

  if (!scan) {
    return (
      <Empty description="还没有扫描结果">
        <Button type="primary" onClick={onGoScan}>
          去扫描
        </Button>
      </Empty>
    );
  }

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Card>
        <Row gutter={16} align="middle">
          <Col flex="auto">
            <Title level={5} style={{ marginTop: 0 }}>
              2. 匹配作品（可选）
            </Title>
            <Text type="secondary">
              点「一键自动匹配」会逐个搜索 TMDB：唯一确定的作品自动套用，多个相似结果直接在下拉框里选，搜不到再手动搜索。
              未被识别的文件会留在下方「未归属区」，归属确认前不能执行。
            </Text>
          </Col>
          <Col>
            <Space wrap>
              <Text type="secondary">
                {groups.length} 个作品 · 已匹配 {groups.filter((item) => item.match).length} 个
                {overview
                  ? ` · 未归属 ${overview.summary.suspected + overview.summary.unassigned} 项`
                  : ""}
              </Text>
              <Button
                icon={<ThunderboltOutlined />}
                loading={autoLoading}
                disabled={scan.is_sample}
                onClick={runAuto}
              >
                一键自动匹配 TMDB
              </Button>
              <Button type="primary" icon={<ArrowRightOutlined />} onClick={onNext}>
                下一步：模板与预览
              </Button>
            </Space>
          </Col>
        </Row>
      </Card>

      {scan.is_sample && (
        <Alert type="warning" showIcon message="当前是内置示例数据，匹配结果只用于演示，不会影响真实文件。" />
      )}

      {overview && (
        <UnassignedPanel
          scan={scan}
          overview={overview}
          works={groups}
          busy={identifyBusy}
          onAssign={handleAssign}
          onIgnore={handleIgnore}
          onConfirm={handleConfirm}
          onDeleteAssignment={handleDeleteAssignment}
        />
      )}

      {autoSummary && (
        <Alert
          type={autoSummary.choose + autoSummary.missing > 0 ? "warning" : "success"}
          showIcon
          closable
          onClose={() => setAutoSummary(null)}
          message={`自动匹配完成：自动 ${autoSummary.auto} 个 · 待选择 ${autoSummary.choose} 个 · 需手动 ${autoSummary.missing} 个`}
          description="自动匹配只套用“标题与年份唯一确定”的结果；待选择的可以在「匹配状态」列直接挑选，需手动的点「搜索」。"
        />
      )}

      <Card title="作品组">
        <Table
          rowKey="group_id"
          size="small"
          loading={loading}
          columns={columns}
          dataSource={groups}
          pagination={{ pageSize: 10, showSizeChanger: true, showTotal: (total) => `共 ${total} 个作品` }}
          scroll={{ x: 980 }}
          locale={{ emptyText: <Empty description="没有可匹配的视频（可能只扫描到字幕等文件）" /> }}
        />
      </Card>

      <Modal
        title={activeGroup ? `匹配「${activeGroup.title}」` : "匹配作品"}
        open={searchOpen}
        onCancel={() => setSearchOpen(false)}
        footer={null}
        width={720}
      >
        <Space direction="vertical" size={12} style={{ width: "100%" }}>
          <Space.Compact style={{ width: "100%" }}>
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onPressEnter={() => runSearch()}
              placeholder="作品名，可改成中文 / 日文 / 英文"
            />
            <Button type="primary" loading={searching} onClick={() => runSearch()}>
              搜索
            </Button>
          </Space.Compact>
          <Segmented
            value={source}
            onChange={(value) => {
              setSource(value as "tmdb" | "bangumi");
              if (query.trim()) runSearch(query, value as "tmdb" | "bangumi");
            }}
            options={[
              { label: "TMDB（主）", value: "tmdb" },
              { label: "Bangumi（辅助）", value: "bangumi" },
            ]}
          />
          <Text type="secondary" style={{ fontSize: 12 }}>
            Bangumi 适合查中文 / 日文正式名；查到后点“用该名称搜 TMDB”再套用 TMDB 的单集数据。
          </Text>
          <List
            loading={searching}
            dataSource={candidates}
            locale={{ emptyText: "输入关键词后搜索" }}
            renderItem={(candidate) => (
              <List.Item
                actions={[
                  candidate.source === "bangumi" ? (
                    <Button
                      key="retry"
                      size="small"
                      onClick={() => {
                        setQuery(candidate.name);
                        setSource("tmdb");
                        runSearch(candidate.name, "tmdb");
                      }}
                    >
                      用该名称搜 TMDB
                    </Button>
                  ) : (
                    <Button
                      key="use"
                      type="primary"
                      size="small"
                      loading={applying}
                      icon={<LinkOutlined />}
                      onClick={() => applyCandidate(candidate)}
                    >
                      使用这条
                    </Button>
                  ),
                ]}
              >
                <List.Item.Meta
                  avatar={
                    candidate.poster ? (
                      <img src={candidate.poster} alt="" style={{ width: 54, height: 80, objectFit: "cover", borderRadius: 4 }} />
                    ) : (
                      <div
                        style={{
                          width: 54,
                          height: 80,
                          borderRadius: 4,
                          background: "#f1f3f5",
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                          color: "#adb5bd",
                          fontSize: 12,
                        }}
                      >
                        无图
                      </div>
                    )
                  }
                  title={
                    <Space wrap size={6}>
                      <span>{candidate.name || candidate.original_name}</span>
                      {candidate.year && <Tag color="blue">{candidate.year}</Tag>}
                      <Tag>{candidate.media_type === "movie" ? "电影" : "剧集"}</Tag>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        #{candidate.id}
                      </Text>
                    </Space>
                  }
                  description={
                    <div>
                      {candidate.original_name && candidate.original_name !== candidate.name && (
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          {candidate.original_name}
                          <br />
                        </Text>
                      )}
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        {candidate.overview || "暂无简介"}
                      </Text>
                    </div>
                  }
                />
              </List.Item>
            )}
          />
        </Space>
      </Modal>
    </Space>
  );
}
