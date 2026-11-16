# ArcKep Blender Suite (`arckep_3d` + `arckep-blender-mcp`)

Официальная интеграция **ArcKep 3D** для **Blender 4.2 LTS — 5.x** и ИИ-ассистентов по протоколу **MCP**:

- **`addon/` (`arckep_3d`, v0.1.0)** — расширение для Blender 4.2+: генерация 3D-моделей по тексту, фото, набору ракурсов и 3D-блокауту (9 моделей), 10 операций доработки сетки (PBR-ретекстура до 8K, ретопология, UV-развёртка, сегментация, авто-риггинг, ретаргет анимаций, конвертация), бесплатная проверка под риг и экспорт в миллиметрах под 3D-печать (`.stl` / `.3mf`).
- **`mcp_server/` (`arckep-blender-mcp`, v0.1.0)** — локальный stdio MCP-сервер (15 инструментов) для Claude Desktop, Cursor, Windsurf, Codex и Antigravity. Связывает открытую сцену Blender по локальному TCP-мосту `127.0.0.1` с облачным API `https://arckep.ru/api/v1` без выполнения произвольного `exec()` кода.

---

## 1. Установка расширения в Blender 4.2+

### Способ А. Автообновляемый репозиторий (рекомендуется)
1. В Blender откройте **Edit → Preferences → Get Extensions → Repositories (⌄) → `+` → Add Remote Repository**.
2. Укажите URL ленты расширений:
   ```text
   https://arckep.ru/blender/index.json
   ```
3. Включите **Check for Updates on Startup**, найдите **ArcKep 3D** в списке и нажмите **Install**.

### Способ Б. Перетаскивание ссылки в окно Blender (Drag-and-Drop)
Перетащите эту ссылку прямо в окно открытого Blender 4.2+ — он установит расширение и сразу подключит репозиторий обновлений:

```text
https://arckep.ru/blender/arckep_3d-0.1.0.zip?repository=https%3A%2F%2Farckep.ru%2Fblender%2Findex.json&blender_version_min=4.2.0
```

### Способ В. Установка из архива `.zip`
Скачайте архив `https://arckep.ru/blender/arckep_3d-0.1.0.zip` и выберите **Edit → Preferences → Get Extensions → ⌄ → Install from Disk…**.

---

## 2. API-ключ и права доступа

1. Откройте [настройки API-ключей ArcKep](https://arckep.ru/settings?tab=api).
2. Создайте ключ с двумя минимально необходимыми правами:
   - `models3d:write` — запуск генерации и обработки 3D-сеток;
   - `balance:read` — проверка баланса и точной цены до списания.
3. Вставьте полученный ключ `ark_live_ваш_ключ` в настройках аддона (**Edit → Preferences → Add-ons → ArcKep 3D**) или в переменную окружения `ARCKEP_API_KEY` для MCP.

Локальные инструменты сцены (`blender_ping`, `blender_get_scene_info`, `blender_capture_viewport`, `blender_import_asset`, `blender_transform_object`) работают даже без API-ключа.

---

## 3. Подключение MCP-сервера (`uvx arckep-blender-mcp`)

1. В 3D Viewport нажмите `N` → вкладка **ArcKep 3D** → панель **MCP** → **Start MCP Bridge** (порт по умолчанию `9876`).
2. Добавьте сервер в конфигурацию вашего MCP-клиента:

```json
{
  "mcpServers": {
    "arckep-blender": {
      "command": "uvx",
      "args": ["arckep-blender-mcp"],
      "env": {
        "ARCKEP_API_KEY": "ark_live_ваш_ключ"
      }
    }
  }
}
```

Подробный справочник всех 15 инструментов и переменных окружения — в [`mcp_server/README.md`](mcp_server/README.md) и в [документации разработчика](https://arckep.ru/developer/docs).

---

## 4. Защита бюджета в рублях (3 режима)

ИИ-агент не может сам повысить лимит или сменить режим защиты через аргументы инструментов. Перед каждым платным вызовом расширение и MCP-сервер запрашивают точную цену через `/estimate`:

1. **Умный порог (`threshold`, по умолчанию до `150 ₽`)** — цена видна до запуска; недорогие задачи стартуют сразу, а для сумм выше порога Blender и MCP попросят ваше подтверждение.
2. **Всегда спрашивать (`confirm_always`)** — каждый платный вызов ждёт подтверждения точной суммы в рублях; бесплатные проверка скелета (`rig-check`) и выгрузка STL/3MF работают сразу.
3. **Без локального порога (`unlimited`)** — задачи запускаются без дополнительных диалогов в пределах баланса счёта и лимита самого запроса.

---

## 5. Сборка расширения из исходников

```bash
BLENDER_BIN=blender ./scripts/build_extension.sh
```

Скрипт проверит манифест `addon/blender_manifest.toml`, соберёт `dist/arckep_3d-0.1.0.zip` и сгенерирует `dist/index.json`.

---

## 6. Лицензирование (Dual License)

Репозиторий разделён на два независимых пакета с разными лицензиями:

- **`addon/`** распространяется под лицензией **GNU General Public License v3.0 or later (`GPL-3.0-or-later`)** в соответствии с требованиями Blender Python API (`bpy`). Полный текст лицензии — в [`addon/LICENSE`](addon/LICENSE).
- **`mcp_server/`** распространяется под пермиссивной лицензией **MIT (`MIT`)**. Пакет `arckep-blender-mcp` не импортирует `bpy` и не включает код из `addon/`, взаимодействуя со сценой только по локальному протоколу TCP JSONL и с API по HTTPS. Полный текст лицензии — в [`mcp_server/LICENSE`](mcp_server/LICENSE).

---

## English Summary

**ArcKep Blender Suite** connects **Blender 4.2+** and MCP-compatible AI coding/3D assistants to the [ArcKep 3D platform](https://arckep.ru/3d-models).

- **Install in Blender 4.2+:** add remote repository `https://arckep.ru/blender/index.json` in *Edit → Preferences → Get Extensions → Repositories*, drag-and-drop `https://arckep.ru/blender/arckep_3d-0.1.0.zip?repository=https%3A%2F%2Farckep.ru%2Fblender%2Findex.json&blender_version_min=4.2.0` into Blender, or install `https://arckep.ru/blender/arckep_3d-0.1.0.zip` from disk.
- **API Key Scopes:** issue a key at `https://arckep.ru/settings?tab=api` with `models3d:write` and `balance:read`.
- **Run MCP Server:** start the local bridge in *3D Viewport → N → ArcKep 3D → MCP* and configure `uvx arckep-blender-mcp` with `ARCKEP_API_KEY=ark_live_YOUR_KEY`.
- **RUB Budget Guard:** `threshold` (default `150 ₽` auto-approve ceiling), `confirm_always` (confirm every paid job in RUB; `0 ₽` rig-check & STL/3MF export run immediately), or `unlimited`.
- **Dual Licensing:** `addon/` is licensed under **GPL-3.0-or-later** ([`addon/LICENSE`](addon/LICENSE)); `mcp_server/` (`arckep-blender-mcp` on PyPI) is licensed under **MIT** ([`mcp_server/LICENSE`](mcp_server/LICENSE)).
