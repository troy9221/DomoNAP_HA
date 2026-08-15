(() => {
  function attr(state, key) {
    return state && state.attributes ? state.attributes[key] : undefined;
  }

  function doorIdOf(state) {
    const value = attr(state, "doorId") || attr(state, "DoorId") || attr(state, "door_id");
    return value ? String(value) : "";
  }

  function addressOf(state) {
    return String(attr(state, "addressString") || attr(state, "Address") || "").trim();
  }

  function nameOf(state) {
    return String(
      attr(state, "name") ||
        (state.attributes && state.attributes.friendly_name) ||
        state.entity_id
    );
  }

  function isLastCallButton(state) {
    return state.entity_id.indexOf("open_relay_by_last_call") !== -1;
  }

  function collect(hass) {
    const buttons = [];
    const cameras = [];
    const calls = [];
    const pins = [];
    let lastCall = null;
    let lastCallButton = null;

    Object.values(hass.states || {}).forEach((state) => {
      if (!state || !state.entity_id) return;
      const id = state.entity_id;
      if (id.startsWith("button.") && isLastCallButton(state)) {
        lastCallButton = state;
        return;
      }
      if (id.startsWith("sensor.") && id.indexOf("last_call_door_id") !== -1) {
        lastCall = state;
        return;
      }
      if (id.startsWith("button.") && doorIdOf(state) && !isLastCallButton(state)) {
        buttons.push(state);
        return;
      }
      if (id.startsWith("camera.") && (doorIdOf(state) || attr(state, "id") || attr(state, "httpVideoUrl") || attr(state, "webrtcVideoUrl"))) {
        cameras.push(state);
        return;
      }
      // Не ищем "incoming_call" / "door_code" в entity_id: в русской HA
      // object_id берётся из перевода («входящий звонок», «код двери»).
      if (id.startsWith("binary_sensor.") && doorIdOf(state)) {
        calls.push(state);
        return;
      }
      if (id.startsWith("sensor.") && doorIdOf(state) && attr(state, "domofonPublicPin")) {
        pins.push(state);
      }
    });
    return { buttons, cameras, calls, pins, lastCall, lastCallButton };
  }

  function cameraFor(button, cameras) {
    const doorId = doorIdOf(button);
    const keyId = String(attr(button, "id") || "");
    return cameras.find((camera) => {
      const camDoor = doorIdOf(camera);
      const camKey = String(attr(camera, "id") || camera.entity_id);
      return (doorId && camDoor === doorId) || (keyId && camKey === keyId);
    });
  }

  function callFor(button, calls) {
    const doorId = doorIdOf(button);
    return calls.find((item) => doorIdOf(item) === doorId);
  }

  function pinFor(button, pins) {
    const doorId = doorIdOf(button);
    return pins.find((item) => doorIdOf(item) === doorId);
  }

  function groupByAddress(buttons) {
    const groups = new Map();
    buttons.forEach((button) => {
      const address = addressOf(button) || "Другие двери";
      if (!groups.has(address)) groups.set(address, []);
      groups.get(address).push(button);
    });
    return [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0], "ru"));
  }

  function shortTitle(address, index) {
    const parts = String(address || "").split(",").map((part) => part.trim()).filter(Boolean);
    if (!parts.length) return "Адрес " + (index + 1);
    if (parts.length <= 2) return parts.join(", ");
    return parts.slice(-2).join(", ");
  }

  function viewIcon(address) {
    const value = String(address || "").toLowerCase();
    if (/паркинг|parking|машиномест/.test(value)) return "mdi:parking";
    if (/кладов|storage|келлер/.test(value)) return "mdi:warehouse";
    return "mdi:doorbell-video";
  }

  function pressAction(entityId) {
    return {
      action: "call-service",
      service: "button.press",
      service_data: { entity_id: entityId },
      target: { entity_id: entityId },
    };
  }

  function callCard(button, camera, callSensor) {
    if (!callSensor) return null;
    const cards = [
      { type: "markdown", content: "## Звонок — " + nameOf(button) },
    ];
    if (camera) {
      cards.push({
        type: "picture-entity",
        entity: camera.entity_id,
        camera_view: "live",
        show_state: false,
        show_name: false,
      });
    }
    cards.push({
      type: "button",
      name: "Открыть дверь",
      icon: "mdi:door-open",
      tap_action: pressAction(button.entity_id),
    });
    return {
      type: "conditional",
      conditions: [{ entity: callSensor.entity_id, state: "on" }],
      card: { type: "vertical-stack", cards },
    };
  }

  function doorGrid(withCamera) {
    const cards = [];
    withCamera.forEach(({ button, camera }) => {
      cards.push({
        type: "picture-entity",
        title: nameOf(button),
        entity: camera.entity_id,
        camera_view: "live",
        show_state: false,
      });
      cards.push({
        type: "button",
        name: "Открыть · " + nameOf(button),
        icon: "mdi:door-open",
        tap_action: pressAction(button.entity_id),
      });
    });
    if (!cards.length) return null;
    return { type: "grid", columns: 2, square: false, cards };
  }

  function otherList(withoutCamera, pins) {
    if (!withoutCamera.length && !pins.length) return null;
    const entities = withoutCamera.map(({ button }) => ({
      type: "button",
      name: nameOf(button),
      icon: /калит|ворот|gate/i.test(nameOf(button))
        ? "mdi:gate"
        : /въезд|выезд|шлагбаум|boom/i.test(nameOf(button))
          ? "mdi:boom-gate"
          : /лестн|stair/i.test(nameOf(button))
            ? "mdi:stairs"
            : "mdi:door",
      action_name: "Открыть",
      tap_action: pressAction(button.entity_id),
    }));
    pins.forEach((pin) => {
      entities.push({
        entity: pin.entity_id,
        name: "Код — " + nameOf(pin),
      });
    });
    return { type: "entities", title: "Двери без камеры", show_header_toggle: false, entities };
  }

  function lastCallCards(lastCall, lastCallButton) {
    const cards = [];
    if (lastCall) {
      const address = attr(lastCall, "Address") || attr(lastCall, "addressString") || lastCall.state || "нет";
      cards.push({
        type: "markdown",
        content: "**Последний звонок:** " + address,
      });
    }
    if (lastCallButton) {
      cards.push({
        type: "button",
        name: "Открыть дверь последнего звонка",
        icon: "mdi:phone-incoming",
        tap_action: pressAction(lastCallButton.entity_id),
      });
    }
    return cards;
  }

  function cabinetView(title, path, icon, mode) {
    return {
      title,
      path,
      icon,
      type: "panel",
      cards: [
        {
          type: "custom:domonap-cabinet-card",
          mode,
        },
      ],
    };
  }

  function doorSignature(hass) {
    return Object.keys(hass.states || {})
      .filter((id) => {
        const state = hass.states[id];
        return (
          state &&
          (id.startsWith("button.") || id.startsWith("camera.") || id.startsWith("binary_sensor.") || id.startsWith("sensor.")) &&
          (doorIdOf(state) || id.indexOf("last_call") !== -1 || id.indexOf("support_tickets") !== -1 || id.indexOf("face_pass") !== -1)
        );
      })
      .sort()
      .join(",");
  }

  class DomonapDashboardStrategy extends HTMLElement {
    static async generate(_config, hass) {
      const data = collect(hass);
      const groups = groupByAddress(data.buttons);
      const views = [];

      if (!groups.length) {
        views.push({
          title: "Домофон",
          path: "doors",
          icon: "mdi:doorbell-video",
          cards: [
            {
              type: "markdown",
              content:
                "Дверей пока нет. Авторизуйтесь в интеграции **Domonap** — на дашборде появятся ключи этого аккаунта.",
            },
          ],
        });
      } else {
        groups.forEach(([address, buttons], index) => {
          const withCamera = [];
          const withoutCamera = [];
          const callCards = [];
          const pinEntities = [];
          buttons.forEach((button) => {
            const camera = cameraFor(button, data.cameras);
            const item = { button, camera };
            if (camera) withCamera.push(item);
            else withoutCamera.push(item);
            const callSensor = callFor(button, data.calls);
            const call = callCard(button, camera, callSensor);
            if (call) callCards.push(call);
            const pin = pinFor(button, data.pins);
            if (pin) pinEntities.push(pin);
          });
          const cards = [];
          if (index === 0) {
            cards.push(...lastCallCards(data.lastCall, data.lastCallButton));
          }
          cards.push(...callCards);
          const grid = doorGrid(withCamera);
          if (grid) cards.push(grid);
          const list = otherList(withoutCamera, pinEntities);
          if (list) cards.push(list);
          if (!grid && !list) {
            cards.push({
              type: "markdown",
              content: "На этом адресе нет дверей с кнопкой открытия.",
            });
          }
          views.push({
            title: shortTitle(address, index),
            path: "addr-" + index,
            icon: viewIcon(address),
            cards,
          });
        });
      }

      views.push(cabinetView("Поддержка", "support", "mdi:headset", "support"));
      views.push(cabinetView("Аватары", "face", "mdi:face-recognition", "face"));
      return { title: "Домофон", views };
    }

    static shouldRegenerate(_config, oldHass, newHass) {
      return doorSignature(oldHass) !== doorSignature(newHass);
    }

    static getCreateSuggestions(_hass) {
      return { title: "Домофон", icon: "mdi:doorbell-video" };
    }
  }

  ["ll-strategy-domonap", "ll-strategy-dashboard-domonap"].forEach((tag) => {
    if (!customElements.get(tag)) customElements.define(tag, DomonapDashboardStrategy);
  });
  window.customStrategies = window.customStrategies || [];
  if (!window.customStrategies.some((item) => item && item.type === "domonap")) {
    window.customStrategies.push({
      type: "domonap",
      strategyType: "dashboard",
      name: "Домофон",
      description: "Двери аккаунта DomoNAP, поддержка и проход по лицу",
      documentationURL: "https://github.com/troy9221/DomoNAP_HA",
    });
  }
})();
