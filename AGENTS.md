# Anime Renamer 项目说明（面向 AI 接手开发）

> 本文是这个项目的“交接说明书”。目标：任何 AI 或开发者读完本文，就能理解整体架构、数据流、接口、配置和关键约定，并直接开始改代码。
> 语言：代码注释、界面文案均为中文；用户没有编程经验，沟通要简单直白。

---

## 1. 项目是什么

一个跑在 Docker 里、带 Web 图形界面的动画批量重命名工具。核心工作流：

```
选择媒体目录 → 扫描并解析目录/文件名 → 自动匹配 TMDB 作品 → 预览新名称/新路径
→ 真正执行改名与移动 → 执行记录可一键撤销
```

设计目标：
- 不靠文件名“逐文件瞎猜”，而是“目录层 + 文件层”两级解析，一个作品目录下的文件整体一致；
- 预览和执行共用同一套计算逻辑，所见即所得；
- 所有改动可撤销（SQLite 记录操作明细）；
- 整套工作流（命名模板 + 解析/执行选项）可保存为预设，一键复用；
- 面向 Jellyfin / Emby 的媒体库目录结构（`作品 (年份)/Season xx/...`）。

当前状态：M1（扫描/识别/预览）、M2（TMDB 匹配）、M3（执行/撤销/历史）、
M4（特典目录与杂项随作品移动、一键自动匹配）、M5（空目录清理、配置文件拆分）均已完成并通过测试。
v3 改造第 1 步（设置页唯一权威、去掉目录结构模板、旧配置迁移）与第 2 步
（目录组、自动目录/Season 规范、边界校验、执行续跑）已完成；
第 3 步（作品识别第一步、未归属区、记住的指派规则）也已完成；
另外已加入 TMDB 代理与连通性测试。界面交互仍是初版，后续会随 v3 后续步骤一起调整。

---

## 2. 技术栈

| 层 | 选型 |
|---|---|
| 后端 | Python 3.12 + FastAPI + Pydantic v2 + Uvicorn |
| 数据库 | SQLite（**只存执行历史**，配置在 JSON 文件里） |
| 前端 | React 18 + TypeScript + Vite 5 + Ant Design 5 |
| 部署 | Docker / docker-compose，单容器，端口 8080 |
| 外部服务 | TMDB API（搜索/详情），Bangumi（预留，仅字段与配置占位） |

后端依赖极少：`fastapi`、`uvicorn[standard]`、`pydantic`（见 `backend/requirements.txt`），HTTP 请求用标准库 `urllib`。

---

## 3. 快速运行

### 3.1 Docker（推荐）

```bash
# 项目根目录
mkdir -p config media
docker compose up -d --build
# 打开 http://localhost:8080
```

然后到「设置」页填入 TMDB API Key（或在 `config/settings.json` 里填 `providers.tmdb_api_key`）。
`docker-compose.yml` 把 `./media` 挂到容器 `/media`、`./config` 挂到 `/config`。

### 3.2 本地开发

```bash
# 后端（端口 8099，前端 vite 已代理到它）
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt        # Windows
.venv/Scripts/python -m uvicorn app.main:app --reload --port 8099

# 前端
cd frontend
npm install
npm run dev            # http://localhost:5173
```

本地未设置 `APP_CONFIG_DIR` 时，配置默认落在 `backend/data/`（已被 .gitignore 排除）。

### 3.3 构建与验证命令

```bash
# 前端类型检查 + 构建（产物输出到 backend/app/static）
cd frontend
npm run typecheck
npm run build

# 重建容器
docker compose up -d --build

# 健康检查
curl http://localhost:8080/api/health
```

**改完前端必须 `npm run build`**，因为后端直接托管 `backend/app/static`；Docker 镜像构建时也会执行前端构建。

---

## 4. 目录结构

