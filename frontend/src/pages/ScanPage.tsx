import { useEffect, useMemo, useState } from "react";
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
  LinkOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  RobotOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { api } from "../api";
import BatchMatchModal from "../components/BatchMatchModal";
import type {
  AiIdentifySuggestion,
  AiMatchSuggestion,
  AiStatus,
  Category,
  DirectoryGroup,
  MatchCandidate,
  MatchGroup,
  MergeInfo,
  NamingPreset,
  ParsedFile,
  QueueEntry,
  QueueView,
  RescanCheck,
  ScanPayload,
  ScanRecord,
  WorkflowPreset,
} from "../types";
import { CATEGORY_LABELS } from "../types";
import ListFilterBar, { EMPTY_FILTER, entryMatches, type ListFilterValue } from "../listFilters";
import PresetBar from "../components/PresetBar";
import PageSections from "../components/SectionNav";

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
  queue: QueueView | null;
  onScan: (payload: ScanPayload | null) => void;
  onNext: () => void;
  reloadQueue: () => Promise<void>;
  workflowPresets: WorkflowPreset[];
  workflowPresetId: string | null;
  onWorkflowPresetChange: (presetId: string) => void;
  namingPresets: NamingPreset[];
  namingPresetId: string | null;
  onNamingPresetChange: (presetId: string) => void;
  reloadWorkflowPresets: (selectId?: string) => Promise<WorkflowPreset[]>;
  settingsVersion: number;
  onSettingsChanged: () => void;
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

