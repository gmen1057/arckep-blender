# SPDX-License-Identifier: GPL-3.0-or-later
"""Local 127.0.0.1 TCP JSONL scene bridge for ``arckep-blender-mcp``."""

from __future__ import annotations

import contextlib
import json
import math
import queue
import socket
import threading
import time
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, NoReturn

from . import ADDON_VERSION, get_addon_preferences, get_job_queue, scene_io
from .api_client import DEFAULT_BASE_URL
from .api_validation import MAX_MESH_UPLOAD_BYTES, ArcKepApiError, ArcKepValidationError
from .scene_io import _require_bpy

try:
    import bpy as _bpy_mod  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    _bpy_mod = None

BRIDGE_HOST, DEFAULT_BRIDGE_PORT = "127.0.0.1", 9876
MAX_PACKET_BYTES, COMMAND_TIMEOUT_SEC = 16 * 1024 * 1024, 120.0
ALLOWED_COMMANDS = frozenset({"ping", "get_scene_info", "get_object_info", "capture_viewport", "export_selection_glb", "import_asset", "transform_object"})
_PACKET_TOO_LARGE_LINE = b'{"id": null, "ok": false, "error": {"code": "PACKET_TOO_LARGE", "message": "Bridge packet exceeds 16 MiB limit."}}\n'


def _err(req_id: Any, code: str, msg: str) -> dict[str, Any]:
    return {"id": req_id, "ok": False, "error": {"code": code, "message": msg}}