```
anime-renamer/
├── Dockerfile                  # 两阶段构建：node 构建前端 → python 运行镜像
├── docker-compose.yml          # 8080 端口，./media:/media，./config:/config，PUID/PGID
├── .dockerignore / .gitignore
├── AGENTS.md                   # 本说明
├── README.md                   # 简要上手说明
├── config/                     # 运行期配置（git 忽略；首次启动自动生成）
│   ├── settings.json           # 全局设置
│   ├── presets.json            # 命名预设（只保存三种文件名规则 + 设置快照）
│   ├── groups.json             # 目录组（多组「源目录 + 目标目录」）
│   ├── assignments.json        # 记住的指派规则（目录组 + 名称段 → 作品名）
│   ├── rules.json              # 文件名识别规则
│   └── app.db                  # SQLite：执行历史 / 撤销记录
├── media/                      # 宿主机媒体目录（示例/测试用，不入包）
├── backend/
│   ├── requirements.txt
│   ├── entrypoint.sh           # 处理 PUID/PGID 后以 appuser 运行
│   ├── data/                   # 本地开发默认配置目录（git 忽略）
│   └── app/
│       ├── main.py             # FastAPI 入口：注册路由、托管 SPA
│       ├── config.py           # 环境变量、设置项定义（单一来源）、预设选项映射
│       ├── store.py            # presets.json / groups.json / rules.json 读写；SQLite 执行历史
│       ├── scanner_helpers.py  # is_under 等路径小工具
│       ├── api/
│       │   ├── routes_meta.py     # /api/health /fields /config /rules /browse
│       │   ├── routes_groups.py   # /api/groups 目录组 CRUD 与边界校验
│       │   ├── routes_scan.py     # /api/scans /samples
│       │   ├── routes_identify.py # /api/identify 未归属区、指派、记住的规则
│       │   ├── routes_presets.py  # 预设 CRUD、导入导出、模板校验、/preview
│       │   ├── routes_match.py    # 分组、TMDB 搜索、套用、一键自动匹配
│       │   └── routes_execute.py  # 执行、历史、撤销、断点续跑
│       └── core/
│           ├── groups.py       # 目录组规范化与“组间不重叠”校验
│           ├── scanner.py      # 目录遍历、忽略规则、标记文件、按目录组生成扫描会话
│           ├── identify.py     # 作品识别（文件名优先 + 目录回退 + 疑似作品 + 未归属）
│           ├── parser.py       # 目录层 + 文件层两级解析 → 模板字段
│           ├── techname.py     # 技术标签（分辨率/编码/字幕组/年份）提取
│           ├── rules.py        # 内置识别规则（正则）与中文数字转换
│           ├── filetypes.py    # 扩展名 → 类别（video/subtitle/audio/image/archive/other）
│           ├── fields.py       # 模板变量注册表（前端变量面板由它驱动）
│           ├── template.py     # 模板渲染、空值折叠、非法字符净化、校验
│           ├── scan_store.py   # 扫描会话内存存储（扫描结果 + 字段回写）
│           ├── preview.py      # 预览构建（预览与执行共用的唯一计算入口）
│           ├── executor.py     # 真实移动/改名、冲突策略、空目录清理
│           ├── operations.py   # 后台执行任务：分批、进度、暂停/取消、续跑
│           ├── matcher.py      # 作品分组、候选打分、auto_pick 自动判定
│           └── metadata.py     # TMDB 搜索/详情、Bangumi 占位
└── frontend/
    ├── index.html / vite.config.ts / tsconfig.json / package.json
    └── src/
        ├── main.tsx / App.tsx / styles.css
        ├── api.ts              # 所有后端请求封装
        ├── types.ts            # 与后端 JSON 对应的类型定义
        └── pages/
            ├── ScanPage.tsx      # 扫描：目录组管理、扫描结果表、预设选择
            ├── MatchPage.tsx     # 匹配：作品列表、自动匹配、未归属区
            ├── UnassignedPanel.tsx # 未归属区：疑似作品 / 单文件 / 待确认 / 记住的规则
            ├── TemplatePage.tsx  # 模板与预览：模板编辑、选项、预览、执行、历史
            └── SettingsPage.tsx  # 设置：全局配置 + 识别规则查看
```

---

## 5. 核心数据流

```
POST /api/scans  {group_ids: [...]}
   store.list_groups() 取目录组（源目录 + 目标目录）
   scanner.scan_groups()
     → 逐组遍历源目录（忽略 .git 等、特典类目录、已带忽略标记的目录），最多 20000 个文件
     → parser.parse_file / parse_asset 解析每条记录，并写入 group_id、组内 rel_path
     → 结果里保存 groups 快照（之后改目录组不影响这次扫描）
     → scan_store.put_scan() 把整个扫描结果放进内存
   ← {scan_id, root, groups[], sources, files[], stats, ignored_dirs, markers_written}

扫描完成后立即识别（core/identify.py）
   apply_identification(scan, assignments, settings)
     → 文件名优先提取作品名；不足时逐层往上收集文件夹名（跳过 Season/SPs/Anime 等收纳目录）
     → 只有“唯一确定”（文件名 + 目录互相印证，或命中记住的规则）才生成 work_id 自动归属
     → 其余进入「未归属区」：疑似作品 / 单个未归属文件 / 已指派待确认
   → 每个视频行写入 identify{work_id,title,year,confidence,source,segment,needs_confirm}
   → 未归属（work_id 为空）的文件在预览 / 执行里直接拒绝

POST /api/identify/assign
   用户把一批文件指派给已有作品或新建作品；可选“记住这条规则”
   → 写入 assignments.json（目录组 + 名称段 → 作品名），下次扫描直接复用
   → 同步写回 scan 行的 fields.series / identify.work_id

POST /api/match/auto（或手动 search + apply）
   matcher.build_groups() 按识别结果里的 work_id 聚合（同一作品可跨多个文件夹，未归属的不进作品列表）
   metadata.search_tmdb() 每个组搜候选 → matcher.score_candidate() 打分 → auto_pick() 判定
   自动组：metadata.fetch_tv/fetch_movie 详情 → build_updates() → scan_store.apply_field_updates() 回写
   ← {results[], groups[], summary{auto,choose,missing}}

POST /api/preview
   preview.build_preview(scan, preset, settings)
     → 按目录组 + 自动目录规范算目标路径（target/作品 (年份)/Season xx/文件名）
     → 勾选“只重命名文件”时改为 target/源相对目录/新文件名
     → 为被忽略目录、压缩包等“杂项”生成移动行（category=folder/archive…）
   ← {rows[]（含 group_id/action/status/messages）, summary, groups[]}

POST /api/execute
   preview.build_preview（保证与预览一致，但使用请求里的最新模板）
   先 create_pending_operation() 落库（每行 planned），再 executor.execute_rows()
   每处理一行就 update_operation_item()，按冲突策略处理
   结束后按目录组清理已清空的源目录树（keep_empty_dirs=False 时）
   → finalize_operation() 把状态改为 done / partial
   ← {counts, operation_id, rows[]}

POST /api/history/{id}/resume
   只重试 status=planned/failed 的明细；已成功的不会重复移动

POST /api/history/{id}/undo
   store.get_operation() → 用 summary 里的 groups 快照 → executor.undo_items() 逆序移回原路径
   → mark_operation_status(id, "undone")
```

