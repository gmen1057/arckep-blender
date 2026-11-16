# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender operators for rig-check, price estimation, and 1-click mesh post-processing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import build_api_client, get_addon_preferences, get_job_queue
from .api_client import (
    MAX_MESH_UPLOAD_BYTES,
    ArcKepApiClient,
    ArcKepApiError,
    ArcKepValidationError,
)
from .budget_guard import evaluate_prefs_budget, round_rub
from .catalog_defs import build_process_payload
from .job_queue import JobEvent
from .scene_io import (
    MESHY_RETEXTURE_MAX_BYTES,
    export_selection_glb,
    import_asset_to_scene,
)

try:
    import bpy  # type: ignore[import-not-found]
    from bpy.props import FloatProperty  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    bpy = None


def _selected_arckep_job_id(context: Any) -> int | None:
    active = getattr(context, "active_object", None)
    if active is not None and active.get("arckep_job_id") is not None:
        return int(active["arckep_job_id"])
    for obj in getattr(context, "selected_objects", ()):
        if obj.get("arckep_job_id") is not None:
            return int(obj["arckep_job_id"])
    return None


def _upload_mesh_source(
    context: Any,
    client: ArcKepApiClient,
    custom_path: str,
    max_bytes: int,
    *,
    disallow_gltf_zip: bool = False,
    require_glb: bool = False,
) -> str:
    resolved_custom = (
        bpy.path.abspath(custom_path).strip() if (bpy is not None and custom_path) else custom_path.strip()
    )
    if resolved_custom:
        p = Path(resolved_custom)
        if not p.is_file():
            raise ArcKepValidationError(400, "FILE_NOT_FOUND", f"Mesh file not found: {p}")
        ext = p.suffix.lower()
        if require_glb and ext != ".glb":
            raise ArcKepValidationError(
                400, "INVALID_PARAMETER", f"This operation requires a binary .glb mesh, got {p.name!r}."
            )
        if disallow_gltf_zip and ext in (".gltf", ".zip"):
            raise ArcKepValidationError(
                400, "INVALID_PARAMETER", f"Format {ext!r} is forbidden for rigging; use binary .glb."
            )
        size = p.stat().st_size
        if size > max_bytes:
            raise ArcKepValidationError(
                413,
                "FILE_TOO_LARGE",
                f"Mesh '{p.name}' is {size / (1024 * 1024):.1f} MiB (limit: {max_bytes / (1024 * 1024):.0f} MiB).",
                {"max_bytes": max_bytes, "size_bytes": size},
            )
        return str(client.upload_file_cached(p)["key"])
    exported = export_selection_glb(context, max_bytes=max_bytes)
    return str(client.upload_file_cached(exported["filepath"])["key"])


def _upload_style_image(client: ArcKepApiClient, raw_path: str) -> str:
    resolved = bpy.path.abspath(raw_path).strip() if bpy is not None else raw_path.strip()
    if not resolved:
        raise ArcKepValidationError(400, "MISSING_FILE", "Select a style/reference image file.")
    return str(client.upload_file_cached(resolved)["key"])


