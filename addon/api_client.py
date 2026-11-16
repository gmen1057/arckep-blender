# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure-stdlib HTTP client for ArcKep Public API ``/api/v1``.

Zero third-party dependencies so the module executes natively inside stock
Blender 4.2 LTS – 5.x Python as well as standalone test environments.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import shutil
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .api_validation import (
    IMAGE_EXTENSIONS,
    MAX_IDEMPOTENCY_KEY_LEN,
    MAX_IMAGE_UPLOAD_BYTES,
    MAX_MESH_UPLOAD_BYTES,
    MESH_MIME_TYPES,
    ArcKepApiError,
    ArcKepValidationError,
    encode_multipart_file,
    parse_error_payload,
    validate_generation_xor,
    validate_process_source,
    validate_rig_check_xor,
)

DEFAULT_BASE_URL = "https://arckep.ru/api/v1"
USER_AGENT = "ArcKep-Blender-Addon/0.1.0"

_CACHE_LOCK = threading.Lock()
_UPLOAD_STAT_CACHE: dict[tuple[str, int, int, str, str], dict[str, Any]] = {}
_UPLOAD_HASH_CACHE: dict[tuple[str, str, str, str], dict[str, Any]] = {}


def clear_upload_cache() -> None:
    """Clear in-memory session cache of uploaded local files."""
    with _CACHE_LOCK:
        _UPLOAD_STAT_CACHE.clear()
        _UPLOAD_HASH_CACHE.clear()


__all__ = [
    "DEFAULT_BASE_URL",
    "MAX_IMAGE_UPLOAD_BYTES",
    "MAX_MESH_UPLOAD_BYTES",
    "ArcKepApiClient",
    "ArcKepApiError",
    "ArcKepValidationError",
    "clear_upload_cache",
    "validate_generation_xor",
    "validate_process_source",
    "validate_rig_check_xor",
]


