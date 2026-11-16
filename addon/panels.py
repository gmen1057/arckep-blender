# SPDX-License-Identifier: GPL-3.0-or-later
"""3D Viewport N-Panel ``ArcKep 3D`` with Create, Process, Archive, and MCP tabs."""

from __future__ import annotations

from typing import Any

from . import get_addon_preferences, get_job_queue, get_mcp_bridge
from .budget_guard import BUDGET_MODE_CONFIRM_ALWAYS, BUDGET_MODE_THRESHOLD

try:
    import bpy  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    bpy = None


def _draw_header(layout: Any, prefs: Any, wm_state: Any) -> None:
    status_box = layout.box()
    if not prefs.api_key.strip():
        status_box.label(text="API Key Not Configured", icon="ERROR")
    elif wm_state.connected:
        if wm_state.is_corporate:
            corp_title = wm_state.corporate_name or "Corporate B2B"
            status_box.label(text=f"B2B: {corp_title}", icon="CHECKMARK")
        else:
            status_box.label(text="Personal Account Connected", icon="CHECKMARK")
        status_box.label(text=f"Balance: {wm_state.balance_rub} RUB", icon="FUND")
    else:
        status_box.label(text="Ready (Refresh Balance)", icon="INFO")

    if wm_state.status_message:
        status_box.label(text=wm_state.status_message, icon="INFO")
    if wm_state.last_error:
        status_box.label(text=wm_state.last_error, icon="CANCEL")
    status_box.operator("arckep.refresh_balance", icon="FILE_REFRESH")

    guard_row = status_box.row(align=True)
    if prefs.budget_mode == BUDGET_MODE_THRESHOLD:
        guard_text = f"Guard: <= {round(float(prefs.max_auto_cost_rub), 2):.0f} RUB auto"
    elif prefs.budget_mode == BUDGET_MODE_CONFIRM_ALWAYS:
        guard_text = "Guard: Confirm Every Paid Job"
    else:
        guard_text = "Guard: Unlimited"
    guard_row.label(text=guard_text, icon="LOCKED")
    guard_row.label(text=f"Jobs: {get_job_queue().active_count}", icon="TIME")

    if wm_state.pending_confirm_cost_rub > 0.0:
        cbox = layout.box()
        amt = round(float(wm_state.pending_confirm_cost_rub), 2)
        cbox.label(text=f"Confirm Spend: {amt:.2f} RUB", icon="ERROR")
        op_id = (
            "arckep.process_mesh"
            if wm_state.pending_confirm_target == "process"
            else "arckep.create_model"
        )
        crow = cbox.row(align=True)
        op_props = crow.operator(op_id, text=f"Confirm {amt:.2f} RUB", icon="CHECKMARK")
        op_props.confirmed_max_cost_rub = amt
        crow.operator("arckep.cancel_pending_confirm", text="Cancel", icon="CANCEL")


