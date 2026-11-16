# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure-Python 3D generation model catalog and strict payload builder (zero ``bpy`` deps)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .api_validation import ArcKepValidationError, validate_generation_xor
from .process_defs import (
    PROCESS_OP_IDS,
    PROCESS_OP_ITEMS,
    build_process_payload,
    check_bare_upload_key,
    clean_upload_key_list,
    normalize_positional_tripo_urls,
)

__all__ = [
    "GENERATION_MODEL_IDS", "GENERATION_MODEL_ITEMS", "PROCESS_OP_IDS", "PROCESS_OP_ITEMS",
    "RODIN_MESH_OPTION_ITEMS", "allowed_input_modes_for_model", "build_generation_payload",
    "build_process_payload", "rodin_allowed_mesh_options",
]

GENERATION_MODEL_ITEMS: tuple[tuple[str, str, str], ...] = (
    ("rodin-v2.5", "Rodin Gen-2.5", "Hard-surface & props (GLB, FBX, OBJ ZIP, USDZ, STL)"),
    ("meshy-v7.1", "Meshy 7.1", "Universal PBR mesh, lowpoly, smart-topology & auto-rig"),
    ("tripo-h3.1", "Tripo H3.1", "Flagship detailed mesh, quad FBX & segmented parts"),
    ("tripo-p2", "Tripo P2", "Clean game-ready mesh (Triangle / Quad)"),
    ("tripo-p1", "Tripo P1", "Fast stylized game mesh"),
    ("hunyuan-3d-3.1", "Hunyuan 3D 3.1", "Fast text or single photo to 3D mesh"),
    ("tripo-multiview", "Tripo Multiview", "Generate or edit 4 orthogonal reference views"),
    ("tripo-splat", "Tripo Splat", "Single image to 3D Gaussian Splat (.splat)"),
    ("idol", "IDOL 3DGS", "Fast 3D Gaussian Splat + point cloud (.glb / .ply)"),
)
GENERATION_MODEL_IDS = frozenset(item[0] for item in GENERATION_MODEL_ITEMS)

CREATE_INPUT_MODE_ITEMS: tuple[tuple[str, str, str, int], ...] = (
    ("text", "Text Prompt", "Generate 3D asset from a text description", 0),
    ("image", "Single Image", "Generate 3D asset from one local reference image", 1),
    ("multi_image", "Multi-Image", "Generate 3D asset from multiple reference views", 2),
    ("viewport", "Viewport Blockout", "Capture Blender 3D viewport/selection as reference PNG", 3),
    ("multiview_job", "Multiview Job", "Generate or edit from an existing Tripo Multiview job ID", 4),
)
_IM = {item[0]: item for item in CREATE_INPUT_MODE_ITEMS}
_MODEL_INPUT_MAP: dict[str, tuple[tuple[str, str, str, int], ...]] = {
    "tripo-splat": (_IM["image"], _IM["viewport"]),
    "idol": (_IM["image"], _IM["viewport"]),
    "tripo-multiview:generate": (_IM["image"], _IM["viewport"]),
    "tripo-multiview:edit": (_IM["multiview_job"],),
    "hunyuan-3d-3.1": (_IM["text"], _IM["image"], _IM["viewport"]),
    "meshy-v7.1": (_IM["text"], _IM["image"], _IM["multi_image"], _IM["viewport"]),
    "rodin-v2.5": (_IM["text"], _IM["image"], _IM["multi_image"], _IM["viewport"]),
}


def allowed_input_modes_for_model(
    model: str, multiview_op: str = "generate"
) -> tuple[tuple[str, str, str, int], ...]:
    """Return permanent GC-safe EnumProperty items tuple valid for ``model``."""
    key = f"tripo-multiview:{multiview_op}" if model == "tripo-multiview" else model
    return _MODEL_INPUT_MAP.get(key, CREATE_INPUT_MODE_ITEMS)


