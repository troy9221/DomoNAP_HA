import asyncio
import importlib.util
import sys
import types
import uuid
from pathlib import Path

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
    assert api.normalize_ticket_messages({"error": "HTTP 500", "status": 500}) == []
    assert api.normalize_ticket_messages(["коротко"])[0]["text"] == "коротко"


def test_domonap_dashboard_config_helper():
    _install_homeassistant_stub()
    const = _load_module("domonap_const", CONST_PATH)
    assert const.DASHBOARD_URL_PATH == "domonap-home"
    assert "-" in const.DASHBOARD_URL_PATH
    assert const.is_domonap_dashboard_config(
        {"strategy": {"type": "custom:domonap"}}
    )
    assert not const.is_domonap_dashboard_config({"views": []})
    assert not const.is_domonap_dashboard_config(None)


def test_is_api_error():
    assert api.is_api_error({"error": "HTTP 401", "status": 401})
    assert not api.is_api_error({"faceImages": []})
    assert not api.is_api_error("ok")


def test_fetch_keys_by_type_handles_non_dict_payload():
    client = IntercomAPI()

    async def fake_get_paged_keys(**kwargs):
        return "not-a-dict"

    client.get_paged_keys = fake_get_paged_keys
    assert asyncio.run(client._fetch_keys_by_type(0)) == []
