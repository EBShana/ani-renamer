import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  Collapse,
  Divider,
  Empty,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Row,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import { ApiOutlined, SaveOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { api } from "../api";
import type { ConfigFieldSpec, ConfigSchema, PatternCacheEntry, ProviderTestResult, RuleInfo, RuleInput } from "../types";

const EMPTY_RULE: RuleInput = {
  name: "",
  scope: "filename",
  kind: "episode",
  priority: 0,
  regex: "",
  enabled: true,
  description: "",
  sample: "",
};

const { Text, Title } = Typography;

const formatTime = (value?: string) => {
  if (!value) return "—";
  return value.replace("T", " ").slice(0, 19);
};

const friendlyError = (error: unknown, fallback: string) => {
  const text = error instanceof Error ? error.message : "";
  return /[\u4e00-\u9fa5]/.test(text) ? text : fallback;
};

export default function SettingsPage() {
  const [schema, setSchema] = useState<ConfigSchema | null>(null);
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [rules, setRules] = useState<RuleInfo[]>([]);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<ProviderTestResult | null>(null);
  const [ruleModalOpen, setRuleModalOpen] = useState(false);
  const [editingRule, setEditingRule] = useState<RuleInfo | null>(null);
  const [ruleDraft, setRuleDraft] = useState<RuleInput>(EMPTY_RULE);
  const [ruleSaving, setRuleSaving] = useState(false);
  const [authEnabled, setAuthEnabled] = useState(false);
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [passwordValue, setPasswordValue] = useState("");
  const [passwordSaving, setPasswordSaving] = useState(false);
  const [patternEntries, setPatternEntries] = useState<PatternCacheEntry[]>([]);
  const [patternLoading, setPatternLoading] = useState(false);
  const [patternToggling, setPatternToggling] = useState("");
  const [patternClearing, setPatternClearing] = useState(false);
  const [groupNames, setGroupNames] = useState<Record<string, string>>({});

  const loadPatterns = async () => {
    setPatternLoading(true);
    try {
      const result = await api.aiPatterns();
      setPatternEntries(result.entries);
    } catch {
      // 缓存列表读不到不阻塞设置页其他内容
      setPatternEntries([]);
    } finally {
      setPatternLoading(false);
    }
  };

  useEffect(() => {
    Promise.all([api.configSchema(), api.config(), api.rules()])
      .then(([schemaResult, configResult, rulesResult]) => {
        setSchema(schemaResult);
        setValues(configResult.values);
        setRules(rulesResult.rules);
      })
      .catch((error) => message.error(`读取设置失败：${(error as Error).message}`));
    api
      .authStatus()
      .then((status) => setAuthEnabled(status.enabled))
      .catch(() => setAuthEnabled(false));
    void loadPatterns();
    api
      .groups()
      .then((result) => {
        const map: Record<string, string> = {};
        for (const item of result.groups) map[item.id] = item.name;
        setGroupNames(map);
      })
      .catch(() => setGroupNames({}));
  }, []);

  const groups = useMemo(() => {
    if (!schema) return [];
    return schema.groups
      .map((group) => ({
        ...group,
        // 「同款目录自动套用」放到下面的缓存管理卡片里，避免出现两个开关
        fields: schema.fields.filter(
          (field) =>
            field.group === group.key &&
            field.key !== "providers.ai_pattern_cache_auto_apply",
        ),
      }))
      .filter((group) => group.fields.length > 0);
  }, [schema]);

  const updateValue = (key: string, value: unknown) => {
    setValues((previous) => ({ ...previous, [key]: value }));
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const result = await api.saveConfig(values);
      setValues(result.values);
      message.success("设置已保存");
    } catch (error) {
      message.error(`保存失败：${(error as Error).message}`);
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    setTesting(true);
    try {
      const result = await api.providerTest({
        proxy_url: String(values["providers.proxy_url"] ?? ""),
        tmdb_api_key: String(values["providers.tmdb_api_key"] ?? ""),
      });
      setTestResult(result);
    } catch (error) {
      message.error(`测试失败：${(error as Error).message}`);
    } finally {
      setTesting(false);
    }
  };

  const renderField = (field: ConfigFieldSpec) => {
    const value = values[field.key];
    if (field.type === "bool") {
      return <Switch checked={Boolean(value)} onChange={(checked) => updateValue(field.key, checked)} />;
    }
    if (field.type === "int") {
      return (
        <InputNumber
          style={{ width: 160 }}
          value={value as number}
          onChange={(next) => updateValue(field.key, next ?? field.default)}
        />
      );
    }
    if (field.type === "select") {
      return (
        <Select
          style={{ width: field.option_labels?.length ? 340 : 220 }}
          value={value as string}
          onChange={(next) => updateValue(field.key, next)}
          options={field.options.map((option, index) => ({
            value: option,
            label: field.option_labels?.[index] || option,
          }))}
        />
      );
    }
    if (field.type === "secret") {
      return (
        <Input.Password
          style={{ maxWidth: 420 }}
          value={(value as string) ?? ""}
          onChange={(event) => updateValue(field.key, event.target.value)}
          placeholder="未配置"
        />
      );
    }
    return (
      <Input
        style={{ maxWidth: 620 }}
        value={(value as string) ?? ""}
        onChange={(event) => updateValue(field.key, event.target.value)}
      />
    );
  };

  const toInput = (rule: RuleInfo): RuleInput => ({
    name: rule.name,
    scope: rule.scope,
    kind: rule.kind,
    priority: rule.priority,
    regex: rule.regex,
    enabled: rule.enabled,
    description: rule.description,
    sample: rule.sample,
  });

  const openCreateRule = () => {
    setEditingRule(null);
    setRuleDraft({ ...EMPTY_RULE });
    setRuleModalOpen(true);
  };

  const openEditRule = (rule: RuleInfo) => {
    setEditingRule(rule);
    setRuleDraft(toInput(rule));
    setRuleModalOpen(true);
  };

  const saveRuleDraft = async () => {
    setRuleSaving(true);
    try {
      const result = editingRule
        ? await api.updateRule(editingRule.id, ruleDraft)
        : await api.createRule(ruleDraft);
      setRules((previous) => {
        const exists = previous.some((item) => item.id === result.rule.id);
        return exists
          ? previous.map((item) => (item.id === result.rule.id ? result.rule : item))
          : [...previous, result.rule];
      });
      setRuleModalOpen(false);
      message.success("规则已保存，只对下一次扫描生效");
    } catch (error) {
      message.error(`规则保存失败：${(error as Error).message}`);
    } finally {
      setRuleSaving(false);
    }
  };

  const toggleRule = async (rule: RuleInfo, enabled: boolean) => {
    try {
      const result = await api.updateRule(rule.id, { ...toInput(rule), enabled });
      setRules((previous) => previous.map((item) => (item.id === rule.id ? result.rule : item)));
      message.success(enabled ? "规则已启用" : "规则已停用");
    } catch (error) {
      message.error(`操作失败：${(error as Error).message}`);
    }
  };

  const removeRule = async (rule: RuleInfo) => {
    try {
      await api.deleteRule(rule.id);
      setRules((previous) => previous.filter((item) => item.id !== rule.id));
      message.success("规则已删除");
    } catch (error) {
      message.error(`删除失败：${(error as Error).message}`);
    }
  };

  const restoreRules = async () => {
    try {
      const result = await api.resetRules();
      setRules(result.rules);
      message.success("内置规则已恢复默认，自定义规则保持不变");
    } catch (error) {
      message.error(`恢复失败：${(error as Error).message}`);
    }
  };

  const togglePatternEntry = async (entry: PatternCacheEntry, enabled: boolean) => {
    setPatternToggling(entry.id);
    try {
      const updated = await api.toggleAiPattern(entry.id, enabled);
      setPatternEntries((previous) =>
        previous.map((item) => (item.id === updated.id ? updated : item)),
      );
      message.success(
        enabled ? "已启用这条规律" : "已停用这条规律，同款目录也不会再自动套用",
      );
    } catch (error) {
      message.error(`操作失败：${friendlyError(error, "请稍后再试")}`);
    } finally {
      setPatternToggling("");
    }
  };

  const removePatternEntry = async (entry: PatternCacheEntry) => {
    try {
      await api.deleteAiPattern(entry.id);
      setPatternEntries((previous) => previous.filter((item) => item.id !== entry.id));
      message.success("已删除这条规律");
    } catch (error) {
      message.error(`删除失败：${friendlyError(error, "请稍后再试")}`);
    }
  };

  const clearPatternEntries = () => {
    if (!patternEntries.length) {
      message.info("当前没有可清空的命名规律");
      return;
    }
    Modal.confirm({
      title: "清空全部命名规律？",
      content: (
        <div>
          将清空 {patternEntries.length} 条规律。清空后同款目录不会再自动套用，需要重新分析才会生成新规律。
          此操作不可撤销。
        </div>
      ),
      okText: "确认清空",
      okButtonProps: { danger: true },
      cancelText: "先不清空",
      onOk: async () => {
        setPatternClearing(true);
        try {
          const result = await api.clearAiPatterns();
          setPatternEntries([]);
          message.success(`已清空 ${result.cleared} 条命名规律`);
        } catch (error) {
          message.error(`清空失败：${friendlyError(error, "请稍后再试")}`);
        } finally {
          setPatternClearing(false);
        }
      },
    });
  };

  const savePassword = async () => {
    setPasswordSaving(true);
    try {
      const result = await api.authSetPassword(passwordValue);
      setAuthEnabled(result.enabled);
      setPasswordOpen(false);
      setPasswordValue("");
      message.success("访问密码已启用，当前浏览器会保持登录约 7 天");
    } catch (error) {
      message.error(`设置失败：${(error as Error).message}`);
    } finally {
      setPasswordSaving(false);
    }
  };

  const disablePassword = async () => {
    try {
      const result = await api.authSetPassword(null);
      setAuthEnabled(result.enabled);
      message.success("访问密码已关闭，打开网页即可直接使用");
    } catch (error) {
      message.error(`关闭失败：${(error as Error).message}`);
    }
  };

  const logout = async () => {
    try {
      await api.authLogout();
    } finally {
      window.location.reload();
    }
  };

  const ruleColumns: ColumnsType<RuleInfo> = [
    { title: "名称", dataIndex: "name", width: 180 },
    { title: "作用域", dataIndex: "scope", width: 96, render: (value: string) => <Tag>{value === "filename" ? "文件名" : "目录名"}</Tag> },
    { title: "类型", dataIndex: "kind", width: 90 },
    { title: "优先级", dataIndex: "priority", width: 80 },
    {
      title: "正则",
      dataIndex: "regex",
      render: (value: string) => <div className="mono" style={{ wordBreak: "break-all", fontSize: 12 }}>{value}</div>,
    },
    { title: "示例", dataIndex: "sample", width: 200, render: (value: string) => <Text type="secondary">{value}</Text> },
    {
      title: "来源",
      dataIndex: "builtin",
      width: 90,
      render: (value: boolean) => <Tag color={value ? "blue" : "green"}>{value ? "内置" : "自定义"}</Tag>,
    },
    {
      title: "启用",
      dataIndex: "enabled",
      width: 80,
      render: (value: boolean, record) => (
        <Switch size="small" checked={value} onChange={(checked) => void toggleRule(record, checked)} />
      ),
    },
    {
      title: "操作",
      width: 140,
      render: (_, record) => (
        <Space>
          <Button size="small" onClick={() => openEditRule(record)}>
            编辑
          </Button>
          {!record.builtin && (
            <Popconfirm title="删除这条规则？" onConfirm={() => void removeRule(record)}>
              <Button size="small" danger>
                删除
              </Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  const patternColumns: ColumnsType<PatternCacheEntry> = [
    {
      title: "同款目录标识",
      dataIndex: "fingerprint",
      width: 160,
      render: (value: string) => (
        <Tooltip title={value}>
          <Text className="mono" style={{ fontSize: 12 }}>
            {value ? `${value.slice(0, 10)}…` : "—"}
          </Text>
        </Tooltip>
      ),
    },
    {
      title: "目录组",
      dataIndex: "group_id",
      width: 160,
      render: (value: string) => groupNames[value] || value || "（未知目录组）",
    },
    {
      title: "子目录",
      dataIndex: "dir_rel",
      render: (value: string) => (
        <span className="mono" style={{ fontSize: 12, wordBreak: "break-all" }}>
          {value || "（根目录）"}
        </span>
      ),
    },
    {
      title: "来源",
      dataIndex: "source_label",
      width: 140,
      render: (value: string) => <Tag>{value || "AI 生成"}</Tag>,
    },
    { title: "命中次数", dataIndex: "hits", width: 90 },
    {
      title: "最近使用",
      width: 170,
      render: (_, record) => (
        <Text type="secondary" style={{ fontSize: 12 }}>
          {formatTime(record.last_used_at || record.updated_at)}
        </Text>
      ),
    },
    {
      title: "启用",
      dataIndex: "enabled",
      width: 80,
      render: (value: boolean, record) => (
        <Switch
          size="small"
          checked={value}
          loading={patternToggling === record.id}
          onChange={(checked) => void togglePatternEntry(record, checked)}
        />
      ),
    },
    {
      title: "操作",
      width: 90,
      render: (_, record) => (
        <Popconfirm
          title="删除这条规律？"
          description="删除后同款目录不会再自动套用。"
          onConfirm={() => void removePatternEntry(record)}
        >
          <Button size="small" danger>
            删除
          </Button>
        </Popconfirm>
      ),
    },
  ];

  if (!schema) {
    return <Empty description="正在读取配置..." />;
  }

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Card>
        <Title level={5} style={{ marginTop: 0 }}>
          运行环境
        </Title>
        <Alert
          type="info"
          showIcon
          message={`媒体根目录：${schema.media_root}`}
          description="容器部署时请把动画目录挂载到该路径；所有扫描路径都必须位于此目录内。"
        />
        <Alert
          type="success"
          showIcon
          style={{ marginTop: 12 }}
          message="这里是所有工作流设置的唯一来源"
          description="字幕、特典、季号、冲突处理等设置对所有预设生效。切换预设时，如果预设里记录过一套不同的设置，系统会先询问你是否覆盖当前设置。"
        />
        {rules.length > 0 && (
          <Collapse
            style={{ marginTop: 12 }}
            items={[
              {
                key: "rules",
                label: `识别规则（${rules.length} 条，数据驱动，可扩展）`,
                children: (
                  <Space direction="vertical" size={12} style={{ width: "100%" }}>
                    <Space wrap>
                      <Button size="small" onClick={openCreateRule}>
                        新增规则
                      </Button>
                      <Popconfirm
                        title="恢复内置规则？"
                        description="只重置内置规则，你自己新增的规则会保留。"
                        onConfirm={() => void restoreRules()}
                      >
                        <Button size="small">恢复内置默认</Button>
                      </Popconfirm>
                      <Text type="secondary">内置规则可以停用或改优先级，但不能删除。</Text>
                    </Space>
                    <Alert
                      type="warning"
                      showIcon
                      message="规则改动只对下一次扫描生效"
                      description="已经生成的扫描结果不会自动重新识别；需要时请重新扫描。保存时会校验正则，写错会给出中文提示。"
                    />
                    <Table
                      rowKey="id"
                      size="small"
                      columns={ruleColumns}
                      dataSource={rules}
                      pagination={false}
                      scroll={{ x: 1200 }}
                    />
                  </Space>
                ),
              },
            ]}
          />
        )}
      </Card>

      {groups.map((group) => (
        <Card
          key={group.key}
          title={group.label}
          extra={
            group.key === "parse" ? (
              <Text type="secondary">配置项由后端 Schema 驱动，新增项无需改前端</Text>
            ) : null
          }
        >
          {group.key === "providers" && (
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 16 }}
              message="AI 辅助只在你手动点击时调用"
              description="只发送文件名、目录名、候选标题等文本，绝不发送文件内容；AI 只给建议，确认后才生效。Key 不会出现在日志、接口响应或界面提示里。"
            />
          )}
          {group.fields.map((field) => (
            <Row key={field.key} gutter={16} style={{ marginBottom: 16 }}>
              <Col xs={24} md={7}>
                <div style={{ fontWeight: 500 }}>{field.label}</div>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {field.key}
                </Text>
              </Col>
              <Col xs={24} md={9}>
                {renderField(field)}
              </Col>
              <Col xs={24} md={8}>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {field.description}
                </Text>
              </Col>
            </Row>
          ))}
        </Card>
      ))}

      <Card
        title="命名规律缓存"
        extra={<Text type="secondary">共 {patternEntries.length} 条</Text>}
      >
        <Space direction="vertical" size={12} style={{ width: "100%" }}>
          <Alert
            type="info"
            showIcon
            message="同款目录自动套用"
            description="「同款目录」指目录组、子目录和文件名形状都完全一致。打开后，规律已通过自检的同款目录会自动套用，并在结果里标明来源为「缓存命中」；关掉后一律只当候选，等你确认。这个开关改完要保存设置。"
          />
          <Space wrap size={12}>
            <Switch
              checked={Boolean(values["providers.ai_pattern_cache_auto_apply"])}
              onChange={(checked) =>
                updateValue("providers.ai_pattern_cache_auto_apply", checked)
              }
            />
            <Text>
              {values["providers.ai_pattern_cache_auto_apply"]
                ? "已开启：同款目录会自动套用"
                : "已关闭：同款目录只当候选"}
            </Text>
            <Button
              size="small"
              danger
              loading={patternClearing}
              disabled={!patternEntries.length}
              onClick={clearPatternEntries}
            >
              一键清空
            </Button>
          </Space>
          <Table
            rowKey="id"
            size="small"
            loading={patternLoading}
            columns={patternColumns}
            dataSource={patternEntries}
            pagination={false}
            scroll={{ x: 1080 }}
            locale={{ emptyText: <Empty description="还没有保存过命名规律" /> }}
          />
        </Space>
      </Card>

      <Card title="访问密码" extra={<Text type="secondary">密码只保存不可逆哈希</Text>}>
        <Space direction="vertical" size={12} style={{ width: "100%" }}>
          <Alert
            type={authEnabled ? "success" : "info"}
            showIcon
            message={authEnabled ? "访问密码已开启" : "当前未设置访问密码"}
            description={
              authEnabled
                ? "打开网页需要先登录，登录状态约 7 天；忘记密码时可在 settings.json 里删除 auth 字段并重启容器。"
                : "保持现在「直接打开就能用」的状态。设置后所有接口（健康检查除外）都需要登录。"
            }
          />
          <Space wrap>
            <Button
              type="primary"
              onClick={() => {
                setPasswordValue("");
                setPasswordOpen(true);
              }}
            >
              {authEnabled ? "修改密码" : "设置访问密码"}
            </Button>
            {authEnabled && (
              <Popconfirm
                title="关闭访问密码？"
                description="关闭后打开网页不再需要登录。"
                onConfirm={() => void disablePassword()}
              >
                <Button danger>关闭密码</Button>
              </Popconfirm>
            )}
            {authEnabled && <Button onClick={() => void logout()}>退出登录</Button>}
          </Space>
        </Space>
      </Card>

      <Card title="网络连通性测试" extra={<Text type="secondary">会使用上面的代理和 API Key</Text>}>
        <Space direction="vertical" size={12} style={{ width: "100%" }}>
          <Space wrap>
            <Button icon={<ApiOutlined />} loading={testing} onClick={handleTest}>
              测试 TMDB 连通性
            </Button>
            <Text type="secondary">
              代理跑在 Windows 宿主机、程序在 Docker 里时，代理地址要填
              http://host.docker.internal:端口，不能填 127.0.0.1。
            </Text>
          </Space>
          {testResult && (
            <Alert
              type={testResult.ok ? "success" : "error"}
              showIcon
              message={testResult.message}
              description={
                testResult.detail ||
                (testResult.proxy ? "本次通过代理连接 TMDB" : "本次为直连")
              }
            />
          )}
        </Space>
      </Card>

      <Card>
        <Space>
          <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={handleSave}>
            保存设置
          </Button>
          <Text type="secondary">配置保存在 /config/settings.json，可随容器一起备份。</Text>
        </Space>
        <Divider />
        <Text type="secondary">
          设置保存后立即生效。预设只保存「正片 / 特典 / 剧场版」三种命名规则。
        </Text>
      </Card>

      <Modal
        title={authEnabled ? "修改访问密码" : "设置访问密码"}
        open={passwordOpen}
        onCancel={() => setPasswordOpen(false)}
        onOk={() => void savePassword()}
        okText="保存"
        confirmLoading={passwordSaving}
        width={420}
      >
        <Space direction="vertical" size={12} style={{ width: "100%" }}>
          <Input.Password
            placeholder="至少 4 个字符"
            value={passwordValue}
            onChange={(event) => setPasswordValue(event.target.value)}
            onPressEnter={() => void savePassword()}
          />
          <Text type="secondary">
            密码只以不可逆哈希保存在 settings.json，接口和界面都不会显示原文。修改后其他设备需要用新密码重新登录。
          </Text>
        </Space>
      </Modal>

      <Modal
        title={editingRule ? "编辑识别规则" : "新增识别规则"}
        open={ruleModalOpen}
        onCancel={() => setRuleModalOpen(false)}
        onOk={() => void saveRuleDraft()}
        okText="保存"
        confirmLoading={ruleSaving}
        width={640}
      >
        <Space direction="vertical" size={10} style={{ width: "100%" }}>
          <div>
            <div style={{ fontWeight: 500 }}>规则名称</div>
            <Input
              value={ruleDraft.name}
              onChange={(event) => setRuleDraft((prev) => ({ ...prev, name: event.target.value }))}
              placeholder="例如：方括号集号"
            />
          </div>
          <Space wrap size={12}>
            <div>
              <div style={{ fontWeight: 500 }}>适用范围</div>
              <Select
                style={{ width: 150 }}
                value={ruleDraft.scope}
                onChange={(scope) => setRuleDraft((prev) => ({ ...prev, scope }))}
                options={[
                  { value: "filename", label: "文件名" },
                  { value: "dirname", label: "目录名" },
                ]}
              />
            </div>
            <div>
              <div style={{ fontWeight: 500 }}>识别类型</div>
              <Select
                style={{ width: 150 }}
                value={ruleDraft.kind}
                onChange={(kind) => setRuleDraft((prev) => ({ ...prev, kind }))}
                options={[
                  { value: "episode", label: "集号" },
                  { value: "special", label: "特典" },
                  { value: "movie", label: "剧场版" },
                  { value: "season", label: "季" },
                  { value: "any", label: "任意" },
                ]}
              />
            </div>
            <div>
              <div style={{ fontWeight: 500 }}>优先级（0-200，越小越先）</div>
              <InputNumber
                style={{ width: 150 }}
                min={0}
                max={200}
                value={ruleDraft.priority}
                onChange={(value) =>
                  setRuleDraft((prev) => ({ ...prev, priority: Number(value ?? 0) }))
                }
              />
            </div>
            <div>
              <div style={{ fontWeight: 500 }}>启用</div>
              <Switch
                checked={ruleDraft.enabled}
                onChange={(enabled) => setRuleDraft((prev) => ({ ...prev, enabled }))}
              />
            </div>
          </Space>
          <div>
            <div style={{ fontWeight: 500 }}>正则表达式</div>
            <Input.TextArea
              rows={4}
              className="mono"
              value={ruleDraft.regex}
              onChange={(event) => setRuleDraft((prev) => ({ ...prev, regex: event.target.value }))}
              placeholder="例如：第\\s*(?P<episode>\\d{1,4})\\s*[集话]"
            />
          </div>
          <div>
            <div style={{ fontWeight: 500 }}>说明</div>
            <Input
              value={ruleDraft.description}
              onChange={(event) =>
                setRuleDraft((prev) => ({ ...prev, description: event.target.value }))
              }
            />
          </div>
          <div>
            <div style={{ fontWeight: 500 }}>示例</div>
            <Input
              value={ruleDraft.sample}
              onChange={(event) => setRuleDraft((prev) => ({ ...prev, sample: event.target.value }))}
            />
          </div>
          <Alert type="info" showIcon message="保存后只对下一次扫描生效" />
        </Space>
      </Modal>
    </Space>
  );
}