关键点：**扫描结果只存在内存里**（进程重启即失效，需要重新扫描）；**预览与执行共用 `preview.build_preview`**，不要在别处另写一套命名逻辑。**每个动作都必须带 group_id**，源路径属于该组源目录、目标路径属于该组目标目录，禁止跨组。

---

## 6. 后端关键设计（改代码前必须知道）

### 6.1 配置：JSON 文件 + SQLite 分工

- `config/settings.json`：全局设置，按分组 `parse / naming / providers / execution / scraper`。
  所有设置项在 `backend/app/config.py` 的 `SETTING_SPECS` 中定义（**单一来源**），
  默认值、类型、界面标签、说明都从这里出，`/api/config/schema` 供前端动态渲染设置页。
- `config/presets.json`：预设列表。每个预设含 `templates`（episode/special/movie 三种命名规则）与
  `options`（保存预设时记录的一份“当前设置快照”，仅用于以后切换预设时恢复，**不再在运行期覆盖设置**）。
  文件结构（v3 起 `templates` 只保存文件名规则，目录由系统自动生成）：
  ```json
  { "version": 3, "default_id": "preset-simple",
    "presets": [ { "id": "...", "name": "...", "is_default": true,
                   "library_profile": "jellyfin",
                   "templates": { "episode": {"pattern": "...", "output_mode": "filename"} },
                   "options": { ... } } ] }
  ```
- `config/groups.json`：目录组列表，结构
  `{"version": 1, "groups": [{"id","name","source","target","created_at","updated_at"}]}`。
  `source` / `target` 都是媒体根目录内的绝对路径，`target` 可以等于 `source`。
  **组间禁止嵌套/重叠**（同一组内的源和目标允许相同或嵌套）；打开组编辑、启动扫描前都会校验。
  扫描时会把这几个组拷一份快照写进扫描结果，之后增删改组不影响已有扫描和已有执行记录。
- `config/rules.json`：文件名识别规则（内置规则首次启动自动写出，可手工增删）。
- `config/app.db`：只存 `operations` / `operation_items` / `meta`（历史、迁移标记）。
  `operation_items` 带 `group_id`，撤销时按组快照判断路径是否越界。
  旧版本库里的 `presets` / `rules` 表在启动时会自动迁移进 JSON，之后不再使用。

预设选项（`PRESET_OPTION_KEYS`）与全局设置的对应关系仍然保留，但用途已经改变：
从 v2 起，**运行期（扫描 / 匹配 / 预览 / 执行）只读 `load_settings()`**，预设不再覆盖设置。
预设选项只在两种情况下使用：
1. 保存预设时，把当前设置打包成快照写进 `options`；
2. 用户切换预设时，前端弹出差异确认，确认后通过 `POST /presets/{id}/apply-settings` 写回设置。

新增可配置项时：
1. 在 `SETTING_SPECS` 加一条；
2. 如果它属于“工作流、希望随预设快照记录”，再在 `PRESET_OPTION_KEYS` 加映射；
3. 前端 `types.ts` 的 `PresetOptions` 同步加字段（设置页会自动出现）。

### 6.2 模板引擎（`core/template.py`）

- 从 v2 起，**界面只编辑三种“文件名规则”**（正片 / 特典 / 剧场版），不再编辑目录结构模板。
- v3 起目录结构由 `core/preview.py` 统一生成，预设不再带 `legacy_dir`：
  - 正片：`目标目录/作品 (年份)/Season xx/文件名`
  - 特典：`目标目录/作品 (年份)/<naming.specials_dir>/文件名`（季号固定 0，文件名用 `S00Exx`）
  - 剧场版：默认 `目标目录/作品 (年份)/文件名`；`naming.movie_layout=special` 时进特典季目录
  - 勾选 `naming.preserve_structure`：改为 `目标目录/源目录相对结构/新文件名`，不动文件夹
- 升级到 v3 时，旧预设的目录部分（`legacy_dir`）会被丢弃；旧内置模板里
  `{episode:02}` / `{special_type}{special_no:02}` 的写法会自动替换为 `S{season:02}E{episode:02}`。
  完全自定义的模板保持原样。
- `pattern` 里用 `{key}` 或 `{key:02}` 占位；`build_filename()` 只渲染文件名并补扩展名、净化非法字符。
- 空值折叠：某变量为空时，自动去掉多余的 ` - `、括号、连续分隔符，避免出现 ` -  - `。
- `sanitize_component()` 清理非法字符，按 `max_filename_bytes` 截断（默认 240）。
- `SPECIALS_SEGMENTS = {"specials", "season 00", "s00"}` 视作特典目录层级。
- 修改渲染规则时务必同步 `preview` 与前端预览。

### 6.3 预览与执行（`core/preview.py` + `core/executor.py`）

- `build_preview()` 接收 `(scan, preset, settings, templates_override?, file_ids?, limit?, subtitle_mode?)`，
  返回带 `group_id` 的 `PreviewRow` 列表：
  - 用扫描里保存的 `groups` 快照决定每个文件的源组和目标组；
  - 视频按 kind 选对应模板（episode/special/movie），目录按上面 6.2 的规则生成；
  - 字幕默认“跟随视频”（`subtitle_mode=follow`），文件名与视频一致；
  - 特典类目录（SPs/CDs/Scans…）与压缩包等杂项，根据 `asset_policy=move` 生成目录/文件移动行，
    目标是 `_show_root()` 找到的作品根目录（跳过 Season xx / Specials 层）；
  - 同一个作品的多份来源会自然合并到同一个作品文件夹（这就是“合并”的基础）；
  - 多个文件算出同一个目标路径时会标记为冲突，不会静默覆盖。
