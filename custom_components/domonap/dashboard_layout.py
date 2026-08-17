"""Сборка Lovelace-видов Domonap из текущих сущностей HA.

Дашборд сохраняется как обычные views, без JS-strategy: иначе HA часто
падает с «Timeout waiting for strategy element ll-strategy-dashboard-domonap».
"""

from __future__ import annotations

import re
from typing import Any

from .const import DASHBOARD_GENERATED_KEY, DASHBOARD_TITLE

_KIND_RANK = {"home": 0, "parking": 1, "storage": 2}
_SLUG_MAP = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def _attr(state: Any, key: str) -> Any:
    attrs = getattr(state, "attributes", None) or {}
    if isinstance(attrs, dict):
        return attrs.get(key)
    return None


def _door_id(state: Any) -> str:
    value = _attr(state, "doorId") or _attr(state, "DoorId") or _attr(state, "door_id")
    return str(value) if value else ""


def _address(state: Any) -> str:
    return str(_attr(state, "addressString") or _attr(state, "Address") or "").strip()


def _raw_name(state: Any) -> str:
    return str(
        _attr(state, "name")
        or _attr(state, "friendly_name")
        or getattr(state, "entity_id", "")
    )


def clean_name(state: Any) -> str:
    name = re.sub(r"\s*Открыть дверь\s*$", "", _raw_name(state), flags=re.I).strip()
    name = re.sub(r"\s*#[0-9a-fA-F]{4,}\s*$", "", name, flags=re.I).strip()
    name = re.sub(
        r"\s*\([^)]*(?:улица|ул\.|д\.|паркинг|кладов|машиномест|место\.)[^)]*\)\s*",
        " ",
        name,
        flags=re.I,
    ).strip()
    name = re.sub(r"\s*\([^)]{12,}\)\s*$", "", name).strip()
    name = re.sub(r"\s{2,}", " ", name).strip()
    return name or _raw_name(state)


def building_of(address: str) -> str:
    value = (address or "").strip()
    if not value:
        return "Другие двери"
    value = re.sub(r"^паркинг\s*:\s*", "", value, flags=re.I)
    value = re.sub(r"\bул\.\s*", "", value, flags=re.I)
    parts = [part.strip() for part in value.split(",") if part.strip()]
    street = ""
    house = ""
    for part in parts:
        house_match = re.match(r"^(д\.?\s*\S+)", part, flags=re.I)
        if house_match and not house:
            house = house_match.group(1)
            continue
        if re.match(r"^(п\.|э\.?-?|кв\.|кладов|место|машиномест)", part, flags=re.I):
            continue
        if not street:
            street = part
    street = re.sub(r"^\s*(улица|ул\.?)\s+", "", street, flags=re.I)
    street = re.sub(r"\s+улица\s*$", "", street, flags=re.I).strip()
    return ", ".join(item for item in (street, house) if item) or value


def door_kind(address: str, name: str) -> str:
    addr_low = (address or "").lower()
    name_low = (name or "").lower()
    if re.search(r"паркинг|машиномест|\bместо\.", addr_low):
        return "parking"
    if re.search(r"тамбур|подвал|кладов", name_low):
        return "storage"
    if re.search(r"калит|ворот|считыват|лифтов|лест|подъезд|вход", name_low):
        return "home"
    if re.search(r"кладов|келлер|э\.-1|э\s*-1", addr_low):
        return "storage"
    return "home"


def _street_of(building: str) -> str:
    return (building or "").split(",")[0].strip()


def _house_of(building: str) -> str:
    index = (building or "").find(",")
    return "" if index == -1 else building[index + 1 :].strip()


def _view_title(building: str, kind: str, street_kind_counts: dict[str, int]) -> str:
    street = _street_of(building)
    house = _house_of(building)
    clash = street_kind_counts.get(f"{street}|{kind}", 0) > 1
    base = f"{street}, {house}" if clash and house else street
    if kind == "parking":
        return f"Паркинг · {base}"
    if kind == "storage":
        return f"Кладовки · {base}"
    return base or DASHBOARD_TITLE


def _kind_label(kind: str) -> str:
    if kind == "parking":
        return "паркинг"
    if kind == "storage":
        return "кладовки"
    return "дом"


def _slugify(title: str, index: int) -> str:
    slug = "".join(_SLUG_MAP.get(ch, ch) for ch in (title or "").lower())
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    return f"{slug or 'addr'}-{index}"


def _entity_seq(state: Any) -> int:
    match = re.search(r"_(\d+)$", str(getattr(state, "entity_id", "") or ""))
    return int(match.group(1)) if match else 1


def _label_number(label: str) -> int:
    match = re.search(r"(\d+)\s*$", label or "")
    return int(match.group(1)) if match else 0


