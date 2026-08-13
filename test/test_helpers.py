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


def test_fetch_keys_by_type_handles_non_dict_payload():
    client = IntercomAPI()

    async def fake_get_paged_keys(**kwargs):
        return "not-a-dict"

    client.get_paged_keys = fake_get_paged_keys
    assert asyncio.run(client._fetch_keys_by_type(0)) == []
