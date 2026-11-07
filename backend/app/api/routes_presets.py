from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..config import (
    SETTING_SPECS,
    load_settings,
    save_settings,
    setting_values_from_preset_options,
)
from ..core.preview import KINDS, build_preview, load_preset
from ..core.scan_store import build_samples, get_scan
from ..core.template import validate_template
from ..store import (
    create_preset,
    delete_preset,
    duplicate_preset,
    export_presets,
    get_preset,
    import_presets,
    list_presets,
    list_rules,
    set_default_preset,
    update_preset,
)

router = APIRouter()


class TemplateSpec(BaseModel):
    pattern: str = ""
    output_mode: str = "filename"
    legacy_dir: str = ""


class PresetPayload(BaseModel):
    name: str = "未命名预设"
    library_profile: str = "generic"
    templates: dict[str, TemplateSpec] = Field(default_factory=dict)
    options: dict | None = None


@router.get("/presets")
def read_presets() -> dict:
    return {"presets": list_presets()}


@router.post("/presets")
def add_preset(payload: PresetPayload) -> dict:
    data = payload.model_dump()
    data["templates"] = {
        key: value.model_dump() if hasattr(value, "model_dump") else value
        for key, value in data["templates"].items()
    }
    return create_preset(data)


@router.put("/presets/{preset_id}")
def modify_preset(preset_id: str, payload: PresetPayload) -> dict:
    data = payload.model_dump()
    data["templates"] = {
        key: value.model_dump() if hasattr(value, "model_dump") else value
        for key, value in data["templates"].items()
    }
    preset = update_preset(preset_id, data)
    if preset is None:
        raise HTTPException(status_code=404, detail="预设不存在")
    return preset


@router.delete("/presets/{preset_id}")
def remove_preset(preset_id: str) -> dict:
    if not delete_preset(preset_id):
        raise HTTPException(status_code=404, detail="预设不存在")
    return {"ok": True}


@router.post("/presets/{preset_id}/duplicate")
def copy_preset(preset_id: str) -> dict:
    preset = duplicate_preset(preset_id)
    if preset is None:
        raise HTTPException(status_code=404, detail="预设不存在")
    return preset


@router.post("/presets/{preset_id}/default")
def make_default(preset_id: str) -> dict:
    preset = set_default_preset(preset_id)
    if preset is None:
        raise HTTPException(status_code=404, detail="预设不存在")
    return preset


def _preset_or_404(preset_id: str) -> dict:
    preset = get_preset(preset_id)
    if preset is None:
        raise HTTPException(status_code=404, detail="预设不存在")
    return preset


@router.get("/presets/{preset_id}/settings-diff")
def preset_settings_diff(preset_id: str) -> dict:
    """对比预设保存的工作流设置快照与当前设置，供前端切换前确认。"""
    preset = _preset_or_404(preset_id)
    current = load_settings()
    next_values = setting_values_from_preset_options(preset.get("options") or {})
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
    return {"preset_id": preset_id, "preset_name": preset["name"], "changes": changes}


@router.post("/presets/{preset_id}/apply-settings")
def apply_preset_settings(preset_id: str) -> dict:
    """把预设保存的工作流设置快照写入全局设置（用户确认后才调用）。"""
    preset = _preset_or_404(preset_id)
    values = setting_values_from_preset_options(preset.get("options") or {})
    saved = save_settings(values)
    return {"preset_id": preset_id, "values": saved}


@router.get("/presets/export")
def export_all() -> dict:
    return export_presets()


@router.post("/presets/import")
def import_all(payload: dict) -> dict:
    created = import_presets(payload)
    return {"created": len(created), "presets": created}


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
    templates: dict[str, TemplateSpec] | None = None
    file_ids: list[str] | None = None
    subtitle_mode: str | None = None
    limit: int = 0


@router.post("/preview")
def preview(payload: PreviewRequest) -> dict:
    # 设置页是唯一权威：预设只提供命名模板，不再覆盖识别 / 执行选项
    settings = load_settings()

    preset = load_preset(payload.preset_id)
    if preset is None:
        raise HTTPException(status_code=500, detail="没有可用预设")

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
    )
    return {
        "preset_id": result["preset_id"],
        "preset_name": result["preset_name"],
        "is_sample": result["is_sample"],
        "subtitle_mode": result["subtitle_mode"],
        "rows": result["rows"],
        "summary": result["summary"],
    }
