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
  Row,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { ApiOutlined, SaveOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { api } from "../api";
import type { ConfigFieldSpec, ConfigSchema, ProviderTestResult, RuleInfo } from "../types";

const { Text, Title } = Typography;

export default function SettingsPage() {
  const [schema, setSchema] = useState<ConfigSchema | null>(null);
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [rules, setRules] = useState<RuleInfo[]>([]);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<ProviderTestResult | null>(null);

  useEffect(() => {
    Promise.all([api.configSchema(), api.config(), api.rules()])
      .then(([schemaResult, configResult, rulesResult]) => {
        setSchema(schemaResult);
        setValues(configResult.values);
        setRules(rulesResult.rules);
      })
      .catch((error) => message.error(`读取设置失败：${(error as Error).message}`));
  }, []);

  const groups = useMemo(() => {
    if (!schema) return [];
    return schema.groups
      .map((group) => ({
        ...group,
        fields: schema.fields.filter((field) => field.group === group.key),
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
          style={{ width: 220 }}
          value={value as string}
          onChange={(next) => updateValue(field.key, next)}
          options={field.options.map((option) => ({ value: option, label: option }))}
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
    { title: "示例", dataIndex: "sample", width: 260, render: (value: string) => <Text type="secondary">{value}</Text> },
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
                  <Table
                    rowKey="id"
                    size="small"
                    columns={ruleColumns}
                    dataSource={rules}
                    pagination={false}
                    scroll={{ x: 1000 }}
                  />
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
    </Space>
  );
}
