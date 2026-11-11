from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..config import (
    LEGACY_PRESET_NOTICE,
    SETTING_SPECS,
    load_settings,
    naming_settings_from_options,
    save_settings,
    workflow_settings_from_options,
)
from ..core.preview import KINDS, build_preview, load_preset
from ..core.scan_store import build_samples, get_scan
from ..core.template import validate_template
from ..store import (
    create_naming_preset,
    create_workflow_preset,
    delete_naming_preset,
    delete_workflow_preset,
    duplicate_naming_preset,
    duplicate_workflow_preset,
    ensure_run,
    export_presets,
    get_naming_preset,
    get_workflow_preset,
    import_presets,
    list_naming_presets,
    list_rules,
    list_workflow_presets,
    naming_preset_references,
    set_default_naming_preset,
    set_default_workflow_preset,
    update_naming_preset,
    update_run,
    update_workflow_preset,
)

router = APIRouter()


class TemplateSpec(BaseModel):
    pattern: str = ""
    output_mode: str = "filename"
    legacy_dir: str = ""


class NamingPresetPayload(BaseModel):
    name: str = "未命名命名预设"
    library_profile: str = "generic"
    templates: dict[str, TemplateSpec] = Field(default_factory=dict)
    options: dict | None = None


class WorkflowPresetPayload(BaseModel):
    name: str = "未命名工作流预设"
    library_profile: str = "generic"
    naming_preset_id: str | None = None
    options: dict | None = None


def _template_dict(templates: dict) -> dict:
    return {
        key: value.model_dump() if hasattr(value, "model_dump") else value
        for key, value in templates.items()
    }


def _settings_changes(next_values: dict) -> list[dict]:
    current = load_settings()
    specs = {spec.key: spec for spec in SETTING_SPECS}
    changes = []
    for key, value in next_values.items():
        if current.get(key) == value:
            continue
        spec = specs.get(key)
        changes.append(
            {
                "key": key,
                "label": spec.label if spec else key,
                "current": current.get(key),
                "next": value,
            }
        )
    return changes


# ---------------------------- 命名预设 ----------------------------


@router.get("/naming-presets")
def read_naming_presets() -> dict:
    return {"presets": list_naming_presets()}


@router.post("/naming-presets")
def add_naming_preset(payload: NamingPresetPayload) -> dict:
    data = payload.model_dump()
    data["templates"] = _template_dict(data["templates"])
    return create_naming_preset(data)


@router.put("/naming-presets/{preset_id}")
def modify_naming_preset(preset_id: str, payload: NamingPresetPayload) -> dict:
    data = payload.model_dump()
    data["templates"] = _template_dict(data["templates"])
    preset = update_naming_preset(preset_id, data)
    if preset is None:
        raise HTTPException(status_code=404, detail="命名预设不存在")
    return preset


@router.delete("/naming-presets/{preset_id}")
def remove_naming_preset(preset_id: str) -> dict:
    references = naming_preset_references(preset_id)
    if references:
        names = "、".join(f"「{item['name']}」" for item in references)
        raise HTTPException(
            status_code=409,
            detail=f"这个命名预设正被工作流预设 {names} 使用，请先在这些工作流预设里换成别的命名预设，再删除。",
        )
    if not delete_naming_preset(preset_id):
        raise HTTPException(status_code=404, detail="命名预设不存在")
    return {"ok": True}


@router.post("/naming-presets/{preset_id}/duplicate")
def copy_naming_preset(preset_id: str) -> dict:
    preset = duplicate_naming_preset(preset_id)
    if preset is None:
        raise HTTPException(status_code=404, detail="命名预设不存在")
    return preset


@router.post("/naming-presets/{preset_id}/default")
def make_default_naming_preset(preset_id: str) -> dict:
    preset = set_default_naming_preset(preset_id)
    if preset is None:
        raise HTTPException(status_code=404, detail="命名预设不存在")
    return preset


@router.get("/naming-presets/{preset_id}/settings-diff")
def naming_preset_settings_diff(preset_id: str) -> dict:
    """只比较命名这一层的设置，不把工作流设置算进来。"""
    preset = get_naming_preset(preset_id)
    if preset is None:
        raise HTTPException(status_code=404, detail="命名预设不存在")
    next_values = naming_settings_from_options(preset.get("options") or {})
    return {
        "preset_id": preset_id,
        "preset_name": preset["name"],
        "changes": _settings_changes(next_values),
    }


