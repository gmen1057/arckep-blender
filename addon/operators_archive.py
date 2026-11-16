# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender operators for account 3D archive browsing, 1-click import, and 0 RUB STL/3MF export."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from . import build_api_client, get_job_queue
from .api_client import ArcKepApiError, ArcKepValidationError
from .job_queue import STATUS_COMPLETED, download_completed_job_asset
from .scene_io import import_asset_to_scene, load_multiview_manifest_to_blender

try:
    import bpy  # type: ignore[import-not-found]
    from bpy.props import (  # type: ignore[import-not-found]
        BoolProperty,
        FloatProperty,
        IntProperty,
        StringProperty,
    )
except ImportError:  # pragma: no cover
    bpy = None

_SKIP = {"SKIP_SAVE"}

if bpy is not None:

    class ARCKEP_OT_refresh_archive(bpy.types.Operator):
        """Fetch recent account or studio 3D generations from GET /models3d/generations."""

        bl_idname = "arckep.refresh_archive"
        bl_label = "Refresh History"
        bl_description = "Load account 3D generation archive with cursor pagination"

        load_more: BoolProperty(default=False, options=_SKIP)  # type: ignore[valid-type]

        def execute(self, context: Any) -> set[str]:
            wm = context.window_manager
            wm_state = wm.arckep_state
            arch = wm.arckep_archive
            before_id = (
                int(arch.next_before_id)
                if (self.load_more and arch.has_more and arch.next_before_id > 0)
                else None
            )
            try:
                client = build_api_client(context)
                resp = client.list_generations(limit=arch.page_limit, before_id=before_id)
            except ArcKepApiError as exc:
                wm_state.last_error = exc.message
                self.report({"ERROR"}, exc.message)
                return {"CANCELLED"}

            if not self.load_more:
                arch.items.clear()
                arch.active_index = 0
            rows = resp.get("data") or []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                item = arch.items.add()
                item.job_id = int(row.get("id") or 0)
                item.model = str(row.get("model") or "")
                item.status = str(row.get("status") or "")
                item.cost_rub = str(row.get("cost_rub") or "0.00")
                out = row.get("output")
                item.asset_format = str(out.get("format") or "") if isinstance(out, dict) else ""
                item.prompt = str(row.get("prompt") or "")
                item.created_at = str(row.get("created_at") or "")

            arch.has_more = bool(resp.get("has_more", False))
            arch.next_before_id = arch.items[-1].job_id if len(arch.items) > 0 else 0
            wm_state.last_error = ""
            wm_state.status_message = f"Loaded {len(arch.items)} archive job(s)"
            self.report({"INFO"}, wm_state.status_message)
            return {"FINISHED"}

    class ARCKEP_OT_import_archive_job(bpy.types.Operator):
        """Download and import a completed account generation onto the 3D cursor."""

        bl_idname = "arckep.import_archive_job"
        bl_label = "Import to Scene"
        bl_description = "Download completed asset (without Bearer leak) and import at 3D cursor"

        job_id: IntProperty(default=0, min=0, options=_SKIP)  # type: ignore[valid-type]

        def execute(self, context: Any) -> set[str]:
            wm = context.window_manager
            wm_state = wm.arckep_state
            arch = wm.arckep_archive
            target_id = int(self.job_id)
            if target_id <= 0 and 0 <= arch.active_index < len(arch.items):
                target_id = int(arch.items[arch.active_index].job_id)
            if target_id <= 0:
                wm_state.last_error = "Select an archive job to import."
                self.report({"ERROR"}, wm_state.last_error)
                return {"CANCELLED"}

            try:
                client = build_api_client(context)
                job = client.get_generation(target_id)
                if job.get("status") != STATUS_COMPLETED:
                    raise ArcKepValidationError(
                        409, "JOB_NOT_COMPLETED", f"Job #{target_id} status is {job.get('status')!r}."
                    )
                dl_dir = get_job_queue().ensure_download_dir()
                local_path = download_completed_job_asset(client, job, dl_dir)
                if not local_path:
                    raise ArcKepValidationError(404, "NO_ASSET", f"Job #{target_id} has no downloadable file.")
                output = job.get("output") or {}
                if local_path.endswith("multiview_manifest.json") or output.get("format") == "multiview":
                    mv = load_multiview_manifest_to_blender(local_path)
                    wm.arckep_create.source_multiview_job_id = target_id
                    wm.arckep_create.last_multiview_info = (
                        f"Job #{target_id}: {', '.join(mv['images'].keys())}"
                    )
                else:
                    task_type = (
                        "parts"
                        if output.get("can_complete_parts")
                        else str(output.get("tripo_task_type") or "archive")
                    )
                    extra_tags = {
                        "arckep_can_retarget": output.get("can_retarget"),
                        "arckep_can_complete": output.get("can_complete_parts"),
                        "arckep_rig_type": output.get("rig_type"),
                        "arckep_rig_model": output.get("rig_model"),
                    }
                    import_asset_to_scene(
                        context,
                        local_path,
                        location="cursor",
                        arckep_job_id=target_id,
                        arckep_task_type=task_type,
                        arckep_model=str(job.get("model") or ""),
                        extra_tags=extra_tags,
                    )
            except ArcKepApiError as exc:
                wm_state.last_error = exc.message
                self.report({"ERROR"}, exc.message)
                return {"CANCELLED"}

            wm_state.last_job_id = target_id
            wm_state.last_error = ""
            wm_state.status_message = f"Imported archive job #{target_id}"
            self.report({"INFO"}, wm_state.status_message)
            return {"FINISHED"}

    class ARCKEP_OT_export_print_mesh(bpy.types.Operator):
        """Download free 0 RUB print-ready binary STL or 3MF in millimeters."""

        bl_idname = "arckep.export_print_mesh"
        bl_label = "Export Print Mesh (0 RUB)"
        bl_description = "Export print-ready STL or 3MF in millimeters with Z-up and bed placement"

        job_id: IntProperty(default=0, min=0, options=_SKIP)  # type: ignore[valid-type]
        print_format: StringProperty(default="", options=_SKIP)  # type: ignore[valid-type]
        target_size_mm: FloatProperty(default=0.0, min=0.0, max=2000.0, options=_SKIP)  # type: ignore[valid-type]
        scale_axis: StringProperty(default="", options=_SKIP)  # type: ignore[valid-type]
        z_up: BoolProperty(default=True, options=_SKIP)  # type: ignore[valid-type]
        place_on_bed: BoolProperty(default=True, options=_SKIP)  # type: ignore[valid-type]
        filepath: StringProperty(subtype="FILE_PATH", default="", options=_SKIP)  # type: ignore[valid-type]
        import_into_scene: BoolProperty(default=False, options=_SKIP)  # type: ignore[valid-type]

        def execute(self, context: Any) -> set[str]:
            wm = context.window_manager
            wm_state = wm.arckep_state
            arch = wm.arckep_archive
            props = self.properties

            active_obj = getattr(context, "active_object", None)
            resolved_id = int(self.job_id) if self.job_id > 0 else 0
            if resolved_id <= 0 and active_obj is not None and active_obj.get("arckep_job_id"):
                resolved_id = int(active_obj["arckep_job_id"])
            if resolved_id <= 0 and arch.print_job_id > 0:
                resolved_id = int(arch.print_job_id)
            if resolved_id <= 0 and 0 <= arch.active_index < len(arch.items):
                resolved_id = int(arch.items[arch.active_index].job_id)
            if resolved_id <= 0:
                wm_state.last_error = "Specify Job ID or select an ArcKep object for STL/3MF export."
                self.report({"ERROR"}, wm_state.last_error)
                return {"CANCELLED"}

            fmt = (self.print_format or arch.print_format or "stl").strip().lower()
            size_mm = (
                float(self.target_size_mm)
                if (props.is_property_set("target_size_mm") and self.target_size_mm > 0.0)
                else float(arch.target_size_mm)
            )
            axis = (self.scale_axis or arch.scale_axis or "max").strip().lower()
            z_up = bool(self.z_up) if props.is_property_set("z_up") else bool(arch.z_up)
            bed = (
                bool(self.place_on_bed)
                if props.is_property_set("place_on_bed")
                else bool(arch.place_on_bed)
            )
            do_import = (
                bool(self.import_into_scene)
                if props.is_property_set("import_into_scene")
                else bool(arch.import_printed_into_scene)
            )
            raw_dest = self.filepath.strip() or arch.print_filepath.strip()

            try:
                if fmt not in ("stl", "3mf"):
                    raise ArcKepValidationError(400, "INVALID_PARAMETER", f"Invalid print_format {fmt!r}.")
                if not (5.0 <= size_mm <= 2000.0):
                    raise ArcKepValidationError(
                        400, "INVALID_PARAMETER", "target_size_mm must be between 5 and 2000 mm."
                    )
                if axis not in ("max", "height", "width", "depth"):
                    raise ArcKepValidationError(400, "INVALID_PARAMETER", f"Invalid scale_axis {axis!r}.")
                if raw_dest:
                    dest = Path(bpy.path.abspath(raw_dest))
                    if dest.suffix.lower() != f".{fmt}":
                        dest = dest.with_suffix(f".{fmt}")
                else:
                    out_dir = Path(tempfile.gettempdir()) / "arckep_3d_print"
                    out_dir.mkdir(parents=True, exist_ok=True)
                    dest = out_dir / f"model-{resolved_id}-{round(size_mm)}mm.{fmt}"

                client = build_api_client(context)
                saved = client.download_stl(
                    resolved_id,
                    dest,
                    print_format=fmt,
                    target_size_mm=size_mm,
                    scale_axis=axis,
                    z_up=z_up,
                    place_on_bed=bed,
                )
                if do_import:
                    import_asset_to_scene(
                        context,
                        saved,
                        fmt=fmt,
                        location="cursor",
                        arckep_job_id=resolved_id,
                        arckep_task_type=f"print_{fmt}",
                    )
            except ArcKepApiError as exc:
                wm_state.last_error = exc.message
                self.report({"ERROR"}, exc.message)
                return {"CANCELLED"}

            arch.print_filepath = str(saved)
            wm_state.last_error = ""
            wm_state.status_message = f"Saved {saved.name} (0 RUB)"
            self.report({"INFO"}, wm_state.status_message)
            return {"FINISHED"}

    ARCHIVE_OPERATOR_CLASSES = (
        ARCKEP_OT_refresh_archive,
        ARCKEP_OT_import_archive_job,
        ARCKEP_OT_export_print_mesh,
    )
else:  # pragma: no cover
    ARCHIVE_OPERATOR_CLASSES = ()