VIEWPORT_CAPTURE_MODE_ITEMS = (
    ("selection", "Selected Mesh", "Frame selected meshes from a 3/4 angle"),
    ("camera", "Active Camera", "Render from the scene's active camera"),
    ("scene", "Whole Scene", "Frame all scene meshes from a 3/4 angle"),
)
TOPOLOGY_ITEMS = (("triangle", "Triangle", "Triangle mesh"), ("quad", "Quad", "Quad-dominant mesh"))
OUTPUT_FORMAT_ITEMS = tuple(
    (f, "OBJ (ZIP)" if f == "obj" else f.upper(), f) for f in ("glb", "fbx", "obj", "usdz", "stl")
)
MESHY_MODEL_TYPE_ITEMS = (
    ("standard", "Standard", "Full-detail mesh"),
    ("lowpoly", "Lowpoly", "Clean low-polygon stylized mesh"),
    ("smart-topology", "Smart Topology", "Adaptive topology up to 15,000 faces"),
)
TRIPO_TEX_QUALITY_ITEMS = tuple(
    (q, q.title(), f"{q.title()} texture") for q in ("fast", "standard", "detailed", "extreme")
)
TRIPO_GEO_QUALITY_ITEMS = (("standard", "Standard", "Standard"), ("detailed", "Detailed", "Detailed H3.1"))
RODIN_VARIANT_ITEMS = (("standard", "Standard", "Full Rodin Gen-2.5"), ("fast", "Fast", "Fast Rodin"))
RODIN_STANDARD_TIERS = (
    "Gen-2.5-Minimum", "Gen-2.5-Extreme-Low", "Gen-2.5-Low",
    "Gen-2.5-Medium", "Gen-2.5-High", "Gen-2.5-Extreme-High",
)
RODIN_FAST_TIERS = ("Gen-2.5-Minimum", "Gen-2.5-Extreme-Low", "Gen-2.5-Low")
RODIN_DEFAULT_TIER = {"standard": "Gen-2.5-High", "fast": "Gen-2.5-Extreme-Low"}
RODIN_TIER_ITEMS = tuple((t, t, t) for t in RODIN_STANDARD_TIERS)
RODIN_MESH_OPTIONS_BY_VARIANT: dict[str, tuple[str, ...]] = {
    "standard": (
        "Auto", "4K Quad", "8K Quad", "18K Quad", "50K Quad", "100K Quad", "200K Quad",
        "2K Triangle", "20K Triangle", "50K Triangle", "150K Triangle", "500K Triangle",
        "1M Triangle", "2M Triangle",
    ),
    "fast": (
        "Auto", "1K Quad", "4K Quad", "8K Quad", "18K Quad", "20K Quad",
        "2K Triangle", "4K Triangle", "8K Triangle", "10K Triangle", "20K Triangle",
    ),
}
RODIN_ALL_MESH_OPTIONS = tuple(
    dict.fromkeys(RODIN_MESH_OPTIONS_BY_VARIANT["standard"] + RODIN_MESH_OPTIONS_BY_VARIANT["fast"])
)
RODIN_MESH_OPTION_ITEMS = tuple((m, m, m) for m in RODIN_ALL_MESH_OPTIONS)


def rodin_allowed_mesh_options(variant: str, tier: str | None = None) -> list[str]:
    """Exact replica of ``backend/app/services/rodin_service.py::allowed_mesh_options``."""
    eff_tier = tier or RODIN_DEFAULT_TIER.get(variant, "Gen-2.5-High")
    if eff_tier == "Gen-2.5-Minimum":
        return ["Auto"]
    allowed = ["Auto"]
    for option in RODIN_MESH_OPTIONS_BY_VARIANT[variant][1:]:
        amount, kind = option.split(" ")
        count = int(amount[:-1]) * (1_000 if amount[-1] == "K" else 1_000_000)
        if kind == "Triangle" and variant == "standard":
            high = eff_tier in ("Gen-2.5-High", "Gen-2.5-Extreme-High")
            if count > (2_000_000 if high else 1_000_000) or (high and count < 20_000):
                continue
        allowed.append(option)
    return allowed