@router.post("/naming-presets/{preset_id}/apply-settings")
def apply_naming_preset_settings(preset_id: str) -> dict:
    preset = get_naming_preset(preset_id)
    if preset is None:
        raise HTTPException(status_code=404, detail="命名预设不存在")
    values = naming_settings_from_options(preset.get("options") or {})
    return {"preset_id": preset_id, "values": save_settings(values)}


# ---------------------------- 工作流预设 ----------------------------


@router.get("/workflow-presets")
def read_workflow_presets() -> dict:
    return {"presets": list_workflow_presets()}


@router.post("/workflow-presets")
def add_workflow_preset(payload: WorkflowPresetPayload) -> dict:
    data = payload.model_dump()
    if data.get("naming_preset_id") is None:
        data.pop("naming_preset_id", None)
    return create_workflow_preset(data)


@router.put("/workflow-presets/{preset_id}")
def modify_workflow_preset(preset_id: str, payload: WorkflowPresetPayload) -> dict:
    data = payload.model_dump()
    if data.get("naming_preset_id") is None:
        data.pop("naming_preset_id", None)
    try:
        preset = update_workflow_preset(preset_id, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if preset is None:
        raise HTTPException(status_code=404, detail="工作流预设不存在")
    return preset


@router.delete("/workflow-presets/{preset_id}")
def remove_workflow_preset(preset_id: str) -> dict:
    if not delete_workflow_preset(preset_id):
        raise HTTPException(status_code=404, detail="工作流预设不存在")
    return {"ok": True}


@router.post("/workflow-presets/{preset_id}/duplicate")
def copy_workflow_preset(preset_id: str) -> dict:
    preset = duplicate_workflow_preset(preset_id)
    if preset is None:
        raise HTTPException(status_code=404, detail="工作流预设不存在")
    return preset


@router.post("/workflow-presets/{preset_id}/default")
def make_default_workflow_preset(preset_id: str) -> dict:
    preset = set_default_workflow_preset(preset_id)
    if preset is None:
        raise HTTPException(status_code=404, detail="工作流预设不存在")
    return preset


@router.get("/workflow-presets/{preset_id}/settings-diff")
def workflow_preset_settings_diff(preset_id: str) -> dict:
    """只比较工作流这一层的设置，命名选项不参与。"""
    preset = get_workflow_preset(preset_id)
    if preset is None:
        raise HTTPException(status_code=404, detail="工作流预设不存在")
    next_values = workflow_settings_from_options(preset.get("options") or {})
    return {
        "preset_id": preset_id,
        "preset_name": preset["name"],
        "changes": _settings_changes(next_values),
    }


@router.post("/workflow-presets/{preset_id}/apply-settings")
def apply_workflow_preset_settings(preset_id: str) -> dict:
    preset = get_workflow_preset(preset_id)
    if preset is None:
        raise HTTPException(status_code=404, detail="工作流预设不存在")
    values = workflow_settings_from_options(preset.get("options") or {})
    return {"preset_id": preset_id, "values": save_settings(values)}


# ---------------------------- 导出 / 导入（仍保留旧路径） ----------------------------


@router.get("/presets/export")
def export_all() -> dict:
    return export_presets()


@router.post("/presets/import")
def import_all(payload: dict) -> dict:
    created = import_presets(payload)
    return {"created": len(created), "presets": created}


# ---------------------------- 旧接口：明确报错，不返回错数据 ----------------------------


@router.get("/presets")
def legacy_list_presets() -> dict:
    raise HTTPException(status_code=410, detail=LEGACY_PRESET_NOTICE)


@router.post("/presets")
def legacy_create_preset() -> dict:
    raise HTTPException(status_code=410, detail=LEGACY_PRESET_NOTICE)


@router.put("/presets/{preset_id}")
def legacy_update_preset(preset_id: str) -> dict:
    raise HTTPException(status_code=410, detail=LEGACY_PRESET_NOTICE)


@router.delete("/presets/{preset_id}")
def legacy_delete_preset(preset_id: str) -> dict:
    raise HTTPException(status_code=410, detail=LEGACY_PRESET_NOTICE)


@router.post("/presets/{preset_id}/duplicate")
def legacy_duplicate_preset(preset_id: str) -> dict:
    raise HTTPException(status_code=410, detail=LEGACY_PRESET_NOTICE)


@router.post("/presets/{preset_id}/default")
def legacy_default_preset(preset_id: str) -> dict:
    raise HTTPException(status_code=410, detail=LEGACY_PRESET_NOTICE)


@router.get("/presets/{preset_id}/settings-diff")
def legacy_preset_settings_diff(preset_id: str) -> dict:
    raise HTTPException(status_code=410, detail=LEGACY_PRESET_NOTICE)


@router.post("/presets/{preset_id}/apply-settings")
def legacy_apply_preset_settings(preset_id: str) -> dict:
    raise HTTPException(status_code=410, detail=LEGACY_PRESET_NOTICE)


# ---------------------------- 模板校验 / 预览 ----------------------------


class ValidateRequest(BaseModel):
    pattern: str
    output_mode: str = "filename"


@router.post("/templates/validate")
def validate(payload: ValidateRequest) -> dict:
    settings = load_settings()
    result = validate_template(payload.pattern, payload.output_mode, settings)
    result["rendered"] = ""
    return result


class PreviewRequest(BaseModel):
    scan_id: str | None = None
    preset_id: str | None = None
    naming_preset_id: str | None = None
    templates: dict[str, TemplateSpec] | None = None
    file_ids: list[str] | None = None
    folder_ids: list[str] | None = None
    subtitle_mode: str | None = None
    limit: int = 0


def _track_preview(scan_id: str, result: dict, naming_preset_id: str) -> None:
    """记下预览阶段快照：本次有多少警告 / 错误，方便运行记录和待人工清单复用。"""
    run = ensure_run(scan_id, naming_preset_id=naming_preset_id)
    issues = []
    for row in result.get("rows") or []:
        if not isinstance(row, dict) or row.get("status") not in ("warning", "error"):
            continue
        ref_id = str(
            row.get("entry_id") or row.get("file_id") or row.get("original_path") or ""
        )
        if not ref_id:
            continue
        issues.append(
            {
                "status": row.get("status"),
                "ref_id": ref_id,
                "title": str(row.get("new_name") or row.get("original_path") or ""),
                "original_path": row.get("original_path"),
                "target_path": row.get("new_path"),
                "reason": "；".join(str(text) for text in row.get("messages") or []),
            }
        )
    update_run(
        run["id"],
        stage="preview",
        preset_id=naming_preset_id,
        snapshot_patch={
            "naming_preset_id": naming_preset_id,
            "preview": {
                "summary": result.get("summary") or {},
                "issues": issues,
            },
        },
    )


@router.post("/preview")
def preview(payload: PreviewRequest) -> dict:
    # 设置页是唯一权威：命名预设只提供模板与命名选项快照
    settings = load_settings()

    preset = load_preset(payload.naming_preset_id or payload.preset_id)
    if preset is None:
        raise HTTPException(status_code=500, detail="没有可用命名预设")

    scan_payload = get_scan(payload.scan_id) if payload.scan_id else None
    if scan_payload is None:
        scan_payload = build_samples(settings, list_rules())

    templates = {
        kind: spec.model_dump() if hasattr(spec, "model_dump") else spec
        for kind, spec in (payload.templates or {}).items()
        if kind in KINDS
    }

    result = build_preview(
        scan_payload,
        preset,
        settings,
        templates,
        payload.file_ids,
        payload.limit,
        payload.subtitle_mode,
        payload.folder_ids,
    )
    if payload.scan_id and not result.get("is_sample"):
        _track_preview(payload.scan_id, result, preset["id"])
    return {
        "preset_id": result["preset_id"],
        "preset_name": result["preset_name"],
        "is_sample": result["is_sample"],
        "subtitle_mode": result["subtitle_mode"],
        "rows": result["rows"],
        "summary": result["summary"],
        "duplicates": result.get("duplicates", []),
    }
