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
第 4~6 步（执行引擎、三页清单、冲突面板）与第 7 步（重扫三档、别名、多版本、特典自动归属）
也已完成并验证；另外已加入 TMDB 代理与连通性测试。
界面交互仍是初版，后续会随 v3 后续步骤一起调整。

---

## 2. 技术栈

| 层 | 选型 |
|---|---|
| 后端 | Python 3.12 + FastAPI + Pydantic v2 + Uvicorn |
| 数据库 | SQLite（存执行历史 + 扫描会话；其余配置在 JSON 文件里） |
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

# 离线回归（不联网、不碰真实数据）
docker exec -w /app anime-renamer python tools/regression_check.py
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
│   ├── pattern_cache.json      # AI 命名规律缓存（指纹 → 结构描述、来源、命中次数）
│   ├── pattern_decisions.json  # 命名规律的取舍记录（采用 / 不采用 / 稍后再说）
│   └── app.db                  # SQLite：执行历史 / 撤销记录 / 扫描会话
├── media/                      # 宿主机媒体目录（示例/测试用，不入包）
├── backend/
│   ├── requirements.txt        # 精确版本锁定（升级见第 14 节）
│   ├── entrypoint.sh           # 处理 PUID/PGID 后以 appuser 运行
│   ├── data/                   # 本地开发默认配置目录（git 忽略）
│   ├── tools/regression_check.py # 离线回归脚本（临时目录，不碰真实数据）
│   └── app/
│       ├── main.py             # FastAPI 入口：注册路由、托管 SPA
│       ├── config.py           # 环境变量、设置项定义（单一来源）、预设选项映射
│       ├── store.py            # presets.json / groups.json / rules.json 读写；SQLite 执行历史与扫描会话
│       ├── scanner_helpers.py  # is_under 等路径小工具
│       ├── api/
│       │   ├── routes_meta.py     # /api/health /fields /config /rules /browse
│       │   ├── routes_groups.py   # /api/groups 目录组 CRUD 与边界校验
│       │   ├── routes_scan.py     # /api/scans /samples
│       │   ├── routes_identify.py # /api/identify 未归属区、指派、记住的规则
│       │   ├── routes_ai.py       # /api/ai 状态、识别/匹配建议与确认、命名规律（提规律/应用/取舍/缓存管理）
│       │   ├── routes_presets.py  # 预设 CRUD、导入导出、模板校验、/preview
│       │   ├── routes_match.py    # 分组、TMDB 搜索、套用、一键自动匹配
│       │   ├── routes_auth.py     # /api/auth 状态、登录、退出、设置访问密码
│       │   └── routes_execute.py  # 执行、历史、撤销、断点续跑
│       └── core/
│           ├── auth.py         # 访问密码哈希、会话 token、输错锁定
│           ├── groups.py       # 目录组规范化与“组间不重叠”校验
│           ├── scanner.py      # 目录遍历、忽略规则、标记文件、按目录组生成扫描会话
│           ├── identify.py     # 作品识别（上一级文件夹优先 + 文件名辅助 + 疑似作品 + 未归属）
│           ├── parser.py       # 目录层 + 文件层两级解析 → 模板字段
│           ├── lexicon.py      # 统一识别词典：特典目录名 / 季目录写法 / 版本号 / 集号清理写法（唯一来源）
│           ├── techname.py     # 技术标签（分辨率/编码/字幕组/年份）提取
│           ├── rules.py        # 内置识别规则（正则）与中文数字转换
│           ├── filetypes.py    # 扩展名 → 类别（video/subtitle/audio/image/archive/other）
│           ├── fields.py       # 模板变量注册表（前端变量面板由它驱动）
│           ├── template.py     # 模板渲染、空值折叠、非法字符净化、校验
│           ├── scan_store.py   # 扫描会话：内存缓存 + SQLite 持久化（重启可恢复）+ 勾选状态
│           ├── preview.py      # 预览构建（预览与执行共用的唯一计算入口）
│           ├── executor.py     # 真实移动/改名、冲突策略、空目录清理
│           ├── operations.py   # 后台执行任务：分批、进度、暂停/取消、续跑
│           ├── matcher.py      # 作品分组、候选打分、auto_pick 自动判定
│           ├── ai.py           # AI 辅助：OpenAI 兼容接口、识别 / 匹配建议（只建议不套用）
│           ├── pattern.py      # AI 命名规律：结构描述校验/编译/取值、形状指纹
│           ├── pattern_ai.py   # 提规律 / 用户确认后应用 / 记录取舍
│           ├── pattern_check.py # 5 项自检、样例预览、失败重问一次、整批放弃
│           ├── pattern_cache.py # 规律缓存与「指纹完全一致才自动套用」
│           └── metadata.py     # TMDB 搜索/详情、Bangumi 占位
└── frontend/
    ├── index.html / vite.config.ts / tsconfig.json / package.json
    └── src/
        ├── main.tsx / App.tsx / styles.css
        ├── api.ts              # 所有后端请求封装
        ├── listFilters.tsx     # 三页共用的搜索 / 筛选条与状态判定
        ├── types.ts            # 与后端 JSON 对应的类型定义
        └── pages/
            ├── ScanPage.tsx      # 扫描：目录组管理、扫描结果表、预设选择
            ├── MatchPage.tsx     # 匹配：作品列表、自动匹配、未归属区
            ├── UnassignedPanel.tsx # 未归属区：疑似作品 / 单文件 / 待确认 / 记住的规则
            ├── LoginPage.tsx     # 访问密码登录页
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
     → 以视频所在上一级普通文件夹名为准（跳过 Season/SPs/Anime 等收纳目录）；
       文件名解析只作辅助（季号、集号、类型、技术标签）
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

关键点：**扫描结果持久化在 SQLite 的 `scans` 表**（容器重启后可从「历史扫描」恢复，恢复后标 `restored=true` 并提示可能已过时）；**预览与执行共用 `preview.build_preview`**，不要在别处另写一套命名逻辑。**每个动作都必须带 group_id**，源路径属于该组源目录、目标路径属于该组目标目录，禁止跨组。

---

## 6. 后端关键设计（改代码前必须知道）

### 6.1 配置：JSON 文件 + SQLite 分工

- `config/settings.json`：全局设置，按分组 `parse / naming / providers / execution / scraper`。
  所有设置项在 `backend/app/config.py` 的 `SETTING_SPECS` 中定义（**单一来源**），
  默认值、类型、界面标签、说明都从这里出，`/api/config/schema` 供前端动态渲染设置页。
- `config/presets.json`：预设列表。每个预设含 `templates`（episode/special/movie 三种命名规则）与
  `options`（保存预设时记录的一份“当前设置快照”，仅用于以后切换预设时恢复，**不再在运行期覆盖设置**）。
  文件结构（v3 起 `templates` 只保存文件名规则，目录由系统自动生成；v4 起特典默认模板见 6.2）：
  ```json
  { "version": 4, "default_id": "preset-simple",
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
  - 特典：`目标目录/作品 (年份)/<naming.specials_dir>/文件名`（季号固定 0）
  - 剧场版：默认 `目标目录/作品 (年份)/文件名`；`naming.movie_layout=special` 时进特典季目录
  - 勾选 `naming.preserve_structure`：改为 `目标目录/源目录相对结构/新文件名`，不动文件夹
- v4（第 9 步）起特典默认文件名规则改为 `{series} - {special_type}{special_no}`
  （例如 `作品名 - NCOP1`），仍放进特典季目录；旧内置写法
  `{series} - S{season:02}E{episode:02}` / `[{group}] … [{resolution}]` 在升级时自动替换一次。
- 升级到 v3 时，旧预设的目录部分（`legacy_dir`）会被丢弃；旧内置模板里
  `{episode:02}` / `{special_type}{special_no:02}` 的写法会自动替换为 `S{season:02}E{episode:02}`。
  完全自定义的模板保持原样。
- `{ep_title}`（单集标题）已注册，三种模板都能用；为空时 `_cleanup()` 会自动去掉多余的
  分隔符、括号和连续的 ` - `，不会出现 `作品 - S01E01 - ` 这样的尾巴或空括号。
- `pattern` 里用 `{key}` 或 `{key:02}` 占位；`build_filename()` 只渲染文件名并补扩展名、净化非法字符。
- 空值折叠：某变量为空时，自动去掉多余的 ` - `、括号、连续分隔符，避免出现 ` -  - `。
- `sanitize_component()` 清理非法字符，按 `max_filename_bytes` 截断（默认 240）。
- `lexicon.SPECIAL_SEASON_FOLDER_NAMES = {"specials", "special", "season 00", "season 0", "season00", "s00"}` 视作特典目录层级。
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

`core/scan_store.py` 是「内存缓存 + SQLite 持久化」两层：`put_scan / get_scan / save_scan / delete_scan / list_scans / apply_field_updates`。
扫描结果（含三页清单、勾选状态 `selections`、匹配/重复集决策）会写进 `app.db` 的 `scans` 表，
容器重启后 `get_scan` 会从库里恢复，并标记 `restored=true` + `restored_at`，前端显示“可能已过时”的黄色提示。
落库前会去掉临时字段（`restored`、每文件 `check`）以控制体积；`SCAN_KEEP_LATEST=10`、`SCAN_TTL_DAYS=14`，
超出自动清理；扫描页「历史扫描」可手动恢复或删除。
执行接口要求传真实 `scan_id`（示例扫描 `is_sample=true` 不能执行）。
新增“批量修改字段”功能时，走 `apply_field_updates`（它会自动落库），不要在前端拼装整份扫描结果。

### 6.7 外部网络与代理（`core/metadata.py`）

- 设置项 `providers.proxy_url`：填写 `http://` 或 `https://` 代理地址（可带用户名密码），留空表示直连。
- 所有 TMDB / Bangumi / AI 请求都经过 `_build_opener()`；空代理也会显式注册 `ProxyHandler({})`
  保证直连，不受系统环境变量影响。
- `POST /api/providers/test` 做连通性测试，返回“连得通 / 连不通 + 原因”；代理密码不会出现在日志或响应里。
- Docker 里访问 Windows 宿主机上的代理必须用 `host.docker.internal`，不能填 `127.0.0.1`；
  `docker-compose.yml` 已加 `extra_hosts: host.docker.internal:host-gateway`。
- Bangumi 备用搜索（第 10 步）：`/match/search` 只查 TMDB 且**结果为空**时，自动用同一关键词
  查一次 Bangumi，把中文名 / 日文原名 / 年份作为 `fallback` 候选返回；
  用户点选后可拿这个名字再搜一次 TMDB。没配 `providers.bangumi_token`、
  请求失败或超时都**静默跳过**（`fallback=[]`），不打断 TMDB 流程、不影响匹配页其它功能。

### 6.9 访问密码与登录会话（第 11 步）

- 密码哈希存在 `settings.json` 的 `auth.password_hash`（**不在 `values` 里，不属于普通设置项**），
  接口、日志、前端都不会拿到原文；保存普通设置时会保留 `auth` 字段。
- 算法：标准库 `hashlib.scrypt`（`scrypt$N$r$p$salt$hash`），常量在 `core/auth.py`。
- 登录成功发随机 token（`HttpOnly` Cookie `ani_session`，约 7 天），数据库只存 token 的 SHA-256
  （`app.db` 的 `auth_sessions` 表），容器重启后仍然有效；修改/关闭密码会作废所有旧会话。
- 中间件 `require_login` 只豁免 `GET /api/health`、`/api/auth/status`、`/api/auth/login` 和静态页面；
  **设置/关闭密码也必须已登录**（避免别人直接关掉密码）。
- 连续输错 5 次锁定 5 分钟，按来源 IP 计数（进程内存储，重启清零），提示为中文。
- 关闭密码（忘记密码）：编辑 `config/settings.json`，删除 `"auth"` 整段后重启容器。
- 前端 `LoginPage`：未登录时只显示登录页；收到接口 401 会自动回到登录页。

### 6.8 作品识别（`core/identify.py` + `config/assignments.json`）

- 扫描结束后立刻识别，结果写进每个视频行的 `identify` 字段；**作品不再等于文件夹**：
  `work_id = w:<目录组>:<规范化标题>|<年份>`，同一作品的文件可以分布在多个文件夹（多季、特典外置），
  一个文件夹里也可以有多部作品（合集）。
- **以视频所在上一级普通文件夹名为准（第 9 步起）**：从文件所在目录逐层往上找第一个
  “普通文件夹”（跳过 `Season xx / SPs / Specials / CDs / Scans / Subs / Anime / TV / Movies`
  等收纳目录，也包括设置里的通用目录名与特典目录名）作为作品名。
  文件名解析仍然保留，但降级为辅助信息：只用于提取季号、集号、类型、技术标签，
  以及作为“互相印证”的参考。
- 判定规则：
  - 上一级文件夹名可用，且文件名里也能看出同一个作品（完全相同或一方包含另一方）
    → `confidence=high`、`source=folder`，自动归属并标记“待确认”；
  - 上一级文件夹名可用，但文件名写的是另一个名字 → `confidence=medium`、`source=folder`，
    **不自动归属**，进入未归属区，`reason` 写明“以上一级文件夹为准；文件名看起来是 X，两者对不上”；
  - 只有文件名（平铺在源目录根下、上级全是收纳目录）→ `confidence=medium`、`source=filename`，
    多个文件共用同一名称段时进入「疑似作品」；
  - 上级和文件名都看不出 → `confidence=none`，单个未归属文件。
- 以前怎么判：文件名优先，文件名和目录互相印证才自动归属，文件夹只作为兜底线索。
  现在怎么判：**文件夹优先**，文件名只做印证；两者对不上时不再猜，交给用户确认或手动指派。
  这样 `作品名/Season 01/第01集.mkv` 不会被判成“Season 01”这部作品，也不会因为文件名里带
  字幕组名或副标题就分裂成多部作品。
