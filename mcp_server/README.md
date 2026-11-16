# `arckep-blender-mcp`

Local stdio **Model Context Protocol (MCP)** server connecting **Blender 4.2 LTS – 5.x** (via the **ArcKep 3D** Blender extension's loopback TCP bridge) with the **ArcKep Public 3D API** (`/api/v1/models3d`).

Licensed under **MIT**. Zero `exec()` of arbitrary Python code in Blender — all scene inspection, viewport rendering, asset import, and object placement run through typed loopback commands on `127.0.0.1`.

---

## Quick Start (`uvx`)

1. Install the **ArcKep 3D** extension in Blender (`4.2+`) and start the local bridge in **3D Viewport → `N` → ArcKep 3D → MCP → Start MCP Bridge** (default port `9876`).
2. Add `arckep-blender-mcp` to your MCP client configuration (Claude Desktop, Cursor, Windsurf, Codex, Antigravity):

```json
{
  "mcpServers": {
    "arckep-blender": {
      "command": "uvx",
      "args": ["arckep-blender-mcp"],
      "env": {
        "ARCKEP_API_KEY": "<YOUR_ARCKEP_KEY>",
        "ARCKEP_BLENDER_PORT": "9876"
      }
    }
  }
}
```

Required API key scopes: `models3d:write` and `balance:read` (local `blender_*` scene tools work even without `ARCKEP_API_KEY`).

---

## Environment Variables

| Variable | Default | Description |
| --- | --- | --- |
| `ARCKEP_API_KEY` | *(empty)* | `ark_live_...` token for `/api/v1/*` cloud calls (never logged or written to `stdout`). |
| `ARCKEP_API_BASE_URL` | Blender `ping().api_base_url` or `https://arckep.ru/api/v1` | Override `/api/v1` base URL. |
| `ARCKEP_BLENDER_HOST` | `127.0.0.1` | Loopback address (`127.0.0.1` or `localhost` only; non-local addresses raise `ValueError`). |
| `ARCKEP_BLENDER_PORT` | `9876` | TCP port of the Blender scene bridge (`1024..65535`). |
| `ARCKEP_BUDGET_MODE` | Live Blender preference or `threshold` | `threshold`, `confirm_always`, or `unlimited`. |
| `ARCKEP_MAX_AUTO_COST_RUB` | Live Blender preference or `150.0` | Auto-approve ceiling in RUB when `ARCKEP_BUDGET_MODE=threshold`. |
| `ARCKEP_POLL_INTERVAL_SEC` | `1.5` | Poll interval in seconds for `/models3d/generations/{id}` (`>= 0.02`). |

---

## RUB Budget Guard (3 Modes)

Agents **cannot** change `budget_mode` or `max_auto_cost_rub` via tool arguments. Before every paid call (`arckep_generate_3d`, `arckep_process_mesh`), the server quotes the exact RUB price via `/estimate`:

1. **`threshold`** *(default `150.0` ₽)* — jobs costing `<= max_auto_cost_rub` run automatically; more expensive jobs return `status: "confirmation_required"` (`error_code: "CONFIRMATION_REQUIRED"`) without charging until the user confirms and the tool is re-called with `confirmed_max_cost_rub >= cost_rub`.
2. **`confirm_always`** — free (`0` ₽) operations run immediately; any paid job (`cost_rub > 0`) stops with `CONFIRMATION_REQUIRED` until explicitly re-called with `confirmed_max_cost_rub >= cost_rub`.
3. **`unlimited`** — no local prompt cap (bounded only by account/corporate balance and optional `confirmed_max_cost_rub`).

---

## Available MCP Tools (15)

### Local Blender Scene Bridge (`127.0.0.1`)
- `blender_ping` — bridge status, Blender/add-on versions, open `.blend`, and active Budget Guard policy.
- `blender_get_scene_info` — collections, objects, polygon counts, materials, `arckep_job_id` tags, 3D Cursor, and camera.
- `blender_get_object_info` — world bounding box, mesh/UV layers, armature bones, actions, modifiers, and `arckep_job_id`.
- `blender_capture_viewport` — render `camera`, `selection`, or `scene` to PNG (`512..2048` px).
- `blender_import_asset` — import `.glb`, `.fbx`, `.obj`, `.zip`, `.usdz`, `.stl`, `.ply`, `.3mf`, or `.splat` at the 3D Cursor or `[x, y, z]`.
- `blender_transform_object` — move, rotate (degrees), scale, and snap objects onto the ground plane (`Z = 0`).

### ArcKep 3D Cloud, Post-Processing & 3D Print (`/api/v1`)
- `arckep_get_balance` — personal or B2B corporate RUB balance + active Budget Guard settings.
- `arckep_list_generations` — paginated generation history or single job inspection (`job_id`).
- `arckep_import_generation` — download a completed archive job and import it into Blender with `arckep_job_id`.
- `arckep_estimate_generation` — free `0` ₽ quote across all 9 creation models (`rodin-v2.5`, `meshy-v7.1`, `tripo-h3.1`, `tripo-p2`, `tripo-p1`, `hunyuan-3d-3.1`, `tripo-multiview`, `tripo-splat`, `idol`), including live Blender viewport blockout capture.
- `arckep_generate_3d` — full 3D / multiview generation with Budget Guard enforcement, polling, download, and scene import.
- `arckep_check_rig` — free `0` ₽ skeleton compatibility check (`rig-check`) for a scene object, job ID, or local mesh.
- `arckep_estimate_process` — free `0` ₽ quote across all 10 mesh operations (`texture`, `decimate`, `uv`, `segment`, `smartsegment`, `complete`, `rig`, `retarget`, `rig_and_retarget`, `convert`).
- `arckep_process_mesh` — run any of the 10 post-processing operations with Budget Guard enforcement and scene import (`ArcKep_Parts_<id>` grouping for segmentation/completion).
- `arckep_export_print_stl` — free `0` ₽ slicer-ready binary `.stl` or `.3mf` export in millimeters (`target_size_mm`, `z_up`, `place_on_bed`).