- `executor.execute_rows()`：
  - 每个动作都校验 `source` 在该组源目录内、`target` 在该组目标目录内，**禁止跨组**；
  - 目标已存在时按 `conflict_policy` 处理：`skip` / `overwrite`（目录不能覆盖）/ `suffix`（自动加 ` (2)`）；
  - 自动创建父目录；跨盘由 `shutil.move` 处理；
  - `keep_empty_dirs=False`（默认）时，执行完调用 `remove_empty_parents()` 与
    `prune_empty_tree()` 清理已清空的源目录树，**只删除真正为空的目录**（`any(iterdir())` 判断 + `rmdir` 兜底），
    绝不删除文件；扫描根目录永不删除。
  - 返回每行 `status: done/skipped/failed` 与说明文字，供历史记录与撤销使用。
- 后台执行：`routes_execute.execute` 先把整份计划和全部明细写入 `operations` / `operation_items`
  （状态 `queued`），交给 `core/operations.py` 的后台线程按批（100 条）执行；
  每处理一条调用 `update_operation_item()` 立即落库。
  结束状态：有冲突 → `conflict`，有失败 → `partial`，否则 `done`；
  容器重启时启动钩子把未完成状态改成 `interrupted`。
- 断点续跑：`POST /history/{id}/resume` 先核对目录组快照（id / 源 / 目标），
  组缺失或改动直接拒绝；只把未完成（planned / failed / conflict）的条目重置后重试，
  已完成 / 已跳过的编号锁定，不会重复移动。
- 暂停 / 取消：暂停在当前文件处理完后生效，可继续；取消后任务不能再续跑，
  已完成的条目保留，可手动撤销。
- 撤销：`undo_items()` 按 `seq` 逆序把目标移回原路径，用执行记录里的 groups 快照校验边界；
  撤销成功后旧记录标记 `undone`。

### 6.4 作品分组与 TMDB 匹配（`core/matcher.py`、`core/metadata.py`）

- `build_groups()`：按“相对路径第一段（作品组目录）+ 标题 + 年份”聚合；
  `group_id` 形如 `normalize_title(title)|year`。
- `score_candidate()`：标题完全相同 +4、包含 +2；年份一致 +2、未知 +1、不符 -1；类型一致 +1。
- `auto_pick()` 自动套用条件（满足其一）：
  1. 最高分 ≥6、分数唯一、且（标题完全一致 或（包含且年份一致））；
  2. **搜索结果只有 1 个且类型一致**（避免“只有一个选项还让人手动选”）。
  否则 `choose`（列出最多 6 个候选）；无候选 `missing`（需手动搜索/Bangumi 后续接入）。
- `match_auto` 对“搜索”和“拉详情”各做一次失败重试（应对 TMDB 限流/网络抖动），
  失败记录会带 `message`，前端显示在状态下方的灰色小字里。
- 详情拉取：剧集逐季取 `season/{n}` 并写入 `ep_title`/`air_date`；
  `build_updates()` 把元数据回写到组内文件字段（`fields`），预览直接使用。

### 6.5 路径安全

所有落盘操作都以 `MEDIA_ROOT` 为边界（`scanner_helpers.is_under` / `preview` 的 `is_within` 检查），
不允许把文件移到媒体根目录之外；扫描路径必须位于 `/media` 内。

### 6.6 扫描会话

`core/scan_store.py` 是进程内字典：`put_scan / get_scan / apply_field_updates`。
执行接口要求传真实 `scan_id`（示例扫描 `is_sample=true` 不能执行）。
新增“批量修改字段”功能时，走 `apply_field_updates`，不要在前端拼装整份扫描结果。

### 6.7 外部网络与代理（`core/metadata.py`）

- 设置项 `providers.proxy_url`：填写 `http://` 或 `https://` 代理地址（可带用户名密码），留空表示直连。
- 所有 TMDB / Bangumi 请求都经过 `_build_opener()`；空代理也会显式注册 `ProxyHandler({})` 保证直连，
  不受系统环境变量影响。
- `POST /api/providers/test` 做连通性测试，返回“连得通 / 连不通 + 原因”；代理密码不会出现在日志或响应里。
- Docker 里访问 Windows 宿主机上的代理必须用 `host.docker.internal`，不能填 `127.0.0.1`；
  `docker-compose.yml` 已加 `extra_hosts: host.docker.internal:host-gateway`。

### 6.8 作品识别（`core/identify.py` + `config/assignments.json`）

- 扫描结束后立刻识别，结果写进每个视频行的 `identify` 字段；**作品不再等于文件夹**：
  `work_id = w:<目录组>:<规范化标题>|<年份>`，同一作品的文件可以分布在多个文件夹（多季、特典外置），
  一个文件夹里也可以有多部作品（合集）。
- 文件名优先：从文件名去掉发布组、集号、画质编码、字幕语言、版本号、年份后取作品名候选；
  文件名不足时逐层往上收集文件夹名，跳过 `Season xx / SPs / CDs / Scans / Subs / Anime / TV / Movies` 等收纳目录。
