# SPDX-License-Identifier: GPL-3.0-or-later
"""ArcKep 3D Blender Extension (Blender 4.2 LTS – 5.x).

CRITICAL BLENDER 4.2+ EXTENSIONS NOTE:
When installed as an extension, ``__package__`` is ``"bl_ext.<repo>.arckep_3d"``
(e.g. ``"bl_ext.user_default.arckep_3d"``), NOT ``"arckep_3d"``. Always resolve
``AddonPreferences`` via ``ADDON_PACKAGE = __package__ or __name__``.
"""

from __future__ import annotations

from typing import Any

from .api_client import (
    DEFAULT_BASE_URL,
    ArcKepApiClient,
    ArcKepApiError,
    clear_upload_cache,
)
from .budget_guard import (
    BUDGET_MODE_CONFIRM_ALWAYS,
    BUDGET_MODE_THRESHOLD,
    BUDGET_MODE_UNLIMITED,
    DEFAULT_MAX_AUTO_COST_RUB,
)
from .job_queue import ArcKepJobQueue

ADDON_PACKAGE: str = __package__ or __name__
ADDON_VERSION: tuple[int, int, int] = (0, 1, 0)

_JOB_QUEUE: ArcKepJobQueue | None = None
_MCP_BRIDGE: ArcKepMcpBridge | None = None
_SUPPRESS_MCP_PREFS_UPDATE: bool = False

try:
    import bpy  # type: ignore[import-not-found]
    from bpy.props import (  # type: ignore[import-not-found]
        BoolProperty,
        EnumProperty,
        FloatProperty,
        IntProperty,
        PointerProperty,
        StringProperty,
    )
except ImportError:  # pragma: no cover - imported outside Blender in unit tests
    bpy = None


def get_job_queue() -> ArcKepJobQueue:
    """Return the active singleton ``ArcKepJobQueue`` instance."""
    global _JOB_QUEUE
    if _JOB_QUEUE is None or _JOB_QUEUE._shutdown_event.is_set():
        _JOB_QUEUE = ArcKepJobQueue()
    return _JOB_QUEUE


def get_addon_preferences(context: Any = None) -> Any:
    """Resolve ``ArcKepAddonPreferences`` using ``ADDON_PACKAGE``."""
    if bpy is None:
        raise RuntimeError("Blender 'bpy' is not available in this Python process.")
    ctx = context if context is not None else bpy.context
    addon_entry = ctx.preferences.addons.get(ADDON_PACKAGE)
    if addon_entry is None:
        raise KeyError(f"Extension preferences not registered for {ADDON_PACKAGE!r}.")
    return addon_entry.preferences


def build_api_client(context: Any = None) -> ArcKepApiClient:
    """Construct an ``ArcKepApiClient`` from current ``AddonPreferences``."""
    prefs = get_addon_preferences(context)
    return ArcKepApiClient(
        api_key=prefs.api_key,
        base_url=prefs.api_base_url or DEFAULT_BASE_URL,
    )


from .mcp_bridge import BRIDGE_HOST, ArcKepMcpBridge  # noqa: E402


def get_mcp_bridge() -> ArcKepMcpBridge:
    """Return the singleton ``ArcKepMcpBridge`` instance."""
    global _MCP_BRIDGE
    if _MCP_BRIDGE is None:
        _MCP_BRIDGE = ArcKepMcpBridge()
    return _MCP_BRIDGE


def _set_mcp_enabled_quietly(prefs: Any, enabled: bool) -> None:
    global _SUPPRESS_MCP_PREFS_UPDATE
    if bool(prefs.mcp_enabled) == enabled:
        return
    _SUPPRESS_MCP_PREFS_UPDATE = True
    try:
        prefs.mcp_enabled = enabled
    finally:
        _SUPPRESS_MCP_PREFS_UPDATE = False


def _on_mcp_enabled_update(self: Any, _context: Any) -> None:
    if _SUPPRESS_MCP_PREFS_UPDATE:
        return
    bridge = get_mcp_bridge()
    if self.mcp_enabled:
        try:
            bridge.start(port=int(self.mcp_port), host=BRIDGE_HOST)
        except (OSError, ValueError):
            _set_mcp_enabled_quietly(self, False)
    else:
        bridge.stop()


def _on_mcp_port_update(self: Any, _context: Any) -> None:
    if _SUPPRESS_MCP_PREFS_UPDATE:
        return
    bridge = get_mcp_bridge()
    if bridge.is_running or self.mcp_enabled:
        try:
            bridge.start(port=int(self.mcp_port), host=BRIDGE_HOST)
        except (OSError, ValueError):
            _set_mcp_enabled_quietly(self, False)