def _draw_create_tab(layout: Any, cp: Any) -> None:
    box = layout.box()
    box.prop(cp, "model")
    model = str(cp.model)

    if model == "tripo-multiview":
        box.prop(cp, "multiview_op", expand=True)
        if cp.multiview_op == "generate":
            box.prop(cp, "input_mode")
            if cp.input_mode == "viewport":
                box.prop(cp, "viewport_capture_mode")
                box.prop(cp, "viewport_resolution")
            else:
                box.prop(cp, "image_path")
        else:
            box.prop(cp, "source_multiview_job_id")
            box.prop(cp, "edit_front_prompt")
            box.prop(cp, "edit_left_prompt")
            box.prop(cp, "edit_back_prompt")
            box.prop(cp, "edit_right_prompt")
        if cp.last_multiview_info:
            box.label(text=cp.last_multiview_info, icon="IMAGE_DATA")
    else:
        box.prop(cp, "input_mode")
        mode = str(cp.input_mode)
        if mode == "text":
            box.prop(cp, "prompt")
        elif mode == "image":
            box.prop(cp, "image_path")
        elif mode == "viewport":
            box.prop(cp, "viewport_capture_mode")
            box.prop(cp, "viewport_resolution")
        elif mode == "multi_image":
            box.prop(cp, "view_front_path")
            box.prop(cp, "view_left_path")
            box.prop(cp, "view_back_path")
            box.prop(cp, "view_right_path")
            if model == "rodin-v2.5":
                box.prop(cp, "extra_image_5_path")
        elif mode == "multiview_job":
            box.prop(cp, "source_multiview_job_id")

    opts = layout.box()
    opts.label(text="Model Parameters", icon="MESH_CUBE")
    if model == "hunyuan-3d-3.1":
        opts.prop(cp, "hunyuan_generate_type")
        opts.prop(cp, "enable_pbr")
        opts.prop(cp, "use_face_count")
        opts.prop(cp, "face_count" if cp.use_face_count else "hunyuan_quality")
    elif model == "meshy-v7.1":
        if cp.input_mode != "multi_image":
            opts.prop(cp, "meshy_model_type")
        if cp.meshy_model_type != "lowpoly":
            opts.prop(cp, "topology")
            opts.prop(cp, "use_polycount")
            if cp.use_polycount:
                opts.prop(cp, "polycount")
        row = opts.row(align=True)
        row.prop(cp, "texture")
        if cp.texture:
            row.prop(cp, "enable_pbr")
        opts.prop(cp, "enable_rigging")
        if cp.enable_rigging:
            opts.prop(cp, "enable_animation")
            if cp.enable_animation:
                opts.prop(cp, "animation_action_id")
    elif model in ("tripo-p1", "tripo-p2", "tripo-h3.1"):
        if model == "tripo-h3.1":
            opts.prop(cp, "generate_parts")
        if not (model == "tripo-h3.1" and cp.generate_parts):
            row = opts.row(align=True)
            row.prop(cp, "texture")
            if cp.texture:
                row.prop(cp, "enable_pbr")
                opts.prop(cp, "texture_quality")
            if model in ("tripo-p2", "tripo-h3.1"):
                opts.prop(cp, "topology")
            if model == "tripo-h3.1":
                opts.prop(cp, "geometry_quality")
                opts.prop(cp, "smart_low_poly")
                opts.prop(cp, "auto_size")
                if cp.texture:
                    opts.prop(cp, "delight")
                if cp.topology == "quad":
                    opts.prop(cp, "output_format")
        opts.prop(cp, "use_polycount")
        if cp.use_polycount:
            opts.prop(cp, "polycount")
        opts.prop(cp, "negative_prompt")
    elif model == "rodin-v2.5":
        opts.prop(cp, "rodin_variant")
        opts.prop(cp, "rodin_tier")
        opts.prop(cp, "rodin_mesh_option")
        opts.prop(cp, "output_format")
        row = opts.row(align=True)
        row.prop(cp, "enable_pbr")
        row.prop(cp, "tpose")
        if cp.rodin_variant == "standard" and not cp.rodin_mesh_option.endswith("Quad"):
            opts.prop(cp, "high_pack")
        if cp.rodin_tier == "Gen-2.5-Extreme-High":
            opts.prop(cp, "is_micro")
        row2 = opts.row(align=True)
        row2.prop(cp, "hd_texture")
        row2.prop(cp, "texture_delight")
        opts.prop(cp, "enable_creative_mode")
        opts.prop(cp, "use_seed")
        if cp.use_seed:
            opts.prop(cp, "seed")
    elif model == "tripo-splat":
        opts.prop(cp, "use_seed")
        if cp.use_seed:
            opts.prop(cp, "seed")
    elif model == "idol":
        opts.prop(cp, "idol_image_type")
        opts.prop(cp, "idol_num_gaussians")
        opts.prop(cp, "idol_steps")
        opts.prop(cp, "idol_guidance")
        opts.prop(cp, "use_seed")
        if cp.use_seed:
            opts.prop(cp, "seed")

    place = layout.box()
    place.prop(cp, "import_to_cursor")
    place.prop(cp, "scale_target_m")

    act = layout.box()
    if cp.estimated_cost_rub:
        act.label(text=f"Estimated Price: {cp.estimated_cost_rub} RUB", icon="FUND")
    act.operator("arckep.estimate_create", icon="FUND")
    act.operator("arckep.create_model", icon="PLAY")