- 需要用户手动确认的情况：文件夹名与文件名对不上（medium）、平铺文件（medium）、
  以及合集/多层目录导致同一文件夹里有多个作品——本轮按“一个普通文件夹=一部作品”处理，
  复杂场景留给后续版本。
- 置信度：
  - `high`：文件夹名与文件名互相印证，或命中 `assignments.json` 记住的规则 / 别名；
  - `medium`：文件夹名与文件名对不上，或只有文件名信息 → 不自动归属，进未归属区；
  - `low` / `none`：信息不足 → 单个未归属文件。
- 未归属区三层：疑似作品（≥2 个文件共用名称段）、单个未归属文件、已指派待确认。
  **未归属的文件在预览和执行里直接拒绝**（预览行 `status=error / action=skip`）。
- 用户指派后可选“记住这条规则”，按「目录组 + 名称段 → 作品名」保存到 `config/assignments.json`。
  第 9 步起 `identify.segment` 优先写上一级文件夹名，因此记住的规则按文件夹名复用；
  仍然兼容旧数据里按文件名段记住的规则（先按文件名段查，再按文件夹名查）。
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
| GET | `/rules` | 识别规则列表（含 `builtin` 标记；返回 `note`：改动只对下一次扫描生效） |
| POST | `/rules` | 新增自定义识别规则（校验正则，失败返回 400 + 中文原因） |
| PUT | `/rules/{id}` | 修改规则（可启用/停用） |
| DELETE | `/rules/{id}` | 删除规则（内置规则不可删） |
| POST | `/rules/reset` | 恢复内置默认（重置内置规则，保留用户自定义） |
| GET | `/auth/status` | 是否开启访问密码、当前浏览器是否已登录（免登录可访问） |
| POST | `/auth/login` | 登录：`{password}`；错误 401，连续错 5 次 429 锁定 5 分钟（免登录） |
| POST | `/auth/logout` | 退出登录（清除当前会话） |
| POST | `/auth/password` | 设置 / 修改 / 关闭访问密码（`password=null` 表示关闭，**必须已登录**） |
| GET | `/browse?path=` | 浏览媒体根目录下的文件夹（扫描页选目录用） |
| GET | `/groups` | 目录组列表 + 媒体根目录 |
| GET | `/groups/check` | 检查现有目录组是否合法（重叠 / 目录不存在） |
| POST | `/groups` | 新建目录组：`{name, source, target}`（target 留空=source） |
| PUT | `/groups/{id}` | 修改目录组（同样做边界校验） |
| DELETE | `/groups/{id}` | 删除目录组（已有扫描/执行保留组快照） |
| POST | `/scans` | 扫描：`{group_ids[]}` → 扫描会话（含 groups 快照） |
| GET | `/scans/{scan_id}` | 取回扫描会话（从库恢复时带 `restored=true` / `restored_at`） |
| GET | `/scans` | 历史扫描记录（最近 10 次、14 天内） |
| DELETE | `/scans/{scan_id}` | 删除一条扫描记录 |
| PUT | `/scans/{scan_id}/selections` | 保存某页勾选：`{stage, ids[]}`（重启后恢复） |
| GET | `/samples` | 内置示例扫描（无真实目录时体验界面） |
| POST | `/queue` | 读取三页清单：`{scan_id}` → 条目 + 各阶段数量 |
| POST | `/queue/move` | 页面之间移动条目：`{scan_id, entry_ids[], to_stage}` |
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
| POST | `/match/search` | TMDB 搜索：`{query}` → 候选；TMDB 无结果时用同一关键词查一次 Bangumi 作为 `fallback` 备用候选 |
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
| GET | `/ai/status` | AI 是否启用/配置完整；不返回地址和 Key |
| POST | `/ai/identify/suggest` | 把未归属文件按目录打包问 AI，返回临时建议（不落库） |
| POST | `/ai/identify/apply` | 确认 AI 建议：写回作品名/年份/季集，可按别名记住写法 |
| POST | `/ai/match/suggest` | 把作品列表打包问 AI，返回建议的 TMDB 搜索关键词（不套用） |

请求/响应结构与前端 `src/types.ts` 一一对应，改接口时两边同步修改。

---

## 8. 前端结构

- `App.tsx`：顶部菜单四个页面（扫描 / 匹配作品 / 模板与预览 / 设置），
  全局状态：`scan`、`presets`、`presetId`（跨页共享，扫描与执行用同一个预设）。
- `api.ts`：所有请求封装，统一抛错；新增接口先加这里。
- `ScanPage`：管理目录组（多组「源目录 + 目标目录」）、选择预设、扫描；
  结果表按类别着色，已跳过目录会标出属于哪个组；目录组改动只影响下一次扫描。
  支持「历史扫描」恢复/删除；从库里恢复的扫描会显示黄色“可能已过时”提示 + 核对入口。
- `MatchPage`：顶部作品列表 + 匹配状态；「一键自动匹配 TMDB」→ 摘要（自动/待选/缺失）；
  待选行内联下拉候选，需手动行显示原因并可打开搜索。
  下方「未归属区」分四块：疑似作品（多选后指派/忽略）、单个未归属文件（多选指派）、
  已指派待确认（一键确认）、记住的规则（可删除）。
- `TemplatePage`：核心页。左侧模板编辑（变量面板可点插）+「目录结构」开关
  （只重命名文件 / 剧场版位置）；右侧预览；底部「保存预设 / 执行改名」与执行记录表
  （可撤销、可续跑）。预览请求 450ms 防抖，保存后会用服务端最新预设覆盖草稿。
  预览里出现重复集时，分组卡片会给出「加入 {resolution} / {group}」一键建议，
  点击只改当前草稿并重新生成预览，不会自动保存。
- `SettingsPage`：按 `/config/schema` 动态渲染全局设置；底部识别规则可新增 / 修改 / 删除 /
  启用停用 / 恢复内置默认，内置规则标 `builtin`，非法正则返回中文提示；改动只对下一次扫描生效。

样式统一在 `styles.css`（少量自定义 class：`preview-name`、`path-text` 等）。