def build_process_request_from_ui(
    context: Any, client: ArcKepApiClient
) -> tuple[dict[str, Any], str | None]:
    """Read ``wm.arckep_process``, export/upload scene mesh if needed, and build process payload."""
    pp = context.window_manager.arckep_process
    op = str(pp.operation)
    active = getattr(context, "active_object", None)
    replace_name = (
        active.name if (pp.hide_original and active is not None and active.type == "MESH") else None
    )

    src_kwargs: dict[str, Any] = {}
    if op == "smartsegment" and pp.seg_type == "image":
        src_kwargs["seg_type"] = "image"
        src_kwargs["image_url"] = _upload_style_image(client, pp.style_image_path)
    elif op in ("complete", "retarget") or pp.use_source_job_id:
        jid = int(pp.source_job_id) if pp.source_job_id > 0 else _selected_arckep_job_id(context)
        if not jid:
            raise ArcKepValidationError(
                400,
                "MISSING_SOURCE_JOB_ID",
                f"Operation {op!r} requires an ArcKep object (arckep_job_id) or explicit Source Job ID.",
            )
        src_kwargs["source_job_id"] = jid
    else:
        is_meshy_retex = op == "texture" and pp.texture_model in (
            "meshy-v7-retexture",
            "meshy-n1-retexture",
        )
        cap = MESHY_RETEXTURE_MAX_BYTES if is_meshy_retex else MAX_MESH_UPLOAD_BYTES
        src_kwargs["model_url"] = _upload_mesh_source(
            context,
            client,
            pp.custom_mesh_path,
            cap,
            disallow_gltf_zip=op in ("rig", "rig_and_retarget"),
            require_glb=(op == "smartsegment" and pp.seg_type == "model"),
        )

    if op == "texture":
        tmodel = str(pp.texture_model)
        if tmodel == "meshy-v7-retexture":
            if pp.style_image_path.strip():
                src_kwargs["image_url"] = _upload_style_image(client, pp.style_image_path)
        elif tmodel == "meshy-n1-retexture":
            imgs = [
                _upload_style_image(client, p)
                for p in (pp.style_image_path, pp.style_image_2_path)
                if p.strip()
            ]
            if len(imgs) == 1:
                src_kwargs["image_url"] = imgs[0]
            elif len(imgs) > 1:
                src_kwargs["image_urls"] = imgs
        else:  # tripo
            img1 = _upload_style_image(client, pp.style_image_path) if pp.style_image_path.strip() else ""
            img2 = _upload_style_image(client, pp.style_image_2_path) if pp.style_image_2_path.strip() else ""
            if img2:
                src_kwargs["image_urls"] = [img1, img2]
            elif img1:
                src_kwargs["image_url"] = img1
    elif op == "segment" and pp.style_image_path.strip():
        src_kwargs["image_url"] = _upload_style_image(client, pp.style_image_path)
    elif op == "smartsegment":
        src_kwargs.setdefault("seg_type", pp.seg_type)

    anims = [a.strip() for a in pp.animations_csv.split(",") if a.strip()]
    parts = [p.strip() for p in pp.part_names_csv.split(",") if p.strip()]
    face_limit = (
        pp.face_limit
        if (op == "decimate" or (op == "convert" and pp.use_convert_face_limit))
        else None
    )
    payload = build_process_payload(
        op, **src_kwargs,
        texture_model=pp.texture_model, texture_prompt=pp.texture_prompt,
        texture_resolution=pp.texture_resolution, texture_quality=pp.texture_quality,
        enable_pbr=pp.enable_pbr, enable_original_uv=pp.enable_original_uv, bake=pp.bake,
        decimate_model=pp.decimate_model, face_limit=face_limit, quad=pp.quad,
        smart_uv=pp.smart_uv if pp.decimate_model == "v3.0" else False,
        export_orientation=pp.export_orientation, compress_geometry=pp.compress_geometry,
        segmentation_granularity=pp.segmentation_granularity,
        split_by_connectivity=pp.split_by_connectivity,
        smart_granularity=pp.smart_granularity, hint=pp.segment_hint,
        completion_mode=pp.completion_mode, part_names=parts,
        rig_model=pp.rig_model, rig_type=pp.rig_type, rig_spec=pp.rig_spec, out_format=pp.out_format,
        animations=anims, animate_in_place=pp.animate_in_place,
        export_with_geometry=pp.export_with_geometry,
        convert_format=pp.convert_format, fbx_preset=pp.fbx_preset,
        force_symmetry=bool(pp.force_symmetry) if pp.quad else False,
        flatten_bottom=pp.flatten_bottom, pack_uv=pp.pack_uv,
        export_vertex_colors=bool(pp.export_vertex_colors) if pp.convert_format in ("OBJ", "GLTF") else False,
        pivot_to_center_bottom=pp.pivot_to_center_bottom,
        scale_factor=pp.scale_factor, texture_size=pp.texture_size,
    )
    return payload, replace_name


