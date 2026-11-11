import { useEffect, useState } from "react";
import { Badge, Button, Drawer, Layout, Menu, Modal, Segmented, Space, Tag, Typography, message, theme } from "antd";
import { EditOutlined, FolderOpenOutlined, LinkOutlined, SettingOutlined } from "@ant-design/icons";
import { api } from "./api";
import type {
  NamingPreset,
  PendingItem,
  QueueView,
  RunStatus,
  ScanPayload,
  WorkflowPreset,
} from "./types";
import { useTheme } from "./theme";
import ScanPage from "./pages/ScanPage";
import MatchPage from "./pages/MatchPage";
import TemplatePage from "./pages/TemplatePage";
import SettingsPage from "./pages/SettingsPage";
import LoginPage from "./pages/LoginPage";

const { Header, Content } = Layout;
const { Text } = Typography;

const STEPS = [
  { key: "scan", label: "扫描" },
  { key: "match", label: "匹配作品" },
  { key: "preview", label: "模板与预览" },
  { key: "done", label: "已完成" },
];

export default function App() {
  const { token } = theme.useToken();
  const { mode, setMode, resolved } = useTheme();
  const [page, setPage] = useState("scan");
  const [scan, setScan] = useState<ScanPayload | null>(null);
  const [queue, setQueue] = useState<QueueView | null>(null);
  const [namingPresets, setNamingPresets] = useState<NamingPreset[]>([]);
  const [namingPresetId, setNamingPresetId] = useState("");
  const [workflowPresets, setWorkflowPresets] = useState<WorkflowPreset[]>([]);
  const [workflowPresetId, setWorkflowPresetId] = useState("");
  // 预设应用 / 设置页保存后 +1，让已挂载的页面重新读设置
  const [settingsVersion, setSettingsVersion] = useState(0);
  const [auth, setAuth] = useState<{ enabled: boolean; authenticated: boolean } | null>(null);
  const ready = auth === null || !auth.enabled || auth.authenticated;
  // 运行记录：本次从扫描到执行停在哪一步，以及待人工清单
  const [runStatus, setRunStatus] = useState<RunStatus | null>(null);
  const [pendingOpen, setPendingOpen] = useState(false);
  const [pendingItems, setPendingItems] = useState<PendingItem[]>([]);

  useEffect(() => {
    api.authStatus().then(setAuth).catch(() => setAuth({ enabled: false, authenticated: true }));
    const onUnauthorized = () => setAuth({ enabled: true, authenticated: false });
    window.addEventListener("ani-unauthorized", onUnauthorized);
    return () => window.removeEventListener("ani-unauthorized", onUnauthorized);
  }, []);

  const loadQueue = async (scanId: string) => {
    try {
      setQueue(await api.queue(scanId));
    } catch (error) {
      message.error(`读取清单失败：${(error as Error).message}`);
    }
  };

  const refreshRun = async () => {
    try {
      setRunStatus(await api.currentRun());
    } catch {
      // 运行记录读取失败不打扰主流程
    }
  };

  const refreshPending = async (scanId?: string) => {
    try {
      const data = await api.syncPending(scanId || scan?.scan_id);
      setRunStatus({ run: data.run, pending: data.counts });
      setPendingItems(data.items);
    } catch {
      // 清单刷新失败时保留上一次结果
    }
  };

  const openPending = async () => {
    setPendingOpen(true);
    await refreshPending();
  };

  const updateScan = (payload: ScanPayload | null) => {
    setScan(payload);
    if (payload) {
      void loadQueue(payload.scan_id);
      void refreshPending(payload.scan_id);
    } else {
      setQueue(null);
    }
  };

  useEffect(() => {
    if (scan?.scan_id) void loadQueue(scan.scan_id);
  }, [scan?.scan_id]);

  useEffect(() => {
    if (!ready) return;
    void refreshRun();
    const timer = window.setInterval(() => void refreshRun(), 10000);
    return () => window.clearInterval(timer);
  }, [ready]);

  const jumpToPending = (kind: string) => {
    if (kind === "conflict" || kind.startsWith("preview")) setPage("templates");
    else if (kind === "unassigned" || kind === "special" || kind === "season") setPage("match");
    else setPage("scan");
    setPendingOpen(false);
  };

  const stagePage = (stage?: string) => {
    if (stage === "match") return "match";
    if (stage === "preview" || stage === "execute" || stage === "done") return "templates";
    return "scan";
  };

  const loadNamingPresets = async (selectId?: string) => {
    try {
      const response = await api.namingPresets();
      setNamingPresets(response.presets);
      setNamingPresetId((current) => {
        if (selectId) return selectId;
        if (response.presets.some((item) => item.id === current)) return current;
        const fallback =
          response.presets.find((item) => item.is_default) ?? response.presets[0];
        return fallback ? fallback.id : "";
      });
      return response.presets;
    } catch (error) {
      message.error(`读取命名预设失败：${(error as Error).message}`);
      return [];
    }
  };

  const loadWorkflowPresets = async (selectId?: string) => {
    try {
      const response = await api.workflowPresets();
      setWorkflowPresets(response.presets);
      setWorkflowPresetId((current) => {
        if (selectId) return selectId;
        if (response.presets.some((item) => item.id === current)) return current;
        const fallback =
          response.presets.find((item) => item.is_default) ?? response.presets[0];
        return fallback ? fallback.id : "";
      });
      return response.presets;
    } catch (error) {
      message.error(`读取工作流预设失败：${(error as Error).message}`);
      return [];
    }
  };

  useEffect(() => {
    if (ready) {
      void loadNamingPresets();
      void loadWorkflowPresets();
    }
  }, [ready]);

  const changeWorkflowPreset = async (id: string) => {
    if (id === workflowPresetId) return;
    const namingId =
      workflowPresets.find((item) => item.id === id)?.naming_preset_id ?? "";
    try {
      const diff = await api.workflowPresetSettingsDiff(id);
      if (!diff.changes.length) {
        setWorkflowPresetId(id);
        if (namingId) setNamingPresetId(namingId);
        return;
      }
      const lines = diff.changes.map(
        (change) => `${change.label}：${String(change.current ?? "（空）")} → ${String(change.next ?? "（空）")}`,
      );
      Modal.confirm({
        title: `切到工作流预设「${diff.preset_name}」？`,
        content: (
          <div>
            <div>这个预设是一整套工作流设置（识别、AI、执行），和你现在的设置不一样：</div>
            <div
              style={{
                marginTop: 8,
                maxHeight: 260,
                overflow: "auto",
                whiteSpace: "pre-wrap",
                color: token.colorTextTertiary,
              }}
            >
              {lines.join("\n")}
            </div>
            <div style={{ marginTop: 8 }}>
              「按预设设置切换」会整套替换你现在调过的值；想保留现在的设置，就选「只换名字，不改设置」。
              模板和命名选项不受影响，它们由命名预设管。
            </div>
          </div>
        ),
        okText: "按预设设置切换",
        cancelText: "只换名字，不改设置",
        onOk: async () => {
          await api.applyWorkflowPresetSettings(id);
          setWorkflowPresetId(id);
          if (namingId) setNamingPresetId(namingId);
          setSettingsVersion((value) => value + 1);
          message.success("已按工作流预设切换");
        },
        onCancel: () => {
          setWorkflowPresetId(id);
          if (namingId) setNamingPresetId(namingId);
        },
      });
    } catch (error) {
      message.error(`读取工作流预设设置失败：${(error as Error).message}`);
      setWorkflowPresetId(id);
      if (namingId) setNamingPresetId(namingId);
    }
  };

  const changeNamingPreset = async (id: string) => {
    if (id === namingPresetId) return;
    try {
      const diff = await api.namingPresetSettingsDiff(id);
      if (!diff.changes.length) {
        setNamingPresetId(id);
        return;
      }
      const lines = diff.changes.map(
        (change) => `${change.label}：${String(change.current ?? "（空）")} → ${String(change.next ?? "（空）")}`,
      );
      Modal.confirm({
        title: `切到命名预设「${diff.preset_name}」？`,
        content: (
          <div>
            <div>这个命名预设带着一套命名与目录摆放选项，和你现在的设置不一样：</div>
            <div
              style={{
                marginTop: 8,
                maxHeight: 260,
                overflow: "auto",
                whiteSpace: "pre-wrap",
                color: token.colorTextTertiary,
              }}
            >
              {lines.join("\n")}
            </div>
            <div style={{ marginTop: 8 }}>
              模板会直接换成这个预设的模板；选「按预设设置切换」还会一并替换命名与目录摆放选项。
            </div>
          </div>
        ),
        okText: "按预设设置切换",
        cancelText: "只换模板，不改选项",
        onOk: async () => {
          await api.applyNamingPresetSettings(id);
          setNamingPresetId(id);
          setSettingsVersion((value) => value + 1);
          message.success("已按命名预设切换");
        },
        onCancel: () => setNamingPresetId(id),
      });
    } catch (error) {
      message.error(`读取命名预设设置失败：${(error as Error).message}`);
      setNamingPresetId(id);
    }
  };

  if (!ready) {
    return <LoginPage onLoggedIn={() => setAuth({ enabled: true, authenticated: true })} />;
  }

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Header
        style={{
          display: "flex",
          alignItems: "center",
          gap: 24,
          height: 64,
          paddingInline: 24,
          background: token.colorBgContainer,
          borderBottom: `1px solid ${token.colorBorderSecondary}`,
        }}
      >
        <div
          style={{
            color: token.colorText,
            fontWeight: 600,
            fontSize: 16,
            whiteSpace: "nowrap",
          }}
        >
          Anime Renamer
        </div>
        <Menu
          theme={resolved === "dark" ? "dark" : "light"}
          mode="horizontal"
          selectedKeys={[page]}
          onClick={(info) => setPage(info.key)}
          style={{ flex: 1, background: "transparent", minWidth: 320, borderBottom: "none" }}
          items={[
            { key: "scan", icon: <FolderOpenOutlined />, label: "扫描" },
            { key: "match", icon: <LinkOutlined />, label: "匹配作品" },
            { key: "templates", icon: <EditOutlined />, label: "模板与预览" },
            { key: "settings", icon: <SettingOutlined />, label: "设置" },
          ]}
        />
        <Segmented
          size="small"
          value={mode}
          onChange={(value) => setMode(value as "system" | "light" | "dark")}
          options={[
            { label: "跟随系统", value: "system" },
            { label: "浅色", value: "light" },
            { label: "深色", value: "dark" },
          ]}
        />
      </Header>
      <Content style={{ padding: 24, width: "100%", maxWidth: 1400, margin: "0 auto", boxSizing: "border-box" }}>
        {runStatus?.run && (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              flexWrap: "wrap",
              gap: 12,
              padding: "8px 12px",
              marginBottom: 12,
              background: token.colorFillQuaternary,
              border: `1px solid ${token.colorBorderSecondary}`,
              borderRadius: 8,
            }}
          >
            <Text strong>本次运行</Text>
            <Tag color="geekblue">{runStatus.run.stage_label}</Tag>
            <Text type="secondary" style={{ fontSize: 12 }}>
              上次停在这里，关掉页面再回来也能接着走
            </Text>
            <Button size="small" onClick={() => setPage(stagePage(runStatus.run?.stage))}>
              继续
            </Button>
            <Badge count={runStatus.pending.open} size="small" offset={[6, -2]}>
              <Button size="small" onClick={() => void openPending()}>
                待人工处理
              </Button>
            </Badge>
            {runStatus.locked && <Tag color="orange">正在推进</Tag>}
            {runStatus.pending.blocking > 0 && (
              <Text type="danger" style={{ fontSize: 12 }}>
                有 {runStatus.pending.blocking} 条会挡住下一步
              </Text>
            )}
          </div>
        )}
        {scan && queue && (
          <Space wrap size={8} style={{ marginBottom: 16 }}>
            <Text type="secondary">流程：</Text>
            {STEPS.map((step) => {
              const active =
                (step.key === "preview" && page === "templates") ||
                (step.key !== "preview" && page === step.key);
              return (
                <Tag key={step.key} color={active ? "blue" : undefined}>
                  {step.label} {queue.counts[step.key as keyof typeof queue.counts] ?? 0}
                </Tag>
              );
            })}
            <Text type="secondary" style={{ fontSize: 12 }}>
              条目在页面之间是「移动」不是复制，同一文件不会重复出现或被重复执行。
            </Text>
          </Space>
        )}
        {page === "scan" && (
          <ScanPage
            scan={scan}
            queue={queue}
            onScan={updateScan}
            onNext={() => setPage("match")}
            reloadQueue={() => (scan ? loadQueue(scan.scan_id) : Promise.resolve())}
            workflowPresets={workflowPresets}
            workflowPresetId={workflowPresetId}
            onWorkflowPresetChange={changeWorkflowPreset}
            namingPresets={namingPresets}
            namingPresetId={namingPresetId}
            onNamingPresetChange={changeNamingPreset}
            reloadWorkflowPresets={loadWorkflowPresets}
            settingsVersion={settingsVersion}
            onSettingsChanged={() => setSettingsVersion((value) => value + 1)}
          />
        )}
        {page === "match" && (
          <MatchPage
            scan={scan}
            queue={queue}
            onScanUpdate={(payload) => {
              setScan(payload);
              void refreshPending(payload.scan_id);
            }}
            reloadQueue={() => (scan ? loadQueue(scan.scan_id) : Promise.resolve())}
            onGoScan={() => setPage("scan")}
            onNext={() => setPage("templates")}
          />
        )}
        {page === "templates" && (
          <TemplatePage
            scan={scan}
            queue={queue}
            reloadQueue={() => (scan ? loadQueue(scan.scan_id) : Promise.resolve())}
            namingPresets={namingPresets}
            namingPresetId={namingPresetId}
            onNamingPresetChange={changeNamingPreset}
            reloadNamingPresets={loadNamingPresets}
            onPresetsImported={async () => {
              await loadWorkflowPresets();
            }}
            onGoScan={() => setPage("scan")}
            onGoMatch={() => setPage("match")}
            onGoSettings={() => setPage("settings")}
            onScan={updateScan}
            onSettingsChanged={() => setSettingsVersion((value) => value + 1)}
            settingsVersion={settingsVersion}
          />
        )}
        {page === "settings" && <SettingsPage />}
      </Content>
      <Drawer
        title={`待人工处理（${runStatus?.pending.open ?? 0}）`}
        open={pendingOpen}
        onClose={() => setPendingOpen(false)}
        width={480}
      >
        {pendingItems.length === 0 ? (
          <Text type="secondary">目前没有需要你处理的事项。</Text>
        ) : (
          <Space direction="vertical" style={{ width: "100%" }} size={8}>
            {pendingItems.map((item) => (
              <div
                key={item.id}
                style={{ border: `1px solid ${token.colorBorderSecondary}`, borderRadius: 6, padding: 10 }}
              >
                <Space wrap size={6}>
                  <Tag color={item.blocking ? "red" : "orange"}>{item.kind_label}</Tag>
                  <Tag color={item.blocking ? "red" : "default"}>
                    {item.blocking ? "会挡住下一步" : "不影响继续"}
                  </Tag>
                </Space>
                <div style={{ fontWeight: 600, marginTop: 4, wordBreak: "break-all" }}>
                  {item.title || item.ref_id}
                </div>
                <div style={{ color: token.colorTextSecondary, fontSize: 12, marginTop: 2 }}>{item.reason}</div>
                <div
                  style={{
                    marginTop: 6,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                  }}
                >
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {item.resolved_at
                      ? `${item.resolved_by || "已处理"} · ${item.resolved_at.slice(0, 19).replace("T", " ")}`
                      : "还没人处理"}
                  </Text>
                  <Button size="small" onClick={() => jumpToPending(item.kind)}>
                    去处理
                  </Button>
                </div>
              </div>
            ))}
          </Space>
        )}
      </Drawer>
    </Layout>
  );
}
