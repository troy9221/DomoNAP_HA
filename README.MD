# DomoNAP Home Assistant Integration

![Logo](img/icon.png)

Интеграция позволяет управлять устройствами, привязанными к приложению **DomoNAP** (альтернативный провайдер домофона в домах ПИК) из **Home Assistant**.

> **Разработчик оригинальной интеграции:** [svmironov/domonap_intercom](https://github.com/svmironov/domonap_intercom)  
> **Данный репозиторий** — форк с дополнительными функциями и исправлениями: [troy9221/DomoNAP_HA](https://github.com/troy9221/DomoNAP_HA)

---

## Возможности

### Управление дверями
- 🔘 **Кнопки открытия дверей** — отдельная кнопка для каждого ключа (двери + пропуска)
- 🔓 **Открытие по последнему звонку** — кнопка для быстрого открытия двери, с которой пришёл звонок
- 🔧 **Сервисы** — `open_relay_by_door_id`, `open_relay_by_key_id`, `open_relay_by_last_call_door_id`

### Все ключи и адреса
- 🗝️ **Загрузка всех ключей** — интеграция запрашивает ключи всех типов (двери + пропуска) с полной пагинацией, а не только первую страницу основных ключей
- 🏠 **Адреса ключей** — каждый ключ отображает `addressString` в атрибутах сущности
- 📍 **Автоматическое определение зоны** — адрес ключа автоматически устанавливается как `suggested_area` устройства в Home Assistant, что позволяет группировать устройства по адресам

### Видеопоток и камеры
- 📹 **HLS-стриминг** — загрузка видеопотока с камер домофона
- 🌐 **Локальный WebRTC/WHEP proxy** для `go2rtc` без авторизации DomoNAP
- 📸 **Фото звонка** — сущность `image` с автоматическим обновлением при входящем звонке
- 🎥 **Камеры из вкладки «Видео»** — загрузка камер из разных категорий (Parking, House, Entrance)

### Уведомления и события
- 🔔 **Бинарный сенсор звонка** — `binary_sensor` для каждой двери с камерой, включается при входящем звонке
- 📡 **Событие `domonap_incoming_call`** — полные данные о звонке для автоматизаций
- 💬 **Событие `domonap_receive_message`** — входящие сообщения
- 👤 **Событие `domonap_user_status_changed`** — изменение статуса пользователя
- 🔢 **Сенсор Door ID последнего звонка** — хранит `DoorId` и полные данные последнего звонка
- 🔑 **Сенсор кода двери** — отображает `domofonPublicPin` для дверей с PIN-кодом

---

## Сущности интеграции

| Тип | Имя | Описание |
|-----|-----|----------|
| `button` | Открыть дверь | Кнопка для каждого ключа |
| `button` | Открыть дверь с которой произошел последний звонок | Кнопка для открытия по DoorId из сенсора |
| `binary_sensor` | Входящий звонок | Для каждой двери с камерой |
| `sensor` | Код двери | PIN-код домофона (`domofonPublicPin`) |
| `sensor` | Door ID последнего звонка | Хранит DoorId + данные звонка |
| `sensor` | Последнее сообщение чата | Текст и атрибуты последнего сообщения из чата / поддержки |
| `camera` | Камера | HLS или WebRTC камера для каждой двери |
| `image` | Фото звонка | Обновляемое фото при входящем звонке |

Каждая сущность содержит атрибут `addressString` с адресом ключа и все данные ключа из API.

---

## WebRTC proxy для go2rtc

Интеграция поднимает локальный WHEP proxy в Home Assistant. Он сам обновляет токен и проксирует WebRTC-сессию в DomoNAP, поэтому `go2rtc` может подключаться к HA URL без авторизации в DomoNAP.

У WebRTC-камеры появляются атрибуты:
- `go2rtc_webrtc_path` — относительный путь proxy внутри Home Assistant
- `go2rtc_webrtc_url` — полный URL для подключения, если в HA настроен внутренний или внешний URL

Пример `go2rtc`:
```yaml
streams:
  domonap_entrance:
    - webrtc:http://HOME_ASSISTANT:8123/api/domonap/webrtc_proxy/<secret>/<camera_id>/whep
```

Практически удобнее брать готовое значение из атрибута `go2rtc_webrtc_url` у сущности камеры и вставлять его в конфиг `go2rtc`.

---

## Автоматизации

### 1. Событие входящего звонка `domonap_incoming_call`

```yaml
event_type: domonap_incoming_call
data:
  EventMessage: DomofonCalling
  DoorId: 8452d508564e5a076c8122b6
  Address: Лифтовой холл
  CallId: "154543486.54786447"
  VideoUrl: https://hls.domonap.ru/8452d508564e5a076c8122b6/index.m3u8
  HttpVideoUrl: https://hls.domonap.ru/8452d508564e5a076c8122b6/index.m3u8
  VideoPreview: >-
    https://api.domonap.ru/video-api/preview/Device/8452d508564e5a076c8122b6/qQtfsMiQYS0YuD8LLKrDGbPijncAShubrISKMs7E1cw
  PhotoUrl: >-
    https://home-assistant.local/api/domonap/media_proxy/...
  OriginalPhotoUrl: >-
    https://s3-api.domonap.ru/...
origin: LOCAL
time_fired: "2025-06-18T15:10:58.919425+00:00"
```

**Поля данных:**
- `DoorId` — идентификатор двери
- `Address` — адрес вызывающего устройства
- `PhotoUrl` / `photoUrl` — URL фотографии звонящего через локальный proxy Home Assistant. Интеграция получает исходный `photoUrl` так же, как приложение DomoNAP: из истории звонков `client-api/CallLog/GetCallLogs`. Если запись звонка или фото ещё недоступны, используется авторизованный `VideoPreview`
- `OriginalPhotoUrl` — оригинальный `photoUrl` из истории звонков DomoNAP для диагностики
- `PushPhotoUrl` — исходный `photoUrl` из push-события, если DomoNAP его прислал; хранится только для диагностики
- `VideoPreview` — URL превью через локальный proxy Home Assistant с авторизацией в DomoNAP

### 2. Событие входящего сообщения `domonap_receive_message`

```yaml
event_type: domonap_receive_message
data:
  id: 8452d508564e5a076c8122b6
  channel: username1-username
  text: "Текст сообщения"
  sender: username1
  name: Username
  chatType: Private
  createdOn: "2025-06-18T15:02:32.1264693Z"
  isRead: false
origin: LOCAL
time_fired: "2025-06-18T15:02:32.208782+00:00"
```

### 3. Событие изменения статуса `domonap_user_status_changed`

```yaml
event_type: domonap_user_status_changed
data:
  user: username1
  status: offline
origin: LOCAL
time_fired: "2025-06-18T15:07:23.395167+00:00"
```

### Примеры автоматизаций

**Push-уведомление мобильного приложения Home Assistant:**
```yaml
automation:
  - alias: "Уведомление о звонке"
    trigger:
      platform: event
      event_type: domonap_incoming_call
    action:
      - service: notify.mobile_app
        data:
          message: "Входящий звонок на домофон"
          data:
            image: "{{ trigger.event.data.PhotoUrl }}"
```

**Уведомление в Telegram с фото и кнопкой открытия:**
```yaml
automation:
  - alias: "Звонок из домофона в Telegram"
    triggers:
      - trigger: event
        event_type: domonap_incoming_call
    actions:
      - action: telegram_bot.send_photo
        data:
          caption: |-
            📞📲 Кто-то звонит в домофон
            📌 {{ trigger.event.data.Address }} 
          url: "{{ trigger.event.data.PhotoUrl }}"
          inline_keyboard:
            - "🔓 Открыть:/open_{{ trigger.event.data.DoorId }}"
```

---

## Звонок на телефон: «и телефон, и HA» одновременно

Сервер DomoNAP шлёт **push о входящем звонке** только на один — последний зарегистрированный — `deviceToken`. Двери, камеры, пропуска и раздел **«Поддержка»** в приложении от этого **не зависят**: они ходят в API по токену авторизации и работают параллельно с Home Assistant.

Полностью «звонок сразу в приложение DomoNAP **и** в сторонний бот/HA как в два независимых push-клиента» сервер не умеет. Обход такой:

### Вариант A (рекомендуется): HA ловит звонок, телефон получает его через Companion / Telegram

1. Оставьте опцию **«Перехватывать входящие звонки»** включённой (по умолчанию).
2. Home Assistant держит WebSocket и генерирует `domonap_incoming_call`.
3. Автоматизация сразу шлёт push в **Home Assistant Companion** или фото в **Telegram-бот Home Assistant** (это уже *ваш* бот, не отдельный вход в DomoNAP).
4. Отдельный Telegram-бот, который логинится в DomoNAP тем же номером, снова перехватит `deviceToken` — его запускать не нужно.

Готовые примеры — в [`examples/`](examples).

### Вариант B: звонок остаётся в приложении DomoNAP, HA слушает параллельно

1. **Настройки → Устройства и службы → Domonap → Настроить** → выключите «Перехватывать входящие звонки».
2. Один раз откройте официальное приложение DomoNAP — оно вернёт себе `deviceToken`.
3. Home Assistant **больше не вызывает** `UpdateDeviceToken` при старте, поэтому не отбирает звонок у телефона.
4. Вызов в HA по-прежнему приходит по WebSocket (интеграция всегда онлайн), сущности и автоматизации работают.

Если после этого звонок в HA пропал, пока открыто приложение — сервер иногда считает активной только одну SignalR-сессию. Тогда используйте вариант A.

### Приоритет уведомления на iOS (звук в беззвучном режиме)

За «громкость» на iPhone отвечает блок `push → interruption-level`:
- `time-sensitive` — пробивает режим «Не беспокоить», не требует дополнительных разрешений;
- `critical` — звучит громко даже в беззвучном режиме и «Не беспокоить», но требует включить **«Critical Notifications»** в приложении Home Assistant (Настройки → Уведомления).

Ключи `channel` / `importance` / `ttl` / `priority` относятся к Android и на iOS игнорируются.

### Фото звонящего и `external_url`

Фото (`PhotoUrl`) отдаётся локальным `media_proxy` и подставляет адрес из настроек Home Assistant. Если `external_url` **не** задан, в ссылку попадает внутренний адрес (например `http://192.168.x.x:8123/...`), недоступный с телефона в сотовой сети — iOS не сможет скачать картинку, и уведомление может прийти с задержкой или без фото.

Решения (любое из двух):
- **Рекомендуется** — задать внешний адрес в `configuration.yaml`, тогда `media_proxy` сам отдаст рабочую ссылку:
  ```yaml
  homeassistant:
    external_url: "https://home-assistant.example.com"
    internal_url: "http://YOUR_INTERNAL_IP:8123"
  ```
- **Быстрый обходной путь** — подменить внутренний адрес на внешний прямо в автоматизации:
  ```yaml
  image: >-
    {{ trigger.event.data.PhotoUrl | replace('YOUR_INTERNAL_IP', 'YOUR_EXTERNAL_IP') }}
  ```

---

## Гео-открытие дверей по входу в зону

Двери и калитки можно открывать автоматически при входе в геозону (например, подходя к калитке). Триггер `zone` по `device_tracker` вашего телефона вызывает `domonap.open_relay_by_door_id`:

```yaml
- id: domonap_zone_gate_1
  alias: "Гео: подход к калитке → открыть"
  triggers:
    - trigger: zone
      entity_id: device_tracker.your_phone
      zone: zone.gate_1
      event: enter
  actions:
    - action: domonap.open_relay_by_door_id
      data:
        door_id: "YOUR_DOOR_ID"
  mode: single
```

Полный набор примеров (6 зон, современный синтаксис `triggers/actions`) — в [`examples/automations_complete.yaml`](examples/automations_complete.yaml).

---

## Дашборд «Домофон»

В комплекте — готовый YAML-дашборд с камерами домофонов, кнопками открытия и виджетом «идёт звонок» (карточка появляется только во время вызова):
- [`examples/dashboards/domofon.yaml`](examples/dashboards/domofon.yaml) — сам дашборд (камеры сверху с кнопкой «Открыть», калитки/лестницы/въезды — списком снизу);
- [`examples/configuration_lovelace_snippet.yaml`](examples/configuration_lovelace_snippet.yaml) — подключение YAML-дашборда в `configuration.yaml`;
- [`examples/lovelace_call_widget.yaml`](examples/lovelace_call_widget.yaml) — отдельный виджет «идёт звонок» для добавления на любой дашборд.

Дашборд подключается в режиме `mode: yaml` и доступен по URL `/domofon-dashboard`. `entity_id` и `door_id` в примерах — заглушки; замените их на свои (получить можно в **Инструменты разработчика → Шаблоны** и из логов интеграции).

---

## Сервисы

| Сервис | Описание |
|--------|----------|
| `domonap.open_relay_by_door_id` | Открыть дверь по `doorId` |
| `domonap.open_relay_by_key_id` | Открыть дверь по `keyId` |
| `domonap.open_relay_by_last_call_door_id` | Открыть дверь по DoorId из сенсора последнего звонка |

---

## Ограничения

Сервер DomoNAP маршрутизирует **push входящего звонка** только на один последний `deviceToken`. Интеграция по умолчанию регистрирует свой токен, и официальное приложение в фоне перестаёт звонить. Это не блокирует открытие дверей, камеры и чат поддержки в приложении.

Обход — см. раздел «Звонок на телефон». Отдельный Telegram-бот с логином в DomoNAP на тот же номер даёт тот же конфликт; рассылку в Telegram делайте из Home Assistant.

Раздел **«Поддержка»** в приложении — чат с операторами УК/DomoNAP. Отвечать из Home Assistant нельзя (нет публичного API отправки в этот канал). Входящие сообщения чата/поддержки приходят событием `domonap_receive_message` и в сенсор «Последнее сообщение чата»; ответить можно в приложении DomoNAP.

---

## Установка

### Через HACS
1. В HACS добавьте пользовательский репозиторий: `https://github.com/troy9221/DomoNAP_HA`
2. Найдите и установите интеграцию **DomoNAP**
3. Перезапустите сервер Home Assistant
4. Добавьте интеграцию в разделе **Настройки → Устройства и службы**
5. Авторизация выполняется по номеру телефона, привязанному к приложению при регистрации

После выхода новой версии HACS и Home Assistant показывают обновление в **Настройки → Система → Обновления**. Для этого в репозитории публикуется GitHub Release с номером из `manifest.json` (тега недостаточно).

### Вручную
1. Склонируйте репозиторий в папку `custom_components/domonap`:
   ```bash
   cd config/custom_components
   git clone https://github.com/troy9221/DomoNAP_HA.git domonap
   ```
2. Перезапустите сервер Home Assistant
3. Добавьте интеграцию в разделе **Настройки → Устройства и службы**

---

## Благодарности

- **[svmironov](https://github.com/svmironov/domonap_intercom)** — разработчик оригинальной интеграции DomoNAP для Home Assistant
- **[krassalexs](https://github.com/krassalexs)** — соавтор оригинальной интеграции

---

## Отказ от ответственности

Данное программное обеспечение никак не связано и не одобрено ООО «ДОМОНАП», владельцем торговой марки ДОМОНАП. Используйте его на свой страх и риск. Автор ни при каких обстоятельствах не несёт ответственности за повреждение или утрату вашего имущества, а также за возможный вред третьим лицам.

Все логотипы, названия брендов и продуктов принадлежат их законным владельцам.
