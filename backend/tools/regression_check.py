"""离线回归检查：在临时目录里造最小样本，跑完 扫描 → 识别 → 预览 → 执行 → 冲突 → 撤销 → 续跑。

用法（容器内）：
    docker compose exec anime-renamer python tools/regression_check.py

用法（本机，Python 3.12）：
    python backend/tools/regression_check.py

脚本只用 Python 自带能力，不联网、不装测试框架。
所有数据都放在系统临时目录，运行前会强制改写 APP_CONFIG_DIR / APP_DB_PATH / MEDIA_ROOT，
因此不会碰用户的真实媒体目录、配置和执行记录。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((name, bool(ok), detail))
    print(("通过 " if ok else "失败 ") + name + (f"（{detail}）" if detail else ""))
    return bool(ok)


def fail(name: str, detail: str) -> bool:
    return check(name, False, detail)


def _ensure_outside(path: Path, root: Path, label: str) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise SystemExit(f"安全中断：{label} 不在临时根目录内，可能是环境变量没生效：{path}") from exc


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _setup_environment(root: Path) -> tuple[Path, Path, Path, Path]:
    media = root / "media"
    config = root / "config"
    target = root / "out"
    source = media / "回归库"
    for directory in (media, config, target, source):
        directory.mkdir(parents=True, exist_ok=True)

    os.environ["MEDIA_ROOT"] = str(media)
    os.environ["APP_CONFIG_DIR"] = str(config)
    os.environ["APP_DB_PATH"] = str(config / "app.db")
    return media, config, source, target


def _build_samples(source: Path) -> None:
    files = [
        "作品甲/Season 1/作品甲 - S01E01 [1080p].mkv",
        "作品甲/Season 1/作品甲 - S01E01 [720p].mkv",
        "作品甲/Season 1/作品甲 - S01E02 [1080p].mkv",
        "作品甲/Season 1/作品甲 - S01E03.mkv",
        "作品甲/Season 1/作品甲 - S01E02 [1080p].ass",
        "作品甲/特典/作品甲 - NCOP01 [1080p].mkv",
        "作品乙/作品乙 - S01E01 [1080p].mp4",
    ]
    for rel in files:
        _write(source / rel)


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="ani-rename-regression-"))
    try:
        media, config, source, target = _setup_environment(root)
        _build_samples(source)

        # 让 app 包可以被 import；必须放在环境变量设置之后
        backend = Path(__file__).resolve().parent.parent
        sys.path.insert(0, str(backend))

        from app.config import get_media_root, load_settings  # noqa: E402
        from app.core import queue  # noqa: E402
        from app.core.executor import build_group_map, execute_item, undo_items  # noqa: E402
        from app.core.duplicates import apply_decision, collect_groups, describe_groups  # noqa: E402
        from app.core.episode_no import episode_value  # noqa: E402
        from app.core.pattern import (  # noqa: E402
            annotate_pattern,
            apply_structure,
            compile_structure,
            fingerprint,
            prepare_patterns,
            shape_of,
            shape_signature,
            validate_structure,
        )
        from app.core.pattern_sample import sample_filenames  # noqa: E402
        from app.core.auto_execute import (  # noqa: E402
            AUTO_EXECUTE_RISK_TEXT,
            AUTO_EXECUTE_RISK_VERSION,
            auto_execute_refusal,
        )
        from app.core.gate import evaluate_gate  # noqa: E402
        from app.core.run_advance import (  # noqa: E402
            AdvanceError,
            can_mark_done,
            validate_advance,
        )
        from app.core.pending import collect_pending  # noqa: E402
        from app.core.identify import apply_identification, confirm_all  # noqa: E402
        from app.core.operations import build_plan, create_operation  # noqa: E402
        from app.core.parser import (  # noqa: E402
            clean_dir_title,
            parse_file,
            renumber_specials,
            title_looks_grouped,
        )
        from app.core.preview import _quality_warnings, build_preview, load_preset  # noqa: E402
        from app.core.scanner import scan_groups  # noqa: E402
        from app.core.template import build_filename  # noqa: E402
        from app.store import (  # noqa: E402
            append_run_event,
            claim_run,
            clear_run_locks,
            consume_auto_authorization,
            create_auto_authorization,
            create_pending_operation,
            create_rule,
            delete_rule,
            ensure_run,
            finalize_operation,
            get_auto_authorization,
            get_operation,
            get_run,
            init_db,
            latest_granted_authorization,
            list_decisions,
            list_pending_items,
            list_run_events,
            list_presets,
            list_rule_dicts,
            list_rules,
            operation_decision_counts,
            pending_counts,
            pending_operation_items,
            record_decision as record_store_decision,
            release_run,
            reset_builtin_rules,
            restore_auto_authorization,
            set_operation_status,
            sync_pending_items,
            update_operation_item,
            update_rule,
            update_run,
            DEFAULT_TEMPLATES,
        )

        # 安全兜底：确认运行环境真的指向临时目录
        _ensure_outside(get_media_root(), root, "媒体根目录")
        from app.config import CONFIG_DIR, DB_PATH  # noqa: E402

        _ensure_outside(Path(CONFIG_DIR), root, "配置目录")
        _ensure_outside(Path(DB_PATH), root, "执行记录数据库")

        init_db()
        settings = load_settings()
        # 回归样本里要检查特典文件夹，所以临时关掉“忽略特典类目录”
        settings["parse.ignore_special_dirs"] = False
        rules = list_rules()

        # ---------------- 1. 扫描 ----------------
        groups = [
            {
                "id": "reg-group",
                "name": "回归目录组",
                "source": str(source),
                "target": str(target / "回归库"),
            }
        ]
        scan = scan_groups(groups, get_media_root(), settings, rules)
        check(
            "扫描：识别到 7 个文件",
            len(scan.get("files") or []) == 7,
            f"实际 {len(scan.get('files') or [])} 个",
        )
        check("扫描：没有被上限截断", scan.get("truncated") is False)

        # ---------------- 2. 识别 ----------------
        apply_identification(scan, [], settings, [])
        queue.init_queue(scan)
        rows = {row["filename"]: row for row in scan["files"]}
        video = rows.get("作品甲 - S01E01 [1080p].mkv")
        check(
            "识别：作品名取上一级普通文件夹",
            bool(video and video.get("identify", {}).get("title") == "作品甲"),
            str(video and video.get("identify", {}).get("title")),
        )
        check(
            "识别：集号 / 季号来自文件名辅助信息",
            bool(video and video.get("season") == 1 and video.get("episode") == 1),
            str(video and (video.get("season"), video.get("episode"))),
        )
        special = rows.get("作品甲 - NCOP01 [1080p].mkv")
        check("识别：特典被识别为 special", bool(special and special.get("kind") == "special"))
        check("清单：条目已生成", len(queue.get_entries(scan)) > 0)

        # ---------------- 3. 预览 ----------------
        preset = load_preset(None) or list_presets()[0]
        preview = build_preview(scan, preset, settings)
        names = [str(row.get("new_name") or "") for row in preview.get("rows") or []]
        check(
            "预览：正片模板带清晰度区分同集多版本",
            any(name.startswith("作品甲 - S01E01 [1080p]") for name in names)
            and any(name.startswith("作品甲 - S01E01 [720p]") for name in names),
            ", ".join(names),
        )
        check(
            "预览：清晰度为空时不留空括号",
            any(name.startswith("作品甲 - S01E03") and "[]" not in name for name in names),
            ", ".join(names),
        )
        check("预览：字幕跟随视频改名", any(name.endswith(".ass") for name in names))
        check(
            "预览：特典走特典模板",
            any(name.startswith("作品甲 - NCOP1") for name in names),
            ", ".join(names),
        )

        # ---------------- 3b. 特典模板与 OVA 编号 ----------------
        check(
            "设置：默认特典目录名包含 EXTRA",
            "EXTRA" in str(settings.get("parse.special_dir_names") or ""),
            str(settings.get("parse.special_dir_names")),
        )
        built_name, _, _ = build_filename(
            {
                "fields": {
                    "series": "作品甲",
                    "season": 0,
                    "special_type": "CM",
                    "special_no": 2,
                    "source_season": "",
                    "ext": "mkv",
                }
            },
            {"pattern": DEFAULT_TEMPLATES["special"]["pattern"], "output_mode": "filename"},
            settings,
        )
        check("特典模板：来源季为空时整段折叠", built_name == "作品甲 - CM2.mkv", built_name)
        built_name, _, _ = build_filename(
            {
                "fields": {
                    "series": "作品甲",
                    "season": 0,
                    "special_type": "CM",
                    "special_no": 2,
                    "source_season": "S02",
                    "ext": "mkv",
                }
            },
            {"pattern": DEFAULT_TEMPLATES["special"]["pattern"], "output_mode": "filename"},
            settings,
        )
        check("特典模板：来源季排在最前", built_name == "作品甲 - S02 CM2.mkv", built_name)

        def _special_row(row_id: str, rel: str, work_id: str) -> dict:
            return {
                "id": row_id,
                "category": "video",
                "kind": "special",
                "path": str(source / rel),
                "rel_path": rel,
                "parent": str((source / rel).parent),
                "season": 0,
                "filename": Path(rel).name,
                "fields": {
                    "series": "作品甲",
                    "year": 2013,
                    "season": 0,
                    "special_type": "OVA",
                    "special_no": "",
                    "source_season": "",
                    "ext": "mkv",
                },
                "identify": {"work_id": work_id},
            }

        synthetic = {
            "root": str(source),
            "groups": [{"id": "sg", "source": str(source), "target": str(target / "合成")}],
            "files": [
                _special_row("o1", "A 特典/O1.mkv", "w2"),
                _special_row("o2", "B 特典/O2.mkv", "w1"),
            ],
            "season_state": {
                "w1": {"tmdb_id": "", "choice": 1},
                "w2": {"tmdb_id": "", "choice": 2},
            },
        }
        synthetic_preview = build_preview(synthetic, preset, settings)
        by_id = {row.get("file_id"): row.get("new_name") for row in synthetic_preview.get("rows") or []}
        check(
            "OVA 撞名：按季号升序编号，不按路径",
            by_id.get("o1") == "作品甲 - OVA2.mkv" and by_id.get("o2") == "作品甲 - OVA1.mkv",
            f"{by_id.get('o1')} / {by_id.get('o2')}",
        )

        pending_scan = {
            "files": [
                {"id": "p1", "category": "video", "identify": {"work_id": "w1", "needs_confirm": True}},
                {"id": "p2", "category": "video", "identify": {"work_id": "w2", "needs_confirm": True}},
                {"id": "p3", "category": "video", "identify": {"work_id": "w3", "needs_confirm": False}},
            ]
        }
        confirmed = confirm_all(pending_scan)
        check(
            "待确认：一键全部确认只清掉待确认标记",
            confirmed == 2
            and all(not row["identify"]["needs_confirm"] for row in pending_scan["files"]),
            f"{confirmed} 条",
        )

        # ---------------- 3c. 特典文件夹：A 整体搬 / B 进 Season 00 ----------------
        special_scan = {
            "root": str(source),
            "groups": [{"id": "sf", "source": str(source), "target": str(target / "特典测试")}],
            "files": [
                {
                    "id": "sf-v",
                    "group_id": "sf",
                    "category": "video",
                    "kind": "special",
                    "path": str(source / "作品甲/EXTRA/CDs/CM01.mkv"),
                    "rel_path": "作品甲/EXTRA/CDs/CM01.mkv",
                    "parent": str(source / "作品甲/EXTRA/CDs"),
                    "season": 0,
                    "filename": "CM01.mkv",
                    "special_folder": "EXTRA",
                    "fields": {
                        "series": "作品甲",
                        "year": 2013,
                        "season": 0,
                        "special_type": "CM",
                        "special_no": 1,
                        "source_season": "",
                        "ext": "mkv",
                    },
                    "identify": {"work_id": "w1"},
                }
            ],
            "ignored_dirs": [{"group_id": "sf", "path": "作品甲/EXTRA", "reason": "special"}],
            "special_decisions": {
                "sf:作品甲/EXTRA": {"work_id": "w1", "title": "作品甲", "year": 2013}
            },
        }
        a_preview = build_preview(special_scan, preset, settings)
        a_rows = {row.get("file_id") or row.get("entry_id"): row for row in a_preview.get("rows") or []}
        a_video = a_rows.get("sf-v") or {}
        a_folder = a_rows.get("d:sf:作品甲/EXTRA") or {}
        check(
            "特典 A：视频不单独改名，落点保留 EXTRA/内部结构",
            a_video.get("action") == "skip"
            and str(a_video.get("new_path") or "").replace("\\", "/").endswith(
                "作品甲 (2013)/EXTRA/CDs/CM01.mkv"
            ),
            f"{a_video.get('status')} | {a_video.get('new_path')}",
        )
        check(
            "特典 A：特典文件夹整目录搬到作品目录下",
            a_folder.get("action") == "rename"
            and str(a_folder.get("new_path") or "").replace("\\", "/").endswith(
                "作品甲 (2013)/EXTRA"
            ),
            str(a_folder.get("new_path")),
        )
        season_settings = {**settings, "execution.special_video_policy": "season"}
        b_preview = build_preview(special_scan, preset, season_settings)
        b_rows = {row.get("file_id") or row.get("entry_id"): row for row in b_preview.get("rows") or []}
        b_video = b_rows.get("sf-v") or {}
        check(
            "特典 B：视频进 Season 00 并按特典模板改名",
            b_video.get("action") == "rename"
            and "/Season 00/" in str(b_video.get("new_path") or "").replace("\\", "/")
            and str(b_video.get("new_name") or "").startswith("作品甲"),
            f"{b_video.get('new_path')} / {b_video.get('new_name')}",
        )

        # ---------------- 3d. 标记文件写到“搬过去之后”的特典文件夹 ----------------
        from app.config import save_settings  # noqa: E402
        from app.core.operations import _write_move_markers  # noqa: E402

        def marker_folder_case(base_name: str) -> Path:
            target_file = target / base_name / "作品甲 (2013)" / "EXTRA" / "CDs" / "CM01.mkv"
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_text("x", encoding="utf-8")
            op_id, _ = create_operation(
                "sf-scan",
                None,
                "marker",
                [
                    {
                        "group_id": "sf",
                        "category": "video",
                        "original_path": str(source / "作品甲/EXTRA/CDs/CM01.mkv"),
                        "target_path": str(target_file),
                        "status": "planned",
                    }
                ],
                [],
                "skip",
            )
            update_operation_item(op_id, 1, "done", str(target_file), "")
            _write_move_markers(op_id)
            return target_file.parents[1]

        save_settings({"execution.special_video_policy": "root"})
        a_marker_dir = marker_folder_case("标记测试A")
        check(
            "标记：A 模式在目标特典文件夹里写 .ignore / .nomedia",
            (a_marker_dir / ".ignore").exists() and (a_marker_dir / ".nomedia").exists(),
            str(a_marker_dir),
        )

        save_settings({"execution.special_video_policy": "season"})
        b_marker_dir = marker_folder_case("标记测试B")
        check(
            "标记：B 模式不写标记文件",
            not (b_marker_dir / ".ignore").exists()
            and not (b_marker_dir / ".nomedia").exists(),
            str(b_marker_dir),
        )

        save_settings({"execution.special_video_policy": "root", "parse.write_ignore_markers": False})
        off_marker_dir = marker_folder_case("标记测试C")
        check(
            "标记：设置里关掉后一律不写",
            not (off_marker_dir / ".ignore").exists()
            and not (off_marker_dir / ".nomedia").exists(),
            str(off_marker_dir),
        )
        save_settings({"parse.write_ignore_markers": True})

        # ---------------- 3d2. 两层预设快照：命名层与工作流层各管各的 ----------------
        from app.config import (  # noqa: E402
            NAMING_PRESET_OPTION_KEYS,
            PRESET_EXCLUDED_KEYS,
            SETTING_SPECS,
            WORKFLOW_PRESET_OPTION_KEYS,
            naming_options_from_settings,
            naming_settings_from_options,
            workflow_options_from_settings,
            workflow_settings_from_options,
        )

        preset_settings = {
            **load_settings(),
            "execution.special_video_policy": "season",
            "execution.special_folder_conflict": "merge",
            "execution.batch_size": 7,
            "execution.batch_delay_seconds": 2,
            "execution.retry_times": 3,
            "execution.retry_backoff_seconds": 9,
            "naming.special_folder_suffix": "{source_season} {name}",
        }
        naming_snapshot = naming_options_from_settings(preset_settings)
        workflow_snapshot = workflow_options_from_settings(preset_settings)
        expected_naming = {
            "special_video_policy": "season",
            "special_folder_conflict": "merge",
            "special_folder_suffix": "{source_season} {name}",
        }
        expected_workflow = {
            "batch_size": 7,
            "batch_delay_seconds": 2,
            "retry_times": 3,
            "retry_backoff_seconds": 9,
        }
        check(
            "命名预设快照：落点与撞名参数已纳入",
            all(naming_snapshot.get(key) == value for key, value in expected_naming.items()),
            str({key: naming_snapshot.get(key) for key in expected_naming}),
        )
        check(
            "工作流预设快照：执行参数已纳入",
            all(workflow_snapshot.get(key) == value for key, value in expected_workflow.items()),
            str({key: workflow_snapshot.get(key) for key in expected_workflow}),
        )
        check(
            "两层预设互不串味：命名层没有批量参数，工作流层没有撞名参数",
            "batch_size" not in naming_snapshot
            and "special_folder_suffix" not in workflow_snapshot
            and "special_folder_conflict" not in workflow_snapshot,
            f"naming={sorted(naming_snapshot)} workflow={sorted(workflow_snapshot)}",
        )
        restored_naming = naming_settings_from_options(naming_snapshot)
        restored_workflow = workflow_settings_from_options(workflow_snapshot)
        check(
            "命名预设：切预设能还原落点参数",
            all(
                restored_naming.get(NAMING_PRESET_OPTION_KEYS[key]) == value
                for key, value in expected_naming.items()
            ),
            str(restored_naming),
        )
        check(
            "工作流预设：切预设能还原执行参数",
            all(
                restored_workflow.get(WORKFLOW_PRESET_OPTION_KEYS[key]) == value
                for key, value in expected_workflow.items()
            ),
            str(restored_workflow),
        )
        legacy_naming = naming_settings_from_options({"subtitle_mode": "keep"})
        legacy_workflow = workflow_settings_from_options({})
        check(
            "两层预设：旧快照缺的项按默认补全，不会留空",
            legacy_naming.get("naming.subtitle_mode") == "keep"
            and all(
                key in legacy_naming for key in NAMING_PRESET_OPTION_KEYS.values()
            )
            and all(
                key in legacy_workflow for key in WORKFLOW_PRESET_OPTION_KEYS.values()
            ),
            str(sorted(legacy_naming) + sorted(legacy_workflow)),
        )

        all_spec_keys = {spec.key for spec in SETTING_SPECS}
        layer_keys = set(NAMING_PRESET_OPTION_KEYS.values()) | set(
            WORKFLOW_PRESET_OPTION_KEYS.values()
        )
        check(
            "两层预设：除密钥、本机开关和全局语言外，设置页的项全部进入快照",
            layer_keys | set(PRESET_EXCLUDED_KEYS) == all_spec_keys,
            f"缺 {sorted(all_spec_keys - layer_keys - set(PRESET_EXCLUDED_KEYS))}",
        )
        check(
            "两层预设：三个密钥、网盘模式、全局语言都不写入预设",
            all(
                key in PRESET_EXCLUDED_KEYS
                for key in (
                    "providers.tmdb_api_key",
                    "providers.bangumi_token",
                    "providers.ai_api_key",
                    "execution.network_drive_mode",
                    "providers.tmdb_lang",
                    "metadata.name_language_order",
                )
            )
            and not (set(PRESET_EXCLUDED_KEYS) & layer_keys),
            str(sorted(set(PRESET_EXCLUDED_KEYS) & layer_keys)),
        )

        # ---------------- 3d3. v7 迁移：旧单层预设拆成命名预设 + 工作流预设 ----------------
        import app.store as store_module  # noqa: E402

        preset_file = store_module.PRESETS_FILE
        original_preset_text = preset_file.read_text("utf-8") if preset_file.exists() else None
        try:
            legacy_doc = {
                "version": 6,
                "default_id": "preset-old",
                "presets": [
                    {
                        "id": "preset-old",
                        "name": "旧预设",
                        "is_default": True,
                        "library_profile": "generic",
                        "templates": {
                            "episode": {
                                "pattern": "{series} 自定义 {episode:02}",
                                "output_mode": "filename",
                            }
                        },
                        "options": {
                            "subtitle_mode": "keep",
                            "preserve_structure": True,
                            "movie_layout": "special",
                            "batch_size": 5,
                            "tmdb_lang": "ja-JP",
                            "无法归类的旧项": 1,
                        },
                    }
                ],
            }
            preset_file.write_text(
                json.dumps(legacy_doc, ensure_ascii=False), encoding="utf-8"
            )
            migrated = store_module._ensure_presets()
            naming = (migrated.get("naming_presets") or [])[0]
            workflow = (migrated.get("workflow_presets") or [])[0]
            record = migrated.get("migration") or {}
            check(
                "v7 迁移：一份旧预设拆成一条命名预设 + 一条工作流预设",
                len(migrated.get("naming_presets") or []) == 1
                and len(migrated.get("workflow_presets") or []) == 1
                and naming["id"] == "preset-old"
                and workflow["id"] == "preset-old"
                and workflow["naming_preset_id"] == "preset-old",
                str(migrated.keys()),
            )
            check(
                "v7 迁移：命名层完整接到原有值",
                naming["options"].get("subtitle_mode") == "keep"
                and naming["options"].get("preserve_structure") is True
                and naming["options"].get("movie_layout") == "special"
                and naming["templates"]["episode"]["pattern"] == "{series} 自定义 {episode:02}",
                str(naming["options"]),
            )
            check(
                "v7 迁移：工作流层完整接到原有值，且不夹带命名项",
                workflow["options"].get("batch_size") == 5
                and "subtitle_mode" not in workflow["options"]
                and "preserve_structure" not in workflow["options"],
                str(sorted(workflow["options"])),
            )
            check(
                "v7 迁移：旧值缺的项按默认补齐并逐条记录",
                naming["options"].get("default_season") == 1
                and workflow["options"].get("retry_times") == 0
                and any(
                    item.get("layer") == "naming"
                    and item.get("key") == "max_filename_bytes"
                    for item in record.get("filled_defaults") or []
                )
                and any(
                    item.get("layer") == "workflow"
                    and item.get("key") == "retry_times"
                    for item in record.get("filled_defaults") or []
                ),
                str(len(record.get("filled_defaults") or [])),
            )
            check(
                "v7 迁移：全局语言项不搬进预设，差异单独记录",
                "tmdb_lang" not in naming["options"]
                and "tmdb_lang" not in workflow["options"]
                and any(
                    item.get("key") == "providers.tmdb_lang"
                    and item.get("preset_value") == "ja-JP"
                    for item in record.get("global_only_ignored") or []
                )
                and load_settings().get("providers.tmdb_lang") != "ja-JP",
                str(record.get("global_only_ignored")),
            )
            check(
                "v7 迁移：无法归类的旧键丢弃但留痕，不瞎猜",
                any(
                    item.get("key") == "无法归类的旧项"
                    for item in record.get("dropped_keys") or []
                ),
                str(record.get("dropped_keys")),
            )
            check(
                "v7 迁移：两层各自标记默认预设",
                naming["is_default"] is True and workflow["is_default"] is True,
                f"{naming['is_default']}/{workflow['is_default']}",
            )
        finally:
            if original_preset_text is None:
                preset_file.unlink(missing_ok=True)
            else:
                preset_file.write_text(original_preset_text, encoding="utf-8")

        # 回归脚本不依赖 FastAPI，这里直接检查路由源码：旧接口必须 410 且给出新地址。
        from app.config import LEGACY_PRESET_NOTICE  # noqa: E402

        routes_source = (backend / "app/api/routes_presets.py").read_text("utf-8")
        check(
            "旧预设接口：不再静默返回旧数据，明确报 410 并说明新地址",
            "status_code=410" in routes_source
            and "LEGACY_PRESET_NOTICE" in routes_source
            and "/api/naming-presets" in LEGACY_PRESET_NOTICE
            and "/api/workflow-presets" in LEGACY_PRESET_NOTICE,
            LEGACY_PRESET_NOTICE[:40],
        )

        run_naming = ensure_run("reg-naming-preset-run", naming_preset_id="preset-old")
        run_naming_row = get_run(run_naming["id"]) or {}
        check(
            "运行记录：本次运行记下使用的命名预设",
            (run_naming_row.get("snapshot") or {}).get("naming_preset_id") == "preset-old",
            str(run_naming_row.get("snapshot")),
        )
        frozen_run = ensure_run("reg-naming-freeze", naming_preset_id="preset-old")
        update_run(frozen_run["id"], stage="execute")
        ensure_run("reg-naming-freeze", naming_preset_id="preset-other")
        frozen_row = get_run(frozen_run["id"]) or {}
        check(
            "运行记录：进入执行阶段后不再被中途换预设改写",
            (frozen_row.get("snapshot") or {}).get("naming_preset_id") == "preset-old",
            str(frozen_row.get("snapshot")),
        )

        # ---------------- 3e. 多季同名素材文件夹：加季号 / 合并 / 自定义格式 ----------------
        def conflict_scan(with_seasons: bool) -> dict:
            decisions = {
                "cf:来源A/第一季/CDs": {"work_id": "w1", "title": "作品甲", "year": 2013},
                "cf:来源B/第二季/CDs": {"work_id": "w2", "title": "作品甲", "year": 2013},
            }
            if not with_seasons:
                decisions = {
                    "cf:来源A/第一季/CDs": {"work_id": "", "title": "作品甲", "year": 2013},
                    "cf:来源B/第二季/CDs": {"work_id": "", "title": "作品甲", "year": 2013},
                }
            return {
                "root": str(source),
                "groups": [
                    {"id": "cf", "source": str(source), "target": str(target / "撞名测试")}
                ],
                "files": [],
                "ignored_dirs": [
                    {"group_id": "cf", "path": "来源A/第一季/CDs", "reason": "special"},
                    {"group_id": "cf", "path": "来源B/第二季/CDs", "reason": "special"},
                ],
                "special_decisions": decisions,
                "season_state": (
                    {
                        "w1": {"choice": 1, "tmdb_id": ""},
                        "w2": {"choice": 2, "tmdb_id": ""},
                    }
                    if with_seasons
                    else {}
                ),
            }

        def folder_map(payload: dict, over: dict) -> dict[str, dict]:
            preview = build_preview(payload, preset, {**settings, **over})
            return {
                str(row.get("entry_id")): row
                for row in preview.get("rows") or []
                if row.get("category") == "folder"
            }

        season_rows = folder_map(conflict_scan(True), {})
        check(
            "素材撞名：默认加季号区分",
            str(season_rows["d:cf:来源A/第一季/CDs"]["new_path"]).endswith("CDs S01")
            and str(season_rows["d:cf:来源B/第二季/CDs"]["new_path"]).endswith("CDs S02"),
            f"{season_rows['d:cf:来源A/第一季/CDs']['new_path']} / {season_rows['d:cf:来源B/第二季/CDs']['new_path']}",
        )
        custom_rows = folder_map(
            conflict_scan(True), {"naming.special_folder_suffix": "{name} [{source_season}]"}
        )
        check(
            "素材撞名：后缀格式可自定义",
            str(custom_rows["d:cf:来源A/第一季/CDs"]["new_path"]).endswith("CDs [S01]"),
            str(custom_rows["d:cf:来源A/第一季/CDs"]["new_path"]),
        )
        merge_rows = folder_map(
            conflict_scan(True), {"execution.special_folder_conflict": "merge"}
        )
        merged_left = merge_rows["d:cf:来源A/第一季/CDs"]
        merged_right = merge_rows["d:cf:来源B/第二季/CDs"]
        check(
            "素材撞名：合并模式保持同一个目录且不报错",
            merged_left.get("new_path") == merged_right.get("new_path")
            and merged_left.get("status") == "ok"
            and merged_right.get("status") == "ok",
            f"{merged_left.get('new_path')} / {merged_left.get('status')}",
        )
        single = conflict_scan(True)
        single["ignored_dirs"] = [single["ignored_dirs"][0]]
        single["special_decisions"] = {
            "cf:来源A/第一季/CDs": single["special_decisions"]["cf:来源A/第一季/CDs"]
        }
        single_rows = folder_map(single, {})
        check(
            "素材撞名：没撞名时保持原名",
            Path(str(single_rows["d:cf:来源A/第一季/CDs"]["new_path"])).name == "CDs",
            str(single_rows["d:cf:来源A/第一季/CDs"]["new_path"]),
        )
        fallback_rows = folder_map(conflict_scan(False), {})
        fallback_names = sorted(
            Path(str(row["new_path"])).name for row in fallback_rows.values()
        )
        check(
            "素材撞名：季号未知时用 S00 兜底并保证不重名",
            fallback_names == ["CDs S00", "CDs S00 2"]
            and any("S00 兜底" in " ".join(row.get("messages") or []) for row in fallback_rows.values()),
            str(fallback_names),
        )

        # ---------------- 3f. 全角【】集号 / OP-ED / 作品名清洗 ----------------
        from app.core.parser import clean_dir_title  # noqa: E402
        from app.core.rules import match_rules  # noqa: E402

        def file_fields(stem: str) -> dict:
            return match_rules(stem, "filename", rules)

        cn_ep = file_fields("【001】【游戏的继承者】【DVDrip】【x264 Hi10p AAC】【MP4】")
        check(
            "全角：开头的【001】识别为第 1 集",
            cn_ep.get("episode") == "001" and cn_ep.get("kind") == "episode",
            str(cn_ep),
        )
        cn_year = file_fields("【2013】【作品甲】【MP4】")
        check("全角：4 位年份【2013】不当集号", not cn_year.get("episode"), str(cn_year))
        cn_mid = file_fields("作品甲【2】【1080p】")
        check("全角：标题中间的【2】不当集号", not cn_mid.get("episode"), str(cn_mid))
        cn_op = file_fields("【Disc-41】【OP】【DVDrip】【x264 Hi10p AAC】【MP4】")
        check(
            "全角：OP 归为特典",
            cn_op.get("kind") == "special" and str(cn_op.get("special_type") or "").upper() == "OP",
            str(cn_op),
        )
        cn_op_no = file_fields("【Disc-09】【OP01】【DVDrip】【MP4】")
        check(
            "全角：OP01 带序号",
            cn_op_no.get("kind") == "special" and cn_op_no.get("special_no") == "01",
            str(cn_op_no),
        )
        cn_title = clean_dir_title("【晨曦制作】【游戏王★决斗怪兽GX】【DVDrip】【x264 Hi10p AAC】【MP4】")
        check(
            "全角：作品名清洗后不留空括号",
            "【" not in cn_title and "】" not in cn_title and "游戏王" in cn_title,
            cn_title,
        )

        # ---------------- 3g. 作品名语言回退 + AI 只挑编号 ----------------
        from app.core import ai as ai_module  # noqa: E402
        from app.core import metadata as metadata_module  # noqa: E402
        from app.core.matcher import build_updates  # noqa: E402

        check(
            "名称来源：默认语言顺序为简→繁→日→英",
            metadata_module.name_language_order({}) == ("zh-CN", "zh-TW", "original", "en-US"),
            str(metadata_module.name_language_order({})),
        )
        simple = {
            "tmdb_id": 100,
            "media_type": "tv",
            "name": "简体名",
            "original_name": "Original Name",
            "year": 2013,
        }
        resolved = metadata_module.resolve_display_name(dict(simple), "key")
        check(
            "名称来源：有简体时直接用简体",
            resolved.get("name") == "简体名" and resolved.get("name_source") == "zh-CN",
            f"{resolved.get('name')} / {resolved.get('name_source')}",
        )
        same_as_original = {**simple, "name": "Original Name"}
        same_resolved = metadata_module.resolve_display_name(
            dict(same_as_original), "key", ("zh-CN", "original")
        )
        check(
            "名称来源：简体等于原名时视为没有简体",
            same_resolved.get("name") == "Original Name"
            and same_resolved.get("name_source") == "original",
            f"{same_resolved.get('name')} / {same_resolved.get('name_source')}",
        )

        original_fetch = metadata_module._fetch_detail

        def fake_detail(media_type: str, tmdb_id: int, api_key: str, language: str) -> dict:
            return {
                "name": {
                    "zh-CN": "简体名",
                    "zh-TW": "繁體名",
                    "en-US": "English Name",
                }.get(language, ""),
                "overview": "",
            }

        metadata_module._fetch_detail = fake_detail
        try:
            tw = metadata_module.resolve_display_name(
                dict(same_as_original), "key", ("zh-CN", "zh-TW", "original", "en-US")
            )
            check(
                "名称来源：缺简体时先回退到繁体",
                tw.get("name") == "繁體名" and tw.get("name_source") == "zh-TW",
                f"{tw.get('name')} / {tw.get('name_source')}",
            )
            jp = metadata_module.resolve_display_name(
                dict(same_as_original), "key", ("zh-CN", "original", "zh-TW", "en-US")
            )
            check(
                "名称来源：顺序可配置（简→原名）",
                jp.get("name") == "Original Name" and jp.get("name_source") == "original",
                f"{jp.get('name')} / {jp.get('name_source')}",
            )
            en = metadata_module.resolve_display_name(
                {**same_as_original, "name": ""}, "key", ("zh-CN", "en-US")
            )
            check(
                "名称来源：繁体也没有时回退到英文",
                en.get("name") == "English Name" and en.get("name_source") == "en-US",
                f"{en.get('name')} / {en.get('name_source')}",
            )
            zh = metadata_module.resolve_display_name(
                {**same_as_original, "name": "繁體名"},
                "key",
                ("zh-CN", "zh-TW", "original", "en-US"),
                "zh-TW",
            )
            check(
                "名称来源：TMDB 语言不是简体时也会先查简体",
                zh.get("name") == "简体名" and zh.get("name_source") == "zh-CN",
                f"{zh.get('name')} / {zh.get('name_source')}",
            )
        finally:
            metadata_module._fetch_detail = original_fetch

        name_updates, _ = build_updates(
            [{"id": "x", "kind": "episode", "season": 1, "episode": 1}],
            {"tmdb_id": 1, "name": "名", "name_source": "zh-TW", "episodes": {}},
        )
        check(
            "名称来源：写进字段供界面显示",
            name_updates["x"].get("name_source") == "zh-TW",
            str(name_updates["x"]),
        )

        ai_settings = {
            "providers.ai_enabled": True,
            "providers.ai_base_url": "http://example.invalid/v1",
            "providers.ai_model": "test-model",
        }
        original_ask = ai_module._ask
        try:
            ai_module._ask = lambda system, user, settings: {
                "works": [{"id": "g1", "query": "日文原名", "title": "AI 编的标题"}]
            }
            asked = ai_module.ask_query({"id": "g1", "title": "作品甲"}, ai_settings)
            check(
                "AI 关键词：只返回关键词，不带标题",
                asked.get("query") == "日文原名" and "title" not in asked,
                str(asked),
            )

            candidates = [{"id": 1, "name": "作品甲", "media_type": "tv"}]
            ai_module._ask = lambda system, user, settings: {"id": 999}
            picked = ai_module.pick_candidate({"title": "作品甲"}, candidates, ai_settings)
            check(
                "AI 挑编号：不存在的编号整条丢弃",
                picked.get("id") is None
                and picked.get("valid") is False
                and "不存在" in str(picked.get("message")),
                str(picked),
            )
            ai_module._ask = lambda system, user, settings: {"id": None}
            picked = ai_module.pick_candidate({"title": "作品甲"}, candidates, ai_settings)
            check(
                "AI 挑编号：说不确定时不猜",
                picked.get("id") is None
                and picked.get("valid") is True
                and "不确定" in str(picked.get("message")),
                str(picked),
            )
            ai_module._ask = lambda system, user, settings: {"id": 1}
            picked = ai_module.pick_candidate({"title": "作品甲"}, candidates, ai_settings)
            check(
                "AI 挑编号：合法编号原样返回",
                picked.get("id") == 1 and picked.get("valid") is True,
                str(picked),
            )
        finally:
            ai_module._ask = original_ask

        duplicates = describe_groups(scan.get("files") or [], scan.get("duplicate_decisions"))
        check(
            "重复集：同集多版本会被列出",
            bool(duplicates) and duplicates[0].get("kind") == "multi_version",
            str(duplicates[0].get("kind") if duplicates else ""),
        )
        suggestion = "\n".join(
            str(message)
            for row in preview.get("rows") or []
            for message in (row.get("messages") or [])
            if "重复集" in str(message)
        )
        check("重复集：建议里说明了可加 {resolution}", "{resolution}" in suggestion)
        # 模拟用户在预览页选择「全部保留」，之后才能继续执行
        for duplicate in duplicates:
            apply_decision(scan, duplicate["key"], "keep_all")
        preview = build_preview(scan, preset, settings)

        # ---------------- 4. 执行 + 故意冲突 ----------------
        plan = build_plan(preview.get("rows") or [])
        planned = [item for item in plan if item.get("status") == "planned"]
        check("执行：生成了可执行计划", bool(planned), f"{len(planned)} 条")
        conflict_item = next(
            (item for item in planned if "S01E01 [1080p]" in item["target_path"]), None
        )
        if conflict_item:
            _write(Path(conflict_item["target_path"]), "已存在的文件")
            check("冲突：已预置一个同名目标文件", True, conflict_item["target_path"])
        else:
            fail("冲突：没有找到可用于制造冲突的目标", "")

        operation_id, _batch = create_operation(
            "reg-scan", preset.get("id"), str(preset.get("name") or ""), plan, groups, "skip"
        )
        group_map = build_group_map(groups, get_media_root())

        def run_pending(policy: str, limit: int | None = None) -> None:
            items = pending_operation_items(operation_id)
            if limit is not None:
                items = items[:limit]
            for item in items:
                status, moved, text = execute_item(item, group_map, get_media_root(), policy)
                update_operation_item(
                    operation_id, int(item["seq"]), status, moved or item.get("target_path", ""), text
                )

        run_pending("skip")
        operation = get_operation(operation_id) or {}
        statuses = {item["seq"]: item["status"] for item in operation.get("items") or []}
        conflict_seqs = [seq for seq, status in statuses.items() if status == "conflict"]
        check("执行：冲突条目被拦下并留下记录", bool(conflict_seqs), f"{len(conflict_seqs)} 条")
        done_count = sum(1 for status in statuses.values() if status == "done")
        check("执行：其它条目正常完成", done_count > 0, f"{done_count} 条")

        # ---------------- 5. 冲突处理 ----------------
        if conflict_seqs:
            seq = conflict_seqs[0]
            item = next(item for item in operation["items"] if item["seq"] == seq)
            status, moved, text = execute_item(dict(item), group_map, get_media_root(), "overwrite")
            update_operation_item(
                operation_id, int(seq), status, moved or item.get("target_path", ""), text
            )
            after = get_operation(operation_id) or {}
            resolved = {i["seq"]: i["status"] for i in after.get("items") or []}
            check("冲突处理：选择覆盖后执行成功", resolved.get(seq) == "done", str(resolved.get(seq)))
        counts = {
            "done": sum(1 for status in statuses.values() if status == "done"),
            "skipped": sum(1 for status in statuses.values() if status == "skipped"),
            "failed": sum(1 for status in statuses.values() if status == "failed"),
            "conflict": 0,
        }
        finalize_operation(operation_id, "done", {"groups": groups, **counts})

        # ---------------- 6. 撤销 ----------------
        operation = get_operation(operation_id) or {}
        results = undo_items(operation.get("items") or [], groups, get_media_root(), True)
        undone = sum(1 for item in results if item.get("status") == "done")
        check("撤销：已完成条目被移回原路径", undone > 0, f"{undone} 条")
        check(
            "撤销：源文件重新出现",
            (source / "作品甲/Season 1/作品甲 - S01E01 [1080p].mkv").exists(),
        )

        # ---------------- 7. 续跑 ----------------
        preview2 = build_preview(scan, preset, settings)
        plan2 = build_plan(preview2.get("rows") or [])
        operation2, _ = create_operation(
            "reg-scan", preset.get("id"), str(preset.get("name") or ""), plan2, groups, "skip"
        )
        first_batch = len(pending_operation_items(operation2)) // 2
        operation_id = operation2
        run_pending("skip", limit=max(1, first_batch))
        set_operation_status(operation2, "interrupted")
        remaining = pending_operation_items(operation2)
        check("续跑：中断后仍保留未完成条目", bool(remaining), f"{len(remaining)} 条")
        run_pending("skip")
        after2 = get_operation(operation2) or {}
        remaining_after = [i for i in after2.get("items") or [] if i["status"] in ("planned", "failed")]
        problems = [
            (i["seq"], i["status"], i.get("message"), i.get("original_path"), i.get("target_path"))
            for i in after2.get("items") or []
            if i["status"] in ("planned", "failed")
        ]
        check("续跑：继续执行后没有残留条目", not remaining_after, str(problems))
        finalize_operation(operation2, "done", {"groups": groups, "done": len(after2.get("items") or [])})

        # ---------------- 7b. P1-P6：状态摘要 / 分批 / 文件夹标记 / 撤销清理 / 副本命名 / 覆盖确认 ----------------
        from app.core.executor import (  # noqa: E402
            cleanup_written_markers,
            suffix_target,
            validate_conflict_choices,
        )
        from app.core.operations import (  # noqa: E402
            _write_move_markers,
            configured_batch_size,
            refresh_interrupted_summaries,
            refresh_summary,
        )

        # P1：暂停 / 取消 / 中断后摘要要与条目明细一致
        p1_items = [
            {
                "group_id": "sf",
                "category": "video",
                "original_path": str(source / f"P1-{n}.mkv"),
                "target_path": str(target / "P1" / f"P1-{n}.mkv"),
                "status": "planned",
            }
            for n in (1, 2, 3)
        ]
        op_p1, _ = create_operation("p1-scan", None, "p1", p1_items, [], "skip")
        update_operation_item(op_p1, 1, "done", str(target / "P1" / "P1-1.mkv"), "")
        update_operation_item(op_p1, 2, "done", str(target / "P1" / "P1-2.mkv"), "")
        set_operation_status(op_p1, "paused")
        refresh_summary(op_p1)
        summary_p1 = (get_operation(op_p1) or {}).get("summary") or {}
        check(
            "P1 暂停：摘要完成数与明细一致",
            summary_p1.get("done") == 2 and summary_p1.get("total") == 3,
            str(summary_p1),
        )
        set_operation_status(op_p1, "cancelled")
        refresh_summary(op_p1)
        summary_p1 = (get_operation(op_p1) or {}).get("summary") or {}
        check("P1 取消：摘要完成数与明细一致", summary_p1.get("done") == 2, str(summary_p1))
        set_operation_status(op_p1, "interrupted")
        refresh_interrupted_summaries()
        summary_p1 = (get_operation(op_p1) or {}).get("summary") or {}
        check("P1 中断：重启补摘要后与明细一致", summary_p1.get("done") == 2, str(summary_p1))

        # P2：重新分批读设置页批大小，不再用固定常量
        original_batch = load_settings().get("execution.batch_size")
        save_settings({"execution.batch_size": 5})
        check("P2 分批：读设置页批大小", configured_batch_size() == 5, str(configured_batch_size()))
        p2_items = [
            {
                "group_id": "sf",
                "category": "video",
                "original_path": str(source / f"P2-{n}.mkv"),
                "target_path": str(target / "P2" / f"P2-{n}.mkv"),
                "status": "planned",
            }
            for n in range(12)
        ]
        _, p2_batches = create_operation("p2-scan", None, "p2", p2_items, [], "skip")
        check("P2 分批：12 条按 5 条一批 = 3 批", p2_batches == 3, str(p2_batches))
        save_settings({"execution.batch_size": original_batch})

        # P3：文件夹整体移动（覆盖 / 生成副本）也按规则写标记
        save_settings({"execution.special_video_policy": "root", "parse.write_ignore_markers": True})
        p3_root = target / "标记文件夹测试"

        def folder_marker_case(origin: str, dest: str, expect: bool) -> bool:
            dest_dir = p3_root / dest
            dest_dir.mkdir(parents=True, exist_ok=True)
            op_folder, _ = create_operation(
                "p3-scan",
                None,
                "p3",
                [
                    {
                        "group_id": "sf",
                        "category": "folder",
                        "original_path": str(source / "作品甲" / origin),
                        "target_path": str(dest_dir),
                        "status": "planned",
                    }
                ],
                [],
                "skip",
            )
            update_operation_item(op_folder, 1, "done", str(dest_dir), "")
            _write_move_markers(op_folder)
            got = (dest_dir / ".ignore").exists() and (dest_dir / ".nomedia").exists()
            recorded = list(
                ((get_operation(op_folder) or {}).get("summary") or {}).get("written_markers") or []
            )
            return got == expect and (len(recorded) == 2 if expect else not recorded)

        check("P3 覆盖目录：标记写进目标特典文件夹", folder_marker_case("EXTRA", "EXTRA", True))
        check("P3 生成副本：EXTRA (1) 也写标记", folder_marker_case("EXTRA", "EXTRA (1)", True))
        check("P3 普通文件夹：不写标记", folder_marker_case("Season 01", "Season 01", False))

        # P4：撤销清理只删执行时写的标记，并删掉因此变空的目录
        undo_root = target / "撤销标记测试"
        app_marker = undo_root / "EXTRA" / "CDs" / ".ignore"
        user_marker = undo_root / "EXTRA" / ".nomedia"
        app_marker.parent.mkdir(parents=True, exist_ok=True)
        app_marker.write_text("", encoding="utf-8")
        user_marker.write_text("user", encoding="utf-8")
        undo_groups = [{"id": "undo", "source": str(undo_root), "target": str(undo_root)}]
        removed = cleanup_written_markers([str(app_marker)], undo_groups, get_media_root())
        check(
            "P4 撤销：执行时写的标记被删除",
            removed == [str(app_marker)] and not app_marker.exists(),
            str(removed),
        )
        check("P4 撤销：因此变空的目录被删掉", not app_marker.parent.exists(), str(app_marker.parent))
        check("P4 撤销：用户原有标记不受影响", user_marker.exists(), str(user_marker))

        # P5：生成副本从 (1) 开始
        suffix_dir = target / "副本命名测试"
        suffix_dir.mkdir(parents=True, exist_ok=True)
        check(
            "P5 生成副本：第一个副本是 (1)",
            suffix_target(suffix_dir).name == "副本命名测试 (1)",
            suffix_target(suffix_dir).name,
        )

        # P6：覆盖必须带后端确认标记
        folder_conflict = {"category": "folder", "status": "conflict"}
        file_conflict = {"category": "video", "status": "conflict"}

        def resolve_reject(action: str, confirm: bool, item: dict) -> str:
            try:
                validate_conflict_choices(
                    [{"seq": 1, "action": action}], {1: item}, confirm
                )
                return ""
            except ValueError as exc:
                return str(exc)

        rejected_overwrite = resolve_reject("overwrite", False, folder_conflict)
        check(
            "P6 覆盖：不带确认标记被后端拒绝",
            "我确认覆盖" in rejected_overwrite,
            rejected_overwrite,
        )
        rejected_merge = resolve_reject("merge-overwrite", False, folder_conflict)
        check(
            "P6 合并覆盖：不带确认标记被后端拒绝",
            "我确认覆盖" in rejected_merge,
            rejected_merge,
        )
        check(
            "P6 覆盖：带确认标记可以通过",
            resolve_reject("overwrite", True, folder_conflict) == "",
        )
        check(
            "P6 非覆盖：生成副本不需要确认",
            resolve_reject("suffix", False, folder_conflict) == "",
        )
        rejected_action = resolve_reject("delete", False, file_conflict)
        check(
            "P6 非法方式：仍然返回中文拒绝说明",
            "不支持" in rejected_action,
            rejected_action,
        )

        # ---------------- 7c. 网盘模式：推荐参数 / 风控预检 / 低 IO 结果一致 ----------------
        from app.config import NETWORK_DRIVE_DEFAULTS, save_settings  # noqa: E402
        from app.core.executor import (  # noqa: E402
            DirIndex,
            build_group_map,
            cleanup_done_sources,
            execute_item,
            undo_items,
        )
        from app.core.preflight import check_execution  # noqa: E402

        # 1) 开关默认关闭；打开时写入推荐参数，之后仍可单独微调
        check("网盘模式：默认关闭", load_settings().get("execution.network_drive_mode") is False)
        save_settings({"execution.batch_size": 77, "execution.batch_delay_seconds": 9})
        save_settings({"execution.network_drive_mode": True})
        enabled = load_settings()
        check(
            "网盘模式：打开后写入推荐批大小",
            enabled.get("execution.batch_size") == NETWORK_DRIVE_DEFAULTS["execution.batch_size"],
            str(enabled.get("execution.batch_size")),
        )
        check(
            "网盘模式：打开后加大批间隔",
            enabled.get("execution.batch_delay_seconds")
            == NETWORK_DRIVE_DEFAULTS["execution.batch_delay_seconds"],
            str(enabled.get("execution.batch_delay_seconds")),
        )
        check(
            "网盘模式：打开后减少重试次数",
            enabled.get("execution.retry_times") == NETWORK_DRIVE_DEFAULTS["execution.retry_times"],
            str(enabled.get("execution.retry_times")),
        )
        save_settings({"execution.batch_size": 3})
        check("网盘模式：之后仍可单独微调", load_settings().get("execution.batch_size") == 3)
        save_settings({"execution.network_drive_mode": False})
        check("网盘模式：关闭后保留用户值", load_settings().get("execution.batch_size") == 3)

        # 2) 风控预检：只提示，不中止
        risk_root = root / "preflight"
        risk_src = risk_root / "src"
        risk_dst = risk_root / "dst"
        risk_src.mkdir(parents=True, exist_ok=True)
        risk_dst.mkdir(parents=True, exist_ok=True)
        _write(risk_src / "a.mkv")
        _write(risk_src / "b.mkv")
        risk_groups = [{"id": "g1", "name": "预检组", "source": str(risk_src), "target": str(risk_dst)}]
        base_item = {
            "group_id": "g1",
            "original_path": str(risk_src / "a.mkv"),
            "target_path": str(risk_dst / "a.mkv"),
            "status": "planned",
        }
        check("预检：正常路径不提示", check_execution([base_item], risk_groups, risk_root) == [])
        bad_name = dict(base_item, target_path=str(risk_dst / "bad?name.mkv"))
        codes = {risk["code"] for risk in check_execution([bad_name], risk_groups, risk_root)}
        check("预检：非法文件名给出提示", "name-invalid" in codes, str(codes))
        outside = dict(base_item, target_path=str(risk_root / "outside" / "a.mkv"))
        codes = {risk["code"] for risk in check_execution([outside], risk_groups, risk_root)}
        check("预检：目标超出目录组给出提示", "target-outside-group" in codes, str(codes))
        unreachable = [
            {"id": "g2", "name": "未挂载", "source": str(risk_src), "target": str(risk_root / "missing" / "deep")}
        ]
        codes = {risk["code"] for risk in check_execution([base_item], unreachable, risk_root)}
        check("预检：目标不可达给出提示", "target-unreachable" in codes, str(codes))
        import app.core.preflight as preflight_module  # noqa: E402

        original_limit = preflight_module.LARGE_DIR_FILE_COUNT
        preflight_module.LARGE_DIR_FILE_COUNT = 2
        try:
            for number in range(3):
                _write(risk_dst / f"extra-{number}.mkv")
            codes = {risk["code"] for risk in check_execution([base_item], risk_groups, risk_root)}
        finally:
            preflight_module.LARGE_DIR_FILE_COUNT = original_limit
        check("预检：目标目录文件过多给出提示", "target-dir-large" in codes, str(codes))

        # 3) 低 IO 扫描：开关两种模式扫同一份样本，结果必须完全一致
        low_root = root / "lowio"
        low_src = low_root / "src"
        for rel in (
            "作品/Season 1/作品 - S01E01.mkv",
            "作品/Season 1/作品 - S01E02.mkv",
            "作品/Season 1/作品 - S01E02.ass",
            "作品/特典/作品 - NCOP01.mkv",
            "作品/字幕组/.ignore",
        ):
            _write(low_src / rel)
        low_groups = [{"id": "lg", "name": "低IO组", "source": str(low_src), "target": str(low_root / "dst")}]

        def scan_shape(scan_result: dict) -> list[tuple]:
            return sorted(
                (
                    row.get("path"),
                    row.get("name"),
                    row.get("size"),
                    row.get("kind"),
                    row.get("category"),
                    row.get("special_folder") or "",
                )
                for row in scan_result.get("files") or []
            )

        save_settings({"execution.network_drive_mode": False})
        scan_off = scan_groups(low_groups, low_root, load_settings(), [])
        save_settings({"execution.network_drive_mode": True})
        scan_on = scan_groups(low_groups, low_root, load_settings(), [])
        check("网盘模式：扫描结果与关闭时完全一致", scan_shape(scan_off) == scan_shape(scan_on))
        check(
            "网盘模式：忽略目录判定与关闭时一致",
            sorted(scan_off.get("ignored") or []) == sorted(scan_on.get("ignored") or []),
        )

        # 4) 低 IO 执行：同一个目录组、同一批文件，两种模式的落点与源目录剩余完全一致
        def run_low_exec(work: Path, mode: bool) -> tuple:
            exec_src = work / "src"
            exec_dst = work / "dst"
            for index in range(3):
                _write(exec_src / f"片名 - {index + 1:02d}.mkv")
            exec_groups = [
                {"id": "eg", "name": "执行组", "source": str(exec_src), "target": str(exec_dst)}
            ]
            group_map = build_group_map(exec_groups, work)
            plan = [
                {
                    "group_id": "eg",
                    "file_id": f"f{index}",
                    "seq": index + 1,
                    "category": "episode",
                    "original_path": str(exec_src / f"片名 - {index + 1:02d}.mkv"),
                    "target_path": str(exec_dst / f"片名 - {index + 1:02d}.mkv"),
                    "status": "planned",
                }
                for index in range(3)
            ]
            index_cache = DirIndex(low_io=mode)
            outcomes: list[str] = []
            done: list[dict] = []
            for item in plan:
                status, _target, _message = execute_item(
                    item, group_map, work, "skip", index=index_cache
                )
                outcomes.append(status)
                item["status"] = status
                if status == "done":
                    done.append(item)
            cleanup_done_sources(done, group_map, work, low_io=mode)
            target_tree = sorted(
                (path.relative_to(exec_dst).as_posix(), path.stat().st_size)
                for path in exec_dst.rglob("*")
                if path.is_file()
            )
            source_left = sorted(
                path.relative_to(exec_src).as_posix()
                for path in exec_src.rglob("*")
                if path.is_file()
            )
            # 撤销能力必须一致：同样的计划、同样的开关，还原后的源目录与目标剩余都要相同
            undo_items(plan, exec_groups, work, keep_empty_dirs=False, low_io=mode)
            restored = sorted(
                (path.relative_to(exec_src).as_posix(), path.stat().st_size)
                for path in exec_src.rglob("*")
                if path.is_file()
            )
            target_after_undo = sorted(
                path.relative_to(exec_dst).as_posix()
                for path in exec_dst.rglob("*")
                if path.is_file()
            )
            return outcomes, target_tree, source_left, restored, target_after_undo

        exec_off = run_low_exec(root / "lowio" / "exec-off", False)
        exec_on = run_low_exec(root / "lowio" / "exec-on", True)
        check("网盘模式：执行落点与关闭时完全一致", exec_off[:3] == exec_on[:3], str(exec_on[:3]))
        check("网盘模式：撤销结果与关闭时完全一致", exec_off[3:] == exec_on[3:], str(exec_on[3:]))
        save_settings({"execution.network_drive_mode": False})
        # 恢复成回归后面步骤预期的批大小
        save_settings(
            {
                "execution.batch_size": 5,
                "execution.batch_delay_seconds": 0,
                "execution.retry_times": 0,
                "execution.retry_backoff_seconds": 1,
            }
        )

        # ---------------- 8. 规则编辑（临时配置，不碰真实规则） ----------------
        from app.core.rules import validate_rule_payload

        bad = validate_rule_payload(
            {"name": "坏正则", "scope": "filename", "kind": "episode", "priority": 0, "regex": "("}
        )
        check("规则：非法正则会给出中文说明", bool(bad) and "正则" in bad[0], bad[0] if bad else "")
        rule = create_rule(
            {"name": "临时规则", "scope": "filename", "kind": "episode", "priority": 3, "regex": r"\d{1,4}"}
        )
        check("规则：可以新增自定义规则", bool(rule.get("id")) and rule.get("builtin") is False)
        check("规则：删除自定义规则成功", delete_rule(rule["id"]))

        builtin = next((item for item in list_rule_dicts() if item.get("builtin")), None)
        if builtin:
            payload = {key: builtin[key] for key in ("name", "scope", "kind", "priority", "regex", "description", "sample")}
            update_rule(builtin["id"], {**payload, "enabled": False})
            disabled = next(item for item in list_rule_dicts() if item["id"] == builtin["id"])
            check("规则：内置规则可以停用", disabled["enabled"] is False)
            custom = create_rule(
                {"name": "恢复默认测试", "scope": "filename", "kind": "episode", "priority": 5, "regex": r"\d+"}
            )
            reset_builtin_rules()
            after = {item["id"]: item for item in list_rule_dicts()}
            check("规则：恢复内置默认会重新启用内置规则", after[builtin["id"]]["enabled"] is True)
            check("规则：恢复内置默认保留自定义规则", custom["id"] in after)
            delete_rule(custom["id"])
        else:
            fail("规则：没有找到内置规则", "")

        # ---------------- 9. 多组指向同一作品时的季号分配 ----------------
        from app.core.season_plan import build_season_plan, detect_clue

        clue_cases = {
            "Season 2": 2,
            "S02": 2,
            "第二季": 2,
            "2nd Season": 2,
        }
        clue_ok = all(
            (detect_clue([text]) or {}).get("season") == expected
            for text, expected in clue_cases.items()
        )
        check("季号线索：Season 2 / S02 / 第二季 / 2nd Season 都能识别", clue_ok, str(
            {text: detect_clue([text]) for text in clue_cases}
        ))
        zoku = detect_clue(["Iru Zoku"])
        kan = detect_clue(["Iru Kan"])
        check(
            "季号线索：续作标记 Zoku / Kan 被识别为第 2 / 3 部",
            (zoku or {}).get("rank") == 2 and (kan or {}).get("rank") == 3,
            f"{zoku} / {kan}",
        )
        check(
            "季号线索：S02E01 和 Kanon 不会误判",
            detect_clue(["作品 S02E01.mkv"]) is None and detect_clue(["Kanon"]) is None,
        )

        def fake_file(fid: str, work_id: str, rel: str, episode: int = 1) -> dict:
            return {
                "id": fid,
                "category": "video",
                "kind": "episode",
                "episode": episode,
                "season": 1,
                "rel_path": rel,
                "filename": rel.split("/")[-1],
                "identify": {"work_id": work_id, "title": work_id, "year": None},
                "fields": {"tmdb_id": "65676", "series": "同一部作品"},
            }

        w_plain, w_zoku, w_kan, w_manual = "w:1", "w:2", "w:3", "w:4"
        w_invalid, w_foo = "w:5", "w:6"
        w_conflict_a, w_conflict_b = "w:7", "w:8"
        w_solo, w_solo_zoku, w_solo_kan = "w:9", "w:10", "w:11"
        fake_scan = {
            "files": [
                fake_file("f1", w_plain, "同一部作品/第01集.mkv"),
                fake_file("f2", w_zoku, "同一部作品 Zoku/第01集.mkv"),
                fake_file("f3", w_kan, "同一部作品 Kan/第01集.mkv"),
                fake_file("f4", w_manual, "同一部作品 手动/第01集.mkv"),
                fake_file("f5", w_invalid, "别的作品 Season 5/第01集.mkv"),
                fake_file("f6", w_foo, "别的作品 无标记/第01集.mkv"),
                fake_file("f7", w_conflict_a, "撞车作品 Season 2/第01集.mkv"),
                fake_file("f8", w_conflict_b, "撞车作品 第二季/第01集.mkv"),
                fake_file("f9", w_solo, "单独作品/第01集.mkv"),
                fake_file("f10", w_solo_zoku, "单独作品 Zoku/第01集.mkv"),
                fake_file("f11", w_solo_kan, "单独作品 Kan/第01集.mkv"),
            ],
            "season_state": {
                w_plain: {"tmdb_id": "65676", "mode": "pending", "choice": None,
                          "seasons": [
                              {"season_number": 1, "name": "", "episode_count": 13},
                              {"season_number": 2, "name": "", "episode_count": 13},
                              {"season_number": 3, "name": "", "episode_count": 12},
                          ]},
                w_zoku: {"tmdb_id": "65676", "mode": "pending", "choice": None, "seasons": []},
                w_kan: {"tmdb_id": "65676", "mode": "pending", "choice": None, "seasons": []},
                w_manual: {"tmdb_id": "65676", "mode": "manual", "choice": 1, "seasons": []},
                w_invalid: {"tmdb_id": "65676", "mode": "pending", "choice": None,
                            "seasons": [
                                {"season_number": 1, "name": "", "episode_count": 13},
                                {"season_number": 2, "name": "", "episode_count": 13},
                            ]},
                w_foo: {"tmdb_id": "65676", "mode": "pending", "choice": None, "seasons": []},
                w_conflict_a: {"tmdb_id": "65676", "mode": "pending", "choice": None,
                               "seasons": [
                                   {"season_number": 1, "name": "", "episode_count": 13},
                                   {"season_number": 2, "name": "", "episode_count": 13},
                               ]},
                w_conflict_b: {"tmdb_id": "65676", "mode": "pending", "choice": None, "seasons": []},
                w_solo: {"tmdb_id": "65676", "mode": "pending", "choice": None,
                         "seasons": [
                             {"season_number": 1, "name": "", "episode_count": 13},
                             {"season_number": 2, "name": "", "episode_count": 13},
                             {"season_number": 3, "name": "", "episode_count": 12},
                         ]},
                w_solo_zoku: {"tmdb_id": "65676", "mode": "pending", "choice": None, "seasons": []},
                w_solo_kan: {"tmdb_id": "65676", "mode": "pending", "choice": None, "seasons": []},
            },
        }
        tmdb_map = {
            w_plain: "100", w_zoku: "100", w_kan: "100", w_manual: "100",
            w_invalid: "200", w_foo: "200", w_conflict_a: "300", w_conflict_b: "300",
            w_solo: "400", w_solo_zoku: "400", w_solo_kan: "400",
        }
        # 每部“作品”用不同 TMDB 编号，避免样本互相混在一起
        for row in fake_scan["files"]:
            row["fields"]["tmdb_id"] = tmdb_map[row["identify"]["work_id"]]
        for key, tmdb_id in tmdb_map.items():
            fake_scan["season_state"][key]["tmdb_id"] = tmdb_id

        plans = {item["tmdb_id"]: item for item in build_season_plan(fake_scan)}
        main_plan = plans.get("100") or {}
        entries = {item["group_id"]: item for item in main_plan.get("groups") or []}
        check("季号分配：同编号的多组会被归到一起", len(main_plan.get("groups") or []) == 4)
        check(
            "季号分配：Zoku 按第 2 部对应第 2 季",
            entries.get(w_zoku, {}).get("action") == "assign"
            and entries.get(w_zoku, {}).get("proposed_season") == 2,
            str(entries.get(w_zoku)),
        )
        check(
            "季号分配：Kan 按第 3 部对应第 3 季",
            entries.get(w_kan, {}).get("action") == "assign"
            and entries.get(w_kan, {}).get("proposed_season") == 3,
            str(entries.get(w_kan)),
        )
        check(
            "季号分配：没有线索的组不硬排",
            entries.get(w_plain, {}).get("action") == "unresolved"
            and entries.get(w_plain, {}).get("proposed_season") is None,
            str(entries.get(w_plain)),
        )
        check(
            "季号分配：手动设置过季号的组不会被覆盖",
            entries.get(w_manual, {}).get("action") == "keep_manual"
            and "不会被覆盖" in str(entries.get(w_manual, {}).get("reason")),
            str(entries.get(w_manual)),
        )
        check(
            "季号分配：修前会提示撞了几处",
            main_plan.get("before_collisions") == 1,
            str(main_plan.get("before_collisions")),
        )
        check(
            "季号分配：分配后仍有撞车时指出是哪几组、哪一集",
            len(main_plan.get("collisions") or []) == 1
            and set((main_plan["collisions"][0] or {}).get("groups") or []) == {w_plain, w_manual},
            str(main_plan.get("collisions")),
        )
        invalid_plan = plans.get("200") or {}
        invalid_entries = {item["group_id"]: item for item in invalid_plan.get("groups") or []}
        check(
            "季号分配：线索指向不存在的季时丢弃并说明",
            invalid_entries.get(w_invalid, {}).get("action") == "invalid_clue"
            and invalid_entries.get(w_invalid, {}).get("proposed_season") is None
            and "TMDB 只有第 1、2 季" in str(invalid_entries.get(w_invalid, {}).get("message")),
            str(invalid_entries.get(w_invalid)),
        )
        conflict_plan = plans.get("300") or {}
        conflict_entries = {item["group_id"]: item for item in conflict_plan.get("groups") or []}
        check(
            "季号分配：两组抢同一季时不自动分配，并点名是哪两组",
            all(
                conflict_entries.get(key, {}).get("action") == "conflict"
                and conflict_entries.get(key, {}).get("proposed_season") is None
                for key in (w_conflict_a, w_conflict_b)
            )
            and "w:8" in str(conflict_entries.get(w_conflict_a, {}).get("message"))
            and "w:7" in str(conflict_entries.get(w_conflict_b, {}).get("message")),
            str(conflict_entries),
        )
        solo_plan = plans.get("400") or {}
        solo_entries = {item["group_id"]: item for item in solo_plan.get("groups") or []}
        check(
            "季号分配：按线索分好后不再撞车（修前有、修后没有）",
            solo_plan.get("before_collisions") == 1
            and solo_plan.get("collisions") == []
            and solo_entries.get(w_solo, {}).get("current_season") == 1
            and solo_entries.get(w_solo_zoku, {}).get("proposed_season") == 2
            and solo_entries.get(w_solo_kan, {}).get("proposed_season") == 3,
            str({
                "before": solo_plan.get("before_collisions"),
                "after": solo_plan.get("collisions"),
                "groups": solo_entries,
            }),
        )

        # ---- AI 命名规律：结构描述校验 + 本地编译 ----
        good_structure = {
            "rules": [
                {"role": "special_type", "container": "【】", "match": "纯文字", "take": "第一个"},
                {"role": "episode", "container": "【】", "match": "纯数字", "take": "第一个"},
            ]
        }
        check("命名规律：合法结构可以通过校验", validate_structure(good_structure) == [])

        bad_regex = {
            "rules": [
                {
                    "role": "episode",
                    "container": "[]",
                    "match": "纯数字",
                    "take": "第一个",
                    "regex": r"\d+",
                }
            ]
        }
        bad = validate_structure(bad_regex)
        check(
            "命名规律：AI 输出正则或文件名字段会被拒绝并给中文说明",
            bool(bad) and "正则" in bad[0],
            bad[0] if bad else "",
        )

        bad_role = {"rules": [{"role": "disc", "container": "[]", "match": "纯数字", "take": "第一个"}]}
        check(
            "命名规律：不认识的角色会被拒绝",
            any("disc" in item for item in validate_structure(bad_role)),
        )

        bad_take = {"rules": [{"role": "episode", "container": "[]", "match": "纯数字", "take": "第0个"}]}
        check("命名规律：第 0 个这种取值会被拒绝", bool(validate_structure(bad_take)))

        bad_range = {"rules": [{"role": "episode", "container": "[]", "match": "区间", "take": "第一个"}]}
        check(
            "命名规律：区间不能当集号",
            any("不能配" in item for item in validate_structure(bad_range)),
        )

        compiled = compile_structure(good_structure)
        disc = apply_structure(compiled, "【OP】【Disc-41】【DVDrip】[x264 Hi10p AAC]")
        check("命名规律：能从 Disc-41 里取出集号 41", disc["values"].get("episode") == 41, str(disc["values"]))
        check(
            "命名规律：判断依据会指出 41 来自 Disc-41 这一段",
            any("41 是集号" in item["note"] and "Disc-41" in item["note"] for item in disc["evidence"]),
            str(disc["evidence"]),
        )
        check(
            "命名规律：Disc 这类碟号会给出可疑提示",
            any("碟片" in item for item in disc["suspects"]),
            str(disc["suspects"]),
        )

        range_compiled = compile_structure(
            {"rules": [{"role": "range", "container": "[]", "match": "区间", "take": "第一个"}]}
        )
        range_result = apply_structure(range_compiled, "[01-13][1080p]")
        check(
            "命名规律：01-13 被识别成区间，不当集号",
            range_result["values"].get("range") == "01-13"
            and range_result["values"].get("range_start") == 1
            and range_result["values"].get("range_end") == 13
            and "episode" not in range_result["values"],
            str(range_result["values"]),
        )

        decimal_compiled = compile_structure(
            {"rules": [{"role": "episode", "container": "[]", "match": "小数", "take": "第一个"}]}
        )
        decimal_result = apply_structure(decimal_compiled, "[24.5][1080p]")
        check(
            "命名规律：24.5 按真实小数保存，不压成 24",
            decimal_result["values"].get("episode") == 24.5
            and decimal_result["values"].get("episode_text") == "24.5",
            str(decimal_result["values"]),
        )

        v2_compiled = compile_structure(
            {"rules": [{"role": "episode", "container": "无", "match": "数字+v2", "take": "第一个"}]}
        )
        v2_result = apply_structure(v2_compiled, "Frieren - 01v2 [1080p]")
        check(
            "命名规律：01v2 同时取出集号 1 和版本 2",
            v2_result["values"].get("episode") == 1 and v2_result["values"].get("version") == 2,
            str(v2_result["values"]),
        )

        check(
            "命名规律：文件形状把数字换成 N、字母换成 A",
            shape_of("Frieren S01E03 [1080p]") == shape_of("Frieren S02E09 [720p]")
            and shape_of("Disc-41") == "A-N",
            f"{shape_of('Frieren S01E03 [1080p]')} / {shape_of('Disc-41')}",
        )
        same_fp = fingerprint("g1", "作品/Season 1", "Frieren - 01.mkv")
        check(
            "命名规律：同组同目录同形状的指纹一致，换目录就不一致",
            same_fp == fingerprint("g1", "作品/Season 1", "Frieren - 02.mkv")
            and same_fp != fingerprint("g1", "作品/Season 2", "Frieren - 01.mkv"),
        )

        # ---- 形状抽样：形状签名必须区分括号顺序、内容类型与版本/区间/小数 ----
        check(
            "形状抽样：括号顺序不同会分到不同形状",
            shape_signature("【01】【标题】") != shape_signature("【标题】【01】"),
            f"{shape_signature('【01】【标题】')} / {shape_signature('【标题】【01】')}",
        )
        check(
            "形状抽样：同是技术标签的不同写法会被归为同一形状",
            shape_signature("【01】【1080p】") == shape_signature("【01】【x264】"),
            f"{shape_signature('【01】【1080p】')} / {shape_signature('【01】【x264】')}",
        )
        check(
            "形状抽样：版本号 / 区间 / 小数会写进形状标记",
            "#V" in shape_signature("Frieren - 01v2")
            and "#R" in shape_signature("[01-13]")
            and "#D" in shape_signature("[24.5]"),
            f"{shape_signature('Frieren - 01v2')} / {shape_signature('[01-13]')} / {shape_signature('[24.5]')}",
        )
        check(
            "命名规律：take 允许 AI 常见的「第 2 个」写法（带空格）",
            not validate_structure(
                {"rules": [{"role": "episode", "container": "【】", "match": "纯数字", "take": "第 2 个"}]}
            ),
            str(validate_structure(
                {"rules": [{"role": "episode", "container": "【】", "match": "纯数字", "take": "第 2 个"}]}
            )),
        )

        # ---- 数字带字：ED01 / OP02 / NCOP01 / SP02 / CM03 ----
        glued_structure = {
            "rules": [
                {"role": "special_type", "container": "()", "match": "数字带字", "take": "第一个"},
                {"role": "special_no", "container": "()", "match": "数字带字", "take": "第一个"},
            ]
        }
        glued_errors = validate_structure(glued_structure)
        check(
            "命名规律：数字带字可以配特典类型 + 特典序号",
            glued_errors == [],
            str(glued_errors),
        )
        for sample_name, want_type, want_no, want_text in (
            ("(ED01) [1080p].mkv", "ED", 1, "01"),
            ("(OP02).mkv", "OP", 2, "02"),
            ("(NCOP01).mkv", "NCOP", 1, "01"),
            ("(SP02).mkv", "SP", 2, "02"),
            ("(CM03).mkv", "CM", 3, "03"),
        ):
            glued_values = apply_structure(compile_structure(glued_structure), sample_name)["values"]
            check(
                f"命名规律：{sample_name} 拆出类型 {want_type}、序号 {want_no}",
                glued_values.get("special_type") == want_type
                and glued_values.get("special_no") == want_no
                and glued_values.get("special_no_text") == want_text,
                str(glued_values),
            )

        glued_name, glued_name_errors, _ = build_filename(
            {
                "fields": {
                    "series": "作品丙",
                    "season": 0,
                    "special_type": "NCOP",
                    "special_no": "01",
                    "ext": "mkv",
                }
            },
            {"pattern": "{series} - {special_type}{special_no}", "output_mode": "filename"},
            settings,
        )
        check(
            "命名规律：数字带字的类型与序号能用于现有 {special_type}{special_no} 模板",
            glued_name == "作品丙 - NCOP01.mkv" and not glued_name_errors,
            glued_name,
        )

        bad_glued = {"rules": [{"role": "episode", "container": "()", "match": "数字带字", "take": "第一个"}]}
        check(
            "命名规律：数字带字不能当集号",
            any("不能配" in item for item in validate_structure(bad_glued)),
            str(validate_structure(bad_glued)),
        )

        raw_tag_structure = {
            "rules": [
                {"role": "special_type", "container": "[]", "match": "技术标签", "take": "第一个"},
                {"role": "special_no", "container": "()", "match": "数字带字", "take": "第一个"},
            ]
        }
        raw_tag_values = apply_structure(
            compile_structure(raw_tag_structure), "[BD] (SP02).mkv"
        )["values"]
        check(
            "命名规律：特典类型可以直接取括号里的原文，不必归到固定标签",
            raw_tag_values.get("special_type") == "BD" and raw_tag_values.get("special_no") == 2,
            str(raw_tag_values),
        )

        # ---- 小数集号：24 与 24.5 必须当成两集 ----
        check(
            "集号取值：原文 24.5 会按 24.5 参与比较",
            episode_value({"episode": 24, "episode_exact": "24.5"}) == 24.5,
        )

        def _dup_row(file_id: str, episode, exact: str, filename: str) -> dict:
            return {
                "id": file_id,
                "category": "video",
                "kind": "episode",
                "season": 1,
                "episode": episode,
                "episode_exact": exact,
                "filename": filename,
                "rel_path": f"作品/Season 1/{filename}",
                "group_id": "g1",
                "identify": {"work_id": "w1", "title": "作品甲"},
                "fields": {},
            }

        dup_rows = [
            _dup_row("a", 24, "", "作品甲 - 第24话.mkv"),
            _dup_row("b", 24.5, "24.5", "作品甲 - OVA 24.5.mkv"),
            _dup_row("c", 24.5, "24.5", "作品甲 - OVA 24.5 修正版.mkv"),
        ]
        dup_groups = collect_groups(dup_rows)
        check(
            "重复集：第 24 集和 24.5 不算重复",
            len(dup_groups) == 1 and all(key.endswith("E24.5") for key in dup_groups),
            str(list(dup_groups)),
        )
        dup_described = describe_groups(dup_rows)
        check(
            "重复集：两份 24.5 仍判重复，排序不报错",
            len(dup_described) == 1
            and dup_described[0]["episode"] == 24.5
            and len(dup_described[0]["files"]) == 2,
            str(dup_described),
        )

        # ---- 命名规律接入解析：只记录，不生效 ----
        pattern_row = {
            "id": "p1",
            "group_id": "g1",
            "category": "video",
            "rel_path": "作品甲/Season 1/作品甲 - 04.mkv",
            "filename": "作品甲 - 04.mkv",
            "kind": "episode",
            "season": 1,
            "episode": 3,
            "special_type": "",
            "special_no": "",
            "collection": False,
            "fields": {"version": ""},
        }
        before_fields = {
            key: pattern_row.get(key)
            for key in ("kind", "season", "episode", "special_type", "special_no", "collection")
        }
        prepared = prepare_patterns(
            [
                {
                    "id": "pat-1",
                    "fingerprint": fingerprint("g1", "作品甲/Season 1", "作品甲 - 04.mkv"),
                    "source": "ai_confirmed",
                    "structure": {
                        "rules": [
                            {"role": "episode", "container": "无", "match": "纯数字", "take": "最后一个"}
                        ]
                    },
                }
            ]
        )
        annotate_pattern(pattern_row, prepared)
        check(
            "命名规律接入：只记录候选，不修改现有识别字段",
            all(pattern_row.get(key) == value for key, value in before_fields.items())
            and pattern_row.get("pattern_candidate", {}).get("values", {}).get("episode") == 4,
            str(pattern_row.get("pattern_candidate")),
        )
        check(
            "命名规律接入：AI 与内置集号不同会记为冲突",
            pattern_row.get("pattern_conflict", {})
            .get("fields", {})
            .get("episode", {})
            == {"builtin": 3, "ai": 4, "evidence": pattern_row["pattern_candidate"]["evidence"]},
            str(pattern_row.get("pattern_conflict")),
        )
        untouched = {
            "id": "p2",
            "group_id": "g1",
            "category": "video",
            "rel_path": "作品甲/Season 2/作品甲 - 05.mkv",
            "filename": "作品甲 - 05.mkv",
            "kind": "episode",
            "season": 1,
            "episode": 5,
            "fields": {},
        }
        annotate_pattern(untouched, prepared)
        check(
            "命名规律接入：指纹对不上时不记录任何东西",
            "pattern_candidate" not in untouched and "pattern_conflict" not in untouched,
            str(sorted(untouched)),
        )

        # ---- 第 3 步：5 项自检 + 失败只重问一次 + 整批放弃 ----
        import copy  # noqa: E402

        from app.core.pattern_check import (  # noqa: E402
            _numbers_check,
            evaluate_structure,
            propose_with_retry,
        )

        def pattern_row(
            file_id: str,
            name: str,
            episode=None,
            kind: str = "episode",
        ) -> dict:
            rel = f"作品丙/Season 1/{name}"
            return {
                "id": file_id,
                "group_id": "pg",
                "category": "video",
                "kind": kind,
                "path": str(source / rel),
                "rel_path": rel,
                "parent": str((source / rel).parent),
                "filename": name,
                "season": 0 if kind == "special" else 1,
                "episode": episode,
                "episode_exact": "",
                "special_type": "NCOP" if kind == "special" else "",
                "special_no": "",
                "collection": False,
                "fields": {
                    "series": "作品丙",
                    "year": 2013,
                    "season": 0 if kind == "special" else 1,
                    "episode": "" if episode is None else str(episode),
                    "special_type": "NCOP" if kind == "special" else "",
                    "special_no": "",
                    "ext": "mkv",
                    "tmdb_id": "731",
                },
                "identify": {"work_id": "wp", "title": "作品丙", "year": 2013},
            }

        def pattern_scan(items: list[dict]) -> dict:
            return {
                "root": str(source),
                "groups": [
                    {"id": "pg", "source": str(source), "target": str(target / "规律测试")}
                ],
                "files": items,
            }

        def check_of(report: dict, key: str) -> dict:
            return next(item for item in report.get("checks") or [] if item.get("key") == key)

        # ---- 形状抽样规则：稀有全带、每桶首尾、上限削减可解释 ----
        common_names = [f"作品丁 - {index:03d} [1080p].mkv" for index in range(1, 101)]
        rare_name = "【特别篇】【x264】【10bit】.mkv"
        sample_report = sample_filenames(common_names + [rare_name], 10)
        check(
            "抽样：只出现 1 次的稀有形状必须被送进样本",
            rare_name in sample_report["filenames"],
            str(sample_report["filenames"]),
        )
        check(
            "抽样：总条数不超过上限，并记录被削掉的形状与条数",
            sample_report["sent"] == 10
            and sample_report["total"] == 101
            and bool(sample_report["trimmed"])
            and sample_report["trimmed"][0]["dropped"] > 0,
            str(sample_report["trimmed"]),
        )
        check(
            "抽样：每个常见形状至少保留最靠前与最靠后各一条",
            common_names[0] in sample_report["filenames"]
            and common_names[-1] in sample_report["filenames"],
            str(sample_report["filenames"]),
        )

        cn_episode = compile_structure(
            {"rules": [{"role": "episode", "container": "【】", "match": "纯数字", "take": "第一个"}]}
        )

        # 案例 1：Disc-41 / 42 / 43 恰好连续 —— 自检会通过（证明拦不住这类自洽错误）
        disc_scan = pattern_scan(
            [
                pattern_row("disc41", "【Disc-41】【作品丙】【DVDrip】【1080p】.mkv", 41),
                pattern_row("disc42", "【Disc-42】【作品丙】【DVDrip】【1080p】.mkv", 42),
                pattern_row("disc43", "【Disc-43】【作品丙】【DVDrip】【1080p】.mkv", 43),
            ]
        )
        disc_report = evaluate_structure(
            disc_scan, cn_episode, preset, settings, "pg", "作品丙/Season 1"
        )
        check(
            "自检 Disc-41/42/43：5 项全部通过（自检拦不住自洽但错误的结果）",
            disc_report["passed"] and all(item["ok"] for item in disc_report["checks"]),
            str([(item["key"], item["ok"], item["detail"]) for item in disc_report["checks"]]),
        )
        disc_names = [item["new_name"] for item in disc_report["sample"]]
        check(
            "自检 Disc-41/42/43：样例预览给出前 3 条真实改名结果",
            disc_names == ["作品丙 - S01E41.mkv", "作品丙 - S01E42.mkv", "作品丙 - S01E43.mkv"],
            str(disc_names),
        )
        check(
            "自检 Disc-41/42/43：样例预览逐条带判断依据和碟号可疑提示",
            all(
                any("Disc-4" in note and "集号" in note for note in item["evidence"])
                and any("碟片" in text for text in item["suspects"])
                for item in disc_report["sample"]
            ),
            str(
                [
                    (item["original"], item["evidence"], item["suspects"])
                    for item in disc_report["sample"]
                ]
            ),
        )
        check(
            "自检 Disc-41/42/43：AI 与内置结果一致时样例不会误标成“改过”",
            all(item["changed"] is False for item in disc_report["sample"]),
            str([item["changed"] for item in disc_report["sample"]]),
        )

        # 案例 2：匹配率不足
        mixed_report = evaluate_structure(
            pattern_scan(
                [
                    pattern_row("mix1", "【01】【x264】.mkv", 1),
                    pattern_row("mix2", "【02】【x264】.mkv", 2),
                    pattern_row("mix3", "作品丙 - 03.mkv", 3),
                ]
            ),
            cn_episode,
            preset,
            settings,
            "pg",
            "作品丙/Season 1",
        )
        match_check = check_of(mixed_report, "match")
        check(
            "自检：有文件解释不了时匹配率被拦下并点名",
            not match_check["ok"]
            and "作品丙 - 03.mkv" in match_check["examples"]
            and not mixed_report["passed"],
            str(match_check),
        )

        # 案例 3：集号重号
        dup_report = evaluate_structure(
            pattern_scan(
                [
                    pattern_row("dup1", "【01】【x264】.mkv", 1),
                    pattern_row("dup2", "【01】【HEVC】.mkv", 1),
                ]
            ),
            cn_episode,
            preset,
            settings,
            "pg",
            "作品丙/Season 1",
        )
        dup_numbers = check_of(dup_report, "numbers")
        check(
            "自检：集号重号会被拦下并说明是重复集号",
            not dup_numbers["ok"]
            and "重复集号" in dup_numbers["detail"]
            and not dup_report["passed"],
            str(dup_numbers),
        )

        # 案例 4：缺号
        gap_report = evaluate_structure(
            pattern_scan(
                [
                    pattern_row("gap1", "【01】【x264】.mkv", 1),
                    pattern_row("gap3", "【03】【x264】.mkv", 3),
                ]
            ),
            cn_episode,
            preset,
            settings,
            "pg",
            "作品丙/Season 1",
        )
        gap_numbers = check_of(gap_report, "numbers")
        check(
            "自检：缺号会被拦下并指出缺第 2 集",
            not gap_numbers["ok"]
            and "缺号" in gap_numbers["detail"]
            and "2" in gap_numbers["detail"]
            and not gap_report["passed"],
            str(gap_numbers),
        )

        # 案例 4b：小数集号按真实数值判重（24 与 24.5 是两集）
        check(
            "自检：24 与 24.5 视为不同集号，两份 24.5 才算重号",
            _numbers_check([24, 24.5, 25])[0] is True
            and _numbers_check([24.5, 24.5])[0] is False
            and "24.5" in _numbers_check([24.5, 24.5])[1],
            str([_numbers_check([24, 24.5, 25]), _numbers_check([24.5, 24.5])]),
        )

        # 案例 5：特典被当正片
        special_report = evaluate_structure(
            pattern_scan([pattern_row("sp1", "【01】【NCOP】【1080p】.mkv", None, kind="special")]),
            cn_episode,
            preset,
            settings,
            "pg",
            "作品丙/Season 1",
        )
        special_check = check_of(special_report, "special")
        check(
            "自检：特典被当正片会被拦下并点名",
            not special_check["ok"]
            and "特典" in special_check["detail"]
            and "【01】【NCOP】【1080p】.mkv" in special_check["examples"]
            and not special_report["passed"],
            str(special_check),
        )

        # 案例 6：套模板后目标路径重复（模板里不带集号）
        flat_preset = {
            "id": "flat",
            "name": "同名模板",
            "templates": {
                "episode": {"pattern": "{series}.{ext}"},
                "special": {"pattern": "{series}.{ext}"},
                "movie": {"pattern": "{series}.{ext}"},
            },
        }
        paths_report = evaluate_structure(
            pattern_scan(
                [
                    pattern_row("path1", "【01】【x264】.mkv", 1),
                    pattern_row("path2", "【02】【x264】.mkv", 2),
                ]
            ),
            cn_episode,
            flat_preset,
            settings,
            "pg",
            "作品丙/Season 1",
        )
        paths_check = check_of(paths_report, "paths")
        check(
            "自检：套模板后目标路径重复会被拦下",
            not paths_check["ok"]
            and "同一目标路径" in paths_check["detail"]
            and not paths_report["passed"],
            str(paths_check),
        )
        check(
            "自检：路径重复用例里其它 4 项仍然通过",
            all(item["ok"] for item in paths_report["checks"] if item["key"] != "paths"),
            str([(item["key"], item["ok"]) for item in paths_report["checks"]]),
        )

        # 案例 7：第一次不过 —— 带着对不上的文件重问一次后通过
        retry_calls: list[dict] = []

        def retry_ai(filenames: list[str], context: dict | None) -> dict:
            retry_calls.append({"filenames": list(filenames), "context": context})
            if len(retry_calls) == 1:
                return {"rules": [{"role": "episode", "container": "[]", "match": "纯数字", "take": "第一个"}]}
            return {"rules": [{"role": "episode", "container": "【】", "match": "纯数字", "take": "第一个"}]}

        retry_scan = pattern_scan(
            [
                pattern_row("rt1", "【01】【x264】.mkv", 1),
                pattern_row("rt2", "【02】【x264】.mkv", 2),
            ]
        )
        retried = propose_with_retry(
            retry_scan, "pg", "作品丙/Season 1", retry_ai, preset, settings
        )
        check(
            "重问：第一次不过时带着对不上的文件再问一次，第二次通过",
            retried["status"] == "proposal"
            and retried["attempts"] == 2
            and retry_calls[0]["context"] is None
            and (retry_calls[1]["context"] or {}).get("mismatched")
            == ["【01】【x264】.mkv", "【02】【x264】.mkv"]
            and "确认后才会生效" in str(retried["message"]),
            str(retried),
        )

        # 案例 8：两次都不过 —— 第二次只重问对不上的文件，之后整批放弃且扫描完全没变
        partial_scan = pattern_scan(
            [
                pattern_row("pa1", "【01】【x264】.mkv", 1),
                pattern_row("pa2", "【02】【x264】.mkv", 2),
                pattern_row("pa3", "作品丙 - 03.mkv", 3),
            ]
        )
        partial_before = copy.deepcopy(partial_scan)
        partial_calls: list[dict | None] = []

        def partial_ai(filenames: list[str], context: dict | None) -> dict:
            partial_calls.append(context)
            return {"rules": [{"role": "episode", "container": "【】", "match": "纯数字", "take": "第一个"}]}

        abandoned = propose_with_retry(
            partial_scan, "pg", "作品丙/Season 1", partial_ai, preset, settings
        )
        check(
            "整批放弃：第二次只重问对不上的那几个文件（不是整个目录）",
            len(partial_calls) == 2
            and partial_calls[0] is None
            and (partial_calls[1] or {}).get("mismatched") == ["作品丙 - 03.mkv"],
            str(partial_calls),
        )
        check(
            "整批放弃：明确说明缓存不写、识别字段不动、全部退回内置规则",
            abandoned["status"] == "abandoned"
            and "整批放弃" in str(abandoned["message"])
            and "缓存没有写入" in str(abandoned["message"])
            and "识别字段没有改动" in str(abandoned["message"]),
            str(abandoned),
        )
        check(
            "整批放弃：扫描数据逐字段完全没变（不可能出现半套用）",
            partial_scan == partial_before,
            "",
        )

        # 案例 8b：抽样只看首尾也能通过，但全量复核发现中间有重复集号 —— 必须补问
        sampling_files = ["【01】【x264】.mkv", "【02】【x264】.mkv", "【03】【x264】.mkv",
                          "【04】【x264】.mkv", "【05】【x264】.mkv", "【05】【DVDrip】.mkv",
                          "【06】【x264】.mkv", "【07】【x264】.mkv", "【08】【x264】.mkv",
                          "【09】【x264】.mkv"]
        sampling_scan = pattern_scan(
            [
                pattern_row(f"sm{index}", name, int(name[1:3]))
                for index, name in enumerate(sampling_files)
            ]
        )
        sampling_before = copy.deepcopy(sampling_scan)
        sampling_settings = dict(settings)
        sampling_settings["providers.ai_batch_limit"] = 2
        sampling_calls: list[dict] = []

        def sampling_ai(filenames: list[str], context: dict | None) -> dict:
            sampling_calls.append({"files": list(filenames), "context": context})
            return {"rules": [{"role": "episode", "container": "【】", "match": "纯数字", "take": "第一个"}]}

        sampling_result = propose_with_retry(
            sampling_scan, "pg", "作品丙/Season 1", sampling_ai, preset, sampling_settings
        )
        sampling_stats = sampling_result.get("sampling") or {}
        check(
            "抽样复核：第一次只发上限内的样本，但会在全量 10 条上复核",
            sampling_stats.get("total") == 10
            and sampling_stats.get("sent") == 2
            and len(sampling_calls[0]["files"]) == 2
            and sampling_result["status"] == "abandoned",
            str(sampling_stats) + " / " + str(sampling_result["status"]),
        )
        check(
            "抽样复核：抽样本身看不出的重复集号，全量复核能抓出来并触发补问",
            (sampling_calls[1]["context"] or {}).get("mismatched")
            == ["【05】【x264】.mkv", "【05】【DVDrip】.mkv"]
            and sampling_calls[1]["files"] == ["【05】【x264】.mkv", "【05】【DVDrip】.mkv"],
            str(sampling_calls),
        )
        check(
            "抽样复核：结果给出全量条数、通过率、补问条数，且没有半套用",
            (sampling_result.get("recheck") or {}).get("mismatched_total") == 2
            and (sampling_result.get("recheck") or {}).get("match_rate") == 0.8
            and (sampling_result.get("recheck") or {}).get("retry_sent") == 2
            and sampling_scan == sampling_before,
            str(sampling_result.get("recheck")),
        )

        # 案例 9：AI 返回非法结构（带正则）—— 重问一次仍非法就整批放弃
        illegal_calls: list[dict | None] = []

        def illegal_ai(filenames: list[str], context: dict | None) -> dict:
            illegal_calls.append(context)
            return {
                "rules": [
                    {
                        "role": "episode",
                        "container": "【】",
                        "match": "纯数字",
                        "take": "第一个",
                        "regex": r"\d+",
                    }
                ]
            }

        illegal = propose_with_retry(
            pattern_scan([pattern_row("il1", "【01】【x264】.mkv", 1)]),
            "pg",
            "作品丙/Season 1",
            illegal_ai,
            preset,
            settings,
        )
        check(
            "非法结构：第一次被拒后重问一次，第二次仍非法就整批放弃并给中文原因",
            illegal["status"] == "abandoned"
            and illegal["attempts"] == 2
            and len(illegal_calls) == 2
            and "正则" in str(illegal["checks"][0]["detail"]),
            str(illegal),
        )

        # ---- 第 4 步：规律缓存存储 + 指纹一致时自动套用 ----
        from app.core.pattern_cache import auto_apply, auto_apply_scan  # noqa: E402
        from app.store import (  # noqa: E402
            PATTERN_CACHE_FILE,
            clear_pattern_cache,
            delete_pattern_entry,
            find_pattern_entry,
            list_pattern_entries,
            mark_pattern_hit,
            save_pattern_entry,
            set_pattern_enabled,
        )

        disc_structure = {
            "rules": [
                {"role": "episode", "container": "【】", "match": "纯数字", "take": "第一个"}
            ]
        }

        def make_disc_scan() -> dict:
            return pattern_scan(
                [
                    pattern_row("disc41", "【Disc-41】【作品丙】【DVDrip】【1080p】.mkv", 41),
                    pattern_row("disc42", "【Disc-42】【作品丙】【DVDrip】【1080p】.mkv", 42),
                    pattern_row("disc43", "【Disc-43】【作品丙】【DVDrip】【1080p】.mkv", 43),
                ]
            )

        def cache_row(row: dict, structure: dict, source: str = "ai") -> dict:
            rel_dir = "/".join(
                [part for part in str(row.get("rel_path") or "").replace("\\", "/").split("/") if part][:-1]
            )
            return save_pattern_entry(
                {
                    "fingerprint": fingerprint(
                        str(row.get("group_id") or ""), rel_dir, str(row.get("filename") or "")
                    ),
                    "group_id": row.get("group_id"),
                    "dir_rel": rel_dir,
                    "structure": structure,
                    "source": source,
                }
            )

        # 存储：文件位置、字段齐全、同一指纹是更新而不是新增
        clear_pattern_cache()
        first_entry = cache_row(make_disc_scan()["files"][0], disc_structure)
        check(
            "缓存：写入 config/pattern_cache.json，条目带齐指纹/目录/结构/来源/时间/命中次数",
            PATTERN_CACHE_FILE.name == "pattern_cache.json"
            and PATTERN_CACHE_FILE.exists()
            and bool(first_entry["fingerprint"])
            and first_entry["group_id"] == "pg"
            and first_entry["dir_rel"] == "作品丙/Season 1"
            and first_entry["structure"] == disc_structure
            and first_entry["source_label"] == "AI 生成"
            and first_entry["hits"] == 0
            and bool(first_entry["created_at"])
            and bool(first_entry["updated_at"])
            and first_entry["enabled"] is True,
            str(first_entry),
        )
        updated_entry = cache_row(make_disc_scan()["files"][0], disc_structure, "manual")
        check(
            "缓存：同一指纹重复写入只更新不新增，来源可改成用户手动修改",
            updated_entry["id"] == first_entry["id"]
            and len(list_pattern_entries()) == 1
            and updated_entry["source_label"] == "用户手动修改"
            and updated_entry["created_at"] == first_entry["created_at"],
            str(updated_entry),
        )
        disabled_entry = set_pattern_enabled(first_entry["id"], False)
        check(
            "缓存：可以禁用单条，禁用后 enabled 为 false",
            disabled_entry is not None
            and disabled_entry["enabled"] is False
            and list_pattern_entries()[0]["enabled"] is False
            and find_pattern_entry(first_entry["fingerprint"])["enabled"] is False,
            str(disabled_entry),
        )
        check(
            "缓存：删除单条后列表为空",
            delete_pattern_entry(first_entry["id"]) and list_pattern_entries() == [],
            "",
        )
        cache_row(make_disc_scan()["files"][0], disc_structure)
        cache_row(pattern_row("other2", "作品丙 - 04.mkv", 4), disc_structure)
        check(
            "缓存：清空返回实际删除条数",
            clear_pattern_cache() == 2 and list_pattern_entries() == [],
            "",
        )
        check(
            "缓存：命中次数只增统计字段",
            mark_pattern_hit(first_entry["fingerprint"]) is None
            and mark_pattern_hit(cache_row(make_disc_scan()["files"][0], disc_structure)["fingerprint"])["hits"] == 1,
            "",
        )

        # 自动套用：指纹完全一致 + 自检通过
        clear_pattern_cache()
        applied_scan = make_disc_scan()
        for row in applied_scan["files"]:
            cache_row(row, disc_structure)
        applied = auto_apply(applied_scan, "pg", "作品丙/Season 1", preset, settings)
        check(
            "缓存：指纹完全一致且 5 项自检通过时自动套用，来源标明缓存命中",
            applied["status"] == "applied"
            and applied["applied"] is True
            and applied["source_label"] == "AI 规律（缓存命中）"
            and "缓存" in applied["reason"]
            and applied["target_count"] == 3,
            str(applied),
        )
        check(
            "缓存：自动套用后每行都标来源与已套用，并累计命中次数",
            all(
                (row.get("pattern_candidate") or {}).get("auto_applied") is True
                and (row.get("pattern_candidate") or {}).get("source") == "cache_hit"
                and (row.get("pattern_candidate") or {}).get("source_label")
                == "AI 规律（缓存命中）"
                for row in applied_scan["files"]
            )
            and all(entry["hits"] == 3 for entry in list_pattern_entries())
            and len(list_pattern_entries()) == 1,
            str(
                [
                    (row.get("id"), row.get("pattern_candidate"), row.get("pattern_conflict"))
                    for row in applied_scan["files"]
                ]
            ),
        )

        # 指纹有任何不同：只作候选，不套用
        clear_pattern_cache()
        mismatch_scan = make_disc_scan()
        for row in mismatch_scan["files"]:
            cache_row(row, disc_structure)
        mismatch_scan["files"].append(pattern_row("mismatch9", "作品丙 - 04.mkv", 4))
        mismatch_before = {
            str(row.get("id")): (row.get("kind"), row.get("episode"))
            for row in mismatch_scan["files"]
        }
        mismatch = auto_apply(mismatch_scan, "pg", "作品丙/Season 1", preset, settings)
        check(
            "缓存：指纹有一个不同就不自动套用，只作候选并点名",
            mismatch["status"] == "candidate_only"
            and mismatch["applied"] is False
            and "指纹" in mismatch["reason"]
            and mismatch["unmatched"] == ["作品丙 - 04.mkv"],
            str(mismatch),
        )
        check(
            "缓存：指纹不完全一致时识别字段一行都没改",
            {
                str(row.get("id")): (row.get("kind"), row.get("episode"))
                for row in mismatch_scan["files"]
            }
            == mismatch_before
            and all(
                (row.get("pattern_candidate") or {}).get("auto_applied") is False
                for row in mismatch_scan["files"][:3]
            ),
            "",
        )

        # 自检不通过：拒绝套用，识别字段不动
        clear_pattern_cache()
        cache_duplicate = pattern_scan(
            [
                pattern_row("cachedup1", "【01】【x264】.mkv", 1),
                pattern_row("cachedup2", "【01】【HEVC】.mkv", 1),
            ]
        )
        for row in cache_duplicate["files"]:
            cache_row(row, disc_structure)
        rejected = auto_apply(cache_duplicate, "pg", "作品丙/Season 1", preset, settings)
        check(
            "缓存：自检不通过就不套用，原因写进返回值",
            rejected["status"] == "rejected"
            and rejected["applied"] is False
            and "没有通过 5 项自检" in rejected["reason"]
            and "重复集号" in rejected["reason"],
            str(rejected),
        )
        check(
            "缓存：自检不通过时识别字段与候选标记都没写入",
            all(
                row.get("episode") == 1 and "pattern_candidate" not in row
                for row in cache_duplicate["files"]
            ),
            "",
        )

        # 设置关闭自动套用
        clear_pattern_cache()
        off_scan = pattern_scan([pattern_row("off1", "【01】【x264】.mkv", 1)])
        cache_row(off_scan["files"][0], disc_structure)
        turned_off = auto_apply(
            off_scan,
            "pg",
            "作品丙/Season 1",
            preset,
            dict(settings, **{"providers.ai_pattern_cache_auto_apply": False}),
        )
        check(
            "缓存：设置关闭自动套用后即使指纹一致也不套用",
            turned_off["status"] == "skipped"
            and turned_off["applied"] is False
            and "关闭" in turned_off["reason"]
            and "pattern_candidate" not in off_scan["files"][0],
            str(turned_off),
        )

        # 与内置识别冲突：内置优先，只作候选
        clear_pattern_cache()
        conflict_scan = pattern_scan(
            [pattern_row("cf1", "【Disc-41】【作品丙】【DVDrip】【1080p】.mkv", 40)]
        )
        cache_row(conflict_scan["files"][0], disc_structure)
        conflict = auto_apply(conflict_scan, "pg", "作品丙/Season 1", preset, settings)
        check(
            "缓存：与内置集号冲突时不自动覆盖，只作候选并列出两边取值",
            conflict["status"] == "candidate_only"
            and conflict["applied"] is False
            and "冲突" in conflict["reason"]
            and conflict["conflicts"]["【Disc-41】【作品丙】【DVDrip】【1080p】.mkv"]["episode"]
            == {"builtin": 40, "ai": 41}
            and conflict_scan["files"][0].get("episode") == 40,
            str(conflict),
        )

        # 缓存里的结构不合法
        clear_pattern_cache()
        bad_scan = pattern_scan([pattern_row("bad1", "【01】【x264】.mkv", 1)])
        save_pattern_entry(
            {
                "fingerprint": fingerprint("pg", "作品丙/Season 1", "【01】【x264】.mkv"),
                "group_id": "pg",
                "dir_rel": "作品丙/Season 1",
                "structure": {
                    "rules": [
                        {
                            "role": "episode",
                            "container": "【】",
                            "match": "纯数字",
                            "take": "第一个",
                            "regex": r"\d+",
                        }
                    ]
                },
                "source": "ai",
            }
        )
        bad = auto_apply(bad_scan, "pg", "作品丙/Season 1", preset, settings)
        check(
            "缓存：缓存里的规律结构不合法时直接拒绝并给中文原因",
            bad["status"] == "rejected"
            and bad["applied"] is False
            and "不合法" in bad["reason"]
            and "正则" in bad["reason"],
            str(bad),
        )
        clear_pattern_cache()

        # ---- 第 5 步：AI 接口（提规律 / 应用 / 记录取舍） ----
        from app.core.ai import AiError  # noqa: E402
        from app.core.pattern_ai import apply_pattern, propose, record_decision  # noqa: E402
        from app.store import (  # noqa: E402
            get_pattern_decision,
            list_pattern_decisions,
        )

        clear_pattern_cache()

        def make_ai_calls(structure: dict) -> tuple[list[dict], object]:
            calls: list[dict] = []

            def call(filenames: list[str], context: dict | None) -> dict:
                calls.append({"filenames": list(filenames), "context": context})
                return structure

            return calls, call

        # 提规律成功：proposal + 样例预览 + 不写字段
        proposal_scan = make_disc_scan()
        proposal_before = copy.deepcopy(proposal_scan)
        propose_calls, propose_ai = make_ai_calls(disc_structure)
        proposed = propose(
            proposal_scan, "pg", "作品丙/Season 1", preset, settings, call_ai=propose_ai
        )
        check(
            "AI 提规律：自检通过返回 proposal，带样例预览与判断依据",
            proposed["status"] == "proposal"
            and proposed["applied"] is False
            and proposed["attempts"] == 1
            and len(proposed["sample"]) == 3
            and all(item["evidence"] for item in proposed["sample"])
            and all("suspects" in item for item in proposed["sample"])
            and [item["new_name"] for item in proposed["sample"]]
            == ["作品丙 - S01E41.mkv", "作品丙 - S01E42.mkv", "作品丙 - S01E43.mkv"],
            str(proposed),
        )
        check(
            "AI 提规律：只把文件名文本发给 AI，且不写任何识别字段",
            propose_calls
            and propose_calls[0]["filenames"]
            == [
                "【Disc-41】【作品丙】【DVDrip】【1080p】.mkv",
                "【Disc-42】【作品丙】【DVDrip】【1080p】.mkv",
                "【Disc-43】【作品丙】【DVDrip】【1080p】.mkv",
            ]
            and proposal_scan == proposal_before,
            str(propose_calls),
        )

        # 提规律失败：整批放弃，不写字段
        abandon_scan = make_disc_scan()
        abandon_before = copy.deepcopy(abandon_scan)
        abandon_calls, abandon_ai = make_ai_calls(
            {"rules": [{"role": "episode", "container": "[]", "match": "纯数字", "take": "第一个"}]}
        )
        abandoned_propose = propose(
            abandon_scan, "pg", "作品丙/Season 1", preset, settings, call_ai=abandon_ai
        )
        check(
            "AI 提规律：重问一次仍不过就整批放弃并给中文原因，字段不动",
            abandoned_propose["status"] == "abandoned"
            and abandoned_propose["applied"] is False
            and abandoned_propose["attempts"] == 2
            and "整批放弃" in abandoned_propose["message"]
            and abandon_scan == abandon_before,
            str(abandoned_propose),
        )

        # 提规律：与内置冲突时只能当候选
        conflict_propose_scan = pattern_scan(
            [pattern_row("pc1", "【Disc-41】【作品丙】【DVDrip】【1080p】.mkv", 40)]
        )
        conflict_before = copy.deepcopy(conflict_propose_scan)
        _, conflict_ai = make_ai_calls(disc_structure)
        conflict_proposed = propose(
            conflict_propose_scan, "pg", "作品丙/Season 1", preset, settings, call_ai=conflict_ai
        )
        check(
            "AI 提规律：自检通过但与内置冲突时返回 candidate_only，不写字段",
            conflict_proposed["status"] == "candidate_only"
            and conflict_proposed["applied"] is False
            and conflict_proposed["conflicts"]["【Disc-41】【作品丙】【DVDrip】【1080p】.mkv"]["episode"]
            == {"builtin": 40, "ai": 41}
            and "冲突" in conflict_proposed["message"]
            and conflict_propose_scan == conflict_before,
            str(conflict_proposed),
        )

        # 超过一次处理上限：本地按形状抽样，仍对全量复核（不再直接拒绝）
        oversize_calls, oversize_ai = make_ai_calls(disc_structure)
        oversize = propose(
            make_disc_scan(),
            "pg",
            "作品丙/Season 1",
            preset,
            dict(settings, **{"providers.ai_batch_limit": 2}),
            call_ai=oversize_ai,
        )
        check(
            "AI 提规律：超过条数上限时本地抽样，AI 只收样本、全量复核",
            oversize["status"] == "proposal"
            and len(oversize_calls) == 1
            and len(oversize_calls[0]["filenames"]) == 2
            and (oversize.get("sampling") or {}).get("total") == 3
            and (oversize.get("sampling") or {}).get("sent") == 2
            and "全量 3 条" in oversize["message"],
            str(oversize.get("sampling")),
        )

        # AI 未配置：给中文说明，不连网
        ai_error = ""
        try:
            propose(make_disc_scan(), "pg", "作品丙/Season 1", preset, dict(settings, **{"providers.ai_enabled": False}))
        except AiError as exc:
            ai_error = str(exc)
        check(
            "AI 提规律：未配置时抛出可直接显示的中文说明，而不是裸错",
            "AI" in ai_error and "设置页" in ai_error,
            ai_error,
        )

        # 应用：再跑自检后写字段 + 写缓存
        clear_pattern_cache()
        apply_scan = make_disc_scan()
        applied_result = apply_pattern(
            apply_scan, "pg", "作品丙/Season 1", disc_structure, "ai", preset, settings
        )
        entries_after_apply = list_pattern_entries()
        check(
            "AI 应用：写入成功时返回写入/跳过行数，并标明 AI 生成来源",
            applied_result["status"] == "applied"
            and applied_result["written"] == 3
            and applied_result["skipped"] == 0
            and applied_result["cached"] == 1
            and "已写入 3 行" in applied_result["message"],
            str(applied_result),
        )
        check(
            "AI 应用：识别字段已写入，缓存来源为 AI 生成，候选标为已确认",
            [row.get("episode") for row in apply_scan["files"]] == [41, 42, 43]
            and len(entries_after_apply) == 1
            and entries_after_apply[0]["source_label"] == "AI 生成"
            and all(
                (row.get("pattern_candidate") or {}).get("confirmed") is True
                and (row.get("pattern_candidate") or {}).get("source_label") == "AI 规律（已确认）"
                for row in apply_scan["files"]
            ),
            str(entries_after_apply),
        )
        manual_result = apply_pattern(
            apply_scan, "pg", "作品丙/Season 1", disc_structure, "manual", preset, settings
        )
        check(
            "AI 应用：来源可标为用户手动修改",
            manual_result["status"] == "applied"
            and manual_result["source_label"] == "手动编写"
            and list_pattern_entries()[0]["source_label"] == "用户手动修改",
            str(manual_result),
        )

        # 应用前重新自检失败：一个字都不写
        clear_pattern_cache()
        changed_scan = pattern_scan(
            [
                pattern_row("chg1", "【01】【x264】.mkv", 1),
                pattern_row("chg2", "【01】【HEVC】.mkv", 1),
            ]
        )
        changed_before = copy.deepcopy(changed_scan)
        changed_result = apply_pattern(
            changed_scan, "pg", "作品丙/Season 1", disc_structure, "ai", preset, settings
        )
        check(
            "AI 应用：写入前自检不过就整批取消，字段与缓存都不动",
            changed_result["status"] == "rejected"
            and changed_result["written"] == 0
            and changed_result["skipped"] == 2
            and "重新自检没有通过" in changed_result["message"]
            and changed_scan == changed_before
            and list_pattern_entries() == [],
            str(changed_result),
        )

        # 应用时与内置冲突：内置优先，整批不写
        clear_pattern_cache()
        conflict_apply_scan = pattern_scan(
            [pattern_row("ca1", "【Disc-41】【作品丙】【DVDrip】【1080p】.mkv", 40)]
        )
        conflict_apply = apply_pattern(
            conflict_apply_scan, "pg", "作品丙/Season 1", disc_structure, "ai", preset, settings
        )
        check(
            "AI 应用：与内置冲突时按内置优先整批取消，不写缓存",
            conflict_apply["status"] == "rejected"
            and conflict_apply["written"] == 0
            and "内置优先" in conflict_apply["message"]
            and conflict_apply_scan["files"][0].get("episode") == 40
            and list_pattern_entries() == [],
            str(conflict_apply),
        )

        # 应用非法结构
        bad_apply = apply_pattern(
            make_disc_scan(),
            "pg",
            "作品丙/Season 1",
            {"rules": [{"role": "episode", "container": "【】", "match": "纯数字", "take": "第一个", "regex": "x"}]},
            "ai",
            preset,
            settings,
        )
        check(
            "AI 应用：结构不合法时拒绝并给中文原因",
            bad_apply["status"] == "rejected"
            and bad_apply["written"] == 0
            and "不合法" in bad_apply["message"]
            and "正则" in bad_apply["message"],
            str(bad_apply),
        )

        # 记录取舍：采用 / 不采用 / 稍后再说；不采用不删缓存
        clear_pattern_cache()
        cache_row(make_disc_scan()["files"][0], disc_structure)
        first_decision = record_decision("pg", "作品丙/Season 1", "rejected", "这次不采用")
        check(
            "AI 取舍：记录「不采用」不删除缓存",
            first_decision["decision"] == "rejected"
            and first_decision["decision_label"] == "不采用"
            and len(list_pattern_entries()) == 1
            and get_pattern_decision("pg", "作品丙/Season 1")["decision"] == "rejected",
            str(first_decision),
        )
        _, decision_ai = make_ai_calls(disc_structure)
        decided_propose = propose(
            make_disc_scan(), "pg", "作品丙/Season 1", preset, settings, call_ai=decision_ai
        )
        check(
            "AI 取舍：「不采用」之后返回 remind=false，不再重复打扰",
            decided_propose["remind"] is False
            and (decided_propose.get("decision") or {}).get("decision") == "rejected",
            str(decided_propose.get("decision")),
        )
        later_decision = record_decision("pg", "作品丙/Season 1", "later")
        _, later_ai = make_ai_calls(disc_structure)
        later_propose = propose(
            make_disc_scan(), "pg", "作品丙/Season 1", preset, settings, call_ai=later_ai
        )
        check(
            "AI 取舍：稍后再说会更新同一条记录，并允许下次提醒",
            later_decision["decision_label"] == "稍后再说"
            and later_decision["id"] == first_decision["id"]
            and len(list_pattern_decisions()) == 1
            and later_propose["remind"] is True,
            str(later_decision),
        )
        invalid_decision = ""
        try:
            record_decision("pg", "作品丙/Season 1", "maybe")
        except ValueError as exc:
            invalid_decision = str(exc)
        check(
            "AI 取舍：非法取值给中文说明",
            "只能是" in invalid_decision,
            invalid_decision,
        )
        clear_pattern_cache()

        # ---- 第 7 步：把缓存自动套用接进扫描流程 ----
        empty_scan = make_disc_scan()
        empty_before = copy.deepcopy(empty_scan)
        empty_results = auto_apply_scan(empty_scan, preset, settings)
        check(
            "自动套用接线：缓存为空时直接跳过，扫描结果完全不变",
            empty_results == [] and empty_scan == empty_before,
            str(empty_results),
        )

        cache_row(make_disc_scan()["files"][0], disc_structure)
        off_scan = make_disc_scan()
        off_results = auto_apply_scan(
            off_scan,
            preset,
            dict(settings, **{"providers.ai_pattern_cache_auto_apply": False}),
        )
        check(
            "自动套用接线：设置里关闭后即使有缓存也不套用",
            off_results == []
            and not any(
                (row.get("pattern_candidate") or {}).get("auto_applied")
                for row in off_scan["files"]
            ),
            str(off_results),
        )

        clear_pattern_cache()
        for entry_row in make_disc_scan()["files"]:
            cache_row(entry_row, disc_structure)
        hit_scan = make_disc_scan()
        hit_results = auto_apply_scan(hit_scan, preset, settings)
        auto_rows = [
            row
            for row in hit_scan["files"]
            if (row.get("pattern_candidate") or {}).get("auto_applied")
        ]
        check(
            "自动套用接线：同款目录命中后标为已自动套用",
            len(hit_results) == 1
            and hit_results[0]["status"] == "applied"
            and len(auto_rows) == 3
            and all(row.get("episode") in (41, 42, 43) for row in auto_rows),
            str(hit_results),
        )

        scan_route_source = (
            Path(__file__).resolve().parents[1] / "app" / "api" / "routes_scan.py"
        ).read_text(encoding="utf-8")
        check(
            "自动套用接线：首次扫描与重扫都在识别之后调用",
            scan_route_source.count("auto_apply_scan(result, load_preset(None), settings)") == 2
            and scan_route_source.index("auto_apply_scan") > scan_route_source.index("apply_identification"),
            "",
        )
        clear_pattern_cache()

        # 接口注册（不导入 FastAPI，避免本机未装依赖时误报）
        routes_source = (
            Path(__file__).resolve().parents[1] / "app" / "api" / "routes_ai.py"
        ).read_text(encoding="utf-8")
        check(
            "AI 接口：提规律 / 应用 / 取舍三个接口已注册",
            all(
                f'"{path}"' in routes_source
                for path in (
                    "/ai/patterns/propose",
                    "/ai/patterns/apply",
                    "/ai/patterns/decision",
                )
            ),
            "",
        )

        # 特典编号：同一季同一类型内连续，不同碟 / 不同类型互不影响
        def special_row(
            rid: str,
            rel: str,
            type_name: str,
            no: int | None = None,
            raw: object = None,
            title: str = "作品甲",
            season_tag: str = "",
        ) -> dict:
            name = rel.rsplit("/", 1)[-1]
            return {
                "id": rid,
                "category": "video",
                "kind": "special",
                "group_id": "pg",
                "rel_path": rel,
                "filename": name,
                "title": title,
                "year": 2024,
                "season": 0,
                "special_type": type_name,
                "special_no": no,
                "special_no_raw": raw,
                "special_folder": False,
                "fields": {"original_stem": name, "source_season": season_tag},
            }

        order_rows = [
            special_row("o3", "Disc-10/【OP】.mkv", "OP"),
            special_row("o2", "Disc-09/【OP02】.mkv", "OP", 2, 2),
            special_row("e3", "Disc-10/【ED】.mkv", "ED"),
            special_row("o1", "Disc-09/【OP01】.mkv", "OP", 9, 1),
            special_row("e2", "Disc-09/【ED02】.mkv", "ED", 2, 2),
            special_row("e1", "Disc-09/【ED01】.mkv", "ED", 9, 1),
        ]
        renumber_specials(order_rows, settings)
        order_map = {row["id"]: row["special_no"] for row in order_rows}
        check(
            "特典编号：按碟号 + 自带序号连续，OP / ED 各自独立",
            [order_map[key] for key in ("o1", "o2", "o3")] == [1, 2, 3]
            and [order_map[key] for key in ("e1", "e2", "e3")] == [1, 2, 3],
            str(order_map),
        )

        season_rows = [
            special_row("s1a", "S1/【NCOP01】.mkv", "NCOP", season_tag="S01"),
            special_row("s1b", "S1/【NCOP02】.mkv", "NCOP", season_tag="S01"),
            special_row("s2a", "S2/【NCOP01】.mkv", "NCOP", season_tag="S02"),
            special_row("s2b", "S2/【NCOP02】.mkv", "NCOP", season_tag="S02"),
        ]
        renumber_specials(season_rows, settings)
        season_map = {row["id"]: row["special_no"] for row in season_rows}
        check(
            "特典编号：不同来源季各自从 1 开始，互不消耗号码",
            [season_map[key] for key in ("s1a", "s1b")] == [1, 2]
            and [season_map[key] for key in ("s2a", "s2b")] == [1, 2],
            str(season_map),
        )

        frac_rows = [
            special_row("f1", "【OVA】24.5.mkv", "OVA", 24, "24.5"),
            special_row("f2", "【OVA】25.5.mkv", "OVA", 25, "25.5"),
        ]
        renumber_specials(frac_rows, settings)
        check(
            "特典编号：24.5 这类小数不被压成整数或重排",
            [row["special_no"] for row in frac_rows] == [24, 25],
            str([row["special_no"] for row in frac_rows]),
        )

        check(
            "标题：整段字幕组前缀会被去掉，作品名里的“制作”不误伤",
            clean_dir_title("【晨曦制作】【游戏王★决斗怪兽GX】【DVDrip】【MP4】")
            == "游戏王★决斗怪兽GX"
            and clean_dir_title("【XX制作】") == "XX制作"
            and clean_dir_title("【XX制作】【第二季】") == "XX制作"
            and clean_dir_title("[VCB-Studio] Oregairu") == "Oregairu",
            str(
                [
                    clean_dir_title("【晨曦制作】【游戏王★决斗怪兽GX】【DVDrip】【MP4】"),
                    clean_dir_title("【XX制作】"),
                    clean_dir_title("【XX制作】【第二季】"),
                ]
            ),
        )
        check(
            "标题：还留着字幕组前缀时只提示、不猜测",
            title_looks_grouped("晨曦制作 作品名") and not title_looks_grouped("Ruri no Houseki"),
            "",
        )

        v2_path = media / "编号测试" / "作品甲 - 41v2.mkv"
        _write(v2_path)
        v2_row = parse_file(media, v2_path, settings, list_rules())
        plain_path = media / "编号测试" / "作品甲 - 412.mkv"
        _write(plain_path)
        plain_row = parse_file(media, plain_path, settings, list_rules())
        check(
            "版本：41v2 识别为第 41 集 + 版本 v2，412 不会被误判",
            v2_row.get("episode") == 41
            and str((v2_row.get("fields") or {}).get("version")) == "2"
            and plain_row.get("episode") == 412
            and not (plain_row.get("fields") or {}).get("version"),
            str([v2_row.get("episode"), v2_row.get("fields"), plain_row.get("episode")]),
        )
        version_name, version_errors, _ = build_filename(
            {
                "fields": {
                    "series": "作品甲",
                    "season": 1,
                    "episode": 41,
                    "version": "2",
                    "resolution": "1080p",
                    "ext": "mkv",
                }
            },
            {
                "pattern": "{series} - S{season}E{episode}[v{version}][{resolution}]",
                "output_mode": "filename",
            },
            settings,
        )
        check(
            "版本：模板里的 {version} 能生成 v2",
            version_name == "作品甲 - S1E41[v2][1080p].mkv" and not version_errors,
            version_name,
        )

        warn_rows = [
            special_row("w1", "D/【OP01】.mkv", "OP", 1),
            special_row("w2", "D/【OP02】.mkv", "OP", 1),
            special_row("w3", "D/【OP03】.mkv", "OP", 3),
        ]
        warn_items = [
            {"file_id": row["id"], "category": "video", "action": "rename", "status": "ok", "messages": []}
            for row in warn_rows
        ]
        _quality_warnings(
            warn_items, warn_rows, {"special": {"pattern": "{series} - {special_type}{special_no}"}}
        )
        warn_text = " ".join(warn_items[0]["messages"])
        check(
            "预览提示：同季同类型编号重复 / 不连续会提示，但不改状态",
            "重复" in warn_text
            and "不连续" in warn_text
            and all(item["status"] == "ok" for item in warn_items),
            warn_text,
        )

        grouped_row = special_row("g1", "D/【01】.mkv", "SP", 1, title="晨曦制作 作品名")
        grouped_item = {
            "file_id": "g1",
            "category": "video",
            "action": "rename",
            "status": "ok",
            "messages": [],
        }
        _quality_warnings(
            [grouped_item], [grouped_row], {"special": {"pattern": "{series} - {special_type}{special_no}"}}
        )
        check(
            "预览提示：标题疑似含字幕组名",
            "字幕组" in " ".join(grouped_item["messages"]),
            str(grouped_item["messages"]),
        )

        version_row = special_row("g2", "D/【02】.mkv", "SP", 2)
        version_row["fields"]["version"] = "2"
        version_item = {
            "file_id": "g2",
            "category": "video",
            "action": "rename",
            "status": "ok",
            "messages": [],
        }
        _quality_warnings(
            [version_item], [version_row], {"special": {"pattern": "{series} - {special_type}{special_no}"}}
        )
        check(
            "预览提示：识别到版本但模板没用 {version}",
            "版本" in " ".join(version_item["messages"]),
            str(version_item["messages"]),
        )

        # ---------------- 12. 第一批能力：运行记录 / 待人工清单 / 决策来源 ----------------
        run = ensure_run("reg-run-stage", [{"id": "g1", "name": "回归组"}])
        update_run(run["id"], stage="preview", snapshot_patch={"scan": {"file_count": 7}})
        update_run(run["id"], stage="scan")
        run_back = get_run(run["id"]) or {}
        check(
            "运行记录：阶段能存能读且只前进不后退",
            run_back.get("stage") == "preview",
            f"写回更早阶段后是 {run_back.get('stage')}",
        )
        update_run(run["id"], stage="execute", snapshot_patch={"preview": {"ok": 3}})
        run_snapshot = (get_run(run["id"]) or {}).get("snapshot") or {}
        check(
            "运行记录：各阶段快照能写能读回",
            (run_snapshot.get("scan") or {}).get("file_count") == 7
            and (run_snapshot.get("preview") or {}).get("ok") == 3,
            str(run_snapshot),
        )
        first_claim = claim_run(run["id"], "推进者A")
        second_claim = claim_run(run["id"], "推进者B")
        check(
            "运行记录：占住后第二个推进者拿不到租约",
            first_claim and not second_claim,
            f"第一个={first_claim}，第二个={second_claim}",
        )
        release_run(run["id"], "推进者A")
        released_claim = claim_run(run["id"], "推进者B")
        check("运行记录：释放后别人能拿到租约", released_claim, str(released_claim))
        # 模拟容器重启：启动时会把所有遗留租约清空
        clear_run_locks()
        lock_after_restart = (get_run(run["id"]) or {}).get("lock_owner", "")
        check(
            "运行记录：模拟重启后遗留租约被清空",
            lock_after_restart == "",
            f"租约持有者={lock_after_restart!r}",
        )

        pending_scan = {
            "scan_id": "reg-pending",
            "files": [
                {
                    "id": "p-unassigned",
                    "category": "video",
                    "filename": "未知.mkv",
                    "path": "/tmp/未知.mkv",
                    "rel_path": "未知.mkv",
                    "group_id": "g1",
                    "kind": "episode",
                    "season": 1,
                    "identify": {
                        "title": "",
                        "segment": "",
                        "reason": "文件名和目录名都没有足够信息",
                    },
                },
                {
                    "id": "p-season",
                    "category": "video",
                    "filename": "作品 - S01E01.mkv",
                    "path": "/tmp/作品 - S01E01.mkv",
                    "rel_path": "作品/作品 - S01E01.mkv",
                    "group_id": "g1",
                    "kind": "episode",
                    "season": 1,
                    "identify": {
                        "work_id": "w-season",
                        "title": "作品",
                        "segment": "作品",
                        "confidence": "high",
                    },
                    "fields": {"tmdb_id": "100", "series": "作品"},
                },
            ],
            "ignored_dirs": [{"group_id": "g1", "path": "SPs", "reason": "special"}],
            "season_state": {
                "w-season": {
                    "seasons": [{"season_number": 1}, {"season_number": 2}],
                    "choice": None,
                }
            },
            "groups": [{"id": "g1", "name": "回归组"}],
        }
        pending_operation = {
            "id": "op-pending",
            "status": "conflict",
            "items": [
                {
                    "seq": 9,
                    "status": "conflict",
                    "original_path": "/tmp/a.mkv",
                    "target_path": "/tmp/b.mkv",
                    "message": "目标已存在，需要选择处理方式",
                }
            ],
        }
        pending_snapshot = {
            "preview": {
                "issues": [
                    {
                        "status": "warning",
                        "ref_id": "preview-w",
                        "title": "警告文件",
                        "reason": "标题可能含字幕组名",
                    },
                    {
                        "status": "error",
                        "ref_id": "preview-e",
                        "title": "错误文件",
                        "reason": "多个文件映射到同一目标路径",
                    },
                ]
            }
        }
        collected = collect_pending(pending_scan, pending_operation, pending_snapshot)
        collected_kinds = {item["kind"] for item in collected}
        check(
            "待人工清单：五类问题都能生成条目",
            {
                "unassigned",
                "season",
                "special",
                "conflict",
                "preview_warning",
                "preview_error",
            }
            <= collected_kinds,
            str(sorted(collected_kinds)),
        )
        check(
            "待人工清单：每条都有中文原因",
            all(str(item.get("reason") or "").strip() for item in collected),
            str([(item["kind"], item.get("reason")) for item in collected]),
        )
        blocking_map = {item["kind"]: bool(item["blocking"]) for item in collected}
        check(
            "待人工清单：是否阻塞下一步判断正确",
            blocking_map
            == {
                "unassigned": False,
                "season": True,
                "special": False,
                "conflict": True,
                "preview_warning": False,
                "preview_error": True,
            },
            str(blocking_map),
        )
        pending_run = ensure_run("reg-pending", pending_scan["groups"])
        sync_pending_items(pending_run["id"], "reg-pending", collected)
        pending_counts_first = pending_counts(pending_run["id"])
        check(
            "待人工清单：条数与阻塞计数一致",
            pending_counts_first["open"] == len(collected)
            and pending_counts_first["blocking"] == 3
            and pending_counts_first["blocking"]
            == sum(1 for item in collected if item["blocking"]),
            str(pending_counts_first),
        )
        kept = [
            item
            for item in collected
            if item["kind"] not in ("unassigned", "preview_warning")
        ]
        open_after_resolve = sync_pending_items(pending_run["id"], "reg-pending", kept)
        pending_counts_second = pending_counts(pending_run["id"])
        open_kinds = {item["kind"] for item in open_after_resolve}
        check(
            "待人工清单：问题解决后自动关闭并从清单消失",
            pending_counts_second["open"] == 4
            and pending_counts_second["resolved"] == 2
            and "unassigned" not in open_kinds
            and "preview_warning" not in open_kinds,
            str(pending_counts_second),
        )
        kept_blocking = [item for item in kept if item["kind"] != "season"]
        sync_pending_items(pending_run["id"], "reg-pending", kept_blocking)
        pending_counts_third = pending_counts(pending_run["id"])
        check(
            "待人工清单：处理掉阻塞条目后阻塞计数减少",
            pending_counts_third["open"] == 3 and pending_counts_third["blocking"] == 2,
            str(pending_counts_third),
        )
        sync_pending_items(pending_run["id"], "reg-pending", kept_blocking)
        resolved_kinds = {
            row["kind"]
            for row in list_pending_items(pending_run["id"], True)
            if row["status"] == "resolved"
        }
        check(
            "待人工清单：已处理条目不会被重新生成",
            resolved_kinds == {"unassigned", "preview_warning", "season"}
            and pending_counts(pending_run["id"])["open"] == 3,
            str(sorted(resolved_kinds)),
        )

        human_decision = record_store_decision(
            "reg-decisions", "assign", "d-human", "作品甲 (2020)", source="human"
        )
        rule_decision = record_store_decision(
            "reg-decisions",
            "season",
            "d-rule",
            "3",
            source="rule",
            rule_id="rule-42",
        )
        ai_decision = record_store_decision(
            "reg-decisions", "conflict", "d-ai", "overwrite", source="ai"
        )
        decision_rows = {
            item["ref_id"]: item for item in list_decisions("reg-decisions")
        }
        check(
            "决策来源：人工确认写入来源与时间",
            decision_rows.get("d-human", {}).get("source") == "human"
            and bool(decision_rows.get("d-human", {}).get("created_at"))
            and bool(human_decision.get("created_at")),
            str(decision_rows.get("d-human")),
        )
        check(
            "决策来源：规则自动带上规则 id",
            decision_rows.get("d-rule", {}).get("source") == "rule"
            and decision_rows.get("d-rule", {}).get("rule_id") == "rule-42",
            str(decision_rows.get("d-rule")),
        )
        check(
            "决策来源：AI 来源是预留占位",
            decision_rows.get("d-ai", {}).get("source") == "ai"
            and decision_rows.get("d-ai", {}).get("source_label") == "AI 决策（预留）"
            and bool(ai_decision.get("created_at")),
            str(decision_rows.get("d-ai")),
        )
        decision_operation_id = create_pending_operation(
            "reg-decisions",
            None,
            "回归预设",
            {"groups": []},
            [
                {
                    "file_id": "d-human",
                    "category": "episode",
                    "original_path": "/tmp/h.mkv",
                    "target_path": "/tmp/h2.mkv",
                    "status": "planned",
                },
                {
                    "file_id": "d-rule",
                    "category": "episode",
                    "original_path": "/tmp/r.mkv",
                    "target_path": "/tmp/r2.mkv",
                    "status": "planned",
                },
                {
                    "file_id": "d-ai",
                    "category": "episode",
                    "original_path": "/tmp/a.mkv",
                    "target_path": "/tmp/a2.mkv",
                    "status": "planned",
                },
                {
                    "file_id": "d-unknown",
                    "category": "episode",
                    "original_path": "/tmp/u.mkv",
                    "target_path": "/tmp/u2.mkv",
                    "status": "planned",
                },
            ],
            status="queued",
            batch_count=1,
        )
        decision_operation = get_operation(decision_operation_id) or {}
        unknown_item = next(
            (
                item
                for item in decision_operation.get("items") or []
                if item.get("file_id") == "d-unknown"
            ),
            None,
        )
        check(
            "决策来源：历史数据缺字段显示未知",
            unknown_item is not None
            and unknown_item.get("decision_source") == ""
            and unknown_item.get("rule_id") == ""
            and unknown_item.get("decided_at") == "",
            str(
                unknown_item
                and {
                    "decision_source": unknown_item.get("decision_source"),
                    "rule_id": unknown_item.get("rule_id"),
                    "decided_at": unknown_item.get("decided_at"),
                }
            ),
        )
        decision_summary = operation_decision_counts(decision_operation_id)
        check(
            "决策来源：汇总计数与实际条目一致",
            decision_summary == {"human": 1, "rule": 1, "ai": 1, "unknown": 1},
            str(decision_summary),
        )

        # ---------------- 13. 第二批能力：自动放行门槛 ----------------
        gate_clean_scan = {
            "scan_id": "reg-gate-clean",
            "files": [
                {
                    "id": "g-ok",
                    "category": "video",
                    "filename": "完成 - S01E01.mkv",
                    "path": "/tmp/完成 - S01E01.mkv",
                    "rel_path": "完成/完成 - S01E01.mkv",
                    "group_id": "g-ok",
                    "kind": "episode",
                    "season": 1,
                    "episode": 1,
                    "identify": {
                        "work_id": "w-clean",
                        "title": "完成",
                        "segment": "完成",
                        "confidence": "high",
                    },
                    "fields": {"tmdb_id": "200", "series": "完成"},
                }
            ],
            "ignored_dirs": [],
            "season_state": {},
            "groups": [{"id": "g-ok", "name": "门槛组"}],
        }
        gate_clean_run = {
            "id": "run-gate-clean",
            "scan_id": "reg-gate-clean",
            "snapshot": {"preview": {"summary": {"ok": 1}, "issues": []}},
        }
        gate_clean = evaluate_gate(gate_clean_scan, None, gate_clean_run)
        check(
            "自动放行门槛：四项全绿才通过",
            gate_clean.get("passed") is True
            and gate_clean.get("blocked") is False
            and len(gate_clean.get("checks") or []) == 4
            and all(item.get("passed") for item in gate_clean.get("checks") or []),
            str(gate_clean),
        )
        check(
            "自动放行门槛：检查时间、运行编号、扫描编号都写进快照",
            bool(gate_clean.get("checked_at"))
            and gate_clean.get("run_id") == "run-gate-clean"
            and gate_clean.get("scan_id") == "reg-gate-clean",
            f"{gate_clean.get('checked_at')} / {gate_clean.get('run_id')} / {gate_clean.get('scan_id')}",
        )

        gate_blocked = evaluate_gate(pending_scan, pending_operation, {
            "id": "run-gate-blocked",
            "scan_id": "reg-pending",
            "snapshot": pending_snapshot,
        })
        blocked_ids = [item.get("id") for item in gate_blocked.get("failed") or []]
        checks_by_id = {item["id"]: item for item in gate_blocked.get("checks") or []}
        check(
            "自动放行门槛：任意一项不满足就整体不放行",
            gate_blocked.get("passed") is False
            and gate_blocked.get("blocked") is True
            and blocked_ids
            == ["preview_clear", "conflict_clear", "unassigned_clear", "season_clear"],
            str(gate_blocked.get("failed")),
        )
        check(
            "自动放行门槛：不放行时写明原因和问题明细",
            all(str(item.get("reason") or "").strip() for item in gate_blocked.get("checks") or [])
            and "未通过" in str(gate_blocked.get("summary") or "")
            and len(checks_by_id["preview_clear"]["issues"]) == 2
            and checks_by_id["conflict_clear"]["issues"][0]["ref_id"] == "op-pending:9"
            and checks_by_id["unassigned_clear"]["issues"][0]["ref_id"] == "p-unassigned"
            and checks_by_id["season_clear"]["issues"][0]["ref_id"] == "w-season",
            str(
                {
                    key: [item.get("ref_id") for item in value.get("issues") or []]
                    for key, value in checks_by_id.items()
                }
            ),
        )

        gate_no_preview = evaluate_gate(
            gate_clean_scan,
            None,
            {"id": "run-gate-nopreview", "scan_id": "reg-gate-clean", "snapshot": {}},
        )
        check(
            "自动放行门槛：没生成预览不算通过",
            gate_no_preview.get("passed") is False
            and [item.get("id") for item in gate_no_preview.get("failed") or []]
            == ["preview_clear"]
            and "还没生成预览" in str(gate_no_preview.get("summary") or "")
            + " ".join(str(item.get("reason") or "") for item in gate_no_preview.get("checks") or []),
            str(gate_no_preview),
        )

        gate_partial = evaluate_gate(
            gate_clean_scan,
            None,
            {
                "id": "run-gate-partial",
                "scan_id": "reg-gate-clean",
                "snapshot": {
                    "preview": {
                        "issues": [
                            {
                                "status": "warning",
                                "ref_id": "pv-partial",
                                "title": "部分警告",
                                "reason": "标题可能含字幕组名",
                            }
                        ]
                    }
                },
            },
        )
        partial_passed = {
            item["id"]: bool(item.get("passed"))
            for item in gate_partial.get("checks") or []
        }
        check(
            "自动放行门槛：只满足部分检查也不放行",
            gate_partial.get("passed") is False
            and [item.get("id") for item in gate_partial.get("failed") or []] == ["preview_clear"]
            and partial_passed
            == {
                "preview_clear": False,
                "conflict_clear": True,
                "unassigned_clear": True,
                "season_clear": True,
            },
            str(partial_passed),
        )

        gate_run = ensure_run("reg-gate-persist", gate_clean_scan["groups"])
        update_run(
            gate_run["id"],
            snapshot_patch={"preview": gate_clean_run["snapshot"]["preview"]},
        )
        gate_run = get_run(gate_run["id"]) or gate_run
        gate_for_run = evaluate_gate(gate_clean_scan, None, gate_run)
        update_run(gate_run["id"], snapshot_patch={"gate": gate_for_run})
        gate_saved = ((get_run(gate_run["id"]) or {}).get("snapshot") or {}).get("gate") or {}
        check(
            "自动放行门槛：结果快照存到本次运行并可按运行回查",
            gate_saved.get("passed") is True
            and gate_saved.get("run_id") == gate_run["id"]
            and gate_saved.get("scan_id") == "reg-gate-persist"
            and gate_saved.get("checked_at") == gate_for_run.get("checked_at"),
            str(gate_saved),
        )

        # ---------------- 14. 第二批能力：每次自动执行的授权记录 ----------------
        auto_settings_on = {"execution.auto_execute_enabled": True}
        check(
            "自动执行授权：默认关闭",
            load_settings().get("execution.auto_execute_enabled") is False,
            str(load_settings().get("execution.auto_execute_enabled")),
        )
        auto_auth = create_auto_authorization(
            run_id=gate_run["id"],
            scan_id="reg-gate-persist",
            confirmed_by="小张",
            risk_version=AUTO_EXECUTE_RISK_VERSION,
            risk_text=AUTO_EXECUTE_RISK_TEXT,
            gate_checked_at=str(gate_for_run.get("checked_at") or ""),
        )
        check(
            "自动执行授权：确认人、时间、风险版本、运行编号单独留档",
            auto_auth.get("confirmed_by") == "小张"
            and auto_auth.get("run_id") == gate_run["id"]
            and auto_auth.get("scan_id") == "reg-gate-persist"
            and auto_auth.get("risk_version") == AUTO_EXECUTE_RISK_VERSION
            and auto_auth.get("gate_checked_at") == gate_for_run.get("checked_at")
            and bool(auto_auth.get("created_at"))
            and auto_auth.get("status") == "granted",
            str(auto_auth),
        )
        check(
            "自动执行授权：风险文案写明无人看守可能改错、撤销未必能完全恢复",
            "无人看守" in AUTO_EXECUTE_RISK_TEXT
            and "改错文件" in AUTO_EXECUTE_RISK_TEXT
            and "撤销未必能完全恢复" in AUTO_EXECUTE_RISK_TEXT,
            AUTO_EXECUTE_RISK_TEXT,
        )
        check(
            "自动执行授权：没有授权记录就拒绝执行",
            "没有找到本次运行的自动执行授权"
            in auto_execute_refusal(auto_settings_on, gate_for_run, None, gate_run["id"]),
            auto_execute_refusal(auto_settings_on, gate_for_run, None, gate_run["id"]),
        )
        check(
            "自动执行授权：开关关闭时即使有授权也拒绝",
            "开关未开启"
            in auto_execute_refusal({}, gate_for_run, auto_auth, gate_run["id"]),
            auto_execute_refusal({}, gate_for_run, auto_auth, gate_run["id"]),
        )
        check(
            "自动执行授权：门槛不绿时即使有授权也拒绝",
            "门槛未通过"
            in auto_execute_refusal(auto_settings_on, gate_blocked, auto_auth, gate_run["id"]),
            auto_execute_refusal(auto_settings_on, gate_blocked, auto_auth, gate_run["id"]),
        )
        check(
            "自动执行授权：授权不能跨运行使用",
            "不属于本次运行"
            in auto_execute_refusal(auto_settings_on, gate_for_run, auto_auth, "run-other"),
            auto_execute_refusal(auto_settings_on, gate_for_run, auto_auth, "run-other"),
        )
        stale_auth = create_auto_authorization(
            run_id="run-gate-stale",
            confirmed_by="小张",
            risk_version="v0",
            risk_text=AUTO_EXECUTE_RISK_TEXT,
        )
        check(
            "自动执行授权：风险文案版本变化后旧授权失效",
            "风险提示已更新"
            in auto_execute_refusal(auto_settings_on, gate_for_run, stale_auth, "run-gate-stale"),
            auto_execute_refusal(auto_settings_on, gate_for_run, stale_auth, "run-gate-stale"),
        )
        first_consume = consume_auto_authorization(auto_auth["id"])
        second_consume = consume_auto_authorization(auto_auth["id"])
        check(
            "自动执行授权：一条授权只能放行一次",
            first_consume
            and not second_consume
            and (get_auto_authorization(auto_auth["id"]) or {}).get("status") == "consumed"
            and latest_granted_authorization(gate_run["id"]) is None,
            f"第一次={first_consume}，第二次={second_consume}",
        )
        restore_auto_authorization(auto_auth["id"])
        restored = get_auto_authorization(auto_auth["id"]) or {}
        check(
            "自动执行授权：执行没启动时授权可退回",
            restored.get("status") == "granted"
            and restored.get("consumed_at") == ""
            and restored.get("risk_version") == AUTO_EXECUTE_RISK_VERSION,
            str(restored),
        )

        # ---------------- 15. 第二批能力：继续接口与推进锁 ----------------
        advance_order = [validate_advance(stage) for stage in ("scan", "match", "preview", "execute")]
        check(
            "推进运行：阶段只能按顺序前进一步",
            advance_order == ["match", "preview", "execute", "done"],
            str(advance_order),
        )
        done_error = ""
        try:
            validate_advance("done")
        except AdvanceError as exc:
            done_error = str(exc)
        check(
            "推进运行：已经完成的运行不能再推进",
            "已经完成" in done_error,
            done_error,
        )
        rule_error = ""
        try:
            validate_advance("scan", "rule", "")
        except AdvanceError as exc:
            rule_error = str(exc)
        source_error = ""
        try:
            validate_advance("scan", "ai", "")
        except AdvanceError as exc:
            source_error = str(exc)
        check(
            "推进运行：规则自动必须带规则 id，来源只接受人工或规则",
            "必须带上规则 id" in rule_error
            and "来源只能是" in source_error
            and validate_advance("scan", "rule", "auto-advance-1") == "match",
            f"{rule_error} / {source_error}",
        )
        check(
            "推进运行：执行未结束不能标记完成",
            not can_mark_done("running")
            and not can_mark_done("conflict")
            and can_mark_done("done")
            and can_mark_done("partial")
            and can_mark_done("cancelled"),
            "running/conflict 拒绝，done/partial/cancelled 允许",
        )

        events_run = ensure_run("reg-events", gate_clean_scan["groups"])
        update_run(
            events_run["id"],
            stage="match",
            event_source="rule",
            event_rule_id="stage-rule-1",
            actor="调度器",
        )
        events = list_run_events(events_run["id"])
        stage_event = next((item for item in events if item.get("kind") == "stage"), None)
        check(
            "运行事件：阶段切换进事件流水并带来源与规则 id",
            stage_event is not None
            and "扫描 → 匹配作品" in str(stage_event.get("title") or "")
            and stage_event.get("stage") == "match"
            and stage_event.get("stage_label") == "匹配作品"
            and stage_event.get("source") == "rule"
            and stage_event.get("source_label") == "规则自动"
            and stage_event.get("rule_id") == "stage-rule-1"
            and stage_event.get("actor") == "调度器",
            str(stage_event),
        )
        before_same = len(list_run_events(events_run["id"]))
        update_run(events_run["id"], stage="match")
        check(
            "运行事件：阶段没变化时不重复写流水",
            len(list_run_events(events_run["id"])) == before_same,
            f"{before_same} -> {len(list_run_events(events_run['id']))}",
        )

        record_store_decision(
            "reg-events",
            "advance",
            events_run["id"],
            choice="preview",
            source="human",
            note="手动点一下",
            run_id=events_run["id"],
        )
        record_store_decision(
            "reg-events",
            "advance",
            events_run["id"],
            choice="execute",
            source="rule",
            rule_id="auto-advance-1",
            note="规则自动推进",
            run_id=events_run["id"],
        )
        events = list_run_events(events_run["id"])
        human_event = next(
            (item for item in events if item.get("kind") == "human"), None
        )
        rule_event = next(
            (
                item
                for item in events
                if item.get("kind") == "rule" and item.get("rule_id") == "auto-advance-1"
            ),
            None,
        )
        check(
            "运行事件：人工确认记为「人工确认」",
            human_event is not None
            and human_event.get("source_label") == "人工确认"
            and "推进运行" in str(human_event.get("title") or "")
            and human_event.get("detail", {}).get("choice") == "preview",
            str(human_event),
        )
        check(
            "运行事件：规则自动记为「规则自动」并带规则 id",
            rule_event is not None
            and rule_event.get("source_label") == "规则自动"
            and rule_event.get("rule_id") == "auto-advance-1"
            and rule_event.get("detail", {}).get("choice") == "execute",
            str(rule_event),
        )
        event_ids = [int(item.get("id") or 0) for item in events]
        event_titles = [str(item.get("title") or "") for item in events]
        check(
            "运行事件：按发生顺序可完整还原",
            len(events) >= 3
            and event_ids == sorted(event_ids)
            and event_ids[0] > 0
            and any("阶段：" in title for title in event_titles)
            and any("推进运行" in title for title in event_titles),
            str(event_titles),
        )

        lock_run = ensure_run("reg-lock", gate_clean_scan["groups"])
        first_claim = claim_run(lock_run["id"], "owner-a", ttl_seconds=60)
        second_claim = claim_run(lock_run["id"], "owner-b", ttl_seconds=60)
        release_run(lock_run["id"], "owner-a")
        third_claim = claim_run(lock_run["id"], "owner-b", ttl_seconds=60)
        release_run(lock_run["id"], "owner-b")
        check(
            "推进锁：同一运行同时只允许一个推进者",
            first_claim and not second_claim and third_claim,
            f"a={first_claim}, b={second_claim}, 释放后 b={third_claim}",
        )

        passed = sum(1 for _, ok, _ in CHECKS if ok)
        total = len(CHECKS)
        print(f"\n结果：{passed}/{total} 通过")
        for name, ok, detail in CHECKS:
            if not ok:
                print(f"  失败：{name}" + (f"（{detail}）" if detail else ""))
        return 0 if passed == total else 1
    finally:
        if os.environ.get("KEEP_REGRESSION_TEMP") == "1":
            print(f"临时目录保留在：{root}")
        else:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