- 置信度：
  - `high`：文件名候选与目录候选互相印证，或命中 `assignments.json` 里记住的规则 → 自动归属，标记为“待确认”；
  - `medium`：只有文件名信息（典型是平铺在下载目录里） → 不自动归属，多个文件共用同一段名称时进入「疑似作品」；
  - `low` / `none`：只有目录信息或信息不足 → 单个未归属文件。
- 未归属区三层：疑似作品（≥2 个文件共用名称段）、单个未归属文件、已指派待确认。
  **未归属的文件在预览和执行里直接拒绝**（预览行 `status=error / action=skip`）。
- 用户指派后可选“记住这条规则”，按「目录组 + 名称段 → 作品名」保存到 `config/assignments.json`，
  下次扫描直接复用；可修改、可删除。单文件指派优先于候选组归属。
- 预览目标路径以识别出的作品名为准；杂项文件优先跟随同一目录里的视频（合集场景不会串到别的作品）。

---

## 7. API 一览（前缀 `/api`）

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/health` | 健康检查（Docker HEALTHCHECK 使用） |
| GET | `/fields` | 模板变量注册表（分组 + 字段说明） |
| GET | `/config` | 当前全局设置值 |
| GET | `/config/schema` | 设置项元数据（类型/默认值/分组/说明） |
| PUT | `/config` | 保存全局设置（自动写入 settings.json） |
| GET | `/rules` | 识别规则列表（来自 rules.json） |
| GET | `/browse?path=` | 浏览媒体根目录下的文件夹（扫描页选目录用） |
| GET | `/groups` | 目录组列表 + 媒体根目录 |
| GET | `/groups/check` | 检查现有目录组是否合法（重叠 / 目录不存在） |
| POST | `/groups` | 新建目录组：`{name, source, target}`（target 留空=source） |
| PUT | `/groups/{id}` | 修改目录组（同样做边界校验） |
| DELETE | `/groups/{id}` | 删除目录组（已有扫描/执行保留组快照） |
| POST | `/scans` | 扫描：`{group_ids[]}` → 扫描会话（含 groups 快照） |
| GET | `/scans/{scan_id}` | 取回扫描会话 |
| GET | `/samples` | 内置示例扫描（无真实目录时体验界面） |
| POST | `/identify` | 读取未归属区：`{scan_id}` → 作品 / 疑似作品 / 单个未归属 / 待确认 |
| POST | `/identify/assign` | 指派：`{scan_id, file_ids[], work_id? 或 title/year, remember?, group_id?, segment?}` |
| POST | `/identify/ignore` | 忽略一组疑似作品：`{scan_id, group_id, segment}` |
| POST | `/identify/confirm` | 确认自动识别结果：`{scan_id, work_id}` |
| GET | `/identify/assignments` | 记住的指派规则列表 |
| POST | `/identify/assignments` | 新增记住的规则 |
| PUT / DELETE | `/identify/assignments/{id}` | 修改 / 删除记住的规则 |
| GET | `/presets` | 预设列表 |
| POST | `/presets` | 新建预设 |
| PUT | `/presets/{id}` | 更新预设（名称/模板/选项） |
| DELETE | `/presets/{id}` | 删除预设（默认预设会自动顺延） |
| POST | `/presets/{id}/duplicate` | 复制预设 |
| POST | `/presets/{id}/default` | 设为默认预设 |
| GET | `/presets/{id}/settings-diff` | 对比预设设置快照与当前设置（切换前确认） |
| POST | `/presets/{id}/apply-settings` | 把预设设置快照写入全局设置（用户确认后） |
| GET | `/presets/export` / POST `/presets/import` | 预设导出/导入 |
| POST | `/templates/validate` | 校验模板（错误/警告） |
| POST | `/preview` | 生成预览：`{scan_id, preset_id, templates?, subtitle_mode?}` |
| POST | `/match/groups` | 列出作品分组 |
| POST | `/match/search` | TMDB 搜索：`{query}` → 候选 |
| POST | `/match/apply` | 套用匹配：`{scan_id, group_id, tmdb_id, media_type}` |
| POST | `/match/auto` | 一键自动匹配：`{scan_id, limit?, include_matched?}` |
| POST | `/execute` | 启动后台执行任务：`{scan_id, preset_id, templates?, subtitle_mode?, conflict_policy?, keep_empty_dirs?}` → `{operation_id, total, batch_count}` |
| GET | `/history` | 执行历史（`?limit=20`），附带 `active_operation_id` |
| GET | `/history/{id}` | 单条执行明细 |
| GET | `/history/{id}/progress` | 进度：已完成/总数、当前文件、成功/跳过/失败/冲突、第几批/共几批 |
| POST | `/history/{id}/pause` | 暂停：当前文件处理完才停 |
| POST | `/history/{id}/continue` | 继续已暂停的任务 |
| POST | `/history/{id}/cancel` | 取消：已完成的不自动回滚，取消后不能再续跑 |
| POST | `/history/{id}/resume` | 断点续跑：校验目录组快照，只重试未完成条目 |
| POST | `/history/{id}/undo` | 撤销一条执行记录 |
| POST | `/providers/test` | 测试 TMDB / 代理连通性（返回连得通/连不通 + 原因） |

请求/响应结构与前端 `src/types.ts` 一一对应，改接口时两边同步修改。

---

## 8. 前端结构

- `App.tsx`：顶部菜单四个页面（扫描 / 匹配作品 / 模板与预览 / 设置），
  全局状态：`scan`、`presets`、`presetId`（跨页共享，扫描与执行用同一个预设）。
- `api.ts`：所有请求封装，统一抛错；新增接口先加这里。
- `ScanPage`：管理目录组（多组「源目录 + 目标目录」）、选择预设、扫描；
  结果表按类别着色，已跳过目录会标出属于哪个组；目录组改动只影响下一次扫描。
- `MatchPage`：顶部作品列表 + 匹配状态；「一键自动匹配 TMDB」→ 摘要（自动/待选/缺失）；
  待选行内联下拉候选，需手动行显示原因并可打开搜索。
  下方「未归属区」分四块：疑似作品（多选后指派/忽略）、单个未归属文件（多选指派）、
  已指派待确认（一键确认）、记住的规则（可删除）。
- `TemplatePage`：核心页。左侧模板编辑（变量面板可点插）+「目录结构」开关
  （只重命名文件 / 剧场版位置）；右侧预览；底部「保存预设 / 执行改名」与执行记录表
  （可撤销、可续跑）。预览请求 450ms 防抖，保存后会用服务端最新预设覆盖草稿。
- `SettingsPage`：按 `/config/schema` 动态渲染全局设置，底部只读展示识别规则。

样式统一在 `styles.css`（少量自定义 class：`preview-name`、`path-text` 等）。

---

## 9. 开发约定（务必遵守）

1. **不要另写命名逻辑**：预览与执行都必须经过 `preview.build_preview`；
   目录结构统一由 `preview` 自动生成，不要在前端或别处拼路径。
2. **执行必须可撤销**：任何落盘操作都要在 `create_pending_operation` / `save_operation` 里留明细
   （group_id、原路径、目标路径、状态）。
3. **删除目录只允许删空目录**：复用 `prune_empty_tree` / `remove_empty_parents`，禁止 `rmtree`。
4. **路径必须限制在该目录组内**：源路径属于组源目录、目标路径属于组目标目录，
   只允许“组内移动”，禁止跨组；落盘前用 `is_within` 校验。
5. **配置项单一来源**：设置键在 `config.SETTING_SPECS`；运行期只读 `load_settings()`，预设不再覆盖设置；
   如果要让某个设置随预设快照记录，再把它加进 `PRESET_OPTION_KEYS`。
   预设本身只保存三种命名规则（正片 / 特典 / 剧场版）。
6. **预设 / 目录组文件读写要原子化**：用 `store._write_json`（临时文件 + `os.replace`），不要直接 open 覆盖。
7. **TMDB 调用要容错**：外部请求失败不能中断整批；给出可读的 `message`，必要时重试；代理密码不要打日志。
8. **中文文案**：界面和提示全部中文，面向非程序员用户，措辞直白。
9. **不要提交配置和媒体**：`config/`、`backend/data/`、`media/` 已在 `.gitignore`。
10. **改前端必须重新 build**（产物在 `backend/app/static`），否则容器里看到的还是旧界面。
11. **扫描 / 执行都用组快照**：组增删改只影响下一次扫描，不要用“当前 groups.json”去解释旧扫描或旧执行。

---

## 10. 配置文件示例

`config/settings.json`：

```json
{
  "version": 3,
  "values": {
    "parse.default_season": 1,
    "parse.special_season_zero": true,
    "parse.generic_dir_names": "Anime,动画,番剧,TV,Movies,Media,media,Videos,Downloads,Download,特典,SPs,Specials,Scans,CDs",
    "parse.min_title_length": 2,
    "parse.scan_all_files": true,
    "parse.ignore_special_dirs": true,
    "parse.ova_policy": "move",
    "parse.special_dir_names": "SPs,Specials,特典,映像特典,CDs,Scans,Bonus,Extras,Menu,PV,CM,Preview,Trailer",
    "parse.ignore_marker_files": ".ignore,.nomedia",
    "parse.write_ignore_markers": true,
    "naming.subtitle_mode": "follow",
    "naming.auto_append_ext": true,
    "naming.max_filename_bytes": 240,
    "naming.specials_dir": "Season 00",
    "naming.forbidden_chars": "<>:\"/\\|?*",
    "naming.preserve_structure": false,
    "naming.movie_layout": "folder",
    "providers.proxy_url": "",
    "providers.tmdb_api_key": "",
    "providers.tmdb_lang": "zh-CN",
    "providers.bangumi_token": "",
    "execution.conflict_policy": "skip",
    "execution.keep_empty_dirs": false,
    "execution.asset_policy": "move",
    "scraper.enabled": false
  }
}
```

预设（`presets.json`，version 3）首次启动会自动生成三个：
「简洁命名」（SxxExx）、「保留技术信息」、「Jellyfin 媒体库目录」。
三种预设都只保存文件名规则，目录统一自动生成。

目录组（`groups.json`，version 1）默认是空的，用户在扫描页点「添加目录组」创建。
例如：

```json
{
  "version": 1,
  "groups": [
    {"id": "group-a1", "name": "动画库", "source": "/media/anime", "target": "/media/library"},
    {"id": "group-b2", "name": "剧场版", "source": "/media/movies", "target": "/media/movies"}
  ]
}
```

---

## 11. 已知限制与后续路线

- 扫描会话仅内存保存，重启容器需重新扫描（可考虑持久化）。
- Bangumi 仅占位（字段、Token 配置、候选类型），未实现搜索/详情。
- 刮削（生成 NFO/下载图片）仅预留开关 `scraper.enabled`。
- 特典（S0）目前只做移动，不按 TMDB S0 剧集精确命名（后续功能）。
- 已有单集标题字段 `ep_title`，模板可用但尚未在 UI 里突出展示。
- 界面交互为初版，用户明确希望最后再统一优化 UI。
- 跨盘移动较慢（`shutil.move` 会复制再删除），大批量时注意提示。

---

## 12. 新项目接手清单

1. 把本包内容放进新仓库根目录。
2. `mkdir -p config media`，把 `config/settings.example.json` 复制为 `config/settings.json`，
   填入 TMDB API Key（`providers.tmdb_api_key`）。
3. `docker compose up -d --build`，访问 http://localhost:8080。
4. 首次启动会自动生成 `presets.json`（三个内置预设）、`groups.json`（空列表）与 `rules.json`（15 条识别规则）。
5. 到「扫描」页先点「添加目录组」，填源目录和目标目录（目标是空的就填和源目录一样），
   再勾选目录组开始扫描；然后按「一键自动匹配 → 模板与预览 → 执行」走一遍。
   TMDB 连不上时，到「设置」页填 `providers.proxy_url`（容器里连宿主机代理用
   `http://host.docker.internal:端口`），并点「测试连通性」。
