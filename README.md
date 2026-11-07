# Anime Renamer

Docker + 网页界面的动画批量重命名工具：扫描媒体目录 → 自动匹配 TMDB → 预览 → 一键改名/整理成
Jellyfin 媒体库结构，所有操作可撤销。

## 快速开始

```bash
mkdir -p config media
cp config/settings.example.json config/settings.json   # 填入 TMDB API Key
docker compose up -d --build
```

浏览器打开 <http://localhost:8080>，然后在「设置」页填写 TMDB API Key 也可以。

目录说明：
- `media/`：放动画的目录（容器内为 `/media`）
- `config/`：设置、预设、识别规则与执行历史（容器内为 `/config`）

## 使用流程

1. **扫描**：选择媒体目录，点「开始扫描」。
2. **匹配作品**：点「一键自动匹配 TMDB」，唯一确定的作品自动套用，不确定的从下拉框选择。
3. **模板与预览**：编辑三种命名规则（正片 / 特典 / 剧场版），右侧预览会实时更新。
   字幕、特典目录、冲突处理等工作流设置统一在「设置」页修改，对所有预设生效。
4. **执行改名**：确认后执行；执行记录在下方，可一键撤销。

## 配置文件

| 文件 | 内容 |
|---|---|
| `config/settings.json` | 全局设置（解析、命名、TMDB、执行策略） |
| `config/presets.json` | 命名预设（三种命名规则 + 一份设置快照） |
| `config/rules.json` | 文件名识别规则 |
| `config/app.db` | 执行历史 / 撤销记录 |

配置文件可手工编辑，改完重启容器生效。

## 开发

见 [AGENTS.md](AGENTS.md)，里面有完整的架构、数据流、接口与开发约定说明。
