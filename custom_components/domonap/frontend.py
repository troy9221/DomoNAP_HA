from __future__ import annotations

import logging
from pathlib import Path

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .actions import _require_entry_api, _refresh_account
from .api import is_api_error
from .const import FACE_MAX_BYTES
from .dashboard import async_setup_dashboard

_LOGGER = logging.getLogger(__name__)

_JS_FILES = ("domonap-card.js", "domonap-dashboard.js")
_JS_VERSION = "1.4.3"
STATIC_DIR = Path(__file__).resolve().parent / "static"


def _guess_filename_content_type(filename: str | None, content_type: str | None) -> tuple[str, str]:
    name = (filename or "face.jpg").split("?")[0] or "face.jpg"
    ctype = (content_type or "").split(";")[0].strip().lower()
    lower = name.lower()
    if ctype in ("image/jpeg", "image/jpg", "image/png", "image/webp", "image/heic", "image/heif"):
        return name, ctype
    if lower.endswith(".png"):
        return name, "image/png"
    if lower.endswith(".webp"):
        return name, "image/webp"
    if lower.endswith(".heic") or lower.endswith(".heif"):
        return name, "image/heic"
    if not lower.endswith((".jpg", ".jpeg")):
        name = f"{name}.jpg" if "." not in name else name
    return name, "image/jpeg"


class DomonapFaceUploadView(HomeAssistantView):
    """Загрузка одного или нескольких фото лица с телефона (камера / галерея)."""

    url = "/api/domonap/face"
    name = "api:domonap:face"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def post(self, request: web.Request) -> web.Response:
        try:
            entry_id, api = _require_entry_api(self.hass, request.query.get("config_entry_id"))
        except Exception as err:
            return self.json({"ok": False, "error": str(err)}, status_code=400)

        created: list[dict] = []
        errors: list[str] = []
        content_type = request.content_type or ""
        parts: list[tuple[str, bytes, str]] = []

        if "multipart" in content_type:
            reader = await request.multipart()
            while True:
                part = await reader.next()
                if part is None:
                    break
                filename = part.filename
                part_type = part.headers.get("Content-Type")
                body = await part.read()
                if not body:
                    continue
                if not filename and not (part_type or "").startswith("image/"):
                    continue
                name, ctype = _guess_filename_content_type(filename, part_type)
                parts.append((name, body, ctype))
        else:
            body = await request.read()
            if body:
                name, ctype = _guess_filename_content_type("face.jpg", content_type)
                parts.append((name, body, ctype))

        if not parts:
            return self.json(
                {"ok": False, "error": "Нет файлов. Выберите фото из галереи или сделайте снимок."},
                status_code=400,
            )

        for index, (name, body, ctype) in enumerate(parts, start=1):
            filename = name if len(parts) == 1 else f"{index}_{name}"
            if len(body) > FACE_MAX_BYTES:
                errors.append(f"{filename}: файл больше 8 МБ")
                continue
            res = await api.create_face(body, filename=filename, content_type=ctype)
            if is_api_error(res):
                errors.append(f"{filename}: {res}")
            else:
                created.append({"filename": filename, "response": res})

        await _refresh_account(self.hass, entry_id)
        return self.json(
            {
                "ok": not errors,
                "created": len(created),
                "failed": len(errors),
                "errors": errors,
                "config_entry_id": entry_id,
            },
            status_code=200 if created else 400,
        )


async def async_setup_frontend(hass: HomeAssistant) -> None:
    hass.http.register_view(DomonapFaceUploadView(hass))
    await _register_static(hass)
    await _register_lovelace_resource(hass)
    await async_setup_dashboard(hass)


async def _register_static(hass: HomeAssistant) -> None:
    static_dir = str(STATIC_DIR)
    try:
        from homeassistant.components.http import StaticPathConfig

        await hass.http.async_register_static_paths(
            [StaticPathConfig("/domonap-static", static_dir, False)]
        )
    except Exception:
        try:
            hass.http.register_static_path("/domonap-static", static_dir, False)
        except Exception:
            _LOGGER.debug("Could not register /domonap-static", exc_info=True)
    try:
        from homeassistant.components.frontend import add_extra_js_url

        for name in _JS_FILES:
            add_extra_js_url(hass, f"/domonap-static/{name}?v={_JS_VERSION}")
    except Exception:
        _LOGGER.debug("Could not add extra JS url", exc_info=True)


async def _register_lovelace_resource(hass: HomeAssistant) -> None:
    urls = [f"/domonap-static/{name}?v={_JS_VERSION}" for name in _JS_FILES]
    try:
        lovelace = hass.data.get("lovelace")
        resources = getattr(lovelace, "resources", None) if lovelace is not None else None
        if resources is None:
            return
        load = getattr(resources, "async_load", None)
        if callable(load):
            await load()
        items_fn = getattr(resources, "async_items", None)
        items = list(items_fn()) if callable(items_fn) else []
        existing = {
            str(item.get("url") or "").split("?")[0]
            for item in items
        }
        create = getattr(resources, "async_create_item", None)
        if not callable(create):
            return
        for url in urls:
            path = url.split("?")[0]
            if path in existing:
                continue
            await create({"res_type": "module", "url": url})
            _LOGGER.info("Registered Lovelace resource %s", url)
    except Exception:
        _LOGGER.debug("Could not auto-register Lovelace resource", exc_info=True)