三页列表（扫描 / 匹配 / 预览）共用 `src/listFilters.tsx`：关键字搜索（作品名 / 文件名）、
状态、类型（视频 / 字幕 / 素材文件夹）、目录组、已选 / 未选筛选，与分页、折叠勾选共存；
「筛选结果 X 项 · 已选（筛选后）Y」按筛选后的结果统计。勾选状态通过
`PUT /scans/{id}/selections` 落库，重启后恢复。

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
    "providers.ai_enabled": false,
    "providers.ai_base_url": "",
    "providers.ai_api_key": "",
    "providers.ai_model": "gpt-4o-mini",
    "providers.ai_batch_limit": 10,
    "providers.ai_special_enabled": false,
    "execution.conflict_policy": "skip",
    "execution.keep_empty_dirs": false,
    "execution.asset_policy": "move",
    "scraper.enabled": false
  }
}
```

预设（`presets.json`，version 6）首次启动会自动生成三个：
「简洁命名」（正片 `{series} - S{season:02}E{episode:02} [{resolution}] [v{version}]`、
特典 `{series} - {special_type}{special_no}`）、「保留技术信息」、「Jellyfin 媒体库目录」。
三种预设都只保存文件名规则，目录统一自动生成。
`version` 从 5 升到 6 时会把「与 v5 内置写法完全一致」的正片模板补上 `[v{version}]`，
用户自己改过的模板不动（详见 13.26.10）。

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

- 扫描会话已持久化到 SQLite（`scans` 表），重启容器后可恢复，保留最近 10 次 / 14 天。
- Bangumi 仅作 TMDB 无结果时的备用候选（第 10 步）；未实现详情/刮削。
- 合集文件（一个视频含多集）只做标记和搬运，**不做拆集**（拆集需要重新编码）。
- 刮削（生成 NFO/下载图片）仅预留开关 `scraper.enabled`。
- 特典命名默认用 `{series} - {source_season} {special_type}{special_no}[v{version}]`
  （来源季为空时整段折叠），不按 TMDB 第 0 季精确匹配
  （很多特典 TMDB 没有收录）；「基于 AI 的特典识别」已留开关 `providers.ai_special_enabled`，功能待实现。
- 单集标题 `ep_title` 已可在三种模板里使用，为空时会自动省略多余分隔符 / 空括号；
  但前端还没有用真实 TMDB 数据实际点验过（见 13.26.11 待办）。
- AI 命名规律是「手动触发 + 本地编译 + 自检 + 用户确认」的辅助手段，不是默认生效；
  其边界与遗留项见 13.26。
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
3. **正片统一 SxxExx**：正片使用 `SxxExx` 形式；特典从第 9 步起默认用
   `{series} - {special_type}{special_no}`（例如 `作品名 - NCOP1`），固定放进特典季目录；
   三种模板都开放给用户自定义。季号未知时默认 `S01`，不做黄色提醒；
   等 TMDB 匹配到该作品确实有多季时再让用户选择。
4. **特典季目录可配置**，默认 `Season 00`（设置项 `naming.specials_dir`）。
5. **剧场版提供选项**：放在作品文件夹内，或按特典方式进入特殊季目录
   （设置项 `naming.movie_layout`，模板页也可切换）。
6. **目录结构处增加“只重命名文件”选项**：勾选后不改文件夹、不改变相对位置，
   源目录里的相对结构原样映射到目标目录，只改文件名（设置项 `naming.preserve_structure`）。
7. **工作目录改为多组「源目录 + 目标目录」**：组间禁止嵌套/重叠，禁止跨组移动；
   扫描结果保存组快照；执行记录也保存组快照，撤销时按快照校验。
8. **作品识别采用“上一级文件夹优先 + 文件名辅助 + 不确定就问 + 未归属手动指派”**，分两步实现；
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

1. 作品根目录识别单独排后。

### 13.5 第 3 步已经落地的内容（作品识别第一步）

- 新增 `core/identify.py`：扫描后立即识别，给每个视频行写入
  `identify{work_id,title,year,confidence,source,segment,needs_confirm,candidates}`。
- 识别顺序（第 3 步版本；**第 9 步已改为文件夹优先，见 13.11**）：**文件名优先**
  （去掉发布组、集号、画质编码、字幕语言、版本号、年份）→
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

### 13.7 第 5 步已经落地的内容（三页清单 + 按作品折叠勾选）

- 新增 `core/queue.py`：每个扫描会话带一份“条目清单”，存在 `scan["queue"]["entries"]`，
  条目阶段为 `scan / match / preview / done`。页面之间只做“移动”，不会复制。
- 条目规则：视频 + 同名字幕 = 一个条目（字幕强制跟随，不能单独取消）；
  散落的音频 / 图片 / 压缩包各自一个条目；素材文件夹整夹一个条目，内部文件不单独列。
- 新增接口：`POST /api/queue`（读取清单与各阶段数量）、
  `POST /api/queue/move`（`{scan_id, entry_ids[], to_stage}`，只允许向前移动）。
- 识别 / 指派 / 确认后会调用 `queue.refresh_queue()` 重建条目，保留原有阶段与结果。
- `/api/preview` 与 `/api/execute` 新增 `folder_ids`；传 `file_ids` / `folder_ids` 时只算这些条目
  （空列表代表什么都不算）。清单模式下字幕固定为“跟随视频”。
- 执行接口只接受当前处于 `preview` 阶段的条目；执行结束后 `apply_operation_result()` 回写：
  全部成功的条目移到 `done` 并移出清单，失败 / 跳过 / 冲突的留在 `preview` 并带上原因。
- 预览行新增 `entry_id`，前端据此按作品折叠、展开到单集；未归属文件不能进入可执行清单，
  只能在匹配页批量指派。
- 前端：顶部流程条显示各页剩余数量；扫描页按目录组分区、支持整组全选；
  匹配页按作品勾选；预览页按作品折叠、可展开到单集，作品头支持全选 / 半选 / 全不选；
  执行只处理当前勾选项，执行完成后自动刷新清单。
- 大列表使用分页（每页 10~12 条），避免上万行一次性渲染。
- 验证链路：扫描 → 带出 → 匹配 → 带出 → 预览 → 只执行勾选部分 → 剩余条目仍在。

### 13.8 第 6 步已经落地的内容（冲突面板 + 合并选项 + 覆盖二次确认）

- 第一轮执行一律使用“跳过”策略：目标已存在时**不打断**，标记为 `conflict` 继续往下跑；
  全部批次结束后执行记录状态为「待处理冲突」，前端自动弹出冲突面板。
  `execution.conflict_policy` 设置暂时保留但不再参与执行（设置页已注明）。
- 新增 `operation_items.choice` 字段（启动时自动迁移），记录用户选的冲突处理方式。
- 文件夹冲突改为“整体一行”：素材文件夹目标已存在时不再逐文件展开，而是记一条
  `category=folder` 的冲突条目，由冲突面板决定整体处理或合并。
- 新增接口：
  - `GET /api/history/{id}/conflicts`：按目录组返回 `files` / `folders` 两块冲突；
  - `POST /api/history/{id}/resolve`：`{choices:[{seq,action}]}`，只处理确认过的冲突项，
    处理前重新核对目标是否还在（按 Windows 习惯不区分大小写）。
- 文件冲突三种：`skip` / `overwrite` / `suffix`；
  文件夹冲突六种：整体 `skip` / `overwrite` / `suffix`，合并 `merge-skip` / `merge-overwrite` / `merge-suffix`。
  「合并」只搬内容，目标文件夹保留；同名项指相对位置和名字都相同；
  `merge-skip` 的同名项留在源目录。合并时会展开成逐文件条目，保证即时落库与可撤销。
- 覆盖 / 合并并覆盖：`store.operation_items.choice=overwrite`，执行时先永久删除目标
  （文件夹整体覆盖会 `rmtree` 整个目标文件夹），这是唯一允许删除目录的例外，
  界面上必须先二次确认「永久删除、撤销无法恢复」，并显示目标路径与数量。
  撤销只能把新移过去的内容搬回，无法恢复被覆盖删除的旧内容。
- 冲突解决同样走后台任务：有进度、暂停 / 取消、即时落库、可撤销；
  解决完的汇总写在 `operations.summary.resolution` 与 `remaining_source`，
  前端进度卡显示“覆盖 / 跳过 / 生成副本 / 合并 / 源目录还剩多少”。
- 前端模板页新增冲突面板：按目录组分区，文件冲突与文件夹冲突分开；
  批量按钮只作用于“本组当前勾选项”，没勾选时作用于本组该区块全部；
  「稍后处理」直接关闭不会执行任何覆盖，任务保持「待处理冲突」。
  执行历史里状态为「待处理冲突」的记录也提供「处理冲突」入口。
- 冲突面板对已有作品文件夹、Season 文件夹不视为冲突：只有最终文件路径撞名，
  或素材文件夹整体撞名，才进入面板。
- 验证：临时目录造出文件冲突、文件夹整体冲突、文件夹同名子项冲突三类场景，
  三种文件策略 + 三种整体策略 + 三种合并策略全部通过，撤销可用，大小写不同仍判定冲突。

### 13.9 第 7 步已经落地的内容（重扫三档 + 作品识别第二步）

#### 重扫三档（`core/rescan.py`、`routes_scan.py`）

- 正常执行结束且状态为 `done` 时**不自动重扫**，前端只提示“执行结束，默认不会自动重扫”。
- 自动轻量核对的时机：执行状态不是 `done`（部分失败 / 冲突 / 中断）、
  有条目 `merged` 或选了 `overwrite / merge-*`、任务被取消、以及撤销前。
- `POST /api/scans/{id}/recheck`（轻量核对）：逐个检查源文件是否还在；
  源文件不见了、但最近一次执行明细里该条状态是 `done` / `merged` 且目标存在 → 记为「已移动」；
  其他不存在 → 记为「缺失」。结果写进每行的 `check` 和 `scan["last_check"]`，
  并把清单里“全部已移动”的条目移到 `done`，缺失的条目标一个失败提示，让用户去完整重扫。
  撤销前的核对传 `update_entries=False`，只做检查、不改清单阶段。
- `POST /api/scans/{id}/rescan`（完整重扫）：按扫描里保存的目录组快照重新扫描、重新识别、
  重建清单，返回新的扫描会话；目录组已被删除时返回 400 并指出是哪几组。
- 前端扫描页统计下方有「核对当前清单（轻量）」「完整重扫」两个按钮，显示上次核对时间与统计；
  模板页执行结束后弹出结束提示（已完成 / 部分完成 / 待处理冲突 / 已取消 / 已中断），
  按钮「核对当前清单」，完整重扫引导到扫描页。

#### 别名（`config/aliases.json`）

- 新文件 `config/aliases.json`（`{"version":1,"aliases":[]}`），记录
  `{id,title,year,alias,created_at,updated_at}`。
- 识别时把文件名段规范化后与别名**精确比对**（不是相似度）；同一别名对应了多个不同作品时
  **不自动套用**，留给用户确认。命中别名时 `source="alias"`、`confidence="high"`，
  但不标 `needs_confirm`（视为用户已经确认过这一写法）。
- 别名只用于推荐与复用，**绝不靠相似度自动合并两部作品**。
- 指派弹窗新增「记住为别名」复选框；未归属区新增「别名」标签页，可新增 / 修改 / 删除。
- 接口：`GET/POST /api/identify/aliases`、`PUT/DELETE /api/identify/aliases/{id}`；
  指派接口增加 `remember_alias`。

#### 多版本 / 重复集（`core/duplicates.py`）

- 只统计已归属（有 `work_id`）、`kind=episode`、未被忽略的视频；
  同一个 `work_id + SxxExx` 出现 ≥2 个文件即为「重复集」。
- 分三类并在提示里说清：
  - `multi_version`「同集多版本」：有版本号（v1/v2）或 ≥2 种不同分辨率；
  - `multi_source`「同作品多来源」：同集文件来自不同文件夹（甚至不同目录组）；
  - `parse_error`「解析算错集号」：同一文件夹、没有版本差异却都解析成同一集。
- 预览阶段就把这些行标红（`status=error`，消息以「重复集（...）」开头），执行前会被拦下。
- 三种处理（`POST /api/identify/duplicates`，`action` 取 `exclude` / `keep` / `keep_all` / `reset`）：
  - 「排除勾选的」：勾选的文件跳过，其余正常改名；
  - 「只保留勾选的（其余排除）」：用于把 v2 当修正版替换 v1，旧版留在源目录；
  - 「全部保留，我自己在模板里区分」：需要模板里有能区分它们的技术变量，
    否则两个文件算出同一目标路径时仍按「失败」处理，**不静默覆盖**。
- 决策存在 `scan["duplicate_decisions"][key]`，可「重新选择」清掉重来。
- **「两个文件算出同一目标路径按失败处理」的原有行为保持不变，没有并入冲突面板。**

#### 特典自动归属

- 扫描时被忽略的特典类目录（`ignored_dirs` 里 `reason="special"`）会生成候选：
  从本次扫描已识别的作品 + 别名里，按目录名做匹配打分，给出最可能的作品与理由。
- 候选**只是建议**，必须由用户在未归属区「特典候选」标签页确认；不确认就**保持原地不动**，
  `asset_policy` 也不会把它移动。
- 确认后移动到 `目标目录/作品 (年份)/<specials_dir>/`，保留特典目录里的子层级；
  未归属的特典目录不会跨目录组移动。
- 接口：`POST /api/identify/special`（`action=assign` 关联作品，`action=keep` 取消关联）。

#### 识别理由

- 每个视频行的 `identify` 增加 `reason`；未归属区的疑似作品 / 待确认 / 单文件也带理由，
  前端显示「判断依据：...」，说明为什么这么判、为什么不自动归属。

#### 新增文件与接口

- 新增文件：`backend/app/core/duplicates.py`、`backend/app/core/rescan.py`、`config/aliases.json`。
- 新增接口：`POST /scans/{id}/recheck`、`POST /scans/{id}/rescan`、
  `/identify/aliases` 系列、`POST /identify/special`、`POST /identify/duplicates`；
  预览响应新增 `duplicates` 字段。

### 13.10 AI 辅助（第 8 步）

- 新增 `backend/app/core/ai.py`：调用标准 OpenAI 兼容接口 `/v1/chat/completions`，
  支持本地 Ollama 或任何在线服务；请求沿用 `providers.proxy_url` 的代理设置。
- 设置项（`providers` 分组，界面自动出现）：
  `ai_enabled`、`ai_base_url`、`ai_api_key`（密钥）、`ai_model`、`ai_batch_limit`（1~50，默认 10）。
  设置页注明：只发送文件名、目录名、候选标题等文本，绝不发送文件内容。
- 触发方式全部是手动按钮：扫描页「AI 辅助识别」，匹配页「AI 辅助匹配」；
  扫描 / 匹配过程不会自动调用。未启用或没配地址时按钮置灰并提示原因。
- AI 只给建议：接口把建议返回给前端，**不写进扫描会话**；前端弹窗展示
  「来自 AI」标签、作品名 / 年份 / 季集 / 类型 / 标签 / 判断依据，用户勾选并点确认后
  才由 `POST /api/ai/identify/apply` 写回（作品名、年份、季集），可按别名记住写法。
  未确认的建议不落库，下次仍会重新询问。
- 匹配建议只给「更适合拿去 TMDB 搜索的关键词」；点「用这个关键词搜索」才进入现有
  TMDB 搜索和候选选择流程，AI 不会直接套用。
- 打包请求：识别按未归属文件打包，匹配按作品列表打包，单次最多 `ai_batch_limit` 条；
  超出部分留给下一次点击。
- 失败降级：连不上、超时、Key 无效、地址 404、返回不是 JSON 等情况统一转成中文提示，
  不影响规则识别和 TMDB 匹配；Key 不会出现在错误信息、日志或接口响应里。
- 接口：`GET /api/ai/status`、`POST /api/ai/identify/suggest`、
  `POST /api/ai/identify/apply`、`POST /api/ai/match/suggest`。

**验证记录（第 8 步）**：临时目录 + 模拟 OpenAI 接口共 17 项全部通过：
未配置 / 关闭时入口不可用、错误地址 / 错误 Key / 非 JSON 的中文报错、
批量上限 2 条生效、未确认不落库、确认后写回并记住别名、完整重扫后别名自动复用、
AI 匹配只给关键词不自动套用；测试目录组、别名与临时目录已清理，
用户原有目录组「1」和现有 Key 未被改动。

### 13.11 第 9 步已经落地的内容（文件夹优先识别 + 重复集建议 + 特典模板 v4）

#### 作品识别改为“上一级普通文件夹优先”

- `core/identify.py` 的主判定改为：**先取视频所在目录往上第一个普通文件夹名**，
  跳过 `Season xx / SPs / Specials / CDs / Scans / Subs / Anime / TV / Movies` 等收纳目录
  （以及设置里的通用目录名、特典目录名、OVA 目录名）。
- 文件名解析（`filename_segment`）保留，但只作为辅助：用于提取季号、集号、类型、技术标签，
  以及判断“文件夹名和文件名是否互相印证”。
- 判定结果：
  - 文件夹名 + 文件名一致 → `high` / `source=folder`，自动归属并标记“待确认”；
  - 文件夹名可用但文件名对不上 → `medium` / `source=folder`，**不自动归属**，
    未归属区的 `reason` 写明“以上一级文件夹为准；文件名看起来是 X，两者对不上”；
  - 只有文件名（平铺） → `medium` / `source=filename`，多个文件共用名称段时进「疑似作品」；
  - 都看不出 → `none`，单个未归属文件。
- `identify.segment` 改为优先写文件夹名，因此「记住的规则」按文件夹名复用；
  查找顺序仍是“先文件名段、再文件夹名”，兼容旧数据。
- 未归属区、别名、特典候选、AI 建议、重复集检测、手动指派与记住规则的机制全部不变。
- 已知不做深度识别的场景：多层目录、一个文件夹多部作品（合集）、多季同目录；
  本轮按“一个普通文件夹 = 一部作品”处理。

**以前 vs 现在**：以前文件名优先，文件名与目录互相印证才自动归属，目录只是兜底；
现在文件夹优先，文件名只做印证。`作品名/Season 01/第01集.mkv` 不会再被判成“Season 01”这部作品；
但“文件夹名与文件名对不上”的文件会进入未归属区，需要用户确认或手动指派（可记住规则）。

#### 重复集一键建议（前端 `TemplatePage.tsx`）

- 预览里检测到重复集时，`multi_version` / `multi_source` 的分组卡片内新增一条蓝色提示，
  建议在正片模板里加入 `{resolution}` 或 `{group}`，并提供两个按钮。
- 点按钮只会把变量追加到 `draft.templates.episode.pattern`（**只改当前草稿，不自动保存**），
  然后立即重新生成预览；预览返回后会把这几份文件将生成的名字显示出来。
- 变量已存在时只重新生成预览，不重复追加。
- 如果预览仍提示「重复集」，还需要在该组点「全部保留（我自己在模板里区分）」放行；
  建议文案里已写明这一步。`parse_error` 分组不显示这个建议。

#### 特典默认模板 v4

- `store.PRESETS_VERSION = 4`；`DEFAULT_TEMPLATES["special"]` =
  `{series} - {special_type}{special_no}`；三个内置预设同步更新。
- `LEGACY_SPECIAL_V4` 只在版本从 3 升到 4（或从旧数据库导入）时执行一次，
  只替换完全一致的内置旧写法，避免以后每次加载都覆盖用户自定义。
- 实际产物：`目标目录/作品 (年份)/<specials_dir>/作品名 - 特典类型+编号.ext`
  （例如 `/dst/Show A/Season 00/Show A - NCOP1.mkv`）。
- 不做 TMDB 第 0 季的精确匹配；新增设置项 `providers.ai_special_enabled`（默认关闭）作为
  「基于 AI 的特典识别」的预留开关，本轮不生效，后续从 `core/ai.py` 接入。

#### `{ep_title}`

- 已在 `core/fields.py` 注册，三种模板都能使用，前端变量面板显示中文说明。
- 为空时由 `template._cleanup()` 自动折叠：`作品 - S01E01 - {ep_title}` → `作品 - S01E03`（无多余 ` - `）；
  `作品 ({ep_title}) - S01E01` → `作品 - S01E01`（无空括号）。
- 非空时正常追加：`作品 - S01E03 - 苍之海`。

**验证记录（第 9 步）**：重新构建容器并启动后，用临时目录（`media/_test9`，目录组 test9）
验证通过：平铺两文件 → medium 且进入疑似作品；`Show A/Season 01/` 两集 → 作品名为 `Show A`
（不是 `Season 01`）；`Show A/Season 00/` 特典 → 作品名 `Show A`，产出
`/dst/Show A/Season 00/Show A - NCOP1.mkv`；`SPs` 目录仍按特典类忽略并生成特典候选；
文件夹名与文件名不一致 → medium + 中文理由，未自动归属；手动指派并记住规则后完整重扫，
按文件夹名自动复用（`source=assignment`）；Show B 同集两份文件被识别为「同集多版本」并标红，
选择「全部保留」+ 模板加 `{resolution}` 后生成两个不同名字且状态变为可执行；
`{ep_title}` 空/非空/括号三种写法渲染正确；执行 3 个文件后撤销成功。
测试目录组、临时目录、测试指派规则、测试别名与两条测试执行记录已全部清理，
用户原有目录组「1」、真实动画与现有配置未改动。

### 13.12 第 10 步已经落地的内容（持久化 + 规则编辑 + 筛选 + 预设 v5 + Bangumi 备用）

> 界面整体美化**没做**，保持现有风格。

#### 依赖锁定与离线回归脚本

- `backend/requirements.txt` 改为精确版本（`fastapi==0.162.0`、`uvicorn[standard]==0.54.0`、
  `pydantic==2.14.3`），升级方法见第 14 节。
- 新增 `backend/tools/regression_check.py`（只用 Python 标准库，不联网、不引测试框架）：
  在 `tempfile.mkdtemp()` 里造最小样本（同集 1080p/720p、无分辨率、字幕、特典文件夹、
  一个故意冲突目标、一个中断记录），把 `MEDIA_ROOT` / `APP_CONFIG_DIR` / `APP_DB_PATH`
  全部指向临时目录，跑完「扫描 → 识别 → 清单 → 预览 → 重复集 → 执行 → 冲突 → 撤销 → 续跑 → 规则」，
  输出 PASS/FAIL 清单与退出码。**不碰真实 media、config、执行记录。**
- `Dockerfile` 增加 `COPY backend/tools ./tools`，可进入容器运行：
  `docker exec -w /app anime-renamer python tools/regression_check.py`。

#### 扫描结果持久化（替代内存 scan_store）

- `app.db` 新增 `scans` 表（`scan_id / created_at / updated_at / file_count / data`（JSON））。
- `scan_store.put_scan / save_scan / apply_field_updates` 都会落库；`queue.move`、
  指派 / 忽略 / 确认 / 特典 / 重复集决策、轻量核对、执行结果回写、撤销后都会 `save_scan`。
- 勾选状态存在 `scan["selections"][stage]`，由 `PUT /scans/{id}/selections` 写入；
  三页各自恢复自己的勾选。
- 体积控制：落库前去掉 `restored` / `restored_at` 和每文件的 `check`；
  `SCAN_KEEP_LATEST=10`、`SCAN_TTL_DAYS=14` 自动清理；`GET /scans` 列表 + `DELETE /scans/{id}` 手动删除。
- 重启后从库恢复时 `restored=true`，扫描页显示黄色提示「这是上次保存的扫描结果，可能已经过时」
  + 「核对当前清单」按钮（轻量核对）。

#### 识别规则可编辑

- `store.py` 新增 `list_rule_dicts / create_rule / update_rule / delete_rule / reset_builtin_rules`
  与 `_validate_rule`；内置规则带 `builtin=true` 且**不可删除**，用户自定义可增删改；
  「恢复内置默认」只重置内置规则、保留自定义。
- 正则校验在 `core/rules.py::validate_rule_payload`：名称 / 范围 / 类型 / 优先级 0~200 / 正则合法性，
  非法正则由 `_regex_message` 转成中文（常见错误映射 + 出错位置），接口返回 400 而不是崩掉。
- `list_rules` 对坏正则 `try/except re.error` 跳过，避免一条脏规则让扫描整体崩溃。
- 改动只对**下一次扫描**生效（已扫描结果不重算），设置页有黄色提示说明这一点。

#### 三页列表搜索与筛选

- 新增 `frontend/src/listFilters.tsx`：`entryType / entryStatus / entryMatches` +
  `ListFilterBar`（关键字、状态、类型、目录组、已选/未选），三页共用。
- 状态口径：未识别 / 疑似作品 / 待确认 / 已匹配 / 未匹配 / 冲突 / 失败 / 已完成；
  类型：视频 / 字幕 / 素材文件夹 / 其它。
- 筛选与分页、按作品折叠勾选共存；「筛选结果 X 项 · 已选（筛选后）Y」按筛选后的集合统计。
- 扫描页筛选到 0 条时提示「没有符合筛选条件的条目」，不再误报“本页没有待带出的条目了”。

#### 正片模板 v5（`{resolution}`）

- `store.PRESETS_VERSION = 5`；`DEFAULT_TEMPLATES["episode"]` 与三个内置预设改为
  `{series} - S{season:02}E{episode:02} [{resolution}]`（technical 原本就有）。
- `LEGACY_EPISODE_V5` 只在版本从 4 升到 5 时执行一次，**只替换与旧内置完全一致的写法**，
  用户自定义模板不覆盖。
- `{resolution}` 为空时由 `template._cleanup()` 正常折叠，不留多余 `[ ]` / 分隔符；
  重复集建议文案同步说明“在模板里加 `{resolution}` 或 `{group}` 区分同集多版本”。

#### Bangumi 备用搜索

- 见 6.7：仅当 `source=tmdb` 且 TMDB 候选为空、且配置了 `providers.bangumi_token` 时查一次 Bangumi，
  结果放 `fallback`；未配置 / 失败 / 超时静默跳过。
- 前端搜索弹窗在候选为空且有备用时显示提示与备用候选，点「用该名称搜 TMDB」用该名字再搜一次。

#### 扫描上限提示

- `scanner.MAX_FILES` 仍为 20000。触发上限时 `truncated=true`，扫描页提示
  「已达到上限（20000 个文件），还有文件未扫描，请缩小目录范围后重新扫描。」

**验证记录（第 10 步）**：容器重建并启动（`/api/health` ok）；回归脚本容器内 27/27 通过；
扫描持久化验证——保存勾选后 `docker compose restart`，再取回返回 `restored=true`、
3 个文件、`selections.scan` 一致、清单停在扫描页；规则验证——非法正则 `POST /rules` 返回 400 +
中文原因，新增自定义规则后新建文件再扫描集号命中、删除测试规则；三页筛选控件与统计在浏览器中可见，
扫描页为「筛选结果 3 项 · 已选（筛选后）1」，预览页为「筛选结果 1 项 · 已选（筛选后）0」；
Bangumi 兜底在容器内 monkeypatch 验证四种情况（有 token 返回 1 条中文名 / 无 token 空 / 抛错空 / TMDB 有结果不调用）；
上限按 `MAX_FILES=2` 临时压测确认 `truncated=true`。
测试目录组、测试扫描记录、`media/_test10*`、`_test10_state.json`、临时脚本已清理，
用户目录组「1」、真实动画、现有预设与 3 条历史执行记录未改动。

---

### 13.13 第 11 步已经落地的内容（合集 + 访问密码 + 网盘参数 + 上限可调）

> 界面整体风格没改，处理顺序和优先级也没动。

#### 合集文件（一个视频含多集）

- `parser.detect_collection` 识别这些写法：`01-12`、`EP01-12`、`1-12话`、`合集`、`Complete`、`全12话`
  （只匹配 1~3 位数字的区间，不会把 `1920x1080`、`2019-2020` 误判成合集）。
- 命中后视频行带 `collection=true` + `collection_range`，`stats.collection` 计数；
  **不按 `SxxExx` 模板命名，也不参与重复集判定**（`duplicates` 直接跳过）。
- 预览页里合集命中 `_build_collection`：
  - 没有选择时 `status=skip`、`action=skip`，提示去「匹配作品」页的合集区做选择，**绝不自动执行**；
  - `原样搬运`：保留原文件名，按原相对目录放到目标组；
  - `指定季存放`：保留原文件名，放到 `目标组/作品文件夹/Season NN/`；
  - 两种情况都保留原文件名；界面明确写「合集不做拆集（拆集需要重新编码）」。
- 选择存在 `scan["collection_decisions"][file_id] = {action, season}`，通过
  `POST /api/identify/collection`（`keep` / `season` / `clear`）写入并落库。
- 合集不会被拆成多个条目，也不会参与「重复集」的 1080p/720p 判定。

#### 访问密码（登录保护）

- 见 6.9。要点：密码只存 `settings.json` 的 `auth.password_hash`（scrypt 不可逆哈希）；
  未设置时行为与原来完全一致；设置了才启用登录页 + 接口校验（`/api/health` 除外）。
- 会话 7 天、存在 SQLite（重启仍有效），可主动退出；连续错 5 次锁 5 分钟。
- 忘记密码：编辑 `config/settings.json` 删除 `"auth"` 段并重启容器。

#### 网盘友好参数（不改变处理顺序）

- `execution.batch_size`（默认 100，1~1000）、`execution.batch_delay_seconds`（默认 0，0~600）、
  `execution.retry_times`（默认 0，0~5）、`execution.retry_backoff_seconds`（默认 3，0~60）。
- `operations._batch_settings()` 每批开始时读取设置；批与批之间按配置 sleep；单条移动失败且错误是
  「移动失败」时按次数与退避重试（文件名 / 路径类永久错误不重试）。
- 这些是普通设置项，可通过「保存预设」把整组参数复用；**不按文件夹或类型重排顺序**，执行顺序仍是预览顺序。
- 「只重命名文件」模式不变；网盘场景推荐它，因为同目录改名不产生数据搬运（见 README）。

#### 扫描上限可调

- `parse.max_files`（默认 20000，范围 1~100000）；`SettingSpec` 新增 `min` / `max`，
  `_coerce_strict` 超范围时抛中文错误，`PUT /api/config` 返回 400。
- `scanner` 从设置里读上限，扫描结果带 `max_files`；达到上限时 `truncated=true`，
  前端提示文案保持「已达到上限（N 个文件），还有文件未扫描，请缩小目录范围后重新扫描。」
- 已知影响：上限越高，内存占用、扫描时间、后续 TMDB 请求量都会上升，建议配合扫描结果持久化使用。

**验证记录（第 11 步）**：容器重建并启动（`/api/health` ok）；回归脚本 27/27 通过；
合集——6 种写法全部识别，未选择时预览为 skip 且不进入执行计划，选「原样搬运」落到原相对目录、
选「指定季存放」落到 `Season 02`，未选择的合集确实没被移动；
密码——未设置时接口直接可用、设置后未登录访问接口 401、错误密码 401 且第 5 次 429 锁定、
重启后会话仍有效、退出后 401、关闭后恢复直接访问，settings.json 无明文密码；
批参数——`batch_size=1, delay=1` 时 4 条记录分 4 批、耗时约 4 秒且顺序不变，
重试逻辑用桩函数验证（重试 2 次共调用 3 次、永久错误不重试）；
上限——设为 30000 时 20050 个文件全部扫到（未截断），设回 20000 时截断在 20000 且 `truncated=true`。
测试目录组、测试扫描、`media/_test11*`、测试密码、测试执行记录、临时 cookie 文件已清理；
用户目录组「1」、真实动画、现有设置与 3 条历史执行记录未动。

---

### 13.14 文件夹 2 修复（作品名 + 特典归属，通用规则）

> 目标：`/media/2` 的扫描 → 识别 → 匹配 → 预览全部正确；不改界面，也不为单个目录写死特例；文件夹 1 已验证结果不回退。

- **年份不再误吞分辨率**：`techname.extract_year` 与 `identify._YEAR_RE` 都会跳过 `1920x1080`、`2048x…` 这类分辨率数字；`1080p` 等本来就不会命中。
- **目录名清洗**：`parser.RELEASE_NOISE_RE` 新增 `BD-BOX` / `Blu-ray BOX`，`_TAG_TOKEN_RE` 把 `Ver.` 和 `1920x1080` 视为标签，新增 `MEDIA_SUFFIX_RE` 去掉末尾的 `- TV + OVA` / `- Movie` 等媒体形态后缀；区间写法改为只认前后都不是字母数字的数字，避免把 `x265-10Bit` 切成 `x Bit`。
- **文件名作品段**：`filename_segment` 新增“作品名 - 01”这种裸集号模式，并把 `[SPxx] Menu/NCOP…` 中的特典标记与分辨率括号清掉；特典行只要目录能推断出作品，就不再拿文件名段去“对不上”（`apply_identification` 里对 `kind=special` 清空 segment）。因此 `[Moozzi2] Aldnoah.Zero BD-BOX …/EXTRA/…` 会统一识别为 `Aldnoah.Zero`。
- **EXTRA 这类目录不再当作品名**：`identify._generic_dirs` 把 `lexicon.SPECIAL_DIR_TYPES` 的键（`extra` / `extras` / `bonus` / `ncop` / `nced` / `menu` …）并入通用目录名；`parser._pick_title` 也会跳过这些目录。规则即使被用户关掉，也不影响这条兜底。
- **特典编号防重名**：`parser.renumber_specials` 先按文件名识别更具体的类型词（Menu / NCOP / NCED / PV / CM / Yokoku / OVA / SP），再按“同作品 + 同类型”检查 `special_no` 是否重复；**只有会重名时才按路径顺序连续编号**，原本唯一的特典保持原编号（所以文件夹 1 的 `NCOP`、`NCED` 名字不变）。预览默认模板仍是 `{series} - {special_type}{special_no}`。
- **S00 特典不再报“缺集”**：`matcher.build_updates` 对 `kind=special` 且 `season=0` 的行不加入 `missing`，因为 TMDB 的 Season 00 本来就可能没有对应集。

**验证记录（文件夹 2）**：目录结构为 `[Moozzi2] Aldnoah.Zero BD-BOX … - TV + OVA` 根目录 + `EXTRA`（61 个特典 + 10 个附件） + `ScreenShot` + `EXTRA` 下的 Bonus CD / BD Scans；修复前特典标题全是 `EXTRA`、年份被识别成 `1920`，预览 105 条中 57 条重名冲突；修复后扫描识别 105 个条目全部为 `Aldnoah.Zero`（high），匹配 TMDB 61259（火星公主 / 2014，1 季 24 集，`season_mode=auto`），预览 `ok=105 / warning=0 / error=0 / skip=0`，特典路径统一为 `火星公主 (2014)/Season 00/火星公主 - Menu1…Menu16 / NCOP1…2 / NCED1…5 / PV1…3 / CM1…10 / Yokoku1…23 / SP9 / SP10 / OVA24`，素材保留 `EXTRA/...` 与 `ScreenShot/` 子目录。
文件夹 1 用内存扫描回测：`Ruri no Houseki`（13 集 + NCED/NCOP，编号不变）、`Bleach Sennen Kessen-hen`（41~50）与修复前一致；离线回归 27/27 通过。
记忆清理：备份 `config/backup-20261109-112309/`；删除测试别名 1 条（`Aldnoah.Zero -BOX x Bit Ver. - TV + OVA` → `Aldnoah.Zero (2014)`，创建于 2026-11-09 11:14）；`config/assignments.json` 原本为空，未删除任何指派规则；更早的真实记录未动。

### 13.15 文件夹 3 修复 + 特典模板变量（第 12 步）

> 目标：修掉「确认无误」500；让特典目录在名字对不上时也能按目录结构确认归属；特典模板支持 `{version}` / `{resolution}`，同时保证不用这些变量时命名不变。

- **500 的真实原因**：`identify.confirm_work` 里把 `_row_identify(row)` 的返回值存进了 `identify` 变量，后面却使用 `ident`，触发 `NameError: name 'ident' is not defined`（容器日志可见完整堆栈）。已统一为 `ident`。
- **接口异常兜底**：`main.py` 注册全局 `Exception` 处理器，未预料到的异常写完整堆栈到日志，返回 `400` 和一句可读中文（`操作失败：服务内部出错了（异常类型）…`），界面不再只看到 `500 Internal Server Error`。
- **特典目录结构归属（通用规则）**：`identify.special_suggestions` 建立「上级目录 → 该目录下出现过的作品」索引；若特典目录的上级目录里只有一个作品，就补一个 `score=1` 的候选，理由为「按目录结构归属」。仍然只推荐、需用户确认，不自动移动。解决中文文件夹名（`奇蛋物语`）与英文作品名（`Wonder Egg Priority`）天然对不上的情况。
- **特典目录跟随匹配后的作品目录**：`preview.build_preview` 统计 `(目录组, work_id) → 视频实际落点根目录`；已确认的特典目录优先对齐该根目录，匹配 TMDB 后作品名变化（`Wonder Egg Priority` → `奇蛋物语 (2021)`）也不会把同一部作品拆成两个文件夹；查不到视频落点时才退回用确认时的标题拼路径。
- **特典模板变量**：`{version}` / `{resolution}` 现在在特典模板里都能用。`techname._VERSION_RE` 支持 `Ver.01`（带空格也认），`_RESOLUTION_RE` 支持 `1920x1080`（归一化为 `1080p`）。
- **保留文件名自己的序号**：`parser.typed_special_number` 识别 `Menu - 01`、`OVA - 24.5` 这类“同类特典自己的序号”，写进 `special_no_raw`；`special_no` 仍保持碟片盒号逻辑。只有当特典模板里出现 `{version}` / `{resolution}` 时才改用 `special_no_raw`（解决 `OVA24.5` 被压成 `OVA24`）。
- **重名兜底不再整目录重排**：`build_preview` 在模板变量仍不足时，只把真正撞名的特典按原 `special_no` 重新渲染（最多两轮），Menu 的版本区分得以保留，CM 这类完全同名的才退回连续编号。
- **空变量折叠**：`template._cleanup` 除了去空括号，还会折掉空变量残留的 `[v]` / `[Ver.]` / `[version]` 壳子，不留多余分隔符。
- **内置默认模板**：`store.DEFAULT_TEMPLATES["special"]` → `{series} - {special_type}{special_no}[v{version}]`；技术版预设同理加 `[v{version}]`。**未迁移现有 `config/presets.json`**（用户没改模板时保持原命名，是否升级待用户拍板）。

**验证记录（文件夹 3）**：`/media/3` 结构为 `奇蛋物语/[SweetSub&VCB-Studio] Wonder Egg Priority [Ma10p_1080p]`（13 集）+ `奇蛋物语/CDs`、`Scans`、`SPs`（3 个特典目录，扫描时跳过）。修复前：3 个目录提示「目录名对不上扫描到的任何作品」，预览 `skip=3`；「确认无误」500。修复后：3 个目录各得一个结构候选，`/identify/confirm` 返回 200，确认归属后预览 `ok=43 / skip=0`；匹配 TMDB 111255（奇蛋物语 / 2021）后视频进 `奇蛋物语 (2021)/Season 01/`，特典目录同步进 `奇蛋物语 (2021)/CDs|Scans|SPs`（同一作品根目录）。
**回测**：文件夹 1 默认预览 25 条全 ok（`Ruri no Houseki - NCED/NCOP` 不变）；文件夹 2 默认 105 条全 ok（`Menu1…16 / CM1…10` 仍与修复前一致）；换成带 `[v{version}]` 的特典模板时得到 `Menu1[v1] / Menu1[v2] … OVA24.5` 且无重名；换成带 `{resolution}` 时得到 `CM1 [1080p] …`。离线回归 27/27 通过。真实数据只做扫描与预览，未执行任何改名或移动。

---

## 13.16 第 13 步：来源季变量 / 特典记忆 / 视频落点 / 标记时序 / 执行提速 / OVA 撞名

> 目标：让同一作品前后季的同名特典（`CM - 01` 与 `S2 CM - 01`）能分开；特典目录归属确认一次就记住；特典文件夹里的视频有明确落点；`.ignore` 不再提前写；执行不再一个文件几百毫秒；OVA 只在撞名时才编号。

- **来源季 / 来源目录变量**：`parser.parse_file` 先看文件名里的 `S2` / `Season 2`，没有再取所在目录的季号，写入 `fields["source_season"]`（形如 `S2`，判断不出就是空）；`fields["source_dir"]` 为视频所在的最深一级有意义目录。两者都在 `fields.FIELD_REGISTRY` 里注册，模板留空时由 `template._cleanup` 自动折叠，不留 `[]` / 空括号。内置默认特典模板改为 `{series} - {special_type}{special_no} {source_season}[v{version}]`（只影响新装 / 重置，现有 `presets.json` 不动）。
- **特典目录确认记忆**：新增 `config/special_memory.json`（`store.list_special_memory / remember_special_folder / delete_special_memory`，按 `group_id + rel_path` 去重）。`/identify/special` 确认归属时顺手记住，选「保持原地」时删除记忆；新扫描、重扫、打开匹配页都会 `identify.apply_special_memory` 自动套用。接口：`GET /identify/special-memory`、`DELETE /identify/special-memory/{id}`；前端在「匹配作品 → 特典」表格下方展示「已记住的特典归属」，可删除。
- **特典文件夹里的视频落点**：新设置 `execution.special_video_policy`（`root` 默认 / `season`），设置页显示为 A / B 两个中文选项（`SettingSpec.option_labels`）。A：视频放作品根目录、保留原文件名；B：进 `Season 00` 并按特典模板改名。扫描时 `scanner._collect_ignored_videos` 只收集“被整体跳过的特典目录”里的视频（`row["special_folder"]`），未跳过的同名目录（如 `EXTRA`）仍按普通视频处理，保证原有命名不变。
- **`.ignore` 写入时序**：扫描阶段只记录 `ignored_dirs`，不再写标记（`ctx.add_ignored` 不再落盘）。执行完成后由 `operations._write_move_markers` 在目标目录补写 `.ignore` / `.nomedia`；策略选 B 或关闭 `parse.write_ignore_markers` 时不写。源目录不再出现空标记文件。
- **执行提速（实测证据见报告）**：慢的原因不是网盘参数（默认批 100 / 间隔 0 / 重试 0），而是三件事叠加——① 每个文件写库 3 次（执行前进度、条目状态、执行后进度）且每次新建连接跑 PRAGMA；② 每个文件重复 `realpath` 十几次（Windows 挂载每次约 11ms）；③ 目标目录每个文件都重新 `iterdir` 找同名。
  改法：`store._connect` 开 WAL + `synchronous=NORMAL`（提交 16ms → 2ms）、`update_operation_item_progress` 把条目状态和进度合成一次提交并复用一条长连接（`store.open_connection`）、`executor.DirIndex` 按目录缓存文件名（移动后同步增删）、`build_group_map` 只解析一次源/目标根目录（无软链时用字符串前缀判断，完全不访问磁盘）、`cleanup_done_sources` 用 `os.walk(topdown=False)` + `scandir` 清理空目录。文件夹 4 实测：每文件 285ms → 31.7ms。
- **OVA 撞名**：`preview.build_preview` 的重名兜底只处理真正撞名的特典组，按 `rel_path` 顺序编号；模板含 `{special_no}` 时覆写序号，否则在文件名后追加编号。没有撞名的 OVA 命名完全不改、不重排。

**验证记录**：文件夹 4 模拟三季 OVA 同名 → `OVA1 / OVA2 / OVA3`（路径序，非季号序），无重复目标；不模拟时三条 OVA 仍是原名。`CM - 01` 与 `S2 CM - 01` 在带 `{source_season}` 的模板下分别得到 `CM1 S1` / `CM1 S2`，不带该变量时退回 `CM1 / CM2`。A / B 两种落点与标记文件时序在临时目录实测通过（A：目标 `CDs` 内有 `.ignore/.nomedia`、源目录已清空；B：视频进 `Season 00` 且无标记）。离线回归 27/27 通过；文件夹 1 / 2 / 3 回测行数与命名未变。

---

## 13.17 第 14 步：EXTRA 特典目录 / 来源季在前 / OVA 季号序 / AI 季号与特典建议 / 一键确认

> 目标：`EXTRA` 也算特典目录；内置特典模板统一「来源季在前」；OVA 撞名按季号而不是路径编号；季号与特典归属都能手动叫 AI 给建议；「已指派待确认」可以一键确认。

- **EXTRA 加入默认特典目录**：`config.NEW_SPECIAL_DIR_DEFAULT` 在旧默认后追加 `EXTRA`（`Extras` 本来就有，匹配不区分大小写）。`load_settings` 只在存量值**完全等于**旧默认时才升级，用户自己改过的列表不动。命中后整目录按「忽略的特典目录」处理：视频与其它文件都随目录走，A / B 语义以 13.18 为准。
- **内置特典模板改为来源季在前**：`store.DEFAULT_TEMPLATES["special"]` 及 jellyfin / technical 两个内置预设都改为 `{series} - {source_season} {special_type}{special_no}[v{version}]`（technical 保留 `[group]` 前缀与 `[{resolution}]`）。`{source_season}` / `{version}` 为空时由 `template._cleanup` 折叠，不会留下孤立分隔符或空括号。现有 `presets.json` 不迁移、不覆盖；想沿用旧格式，把特典模板改回 `{series} - {special_type}{special_no} [v{version}]` 即可。
- **OVA 撞名按季号排序**：`preview._special_season_order` 取「用户在匹配页为该作品选定的季号」→「文件自身季号」→ 未知。`build_preview` 的重名兜底排序键改为 `(季号, rel_path)`，同一季排完再进下一季；只处理真正撞名的组，没撞名的不重排。用户没选季号时仍退回路径序（与旧行为一致）。
- **季号「推荐」的依据（现状说明）**：`routes_match._season_recommendation` 只用「扫描到的最大集号」和 TMDB 每季的「集数」：在所有 `episode_count >= 最大集号` 的季里，选 `(集数, 季号)` 最小的那一季；仅当 TMDB 有 ≥2 季且用户还没选季时显示，只写进下拉框的提示文字，不会自动应用。明显局限：不看季名 / 目录名，两季集数相同必然推荐小季号，缺集或跨季连续编号时可能推荐错或给不出推荐。
- **AI 推荐季号**：`ai.suggest_season` + `POST /ai/season/suggest`（前端「季」列的「AI 推荐季号」按钮）。只发送作品名、年份、季列表（季号 / 季名 / 集数）和目录名 / 文件名；返回的季号必须在 TMDB 季列表里，否则忽略并给出中文提示；多候选 / 无法判断分别给提示。结果只在界面上显示，点「采用」才调用原有 `match/season` 写回。
- **AI 特典归属建议**：`ai.suggest_special` + `POST /ai/special/suggest`（特典候选页的「AI 判断归属」按钮）。复用 `identify.special_suggestions` 的目录与作品列表，只发目录名 / 示例文件名；返回的 `folder_key` / `work_id` 必须存在于本次扫描，否则丢弃。结果只是预选下拉框，仍需点「确认归属」。AI 不可用 / 未开启时按钮禁用并显示原因，原有规则候选完全不受影响。`providers.ai_special_enabled` 从占位开关变为真实开关（v3 升级时自动置为 True）。
- **一键全部确认**：`identify.confirm_all` + `POST /identify/confirm-all`，前端「已指派待确认」页签的「全部确认（N）」按钮，二次确认弹窗提示数量，成功后提示实际确认条数并一次写回扫描记录。

**验证记录**：离线回归当时 32/32 通过（EXTRA 默认值、模板折叠、OVA 季号序、一键确认 4 项）。其中文件夹 2 的 A 语义当时实现为「视频挑到作品根目录」，该结论已被 13.18 修正并取代；以 13.18 的 38/38 与文件夹 2 整目录搬运结果为准。

---

## 13.18 第 15 步：特典 A 语义修正 / 标记落点 / `[v{version}]` 加回内置

> 目标：选项 A 改为「整个特典文件夹跟着作品搬走，文件夹名和内部结构不变，视频/字幕一律不改名」；`.ignore` 写在搬过去之后的特典文件夹里；内置特典模板把 `[v{version}]` 加回。

- **A 语义修正**：`preview._build_special_folder_video` 只生成预览落点（`action=skip`、`new_path=作品目录/特典目录名/内部相对路径/原名`），实际搬运由「特典文件夹」那一行（`category=folder`）整目录完成；落点优先对齐正片实际去的作品目录（`work_roots_by_work`），特典目录未确认时整个目录保持原地。`extracted_by_folder` 只在 B 模式收集视频；作品根目录投票、重复集检测均排除 A 模式特典视频，避免把 `EXTRA` 误当作品根目录或把特典同集号误报冲突。压缩包 / 图片 / md5 不再单独判断，随目录一起走。
- **标记落点修正**：`operations._write_move_markers` 改为从目标文件向上取「最外层连续的特典目录名层级」，在该目录写 `.ignore` / `.nomedia`（例：`作品/EXTRA/CDs/x.zip` → 写 `作品/EXTRA`）。先搬完再写；B 模式或 `parse.write_ignore_markers` 关闭时不写。
- **根因（文件夹 2 没有 `.ignore`）**：旧 A 只把视频单独搬到作品根目录，特典目录本身没有搬；`_write_move_markers` 又只看每行目标路径的**直接父目录**是不是特典目录名。视频落到作品根目录后，父目录不是 `EXTRA`，所以永远找不到要写标记的文件夹。A 改整目录搬 + 向上找特典层级后，确认归属即会在目标 `EXTRA` 内写标记。
- **模板**：`store.DEFAULT_TEMPLATES["special"]` = `{series} - {source_season} {special_type}{special_no}[v{version}]`；jellyfin / technical 内置预设同步加回 `[v{version}]`。`{version}` 为空时由 `template._cleanup` 折叠，不留空括号。现有 `presets.json` 不动。

**验证记录**：离线回归 38/38 通过（新增 A 预览落点、B 进 Season 00、A/B/关闭三种标记行为共 6 项）。文件夹 2 从备份 `media.zip` 解出副本回测：61 个 `EXTRA` 视频预览为 `skip`、落点为 `作品/EXTRA/<原名>`；`EXTRA` 目录行整目录搬到 `Aldnoah.Zero/EXTRA`。临时副本实际执行后，目标 `EXTRA` 内有 61 个 mkv + 10 个 rar + `.ignore/.nomedia`（共 73 个文件），作品根目录不再有特典视频；OVA24.5 仍在 `Season 00` 按模板改名；`ScreenShot` 8 张图与 `.md5` 随作品移动。文件夹 1 / 3 命名不变；文件夹 4 三季各自的 `CDs/Scans/SPs` 在 A 模式下都指向同一个作品目录，预览提示「多个文件映射到同一目标路径」（已在 13.19 处理）。真实数据只扫描 / 预览；执行验证全部在临时副本完成并已删除。

---

## 13.19 第 16 步：多季同名素材文件夹（CDs/Scans/SPs）撞名处理

> 目标：同一作品多季各带一份同名素材文件夹时，不再报「多个文件映射到同一目标路径」，而是按季号区分；也可选择合并。没撞名的保持原名不动。

- **新增设置**（`config.SETTING_SPECS`，设置页自动渲染）：
  - `execution.special_folder_conflict`：`season`（默认，加季号区分）/ `merge`（合并进同一个文件夹）。
  - `naming.special_folder_suffix`：默认 `{name} {source_season}`；支持 `{name}`、`{source_season}`（`S01`）、`{season}`（`1`）、`{season:02}`（`01`）。例：`{name} [{source_season}]` → `CDs [S01]`；`{source_season} {name}` → `S01 CDs`。
- **实现**：`preview._apply_folder_conflicts` 在生成 folder 行后运行，只对「同一目标路径出现 2 个及以上 folder 行」的组生效。
  - 季号来源：`season_state[work_id].choice`（匹配页选定季）→ 同一父目录下正片文件识别到的 `season` → 都没有时用 `S00` 兜底并提示手动确认；兜底后仍重名则自动追加 ` 2`、` 3`。
  - 合并模式：目标路径不变，提示「与其他季的同名素材文件夹合并到同一目录」。
  - 重复路径检测现在跳过 `category == "folder"` 的行，folder 行之间同名不再算错误（合并模式需要）。
- **验证记录**：离线回归 43/43 通过（新增撞名 5 项：默认季号、自定义格式、合并、未撞名保持原名、季号未知兜底）。文件夹 4 模拟三季匹配到同一部作品（季号 1/2/3）：修前 8 个 folder 行都指向 `作品/CDs`、`作品/Scans`、`作品/SPs` 并报重复错误；修后 `CDs S01/S02/S03`、`Scans S01/S02`、`SPs S01/S02/S03`，汇总 `ok 162 / error 0 / skip 48`。合并模式同样 8 行全部 `ok`。自定义格式实测输出 `CDs [S01]`。文件夹 1 无 folder 行；文件夹 2 `EXTRA`、文件夹 3 `CDs/Scans/SPs` 均不撞名，名字和落点保持不变。

---

## 13.20 第 17 步：全角【】集号 / OP-ED 特典 / 作品名清洗

> 目标：中文压制常见的 `【001】【标题】【DVDrip】…` 这类全角写法能识别集号，片头片尾不再算「未知」，作品名不再残留空括号。

- **新增内置规则**（`core/rules.py` 的 `BUILTIN_RULES`）：
  - `fn-cn-bracket-episode`（优先级 31）：`^\s*【\s*(?P<episode>\d{1,3})(?:v(?P<version>\d))?\s*】`。**必须出现在文件名开头**，所以 `【2013】`（4 位）和标题中间的 `【2】` 都不会命中。
  - `fn-cn-op-ed`（优先级 11）：`(?i)【\s*(?P<special_type>OP|ED)(?:[-\s]?(?P<special_no>\d{1,2}))?\s*】`。`【OP】`、`【OP01】`、`【Disc-41】【ED】` 都归为特典。
- **规则表迁移**（`store._append_missing_builtin_rules`）：`_ensure_rules()` 发现 rules.json 已存在时，会把新增的内置规则补进去；用户改过的旧规则保持原样，不覆盖。
- **作品名清洗**（`parser.clean_dir_title`）：新增 `CN_BRACKET_RE = 【([^【】]*)】`，和半角括号走同一套判断——空内容、技术标签、年份整段丢弃；`【游戏的继承者】` 这类真实文字保留。`techname._TAG_TOKEN_RE` 补了 `hi10p / hi422p / hi444pp`，`【x264 Hi10p AAC】` 才能整段被认成技术标签。
- **验证记录**：离线回归 49/49 通过（新增 6 项：开头的 `【001】` 是第 1 集、`【2013】` 不是集号、标题中间 `【2】` 不是集号、`【OP】` 归特典、`【OP01】` 带序号、作品名清洗后无空括号）。
  - 文件夹 5（319 个文件）：180 个正片 `S01E01`～`S01E180` 连续无缺号，94 个 OP/ED 全部归为特典且文件名唯一（47 OP + 47 ED），45 张 JPG 仍不进视频列表，汇总 `ok 319 / error 0`，无重复目标。作品名从 `【晨曦制作】【游戏王★决斗怪兽GX】【 】【x264 Hi10p AAC】【MP4】` 变成 `晨曦制作 游戏王★决斗怪兽GX`。
  - 文件夹 1～4：把新规则和全角清洗临时关掉跑同一批数据，逐行对比 `new_path / new_name / status / messages` **完全一致**（1: 25 ok；2: 34 ok / 62 skip；3: 40 ok / 21 skip；4: 154 ok / 56 skip）。
- **已知遗留**：`【晨曦制作】` 是制作组不是技术标签，按当前约定保留在作品名里；如需去掉，应单独加「制作组 / 字幕组」识别，而不是塞进技术标签列表。

---

## 13.21 第 18 步（1/2）：作品锚点 —— 别名升级为「写法 → TMDB 编号」

> 目标：同一部作品在不同批次、不同目录里都复用同一个 TMDB 编号，不再靠 AI 重新猜名字。本步只做后端数据与匹配链路，界面文案后续再补。

- **数据**（`store._normalize_alias`）：别名新增 `tmdb_id`、`media_type`（`tv`/`movie`）字段；旧别名读出来自动补空值，**不迁移数据**，旧别名继续按手动记录使用。`update_alias` 透传这两个字段，改名不会丢编号。
- **写入**（`routes_match._remember_anchor`）：`_apply_meta` 匹配成功后，把该作品组的「名称写法」（`identify.segment` + 组标题，最多 5 个）写成别名，带上 `tmdb_id` / `media_type` / TMDB 简体名 / 年份。同一写法重复匹配只更新，不新增。
- **复用**（`routes_match._anchor_for_group`）：`/match/auto` 先查锚点，命中就直接按编号读取 TMDB（`status="anchor"`，提示「这是你之前确认过的作品，已直接用它的 TMDB 编号，没有重新搜索」），**跳过搜索**；写入/读取都失败时自动落回原来的搜索流程。
- **冲突**：同一作品组的多种写法指向不同 `tmdb_id` 时视为冲突，`_anchor_for_group` 返回 `None`，不自动使用。
- **展示**：`_decorate_groups` 给每个组附 `anchor`（`tmdb_id` / `media_type` / `name`），前端后续可直接显示「已记住」。
- **不改的东西**：`work_id` 格式保持 `w:组:标题|年份` 不变，历史执行记录与撤销记录不受影响；本步不设置 `fields.tmdb_id`，避免出现「已匹配但缺集标题」的半成品状态。
- **验证记录**：离线回归 49/49 通过；临时配置目录实测 5 项——旧别名可读且不当锚点、匹配后写入 2 条锚点、重复写入不新增、两种写法指向不同作品判冲突、`update_alias` 保留编号。文件夹 1～5 预览与改动前完全一致（25 / 34+62 / 40+21 / 154+56 / 319 全正常，作品名未因锚点变化，因为当前配置里没有别名）。

---

## 13.22 第 18 步（2/2）：完整的作品名锚定流程

> 目标：本地能定的不让 AI 插手；AI 只能给「搜索关键词」或「从候选里挑编号」，作品名永远取 TMDB 返回值。

- **语言回退**（`config.metadata.name_language_order` + `metadata.resolve_display_name`）：设置项可选 3 种顺序，默认「简体 → 繁体 → 日文原名 → 英文」。判定「TMDB 没有简体」= 简体名为空，或与原名完全相同；回退时逐档请求 TMDB 详情，空名或等于原名就跳过。
- **名称来源**：`_apply_meta` 抓完详情后统一走 `resolve_display_name`，把 `name_source` 写进字段；`matcher.build_groups` 汇总到 `group.match.name_source`，接口另附中文 `name_source_label`（简体中文 / 繁体中文 / 日文原名 / 英文）。
- **手动改名**（`POST /match/rename`）：只改 `fields.series` 并写 `name_source="manual"`，TMDB 编号、季号、`work_id` 都不动；换季（`/match/season`）遇到手动名会保留，不被 TMDB 覆盖。
- **AI 兜底**（`ai.ask_query` / `ai.pick_candidate`）：只在 `/match/auto` 用本地规则 `auto_pick` 卡住时调用。搜不到 → 让 AI 换关键词，最多 2 次；多个候选分数接近 → 把候选列表交给 AI，只允许返回候选里的 `id`，选了不存在的编号整条丢弃。一次自动匹配最多问 AI 的次数 = `providers.ai_batch_limit`。
- **四种失败提示**（都不猜）：候选为空「没有搜到结果，请手动搜索」；AI 不存在的编号「已丢弃这条结果」；AI 说不确定「请手动选择」；多候选接近且 AI 也没定「请手动选择」。自动套用 AI 挑选的结果时标 `status="ai_pick"`，界面提示「请核对」。
- **界面**：作品列显示「元数据名称」+「名称来源」标签；操作列新增「手动改名」（仅已匹配作品），弹窗只改名字。
- **不改的东西**：`work_id` 格式 `w:组:标题|年份` 保持不变；AI 未配置或额度用完时，行为与改动前完全一致（不会报错）。
- **验证记录**：离线回归 60/60 通过（新增 11 条：语言顺序、简体缺失判定、繁/日/英回退、名称来源入字段、AI 只回关键词、AI 编号校验/不确定/合法编号）。接口级测试用临时配置扫描 `/media/4`：设置项默认值正确、`/match/auto` 返回名称来源、`/match/rename` 更新 29 个文件且来源变「手动改名」、预览正常。
- **回测（只扫描 / 预览，未执行改名）**：文件夹 1（2 部作品，自动 2）作品名由罗马音变简体；文件夹 2（1 部，待选择，无变化）；文件夹 3（2 部，自动 2，其中两组指向同一部作品，预览 40 正常 / 21 跳过）；文件夹 4（3 部全部自动匹配到同一部作品 `#65676`，作品名统一成简体，预览出现 24 条「重复集」错误，需用户在重复集面板选择保留哪一版）；文件夹 5（1 部未搜到，无变化）。
- **已知限制**：文件夹 4 的三个子目录其实是同一部作品的不同季，识别阶段都记为第 1 季，匹配到同一 TMDB 编号后集号撞车，被重复集检测拦下。这是既有的季号识别问题，不是名称锚定引入的；后续需要在匹配页手动指定季号，或让识别从目录名推断季号。

