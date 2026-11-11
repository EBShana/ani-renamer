import { useState } from "react";
import { Alert, Button, Card, Input, Typography } from "antd";
import { LockOutlined } from "@ant-design/icons";
import { api } from "../api";

const { Text, Title } = Typography;

interface Props {
  onLoggedIn: () => void;
}

export default function LoginPage({ onLoggedIn }: Props) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async () => {
    if (!password.trim()) {
      setError("请输入访问密码");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api.authLogin(password);
      onLoggedIn();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "#101828",
      }}
    >
      <Card style={{ width: 360 }}>
        <Title level={4} style={{ marginBottom: 4 }}>
          Anime Renamer
        </Title>
        <Text type="secondary">已开启访问密码，请输入密码后进入。</Text>
        <Input.Password
          prefix={<LockOutlined />}
          placeholder="访问密码"
          value={password}
          autoFocus
          onChange={(event) => setPassword(event.target.value)}
          onPressEnter={submit}
          style={{ marginTop: 16 }}
        />
        {error && <Alert type="error" showIcon message={error} style={{ marginTop: 12 }} />}
        <Button
          type="primary"
          block
          loading={busy}
          onClick={submit}
          style={{ marginTop: 16 }}
        >
          登录
        </Button>
        <Text type="secondary" style={{ display: "block", marginTop: 16, fontSize: 12 }}>
          忘记密码：在配置文件 settings.json 里删除 auth 字段后重启容器即可关闭密码。
        </Text>
      </Card>
    </div>
  );
}
