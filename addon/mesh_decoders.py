# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure-stdlib decoders for ``.3mf`` XML archives, ``.splat`` point clouds, and Rodin ``.zip``."""

from __future__ import annotations

import shutil
import struct
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

from .api_validation import ArcKepValidationError

MAX_UNCOMPRESSED_ARCHIVE_BYTES = 250 * 1024 * 1024  # 250 MiB

_UNIT_TO_METERS = {
    "micron": 1e-6,
    "millimeter": 0.001,
    "centimeter": 0.01,
    "inch": 0.0254,
    "foot": 0.3048,
    "meter": 1.0,
}


def _require_bpy() -> Any:
    try:
        import bpy  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Blender 'bpy' is required for mesh decoding.") from exc
    return bpy


def _enforce_zip_quota(zf: zipfile.ZipFile) -> None:
    total = sum(info.file_size for info in zf.infolist())
    if total > MAX_UNCOMPRESSED_ARCHIVE_BYTES:
        raise ArcKepValidationError(
            413,
            "ARCHIVE_TOO_LARGE",
            f"Uncompressed archive size {total / (1024 * 1024):.1f} MiB exceeds 250 MiB limit.",
        )


def import_3mf_meshes(context: Any, path: Path, prefix: str) -> None:
    """Parse ``3D/3dmodel.model`` inside a ``.3mf`` archive into ``bpy.data.meshes`` (in meters)."""
    bpy = _require_bpy()
    with zipfile.ZipFile(path, "r") as zf:
        _enforce_zip_quota(zf)
        model_entry = next(
            (n for n in zf.namelist() if n == "3D/3dmodel.model" or n.lower().endswith(".model")),
            None,
        )
        if model_entry is None:
            raise ArcKepValidationError(400, "INVALID_3MF", "3MF archive lacks 3D/3dmodel.model.")
        raw_xml = zf.read(model_entry)
    upper_xml = raw_xml.upper()
    if b"<!DOCTYPE" in upper_xml or b"<!ENTITY" in upper_xml:
        raise ArcKepValidationError(
            400, "UNSAFE_XML", "XML DOCTYPE and ENTITY declarations are forbidden in 3MF files."
        )
    root = ET.fromstring(raw_xml)
    scale = _UNIT_TO_METERS.get(root.attrib.get("unit", "millimeter").lower(), 0.001)
    idx = 0
    for elem in root.iter():
        if not (elem.tag == "mesh" or elem.tag.endswith("}mesh")):
            continue
        verts: list[tuple[float, float, float]] = []
        tris: list[tuple[int, int, int]] = []
        for sub in elem:
            if sub.tag == "vertices" or sub.tag.endswith("}vertices"):
                for v in sub:
                    verts.append(
                        (
                            float(v.attrib["x"]) * scale,
                            float(v.attrib["y"]) * scale,
                            float(v.attrib["z"]) * scale,
                        )
                    )
            elif sub.tag == "triangles" or sub.tag.endswith("}triangles"):
                for t in sub:
                    tris.append((int(t.attrib["v1"]), int(t.attrib["v2"]), int(t.attrib["v3"])))
        if verts:
            idx += 1
            mname = f"{prefix}_{idx}" if idx > 1 else prefix
            mesh = bpy.data.meshes.new(mname)
            mesh.from_pydata(verts, [], tris)
            mesh.update()
            obj = bpy.data.objects.new(mname, mesh)
            context.scene.collection.objects.link(obj)


def import_splat_cloud(context: Any, path: Path, prefix: str) -> None:
    """Decode 32-byte Gaussian Splat records into a Blender vertex cloud with ``BYTE_COLOR``."""
    bpy = _require_bpy()
    raw = path.read_bytes()
    if len(raw) < 32 or len(raw) % 32 != 0:
        raise ArcKepValidationError(400, "INVALID_SPLAT", "Invalid 32-byte .splat binary file.")
    count = len(raw) // 32
    verts: list[tuple[float, float, float]] = []
    colors_flat: list[float] = []
    for offset in range(0, len(raw), 32):
        x, y, z = struct.unpack_from("<3f", raw, offset)
        r, g, b, a = struct.unpack_from("<4B", raw, offset + 24)
        verts.append((x, y, z))
        colors_flat.extend((r / 255.0, g / 255.0, b / 255.0, a / 255.0))
    mesh = bpy.data.meshes.new(prefix)
    mesh.from_pydata(verts, [], [])
    mesh.update()
    col_attr = mesh.color_attributes.new(name="Col", type="BYTE_COLOR", domain="POINT")
    if len(col_attr.data) == count:
        col_attr.data.foreach_set("color", colors_flat)
    obj = bpy.data.objects.new(prefix, mesh)
    context.scene.collection.objects.link(obj)


def unpack_zip_and_find_mesh(zip_path: Path) -> Path:
    """Safely extract a mesh ZIP archive (guarding against zip-slip & zip-bombs) and find mesh."""
    unpack_dir = zip_path.parent / f"{zip_path.stem}_unpacked"
    if unpack_dir.exists():
        shutil.rmtree(unpack_dir)
    unpack_dir.mkdir(parents=True, exist_ok=True)
    root_resolved = unpack_dir.resolve()
    with zipfile.ZipFile(zip_path, "r") as zf:
        _enforce_zip_quota(zf)
        written = 0
        for member in zf.infolist():
            target = (unpack_dir / member.filename).resolve()
            if not target.is_relative_to(root_resolved):
                raise ArcKepValidationError(400, "UNSAFE_ARCHIVE", "ZIP contains path traversal.")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member, "r") as src, target.open("wb") as dst:
                while chunk := src.read(65536):
                    written += len(chunk)
                    if written > MAX_UNCOMPRESSED_ARCHIVE_BYTES:
                        raise ArcKepValidationError(
                            413, "ARCHIVE_TOO_LARGE", "Extracted archive exceeded 250 MiB limit."
                        )
                    dst.write(chunk)
    for ext in (".glb", ".gltf", ".fbx", ".obj", ".stl", ".ply", ".usdz"):
        found = sorted(unpack_dir.rglob(f"*{ext}"))
        if found:
            return found[0]
    raise ArcKepValidationError(400, "EMPTY_ARCHIVE", "No supported 3D mesh found inside ZIP.")