def _press_action(entity_id: str) -> dict[str, Any]:
    return {
        "action": "call-service",
        "service": "button.press",
        "service_data": {"entity_id": entity_id},
        "target": {"entity_id": entity_id},
    }


def _door_icon(name: str) -> str:
    if re.search(r"калит|ворот|gate", name, re.I):
        return "mdi:gate"
    if re.search(r"въезд|выезд|шлагбаум|boom", name, re.I):
        return "mdi:boom-gate"
    if re.search(r"лестн|stair", name, re.I):
        return "mdi:stairs"
    if re.search(r"тамбур", name, re.I):
        return "mdi:door-closed-lock"
    if re.search(r"подвал", name, re.I):
        return "mdi:stairs-down"
    return "mdi:door"


def _iter_states(hass: Any) -> list[Any]:
    states_obj = getattr(hass, "states", None)
    if states_obj is None:
        return []
    all_fn = getattr(states_obj, "async_all", None)
    if callable(all_fn):
        try:
            return list(all_fn())
        except Exception:
            pass
    values = getattr(states_obj, "values", None)
    if callable(values):
        return list(values())
    return []


def _collect(hass: Any) -> dict[str, Any]:
    buttons: list[Any] = []
    cameras: list[Any] = []
    calls: list[Any] = []
    pins: list[Any] = []
    last_call = None
    last_call_button = None
    for state in _iter_states(hass):
        entity_id = str(getattr(state, "entity_id", "") or "")
        if not entity_id:
            continue
        if entity_id.startswith("button.") and "open_relay_by_last_call" in entity_id:
            last_call_button = state
            continue
        if entity_id.startswith("sensor.") and "last_call_door_id" in entity_id:
            last_call = state
            continue
        if entity_id.startswith("button.") and _door_id(state) and "open_relay_by_last_call" not in entity_id:
            buttons.append(state)
            continue
        if entity_id.startswith("camera.") and (
            _door_id(state)
            or _attr(state, "id")
            or _attr(state, "httpVideoUrl")
            or _attr(state, "webrtcVideoUrl")
        ):
            cameras.append(state)
            continue
        if entity_id.startswith("binary_sensor.") and _door_id(state):
            calls.append(state)
            continue
        if entity_id.startswith("sensor.") and _door_id(state) and _attr(state, "domofonPublicPin"):
            pins.append(state)
    return {
        "buttons": buttons,
        "cameras": cameras,
        "calls": calls,
        "pins": pins,
        "last_call": last_call,
        "last_call_button": last_call_button,
    }


def _camera_for(button: Any, cameras: list[Any]) -> Any | None:
    door_id = _door_id(button)
    key_id = str(_attr(button, "id") or "")
    for camera in cameras:
        cam_door = _door_id(camera)
        cam_key = str(_attr(camera, "id") or getattr(camera, "entity_id", ""))
        if (door_id and cam_door == door_id) or (key_id and cam_key == key_id):
            return camera
    return None


def _match_door(button: Any, items: list[Any]) -> Any | None:
    door_id = _door_id(button)
    for item in items:
        if _door_id(item) == door_id:
            return item
    return None


def _display_names(buttons: list[Any]) -> dict[str, str]:
    items = [{"button": button, "base": clean_name(button)} for button in buttons]
    counts: dict[str, int] = {}
    for item in items:
        counts[item["base"]] = counts.get(item["base"], 0) + 1
    seen: dict[str, int] = {}
    names: dict[str, str] = {}

    def _key(item: dict[str, Any]) -> tuple:
        button = item["button"]
        return (
            re.sub(r"_\d+$", "", str(getattr(button, "entity_id", "") or "")),
            _entity_seq(button),
            _door_id(button),
        )

    for item in sorted(items, key=_key):
        base = item["base"]
        entity_id = str(getattr(item["button"], "entity_id", "") or "")
        if counts.get(base, 0) > 1:
            seen[base] = seen.get(base, 0) + 1
            names[entity_id] = f"{base} {seen[base]}"
        else:
            names[entity_id] = base
    return names


def _label_of(button: Any, names: dict[str, str]) -> str:
    return names.get(str(getattr(button, "entity_id", "") or ""), "") or clean_name(button)


def _sort_items(items: list[dict[str, Any]], names: dict[str, str]) -> list[dict[str, Any]]:
    def _key(item: dict[str, Any]) -> tuple:
        button = item["button"]
        label = _label_of(button, names)
        return (
            _label_number(label),
            re.sub(r"_\d+$", "", str(getattr(button, "entity_id", "") or "")),
            _entity_seq(button),
            label,
        )

    return sorted(items, key=_key)


