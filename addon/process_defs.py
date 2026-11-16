# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure-Python 3D mesh post-processing catalog specs and payload builder (zero ``bpy`` deps)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .api_validation import ArcKepValidationError, validate_process_source

PROCESS_OP_ITEMS: tuple[tuple[str, str, str], ...] = (
    ("texture", "AI Retexture", "PBR retexture up to 8K (Tripo v3.5, Meshy 7, Meshy N1)"),
    ("decimate", "Retopology / Decimate", "Polygon reduction with Triangle or Quad topology"),
    ("uv", "Smart UV Unwrap", "Unwrap UV islands and bake textures"),
    ("segment", "Mesh Segmentation", "Split mesh into semantic parts in a collection"),
    ("smartsegment", "Smart Segment", "Granular model or image segmentation with optional hint"),
    ("complete", "Complete Parts", "Close open geometry on segmented Tripo parts"),
    ("rig", "Auto-Rig", "Build biped/quadruped/creature skeleton (GLB / FBX)"),
    ("retarget", "Retarget Animations", "Apply 1..5 animation presets to a rigged Tripo job"),
    ("rig_and_retarget", "Rig & Retarget", "One-step skeleton rigging and animation retargeting"),
    ("convert", "Convert Format", "Server-side conversion (FBX Blender preset, USDZ, OBJ, 3MF)"),
)
PROCESS_OP_IDS = frozenset(item[0] for item in PROCESS_OP_ITEMS)

TEXTURE_MODEL_ITEMS = (
    ("tripo", "Tripo v3.5 Texture", "Text/image PBR retexture"),
    ("meshy-v7-retexture", "Meshy 7 Retexture (2K–8K)", "Text or 1 image retexture up to 8K (<=30 MiB)"),
    ("meshy-n1-retexture", "Meshy N1 Style Retexture", "1–4 style images retexture (<=30 MiB)"),
)
MESHY_TEX_RES_ITEMS = (("2k", "2K", "2048px"), ("4k", "4K", "4096px"), ("8k", "8K", "8192px"))
DECIMATE_MODEL_ITEMS = (
    ("v2.0", "Tripo v2.0", "Up to 500K faces"),
    ("v3.0", "Tripo v3.0", "Up to 20K faces + Smart UV"),
    ("v1.0", "Tripo v1.0", "Legacy decimation"),
)
ORIENTATION_AXIS_ITEMS = (("+x", "+X", "+X"), ("-x", "-X", "-X"), ("+y", "+Y", "+Y"), ("-y", "-Y", "-Y"))
SEG_GRANULARITY_ITEMS = (
    ("simple", "Simple", "Few major parts"),
    ("balanced", "Balanced", "Balanced part count"),
    ("detailed", "Detailed", "Fine semantic parts"),
)
SMART_SEG_TYPE_ITEMS = (("model", "3D Mesh (.glb)", "Segment 3D mesh"), ("image", "2D Image", "Segment image"))
SMART_GRANULARITY_ITEMS = (("coarse", "Coarse", "Coarse"), ("medium", "Medium", "Medium"), ("fine", "Fine", "Fine"))
COMPLETION_MODE_ITEMS = (("ai_completion", "AI Completion", "AI fill"), ("quick_cap", "Quick Cap", "Fast planar cap"))
RIG_MODEL_ITEMS = (
    ("v3.0-20260909", "Rig v3.0", "Latest biped & creature rig"),
    ("v2.5-20260210", "Rig v2.5", "Stable v2.5 rig"),
    ("v1.0-20240301", "Rig v1.0", "Legacy biped rig"),
)
RIG_TYPE_ITEMS = tuple(
    (r, r.title(), r)
    for r in ("biped", "quadruped", "hexapod", "octopod", "avian", "serpentine", "aquatic", "others")
)
RIG_SPEC_ITEMS = tuple(
    (s, s.upper() if len(s) <= 3 else s.title(), s)
    for s in ("tripo", "mixamo", "unity", "unreal", "vrm", "actorcore")
)
RIG_OUT_FORMAT_ITEMS = (("glb", "GLB", "glTF Binary"), ("fbx", "FBX", "Autodesk FBX"))
CONVERT_FORMAT_ITEMS = tuple((f, f, f) for f in ("FBX", "GLTF", "USDZ", "OBJ", "STL", "3MF"))
FBX_PRESET_ITEMS = (
    ("blender", "Blender", "Blender axis & scale preset"),
    ("3dsmax", "3ds Max", "3ds Max preset"),
    ("mixamo", "Mixamo", "Mixamo preset"),
    ("bake_scale", "Bake Scale", "Bake scale"),
)
PRINT_FORMAT_ITEMS = (("stl", "Binary STL", "Standard slicer STL"), ("3mf", "3MF (mm)", "3MF archive in millimeters"))
PRINT_SCALE_AXIS_ITEMS = (
    ("max", "Longest Axis", "Scale longest dimension"),
    ("height", "Height", "Scale Z/Y height"),
    ("width", "Width", "Scale X width"),
    ("depth", "Depth", "Scale depth"),
)


