import logging
import aiohttp
import asyncio
import time
from datetime import datetime, timezone
from secrets import token_bytes, token_urlsafe
from typing import Any, Callable, Dict, Optional, Union
from uuid import UUID

_LOGGER = logging.getLogger(__name__)


DEFAULT_DEVICE_PLATFORM = "Android"
DEFAULT_DOM_APP = "mobile"
DEFAULT_JSON_CONTENT_TYPE = "application/json; charset=UTF-8"
DEFAULT_USER_AGENT = "okhttp/5.3.2"
# User-Agent клиента Microsoft SignalR (Java, v8.0.6) для negotiate и WS-апгрейда.
SIGNALR_USER_AGENT = "Microsoft SignalR/8.0 (8.0.6; Linux; Java; 0; The Android Project)"
_ANDROID_GUID_RETRY_LIMIT = 8
_GENERATED_ANDROID_GUIDS: set[str] = set()
MAX_KEY_PAGES = 50
KEYS_CACHE_TTL = 60.0

# API Domonap использует .NET enum KeysType. Разные типы возвращают РАЗНЫЕ
# подмножества ключей (двери/калитки/пропуски), а не строгие надмножества,
# поэтому чтобы получить действительно все двери, нужно опросить несколько
# типов и объединить результаты с дедупликацией по id.
#   0 / Main      — основные ключи (двери резидента)
#   1 / Resident  — двери резидента
#   2 / All       — «все» ключи (но на практике неполный набор)
#   3 / Pass      — пропуски
#   4+            — дополнительные типы (калитки, служебные двери и т.п.)
# Неподдерживаемые типы возвращают ошибку и просто дают пустой список.
KEY_TYPES_ALL = (0, 1, 2, 3, 4, 5, 6)
KEY_TYPES_DOORS = (0, 1, 2, 4, 5, 6)
KEY_TYPES_PASSES = (3,)


def _with_app_header_suffix(value: str) -> str:
    return value if value.endswith(";") else f"{value};"


def _generate_android_guid() -> str:
    random_bytes = bytearray(token_bytes(16))
    # Match Java/Android UUID.randomUUID(): RFC 4122 variant, version 4.
    random_bytes[6] = (random_bytes[6] & 0x0F) | 0x40
    random_bytes[8] = (random_bytes[8] & 0x3F) | 0x80
    return str(UUID(bytes=bytes(random_bytes)))


def _generate_unique_android_guid() -> str:
    for _ in range(_ANDROID_GUID_RETRY_LIMIT):
        guid = _generate_android_guid()
        if guid not in _GENERATED_ANDROID_GUIDS:
            _GENERATED_ANDROID_GUIDS.add(guid)
            return guid
    guid = _generate_android_guid()
    _GENERATED_ANDROID_GUIDS.add(guid)
    return guid


def _generate_device_token() -> str:
    return f"{token_urlsafe(22)}:APA91b{token_urlsafe(134)}"


def is_fcm_like_token(value: Optional[str]) -> bool:
    """True, если строка похожа на FCM device token ({id}:APA91b{...})."""
    if not isinstance(value, str):
        return False
    return ":APA91b" in value and len(value) > 50


def is_android_guid(value: Optional[str]) -> bool:
    if not isinstance(value, str):
        return False
    try:
        guid = UUID(value)
    except ValueError:
        return False
    return guid.version == 4 and value == str(guid)


