import asyncio
import importlib.util
import sys
import types
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
API_PATH = ROOT / "custom_components" / "domonap" / "api.py"
CONST_PATH = ROOT / "custom_components" / "domonap" / "const.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _install_homeassistant_stub() -> None:
    if "homeassistant.const" in sys.modules:
        return
    ha = types.ModuleType("homeassistant")
    ha_const = types.ModuleType("homeassistant.const")

    class Platform:
        BUTTON = "button"
        CAMERA = "camera"
        BINARY_SENSOR = "binary_sensor"
        SENSOR = "sensor"
        IMAGE = "image"
        UPDATE = "update"

    ha_const.Platform = Platform
    sys.modules.setdefault("homeassistant", ha)
    sys.modules["homeassistant.const"] = ha_const


api = _load_module("domonap_api", API_PATH)
IntercomAPI = api.IntercomAPI
SIGNALR_USER_AGENT = api.SIGNALR_USER_AGENT
_with_app_header_suffix = api._with_app_header_suffix
is_android_guid = api.is_android_guid
is_fcm_like_token = api.is_fcm_like_token


def test_fcm_like_token_detection():
    assert is_fcm_like_token("abcDEF1234567890abcd:APA91b" + "x" * 40)
    assert not is_fcm_like_token("short:APA91b")
    assert not is_fcm_like_token(str(uuid.uuid4()))
    assert not is_fcm_like_token(None)
    assert not is_fcm_like_token(123)


def test_android_guid_detection():
    guid = "12345678-1234-4123-8123-1234567890ab"
    assert is_android_guid(guid)
    assert not is_android_guid("not-a-guid")
    assert not is_android_guid(None)


def test_app_header_suffix():
    assert _with_app_header_suffix("Android") == "Android;"
    assert _with_app_header_suffix("Android;") == "Android;"


def test_signalr_headers_do_not_include_instance_id():
    client = IntercomAPI(instance_id="11111111-1111-4111-8111-111111111111")
    headers = client.signalr_headers()
    assert headers["User-Agent"] == SIGNALR_USER_AGENT
    assert "instanceId" not in headers
    assert headers["dom-app"].endswith(";")
    assert headers["dom-platform"].endswith(";")


def test_disambiguate_duplicate_door_names():
    keys = [
        {"name": "Калитка 1", "doorId": "aaa111", "addressString": "Двор"},
        {"name": "Калитка 1", "doorId": "bbb222", "addressString": "Паркинг"},
    ]
    IntercomAPI._disambiguate_names(keys)
    names = [key["name"] for key in keys]
    assert len(set(names)) == 2
    assert any("Двор" in name for name in names)
    assert any("Паркинг" in name for name in names)


def test_disambiguate_same_name_and_address_uses_door_id_tail():
    keys = [
        {"name": "Тамбур", "doorId": "doorAAAAAA", "addressString": "Дом 1"},
        {"name": "Тамбур", "doorId": "doorBBBBBB", "addressString": "Дом 1"},
    ]
    IntercomAPI._disambiguate_names(keys)
    names = [key["name"] for key in keys]
    assert len(set(names)) == 2
    assert any(name.endswith("#AAAAAA") for name in names)
    assert any(name.endswith("#BBBBBB") for name in names)


def test_split_signalr_records_keeps_batched_invocations():
    _install_homeassistant_stub()
    const = _load_module("domonap_const", CONST_PATH)
    invocation = (
        '{"type":1,"target":"ReceivePush","arguments":[1,2,{"EventMessage":"DomofonCalling"}]}'
    )
    raw = '{"type":6}' + const.WS_MESSAGE_END + invocation + const.WS_MESSAGE_END
    assert const.split_signalr_records(raw) == ['{"type":6}', invocation]


def test_split_signalr_records_ignores_empty_chunks():
    _install_homeassistant_stub()
    const = _load_module("domonap_const", CONST_PATH)
    assert const.split_signalr_records(const.WS_MESSAGE_END) == []
    assert const.split_signalr_records("") == []
    assert const.split_signalr_records("{}" + const.WS_MESSAGE_END) == ["{}"]