def _err(msg: str) -> ArcKepValidationError:
    return ArcKepValidationError(400, "INVALID_PARAMETER", msg)


def check_bare_upload_key(ref: str | None, field: str) -> str | None:
    if ref is None:
        return None
    val = str(ref).strip()
    if not val:
        return None
    if val.startswith(("http://", "https://")):
        raise _err(f"{field} must be a bare upload 'key' from POST /uploads, never an HTTP URL.")
    return val


def clean_upload_key_list(refs: Sequence[str] | None, field: str) -> list[str] | None:
    """Validate a compacted non-empty list of bare keys (for Meshy / Rodin)."""
    if refs is None or len(refs) == 0:
        return None
    cleaned: list[str] = []
    for raw in refs:
        val = check_bare_upload_key(raw, field)
        if not val:
            raise _err(f"{field} must not contain empty string slots for this model.")
        cleaned.append(val)
    return cleaned


def normalize_positional_tripo_urls(
    refs: Sequence[str] | None, *, require_front: bool, min_filled: int
) -> list[str] | None:
    """Validate positional Tripo 4-view ``[front, left, back, right]`` preserving ``""`` slots."""
    if refs is None or len(refs) == 0:
        return None
    if len(refs) > 4:
        raise _err("Tripo image_urls supports at most 4 positional view slots.")
    slots = [check_bare_upload_key(r, "image_urls") or "" for r in refs]
    filled = sum(1 for s in slots if s)
    if filled == 0:
        return None
    if filled < min_filled:
        raise _err(f"Tripo image_urls requires at least {min_filled} non-empty view(s).")
    if require_front and not slots[0]:
        raise _err("Tripo multiview image_urls requires Front view in slot 0 (image_urls[0]).")
    return slots