---

## 13.23 第 19 步：多组指向同一部作品时分配季号

> 目标：多组匹配到同一个 TMDB 编号时，先按目录名/文件名里的季号线索分配；没有线索绝不按目录顺序硬排，交给 AI 建议或用户手动选择。

- **后端**（`core/season_plan.py` + `POST /match/season-plan`，只读）：
  - `detect_clue` 先找明确季号 `Season 2` / `S02` / `第二季` / `2nd Season`，再找续作标记 `Zoku`（第 2 部）、`Kan` / `Final Season`（第 3 部），按 TMDB 正式季列表的顺序对应；`S02E01`、`Kanon` 不会误判。
  - `build_season_plan` 按 TMDB 编号聚合作品组；每组输出 `action`（`assign` / `keep_manual` / `unresolved` / `invalid_clue` / `conflict`）、建议季号、线索和中文原因；同时返回修前/修后撞车清单（季 + 集 + 哪几组）。
  - 线索指向 TMDB 不存在的季 → `invalid_clue`，不采用；两组抢同一季 → 双方都变 `conflict`，不自动分配；`mode="manual"` 的组固定 `keep_manual`，不会被覆盖。
- **前端**（`MatchPage.tsx`）：同一 TMDB 编号出现多组时，顶部显示「这 N 组指向同一部作品……」提示和「分配季号」按钮；弹窗逐组显示当前季 / 建议季 / 依据，非手动组可选季（清空 = 保持不变），底部「用 AI 判断 / 取消 / 确认分配」，并实时列出按当前选择仍会撞车的「第 X 季第 Y 集：哪几组」。
- **AI**：复用 `POST /ai/season/suggest`（只建议、不生效）；AI 返回季号必须在 TMDB 季列表里，越界整条丢弃并给中文提示；未配置 AI 时按钮禁用，只能手动选择。
- **验证记录**：离线回归 74/74 通过（新增 13 条：线索识别、Zoku/Kan 排序、无线索不硬排、手动不覆盖、非法线索丢弃、两组抢季冲突、修前撞车 / 修后归零）。
- **文件夹 4 实测（临时配置副本，只预览未执行）**：3 组都指向 `#65676`，目录名线索识别到 `Zoku → 第 2 季`、`Kan → 第 3 季`，第一组没有线索（提示保持当前季，可用 AI 判断或手动选择）。把副本还原成「三组都未选季」后，预览有 116 行「多个文件映射到同一目标路径」（正片 13 个集位三组互撞，字幕和特典跟随）；按线索分配 S2/S3 后预览错误 0、撞车清单为空。样例：`Season 01/… - S01E01`、`Season 02/… - S02E01`、`Season 03/… - S03E01`。
- **手动保护实测**：当前真实扫描记录里三组已是 `mode="manual"`，计划把它们全部标为 `keep_manual`（「你已经手动设为第 X 季，不会被覆盖」），预览错误 0。
- **回测（只扫描 / 预览）**：文件夹 1（25 文件，0 错）、2（95 文件，0 错）、3（58 文件，0 错）、5（319 文件，0 错），重复集组都是 0，与改动前一致。

