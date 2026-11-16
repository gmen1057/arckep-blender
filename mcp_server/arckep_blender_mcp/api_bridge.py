# SPDX-License-Identifier: MIT
"""ArcKep ``/api/v1`` HTTP bridge, upload cache, asset downloader, and 3-mode RUB Budget Guard."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import shutil
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .blender_rpc import BlenderBridgeError, BlenderRpcClient
from .payload_builders import (
    GENERATION_MODEL_IDS,
    MAX_IMAGE_UPLOAD_BYTES,
    MAX_MESH_UPLOAD_BYTES,
    MESHY_RETEXTURE_MAX_BYTES,
    PROCESS_OP_IDS,
    ArcKepApiError,
    ArcKepValidationError,
    build_generation_payload,
    build_process_payload,
    validate_generation_xor,
    validate_process_source,
)

DEFAULT_API_BASE_URL = "https://arckep.ru/api/v1"
USER_AGENT = "ArcKep-Blender-MCP/0.1.0"
MAX_IDEMPOTENCY_KEY_LEN = 128
BUDGET_MODE_THRESHOLD = "threshold"
BUDGET_MODE_CONFIRM_ALWAYS = "confirm_always"
BUDGET_MODE_UNLIMITED = "unlimited"
BUDGET_MODES: tuple[str, ...] = (BUDGET_MODE_THRESHOLD, BUDGET_MODE_CONFIRM_ALWAYS, BUDGET_MODE_UNLIMITED)
DEFAULT_MAX_AUTO_COST_RUB = 150.0

_MESH_MIMES = {".glb": "model/gltf-binary", ".gltf": "model/gltf+json", ".fbx": "application/octet-stream", ".obj": "text/plain", ".stl": "model/stl"}
_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".tif", ".heic", ".heif"})
_CACHE_LOCK = threading.Lock()
_UPLOAD_STAT_CACHE: dict[tuple[str, int, int, str, str], dict[str, Any]] = {}
_UPLOAD_HASH_CACHE: dict[tuple[str, str, str, str], dict[str, Any]] = {}
_SESSION_DOWNLOAD_DIR: Path | None = None


def clear_upload_cache() -> None:
    with _CACHE_LOCK:
        _UPLOAD_STAT_CACHE.clear()
        _UPLOAD_HASH_CACHE.clear()


def _ensure_download_dir() -> Path:
    global _SESSION_DOWNLOAD_DIR
    with _CACHE_LOCK:
        if _SESSION_DOWNLOAD_DIR is None:
            _SESSION_DOWNLOAD_DIR = Path(tempfile.mkdtemp(prefix="arckep_mcp_assets_"))
        _SESSION_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        return _SESSION_DOWNLOAD_DIR


@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool
    requires_confirmation: bool
    effective_max_cost_rub: float | None
    message: str


@dataclass(frozen=True)
class RuntimePolicy:
    api_key: str
    api_base_url: str
    budget_mode: str
    max_auto_cost_rub: float
    budget_source: str
    poll_interval_sec: float


def _parse_decimal(value: float | int | str | Decimal, field_name: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a non-negative number, got bool")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{field_name} must be a valid decimal amount: {value!r}") from exc
    if not parsed.is_finite() or parsed < Decimal("0"):
        raise ValueError(f"{field_name} must be finite and >= 0, got {value!r}")
    return parsed


def round_rub(value: float | int | str | Decimal) -> float:
    return round(float(_parse_decimal(value, "cost_rub")), 2)


def evaluate_budget(cost_rub: float | int | str | Decimal, mode: str = BUDGET_MODE_THRESHOLD, max_auto_cost_rub: float | int | str | Decimal = DEFAULT_MAX_AUTO_COST_RUB, confirmed_max_cost_rub: float | int | str | Decimal | None = None) -> BudgetDecision:
    """Evaluate 3-mode RUB Budget Guard (``threshold`` / ``confirm_always`` / ``unlimited``)."""
    if mode not in BUDGET_MODES:
        raise ValueError(f"Unsupported budget mode {mode!r}; expected one of {BUDGET_MODES}")
    cost = _parse_decimal(cost_rub, "cost_rub")
    conf = _parse_decimal(confirmed_max_cost_rub, "confirmed_max_cost_rub") if confirmed_max_cost_rub is not None else None
    if mode == BUDGET_MODE_UNLIMITED:
        return BudgetDecision(True, False, float(conf) if conf is not None else None, f"Allowed in unlimited mode ({cost:.2f} RUB).")
    if mode == BUDGET_MODE_CONFIRM_ALWAYS:
        if cost == Decimal("0"):
            return BudgetDecision(True, False, float(conf) if conf is not None else 0.0, "Free operation (0.00 RUB) allowed.")
        if conf is not None and conf >= cost:
            return BudgetDecision(True, False, float(conf), f"Confirmed {cost:.2f} RUB within cap {conf:.2f} RUB.")
        return BudgetDecision(False, True, None, f"Confirmation required: estimated cost is {cost:.2f} RUB (mode='confirm_always'; ask the user and re-call with confirmed_max_cost_rub={cost:.2f}).")
    cap = _parse_decimal(max_auto_cost_rub, "max_auto_cost_rub")
    if conf is not None:
        return BudgetDecision(True, False, float(conf), f"Confirmed {cost:.2f} RUB within cap {conf:.2f} RUB.") if conf >= cost else BudgetDecision(False, True, None, f"Confirmation required: estimated cost {cost:.2f} RUB exceeds confirmed_max_cost_rub {conf:.2f} RUB.")
    return BudgetDecision(True, False, float(cap), f"Auto-approved {cost:.2f} RUB (within {cap:.2f} RUB threshold).") if cost <= cap else BudgetDecision(False, True, None, f"Confirmation required: estimated cost {cost:.2f} RUB exceeds auto-approve threshold {cap:.2f} RUB (ask the user and re-call with confirmed_max_cost_rub={cost:.2f}).")


def build_confirmation_required_response(cost_str: str, mode: str, max_auto_cost_rub: float, message: str) -> dict[str, Any]:
    cost_val = round_rub(cost_str)
    return {"ok": False, "status": "confirmation_required", "error_code": "CONFIRMATION_REQUIRED", "budget_mode": mode, "estimated_cost_rub": str(cost_str), "cost_rub": cost_val, "max_auto_cost_rub": round_rub(max_auto_cost_rub), "required_confirmed_max_cost_rub": cost_val, "message": message}


def resolve_runtime_policy(rpc: BlenderRpcClient | None = None, ping_info: Mapping[str, Any] | None = None) -> RuntimePolicy:
    """Resolve API URL and Budget Guard from env vars, falling back to live Blender ``ping()``."""
    api_key, env_base = os.environ.get("ARCKEP_API_KEY", "").strip(), os.environ.get("ARCKEP_API_BASE_URL", "").strip().rstrip("/")
    env_mode, env_cap_raw = os.environ.get("ARCKEP_BUDGET_MODE", "").strip(), os.environ.get("ARCKEP_MAX_AUTO_COST_RUB", "").strip()
    poll_sec = max(0.02, float(raw_p)) if (raw_p := os.environ.get("ARCKEP_POLL_INTERVAL_SEC", "").strip()) else 1.5
    live = dict(ping_info) if ping_info is not None else None
    if live is None and (not env_base or not env_mode):
        probe = rpc or BlenderRpcClient(timeout=3.0)
        try:
            live = probe.ping()
        except (BlenderBridgeError, OSError):
            live = None
    base_url = env_base or (str(live["api_base_url"]).strip().rstrip("/") if live and str(live.get("api_base_url") or "").strip() else "") or DEFAULT_API_BASE_URL
    if env_mode:
        if env_mode not in BUDGET_MODES:
            raise ArcKepValidationError(400, "INVALID_BUDGET_MODE", f"ARCKEP_BUDGET_MODE must be one of {BUDGET_MODES}.")
        cap, mode, source = float(_parse_decimal(env_cap_raw or DEFAULT_MAX_AUTO_COST_RUB, "ARCKEP_MAX_AUTO_COST_RUB")), env_mode, "env"
    elif live and live.get("budget_mode") in BUDGET_MODES:
        mode, source, cap = str(live["budget_mode"]), "blender", float(_parse_decimal(live.get("max_auto_cost_rub", DEFAULT_MAX_AUTO_COST_RUB), "max_auto_cost_rub"))
    else:
        mode, source, cap = BUDGET_MODE_THRESHOLD, "default", float(_parse_decimal(env_cap_raw or DEFAULT_MAX_AUTO_COST_RUB, "ARCKEP_MAX_AUTO_COST_RUB"))
    return RuntimePolicy(api_key, base_url, mode, round(cap, 2), source, poll_sec)


def parse_error_payload(status_code: int, raw_body: bytes, reason: str) -> ArcKepApiError:
    """Parse both top-level APIError JSON and ``detail``-wrapped idempotency errors."""
    text = raw_body.decode("utf-8", errors="replace").strip()
    if text:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            if isinstance(data.get("error_code"), str) and isinstance(data.get("message"), str):
                det = data.get("details")
                return ArcKepApiError(status_code, data["error_code"], data["message"], det if isinstance(det, dict) else None)
            if isinstance(detail := data.get("detail"), dict) and isinstance(detail.get("error_code"), str):
                det = detail.get("details")
                return ArcKepApiError(status_code, detail["error_code"], str(detail.get("message") or reason), det if isinstance(det, dict) else None)
            if detail is not None:
                msg = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)
                return ArcKepApiError(status_code, "VALIDATION_ERROR" if status_code in (400, 422) else f"HTTP_{status_code}", msg, detail if isinstance(detail, dict) else {"detail": detail})
    return ArcKepApiError(status_code, f"HTTP_{status_code}", text or reason or f"HTTP {status_code}")


class ArcKepCloudBridge:
    """HTTP client + upload cache + scene/API orchestrator for ``/api/v1/models3d``."""

    def __init__(self, rpc: BlenderRpcClient | None = None, policy: RuntimePolicy | None = None, timeout: float = 60.0) -> None:
        self.rpc, self.policy, self.timeout = rpc, policy or resolve_runtime_policy(rpc), timeout

    def require_rpc(self) -> BlenderRpcClient:
        if self.rpc is None:
            self.rpc = BlenderRpcClient()
        return self.rpc

    def _auth_headers(self, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        if not self.policy.api_key:
            raise ArcKepValidationError(401, "MISSING_API_KEY", "ARCKEP_API_KEY is not set. Configure ARCKEP_API_KEY=ark_live_... in your MCP environment.")
        return {"Authorization": f"Bearer {self.policy.api_key}", "Accept": "application/json", "User-Agent": USER_AGENT, **(extra or {})}

    def request_json(self, method: str, path: str, *, payload: Mapping[str, Any] | None = None, body_bytes: bytes | None = None, headers: Mapping[str, str] | None = None) -> dict[str, Any]:
        req_headers = self._auth_headers(headers)
        data = json.dumps(dict(payload)).encode("utf-8") if payload is not None else body_bytes
        if payload is not None:
            req_headers.setdefault("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(urllib.request.Request(f"{self.policy.api_base_url}{path}", data=data, headers=req_headers, method=method), timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raise parse_error_payload(exc.code, exc.read(), str(exc.reason)) from exc
        except urllib.error.URLError as exc:
            raise ArcKepApiError(0, "NETWORK_ERROR", f"ArcKep API connection error: {exc.reason}") from exc
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ArcKepApiError(502, "INVALID_JSON", "Server returned non-JSON response.") from exc
        if not isinstance(parsed, dict):
            raise ArcKepApiError(502, "INVALID_RESPONSE", "Expected JSON object response.")
        return parsed

    def upload_file_cached(self, filepath: str | Path, *, max_bytes: int | None = None) -> dict[str, Any]:
        """Upload local mesh/image to ``POST /uploads`` with byte-cap checks and session caching."""
        self._auth_headers()
        path = Path(filepath).expanduser().resolve()
        if not path.is_file() or (st := path.stat()).st_size == 0:
            raise ArcKepValidationError(400, "FILE_NOT_FOUND" if not path.is_file() else "EMPTY_FILE", f"Missing or empty file: {path}")
        ext, ct = path.suffix.lower(), _MESH_MIMES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        limit = max_bytes if max_bytes is not None else (MAX_MESH_UPLOAD_BYTES if ext in _MESH_MIMES else (MAX_IMAGE_UPLOAD_BYTES if (ct.startswith("image/") or ext in _IMAGE_EXTS) else None))
        if limit is not None and st.st_size > limit:
            raise ArcKepValidationError(413, "FILE_TOO_LARGE", f"File '{path.name}' is {st.st_size / (1024 * 1024):.1f} MiB (limit: {limit / (1024 * 1024):.0f} MiB).", {"max_bytes": limit, "size_bytes": st.st_size})
        token_fp = hashlib.sha256(self.policy.api_key.encode("utf-8")).hexdigest()[:16]
        stat_key = (str(path), st.st_size, st.st_mtime_ns, self.policy.api_base_url, token_fp)
        with _CACHE_LOCK:
            if (hit := _UPLOAD_STAT_CACHE.get(stat_key)) is not None:
                return dict(hit)
        raw_bytes = path.read_bytes()
        hash_key = (hashlib.sha256(raw_bytes).hexdigest(), ext, self.policy.api_base_url, token_fp)
        with _CACHE_LOCK:
            if (hit_h := _UPLOAD_HASH_CACHE.get(hash_key)) is not None:
                _UPLOAD_STAT_CACHE[stat_key] = dict(hit_h)
                return dict(hit_h)
        boundary, safe_name = f"----ArcKepBoundary{uuid.uuid4().hex}", path.name.replace('"', "_").replace("\r", "").replace("\n", "")
        body = f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{safe_name}"\r\nContent-Type: {ct}\r\n\r\n'.encode("utf-8") + raw_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")
        uploaded = self.request_json("POST", "/uploads", body_bytes=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with _CACHE_LOCK:
            _UPLOAD_STAT_CACHE[stat_key] = _UPLOAD_HASH_CACHE[hash_key] = dict(uploaded)
        return dict(uploaded)

    def download_file(self, url: str, dest_path: str | Path) -> Path:
        return self._stream_get(url, dest_path, headers={"User-Agent": USER_AGENT})

    def download_stl(self, job_id: int, dest_path: str | Path | None = None, *, print_format: str = "stl", target_size_mm: float = 100.0, scale_axis: str = "max", z_up: bool = True, place_on_bed: bool = True) -> Path:
        if print_format not in ("stl", "3mf") or scale_axis not in ("max", "height", "width", "depth") or not (5.0 <= float(target_size_mm) <= 2000.0):
            raise ArcKepValidationError(400, "INVALID_PARAMETER", "print_format must be stl/3mf, scale_axis in max/height/width/depth, target_size_mm in 5..2000.")
        out = Path(dest_path) if dest_path else (_ensure_download_dir() / f"print-{int(job_id)}-{int(round(target_size_mm))}mm.{print_format}")
        q = urllib.parse.urlencode({"print_format": print_format, "scale_axis": scale_axis, "target_size_mm": str(float(target_size_mm)), "z_up": "true" if z_up else "false", "place_on_bed": "true" if place_on_bed else "false"})
        return self._stream_get(f"{self.policy.api_base_url}/models3d/generations/{int(job_id)}/download/stl?{q}", out, headers=self._auth_headers({"Accept": "*/*"}))

    def _stream_get(self, url: str, dest_path: str | Path, headers: Mapping[str, str]) -> Path:
        target = Path(dest_path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=dict(headers), method="GET"), timeout=self.timeout) as resp, target.open("wb") as out:
                shutil.copyfileobj(resp, out)
        except urllib.error.HTTPError as exc:
            raise parse_error_payload(exc.code, exc.read(), str(exc.reason)) from exc
        except urllib.error.URLError as exc:
            raise ArcKepApiError(0, "NETWORK_ERROR", f"Asset download failed: {exc.reason}") from exc
        return target

    def poll_generation(self, job_id: int, timeout_sec: float = 900.0) -> dict[str, Any]:
        deadline = time.monotonic() + max(1.0, float(timeout_sec))
        while True:
            job = self.request_json("GET", f"/models3d/generations/{int(job_id)}")
            if (status := str(job.get("status") or "")) == "completed":
                return job
            if status == "failed":
                raise ArcKepApiError(500, "JOB_FAILED", str(job.get("error") or f"3D job #{job_id} failed."), {"job_id": int(job_id), "job": job})
            if time.monotonic() >= deadline:
                raise ArcKepApiError(504, "POLL_TIMEOUT", f"Timed out polling job #{job_id} (last status: {status!r}).", {"job_id": int(job_id), "status": status})
            time.sleep(self.policy.poll_interval_sec)

    def download_completed_asset(self, job: Mapping[str, Any]) -> dict[str, Any]:
        jid, dl_dir = int(job["id"]), _ensure_download_dir()
        output = job.get("output") if isinstance(job.get("output"), dict) else {}
        if isinstance(mv := output.get("multiview_urls"), dict) and mv:
            mv_dir = dl_dir / f"multiview-{jid}"
            mv_dir.mkdir(parents=True, exist_ok=True)
            views = {k: str(self.download_file(str(mv[k]).strip(), mv_dir / f"{k}.png")) for k in ("front", "left", "back", "right") if str(mv.get(k) or "").strip()}
            manifest = mv_dir / "multiview_manifest.json"
            manifest.write_text(json.dumps({"job_id": jid, "format": "multiview", "views": views}), encoding="utf-8")
            return {"kind": "multiview", "local_path": None, "manifest_path": str(manifest), "multiview_paths": views}
        for key, suffix in (("point_cloud_glb_url", "pointcloud.glb"), ("ply_url", "pointcloud.ply")):
            if str(output.get(key) or "").strip():
                return {"kind": "mesh", "local_path": str(self.download_file(str(output[key]).strip(), dl_dir / f"{jid}-{suffix}"))}
        if not (asset_url := str(output.get("file_url") or output.get("glb_url") or output.get("splat_url") or "").strip()):
            return {"kind": "none", "local_path": None}
        raw_name = os.path.basename(str(output.get("file_name") or "").strip())
        if not raw_name or raw_name in (".", ".."):
            ext, fmt = Path(urllib.parse.urlsplit(asset_url).path).suffix.lower(), str(output.get("format") or "glb").strip().lstrip(".") or "glb"
            raw_name = f"arckep-{jid}-obj.zip" if ext == ".zip" else f"arckep-{jid}.{fmt}"
        return {"kind": "mesh", "local_path": str(self.download_file(asset_url, dl_dir / (raw_name if raw_name.startswith(f"{jid}-") else f"{jid}-{raw_name}")))}

    def prepare_generation_payload(self, model: str, *, prompt: str | None = None, image_path: str | None = None, image_paths: Sequence[str] | None = None, viewport_capture_mode: str | None = None, viewport_resolution: int = 1024, source_multiview_job_id: int | None = None, options: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if model in ("meshy-v7-retexture", "meshy-n1-retexture") or model not in GENERATION_MODEL_IDS:
            raise ArcKepValidationError(400, "INVALID_PARAMETER", f"Model {model!r} is only valid via /models3d/process/* with operation='texture'." if model in ("meshy-v7-retexture", "meshy-n1-retexture") else f"Unsupported 3D generation model {model!r}.")
        opts = {k: v for k, v in (options or {}).items() if k != "max_cost_rub"}
        eff_prompt, eff_mv = (prompt if prompt is not None else opts.get("prompt")), (source_multiview_job_id if source_multiview_job_id is not None else opts.get("source_multiview_job_id"))
        has_vp = bool(viewport_capture_mode and str(viewport_capture_mode).strip())
        if sum((bool((eff_prompt or "").strip()), bool((image_path or "").strip() or (opts.get("image_url") or "").strip()), bool(image_paths) or bool(opts.get("image_urls")), has_vp, eff_mv not in (None, 0, False, ""))) != 1:
            raise ArcKepValidationError(400, "INVALID_PARAMETER", "Provide exactly one input source: prompt, image_path, image_paths, viewport_capture_mode, or source_multiview_job_id.")
        if has_vp:
            cap = self.require_rpc().capture_viewport(mode=str(viewport_capture_mode).strip(), resolution=int(viewport_resolution))
            opts["image_url"] = str(self.upload_file_cached(cap["filepath"])["key"])
        elif image_path and image_path.strip():
            opts["image_url"] = str(self.upload_file_cached(image_path.strip())["key"])
        elif image_paths:
            if model not in ("tripo-p1", "tripo-p2", "tripo-h3.1") and any(not str(p).strip() for p in image_paths):
                raise ArcKepValidationError(400, "INVALID_PARAMETER", f"{model} image_paths must not contain empty paths.")
            opts["image_urls"] = [str(self.upload_file_cached(str(p).strip())["key"]) if str(p).strip() else "" for p in image_paths]
        if prompt is not None:
            opts["prompt"] = prompt
        if source_multiview_job_id is not None:
            opts["source_multiview_job_id"] = source_multiview_job_id
        payload = build_generation_payload(model, **opts)
        validate_generation_xor(payload)
        return payload

    def prepare_process_payload(self, operation: str, *, object_name: str | None = None, source_job_id: int | None = None, mesh_path: str | None = None, use_object_job_id: bool = True, image_path: str | None = None, style_image_paths: Sequence[str] | None = None, options: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if operation not in PROCESS_OP_IDS:
            raise ArcKepValidationError(400, "INVALID_PARAMETER", f"Unsupported process operation {operation!r}.")
        opts = {k: v for k, v in (options or {}).items() if k != "max_cost_rub"}
        single_img = image_path or opts.pop("style_image_path", None) or opts.pop("image_path", None)
        multi_imgs = style_image_paths if style_image_paths is not None else opts.pop("style_image_paths", None)
        if single_img and multi_imgs:
            raise ArcKepValidationError(400, "INVALID_PARAMETER", "Provide either image_path/style_image_path or style_image_paths, not both.")
        obj_name, m_path, src_jid = (object_name or "").strip() or None, (mesh_path or "").strip() or None, (int(source_job_id) if source_job_id not in (None, 0, False, "") else None)
        seg_type, tmodel = str(opts.get("seg_type") or "model"), str(opts.get("texture_model") or "tripo")
        mesh_cap = MESHY_RETEXTURE_MAX_BYTES if (operation == "texture" and tmodel in ("meshy-v7-retexture", "meshy-n1-retexture")) else MAX_MESH_UPLOAD_BYTES
        if operation == "smartsegment" and seg_type == "image":
            if obj_name or src_jid is not None or m_path:
                raise ArcKepValidationError(400, "INVALID_PARAMETER", "smartsegment with seg_type='image' forbids mesh/job sources.")
        elif operation in ("retarget", "complete"):
            if m_path or (obj_name and src_jid is not None) or (not obj_name and src_jid is None and opts.get("source_job_id") is None):
                raise ArcKepValidationError(400, "INVALID_PARAMETER", f"Operation {operation!r} requires exactly one of object_name (with arckep_job_id) or source_job_id.")
            if obj_name:
                if (found := self.require_rpc().get_object_info(obj_name).get("arckep_job_id")) in (None, 0, False, ""):
                    raise ArcKepValidationError(400, "MISSING_SOURCE_JOB_ID", f"Blender object {obj_name!r} has no 'arckep_job_id'. Operation {operation!r} requires an ArcKep job.")
                opts["source_job_id"] = int(found)
            elif src_jid is not None:
                opts["source_job_id"] = src_jid
        else:
            cnt = sum((obj_name is not None, src_jid is not None, m_path is not None))
            if cnt > 1 or (cnt == 0 and not opts.get("model_url") and opts.get("source_job_id") is None):
                raise ArcKepValidationError(400, "INVALID_PARAMETER", "Provide exactly one mesh source: object_name, source_job_id, or mesh_path.")
            if src_jid is not None:
                opts["source_job_id"] = src_jid
            elif m_path is not None:
                ext = Path(m_path).suffix.lower()
                if (operation == "smartsegment" and ext != ".glb") or (operation in ("rig", "rig_and_retarget") and ext in (".gltf", ".zip")):
                    raise ArcKepValidationError(400, "INVALID_PARAMETER", f"Operation {operation!r} requires a binary .glb mesh (got {ext!r}).")
                opts["model_url"] = str(self.upload_file_cached(m_path, max_bytes=mesh_cap)["key"])
            elif obj_name is not None:
                found = self.require_rpc().get_object_info(obj_name).get("arckep_job_id")
                if use_object_job_id and found not in (None, 0, False, ""):
                    opts["source_job_id"] = int(found)
                else:
                    exp = self.require_rpc().export_selection_glb(names=[obj_name], max_bytes=mesh_cap)
                    opts["model_url"] = str(self.upload_file_cached(exp["filepath"], max_bytes=mesh_cap)["key"])
        if single_img and str(single_img).strip():
            opts["image_url"] = str(self.upload_file_cached(str(single_img).strip())["key"])
        elif multi_imgs:
            if operation == "texture" and tmodel == "tripo":
                up = [str(self.upload_file_cached(str(p).strip())["key"]) if str(p).strip() else "" for p in multi_imgs]
                opts["image_url" if (len(up) == 1 and up[0]) else "image_urls"] = up[0] if (len(up) == 1 and up[0]) else up
            else:
                if any(not str(p).strip() for p in multi_imgs):
                    raise ArcKepValidationError(400, "INVALID_PARAMETER", "style_image_paths must not contain empty paths.")
                up = [str(self.upload_file_cached(str(p).strip())["key"]) for p in multi_imgs]
                single_slot = len(up) == 1 and (operation != "texture" or tmodel == "meshy-v7-retexture")
                opts["image_url" if single_slot else "image_urls"] = up[0] if single_slot else up
        payload = build_process_payload(operation, **opts)
        validate_process_source(payload)
        return payload

    def submit_paid_job(self, path: str, payload: Mapping[str, Any], max_cost_rub: float | None) -> dict[str, Any]:
        body = {**payload, **({"max_cost_rub": round_rub(max_cost_rub)} if max_cost_rub is not None else {})}
        return self.request_json("POST", path, payload=body, headers={"Idempotency-Key": str(uuid.uuid4())[:MAX_IDEMPOTENCY_KEY_LEN]})