def build_process_payload(operation: str, **opts: Any) -> dict[str, Any]:
    """Build a clean ``/models3d/process/{estimate,generations}`` payload with exact field names."""
    if operation not in PROCESS_OP_IDS:
        raise _err(f"Unsupported process operation {operation!r}.")
    src_id = opts.get("source_job_id")
    job_id = int(src_id) if src_id not in (None, 0, False, "") else None
    model_url = check_bare_upload_key(opts.get("model_url"), "model_url")
    image_url = check_bare_upload_key(opts.get("image_url"), "image_url")
    raw_urls = opts.get("image_urls")
    if image_url and raw_urls:
        raise _err("Provide either image_url or image_urls, not both.")

    payload: dict[str, Any] = {"operation": operation}
    if job_id is not None:
        payload["source_job_id"] = job_id
    if model_url:
        payload["model_url"] = model_url

    if operation == "smartsegment":
        seg_type = str(opts.get("seg_type") or "model")
        if seg_type not in ("model", "image"):
            raise _err(f"Invalid seg_type {seg_type!r}.")
        payload["seg_type"] = seg_type
        if seg_type == "image" and image_url:
            payload["image_url"] = image_url
        elif seg_type == "model" and model_url and not model_url.lower().endswith(".glb"):
            raise _err("smartsegment with seg_type='model' requires a .glb mesh.")
    validate_process_source(payload)

    if operation in ("rig", "rig_and_retarget") and model_url and model_url.lower().endswith((".gltf", ".zip")):
        raise _err(f"{operation} forbids .gltf and .zip; export binary .glb instead.")

    if operation == "texture":
        tmodel = str(opts.get("texture_model") or "tripo")
        if tmodel not in ("tripo", "meshy-v7-retexture", "meshy-n1-retexture"):
            raise _err(f"Unsupported texture_model {tmodel!r}.")
        tprompt = (opts.get("texture_prompt") or "").strip() or None
        payload["texture_model"] = tmodel
        payload["enable_pbr"] = bool(opts.get("enable_pbr", True))
        if tmodel == "tripo":
            pos_urls = normalize_positional_tripo_urls(
                raw_urls, require_front=not bool(tprompt), min_filled=1
            )
            if not (tprompt or image_url or pos_urls):
                raise _err("Tripo texture requires texture_prompt, image_url, or image_urls.")
            tq = str(opts.get("texture_quality") or "standard")
            if tq not in ("fast", "standard", "detailed", "extreme"):
                raise _err(f"Invalid texture_quality {tq!r}.")
            payload["texture_quality"] = tq
            payload["bake"] = bool(opts.get("bake", True))
            if tprompt:
                payload["texture_prompt"] = tprompt
            if image_url:
                payload["image_url"] = image_url
            elif pos_urls:
                payload["image_urls"] = pos_urls
            if opts.get("delight") is not None:
                payload["delight"] = bool(opts["delight"])
            if (talign := opts.get("texture_alignment")) is not None:
                if talign not in ("original_image", "geometry"):
                    raise _err(f"Invalid texture_alignment {talign!r}.")
                payload["texture_alignment"] = talign
            if (tseed := opts.get("texture_seed")) is not None:
                payload["texture_seed"] = int(tseed)
        elif tmodel == "meshy-v7-retexture":
            comp_urls = clean_upload_key_list(raw_urls, "image_urls")
            if comp_urls and len(comp_urls) > 1:
                raise _err("meshy-v7-retexture accepts at most 1 reference style image.")
            single_img = image_url or (comp_urls[0] if comp_urls else None)
            if not (tprompt or single_img):
                raise _err("meshy-v7-retexture requires texture_prompt and/or image_url.")
            if tprompt and len(tprompt) > 800:
                raise _err("meshy-v7-retexture texture_prompt must be <= 800 characters.")
            res = str(opts.get("texture_resolution") or "2k")
            if res not in ("2k", "4k", "8k"):
                raise _err(f"Invalid texture_resolution {res!r}.")
            payload["texture_resolution"] = res
            payload["enable_original_uv"] = bool(opts.get("enable_original_uv", True))
            if tprompt:
                payload["texture_prompt"] = tprompt
            if single_img:
                payload["image_url"] = single_img
        else:
            comp_urls = clean_upload_key_list(raw_urls, "image_urls")
            if not (image_url or comp_urls):
                raise _err("meshy-n1-retexture requires 1..4 style reference images.")
            if comp_urls and len(comp_urls) > 4:
                raise _err("meshy-n1-retexture accepts at most 4 style images.")
            payload["enable_original_uv"] = bool(opts.get("enable_original_uv", True))
            if image_url:
                payload["image_url"] = image_url
            else:
                payload["image_urls"] = comp_urls
    elif operation == "decimate":
        dmodel = str(opts.get("decimate_model") or "v2.0")
        if dmodel not in ("v1.0", "v2.0", "v3.0"):
            raise _err(f"Invalid decimate_model {dmodel!r}.")
        quad = bool(opts.get("quad", False))
        smart_uv = bool(opts.get("smart_uv", False))
        if smart_uv and dmodel != "v3.0":
            raise _err("decimate smart_uv=True is only supported on decimate_model='v3.0'.")
        if (fl := opts.get("face_limit")) is not None:
            fl_int = int(fl)
            lo = 500 if dmodel == "v3.0" else 48
            hi = (10_000 if quad else 20_000) if dmodel == "v3.0" else 500_000
            if not (lo <= fl_int <= hi):
                raise _err(f"decimate ({dmodel}) face_limit must be in {lo}..{hi}.")
            payload["face_limit"] = fl_int
        payload.update({"decimate_model": dmodel, "quad": quad, "bake": bool(opts.get("bake", True))})
        if dmodel == "v3.0":
            payload["smart_uv"] = smart_uv
    elif operation == "uv":
        orient = str(opts.get("export_orientation") or "+x")
        if orient not in ("+x", "-x", "+y", "-y"):
            raise _err(f"Invalid export_orientation {orient!r}.")
        payload.update(
            {"bake": bool(opts.get("bake", True)), "export_orientation": orient, "compress_geometry": bool(opts.get("compress_geometry", False))}
        )
    elif operation == "segment":
        payload["segment_model"] = "v2.0-20260430"
        if image_url:
            payload["image_url"] = image_url
        else:
            gran = str(opts.get("segmentation_granularity") or "balanced")
            if gran not in ("simple", "balanced", "detailed"):
                raise _err(f"Invalid segmentation_granularity {gran!r}.")
            payload["segmentation_granularity"] = gran
            payload["split_by_connectivity"] = bool(opts.get("split_by_connectivity", True))
    elif operation == "smartsegment":
        sgran = str(opts.get("smart_granularity") or "medium")
        if sgran not in ("coarse", "medium", "fine"):
            raise _err(f"Invalid smart_granularity {sgran!r}.")
        payload["smart_granularity"] = sgran
        if hint := (opts.get("hint") or "").strip():
            if len(hint) > 512:
                raise _err("smartsegment hint must be <= 512 chars.")
            payload["hint"] = hint
        if (tr := opts.get("transform")) is not None:
            if not isinstance(tr, Sequence) or len(tr) != 16:
                raise _err("smartsegment transform must contain exactly 16 floats.")
            payload["transform"] = [float(x) for x in tr]
    elif operation == "complete":
        cmode = str(opts.get("completion_mode") or "ai_completion")
        if cmode not in ("ai_completion", "quick_cap"):
            raise _err(f"Invalid completion_mode {cmode!r}.")
        payload["completion_mode"] = cmode
        if parts := [str(p).strip() for p in (opts.get("part_names") or []) if str(p).strip()]:
            payload["part_names"] = parts
    elif operation in ("rig", "rig_and_retarget"):
        rmodel = str(opts.get("rig_model") or "v3.0-20260909")
        rtype = str(opts.get("rig_type") or "biped")
        rspec = str(opts.get("rig_spec") or "tripo")
        out_fmt = str(opts.get("out_format") or "glb")
        if rmodel not in ("v3.0-20260909", "v2.5-20260210", "v1.0-20240301"):
            raise _err(f"Invalid rig_model {rmodel!r}.")
        if rtype not in ("biped", "quadruped", "hexapod", "octopod", "avian", "serpentine", "aquatic", "others"):
            raise _err(f"Invalid rig_type {rtype!r}.")
        if rspec not in ("tripo", "mixamo", "unity", "unreal", "vrm", "actorcore"):
            raise _err(f"Invalid rig_spec {rspec!r}.")
        if out_fmt not in ("glb", "fbx"):
            raise _err(f"Invalid out_format {out_fmt!r}.")
        if rmodel == "v1.0-20240301" and rtype != "biped":
            raise _err("rig_model v1.0 supports only rig_type='biped'.")
        if rtype == "others" and rmodel != "v3.0-20260909":
            raise _err("rig_type='others' requires rig_model='v3.0-20260909'.")
        if rtype == "biped" and rspec in ("actorcore", "unity", "unreal", "vrm") and rmodel != "v3.0-20260909":
            raise _err(f"rig_spec={rspec!r} requires rig_model='v3.0-20260909'.")
        payload.update({"rig_model": rmodel, "rig_type": rtype, "rig_spec": rspec, "out_format": out_fmt})
        if operation == "rig_and_retarget":
            if rtype in ("avian", "others"):
                raise _err(f"rig_type={rtype!r} has no retarget animation presets.")
            anims = [str(a).strip() for a in (opts.get("animations") or []) if str(a).strip()]
            if not (1 <= len(anims) <= 5):
                raise _err("rig_and_retarget requires 1..5 animation presets in 'animations'.")
            payload["animations"] = anims
            payload["animate_in_place"] = bool(opts.get("animate_in_place", False))
            payload["export_with_geometry"] = bool(opts.get("export_with_geometry", True))
    elif operation == "retarget":
        anims = [str(a).strip() for a in (opts.get("animations") or []) if str(a).strip()]
        if not (1 <= len(anims) <= 5):
            raise _err("retarget requires 1..5 animation presets in 'animations'.")
        out_fmt = str(opts.get("out_format") or "glb")
        if out_fmt not in ("glb", "fbx"):
            raise _err(f"Invalid out_format {out_fmt!r}.")
        payload.update(
            {
                "animations": anims,
                "out_format": out_fmt,
                "animate_in_place": bool(opts.get("animate_in_place", False)),
                "export_with_geometry": bool(opts.get("export_with_geometry", True)),
            }
        )
    elif operation == "convert":
        cfmt = str(opts.get("convert_format") or "FBX")
        if cfmt not in ("GLTF", "FBX", "USDZ", "OBJ", "STL", "3MF"):
            raise _err(f"Unsupported convert_format {cfmt!r}.")
        quad = bool(opts.get("quad", False))
        sym = bool(opts.get("force_symmetry", False))
        if sym and not quad:
            raise _err("convert force_symmetry=True requires quad=True.")
        vcol = bool(opts.get("export_vertex_colors", False))
        if vcol and cfmt not in ("OBJ", "GLTF"):
            raise _err("convert export_vertex_colors=True is only valid for OBJ or GLTF.")
        fpreset = str(opts.get("fbx_preset") or "blender")
        if fpreset not in ("blender", "3dsmax", "mixamo", "bake_scale"):
            raise _err(f"Invalid fbx_preset {fpreset!r}.")
        scale_f = float(opts.get("scale_factor", 1.0))
        tex_sz = int(opts.get("texture_size", 4096))
        if not (0.01 <= scale_f <= 1000.0) or not (128 <= tex_sz <= 8192):
            raise _err("convert scale_factor or texture_size out of range.")
        tex_fmt = str(opts.get("texture_format") or "JPEG")
        if tex_fmt not in ("JPEG", "PNG", "WEBP", "BMP", "DPX", "HDR", "OPEN_EXR", "TARGA", "TIFF"):
            raise _err(f"Unsupported texture_format {tex_fmt!r}.")
        exp_orient = str(opts.get("export_orientation") or "+x")
        if exp_orient not in ("+x", "-x", "+y", "-y", "+z", "-z"):
            raise _err(f"Unsupported export_orientation {exp_orient!r}.")
        payload.update(
            {
                "convert_format": cfmt,
                "fbx_preset": fpreset,
                "quad": quad,
                "force_symmetry": sym,
                "flatten_bottom": bool(opts.get("flatten_bottom", False)),
                "pack_uv": bool(opts.get("pack_uv", False)),
                "export_vertex_colors": vcol,
                "pivot_to_center_bottom": bool(opts.get("pivot_to_center_bottom", False)),
                "scale_factor": scale_f,
                "texture_size": tex_sz,
                "texture_format": tex_fmt,
                "bake": bool(opts.get("bake", True)),
                "export_orientation": exp_orient,
            }
        )
        if (fl := opts.get("face_limit")) is not None:
            fl_int = int(fl)
            if not (48 <= fl_int <= 2_000_000):
                raise _err("convert face_limit must be in 48..2000000.")
            payload["face_limit"] = fl_int

    if opts.get("max_cost_rub") is not None:
        payload["max_cost_rub"] = round(float(opts["max_cost_rub"]), 2)
    return payload