def test_normalize_release_version():
    _install_homeassistant_stub()
    const = _load_module("domonap_const", CONST_PATH)
    assert const.normalize_release_version("v1.3.18") == "1.3.18"
    assert const.normalize_release_version("1.3.18") == "1.3.18"
    assert const.normalize_release_version("  V2.0.0 ") == "2.0.0"
    assert const.normalize_release_version(None) is None
    assert const.normalize_release_version("") is None


def test_is_newer_version():
    _install_homeassistant_stub()
    const = _load_module("domonap_const", CONST_PATH)
    assert const.is_newer_version("1.3.19", "1.3.18")
    assert const.is_newer_version("v1.3.19", "1.3.18")
    assert const.is_newer_version("1.3.18", "1.3.9")
    assert not const.is_newer_version("1.3.18", "1.3.18")
    assert not const.is_newer_version("1.3.17", "1.3.18")
    assert not const.is_newer_version(None, "1.3.18")
    assert const.is_newer_version("1.3.19", None)


def test_parse_github_release_payload():
    _install_homeassistant_stub()
    const = _load_module("domonap_const", CONST_PATH)
    latest = {"tag_name": "1.3.19", "zipball_url": "https://example/zip"}
    assert const.parse_github_release_payload(latest)["tag_name"] == "1.3.19"
    listing = [
        {"tag_name": "1.3.19-rc", "prerelease": True},
        {"tag_name": "1.3.19", "draft": False, "prerelease": False},
    ]
    assert const.parse_github_release_payload(listing)["tag_name"] == "1.3.19"
    assert const.parse_github_release_payload([]) is None
    assert const.parse_github_release_payload("nope") is None


def test_update_entity_declares_install_feature():
    text = (ROOT / "custom_components" / "domonap" / "update.py").read_text()
    assert "UpdateEntityFeature.INSTALL" in text
    assert "timedelta(minutes=30)" in text


def test_install_component_from_zip_replaces_integration(tmp_path):
    import io
    import zipfile

    install = _load_module(
        "domonap_release_install",
        ROOT / "custom_components" / "domonap" / "release_install.py",
    )
    dest = tmp_path / "domonap"
    dest.mkdir()
    (dest / "old.py").write_text("old")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr(
            "DomoNAP_HA-1.3.19/custom_components/domonap/manifest.json",
            '{"version": "1.3.19"}',
        )
        archive.writestr(
            "DomoNAP_HA-1.3.19/custom_components/domonap/const.py",
            "DOMAIN = 'domonap'\n",
        )
        archive.writestr("DomoNAP_HA-1.3.19/README.MD", "docs")
        archive.writestr(
            "DomoNAP_HA-1.3.19/custom_components/domonap/../../evil.txt",
            "nope",
        )
    install.install_component_from_zip(buf.getvalue(), dest)

    assert (dest / "manifest.json").read_text() == '{"version": "1.3.19"}'
    assert (dest / "const.py").exists()
    assert not (dest / "old.py").exists()
    assert not (tmp_path / "evil.txt").exists()
    assert not (tmp_path / ".domonap.bak").exists()


def test_normalize_face_items():
    payload = {
        "faceImages": [
            {"imageId": "abc", "imageUrl": "https://s3/a.jpg", "faceName": "Me"},
            {"id": "def", "imageData": {"url": "https://s3/b.jpg"}},
            "https://s3/c.jpg",
        ]
    }
    faces = api.normalize_face_items(payload)
    assert len(faces) == 3
    assert faces[0]["imageId"] == "abc"
    assert faces[0]["imageUrl"] == "https://s3/a.jpg"
    assert faces[1]["imageId"] == "def"
    assert faces[1]["imageUrl"] == "https://s3/b.jpg"
    assert faces[2]["imageId"] == "https://s3/c.jpg"
    assert api.normalize_face_items({"error": "HTTP 500", "status": 500}) == []


def test_normalize_ticket_items():
    payload = {
        "results": [
            {"id": "t1", "ticketStatus": "Open", "text": "Hello", "themeHeader": "Lift"},
        ]
    }
    tickets = api.normalize_ticket_items(payload)
    assert tickets[0]["ticketId"] == "t1"
    summary = api.summarize_ticket(tickets[0])
    assert summary["theme"] == "Lift"
    assert summary["text"] == "Hello"
    nested = api.summarize_ticket(
        {"id": "t2", "themeHeader": "Gate", "lastMessage": {"text": "Когда откроют?"}}
    )
    assert nested["text"] == "Когда откроют?"
    assert api.normalize_ticket_items({"error": "HTTP 401", "status": 401}) == []