def _draw_process_tab(layout: Any, context: Any, pp: Any) -> None:
    src = layout.box()
    active_obj = getattr(context, "active_object", None)
    if active_obj is not None:
        jid = active_obj.get("arckep_job_id")
        tag = f" (Job #{jid})" if jid else ""
        src.label(text=f"Active: {active_obj.name}{tag}", icon="MESH_CUBE")
    else:
        src.label(text="No active mesh selected", icon="INFO")
    src.operator("arckep.check_rig", icon="ARMATURE_DATA")
    if pp.rig_check_summary:
        src.label(text=pp.rig_check_summary, icon="CHECKMARK")

    op_box = layout.box()
    op_box.prop(pp, "operation")
    op = str(pp.operation)
    if op in ("complete", "retarget"):
        op_box.prop(pp, "source_job_id")
    else:
        op_box.prop(pp, "use_source_job_id")
        if pp.use_source_job_id:
            op_box.prop(pp, "source_job_id")
        elif not (op == "smartsegment" and pp.seg_type == "image"):
            op_box.prop(pp, "custom_mesh_path")
    op_box.prop(pp, "hide_original")

    cfg = layout.box()
    if op == "texture":
        cfg.prop(pp, "texture_model")
        if pp.texture_model != "meshy-n1-retexture":
            cfg.prop(pp, "texture_prompt")
        cfg.prop(pp, "style_image_path")
        if pp.texture_model in ("meshy-n1-retexture", "tripo"):
            cfg.prop(pp, "style_image_2_path")
        if pp.texture_model == "meshy-v7-retexture":
            cfg.prop(pp, "texture_resolution")
            cfg.prop(pp, "enable_original_uv")
        elif pp.texture_model == "meshy-n1-retexture":
            cfg.prop(pp, "enable_original_uv")
        else:
            cfg.prop(pp, "texture_quality")
            cfg.prop(pp, "bake")
        cfg.prop(pp, "enable_pbr")
    elif op == "decimate":
        cfg.prop(pp, "decimate_model")
        cfg.prop(pp, "face_limit")
        cfg.prop(pp, "quad")
        if pp.decimate_model == "v3.0":
            cfg.prop(pp, "smart_uv")
        cfg.prop(pp, "bake")
    elif op == "uv":
        cfg.prop(pp, "bake")
        cfg.prop(pp, "export_orientation")
        cfg.prop(pp, "compress_geometry")
    elif op == "segment":
        cfg.prop(pp, "segmentation_granularity")
        cfg.prop(pp, "split_by_connectivity")
        cfg.prop(pp, "style_image_path")
    elif op == "smartsegment":
        cfg.prop(pp, "seg_type")
        if pp.seg_type == "image":
            cfg.prop(pp, "style_image_path")
        cfg.prop(pp, "smart_granularity")
        cfg.prop(pp, "segment_hint")
    elif op == "complete":
        cfg.prop(pp, "completion_mode")
        cfg.prop(pp, "part_names_csv")
    elif op in ("rig", "rig_and_retarget"):
        cfg.prop(pp, "rig_model")
        cfg.prop(pp, "rig_type")
        cfg.prop(pp, "rig_spec")
        cfg.prop(pp, "out_format")
        if op == "rig_and_retarget":
            cfg.prop(pp, "animations_csv")
            cfg.prop(pp, "animate_in_place")
            cfg.prop(pp, "export_with_geometry")
    elif op == "retarget":
        cfg.prop(pp, "animations_csv")
        cfg.prop(pp, "out_format")
        cfg.prop(pp, "animate_in_place")
        cfg.prop(pp, "export_with_geometry")
    elif op == "convert":
        cfg.prop(pp, "convert_format")
        if pp.convert_format == "FBX":
            cfg.prop(pp, "fbx_preset")
        cfg.prop(pp, "quad")
        if pp.quad:
            cfg.prop(pp, "force_symmetry")
        cfg.prop(pp, "use_convert_face_limit")
        if pp.use_convert_face_limit:
            cfg.prop(pp, "face_limit")
        cfg.prop(pp, "flatten_bottom")
        cfg.prop(pp, "pack_uv")
        if pp.convert_format in ("OBJ", "GLTF"):
            cfg.prop(pp, "export_vertex_colors")
        cfg.prop(pp, "pivot_to_center_bottom")
        cfg.prop(pp, "scale_factor")
        cfg.prop(pp, "texture_size")

    act = layout.box()
    if pp.estimated_cost_rub:
        act.label(text=f"Estimated Price: {pp.estimated_cost_rub} RUB", icon="FUND")
    act.operator("arckep.estimate_process", icon="FUND")
    act.operator("arckep.process_mesh", icon="MOD_REMESH")


