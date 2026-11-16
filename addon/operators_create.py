# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender operators for 3D generation price estimation and cursor-targeted creation."""

from __future__ import annotations

from typing import Any

from . import build_api_client, get_addon_preferences, get_job_queue
from .api_client import ArcKepApiClient, ArcKepApiError, ArcKepValidationError
from .budget_guard import evaluate_prefs_budget, round_rub
from .catalog_defs import (
    RODIN_DEFAULT_TIER,
    RODIN_FAST_TIERS,
    RODIN_STANDARD_TIERS,
    build_generation_payload,
    rodin_allowed_mesh_options,
)
from .job_queue import JobEvent
from .scene_io import (
    capture_viewport_png,
    import_asset_to_scene,
    load_multiview_manifest_to_blender,
)

try:
    import bpy  # type: ignore[import-not-found]
    from bpy.props import FloatProperty  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    bpy = None


def _upload_local_path(client: ArcKepApiClient, raw_path: str, label: str) -> str:
    resolved = bpy.path.abspath(raw_path).strip() if bpy is not None else raw_path.strip()
    if not resolved:
        raise ArcKepValidationError(400, "MISSING_FILE", f"Select a local {label} image file.")
    return str(client.upload_file_cached(resolved)["key"])


def build_create_request_from_ui(context: Any, client: ArcKepApiClient) -> dict[str, Any]:
    """Read ``wm.arckep_create``, upload any local/viewport media (cached), and build payload."""
    cp = context.window_manager.arckep_create
    model = str(cp.model)
    mode = str(cp.input_mode)

    src_kwargs: dict[str, Any] = {}
    if model == "tripo-multiview":
        src_kwargs["multiview_op"] = cp.multiview_op
        if cp.multiview_op == "edit":
            src_kwargs["source_multiview_job_id"] = cp.source_multiview_job_id
            edits = [
                {"view": v, "prompt": txt.strip()}
                for v, txt in (
                    ("front", cp.edit_front_prompt),
                    ("left", cp.edit_left_prompt),
                    ("back", cp.edit_back_prompt),
                    ("right", cp.edit_right_prompt),
                )
                if txt.strip()
            ]
            src_kwargs["edit_view_prompts"] = edits
        elif mode == "viewport":
            cap = capture_viewport_png(
                context, mode=cp.viewport_capture_mode, resolution=cp.viewport_resolution
            )
            src_kwargs["image_url"] = str(client.upload_file_cached(cap["filepath"])["key"])
        else:
            src_kwargs["image_url"] = _upload_local_path(client, cp.image_path, "reference")
    elif mode == "text":
        src_kwargs["prompt"] = cp.prompt
    elif mode == "image":
        src_kwargs["image_url"] = _upload_local_path(client, cp.image_path, "reference")
    elif mode == "viewport":
        cap = capture_viewport_png(
            context, mode=cp.viewport_capture_mode, resolution=cp.viewport_resolution
        )
        src_kwargs["image_url"] = str(client.upload_file_cached(cap["filepath"])["key"])
    elif mode == "multi_image":
        four_slots = [cp.view_front_path, cp.view_left_path, cp.view_back_path, cp.view_right_path]
        if model in ("tripo-p1", "tripo-p2", "tripo-h3.1"):
            if not cp.view_front_path.strip():
                raise ArcKepValidationError(
                    400, "INVALID_PARAMETER", f"{model} multi-image requires Front image slot."
                )
            src_kwargs["image_urls"] = [
                _upload_local_path(client, p, "multi-view") if p.strip() else ""
                for p in four_slots
            ]
        else:
            slots = [*four_slots, cp.extra_image_5_path] if model == "rodin-v2.5" else four_slots
            src_kwargs["image_urls"] = [
                _upload_local_path(client, p, "multi-view") for p in slots if p.strip()
            ]
    elif mode == "multiview_job":
        src_kwargs["source_multiview_job_id"] = cp.source_multiview_job_id

    if model == "hunyuan-3d-3.1":
        return build_generation_payload(
            model,
            **src_kwargs,
            enable_pbr=cp.enable_pbr,
            generate_type=cp.hunyuan_generate_type,
            quality=None if cp.use_face_count else cp.hunyuan_quality,
            face_count=cp.face_count if cp.use_face_count else None,
        )
    if model == "meshy-v7.1":
        mtype = "standard" if mode == "multi_image" else cp.meshy_model_type
        rig = bool(cp.enable_rigging)
        return build_generation_payload(
            model,
            **src_kwargs,
            model_type=mtype,
            texture=cp.texture,
            enable_pbr=cp.enable_pbr if cp.texture else False,
            topology="triangle" if mtype == "lowpoly" else cp.topology,
            polycount=cp.polycount if (cp.use_polycount and mtype != "lowpoly") else None,
            enable_rigging=rig,
            enable_animation=bool(cp.enable_animation) if rig else False,
            animation_action_id=cp.animation_action_id,
        )
    if model in ("tripo-p1", "tripo-p2", "tripo-h3.1"):
        gen_parts = bool(cp.generate_parts) if model == "tripo-h3.1" else False
        topo = "triangle" if (model == "tripo-p1" or gen_parts) else cp.topology
        out_fmt = (
            "fbx"
            if (model == "tripo-h3.1" and topo == "quad" and not gen_parts and cp.output_format == "fbx")
            else "glb"
        )
        return build_generation_payload(
            model,
            **src_kwargs,
            texture=cp.texture,
            enable_pbr=cp.enable_pbr if cp.texture else False,
            texture_quality=cp.texture_quality,
            topology=topo,
            geometry_quality=cp.geometry_quality if model == "tripo-h3.1" else "standard",
            smart_low_poly=cp.smart_low_poly if model == "tripo-h3.1" else False,
            generate_parts=gen_parts,
            auto_size=cp.auto_size if model == "tripo-h3.1" else False,
            delight=cp.delight if (model == "tripo-h3.1" and cp.texture) else False,
            negative_prompt=cp.negative_prompt,
            polycount=cp.polycount if cp.use_polycount else None,
            output_format=out_fmt,
        )
    if model == "rodin-v2.5":
        variant = str(cp.rodin_variant)
        valid_tiers = RODIN_FAST_TIERS if variant == "fast" else RODIN_STANDARD_TIERS
        tier_arg = cp.rodin_tier if cp.rodin_tier in valid_tiers else None
        eff_tier = tier_arg or RODIN_DEFAULT_TIER[variant]
        allowed_meshes = rodin_allowed_mesh_options(variant, eff_tier)
        mesh_opt = cp.rodin_mesh_option if cp.rodin_mesh_option in allowed_meshes else "Auto"
        high_pack = bool(cp.high_pack) if (variant == "standard" and not mesh_opt.endswith("Quad")) else False
        is_micro = bool(cp.is_micro) if eff_tier == "Gen-2.5-Extreme-High" else False
        out_fmt = cp.output_format if cp.output_format in ("glb", "fbx", "obj", "usdz", "stl") else "glb"
        return build_generation_payload(
            model,
            **src_kwargs,
            variant=variant,
            tier=tier_arg,
            quality_mesh_option=mesh_opt,
            high_pack=high_pack,
            is_micro=is_micro,
            output_format=out_fmt,
            enable_pbr=cp.enable_pbr,
            tpose=cp.tpose,
            hd_texture=cp.hd_texture,
            texture_delight=cp.texture_delight,
            enable_creative_mode=cp.enable_creative_mode,
            seed=cp.seed if cp.use_seed else None,
        )
    if model == "tripo-splat":
        return build_generation_payload(
            model, **src_kwargs, output_format="glb", model_seed=cp.seed if cp.use_seed else None
        )
    if model == "idol":
        return build_generation_payload(
            model,
            **src_kwargs,
            output_format="glb",
            image_type=cp.idol_image_type,
            num_gaussians=cp.idol_num_gaussians,
            num_inference_steps=cp.idol_steps,
            guidance_scale=cp.idol_guidance,
            seed=cp.seed if cp.use_seed else None,
        )
    return build_generation_payload(model, **src_kwargs)


