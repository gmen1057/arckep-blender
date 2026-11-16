# SPDX-License-Identifier: MIT
"""Loopback-only TCP JSON-RPC client for the local Blender MCP scene bridge."""

from __future__ import annotations

import json
import os
import socket
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

DEFAULT_BLENDER_HOST = "127.0.0.1"
DEFAULT_BLENDER_PORT = 9876
MAX_RPC_RESPONSE_BYTES = 16 * 1024 * 1024  # 16 MiB
VIEWPORT_MODES = frozenset({"camera", "selection", "scene"})


class BlenderBridgeError(Exception):
    """Structured error returned by or raised while talking to Blender's local MCP bridge."""

    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.error_code = code
        self.message = message
        self.details = details


def normalize_loopback_host(host: str | None = None) -> str:
    """Resolve loopback host strictly to ``127.0.0.1`` and reject any non-local address."""
    raw = host if host is not None else os.environ.get("ARCKEP_BLENDER_HOST", DEFAULT_BLENDER_HOST)
    candidate = str(raw).strip().lower()
    if candidate in ("127.0.0.1", "localhost"):
        return DEFAULT_BLENDER_HOST
    raise ValueError("Blender MCP bridge client only connects to 127.0.0.1")


def parse_bridge_port(port: int | str | None = None) -> int:
    """Validate TCP bridge port in ``1024..65535``."""
    raw: int | str = (
        port
        if port is not None
        else (os.environ.get("ARCKEP_BLENDER_PORT", "").strip() or DEFAULT_BLENDER_PORT)
    )
    if isinstance(raw, bool):
        raise ValueError("ARCKEP_BLENDER_PORT must be an integer in 1024..65535")
    try:
        parsed = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid Blender MCP port {raw!r}; expected integer in 1024..65535") from exc
    if not (1024 <= parsed <= 65535):
        raise ValueError(f"Blender MCP port {parsed} out of range (expected 1024..65535)")
    return parsed


