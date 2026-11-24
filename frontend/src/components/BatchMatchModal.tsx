import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Divider,
  Empty,
  Input,
  List,
  Modal,
  Segmented,
  Select,
  Space,
  Tag,
  Typography,
  message,
} from "antd";
import { LinkOutlined, RobotOutlined } from "@ant-design/icons";
import { api } from "../api";
import type { AiMatchSuggestion, MatchCandidate, MatchGroup, MergeInfo } from "../types";

const { Text } = Typography;

export interface BatchTarget {
  group_id: string;
  title: string;
  year?: number | null;
  video_count?: number;
  entry_count?: number;
}

interface Props {
  open: boolean;
  scanId: string;
  targets: BatchTarget[];
  skippedUnassigned: number;
  aiConfigured: boolean;
  aiMessage?: string;
  aiLimit: number;
  onClose: () => void;
  onChanged: (groups: MatchGroup[], merged: MergeInfo[]) => void;
}

interface RowResult {
  ok: boolean;
  error?: string;
  updated?: number;
}

export default function BatchMatchModal({
  open,
  scanId,
  targets,
  skippedUnassigned,
  aiConfigured,
  aiMessage,
  aiLimit,
  onClose,
  onChanged,
}: Props) {
  const [mode, setMode] = useState<"manual" | "ai">("manual");
  const [query, setQuery] = useState("");
  const [source, setSource] = useState<"tmdb" | "bangumi">("tmdb");
  const [candidates, setCandidates] = useState<MatchCandidate[]>([]);
  const [fallback, setFallback] = useState<MatchCandidate[]>([]);
  const [searching, setSearching] = useState(false);
  const [applying, setApplying] = useState("");
  const [results, setResults] = useState<Record<string, RowResult>>({});
  const [aiLoading, setAiLoading] = useState(false);
  const [suggestions, setSuggestions] = useState<Record<string, AiMatchSuggestion>>({});
  const [aiNote, setAiNote] = useState("");
  const [activeGroup, setActiveGroup] = useState("");
  const [groupCandidates, setGroupCandidates] = useState<Record<string, MatchCandidate[]>>({});
  const [groupSearching, setGroupSearching] = useState("");

  useEffect(() => {
    if (!open) return;
    setMode("manual");
    setQuery(targets.length === 1 ? targets[0].title : "");
    setSource("tmdb");
    setCandidates([]);
    setFallback([]);
    setResults({});
    setSuggestions({});
    setAiNote("");
    setActiveGroup("");
    setGroupCandidates({});
  }, [open, targets]);

  const successCount = useMemo(
    () => Object.values(results).filter((item) => item.ok).length,
    [results],
  );
  const failed = useMemo(
    () => targets.filter((target) => results[target.group_id] && !results[target.group_id].ok),
    [results, targets],
  );

  const search = async (text?: string, target: "tmdb" | "bangumi" = source) => {
    const keyword = (text ?? query).trim();
    if (!keyword) {
      message.warning("请填写要搜索的作品名");
      return;
    }
    setSearching(true);
    try {
      const result = await api.matchSearch({ scan_id: scanId, query: keyword, source: target });
      setCandidates(result.candidates);
      setFallback(result.fallback ?? []);
      if (!result.candidates.length) {
        message.info(result.fallback?.length ? "TMDB 没有结果，可试试下面的 Bangumi 备用候选" : "没有找到候选");
      }
    } catch (error) {
      message.error(`搜索失败：${(error as Error).message}`);
    } finally {
      setSearching(false);
    }
  };

  const applyAll = async (candidate: MatchCandidate) => {
    setApplying("all");
    try {
      const result = await api.matchApplyBatch({
        scan_id: scanId,
        group_ids: targets.map((target) => target.group_id),
        tmdb_id: candidate.id,
        media_type: candidate.media_type,
      });
      const next: Record<string, RowResult> = {};
      for (const item of result.results) {
        next[item.group_id] = { ok: item.ok, error: item.error, updated: item.updated };
      }
      setResults(next);
      onChanged(result.groups, result.merged);
      const ok = result.results.filter((item) => item.ok).length;
      const bad = result.results.length - ok;
      if (bad) {
        message.warning(`批量匹配完成：成功 ${ok} 组，失败 ${bad} 组，失败原因见弹窗列表`);
      } else {
        message.success(`已把 ${ok} 个作品组匹配为「${candidate.name}」`);
      }
    } catch (error) {
      message.error(`批量匹配失败：${(error as Error).message}`);
    } finally {
      setApplying("");
    }
  };

  const loadAi = async () => {
    if (!aiConfigured) {
      message.warning(aiMessage || "AI 还没配置好");
      return;
    }
    setAiLoading(true);
    setAiNote("");
    try {
      const result = await api.aiMatchSuggest({
        scan_id: scanId,
        include_matched: true,
        limit: Math.max(1, Math.min(aiLimit, targets.length)),
      });
      const map: Record<string, AiMatchSuggestion> = {};
      const wanted = new Set(targets.map((target) => target.group_id));
      for (const item of result.suggestions) {
        if (wanted.has(item.group_id)) map[item.group_id] = item;
      }
      setSuggestions(map);
      setAiNote(
        result.message ||
          `AI 给了 ${Object.keys(map).length} / ${targets.length} 组的候选；没有给到的组可以手动搜索。`,
      );
    } catch (error) {
      message.error(`AI 辅助失败：${(error as Error).message}`);
    } finally {
      setAiLoading(false);
    }
  };

  const searchForGroup = async (target: BatchTarget) => {
    const suggestion = suggestions[target.group_id];
    const keyword = suggestion?.query || target.title;
    setActiveGroup(target.group_id);
    setGroupSearching(target.group_id);
    try {
      const result = await api.matchSearch({ scan_id: scanId, query: keyword, source: "tmdb" });
      setGroupCandidates((previous) => ({ ...previous, [target.group_id]: result.candidates }));
    } catch (error) {
      message.error(`搜索失败：${(error as Error).message}`);
    } finally {
      setGroupSearching("");
    }
  };

  const applyOne = async (target: BatchTarget, candidate: MatchCandidate) => {
    setApplying(target.group_id);
    try {
      const result = await api.matchApply({
        scan_id: scanId,
        group_id: target.group_id,
        tmdb_id: candidate.id,
        media_type: candidate.media_type,
      });
      setResults((previous) => ({
        ...previous,
        [target.group_id]: { ok: true, updated: result.updated },
      }));
      onChanged(result.groups, result.merged ?? []);
      message.success(`「${target.title}」已匹配为「${result.meta.name}」`);
    } catch (error) {
      setResults((previous) => ({
        ...previous,
        [target.group_id]: { ok: false, error: (error as Error).message },
      }));
      message.error(`「${target.title}」匹配失败：${(error as Error).message}`);
    } finally {
      setApplying("");
    }
  };

  const candidateActions = (candidate: MatchCandidate, onUse: () => void) =>
    candidate.source === "bangumi"
      ? [
          <Button
            key="retry"
            size="small"
            onClick={() => {
              setSource("tmdb");
              void search(candidate.name, "tmdb");
            }}
          >
            用该名称搜 TMDB
          </Button>,
        ]
      : [
          <Button
            key="use"
            type="primary"
            size="small"
            icon={<LinkOutlined />}
            loading={applying === "all"}
            onClick={onUse}
          >
            使用这条
          </Button>,
        ];

  return (
    <Modal
      title="批量手动匹配"
      open={open}
      onCancel={onClose}
      footer={<Button onClick={onClose}>关闭</Button>}
      width={860}
    >
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Alert
          type="info"
          showIcon
          message={`已勾选 ${targets.length} 个作品组（同一组里勾了多行只算一次）`}
          description={
            skippedUnassigned > 0
              ? `另有 ${skippedUnassigned} 个未归属条目被跳过：它们还没有作品组，请先用行上的「手动匹配」建立归属。`
              : "批量操作会把这些组一起匹配到同一部 TMDB 作品；每组仍然单独套用季集信息。"
          }
        />
        <Segmented
          value={mode}
          onChange={(value) => setMode(value as "manual" | "ai")}
          options={[
            { label: "统一指定同一部作品", value: "manual" },
            { label: "AI 给候选，逐个确认", value: "ai" },
          ]}
        />

        {mode === "manual" ? (
          <>
            <Space.Compact style={{ width: "100%" }}>
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onPressEnter={() => void search()}
                placeholder="输入要统一指定的作品名"
              />
              <Select
                style={{ width: 112 }}
                value={source}
                onChange={(value) => setSource(value as "tmdb" | "bangumi")}
                options={[
                  { value: "tmdb", label: "TMDB" },
                  { value: "bangumi", label: "Bangumi" },
                ]}
              />
              <Button type="primary" loading={searching} onClick={() => void search()}>
                搜索
              </Button>
            </Space.Compact>
            <Text type="secondary" style={{ fontSize: 12 }}>
              搜到后点「使用这条并套用到全部 {targets.length} 组」。失败的组会留在列表里，可以单独重试。
            </Text>
            <List
              loading={searching}
              dataSource={candidates}
              locale={{ emptyText: "输入关键词后搜索" }}
              renderItem={(candidate) => (
                <List.Item actions={candidateActions(candidate, () => void applyAll(candidate))}>
                  <List.Item.Meta
                    title={
                      <Space wrap size={6}>
                        <span>{candidate.name || candidate.original_name}</span>
                        {candidate.year && <Tag color="blue">{candidate.year}</Tag>}
                        <Tag>{candidate.media_type === "movie" ? "电影" : "剧集"}</Tag>
                      </Space>
                    }
                    description={candidate.overview || "暂无简介"}
                  />
                </List.Item>
              )}
            />
            {candidates.length === 0 && fallback.length > 0 && (
              <List
                dataSource={fallback}
                renderItem={(candidate) => (
                  <List.Item actions={candidateActions(candidate, () => undefined)}>
                    <List.Item.Meta
                      title={<Space wrap size={6}>{candidate.name || candidate.original_name}<Tag color="purple">Bangumi</Tag></Space>}
                      description={candidate.original_name || "暂无原名"}
                    />
                  </List.Item>
                )}
              />
            )}
          </>
        ) : (
          <>
            <Space wrap>
              <Button icon={<RobotOutlined />} loading={aiLoading} onClick={() => void loadAi()}>
                让 AI 给这批候选
              </Button>
              {aiNote && <Text type="secondary">{aiNote}</Text>}
            </Space>
            <Alert
              type="warning"
              showIcon
              message="AI 建议按作品组给，不按文件给"
              description="AI 只提供一个搜索关键词或候选名称；你仍然要在这里逐组确认，确认后才会套用 TMDB 数据。"
            />
            {targets.map((target) => {
              const suggestion = suggestions[target.group_id];
              const row = results[target.group_id];
              return (
                <div key={target.group_id}>
                  <Space wrap>
                    <Text strong>{target.title || target.group_id}</Text>
                    {target.year && <Tag color="blue">{target.year}</Tag>}
                    <Tag>{target.entry_count ?? target.video_count ?? 0} 项</Tag>
                    {row?.ok && <Tag color="green">已匹配</Tag>}
                    {row && !row.ok && <Tag color="red">失败</Tag>}
                    {suggestion ? (
                      <Tag color="purple">
                        AI：{suggestion.title || suggestion.query}
                        {suggestion.year ? `（${suggestion.year}）` : ""}
                      </Tag>
                    ) : (
                      <Tag>AI 未给建议</Tag>
                    )}
                    <Button
                      size="small"
                      loading={groupSearching === target.group_id}
                      onClick={() => void searchForGroup(target)}
                    >
                      搜索候选
                    </Button>
                  </Space>
                  {row && !row.ok && (
                    <div style={{ marginTop: 4 }}>
                      <Text type="danger" style={{ fontSize: 12 }}>
                        失败原因：{row.error}
                      </Text>
                    </div>
                  )}
                  {activeGroup === target.group_id && (
                    <List
                      size="small"
                      style={{ marginTop: 8 }}
                      dataSource={groupCandidates[target.group_id] ?? []}
                      locale={{ emptyText: "没有候选，换个关键词再试" }}
                      renderItem={(candidate) => (
                        <List.Item
                          actions={[
                            <Button
                              key="use"
                              type="primary"
                              size="small"
                              loading={applying === target.group_id}
                              onClick={() => void applyOne(target, candidate)}
                            >
                              用这条确认
                            </Button>,
                          ]}
                        >
                          <List.Item.Meta
                            title={
                              <Space wrap size={6}>
                                <span>{candidate.name || candidate.original_name}</span>
                                {candidate.year && <Tag color="blue">{candidate.year}</Tag>}
                                <Tag>{candidate.media_type === "movie" ? "电影" : "剧集"}</Tag>
                              </Space>
                            }
                          />
                        </List.Item>
                      )}
                    />
                  )}
                </div>
              );
            })}
            {targets.length === 0 && <Empty description="没有可批量处理的作品组" />}
          </>
        )}

        {Object.keys(results).length > 0 && (
          <>
            <Divider style={{ margin: "8px 0" }} />
            <Alert
              type={failed.length ? "warning" : "success"}
              showIcon
              message={`本次结果：成功 ${successCount} 组，失败 ${failed.length} 组`}
              description={
                failed.length ? (
                  <Space direction="vertical" size={2}>
                    {failed.map((target) => (
                      <Text key={target.group_id} type="secondary">
                        {target.title || target.group_id}：{results[target.group_id].error || "未知错误"}
                      </Text>
                    ))}
                    <Text type="secondary">失败的组可以重新搜索后再确认，不影响已经成功的组。</Text>
                  </Space>
                ) : (
                  "全部成功。回到扫描页后勾选状态和分组会保留。"
                )
              }
            />
          </>
        )}
      </Space>
    </Modal>
  );
}
