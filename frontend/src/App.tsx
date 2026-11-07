import { useEffect, useState } from "react";
import { Layout, Menu, Modal, message } from "antd";
import { EditOutlined, FolderOpenOutlined, LinkOutlined, SettingOutlined } from "@ant-design/icons";
import { api } from "./api";
import type { Preset, ScanPayload } from "./types";
import ScanPage from "./pages/ScanPage";
import MatchPage from "./pages/MatchPage";
import TemplatePage from "./pages/TemplatePage";
import SettingsPage from "./pages/SettingsPage";

const { Header, Content } = Layout;

export default function App() {
  const [page, setPage] = useState("scan");
  const [scan, setScan] = useState<ScanPayload | null>(null);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [presetId, setPresetId] = useState("");

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
    loadPresets();
  }, []);

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
        {page === "scan" && (
          <ScanPage
            scan={scan}
            onScan={setScan}
            onNext={() => setPage("match")}
            presets={presets}
            presetId={presetId}
            onPresetChange={changePreset}
          />
        )}
        {page === "match" && (
          <MatchPage
            scan={scan}
            onScanUpdate={setScan}
            onGoScan={() => setPage("scan")}
            onNext={() => setPage("templates")}
          />
        )}
        {page === "templates" && (
          <TemplatePage
            scan={scan}
            presets={presets}
            presetId={presetId}
            onPresetChange={changePreset}
            reloadPresets={loadPresets}
            onGoScan={() => setPage("scan")}
          />
        )}
        {page === "settings" && <SettingsPage />}
      </Content>
    </Layout>
  );
}
