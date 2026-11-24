import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  Input,
  InputNumber,
  List,
  Modal,
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
  LinkOutlined,
  RobotOutlined,
  SearchOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { api } from "../api";
import type {
  AiMatchSuggestion,
  AiSeasonSuggestion,
  AiSpecialResponse,
  AiStatus,
  AutoMatchCandidate,
  AutoMatchResult,
  CollectionItem,
  IdentifyOverview,
  MatchCandidate,
  MatchGroup,
  MergeInfo,
  ParsedFile,
  PatternApplyResult,
  PatternCheckItem,
  PatternDecisionRecord,
  PatternDecisionValue,
  PatternFileValues,
  PatternProposeResult,
  PatternSampleRow,
  QueueView,
  ScanPayload,
  SeasonPlanCluster,
} from "../types";
import UnassignedPanel from "./UnassignedPanel";
import type { AssignPayload } from "./UnassignedPanel";
import PageSections from "../components/SectionNav";
import ListFilterBar, { EMPTY_FILTER, type ListFilterValue } from "../listFilters";

const { Text, Title } = Typography;

const seasonLabel = (value: number) => `S${String(value).padStart(2, "0")}`;

const NAME_SOURCE_LABELS: Record<string, string> = {
  "zh-CN": "简体中文",
  "zh-TW": "繁体中文",
  original: "日文原名",
  "en-US": "英文",
  manual: "手动改名",
};

const PATTERN_KIND_LABELS: Record<string, string> = {
  episode: "正片",
  special: "特典",
  movie: "剧场版",
  collection: "合集",
  unknown: "未识别",
};

type PatternDirStatus =
  | "pending"
  | "proposal"
  | "candidate_only"
  | "abandoned"
  | "cached"
  | "applied";

const PATTERN_STATUS_META: Record<PatternDirStatus, { label: string; color?: string }> = {
  pending: { label: "未分析" },
  proposal: { label: "可应用", color: "green" },
  candidate_only: { label: "只能当候选", color: "orange" },
  abandoned: { label: "已放弃" },
  cached: { label: "已套用缓存", color: "blue" },
  applied: { label: "已应用", color: "blue" },
};

const PATTERN_FIELD_LABELS: Record<string, string> = {
  kind: "类型",
  episode: "集号",
  season: "季号",
  special_no: "特典序号",
  version: "版本",
};

interface PatternDirRow {
  key: string;
  group_id: string;
  group_name: string;
  dir_rel: string;
  count: number;
  movies: number;
  cached: boolean;
  applied: boolean;
  source_label: string;
}

const dirOfFile = (row: ParsedFile) => {
  const parts = String(row.rel_path || "").replace(/\\/g, "/").split("/").filter(Boolean);
  return parts.slice(0, -1).join("/");
};

const patternKey = (groupId: string, dirRel: string) => `${groupId}||${dirRel}`;

const humanError = (error: unknown, fallback: string) => {
  const text = error instanceof Error ? error.message : "";
  return /[\u4e00-\u9fa5]/.test(text) ? text : fallback;
};

const describeValues = (values: PatternFileValues) => {
  const parts: string[] = [];
  if (values.kind) parts.push(PATTERN_KIND_LABELS[values.kind] ?? values.kind);
  if (values.season !== null && values.season !== undefined) {
    parts.push(`第 ${values.season} 季`);
  }
  if (values.episode !== null && values.episode !== undefined && values.episode !== "") {
    parts.push(`第 ${values.episode} 集`);
  }
  if (values.special_no !== null && values.special_no !== undefined) {
    parts.push(`特典 ${values.special_no}`);
  }
  if (values.version !== null && values.version !== undefined && values.version !== "") {
    parts.push(`v${values.version}`);
  }
  return parts.length ? parts.join(" · ") : "未识别";
};

const patternCheckColumns: ColumnsType<PatternCheckItem> = [
  { title: "检查项", dataIndex: "label", width: 230 },
  {
    title: "结果",
    dataIndex: "ok",
    width: 90,
    render: (ok: boolean) => <Tag color={ok ? "green" : "red"}>{ok ? "通过" : "没通过"}</Tag>,
  },
  {
    title: "说明",
    dataIndex: "detail",
    render: (value: string, record) => (
      <div>
        <Text style={{ fontSize: 12 }}>{value}</Text>
        {record.examples?.length > 0 && (
          <div>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {record.examples.join("、")}
            </Text>
          </div>
        )}
      </div>
    ),
  },
];

const patternSampleColumns: ColumnsType<PatternSampleRow> = [
  {
    title: "原文件名",
    dataIndex: "original",
    width: 210,
    render: (value: string, record) => (
      <div>
        <div className="preview-name" style={{ wordBreak: "break-all", fontSize: 12 }}>
          {value}
        </div>
        <Text type="secondary" style={{ fontSize: 12 }}>
          {record.position}
        </Text>
      </div>
    ),
  },
  {
    title: "新文件名 / 路径",
    width: 260,
    render: (_, record) =>
      record.new_name ? (
        <div>
          <div className="preview-name" style={{ wordBreak: "break-all", fontSize: 12 }}>
            {record.new_name}
          </div>
          <Text type="secondary" style={{ fontSize: 12 }} ellipsis={{ tooltip: record.new_path }}>
            {record.new_path}
          </Text>
        </div>
      ) : (
        <Text type="secondary" style={{ fontSize: 12 }}>
          没有生成新文件名
        </Text>
      ),
  },
  {
    title: "判断依据",
    width: 210,
    render: (_, record) =>
      record.evidence.length ? (
        <Text style={{ fontSize: 12 }}>{record.evidence.join("；")}</Text>
      ) : (
        <Text type="secondary" style={{ fontSize: 12 }}>
          —
        </Text>
      ),
  },
  {
    title: "可疑提示",
    width: 150,
    render: (_, record) =>
      record.suspects.length ? (
        <Tag color="orange" style={{ whiteSpace: "normal" }}>
          {record.suspects.join("；")}
        </Tag>
      ) : (
        <Text type="secondary" style={{ fontSize: 12 }}>
          未发现
        </Text>
      ),
  },
  {
    title: "内置 / AI 对照",
    width: 230,
    render: (_, record) => (
      <div style={{ fontSize: 12 }}>
        <div>内置：{describeValues(record.builtin)}</div>
        <div>AI：{describeValues(record.ai)}</div>
      </div>
    ),
  },
  {
    title: "改动",
    dataIndex: "changed",
    width: 90,
    render: (changed: boolean) =>
      changed ? <Tag color="orange">会改动</Tag> : <Tag>不变</Tag>,
  },
];

interface Props {
  scan: ScanPayload | null;
  queue: QueueView | null;
  onScanUpdate: (payload: ScanPayload) => void;
  reloadQueue: () => Promise<void>;
  onGoScan: () => void;
  onNext: () => void;
}

