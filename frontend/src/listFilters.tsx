import { Input, Select, Space, Typography } from "antd";
import { SearchOutlined } from "@ant-design/icons";
import type { ReactNode } from "react";
import type { QueueEntry, ParsedFile } from "./types";

const { Text } = Typography;

export type EntryStatus =
  | "unidentified"
  | "suspected"
  | "pending"
  | "matched"
  | "unmatched"
  | "conflict"
  | "failed"
  | "done";

export interface ListFilterValue {
  keyword: string;
  statuses: string[];
  types: string[];
  groupIds: string[];
  selection: "all" | "selected" | "unselected";
}

export const EMPTY_FILTER: ListFilterValue = {
  keyword: "",
  statuses: [],
  types: [],
  groupIds: [],
  selection: "all",
};

export const STATUS_OPTIONS: { value: EntryStatus; label: string }[] = [
  { value: "unidentified", label: "未识别" },
  { value: "suspected", label: "疑似作品" },
  { value: "pending", label: "待确认" },
  { value: "matched", label: "已匹配" },
  { value: "unmatched", label: "未匹配" },
  { value: "conflict", label: "冲突" },
  { value: "failed", label: "失败" },
  { value: "done", label: "已完成" },
];

export const TYPE_OPTIONS = [
  { value: "video", label: "视频" },
  { value: "subtitle", label: "字幕" },
  { value: "folder", label: "素材文件夹" },
  { value: "other", label: "其它" },
];

export function entryType(entry: QueueEntry): string {
  if (entry.is_folder || entry.category === "folder") return "folder";
  if (entry.category === "video") return "video";
  if (entry.category === "subtitle") return "subtitle";
  return "other";
}

export function entryStatus(
  entry: QueueEntry,
  file?: ParsedFile,
  rowStatus?: string,
): EntryStatus {
  const result = entry.result?.status;
  if (result === "done") return "done";
  if (result === "failed") return "failed";
  if (result === "conflict") return "conflict";
  if (result === "skipped") return "unmatched";
  if (rowStatus === "error") return "failed";
  if (rowStatus === "warning") return "pending";
  const identify = file?.identify;
  if (identify?.needs_confirm) return "pending";
  if (identify?.work_id) return "matched";
  if (identify?.ignored) return "unidentified";
  if ((identify?.candidates ?? []).length > 0) return "suspected";
  return "unidentified";
}

export function entryMatches(
  entry: QueueEntry,
  filter: ListFilterValue,
  options: {
    file?: ParsedFile;
    rowStatus?: string;
    selected?: boolean;
  },
): boolean {
  const text = filter.keyword.trim().toLowerCase();
  if (text) {
    const haystack = `${entry.filename} ${entry.title} ${entry.rel_path}`.toLowerCase();
    if (!haystack.includes(text)) return false;
  }
  if (filter.statuses.length) {
    const status = entryStatus(entry, options.file, options.rowStatus);
    if (!filter.statuses.includes(status)) return false;
  }
  if (filter.types.length && !filter.types.includes(entryType(entry))) return false;
  if (filter.groupIds.length && !filter.groupIds.includes(entry.group_id || "")) return false;
  if (filter.selection === "selected" && !options.selected) return false;
  if (filter.selection === "unselected" && options.selected) return false;
  return true;
}

interface Props {
  value: ListFilterValue;
  onChange: (value: ListFilterValue) => void;
  groupOptions: { value: string; label: string }[];
  selectedCount: number;
  totalCount: number;
  /** 放在搜索框前面的额外控件（例如扫描页的分组依据） */
  leading?: ReactNode;
  extra?: ReactNode;
}

export default function ListFilterBar({
  value,
  onChange,
  groupOptions,
  selectedCount,
  totalCount,
  leading,
  extra,
}: Props) {
  const patch = (partial: Partial<ListFilterValue>) => onChange({ ...value, ...partial });
  return (
    <Space wrap style={{ marginBottom: 12 }}>
      {leading}
      <Input
        prefix={<SearchOutlined />}
        placeholder="按作品名 / 文件名搜索"
        style={{ width: 260 }}
        value={value.keyword}
        onChange={(event) => patch({ keyword: event.target.value })}
        allowClear
      />
      <Select
        mode="multiple"
        allowClear
        placeholder="状态"
        style={{ minWidth: 170 }}
        value={value.statuses}
        onChange={(statuses) => patch({ statuses })}
        options={STATUS_OPTIONS}
        maxTagCount="responsive"
      />
      <Select
        mode="multiple"
        allowClear
        placeholder="类型"
        style={{ minWidth: 150 }}
        value={value.types}
        onChange={(types) => patch({ types })}
        options={TYPE_OPTIONS}
        maxTagCount="responsive"
      />
      {groupOptions.length > 0 && (
        <Select
          mode="multiple"
          allowClear
          placeholder="目录组"
          style={{ minWidth: 160 }}
          value={value.groupIds}
          onChange={(groupIds) => patch({ groupIds })}
          options={groupOptions}
          maxTagCount="responsive"
        />
      )}
      <Select
        style={{ width: 130 }}
        value={value.selection}
        onChange={(selection) => patch({ selection })}
        options={[
          { value: "all", label: "全部条目" },
          { value: "selected", label: "只看已选" },
          { value: "unselected", label: "只看未选" },
        ]}
      />
      <Text type="secondary">
        筛选结果 {totalCount} 项 · 已选（筛选后）{selectedCount}
      </Text>
      {extra}
    </Space>
  );
}
