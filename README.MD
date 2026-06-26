# Domonap Home Assistant Integration
Код взял у - https://github.com/svmironov/domonap_intercom 
![Logo](https://github.com/svmironov/domonap_intercom/blob/main/img/icon.png?raw=true)

Интеграция позволяет управлять устройствами, привязанными к приложению **Domonap** (альтернативный провайдер домофона в домах ПИК) из **Home Assistant**.

## Возможности

* Открытие дверей  
* Загрузка видеопотока  
* Локальный WebRTC/WHEP proxy для `go2rtc` без авторизации Domonap  
* Уведомления о звонках в виде бинарного сенсора
* События в фоновом процессе для использования в автоматизациях  

## WebRTC proxy для go2rtc

Интеграция поднимает локальный WHEP proxy в Home Assistant. Он сам обновляет токен и проксирует WebRTC-сессию в Domonap, поэтому `go2rtc` может подключаться к HA URL без авторизации в Domonap.

У WebRTC-камеры появляются атрибуты:
* `go2rtc_webrtc_path` - относительный путь proxy внутри Home Assistant
* `go2rtc_webrtc_url` - полный URL для подключения, если в HA настроен внутренний или внешний URL

Пример `go2rtc`:
```yaml
streams:
  domonap_entrance:
    - webrtc:http://HOME_ASSISTANT:8123/api/domonap/webrtc_proxy/<secret>/<camera_id>/whep
```

Практически удобнее брать готовое значение из атрибута `go2rtc_webrtc_url` у сущности камеры и вставлять его в конфиг `go2rtc`.

## Автоматизации

### Интеграция генерирует события, которые можно использовать в автоматизациях Home Assistant:
1. При входящем звонке```domonap_incoming_call``` следующего содержания:
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
  SipAccount: *
  SipPassword: *
  SipDomain: *
  SipPort: *
  PushType: Domofon
  PhotoUrl: >-
    https://home-assistant.local/api/domonap/media_proxy/...
  photoUrl: >-
    https://home-assistant.local/api/domonap/media_proxy/...
  OriginalPhotoUrl: >-
    https://s3-api.domonap.ru/...
origin: LOCAL
time_fired: "2025-06-18T15:10:58.919425+00:00"
```
* `DoorId` - идентификатор двери
* `Address` - адрес вызывающего устройства 
* `PhotoUrl` / `photoUrl` - URL фотографии звонящего через локальный proxy Home Assistant. Интеграция получает исходный `photoUrl` так же, как приложение Domonap: из истории звонков `client-api/CallLog/GetCallLogs`. Если запись звонка или фото ещё недоступны, используется авторизованный `VideoPreview`
* `OriginalPhotoUrl` - оригинальный `photoUrl` из истории звонков Domonap для диагностики
* `PushPhotoUrl` - исходный `photoUrl` из push-события, если Domonap его прислал; хранится только для диагностики
* `VideoPreview` - URL превью через локальный proxy Home Assistant с авторизацией в Domonap

2. При входящем сообщении```domonap_receive_message``` следующего содержания:
```yaml
event_type: domonap_receive_message
data:
  id: 8452d508564e5a076c8122b6
  channel: username1-username
  text: "Текст сообщения"
  data: null
  sender: username1
  name: Username
  chatType: Private
  createdOn: "2025-06-18T15:02:32.1264693Z"
  isRead: false
  avatar: https://s3-api.domonap.ru/avatar/avatar
origin: LOCAL
time_fired: "2025-06-18T15:02:32.208782+00:00"
```

3. При изменении статуса пользователя (жителя дома)```domonap_user_status_changed``` следующего содержания:
```yaml
event_type: domonap_user_status_changed
data:
  user: username1
  status: offline
origin: LOCAL
time_fired: "2025-06-18T15:07:23.395167+00:00"
```

### Пример автоматизаций:
Push уведомление мобильного приложения Home Assistant:
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
Уведомления о звонке сообщением с фото в Telegram (настройка [Telegram](https://www.home-assistant.io/integrations/telegram_polling)):
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
## Ограничения
Существует ограничение на одновременное использование одного номера телефона в приложении Domonap и интеграции HA. На мобильное устройство с официальным приложением Domonap перестанут приходить push уведомления о входящем звонке в режиме когда приложение не находится на открытом экране.

## Установка
* Установка осветляется через HACS, предварительно необходимо добавить репозиторий https://github.com/svmironov/domonap_intercom
* После установки перезапустите сервер Home Assistant  
* Добавьте интеграцию в разделе **Настройки → Устройства и службы**  
* Авторизация выполняется по номеру телефона, привязанному к приложению при регистрации  

## Отказ от ответственности

Данное программное обеспечение никак не связано и не одобрено ООО «ДОМОНАП», владельцем торговой марки ДОМОНАП. Используйте его на свой страх и риск. Автор ни при каких обстоятельствах не несёт ответственности за повреждение или утрату вашего имущества, а также за возможный вред третьим лицам.

Все логотипы, названия брендов и продуктов принадлежат их законным владельцам.