---

## 13.24 第 20 步：识别词典收口（重构，行为零变化）

> 目标：同一个“名字 / 写法”只在一个文件里定义，parser / identify / duplicates / operations / preview / config / rules 都从 `core/lexicon.py` 取。

- **唯一来源**（`backend/app/core/lexicon.py`，只放数据与纯函数，不导入其它应用模块）：
  - 特典目录名：`SPECIAL_DIR_TYPES`、`OVA_DIR_NAMES`、`BUILTIN_GENERIC_DIRS`、`DIR_SPECIAL_RULE_NAMES`，以及两个设置默认字符串 `DEFAULT_SPECIAL_DIR_NAMES` / `DEFAULT_GENERIC_DIR_NAMES`；`special_dir_name_set()` 统一“用户设置 + 内置词典”的合并方式。
  - 季目录写法：`SEASON_DIR_NAME_RE`（识别：Season 2 / S02 / 第一季）、`SEASON_FOLDER_NAME_RE`（预览：Season 00 / S01）、`SPECIAL_SEASON_FOLDER_NAMES`、`DEFAULT_SPECIALS_DIR_NAME`。
  - 版本号：`VERSION_MARK_RE`（identify 清洗 + duplicates 区分多版本共用）、`TECH_VERSION_RE`（techname 提取 `{version}`）。
  - 集号清理：`EPISODE_MARKER_PATTERNS`（identify 从文件名清洗作品名）。