def test_extract_api_list_ignores_null_error_and_appeals_list():
    payload = {
        "error": None,
        "results": [{"id": "t1"}],
    }
    assert api.extract_api_list(payload, "results", "appealsList") == [{"id": "t1"}]
    assert api.extract_api_list(
        {"appealsList": [{"id": "a1"}]}, "results", "appealsList"
    ) == [{"id": "a1"}]
    assert api.extract_api_list(
        {"data": {"results": [{"id": "n1"}]}}, "results"
    ) == [{"id": "n1"}]


def test_theme_and_property_from_apk_shapes():
    client = IntercomAPI()
    theme = client._theme_from_suggestions(
        {
            "items": [
                {
                    "id": "th1",
                    "header": "Домофон",
                    "supportHelpType": "intercom",
                }
            ]
        }
    )
    assert theme["themeId"] == "th1"
    assert theme["themeHeader"] == "Домофон"
    assert theme["supportHelpType"] == "intercom"
    prop = client._property_from_keys(
        {"results": [{"name": "Калитка", "propertyId": "prop-1", "addressString": "д.3"}]}
    )
    assert prop["propertyId"] == "prop-1"
    assert prop["address"] == "д.3"


def test_normalize_ticket_messages():
    payload = {
        "results": [
            {
                "text": "Не открывается калитка",
                "name": "Я",
                "createdOn": "2026-08-01T10:00:00Z",
            },
            {
                "message": "Принято",
                "senderName": "Оператор",
                "isSupport": True,
                "createdOn": "2026-08-01T10:05:00Z",
            },
        ]
    }
    messages = api.normalize_ticket_messages(payload)
    assert len(messages) == 2
    assert messages[0]["text"] == "Не открывается калитка"
    assert messages[1]["isSupport"] is True
    assert messages[1]["name"] == "Оператор"
    incoming = api.normalize_ticket_messages(
        {
            "results": [
                {"text": "Сломался домофон", "nameCreatedBy": "Житель"},
                {"Text": "Мастер выехал", "NameCreatedBy": "Оператор", "IsIncoming": True},
                {"text": "Ждём", "createdBy": "support"},
            ]
        }
    )
    assert incoming[0]["isSupport"] is False
    assert incoming[0]["name"] == "Вы"
    assert incoming[1]["isSupport"] is True
    assert incoming[1]["name"] == "Оператор"
    assert incoming[2]["isSupport"] is True
    assert incoming[2]["name"] == "Поддержка"
    assert api.normalize_ticket_messages({"error": "HTTP 500", "status": 500}) == []
    assert api.normalize_ticket_messages(["коротко"])[0]["text"] == "коротко"


def test_domonap_dashboard_config_helper():
    _install_homeassistant_stub()
    const = _load_module("domonap_const", CONST_PATH)
    assert const.DASHBOARD_URL_PATH == "domonap-home"
    assert "-" in const.DASHBOARD_URL_PATH
    assert const.DASHBOARD_TITLE == "Domonap"
    assert const.is_domonap_dashboard_config(
        {"strategy": {"type": "custom:domonap"}}
    )
    assert not const.is_domonap_dashboard_config({"views": []})
    assert not const.is_domonap_dashboard_config(None)
    assert const.FACE_MAX_BYTES == 8 * 1024 * 1024


def test_dashboard_js_registers_official_strategy_tag():
    text = (
        ROOT / "custom_components" / "domonap" / "static" / "domonap-dashboard.js"
    ).read_text()
    assert "ll-strategy-dashboard-domonap" in text
    assert "custom:domonap-cabinet-card" in text
    assert "window.customStrategies" in text
    assert "iframe" not in text
    assert "9164270777" not in text
    assert "domofonPublicPin" in text


def test_dashboard_js_groups_by_street_and_kind():
    text = (
        ROOT / "custom_components" / "domonap" / "static" / "domonap-dashboard.js"
    ).read_text(encoding="utf-8")
    assert "function buildingOf(" in text
    assert "function doorKind(" in text
    assert "function groupBySite(" in text
    assert "\u00b7 Паркинг" in text
    assert "\u00b7 Кладовки" in text
    assert "parts.slice(-2)" not in text
    assert "allCallCards" in text
    assert "Калитки и входы" in text