HUNYUAN_QUALITY_ITEMS = tuple((q, q.title(), q) for q in ("fast", "medium", "quality", "max", "ultra"))
HUNYUAN_GEN_TYPE_ITEMS = (("Normal", "Normal (Textured)", "Mesh with maps"), ("Geometry", "Geometry", "Clay mesh"))
MULTIVIEW_OP_ITEMS = (
    ("generate", "Generate 4 Views", "Single image to front/left/back/right views"),
    ("edit", "Edit Views", "Edit specific views of an existing multiview job"),
)
IDOL_IMAGE_TYPE_ITEMS = (("synthetic", "Synthetic", "Rendered/clean object"), ("real", "Real Photo", "Real photo"))


def _err(msg: str) -> ArcKepValidationError:
    return ArcKepValidationError(400, "INVALID_PARAMETER", msg)


def build_generation_payload(model: str, **opts: Any) -> dict[str, Any]:
    """Build a clean ``/models3d/{estimate,generations}`` payload with zero cross-family leakage."""
    if model in ("meshy-v7-retexture", "meshy-n1-retexture"):
        raise _err(f"Model {model!r} is only valid via /models3d/process/* with operation='texture'.")
    if model not in GENERATION_MODEL_IDS:
        raise _err(f"Unsupported 3D generation model {model!r}.")

    prompt = (opts.get("prompt") or "").strip() or None
    image_url = check_bare_upload_key(opts.get("image_url"), "image_url")
    raw_urls = opts.get("image_urls")
    is_tripo_mv_mesh = model in ("tripo-p1", "tripo-p2", "tripo-h3.1")
    image_urls = (
        normalize_positional_tripo_urls(raw_urls, require_front=True, min_filled=2)
        if is_tripo_mv_mesh
        else clean_upload_key_list(raw_urls, "image_urls")
    )
    mv_job_id = opts.get("source_multiview_job_id")
    mv_id = int(mv_job_id) if mv_job_id not in (None, 0, False, "") else None
    if prompt and len(prompt) > 1024:
        raise _err("prompt must be <= 1024 characters.")

    payload: dict[str, Any] = {"model": model}
    if model == "tripo-multiview":
        mv_op = str(opts.get("multiview_op") or "generate")
        if mv_op not in ("generate", "edit"):
            raise _err(f"Invalid multiview_op {mv_op!r}.")
        payload["multiview_op"] = mv_op
        if mv_op == "generate":
            if not image_url or prompt or image_urls or mv_id is not None:
                raise _err("tripo-multiview generate requires image_url only.")
            payload["image_url"] = image_url
        else:
            if mv_id is None or prompt or image_url or image_urls:
                raise _err("tripo-multiview edit requires source_multiview_job_id only.")
            raw_edits = opts.get("edit_view_prompts") or []
            if not isinstance(raw_edits, Sequence) or not (1 <= len(raw_edits) <= 4):
                raise _err("tripo-multiview edit requires 1..4 edit_view_prompts.")
            seen_views: set[str] = set()
            edits: list[dict[str, str]] = []
            for item in raw_edits:
                view = str(item.get("view") or "").strip()
                vp = str(item.get("prompt") or "").strip()
                if view not in ("front", "left", "back", "right") or view in seen_views:
                    raise _err(f"Invalid or duplicate multiview edit view {view!r}.")
                if not (1 <= len(vp) <= 1024):
                    raise _err("Each edit_view_prompts entry requires prompt of 1..1024 chars.")
                seen_views.add(view)
                edits.append({"view": view, "prompt": vp})
            payload["source_multiview_job_id"] = mv_id
            payload["edit_view_prompts"] = edits
    else:
        if prompt:
            payload["prompt"] = prompt
        if image_url:
            payload["image_url"] = image_url
        if image_urls:
            payload["image_urls"] = image_urls
        if mv_id is not None:
            payload["source_multiview_job_id"] = mv_id
        validate_generation_xor(payload)

    if model == "hunyuan-3d-3.1":
        if image_urls or mv_id is not None:
            raise _err("hunyuan-3d-3.1 supports only prompt or single image_url.")
        gen_type = str(opts.get("generate_type") or "Normal")
        if gen_type not in ("Normal", "Geometry"):
            raise _err(f"Invalid generate_type {gen_type!r}.")
        payload["generate_type"] = gen_type
        payload["enable_pbr"] = bool(opts.get("enable_pbr", False))
        if (fc := opts.get("face_count")) is not None:
            fc_int = int(fc)
            if not (40_000 <= fc_int <= 1_500_000):
                raise _err("hunyuan-3d-3.1 face_count must be in 40000..1500000.")
            payload["face_count"] = fc_int
        elif (qual := opts.get("quality")) is not None:
            if qual not in ("fast", "medium", "quality", "max", "ultra"):
                raise _err(f"Invalid hunyuan quality {qual!r}.")
            payload["quality"] = qual
    elif model == "meshy-v7.1":
        if mv_id is not None:
            raise _err("meshy-v7.1 does not accept source_multiview_job_id.")
        if prompt and len(prompt) > 600:
            raise _err("meshy-v7.1 prompt must be <= 600 characters.")
        if image_urls and len(image_urls) > 4:
            raise _err("meshy-v7.1 supports at most 4 reference images.")
        mtype = str(opts.get("model_type") or "standard")
        if mtype not in ("standard", "lowpoly", "smart-topology"):
            raise _err(f"Invalid meshy model_type {mtype!r}.")
        if image_urls and mtype != "standard":
            raise _err("meshy-v7.1 multi-image requires model_type='standard'.")
        topo = str(opts.get("topology") or "triangle")
        poly = opts.get("polycount")
        if mtype == "lowpoly":
            if poly is not None or topo != "triangle":
                raise _err("meshy-v7.1 lowpoly forbids polycount and quad topology.")
        else:
            if topo not in ("triangle", "quad"):
                raise _err(f"Invalid topology {topo!r}.")
            payload["topology"] = topo
            if poly is not None:
                p_int = int(poly)
                max_p = 15_000 if mtype == "smart-topology" else 300_000
                if not (100 <= p_int <= max_p):
                    raise _err(f"meshy-v7.1 ({mtype}) polycount must be in 100..{max_p}.")
                payload["polycount"] = p_int
        tex = bool(opts.get("texture", True))
        pbr = bool(opts.get("enable_pbr", False))
        if pbr and not tex:
            raise _err("meshy-v7.1 enable_pbr=True requires texture=True.")
        rig = bool(opts.get("enable_rigging", False))
        anim = bool(opts.get("enable_animation", False))
        if anim and not rig:
            raise _err("meshy-v7.1 enable_animation=True requires enable_rigging=True.")
        payload.update({"model_type": mtype, "texture": tex, "enable_pbr": pbr, "enable_rigging": rig})
        if anim:
            act = int(opts.get("animation_action_id", 92))
            if not (0 <= act <= 696):
                raise _err("meshy-v7.1 animation_action_id must be in 0..696.")
            payload["enable_animation"] = True
            payload["animation_action_id"] = act
    elif is_tripo_mv_mesh:
        gen_parts = bool(opts.get("generate_parts", False))
        smart_lp = bool(opts.get("smart_low_poly", False))
        topo = str(opts.get("topology") or "triangle")
        geo_q = str(opts.get("geometry_quality") or "standard")
        out_fmt = str(opts.get("output_format") or "glb")
        tex = bool(opts.get("texture", True))
        pbr = bool(opts.get("enable_pbr", False))
        if model != "tripo-h3.1" and (gen_parts or smart_lp or geo_q != "standard"):
            raise _err(f"{model} forbids generate_parts, smart_low_poly, and detailed geometry.")
        if model == "tripo-p1" and topo != "triangle":
            raise _err("tripo-p1 forbids quad topology.")
        if gen_parts:
            tex, pbr, topo, smart_lp, out_fmt = False, False, "triangle", False, "glb"
        if out_fmt == "fbx" and not (model == "tripo-h3.1" and topo == "quad"):
            raise _err("Tripo output_format='fbx' is only allowed for tripo-h3.1 with topology='quad'.")
        if out_fmt not in ("glb", "fbx"):
            raise _err(f"Unsupported Tripo output_format {out_fmt!r}.")
        if pbr and not tex:
            raise _err(f"{model} enable_pbr=True requires texture=True.")
        payload.update({"texture": tex, "enable_pbr": pbr, "output_format": out_fmt})
        if tex:
            tq = str(opts.get("texture_quality") or "standard")
            if tq not in ("fast", "standard", "detailed", "extreme"):
                raise _err(f"Invalid texture_quality {tq!r}.")
            payload["texture_quality"] = tq
        if model in ("tripo-p2", "tripo-h3.1"):
            payload["topology"] = topo
        if model == "tripo-h3.1":
            payload["geometry_quality"] = geo_q
            payload["smart_low_poly"] = smart_lp
            payload["generate_parts"] = gen_parts
            if opts.get("auto_size"):
                payload["auto_size"] = True
            if opts.get("delight") and tex:
                payload["delight"] = True
        if neg := (opts.get("negative_prompt") or "").strip():
            if len(neg) > 255:
                raise _err("Tripo negative_prompt must be <= 255 chars.")
            payload["negative_prompt"] = neg
        if (orient := opts.get("orientation")) is not None:
            if orient not in ("default", "align_image"):
                raise _err(f"Invalid orientation {orient!r}.")
            payload["orientation"] = orient
        if (eo := opts.get("export_orientation")) is not None:
            if eo not in ("+x", "-x", "+y", "-y"):
                raise _err(f"Invalid export_orientation {eo!r}.")
            payload["export_orientation"] = eo
        if (poly := opts.get("polycount")) is not None:
            p_int = int(poly)
            if model == "tripo-p1" and not (50 <= p_int <= 20_000):
                raise _err("tripo-p1 polycount must be in 50..20000.")
            if model == "tripo-p2" and not (48 <= p_int <= (25_000 if topo == "quad" else 50_000)):
                raise _err("tripo-p2 polycount out of allowed range.")
            if model == "tripo-h3.1":
                lo = 500 if smart_lp else 1_000
                hi = (10_000 if topo == "quad" else 20_000) if smart_lp else (150_000 if topo == "quad" else 2_000_000)
                if not (lo <= p_int <= hi):
                    raise _err(f"tripo-h3.1 polycount must be in {lo}..{hi}.")
            payload["polycount"] = p_int
    elif model == "rodin-v2.5":
        if mv_id is not None:
            raise _err("rodin-v2.5 does not accept source_multiview_job_id.")
        if image_urls and len(image_urls) > 5:
            raise _err("rodin-v2.5 supports at most 5 reference images.")
        variant = str(opts.get("variant") or "standard")
        if variant not in ("standard", "fast"):
            raise _err(f"Invalid rodin variant {variant!r}.")
        tier = opts.get("tier")
        allowed_tiers = RODIN_FAST_TIERS if variant == "fast" else RODIN_STANDARD_TIERS
        if tier is not None and tier not in allowed_tiers:
            raise _err(f"Rodin tier {tier!r} is not valid for variant={variant!r}.")
        eff_tier = tier or RODIN_DEFAULT_TIER[variant]
        mesh_opt = str(opts.get("quality_mesh_option") or "Auto")
        if mesh_opt not in rodin_allowed_mesh_options(variant, eff_tier):
            raise _err(f"Rodin quality_mesh_option {mesh_opt!r} is not allowed for {variant}/{eff_tier}.")
        high_pack = bool(opts.get("high_pack", False))
        if high_pack and (variant == "fast" or mesh_opt.endswith("Quad")):
            raise _err("rodin-v2.5 high_pack is forbidden with fast variant or *Quad mesh options.")
        is_micro = bool(opts.get("is_micro", False))
        if is_micro and eff_tier != "Gen-2.5-Extreme-High":
            raise _err("rodin-v2.5 is_micro=True requires tier='Gen-2.5-Extreme-High'.")
        out_fmt = str(opts.get("output_format") or "glb")
        if out_fmt not in ("glb", "fbx", "obj", "usdz", "stl"):
            raise _err(f"Unsupported rodin-v2.5 output_format {out_fmt!r}.")
        payload.update(
            variant=variant, quality_mesh_option=mesh_opt, high_pack=high_pack, is_micro=is_micro,
            output_format=out_fmt, enable_pbr=bool(opts.get("enable_pbr", True)),
            tpose=bool(opts.get("tpose", False)), hd_texture=bool(opts.get("hd_texture", False)),
            texture_delight=bool(opts.get("texture_delight", False)),
            enable_creative_mode=bool(opts.get("enable_creative_mode", False)),
        )
        if tier is not None:
            payload["tier"] = tier
        if (tmode := opts.get("texture_mode")) is not None:
            if tmode not in ("legacy", "extreme-low", "low", "medium", "high"):
                raise _err(f"Invalid rodin texture_mode {tmode!r}.")
            payload["texture_mode"] = tmode
        if (seed := opts.get("seed")) is not None:
            s_int = int(seed)
            if not (0 <= s_int <= 65535):
                raise _err("rodin-v2.5 seed must be in 0..65535.")
            payload["seed"] = s_int
    elif model == "tripo-splat":
        if not image_url:
            raise _err("tripo-splat requires a single image_url.")
        out_fmt = str(opts.get("output_format") or "glb")
        if out_fmt != "glb":
            raise _err("tripo-splat requires output_format='glb' (or omitted).")
        payload["output_format"] = "glb"
        if (mseed := opts.get("model_seed")) is not None:
            s_int = int(mseed)
            if not (0 <= s_int <= 2_147_483_647):
                raise _err("tripo-splat model_seed out of range.")
            payload["model_seed"] = s_int
    elif model == "idol":
        if not image_url:
            raise _err("idol requires a single image_url.")
        out_fmt = str(opts.get("output_format") or "glb")
        if out_fmt != "glb":
            raise _err("idol requires output_format='glb' (or omitted).")
        img_type = str(opts.get("image_type") or "synthetic")
        if img_type not in ("synthetic", "real"):
            raise _err(f"Invalid idol image_type {img_type!r}.")
        ng = int(opts.get("num_gaussians", 262_144))
        steps = int(opts.get("num_inference_steps", 50))
        cfg = float(opts.get("guidance_scale", 3.0))
        if not (32_768 <= ng <= 1_048_576) or not (1 <= steps <= 50) or not (0.0 <= cfg <= 10.0):
            raise _err("idol parameters out of range.")
        payload.update(
            output_format="glb", image_type=img_type, num_gaussians=ng,
            num_inference_steps=steps, guidance_scale=cfg,
        )
        if (seed := opts.get("seed")) is not None:
            s_int = int(seed)
            if not (0 <= s_int <= 2_147_483_647):
                raise _err("idol seed out of range.")
            payload["seed"] = s_int

    if opts.get("max_cost_rub") is not None:
        payload["max_cost_rub"] = round(float(opts["max_cost_rub"]), 2)
    return payload