def _make_create_callbacks(import_to_cursor: bool, scale_target_m: float, gen_parts: bool):
    def _on_completed(event: JobEvent) -> None:
        wm = bpy.context.window_manager
        wm_state = wm.arckep_state
        output = event.job.get("output") or {}
        try:
            if event.local_path and (
                event.local_path.endswith("multiview_manifest.json")
                or output.get("format") == "multiview"
            ):
                mv = load_multiview_manifest_to_blender(event.local_path)
                wm.arckep_create.source_multiview_job_id = event.job_id
                wm.arckep_create.last_multiview_info = f"Job #{event.job_id}: {', '.join(mv['images'].keys())}"
                wm_state.status_message = f"Multiview #{event.job_id} ready (4 views loaded)"
                wm_state.last_error = ""
                return
            if event.local_path:
                is_parts = gen_parts or bool(output.get("can_complete_parts"))
                task_type = "parts" if is_parts else str(output.get("tripo_task_type") or "generation")
                extra_tags = {
                    "arckep_can_retarget": output.get("can_retarget"),
                    "arckep_can_complete": output.get("can_complete_parts"),
                    "arckep_rig_type": output.get("rig_type"),
                    "arckep_rig_model": output.get("rig_model"),
                }
                import_asset_to_scene(
                    bpy.context,
                    event.local_path,
                    location="cursor" if import_to_cursor else (0.0, 0.0, 0.0),
                    scale_target_m=scale_target_m if scale_target_m > 0.0 else None,
                    arckep_job_id=event.job_id,
                    arckep_task_type=task_type,
                    arckep_model=str(event.job.get("model") or ""),
                    extra_tags=extra_tags,
                )
                wm_state.status_message = f"Imported job #{event.job_id}"
                wm_state.last_error = ""
        except Exception as exc:
            wm_state.last_error = f"Import failed for #{event.job_id}: {exc}"

    def _on_failed(event: JobEvent) -> None:
        bpy.context.window_manager.arckep_state.last_error = (
            f"Job #{event.job_id} failed: {event.error or 'Unknown error'}"
        )

    return _on_completed, _on_failed