if bpy is not None:
    from .operators_archive import ARCHIVE_OPERATOR_CLASSES
    from .operators_create import CREATE_OPERATOR_CLASSES
    from .operators_process import PROCESS_OPERATOR_CLASSES
    from .panels import PANEL_CLASSES
    from .state_props import (
        STATE_CLASSES,
        ArcKepArchiveProps,
        ArcKepCreateProps,
        ArcKepProcessProps,
        ArcKepWindowState,
    )

    BUDGET_MODE_ITEMS = (
        (
            BUDGET_MODE_THRESHOLD,
            "Smart Threshold",
            "Auto-approve jobs up to the configured RUB threshold; prompt above it",
        ),
        (
            BUDGET_MODE_CONFIRM_ALWAYS,
            "Always Confirm",
            "Require explicit confirmation before any paid RUB generation",
        ),
        (
            BUDGET_MODE_UNLIMITED,
            "Unlimited",
            "Allow jobs without a local cap (bounded only by account balance)",
        ),
    )

    class ArcKepAddonPreferences(bpy.types.AddonPreferences):
        """User-level settings stored in Blender preferences (never saved in .blend)."""

        bl_idname = ADDON_PACKAGE

        api_key: StringProperty(  # type: ignore[valid-type]
            name="API Key",
            description="ArcKep API key (ark_live_...) stored only in user preferences",
            default="",
            subtype="PASSWORD",
        )
        api_base_url: StringProperty(  # type: ignore[valid-type]
            name="API Base URL",
            description="ArcKep Public API v1 base URL",
            default=DEFAULT_BASE_URL,
        )
        budget_mode: EnumProperty(  # type: ignore[valid-type]
            name="Budget Guard",
            description="Local spending protection mode for paid 3D operations",
            items=BUDGET_MODE_ITEMS,
            default=BUDGET_MODE_THRESHOLD,
        )
        max_auto_cost_rub: FloatProperty(  # type: ignore[valid-type]
            name="Auto-Approve Limit (RUB)",
            description="Maximum RUB price allowed without confirmation in Smart Threshold mode",
            default=DEFAULT_MAX_AUTO_COST_RUB,
            min=0.0,
            soft_max=10000.0,
            precision=2,
        )
        mcp_enabled: BoolProperty(  # type: ignore[valid-type]
            name="Enable Local MCP Bridge",
            description="Accept scene bridge connections from arckep-blender-mcp on 127.0.0.1",
            default=False,
            update=_on_mcp_enabled_update,
        )
        mcp_port: IntProperty(  # type: ignore[valid-type]
            name="MCP Port",
            description="Local TCP port on 127.0.0.1 for arckep-blender-mcp",
            default=9876,
            min=1024,
            max=65535,
            update=_on_mcp_port_update,
        )

        def draw(self, context: Any) -> None:
            layout = self.layout
            api_box = layout.box()
            api_box.label(text="ArcKep API Connection", icon="WORLD")
            api_box.prop(self, "api_key")
            api_box.prop(self, "api_base_url")
            api_box.operator("arckep.refresh_balance", icon="FILE_REFRESH")

            budget_box = layout.box()
            budget_box.label(text="Budget Guard (RUB)", icon="LOCKED")
            budget_box.prop(self, "budget_mode")
            if self.budget_mode == BUDGET_MODE_THRESHOLD:
                budget_box.prop(self, "max_auto_cost_rub")

            mcp_box = layout.box()
            mcp_box.label(text="Local MCP Bridge (127.0.0.1 only)", icon="PLUGIN")
            row = mcp_box.row(align=True)
            row.prop(self, "mcp_enabled")
            row.prop(self, "mcp_port")
            bridge = get_mcp_bridge()
            if bridge.is_running:
                mcp_box.label(
                    text=f"Status: Listening on 127.0.0.1:{bridge.bound_port}", icon="CHECKMARK"
                )
                mcp_box.operator("arckep.stop_mcp_bridge", text="Stop MCP Bridge", icon="CANCEL")
            else:
                mcp_box.label(text=f"Status: Stopped (127.0.0.1:{self.mcp_port})", icon="LOCKED")
                mcp_box.operator("arckep.start_mcp_bridge", text="Start MCP Bridge", icon="PLAY")
            if bridge.last_error:
                mcp_box.label(text=bridge.last_error, icon="ERROR")

    class ARCKEP_OT_refresh_balance(bpy.types.Operator):
        """Fetch personal or corporate B2B RUB balance from ArcKep /api/v1/balance."""

        bl_idname = "arckep.refresh_balance"
        bl_label = "Refresh Balance"
        bl_description = "Verify API key and refresh current RUB balance from ArcKep"

        def execute(self, context: Any) -> set[str]:
            wm_state = context.window_manager.arckep_state
            try:
                client = build_api_client(context)
                data = client.get_balance()
            except ArcKepApiError as exc:
                wm_state.connected = False
                wm_state.last_error = exc.message
                self.report({"ERROR"}, f"ArcKep API error: {exc.message}")
                return {"CANCELLED"}
            except Exception as exc:
                wm_state.connected = False
                wm_state.last_error = str(exc)
                self.report({"ERROR"}, f"Failed to refresh balance: {exc}")
                return {"CANCELLED"}

            wm_state.connected = True
            wm_state.balance_rub = str(data.get("balance_rub") or "0.00")
            wm_state.is_corporate = bool(data.get("is_corporate", False))
            wm_state.corporate_name = str(data.get("corporate_name") or "")
            wm_state.last_error = ""
            if wm_state.is_corporate and wm_state.corporate_name:
                msg = f"{wm_state.corporate_name}: {wm_state.balance_rub} RUB"
            else:
                msg = f"Balance: {wm_state.balance_rub} RUB"
            self.report({"INFO"}, msg)
            return {"FINISHED"}

    def _start_mcp_op(op: Any, context: Any) -> set[str]:
        prefs = get_addon_preferences(context)
        try:
            port = get_mcp_bridge().start(port=int(prefs.mcp_port), host=BRIDGE_HOST)
        except (OSError, ValueError) as exc:
            _set_mcp_enabled_quietly(prefs, False)
            op.report({"WARNING"}, f"MCP bridge failed: {exc}")
            return {"CANCELLED"}
        _set_mcp_enabled_quietly(prefs, True)
        op.report({"INFO"}, f"MCP bridge listening on {BRIDGE_HOST}:{port}")
        return {"FINISHED"}

    def _stop_mcp_op(op: Any, context: Any) -> set[str]:
        prefs = get_addon_preferences(context)
        get_mcp_bridge().stop()
        _set_mcp_enabled_quietly(prefs, False)
        op.report({"INFO"}, "MCP bridge stopped")
        return {"FINISHED"}

    class ARCKEP_OT_start_mcp_bridge(bpy.types.Operator):
        """Start the local 127.0.0.1 TCP MCP scene bridge."""

        bl_idname = "arckep.start_mcp_bridge"
        bl_label = "Start MCP Bridge"
        bl_description = "Start listening for arckep-blender-mcp commands on 127.0.0.1"

        def execute(self, context: Any) -> set[str]:
            return _start_mcp_op(self, context)

    class ARCKEP_OT_stop_mcp_bridge(bpy.types.Operator):
        """Stop the local 127.0.0.1 TCP MCP scene bridge."""

        bl_idname = "arckep.stop_mcp_bridge"
        bl_label = "Stop MCP Bridge"
        bl_description = "Stop the local 127.0.0.1 MCP scene bridge listener"

        def execute(self, context: Any) -> set[str]:
            return _stop_mcp_op(self, context)

    class ARCKEP_OT_toggle_mcp_bridge(bpy.types.Operator):
        """Toggle the local 127.0.0.1 TCP MCP scene bridge on or off."""

        bl_idname = "arckep.toggle_mcp_bridge"
        bl_label = "Toggle MCP Bridge"
        bl_description = "Toggle the local 127.0.0.1 MCP scene bridge"

        def execute(self, context: Any) -> set[str]:
            if get_mcp_bridge().is_running:
                return _stop_mcp_op(self, context)
            return _start_mcp_op(self, context)

    _CLASSES = (
        ArcKepAddonPreferences,
        *STATE_CLASSES,
        ARCKEP_OT_refresh_balance,
        ARCKEP_OT_start_mcp_bridge,
        ARCKEP_OT_stop_mcp_bridge,
        ARCKEP_OT_toggle_mcp_bridge,
        *CREATE_OPERATOR_CLASSES,
        *PROCESS_OPERATOR_CLASSES,
        *ARCHIVE_OPERATOR_CLASSES,
        *PANEL_CLASSES,
    )
    _WM_PROPS = (
        ("arckep_state", ArcKepWindowState),
        ("arckep_create", ArcKepCreateProps),
        ("arckep_process", ArcKepProcessProps),
        ("arckep_archive", ArcKepArchiveProps),
    )

    def register() -> None:
        global _JOB_QUEUE, _MCP_BRIDGE
        for cls in _CLASSES:
            bpy.utils.register_class(cls)
        for attr, prop_cls in _WM_PROPS:
            setattr(
                bpy.types.WindowManager,
                attr,
                PointerProperty(type=prop_cls, options={"SKIP_SAVE"}),
            )
        _JOB_QUEUE = ArcKepJobQueue()
        _JOB_QUEUE.register_bpy_timer()
        _MCP_BRIDGE = ArcKepMcpBridge()

    def unregister() -> None:
        global _JOB_QUEUE, _MCP_BRIDGE
        if _MCP_BRIDGE is not None:
            _MCP_BRIDGE.stop()
            _MCP_BRIDGE = None
        if _JOB_QUEUE is not None:
            _JOB_QUEUE.shutdown(wait=False)
            _JOB_QUEUE = None
        clear_upload_cache()
        for attr, _ in reversed(_WM_PROPS):
            if hasattr(bpy.types.WindowManager, attr):
                delattr(bpy.types.WindowManager, attr)
        for cls in reversed(_CLASSES):
            bpy.utils.unregister_class(cls)