def _draw_archive_tab(layout: Any, arch: Any) -> None:
    hbox = layout.box()
    row = hbox.row(align=True)
    op_refresh = row.operator("arckep.refresh_archive", text="Refresh Archive", icon="FILE_REFRESH")
    op_refresh.load_more = False
    if arch.has_more:
        op_more = row.operator("arckep.refresh_archive", text="Load Older", icon="IMPORT")
        op_more.load_more = True

    if len(arch.items) == 0:
        hbox.label(text="No cached archive entries yet.", icon="INFO")
    else:
        hbox.template_list("ARCKEP_UL_archive_items", "", arch, "items", arch, "active_index", rows=6)
        if 0 <= arch.active_index < len(arch.items) and arch.items[arch.active_index].status == "completed":
            sel = arch.items[arch.active_index]
            imp = hbox.operator("arckep.import_archive_job", text=f"Import Selected (#{sel.job_id})", icon="IMPORT")
            imp.job_id = sel.job_id

    pbox = layout.box()
    pbox.label(text="3D Print Export (0 RUB)", icon="EXPORT")
    pbox.prop(arch, "print_job_id")
    pbox.prop(arch, "print_format")
    pbox.prop(arch, "target_size_mm")
    pbox.prop(arch, "scale_axis")
    row_p = pbox.row(align=True)
    row_p.prop(arch, "z_up")
    row_p.prop(arch, "place_on_bed")
    pbox.prop(arch, "print_filepath")
    pbox.prop(arch, "import_printed_into_scene")
    pbox.operator("arckep.export_print_mesh", icon="EXPORT")


def _draw_mcp_tab(layout: Any, prefs: Any) -> None:
    bridge = get_mcp_bridge()
    mbox = layout.box()
    mbox.label(text="Local Scene Bridge (127.0.0.1 only)", icon="PLUGIN")
    mbox.prop(prefs, "mcp_enabled")
    mbox.prop(prefs, "mcp_port")
    if bridge.is_running:
        mbox.label(text=f"Status: Listening on 127.0.0.1:{bridge.bound_port}", icon="CHECKMARK")
        mbox.operator("arckep.stop_mcp_bridge", text="Stop MCP Bridge", icon="CANCEL")
    else:
        mbox.label(text=f"Status: Stopped (127.0.0.1:{prefs.mcp_port})", icon="LOCKED")
        mbox.operator("arckep.start_mcp_bridge", text="Start MCP Bridge", icon="PLAY")
    mbox.label(text=f"Requests Handled: {bridge.requests_handled}", icon="INFO")
    if bridge.last_command:
        mbox.label(text=f"Last Command: {bridge.last_command}", icon="CHECKMARK")
    if bridge.last_error:
        mbox.label(text=bridge.last_error, icon="ERROR")
    mbox.label(text=f"Shared Budget Policy: {prefs.budget_mode}", icon="FUND")


if bpy is not None:

    class ARCKEP_UL_archive_items(bpy.types.UIList):
        """Scrollable UIList rendering all loaded account archive jobs without truncation."""

        bl_idname = "ARCKEP_UL_archive_items"

        def draw_item(
            self, context: Any, layout: Any, data: Any, item: Any,
            icon: int, active_data: Any, active_propname: str, index: int = 0,
        ) -> None:
            row = layout.row(align=True)
            row.label(text=f"#{item.job_id} {item.model} [{item.status}] {item.cost_rub} RUB")
            if item.status == "completed":
                imp = row.operator("arckep.import_archive_job", text="", icon="IMPORT")
                imp.job_id = item.job_id

    class ARCKEP_PT_main_panel(bpy.types.Panel):
        """Main N-panel in 3D Viewport with Create, Process, Archive, and MCP tabs."""

        bl_idname = "ARCKEP_PT_main_panel"
        bl_label = "ArcKep 3D"
        bl_space_type = "VIEW_3D"
        bl_region_type = "UI"
        bl_category = "ArcKep 3D"

        def draw(self, context: Any) -> None:
            layout = self.layout
            prefs = get_addon_preferences(context)
            wm = context.window_manager
            wm_state = wm.arckep_state

            _draw_header(layout, prefs, wm_state)
            tab_row = layout.row(align=True)
            tab_row.prop(wm_state, "ui_tab", expand=True)

            tab = str(wm_state.ui_tab)
            if tab == "CREATE":
                _draw_create_tab(layout, wm.arckep_create)
            elif tab == "PROCESS":
                _draw_process_tab(layout, context, wm.arckep_process)
            elif tab == "ARCHIVE":
                _draw_archive_tab(layout, wm.arckep_archive)
            elif tab == "MCP":
                _draw_mcp_tab(layout, prefs)

    PANEL_CLASSES = (ARCKEP_UL_archive_items, ARCKEP_PT_main_panel)
else:  # pragma: no cover
    PANEL_CLASSES = ()
