from homeassistant.const import Platform


DOMAIN = 'domonap'
API = "api"
GITHUB_REPO = "troy9221/DomoNAP_HA"
GITHUB_RELEASES_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
CONF_COUNTRY_CODE = "country_code"
CONF_PHONE_NUMBER = "phone_number"
CONF_CONFIRM_CODE = "confirm_code"

PARAM_ACCESS_TOKEN = "access_token"
PARAM_REFRESH_TOKEN = "refresh_token"
PARAM_REFRESH_EXPIRATION = "refresh_expiration_date"
PARAM_DEVICE_TOKEN = "device_token"
PARAM_INSTANCE_ID = "instance_id"
PARAM_WEBRTC_PROXY_SECRET = "webrtc_proxy_secret"
EVENT_INCOMING_CALL = "domonap_incoming_call"
EVENT_CALL_ENDED = "domonap_call_ended"
WEBRTC_PROXY = "webrtc_proxy"
MEDIA_PROXY = "media_proxy"
UPDATE_COORDINATOR = "update_coordinator"

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
