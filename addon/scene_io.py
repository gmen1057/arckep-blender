# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender scene I/O: viewport blockout capture, GLB export, and multi-format asset import.

Functions accept explicit ``context`` and keyword arguments so both Blender UI operators
(Session 2) and the local TCP MCP bridge ``mcp_bridge.py`` (Session 3) call them directly.
"""

from __future__ import annotations

import json
import math
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .api_validation import MAX_MESH_UPLOAD_BYTES, ArcKepValidationError
from .mesh_decoders import (
    import_3mf_meshes,
    import_splat_cloud,
    unpack_zip_and_find_mesh,
)

MESHY_RETEXTURE_MAX_BYTES = 30 * 1024 * 1024  # 30 MiB
_SEGMENT_TASK_TYPES = frozenset(
    {"segment", "smartsegment", "complete", "mesh_segmentation", "smart_mesh_segmentation", "mesh_completion", "parts"}
)


def _require_bpy() -> tuple[Any, Any]:
    try:
        import bpy  # type: ignore[import-not-found]
        from mathutils import Vector  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Blender 'bpy' is required for scene_io operations.") from exc
    return bpy, Vector


def _temp_session_file(prefix: str, suffix: str) -> Path:
    fd_dir = Path(tempfile.gettempdir()) / "arckep_3d_io"
    fd_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=prefix, suffix=suffix, dir=fd_dir, delete=False) as fh:
        return Path(fh.name)


def _world_bbox(objects: Sequence[Any], Vector: Any) -> tuple[Any, Any]:
    meshes = [o for o in objects if getattr(o, "type", None) == "MESH"]
    if not meshes:
        return Vector((-1.0, -1.0, -1.0)), Vector((1.0, 1.0, 1.0))
    mins = [math.inf, math.inf, math.inf]
    maxs = [-math.inf, -math.inf, -math.inf]
    for obj in meshes:
        for corner in obj.bound_box:
            wp = obj.matrix_world @ Vector(corner)
            for i in range(3):
                mins[i] = min(mins[i], wp[i])
                maxs[i] = max(maxs[i], wp[i])
    return Vector(mins), Vector(maxs)


def capture_viewport_png(
    context: Any,
    *,
    mode: str = "selection",
    resolution: int = 1024,
    dest_path: str | Path | None = None,
) -> dict[str, Any]:
    """Render ``selection``, ``camera``, or ``scene`` to PNG with Cycles CPU headless fallback."""
    bpy, Vector = _require_bpy()
    if mode not in ("selection", "camera", "scene"):
        raise ArcKepValidationError(400, "INVALID_PARAMETER", f"Unsupported capture mode {mode!r}.")
    res = max(64, min(4096, int(resolution)))
    out_path = Path(dest_path) if dest_path else _temp_session_file("viewport_", ".png")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    scene = context.scene
    rnd = scene.render
    img_cfg = rnd.image_settings
    orig_state = (
        rnd.engine, rnd.resolution_x, rnd.resolution_y, rnd.resolution_percentage,
        rnd.filepath, img_cfg.file_format, img_cfg.color_mode, scene.camera,
    )
    cycles = getattr(scene, "cycles", None)
    orig_cycles = (
        (cycles.device, cycles.samples, getattr(cycles, "use_denoising", False))
        if cycles is not None
        else None
    )
    temp_objs: list[Any] = []
    temp_cams: list[Any] = []
    temp_lights: list[Any] = []

    try:
        if mode in ("selection", "scene") or scene.camera is None:
            pool = (
                [o for o in context.selected_objects if o.type == "MESH"]
                if mode == "selection"
                else []
            )
            if not pool:
                pool = [o for o in scene.objects if o.type == "MESH"]
            bmin, bmax = _world_bbox(pool, Vector)
            center = (bmin + bmax) * 0.5
            radius = max((bmax - bmin).length * 0.5, 0.75)
            cam_data = bpy.data.cameras.new(name="_ArcKepTempCam")
            cam_data.lens = 50.0
            cam_data.clip_start = 0.01
            cam_data.clip_end = max(1000.0, radius * 25.0)
            temp_cams.append(cam_data)
            cam_obj = bpy.data.objects.new("_ArcKepTempCamObj", cam_data)
            cam_pos = center + Vector((radius * 1.9, -radius * 1.9, radius * 1.35))
            cam_obj.location = cam_pos
            cam_obj.rotation_euler = (center - cam_pos).normalized().to_track_quat("-Z", "Y").to_euler()
            scene.collection.objects.link(cam_obj)
            temp_objs.append(cam_obj)
            scene.camera = cam_obj

        if not any(o.type == "LIGHT" for o in scene.objects):
            light_data = bpy.data.lights.new(name="_ArcKepTempSun", type="SUN")
            light_data.energy = 3.0
            temp_lights.append(light_data)
            light_obj = bpy.data.objects.new("_ArcKepTempSunObj", light_data)
            light_obj.rotation_euler = (0.65, 0.2, 0.75)
            scene.collection.objects.link(light_obj)
            temp_objs.append(light_obj)

        rnd.resolution_x = res
        rnd.resolution_y = res
        rnd.resolution_percentage = 100
        img_cfg.file_format = "PNG"
        img_cfg.color_mode = "RGBA"
        rnd.filepath = str(out_path)

        rendered = False
        if not bpy.app.background:
            try:
                op_res = bpy.ops.render.opengl(write_still=True)
                rendered = op_res == {"FINISHED"} and out_path.is_file() and out_path.stat().st_size > 0
            except RuntimeError:
                rendered = False

        if not rendered:
            rnd.engine = "CYCLES"
            if cycles is not None:
                cycles.device = "CPU"
                cycles.samples = 4
                if hasattr(cycles, "use_denoising"):
                    cycles.use_denoising = False
            bpy.ops.render.render(write_still=True)
    finally:
        (
            rnd.engine, rnd.resolution_x, rnd.resolution_y, rnd.resolution_percentage,
            rnd.filepath, img_cfg.file_format, img_cfg.color_mode, scene.camera,
        ) = orig_state
        if cycles is not None and orig_cycles is not None:
            cycles.device, cycles.samples, denoise = orig_cycles
            if hasattr(cycles, "use_denoising"):
                cycles.use_denoising = denoise
        for obj in temp_objs:
            if obj.name in bpy.data.objects:
                bpy.data.objects.remove(obj, do_unlink=True)
        for cam in temp_cams:
            if cam.name in bpy.data.cameras:
                bpy.data.cameras.remove(cam)
        for lgt in temp_lights:
            if lgt.name in bpy.data.lights:
                bpy.data.lights.remove(lgt)

    if not out_path.is_file() or out_path.stat().st_size == 0:
        raise ArcKepValidationError(500, "RENDER_FAILED", "Failed to capture viewport PNG.")
    return {"filepath": str(out_path), "width": res, "height": res, "size_bytes": out_path.stat().st_size}


def export_selection_glb(
    context: Any,
    *,
    object_names: Sequence[str] | None = None,
    max_bytes: int = MAX_MESH_UPLOAD_BYTES,
    dest_path: str | Path | None = None,
) -> dict[str, Any]:
    """Export selected (or named) scene meshes to a temporary ``.glb`` and enforce byte caps."""
    bpy, _ = _require_bpy()
    if object_names:
        targets = [
            obj
            for name in object_names
            if (obj := context.scene.objects.get(name) or bpy.data.objects.get(name)) is not None
        ]
    else:
        targets = list(context.selected_objects)
    mesh_targets = [o for o in targets if o.type == "MESH"]
    if not mesh_targets:
        raise ArcKepValidationError(
            400, "NO_MESH_SELECTED", "Select at least one MESH object in the Blender scene."
        )

    job_ids = {int(o["arckep_job_id"]) for o in mesh_targets if o.get("arckep_job_id") is not None}
    source_job_id = next(iter(job_ids)) if len(job_ids) == 1 else None

    auto_created = dest_path is None
    out_path = Path(dest_path) if dest_path else _temp_session_file("selection_", ".glb")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    view_layer = context.view_layer
    orig_active = view_layer.objects.active
    orig_mode = orig_active.mode if orig_active is not None else "OBJECT"
    orig_hidden = (
        {o.name: o.hide_get() for o in targets if o.name in view_layer.objects}
        if object_names
        else {}
    )
    orig_selected = [o for o in view_layer.objects if o.select_get()]

    try:
        if orig_active is not None and orig_mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for name, was_hidden in orig_hidden.items():
            if was_hidden and name in view_layer.objects:
                view_layer.objects[name].hide_set(False)
        for obj in view_layer.objects:
            obj.select_set(False)
        for obj in targets:
            if obj.name in view_layer.objects:
                obj.select_set(True)
        if mesh_targets[0].name in view_layer.objects:
            view_layer.objects.active = mesh_targets[0]
        bpy.ops.export_scene.gltf(
            filepath=str(out_path),
            export_format="GLB",
            use_selection=True,
            export_apply=True,
            export_yup=True,
        )
    finally:
        for obj in view_layer.objects:
            obj.select_set(obj in orig_selected)
        for name, was_hidden in orig_hidden.items():
            if name in view_layer.objects:
                view_layer.objects[name].hide_set(was_hidden)
        if orig_active is not None and orig_active.name in view_layer.objects:
            view_layer.objects.active = orig_active
            if orig_mode != "OBJECT":
                bpy.ops.object.mode_set(mode=orig_mode)

    if not out_path.is_file() or out_path.stat().st_size == 0:
        raise ArcKepValidationError(500, "EXPORT_FAILED", "Blender glTF exporter produced no file.")
    size = out_path.stat().st_size
    if size > max_bytes:
        if auto_created:
            out_path.unlink(missing_ok=True)
        raise ArcKepValidationError(
            413,
            "FILE_TOO_LARGE",
            f"Exported GLB is {size / (1024 * 1024):.1f} MiB (limit: {max_bytes / (1024 * 1024):.0f} MiB).",
            {"max_bytes": max_bytes, "size_bytes": size},
        )
    return {
        "filepath": str(out_path),
        "size_bytes": size,
        "source_job_id": source_job_id,
        "object_names": [o.name for o in mesh_targets],
    }


def import_asset_to_scene(
    context: Any,
    filepath: str | Path,
    *,
    fmt: str | None = None,
    name_prefix: str | None = None,
    location: str | Sequence[float] | None = "cursor",
    scale_target_m: float | None = None,
    collection_name: str | None = None,
    arckep_job_id: int | None = None,
    arckep_task_type: str | None = None,
    arckep_model: str | None = None,
    extra_tags: Mapping[str, Any] | None = None,
    replace_object: str | None = None,
) -> list[Any]:
    """Import any supported 3D asset into Blender, place it, group parts, and stamp metadata."""
    bpy, Vector = _require_bpy()
    path = Path(filepath)
    if not path.is_file():
        raise ArcKepValidationError(400, "FILE_NOT_FOUND", f"Asset file not found: {path}")

    if path.suffix.lower() == ".zip":
        path = unpack_zip_and_find_mesh(path)
        ext = path.suffix.lower().lstrip(".")
    else:
        ext = (fmt or path.suffix).lower().lstrip(".")
        if path.suffix.lower() in (".glb", ".ply", ".fbx", ".obj", ".usdz", ".stl", ".3mf", ".splat"):
            ext = path.suffix.lower().lstrip(".")

    before_names = set(bpy.data.objects.keys())
    prefix = name_prefix or (f"ArcKep_{arckep_job_id}" if arckep_job_id else path.stem)

    if ext in ("glb", "gltf"):
        bpy.ops.import_scene.gltf(filepath=str(path))
    elif ext == "fbx":
        bpy.ops.import_scene.fbx(filepath=str(path))
    elif ext == "obj":
        bpy.ops.wm.obj_import(filepath=str(path))
    elif ext in ("usdz", "usd", "usda", "usdc"):
        bpy.ops.wm.usd_import(filepath=str(path))
    elif ext == "stl":
        bpy.ops.wm.stl_import(filepath=str(path))
    elif ext == "ply":
        bpy.ops.wm.ply_import(filepath=str(path))
    elif ext == "3mf":
        import_3mf_meshes(context, path, prefix)
    elif ext == "splat":
        import_splat_cloud(context, path, prefix)
    else:
        raise ArcKepValidationError(400, "UNSUPPORTED_FORMAT", f"Unsupported 3D format {ext!r}.")

    created = [bpy.data.objects[name] for name in bpy.data.objects.keys() if name not in before_names]
    if not created:
        raise ArcKepValidationError(500, "IMPORT_EMPTY", f"Importer created no objects for {path.name}.")

    target_col_name = collection_name
    if not target_col_name and arckep_task_type in _SEGMENT_TASK_TYPES:
        target_col_name = f"ArcKep_Parts_{arckep_job_id or 'scene'}"
    if target_col_name:
        col = bpy.data.collections.get(target_col_name)
        if col is None:
            col = bpy.data.collections.new(target_col_name)
            context.scene.collection.children.link(col)
        for obj in created:
            for existing_col in list(obj.users_collection):
                existing_col.objects.unlink(obj)
            col.objects.link(obj)

    roots = [o for o in created if o.parent not in created]
    if scale_target_m is not None and float(scale_target_m) > 0.0:
        context.view_layer.update()
        bmin, bmax = _world_bbox(created, Vector)
        max_dim = max(bmax - bmin)
        if max_dim > 1e-6:
            factor = float(scale_target_m) / max_dim
            for root in roots:
                root.scale = tuple(s * factor for s in root.scale)

    offset_vec = None
    if location == "cursor":
        offset_vec = Vector(context.scene.cursor.location)
    elif isinstance(location, Sequence) and not isinstance(location, str) and len(location) == 3:
        offset_vec = Vector((float(location[0]), float(location[1]), float(location[2])))
    if offset_vec is not None:
        for root in roots:
            root.location = Vector(root.location) + offset_vec

    if replace_object:
        orig_obj = context.scene.objects.get(replace_object)
        if orig_obj is not None and orig_obj not in created:
            orig_obj.hide_set(True)
            orig_obj.hide_render = True

    if name_prefix and len(created) == 1:
        created[0].name = name_prefix
    for obj in created:
        if arckep_job_id is not None:
            obj["arckep_job_id"] = int(arckep_job_id)
        if arckep_model:
            obj["arckep_model"] = str(arckep_model)
        if arckep_task_type:
            obj["arckep_task_type"] = str(arckep_task_type)
        if extra_tags:
            for k, v in extra_tags.items():
                if v is not None:
                    obj[k] = v

    context.view_layer.update()
    return created


def load_multiview_manifest_to_blender(manifest_path: str | Path) -> dict[str, Any]:
    """Load 4 downloaded multiview PNGs into ``bpy.data.images`` on the main thread."""
    bpy, _ = _require_bpy()
    data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    views: dict[str, str] = data.get("views") or {}
    loaded: dict[str, str] = {}
    for view_name, img_path in views.items():
        if Path(img_path).is_file():
            img = bpy.data.images.load(img_path, check_existing=True)
            loaded[view_name] = img.name
    return {"job_id": int(data["job_id"]), "images": loaded, "paths": views}