def _dashboard_building_of(address: str) -> str:
    import re

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


def _dashboard_door_kind(address: str, name: str) -> str:
    import re

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


def test_dashboard_grouping_matches_manual_domofon_tabs():
    cases = [
        (
            "улица Малое Понизовье, д.3, п.5, э.14, кв.264",
            "Лифтовой холл 14 эт",
            "home",
            "Малое Понизовье, д.3",
        ),
        (
            "улица Малое Понизовье, д.3, п.5, э.-1, кладовка.140",
            "Вход 1, 1эт.",
            "home",
            "Малое Понизовье, д.3",
        ),
        (
            "улица Малое Понизовье, д.3, п.5, э.-1, кладовка.140",
            "Калитка 1",
            "home",
            "Малое Понизовье, д.3",
        ),
        (
            "улица Малое Понизовье, д.3, п.5, э.-1, кладовка.140",
            "Тамбур-шлюз",
            "storage",
            "Малое Понизовье, д.3",
        ),
        (
            "Паркинг : ул. Малое Понизовье, д.1А, п.1, э.1, место.122",
            "Выезд",
            "parking",
            "Малое Понизовье, д.1А",
        ),
        (
            "Саларьевская улица, д.8к1, п.1, э.4, кв.26",
            "1 Подъезд 1 Этаж Вход 1",
            "home",
            "Саларьевская, д.8к1",
        ),
        (
            "Саларьевская улица, д.12с2, п.1, э.1, машиноместа.121",
            "Въезд",
            "parking",
            "Саларьевская, д.12с2",
        ),
    ]
    for address, name, kind, building in cases:
        assert _dashboard_door_kind(address, name) == kind, name
        assert _dashboard_building_of(address) == building, address


def test_cabinet_html_has_no_hardcoded_phone():
    text = (
        ROOT / "custom_components" / "domonap" / "static" / "domonap-cabinet.html"
    ).read_text()
    assert "9164270777" not in text
    assert 'capture="user"' in text
    assert "jpegFromGalleryFile" in text


def test_summarize_ticket_omits_empty_messages():
    summary = api.summarize_ticket({"id": "t1", "themeHeader": "Lift", "text": "Hi"})
    assert "messages" not in summary
    with_msgs = api.summarize_ticket(
        {"id": "t2", "messages": [{"text": "ok", "isSupport": True}]}
    )
    assert with_msgs["messages"][0]["text"] == "ok"


def test_is_api_error():
    assert api.is_api_error({"error": "HTTP 401", "status": 401})
    assert api.is_api_error({"error": "Session expired", "ok": False, "body": ""})
    assert api.is_api_error({"error": "No access token available", "ok": False})
    assert not api.is_api_error({"error": None, "results": [{"id": "k1"}]})
    assert not api.is_api_error({"error": "", "results": []})
    assert not api.is_api_error({"faceImages": []})
    assert not api.is_api_error("ok")


def test_fetch_keys_by_type_ignores_null_error():
    client = IntercomAPI()

    async def fake_get_paged_keys(**kwargs):
        return {
            "error": None,
            "results": [{"id": "k1", "name": "Door", "doorId": "d1"}],
            "pageCount": 1,
        }

    client.get_paged_keys = fake_get_paged_keys
    keys = asyncio.run(client._fetch_keys_by_type(0))
    assert keys[0]["id"] == "k1"


def test_fetch_keys_by_type_handles_non_dict_payload():
    client = IntercomAPI()

    async def fake_get_paged_keys(**kwargs):
        return "not-a-dict"

    client.get_paged_keys = fake_get_paged_keys
    assert asyncio.run(client._fetch_keys_by_type(0)) == []


def test_fetch_keys_by_type_reads_items_wrapper():
    client = IntercomAPI()

    async def fake_get_paged_keys(**kwargs):
        return {
            "error": None,
            "items": [{"id": "k2", "name": "Gate", "doorId": "d2"}],
            "pageCount": 1,
        }

    client.get_paged_keys = fake_get_paged_keys
    keys = asyncio.run(client._fetch_keys_by_type(0))
    assert keys[0]["id"] == "k2"