class BlenderRpcClient:
    """Line-delimited UTF-8 JSON client for ``127.0.0.1:<port>`` Blender MCP bridge."""

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.host = normalize_loopback_host(host)
        self.port = parse_bridge_port(port)
        self.timeout = max(0.5, float(timeout))

    def _start_hint(self) -> str:
        return (
            f"Enable the bridge in Blender: open 3D Viewport -> N-panel -> ArcKep 3D -> MCP -> "
            f"Start MCP Bridge (port {self.port})."
        )

    def call(self, command: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Send one JSON-line command to Blender and return the ``result`` dictionary."""
        cmd = str(command or "").strip()
        if not cmd:
            raise BlenderBridgeError("INVALID_COMMAND", "Bridge command name must not be empty.")
        req_id = str(uuid.uuid4())
        envelope = {"id": req_id, "command": cmd, "params": dict(params or {})}
        wire = (json.dumps(envelope, ensure_ascii=False) + "\n").encode("utf-8")
        if len(wire) > MAX_RPC_RESPONSE_BYTES:
            raise BlenderBridgeError("REQUEST_TOO_LARGE", "Bridge request exceeds 16 MiB packet limit.")

        buf = bytearray()
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
                sock.settimeout(self.timeout)
                sock.sendall(wire)
                while b"\n" not in buf:
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    buf.extend(chunk)
                    if len(buf) > MAX_RPC_RESPONSE_BYTES:
                        raise BlenderBridgeError(
                            "RESPONSE_TOO_LARGE",
                            "Blender MCP bridge response exceeded the 16 MiB safety cap.",
                        )
        except BlenderBridgeError:
            raise
        except ConnectionRefusedError as exc:
            raise BlenderBridgeError(
                "BRIDGE_UNAVAILABLE",
                f"Connection refused at {self.host}:{self.port}. {self._start_hint()}",
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise BlenderBridgeError(
                "BRIDGE_TIMEOUT",
                f"Timed out waiting for Blender at {self.host}:{self.port}. {self._start_hint()}",
            ) from exc
        except OSError as exc:
            raise BlenderBridgeError(
                "BRIDGE_CONNECTION_ERROR",
                f"Cannot reach Blender bridge at {self.host}:{self.port} ({exc}). {self._start_hint()}",
            ) from exc

        raw_line = bytes(buf).split(b"\n", 1)[0].strip()
        if not raw_line:
            raise BlenderBridgeError(
                "BRIDGE_CLOSED",
                f"Blender bridge at {self.host}:{self.port} closed without sending a response.",
            )
        try:
            decoded = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BlenderBridgeError("INVALID_BRIDGE_JSON", f"Malformed JSON from Blender: {exc}") from exc
        if not isinstance(decoded, dict):
            raise BlenderBridgeError("INVALID_BRIDGE_RESPONSE", "Expected JSON object from Blender bridge.")
        if decoded.get("id") not in (None, req_id):
            raise BlenderBridgeError("PROTOCOL_MISMATCH", "Mismatched response ID from Blender bridge.")

        if decoded.get("ok") is True:
            result = decoded.get("result")
            return result if isinstance(result, dict) else {"value": result}

        err_obj = decoded.get("error")
        if isinstance(err_obj, dict):
            code = str(err_obj.get("code") or "BLENDER_ERROR")
            msg = str(err_obj.get("message") or "Blender command failed.")
            details = err_obj.get("details")
            raise BlenderBridgeError(code, msg, details if isinstance(details, dict) else None)
        raise BlenderBridgeError("BLENDER_ERROR", str(err_obj or "Blender bridge command failed."))

    def ping(self) -> dict[str, Any]:
        return self.call("ping")

    def get_scene_info(self) -> dict[str, Any]:
        return self.call("get_scene_info")

    def get_object_info(self, name: str) -> dict[str, Any]:
        cleaned = str(name or "").strip()
        if not cleaned:
            raise BlenderBridgeError("INVALID_PARAMETER", "Object 'name' must not be empty.")
        return self.call("get_object_info", {"name": cleaned})

    def capture_viewport(self, mode: str = "scene", resolution: int = 1024) -> dict[str, Any]:
        if mode not in VIEWPORT_MODES:
            raise BlenderBridgeError(
                "INVALID_PARAMETER",
                f"Unsupported viewport capture mode {mode!r}; expected 'camera', 'selection', or 'scene'.",
            )
        if isinstance(resolution, bool) or not (512 <= int(resolution) <= 2048):
            raise BlenderBridgeError(
                "INVALID_PARAMETER",
                f"Viewport resolution must be in 512..2048 pixels, got {resolution!r}.",
            )
        return self.call("capture_viewport", {"mode": mode, "resolution": int(resolution)})

    def export_selection_glb(
        self, names: list[str] | None = None, max_bytes: int | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if names is not None:
            params["names"] = [str(n).strip() for n in names if str(n).strip()]
        if max_bytes is not None:
            params["max_bytes"] = int(max_bytes)
        return self.call("export_selection_glb", params)

    def import_asset(
        self,
        *,
        filepath: str,
        format: str | None = None,
        name: str | None = None,
        location: list[float] | str | None = "cursor",
        scale_target_m: float | None = None,
        collection: str | None = None,
        arckep_job_id: int | None = None,
        arckep_task_type: str | None = None,
        arckep_model: str | None = None,
        replace_object: str | None = None,
    ) -> dict[str, Any]:
        cleaned_path = str(filepath or "").strip()
        if not cleaned_path:
            raise BlenderBridgeError("INVALID_PARAMETER", "'filepath' must not be empty.")
        params: dict[str, Any] = {"filepath": cleaned_path, "location": location}
        optional: dict[str, Any] = {
            "format": format,
            "name": name,
            "scale_target_m": float(scale_target_m) if scale_target_m is not None else None,
            "collection": collection,
            "arckep_job_id": int(arckep_job_id) if arckep_job_id is not None else None,
            "arckep_task_type": arckep_task_type,
            "arckep_model": arckep_model,
            "replace_object": replace_object,
        }
        for key, val in optional.items():
            if val is not None:
                params[key] = val
        return self.call("import_asset", params)

    def transform_object(
        self,
        *,
        name: str,
        location: list[float] | None = None,
        rotation_euler_deg: list[float] | None = None,
        scale: list[float] | float | None = None,
        place_on_ground: bool = False,
    ) -> dict[str, Any]:
        cleaned_name = str(name or "").strip()
        if not cleaned_name:
            raise BlenderBridgeError("INVALID_PARAMETER", "Object 'name' must not be empty.")
        params: dict[str, Any] = {"name": cleaned_name, "place_on_ground": bool(place_on_ground)}
        if location is not None:
            if isinstance(location, (str, bytes)) or not isinstance(location, Sequence) or len(location) != 3:
                raise BlenderBridgeError("INVALID_PARAMETER", "'location' must be [x, y, z].")
            params["location"] = [float(v) for v in location]
        if rotation_euler_deg is not None:
            if isinstance(rotation_euler_deg, (str, bytes)) or not isinstance(rotation_euler_deg, Sequence) or len(rotation_euler_deg) != 3:
                raise BlenderBridgeError("INVALID_PARAMETER", "'rotation_euler_deg' must be [rx, ry, rz].")
            params["rotation_euler_deg"] = [float(v) for v in rotation_euler_deg]
        if scale is not None:
            if isinstance(scale, (int, float)) and not isinstance(scale, bool):
                params["scale"] = float(scale)
            elif not isinstance(scale, (str, bytes)) and isinstance(scale, Sequence) and len(scale) == 3:
                params["scale"] = [float(v) for v in scale]
            else:
                raise BlenderBridgeError("INVALID_PARAMETER", "'scale' must be a float or [sx, sy, sz].")
        return self.call("transform_object", params)
