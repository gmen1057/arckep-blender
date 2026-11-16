# SPDX-License-Identifier: MIT
"""ArcKep Blender MCP Server package."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .api_bridge import (
    BUDGET_MODE_CONFIRM_ALWAYS,
    BUDGET_MODE_THRESHOLD,
    BUDGET_MODE_UNLIMITED,
    BUDGET_MODES,
    DEFAULT_API_BASE_URL,
    DEFAULT_MAX_AUTO_COST_RUB,
    ArcKepCloudBridge,
    BudgetDecision,
    RuntimePolicy,
    clear_upload_cache,
    evaluate_budget,
    resolve_runtime_policy,
)
from .blender_rpc import (
    DEFAULT_BLENDER_HOST,
    DEFAULT_BLENDER_PORT,
    MAX_RPC_RESPONSE_BYTES,
    BlenderBridgeError,
    BlenderRpcClient,
    normalize_loopback_host,
    parse_bridge_port,
)
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
    rodin_allowed_mesh_options,
    validate_generation_xor,
    validate_process_source,
    validate_rig_check_xor,
)

if TYPE_CHECKING:
    from .server import main, mcp

__version__ = "0.1.0"


def __getattr__(name: str) -> Any:
    if name in ("mcp", "main"):
        from . import server

        return getattr(server, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BUDGET_MODES",
    "BUDGET_MODE_CONFIRM_ALWAYS",
    "BUDGET_MODE_THRESHOLD",
    "BUDGET_MODE_UNLIMITED",
    "DEFAULT_API_BASE_URL",
    "DEFAULT_BLENDER_HOST",
    "DEFAULT_BLENDER_PORT",
    "DEFAULT_MAX_AUTO_COST_RUB",
    "GENERATION_MODEL_IDS",
    "MAX_IMAGE_UPLOAD_BYTES",
    "MAX_MESH_UPLOAD_BYTES",
    "MAX_RPC_RESPONSE_BYTES",
    "MESHY_RETEXTURE_MAX_BYTES",
    "PROCESS_OP_IDS",
    "ArcKepApiError",
    "ArcKepCloudBridge",
    "ArcKepValidationError",
    "BlenderBridgeError",
    "BlenderRpcClient",
    "BudgetDecision",
    "RuntimePolicy",
    "__version__",
    "build_generation_payload",
    "build_process_payload",
    "clear_upload_cache",
    "evaluate_budget",
    "main",
    "mcp",
    "normalize_loopback_host",
    "parse_bridge_port",
    "resolve_runtime_policy",
    "rodin_allowed_mesh_options",
    "validate_generation_xor",
    "validate_process_source",
    "validate_rig_check_xor",
]