6. 改代码前先读本文第 6、9 节，尤其是“预览与执行共用”和“只删空目录”两条。

---

## 13. v3 定稿决策（后续开发以此为准）

### 13.1 已经确认的产品规则

1. **设置页是唯一权威**：扫描、匹配、预览、执行都只读设置页。
   预设只保存「正片 / 特典 / 剧场版」三种命名规则；保存预设时附带一份当前设置快照，
   切换预设时前端先显示差异，用户确认后才把快照写回设置。
2. **目录结构不再由用户编辑**：文件夹结构按规范自动生成（`作品 (年份)/Season xx/...`）。
   第 1 步先隐藏目录结构编辑入口；第 2 步已改为完全自动生成，旧预设的 `legacy_dir` 在升级时丢弃。
3. **统一 SxxExx 命名**：正片、特典使用 `SxxExx` 形式（特典固定 `S00`，放进特典季目录）；
   剧场版默认 `作品 (年份)`。季号未知时默认 `S01`，不做黄色提醒；
   等 TMDB 匹配到该作品确实有多季时再让用户选择。
4. **特典季目录可配置**，默认 `Season 00`（设置项 `naming.specials_dir`）。
5. **剧场版提供选项**：放在作品文件夹内，或按特典方式进入特殊季目录
   （设置项 `naming.movie_layout`，模板页也可切换）。