- **故意保留两套、没有强行合并**：
  - 预览的季目录正则只认 `Season xx` / `Sxx`，识别的还认 `S1` / `第一季`；合并会改变“作品根目录”的判定边界。
  - `TECH_VERSION_RE` 比 `VERSION_MARK_RE` 宽（认 `version 2` / `ver.2`，且拒绝 `v12`），合并会改变 `v12` 这类边界。
  - `rules.py` 的集号规则表是用户可编辑、持久化的数据，identify 的清洗写法是内部固定启发式；合并会让“改规则”连带影响作品名清洗。
- **规则表兼容**：`dir-special` 的内置正则改为 `DIR_SPECIAL_RULE_NAMES` 拼接，生成结果与旧字符串逐字节相同；已存在用户的 `rules.json` 不迁移、不重写。
- **验证记录**：分 3 小步替换，每步跑离线回归均 74/74；改动前后对 `/media/1..5` 做扫描 + 识别 + 预览快照逐行对比（预览 25/96/61/210/319 行，识别 25/86/31/89/274 条，含新路径、新文件名、状态、提示、忽略目录、季号计划），**差异 0**；设置项默认值字符串逐字一致。

---

## 13.25 第 21 步：补预设快照漏项

> 目标：预设快照要真的覆盖“会改变最终结果”的工作流参数，切换预设时通过差异确认一起应用。

