from homeassistant.const import Platform


DOMAIN = 'domonap'
API = "api"
GITHUB_REPO = "troy9221/DomoNAP_HA"
GITHUB_RELEASES_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
GITHUB_RELEASES_LIST_URL = (
    f"https://api.github.com/repos/{GITHUB_REPO}/releases?per_page=5"
)
CONF_COUNTRY_CODE = "country_code"
CONF_PHONE_NUMBER = "phone_number"
CONF_CONFIRM_CODE = "confirm_code"
CONF_REGISTER_DEVICE_TOKEN = "register_device_token"

PARAM_ACCESS_TOKEN = "access_token"
PARAM_REFRESH_TOKEN = "refresh_token"
PARAM_REFRESH_EXPIRATION = "refresh_expiration_date"
PARAM_DEVICE_TOKEN = "device_token"
PARAM_INSTANCE_ID = "instance_id"
PARAM_WEBRTC_PROXY_SECRET = "webrtc_proxy_secret"
EVENT_INCOMING_CALL = "domonap_incoming_call"
EVENT_CALL_ENDED = "domonap_call_ended"
EVENT_RECEIVE_MESSAGE = "domonap_receive_message"
EVENT_USER_STATUS_CHANGED = "domonap_user_status_changed"
WEBRTC_PROXY = "webrtc_proxy"
MEDIA_PROXY = "media_proxy"
UPDATE_COORDINATOR = "update_coordinator"
ACCOUNT_COORDINATOR = "account_coordinator"
DASHBOARD_URL_PATH = "domonap-home"
DASHBOARD_TITLE = "Domonap"
DASHBOARD_STRATEGY_TYPE = "custom:domonap"
DASHBOARD_GENERATED_KEY = "domonap_generated"
DASHBOARD_SETUP_FLAG = "_dashboard_setup_scheduled"
FACE_MAX_BYTES = 8 * 1024 * 1024


def is_domonap_dashboard_config(config: object) -> bool:
    """True, если Lovelace-конфиг — автодашборд интеграции."""
    if not isinstance(config, dict):
        return False
    if config.get(DASHBOARD_GENERATED_KEY) is True:
        return True
    strategy = config.get("strategy")
    if not isinstance(strategy, dict):
        return False
    return strategy.get("type") == DASHBOARD_STRATEGY_TYPE

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.CAMERA,
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.IMAGE,
    Platform.UPDATE,
]

RESET_DELAY = 10 # секунды

WS_MESSAGE_END = "\x1e"
WS_HANDSHAKE_MESSAGE = '{"protocol":"json","version":1}' + WS_MESSAGE_END
WS_PING_MESSAGE = '{"type":6}' + WS_MESSAGE_END
WS_URL = "wss://api.domonap.ru/notificationHub?id="

# SignalR keep-alive (значения по умолчанию клиента Microsoft SignalR).
# Клиент шлёт app-level ping ({"type":6}) каждые WS_KEEPALIVE_INTERVAL секунд,
# иначе сервер разрывает соединение по ClientTimeoutInterval и звонки пропадают.
# WS_SERVER_TIMEOUT — если за это время нет ни одного сообщения (включая
# серверные ping), соединение считается мёртвым и переподключается.
WS_KEEPALIVE_INTERVAL = 15  # секунды
WS_SERVER_TIMEOUT = 30  # секунды


def split_signalr_records(raw: str) -> list[str]:
    """Разбить WebSocket-кадр SignalR на отдельные JSON-записи.

    Сервер может упаковать несколько сообщений в один кадр, разделяя их
    символом 0x1e. Если разбирать кадр целиком, json.loads падает и
    ReceivePush о входящем звонке теряется.
    """
    return [record for record in raw.split(WS_MESSAGE_END) if record]


def normalize_release_version(value: str | None) -> str | None:
    """Нормализовать tag GitHub-релиза к виду манифеста (`1.3.18`)."""
    if not isinstance(value, str):
        return None
    version = value.strip()
    if len(version) > 1 and version[0] in "vV" and version[1].isdigit():
        version = version[1:]
    return version or None


def _version_tuple(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in value.split("."):
        digits = ""
        for char in chunk:
            if char.isdigit():
                digits += char
            else:
                break
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def is_newer_version(latest: str | None, installed: str | None) -> bool:
    """True, если latest строго новее установленной версии."""
    latest_norm = normalize_release_version(latest)
    installed_norm = normalize_release_version(installed)
    if not latest_norm:
        return False
    if not installed_norm:
        return True
    left = _version_tuple(latest_norm)
    right = _version_tuple(installed_norm)
    width = max(len(left), len(right))
    left += (0,) * (width - len(left))
    right += (0,) * (width - len(right))
    return left > right


def parse_github_release_payload(payload: object) -> dict | None:
    """Достать объект релиза из /releases/latest или из списка /releases."""
    if isinstance(payload, list):
        for item in payload:
            if (
                isinstance(item, dict)
                and not item.get("draft")
                and not item.get("prerelease")
            ):
                return item
        first = payload[0] if payload else None
        return first if isinstance(first, dict) else None
    if isinstance(payload, dict) and (
        payload.get("tag_name") or payload.get("zipball_url")
    ):
        return payload
    return None


def github_tag_archive_url(tag: str) -> str:
    return f"https://github.com/{GITHUB_REPO}/archive/refs/tags/{tag}.zip"


def planned_lovelace_resource_changes(
    items: list | None, wanted_urls: list[str]
) -> tuple[list[str], list[tuple[object, str]]]:
    """Какие Lovelace JS-ресурсы создать и какие обновить (?v=)."""
    by_path: dict[str, dict] = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("url") or "").split("?")[0]
        if path:
            by_path[path] = item
    to_create: list[str] = []
    to_update: list[tuple[object, str]] = []
    for url in wanted_urls:
        path = url.split("?")[0]
        existing = by_path.get(path)
        if existing is None:
            to_create.append(url)
            continue
        current = str(existing.get("url") or "")
        item_id = existing.get("id")
        if current != url and item_id is not None:
            to_update.append((item_id, url))
    return to_create, to_update