6. **目录结构处增加“只重命名文件”选项**：勾选后不改文件夹、不改变相对位置，
   源目录里的相对结构原样映射到目标目录，只改文件名（设置项 `naming.preserve_structure`）。
7. **工作目录改为多组「源目录 + 目标目录」**：组间禁止嵌套/重叠，禁止跨组移动；
   扫描结果保存组快照；执行记录也保存组快照，撤销时按快照校验。
8. **作品识别采用“文件名优先 + 不确定就问 + 未归属手动指派”**，分两步实现；
   未识别文件如果多个共用同一段名称，自动生成「疑似作品」候选组，供整组指派。
9. **预览按作品折叠**，可整部勾选，也可展开后部分勾选。
10. **执行引擎**：去掉 500 条人为上限、分批执行、每步立即落库、断点续跑、暂停/取消、进度轮询（第 4 步）。
11. **冲突延后统一处理**：执行前汇总冲突，提供合并等选项；覆盖需要二次确认（第 6 步）。
12. **重扫三档**：默认不重扫 + 轻量核对 + 完整重扫（第 7 步）。
13. 扫描上限仍为 20000 个文件。

### 13.2 第 1 步已经落地的内容（v2 配置版本）

- 运行期移除 `config.apply_preset_options()` 的所有调用；扫描 / 匹配 / 预览 / 执行只读 `load_settings()`。
- `SETTING_SPECS` 继续是配置单一来源；`naming.specials_dir` 改为可自由填写的文本，默认 `Season 00`。
- 新增 `GET /api/presets/{id}/settings-diff` 与 `POST /api/presets/{id}/apply-settings`：
  切换预设时先比较、确认后再覆盖设置。
- 保存 / 新建预设时，如果请求没有带 `options`，后端自动记录当前设置快照。
- 预设文件升到 `version: 2`；旧版“完整路径模板”自动拆成 `pattern + legacy_dir`，
  `core/preview.py::_compose_template()` 在预览/执行时拼回完整路径，旧行为保持不变。
- `config/settings.json` 升到 `version: 2`；首次启动时把“默认预设当时实际生效的选项”写进设置，
  保证升级后行为不突变。迁移只执行一次。
- 前端「模板与预览」页删除“工作流选项”和“仅文件名 / 完整路径”切换；
  旧预设若带 `legacy_dir` 会显示一条提示。设置页增加“这里是所有工作流设置的唯一来源”说明。

### 13.3 第 2 步已经落地的内容（v3 配置版本）

- 新增 `config/groups.json` 与 `/api/groups`：多组「源目录 + 目标目录」，组间不允许嵌套/重叠，
  目标目录可以等于源目录；目录组改动只影响下一次扫描（扫描结果和执行记录都保存组快照）。
- `core/preview.py` 改为按规范自动生成目录：
  正片 `目标/作品 (年份)/Season xx/`，特典 `目标/作品 (年份)/<specials_dir>/`，
  剧场版按 `naming.movie_layout` 放作品文件夹或特典季目录；
  `naming.preserve_structure=true` 时改为 `目标/源相对目录/`，只改文件名。
