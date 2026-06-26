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

## Сервисы

| Сервис | Описание |
|--------|----------|
| `domonap.open_relay_by_door_id` | Открыть дверь по `doorId` |
| `domonap.open_relay_by_key_id` | Открыть дверь по `keyId` |
| `domonap.open_relay_by_last_call_door_id` | Открыть дверь по DoorId из сенсора последнего звонка |

---

## Ограничения

Существует ограничение на одновременное использование одного номера телефона в приложении DomoNAP и интеграции HA. На мобильное устройство с официальным приложением DomoNAP перестанут приходить push-уведомления о входящем звонке в режиме, когда приложение не находится на открытом экране.

---

## Установка

### Через HACS
1. В HACS добавьте пользовательский репозиторий: `https://github.com/troy9221/DomoNAP_HA`
2. Найдите и установите интеграцию **DomoNAP**
3. Перезапустите сервер Home Assistant
4. Добавьте интеграцию в разделе **Настройки → Устройства и службы**
5. Авторизация выполняется по номеру телефона, привязанному к приложению при регистрации

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