class ArcKepApiClient:
    """Synchronous HTTP client for ArcKep ``/api/v1`` 3D, upload, and billing endpoints."""

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 60.0,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or DEFAULT_BASE_URL).strip().rstrip("/")
        self.timeout = timeout

    def _auth_headers(self, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        if not self.api_key:
            raise ArcKepValidationError(
                status_code=401,
                error_code="MISSING_API_KEY",
                message="ArcKep API key is missing. Configure ark_live_... in Add-on Preferences.",
            )
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        if extra:
            headers.update(extra)
        return headers

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, Any] | None = None,
        body_bytes: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        req_headers = self._auth_headers(headers)
        data = body_bytes
        if payload is not None:
            data = json.dumps(dict(payload)).encode("utf-8")
            req_headers.setdefault("Content-Type", "application/json")
        request = urllib.request.Request(url, data=data, headers=req_headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raise parse_error_payload(exc.code, exc.read(), str(exc.reason)) from exc
        except urllib.error.URLError as exc:
            raise ArcKepApiError(0, "NETWORK_ERROR", f"Connection error: {exc.reason}") from exc
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ArcKepApiError(502, "INVALID_JSON", "Server returned non-JSON response.") from exc
        if not isinstance(parsed, dict):
            raise ArcKepApiError(502, "INVALID_RESPONSE", "Expected JSON object response.")
        return parsed

    @staticmethod
    def _make_idempotency_key(key: str | None) -> str:
        resolved = (key or str(uuid.uuid4())).strip()
        if not resolved or len(resolved) > MAX_IDEMPOTENCY_KEY_LEN:
            raise ArcKepValidationError(
                400, "VALIDATION_ERROR", f"Idempotency-Key must be 1..{MAX_IDEMPOTENCY_KEY_LEN} chars."
            )
        return resolved

    def get_balance(self) -> dict[str, Any]:
        return self._request_json("GET", "/balance")

    def list_models(self) -> dict[str, Any]:
        return self._request_json("GET", "/models")

    def upload_file(
        self,
        filepath: str | Path,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> dict[str, Any]:
        """Upload a 3D mesh (<=150 MiB) or reference image (<=10 MiB) to ``POST /uploads``.

        IMPORTANT: Callers MUST pass ``result["key"]`` (the bare S3 storage key, NOT
        ``result["url"]``) into ``model_url``, ``image_url``, and ``image_urls`` of all
        ``/models3d/*`` requests so owned keys never expire and work when S3_ENABLED=False.
        """
        path = Path(filepath)
        if not path.is_file():
            raise ArcKepValidationError(400, "FILE_NOT_FOUND", f"File not found: {path}")
        size = path.stat().st_size
        if size == 0:
            raise ArcKepValidationError(400, "EMPTY_FILE", f"Cannot upload empty file: {path.name}")
        effective_name = filename or path.name
        ext = Path(effective_name).suffix.lower()
        guessed = mimetypes.guess_type(effective_name)[0]
        resolved_ct = content_type or MESH_MIME_TYPES.get(ext) or guessed or "application/octet-stream"
        if ext in MESH_MIME_TYPES and size > MAX_MESH_UPLOAD_BYTES:
            raise ArcKepValidationError(
                413,
                "FILE_TOO_LARGE",
                f"3D mesh '{effective_name}' is {size / (1024 * 1024):.1f} MiB (limit: 150 MiB).",
                {"max_bytes": MAX_MESH_UPLOAD_BYTES, "size_bytes": size},
            )
        if (resolved_ct.startswith("image/") or ext in IMAGE_EXTENSIONS) and size > MAX_IMAGE_UPLOAD_BYTES:
            raise ArcKepValidationError(
                413,
                "FILE_TOO_LARGE",
                f"Image '{effective_name}' is {size / (1024 * 1024):.1f} MiB (limit: 10 MiB).",
                {"max_bytes": MAX_IMAGE_UPLOAD_BYTES, "size_bytes": size},
            )
        body, multipart_ct = encode_multipart_file("file", effective_name, resolved_ct, path.read_bytes())
        return self._request_json("POST", "/uploads", body_bytes=body, headers={"Content-Type": multipart_ct})

    def upload_file_cached(
        self,
        filepath: str | Path,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> dict[str, Any]:
        """Upload ``filepath`` once per session, caching by ``(abs_path, size, mtime_ns, base_url)``."""
        path = Path(filepath).resolve()
        if not path.is_file():
            raise ArcKepValidationError(400, "FILE_NOT_FOUND", f"File not found: {path}")
        st = path.stat()
        if st.st_size == 0:
            raise ArcKepValidationError(400, "EMPTY_FILE", f"Cannot upload empty file: {path.name}")
        token_fp = hashlib.sha256(self.api_key.encode("utf-8")).hexdigest()[:16]
        stat_key = (str(path), st.st_size, st.st_mtime_ns, self.base_url, token_fp)
        with _CACHE_LOCK:
            cached = _UPLOAD_STAT_CACHE.get(stat_key)
            if cached is not None:
                return dict(cached)
        ext = Path(filename or path.name).suffix.lower()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        hash_key = (digest, ext, self.base_url, token_fp)
        with _CACHE_LOCK:
            cached_by_hash = _UPLOAD_HASH_CACHE.get(hash_key)
            if cached_by_hash is not None:
                _UPLOAD_STAT_CACHE[stat_key] = dict(cached_by_hash)
                return dict(cached_by_hash)
        uploaded = self.upload_file(path, filename=filename, content_type=content_type)
        with _CACHE_LOCK:
            _UPLOAD_STAT_CACHE[stat_key] = dict(uploaded)
            _UPLOAD_HASH_CACHE[hash_key] = dict(uploaded)
        return dict(uploaded)

    def estimate_generation(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        validate_generation_xor(payload)
        return self._request_json("POST", "/models3d/estimate", payload=payload)

    def create_generation(
        self, payload: Mapping[str, Any], idempotency_key: str | None = None
    ) -> dict[str, Any]:
        validate_generation_xor(payload)
        idem = self._make_idempotency_key(idempotency_key)
        return self._request_json(
            "POST", "/models3d/generations", payload=payload, headers={"Idempotency-Key": idem}
        )

    def list_generations(self, limit: int = 20, before_id: int | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": int(limit)}
        if before_id is not None:
            params["before_id"] = int(before_id)
        return self._request_json("GET", f"/models3d/generations?{urllib.parse.urlencode(params)}")

    def get_generation(self, job_id: int) -> dict[str, Any]:
        return self._request_json("GET", f"/models3d/generations/{int(job_id)}")

    def check_rig(
        self, source_job_id: int | None = None, model_url: str | None = None
    ) -> dict[str, Any]:
        body = validate_rig_check_xor(source_job_id, model_url)
        return self._request_json("POST", "/models3d/rig-check", payload=body)

    def estimate_process(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        validate_process_source(payload)
        return self._request_json("POST", "/models3d/process/estimate", payload=payload)

    def create_process(
        self, payload: Mapping[str, Any], idempotency_key: str | None = None
    ) -> dict[str, Any]:
        validate_process_source(payload)
        idem = self._make_idempotency_key(idempotency_key)
        return self._request_json(
            "POST",
            "/models3d/process/generations",
            payload=payload,
            headers={"Idempotency-Key": idem},
        )

    def download_stl(
        self,
        job_id: int,
        dest_path: str | Path,
        print_format: str = "stl",
        target_size_mm: float | None = None,
        scale_axis: str = "max",
        z_up: bool = False,
        place_on_bed: bool = False,
    ) -> Path:
        params: dict[str, str] = {
            "print_format": print_format,
            "scale_axis": scale_axis,
            "z_up": "true" if z_up else "false",
            "place_on_bed": "true" if place_on_bed else "false",
        }
        if target_size_mm is not None:
            params["target_size_mm"] = str(float(target_size_mm))
        query = urllib.parse.urlencode(params)
        url = f"{self.base_url}/models3d/generations/{int(job_id)}/download/stl?{query}"
        return self._stream_to_path(url, dest_path, headers=self._auth_headers({"Accept": "*/*"}))

    def download_file(self, url: str, dest_path: str | Path) -> Path:
        """Stream a presigned asset URL to ``dest_path`` without sending API Bearer credentials."""
        return self._stream_to_path(url, dest_path, headers={"User-Agent": USER_AGENT})

    def _stream_to_path(self, url: str, dest_path: str | Path, headers: Mapping[str, str]) -> Path:
        target = Path(dest_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(url, headers=dict(headers), method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp, target.open("wb") as out:
                shutil.copyfileobj(resp, out)
        except urllib.error.HTTPError as exc:
            raise parse_error_payload(exc.code, exc.read(), str(exc.reason)) from exc
        except urllib.error.URLError as exc:
            raise ArcKepApiError(0, "NETWORK_ERROR", f"Download error: {exc.reason}") from exc
        return target
