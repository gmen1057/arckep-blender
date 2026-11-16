# SPDX-License-Identifier: GPL-3.0-or-later
"""Error envelopes, upload limits, and strict XOR pre-validators for ``/api/v1``."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from typing import Any

MAX_MESH_UPLOAD_BYTES = 150 * 1024 * 1024  # 150 MiB
MAX_IMAGE_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MiB
MAX_IDEMPOTENCY_KEY_LEN = 128

MESH_MIME_TYPES: dict[str, str] = {
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
    ".fbx": "application/octet-stream",
    ".obj": "text/plain",
    ".stl": "model/stl",
}
IMAGE_EXTENSIONS = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".tif", ".heic", ".heif"}
)


class ArcKepApiError(Exception):
    """Structured error returned by ``/api/v1`` or raised on transport/validation failure."""

    def __init__(
        self,
        status_code: int,
        error_code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"[{status_code} {error_code}] {message}")
        self.status_code = status_code
        self.error_code = error_code
        self.message = message
        self.details = details


class ArcKepValidationError(ArcKepApiError, ValueError):
    """Client-side pre-validation failure raised before issuing a network call."""


def parse_error_payload(status_code: int, raw_body: bytes, reason: str) -> ArcKepApiError:
    """Parse both top-level APIError JSON and ``detail``-wrapped middleware errors."""
    text = raw_body.decode("utf-8", errors="replace").strip()
    if text:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            if isinstance(data.get("error_code"), str) and isinstance(data.get("message"), str):
                details = data.get("details")
                return ArcKepApiError(
                    status_code=status_code,
                    error_code=data["error_code"],
                    message=data["message"],
                    details=details if isinstance(details, dict) else None,
                )
            detail = data.get("detail")
            if isinstance(detail, dict) and isinstance(detail.get("error_code"), str):
                sub_details = detail.get("details")
                return ArcKepApiError(
                    status_code=status_code,
                    error_code=detail["error_code"],
                    message=str(detail.get("message") or reason),
                    details=sub_details if isinstance(sub_details, dict) else None,
                )
            if detail is not None:
                code = "VALIDATION_ERROR" if status_code in (400, 422) else f"HTTP_{status_code}"
                msg = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)
                return ArcKepApiError(
                    status_code=status_code,
                    error_code=code,
                    message=msg,
                    details=detail if isinstance(detail, dict) else {"detail": detail},
                )
    return ArcKepApiError(
        status_code=status_code,
        error_code=f"HTTP_{status_code}",
        message=text or reason or f"HTTP {status_code}",
        details=None,
    )


def validate_generation_xor(payload: Mapping[str, Any]) -> None:
    """Enforce strict XOR of ``prompt``, ``image_url``, ``image_urls``, ``source_multiview_job_id``."""
    prompt = payload.get("prompt")
    image_url = payload.get("image_url")
    image_urls = payload.get("image_urls")
    mv_job_id = payload.get("source_multiview_job_id")
    active = sum(
        (
            isinstance(prompt, str) and bool(prompt.strip()),
            isinstance(image_url, str) and bool(image_url.strip()),
            isinstance(image_urls, (list, tuple)) and len(image_urls) > 0,
            mv_job_id is not None and not isinstance(mv_job_id, bool),
        )
    )
    if active != 1:
        raise ArcKepValidationError(
            status_code=400,
            error_code="INVALID_PARAMETER",
            message="Provide exactly one of: prompt, image_url, image_urls, or source_multiview_job_id.",
        )


def validate_rig_check_xor(source_job_id: int | None, model_url: str | None) -> dict[str, Any]:
    """Enforce strict XOR, bare upload key, and non-.gltf/.zip rules for ``/models3d/rig-check``."""
    has_job = source_job_id is not None and not isinstance(source_job_id, bool)
    has_url = isinstance(model_url, str) and bool(model_url.strip())
    if has_job == has_url:
        raise ArcKepValidationError(
            status_code=400,
            error_code="INVALID_PARAMETER",
            message="Provide exactly one of source_job_id or model_url.",
        )
    if has_job:
        return {"source_job_id": int(source_job_id)}  # type: ignore[arg-type]
    cleaned = str(model_url).strip()
    if cleaned.startswith(("http://", "https://")):
        raise ArcKepValidationError(
            400,
            "INVALID_PARAMETER",
            "model_url must be a bare upload 'key' from POST /uploads, never an HTTP URL.",
        )
    if cleaned.lower().endswith((".gltf", ".zip")):
        raise ArcKepValidationError(
            400,
            "INVALID_PARAMETER",
            "rig-check forbids .gltf and .zip; upload binary .glb instead.",
        )
    return {"model_url": cleaned}


def validate_process_source(payload: Mapping[str, Any]) -> None:
    """Pre-validate mesh/image source rules for ``/models3d/process/*``."""
    op = payload.get("operation")
    if not isinstance(op, str) or not op.strip():
        raise ArcKepValidationError(400, "INVALID_PARAMETER", "Process payload requires 'operation'.")
    raw_job = payload.get("source_job_id")
    has_job = raw_job is not None and not isinstance(raw_job, bool)
    model_url = payload.get("model_url")
    has_model = isinstance(model_url, str) and bool(model_url.strip())
    if op == "smartsegment" and payload.get("seg_type") == "image":
        img = payload.get("image_url")
        if not (isinstance(img, str) and img.strip()) or has_job or has_model:
            raise ArcKepValidationError(
                400,
                "INVALID_PARAMETER",
                "smartsegment with seg_type='image' requires image_url and no mesh source.",
            )
        return
    if op in ("retarget", "complete"):
        if not has_job or has_model:
            raise ArcKepValidationError(
                400,
                "INVALID_PARAMETER",
                f"Operation {op!r} requires source_job_id from an owned ArcKep generation.",
            )
        return
    if has_job == has_model:
        raise ArcKepValidationError(
            400, "INVALID_PARAMETER", "Provide exactly one of source_job_id or model_url."
        )


def encode_multipart_file(
    field_name: str, filename: str, content_type: str, data: bytes
) -> tuple[bytes, str]:
    """Encode a single file into a ``multipart/form-data`` request body."""
    boundary = f"----ArcKepBoundary{uuid.uuid4().hex}"
    safe_name = filename.replace('"', "_").replace("\r", "").replace("\n", "")
    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field_name}"; filename="{safe_name}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8")
    footer = f"\r\n--{boundary}--\r\n".encode("utf-8")
    return header + data + footer, f"multipart/form-data; boundary={boundary}"
