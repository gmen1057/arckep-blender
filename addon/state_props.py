# SPDX-License-Identifier: GPL-3.0-or-later
"""Transient Blender ``PropertyGroup`` definitions for Create, Process, Archive, and Window state.

All properties carry ``options={'SKIP_SAVE'}`` on ``WindowManager`` so neither credentials
nor local session paths are ever serialized into ``.blend`` project files.
"""

from __future__ import annotations

from .catalog_defs import (
    GENERATION_MODEL_ITEMS,
    HUNYUAN_GEN_TYPE_ITEMS,
    HUNYUAN_QUALITY_ITEMS,
    IDOL_IMAGE_TYPE_ITEMS,
    MESHY_MODEL_TYPE_ITEMS,
    MULTIVIEW_OP_ITEMS,
    OUTPUT_FORMAT_ITEMS,
    RODIN_MESH_OPTION_ITEMS,
    RODIN_TIER_ITEMS,
    RODIN_VARIANT_ITEMS,
    TOPOLOGY_ITEMS,
    TRIPO_GEO_QUALITY_ITEMS,
    TRIPO_TEX_QUALITY_ITEMS,
    VIEWPORT_CAPTURE_MODE_ITEMS,
    allowed_input_modes_for_model,
)
from .process_defs import (
    COMPLETION_MODE_ITEMS,
    CONVERT_FORMAT_ITEMS,
    DECIMATE_MODEL_ITEMS,
    FBX_PRESET_ITEMS,
    MESHY_TEX_RES_ITEMS,
    ORIENTATION_AXIS_ITEMS,
    PRINT_FORMAT_ITEMS,
    PRINT_SCALE_AXIS_ITEMS,
    PROCESS_OP_ITEMS,
    RIG_MODEL_ITEMS,
    RIG_OUT_FORMAT_ITEMS,
    RIG_SPEC_ITEMS,
    RIG_TYPE_ITEMS,
    SEG_GRANULARITY_ITEMS,
    SMART_GRANULARITY_ITEMS,
    SMART_SEG_TYPE_ITEMS,
    TEXTURE_MODEL_ITEMS,
)

try:
    import bpy  # type: ignore[import-not-found]
    from bpy.props import (  # type: ignore[import-not-found]
        BoolProperty,
        CollectionProperty,
        EnumProperty,
        FloatProperty,
        IntProperty,
        StringProperty,
    )
except ImportError:  # pragma: no cover
    bpy = None

UI_TAB_ITEMS = (
    ("CREATE", "Create", "Generate 3D meshes, splats, and multiviews on 3D cursor"),
    ("PROCESS", "Process", "Retexture, retopo, UV, segment, rig, and convert meshes"),
    ("ARCHIVE", "Archive", "Account 3D history and 0 RUB STL/3MF print export"),
    ("MCP", "MCP", "Local 127.0.0.1 scene bridge settings for arckep-blender-mcp"),
)

_SKIP = {"SKIP_SAVE"}