if bpy is not None:

    class ARCKEP_OT_cancel_pending_confirm(bpy.types.Operator):
        """Dismiss the pending RUB confirmation banner without starting a paid job."""

        bl_idname = "arckep.cancel_pending_confirm"
        bl_label = "Cancel"
        bl_description = "Dismiss pending spend confirmation"

        def execute(self, context: Any) -> set[str]:
            wm_state = context.window_manager.arckep_state
            wm_state.pending_confirm_cost_rub = 0.0
            wm_state.pending_confirm_target = ""
            wm_state.last_error = ""
            return {"FINISHED"}

    class ARCKEP_OT_estimate_create(bpy.types.Operator):
        """Estimate exact RUB price for the configured 3D generation request."""

        bl_idname = "arckep.estimate_create"
        bl_label = "Estimate Price (RUB)"
        bl_description = "Quote exact RUB price via /api/v1/models3d/estimate without charging"

        def execute(self, context: Any) -> set[str]:
            wm_state = context.window_manager.arckep_state
            cp = context.window_manager.arckep_create
            try:
                client = build_api_client(context)
                payload = build_create_request_from_ui(context, client)
                quote = client.estimate_generation(payload)
            except ArcKepApiError as exc:
                wm_state.last_error = exc.message
                self.report({"ERROR"}, exc.message)
                return {"CANCELLED"}
            cost_str = str(quote.get("cost_rub") or "0.00")
            cp.estimated_cost_rub = cost_str
            wm_state.last_error = ""
            wm_state.status_message = f"Estimated {cp.model}: {cost_str} RUB"
            self.report({"INFO"}, wm_state.status_message)
            return {"FINISHED"}

    class ARCKEP_OT_create_model(bpy.types.Operator):
        """Generate a 3D asset via ArcKep API with Budget Guard enforcement."""

        bl_idname = "arckep.create_model"
        bl_label = "Generate on 3D Cursor"
        bl_description = "Estimate, enforce RUB Budget Guard, and submit background 3D generation"

        confirmed_max_cost_rub: FloatProperty(  # type: ignore[valid-type]
            name="Confirmed Max Cost (RUB)", default=-1.0, precision=2, options={"SKIP_SAVE"}
        )

        def draw(self, context: Any) -> None:
            cp = context.window_manager.arckep_create
            layout = self.layout
            layout.label(text=f"Estimated price: {cp.estimated_cost_rub} RUB", icon="FUND")
            layout.label(text="Confirm RUB spend to start 3D generation:")
            layout.prop(self, "confirmed_max_cost_rub")

        def _prepare_quote(self, context: Any) -> tuple[ArcKepApiClient, dict[str, Any], str] | None:
            cached = getattr(self, "_cached_quote", None)
            if cached is not None:
                self._cached_quote = None
                return cached
            wm_state = context.window_manager.arckep_state
            cp = context.window_manager.arckep_create
            try:
                client = build_api_client(context)
                payload = build_create_request_from_ui(context, client)
                quote = client.estimate_generation(payload)
            except ArcKepApiError as exc:
                wm_state.last_error = exc.message
                self.report({"ERROR"}, exc.message)
                return None
            cost_str = str(quote.get("cost_rub") or "0.00")
            cp.estimated_cost_rub = cost_str
            return client, payload, cost_str

        def invoke(self, context: Any, event: Any) -> set[str]:
            if bpy.app.background:
                return self.execute(context)
            prepared = self._prepare_quote(context)
            if prepared is None:
                return {"CANCELLED"}
            client, payload, cost_str = prepared
            decision = evaluate_prefs_budget(
                cost_str, get_addon_preferences(context), self.confirmed_max_cost_rub
            )
            if decision.requires_confirmation:
                wm_state = context.window_manager.arckep_state
                cost_val = round_rub(cost_str)
                self._cached_quote = (client, payload, cost_str)
                self.confirmed_max_cost_rub = cost_val
                wm_state.pending_confirm_cost_rub = cost_val
                wm_state.pending_confirm_target = "create"
                return context.window_manager.invoke_props_dialog(self, width=380)
            return self._submit_job(context, client, payload, decision.effective_max_cost_rub)

        def execute(self, context: Any) -> set[str]:
            prepared = self._prepare_quote(context)
            if prepared is None:
                return {"CANCELLED"}
            client, payload, cost_str = prepared
            decision = evaluate_prefs_budget(
                cost_str, get_addon_preferences(context), self.confirmed_max_cost_rub
            )
            if not decision.allowed:
                wm_state = context.window_manager.arckep_state
                wm_state.pending_confirm_cost_rub = round_rub(cost_str)
                wm_state.pending_confirm_target = "create"
                wm_state.last_error = decision.message
                self.report({"WARNING"}, decision.message)
                return {"CANCELLED"}
            return self._submit_job(context, client, payload, decision.effective_max_cost_rub)

        def _submit_job(
            self, context: Any, client: ArcKepApiClient, payload: dict[str, Any], max_cost: float | None
        ) -> set[str]:
            wm_state = context.window_manager.arckep_state
            cp = context.window_manager.arckep_create
            gen_body = {**payload, **({"max_cost_rub": round_rub(max_cost)} if max_cost is not None else {})}
            try:
                job = client.create_generation(gen_body)
            except ArcKepApiError as exc:
                wm_state.last_error = exc.message
                self.report({"ERROR"}, exc.message)
                return {"CANCELLED"}
            job_id = int(job["id"])
            wm_state.last_job_id = job_id
            wm_state.pending_confirm_cost_rub = 0.0
            wm_state.pending_confirm_target = ""
            wm_state.last_error = ""
            wm_state.status_message = f"Queued job #{job_id} ({cp.model})"
            on_done, on_fail = _make_create_callbacks(
                bool(cp.import_to_cursor),
                float(cp.scale_target_m),
                bool(cp.model == "tripo-h3.1" and cp.generate_parts),
            )
            get_job_queue().submit(client, job_id, on_completed=on_done, on_failed=on_fail)
            self.report({"INFO"}, wm_state.status_message)
            return {"FINISHED"}

    CREATE_OPERATOR_CLASSES = (
        ARCKEP_OT_cancel_pending_confirm,
        ARCKEP_OT_estimate_create,
        ARCKEP_OT_create_model,
    )
else:  # pragma: no cover
    CREATE_OPERATOR_CLASSES = ()