class IntercomAPI:
    def __init__(
        self,
        base_url: str = "https://api.domonap.ru",
        device_token: Optional[str] = None,
        instance_id: Optional[str] = None,
        device_platform: str = DEFAULT_DEVICE_PLATFORM,
        dom_app: str = DEFAULT_DOM_APP,
    ):
        self.base_url = base_url.rstrip("/")
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.refresh_expiration_date: Optional[str] = None
        self.device_token = device_token or _generate_device_token()
        self.instance_id = instance_id or _generate_unique_android_guid()
        self.device_platform = device_platform
        self.dom_app = dom_app
        self._refresh_token_invalid: bool = False
        self._refresh_lock = asyncio.Lock()
        self.headers: Dict[str, str] = {
            "User-Agent": DEFAULT_USER_AGENT,
            "dom-app": _with_app_header_suffix(self.dom_app),
            "dom-platform": _with_app_header_suffix(self.device_platform),
            "instanceId": _with_app_header_suffix(self.instance_id),
        }
        self.token_update_callback: Optional[
            Callable[[Optional[str], Optional[str], Optional[str]], None]
        ] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._external_session: Optional[aiohttp.ClientSession] = None
        self._closed = False
        self._keys_cache: Dict[str, tuple[float, dict]] = {}
        self._keys_cache_lock = asyncio.Lock()

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._closed:
            raise RuntimeError("Client is closed")
        if not self._session or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            # Не кладём API-заголовки в defaults сессии: иначе header_set
            # (SignalR) не сможет убрать instanceId — aiohttp мержит defaults.
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def _ensure_external_session(self) -> aiohttp.ClientSession:
        if self._closed:
            raise RuntimeError("Client is closed")
        if not self._external_session or self._external_session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            self._external_session = aiohttp.ClientSession(timeout=timeout)
        return self._external_session

    async def close(self):
        self._closed = True
        if self._session and not self._session.closed:
            await self._session.close()
        if self._external_session and not self._external_session.closed:
            await self._external_session.close()

    async def __aenter__(self):
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()

    def set_tokens(
        self,
        access_token: Optional[str],
        refresh_token: Optional[str],
        refresh_expiration_date: Optional[str],
    ):
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.refresh_expiration_date = refresh_expiration_date
        if refresh_token:
            self._refresh_token_invalid = False
        self.headers.pop("Authorization", None)

    def signalr_headers(self) -> Dict[str, str]:
        """Заголовки для SignalR (negotiate + WebSocket-апгрейд).

        В приложении hub использует отдельный HTTP-клиент: `dom-app` /
        `dom-platform` и User-Agent Microsoft SignalR, без instanceId.
        Авторизацию (Bearer) добавляет вызывающий код.
        """
        return {
            "User-Agent": SIGNALR_USER_AGENT,
            "dom-app": self.headers["dom-app"],
            "dom-platform": self.headers["dom-platform"],
        }

    def _parse_dt(self, val: str) -> Optional[datetime]:
        fmts = ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z")
        for fmt in fmts:
            try:
                return datetime.strptime(val, fmt)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00"))
        except ValueError:
            pass
        _LOGGER.warning("Cannot parse datetime: %s", val)
        return None

    def _now_utc(self) -> datetime:
        return datetime.now(timezone.utc)

    def _refresh_expired(self) -> bool:
        if not self.refresh_token or not self.refresh_expiration_date:
            return False
        exp = self._parse_dt(self.refresh_expiration_date)
        return bool(exp and self._now_utc() >= exp)

    def has_valid_refresh_token(self) -> bool:
        return bool(
            self.refresh_token
            and not self._refresh_token_invalid
            and not self._refresh_expired()
        )

    def mark_session_expired(self, reason: str) -> None:
        self._mark_refresh_token_invalid(reason)

    def _mark_refresh_token_invalid(self, reason: str) -> None:
        if self._refresh_token_invalid and not self.refresh_token and not self.access_token:
            return
        _LOGGER.warning("Domonap session expired: %s", reason)
        self._refresh_token_invalid = True
        self.access_token = None
        self.refresh_token = None
        self.refresh_expiration_date = None
        self.headers.pop("Authorization", None)
        if self.token_update_callback:
            self.token_update_callback(None, None, None)

    def _refresh_unavailable_error(self, error: str) -> Dict[str, Any]:
        return {
            "error": error,
            "ok": False,
            "session_expired": self._refresh_token_invalid,
            "body": "",
        }

    def _ensure_refresh_is_available(self) -> bool:
        if self._refresh_token_invalid:
            return False
        if self._refresh_expired():
            self._mark_refresh_token_invalid("refresh token expired")
            return False
        return bool(self.refresh_token)

    async def _ensure_alive(self) -> None:
        if self._refresh_expired():
            self._mark_refresh_token_invalid("refresh token expired")

    async def _refresh_for_retry(self, first_try_access_token: Optional[str]) -> bool:
        if not self._ensure_refresh_is_available():
            return False
        async with self._refresh_lock:
            if (
                first_try_access_token
                and self.access_token
                and self.access_token != first_try_access_token
            ):
                return True
            if not self._ensure_refresh_is_available():
                return False
            result = await self.update_token()
            return bool(isinstance(result, dict) and result.get("ok"))

    async def _post(
        self,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        need_auth: bool = False,
        ensure_alive: bool = True,
        send_auth: Optional[bool] = None,
        expect: str = "json",
        retry_on_401: bool = True,
        header_set: Optional[Dict[str, str]] = None,
    ) -> Union[Dict[str, Any], str]:
        if send_auth is None:
            send_auth = need_auth
        if need_auth:
            if self._refresh_token_invalid:
                return self._refresh_unavailable_error("Session expired")
            if not self.access_token:
                if not await self._refresh_for_retry(None):
                    return {"error": "No access token available", "ok": False, "body": ""}
            if ensure_alive:
                await self._ensure_alive()
            if not self.access_token:
                return self._refresh_unavailable_error("Session expired")

        session = await self._ensure_session()
        url = f"{self.base_url}{path}"
        first_try_access_token = self.access_token

        async def _once() -> Union[Dict[str, Any], str]:
            headers = dict(self.headers if header_set is None else header_set)
            if payload is not None:
                headers["Content-Type"] = DEFAULT_JSON_CONTENT_TYPE
            if send_auth and self.access_token:
                headers["Authorization"] = f"Bearer {self.access_token}"
            request_kwargs: Dict[str, Any] = {"headers": headers, "ssl": False}
            if payload is not None:
                request_kwargs["json"] = payload
            async with session.post(url, **request_kwargs) as resp:
                if 200 <= resp.status < 300:
                    if expect == "json":
                        try:
                            return await resp.json(content_type=None)
                        except Exception:
                            body_text = await resp.text()
                            return {
                                "error": "Invalid JSON response",
                                "ok": False,
                                "status": resp.status,
                                "body": body_text[:2000],
                            }
                    return await resp.text()

                body_text = ""
                try:
                    body_text = await resp.text()
                except Exception:
                    pass
                err = {
                    "error": f"HTTP {resp.status}",
                    "status": resp.status,
                    "body": body_text[:2000],
                }
                _LOGGER.error(
                    "Request failed: POST %s payload=%s -> %s", path, payload, err
                )
                return err

        result = await _once()
        if (
            retry_on_401
            and self.refresh_token
            and isinstance(result, dict)
            and result.get("status") == 401
        ):
            _LOGGER.warning("401 Unauthorized, refreshing token and retrying %s", path)
            if await self._refresh_for_retry(first_try_access_token):
                result = await _once()
        return result

    async def update_device_token(self, device_token: str) -> bool:
        _LOGGER.debug("UpdateDeviceToken start")
        result = await self._post(
            "/sso-api/Authorization/UpdateDeviceToken",
            {"deviceToken": device_token, "platform": self.device_platform},
            need_auth=True,
            ensure_alive=False,
            expect="text",
            retry_on_401=True,
        )
        if isinstance(result, dict) and "error" in result:
            _LOGGER.error("UpdateDeviceToken failed: %s", result)
            return False
        _LOGGER.debug("UpdateDeviceToken ok")
        return True

    async def authorize(self, country_code: str, phone_number: str) -> Union[bool, Dict[str, Any]]:
        payload = {"phoneNumber": self._phone_number(country_code, phone_number)}
        res = await self._post("/sso-api/Authorization/Authorize", payload, expect="text", need_auth=False)
        if isinstance(res, dict) and "error" in res:
            return {"error": f"Authorization failed: {res}"}
        return True

    async def confirm_authorization(
        self,
        country_code: str,
        phone_number: str,
        confirm_code: str,
        device_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = {
            "phoneNumber": self._phone_number(country_code, phone_number),
            "confirmCode": confirm_code,
            "deviceToken": device_token or self.device_token,
        }
        res = await self._post("/sso-api/Authorization/ConfirmAuthorization", payload, expect="json", need_auth=False)
        if isinstance(res, dict) and "error" in res and "status" in res:
            return res
        try:
            ct = res["completeToken"]
            self.set_tokens(ct["accessToken"], ct["refreshToken"], ct["refreshExpirationDate"])
            if self.token_update_callback:
                self.token_update_callback(ct["accessToken"], ct["refreshToken"], ct["refreshExpirationDate"])
            await self.update_device_token(device_token or self.device_token)
        except Exception as e:
            _LOGGER.exception("Unexpected response on confirm_authorization: %s", e)
        return res

    def _phone_number(self, country_code: str, phone_number: str) -> Dict[str, int]:
        return {"countryCode": int(country_code), "number": int(phone_number)}

    async def update_token(self) -> Dict[str, Any]:
        if self._refresh_token_invalid:
            return self._refresh_unavailable_error("Refresh token is invalid")
        if not self.refresh_token:
            return {"error": "No refresh token available", "ok": False, "body": ""}
        if self._refresh_expired():
            self._mark_refresh_token_invalid("refresh token expired")
            return self._refresh_unavailable_error("Refresh token expired")
        _LOGGER.info("Begin refreshToken. Old refresh_expiration=%s now=%s", self.refresh_expiration_date, self._now_utc())
        res = await self._post(
            "/sso-api/Authorization/RefreshToken",
            {"refreshToken": self.refresh_token},
            expect="json",
            need_auth=False,
            retry_on_401=False,
        )
        if isinstance(res, dict) and "error" in res and "status" in res:
            if res["status"] in (400, 401, 403):
                self._mark_refresh_token_invalid(f"refresh token rejected with HTTP {res['status']}")
            return res
        try:
            self.set_tokens(res["accessToken"], res["refreshToken"], res["refreshExpirationDate"])
            _LOGGER.info("Tokens refreshed. New refresh_expiration=%s", res["refreshExpirationDate"])
            if self.token_update_callback:
                self.token_update_callback(res["accessToken"], res["refreshToken"], res["refreshExpirationDate"])
            return {
                "ok": True,
                "access_token": res["accessToken"],
                "refresh_token": res["refreshToken"],
                "refresh_expiration_date": res["refreshExpirationDate"],
            }
        except Exception as e:
            _LOGGER.exception("Unexpected refresh response: %s", e)
            return {"error": "Unexpected refresh response format", "ok": False, "body": str(res)}

    async def get_user(self) -> Union[Dict[str, Any], str]:
        return await self._post("/sso-api/User/GetUser", need_auth=True, expect="json")

    async def get_username(self) -> Optional[str]:
        user = await self.get_user()
        if isinstance(user, dict) and "error" not in user:
            profile = user.get("userProfile")
            if isinstance(profile, dict):
                self._log_call_mode_fields(user)
                return profile.get("username")
        return None

    @staticmethod
    def _log_call_mode_fields(user: dict) -> None:
        if not _LOGGER.isEnabledFor(logging.DEBUG):
            return
        keywords = ("call", "notif", "push", "phone", "sip", "voip", "mode", "type")

        def _scan(prefix: str, obj: Any) -> None:
            if isinstance(obj, dict):
                for k, v in obj.items():
                    key = f"{prefix}.{k}" if prefix else str(k)
                    if isinstance(v, (dict, list)):
                        _scan(key, v)
                    elif any(w in str(k).lower() for w in keywords):
                        _LOGGER.debug("Profile candidate field: %s = %r", key, v)
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    _scan(f"{prefix}[{i}]", item)

        _LOGGER.debug("Full GetUser response: %s", user)
        _scan("", user)

    async def get_paged_keys(self, per_page: int = 100, current_page: int = 1, keys_type="Main"):
        """Получить ключи по типу. keys_type может быть строкой ('Main') или числом (0, 1, 2...)."""
        payload = {
            "currentPage": current_page,
            "perPage": per_page,
            "keysType": keys_type,
        }
        return await self._post("/client-api/Key/GetPagedKeysByKeysType", payload, need_auth=True, expect="json")

    async def _fetch_keys_by_type(self, keys_type, per_page: int = 100) -> list:
        """Получить все ключи указанного типа с пагинацией. Возвращает список ключей или пустой список."""
        all_keys = []
        current_page = 1
        type_label = str(keys_type)

        while True:
            keys_data = await self.get_paged_keys(
                per_page=per_page, current_page=current_page, keys_type=keys_type
            )

            if not isinstance(keys_data, dict):
                _LOGGER.debug(
                    "Unexpected keys payload for type '%s': %s",
                    type_label,
                    type(keys_data).__name__,
                )
                return []

            if "error" in keys_data:
                _LOGGER.debug(
                    "Key type '%s' not supported: %s", type_label, keys_data.get("error")
                )
                return []

            keys = keys_data.get("results", [])
            if keys:
                key_names = [str(k.get("name")) for k in keys if isinstance(k, dict)]
                _LOGGER.debug(
                    "Found %d keys of type '%s' on page %d: %s",
                    len(keys), type_label, current_page, key_names,
                )
                all_keys.extend(keys)
            else:
                if current_page == 1:
                    _LOGGER.debug("No keys of type '%s' found", type_label)
                break

            page_count = keys_data.get("pageCount", 1)
            if current_page >= page_count:
                break

            current_page += 1

            # Protection against infinite loop
            if current_page > MAX_KEY_PAGES:
                _LOGGER.warning("Too many pages for key type '%s', aborting", type_label)
                break

        return all_keys

    @staticmethod
    def is_pass_key(key: dict) -> bool:
        """Determine if a key is a pass (not a door).
        Passes have names like 'Пропуск от ...'."""
        name = (key.get("name") or "").strip().lower()
        return name.startswith("пропуск ")

    def invalidate_keys_cache(self) -> None:
        """Сбросить кэш ключей (например, после изменения состава дверей)."""
        self._keys_cache.clear()

    async def get_all_keys(self, keys_filter: str = "all") -> dict:
        """Get all user keys with filtering and pagination (кэшируется).

        При старте HA каждая платформа вызывает get_all_keys независимо;
        чтобы не дёргать API 5+ раз, результат кэшируется на KEYS_CACHE_TTL.
        """
        now = time.monotonic()

        cached = self._keys_cache.get(keys_filter)
        if cached is not None and (now - cached[0]) < KEYS_CACHE_TTL:
            _LOGGER.debug("Keys cache hit (filter=%s)", keys_filter)
            return cached[1]

        async with self._keys_cache_lock:
            # Повторная проверка после захвата блокировки: пока мы ждали,
            # другой вызов мог уже наполнить кэш.
            cached = self._keys_cache.get(keys_filter)
            now = time.monotonic()
            if cached is not None and (now - cached[0]) < KEYS_CACHE_TTL:
                _LOGGER.debug("Keys cache hit after lock (filter=%s)", keys_filter)
                return cached[1]

            combined_data = await self._fetch_all_keys(keys_filter)
            # Кэшируем только успешный результат (без ошибок).
            if "error" not in combined_data:
                self._keys_cache[keys_filter] = (time.monotonic(), combined_data)
            return combined_data

    async def _fetch_all_keys(self, keys_filter: str = "all") -> dict:
        """Фактически опросить API и собрать список ключей.

        keys_filter:
            'doors'  — only doors (no passes)
            'passes' — only passes
            'all'    — everything (doors + passes)

        Разные значения KeysType возвращают РАЗНЫЕ подмножества ключей, поэтому
        мы опрашиваем несколько типов параллельно и объединяем результаты.

        Дедупликация выполняется по doorId (а НЕ по key id): одна и та же
        физическая дверь может прийти под несколькими key id — как реальная
        дверь («Калитка 1») и как пропуск («Пропуск от ...») с тем же doorId.
        Поскольку сущности HA используют door_id в unique_id, дубли по doorId
        приводили к коллизиям unique_id и «пропаданию» дверей. При коллизии
        отдаём приоритет реальной двери над пропуском.
        """
        per_page = 100

        if keys_filter == "doors":
            key_types = KEY_TYPES_DOORS
        elif keys_filter == "passes":
            key_types = KEY_TYPES_PASSES
        else:
            key_types = KEY_TYPES_ALL

        _LOGGER.debug(
            "Getting keys (filter=%s) across key types %s in parallel",
            keys_filter,
            key_types,
        )
        results = await asyncio.gather(
            *(self._fetch_keys_by_type(key_type, per_page) for key_type in key_types)
        )

        all_keys = []
        for keys in results:
            all_keys.extend(keys)

        # Дедупликация по doorId с приоритетом реальных дверей над пропусками.
        # Ключи без doorId (если такие есть) дедуплицируются по key id.
        unique_keys: dict = {}
        for key in all_keys:
            door_id = key.get("doorId")
            dedup_key = ("door", door_id) if door_id else ("id", key.get("id"))
            if dedup_key[1] is None:
                continue

            existing = unique_keys.get(dedup_key)
            if existing is None:
                unique_keys[dedup_key] = key
                continue

            # Коллизия по doorId: реальная дверь важнее пропуска.
            if self.is_pass_key(existing) and not self.is_pass_key(key):
                _LOGGER.debug(
                    "doorId %s: заменяем пропуск '%s' реальной дверью '%s'",
                    door_id,
                    existing.get("name"),
                    key.get("name"),
                )
                unique_keys[dedup_key] = key

        all_keys = list(unique_keys.values())

        # Additional client-side filtering
        if keys_filter == "doors":
            all_keys = [k for k in all_keys if not self.is_pass_key(k)]
        elif keys_filter == "passes":
            all_keys = [k for k in all_keys if self.is_pass_key(k)]

        self._disambiguate_names(all_keys)

        combined_data = {
            "results": all_keys,
            "currentPage": 1,
            "pageCount": 1,
            "pageSize": len(all_keys),
            "rowCount": len(all_keys),
            "perPage": len(all_keys),
        }

        _LOGGER.info("Keys loaded: %d (filter=%s)", len(all_keys), keys_filter)
        _LOGGER.debug(
            "Keys loaded names (filter=%s): %s",
            keys_filter,
            [str(k.get("name")) for k in all_keys],
        )
        return combined_data

    @staticmethod
    def _disambiguate_names(keys: list) -> None:
        """Сделать отображаемые имена дверей ГАРАНТИРОВАННО уникальными (in-place).

        Разные двери (разный doorId) с одинаковым именем сбиваются в UI в один
        неразличимый пункт, из-за чего кнопки «путаются». Делаем имена
        уникальными в два шага:

        1. Для групп дублей по имени добавляем адрес (addressString) — так
           двери одного адреса группируются, а двери с разных адресов
           (двор/паркинг) визуально разделяются.
        2. Если после этого остаются одинаковые имена (несколько дверей с
           ОДНИМ именем И ОДНИМ адресом, напр. несколько «Тамбур-шлюз» по
           одному адресу), к каждому такому имени добавляем стабильный хвост
           doorId. Хвост детерминирован и не меняется между рестартами, поэтому
           entity_id остаются стабильными.

        unique_id сущностей (door_id) не затрагивается.
        """
        def base_name(key: dict) -> str:
            return (key.get("name") or "").strip()

        # Шаг 1: группировка дублей по имени → добавляем адрес.
        by_name: dict[str, list[int]] = {}
        for idx, key in enumerate(keys):
            by_name.setdefault(base_name(key), []).append(idx)

        for name, indices in by_name.items():
            if len(indices) < 2:
                continue  # имя уже уникально
            for idx in indices:
                key = keys[idx]
                address = (key.get("addressString") or "").strip()
                if address and address.lower() != name.lower():
                    key["name"] = f"{name} ({address})" if name else address

        counts: dict[str, int] = {}
        for key in keys:
            cur = key.get("name") or ""
            counts[cur] = counts.get(cur, 0) + 1

        for key in keys:
            cur = key.get("name") or ""
            if counts.get(cur, 0) > 1:
                door_id = str(key.get("doorId") or key.get("id") or "")
                tail = door_id[-6:] if door_id else "?"
                key["name"] = f"{cur} #{tail}" if cur else tail

    async def get_video_area(self):
        return await self._post(
            "/client-api/VideoCamera/GetVideoArea",
            need_auth=True,
            expect="json",
        )

    async def get_user_video_cameras(self, category: str):
        payload = {"category": category}
        return await self._post(
            "/client-api/VideoCamera/GetUserVideoCameras",
            payload,
            need_auth=True,
            expect="json",
        )

    async def get_user_key(self, key_id: str):
        payload = {"keyId": key_id}
        return await self._post("/client-api/Key/GetUserKey", payload, need_auth=True, expect="json")

    async def get_call_logs(
        self,
        per_page: int = 20,
        current_page: int = 1,
        missed_calls: bool = False,
    ):
        payload = {
            "currentPage": current_page,
            "perPage": per_page,
            "missedCalls": missed_calls,
        }
        return await self._post(
            "/client-api/CallLog/GetCallLogs",
            payload,
            need_auth=True,
            expect="json",
        )

    async def open_relay_by_door_id(self, door_id: str):
        payload = {"doorId": door_id}
        res = await self._post("/client-api/Device/OpenRelayByDoorId", payload, need_auth=True, expect="text")
        if isinstance(res, dict) and "error" in res:
            return res
        return {"ok": True, "body": res}

    async def open_relay_by_key_id(self, key_id: str):
        payload = {"keyId": key_id}
        res = await self._post("/client-api/Device/OpenRelayByKeyId", payload, need_auth=True, expect="text")
        if isinstance(res, dict) and "error" in res:
            return res
        return {"ok": True, "body": res}

    async def answer_call_notify(self, call_id: str):
        payload = {"callId": call_id}
        res = await self._post("/communication-api/Call/NotifyCallAnswered", payload, need_auth=True, expect="text")
        if isinstance(res, dict) and "error" in res:
            return res
        _LOGGER.debug("answer_call_notify(%s) -> %s", call_id, res)
        return {"ok": True, "body": res}

    async def fetch_external_bytes(
        self,
        url: str,
        *,
        authorized: bool = True,
        headers: Optional[Dict[str, str]] = None,
        retry_on_401: bool = True,
    ) -> Dict[str, Any]:
        if authorized:
            auth_error = await self._ensure_external_auth()
            if auth_error:
                return auth_error

        session = await self._ensure_external_session()
        first_try_access_token = self.access_token

        def _request():
            request_headers = dict(headers or {})
            if authorized:
                request_headers = self._authorized_external_headers(request_headers)
            return session.get(url, headers=request_headers)

        async def _handle_response(resp: aiohttp.ClientResponse) -> Dict[str, Any]:
            body = await resp.read()
            if 200 <= resp.status < 300:
                return {
                    "ok": True,
                    "status": resp.status,
                    "body": body,
                    "content_type": resp.headers.get("Content-Type"),
                }
            return {
                "ok": False,
                "error": f"HTTP {resp.status}",
                "status": resp.status,
                "body": body[:2000].decode("utf-8", "replace"),
            }

        try:
            async with _request() as resp:
                if (
                    resp.status == 401
                    and authorized
                    and retry_on_401
                    and self.refresh_token
                ):
                    _LOGGER.warning(
                        "401 Unauthorized, refreshing token and retrying external GET %s",
                        url,
                    )
                    if await self._refresh_for_retry(first_try_access_token):
                        async with _request() as retry_resp:
                            return await _handle_response(retry_resp)

                return await _handle_response(resp)
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.error("External GET failed: %s -> %s", url, err)
            return {"ok": False, "error": str(err), "body": ""}

    def _authorized_external_headers(self, headers: Dict[str, str]) -> Dict[str, str]:
        request_headers = dict(headers)
        if self.access_token:
            request_headers["Authorization"] = f"Bearer {self.access_token}"
        return request_headers

    async def _ensure_external_auth(self) -> Optional[Dict[str, Any]]:
        if not self.access_token:
            return {"ok": False, "error": "No access token available", "body": ""}
        await self._ensure_alive()
        if not self.access_token:
            return self._refresh_unavailable_error("Session expired")
        return None

    async def create_whep_session(self, whep_url: str, offer_sdp: str) -> Dict[str, Any]:
        auth_error = await self._ensure_external_auth()
        if auth_error:
            return auth_error

        session = await self._ensure_external_session()
        first_try_access_token = self.access_token

        def _request():
            return session.post(
                whep_url,
                data=offer_sdp,
                headers=self._authorized_external_headers(
                    {
                        "Content-Type": "application/sdp",
                        "Accept": "application/sdp",
                    }
                ),
            )

        async def _handle_response(resp: aiohttp.ClientResponse) -> Dict[str, Any]:
            answer_sdp = await resp.text()
            if resp.status != 201:
                return {
                    "ok": False,
                    "error": f"HTTP {resp.status}",
                    "status": resp.status,
                    "body": answer_sdp[:2000],
                }

            location = resp.headers.get("Location")
            if not location:
                return {
                    "ok": False,
                    "error": "WHEP response did not include a session URL",
                    "status": resp.status,
                    "body": answer_sdp[:2000],
                }

            return {
                "ok": True,
                "status": resp.status,
                "answer_sdp": answer_sdp,
                "location": location,
            }

        try:
            async with _request() as resp:
                if resp.status == 401 and self.refresh_token:
                    _LOGGER.warning("401 Unauthorized, refreshing token and retrying WHEP offer %s", whep_url)
                    if await self._refresh_for_retry(first_try_access_token):
                        async with _request() as retry_resp:
                            return await _handle_response(retry_resp)

                return await _handle_response(resp)
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.error("WHEP offer failed: %s -> %s", whep_url, err)
            return {"ok": False, "error": str(err), "body": ""}

    async def send_whep_candidates(self, session_url: str, sdp_fragment: str) -> Dict[str, Any]:
        auth_error = await self._ensure_external_auth()
        if auth_error:
            return auth_error

        session = await self._ensure_external_session()
        first_try_access_token = self.access_token

        def _request():
            return session.patch(
                session_url,
                data=sdp_fragment,
                headers=self._authorized_external_headers(
                    {
                        "Content-Type": "application/trickle-ice-sdpfrag",
                        "If-Match": "*",
                    }
                ),
            )

        async def _handle_response(resp: aiohttp.ClientResponse) -> Dict[str, Any]:
            if resp.status in (200, 204):
                return {"ok": True, "status": resp.status}

            body = await resp.text()
            return {
                "ok": False,
                "error": f"HTTP {resp.status}",
                "status": resp.status,
                "body": body[:2000],
            }

        try:
            async with _request() as resp:
                if resp.status == 401 and self.refresh_token:
                    _LOGGER.warning("401 Unauthorized, refreshing token and retrying WHEP candidate %s", session_url)
                    if await self._refresh_for_retry(first_try_access_token):
                        async with _request() as retry_resp:
                            return await _handle_response(retry_resp)

                return await _handle_response(resp)
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.error("WHEP candidate failed: %s -> %s", session_url, err)
            return {"ok": False, "error": str(err), "body": ""}

    async def close_whep_session(self, session_url: str) -> Dict[str, Any]:
        auth_error = await self._ensure_external_auth()
        if auth_error:
            return auth_error

        session = await self._ensure_external_session()
        first_try_access_token = self.access_token

        def _request():
            return session.delete(
                session_url,
                headers=self._authorized_external_headers({}),
            )

        async def _handle_response(resp: aiohttp.ClientResponse) -> Dict[str, Any]:
            if resp.status in (200, 204):
                return {"ok": True, "status": resp.status}
            return {
                "ok": False,
                "error": f"HTTP {resp.status}",
                "status": resp.status,
                "body": await resp.text(),
            }

        try:
            async with _request() as resp:
                if resp.status == 401 and self.refresh_token:
                    _LOGGER.warning("401 Unauthorized, refreshing token and retrying WHEP close %s", session_url)
                    if await self._refresh_for_retry(first_try_access_token):
                        async with _request() as retry_resp:
                            return await _handle_response(retry_resp)

                return await _handle_response(resp)
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.debug("WHEP session close failed: %s -> %s", session_url, err)
            return {"ok": False, "error": str(err), "body": ""}

    async def end_call_notify(self, call_id: str):
        payload = {"callId": call_id}
        res = await self._post("/communication-api/Call/NotifyCallEnded", payload, need_auth=True, expect="text")
        if isinstance(res, dict) and "error" in res:
            return res
        _LOGGER.debug("end_call_notify(%s) -> %s", call_id, res)
        return {"ok": True, "body": res}

    async def get_notify_id_token(self) -> Optional[str]:
        res = await self._post(
            "/notificationHub/negotiate?negotiateVersion=1",
            need_auth=True,
            expect="json",
            header_set=self.signalr_headers(),
        )
        if not isinstance(res, dict) or ("error" in res and "status" in res):
            _LOGGER.debug("negotiate failed: %s", res)
            return None
        _LOGGER.debug("negotiate response: %s", res)
        token = res.get("connectionToken")
        _LOGGER.debug("get_notify_id_token -> %s", token)
        return token

    def get_notify_cookie_header(self) -> Optional[str]:
        """Вернуть Cookie-заголовок с affinity-куками, выставленными сервером
        на этапе negotiate.

        notificationHub (SignalR) работает за балансировщиком со «липкой»
        сессией: negotiate и последующий WebSocket-апгрейд ОБЯЗАНЫ попасть на
        один и тот же backend, иначе connectionToken невалиден и сервер
        отвечает 404. Сервер привязывает запросы cookie
        'domonap-api-communication-affinity'. Negotiate выполняется на
        API-сессии, а WS открывается на отдельной сессии HA, поэтому cookie не
        передаётся автоматически — переносим её вручную.
        """
        session = self._session
        if session is None:
            return None
        try:
            from yarl import URL

            cookies = session.cookie_jar.filter_cookies(URL(self.base_url))
        except Exception:
            _LOGGER.debug("Cannot read notify affinity cookies", exc_info=True)
            return None
        if not cookies:
            return None
        return "; ".join(f"{name}={morsel.value}" for name, morsel in cookies.items())
