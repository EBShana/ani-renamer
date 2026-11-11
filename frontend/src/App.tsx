import { useEffect, useState } from "react";
import { Layout, Menu, Modal, Space, Tag, Typography, message } from "antd";
import { EditOutlined, FolderOpenOutlined, LinkOutlined, SettingOutlined } from "@ant-design/icons";
import { api } from "./api";
import type { Preset, QueueView, ScanPayload } from "./types";
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
  const [page, setPage] = useState("scan");
  const [scan, setScan] = useState<ScanPayload | null>(null);
  const [queue, setQueue] = useState<QueueView | null>(null);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [presetId, setPresetId] = useState("");
  const [auth, setAuth] = useState<{ enabled: boolean; authenticated: boolean } | null>(null);
  const ready = auth === null || !auth.enabled || auth.authenticated;

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

  const updateScan = (payload: ScanPayload | null) => {
    setScan(payload);
    if (payload) void loadQueue(payload.scan_id);
    else setQueue(null);
  };

  useEffect(() => {
    if (scan?.scan_id) void loadQueue(scan.scan_id);
  }, [scan?.scan_id]);

  const loadPresets = async (selectId?: string) => {
    try {
      const response = await api.presets();
      setPresets(response.presets);
      setPresetId((current) => {
        if (selectId) return selectId;
        if (response.presets.some((item) => item.id === current)) return current;
        const fallback = response.presets.find((item) => item.is_default) ?? response.presets[0];
        return fallback ? fallback.id : "";
      });
      return response.presets;
    } catch (error) {
      message.error(`读取预设失败：${(error as Error).message}`);
      return [];
    }
  };

  useEffect(() => {
    if (ready) loadPresets();
  }, [ready]);

  const changePreset = async (id: string) => {
    if (id === presetId) return;
    try {
      const diff = await api.presetSettingsDiff(id);
      if (!diff.changes.length) {
        setPresetId(id);
        return;
      }
      const lines = diff.changes.map(
        (change) => `${change.label}：${String(change.current ?? "（空）")} → ${String(change.next ?? "（空）")}`,
      );
      Modal.confirm({
        title: `切换预设「${diff.preset_name}」`,
        content: (
          <div>
            <div>这个预设保存的工作流设置与当前设置不同：</div>
            <div
              style={{
                marginTop: 8,
                maxHeight: 260,
                overflow: "auto",
                whiteSpace: "pre-wrap",
                color: "#888",
              }}
            >
              {lines.join("\n")}
            </div>
            <div style={{ marginTop: 8 }}>
              「应用设置并切换」会用预设里的设置覆盖当前设置；「只切换预设」保留当前设置。
            </div>
          </div>
        ),
        okText: "应用设置并切换",
        cancelText: "只切换预设",
        onOk: async () => {
          await api.applyPresetSettings(id);
          setPresetId(id);
          message.success("已应用预设的工作流设置");
        },
        onCancel: () => setPresetId(id),
      });
    } catch (error) {
      message.error(`读取预设设置失败：${(error as Error).message}`);
      setPresetId(id);
    }
  };

  if (!ready) {
    return <LoginPage onLoggedIn={() => setAuth({ enabled: true, authenticated: true })} />;
  }

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Header style={{ display: "flex", alignItems: "center", gap: 24, background: "#101828" }}>
        <div style={{ color: "#fff", fontWeight: 600, fontSize: 16, whiteSpace: "nowrap" }}>
          Anime Renamer
        </div>
        <Menu
          theme="dark"
          mode="horizontal"
          selectedKeys={[page]}
          onClick={(info) => setPage(info.key)}
          style={{ flex: 1, background: "transparent", minWidth: 320 }}
          items={[
            { key: "scan", icon: <FolderOpenOutlined />, label: "扫描" },
            { key: "match", icon: <LinkOutlined />, label: "匹配作品" },
            { key: "templates", icon: <EditOutlined />, label: "模板与预览" },
            { key: "settings", icon: <SettingOutlined />, label: "设置" },
          ]}
        />
      </Header>
      <Content style={{ padding: 24, width: "100%", maxWidth: 1400, margin: "0 auto", boxSizing: "border-box" }}>
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
            presets={presets}
            presetId={presetId}
            onPresetChange={changePreset}
          />
        )}
        {page === "match" && (
          <MatchPage
            scan={scan}
            queue={queue}
            onScanUpdate={setScan}
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
            presets={presets}
            presetId={presetId}
            onPresetChange={changePreset}
            reloadPresets={loadPresets}
            onGoScan={() => setPage("scan")}
            onScan={updateScan}
          />
        )}
        {page === "settings" && <SettingsPage />}
      </Content>
    </Layout>
  );
}
