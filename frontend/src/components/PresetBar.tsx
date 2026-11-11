import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Divider,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Switch,
  Tag,
  Typography,
  message,
} from "antd";
import { SaveOutlined, SettingOutlined } from "@ant-design/icons";
import { api } from "../api";
import type {
  ConfigFieldSpec,
  ConfigSchema,
  NamingPreset,
  WorkflowPreset,
} from "../types";

const { Text } = Typography;

const GROUP_HINTS: Record<string, string> = {
  parse: "扫描时怎么认作品、认季、认特典",
  providers: "TMDB 与 AI，密钥请到「设置」页填写",
  execution: "执行时的节奏与冲突处理",
  scraper: "预留功能，当前版本不生效",
};

// 这些设置归命名预设管，不在工作流预设的微调里出现。
const NAMING_SETTING_KEYS = new Set([
  "naming.subtitle_mode",
  "naming.auto_append_ext",
  "naming.max_filename_bytes",
  "naming.forbidden_chars",
  "naming.specials_dir",
  "naming.preserve_structure",
  "naming.movie_layout",
  "naming.special_folder_suffix",
  "parse.default_season",
  "parse.special_season_zero",
  "parse.ova_policy",
  "execution.special_video_policy",
  "execution.special_folder_conflict",
]);

interface Props {
  workflowPresets: WorkflowPreset[];
  workflowPresetId: string | null;
  onWorkflowPresetChange: (presetId: string) => void;
  namingPresets: NamingPreset[];
  namingPresetId: string | null;
  onNamingPresetChange: (presetId: string) => void;
  reloadWorkflowPresets: (selectId?: string) => Promise<WorkflowPreset[]>;
  onSettingsChanged: () => void;
  settingsVersion: number;
}