export default function MatchPage({
  scan,
  queue,
  onScanUpdate,
  reloadQueue,
  onGoScan,
  onNext,
}: Props) {
  const [checkedWorks, setCheckedWorks] = useState<string[]>([]);
  const [carrying, setCarrying] = useState(false);
  const [groups, setGroups] = useState<MatchGroup[]>([]);
  const [loading, setLoading] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [activeGroup, setActiveGroup] = useState<MatchGroup | null>(null);
  const [query, setQuery] = useState("");
  const [source, setSource] = useState<"tmdb" | "bangumi">("tmdb");
  const [candidates, setCandidates] = useState<MatchCandidate[]>([]);
  const [fallback, setFallback] = useState<MatchCandidate[]>([]);
  const [filters, setFilters] = useState<ListFilterValue>(EMPTY_FILTER);
  const [searching, setSearching] = useState(false);
  const [applying, setApplying] = useState(false);
  const [lastMerge, setLastMerge] = useState<MergeInfo | null>(null);
  const visibleMerge = useMemo<MergeInfo | null>(() => {
    if (lastMerge) return lastMerge;
    const merges = scan?.merges ?? [];
    if (!merges.length) return null;
    const last = merges[merges.length - 1];
    return {
      id: last.id,
      primary: last.primary,
      title: last.primary_title || "",
      merged_group_ids: (last.members ?? []).map((item) => item.group_id),
      count: (last.members?.length ?? 0) + 1,
    };
  }, [lastMerge, scan]);
  const [autoLoading, setAutoLoading] = useState(false);
  const [autoResults, setAutoResults] = useState<Record<string, AutoMatchResult>>({});
  const [autoSummary, setAutoSummary] = useState<{ auto: number; ai_pick?: number; choose: number; missing: number } | null>(null);
  const [renameGroup, setRenameGroup] = useState<MatchGroup | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [renaming, setRenaming] = useState(false);
  const [seasonPlans, setSeasonPlans] = useState<SeasonPlanCluster[]>([]);
  const [planCluster, setPlanCluster] = useState<SeasonPlanCluster | null>(null);
  const [planChoices, setPlanChoices] = useState<Record<string, number | undefined>>({});
  const [planNotes, setPlanNotes] = useState<Record<string, string>>({});
  const [planAiLoading, setPlanAiLoading] = useState(false);
  const [planApplying, setPlanApplying] = useState(false);
  const [applyingGroup, setApplyingGroup] = useState("");
  const [seasonSaving, setSeasonSaving] = useState("");
  const [seasonAi, setSeasonAi] = useState<
    Record<string, { loading: boolean; result?: AiSeasonSuggestion }>
  >({});
  const [overview, setOverview] = useState<IdentifyOverview | null>(null);
  const [identifyBusy, setIdentifyBusy] = useState(false);
  const [aiStatus, setAiStatus] = useState<AiStatus | null>(null);
  const [aiMatchLoading, setAiMatchLoading] = useState(false);
  const [aiMatchOpen, setAiMatchOpen] = useState(false);
  const [aiMatchSuggestions, setAiMatchSuggestions] = useState<AiMatchSuggestion[]>([]);
  const [aiMatchNote, setAiMatchNote] = useState("");
  const [patternResults, setPatternResults] = useState<Record<string, PatternProposeResult>>({});
  const [patternDecisions, setPatternDecisions] = useState<Record<string, PatternDecisionRecord>>({});
  const [patternTarget, setPatternTarget] = useState<PatternDirRow | null>(null);
  const [patternOpen, setPatternOpen] = useState(false);
  const [patternBusy, setPatternBusy] = useState("");
  const [patternDecisionSaving, setPatternDecisionSaving] = useState("");
  const [patternApplying, setPatternApplying] = useState(false);

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

  const loadPlan = async (scanId: string) => {
    try {
      const result = await api.matchSeasonPlan(scanId);
      setSeasonPlans(result.clusters);
    } catch {
      setSeasonPlans([]);
    }
  };

  const refreshAfterIdentify = async (scanId: string) => {
    const [groupResult, fresh] = await Promise.all([
      api.matchGroups(scanId),
      api.getScan(scanId),
    ]);
    setGroups(groupResult.groups);
    onScanUpdate(fresh);
    await loadPlan(scanId);
    await reloadQueue();
  };

  const loadPatternMeta = async () => {
    try {
      const result = await api.aiPatterns();
      const map: Record<string, PatternDecisionRecord> = {};
      for (const item of result.decisions) {
        map[patternKey(item.group_id, item.dir_rel)] = item;
      }
      setPatternDecisions(map);
    } catch {
      // 读不到取舍记录不影响匹配主流程，目录状态会按扫描结果显示
    }
  };

  useEffect(() => {
    if (scan) {
      void loadPatternMeta();
    } else {
      setPatternResults({});
      setPatternDecisions({});
    }
  }, [scan?.scan_id]);

  useEffect(() => {
    api.aiStatus().then(setAiStatus).catch(() => setAiStatus(null));
  }, []);

  useEffect(() => {
    if (scan) {
      loadGroups(scan.scan_id);
      loadIdentify(scan.scan_id);
      loadPlan(scan.scan_id);
    } else {
      setGroups([]);
      setOverview(null);
      setSeasonPlans([]);
    }
  }, [scan?.scan_id]);

  useEffect(() => {
    setCheckedWorks(scan?.selections?.match ?? []);
  }, [scan?.scan_id]);

  const persistSelection = (ids: string[]) => {
    if (scan) void api.saveSelection(scan.scan_id, "match", ids).catch(() => undefined);
  };

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

  const handleCreateAlias = async (payload: {
    alias: string;
    title: string;
    year?: number | null;
  }) => {
    if (!scan) return;
    setIdentifyBusy(true);
    try {
      await api.createAlias(payload);
      const result = await api.identify(scan.scan_id);
      setOverview(result);
    } finally {
      setIdentifyBusy(false);
    }
  };

  const handleUpdateAlias = async (
    id: string,
    payload: { alias: string; title: string; year?: number | null },
  ) => {
    if (!scan) return;
    setIdentifyBusy(true);
    try {
      await api.updateAlias(id, payload);
      const result = await api.identify(scan.scan_id);
      setOverview(result);
    } finally {
      setIdentifyBusy(false);
    }
  };

  const handleDeleteAlias = async (id: string) => {
    if (!scan) return;
    setIdentifyBusy(true);
    try {
      await api.deleteAlias(id);
      const result = await api.identify(scan.scan_id);
      setOverview(result);
      message.success("已删除这条别名");
    } catch (error) {
      message.error(`删除失败：${(error as Error).message}`);
    } finally {
      setIdentifyBusy(false);
    }
  };

  const handleDeleteSpecialMemory = async (id: string) => {
    if (!scan) return;
    setIdentifyBusy(true);
    try {
      await api.deleteSpecialMemory(id);
      const result = await api.identify(scan.scan_id);
      setOverview(result);
      message.success("已删除这条特典目录记录");
    } catch (error) {
      message.error(`删除失败：${(error as Error).message}`);
    } finally {
      setIdentifyBusy(false);
    }
  };

  const applyCollection = async (
    item: CollectionItem,
    action: "keep" | "season" | "clear",
    season?: number,
  ) => {
    if (!scan) return;
    setIdentifyBusy(true);
    try {
      const result = await api.identifyCollection({
        scan_id: scan.scan_id,
        file_id: item.file_id,
        action,
        season,
      });
      setOverview(result);
      message.success(
        action === "clear"
          ? "已取消选择，这个合集不会自动执行"
          : "合集处理方式已保存，预览页会按这个选择生成",
      );
    } catch (error) {
      message.error(`保存合集选择失败：${(error as Error).message}`);
    } finally {
      setIdentifyBusy(false);
    }
  };

  const handleSpecial = async (
    folderKey: string,
    action: "assign" | "keep",
    workId?: string,
  ) => {
    if (!scan) return;
    setIdentifyBusy(true);
    try {
      const result = await api.identifySpecial({
        scan_id: scan.scan_id,
        folder_key: folderKey,
        action,
        work_id: workId,
      });
      setOverview(result);
      await refreshAfterIdentify(scan.scan_id);
    } finally {
      setIdentifyBusy(false);
    }
  };

  const openSearch = (group: MatchGroup) => {
    setActiveGroup(group);
    setQuery(group.title);
    setCandidates([]);
    setFallback([]);
    setSource("tmdb");
    setSearchOpen(true);
  };

  const runSearch = async (
    text?: string,
    target: "tmdb" | "bangumi" = source,
    groupOverride?: MatchGroup,
  ) => {
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
        group_id: (groupOverride ?? activeGroup)?.group_id,
        query: keyword,
        source: target,
      });
      setCandidates(result.candidates);
      setFallback(result.fallback ?? []);
      if (!result.candidates.length) {
        if (result.fallback?.length) {
          message.info("TMDB 没有结果，下面是 Bangumi 的备用候选，可以点「用该名称搜 TMDB」再试一次");
        } else {
          message.info("没有找到候选，可换个关键词或改用其他数据源");
        }
      }
    } catch (error) {
      message.error((error as Error).message);
    } finally {
      setSearching(false);
    }
  };

  const runAiMatch = async () => {
    if (!scan) return;
    setAiMatchLoading(true);
    try {
      const result = await api.aiMatchSuggest({ scan_id: scan.scan_id });
      setAiMatchSuggestions(result.suggestions);
      setAiMatchNote(result.message);
      setAiMatchOpen(true);
      if (!result.suggestions.length) {
        message.info(result.message || "AI 没有给出建议");
      }
    } catch (error) {
      message.error(`AI 辅助匹配失败：${(error as Error).message}`);
    } finally {
      setAiMatchLoading(false);
    }
  };

  const searchWithAi = (suggestion: AiMatchSuggestion) => {
    const group = groups.find((item) => item.group_id === suggestion.group_id);
    if (!group) {
      message.warning("这部作品不在本页清单里，可能还没从扫描页带出");
      return;
    }
    setAiMatchOpen(false);
    setActiveGroup(group);
    setQuery(suggestion.query);
    setCandidates([]);
    setSource("tmdb");
    setSearchOpen(true);
    void runSearch(suggestion.query, "tmdb", group);
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
      if (result.merged?.length) setLastMerge(result.merged[result.merged.length - 1]);
      const fresh = await api.getScan(scan.scan_id);
      onScanUpdate(fresh);
      await loadPlan(scan.scan_id);
      setSearchOpen(false);
      setAutoResults((prev) => {
        if (!prev[group.group_id]) return prev;
        const next = { ...prev };
        delete next[group.group_id];
        return next;
      });
      message.success(`已匹配「${result.meta.name}」，更新 ${result.updated} 个文件`);
      const matchedGroup = result.groups.find((item) => item.group_id === group.group_id);
      if (matchedGroup?.season_pending) {
        message.info(
          `TMDB 有 ${matchedGroup.season_options?.length ?? 0} 个正式季，请在「季」列确认季号后再预览`,
        );
      }
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

  const undoMerge = async () => {
    if (!scan || !visibleMerge) return;
    try {
      const result = await api.matchMergeUndo({
        scan_id: scan.scan_id,
        merge_id: visibleMerge.id,
      });
      setGroups(result.groups);
      const fresh = await api.getScan(scan.scan_id);
      onScanUpdate(fresh);
      await loadPlan(scan.scan_id);
      setLastMerge(null);
      message.success("已撤销合并，恢复成合并前的作品组");
    } catch (error) {
      message.error(`撤销合并失败：${(error as Error).message}`);
    }
  };

  const applySeason = async (group: MatchGroup, season: number) => {
    if (!scan) return;
    setSeasonSaving(group.group_id);
    try {
      const result = await api.matchSeason({
        scan_id: scan.scan_id,
        group_id: group.group_id,
        season,
      });
      setGroups(result.groups);
      const fresh = await api.getScan(scan.scan_id);
      onScanUpdate(fresh);
      await loadPlan(scan.scan_id);
      message.success(
        `已把「${group.match?.name ?? group.title}」的季号定为 ${seasonLabel(season)}，预览会按这一季重新生成`,
      );
    } catch (error) {
      message.error(`保存季号失败：${(error as Error).message}`);
    } finally {
      setSeasonSaving("");
    }
  };

  const openRename = (group: MatchGroup) => {
    setRenameGroup(group);
    setRenameValue(group.match?.name || group.title);
  };

  const submitRename = async () => {
    if (!scan || !renameGroup) return;
    const name = renameValue.trim();
    if (!name) {
      message.warning("作品名不能为空");
      return;
    }
    setRenaming(true);
    try {
      const result = await api.matchRename({
        scan_id: scan.scan_id,
        group_id: renameGroup.group_id,
        name,
      });
      setGroups(result.groups);
      const fresh = await api.getScan(scan.scan_id);
      onScanUpdate(fresh);
      setRenameGroup(null);
      message.success(`已把作品名改为「${result.name}」`);
    } catch (error) {
      message.error(`改名失败：${(error as Error).message}`);
    } finally {
      setRenaming(false);
    }
  };

  const openSeasonPlan = (cluster: SeasonPlanCluster) => {
    const choices: Record<string, number | undefined> = {};
    for (const group of cluster.groups) {
      if (group.manual) continue;
      choices[group.group_id] = group.proposed_season ?? undefined;
    }
    setPlanChoices(choices);
    setPlanNotes({});
    setPlanCluster(cluster);
  };

  const runPlanAi = async () => {
    if (!scan || !planCluster) return;
    if (!aiStatus?.configured) {
      message.warning(aiStatus?.message ?? "AI 辅助还没配置，请先到设置页填写");
      return;
    }
    setPlanAiLoading(true);
    const choices = { ...planChoices };
    const notes = { ...planNotes };
    try {
      for (const group of planCluster.groups) {
        if (group.manual) continue;
        try {
          const result = await api.aiSeasonSuggest({
            scan_id: scan.scan_id,
            work_id: group.group_id,
            title: planCluster.name || group.title,
            year: group.year,
            seasons: planCluster.seasons,
          });
          if (result.season !== null) {
            choices[group.group_id] = result.season;
            notes[group.group_id] = `AI 建议第 ${result.season} 季${
              result.reason ? `：${result.reason}` : ""
            }，确认后才会生效`;
          } else {
            notes[group.group_id] = result.message || "AI 没能确定季号，请手动选择";
          }
        } catch (error) {
          notes[group.group_id] = `AI 判断失败：${(error as Error).message}`;
        }
      }
      setPlanChoices(choices);
      setPlanNotes(notes);
    } finally {
      setPlanAiLoading(false);
    }
  };

  const applySeasonPlan = async () => {
    if (!scan || !planCluster) return;
    const pending = planCluster.groups.filter(
      (group) =>
        !group.manual &&
        planChoices[group.group_id] !== undefined &&
        planChoices[group.group_id] !== group.season_choice,
    );
    if (!pending.length) {
      message.info("没有需要分配的季号：可以先用 AI 判断或手动选择");
      return;
    }
    setPlanApplying(true);
    const failures: string[] = [];
    let latestGroups = groups;
    let done = 0;
    try {
      for (const group of pending) {
        const season = planChoices[group.group_id];
        if (season === undefined) continue;
        try {
          const result = await api.matchSeason({
            scan_id: scan.scan_id,
            group_id: group.group_id,
            season,
          });
          latestGroups = result.groups;
          done += 1;
        } catch (error) {
          failures.push(`${group.title}：${(error as Error).message}`);
        }
      }
      setGroups(latestGroups);
      const fresh = await api.getScan(scan.scan_id);
      onScanUpdate(fresh);
      await loadPlan(scan.scan_id);
      setPlanCluster(null);
      if (failures.length) {
        message.warning(`已分配 ${done} 组，${failures.length} 组失败：${failures.join("；")}`);
      } else {
        message.success(`已为 ${done} 组分配季号`);
      }
    } finally {
      setPlanApplying(false);
    }
  };

  const planCollisions = (() => {
    if (!planCluster) return [];
    const bucket = new Map<string, { season: number; episode: number; titles: Set<string> }>();
    for (const group of planCluster.groups) {
      const season = group.manual
        ? group.season_choice ?? group.current_season
        : planChoices[group.group_id] ?? group.current_season;
      for (const episode of group.episodes) {
        const key = `${season}:${episode}`;
        const entry = bucket.get(key) ?? { season, episode, titles: new Set<string>() };
        entry.titles.add(group.title);
        bucket.set(key, entry);
      }
    }
    return [...bucket.values()]
      .filter((item) => item.titles.size > 1)
      .map((item) => ({ season: item.season, episode: item.episode, groups: [...item.titles] }))
      .sort((a, b) => a.season - b.season || a.episode - b.episode);
  })();

  const suggestSeason = async (group: MatchGroup) => {
    if (!scan) return;
    if (!aiStatus?.configured) {
      message.warning(aiStatus?.message ?? "AI 辅助还没配置，请先到设置页填写");
      return;
    }
    setSeasonAi((prev) => ({ ...prev, [group.group_id]: { loading: true } }));
    try {
      const result = await api.aiSeasonSuggest({
        scan_id: scan.scan_id,
        work_id: group.group_id,
        title: group.match?.name ?? group.title,
        year: group.year,
        seasons: group.season_options ?? [],
      });
      setSeasonAi((prev) => ({ ...prev, [group.group_id]: { loading: false, result } }));
      if (result.season !== null) {
        message.info(`AI 建议 ${seasonLabel(result.season)}，点「采用」后才会生效`);
      } else {
        message.warning(result.message || "AI 没能确定季号，请手动选择");
      }
    } catch (error) {
      setSeasonAi((prev) => ({ ...prev, [group.group_id]: { loading: false } }));
      message.error(`AI 推荐季号失败：${(error as Error).message}`);
    }
  };

  const confirmAllPending = async () => {
    if (!scan) return;
    setIdentifyBusy(true);
    try {
      const result = await api.identifyConfirmAll(scan.scan_id);
      setOverview(result);
      await refreshAfterIdentify(scan.scan_id);
      message.success(`已确认 ${result.confirmed} 条待确认记录`);
    } catch (error) {
      message.error(`一键确认失败：${(error as Error).message}`);
    } finally {
      setIdentifyBusy(false);
    }
  };

  const runAiSpecial = async (): Promise<AiSpecialResponse> => {
    if (!scan) throw new Error("扫描结果已失效，请重新扫描");
    return api.aiSpecialSuggest({ scan_id: scan.scan_id });
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
      if (result.merged?.length) setLastMerge(result.merged[result.merged.length - 1]);
      setAutoResults(
        Object.fromEntries(result.results.map((item) => [item.group_id, item])),
      );
      setAutoSummary(result.summary);
      const fresh = await api.getScan(scan.scan_id);
      onScanUpdate(fresh);
      await loadPlan(scan.scan_id);
      const parts = [
        `自动匹配 ${result.summary.auto} 个`,
        ...(result.summary.ai_pick ? [`AI 挑选 ${result.summary.ai_pick} 个`] : []),
        `待选择 ${result.summary.choose} 个`,
        `需手动 ${result.summary.missing} 个`,
      ];
      if (result.summary.auto + (result.summary.ai_pick ?? 0) > 0) message.success(parts.join("，"));
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

  const matchEntries = (queue?.entries ?? []).filter((entry) => entry.stage === "match");
  const workIdsWithMatch = new Set(
    matchEntries.map((entry) => entry.work_id).filter(Boolean) as string[],
  );
  const visibleGroups = groups.filter((group) => workIdsWithMatch.has(group.group_id));

  const filesById = new Map<string, ParsedFile>((scan?.files ?? []).map((file) => [file.id, file]));
  const workFiles = new Map<string, ParsedFile[]>();
  const workDirGroups = new Map<string, string[]>();
  for (const entry of matchEntries) {
    const workId = entry.work_id || "";
    if (!workId) continue;
    const files = workFiles.get(workId) ?? [];
    for (const fileId of entry.file_ids) {
      const file = filesById.get(fileId);
      if (file) files.push(file);
    }
    workFiles.set(workId, files);
    const dirs = workDirGroups.get(workId) ?? [];
    if (entry.group_id && !dirs.includes(entry.group_id)) dirs.push(entry.group_id);
    workDirGroups.set(workId, dirs);
  }
  const groupOptions = Array.from(
    new Set([...(scan?.groups ?? []).map((group) => group.id), ...matchEntries.map((entry) => entry.group_id || "")]),
  )
    .filter(Boolean)
    .map((id) => ({
      value: id,
      label: (scan?.groups ?? []).find((group) => group.id === id)?.name || id,
    }));

  const filteredGroups = visibleGroups.filter((group) => {
    const text = filters.keyword.trim().toLowerCase();
    if (text) {
      const filenames = (workFiles.get(group.group_id) ?? []).map((file) => file.filename).join(" ");
      const haystack = `${group.title} ${group.match?.name ?? ""} ${filenames}`.toLowerCase();
      if (!haystack.includes(text)) return false;
    }
    if (filters.statuses.length) {
      const status = group.match ? "matched" : group.needs_confirm ? "pending" : "unmatched";
      if (!filters.statuses.includes(status)) return false;
    }
    if (filters.types.length) {
      const type = group.video_count > 0 ? "video" : "other";
      if (!filters.types.includes(type)) return false;
    }
    if (filters.groupIds.length) {
      const dirs = workDirGroups.get(group.group_id) ?? [];
      if (!dirs.some((id) => filters.groupIds.includes(id))) return false;
    }
    if (filters.selection === "selected" && !checkedWorks.includes(group.group_id)) return false;
    if (filters.selection === "unselected" && checkedWorks.includes(group.group_id)) return false;
    return true;
  });
  const filteredSelected = filteredGroups.filter((group) => checkedWorks.includes(group.group_id)).length;

  const carryNext = async () => {
    if (!scan) return;
    const entryIds = matchEntries
      .filter((entry) => entry.work_id && checkedWorks.includes(entry.work_id))
      .map((entry) => entry.entry_id);
    if (!entryIds.length) {
      message.warning("请先勾选要带出的作品");
      return;
    }
    setCarrying(true);
    try {
      const count = checkedWorks.length;
      await api.queueMove(scan.scan_id, entryIds, "preview");
      setCheckedWorks([]);
      persistSelection([]);
      await reloadQueue();
      message.success(`已带出 ${count} 个作品到「模板与预览」`);
      onNext();
    } catch (error) {
      message.error(`带出失败：${(error as Error).message}`);
    } finally {
      setCarrying(false);
    }
  };

  // ---- AI 命名规律：按「目录组 + 子目录」列出可分析的目录 ----
  const patternDirs = useMemo<PatternDirRow[]>(() => {
    if (!scan) return [];
    const groupNames = new Map((scan.groups ?? []).map((item) => [item.id, item.name]));
    const map = new Map<string, PatternDirRow>();
    for (const file of scan.files ?? []) {
      if (file.category !== "video") continue;
      const groupId = String(file.group_id || "");
      const dirRel = dirOfFile(file);
      const key = patternKey(groupId, dirRel);
      const row =
        map.get(key) ?? {
          key,
          group_id: groupId,
          group_name: groupNames.get(groupId) || groupId || "未分组",
          dir_rel: dirRel,
          count: 0,
          movies: 0,
          cached: false,
          applied: false,
          source_label: "",
        };
      row.count += 1;
      if (String(file.kind) === "movie") row.movies += 1;
      const candidate = file.pattern_candidate;
      if (candidate?.source === "cache_hit" && candidate.auto_applied) {
        row.cached = true;
        row.source_label = candidate.source_label || "AI 规律（缓存命中）";
      } else if (candidate?.confirmed) {
        row.applied = true;
        row.source_label = candidate.source_label || "AI 规律（已确认）";
      }
      map.set(key, row);
    }
    return [...map.values()].sort(
      (a, b) =>
        a.group_name.localeCompare(b.group_name, "zh-CN") ||
        a.dir_rel.localeCompare(b.dir_rel, "zh-CN"),
    );
  }, [scan]);

  const patternStatusOf = (dir: PatternDirRow): PatternDirStatus => {
    const result = patternResults[dir.key];
    if (result?.status === "proposal") return "proposal";
    if (result?.status === "candidate_only") return "candidate_only";
    if (result?.status === "abandoned") return "abandoned";
    if (dir.cached) return "cached";
    if (dir.applied) return "applied";
    if (patternDecisions[dir.key]?.decision === "rejected") return "abandoned";
    return "pending";
  };

  const patternResult = patternTarget ? patternResults[patternTarget.key] : null;

  const analyzePattern = async (dir: PatternDirRow) => {
    if (!scan) return;
    setPatternBusy(dir.key);
    try {
      const result = await api.aiPatternPropose({
        scan_id: scan.scan_id,
        group_id: dir.group_id,
        dir_rel: dir.dir_rel,
      });
      setPatternResults((prev) => ({ ...prev, [dir.key]: result }));
      setPatternTarget(dir);
      setPatternOpen(true);
      if (result.status === "proposal") {
        message.success("分析完成：5 项自检全部通过，确认后才会写入");
      } else if (result.status === "candidate_only") {
        message.warning("分析完成：只能当候选，不会自动写入");
      } else {
        message.warning(result.message || "这次分析没有通过，已整批放弃");
      }
    } catch (error) {
      message.error(`分析失败：${humanError(error, "请检查 AI 设置后重试")}`);
    } finally {
      setPatternBusy("");
    }
  };

  const openPatternResult = (dir: PatternDirRow) => {
    setPatternTarget(dir);
    setPatternOpen(true);
  };

  const decidePattern = async (decision: PatternDecisionValue) => {
    if (!patternTarget) return;
    const target = patternTarget;
    setPatternDecisionSaving(decision);
    try {
      const entry = await api.aiPatternDecision({
        group_id: target.group_id,
        dir_rel: target.dir_rel,
        decision,
        structure: patternResults[target.key]?.structure,
      });
      setPatternDecisions((prev) => ({ ...prev, [target.key]: entry }));
      if (decision === "rejected") {
        setPatternResults((prev) => {
          const next = { ...prev };
          delete next[target.key];
          return next;
        });
        message.success("已记下：不再提醒这个目录；需要时仍可重新分析");
      } else if (decision === "later") {
        message.info("已记下：稍后再看");
      } else {
        message.success("已记下：采用这条规律");
        if (patternResults[target.key]?.status === "proposal") {
          // 采用后直接进入写入确认；仍然要点「确认应用」才会改识别结果
          confirmApplyPattern();
        }
      }
    } catch (error) {
      message.error(`记录失败：${humanError(error, "请稍后再试")}`);
    } finally {
      setPatternDecisionSaving("");
    }
  };

  const confirmApplyPattern = () => {
    if (!scan || !patternTarget || !patternResult?.structure) return;
    const target = patternTarget;
    const result = patternResult;
    const written = Math.max(target.count - target.movies, 0);
    Modal.confirm({
      title: "确认应用这份命名规律？",
      content: (
        <div>
          <div>
            目录：{target.group_name} / {target.dir_rel || "（根目录）"}
          </div>
          <div style={{ marginTop: 8 }}>
            将写入 <strong>{written}</strong> 行识别结果，跳过 <strong>{target.movies}</strong> 行（剧场版不参与）。
          </div>
          <div style={{ marginTop: 8, color: "var(--ani-text-tertiary)" }}>
            只改识别结果，不改文件名。应用前会再自检一次，不通过就整批取消。
          </div>
        </div>
      ),
      okText: "确认应用",
      cancelText: "先不应用",
      onOk: async () => {
        setPatternApplying(true);
        try {
          const applied: PatternApplyResult = await api.aiPatternApply({
            scan_id: scan.scan_id,
            group_id: target.group_id,
            dir_rel: target.dir_rel,
            structure: result.structure!,
            source: "ai",
          });
          if (applied.written > 0) {
            message.success(`已写入 ${applied.written} 行，跳过 ${applied.skipped} 行`);
            setPatternOpen(false);
            setPatternResults((prev) => {
              const next = { ...prev };
              delete next[target.key];
              return next;
            });
            await refreshAfterIdentify(scan.scan_id);
            await loadPatternMeta();
          } else {
            message.warning(applied.message || "没有写入任何内容");
          }
        } catch (error) {
          message.error(`应用失败：${humanError(error, "请稍后再试")}`);
        } finally {
          setPatternApplying(false);
        }
      },
    });
  };

  const patternColumns: ColumnsType<PatternDirRow> = [
    {
      title: "目录组",
      dataIndex: "group_name",
      width: 190,
      render: (value: string) => <Text strong>{value}</Text>,
    },
    {
      title: "子目录",
      dataIndex: "dir_rel",
      render: (value: string) => (
        <span className="mono" style={{ wordBreak: "break-all", fontSize: 12 }}>
          {value || "（根目录）"}
        </span>
      ),
    },
    {
      title: "视频",
      dataIndex: "count",
      width: 110,
      render: (value: number, record) => (
        <Text type="secondary">
          {value} 个{record.movies > 0 ? `（剧场版 ${record.movies}）` : ""}
        </Text>
      ),
    },
    {
      title: "状态",
      width: 130,
      render: (_, record) => {
        const status = patternStatusOf(record);
        const meta = PATTERN_STATUS_META[status];
        return (
          <Space direction="vertical" size={0}>
            <Tag color={meta.color}>{meta.label}</Tag>
            {status === "abandoned" && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                不再主动提醒
              </Text>
            )}
            {(status === "cached" || status === "applied") && record.source_label && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                {record.source_label}
              </Text>
            )}
          </Space>
        );
      },
    },
    {
      title: "操作",
      width: 260,
      render: (_, record) => {
        const result = patternResults[record.key];
        return (
          <Space size={4} wrap>
            <Tooltip
              title={
                !aiStatus
                  ? "AI 状态加载中"
                  : !aiStatus.configured
                    ? aiStatus.message
                    : "内置规则优先；只有手动点击才会调用 AI，只发送目录名和文件名"
              }
            >
              <Button
                size="small"
                icon={<RobotOutlined />}
                loading={patternBusy === record.key}
                disabled={!aiStatus?.configured || Boolean(patternBusy)}
                onClick={() => void analyzePattern(record)}
              >
                {patternBusy === record.key
                  ? "正在分析…"
                  : result
                    ? "重新分析"
                    : "AI 分析命名规律"}
              </Button>
            </Tooltip>
            {result && (
              <Button size="small" type="link" onClick={() => openPatternResult(record)}>
                查看结果
              </Button>
            )}
          </Space>
        );
      },
    },
  ];

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
          {record.match?.name && (
            <Space size={4} wrap style={{ marginTop: 2 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                元数据名称：{record.match.name}
              </Text>
              {record.match.name_source && (
                <Tag color={record.match.name_source === "manual" ? "purple" : "geekblue"}>
                  名称来源：{NAME_SOURCE_LABELS[record.match.name_source] ?? record.match.name_source}
                </Tag>
              )}
            </Space>
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
      width: 310,
      render: (value: number[], record: MatchGroup) => {
        const defaultSeason = value?.[0] ?? 1;
        const options = record.season_options ?? [];
        if (!record.match) {
          return (
            <Space direction="vertical" size={0}>
              <Tag>{seasonLabel(defaultSeason)}（默认）</Tag>
              <Text type="secondary" style={{ fontSize: 12 }}>未匹配 TMDB，按扫描默认季</Text>
            </Space>
          );
        }
        if (!options.length) {
          return (
            <Space direction="vertical" size={0}>
              <Tag>{seasonLabel(defaultSeason)}（默认）</Tag>
              <Text type="secondary" style={{ fontSize: 12 }}>重新匹配后可选择季号</Text>
            </Space>
          );
        }
        if (options.length === 1) {
          return (
            <Space direction="vertical" size={0}>
              <Tag color="green">{seasonLabel(options[0].season_number)}（自动）</Tag>
              <Text type="secondary" style={{ fontSize: 12 }}>TMDB 只有 1 个正式季</Text>
            </Space>
          );
        }
        return (
          <div>
            <Select
              size="small"
              style={{ width: 232 }}
              loading={seasonSaving === record.group_id}
              value={record.season_choice ?? undefined}
              placeholder={`请选择季号${
                record.season_recommended ? `（推荐 ${seasonLabel(record.season_recommended)}）` : ""
              }`}
              onChange={(season) => void applySeason(record, season)}
              options={options.map((item) => ({
                value: item.season_number,
                label: `${seasonLabel(item.season_number)}${
                  item.name ? ` · ${item.name}` : ""
                } · ${item.episode_count} 集${
                  item.season_number === record.season_recommended ? "（推荐）" : ""
                }`,
              }))}
            />
            <div style={{ marginTop: 4 }}>
              <Text
                type={record.season_pending ? "warning" : "secondary"}
                style={{ fontSize: 12 }}
              >
                {record.season_pending
                  ? `TMDB 有 ${options.length} 个正式季，请确认后再预览；不选则按默认 ${seasonLabel(defaultSeason)}`
                  : "切换季号后预览会按新季重新生成"}
              </Text>
            </div>
            <Space size={4} wrap style={{ marginTop: 4 }}>
              <Tooltip
                title={
                  !aiStatus
                    ? "AI 状态加载中"
                    : !aiStatus.configured
                      ? aiStatus.message
                      : "只发送作品名、季名和文件名给 AI，返回的建议不会自动生效"
                }
              >
                <Button
                  size="small"
                  icon={<RobotOutlined />}
                  loading={seasonAi[record.group_id]?.loading}
                  disabled={!aiStatus?.configured || seasonSaving === record.group_id}
                  onClick={() => void suggestSeason(record)}
                >
                  AI 推荐季号
                </Button>
              </Tooltip>
              {seasonAi[record.group_id]?.result?.season !== null &&
                seasonAi[record.group_id]?.result?.season !== undefined && (
                  <>
                    <Tag color="blue">
                      AI 建议 {seasonLabel(seasonAi[record.group_id].result!.season!)}
                    </Tag>
                    <Button
                      size="small"
                      type="link"
                      disabled={seasonSaving === record.group_id}
                      onClick={() =>
                        void applySeason(record, seasonAi[record.group_id].result!.season!)
                      }
                    >
                      采用
                    </Button>
                  </>
                )}
            </Space>
            {seasonAi[record.group_id]?.result && (
              <Text type="secondary" style={{ fontSize: 12, display: "block", marginTop: 2 }}>
                {seasonAi[record.group_id].result!.message}
                {seasonAi[record.group_id].result!.reason
                  ? `（${seasonAi[record.group_id].result!.reason}）`
                  : ""}
              </Text>
            )}
          </div>
        );
      },
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
              {info?.status === "ai_pick" && <Tag color="geekblue">AI 挑选</Tag>}
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
      width: 220,
      render: (_, record) => (
        <Space size={4} wrap>
          <Button
            type={record.match ? "default" : "primary"}
            icon={<SearchOutlined />}
            onClick={() => openSearch(record)}
          >
            {record.match ? "重新匹配" : "搜索匹配"}
          </Button>
          {record.match && (
            <Button size="small" type="link" onClick={() => openRename(record)}>
              手动改名
            </Button>
          )}
        </Space>
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
    <PageSections
      items={[
        { id: "sec-match", label: "匹配作品" },
        ...(!scan.is_sample ? [{ id: "sec-ai-pattern", label: "AI 命名规律" }] : []),
        ...(overview ? [{ id: "sec-unassigned", label: "未归属区" }] : []),
        ...(overview && overview.collections.length > 0
          ? [{ id: "sec-collections", label: "合集文件" }]
          : []),
        { id: "sec-works", label: "本页作品清单" },
      ]}
      primaryAction={{
        label: "下一步",
        hint: `${checkedWorks.length}/${groups.length}`,
        disabled: checkedWorks.length === 0 || carrying,
        loading: carrying,
        onClick: () => void carryNext(),
      }}
    >
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      {visibleMerge && (
        <Alert
          type="success"
          showIcon
          message={`已自动合并 ${visibleMerge.count} 个「${visibleMerge.title}」作品组`}
          description="组标题已统一；勾选状态、未归属指派和已有季号都保留。只允许撤销最近一次合并。"
          action={
            <Button size="small" onClick={() => void undoMerge()}>
              撤销合并
            </Button>
          }
        />
      )}
      <Card id="sec-match" data-section>
        <Row gutter={16} align="middle">
          <Col flex="auto">
            <Title level={5} style={{ marginTop: 0 }}>
              2. 匹配作品（可选）
            </Title>
            <Text type="secondary">
              点「一键自动匹配」会逐个搜索 TMDB：唯一确定的作品自动套用，多个相似结果直接在下拉框里选，搜不到再手动搜索。
              开启 AI 辅助后，本地搜不到会换关键词重试、多个候选分不清会让 AI 只从候选里挑编号；作品名始终取 TMDB 返回值，
              并标出名称来源，可在「操作」列手动改名覆盖。
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
              <Tooltip
                title={
                  !aiStatus
                    ? "AI 状态加载中"
                    : !aiStatus.configured
                      ? aiStatus.message
                      : "一次把本页作品的标题、年份和文件名发给 AI，只返回搜索关键词建议"
                }
              >
                <Button
                  icon={<RobotOutlined />}
                  loading={aiMatchLoading}
                  disabled={scan.is_sample || !aiStatus?.configured}
                  onClick={runAiMatch}
                >
                  AI 辅助匹配
                </Button>
              </Tooltip>
              <Text type="secondary">
                已选（筛选后）{filteredSelected} 个作品 · 筛选结果 {filteredGroups.length} 个 / 总已选{" "}
                {checkedWorks.length}
              </Text>
            </Space>
          </Col>
        </Row>
      </Card>

      {scan.is_sample && (
        <Alert type="warning" showIcon message="当前是内置示例数据，匹配结果只用于演示，不会影响真实文件。" />
      )}

      {!scan.is_sample && (
        <Card
          id="sec-ai-pattern"
          data-section
          title={
            <Space>
              <RobotOutlined />
              AI 分析命名规律（可选）
            </Space>
          }
          extra={<Text type="secondary">内置规则优先，AI 只在手动分析时才用</Text>}
        >
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            message="内置规则优先：只有你手动点「分析」时，才会让 AI 试一次"
            description="内置规则能识别的目录不需要用这里；内置规则解释不了时，再点「分析」让 AI 试着提一套分段规律。只把目录名和文件名发给 AI，不发视频内容；一次只分析一个子目录。分析通过 5 项自检后才会出现「应用」按钮，应用前还会再确认一次要改多少行。已经点过「不采用」的目录不会再主动提醒。"
          />
          <Table
            rowKey="key"
            size="small"
            columns={patternColumns}
            dataSource={patternDirs}
            pagination={{
              pageSize: 8,
              showSizeChanger: false,
              showTotal: (total) => `共 ${total} 个目录`,
            }}
            locale={{ emptyText: <Empty description="扫描结果里还没有视频目录" /> }}
            scroll={{ x: 880 }}
          />
        </Card>
      )}

      {!scan.is_sample &&
        seasonPlans.map((cluster) => (
          <Alert
            key={cluster.tmdb_id}
            type={cluster.collisions.length ? "warning" : "info"}
            showIcon
            message={
              `这 ${cluster.groups.length} 组指向同一部作品「${cluster.name}」` +
              (cluster.looks_like.length > 1 ? `，看起来是第 ${cluster.looks_like.join("、")} 季` : "")
            }
            description={
              cluster.before_collisions
                ? `目前有 ${cluster.before_collisions} 处目标路径撞车。可以按目录名里的季号线索分配；没有线索的组用 AI 判断或手动选择。`
                : "要调整季号吗？可以按目录名里的线索分配；没有线索的组用 AI 判断或手动选择。"
            }
            action={
              <Button size="small" type="primary" onClick={() => openSeasonPlan(cluster)}>
                分配季号
              </Button>
            }
          />
        ))}

      {overview && (
        <div id="sec-unassigned" data-section>
          <UnassignedPanel
          scan={scan}
          overview={overview}
          works={groups}
          busy={identifyBusy}
          onAssign={handleAssign}
          onIgnore={handleIgnore}
          onConfirm={handleConfirm}
          onDeleteAssignment={handleDeleteAssignment}
          onCreateAlias={handleCreateAlias}
          onUpdateAlias={handleUpdateAlias}
          onDeleteAlias={handleDeleteAlias}
          onDeleteSpecialMemory={handleDeleteSpecialMemory}
          onSpecial={handleSpecial}
          onConfirmAll={confirmAllPending}
          onAiSpecial={runAiSpecial}
          aiSpecialEnabled={Boolean(aiStatus?.configured && aiStatus?.special_enabled)}
          aiSpecialHint={
            !aiStatus
              ? "AI 状态加载中"
              : !aiStatus.configured
                ? aiStatus.message
                : !aiStatus.special_enabled
                  ? "AI 特典归属判断未启用：请到设置页打开"
                  : ""
          }
        />
        </div>
      )}

      {overview && overview.collections.length > 0 && (
        <Card
          id="sec-collections"
          data-section
          title="合集文件（一个视频含多集）"
          extra={<Text type="secondary">共 {overview.collections.length} 个</Text>}
        >
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            message="合集不做拆集"
            description="拆集需要重新编码，本工具不处理。合集默认不会自动执行，请在这里选择「原样搬运（保留原名）」或「指定季存放」。"
          />
          <Space direction="vertical" size={10} style={{ width: "100%" }}>
            {overview.collections.map((item) => (
              <Space key={item.file_id} wrap>
                <Text strong>{item.filename}</Text>
                <Tag color="purple">合集 {item.collection_range || "多集"}</Tag>
                {item.title && <Text type="secondary">归属：{item.title}</Text>}
                {!item.work_id && <Tag color="red">未归属</Tag>}
                <Select
                  size="small"
                  style={{ width: 160 }}
                  value={item.decision?.action ?? "none"}
                  onChange={(value) => {
                    const action = value as "none" | "keep" | "season";
                    void applyCollection(
                      item,
                      action === "none" ? "clear" : action,
                      item.decision?.season ?? item.season,
                    );
                  }}
                  options={[
                    { value: "none", label: "未选择（不执行）" },
                    { value: "keep", label: "原样搬运（保留原名）" },
                    { value: "season", label: "指定季存放" },
                  ]}
                />
                {item.decision?.action === "season" && (
                  <>
                    <InputNumber
                      size="small"
                      min={0}
                      max={99}
                      value={item.decision?.season ?? item.season}
                      onChange={(value) => {
                        if (typeof value === "number") {
                          void applyCollection(item, "season", value);
                        }
                      }}
                    />
                    <Text type="secondary">季</Text>
                  </>
                )}
              </Space>
            ))}
          </Space>
        </Card>
      )}

      {autoSummary && (
        <Alert
          type={autoSummary.choose + autoSummary.missing > 0 ? "warning" : "success"}
          showIcon
          closable
          onClose={() => setAutoSummary(null)}
          message={`自动匹配完成：自动 ${autoSummary.auto} 个${
            autoSummary.ai_pick ? ` · AI 挑选 ${autoSummary.ai_pick} 个` : ""
          } · 待选择 ${autoSummary.choose} 个 · 需手动 ${autoSummary.missing} 个`}
          description="自动匹配只套用“标题与年份唯一确定”的结果；AI 辅助开启时，本地搜不到会换关键词重试，多个候选分不清会让 AI 只从候选里挑编号。作品名始终来自 TMDB，可以在「操作」列点「手动改名」覆盖。"
        />
      )}

      <Card id="sec-works" data-section title="本页作品清单（按作品折叠）">
        <Text type="secondary" style={{ display: "block", marginBottom: 12 }}>
          这里只显示已经从扫描页带出来的作品。没带出的作品留在扫描页，可以之后再带。
          勾选作品后点「下一步」就会移到「模板与预览」，同一部作品不会重复出现。
        </Text>
        <ListFilterBar
          value={filters}
          onChange={setFilters}
          groupOptions={groupOptions}
          selectedCount={filteredSelected}
          totalCount={filteredGroups.length}
        />
        <Table
          rowKey="group_id"
          size="small"
          loading={loading}
          columns={columns}
          dataSource={filteredGroups}
          rowSelection={{
            selectedRowKeys: checkedWorks,
            onChange: (keys) => {
              const next = keys as string[];
              setCheckedWorks(next);
              persistSelection(next);
            },
          }}
          pagination={{ pageSize: 10, showSizeChanger: true, showTotal: (total) => `共 ${total} 个作品` }}
          scroll={{ x: 1120 }}
          locale={{ emptyText: <Empty description="本页还没有作品，请先回扫描页勾选并带出" /> }}
        />
      </Card>

      <Modal
        title="AI 辅助匹配（建议）"
        open={aiMatchOpen}
        width={760}
        onCancel={() => setAiMatchOpen(false)}
        footer={null}
      >
        <Space direction="vertical" size={10} style={{ width: "100%" }}>
          <Alert
            type="warning"
            showIcon
            message="AI 只建议搜索关键词，不会直接套用。点「用这个关键词搜索」后，仍需你在候选里选择正确作品。"
          />
          {aiMatchNote && <Text type="secondary">{aiMatchNote}</Text>}
          {aiMatchSuggestions.length === 0 ? (
            <Empty description="AI 没有给出建议" />
          ) : (
            <div style={{ maxHeight: 440, overflowY: "auto" }}>
              {aiMatchSuggestions.map((item) => (
                <div
                  key={item.group_id}
                  style={{
                    display: "flex",
                    gap: 12,
                    alignItems: "flex-start",
                    padding: "10px 0",
                    borderBottom: "1px solid var(--ani-border)",
                  }}
                >
                  <div style={{ flex: 1 }}>
                    <Space wrap size={4}>
                      <Tag color="purple">来自 AI</Tag>
                      <Text strong>{item.current_title}</Text>
                      {item.year != null && <Text type="secondary">({item.year})</Text>}
                    </Space>
                    <div style={{ marginTop: 4 }}>
                      <Text>建议关键词：</Text>
                      <Text code>{item.query}</Text>
                      {item.title && item.title !== item.query && (
                        <Text type="secondary"> · 建议名称：{item.title}</Text>
                      )}
                      <Tag style={{ marginLeft: 8 }}>
                        {item.media_type === "movie" ? "电影" : "剧集"}
                      </Tag>
                    </div>
                    {item.reason && (
                      <div>
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          判断依据：{item.reason}
                        </Text>
                      </div>
                    )}
                  </div>
                  <Button size="small" type="primary" onClick={() => searchWithAi(item)}>
                    用这个关键词搜索
                  </Button>
                </div>
              ))}
            </div>
          )}
        </Space>
      </Modal>

      <Modal
        title={planCluster ? `分配季号「${planCluster.name}」` : "分配季号"}
        open={Boolean(planCluster)}
        width={780}
        onCancel={() => setPlanCluster(null)}
        footer={[
          <Tooltip
            key="ai"
            title={
              !aiStatus
                ? "AI 状态加载中"
                : !aiStatus.configured
                  ? aiStatus.message
                  : "只发送作品名、季名和集号给 AI，建议不会自动生效"
            }
          >
            <Button
              icon={<RobotOutlined />}
              loading={planAiLoading}
              disabled={!aiStatus?.configured}
              onClick={() => void runPlanAi()}
            >
              用 AI 判断
            </Button>
          </Tooltip>,
          <Button key="cancel" onClick={() => setPlanCluster(null)}>
            取消
          </Button>,
          <Button
            key="ok"
            type="primary"
            loading={planApplying}
            onClick={() => void applySeasonPlan()}
          >
            确认分配
          </Button>,
        ]}
      >
        {planCluster && (
          <Space direction="vertical" size={12} style={{ width: "100%" }}>
            <Alert
              type="info"
              showIcon
              message="先看目录名/文件名里的季号线索，有就直接采用"
              description="没有明确线索的组不会按目录顺序硬排。可以点下方「用 AI 判断」拿建议，或直接手动选择；只有点「确认分配」后才会保存。"
            />
            <Text type="secondary" style={{ fontSize: 12 }}>
              TMDB 季列表：
              {planCluster.seasons.map((item) => `第 ${item.season_number} 季（${item.episode_count} 集）`).join("、")}
            </Text>
            {planCluster.manual.length > 0 && (
              <Alert
                type="warning"
                showIcon
                message={`有 ${planCluster.manual.length} 组已经手动设置过季号，不会被这次分配覆盖`}
                description={planCluster.manual
                  .map((item) => `${item.title}：${item.message}`)
                  .join("；")}
              />
            )}
            {planCluster.groups.map((group) => (
              <Card key={group.group_id} size="small" styles={{ body: { padding: 12 } }}>
                <Space direction="vertical" size={6} style={{ width: "100%" }}>
                  <Space wrap size={6}>
                    <Text strong>{group.title}</Text>
                    {group.year != null && <Text type="secondary">({group.year})</Text>}
                    <Tag>当前第 {group.manual ? group.season_choice ?? group.current_season : group.current_season} 季</Tag>
                    {group.manual && <Tag color="blue">已手动设置，不覆盖</Tag>}
                    {group.action === "assign" && group.proposed_season != null && (
                      <Tag color="green">建议第 {group.proposed_season} 季</Tag>
                    )}
                    {group.action === "conflict" && <Tag color="red">季号撞车</Tag>}
                    {group.action === "invalid_clue" && <Tag color="orange">线索对不上</Tag>}
                  </Space>
                  {group.reason && (
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {group.reason}
                    </Text>
                  )}
                  {group.message && group.action !== "keep_manual" && (
                    <Text type={group.action === "unresolved" ? "secondary" : "warning"} style={{ fontSize: 12 }}>
                      {group.message}
                    </Text>
                  )}
                  {!group.manual && (
                    <Space wrap size={6}>
                      <Text style={{ fontSize: 12 }}>这一组用：</Text>
                      <Select
                        size="small"
                        style={{ width: 260 }}
                        allowClear
                        value={planChoices[group.group_id]}
                        placeholder="保持不变"
                        onChange={(value) => {
                          setPlanChoices((prev) => ({ ...prev, [group.group_id]: value ?? undefined }));
                          setPlanNotes((prev) => {
                            const next = { ...prev };
                            delete next[group.group_id];
                            return next;
                          });
                        }}
                        options={planCluster.seasons.map((item) => ({
                          value: item.season_number,
                          label: `第 ${item.season_number} 季${
                            item.name ? ` · ${item.name}` : ""
                          } · ${item.episode_count} 集`,
                        }))}
                      />
                      {planNotes[group.group_id] && (
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          {planNotes[group.group_id]}
                        </Text>
                      )}
                    </Space>
                  )}
                </Space>
              </Card>
            ))}
            {planCollisions.length > 0 ? (
              <Alert
                type="warning"
                showIcon
                message={`按当前选择，还有 ${planCollisions.length} 处会撞在一起`}
                description={
                  <div>
                    {planCollisions.slice(0, 8).map((item) => (
                      <div key={`${item.season}-${item.episode}`}>
                        第 {item.season} 季第 {item.episode} 集：「{item.groups.join("」和「")}」
                      </div>
                    ))}
                    {planCollisions.length > 8 && <div>……还有 {planCollisions.length - 8} 处</div>}
                  </div>
                }
              />
            ) : (
              <Alert
                type="success"
                showIcon
                message="按当前选择，各组季号不会撞在一起"
              />
            )}
          </Space>
        )}
      </Modal>

      <Modal
        title={renameGroup ? `手动改名「${renameGroup.title}」` : "手动改名"}
        open={Boolean(renameGroup)}
        onCancel={() => setRenameGroup(null)}
        onOk={submitRename}
        okText="保存"
        cancelText="取消"
        confirmLoading={renaming}
      >
        <Space direction="vertical" size={8} style={{ width: "100%" }}>
          <Input
            value={renameValue}
            onChange={(event) => setRenameValue(event.target.value)}
            onPressEnter={submitRename}
            placeholder="填写最终要用的作品名"
          />
          <Text type="secondary" style={{ fontSize: 12 }}>
            只会改作品名，TMDB 编号和季号保持不变；改完后名称来源会标成「手动改名」。
          </Text>
        </Space>
      </Modal>

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
                          background: "var(--ani-subtle)",
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                          color: "var(--ani-text-tertiary)",
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
          {candidates.length === 0 && fallback.length > 0 && (
            <div>
              <Alert
                type="info"
                showIcon
                message="TMDB 没有结果，下面是 Bangumi 的备用候选"
                description="点「用该名称搜 TMDB」会用 Bangumi 的中文名 / 日文原名再查一次 TMDB，确认后再套用 TMDB 的单集数据。"
                style={{ marginBottom: 8 }}
              />
              <List
                dataSource={fallback}
                renderItem={(candidate) => (
                  <List.Item
                    actions={[
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
                      </Button>,
                    ]}
                  >
                    <List.Item.Meta
                      title={
                        <Space wrap size={6}>
                          <span>{candidate.name || candidate.original_name}</span>
                          {candidate.year && <Tag color="blue">{candidate.year}</Tag>}
                          <Tag color="purple">Bangumi</Tag>
                        </Space>
                      }
                      description={
                        candidate.original_name && candidate.original_name !== candidate.name
                          ? candidate.original_name
                          : "暂无原名"
                      }
                    />
                  </List.Item>
                )}
              />
            </div>
          )}
        </Space>
      </Modal>

      <Modal
        title={
          patternTarget
            ? `命名规律分析结果 · ${patternTarget.group_name} / ${patternTarget.dir_rel || "（根目录）"}`
            : "命名规律分析结果"
        }
        open={patternOpen && Boolean(patternResult) && Boolean(patternTarget)}
        width={1080}
        onCancel={() => setPatternOpen(false)}
        footer={
          patternResult ? (
            <Space wrap>
              {patternResult.status === "proposal" && patternResult.structure && (
                <Button type="primary" loading={patternApplying} onClick={confirmApplyPattern}>
                  应用这份规律
                </Button>
              )}
              {patternResult.status !== "abandoned" && (
                <Button
                  loading={patternDecisionSaving === "adopted"}
                  disabled={Boolean(patternDecisionSaving)}
                  onClick={() => void decidePattern("adopted")}
                >
                  采用
                </Button>
              )}
              <Button
                danger
                loading={patternDecisionSaving === "rejected"}
                disabled={Boolean(patternDecisionSaving)}
                onClick={() => void decidePattern("rejected")}
              >
                不采用
              </Button>
              <Button
                loading={patternDecisionSaving === "later"}
                disabled={Boolean(patternDecisionSaving)}
                onClick={() => void decidePattern("later")}
              >
                稍后再说
              </Button>
              <Button onClick={() => setPatternOpen(false)}>关闭</Button>
            </Space>
          ) : null
        }
      >
        {patternResult && (
          <Space direction="vertical" size={14} style={{ width: "100%" }}>
            <Alert
              type={
                patternResult.status === "proposal"
                  ? "success"
                  : patternResult.status === "candidate_only"
                    ? "warning"
                    : "error"
              }
              showIcon
              message={
                patternResult.status === "proposal"
                  ? "结论：可以应用"
                  : patternResult.status === "candidate_only"
                    ? "结论：只能当候选"
                    : "结论：已放弃"
              }
              description={
                <div>
                  <div>{patternResult.message}</div>
                  {patternResult.status === "candidate_only" && (
                    <div style={{ marginTop: 6 }}>
                      和内置识别有冲突时按内置优先处理，不会自动写入；请对比下面的两边依据后再决定。
                    </div>
                  )}
                  {patternResult.status === "abandoned" && (
                    <div style={{ marginTop: 6 }}>
                      缓存没有写入，识别字段也没有改动，这个目录继续按内置规则处理。
                    </div>
                  )}
                </div>
              }
            />

            <div>
              <Space wrap size={8}>
                <Text strong>自检结果</Text>
                {patternResult.attempts ? (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    共问了 AI {patternResult.attempts} 次
                  </Text>
                ) : null}
                <Text type="secondary" style={{ fontSize: 12 }}>
                  目标目录共 {patternResult.target_count} 个视频
                </Text>
              </Space>
              {patternResult.checks.length > 0 ? (
                <Table
                  style={{ marginTop: 8 }}
                  rowKey="key"
                  size="small"
                  pagination={false}
                  columns={patternCheckColumns}
                  dataSource={patternResult.checks}
                />
              ) : (
                <Text type="secondary" style={{ display: "block", marginTop: 6 }}>
                  这次没有产生自检结果。
                </Text>
              )}
            </div>

            {patternResult.conflicts && Object.keys(patternResult.conflicts).length > 0 && (
              <div>
                <Text strong>与内置识别的冲突（内置优先，不会自动写入）</Text>
                <div style={{ marginTop: 8 }}>
                  {Object.entries(patternResult.conflicts).map(([filename, fields]) => (
                    <div key={filename} style={{ fontSize: 12, marginBottom: 6 }}>
                      <Text style={{ fontSize: 12 }}>{filename}</Text>
                      {Object.entries(fields).map(([key, value]) => (
                        <Tag key={key} color="orange" style={{ marginLeft: 6 }}>
                          {PATTERN_FIELD_LABELS[key] ?? key}：内置 {String(value.builtin ?? "空")} /
                          AI {String(value.ai ?? "空")}
                        </Tag>
                      ))}
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div>
              <Space wrap size={8}>
                <Text strong>样例预览</Text>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  前 3 条 + 后 3 条真实结果；只预览，不会改文件名
                </Text>
              </Space>
              {patternResult.sample.length > 0 ? (
                <Table
                  style={{ marginTop: 8 }}
                  rowKey={(record) => record.file_id}
                  size="small"
                  pagination={false}
                  columns={patternSampleColumns}
                  dataSource={patternResult.sample}
                  scroll={{ x: 1120 }}
                />
              ) : (
                <Text type="secondary" style={{ display: "block", marginTop: 6 }}>
                  这次没有可展示的样例。
                </Text>
              )}
            </div>

            {patternResult.mismatched.length > 0 && (
              <Alert
                type="warning"
                showIcon
                message={`对不上的文件（${patternResult.mismatched.length} 个）`}
                description={patternResult.mismatched.slice(0, 8).join("、")}
              />
            )}
          </Space>
        )}
      </Modal>
    </Space>
    </PageSections>
  );
}