- 特典文件名里的季号强制为 `0`，避免出现 `Season 00` 里却是 `S01Exx` 的不一致。
- 预设文件升到 `version: 3`：`legacy_dir` 丢弃，旧内置模板自动替换为 `SxxExx`（只替换完全一致的旧写法）。
- 新增设置项：`naming.preserve_structure`、`naming.movie_layout`；设置页自动出现，模板页也有快捷开关。
- 执行改为“先落库、后移动”：`operations` 先记 running，明细状态 planned，每行处理完立即更新；
  失败或中断后可在历史里点「续跑」，只重试 planned / failed 的行，已成功的不会重复移动。
- 执行 / 撤销都按组快照校验路径：源必须属于组源目录、目标必须属于组目标目录，禁止跨组。
- 前端扫描页改为“目录组管理 + 勾选扫描”；模板页增加「目录结构」开关；历史表增加「续跑」。

### 13.4 后续实现顺序（未完成）

1. 第 5 步：三页清单 + 按作品折叠勾选。
2. 第 6 步：冲突面板 + 合并六选项 + 覆盖二次确认。
3. 第 7 步：重扫开关 + 作品识别第二步（别名、多版本、特典自动归属）。
4. 作品根目录识别单独排后。

### 13.5 第 3 步已经落地的内容（作品识别第一步）

- 新增 `core/identify.py`：扫描后立即识别，给每个视频行写入
  `identify{work_id,title,year,confidence,source,segment,needs_confirm,candidates}`。
- 识别顺序：**文件名优先**（去掉发布组、集号、画质编码、字幕语言、版本号、年份）→
  不足时逐层往上收集文件夹名（跳过 `Season xx / SPs / CDs / Scans / Subs / Anime / TV / Movies` 等收纳目录）。
- 只有“唯一确定”才自动归属：文件名候选与目录候选互相印证，或命中记住的规则；
  只有文件名信息（典型是平铺下载目录）不自动归属，多个文件共用同一段名称时生成「疑似作品」。
- 新增未归属区（`MatchPage` 下方）：疑似作品 / 单个未归属文件 / 已指派待确认 / 记住的规则。
  支持多选后指派给已有作品或新建作品、忽略、一键确认；单文件指派优先于候选组归属。
- 新增 `config/assignments.json` 与 `/api/identify/assignments`：按「目录组 + 名称段」记住指派，
  下次扫描直接复用，可修改、可删除。
- 作品不再等于文件夹：`matcher.build_groups()` 改为按 `work_id` 聚合，
  同一作品可分布在多个文件夹（多季、特典外置），一个文件夹里也可以有多部作品（合集）。
- 未归属的文件在预览 / 执行里直接拒绝（`status=error / action=skip`），不能改名或移动。
- 杂项文件（字幕、图片、压缩包）优先跟随同一目录里的视频，合集场景不会串到别的作品。
- 前端新增 `UnassignedPanel.tsx`；`types.ts` / `api.ts` 增加对应类型与接口。
- 本阶段**未做**：别名管理、多版本自动区分、特典自动归属（属于第 7 步的作品识别第二步）。

### 13.6 第 4 步已经落地的内容（执行引擎）

- 去掉预览 / 执行的 500 与 1000 条上限：`preview.build_preview` 的 `limit` 默认 0 表示不限制，
  扫描上限仍为 20000 个文件。
- 新增 `core/operations.py` 后台任务管理器：单进程单任务，每批 100 个自动连续跑完；
  同一时间只允许一个任务（排队中 / 执行中 / 已暂停都占用通道）。
- 开工前把整份执行计划和每一条目写入数据库（`operations` + `operation_items`），
  每完成一个动作立刻更新该条状态并落库；文件夹合并在建计划时展开为“每个文件一行”，
  扫描器留下的 `.ignore` / `.nomedia` 等隐藏标记文件不参与移动。
- 执行记录状态：`queued / running / paused / interrupted / cancelled / conflict / done / partial / undone`
  （排队中 / 执行中 / 已暂停 / 已中断 / 已取消 / 待处理冲突 / 已完成 / 部分完成 / 已撤销），
  失败条目带原因。结束规则：有冲突 → `conflict`，有失败 → `partial`，否则 `done`。
- 断点续跑：容器重启时启动钩子把 `queued/running/paused` 标成 `interrupted`；
  历史记录里出现「续跑」，续跑前逐个核对目录组快照（id / 源 / 目标），
  组被删除或改动会拒绝并指出是哪一组；已成功 / 已跳过的条目编号锁定，不会重复执行。
- 进度：`GET /api/history/{id}/progress` 返回进度条数据（已完成 / 总数、当前文件、
  成功 / 跳过 / 失败 / 冲突、第几批 / 共几批）；前端每 1.2 秒轮询，关掉页面任务继续跑，
  重新打开页面会自动接上进度。
- 暂停 / 取消：暂停是“当前文件处理完才停”，可继续；取消需二次确认，
  已完成的不自动回滚，记录保留可手动撤销，取消后不能再续跑。
- 运行中 / 已暂停时禁止增删改目录组：`/api/groups` 返回 `locked`，
  写操作返回 409；扫描页会显示锁定提示。
- 新增接口：`GET /history/{id}/progress`、`POST /history/{id}/pause`、
  `POST /history/{id}/continue`、`POST /history/{id}/cancel`。
- 前端模板页新增「执行进度」卡片（进度条 + 统计 + 当前文件 + 批次 + 暂停 / 继续 / 取消），
  执行记录表按状态显示「暂停 / 继续 / 取消 / 续跑 / 撤销」。