class ArcKepMcpBridge:
    """Thread-safe 127.0.0.1 TCP bridge dispatching commands onto Blender's main thread."""

    def __init__(self, timer_interval: float = 0.02) -> None:
        self.timer_interval, self.is_running, self.bound_port, self.requests_handled = max(0.005, float(timer_interval)), False, 0, 0
        self.last_command, self.last_error, self._server_sock, self._accept_thread = "", "", None, None
        self._lock, self._stop_event, self._pending, self._inflight, self._client_sockets = threading.Lock(), threading.Event(), queue.Queue(), {}, set()
        self._timer_callback: Callable[[], float | None] = self._bpy_timer_tick

    def _bad(self, msg: str) -> NoReturn:
        raise ArcKepValidationError(400, "INVALID_PARAMETER", msg)

    def _is_num(self, v: Any) -> bool:
        return not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(float(v))

    def _v3(self, v: Any, *, deg: bool = False) -> list[float]:
        return [round(math.degrees(float(v[i])), 4) if deg else round(float(v[i]), 6) for i in range(3)]

    def _parse_vec3(self, raw: Any, key: str, *, non_zero: bool = False) -> tuple[float, float, float]:
        if not isinstance(raw, (list, tuple)) or len(raw) != 3 or not all(self._is_num(x) for x in raw):
            self._bad(f"Parameter {key!r} must be finite [x, y, z].")
        vals = (float(raw[0]), float(raw[1]), float(raw[2]))
        return self._bad(f"Parameter {key!r} cannot be zero.") if (non_zero and any(v == 0.0 for v in vals)) else vals

    def _opt_int(self, params: dict[str, Any], key: str, default: int | None = None, *, lo: int | None = None, hi: int | None = None) -> int | None:
        val = default if params.get(key) is None else params.get(key)
        if val is not None and (isinstance(val, bool) or not isinstance(val, int) or (lo is not None and val < lo) or (hi is not None and val > hi)):
            self._bad(f"Invalid integer parameter {key!r}.")
        return val

    def _world_bbox(self, objects: list[Any], Vector: Any) -> tuple[list[float], list[float]]:
        pts = [obj.matrix_world @ Vector(c) for obj in objects for c in obj.bound_box]
        return ([round(min(float(p[i]) for p in pts), 6) for i in range(3)], [round(max(float(p[i]) for p in pts), 6) for i in range(3)]) if pts else ([0.0, 0.0, 0.0], [0.0, 0.0, 0.0])

    def _close_sock(self, sock: socket.socket | None, *, oversize: bool = False) -> None:
        if sock is not None:
            with contextlib.suppress(OSError):
                if oversize:
                    sock.sendall(_PACKET_TOO_LARGE_LINE)
                    sock.shutdown(socket.SHUT_WR)
                    sock.settimeout(0.02)
                    while sock.recv(262144):
                        pass
                else:
                    sock.shutdown(socket.SHUT_RDWR)
            with contextlib.suppress(OSError):
                sock.close()

    def _base_obj_info(self, obj: Any, *, in_vl: bool = True) -> dict[str, Any]:
        raw_jid, raw_model, raw_task = obj.get("arckep_job_id"), obj.get("arckep_model"), obj.get("arckep_task_type")
        return {"name": obj.name, "type": obj.type, "visible": (not bool(obj.hide_get())) if in_vl else (not bool(obj.hide_viewport)),
                "selected": bool(obj.select_get()) if in_vl else False, "location": self._v3(obj.location), "rotation_euler": self._v3(obj.rotation_euler),
                "rotation_euler_deg": self._v3(obj.rotation_euler, deg=True), "scale": self._v3(obj.scale), "dimensions": self._v3(obj.dimensions),
                "materials": [s.material.name for s in obj.material_slots if s.material is not None],
                "arckep_job_id": int(raw_jid) if raw_jid is not None and not isinstance(raw_jid, bool) else None,
                "arckep_model": str(raw_model) if raw_model else None, "arckep_task_type": str(raw_task) if raw_task else None}

    def start(self, port: int = DEFAULT_BRIDGE_PORT, host: str = BRIDGE_HOST) -> int:
        if host != BRIDGE_HOST or isinstance(port, bool) or not isinstance(port, int) or not (0 <= port <= 65535):
            self.last_error = "MCP bridge may only bind to 127.0.0.1" if host != BRIDGE_HOST else f"Invalid MCP TCP port: {port!r}"
            raise ValueError(self.last_error)
        if self.is_running and port != 0 and self.bound_port == port:
            self._set_bpy_timer(True)
            return self.bound_port
        if self.is_running:
            self.stop()
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.settimeout(0.2)
            srv.bind((BRIDGE_HOST, port))
            srv.listen(16)
            actual_port = int(srv.getsockname()[1])
        except OSError as exc:
            srv.close()
            self.is_running, self.bound_port, self.last_error = False, 0, f"Port {port} unavailable: {exc}"
            raise
        stop_ev = threading.Event()
        thread = threading.Thread(target=self._accept_loop, args=(srv, stop_ev), name=f"arckep-mcp-{actual_port}", daemon=True)
        with self._lock:
            self._server_sock, self._stop_event, self._accept_thread, self.is_running, self.bound_port, self.last_error = srv, stop_ev, thread, True, actual_port, ""
        self._set_bpy_timer(True)
        thread.start()
        return actual_port

    def stop(self) -> None:
        with self._lock:
            stop_ev, srv, thread, inflight, clients = self._stop_event, self._server_sock, self._accept_thread, list(self._inflight.values()), list(self._client_sockets)
            self._server_sock, self._accept_thread, self.is_running, self.bound_port = None, None, False, 0
            self._inflight.clear()
            self._client_sockets.clear()
        stop_ev.set()
        self._set_bpy_timer(False)
        self._close_sock(srv)
        while not self._pending.empty():
            with contextlib.suppress(queue.Empty):
                inflight.append(self._pending.get_nowait())
        for req in inflight:
            req.response = req.response or _err(req.req_id, "BRIDGE_STOPPED", "MCP bridge stopped.")
            req.done_event.set()
        if inflight:
            time.sleep(0.02)
        for csock in clients:
            self._close_sock(csock)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)

    def _accept_loop(self, srv: socket.socket, stop_ev: threading.Event) -> None:
        while not stop_ev.is_set():
            try:
                conn, _ = srv.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            with self._lock:
                if stop_ev.is_set() or not self.is_running:
                    return self._close_sock(conn)
                self._client_sockets.add(conn)
            threading.Thread(target=self._handle_client, args=(conn, stop_ev), daemon=True).start()

    def _handle_client(self, conn: socket.socket, stop_ev: threading.Event) -> None:
        conn.settimeout(0.25)
        buf, oversize = bytearray(), False
        try:
            with contextlib.suppress(OSError):
                while not stop_ev.is_set():
                    try:
                        chunk = conn.recv(65536)
                    except TimeoutError:
                        continue
                    buf.extend(chunk)
                    while (nl := buf.find(b"\n")) != -1 or (not chunk and buf):
                        end = nl if nl != -1 else len(buf)
                        if end > MAX_PACKET_BYTES:
                            oversize = True
                            return
                        line = bytes(buf[:end]).rstrip(b"\r")
                        del buf[: end + 1]
                        if line.strip():
                            conn.sendall((json.dumps(self._enqueue_line(line, stop_ev), ensure_ascii=False) + "\n").encode("utf-8"))
                            if stop_ev.is_set():
                                return
                    if len(buf) > MAX_PACKET_BYTES or not chunk:
                        oversize = len(buf) > MAX_PACKET_BYTES
                        return
        finally:
            with self._lock:
                self._client_sockets.discard(conn)
            self._close_sock(conn, oversize=oversize)

    def _enqueue_line(self, raw_line: bytes, stop_ev: threading.Event) -> dict[str, Any]:
        try:
            payload = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return _err(None, "INVALID_JSON", f"Invalid UTF-8 JSON packet: {exc}")
        if not isinstance(payload, dict):
            return _err(None, "INVALID_REQUEST", "Bridge request must be a JSON object.")
        req_id, cmd, params = payload.get("id"), payload.get("command"), payload.get("params")
        if not isinstance(cmd, str) or not cmd.strip():
            return _err(req_id, "INVALID_REQUEST", "Field 'command' must be a non-empty string.")
        params = {} if params is None else params
        if not isinstance(params, dict):
            return _err(req_id, "INVALID_PARAMETER", "Field 'params' must be a JSON object.")
        req = SimpleNamespace(req_id=req_id, command=cmd.strip(), params=params, done_event=threading.Event(), response=None)
        with self._lock:
            if not self.is_running or stop_ev.is_set():
                return _err(req_id, "BRIDGE_STOPPED", "MCP bridge stopped.")
            self._inflight[id(req)] = req
            self._pending.put(req)
        finished = req.done_event.wait(timeout=COMMAND_TIMEOUT_SEC)
        with self._lock:
            self._inflight.pop(id(req), None)
        return (req.response or _err(req_id, "BRIDGE_STOPPED", "MCP bridge stopped.")) if finished else _err(req_id, "COMMAND_TIMEOUT", "Timed out waiting for Blender main thread.")

    def pump(self, max_commands: int = 32) -> int:
        executed, limit = 0, max(1, int(max_commands))
        while executed < limit and not self._pending.empty():
            with contextlib.suppress(queue.Empty):
                req = self._pending.get_nowait()
                if not req.done_event.is_set():
                    req.response, err_text = self._execute_request(req)
                    with self._lock:
                        self._inflight.pop(id(req), None)
                        self.requests_handled, self.last_command, self.last_error = self.requests_handled + 1, req.command, err_text
                    req.done_event.set()
                    executed += 1
        if executed > 0 and _bpy_mod is not None:
            for a in (a for w in getattr(_bpy_mod.context.window_manager, "windows", ()) for a in getattr(w.screen, "areas", ()) if a.type == "VIEW_3D"):
                a.tag_redraw()
        return executed

    def _bpy_timer_tick(self) -> float | None:
        if not self.is_running:
            return None
        try:
            self.pump()
        except Exception as exc:  # pragma: no cover
            self.last_error = str(exc)
        return self.timer_interval

    def is_bpy_timer_registered(self) -> bool:
        return bool(_bpy_mod is not None and _bpy_mod.app.timers.is_registered(self._timer_callback))

    def _set_bpy_timer(self, active: bool) -> None:
        if _bpy_mod is not None and active != _bpy_mod.app.timers.is_registered(self._timer_callback):
            (_bpy_mod.app.timers.register(self._timer_callback, first_interval=0.02, persistent=True) if active else _bpy_mod.app.timers.unregister(self._timer_callback))

    def run_headless_loop(self, stop_event: threading.Event | None = None, poll_interval: float = 0.01) -> None:
        iv = max(0.001, float(poll_interval))
        try:
            while self.is_running and not (stop_event and stop_event.is_set()):
                self._bpy_timer_tick()
                get_job_queue().pump()
                if stop_event.wait(iv) if stop_event else time.sleep(iv):
                    break
        except (KeyboardInterrupt, SystemExit) as exc:
            self.stop()
            if isinstance(exc, SystemExit) and exc.code not in (None, 0):
                raise

    def _execute_request(self, req: SimpleNamespace) -> tuple[dict[str, Any], str]:
        if req.command not in ALLOWED_COMMANDS:
            return _err(req.req_id, "UNKNOWN_COMMAND", f"Unsupported bridge command {req.command!r}."), f"UNKNOWN_COMMAND: {req.command}"
        try:
            bpy, Vector = _require_bpy()
            ctx, wins, fn = bpy.context, getattr(bpy.context.window_manager, "windows", ()), getattr(self, f"_cmd_{req.command}")
            if getattr(ctx, "window", None) is None and len(wins) > 0:
                with ctx.temp_override(window=wins[0]):
                    res = fn(bpy.context, bpy, Vector, req.params)
            else:
                res = fn(ctx, bpy, Vector, req.params)
            return {"id": req.req_id, "ok": True, "result": res}, ""
        except ArcKepApiError as exc:
            return _err(req.req_id, exc.error_code, exc.message), f"{exc.error_code}: {exc.message}"
        except Exception as exc:
            return _err(req.req_id, "EXECUTION_ERROR", str(exc)), f"EXECUTION_ERROR: {exc}"

    def _find_obj(self, ctx: Any, bpy: Any, params: dict[str, Any], *, fallback: bool = False) -> Any:
        name = params.get("name")
        if not isinstance(name, str) or not name.strip():
            self._bad("Parameter 'name' must be a non-empty string.")
        if (obj := ctx.scene.objects.get(name) or (bpy.data.objects.get(name) if fallback else None)) is None:
            raise ArcKepValidationError(404, "OBJECT_NOT_FOUND", f"Object {name!r} not found in scene.")
        return obj

    def _cmd_ping(self, ctx: Any, bpy: Any, _V: Any, _p: dict[str, Any]) -> dict[str, Any]:
        p = get_addon_preferences(ctx)
        return {"status": "ok", "blender_version": str(bpy.app.version_string), "addon_version": ".".join(str(x) for x in ADDON_VERSION),
                "blend_filepath": str(bpy.data.filepath or ""), "background": bool(bpy.app.background), "host": BRIDGE_HOST, "port": self.bound_port,
                "has_api_key": bool(p.api_key.strip()), "api_base_url": str(p.api_base_url or DEFAULT_BASE_URL), "budget_mode": str(p.budget_mode), "max_auto_cost_rub": round(float(p.max_auto_cost_rub), 2)}

    def _cmd_get_scene_info(self, ctx: Any, bpy: Any, _V: Any, _p: dict[str, Any]) -> dict[str, Any]:
        ctx.view_layer.update()
        scene, active, cols = ctx.scene, ctx.view_layer.objects.active, getattr(ctx.scene.collection, "children_recursive", bpy.data.collections)
        objs = [{**self._base_obj_info(o), "polygon_count": len(o.data.polygons) if o.type == "MESH" and o.data else 0, "vertex_count": len(o.data.vertices) if o.type == "MESH" and o.data else 0} for o in scene.objects]
        return {"scene_name": scene.name, "blend_filepath": str(bpy.data.filepath or ""), "collections": [{"name": c.name, "object_count": len(c.objects)} for c in cols],
                "objects": objs, "object_count": len(objs), "active_object": active.name if active else None,
                "selected_objects": [o.name for o in scene.objects if o.select_get()], "cursor_location": self._v3(scene.cursor.location), "active_camera": scene.camera.name if scene.camera else None}

    def _cmd_get_object_info(self, ctx: Any, bpy: Any, Vector: Any, params: dict[str, Any]) -> dict[str, Any]:
        obj = self._find_obj(ctx, bpy, params, fallback=True)
        ctx.view_layer.update()
        bmin, bmax = self._world_bbox([obj], Vector)
        mesh = obj.data if obj.type == "MESH" and obj.data is not None else None
        cattrs = getattr(mesh, "color_attributes", None) if mesh is not None else None
        arm = (obj if obj.type == "ARMATURE" else (obj.parent if obj.parent and obj.parent.type == "ARMATURE" else None)) or next((m.object for m in obj.modifiers if m.type == "ARMATURE" and getattr(m, "object", None)), None)
        bones, active_action, actions = ([b.name for b in arm.data.bones] if arm and arm.data else []), None, []
        for t in (obj, arm) if arm is not None and arm is not obj else (obj,):
            if (ad := getattr(t, "animation_data", None)) is not None:
                active_action = active_action or (ad.action.name if ad.action else None)
                for act in ([ad.action] if ad.action else []) + [s.action for tr in getattr(ad, "nla_tracks", ()) for s in getattr(tr, "strips", ()) if s.action]:
                    if act.name not in actions:
                        actions.append(act.name)
        return {**self._base_obj_info(obj, in_vl=obj.name in ctx.view_layer.objects), "parent": obj.parent.name if obj.parent else None,
                "collections": [c.name for c in obj.users_collection], "world_bbox_min": bmin, "world_bbox_max": bmax,
                "vertex_count": len(mesh.vertices) if mesh else 0, "edge_count": len(mesh.edges) if mesh else 0, "polygon_count": len(mesh.polygons) if mesh else 0,
                "uv_layers": [uv.name for uv in mesh.uv_layers] if mesh else [], "vertex_colors": [ca.name for ca in cattrs] if cattrs is not None else [],
                "bones": bones[:200], "bone_count": len(bones), "animation_actions": actions, "active_action": active_action, "modifiers": [m.type for m in obj.modifiers]}

    def _cmd_capture_viewport(self, ctx: Any, _bpy: Any, _V: Any, params: dict[str, Any]) -> dict[str, Any]:
        mode = "scene" if params.get("mode") is None else params.get("mode")
        if mode not in ("camera", "selection", "scene"):
            self._bad(f"Invalid capture mode {mode!r}.")
        return {**scene_io.capture_viewport_png(ctx, mode=mode, resolution=self._opt_int(params, "resolution", 1024, lo=512, hi=2048) or 1024), "mode": mode}

    def _cmd_export_selection_glb(self, ctx: Any, _bpy: Any, _V: Any, params: dict[str, Any]) -> dict[str, Any]:
        if (names := params.get("names")) is not None:
            if not isinstance(names, list) or not all(isinstance(n, str) and n.strip() for n in names):
                self._bad("Parameter 'names' must be a list of object names.")
            if (missing := next((n for n in names if ctx.scene.objects.get(n) is None), None)) is not None:
                raise ArcKepValidationError(404, "OBJECT_NOT_FOUND", f"Object {missing!r} not found in scene.")
        return scene_io.export_selection_glb(ctx, object_names=names or None, max_bytes=self._opt_int(params, "max_bytes", lo=1) or MAX_MESH_UPLOAD_BYTES)

    def _cmd_import_asset(self, ctx: Any, _bpy: Any, Vector: Any, params: dict[str, Any]) -> dict[str, Any]:
        filepath, loc, raw_scale = params.get("filepath"), params.get("location"), params.get("scale_target_m")
        if not isinstance(filepath, str) or not filepath.strip():
            self._bad("Parameter 'filepath' must be a non-empty string.")
        loc_arg = loc if (loc is None or loc == "cursor") else self._parse_vec3(loc, "location")
        if raw_scale is not None and (not self._is_num(raw_scale) or float(raw_scale) < 0.0):
            self._bad("Parameter 'scale_target_m' must be >= 0.")
        jid_val, collection = self._opt_int(params, "arckep_job_id"), params.get("collection")
        created = scene_io.import_asset_to_scene(
            ctx, filepath, fmt=params.get("format"), name_prefix=params.get("name"), location=loc_arg,
            scale_target_m=float(raw_scale) if raw_scale is not None and float(raw_scale) > 0.0 else None,
            collection_name=collection, arckep_job_id=jid_val, arckep_task_type=params.get("arckep_task_type"),
            arckep_model=params.get("arckep_model"), replace_object=params.get("replace_object"))
        vl, created_set = ctx.view_layer, set(created)
        for existing in vl.objects:
            existing.select_set(existing in created_set)
        if created[0].name in vl.objects:
            vl.objects.active = created[0]
        vl.update()
        bmin, bmax = self._world_bbox([o for o in created if o.type == "MESH"] or created, Vector)
        return {"imported_objects": [o.name for o in created], "active_object": created[0].name, "collection": collection, "arckep_job_id": jid_val, "world_bbox_min": bmin, "world_bbox_max": bmax}

    def _cmd_transform_object(self, ctx: Any, bpy: Any, Vector: Any, params: dict[str, Any]) -> dict[str, Any]:
        obj = self._find_obj(ctx, bpy, params)
        if params.get("location") is not None:
            obj.location = self._parse_vec3(params["location"], "location")
        if params.get("rotation_euler_deg") is not None:
            if obj.rotation_mode in ("QUATERNION", "AXIS_ANGLE"):
                obj.rotation_mode = "XYZ"
            obj.rotation_euler = tuple(math.radians(d) for d in self._parse_vec3(params["rotation_euler_deg"], "rotation_euler_deg"))
        if (raw_s := params.get("scale")) is not None:
            obj.scale = ((float(raw_s),) * 3 if float(raw_s) != 0.0 else self._bad("Parameter 'scale' cannot be zero.")) if self._is_num(raw_s) else self._parse_vec3(raw_s, "scale", non_zero=True)
        ground = False if params.get("place_on_ground") is None else params.get("place_on_ground")
        if not isinstance(ground, bool):
            self._bad("Parameter 'place_on_ground' must be a boolean.")
        ctx.view_layer.update()
        targets = [o for o in (obj, *getattr(obj, "children_recursive", ())) if o.type == "MESH"] or [obj]
        if ground:
            min_z = self._world_bbox(targets, Vector)[0][2]
            if obj.parent is None:
                obj.location.z = float(obj.location.z) - min_z
            else:
                mw = obj.matrix_world.copy()
                mw.translation.z -= min_z
                obj.matrix_world = mw
            ctx.view_layer.update()
        bmin, bmax = self._world_bbox(targets, Vector)
        return {"name": obj.name, "location": self._v3(obj.location), "rotation_euler_deg": self._v3(obj.rotation_euler, deg=True), "scale": self._v3(obj.scale), "dimensions": self._v3(obj.dimensions), "world_bbox_min": bmin, "world_bbox_max": bmax}