export default function PresetBar({
  workflowPresets,
  workflowPresetId,
  onWorkflowPresetChange,
  namingPresets,
  namingPresetId,
  onNamingPresetChange,
  reloadWorkflowPresets,
  onSettingsChanged,
  settingsVersion,
}: Props) {
  const [schema, setSchema] = useState<ConfigSchema | null>(null);
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [dirty, setDirty] = useState(false);
  const [diffCount, setDiffCount] = useState(0);
  const [savingSettings, setSavingSettings] = useState(false);
  const [savingPreset, setSavingPreset] = useState(false);
  const [tuneOpen, setTuneOpen] = useState(false);
  const [saveAsOpen, setSaveAsOpen] = useState(false);
  const [saveAsName, setSaveAsName] = useState("");

  useEffect(() => {
    Promise.all([api.configSchema(), api.config()])
      .then(([schemaResult, configResult]) => {
        setSchema(schemaResult);
        setValues(configResult.values);
      })
      .catch((error) => message.error(`读取工作流设置失败：${(error as Error).message}`));
  }, [settingsVersion]);

  const refreshDirty = async (id: string | null) => {
    if (!id) {
      setDirty(false);
      setDiffCount(0);
      return;
    }
    try {
      const diff = await api.workflowPresetSettingsDiff(id);
      setDirty(diff.changes.length > 0);
      setDiffCount(diff.changes.length);
    } catch {
      setDirty(false);
      setDiffCount(0);
    }
  };

  useEffect(() => {
    void refreshDirty(workflowPresetId);
  }, [workflowPresetId, settingsVersion]);

  const currentPreset =
    workflowPresets.find((item) => item.id === workflowPresetId) ?? null;
  const namingChanged = Boolean(
    namingPresetId &&
      currentPreset &&
      namingPresetId !== currentPreset.naming_preset_id,
  );

  const groups = useMemo(() => {
    if (!schema) return [];
    return schema.groups
      .filter((group) => group.key !== "naming" && group.key !== "language")
      .map((group) => ({
        ...group,
        fields: schema.fields.filter(
          (field) =>
            field.group === group.key &&
            field.type !== "secret" &&
            !NAMING_SETTING_KEYS.has(field.key),
        ),
      }))
      .filter((group) => group.fields.length > 0);
  }, [schema]);

  const updateValue = (key: string, value: unknown) => {
    setValues((previous) => ({ ...previous, [key]: value }));
  };

  const renderField = (field: ConfigFieldSpec) => {
    const value = values[field.key];
    if (field.type === "bool") {
      return (
        <Switch checked={Boolean(value)} onChange={(checked) => updateValue(field.key, checked)} />
      );
    }
    if (field.type === "int") {
      return (
        <InputNumber
          style={{ width: 150 }}
          value={value as number}
          onChange={(next) => updateValue(field.key, next ?? field.default)}
        />
      );
    }
    if (field.type === "select") {
      return (
        <Select
          style={{ width: field.option_labels?.length ? 320 : 200 }}
          value={value as string}
          onChange={(next) => updateValue(field.key, next)}
          options={field.options.map((option, index) => ({
            value: option,
            label: field.option_labels?.[index] || option,
          }))}
        />
      );
    }
    return (
      <Input
        style={{ maxWidth: 520 }}
        value={(value as string) ?? ""}
        onChange={(event) => updateValue(field.key, event.target.value)}
      />
    );
  };

  const saveSettings = async () => {
    setSavingSettings(true);
    try {
      const result = await api.saveConfig(values);
      setValues(result.values);
      await refreshDirty(workflowPresetId);
      onSettingsChanged();
      message.success("微调已保存，工作流预设会显示「已修改」");
    } catch (error) {
      message.error(`保存失败：${(error as Error).message}`);
    } finally {
      setSavingSettings(false);
    }
  };

  const overwritePreset = async () => {
    if (!currentPreset) {
      message.warning("请先选择一个工作流预设");
      return;
    }
    setSavingPreset(true);
    try {
      await api.updateWorkflowPreset(currentPreset.id, {
        name: currentPreset.name,
        library_profile: currentPreset.library_profile,
        naming_preset_id: namingPresetId ?? currentPreset.naming_preset_id,
      });
      await reloadWorkflowPresets(currentPreset.id);
      await refreshDirty(currentPreset.id);
      message.success("已用当前设置覆盖这个工作流预设");
    } catch (error) {
      message.error(`覆盖失败：${(error as Error).message}`);
    } finally {
      setSavingPreset(false);
    }
  };

  const saveAsNew = async () => {
    if (!currentPreset) {
      message.warning("请先选择一个工作流预设");
      return;
    }
    const name = saveAsName.trim();
    if (!name) {
      message.warning("请给新预设起个名字");
      return;
    }
    try {
      const created = await api.createWorkflowPreset({
        name,
        library_profile: currentPreset.library_profile,
        naming_preset_id: namingPresetId ?? currentPreset.naming_preset_id,
      });
      await reloadWorkflowPresets(created.id);
      setSaveAsOpen(false);
      setSaveAsName("");
      onWorkflowPresetChange(created.id);
      message.success(`已另存为新工作流预设「${created.name}」`);
    } catch (error) {
      message.error(`另存失败：${(error as Error).message}`);
    }
  };

  return (
    <Card size="small" style={{ borderLeft: "3px solid var(--ani-primary)" }}>
      <Space wrap size={12} align="center">
        <Text strong>工作流预设</Text>
        <Select
          style={{ minWidth: 240 }}
          value={workflowPresetId ?? undefined}
          onChange={(value) => onWorkflowPresetChange(value)}
          options={workflowPresets.map((item) => ({
            value: item.id,
            label: `${item.name}${item.is_default ? "（默认）" : ""}`,
          }))}
          placeholder="选择工作流预设"
        />
        {dirty ? (
          <Tag color="gold">已修改（工作流，{diffCount} 项）</Tag>
        ) : (
          <Tag color="green">和预设一致</Tag>
        )}
        {namingChanged && <Tag color="blue">命名预设已换</Tag>}
        <Text type="secondary">使用哪个命名预设</Text>
        <Select
          style={{ minWidth: 200 }}
          value={namingPresetId ?? undefined}
          onChange={(value) => onNamingPresetChange(value)}
          options={namingPresets.map((item) => ({
            value: item.id,
            label: `${item.name}${item.is_default ? "（默认）" : ""}`,
          }))}
          placeholder="选择命名预设"
        />
        <Button icon={<SettingOutlined />} onClick={() => setTuneOpen((open) => !open)}>
          {tuneOpen ? "收起微调" : "微调选项"}
        </Button>
        <Button
          icon={<SaveOutlined />}
          loading={savingPreset}
          disabled={(!dirty && !namingChanged) || !currentPreset}
          onClick={overwritePreset}
        >
          覆盖当前预设
        </Button>
        <Button disabled={!currentPreset} onClick={() => setSaveAsOpen(true)}>
          另存为新预设
        </Button>
        {!dirty && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            工作流预设只换识别、AI、执行这些设置；模板和命名选项在「模板与预览」页编辑。
          </Text>
        )}
      </Space>

      {tuneOpen && (
        <>
          <Divider style={{ margin: "12px 0" }} />
          {groups.map((group) => (
            <div key={group.key} style={{ marginBottom: 12 }}>
              <Text strong>{group.label}</Text>
              <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
                {GROUP_HINTS[group.key] ?? ""}
              </Text>
              <div style={{ marginTop: 8 }}>
                {group.fields.map((field) => (
                  <div
                    key={field.key}
                    style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 6 }}
                  >
                    <div style={{ width: 220 }}>
                      <Text>{field.label}</Text>
                    </div>
                    <div style={{ flex: 1 }}>{renderField(field)}</div>
                  </div>
                ))}
              </div>
            </div>
          ))}
          <Space>
            <Button type="primary" loading={savingSettings} onClick={saveSettings}>
              保存微调
            </Button>
            <Text type="secondary" style={{ fontSize: 12 }}>
              保存后工作流预设会显示「已修改」，再点「覆盖当前预设」才会写回预设。
              密钥与语言设置请到「设置」页填写，不会写进预设。
            </Text>
          </Space>
        </>
      )}

      <Modal
        title="另存为新工作流预设"
        open={saveAsOpen}
        okText="保存"
        cancelText="取消"
        onOk={saveAsNew}
        onCancel={() => setSaveAsOpen(false)}
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="只保存识别、AI、执行这些工作流设置，以及当前选中的命名预设；模板和其他预设不受影响。"
        />
        <Input
          placeholder="新预设名字，例如：网盘 + 小批量"
          value={saveAsName}
          onChange={(event) => setSaveAsName(event.target.value)}
          onPressEnter={saveAsNew}
        />
      </Modal>
    </Card>
  );
}
