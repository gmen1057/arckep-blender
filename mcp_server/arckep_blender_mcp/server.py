# SPDX-License-Identifier: MIT
"""Stdio FastMCP server connecting Blender 4.2+ scenes with ArcKep ``/api/v1/models3d``."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .api_bridge import (
    ArcKepCloudBridge,
    build_confirmation_required_response,
    evaluate_budget,
    resolve_runtime_policy,
    round_rub,
)
from .blender_rpc import BlenderBridgeError, BlenderRpcClient
from .payload_builders import (
    MAX_MESH_UPLOAD_BYTES,
    ArcKepApiError,
    ArcKepValidationError,
    validate_rig_check_xor,
)

mcp = FastMCP("arckep_blender_mcp")

_RO_LOCAL = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
_RW_LOCAL = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
_RO_CLOUD = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)
_RW_CLOUD = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)

ModelArg = Annotated[str, Field(description="Model ID: rodin-v2.5, meshy-v7.1, tripo-h3.1, tripo-p2, tripo-p1, hunyuan-3d-3.1, tripo-multiview, tripo-splat, or idol.")]
OpArg = Annotated[str, Field(description="Operation: texture, decimate, uv, segment, smartsegment, complete, rig, retarget, rig_and_retarget, or convert.")]
PromptArg = Annotated[str | None, Field(description="Text prompt (XOR with image/viewport/multiview sources).")]
ImgPathArg = Annotated[str | None, Field(description="Single local reference/style image path.")]
ImgPathsArg = Annotated[list[str] | None, Field(description="Multiple local reference image paths.")]
VpModeArg = Annotated[str | None, Field(description="Capture Blender viewport PNG: 'selection', 'camera', or 'scene'.")]
VpResArg = Annotated[int, Field(description="Viewport PNG resolution (512..2048).")]
MvJobArg = Annotated[int | None, Field(description="Existing completed tripo-multiview job ID.")]
ObjNameArg = Annotated[str | None, Field(description="Blender scene object name.")]
SrcJobArg = Annotated[int | None, Field(description="Explicit owned ArcKep job ID.")]
MeshPathArg = Annotated[str | None, Field(description="Local mesh file path (.glb/.fbx/.obj/.stl).")]
UseObjJobArg = Annotated[bool, Field(description="Reuse obj['arckep_job_id'] when present instead of exporting .glb.")]
StylePathsArg = Annotated[list[str] | None, Field(description="1..4 style reference image paths (meshy-n1-retexture / tripo).")]
LocationArg = Annotated[list[float] | str | None, Field(description="'cursor' or [x, y, z] in meters.")]
ScaleArg = Annotated[float | None, Field(description="Optional target longest dimension in meters.")]
NameArg = Annotated[str | None, Field(description="Optional Blender object name prefix.")]
CollectionArg = Annotated[str | None, Field(description="Optional target Blender collection name.")]
OptionsArg = Annotated[dict[str, Any] | None, Field(description="Model/operation-specific options dict.")]
ConfirmedCapArg = Annotated[float | None, Field(description="Explicit user-confirmed RUB spend cap (>= cost_rub).")]


def _dump(data: Mapping[str, Any]) -> str:
    return json.dumps(dict(data), indent=2, ensure_ascii=False)


async def _run_tool(fn: Callable[[], Mapping[str, Any]]) -> str:
    try:
        return _dump(await asyncio.to_thread(fn))
    except ArcKepApiError as exc:
        return _dump({"ok": False, "error_code": exc.error_code, "status_code": exc.status_code, "message": exc.message, **({"details": exc.details} if exc.details else {})})
    except BlenderBridgeError as exc:
        return _dump({"ok": False, "error_code": exc.code, "message": exc.message, **({"details": exc.details} if exc.details else {})})
    except ValueError as exc:
        return _dump({"ok": False, "error_code": "INVALID_PARAMETER", "message": str(exc)})
    except Exception as exc:
        return _dump({"ok": False, "error_code": "INTERNAL_ERROR", "message": f"{type(exc).__name__}: {exc}"})


def _quote_summary(bridge: ArcKepCloudBridge, est_path: str, payload: Mapping[str, Any], label_k: str, label_v: str) -> dict[str, Any]:
    quote = bridge.request_json("POST", est_path, payload=payload)
    cost_str = str(quote.get("cost_rub") or "0.00")
    dec = evaluate_budget(cost_str, bridge.policy.budget_mode, bridge.policy.max_auto_cost_rub)
    return {"ok": True, label_k: label_v, "estimated_cost_rub": cost_str, "cost_rub": round_rub(cost_str), "budget_mode": bridge.policy.budget_mode, "max_auto_cost_rub": bridge.policy.max_auto_cost_rub, "would_require_confirmation": dec.requires_confirmation, "required_confirmed_max_cost_rub": round_rub(cost_str) if dec.requires_confirmation else None, "quote": quote, "payload": dict(payload)}


def _execute_paid_job(
    bridge: ArcKepCloudBridge, est_path: str, gen_path: str, payload: Mapping[str, Any], *,
    label_k: str, label_v: str, confirmed_max_cost_rub: float | None, wait: bool, import_to_scene: bool,
    location: list[float] | str | None, scale_target_m: float | None, name: str | None,
    collection: str | None, replace_object: str | None, force_parts_col: bool,
) -> dict[str, Any]:
    quote = bridge.request_json("POST", est_path, payload=payload)
    cost_str = str(quote.get("cost_rub") or "0.00")
    dec = evaluate_budget(cost_str, bridge.policy.budget_mode, bridge.policy.max_auto_cost_rub, confirmed_max_cost_rub)
    if dec.requires_confirmation:
        return build_confirmation_required_response(cost_str, bridge.policy.budget_mode, bridge.policy.max_auto_cost_rub, dec.message)
    queued = bridge.submit_paid_job(gen_path, payload, dec.effective_max_cost_rub)
    jid = int(queued["id"])
    base = {"ok": True, "job_id": jid, label_k: label_v, "estimated_cost_rub": cost_str}
    if not wait:
        return {**base, "status": queued.get("status"), "imported": False, "job": queued}
    done = bridge.poll_generation(jid)
    dl = bridge.download_completed_asset(done)
    if label_v == "tripo-multiview" or dl["kind"] == "multiview":
        return {**base, "status": "completed", "multiview_paths": dl.get("multiview_paths", {}), "manifest_path": dl.get("manifest_path"), "imported": False, "job": done}
    local_path = dl.get("local_path")
    if import_to_scene and local_path:
        is_parts = force_parts_col or bool(payload.get("generate_parts") or (done.get("output") or {}).get("segmented_parts"))
        task_t = label_v if label_k == "operation" else ("parts" if is_parts else "create")
        model_tag = label_v if label_k == "model" else str(done.get("model") or f"process-{label_v}")
        imp = bridge.require_rpc().import_asset(filepath=local_path, name=name, location=location, scale_target_m=scale_target_m, collection=collection or (f"ArcKep_Parts_{jid}" if is_parts else None), arckep_job_id=jid, arckep_task_type=task_t, arckep_model=model_tag, replace_object=replace_object)
        return {**base, "status": "completed", "local_path": local_path, "imported": True, "imported_objects": imp.get("imported_objects", []), "scene_import": imp, "job": done}
    return {**base, "status": "completed", "local_path": local_path, "imported": False, "job": done}


@mcp.tool(name="blender_ping", annotations=_RO_LOCAL.model_copy(update={"title": "Ping Blender Bridge"}))
async def blender_ping() -> str:
    """Inspect local Blender MCP bridge health, active .blend file, and effective RUB Budget Guard policy."""
    def _op() -> dict[str, Any]:
        rpc = BlenderRpcClient()
        ping = rpc.ping()
        pol = resolve_runtime_policy(rpc, ping_info=ping)
        return {"ok": True, **ping, "bridge_host": rpc.host, "bridge_port": rpc.port, "mcp_api_key_configured": bool(pol.api_key), "effective_api_base_url": pol.api_base_url, "effective_budget_mode": pol.budget_mode, "effective_max_auto_cost_rub": pol.max_auto_cost_rub, "budget_source": pol.budget_source}
    return await _run_tool(_op)


@mcp.tool(name="blender_get_scene_info", annotations=_RO_LOCAL.model_copy(update={"title": "Get Scene Info"}))
async def blender_get_scene_info() -> str:
    """List collections, scene objects, face counts, materials, arckep_job_id tags, 3D cursor, and camera."""
    return await _run_tool(lambda: {"ok": True, **BlenderRpcClient().get_scene_info()})


@mcp.tool(name="blender_get_object_info", annotations=_RO_LOCAL.model_copy(update={"title": "Get Object Info"}))
async def blender_get_object_info(name: Annotated[str, Field(description="Exact Blender object name in the scene.")]) -> str:
    """Return world bounding box, mesh/UV stats, bones, animation actions, modifiers, and arckep_job_id."""
    return await _run_tool(lambda: {"ok": True, **BlenderRpcClient().get_object_info(name)})


@mcp.tool(name="blender_capture_viewport", annotations=_RO_LOCAL.model_copy(update={"title": "Capture Viewport PNG"}))
async def blender_capture_viewport(mode: Annotated[str, Field(description="Framing mode: 'scene', 'selection', or 'camera'.")] = "scene", resolution: VpResArg = 1024) -> str:
    """Render the Blender camera, selected meshes, or full scene to a PNG file (512..2048 px)."""
    return await _run_tool(lambda: {"ok": True, "mode": mode, **BlenderRpcClient().capture_viewport(mode, resolution)})


@mcp.tool(name="blender_import_asset", annotations=_RW_LOCAL.model_copy(update={"title": "Import Local 3D Asset"}))
async def blender_import_asset(
    filepath: Annotated[str, Field(description="Local 3D file path (.glb/.fbx/.obj/.zip/.usdz/.stl/.ply/.3mf/.splat).")],
    format: Annotated[str | None, Field(description="Optional format hint (e.g. 'glb', 'fbx', '3mf', 'splat').")] = None,
    name: NameArg = None, location: LocationArg = "cursor", scale_target_m: ScaleArg = None, collection: CollectionArg = None,
    arckep_job_id: Annotated[int | None, Field(description="Optional ArcKep job ID to stamp on imported objects.")] = None,
    replace_object: Annotated[str | None, Field(description="Optional existing object name to hide after import.")] = None,
) -> str:
    """Import a local 3D mesh/splat into Blender at the 3D Cursor or world coordinates."""
    return await _run_tool(lambda: {"ok": True, **BlenderRpcClient().import_asset(filepath=filepath, format=format, name=name, location=location, scale_target_m=scale_target_m, collection=collection, arckep_job_id=arckep_job_id, replace_object=replace_object)})


@mcp.tool(name="blender_transform_object", annotations=_RW_LOCAL.model_copy(update={"title": "Transform Scene Object", "idempotentHint": True}))
async def blender_transform_object(
    name: Annotated[str, Field(description="Target Blender object name.")],
    location: Annotated[list[float] | None, Field(description="Optional world location [x, y, z] in meters.")] = None,
    rotation_euler_deg: Annotated[list[float] | None, Field(description="Optional XYZ Euler angles [rx, ry, rz] in degrees.")] = None,
    scale: Annotated[list[float] | float | None, Field(description="Optional uniform scale float or [sx, sy, sz].")] = None,
    place_on_ground: Annotated[bool, Field(description="Place object's world bounding-box bottom onto Z = 0.")] = False,
) -> str:
    """Move, rotate (in degrees), scale, and optionally rest a Blender object cleanly on Z = 0."""
    return await _run_tool(lambda: {"ok": True, **BlenderRpcClient().transform_object(name=name, location=location, rotation_euler_deg=rotation_euler_deg, scale=scale, place_on_ground=place_on_ground)})


@mcp.tool(name="arckep_get_balance", annotations=_RO_CLOUD.model_copy(update={"title": "Get ArcKep Balance"}))
async def arckep_get_balance() -> str:
    """Fetch ArcKep RUB balance (personal or B2B corporate) and active Budget Guard policy."""
    def _op() -> dict[str, Any]:
        bridge = ArcKepCloudBridge()
        return {"ok": True, **bridge.request_json("GET", "/balance"), "budget_mode": bridge.policy.budget_mode, "max_auto_cost_rub": bridge.policy.max_auto_cost_rub, "budget_source": bridge.policy.budget_source}
    return await _run_tool(_op)


@mcp.tool(name="arckep_list_generations", annotations=_RO_CLOUD.model_copy(update={"title": "List 3D Generations"}))
async def arckep_list_generations(
    limit: Annotated[int, Field(description="Page size (1..100) when job_id is omitted.")] = 20,
    before_id: Annotated[int | None, Field(description="Pagination cursor: fetch jobs with ID < before_id.")] = None,
    job_id: Annotated[int | None, Field(description="Specific generation job ID to inspect.")] = None,
) -> str:
    """List recent ArcKep 3D generations or fetch a single job status/output card by job_id."""
    def _op() -> dict[str, Any]:
        bridge = ArcKepCloudBridge()
        if job_id is not None:
            return {"ok": True, "job": bridge.request_json("GET", f"/models3d/generations/{int(job_id)}")}
        if not (1 <= int(limit) <= 100):
            raise ArcKepValidationError(400, "INVALID_PARAMETER", "limit must be in 1..100.")
        q = f"?limit={int(limit)}" + (f"&before_id={int(before_id)}" if before_id is not None else "")
        res = bridge.request_json("GET", f"/models3d/generations{q}")
        rows = res.get("data") if isinstance(res.get("data"), list) else []
        next_cursor = rows[-1]["id"] if res.get("has_more") and rows else None
        return {"ok": True, **res, "count": len(rows), "next_before_id": next_cursor}
    return await _run_tool(_op)


@mcp.tool(name="arckep_import_generation", annotations=_RW_CLOUD.model_copy(update={"title": "Import ArcKep Job"}))
async def arckep_import_generation(
    job_id: Annotated[int, Field(description="Completed ArcKep 3D job ID to download and import.")],
    location: LocationArg = "cursor", scale_target_m: ScaleArg = None, name: NameArg = None,
    collection: CollectionArg = None, replace_object: Annotated[str | None, Field(description="Optional scene object name to hide.")] = None,
) -> str:
    """Download a completed generation from the ArcKep archive and import it into Blender with arckep_job_id."""
    def _op() -> dict[str, Any]:
        rpc = BlenderRpcClient()
        bridge = ArcKepCloudBridge(rpc=rpc)
        job = bridge.request_json("GET", f"/models3d/generations/{int(job_id)}")
        if job.get("status") != "completed":
            raise ArcKepValidationError(409, "JOB_NOT_COMPLETED", f"Job #{job_id} status is {job.get('status')!r} (expected 'completed').")
        dl = bridge.download_completed_asset(job)
        if dl["kind"] == "multiview":
            return {"ok": True, "job_id": int(job_id), "kind": "multiview", "multiview_paths": dl["multiview_paths"], "manifest_path": dl["manifest_path"], "imported": False, "job": job}
        if not dl.get("local_path"):
            raise ArcKepApiError(502, "MISSING_OUTPUT_ASSET", f"Job #{job_id} has no downloadable 3D asset.")
        is_parts = bool((job.get("output") or {}).get("segmented_parts"))
        imp = rpc.import_asset(filepath=dl["local_path"], name=name, location=location, scale_target_m=scale_target_m, collection=collection or (f"ArcKep_Parts_{int(job_id)}" if is_parts else None), arckep_job_id=int(job_id), arckep_task_type="parts" if is_parts else "archive", arckep_model=str(job.get("model") or ""), replace_object=replace_object)
        return {"ok": True, "job_id": int(job_id), "local_path": dl["local_path"], "imported": True, "imported_objects": imp.get("imported_objects", []), "scene_import": imp, "job": job}
    return await _run_tool(_op)


@mcp.tool(name="arckep_estimate_generation", annotations=_RO_CLOUD.model_copy(update={"title": "Estimate 3D Generation (0 RUB)"}))
async def arckep_estimate_generation(model: ModelArg, prompt: PromptArg = None, image_path: ImgPathArg = None, image_paths: ImgPathsArg = None, viewport_capture_mode: VpModeArg = None, viewport_resolution: VpResArg = 1024, source_multiview_job_id: MvJobArg = None, options: OptionsArg = None) -> str:
    """Quote exact RUB price via free POST /api/v1/models3d/estimate and preview Budget Guard status."""
    def _op() -> dict[str, Any]:
        bridge = ArcKepCloudBridge()
        payload = bridge.prepare_generation_payload(model, prompt=prompt, image_path=image_path, image_paths=image_paths, viewport_capture_mode=viewport_capture_mode, viewport_resolution=viewport_resolution, source_multiview_job_id=source_multiview_job_id, options=options)
        return _quote_summary(bridge, "/models3d/estimate", payload, "model", model)
    return await _run_tool(_op)


@mcp.tool(name="arckep_generate_3d", annotations=_RW_CLOUD.model_copy(update={"title": "Generate 3D Asset"}))
async def arckep_generate_3d(
    model: ModelArg, prompt: PromptArg = None, image_path: ImgPathArg = None, image_paths: ImgPathsArg = None,
    viewport_capture_mode: VpModeArg = None, viewport_resolution: VpResArg = 1024,
    source_multiview_job_id: MvJobArg = None, options: OptionsArg = None, confirmed_max_cost_rub: ConfirmedCapArg = None,
    wait: Annotated[bool, Field(description="Poll until job completes.")] = True,
    import_to_scene: Annotated[bool, Field(description="Import generated 3D asset into Blender scene.")] = True,
    location: LocationArg = "cursor", scale_target_m: ScaleArg = None, name: NameArg = None, collection: CollectionArg = None,
) -> str:
    """Estimate, enforce 3-mode RUB Budget Guard, generate a 3D asset (or 4 multiviews), and import into Blender."""
    def _op() -> dict[str, Any]:
        bridge = ArcKepCloudBridge()
        payload = bridge.prepare_generation_payload(model, prompt=prompt, image_path=image_path, image_paths=image_paths, viewport_capture_mode=viewport_capture_mode, viewport_resolution=viewport_resolution, source_multiview_job_id=source_multiview_job_id, options=options)
        return _execute_paid_job(bridge, "/models3d/estimate", "/models3d/generations", payload, label_k="model", label_v=model, confirmed_max_cost_rub=confirmed_max_cost_rub, wait=wait, import_to_scene=import_to_scene, location=location, scale_target_m=scale_target_m, name=name, collection=collection, replace_object=None, force_parts_col=False)
    return await _run_tool(_op)


@mcp.tool(name="arckep_check_rig", annotations=_RO_CLOUD.model_copy(update={"title": "Check Auto-Rig Compatibility (0 RUB)"}))
async def arckep_check_rig(object_name: ObjNameArg = None, source_job_id: SrcJobArg = None, mesh_path: MeshPathArg = None) -> str:
    """Run free 0 RUB auto-rig compatibility check (reuses obj['arckep_job_id'] or exports .glb)."""
    def _op() -> dict[str, Any]:
        obj_n, m_p = (object_name or "").strip() or None, (mesh_path or "").strip() or None
        jid = int(source_job_id) if source_job_id not in (None, 0, False, "") else None
        if sum((obj_n is not None, jid is not None, m_p is not None)) != 1:
            raise ArcKepValidationError(400, "INVALID_PARAMETER", "Provide exactly one of object_name, source_job_id, or mesh_path.")
        bridge = ArcKepCloudBridge()
        if jid is not None:
            body = validate_rig_check_xor(jid, None)
        elif m_p is not None:
            if Path(m_p).suffix.lower() in (".gltf", ".zip"):
                raise ArcKepValidationError(400, "INVALID_PARAMETER", "rig-check forbids .gltf and .zip; use binary .glb.")
            body = validate_rig_check_xor(None, str(bridge.upload_file_cached(m_p, max_bytes=MAX_MESH_UPLOAD_BYTES)["key"]))
        else:
            assert obj_n is not None
            found = bridge.require_rpc().get_object_info(obj_n).get("arckep_job_id")
            if found not in (None, 0, False, ""):
                body = validate_rig_check_xor(int(found), None)
            else:
                exp = bridge.require_rpc().export_selection_glb(names=[obj_n], max_bytes=MAX_MESH_UPLOAD_BYTES)
                body = validate_rig_check_xor(None, str(bridge.upload_file_cached(exp["filepath"], max_bytes=MAX_MESH_UPLOAD_BYTES)["key"]))
        return {"ok": True, **bridge.request_json("POST", "/models3d/rig-check", payload=body)}
    return await _run_tool(_op)


@mcp.tool(name="arckep_estimate_process", annotations=_RO_CLOUD.model_copy(update={"title": "Estimate Mesh Post-Process (0 RUB)"}))
async def arckep_estimate_process(operation: OpArg, object_name: ObjNameArg = None, source_job_id: SrcJobArg = None, mesh_path: MeshPathArg = None, use_object_job_id: UseObjJobArg = True, image_path: ImgPathArg = None, style_image_paths: StylePathsArg = None, options: OptionsArg = None) -> str:
    """Quote exact RUB price via free POST /api/v1/models3d/process/estimate across all 10 mesh operations."""
    def _op() -> dict[str, Any]:
        bridge = ArcKepCloudBridge()
        payload = bridge.prepare_process_payload(operation, object_name=object_name, source_job_id=source_job_id, mesh_path=mesh_path, use_object_job_id=use_object_job_id, image_path=image_path, style_image_paths=style_image_paths, options=options)
        return _quote_summary(bridge, "/models3d/process/estimate", payload, "operation", operation)
    return await _run_tool(_op)


@mcp.tool(name="arckep_process_mesh", annotations=_RW_CLOUD.model_copy(update={"title": "Post-Process 3D Mesh"}))
async def arckep_process_mesh(
    operation: OpArg, object_name: ObjNameArg = None, source_job_id: SrcJobArg = None, mesh_path: MeshPathArg = None,
    use_object_job_id: UseObjJobArg = True, image_path: ImgPathArg = None, style_image_paths: StylePathsArg = None,
    options: OptionsArg = None, confirmed_max_cost_rub: ConfirmedCapArg = None,
    wait: Annotated[bool, Field(description="Poll until job completes.")] = True,
    import_to_scene: Annotated[bool, Field(description="Import processed asset into Blender.")] = True,
    hide_original: Annotated[bool, Field(description="Hide original object_name in Blender upon import.")] = True,
    location: LocationArg = "cursor", scale_target_m: ScaleArg = None, name: NameArg = None, collection: CollectionArg = None,
) -> str:
    """Estimate, enforce RUB Budget Guard, run mesh post-processing, and import result into Blender."""
    def _op() -> dict[str, Any]:
        bridge = ArcKepCloudBridge()
        payload = bridge.prepare_process_payload(operation, object_name=object_name, source_job_id=source_job_id, mesh_path=mesh_path, use_object_job_id=use_object_job_id, image_path=image_path, style_image_paths=style_image_paths, options=options)
        replace_obj = ((object_name or "").strip() or None) if hide_original else None
        return _execute_paid_job(bridge, "/models3d/process/estimate", "/models3d/process/generations", payload, label_k="operation", label_v=operation, confirmed_max_cost_rub=confirmed_max_cost_rub, wait=wait, import_to_scene=import_to_scene, location=location, scale_target_m=scale_target_m, name=name, collection=collection, replace_object=replace_obj, force_parts_col=operation in ("segment", "smartsegment", "complete"))
    return await _run_tool(_op)


@mcp.tool(name="arckep_export_print_stl", annotations=_RW_CLOUD.model_copy(update={"title": "Export Printable STL/3MF (0 RUB)", "idempotentHint": True}))
async def arckep_export_print_stl(
    job_id: SrcJobArg = None, object_name: ObjNameArg = None,
    dest_path: Annotated[str | None, Field(description="Optional output .stl or .3mf path.")] = None,
    print_format: Annotated[str, Field(description="Print format: 'stl' or '3mf'.")] = "stl",
    target_size_mm: Annotated[float, Field(description="Target physical size in mm (5.0..2000.0).")] = 100.0,
    scale_axis: Annotated[str, Field(description="Scaling axis: 'max', 'height', 'width', or 'depth'.")] = "max",
    z_up: Annotated[bool, Field(description="Rotate glTF Y-up to slicer Z-up.")] = True,
    place_on_bed: Annotated[bool, Field(description="Place mesh bottom on build plate Z = 0.")] = True,
    import_into_scene: Annotated[bool, Field(description="Also import the exported .stl/.3mf into Blender.")] = False,
) -> str:
    """Download a free 0 RUB slicer-ready binary .stl or .3mf in millimeters for an ArcKep 3D job."""
    def _op() -> dict[str, Any]:
        obj_n, jid = (object_name or "").strip() or None, (int(job_id) if job_id not in (None, 0, False, "") else None)
        if (jid is not None) == (obj_n is not None):
            raise ArcKepValidationError(400, "INVALID_PARAMETER", "Provide exactly one of job_id or object_name.")
        bridge = ArcKepCloudBridge()
        if obj_n is not None:
            if (found := bridge.require_rpc().get_object_info(obj_n).get("arckep_job_id")) in (None, 0, False, ""):
                raise ArcKepValidationError(400, "MISSING_SOURCE_JOB_ID", f"Blender object {obj_n!r} has no 'arckep_job_id'.")
            jid = int(found)
        assert jid is not None
        saved = bridge.download_stl(jid, dest_path, print_format=print_format, target_size_mm=target_size_mm, scale_axis=scale_axis, z_up=z_up, place_on_bed=place_on_bed)
        out: dict[str, Any] = {"ok": True, "job_id": jid, "filepath": str(saved), "print_format": print_format, "size_bytes": saved.stat().st_size, "target_size_mm": float(target_size_mm), "scale_axis": scale_axis, "z_up": z_up, "place_on_bed": place_on_bed, "imported": bool(import_into_scene)}
        if import_into_scene:
            imp = bridge.require_rpc().import_asset(filepath=str(saved), format=print_format, arckep_job_id=jid, arckep_task_type="print")
            out.update(imported_objects=imp.get("imported_objects", []), scene_import=imp)
        return out
    return await _run_tool(_op)


def main() -> None:
    """Run the ArcKep Blender MCP server over stdio (diagnostics strictly on stderr)."""
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, format="[arckep-blender-mcp] %(levelname)s: %(message)s")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
