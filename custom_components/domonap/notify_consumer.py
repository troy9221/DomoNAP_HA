import json
import logging
import asyncio
import aiohttp
from random import uniform
from typing import Callable, Optional, Any, Union
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .api import IntercomAPI, is_api_error
from .const import (
    EVENT_INCOMING_CALL,
    EVENT_CALL_ENDED,
    EVENT_RECEIVE_MESSAGE,
    EVENT_USER_STATUS_CHANGED,
    WS_HANDSHAKE_MESSAGE,
    WS_KEEPALIVE_INTERVAL,
    WS_PING_MESSAGE,
    WS_SERVER_TIMEOUT,
    WS_URL,
    split_signalr_records,
)

_LOGGER = logging.getLogger(__name__)


class IntercomNotifyConsumer:
    def __init__(
        self,
        hass: HomeAssistant,
        api: IntercomAPI,
        media_proxy=None,
        media_proxy_secret: Optional[str] = None,
    ) -> None:
        self._hass = hass
        self._api = api
        self._media_proxy = media_proxy
        self._media_proxy_secret = media_proxy_secret
        self._callbacks: set[Callable[[], Union[None, Any]]] = set()
        self._notify_id_token: Optional[str] = None
        self._connected: bool = False
        self._username: str = ""
        self._reconnect_delay: int = 1
        self._max_reconnect: int = 10
        self._stop_event = asyncio.Event()
        # Map CallId -> DoorId to resolve DomofonCallEnded (which lacks DoorId).
        self._active_calls: dict[str, str] = {}
        self._session = async_get_clientsession(hass)
        self._headers = self._build_ws_headers()
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        existing_callback = getattr(self._api, "token_update_callback", None)

        def _on_token_update(
            access: Optional[str],
            refresh: Optional[str],
            exp: Optional[str],
        ) -> None:
            self._headers["Authorization"] = f"Bearer {access or ''}"
            if callable(existing_callback):
                existing_callback(access, refresh, exp)

        self._api.token_update_callback = _on_token_update

    async def start(self) -> None:
        self._stop_event.clear()
        while not self._stop_event.is_set():
            try:
                await self._connect_and_run()
            except asyncio.CancelledError:
                raise
            except aiohttp.WSServerHandshakeError as e:
                if e.status == 401:
                    _LOGGER.error("WS 401 Unauthorized: %s", e.headers.get("WWW-Authenticate"))
                elif e.status == 404:
                    # Расширенная диагностика: печатаем URL запроса, сообщение и
                    # заголовки ответа сервера, чтобы понять, почему хаб не найден.
                    req = getattr(e, "request_info", None)
                    _LOGGER.warning(
                        "WS 404 Not found. request_url=%s message=%s response_headers=%s",
                        getattr(req, "real_url", None) or getattr(req, "url", None),
                        getattr(e, "message", None),
                        dict(e.headers) if e.headers else None,
                    )
                else:
                    _LOGGER.warning(
                        "WS handshake error: status=%s message=%s headers=%s",
                        getattr(e, "status", None),
                        getattr(e, "message", None),
                        dict(e.headers) if getattr(e, "headers", None) else None,
                    )
            except (asyncio.TimeoutError, aiohttp.ServerTimeoutError):
                _LOGGER.debug("WS server timeout, reconnecting")
            except Exception as e:
                _LOGGER.debug("Notify loop error: %s", e)
            if self._stop_event.is_set():
                break
            delay = min(self._reconnect_delay, self._max_reconnect)
            await asyncio.sleep(delay + uniform(0, 0.5))
            self._reconnect_delay = min(self._reconnect_delay * 2, self._max_reconnect)

    async def stop(self) -> None:
        self._stop_event.set()
        if self._ws is not None and not self._ws.closed:
            try:
                await self._ws.close()
            except Exception:
                pass

    def register_callback(self, callback: Callable[[], Any]) -> None:
        self._callbacks.add(callback)

    def remove_callback(self, callback: Callable[[], Any]) -> None:
        self._callbacks.discard(callback)

    @property
    def connected(self) -> bool:
        return self._connected

    def _build_ws_headers(self) -> dict[str, str]:
        get_signalr_headers = getattr(self._api, "signalr_headers", None)
        if callable(get_signalr_headers):
            headers = dict(get_signalr_headers())
        else:
            headers = {}
        headers["Authorization"] = f"Bearer {self._api.access_token or ''}"
        return headers

    async def _connect_and_run(self) -> None:
        self._notify_id_token = await self._api.get_notify_id_token()
        _LOGGER.debug("Negotiated connectionToken: %s", self._notify_id_token)
        if not self._notify_id_token:
            raise RuntimeError("Negotiation failed: empty connectionToken")
        ws_url = WS_URL + self._notify_id_token
        self._headers = self._build_ws_headers()
        # SignalR за балансировщиком со «липкой» сессией: negotiate и WS-апгрейд
        # должны попасть на один backend. Сервер помечает запросы cookie
        # 'domonap-api-communication-affinity'. Negotiate идёт на API-сессии, а
        # WS открываем на сессии HA, поэтому переносим affinity-cookie вручную —
        # иначе WS уходит на другой backend и отвечает 404.
        affinity_cookie = None
        get_cookie = getattr(self._api, "get_notify_cookie_header", None)
        if callable(get_cookie):
            affinity_cookie = get_cookie()
        if affinity_cookie:
            self._headers["Cookie"] = affinity_cookie
        _LOGGER.debug(
            "WS connecting: url=%s headers=%s",
            ws_url,
            {
                k: (v[:12] + "…(masked)" if k.lower() == "authorization" and v else v)
                for k, v in self._headers.items()
            },
        )
        ping_task: Optional[asyncio.Task] = None
        try:
            async with self._session.ws_connect(
                ws_url,
                headers=self._headers,
                receive_timeout=WS_SERVER_TIMEOUT,
            ) as ws:
                self._ws = ws
                _LOGGER.debug("WS connected")
                self._connected = True
                self._reconnect_delay = 1
                self._username = await self._api.get_username() or ""
                await ws.send_str(WS_HANDSHAKE_MESSAGE)
                ping_task = asyncio.ensure_future(self._keepalive(ws))
                try:
                    async for msg in ws:
                        if self._stop_event.is_set():
                            break
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            await self._handle_text(msg.data, ws)
                            if self._callbacks:
                                await self._publish_updates()
                        elif msg.type == aiohttp.WSMsgType.PING:
                            await ws.pong()
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            _LOGGER.debug("WS closed/error: %s", msg.data)
                            break
                finally:
                    if ping_task is not None:
                        ping_task.cancel()
        except (asyncio.TimeoutError, aiohttp.ServerTimeoutError):
            _LOGGER.debug("WS server timeout, reconnecting")
        finally:
            self._connected = False
            self._username = ""
            self._ws = None
            _LOGGER.debug("WS disconnected")

    async def _keepalive(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """Периодически шлёт SignalR ping (`{"type":6}`).

        Без этого сервер разрывает соединение по ClientTimeoutInterval, когда
        нет входящих звонков/сообщений, и уведомления перестают приходить.
        """
        try:
            while not ws.closed and not self._stop_event.is_set():
                await asyncio.sleep(WS_KEEPALIVE_INTERVAL)
                if ws.closed or self._stop_event.is_set():
                    break
                try:
                    await ws.send_str(WS_PING_MESSAGE)
                except Exception:
                    break
        except asyncio.CancelledError:
            pass

    async def _handle_text(self, raw: str, ws: aiohttp.ClientWebSocketResponse) -> None:
        for record in split_signalr_records(raw):
            await self._handle_record(record, ws)

    async def _handle_record(self, payload: str, ws: aiohttp.ClientWebSocketResponse) -> None:
        if payload == "{}":
            _LOGGER.debug("Handshake ack")
            return
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            _LOGGER.debug("Non-JSON frame: %s", payload[:200])
            return
        t = data.get("type")
        if t == 1:
            await self._handle_invocation(data, ws)
        elif t == 6:
            # Серверный ping. Клиент Microsoft SignalR его не эхоит — только
            # сбрасывает serverTimeout и шлёт собственные ping по таймеру.
            _LOGGER.debug("Server ping")
        elif t == 3:
            _LOGGER.debug("Completion frame: %s", data)
        else:
            _LOGGER.debug("Unknown frame type=%s data=%s", t, payload[:200])

    async def _handle_invocation(self, data: dict, ws: aiohttp.ClientWebSocketResponse) -> None:
        target = data.get("target")
        raw_args = data.get("arguments") or []
        args = list(raw_args) if isinstance(raw_args, (list, tuple)) else []
        if target == "ReceivePush":
            push_data = args[2] if len(args) >= 3 else None
            if isinstance(push_data, dict):
                evt = push_data.get("EventMessage")
                if evt == "DomofonCalling":
                    await self._prepare_incoming_call_event(push_data)
                    call_id = str(push_data.get("CallId", ""))
                    door_id = push_data.get("DoorId")
                    if call_id and door_id:
                        self._active_calls[call_id] = door_id
                    self._hass.bus.fire(EVENT_INCOMING_CALL, push_data)
                    _LOGGER.debug("Incoming call: %s", push_data)
                elif evt == "DomofonCallEnded":
                    self._handle_call_ended(push_data)
                else:
                    _LOGGER.debug("Unknown EventMessage=%s push=%s", evt, str(push_data)[:200])
        elif target in ("ReceiveOnline", "ReceiveOffline"):
            user = args[0] if args else None
            status = str(target).replace("ReceiveO", "o")

            _LOGGER.debug("User %s is %s", user, status)

            self._hass.bus.fire(EVENT_USER_STATUS_CHANGED, {
                "user": user,
                "status": status,
            })
            if user == self._username and status == "offline":
                _LOGGER.debug(
                    "Current login user: %s status changed to %s. Reconnecting websocket...",
                    user,
                    status,
                )
                if not ws.closed:
                    await ws.close()

        elif target == "ReceiveMessage":
            chat_data = args[0] if args else None
            if not isinstance(chat_data, dict):
                _LOGGER.debug("ReceiveMessage without payload: %s", data)
                return
            self._hass.bus.fire(EVENT_RECEIVE_MESSAGE, chat_data)
            _LOGGER.debug(
                "Received message from %s: %s",
                chat_data.get("sender"),
                chat_data.get("text"),
            )
        elif target == "ReceiveRead":
            _LOGGER.debug("Read confirm messages in channel %s", args[0] if args else None)
        else:
            _LOGGER.debug("Unknown target type %s message:\n%s", data.get("target"), data)

    def _handle_call_ended(self, push_data: dict) -> None:
        """Handle DomofonCallEnded: fire EVENT_CALL_ENDED with resolved DoorId."""
        call_id = str(push_data.get("CallId", ""))
        door_id = push_data.get("DoorId") or self._active_calls.pop(call_id, None)
        event_data = dict(push_data)
        if door_id:
            event_data["DoorId"] = door_id
        self._hass.bus.fire(EVENT_CALL_ENDED, event_data)
        _LOGGER.debug("Call ended: call_id=%s door_id=%s", call_id, door_id)

    async def _prepare_incoming_call_event(self, push_data: dict) -> None:
        call_id = str(push_data.get("CallId", ""))
        video_preview = push_data.get("VideoPreview") or push_data.get("videoPreview")
        proxied_video_preview = self._proxied_media_url(video_preview)
        if video_preview:
            push_data.setdefault("OriginalVideoPreview", video_preview)
            push_data["VideoPreview"] = proxied_video_preview or video_preview
            push_data["videoPreview"] = proxied_video_preview or video_preview

        push_photo_url = push_data.get("PhotoUrl") or push_data.get("photoUrl")
        if push_photo_url:
            push_data.setdefault("PushPhotoUrl", push_photo_url)

        photo_url = await self._get_call_log_photo_url(call_id)

        proxied_photo_url = self._proxied_media_url(
            photo_url,
            fallback_url=video_preview,
            authorized=False,
        )
        if photo_url:
            push_data.setdefault("OriginalPhotoUrl", photo_url)
            push_data["PhotoUrl"] = proxied_photo_url or photo_url
            push_data["photoUrl"] = proxied_photo_url or photo_url
        elif video_preview:
            push_data["PhotoUrl"] = proxied_video_preview or video_preview
            push_data["photoUrl"] = proxied_video_preview or video_preview

    async def _get_call_log_photo_url(self, call_id: str) -> Optional[str]:
        if not call_id:
            return None

        for attempt in range(3):
            if attempt:
                await asyncio.sleep(1)

            try:
                response = await self._api.get_call_logs(per_page=20, current_page=1)
            except Exception:
                _LOGGER.debug("Failed to load Domonap call logs", exc_info=True)
                return None
            if not isinstance(response, dict):
                _LOGGER.debug(
                    "Unexpected Domonap call logs payload: %s",
                    type(response).__name__,
                )
                return None
            if is_api_error(response):
                _LOGGER.debug("Failed to load Domonap call logs: %s", response)
                return None

            call_logs = response.get("results", [])
            if not isinstance(call_logs, list):
                _LOGGER.debug("Unexpected Domonap call logs results: %s", call_logs)
                return None

            for call_log in call_logs:
                if not isinstance(call_log, dict):
                    continue
                if str(call_log.get("callId", "")) != call_id:
                    continue
                photo_url = call_log.get("photoUrl")
                if photo_url:
                    return photo_url
                return None

        _LOGGER.debug("Call log photoUrl not found for call %s", call_id)
        return None

    def _proxied_media_url(
        self,
        url: Optional[str],
        *,
        fallback_url: Optional[str] = None,
        authorized: bool = True,
        fallback_authorized: bool = True,
    ) -> Optional[str]:
        if not url or not self._media_proxy or not self._media_proxy_secret:
            return None
        try:
            return self._media_proxy.register_url(
                self._media_proxy_secret,
                self._api,
                url,
                fallback_url=fallback_url,
                authorized=authorized,
                fallback_authorized=fallback_authorized,
            )
        except Exception:
            _LOGGER.debug("Failed to register Domonap media proxy URL", exc_info=True)
            return None

    async def _publish_updates(self) -> None:
        for cb in list(self._callbacks):
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb()
                else:
                    cb()
            except Exception as e:
                _LOGGER.debug("Callback error: %s", e)