def test_fetch_all_keys_skips_non_dict_items():
    client = IntercomAPI()

    async def fake_fetch(keys_type, per_page=100):
        return ["bad", {"id": "k1", "doorId": "d1", "name": "Дверь"}]

    client._fetch_keys_by_type = fake_fetch
    data = asyncio.run(client._fetch_all_keys("doors"))
    assert len(data["results"]) == 1
    assert data["results"][0]["id"] == "k1"


def test_get_all_tickets_returns_none_on_first_page_error():
    client = IntercomAPI()

    async def fail(**kwargs):
        return {"error": "HTTP 500", "status": 500, "ok": False}

    client.get_paged_tickets = fail
    assert asyncio.run(client.get_all_tickets()) is None


def test_get_all_tickets_empty_list_is_not_error():
    client = IntercomAPI()

    async def empty(**kwargs):
        return {"error": None, "results": []}

    client.get_paged_tickets = empty
    assert asyncio.run(client.get_all_tickets()) == []


def test_redact_payload_hides_tokens():
    redacted = api._redact_payload(
        {"refreshToken": "secret", "keysType": 0, "confirmCode": "1234"}
    )
    assert redacted["refreshToken"] == "***"
    assert redacted["confirmCode"] == "***"
    assert redacted["keysType"] == 0
    assert api._redact_payload("not-a-dict") == "not-a-dict"


def test_extract_api_list_video_area_wrappers():
    assert api.extract_api_list(
        {"error": None, "results": [{"category": "Parking"}]}
    ) == [{"category": "Parking"}]
    assert api.extract_api_list(
        {"items": [{"category": "House", "id": "a1"}]}
    ) == [{"category": "House", "id": "a1"}]


def test_dashboard_js_matches_call_sensors_without_english_entity_id():
    text = (
        ROOT / "custom_components" / "domonap" / "static" / "domonap-dashboard.js"
    ).read_text()
    assert 'id.indexOf("incoming_call")' not in text
    assert 'id.indexOf("door_code")' not in text
    assert "domofonPublicPin" in text
    assert 'id.startsWith("binary_sensor.") && doorIdOf(state)' in text


def test_cabinet_card_guards_double_custom_element_define():
    text = (
        ROOT / "custom_components" / "domonap" / "static" / "domonap-card.js"
    ).read_text()
    assert 'customElements.get("domonap-cabinet-card")' in text
    assert "domonap-cabinet-card" in text
    assert "jpegFromGalleryFile" in text
    assert 'type: "image/jpeg"' in text


def test_cabinet_card_face_thumbnail_preview_and_delete_layout():
    text = (
        ROOT / "custom_components" / "domonap" / "static" / "domonap-card.js"
    ).read_text()
    assert "lightbox" in text
    assert "ID ·" in text
    assert ".face .cap" in text
    assert "flex-direction:column" in text
    assert "openFacePreview" in text or "_openFacePreview" in text
    assert "Закрыть" in text
    assert 'class="thumb"' in text
    assert 'class="uid"' in text
    assert ".face .del { width:100%;" in text
    assert "width:156px" in text
    assert "Нажмите миниатюру" in text
    assert "id.slice(0, 8)" in text


def test_cabinet_html_face_thumbnail_preview_and_delete_layout():
    text = (
        ROOT / "custom_components" / "domonap" / "static" / "domonap-cabinet.html"
    ).read_text()
    assert "lightbox" in text
    assert "ID ·" in text
    assert "flex-direction: column" in text
    assert "openFacePreview" in text
    assert "Закрыть" in text
    assert 'class="thumb"' in text
    assert 'class="uid"' in text
    assert ".face .del { width: 100%;" in text
    assert "width: 156px" in text
    assert "Нажмите миниатюру" in text


