# SPDX-License-Identifier: GPL-3.0-or-later
"""Non-blocking background job coordinator for ArcKep 3D Blender add-on.

Network polling and asset downloads run on daemon worker threads, while all
callbacks and ``bpy`` scene mutations execute exclusively inside ``pump()`` on
Blender's main thread (driven by ``bpy.app.timers`` or called directly in tests).
"""

from __future__ import annotations

import json
import os
import queue
import tempfile
import threading
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .api_client import ArcKepApiClient, ArcKepApiError

EventType = Literal["progress", "completed", "failed"]
JobCallback = Callable[["JobEvent"], None]

STATUS_QUEUED = "queued"
STATUS_PROCESSING = "processing"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
TERMINAL_STATUSES = frozenset({STATUS_COMPLETED, STATUS_FAILED})


@dataclass(frozen=True)
class JobEvent:
    """Thread-safe event emitted by background polling workers."""

    job_id: int
    event_type: EventType
    status: str
    job: dict[str, Any]
    local_path: str | None = None
    error: str | None = None


@dataclass
class _TrackedJob:
    stop_event: threading.Event
    thread: threading.Thread
    on_progress: JobCallback | None = None
    on_completed: JobCallback | None = None
    on_failed: JobCallback | None = None


def _safe_asset_filename(job_id: int, output: dict[str, Any], asset_url: str = "") -> str:
    raw_name = str(output.get("file_name") or "").strip()
    base = os.path.basename(raw_name) if raw_name else ""
    if not base or base in (".", ".."):
        url_ext = Path(urllib.parse.urlsplit(asset_url).path).suffix.lower() if asset_url else ""
        if url_ext == ".zip":
            base = f"arckep-{job_id}-obj.zip"
        else:
            fmt = str(output.get("format") or "glb").strip().lstrip(".") or "glb"
            base = f"arckep-{job_id}.{fmt}"
    return f"{job_id}-{base}" if not base.startswith(f"{job_id}-") else base


def download_completed_job_asset(
    client: ArcKepApiClient, job: dict[str, Any], download_dir: Path
) -> str | None:
    """Download the primary 3D asset or multiview sheet for a completed job without Bearer auth."""
    job_id = int(job["id"])
    output = job.get("output")
    if not isinstance(output, dict):
        return None

    mv_urls = output.get("multiview_urls")
    if isinstance(mv_urls, dict) and mv_urls:
        mv_dir = download_dir / f"multiview-{job_id}"
        mv_dir.mkdir(parents=True, exist_ok=True)
        saved_views: dict[str, str] = {}
        for view_key in ("front", "left", "back", "right"):
            raw_url = mv_urls.get(view_key)
            if isinstance(raw_url, str) and raw_url.strip():
                dest_png = mv_dir / f"{view_key}.png"
                client.download_file(raw_url.strip(), dest_png)
                saved_views[view_key] = str(dest_png)
        manifest_path = mv_dir / "multiview_manifest.json"
        manifest_path.write_text(
            json.dumps({"job_id": job_id, "format": "multiview", "views": saved_views}),
            encoding="utf-8",
        )
        return str(manifest_path)

    pc_glb = output.get("point_cloud_glb_url")
    if isinstance(pc_glb, str) and pc_glb.strip():
        dest = download_dir / f"{job_id}-pointcloud.glb"
        client.download_file(pc_glb.strip(), dest)
        return str(dest)

    ply_url = output.get("ply_url")
    if isinstance(ply_url, str) and ply_url.strip():
        dest = download_dir / f"{job_id}-pointcloud.ply"
        client.download_file(ply_url.strip(), dest)
        return str(dest)

    asset_url = output.get("file_url") or output.get("glb_url") or output.get("splat_url")
    if isinstance(asset_url, str) and asset_url.strip():
        dest = download_dir / _safe_asset_filename(job_id, output, asset_url.strip())
        client.download_file(asset_url.strip(), dest)
        return str(dest)
    return None