def _make_process_callbacks(operation: str, replace_object: str | None):
    def _on_completed(event: JobEvent) -> None:
        wm_state = bpy.context.window_manager.arckep_state
        output = event.job.get("output") or {}
        if not event.local_path:
            wm_state.status_message = f"Process #{event.job_id} completed"
            return
        try:
            extra_tags = {
                "arckep_can_retarget": output.get("can_retarget"),
                "arckep_can_complete": output.get("can_complete_parts"),
                "arckep_rig_type": output.get("rig_type"),
                "arckep_rig_model": output.get("rig_model"),
            }
            import_asset_to_scene(
                bpy.context,
                event.local_path,
                location="cursor",
                arckep_job_id=event.job_id,
                arckep_task_type=operation,
                arckep_model=str(event.job.get("model") or f"process-{operation}"),
                extra_tags=extra_tags,
                replace_object=replace_object,
            )
            wm_state.status_message = f"Imported {operation} job #{event.job_id}"
            wm_state.last_error = ""
        except Exception as exc:
            wm_state.last_error = f"Process import failed for #{event.job_id}: {exc}"

    def _on_failed(event: JobEvent) -> None:
        bpy.context.window_manager.arckep_state.last_error = (
            f"Process #{event.job_id} failed: {event.error or 'Unknown error'}"
        )

    return _on_completed, _on_failed