if bpy is not None:

    class ArcKepWindowState(bpy.types.PropertyGroup):
        """Runtime connection, balance, tab, and budget confirmation state."""

        connected: BoolProperty(default=False, options=_SKIP)  # type: ignore[valid-type]
        balance_rub: StringProperty(default="", options=_SKIP)  # type: ignore[valid-type]
        is_corporate: BoolProperty(default=False, options=_SKIP)  # type: ignore[valid-type]
        corporate_name: StringProperty(default="", options=_SKIP)  # type: ignore[valid-type]
        last_error: StringProperty(default="", options=_SKIP)  # type: ignore[valid-type]
        status_message: StringProperty(default="", options=_SKIP)  # type: ignore[valid-type]
        ui_tab: EnumProperty(name="Tab", items=UI_TAB_ITEMS, default="CREATE", options=_SKIP)  # type: ignore[valid-type]
        last_job_id: IntProperty(default=0, min=0, options=_SKIP)  # type: ignore[valid-type]
        pending_confirm_cost_rub: FloatProperty(default=0.0, min=0.0, precision=2, options=_SKIP)  # type: ignore[valid-type]
        pending_confirm_target: StringProperty(default="", options=_SKIP)  # type: ignore[valid-type]

    def _dynamic_create_input_items(
        self: object, _context: object
    ) -> tuple[tuple[str, str, str, int], ...]:
        model = str(getattr(self, "model", "tripo-h3.1") or "tripo-h3.1")
        mv_op = str(getattr(self, "multiview_op", "generate") or "generate")
        return allowed_input_modes_for_model(model, mv_op)

    def _sync_create_input_mode(self: object, _context: object) -> None:
        model = str(getattr(self, "model", "tripo-h3.1") or "tripo-h3.1")
        mv_op = str(getattr(self, "multiview_op", "generate") or "generate")
        allowed = allowed_input_modes_for_model(model, mv_op)
        if str(getattr(self, "input_mode", "")) not in {item[0] for item in allowed}:
            setattr(self, "input_mode", allowed[0][0])

    class ArcKepCreateProps(bpy.types.PropertyGroup):
        """Form state for 3D model creation on the 3D cursor."""

        model: EnumProperty(
            name="Model",
            items=GENERATION_MODEL_ITEMS,
            default="tripo-h3.1",
            update=_sync_create_input_mode,
            options=_SKIP,
        )  # type: ignore[valid-type]
        input_mode: EnumProperty(name="Input", items=_dynamic_create_input_items, options=_SKIP)  # type: ignore[valid-type]
        viewport_capture_mode: EnumProperty(
            name="Capture", items=VIEWPORT_CAPTURE_MODE_ITEMS, default="selection", options=_SKIP
        )  # type: ignore[valid-type]
        viewport_resolution: IntProperty(name="Resolution", default=512, min=64, max=2048, options=_SKIP)  # type: ignore[valid-type]
        prompt: StringProperty(name="Prompt", default="", maxlen=1024, options=_SKIP)  # type: ignore[valid-type]
        negative_prompt: StringProperty(name="Negative", default="", maxlen=255, options=_SKIP)  # type: ignore[valid-type]
        image_path: StringProperty(name="Image", subtype="FILE_PATH", default="", options=_SKIP)  # type: ignore[valid-type]
        view_front_path: StringProperty(name="Front", subtype="FILE_PATH", default="", options=_SKIP)  # type: ignore[valid-type]
        view_left_path: StringProperty(name="Left", subtype="FILE_PATH", default="", options=_SKIP)  # type: ignore[valid-type]
        view_back_path: StringProperty(name="Back", subtype="FILE_PATH", default="", options=_SKIP)  # type: ignore[valid-type]
        view_right_path: StringProperty(name="Right", subtype="FILE_PATH", default="", options=_SKIP)  # type: ignore[valid-type]
        extra_image_5_path: StringProperty(name="Extra #5", subtype="FILE_PATH", default="", options=_SKIP)  # type: ignore[valid-type]
        source_multiview_job_id: IntProperty(name="Multiview Job ID", default=0, min=0, options=_SKIP)  # type: ignore[valid-type]

        texture: BoolProperty(name="Texture", default=True, options=_SKIP)  # type: ignore[valid-type]
        enable_pbr: BoolProperty(name="PBR Maps", default=False, options=_SKIP)  # type: ignore[valid-type]
        topology: EnumProperty(name="Topology", items=TOPOLOGY_ITEMS, default="triangle", options=_SKIP)  # type: ignore[valid-type]
        use_polycount: BoolProperty(name="Custom Polycount", default=False, options=_SKIP)  # type: ignore[valid-type]
        polycount: IntProperty(name="Polycount", default=30000, min=48, max=2000000, options=_SKIP)  # type: ignore[valid-type]
        output_format: EnumProperty(name="Format", items=OUTPUT_FORMAT_ITEMS, default="glb", options=_SKIP)  # type: ignore[valid-type]

        meshy_model_type: EnumProperty(name="Mesh Mode", items=MESHY_MODEL_TYPE_ITEMS, default="standard", options=_SKIP)  # type: ignore[valid-type]
        enable_rigging: BoolProperty(name="Auto-Rig", default=False, options=_SKIP)  # type: ignore[valid-type]
        enable_animation: BoolProperty(name="Animate", default=False, options=_SKIP)  # type: ignore[valid-type]
        animation_action_id: IntProperty(name="Action ID", default=92, min=0, max=696, options=_SKIP)  # type: ignore[valid-type]

        texture_quality: EnumProperty(name="Tex Quality", items=TRIPO_TEX_QUALITY_ITEMS, default="standard", options=_SKIP)  # type: ignore[valid-type]
        geometry_quality: EnumProperty(name="Geo Quality", items=TRIPO_GEO_QUALITY_ITEMS, default="standard", options=_SKIP)  # type: ignore[valid-type]
        smart_low_poly: BoolProperty(name="Smart Low-Poly", default=False, options=_SKIP)  # type: ignore[valid-type]
        generate_parts: BoolProperty(name="Generate Parts", default=False, options=_SKIP)  # type: ignore[valid-type]
        auto_size: BoolProperty(name="Real-World Scale", default=False, options=_SKIP)  # type: ignore[valid-type]
        delight: BoolProperty(name="Delight Albedo", default=False, options=_SKIP)  # type: ignore[valid-type]

        rodin_variant: EnumProperty(name="Variant", items=RODIN_VARIANT_ITEMS, default="standard", options=_SKIP)  # type: ignore[valid-type]
        rodin_tier: EnumProperty(name="Tier", items=RODIN_TIER_ITEMS, default="Gen-2.5-High", options=_SKIP)  # type: ignore[valid-type]
        rodin_mesh_option: EnumProperty(name="Mesh Preset", items=RODIN_MESH_OPTION_ITEMS, default="Auto", options=_SKIP)  # type: ignore[valid-type]
        high_pack: BoolProperty(name="4K High Pack", default=False, options=_SKIP)  # type: ignore[valid-type]
        tpose: BoolProperty(name="T-Pose", default=False, options=_SKIP)  # type: ignore[valid-type]
        hd_texture: BoolProperty(name="HD Texture", default=False, options=_SKIP)  # type: ignore[valid-type]
        texture_delight: BoolProperty(name="Delight", default=False, options=_SKIP)  # type: ignore[valid-type]
        enable_creative_mode: BoolProperty(name="Creative Mode", default=False, options=_SKIP)  # type: ignore[valid-type]
        is_micro: BoolProperty(name="Micro Detail", default=False, options=_SKIP)  # type: ignore[valid-type]
        use_seed: BoolProperty(name="Fixed Seed", default=False, options=_SKIP)  # type: ignore[valid-type]
        seed: IntProperty(name="Seed", default=0, min=0, max=2147483647, options=_SKIP)  # type: ignore[valid-type]

        hunyuan_quality: EnumProperty(name="Quality", items=HUNYUAN_QUALITY_ITEMS, default="quality", options=_SKIP)  # type: ignore[valid-type]
        hunyuan_generate_type: EnumProperty(name="Mode", items=HUNYUAN_GEN_TYPE_ITEMS, default="Normal", options=_SKIP)  # type: ignore[valid-type]
        use_face_count: BoolProperty(name="Custom Faces", default=False, options=_SKIP)  # type: ignore[valid-type]
        face_count: IntProperty(name="Face Count", default=500000, min=40000, max=1500000, options=_SKIP)  # type: ignore[valid-type]

        multiview_op: EnumProperty(
            name="Operation",
            items=MULTIVIEW_OP_ITEMS,
            default="generate",
            update=_sync_create_input_mode,
            options=_SKIP,
        )  # type: ignore[valid-type]
        edit_front_prompt: StringProperty(name="Front Edit", default="", maxlen=1024, options=_SKIP)  # type: ignore[valid-type]
        edit_left_prompt: StringProperty(name="Left Edit", default="", maxlen=1024, options=_SKIP)  # type: ignore[valid-type]
        edit_back_prompt: StringProperty(name="Back Edit", default="", maxlen=1024, options=_SKIP)  # type: ignore[valid-type]
        edit_right_prompt: StringProperty(name="Right Edit", default="", maxlen=1024, options=_SKIP)  # type: ignore[valid-type]
        last_multiview_info: StringProperty(default="", options=_SKIP)  # type: ignore[valid-type]

        idol_image_type: EnumProperty(name="Image Type", items=IDOL_IMAGE_TYPE_ITEMS, default="synthetic", options=_SKIP)  # type: ignore[valid-type]
        idol_num_gaussians: IntProperty(name="Gaussians", default=262144, min=32768, max=1048576, options=_SKIP)  # type: ignore[valid-type]
        idol_steps: IntProperty(name="Steps", default=50, min=1, max=50, options=_SKIP)  # type: ignore[valid-type]
        idol_guidance: FloatProperty(name="Guidance", default=3.0, min=0.0, max=10.0, options=_SKIP)  # type: ignore[valid-type]

        import_to_cursor: BoolProperty(name="Place at 3D Cursor", default=True, options=_SKIP)  # type: ignore[valid-type]
        scale_target_m: FloatProperty(name="Target Size (m, 0=raw)", default=0.0, min=0.0, max=100.0, options=_SKIP)  # type: ignore[valid-type]
        estimated_cost_rub: StringProperty(default="", options=_SKIP)  # type: ignore[valid-type]

    class ArcKepProcessProps(bpy.types.PropertyGroup):
        """Form state for 1-click post-processing of selected Blender meshes."""

        operation: EnumProperty(name="Operation", items=PROCESS_OP_ITEMS, default="texture", options=_SKIP)  # type: ignore[valid-type]
        use_source_job_id: BoolProperty(name="Use Cloud Job ID", default=False, options=_SKIP)  # type: ignore[valid-type]
        source_job_id: IntProperty(name="Source Job ID", default=0, min=0, options=_SKIP)  # type: ignore[valid-type]
        custom_mesh_path: StringProperty(name="External Mesh", subtype="FILE_PATH", default="", options=_SKIP)  # type: ignore[valid-type]
        hide_original: BoolProperty(name="Hide Source Mesh", default=False, options=_SKIP)  # type: ignore[valid-type]

        texture_model: EnumProperty(name="Engine", items=TEXTURE_MODEL_ITEMS, default="tripo", options=_SKIP)  # type: ignore[valid-type]
        texture_prompt: StringProperty(name="Texture Prompt", default="", maxlen=800, options=_SKIP)  # type: ignore[valid-type]
        style_image_path: StringProperty(name="Style Image", subtype="FILE_PATH", default="", options=_SKIP)  # type: ignore[valid-type]
        style_image_2_path: StringProperty(name="Style Image #2", subtype="FILE_PATH", default="", options=_SKIP)  # type: ignore[valid-type]
        texture_resolution: EnumProperty(name="Resolution", items=MESHY_TEX_RES_ITEMS, default="2k", options=_SKIP)  # type: ignore[valid-type]
        texture_quality: EnumProperty(name="Quality", items=TRIPO_TEX_QUALITY_ITEMS, default="standard", options=_SKIP)  # type: ignore[valid-type]
        enable_pbr: BoolProperty(name="PBR Maps", default=True, options=_SKIP)  # type: ignore[valid-type]
        enable_original_uv: BoolProperty(name="Keep Original UV", default=True, options=_SKIP)  # type: ignore[valid-type]
        bake: BoolProperty(name="Bake Textures", default=True, options=_SKIP)  # type: ignore[valid-type]

        decimate_model: EnumProperty(name="Retopo Version", items=DECIMATE_MODEL_ITEMS, default="v2.0", options=_SKIP)  # type: ignore[valid-type]
        face_limit: IntProperty(name="Face Limit", default=5000, min=48, max=2000000, options=_SKIP)  # type: ignore[valid-type]
        quad: BoolProperty(name="Quad Mesh", default=False, options=_SKIP)  # type: ignore[valid-type]
        smart_uv: BoolProperty(name="Smart UV (v3.0)", default=False, options=_SKIP)  # type: ignore[valid-type]

        export_orientation: EnumProperty(name="Orientation", items=ORIENTATION_AXIS_ITEMS, default="+x", options=_SKIP)  # type: ignore[valid-type]
        compress_geometry: BoolProperty(name="Compress Geometry", default=False, options=_SKIP)  # type: ignore[valid-type]

        segmentation_granularity: EnumProperty(
            name="Granularity", items=SEG_GRANULARITY_ITEMS, default="balanced", options=_SKIP
        )  # type: ignore[valid-type]
        split_by_connectivity: BoolProperty(name="Split Disconnected", default=True, options=_SKIP)  # type: ignore[valid-type]
        seg_type: EnumProperty(name="Source Type", items=SMART_SEG_TYPE_ITEMS, default="model", options=_SKIP)  # type: ignore[valid-type]
        smart_granularity: EnumProperty(
            name="Smart Granularity", items=SMART_GRANULARITY_ITEMS, default="medium", options=_SKIP
        )  # type: ignore[valid-type]
        segment_hint: StringProperty(name="Hint", default="", maxlen=512, options=_SKIP)  # type: ignore[valid-type]
        completion_mode: EnumProperty(name="Completion", items=COMPLETION_MODE_ITEMS, default="ai_completion", options=_SKIP)  # type: ignore[valid-type]
        part_names_csv: StringProperty(name="Part Names (CSV)", default="", options=_SKIP)  # type: ignore[valid-type]

        rig_model: EnumProperty(name="Rig Version", items=RIG_MODEL_ITEMS, default="v3.0-20260909", options=_SKIP)  # type: ignore[valid-type]
        rig_type: EnumProperty(name="Creature Type", items=RIG_TYPE_ITEMS, default="biped", options=_SKIP)  # type: ignore[valid-type]
        rig_spec: EnumProperty(name="Bone Profile", items=RIG_SPEC_ITEMS, default="tripo", options=_SKIP)  # type: ignore[valid-type]
        out_format: EnumProperty(name="Output Format", items=RIG_OUT_FORMAT_ITEMS, default="glb", options=_SKIP)  # type: ignore[valid-type]
        animations_csv: StringProperty(name="Animations (CSV)", default="preset:idle,preset:walk", options=_SKIP)  # type: ignore[valid-type]
        animate_in_place: BoolProperty(name="In-Place", default=False, options=_SKIP)  # type: ignore[valid-type]
        export_with_geometry: BoolProperty(name="Include Mesh", default=True, options=_SKIP)  # type: ignore[valid-type]
        rig_check_summary: StringProperty(default="", options=_SKIP)  # type: ignore[valid-type]

        convert_format: EnumProperty(name="Target Format", items=CONVERT_FORMAT_ITEMS, default="FBX", options=_SKIP)  # type: ignore[valid-type]
        fbx_preset: EnumProperty(name="FBX Preset", items=FBX_PRESET_ITEMS, default="blender", options=_SKIP)  # type: ignore[valid-type]
        use_convert_face_limit: BoolProperty(name="Limit Faces", default=False, options=_SKIP)  # type: ignore[valid-type]
        force_symmetry: BoolProperty(name="Force Symmetry", default=False, options=_SKIP)  # type: ignore[valid-type]
        flatten_bottom: BoolProperty(name="Flatten Bottom", default=False, options=_SKIP)  # type: ignore[valid-type]
        pack_uv: BoolProperty(name="Pack UV", default=False, options=_SKIP)  # type: ignore[valid-type]
        export_vertex_colors: BoolProperty(name="Vertex Colors", default=False, options=_SKIP)  # type: ignore[valid-type]
        pivot_to_center_bottom: BoolProperty(name="Bottom Pivot", default=False, options=_SKIP)  # type: ignore[valid-type]
        scale_factor: FloatProperty(name="Scale Factor", default=1.0, min=0.01, max=1000.0, options=_SKIP)  # type: ignore[valid-type]
        texture_size: IntProperty(name="Texture Size", default=4096, min=128, max=8192, options=_SKIP)  # type: ignore[valid-type]

        estimated_cost_rub: StringProperty(default="", options=_SKIP)  # type: ignore[valid-type]

    class ArcKepArchiveItem(bpy.types.PropertyGroup):
        """Single row in the account 3D generation archive."""

        job_id: IntProperty(default=0, options=_SKIP)  # type: ignore[valid-type]
        model: StringProperty(default="", options=_SKIP)  # type: ignore[valid-type]
        status: StringProperty(default="", options=_SKIP)  # type: ignore[valid-type]
        cost_rub: StringProperty(default="", options=_SKIP)  # type: ignore[valid-type]
        asset_format: StringProperty(default="", options=_SKIP)  # type: ignore[valid-type]
        prompt: StringProperty(default="", options=_SKIP)  # type: ignore[valid-type]
        created_at: StringProperty(default="", options=_SKIP)  # type: ignore[valid-type]

    class ArcKepArchiveProps(bpy.types.PropertyGroup):
        """Archive pagination state and 0 RUB 3D print export parameters."""

        items: CollectionProperty(type=ArcKepArchiveItem, options=_SKIP)  # type: ignore[valid-type]
        active_index: IntProperty(default=0, min=0, options=_SKIP)  # type: ignore[valid-type]
        has_more: BoolProperty(default=False, options=_SKIP)  # type: ignore[valid-type]
        next_before_id: IntProperty(default=0, min=0, options=_SKIP)  # type: ignore[valid-type]
        page_limit: IntProperty(name="Page Size", default=20, min=1, max=100, options=_SKIP)  # type: ignore[valid-type]

        print_job_id: IntProperty(name="Job ID", default=0, min=0, options=_SKIP)  # type: ignore[valid-type]
        print_format: EnumProperty(name="Format", items=PRINT_FORMAT_ITEMS, default="stl", options=_SKIP)  # type: ignore[valid-type]
        target_size_mm: FloatProperty(name="Size (mm)", default=100.0, min=5.0, max=2000.0, options=_SKIP)  # type: ignore[valid-type]
        scale_axis: EnumProperty(name="Scale Axis", items=PRINT_SCALE_AXIS_ITEMS, default="max", options=_SKIP)  # type: ignore[valid-type]
        z_up: BoolProperty(name="Z-Up Orientation", default=True, options=_SKIP)  # type: ignore[valid-type]
        place_on_bed: BoolProperty(name="Place on Print Bed", default=True, options=_SKIP)  # type: ignore[valid-type]
        print_filepath: StringProperty(name="Save To", subtype="FILE_PATH", default="", options=_SKIP)  # type: ignore[valid-type]
        import_printed_into_scene: BoolProperty(name="Import into Scene", default=False, options=_SKIP)  # type: ignore[valid-type]

    STATE_CLASSES = (
        ArcKepWindowState,
        ArcKepCreateProps,
        ArcKepProcessProps,
        ArcKepArchiveItem,
        ArcKepArchiveProps,
    )
else:  # pragma: no cover
    STATE_CLASSES = ()