def test_lovelace_resource_query_is_updated_on_version_bump():
    _install_homeassistant_stub()
    const = _load_module("domonap_const", CONST_PATH)
    items = [
        {"id": "r1", "url": "/domonap-static/domonap-card.js?v=1.4.4"},
        {"id": "r2", "url": "/domonap-static/domonap-dashboard.js?v=1.4.4"},
    ]
    wanted = [
        "/domonap-static/domonap-card.js?v=1.4.7",
        "/domonap-static/domonap-dashboard.js?v=1.4.7",
    ]
    to_create, to_update = const.planned_lovelace_resource_changes(items, wanted)
    assert to_create == []
    assert to_update == [
        ("r1", "/domonap-static/domonap-card.js?v=1.4.7"),
        ("r2", "/domonap-static/domonap-dashboard.js?v=1.4.7"),
    ]
    to_create, to_update = const.planned_lovelace_resource_changes([], wanted)
    assert to_create == wanted
    assert to_update == []
    to_create, to_update = const.planned_lovelace_resource_changes(items, [
        "/domonap-static/domonap-card.js?v=1.4.4",
        "/domonap-static/domonap-dashboard.js?v=1.4.4",
    ])
    assert to_create == []
    assert to_update == []


FACE_IMAGE_PATH = ROOT / "custom_components" / "domonap" / "face_image.py"
face_image = _load_module("domonap_face_image", FACE_IMAGE_PATH)


def test_iphone_jpeg_filename_becomes_jpg():
    name, ctype = api._normalize_face_upload_meta("IMG_2940.jpeg", "image/jpeg")
    assert name == "IMG_2940.jpg"
    assert ctype == "image/jpeg"
    name, ctype = api._normalize_face_upload_meta("photo.JPG", "image/jpg")
    assert name == "photo.jpg"
    assert ctype == "image/jpeg"
    name, ctype = face_image.normalize_face_meta("IMG_2940.jpeg", "image/jpeg")
    assert name == "IMG_2940.jpg"
    assert ctype == "image/jpeg"


def test_sniff_image_type():
    assert face_image.sniff_image_type(b"\xff\xd8\xff\xe0" + b"\x00" * 12) == "jpeg"
    assert face_image.sniff_image_type(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8) == "png"
    assert face_image.sniff_image_type(b"RIFF" + b"\x00" * 4 + b"WEBP") == "webp"
    assert face_image.sniff_image_type(b"\x00\x00\x00\x18ftypheic" + b"\x00" * 8) == "heic"
    assert face_image.sniff_image_type(b"not-an-image") is None


def test_prepare_face_jpeg_renames_iphone_file():
    jpeg = b"\xff\xd8\xff" + b"\x00" * 32
    data, name, ctype = face_image.prepare_face_jpeg(
        jpeg, "IMG_2940.jpeg", "image/jpeg"
    )
    assert data.startswith(b"\xff\xd8\xff")
    assert name == "IMG_2940.jpg"
    assert ctype == "image/jpeg"


def test_prepare_face_heic_without_decoder_raises():
    heic = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 32
    try:
        face_image.prepare_face_jpeg(heic, "IMG_2940.HEIC", "image/heic")
    except face_image.FaceImageError as err:
        assert "HEIC" in str(err)
    else:
        raise AssertionError("expected FaceImageError for HEIC")


def test_format_face_upload_error_hides_raw_http_400():
    payload = {
        "error": "HTTP 400",
        "ok": False,
        "status": 400,
        "body": '{"statusCode":400,"statusText":"BAD_REQUEST","errorText":"Некорректный запрос."}',
    }
    text = face_image.format_face_upload_error("IMG_2940.jpg", payload)
    assert "IMG_2940.jpg" in text
    assert "JPEG" in text
    assert "HTTP 400" not in text
    assert "{'error'" not in text


def test_face_name_from_iphone_filename():
    assert face_image.face_name_from_filename("IMG_2940.jpeg") == "IMG_2940"
    assert api._face_name_from_filename("IMG_2940.jpg") == "IMG_2940"


def test_prepare_face_jpeg_with_pillow_resizes_and_renames():
    pytest.importorskip("PIL")
    import io
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (2000, 1000), (10, 20, 30)).save(buf, format="JPEG", quality=90)
    data, name, ctype = face_image.prepare_face_jpeg(
        buf.getvalue(), "IMG_2940.jpeg", "image/jpeg"
    )
    assert name == "IMG_2940.jpg"
    assert ctype == "image/jpeg"
    out = Image.open(io.BytesIO(data))
    assert out.format == "JPEG"
    assert max(out.size) <= face_image.FACE_JPEG_MAX_EDGE
