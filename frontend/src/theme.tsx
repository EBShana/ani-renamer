import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { ConfigProvider, theme as antdTheme } from "antd";
import zhCN from "antd/locale/zh_CN";

export type ThemeMode = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

const STORAGE_KEY = "ani.theme";

interface ThemeContextValue {
  mode: ThemeMode;
  resolved: ResolvedTheme;
  setMode: (mode: ThemeMode) => void;
}

const ThemeContext = createContext<ThemeContextValue>({
  mode: "system",
  resolved: "light",
  setMode: () => undefined,
});

function readMode(): ThemeMode {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === "light" || saved === "dark" || saved === "system") return saved;
  } catch {
    // 隐私模式等读不到 localStorage 时按跟随系统处理
  }
  return "system";
}

function systemDark(): boolean {
  return typeof window !== "undefined" && window.matchMedia
    ? window.matchMedia("(prefers-color-scheme: dark)").matches
    : false;
}

export function ThemeRoot({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<ThemeMode>(readMode);
  const [system, setSystem] = useState<boolean>(systemDark);
  const resolved: ResolvedTheme = mode === "system" ? (system ? "dark" : "light") : mode;

  useEffect(() => {
    if (!window.matchMedia) return;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = (event: MediaQueryListEvent) => setSystem(event.matches);
    setSystem(media.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = resolved;
    document.documentElement.style.colorScheme = resolved;
  }, [resolved]);

  const setMode = (next: ThemeMode) => {
    setModeState(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // 存不下也不影响本次使用
    }
  };

  const themeConfig = useMemo(
    () => ({
      algorithm: resolved === "dark" ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
      token: {
        borderRadius: 8,
        colorPrimary: resolved === "dark" ? "#ff8a2b" : "#1677ff",
        colorInfo: resolved === "dark" ? "#ff8a2b" : "#1677ff",
        ...(resolved === "dark"
          ? {
              colorBgBase: "#16171b",
              colorBgLayout: "#0e0f11",
              colorBgContainer: "#1c1d21",
              colorBgElevated: "#232429",
              colorBorder: "#3a3c43",
              colorBorderSecondary: "#2a2c31",
              colorText: "#e8e8ea",
              colorTextSecondary: "#a9abb1",
              colorTextTertiary: "#85878f",
              colorSplit: "#2a2c31",
            }
          : {
              colorBgLayout: "#f5f6f8",
            }),
      },
    }),
    [resolved],
  );

  // 让 message / Modal.confirm 这类静态弹层也跟随当前主题
  useEffect(() => {
    ConfigProvider.config({
      holderRender: (children) => (
        <ConfigProvider locale={zhCN} theme={themeConfig}>
          {children}
        </ConfigProvider>
      ),
    });
  }, [themeConfig]);

  return (
    <ThemeContext.Provider value={{ mode, resolved, setMode }}>
      <ConfigProvider locale={zhCN} theme={themeConfig}>
        {children}
      </ConfigProvider>
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  return useContext(ThemeContext);
}
