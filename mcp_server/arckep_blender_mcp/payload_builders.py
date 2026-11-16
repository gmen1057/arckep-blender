# SPDX-License-Identifier: MIT
"""Standalone MIT payload builders and strict ``/api/v1/models3d`` request validators."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

MAX_MESH_UPLOAD_BYTES = 150 * 1024 * 1024  # 150 MiB
MESHY_RETEXTURE_MAX_BYTES = 30 * 1024 * 1024  # 30 MiB
MAX_IMAGE_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MiB

GENERATION_MODEL_IDS = frozenset({"rodin-v2.5", "meshy-v7.1", "tripo-h3.1", "tripo-p2", "tripo-p1", "hunyuan-3d-3.1", "tripo-multiview", "tripo-splat", "idol"})
PROCESS_OP_IDS = frozenset({"texture", "decimate", "uv", "segment", "smartsegment", "complete", "rig", "retarget", "rig_and_retarget", "convert"})
RODIN_STANDARD_TIERS = ("Gen-2.5-Minimum", "Gen-2.5-Extreme-Low", "Gen-2.5-Low", "Gen-2.5-Medium", "Gen-2.5-High", "Gen-2.5-Extreme-High")
RODIN_FAST_TIERS = ("Gen-2.5-Minimum", "Gen-2.5-Extreme-Low", "Gen-2.5-Low")
RODIN_DEFAULT_TIER = {"standard": "Gen-2.5-High", "fast": "Gen-2.5-Extreme-Low"}
RODIN_MESH_BY_VARIANT: dict[str, tuple[str, ...]] = {
    "standard": ("Auto", "4K Quad", "8K Quad", "18K Quad", "50K Quad", "100K Quad", "200K Quad", "2K Triangle", "20K Triangle", "50K Triangle", "150K Triangle", "500K Triangle", "1M Triangle", "2M Triangle"),
    "fast": ("Auto", "1K Quad", "4K Quad", "8K Quad", "18K Quad", "20K Quad", "2K Triangle", "4K Triangle", "8K Triangle", "10K Triangle", "20K Triangle"),
}
_FORBIDDEN_PROCESS_TYPOS = {
    "target_face_count": "Use 'face_limit' instead of 'target_face_count'.",
    "prompt": "Use 'texture_prompt' instead of 'prompt' for retexturing.",
    "animation_preset": "Use 'animations' (list[str]) instead of 'animation_preset'.",
    "format": "Use 'convert_format' or 'out_format' instead of 'format'.",
}
_AXES = ("+x", "-x", "+y", "-y")
_TEX_QUAL = ("fast", "standard", "detailed", "extreme")


class ArcKepApiError(Exception):
    """Structured error raised on ``/api/v1`` HTTP, transport, or validation failures."""

    def __init__(self, status_code: int, error_code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(f"[{status_code} {error_code}] {message}")
        self.status_code, self.error_code, self.message, self.details = status_code, error_code, message, details


class ArcKepValidationError(ArcKepApiError, ValueError):
    """Client-side pre-flight validation error raised before calling ``/api/v1``."""


def _invalid(message: str) -> ArcKepValidationError:
    return ArcKepValidationError(400, "INVALID_PARAMETER", message)


def _choice(val: Any, allowed: Sequence[str], name: str) -> str:
    if (s := str(val)) not in allowed:
        raise _invalid(f"Invalid {name} {s!r}; expected one of {tuple(allowed)}.")
    return s


def _int_range(val: Any, lo: int, hi: int, name: str) -> int:
    if isinstance(val, bool) or not (lo <= (i := int(val)) <= hi):
        raise _invalid(f"{name} must be an integer in {lo}..{hi}.")
    return i


def _float_range(val: Any, lo: float, hi: float, name: str) -> float:
    if isinstance(val, bool) or not (lo <= (f := float(val)) <= hi):
        raise _invalid(f"{name} must be a number in {lo}..{hi}.")
    return f


def _put_choice(dst: dict[str, Any], opts: Mapping[str, Any], key: str, allowed: Sequence[str]) -> None:
    if (v := opts.get(key)) is not None:
        dst[key] = _choice(v, allowed, key)


def _put_int(dst: dict[str, Any], opts: Mapping[str, Any], key: str, lo: int, hi: int) -> None:
    if (v := opts.get(key)) is not None:
        dst[key] = _int_range(v, lo, hi, key)


def rodin_allowed_mesh_options(variant: str, tier: str | None = None) -> list[str]:
    _choice(variant, ("standard", "fast"), "variant")
    eff_tier = tier or RODIN_DEFAULT_TIER[variant]
    if eff_tier == "Gen-2.5-Minimum":
        return ["Auto"]
    allowed = ["Auto"]
    for opt in RODIN_MESH_BY_VARIANT[variant][1:]:
        amt, kind = opt.split(" ")
        cnt = int(amt[:-1]) * (1_000 if amt[-1] == "K" else 1_000_000)
        if kind == "Triangle" and variant == "standard":
            high = eff_tier in ("Gen-2.5-High", "Gen-2.5-Extreme-High")
            if cnt > (2_000_000 if high else 1_000_000) or (high and cnt < 20_000):
                continue
        allowed.append(opt)
    return allowed


def ensure_bare_upload_key(ref: str | None, field_name: str) -> str | None:
    if ref is None or not (cleaned := str(ref).strip()):
        return None
    if cleaned.startswith(("http://", "https://")):
        raise _invalid(f"{field_name} must be a bare upload 'key' from POST /uploads, never an HTTP URL.")
    return cleaned


def clean_upload_key_list(refs: Sequence[str] | None, field_name: str) -> list[str] | None:
    if not refs:
        return None
    out = [ensure_bare_upload_key(item, field_name) for item in refs]
    if any(not k for k in out):
        raise _invalid(f"{field_name} must not contain empty string slots for this model.")
    return [k for k in out if k]


def normalize_tripo_view_keys(refs: Sequence[str] | None, *, require_front: bool, min_filled: int) -> list[str] | None:
    if not refs:
        return None
    if len(refs) > 4:
        raise _invalid("Tripo image_urls supports at most 4 positional slots [front, left, back, right].")
    slots = [ensure_bare_upload_key(r, "image_urls") or "" for r in refs]
    if (filled := sum(1 for s in slots if s)) == 0:
        return None
    if filled < min_filled or (require_front and not slots[0]):
        raise _invalid(f"Tripo image_urls requires >={min_filled} view(s)" + (" and Front at slot 0." if require_front else "."))
    return slots


def validate_generation_xor(payload: Mapping[str, Any]) -> None:
    mv_id = payload.get("source_multiview_job_id")
    if sum((isinstance(payload.get("prompt"), str) and bool(payload["prompt"].strip()), isinstance(payload.get("image_url"), str) and bool(payload["image_url"].strip()), isinstance(payload.get("image_urls"), (list, tuple)) and len(payload["image_urls"]) > 0, mv_id is not None and not isinstance(mv_id, bool))) != 1:
        raise _invalid("Provide exactly one of: prompt, image_url, image_urls, or source_multiview_job_id.")


def validate_rig_check_xor(source_job_id: int | None, model_url: str | None) -> dict[str, Any]:
    has_job = source_job_id is not None and not isinstance(source_job_id, bool)
    key = ensure_bare_upload_key(model_url, "model_url")
    if has_job == bool(key):
        raise _invalid("Provide exactly one of source_job_id or model_url.")
    if has_job:
        return {"source_job_id": int(source_job_id)}  # type: ignore[arg-type]
    assert key is not None
    if key.lower().endswith((".gltf", ".zip")):
        raise _invalid("rig-check forbids .gltf and .zip; upload binary .glb instead.")
    return {"model_url": key}


def validate_process_source(payload: Mapping[str, Any]) -> None:
    if not (op := str(payload.get("operation") or "").strip()):
        raise _invalid("Process payload requires 'operation'.")
    raw_job = payload.get("source_job_id")
    has_job = raw_job is not None and not isinstance(raw_job, bool)
    has_model = isinstance(payload.get("model_url"), str) and bool(payload["model_url"].strip())
    if op == "smartsegment" and payload.get("seg_type") == "image":
        if not (isinstance(payload.get("image_url"), str) and payload["image_url"].strip()) or has_job or has_model:
            raise _invalid("smartsegment with seg_type='image' requires image_url and no mesh source.")
    elif op in ("retarget", "complete"):
        if not has_job or has_model:
            raise _invalid(f"Operation {op!r} requires source_job_id from an owned ArcKep generation.")
    elif has_job == has_model:
        raise _invalid("Provide exactly one of source_job_id or model_url.")


def build_generation_payload(model: str, **opts: Any) -> dict[str, Any]:
    """Build a validated ``/models3d/{estimate,generations}`` payload with zero cross-model leakage."""
    if model in ("meshy-v7-retexture", "meshy-n1-retexture") or model not in GENERATION_MODEL_IDS:
        raise _invalid(f"Model {model!r} is only valid via /models3d/process/* with operation='texture'." if model in ("meshy-v7-retexture", "meshy-n1-retexture") else f"Unsupported 3D generation model {model!r}.")
    prompt = (opts.get("prompt") or "").strip() or None
    if prompt and len(prompt) > 1024:
        raise _invalid("prompt must be <= 1024 characters.")
    image_url = ensure_bare_upload_key(opts.get("image_url"), "image_url")
    is_tripo = model in ("tripo-p1", "tripo-p2", "tripo-h3.1")
    image_urls = normalize_tripo_view_keys(opts.get("image_urls"), require_front=True, min_filled=2) if is_tripo else clean_upload_key_list(opts.get("image_urls"), "image_urls")
    mv_id = int(raw_mv) if (raw_mv := opts.get("source_multiview_job_id")) not in (None, 0, False, "") else None
    payload: dict[str, Any] = {"model": model}

    if model == "tripo-multiview":
        mv_op = _choice(opts.get("multiview_op") or "generate", ("generate", "edit"), "multiview_op")
        payload["multiview_op"] = mv_op
        if mv_op == "generate":
            if not image_url or prompt or image_urls or mv_id is not None:
                raise _invalid("tripo-multiview generate requires image_url only.")
            payload["image_url"] = image_url
        else:
            raw_edits = opts.get("edit_view_prompts") or []
            if mv_id is None or prompt or image_url or image_urls or not isinstance(raw_edits, Sequence) or not (1 <= len(raw_edits) <= 4):
                raise _invalid("tripo-multiview edit requires source_multiview_job_id and 1..4 edit_view_prompts.")
            seen, edits = set(), []
            for entry in raw_edits:
                if not isinstance(entry, Mapping):
                    raise _invalid("edit_view_prompts items must be mappings with 'view' and 'prompt'.")
                view = _choice(str(entry.get("view") or "").strip(), ("front", "left", "back", "right"), "view")
                if view in seen or not (1 <= len(vp := str(entry.get("prompt") or "").strip()) <= 1024):
                    raise _invalid(f"Duplicate view {view!r} or invalid edit prompt length.")
                seen.add(view)
                edits.append({"view": view, "prompt": vp})
            payload.update(source_multiview_job_id=mv_id, edit_view_prompts=edits)
    else:
        payload.update({k: v for k, v in (("prompt", prompt), ("image_url", image_url), ("image_urls", image_urls), ("source_multiview_job_id", mv_id)) if v is not None})
        validate_generation_xor(payload)

    if model == "hunyuan-3d-3.1":
        if image_urls or mv_id is not None:
            raise _invalid("hunyuan-3d-3.1 supports only prompt or single image_url.")
        payload.update(generate_type=_choice(opts.get("generate_type") or "Normal", ("Normal", "Geometry"), "generate_type"), enable_pbr=bool(opts.get("enable_pbr", False)))
        if opts.get("face_count") is not None:
            _put_int(payload, opts, "face_count", 40_000, 1_500_000)
        else:
            _put_choice(payload, opts, "quality", ("fast", "medium", "quality", "max", "ultra"))
    elif model == "meshy-v7.1":
        mtype = _choice(opts.get("model_type") or "standard", ("standard", "lowpoly", "smart-topology"), "model_type")
        if mv_id is not None or (prompt and len(prompt) > 600) or (image_urls and (len(image_urls) > 4 or mtype != "standard")):
            raise _invalid("meshy-v7.1 requires prompt<=600 or 1..4 images with model_type='standard'.")
        topo, poly = str(opts.get("topology") or "triangle"), opts.get("polycount")
        if mtype == "lowpoly":
            if poly is not None or topo != "triangle":
                raise _invalid("meshy-v7.1 lowpoly forbids polycount and quad topology.")
        else:
            payload["topology"] = _choice(topo, ("triangle", "quad"), "topology")
            _put_int(payload, opts, "polycount", 100, 15_000 if mtype == "smart-topology" else 300_000)
        tex, pbr, rig, anim = bool(opts.get("texture", True)), bool(opts.get("enable_pbr", False)), bool(opts.get("enable_rigging", False)), bool(opts.get("enable_animation", False))
        if (pbr and not tex) or (anim and not rig):
            raise _invalid("meshy-v7.1 enable_pbr requires texture=True; enable_animation requires enable_rigging=True.")
        payload.update(model_type=mtype, texture=tex, enable_pbr=pbr, enable_rigging=rig, **({"enable_animation": True, "animation_action_id": _int_range(opts.get("animation_action_id", 92), 0, 696, "animation_action_id")} if anim else {}))
    elif is_tripo:
        gen_parts, smart_lp = bool(opts.get("generate_parts", False)), bool(opts.get("smart_low_poly", False))
        topo, geo_q = _choice(opts.get("topology") or "triangle", ("triangle", "quad"), "topology"), _choice(opts.get("geometry_quality") or "standard", ("standard", "detailed"), "geometry_quality")
        out_fmt, tex, pbr = _choice(opts.get("output_format") or "glb", ("glb", "fbx"), "output_format"), bool(opts.get("texture", True)), bool(opts.get("enable_pbr", False))
        if (model != "tripo-h3.1" and (gen_parts or smart_lp or geo_q != "standard")) or (model == "tripo-p1" and topo != "triangle"):
            raise _invalid(f"Unsupported Tripo topology/parts/geometry options for {model}.")
        if gen_parts:
            tex, pbr, topo, smart_lp, out_fmt = False, False, "triangle", False, "glb"
        if (out_fmt == "fbx" and not (model == "tripo-h3.1" and topo == "quad")) or (pbr and not tex):
            raise _invalid("Tripo fbx requires tripo-h3.1+quad, and enable_pbr requires texture=True.")
        payload.update(texture=tex, enable_pbr=pbr, output_format=out_fmt, **({"texture_quality": _choice(opts.get("texture_quality") or "standard", _TEX_QUAL, "texture_quality")} if tex else {}), **({"topology": topo} if model in ("tripo-p2", "tripo-h3.1") else {}))
        if model == "tripo-h3.1":
            payload.update(geometry_quality=geo_q, smart_low_poly=smart_lp, generate_parts=gen_parts, **({"auto_size": True} if opts.get("auto_size") else {}), **({"delight": True} if (opts.get("delight") and tex) else {}))
        if neg := (opts.get("negative_prompt") or "").strip():
            if len(neg) > 255:
                raise _invalid("Tripo negative_prompt must be <= 255 characters.")
            payload["negative_prompt"] = neg
        _put_choice(payload, opts, "orientation", ("default", "align_image"))
        _put_choice(payload, opts, "export_orientation", _AXES)
        _put_int(payload, opts, "model_seed", 0, 2_147_483_647)
        lo = (500 if smart_lp else 1_000) if model == "tripo-h3.1" else (48 if model == "tripo-p2" else 50)
        hi = (((10_000 if topo == "quad" else 20_000) if smart_lp else (150_000 if topo == "quad" else 2_000_000)) if model == "tripo-h3.1" else ((25_000 if topo == "quad" else 50_000) if model == "tripo-p2" else 20_000))
        _put_int(payload, opts, "polycount", lo, hi)
    elif model == "rodin-v2.5":
        if mv_id is not None or (image_urls and len(image_urls) > 5):
            raise _invalid("rodin-v2.5 supports prompt or 1..5 images (no source_multiview_job_id).")
        variant = _choice(opts.get("variant") or "standard", ("standard", "fast"), "variant")
        if (tier := opts.get("tier")) is not None:
            _choice(tier, RODIN_FAST_TIERS if variant == "fast" else RODIN_STANDARD_TIERS, "tier")
        eff_tier = tier or RODIN_DEFAULT_TIER[variant]
        mesh_opt = _choice(opts.get("quality_mesh_option") or "Auto", rodin_allowed_mesh_options(variant, eff_tier), "quality_mesh_option")
        high_pack, is_micro = bool(opts.get("high_pack", False)), bool(opts.get("is_micro", False))
        if (high_pack and (variant == "fast" or mesh_opt.endswith("Quad"))) or (is_micro and eff_tier != "Gen-2.5-Extreme-High"):
            raise _invalid("rodin-v2.5 high_pack/is_micro constraint violated.")
        payload.update(variant=variant, quality_mesh_option=mesh_opt, high_pack=high_pack, is_micro=is_micro, output_format=_choice(opts.get("output_format") or "glb", ("glb", "fbx", "obj", "usdz", "stl"), "output_format"), enable_pbr=bool(opts.get("enable_pbr", True)), tpose=bool(opts.get("tpose", False)), hd_texture=bool(opts.get("hd_texture", False)), texture_delight=bool(opts.get("texture_delight", False)), enable_creative_mode=bool(opts.get("enable_creative_mode", False)), **({"tier": tier} if tier is not None else {}))
        _put_choice(payload, opts, "texture_mode", ("legacy", "extreme-low", "low", "medium", "high"))
        _put_int(payload, opts, "seed", 0, 65535)
    elif model in ("tripo-splat", "idol"):
        if not image_url or str(opts.get("output_format") or "glb") != "glb":
            raise _invalid(f"{model} requires a single image_url and output_format='glb'.")
        payload["output_format"] = "glb"
        if model == "tripo-splat":
            _put_int(payload, opts, "model_seed", 0, 2_147_483_647)
        else:
            payload.update(image_type=_choice(opts.get("image_type") or "synthetic", ("synthetic", "real"), "image_type"), num_gaussians=_int_range(opts.get("num_gaussians", 262_144), 32_768, 1_048_576, "num_gaussians"), num_inference_steps=_int_range(opts.get("num_inference_steps", 50), 1, 50, "num_inference_steps"), guidance_scale=_float_range(opts.get("guidance_scale", 3.0), 0.0, 10.0, "guidance_scale"), **({"enable_safety_checker": bool(opts["enable_safety_checker"])} if opts.get("enable_safety_checker") is not None else {}))
            _put_int(payload, opts, "seed", 0, 2_147_483_647)

    if opts.get("max_cost_rub") is not None:
        payload["max_cost_rub"] = round(float(opts["max_cost_rub"]), 2)
    return payload


def build_process_payload(operation: str, **opts: Any) -> dict[str, Any]:
    """Build a validated ``/models3d/process/{estimate,generations}`` payload."""
    for typo_key, hint in _FORBIDDEN_PROCESS_TYPOS.items():
        if typo_key in opts and opts[typo_key] is not None:
            raise _invalid(hint)
    if operation not in PROCESS_OP_IDS:
        raise _invalid(f"Unsupported process operation {operation!r}.")
    job_id = int(raw_src) if (raw_src := opts.get("source_job_id")) not in (None, 0, False, "") else None
    model_url, image_url, raw_urls = ensure_bare_upload_key(opts.get("model_url"), "model_url"), ensure_bare_upload_key(opts.get("image_url"), "image_url"), opts.get("image_urls")
    if image_url and raw_urls:
        raise _invalid("Provide either image_url or image_urls, not both.")
    payload: dict[str, Any] = {"operation": operation, **({"source_job_id": job_id} if job_id is not None else {}), **({"model_url": model_url} if model_url else {})}
    if operation == "smartsegment":
        payload["seg_type"] = (seg_type := _choice(opts.get("seg_type") or "model", ("model", "image"), "seg_type"))
        if seg_type == "image" and image_url:
            payload["image_url"] = image_url
        elif seg_type == "model" and model_url and not model_url.lower().endswith(".glb"):
            raise _invalid("smartsegment with seg_type='model' requires a .glb mesh.")
    validate_process_source(payload)
    if operation in ("rig", "rig_and_retarget") and model_url and model_url.lower().endswith((".gltf", ".zip")):
        raise _invalid(f"{operation} forbids .gltf and .zip; export binary .glb instead.")

    if operation == "texture":
        tmodel, tprompt = _choice(opts.get("texture_model") or "tripo", ("tripo", "meshy-v7-retexture", "meshy-n1-retexture"), "texture_model"), (opts.get("texture_prompt") or "").strip() or None
        payload.update(texture_model=tmodel, enable_pbr=bool(opts.get("enable_pbr", True)))
        if tmodel == "tripo":
            pos_urls = normalize_tripo_view_keys(raw_urls, require_front=not bool(tprompt), min_filled=1)
            if not (tprompt or image_url or pos_urls):
                raise _invalid("Tripo texture requires texture_prompt, image_url, or image_urls.")
            payload.update(texture_quality=_choice(opts.get("texture_quality") or "standard", _TEX_QUAL, "texture_quality"), bake=bool(opts.get("bake", True)), **({"texture_prompt": tprompt} if tprompt else {}), **({"image_url": image_url} if image_url else ({"image_urls": pos_urls} if pos_urls else {})), **({"delight": bool(opts["delight"])} if opts.get("delight") is not None else {}), **({"texture_seed": int(opts["texture_seed"])} if opts.get("texture_seed") is not None else {}))
            _put_choice(payload, opts, "texture_alignment", ("original_image", "geometry"))
        elif tmodel == "meshy-v7-retexture":
            comp_urls = clean_upload_key_list(raw_urls, "image_urls")
            single_img = image_url or (comp_urls[0] if comp_urls and len(comp_urls) == 1 else None)
            if (comp_urls and len(comp_urls) > 1) or not (tprompt or single_img) or (tprompt and len(tprompt) > 800):
                raise _invalid("meshy-v7-retexture requires prompt<=800 and/or at most 1 style image.")
            payload.update(texture_resolution=_choice(opts.get("texture_resolution") or "2k", ("2k", "4k", "8k"), "texture_resolution"), enable_original_uv=bool(opts.get("enable_original_uv", True)), **({"texture_prompt": tprompt} if tprompt else {}), **({"image_url": single_img} if single_img else {}))
        else:
            comp_urls = clean_upload_key_list(raw_urls, "image_urls")
            if not (image_url or comp_urls) or (comp_urls and len(comp_urls) > 4):
                raise _invalid("meshy-n1-retexture requires 1..4 style reference images.")
            payload.update(enable_original_uv=bool(opts.get("enable_original_uv", True)), **({"image_url": image_url} if image_url else {"image_urls": comp_urls}))
    elif operation == "decimate":
        dmodel, quad, smart_uv = _choice(opts.get("decimate_model") or "v2.0", ("v1.0", "v2.0", "v3.0"), "decimate_model"), bool(opts.get("quad", False)), bool(opts.get("smart_uv", False))
        if smart_uv and dmodel != "v3.0":
            raise _invalid("decimate smart_uv=True requires decimate_model='v3.0'.")
        _put_int(payload, opts, "face_limit", 500 if dmodel == "v3.0" else 48, (10_000 if quad else 20_000) if dmodel == "v3.0" else 500_000)
        payload.update(decimate_model=dmodel, quad=quad, bake=bool(opts.get("bake", True)), **({"smart_uv": smart_uv} if dmodel == "v3.0" else {}))
    elif operation == "uv":
        payload.update(bake=bool(opts.get("bake", True)), export_orientation=_choice(opts.get("export_orientation") or "+x", _AXES, "export_orientation"), compress_geometry=bool(opts.get("compress_geometry", False)))
    elif operation == "segment":
        payload["segment_model"] = "v2.0-20260430"
        payload.update({"image_url": image_url} if image_url else {"segmentation_granularity": _choice(opts.get("segmentation_granularity") or "balanced", ("simple", "balanced", "detailed"), "segmentation_granularity"), "split_by_connectivity": bool(opts.get("split_by_connectivity", True))})
    elif operation == "smartsegment":
        payload["smart_granularity"] = _choice(opts.get("smart_granularity") or "medium", ("coarse", "medium", "fine"), "smart_granularity")
        if hint := (opts.get("hint") or "").strip():
            if len(hint) > 512:
                raise _invalid("smartsegment hint must be <= 512 characters.")
            payload["hint"] = hint
        if (tr := opts.get("transform")) is not None:
            if not isinstance(tr, Sequence) or len(tr) != 16:
                raise _invalid("smartsegment transform must contain 16 floats.")
            payload["transform"] = [float(x) for x in tr]
    elif operation == "complete":
        payload["completion_mode"] = _choice(opts.get("completion_mode") or "ai_completion", ("ai_completion", "quick_cap"), "completion_mode")
        if parts := [str(p).strip() for p in (opts.get("part_names") or []) if str(p).strip()]:
            payload["part_names"] = parts
    elif operation in ("rig", "rig_and_retarget"):
        rmodel = _choice(opts.get("rig_model") or "v3.0-20260909", ("v3.0-20260909", "v2.5-20260210", "v1.0-20240301"), "rig_model")
        rtype = _choice(opts.get("rig_type") or "biped", ("biped", "quadruped", "hexapod", "octopod", "avian", "serpentine", "aquatic", "others"), "rig_type")
        rspec, out_fmt = _choice(opts.get("rig_spec") or "tripo", ("tripo", "mixamo", "unity", "unreal", "vrm", "actorcore"), "rig_spec"), _choice(opts.get("out_format") or "glb", ("glb", "fbx"), "out_format")
        if (rmodel == "v1.0-20240301" and rtype != "biped") or (rtype == "others" and rmodel != "v3.0-20260909") or (rtype == "biped" and rspec in ("actorcore", "unity", "unreal", "vrm") and rmodel != "v3.0-20260909"):
            raise _invalid("Incompatible rig_model / rig_type / rig_spec combination.")
        payload.update(rig_model=rmodel, rig_type=rtype, rig_spec=rspec, out_format=out_fmt)
        if operation == "rig_and_retarget":
            anims = [str(a).strip() for a in (opts.get("animations") or []) if str(a).strip()]
            if rtype in ("avian", "others") or not (1 <= len(anims) <= 5):
                raise _invalid("rig_and_retarget requires 1..5 animations on a retargetable rig_type.")
            payload.update(animations=anims, animate_in_place=bool(opts.get("animate_in_place", False)), export_with_geometry=bool(opts.get("export_with_geometry", True)))
    elif operation == "retarget":
        if not (1 <= len(anims := [str(a).strip() for a in (opts.get("animations") or []) if str(a).strip()]) <= 5):
            raise _invalid("retarget requires 1..5 animation presets in 'animations'.")
        payload.update(animations=anims, out_format=_choice(opts.get("out_format") or "glb", ("glb", "fbx"), "out_format"), animate_in_place=bool(opts.get("animate_in_place", False)), export_with_geometry=bool(opts.get("export_with_geometry", True)))
    elif operation == "convert":
        cfmt, quad, sym, vcol = _choice(opts.get("convert_format") or "FBX", ("GLTF", "FBX", "USDZ", "OBJ", "STL", "3MF"), "convert_format"), bool(opts.get("quad", False)), bool(opts.get("force_symmetry", False)), bool(opts.get("export_vertex_colors", False))
        if (sym and not quad) or (vcol and cfmt not in ("OBJ", "GLTF")):
            raise _invalid("convert force_symmetry requires quad=True, and export_vertex_colors requires OBJ/GLTF.")
        payload.update(convert_format=cfmt, fbx_preset=_choice(opts.get("fbx_preset") or "blender", ("blender", "3dsmax", "mixamo", "bake_scale"), "fbx_preset"), quad=quad, force_symmetry=sym, flatten_bottom=bool(opts.get("flatten_bottom", False)), pack_uv=bool(opts.get("pack_uv", False)), export_vertex_colors=vcol, pivot_to_center_bottom=bool(opts.get("pivot_to_center_bottom", False)), scale_factor=_float_range(opts.get("scale_factor", 1.0), 0.01, 1000.0, "scale_factor"), texture_size=_int_range(opts.get("texture_size", 4096), 128, 8192, "texture_size"), texture_format=_choice(opts.get("texture_format") or "JPEG", ("JPEG", "PNG", "WEBP", "BMP", "DPX", "HDR", "OPEN_EXR", "TARGA", "TIFF"), "texture_format"), bake=bool(opts.get("bake", True)), export_orientation=_choice(opts.get("export_orientation") or "+x", _AXES, "export_orientation"))
        _put_int(payload, opts, "face_limit", 48, 2_000_000)

    if opts.get("max_cost_rub") is not None:
        payload["max_cost_rub"] = round(float(opts["max_cost_rub"]), 2)
    return payload