class ArcKepJobQueue:
    """Thread-safe poller and asset downloader decoupled from ``bpy``."""

    def __init__(
        self,
        poll_interval: float = 1.5,
        timer_interval: float = 0.2,
        download_dir: str | Path | None = None,
    ) -> None:
        self.poll_interval = max(0.01, float(poll_interval))
        self.timer_interval = max(0.05, float(timer_interval))
        self._download_dir = Path(download_dir) if download_dir else None
        self._events: queue.Queue[JobEvent] = queue.Queue()
        self._lock = threading.Lock()
        self._jobs: dict[int, _TrackedJob] = {}
        self._listeners: list[JobCallback] = []
        self._shutdown_event = threading.Event()
        self._timer_callback: Callable[[], float | None] = self._bpy_timer_tick
        self._timer_registered = False

    @property
    def active_count(self) -> int:
        with self._lock:
            return sum(1 for item in self._jobs.values() if item.thread.is_alive())

    def active_job_ids(self) -> list[int]:
        with self._lock:
            return [jid for jid, item in self._jobs.items() if item.thread.is_alive()]

    def add_listener(self, callback: JobCallback) -> None:
        with self._lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def remove_listener(self, callback: JobCallback) -> None:
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    def ensure_download_dir(self) -> Path:
        return self._ensure_download_dir()

    def _ensure_download_dir(self) -> Path:
        with self._lock:
            if self._download_dir is None:
                self._download_dir = Path(tempfile.mkdtemp(prefix="arckep_3d_assets_"))
            self._download_dir.mkdir(parents=True, exist_ok=True)
            return self._download_dir

    def submit(
        self,
        client: ArcKepApiClient,
        job_id: int,
        *,
        download_asset: bool = True,
        on_progress: JobCallback | None = None,
        on_completed: JobCallback | None = None,
        on_failed: JobCallback | None = None,
    ) -> None:
        """Start polling ``GET /models3d/generations/{job_id}`` in a daemon thread."""
        jid = int(job_id)
        if self._shutdown_event.is_set():
            raise RuntimeError("ArcKepJobQueue is shut down.")
        stop_event = threading.Event()
        worker = threading.Thread(
            target=self._poll_worker,
            args=(client, jid, download_asset, stop_event),
            name=f"arckep-3d-job-{jid}",
            daemon=True,
        )
        with self._lock:
            existing = self._jobs.get(jid)
            if existing is not None:
                existing.stop_event.set()
            self._jobs[jid] = _TrackedJob(
                stop_event=stop_event,
                thread=worker,
                on_progress=on_progress,
                on_completed=on_completed,
                on_failed=on_failed,
            )
        worker.start()

    def _poll_worker(
        self,
        client: ArcKepApiClient,
        job_id: int,
        download_asset: bool,
        stop_event: threading.Event,
    ) -> None:
        last_job: dict[str, Any] = {"id": job_id, "status": STATUS_QUEUED}
        try:
            while not stop_event.is_set() and not self._shutdown_event.is_set():
                job = client.get_generation(job_id)
                last_job = job
                status = str(job.get("status") or "")
                if status == STATUS_COMPLETED:
                    local_path = (
                        download_completed_job_asset(client, job, self._ensure_download_dir())
                        if download_asset
                        else None
                    )
                    self._events.put(
                        JobEvent(
                            job_id=job_id,
                            event_type="completed",
                            status=STATUS_COMPLETED,
                            job=job,
                            local_path=local_path,
                        )
                    )
                    return
                if status == STATUS_FAILED:
                    err = str(job.get("error") or "3D generation job failed.")
                    self._events.put(
                        JobEvent(
                            job_id=job_id,
                            event_type="failed",
                            status=STATUS_FAILED,
                            job=job,
                            error=err,
                        )
                    )
                    return
                self._events.put(
                    JobEvent(job_id=job_id, event_type="progress", status=status, job=job)
                )
                if stop_event.wait(self.poll_interval) or self._shutdown_event.is_set():
                    return
        except (ArcKepApiError, OSError, Exception) as exc:
            if stop_event.is_set() or self._shutdown_event.is_set():
                return
            self._events.put(
                JobEvent(
                    job_id=job_id,
                    event_type="failed",
                    status=STATUS_FAILED,
                    job=last_job,
                    error=str(exc),
                )
            )

    def pump(self) -> list[JobEvent]:
        """Drain queued events and invoke callbacks on the calling thread."""
        drained: list[JobEvent] = []
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                break
            drained.append(event)
            with self._lock:
                tracked = self._jobs.get(event.job_id)
                listeners = list(self._listeners)
                if event.event_type in ("completed", "failed"):
                    self._jobs.pop(event.job_id, None)
            if tracked is not None:
                cb = (
                    tracked.on_completed
                    if event.event_type == "completed"
                    else tracked.on_failed
                    if event.event_type == "failed"
                    else tracked.on_progress
                )
                if cb is not None:
                    cb(event)
            for listener in listeners:
                listener(event)
        return drained

    def _bpy_timer_tick(self) -> float | None:
        if self._shutdown_event.is_set():
            self._timer_registered = False
            return None
        try:
            self.pump()
        except Exception as exc:  # pragma: no cover - defensive timer guard
            print(f"[ArcKep 3D] Timer callback error: {exc}")
        return self.timer_interval

    def is_bpy_timer_registered(self) -> bool:
        try:
            import bpy  # type: ignore[import-not-found]
        except ImportError:
            return False
        return bool(bpy.app.timers.is_registered(self._timer_callback))

    def register_bpy_timer(self) -> None:
        try:
            import bpy  # type: ignore[import-not-found]
        except ImportError:
            return
        if not bpy.app.timers.is_registered(self._timer_callback):
            bpy.app.timers.register(
                self._timer_callback, first_interval=self.timer_interval, persistent=True
            )
        self._timer_registered = True

    def unregister_bpy_timer(self) -> None:
        try:
            import bpy  # type: ignore[import-not-found]
        except ImportError:
            self._timer_registered = False
            return
        if bpy.app.timers.is_registered(self._timer_callback):
            bpy.app.timers.unregister(self._timer_callback)
        self._timer_registered = False

    def cancel(self, job_id: int) -> None:
        with self._lock:
            tracked = self._jobs.pop(int(job_id), None)
        if tracked is not None:
            tracked.stop_event.set()

    def shutdown(self, wait: bool = False, timeout: float = 2.0) -> None:
        """Stop all polling workers and detach from ``bpy.app.timers``."""
        self._shutdown_event.set()
        self.unregister_bpy_timer()
        with self._lock:
            tracked_items = list(self._jobs.values())
            self._jobs.clear()
        for item in tracked_items:
            item.stop_event.set()
        if wait:
            for item in tracked_items:
                item.thread.join(timeout=timeout)