def _group_by_site(buttons: list[Any]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for button in buttons:
        address = _address(button)
        kind = door_kind(address, clean_name(button))
        building = building_of(address)
        key = f"{building}|{kind}"
        group = groups.setdefault(key, {"building": building, "kind": kind, "buttons": []})
        group["buttons"].append(button)
    street_kind_counts: dict[str, int] = {}
    for group in groups.values():
        key = f"{_street_of(group['building'])}|{group['kind']}"
        street_kind_counts[key] = street_kind_counts.get(key, 0) + 1
    result = []
    for group in groups.values():
        buttons_sorted = sorted(
            group["buttons"],
            key=lambda button: (
                re.sub(r"_\d+$", "", str(getattr(button, "entity_id", "") or "")),
                _entity_seq(button),
                _door_id(button),
            ),
        )
        result.append(
            {
                "building": group["building"],
                "kind": group["kind"],
                "title": _view_title(group["building"], group["kind"], street_kind_counts),
                "buttons": buttons_sorted,
            }
        )
    result.sort(
        key=lambda group: (
            _KIND_RANK.get(group["kind"], 9),
            _street_of(group["building"]),
            group["building"],
        )
    )
    return result


def _call_card(button: Any, camera: Any | None, call_sensor: Any | None, names: dict[str, str]) -> dict[str, Any] | None:
    if call_sensor is None:
        return None
    cards: list[dict[str, Any]] = [
        {"type": "markdown", "content": "## Звонок — " + _label_of(button, names)}
    ]
    if camera is not None:
        cards.append(
            {
                "type": "picture-entity",
                "entity": camera.entity_id,
                "camera_view": "live",
                "show_state": False,
                "show_name": False,
            }
        )
    cards.append(
        {
            "type": "button",
            "name": "Открыть",
            "icon": "mdi:door-open",
            "icon_height": "36px",
            "tap_action": _press_action(button.entity_id),
        }
    )
    return {
        "type": "conditional",
        "conditions": [{"entity": call_sensor.entity_id, "state": "on"}],
        "card": {"type": "vertical-stack", "cards": cards},
    }


def _door_grid(with_camera: list[dict[str, Any]], names: dict[str, str]) -> dict[str, Any] | None:
    cards: list[dict[str, Any]] = []
    for item in _sort_items(with_camera, names):
        button, camera = item["button"], item["camera"]
        cards.append(
            {
                "type": "picture-entity",
                "title": _label_of(button, names),
                "entity": camera.entity_id,
                "camera_view": "live",
                "show_state": False,
            }
        )
        cards.append(
            {
                "type": "button",
                "name": "Открыть",
                "icon": "mdi:door-open",
                "icon_height": "36px",
                "tap_action": _press_action(button.entity_id),
            }
        )
    if not cards:
        return None
    return {"type": "grid", "columns": 2, "square": False, "cards": cards}


def _entities_card(
    title: str,
    items: list[dict[str, Any]],
    names: dict[str, str],
    pins: list[Any],
) -> dict[str, Any] | None:
    entities: list[dict[str, Any]] = []
    for item in _sort_items(items, names):
        name = _label_of(item["button"], names)
        entities.append(
            {
                "type": "button",
                "name": name,
                "icon": _door_icon(name),
                "action_name": "Открыть",
                "tap_action": _press_action(item["button"].entity_id),
            }
        )
    for pin in pins or []:
        entities.append(
            {
                "entity": pin.entity_id,
                "name": "Код — " + clean_name(pin),
            }
        )
    if not entities:
        return None
    return {"type": "entities", "title": title, "show_header_toggle": False, "entities": entities}


def _other_lists(
    without_camera: list[dict[str, Any]],
    pins: list[Any],
    names: dict[str, str],
    kind: str,
) -> list[dict[str, Any]]:
    by_base: dict[str, list[dict[str, Any]]] = {}
    for item in _sort_items(without_camera, names):
        by_base.setdefault(clean_name(item["button"]), []).append(item)
    cards: list[dict[str, Any]] = []
    leftover: list[dict[str, Any]] = []
    for base in sorted(by_base, key=lambda value: value.lower()):
        items = by_base[base]
        if len(items) >= 3:
            card = _entities_card(base, items, names, [])
            if card:
                cards.append(card)
        else:
            leftover.extend(items)
    leftover_title = {
        "parking": "Калитки и шлагбаумы",
        "storage": "Прочее",
    }.get(kind, "Калитки и входы")
    leftover_card = _entities_card(leftover_title, leftover, names, pins)
    if leftover_card:
        cards.append(leftover_card)
    return cards


def _last_call_cards(last_call: Any, last_call_button: Any) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    if last_call is not None:
        address = (
            _attr(last_call, "Address")
            or _attr(last_call, "addressString")
            or getattr(last_call, "state", None)
            or "нет"
        )
        cards.append({"type": "markdown", "content": f"**Последний звонок:** {address}"})
    if last_call_button is not None:
        cards.append(
            {
                "type": "entities",
                "show_header_toggle": False,
                "entities": [
                    {
                        "type": "button",
                        "name": "Дверь последнего звонка",
                        "icon": "mdi:phone-incoming",
                        "action_name": "Открыть",
                        "tap_action": _press_action(last_call_button.entity_id),
                    }
                ],
            }
        )
    return cards


def _cabinet_view(title: str, path: str, icon: str, mode: str) -> dict[str, Any]:
    return {
        "title": title,
        "path": path,
        "icon": icon,
        "type": "panel",
        "cards": [{"type": "custom:domonap-cabinet-card", "mode": mode}],
    }


def layout_fingerprint(config: dict[str, Any] | None) -> str:
    """Стабильный отпечаток, чтобы не перезаписывать дашборд без изменений."""
    if not isinstance(config, dict):
        return ""
    views = config.get("views") or []
    parts: list[str] = [str(config.get("title") or "")]
    for view in views:
        if not isinstance(view, dict):
            continue
        parts.append(str(view.get("path") or ""))
        parts.append(str(view.get("title") or ""))
        parts.append(_cards_fingerprint(view.get("cards") or []))
    return "\n".join(parts)


def _cards_fingerprint(cards: list[Any]) -> str:
    bits: list[str] = []
    for card in cards:
        if not isinstance(card, dict):
            continue
        bits.append(str(card.get("type") or ""))
        bits.append(str(card.get("entity") or ""))
        bits.append(str(card.get("title") or card.get("name") or ""))
        nested = card.get("cards") or []
        if nested:
            bits.append(_cards_fingerprint(nested))
        entities = card.get("entities") or []
        for entity in entities:
            if isinstance(entity, dict):
                bits.append(str(entity.get("entity") or entity.get("name") or ""))
            else:
                bits.append(str(entity))
        inner = card.get("card")
        if isinstance(inner, dict):
            bits.append(_cards_fingerprint([inner]))
    return "|".join(bits)


def generate_dashboard_config(hass: Any) -> dict[str, Any]:
    data = _collect(hass)
    groups = _group_by_site(data["buttons"])
    all_names = _display_names(data["buttons"])
    views: list[dict[str, Any]] = []
    all_call_cards: list[dict[str, Any]] = []
    for button in data["buttons"]:
        call = _call_card(
            button,
            _camera_for(button, data["cameras"]),
            _match_door(button, data["calls"]),
            all_names,
        )
        if call:
            all_call_cards.append(call)
    status_cards = _last_call_cards(data["last_call"], data["last_call_button"])

    if not groups:
        views.append(
            {
                "title": DASHBOARD_TITLE,
                "path": "doors",
                "icon": "mdi:doorbell-video",
                "cards": [
                    {
                        "type": "markdown",
                        "content": (
                            "Дверей пока нет. Авторизуйтесь в интеграции **Domonap** — "
                            "на дашборде появятся ключи этого аккаунта."
                        ),
                    }
                ],
            }
        )
    else:
        home_index = 0
        for index, group in enumerate(groups):
            names = _display_names(group["buttons"])
            with_camera: list[dict[str, Any]] = []
            without_camera: list[dict[str, Any]] = []
            pin_entities: list[Any] = []
            for button in group["buttons"]:
                camera = _camera_for(button, data["cameras"])
                item = {"button": button, "camera": camera}
                if camera is not None:
                    with_camera.append(item)
                else:
                    without_camera.append(item)
                pin = _match_door(button, data["pins"])
                if pin is not None:
                    pin_entities.append(pin)
            kind = group["kind"]
            icon = (
                "mdi:parking"
                if kind == "parking"
                else "mdi:warehouse"
                if kind == "storage"
                else ("mdi:home-city" if home_index == 0 else "mdi:home-city-outline")
            )
            if kind == "home":
                home_index += 1
            cards: list[dict[str, Any]] = [
                {
                    "type": "markdown",
                    "content": f"**{group['building']}** · {_kind_label(kind)}",
                },
                *status_cards,
                *all_call_cards,
            ]
            grid = _door_grid(with_camera, names)
            if grid:
                cards.append(grid)
            cards.extend(_other_lists(without_camera, pin_entities, names, kind))
            if not grid and not without_camera and not pin_entities:
                cards.append(
                    {
                        "type": "markdown",
                        "content": "На этом адресе нет дверей с кнопкой открытия.",
                    }
                )
            views.append(
                {
                    "title": group["title"],
                    "path": f"{_KIND_RANK.get(kind, 9)}-{_slugify(group['title'], index)}",
                    "icon": icon,
                    "cards": cards,
                }
            )

    views.append(_cabinet_view("Поддержка", "support", "mdi:headset", "support"))
    views.append(_cabinet_view("Аватары", "face", "mdi:face-recognition", "face"))
    return {
        DASHBOARD_GENERATED_KEY: True,
        "title": DASHBOARD_TITLE,
        "views": views,
    }