export default function ScanPage({
  scan,
  queue,
  onScan,
  onNext,
  reloadQueue,
  workflowPresets,
  workflowPresetId,
  onWorkflowPresetChange,
  namingPresets,
  namingPresetId,
  onNamingPresetChange,
  reloadWorkflowPresets,
  settingsVersion,
  onSettingsChanged,
}: Props) {
  const [groups, setGroups] = useState<DirectoryGroup[]>([]);
  const [checked, setChecked] = useState<string[]>([]);
  const [moving, setMoving] = useState(false);
  const [mediaRoot, setMediaRoot] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [groupErrors, setGroupErrors] = useState<string[]>([]);
  const [loadError, setLoadError] = useState("");
  const [scanning, setScanning] = useState(false);
  const [filters, setFilters] = useState<ListFilterValue>(EMPTY_FILTER);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [history, setHistory] = useState<ScanRecord[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
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
  const [checking, setChecking] = useState(false);
  const [fullRescanning, setFullRescanning] = useState(false);
  const [lastCheck, setLastCheck] = useState<RescanCheck | null>(null);
  const [aiStatus, setAiStatus] = useState<AiStatus | null>(null);
  const [aiOpen, setAiOpen] = useState(false);
  const [aiLoading, setAiLoading] = useState(false);
  const [aiApplying, setAiApplying] = useState(false);
  const [aiSuggestions, setAiSuggestions] = useState<AiIdentifySuggestion[]>([]);
  const [aiChecked, setAiChecked] = useState<string[]>([]);
  const [aiRemember, setAiRemember] = useState(true);
  const [aiNote, setAiNote] = useState("");
  // 扫描结果分组：默认按匹配到的作品，也可以按文件所在目录
  const [groupBy, setGroupBy] = useState<"work" | "directory">("work");
  // 手动匹配：直接复用匹配页的搜索 / AI 建议 / 套用接口，不在扫描页另写规则
  const [matchEntry, setMatchEntry] = useState<QueueEntry | null>(null);
  const [matchQuery, setMatchQuery] = useState("");
  const [matchSource, setMatchSource] = useState<"tmdb" | "bangumi">("tmdb");
  const [matchCandidates, setMatchCandidates] = useState<MatchCandidate[]>([]);
  const [matchFallback, setMatchFallback] = useState<MatchCandidate[]>([]);
  const [matchSearching, setMatchSearching] = useState(false);
  const [matchApplying, setMatchApplying] = useState("");
  const [matchAiLoading, setMatchAiLoading] = useState(false);
  const [matchAiHint, setMatchAiHint] = useState<{
    query: string;
    title?: string;
    year?: number | null;
    confidence?: string;
    reason?: string;
    from: string;
  } | null>(null);
  const [matchAiNote, setMatchAiNote] = useState("");
  const [batchOpen, setBatchOpen] = useState(false);
  const [lastMerge, setLastMerge] = useState<MergeInfo | null>(null);

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
    api.aiStatus().then(setAiStatus).catch(() => setAiStatus(null));
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

  useEffect(() => {
    setLastCheck(scan?.last_check ?? null);
  }, [scan?.scan_id, scan?.last_check?.checked_at]);

  useEffect(() => {
    setChecked(scan?.selections?.scan ?? []);
  }, [scan?.scan_id]);

  const runRecheck = async () => {
    if (!scan) return;
    setChecking(true);
    try {
      const result = await api.recheck(scan.scan_id);
      setLastCheck(result.check);
      const fresh = await api.getScan(scan.scan_id);
      onScan(fresh);
      await reloadQueue();
      message.success(
        `核对完成：还在 ${result.check.ok} 个，已移动 ${result.check.moved} 个，缺失 ${result.check.missing} 个`,
      );
    } catch (error) {
      message.error(`核对失败：${(error as Error).message}`);
    } finally {
      setChecking(false);
    }
  };

  const runFullRescan = async () => {
    if (!scan) return;
    setFullRescanning(true);
    try {
      const groupIds = (scan.groups ?? []).map((item) => item.id);
      const result = await api.rescan(scan.scan_id, groupIds);
      setLastCheck(null);
      setChecked([]);
      onScan(result);
      await reloadQueue();
      message.success(`完整重扫完成：${result.stats.total} 个文件，清单已回到扫描页`);
    } catch (error) {
      message.error(`完整重扫失败：${(error as Error).message}`);
    } finally {
      setFullRescanning(false);
    }
  };

  const openHistory = async () => {
    setHistoryOpen(true);
    setHistoryLoading(true);
    try {
      const result = await api.scans();
      setHistory(result.scans);
    } catch (error) {
      message.error(`读取历史扫描失败：${(error as Error).message}`);
    } finally {
      setHistoryLoading(false);
    }
  };

  const restoreHistory = async (record: ScanRecord) => {
    try {
      const payload = await api.getScan(record.scan_id);
      setChecked([]);
      setFilters(EMPTY_FILTER);
      onScan(payload);
      await reloadQueue();
      setHistoryOpen(false);
      message.success(
        payload.restored
          ? "已恢复上次的扫描结果，可能已经过时，建议先点「核对当前清单」"
          : "已恢复扫描结果",
      );
    } catch (error) {
      message.error(`恢复失败：${(error as Error).message}`);
    }
  };

  const removeHistory = async (record: ScanRecord) => {
    try {
      await api.deleteScan(record.scan_id);
      setHistory((prev) => prev.filter((item) => item.scan_id !== record.scan_id));
      message.success("已删除这条扫描记录");
    } catch (error) {
      message.error(`删除失败：${(error as Error).message}`);
    }
  };

  const runAiIdentify = async () => {
    if (!scan) return;
    setAiLoading(true);
    try {
      const result = await api.aiIdentifySuggest({
        scan_id: scan.scan_id,
        limit: aiStatus?.batch_limit,
      });
      setAiSuggestions(result.suggestions);
      setAiChecked(result.suggestions.map((item) => item.file_id));
      setAiNote(result.message);
      setAiOpen(true);
      if (!result.suggestions.length) {
        message.info(result.message || "AI 没有给出建议");
      }
    } catch (error) {
      message.error(`AI 辅助识别失败：${(error as Error).message}`);
    } finally {
      setAiLoading(false);
    }
  };

  const applyAiIdentify = async () => {
    if (!scan) return;
    const picked = aiSuggestions.filter((item) => aiChecked.includes(item.file_id));
    if (!picked.length) {
      message.warning("请先勾选要确认的建议");
      return;
    }
    setAiApplying(true);
    try {
      const result = await api.aiIdentifyApply({
        scan_id: scan.scan_id,
        remember_alias: aiRemember,
        items: picked.map((item) => ({
          file_ids: [item.file_id],
          title: item.title,
          year: item.year,
          season: item.season,
          episode: item.episode,
          kind: item.kind,
          segment: item.segment,
          remember_alias: aiRemember,
        })),
      });
      message.success(
        `已确认 ${result.applied} 个文件${result.remembered ? `，记住了 ${result.remembered} 个写法` : ""}`,
      );
      setAiOpen(false);
      const fresh = await api.getScan(scan.scan_id);
      onScan(fresh);
      await reloadQueue();
    } catch (error) {
      message.error(`确认 AI 建议失败：${(error as Error).message}`);
    } finally {
      setAiApplying(false);
    }
  };

  const openManualMatch = (entry: QueueEntry) => {
    setMatchEntry(entry);
    const fallback = entry.filename.replace(/\.[^.]+$/, "");
    const keyword = entry.work_id && entry.title ? entry.title : fallback;
    setMatchQuery(keyword);
    setMatchSource("tmdb");
    setMatchCandidates([]);
    setMatchFallback([]);
    setMatchAiHint(null);
    setMatchAiNote("");
    if (keyword.trim()) void runManualSearch(keyword, "tmdb", entry);
  };

  const runManualSearch = async (
    text?: string,
    target: "tmdb" | "bangumi" = matchSource,
    entryOverride?: QueueEntry,
  ) => {
    if (!scan) return;
    const entry = entryOverride ?? matchEntry;
    if (!entry) return;
    const keyword = (text ?? matchQuery).trim();
    if (!keyword) {
      message.warning("请填写要搜索的作品名");
      return;
    }
    setMatchSearching(true);
    try {
      const result = await api.matchSearch({
        scan_id: scan.scan_id,
        group_id: entry.work_id ?? undefined,
        query: keyword,
        source: target,
      });
      setMatchCandidates(result.candidates);
      setMatchFallback(result.fallback ?? []);
      if (!result.candidates.length) {
        if (result.fallback?.length) {
          message.info("TMDB 没有结果，下面是 Bangumi 的备用候选，可以点「用该名称搜 TMDB」再试一次");
        } else {
          message.info("没有找到候选，换个关键词或数据源再试");
        }
      }
    } catch (error) {
      message.error(`搜索失败：${(error as Error).message}`);
    } finally {
      setMatchSearching(false);
    }
  };

  const runManualAi = async () => {
    if (!scan || !matchEntry) return;
    if (!aiStatus?.configured) {
      message.warning(aiStatus?.message || "AI 还没配置好");
      return;
    }
    setMatchAiLoading(true);
    setMatchAiHint(null);
    setMatchAiNote("");
    try {
      if (matchEntry.work_id) {
        // 已归属的作品：复用匹配页的 AI 匹配建议，只取当前这一组
        const result = await api.aiMatchSuggest({
          scan_id: scan.scan_id,
          include_matched: true,
          limit: aiStatus.batch_limit,
        });
        const suggestion: AiMatchSuggestion | undefined = result.suggestions.find(
          (item) => item.group_id === matchEntry.work_id,
        );
        if (!suggestion) {
          setMatchAiNote(result.message || "AI 这次没有给出这部作品的建议，请手动输入关键词搜索。");
          return;
        }
        setMatchAiHint({
          query: suggestion.query,
          title: suggestion.title,
          year: suggestion.year,
          confidence: suggestion.confidence,
          reason: suggestion.reason,
          from: suggestion.media_type === "movie" ? "AI 建议（电影）" : "AI 建议（剧集）",
        });
        setMatchQuery(suggestion.query);
        await runManualSearch(suggestion.query, "tmdb", matchEntry);
      } else {
        // 未归属的条目：复用扫描页已有的 AI 识别建议，只取当前这个文件
        const result = await api.aiIdentifySuggest({
          scan_id: scan.scan_id,
          group_id: matchEntry.group_id,
          limit: aiStatus.batch_limit,
        });
        const wanted = new Set(matchEntry.file_ids);
        const suggestion = result.suggestions.find((item) => wanted.has(item.file_id));
        if (!suggestion) {
          setMatchAiNote(result.message || "AI 这次没有给出这个条目的建议，请手动输入关键词搜索。");
          return;
        }
        setMatchAiHint({
          query: suggestion.title,
          title: suggestion.title,
          year: suggestion.year,
          confidence: suggestion.confidence,
          reason: suggestion.reason,
          from: "AI 识别建议",
        });
        setMatchQuery(suggestion.title);
        await runManualSearch(suggestion.title, "tmdb", matchEntry);
      }
    } catch (error) {
      message.error(`AI 辅助失败：${(error as Error).message}`);
    } finally {
      setMatchAiLoading(false);
    }
  };

  const applyManualMatch = async (candidate: MatchCandidate) => {
    if (!scan || !matchEntry) return;
    setMatchApplying(String(candidate.id));
    try {
      let workId = matchEntry.work_id ?? "";
      if (!workId) {
        // 未归属条目：先按现有指派接口建立归属，再走同一套匹配写回
        await api.identifyAssign({
          scan_id: scan.scan_id,
          file_ids: matchEntry.file_ids,
          title: candidate.name,
          year: candidate.year,
        });
        const freshQueue = await api.queue(scan.scan_id);
        const freshEntry = freshQueue.entries.find(
          (item) => item.entry_id === matchEntry.entry_id,
        );
        workId = freshEntry?.work_id ?? "";
        if (!workId) throw new Error("指派后没有找到作品，请重试");
      }
      const result = await api.matchApply({
        scan_id: scan.scan_id,
        group_id: workId,
        tmdb_id: candidate.id,
        media_type: candidate.media_type,
      });
      const fresh = await api.getScan(scan.scan_id);
      onScan(fresh);
      await reloadQueue();
      setMatchEntry(null);
      if (result.merged?.length) setLastMerge(result.merged[result.merged.length - 1]);
      message.success(`已改匹配为「${result.meta.name}」，更新 ${result.updated} 个文件`);
      if (result.missing.length) {
        message.warning(
          `有 ${result.missing.length} 集未在 TMDB 中找到（${result.missing.slice(0, 6).join("、")}）`,
        );
      }
      const matchedGroup = result.groups.find((item) => item.group_id === workId);
      if (matchedGroup?.season_pending) {
        message.info(
          `TMDB 有 ${matchedGroup.season_options?.length ?? 0} 个正式季，请到「匹配作品」页确认季号后再预览`,
        );
      }
    } catch (error) {
      message.error(`匹配失败：${(error as Error).message}`);
    } finally {
      setMatchApplying("");
    }
  };

  const handleBatchChanged = async (groups: MatchGroup[], merged: MergeInfo[]) => {
    void groups;
    if (merged.length) setLastMerge(merged[merged.length - 1]);
    if (!scan) return;
    const fresh = await api.getScan(scan.scan_id);
    onScan(fresh);
    await reloadQueue();
  };

  const undoMerge = async (mergeId: string) => {
    if (!scan) return;
    try {
      await api.matchMergeUndo({ scan_id: scan.scan_id, merge_id: mergeId });
      const fresh = await api.getScan(scan.scan_id);
      onScan(fresh);
      await reloadQueue();
      setLastMerge(null);
      message.success("已撤销合并，恢复成合并前的作品组");
    } catch (error) {
      message.error(`撤销合并失败：${(error as Error).message}`);
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

  const fileMap = useMemo(
    () => new Map((scan?.files ?? []).map((file) => [file.id, file])),
    [scan],
  );

  const unassignedCount = useMemo(
    () =>
      (scan?.files ?? []).filter(
        (file) =>
          file.category === "video" && !file.identify?.work_id && !file.identify?.ignored,
      ).length,
    [scan],
  );

  const scanEntries = useMemo(() => {
    const entries = (queue?.entries ?? []).filter((entry) => entry.stage === "scan");
    return entries.filter((entry) =>
      entryMatches(entry, filters, {
        file: fileMap.get(entry.file_ids[0] ?? ""),
        selected: checked.includes(entry.entry_id),
      }),
    );
  }, [queue, filters, checked, fileMap]);

  const filteredChecked = scanEntries.filter((entry) => checked.includes(entry.entry_id)).length;
  const groupOptions = useMemo(
    () =>
      groups
        .map((group) => ({ value: group.id, label: group.name }))
        .concat(
          scanEntries
            .map((entry) => entry.group_id || "")
            .filter((id, index, list) => id && groups.every((group) => group.id !== id) && list.indexOf(id) === index)
            .map((id) => ({ value: id, label: id })),
        ),
    [groups, scanEntries],
  );

  // 扫描页只改变展示分组，搜索、筛选、勾选状态都保持原样
  const groupedEntries = useMemo(() => {
    const nameOfGroup = (groupId?: string) => {
      const fromScan = scan?.groups?.find((item) => item.id === groupId);
      const fromList = groups.find((item) => item.id === groupId);
      return fromScan?.name || fromList?.name || "";
    };
    const buckets = new Map<string, { key: string; label: string; entries: QueueEntry[] }>();
    for (const entry of scanEntries) {
      let key: string;
      let label: string;
      if (groupBy === "work") {
        if (entry.work_id) {
          key = `work:${entry.work_id}`;
          label = `${entry.title || "未命名作品"}${entry.year ? ` (${entry.year})` : ""}`;
        } else {
          key = "work:__unassigned__";
          label = "未归属（待指派）";
        }
      } else {
        const rel = entry.rel_path.replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
        const parts = rel.split("/").filter(Boolean);
        const dir = parts.length > 1 ? parts.slice(0, -1).join("/") : "";
        key = `dir:${entry.group_id || ""}:${dir}`;
        const groupName = nameOfGroup(entry.group_id);
        label = `${groupName ? `${groupName} / ` : ""}${dir || "根目录"}`;
      }
      const bucket = buckets.get(key) ?? { key, label, entries: [] };
      bucket.entries.push(entry);
      buckets.set(key, bucket);
    }
    const list = [...buckets.values()];
    if (groupBy === "directory") {
      list.sort((left, right) => left.label.localeCompare(right.label, "zh-CN"));
    } else {
      // 未归属统一放到最后，其余保持扫描时的出现顺序
      list.sort(
        (left, right) =>
          Number(left.key === "work:__unassigned__") - Number(right.key === "work:__unassigned__"),
      );
    }
    return list;
  }, [scanEntries, groupBy, groups, scan]);

  // 批量匹配按「作品组」去重：同一个组里勾了多行也只算一组；未归属条目另行统计
  const selectedWorkGroups = useMemo(() => {
    const counts = new Map<string, number>();
    for (const entry of scanEntries) {
      if (entry.work_id) counts.set(entry.work_id, (counts.get(entry.work_id) ?? 0) + 1);
    }
    const picked = new Map<string, QueueEntry>();
    for (const entry of scanEntries) {
      if (!checked.includes(entry.entry_id) || !entry.work_id || picked.has(entry.work_id)) continue;
      picked.set(entry.work_id, entry);
    }
    return [...picked.values()].map((entry) => ({
      group_id: entry.work_id as string,
      title: entry.title,
      year: entry.year,
      entry_count: counts.get(entry.work_id as string) ?? 1,
    }));
  }, [checked, scanEntries]);

  const selectedUnassigned = useMemo(
    () => scanEntries.filter((entry) => checked.includes(entry.entry_id) && !entry.work_id).length,
    [checked, scanEntries],
  );

  // 合并记录保存在扫描结果里，刷新页面后仍然可以撤销最近一次合并
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

  const persistSelection = (ids: string[]) => {
    if (scan) void api.saveSelection(scan.scan_id, "scan", ids).catch(() => undefined);
  };

  const toggleEntry = (entryId: string, on: boolean) => {
    const next = on ? [...checked, entryId] : checked.filter((id) => id !== entryId);
    setChecked(next);
    persistSelection(next);
  };

  const toggleMany = (entryIds: string[], on: boolean) => {
    const rest = checked.filter((id) => !entryIds.includes(id));
    const next = on ? [...rest, ...entryIds] : rest;
    setChecked(next);
    persistSelection(next);
  };

  const moveNext = async () => {
    if (!scan || !checked.length) {
      message.warning("请先勾选要带出的条目");
      return;
    }
    setMoving(true);
    try {
      const count = checked.length;
      await api.queueMove(scan.scan_id, checked, "match");
      setChecked([]);
      persistSelection([]);
      await reloadQueue();
      message.success(`已带出 ${count} 个条目到「匹配作品」`);
      onNext();
    } catch (error) {
      message.error(`带出失败：${(error as Error).message}`);
    } finally {
      setMoving(false);
    }
  };

  const entryColumns: ColumnsType<QueueEntry> = [
    {
      title: "",
      width: 40,
      render: (_, record) => (
        <Checkbox
          checked={checked.includes(record.entry_id)}
          onChange={(event) => toggleEntry(record.entry_id, event.target.checked)}
        />
      ),
    },
    {
      title: "条目",
      render: (_, record) => (
        <div>
          <div className="preview-name">
            {record.is_folder ? "[素材文件夹] " : ""}
            {record.filename}
          </div>
          <div className="path-text">{record.rel_path}</div>
        </div>
      ),
    },
    {
      title: "类别",
      dataIndex: "category",
      width: 84,
      render: (value: Category) => (
        <Tag color={CATEGORY_COLORS[value]}>{CATEGORY_LABELS[value] ?? value}</Tag>
      ),
    },
    {
      title: "类型",
      dataIndex: "kind",
      width: 86,
      render: (value: string) => {
        if (!value || value === "asset") return <Text type="secondary">-</Text>;
        const meta = KIND_META[value] ?? KIND_META.unknown;
        return <Tag color={meta.color}>{meta.label}</Tag>;
      },
    },
    {
      title: "作品",
      render: (_, record) =>
        record.needs_assign ? (
          <Tag color="red">未归属，需在匹配页指派</Tag>
        ) : (
          record.title || <Text type="danger">未提取</Text>
        ),
    },
    {
      title: "季 / 集",
      width: 100,
      render: (_, record) =>
        record.kind === "special"
          ? "特典"
          : record.season != null && record.episode != null
            ? `S${String(record.season).padStart(2, "0")}E${String(record.episode).padStart(2, "0")}`
            : "-",
    },
    {
      title: "字幕",
      width: 84,
      render: (_, record) =>
        record.category === "video" && record.file_ids.length > 1
          ? `${record.file_ids.length - 1} 个（跟随）`
          : "-",
    },
    {
      title: "操作",
      width: 108,
      render: (_, record) =>
        record.category === "video" ? (
          <Button
            size="small"
            icon={<LinkOutlined />}
            onClick={() => openManualMatch(record)}
          >
            手动匹配
          </Button>
        ) : (
          <Text type="secondary">-</Text>
        ),
    },
  ];

  const groupNameOf = (groupId?: string) => {
    const fromScan = scan?.groups?.find((item) => item.id === groupId);
    const fromList = groups.find((item) => item.id === groupId);
    return fromScan?.name || fromList?.name || "";
  };

  return (
    <PageSections
      items={[
        { id: "sec-preset", label: "工作流预设" },
        { id: "sec-groups", label: "目录组与扫描" },
        ...(scan ? [{ id: "sec-result", label: "扫描结果" }] : []),
      ]}
      primaryAction={{
        label: "下一步",
        hint: `${checked.length}/${queue?.counts.scan ?? 0}`,
        disabled: checked.length === 0 || moving,
        loading: moving,
        onClick: () => void moveNext(),
      }}
    >
      <Space direction="vertical" size={16} style={{ width: "100%" }}>
        <div id="sec-preset" data-section>
          <PresetBar
            workflowPresets={workflowPresets}
            workflowPresetId={workflowPresetId}
            onWorkflowPresetChange={onWorkflowPresetChange}
            namingPresets={namingPresets}
            namingPresetId={namingPresetId}
            onNamingPresetChange={onNamingPresetChange}
            reloadWorkflowPresets={reloadWorkflowPresets}
            settingsVersion={settingsVersion}
            onSettingsChanged={onSettingsChanged}
          />
        </div>
        <Card id="sec-groups" data-section>
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
          <Button onClick={openHistory} style={{ marginTop: 12 }}>
            历史扫描
          </Button>
        </div>
      </Card>

      {scan && (
        <Card id="sec-result" data-section>
          {scan.restored && (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 12 }}
              message="这是上次保存的扫描结果，可能已经过时"
              description="容器重启后从数据库恢复。文件可能已经移动或新增，建议先点「核对当前清单」；需要发现新文件时用「完整重扫」。"
              action={
                <Button size="small" loading={checking} onClick={runRecheck}>
                  核对当前清单
                </Button>
              }
            />
          )}
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
          <Divider />
          <Space direction="vertical" size={8} style={{ width: "100%" }}>
            <Space wrap>
              <Button loading={checking} onClick={runRecheck}>
                核对当前清单（轻量）
              </Button>
              <Button loading={fullRescanning} onClick={runFullRescan}>
                完整重扫
              </Button>
              <Tooltip
                title={
                  !aiStatus
                    ? "AI 状态加载中"
                    : !aiStatus.configured
                      ? aiStatus.message
                      : unassignedCount === 0
                        ? "没有需要 AI 判断的未归属文件"
                        : "把未归属文件按目录打包一次发给 AI，只返回建议"
                }
              >
                <Button
                  loading={aiLoading}
                  disabled={!aiStatus?.configured || unassignedCount === 0}
                  onClick={runAiIdentify}
                >
                  AI 辅助识别{unassignedCount > 0 ? `（未归属 ${unassignedCount}）` : ""}
                </Button>
              </Tooltip>
              {lastCheck && (
                <Text type="secondary">
                  上次核对：还在 {lastCheck.ok} 个 · 已移动 {lastCheck.moved} 个 · 缺失{" "}
                  {lastCheck.missing} 个 · 清单完成 {lastCheck.entries_done} 项
                </Text>
              )}
            </Space>
            <Text type="secondary" style={{ fontSize: 12 }}>
              执行成功完成后默认不会自动重扫；失败、文件夹合并 / 覆盖、取消后会自动核对一次。
              「核对当前清单」只检查文件还在不在；文件缺失或需要发现新文件时用「完整重扫」，
              完整重扫会把清单重新回到扫描页。AI 辅助只给建议，确认后才生效，
              只发送文件名和目录名，不发送文件内容。
            </Text>
          </Space>
          {(scan.ignored_dirs?.length ?? 0) > 0 && (
            <Alert
              type="info"
              showIcon
              style={{ marginTop: 12, marginBottom: 12 }}
              message={`已跳过 ${scan.ignored_dirs?.length} 个目录（空标记文件改为移动完成后再写入）`}
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
            <Alert
              type="warning"
              showIcon
              message={`已达到上限（${scan.max_files ?? 20000} 个文件），还有文件未扫描，请缩小目录范围后重新扫描。`}
              style={{ marginBottom: 12 }}
            />
          )}
          <ListFilterBar
            value={filters}
            onChange={setFilters}
            groupOptions={groupOptions}
            selectedCount={filteredChecked}
            totalCount={scanEntries.length}
            leading={
              <Space size={4}>
                <Text type="secondary">分组</Text>
                <Select
                  style={{ width: 116 }}
                  value={groupBy}
                  onChange={(value) => setGroupBy(value as "work" | "directory")}
                  options={[
                    { value: "work", label: "按作品" },
                    { value: "directory", label: "按目录" },
                  ]}
                />
                <Tooltip
                  title={
                    selectedWorkGroups.length
                      ? "把勾选到的作品组一次匹配到同一部作品，或让 AI 给出候选后逐个确认"
                      : "先在下面勾选作品组里的条目"
                  }
                >
                  <Button
                    size="small"
                    icon={<LinkOutlined />}
                    disabled={selectedWorkGroups.length === 0}
                    onClick={() => setBatchOpen(true)}
                  >
                    批量匹配{selectedWorkGroups.length ? `（${selectedWorkGroups.length} 组）` : ""}
                  </Button>
                </Tooltip>
              </Space>
            }
            extra={
              <Text type="secondary">
                总已选 {checked.length} / 留在本页 {Math.max(0, (queue?.counts.scan ?? 0) - checked.length)}
              </Text>
            }
          />
          {visibleMerge && (
            <Alert
              type="success"
              showIcon
              style={{ marginBottom: 12 }}
              message={`已自动合并 ${visibleMerge.count} 个「${visibleMerge.title}」作品组`}
              description="组标题已统一；勾选状态、未归属指派和已有季号都保留。只允许撤销最近一次合并。"
              action={
                <Button size="small" onClick={() => void undoMerge(visibleMerge.id)}>
                  撤销合并
                </Button>
              }
            />
          )}
          {scanEntries.length === 0 ? (
            <Empty
              description={
                (queue?.entries ?? []).some((entry) => entry.stage === "scan")
                  ? "没有符合筛选条件的条目，可以清空搜索和筛选再看看"
                  : "本页没有待带出的条目了，可以回到「匹配作品」页继续"
              }
            />
          ) : (
            <Collapse
              key={`${groupBy}:${groupedEntries.map((item) => item.key).join("|")}`}
              defaultActiveKey={groupedEntries.map((item) => item.key)}
              items={groupedEntries.map(({ key, label, entries }) => {
                const ids = entries.map((entry) => entry.entry_id);
                const picked = ids.filter((id) => checked.includes(id)).length;
                return {
                  key,
                  label: (
                    <Space>
                      <Text strong>{label}</Text>
                      <Tag>{entries.length} 项</Tag>
                      {picked > 0 && <Tag color="blue">已选 {picked}</Tag>}
                    </Space>
                  ),
                  children: (
                    <>
                      <Checkbox
                        checked={picked === ids.length}
                        indeterminate={picked > 0 && picked < ids.length}
                        onChange={(event) => toggleMany(ids, event.target.checked)}
                        style={{ marginBottom: 8 }}
                      >
                        全选本组（{ids.length} 项）
                      </Checkbox>
                      <Table
                        rowKey="entry_id"
                        size="small"
                        columns={entryColumns}
                        dataSource={entries}
                        pagination={{ pageSize: 10, showSizeChanger: true, showTotal: (total) => `共 ${total} 条` }}
                        scroll={{ x: 900 }}
                        expandable={{
                          expandedRowRender: (record) => {
                            const file = fileMap.get(record.file_ids[0] ?? "");
                            if (!file) return <Text type="secondary">素材文件夹作为一个整体移动，内部文件不单独列。</Text>;
                            return (
                              <div className="mono" style={{ whiteSpace: "pre-wrap" }}>
                                {file.warnings.length > 0 && (
                                  <Text type="warning">提示：{file.warnings.join("；")}</Text>
                                )}
                                <div style={{ marginTop: 4 }}>
                                  路径：{file.path}
                                </div>
                                <div style={{ marginTop: 4 }}>解析字段：</div>
                                {Object.entries(file.fields)
                                  .filter(([, value]) => value !== "" && value !== 0)
                                  .map(([key, value]) => (
                                    <span key={key} style={{ marginRight: 16 }}>
                                      {key}={String(value)}
                                    </span>
                                  ))}
                              </div>
                            );
                          },
                        }}
                        locale={{ emptyText: <Empty description="没有匹配的条目" /> }}
                      />
                    </>
                  ),
                };
              })}
            />
          )}
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

      <Modal
        title="AI 辅助识别（建议）"
        open={aiOpen}
        width={760}
        onCancel={() => setAiOpen(false)}
        footer={
          <Space>
            <Checkbox checked={aiRemember} onChange={(event) => setAiRemember(event.target.checked)}>
              记住这些写法（别名），下次自动复用
            </Checkbox>
            <Button onClick={() => setAiOpen(false)}>取消</Button>
            <Button type="primary" loading={aiApplying} onClick={applyAiIdentify}>
              确认勾选的建议
            </Button>
          </Space>
        }
      >
        <Space direction="vertical" size={8} style={{ width: "100%" }}>
          <Alert
            type="warning"
            showIcon
            message="AI 只给建议，确认后才会写入；未确认的不会保存。只发送文件名和目录名，不发送文件内容。"
          />
          {aiNote && <Text type="secondary">{aiNote}</Text>}
          {aiSuggestions.length === 0 ? (
            <Empty description="AI 没有给出建议" />
          ) : (
            <div style={{ maxHeight: 440, overflowY: "auto" }}>
              {aiSuggestions.map((item) => (
                <div
                  key={item.file_id}
                  style={{
                    display: "flex",
                    gap: 8,
                    padding: "8px 0",
                    borderBottom: "1px solid var(--ani-border)",
                  }}
                >
                  <Checkbox
                    checked={aiChecked.includes(item.file_id)}
                    onChange={(event) =>
                      setAiChecked((prev) =>
                        event.target.checked
                          ? [...prev, item.file_id]
                          : prev.filter((id) => id !== item.file_id),
                      )
                    }
                  />
                  <div style={{ flex: 1 }}>
                    <div className="preview-name">{item.filename}</div>
                    <Space wrap size={4} style={{ marginTop: 4 }}>
                      <Tag color="purple">来自 AI</Tag>
                      <Text strong>{item.title}</Text>
                      {item.year != null && <Text type="secondary">({item.year})</Text>}
                      {item.kind === "special" && <Tag>特典</Tag>}
                      {item.kind === "movie" && <Tag>剧场版</Tag>}
                      {item.kind !== "movie" && item.season != null && item.episode != null && (
                        <Tag>
                          {`S${String(item.season).padStart(2, "0")}E${String(item.episode).padStart(2, "0")}`}
                        </Tag>
                      )}
                      {Object.entries(item.tags || {}).map(([key, value]) => (
                        <Tag key={key}>
                          {key}: {String(value)}
                        </Tag>
                      ))}
                    </Space>
                    {item.reason && (
                      <div>
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          判断依据：{item.reason}
                        </Text>
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </Space>
      </Modal>

      <Modal
        title="历史扫描（最近 10 次，超过 14 天自动清理）"
        open={historyOpen}
        onCancel={() => setHistoryOpen(false)}
        footer={null}
        width={620}
      >
        <Alert
          type="info"
          showIcon
          message="扫描结果保存在数据库里，容器重启后还能恢复；删除后不可找回。"
          style={{ marginBottom: 12 }}
        />
        <List
          loading={historyLoading}
          dataSource={history}
          locale={{ emptyText: "还没有保存的扫描记录" }}
          renderItem={(record) => (
            <List.Item
              actions={[
                <Button key="restore" size="small" onClick={() => restoreHistory(record)}>
                  恢复
                </Button>,
                <Popconfirm
                  key="delete"
                  title="删除这条扫描记录？"
                  onConfirm={() => removeHistory(record)}
                >
                  <Button size="small" danger>
                    删除
                  </Button>
                </Popconfirm>,
              ]}
            >
              <List.Item.Meta
                title={`${record.file_count} 个文件`}
                description={`扫描于 ${new Date(record.created_at).toLocaleString()}`}
              />
            </List.Item>
          )}
        />
      </Modal>

      {scan && (
        <BatchMatchModal
          open={batchOpen}
          scanId={scan.scan_id}
          targets={selectedWorkGroups}
          skippedUnassigned={selectedUnassigned}
          aiConfigured={Boolean(aiStatus?.configured)}
          aiMessage={aiStatus?.message}
          aiLimit={aiStatus?.batch_limit ?? 20}
          onClose={() => setBatchOpen(false)}
          onChanged={handleBatchChanged}
        />
      )}

      <Modal
        title={matchEntry ? `手动匹配：${matchEntry.filename}` : "手动匹配"}
        open={Boolean(matchEntry)}
        onCancel={() => setMatchEntry(null)}
        footer={null}
        width={760}
      >
        <Space direction="vertical" size={12} style={{ width: "100%" }}>
          <Alert
            type="info"
            showIcon
            message={
              matchEntry?.work_id
                ? "改的是整个作品组：同一作品下的所有文件会一起换成新作品的名称、季集信息和目标路径，下一次预览按新结果生成。"
                : "这个条目还没归属作品：选中候选后会先建立归属，再套用 TMDB 的季集信息。"
            }
            description="已执行过的历史记录不受影响；只影响当前扫描的后续预览与执行。"
          />
          <Space.Compact style={{ width: "100%" }}>
            <Input
              value={matchQuery}
              onChange={(event) => setMatchQuery(event.target.value)}
              onPressEnter={() => void runManualSearch()}
              placeholder="输入作品名，可改成中文 / 日文 / 英文"
            />
            <Select
              style={{ width: 112 }}
              value={matchSource}
              onChange={(value) => {
                const next = value as "tmdb" | "bangumi";
                setMatchSource(next);
                if (matchQuery.trim()) void runManualSearch(matchQuery, next);
              }}
              options={[
                { value: "tmdb", label: "TMDB" },
                { value: "bangumi", label: "Bangumi" },
              ]}
            />
            <Button type="primary" loading={matchSearching} onClick={() => void runManualSearch()}>
              搜索
            </Button>
          </Space.Compact>
          <Space wrap size={8}>
            <Tooltip
              title={
                !aiStatus
                  ? "AI 状态加载中"
                  : !aiStatus.configured
                    ? aiStatus.message
                    : matchEntry?.work_id
                      ? "按当前作品问 AI 要一个更准的搜索关键词，仍需你从候选中选择"
                      : "让 AI 先判断这个文件可能是什么作品，仍需你从候选中选择"
              }
            >
              <Button
                icon={<RobotOutlined />}
                loading={matchAiLoading}
                disabled={!aiStatus?.configured}
                onClick={() => void runManualAi()}
              >
                AI 辅助给候选
              </Button>
            </Tooltip>
            {matchAiNote && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                {matchAiNote}
              </Text>
            )}
          </Space>
          {matchAiHint && (
            <Alert
              type="warning"
              showIcon
              message={
                <Space wrap size={6}>
                  <Tag color="purple">{matchAiHint.from}</Tag>
                  <Text>{matchAiHint.title || matchAiHint.query}</Text>
                  {matchAiHint.year != null && (
                    <Text type="secondary">({matchAiHint.year})</Text>
                  )}
                  {matchAiHint.confidence && <Tag>{matchAiHint.confidence}</Tag>}
                </Space>
              }
              description={
                <div>
                  {matchAiHint.reason && (
                    <div style={{ marginBottom: 6 }}>判断依据：{matchAiHint.reason}</div>
                  )}
                  <Text type="secondary">
                    AI 只给关键词，不会直接套用；请从下方候选中选择正确作品。
                  </Text>
                </div>
              }
              action={
                <Button size="small" onClick={() => void runManualSearch(matchAiHint.query, "tmdb")}>
                  用这个关键词搜索
                </Button>
              }
            />
          )}
          <List
            loading={matchSearching}
            dataSource={matchCandidates}
            locale={{ emptyText: "输入关键词后搜索，或点「AI 辅助给候选」" }}
            renderItem={(candidate) => (
              <List.Item
                actions={[
                  candidate.source === "bangumi" ? (
                    <Button
                      key="retry"
                      size="small"
                      onClick={() => {
                        setMatchQuery(candidate.name);
                        setMatchSource("tmdb");
                        void runManualSearch(candidate.name, "tmdb");
                      }}
                    >
                      用该名称搜 TMDB
                    </Button>
                  ) : (
                    <Button
                      key="use"
                      type="primary"
                      size="small"
                      icon={<LinkOutlined />}
                      loading={matchApplying === String(candidate.id)}
                      onClick={() => void applyManualMatch(candidate)}
                    >
                      使用这条
                    </Button>
                  ),
                ]}
              >
                <List.Item.Meta
                  avatar={
                    candidate.poster ? (
                      <img
                        src={candidate.poster}
                        alt=""
                        style={{ width: 54, height: 80, objectFit: "cover", borderRadius: 4 }}
                      />
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
          {matchCandidates.length === 0 && matchFallback.length > 0 && (
            <div>
              <Alert
                type="info"
                showIcon
                message="TMDB 没有结果，下面是 Bangumi 的备用候选"
                description="点「用该名称搜 TMDB」会用 Bangumi 的中文名 / 日文原名再查一次 TMDB，确认后再套用 TMDB 的单集数据。"
                style={{ marginBottom: 8 }}
              />
              <List
                dataSource={matchFallback}
                renderItem={(candidate) => (
                  <List.Item
                    actions={[
                      <Button
                        key="retry"
                        size="small"
                        onClick={() => {
                          setMatchQuery(candidate.name);
                          setMatchSource("tmdb");
                          void runManualSearch(candidate.name, "tmdb");
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
      </Space>
    </PageSections>
  );
}