if bpy is not None:

    class ARCKEP_OT_check_rig(bpy.types.Operator):
        """Run free 0 RUB rig compatibility check on the selected mesh or job."""

        bl_idname = "arckep.check_rig"
        bl_label = "Check Rig Compatibility (0 RUB)"
        bl_description = "Inspect skeleton compatibility via free /api/v1/models3d/rig-check"

        def execute(self, context: Any) -> set[str]:
            wm_state = context.window_manager.arckep_state
            pp = context.window_manager.arckep_process
            try:
                client = build_api_client(context)
                if pp.use_source_job_id:
                    jid = int(pp.source_job_id) if pp.source_job_id > 0 else _selected_arckep_job_id(context)
                    if not jid:
                        raise ArcKepValidationError(400, "MISSING_SOURCE_JOB_ID", "No arckep_job_id found.")
                    res = client.check_rig(source_job_id=jid)
                else:
                    key = _upload_mesh_source(
                        context,
                        client,
                        pp.custom_mesh_path,
                        MAX_MESH_UPLOAD_BYTES,
                        disallow_gltf_zip=True,
                    )
                    res = client.check_rig(model_url=key)
            except ArcKepApiError as exc:
                wm_state.last_error = exc.message
                self.report({"ERROR"}, exc.message)
                return {"CANCELLED"}
            riggable = bool(res.get("riggable", False))
            rtype = str(res.get("rig_type") or "biped")
            rmodel = str(res.get("recommended_rig_model") or "v3.0-20260909")
            pp.rig_check_summary = f"{'Riggable' if riggable else 'Not riggable'}: {rtype} ({rmodel})"
            if rtype in ("biped", "quadruped", "hexapod", "octopod", "avian", "serpentine", "aquatic", "others"):
                pp.rig_type = rtype
            if rmodel in ("v3.0-20260909", "v2.5-20260210", "v1.0-20240301"):
                pp.rig_model = rmodel
            wm_state.last_error = ""
            wm_state.status_message = pp.rig_check_summary
            self.report({"INFO"}, pp.rig_check_summary)
            return {"FINISHED"}

    class ARCKEP_OT_estimate_process(bpy.types.Operator):
        """Estimate exact RUB price for the configured mesh post-processing operation."""

        bl_idname = "arckep.estimate_process"
        bl_label = "Estimate Price (RUB)"
        bl_description = "Quote exact RUB price via /api/v1/models3d/process/estimate"

        def execute(self, context: Any) -> set[str]:
            wm_state = context.window_manager.arckep_state
            pp = context.window_manager.arckep_process
            try:
                client = build_api_client(context)
                payload, _ = build_process_request_from_ui(context, client)
                quote = client.estimate_process(payload)
            except ArcKepApiError as exc:
                wm_state.last_error = exc.message
                self.report({"ERROR"}, exc.message)
                return {"CANCELLED"}
            cost_str = str(quote.get("cost_rub") or "0.00")
            pp.estimated_cost_rub = cost_str
            wm_state.last_error = ""
            wm_state.status_message = f"Estimated {pp.operation}: {cost_str} RUB"
            self.report({"INFO"}, wm_state.status_message)
            return {"FINISHED"}

    class ARCKEP_OT_process_mesh(bpy.types.Operator):
        """Execute paid 3D mesh post-processing with Budget Guard enforcement."""

        bl_idname = "arckep.process_mesh"
        bl_label = "Run Process"
        bl_description = "Estimate, enforce RUB Budget Guard, and submit mesh post-processing"

        confirmed_max_cost_rub: FloatProperty(  # type: ignore[valid-type]
            name="Confirmed Max Cost (RUB)", default=-1.0, precision=2, options={"SKIP_SAVE"}
        )

        def draw(self, context: Any) -> None:
            pp = context.window_manager.arckep_process
            layout = self.layout
            layout.label(text=f"Estimated price: {pp.estimated_cost_rub} RUB", icon="FUND")
            layout.label(text=f"Confirm RUB spend for '{pp.operation}':")
            layout.prop(self, "confirmed_max_cost_rub")

        def _prepare_quote(
            self, context: Any
        ) -> tuple[ArcKepApiClient, dict[str, Any], str | None, str] | None:
            cached = getattr(self, "_cached_quote", None)
            if cached is not None:
                self._cached_quote = None
                return cached
            wm_state = context.window_manager.arckep_state
            pp = context.window_manager.arckep_process
            try:
                client = build_api_client(context)
                payload, replace_name = build_process_request_from_ui(context, client)
                quote = client.estimate_process(payload)
            except ArcKepApiError as exc:
                wm_state.last_error = exc.message
                self.report({"ERROR"}, exc.message)
                return None
            cost_str = str(quote.get("cost_rub") or "0.00")
            pp.estimated_cost_rub = cost_str
            return client, payload, replace_name, cost_str

        def invoke(self, context: Any, event: Any) -> set[str]:
            if bpy.app.background:
                return self.execute(context)
            prepared = self._prepare_quote(context)
            if prepared is None:
                return {"CANCELLED"}
            client, payload, replace_name, cost_str = prepared
            decision = evaluate_prefs_budget(
                cost_str, get_addon_preferences(context), self.confirmed_max_cost_rub
            )
            if decision.requires_confirmation:
                wm_state = context.window_manager.arckep_state
                cost_val = round_rub(cost_str)
                self._cached_quote = (client, payload, replace_name, cost_str)
                self.confirmed_max_cost_rub = cost_val
                wm_state.pending_confirm_cost_rub = cost_val
                wm_state.pending_confirm_target = "process"
                return context.window_manager.invoke_props_dialog(self, width=380)
            return self._submit_job(context, client, payload, replace_name, decision.effective_max_cost_rub)

        def execute(self, context: Any) -> set[str]:
            prepared = self._prepare_quote(context)
            if prepared is None:
                return {"CANCELLED"}
            client, payload, replace_name, cost_str = prepared
            decision = evaluate_prefs_budget(
                cost_str, get_addon_preferences(context), self.confirmed_max_cost_rub
            )
            if not decision.allowed:
                wm_state = context.window_manager.arckep_state
                wm_state.pending_confirm_cost_rub = round_rub(cost_str)
                wm_state.pending_confirm_target = "process"
                wm_state.last_error = decision.message
                self.report({"WARNING"}, decision.message)
                return {"CANCELLED"}
            return self._submit_job(context, client, payload, replace_name, decision.effective_max_cost_rub)

        def _submit_job(
            self, context: Any, client: ArcKepApiClient, payload: dict[str, Any],
            replace_name: str | None, max_cost: float | None,
        ) -> set[str]:
            wm_state = context.window_manager.arckep_state
            pp = context.window_manager.arckep_process
            proc_body = {**payload, **({"max_cost_rub": round_rub(max_cost)} if max_cost is not None else {})}
            try:
                job = client.create_process(proc_body)
            except ArcKepApiError as exc:
                wm_state.last_error = exc.message
                self.report({"ERROR"}, exc.message)
                return {"CANCELLED"}
            job_id = int(job["id"])
            wm_state.last_job_id = job_id
            wm_state.pending_confirm_cost_rub = 0.0
            wm_state.pending_confirm_target = ""
            wm_state.last_error = ""
            wm_state.status_message = f"Queued {pp.operation} job #{job_id}"
            on_done, on_fail = _make_process_callbacks(str(pp.operation), replace_name)
            get_job_queue().submit(client, job_id, on_completed=on_done, on_failed=on_fail)
            self.report({"INFO"}, wm_state.status_message)
            return {"FINISHED"}

    PROCESS_OPERATOR_CLASSES = (
        ARCKEP_OT_check_rig,
        ARCKEP_OT_estimate_process,
        ARCKEP_OT_process_mesh,
    )
else:  # pragma: no cover
    PROCESS_OPERATOR_CLASSES = ()