- **新增进 `PRESET_OPTION_KEYS`**（[config.py](file:///c:/Trae/ani-rename/backend/app/config.py)）：
  `special_video_policy`、`special_folder_conflict`、`special_folder_suffix`、`batch_size`、`batch_delay_seconds`、`retry_times`、`retry_backoff_seconds`。
  前两项与批处理参数此前只存在全局设置里，换预设不会跟着变；`naming.special_folder_suffix` 同属命名结果参数，一并补上。
- **兼容旧预设**：`_normalize_options` 对缺失字段按默认值补全，但只在内存里生效、不改写旧 `presets.json`；因此旧预设可正常读取、导出、应用，切到旧预设时新增字段会以默认值出现在差异弹窗里，用户确认后才写入全局设置。
- **没有进预设、以及原因**：
  - `providers.proxy_url` / `tmdb_api_key` / `bangumi_token` / `ai_*`：机器或账号级凭据，不应随预设切换。
  - `metadata.name_language_order`：全局语言偏好，跨作品复用。
  - `parse.max_files`：扫描安全上限，防止一次性扫爆内存。
  - `parse.ignore_marker_files`：目录卫生用的标记文件名，跟命名方案无关。
  - `parse.generic_dir_names` / `parse.min_title_length`：识别口径参数；换预设不应改变识别边界（如后续确需随预设走，再加并同步说明）。
  - `scraper.enabled`：预留未生效。
- **验证记录**：离线回归 77/77（新增 3 条：快照包含、切换还原、旧预设不报错且不覆盖）；临时目录实测“保存预设 → 改设置 → 切回预设”后 7 项全部还原；文件夹 1～5 扫描/预览行数与识别条数不变（25/96/61/210/319；识别 25/86/31/89/274），错误 0。

---

## 13.26 第 22 步：AI 命名规律 + 本地执行（8 步方案落地）

> 一句话：遇到内置规则解释不了的目录形态时，让 AI 只回答「文件名由哪几段组成」，
> 本地把它编译成取值逻辑、跑 5 项自检、生成样例预览；**用户点确认才写入识别结果**。
> AI 永远不直接产出正则，也不直接产出文件名。
>
> 为什么这么定：AI 直接写正则会不可审计、无法自检、出错也说不清错在哪；
> 只让它做“分段归类”，规则就能被本地代码复现、校验、逐条展示依据，
> 出问题时能明确指出“哪一段被判成了什么”，而不是给用户一个黑盒结果。

### 13.26.0 定位：内置规则是主力，AI 只是手动试用（方案 B 收口）

**内置规则是主力，AI 命名规律只在用户手动点击、且内置给不出可用结果时作为补充。**
它不是默认路径，不参与“先跑一遍看看”，也不因为缓存存在就替用户改数据；
自动套用仍受 13.26.6 的五个前提约束，任何一个不满足都只展示候选。

**不再主动扩展它的词汇。** 新增 `role` / `container` / `match` / `take` 取值必须有用户的明确批准，
并附带真实目录的失败证据；没有批准就不加。理由来自这几次实测：

- **文件夹 5（274 条）两次整批放弃**：第一次 4 条规则的 `take` 写成「第二个」（不在词表内）；
  补问后改成「第 2 个」，又把「特典类型」拆成两条规则，被“同一角色只能出现一次”拒绝。
- **Ruri 一次给出错误答案**：AI 的分段与实际文件名对不上，被 5 项自检拦下，整批放弃，没有写入。
- **补「数字带字」+「特典类型取括号原文」两样词汇后仍卡在碟号**：
  `Disc-01` 被拆成 `Disc` + `01`，正片规则把 `01` 当集号，出现重复集号 1、10、11…；
  这是「碟号与集号同形」的产品问题，不是再加一个词能解决的。
- 两次补问里 AI 都自己写错格式（`take` 用中文序数、同角色写两条），说明继续扩词只会增加
  校验面和解释成本，收益却仍受真实文件形态限制。

因此：界面把它标成可选入口；文档把词汇表冻结在当前范围；真正的识别仍以内置规则为准。

### 13.26.1 结构描述：AI 只输出四个字段

一条结构描述由若干条规律组成，每条规律只能说四件事（`backend/app/core/pattern.py`）：

| 字段 | 可选值 | 含义 |
|---|---|---|
| `role` | 集号 / 季号 / 作品名段 / 特典类型 / 特典序号 / 版本 / 区间 / 忽略 | 这一段是什么 |
| `container` | `【】` / `[]` / `()` / 无 | 它被哪种括号包着 |
| `match` | 纯数字 / 数字+v2 / 小数 / 区间 / 纯文字 / 技术标签 / 数字带字 | 它长什么样 |
| `take` | 第一个 / 最后一个 / 第 N 个 / 其余全部 | 同类候选里取哪个 |

`validate_structure` 的硬边界（都是为了让 AI 不可能“夹带”别的东西）：

- 只允许这四个 key，出现任何其它字段直接报错（例如想写 `regex` / `filename` 会被拒绝并提示原因）；
- 字段值里出现 `\d`、`.*`、`(?` 这类正则痕迹，直接拒绝；
- 角色与匹配方式必须搭配（如「区间」不能当集号，「版本」只能用「数字+v2」）；
- 「其余全部」只能给「忽略」用，否则一个角色会拿到多个值；
- 同一个非忽略角色只能出现一次，重复会提示“无法确定用哪个”；
- 至少要有一条「集号 / 特典序号 / 区间」，否则等于什么都没识别；
- 最多 30 条规律。

`compile_structure` 只做整理和复检；`apply_structure` 负责真正的取值，
返回 `values`（识别值）、`evidence`（每一段的判断依据，用于界面展示）、
`missing`（没取到的角色）、`suspects`（例如“这一段里含 Disc/Vol 字样，可能不是集号”）。
`41v2` 这种写法在「数字+v2」下会同时得到 `episode=41`、`version=2`，不会变成 412。

补充两条已经保留、但不再继续扩展的取值：

- **数字带字**：字母与数字直接相邻的一整段（`ED01` / `OP02` / `NCOP01` / `SP02` / `CM03`）。
  只允许 `special_type` 取文字部分（`ED`）、`special_no` 取数字部分（`01`），不能当集号；
- **特典类型取括号原文**：`special_type` 除 `纯文字` 外，也可以配 `技术标签` 或 `数字带字`，
  值是文件名里真实存在的 token，不允许 AI 编造类型名。

这两条不改变内置识别，也不代表后续还会继续扩词（见 13.26.0）。

### 13.26.2 为什么按「目录组 + 子目录」触发

同一部作品的不同季、不同 Disc 目录，文件名形态常常不一样；按整部作品套一条规律会互相打架。
所以触发单位固定为「一个目录组里的一个子目录」，一次只分析和应用这一层。
指纹也因此包含四项：`group_id | dir_rel | shape | ext`，其中
`shape` 是把文件名里的字母换成 `A`、数字换成 `N` 后的形状（`Bleach - 41v2.mkv` → `A - NvN`）。

### 13.26.3 按形状抽样 + 全量复核（`backend/app/core/pattern_sample.py`）

目录条数超过 `providers.ai_batch_limit` 时，不再直接拒绝，而是先本地抽样，再让规律在**全部**文件上复核：

- 形状只看文件名，不看路径：括号种类与顺序 + 每段内容类型（纯数字 / 数字带字 / 纯文字 /
  技术标签 / 版本 / 区间 / 小数），再追加 `V/R/D` 标记；`shape_signature` 是唯一入口。
- 出现次数 ≤ 2 的稀有形状**全部**带上；每个桶至少 1 条，优先首条与末条；
  还有余量就按“中间向两侧”均匀补样，轮询各个常见形状。
- 超出上限时先保稀有形状，再削常见形状；每个桶的 `total / sent / dropped` 与样例都写进
  `sampling.trimmed`，结果里的 `note` 直接说明“全量多少、实际发多少、削了什么”。
- `propose_with_retry` 的第二次请求只带**全量复核对不上的文件**（最多一个上限），
  而不是把同一批样本再发一遍；全量复核不过就整批放弃，绝不出现部分文件用新规律。

### 13.26.4 5 项自检（`backend/app/core/pattern_check.py`）

只有 5 项全过，才会形成「可应用」方案：

1. **匹配率 100%**：目录里每个视频都能被结构描述解释；
2. **编号数量等于视频数**：每个视频都归入正片 / 特典 / 合集之一，不重复计数（剧场版不参与）；
3. **无缺号无重号**：集号按真实数值比较，24 和 24.5 是两集；
4. **特典 / 合集未被误当正片**：文件名或目录里有 SP/OVA/NCOP/OP-ED/区间等线索时重点检查；
5. **套模板后目标路径无重复**：用深拷贝模拟一遍预览，只比较这个目录的目标路径。

为什么用“模拟”而不是直接写：自检必须能失败得干干净净。
模拟只作用于 `copy.deepcopy` 出来的行，自检失败时扫描结果一个字段都不会变，
因此不存在“一半用 AI 规律、一半用旧规则”的半套状态。

### 13.26.5 失败只重问一次，仍不过整批放弃

`propose_with_retry` 第一次问 AI → 本地编译 + 5 项自检；不通过时**只把“结构描述 + 对不上的文件 + 错误说明”**
再问一次，而不是整目录重来。第二次仍不通过就返回 `abandoned`：

- 不写缓存；
- 不改识别字段；
- 全部继续按内置规则处理。

为什么只重问一次：重试是有成本的（时间、额度），而且大多数失败是“AI 没看懂文件形态”，
第二次带上具体反例通常就够；连续两次不过说明这条目录不适合交给 AI，继续重试只会烧钱。

### 13.26.6 缓存与自动套用（`backend/app/core/pattern_cache.py`）

- 缓存文件：`config/pattern_cache.json`（不是 SQLite，方便用户直接查看/备份）。
- 条目来源三种：`ai`（AI 首次生成）、`manual`（用户手动改过）、`cache_hit`（从别的指纹命中后写入）。
- **自动套用的五个前提**（缺一不可）：
  1. 设置里 `providers.ai_pattern_cache_auto_apply` 打开（默认打开）；
  2. 目录里**每个**视频的指纹都能在缓存里找到；
  3. 命中的条目结构完全一致（归一化 JSON 比较）；
  4. 5 项自检通过；
  5. 与内置识别没有“内置已有值且不同”的冲突（内置空值不算冲突）。
- 任何一条不满足，都只把命中的规律作为**候选**（`pattern_candidate`）展示，
  不写识别字段；结果状态会区分 `skipped` / `candidate_only` / `rejected` / `applied`，
  每种都带中文 `reason`，不会静默跳过。
- 目录里有剧场版时直接跳过自动套用，避免把剧场版改成正片。
- 自动套用写入后，候选上会标记来源为「AI 规律（缓存命中）」，并累加命中次数。
- 扫描/重扫结束时由 `auto_apply_scan` 逐个目录尝试；缓存为空或开关关闭时**直接返回**，
  保证没配 AI 时扫描结果与旧版本完全一致。

为什么指纹要带 `group_id` 和 `dir_rel`：同名文件在不同目录组可能有完全不同的含义
（特典目录与正片目录、不同季的同名 OP）。宁可漏命中，也不要跨目录误套用。

### 13.26.7 三个核心接口（都在 `backend/app/api/routes_ai.py`）

| 接口 | 作用 | 关键约束 |
|---|---|---|
| `POST /api/ai/patterns/propose` | 让 AI 提规律 | 只发文件名与目录名，不发媒体内容；只读扫描结果 |
| `POST /api/ai/patterns/apply` | 用户确认后写入 | 写入前**再跑一次 5 项自检**；通过后 `refresh_queue` + `save_scan` |
| `POST /api/ai/patterns/decision` | 记录采用 / 不采用 / 稍后再说 | 只记录取舍，不删除缓存；“不采用”只影响列表提醒 |

配套管理接口：`GET /api/ai/patterns`（列表 + 自动套用开关状态 + 取舍记录）、
`PATCH /api/ai/patterns/{id}`（启用/停用单条）、`DELETE /api/ai/patterns/{id}`、
`POST /api/ai/patterns/clear`（清空全部）。

为什么 `apply` 要再自检一次：提案和确认之间，扫描结果可能被别的操作改过（重新识别、手工指派）；
再自检一次保证“写入的一定是当下自检通过的”，避免拿旧快照覆盖新状态。

### 13.26.8 前端入口（手动触发，不静默）

- **匹配作品页**（`frontend/src/pages/MatchPage.tsx`）：底部「AI 命名规律」区域按
  「目录组 + 子目录」列出可分析目录，显示视频数/剧场版数/状态标签：
  `未分析` / `可应用` / `只能当候选` / `已放弃` / `已套用缓存` / `已应用`。
- 入口卡片文案写明「内置规则优先，AI 只在手动分析时才用」，分析按钮用普通按钮而不是主按钮，
  只做弱化、不删除也不隐藏；用户随时可以改主意回来点。
- 点「分析」→ 弹窗展示：5 项自检结果、样例行（原名 → 新名、AI 判断依据、可疑点、
  内置值与 AI 值的差异）、三个取舍按钮「采用 / 不采用 / 稍后再说」。
- 点「采用」不会立刻改数据，还会再弹「确认应用」；确认后只改识别结果，**不改文件名**。
- **设置页**（`SettingsPage.tsx`）：「命名规律缓存」卡片，含自动套用开关、
  条目列表（来源、指纹形状、命中次数、启用状态）、单条删除、清空全部。
- 所有按钮都是手动触发；没有任何“扫描时自动问 AI”的路径。

### 13.26.9 这次一起修的四个识别问题

**（1）特典编号跳变 → 按「作品 + 来源季 + 类型」统一编号**
旧逻辑在同一个特典目录里按碟片/路径累计，换一张碟就跳到 OP34、回头又变 OP10。
新规则（`parser.renumber_specials`）：分桶键是 `(group_id, 作品名, 年份, 来源季, 特典类型)`，
桶内从 1 连续编号；排序先看碟号（Disc-XX / DVD 3 / Vol.2），再看文件自带序号（OP02 → 2），
最后才按路径自然序。OP / ED / NCOP / NCED / SP / CM 各自一份计数器，互不消耗。
只有一条的特典保持原样（孤立 OVA 不会被硬加 `1`）；小数特典（24.5）不参与连续编号，
因为 24.5 是作品语义的一部分。

**（2）字幕组名混入作品名 → 只削“开头整段明显是机构名”的方括号**
`parser.strip_group_prefix` 的边界刻意保守：只处理名称最开头的第一个方括号；
整段不超过 8 个字、不含假名和数字；必须以「字幕组 / 汉化组 / 制作 / 工作室 / Raws / Subs」这类词结尾；
后面必须还有另一个像作品名的方括号内容，或方括号外还有有效文字；
后面紧跟季号或技术标签时不动；只有一个方括号时不动（那通常就是作品名）。
例：`【晨曦制作】【游戏王★决斗怪兽GX】...` → `游戏王★决斗怪兽GX`；
`【XX制作】`、`【XX制作】【第二季】` 不误切。
为什么这么保守：误削作品名比漏削字幕组更难发现，也更容易污染后续 TMDB 匹配。

**（3）版本后缀丢失（`41v2` 被当成 412）→ 解析出版本并接进模板**
`41v2` / `41v3` 识别为 `episode=41`、`fields.version=2/3`，不会拼成 412；
模板里可以用 `{version}`。默认正片模板已升级到 v6 带上 `[v{version}]`，详见 13.26.10。

**（4）四类预览提示（只提醒，不改名、不改状态）**
`preview._quality_warnings` 在预览末尾追加：

1. 同类特典编号重复；
2. 同类特典编号不连续；
3. 标题可能还留着「字幕组名 + 空格 + 作品名」的形态；
4. 识别到版本，但当前模板没有使用 `{version}`，新名字会丢失版本信息。

为什么不自动改：用户没有编程经验，静默改字段或改名风险太大；
这些提示把“哪里可能不对”摆出来，由用户决定是改模板、改识别，还是忽略继续。

### 13.26.10 正片模板 v6 与已有设置

默认正片模板（`store.DEFAULT_TEMPLATES["episode"]` 与 `preset-jellyfin`）现在是：

```
{series} - S{season:02}E{episode:02} [{resolution}] [v{version}]
```

依靠已有的“空值自动折叠”：没有版本号的文件，`[v{version}]` 整段消失，
不会留下空括号或双空格；所以普通文件输出不受影响。

**已有设置会不会自动更新：只会迁移“与旧内置写法完全一致”的模板。**
`PRESETS_VERSION` 从 5 升到 6，配合 `LEGACY_EPISODE_V6` 做一次性迁移：

- 如果 `config/presets.json` 里的正片模板**逐字等于** v5 内置写法
  `{series} - S{season:02}E{episode:02} [{resolution}]`，会自动替换成 v6；
- 如果用户自己改过（哪怕只差一个空格），**不会动**，因为无法判断用户是不是故意这么写；
- 旧版本 `presets.json` 没有 `version` 字段时走同一套初始化迁移。

**用户需要手动改的地方（两处任选）：**

1. 界面：设置 → 命名预设 → 选中当前预设 → 正片规则改成
   `{series} - S{season:02}E{episode:02} [{resolution}] [v{version}]` → 保存；
2. 文件：`config/presets.json` 里对应预设的 `templates.episode.pattern` 改成同一串，重启后生效。

对照（Bleach，文件夹 1）：`41v2` / `42v2` / `43v2` 三集输出为
`Bleach Sennen Kessen-hen - S01E41 [1080p] [v2].mp4`、`…S01E42…`、`…S01E43…`；
A/B 对比文件夹 1～5（v6 vs 去掉 `[v{version}]`）只有这 3 行不同，其余 0 差异。

### 13.26.11 待办清单（这次故意留着、以后要做的事）

> 写在这里是为了不让它们消失在对话里。都不是“忘了”，而是有明确取舍。

1. **AI 单次上限**：`providers.ai_batch_limit` 默认 10、可调范围 1～50（`pattern_ai.MAX_PATTERN_FILES=50`），
   结构描述最多 30 条规律。超过上限时使用「按形状抽样 + 全量复核」（见 13.26.3）：
   稀有形状全部保留，常见形状按首尾 + 均匀分布减量，发出去的条数、削掉的形状都写进结果；
   没有任何自动分批，复核仍然针对整个目录一次完成。
2. **文件夹 4 的特典没有被新编号规则覆盖**：该目录走的是「特典目录整目录保留」策略
   （`execution.special_video_policy=root`），`renumber_specials` 对这类行沿用旧编号，
   以保证与既有输出 0 差异。只有改成进入 Season 00 时才会启用新编号。
3. **`preview.py` 需要拆分**：目前预览、共享计算、字幕配对、质量提示都在一个文件里（约 900 行），
   改动风险偏高；拆分要先冻结当前输出做逐行对比。
4. **字幕组规则是启发式**：只处理“开头整段短括号 + 机构词”的形态；
   `XX字幕组 作品名` 这种括号外写法不会自动削，只在预览里提示。
   误削作品名的风险高于漏削，所以保持保守。
5. **`{ep_title}` 前端还没有用真实 TMDB 数据实际点验**：字段和空值折叠逻辑都在，
   但还没有一次“真实单集标题渲染”的端到端确认。
6. **缓存没有容量上限 / 过期时间**：只累计命中次数，需要用户手动清理；
   指纹包含 `group_id` 和 `dir_rel`，改目录组或改目录名后不会命中（有意保守）。
7. **结构描述不支持同一角色出现多次**：一个目录里如果同时有两种“集号段”（例如正片 + 附带短片），
   当前只能选一种，另一种需要“忽略”或拆目录。
8. **含剧场版的目录不参与缓存自动套用**：直接跳过，避免把剧场版改成正片；
   剧场版如果需要 AI 规律，要单独设计。
9. **AI 规律只改识别结果，不改文件名**：真正的改名仍在预览/执行阶段；
   没有“AI 一步到位生成新文件名”的路径。
10. **其它长期项**：合集拆集（需重编码）、刮削（NFO/图片）、UI 统一优化，均未开始。
11. **Disc 号与集号区分（已确认是真问题，暂不做）**：文件夹 5 里 `【Disc-01】【ED】…` 的 `Disc-01`
    会被分隔符拆成 `Disc` + `01`，正片规则把 `01` 当集号，出现重复集号 1、10、11…，
    「特典/合集未被误当正片」也点名这些文件；274 条全量复核因此不通过。
    这是「一条规则没法同时做到正片取第一个数字、碟号取第二个数字」的产品缺口，
    需要单独设计（例如碟号识别或排除 `Disc-*` 段），**本次明确不碰**，也不许用正则绕过。
12. **AI 输出格式不稳（真实证据，暂不扩词）**：文件夹 5 第一次返回把 4 条规则的 `take`
    写成「第二个」，不在词表内；补问后改成「第 2 个」，又把 `special_type` 拆成
    `纯文字` + `数字带字` 两条规则，被“同一角色只能出现一次”拒绝；Ruri 一次给出的结构
    也因分段不符被 5 项自检拦下。现有「补问一次 + 不过就整批放弃」已经能兜住这些错误，
    但要让 AI 稳定产出合法结构，需要更明确的示例或更强的本地纠正，而不是继续扩词。

---

## 14. 依赖版本与升级（「依赖升级」）

`backend/requirements.txt` 使用**精确版本号**（`==`），容器每次构建都装同一套版本，避免“今天能跑、明天构建坏掉”。

升级步骤：

1. 改 `backend/requirements.txt` 里的版本号（一次只升一个包，优先升 `uvicorn`，最后升 `fastapi`）。
2. 本地/容器跑一遍离线回归脚本：
   `docker compose build && docker compose up -d`，
   然后 `docker exec -w /app anime-renamer python tools/regression_check.py`，确认全部 PASS。
3. 再跑一次 `curl http://localhost:8080/api/health`，并手动走一遍「扫描 → 预览 → 执行 → 撤销」。
4. 有问题就回退：把版本号改回上一个精确值，重新 `docker compose up -d --build`。

注意：本项目只用标准库 `urllib` 发 HTTP 请求，不依赖 `httpx` / `requests`，升级面很小。
