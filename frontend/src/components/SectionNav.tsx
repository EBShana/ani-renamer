import { useEffect, useState, type ReactNode } from "react";
import { Button, Typography, theme } from "antd";

const { Text } = Typography;

export interface SectionItem {
  id: string;
  label: string;
}

export interface PrimaryAction {
  label: string;
  hint: string;
  disabled?: boolean;
  loading?: boolean;
  onClick: () => void;
}

interface Props {
  items: SectionItem[];
  primaryAction?: PrimaryAction;
}

/** 页面左侧的固定窄栏：顶部是本页主按钮，下面只在本页内跳板块。 */
export function SectionNav({ items, primaryAction }: Props) {
  const { token } = theme.useToken();
  const [active, setActive] = useState(items[0]?.id ?? "");
  const signature = items.map((item) => item.id).join(",");

  useEffect(() => {
    const pick = () => {
      let current = items[0]?.id ?? "";
      for (const item of items) {
        const node = document.getElementById(item.id);
        if (!node) continue;
        if (node.getBoundingClientRect().top <= 160) current = item.id;
      }
      setActive(current);
    };
    pick();
    window.addEventListener("scroll", pick, { passive: true });
    window.addEventListener("resize", pick);
    return () => {
      window.removeEventListener("scroll", pick);
      window.removeEventListener("resize", pick);
    };
  }, [signature]);

  if (items.length <= 1 && !primaryAction) return null;

  const jump = (id: string) => {
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <div
      style={{
        position: "sticky",
        top: 16,
        flex: "0 0 auto",
        width: 104,
        padding: 8,
        borderRadius: 8,
        background: token.colorBgContainer,
        border: `1px solid ${token.colorBorderSecondary}`,
        maxHeight: "calc(100vh - 96px)",
        overflow: "auto",
        boxSizing: "border-box",
      }}
    >
      {primaryAction && (
        <div style={{ marginBottom: 10 }}>
          <Button
            type="primary"
            block
            size="large"
            loading={primaryAction.loading}
            disabled={primaryAction.disabled}
            onClick={primaryAction.onClick}
            style={{
              height: 46,
              paddingInline: 4,
              fontWeight: 600,
              fontSize: 16,
              whiteSpace: "normal",
              lineHeight: 1.2,
            }}
          >
            {primaryAction.label}
          </Button>
          <Text
            type="secondary"
            style={{
              display: "block",
              marginTop: 6,
              fontSize: 12,
              textAlign: "center",
              whiteSpace: "nowrap",
            }}
          >
            {primaryAction.hint}
          </Text>
        </div>
      )}
      {items.length > 1 &&
        items.map((item) => {
          const current = item.id === active;
          return (
            <div
              key={item.id}
              onClick={() => jump(item.id)}
              style={{
                padding: "5px 6px",
                marginBottom: 2,
                borderRadius: 6,
                cursor: "pointer",
                fontSize: 13,
                lineHeight: "18px",
                color: current ? token.colorPrimary : token.colorTextSecondary,
                background: current ? token.colorPrimaryBg : "transparent",
                fontWeight: current ? 600 : 400,
                userSelect: "none",
                wordBreak: "break-word",
                overflowWrap: "anywhere",
              }}
            >
              {item.label}
            </div>
          );
        })}
    </div>
  );
}

/** 把页面内容和左侧固定窄栏摆在一起；没有板块也没有主按钮时整栏不显示。 */
export default function PageSections({
  items,
  primaryAction,
  children,
}: {
  items: SectionItem[];
  primaryAction?: PrimaryAction;
  children: ReactNode;
}) {
  return (
    <div style={{ display: "flex", gap: 16, alignItems: "flex-start", width: "100%" }}>
      <SectionNav items={items} primaryAction={primaryAction} />
      <div style={{ flex: 1, minWidth: 0 }}>{children}</div>
    </div>
  );
}
