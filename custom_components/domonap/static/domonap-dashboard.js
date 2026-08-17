(() => {
  // Регистрируем тег сразу, пока грузится остальной файл. Иначе HA на
  // strategy-дашборде падает: Timeout waiting for strategy element.
  class DomonapDashboardStrategy extends HTMLElement {
    static async generate(config, hass) {
      const started = Date.now();
      while (typeof window.__domonapDashboardGenerate !== "function") {
        if (Date.now() - started > 8000) {
          throw new Error("Domonap dashboard script is still loading");
        }
        await new Promise((resolve) => setTimeout(resolve, 50));
      }
      return window.__domonapDashboardGenerate(config, hass);
    }

    static shouldRegenerate(config, oldHass, newHass) {
      if (typeof window.__domonapDashboardShouldRegenerate === "function") {
        return window.__domonapDashboardShouldRegenerate(config, oldHass, newHass);
      }
      return false;
    }

    static getCreateSuggestions(hass) {
      if (typeof window.__domonapDashboardSuggestions === "function") {
        return window.__domonapDashboardSuggestions(hass);
      }
      return { title: "Domonap", icon: "mdi:doorbell-video" };
    }
  }

  ["ll-strategy-domonap", "ll-strategy-dashboard-domonap"].forEach((tag) => {
    if (!customElements.get(tag)) customElements.define(tag, DomonapDashboardStrategy);
  });

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

  function rawName(state) {
    return String(
      attr(state, "name") ||
        (state.attributes && state.attributes.friendly_name) ||
        state.entity_id
    );
  }

  // Короткое имя двери без хвоста «Открыть дверь», #doorId и длинного
  // addressString в скобках — иначе в entities обрезается «Малое Пон...».
  function cleanName(state) {
    let name = rawName(state).replace(/\s*Открыть дверь\s*$/i, "").trim();
    name = name.replace(/\s*#[0-9a-fA-F]{4,}\s*$/i, "").trim();
    name = name.replace(/\s*\([^)]*(?:улица|ул\.|д\.|паркинг|кладов|машиномест|место\.)[^)]*\)\s*/gi, " ").trim();
    name = name.replace(/\s*\([^)]{12,}\)\s*$/g, "").trim();
    name = name.replace(/\s{2,}/g, " ").trim();
    return name || rawName(state);
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

  function streetOf(building) {
    return String(building || "").split(",")[0].trim();
  }

  function houseOf(building) {
    const value = String(building || "");
    const index = value.indexOf(",");
    return index === -1 ? "" : value.slice(index + 1).trim();
  }

  // addressString в API — квартира/кладовка/машиноместо целиком.
  // Для вкладок оставляем улицу и дом, как на ручной панели «Домофон».
  function buildingOf(address) {
    let value = String(address || "").trim();
    if (!value) return "Другие двери";
    value = value.replace(/^паркинг\s*:\s*/i, "");
    value = value.replace(/\bул\.\s*/gi, "");
    const parts = value.split(",").map((part) => part.trim()).filter(Boolean);
    let street = "";
    let house = "";
    parts.forEach((part) => {
      const houseMatch = part.match(/^(д\.?\s*\S+)/i);
      if (houseMatch && !house) {
        house = houseMatch[1];
        return;
      }
      if (/^(п\.|э\.?-?|кв\.|кладов|место|машиномест)/i.test(part)) return;
      if (!street) street = part;
    });
    street = street.replace(/^\s*(улица|ул\.?)\s+/i, "").replace(/\s+улица\s*$/i, "").trim();
    return [street, house].filter(Boolean).join(", ") || value;
  }

  function doorKind(address, name) {
    const addrLow = String(address || "").toLowerCase();
    const nameLow = String(name || "").toLowerCase();
    if (/паркинг|машиномест|\bместо\./i.test(addrLow)) return "parking";
    if (/тамбур|подвал|кладов/i.test(nameLow)) return "storage";
    if (/калит|ворот|считыват|лифтов|лест|подъезд|вход/i.test(nameLow)) return "home";
    if (/кладов|келлер|э\.-1|э\s*-1/i.test(addrLow)) return "storage";
    return "home";
  }

  function viewTitle(building, kind, streetKindCounts) {
    const street = streetOf(building);
    const house = houseOf(building);
    const clash = (streetKindCounts[street + "|" + kind] || 0) > 1;
    const base = clash && house ? street + ", " + house : street;
    if (kind === "parking") return "Паркинг · " + base;
    if (kind === "storage") return "Кладовки · " + base;
    return base || "Domonap";
  }

  function viewIcon(kind, homeIndex) {
    if (kind === "parking") return "mdi:parking";
    if (kind === "storage") return "mdi:warehouse";
    return homeIndex === 0 ? "mdi:home-city" : "mdi:home-city-outline";
  }

  function kindLabel(kind) {
    if (kind === "parking") return "паркинг";
    if (kind === "storage") return "кладовки";
    return "дом";
  }

  function kindRank(kind) {
    if (kind === "home") return 0;
    if (kind === "parking") return 1;
    if (kind === "storage") return 2;
    return 9;
  }

  function slugify(title, index) {
    const map = {
      а: "a", б: "b", в: "v", г: "g", д: "d", е: "e", ё: "e", ж: "zh", з: "z",
      и: "i", й: "y", к: "k", л: "l", м: "m", н: "n", о: "o", п: "p", р: "r",
      с: "s", т: "t", у: "u", ф: "f", х: "h", ц: "ts", ч: "ch", ш: "sh", щ: "sch",
      ъ: "", ы: "y", ь: "", э: "e", ю: "yu", я: "ya",
    };
    let slug = String(title || "").toLowerCase().split("").map((ch) => (
      Object.prototype.hasOwnProperty.call(map, ch) ? map[ch] : ch
    )).join("");
    slug = slug.replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
    return (slug || "addr") + "-" + index;
  }

  // Номер из entity_id (..._open_door, ..._open_door_2) — стабильный порядок 1..N.
  function entitySeq(state) {
    const id = String(state && state.entity_id || "");
    const match = id.match(/_(\d+)$/);
    return match ? parseInt(match[1], 10) : 1;
  }

  function compareButtons(left, right) {
    const baseLeft = String(left.entity_id || "").replace(/_\d+$/, "");
    const baseRight = String(right.entity_id || "").replace(/_\d+$/, "");
    if (baseLeft !== baseRight) return baseLeft.localeCompare(baseRight, "en");
    const bySeq = entitySeq(left) - entitySeq(right);
    if (bySeq) return bySeq;
    return doorIdOf(left).localeCompare(doorIdOf(right));
  }

  function labelNumber(label) {
    const match = String(label || "").match(/(\d+)\s*$/);
    return match ? parseInt(match[1], 10) : 0;
  }

  function sortDoorItems(items, names) {
    return items.slice().sort((left, right) => {
      const nameLeft = labelOf(left.button, names);
      const nameRight = labelOf(right.button, names);
      const numLeft = labelNumber(nameLeft);
      const numRight = labelNumber(nameRight);
      if (numLeft !== numRight) return numLeft - numRight;
      const byButton = compareButtons(left.button, right.button);
      if (byButton) return byButton;
      return nameLeft.localeCompare(nameRight, "ru", { numeric: true });
    });
  }

  function groupBySite(buttons) {
    const groups = new Map();
    buttons.forEach((button) => {
      const address = addressOf(button);
      const kind = doorKind(address, cleanName(button));
      const building = buildingOf(address);
      const key = building + "|" + kind;
      if (!groups.has(key)) groups.set(key, { building, kind, buttons: [] });
      groups.get(key).buttons.push(button);
    });
    const streetKindCounts = {};
    groups.forEach((group) => {
      const key = streetOf(group.building) + "|" + group.kind;
      streetKindCounts[key] = (streetKindCounts[key] || 0) + 1;
    });
    return [...groups.values()]
      .map((group) => ({
        building: group.building,
        kind: group.kind,
        title: viewTitle(group.building, group.kind, streetKindCounts),
        // Стабильный порядок дверей внутри вкладки.
        buttons: group.buttons.slice().sort(compareButtons),
      }))
      .sort((left, right) => {
        // Сначала все дома, потом паркинги, потом кладовки.
        const byKind = kindRank(left.kind) - kindRank(right.kind);
        if (byKind) return byKind;
        const byStreet = streetOf(left.building).localeCompare(streetOf(right.building), "ru");
        if (byStreet) return byStreet;
        return left.building.localeCompare(right.building, "ru");
      });
  }

  function displayNames(buttons) {
    const items = buttons.map((button) => ({ button, base: cleanName(button) }));
    const counts = {};
    items.forEach((item) => {
      counts[item.base] = (counts[item.base] || 0) + 1;
    });
    const seen = {};
    const names = new Map();
    items
      .slice()
      .sort((left, right) => compareButtons(left.button, right.button))
      .forEach((item) => {
        if (counts[item.base] > 1) {
          seen[item.base] = (seen[item.base] || 0) + 1;
          names.set(item.button.entity_id, item.base + " " + seen[item.base]);
        } else {
          names.set(item.button.entity_id, item.base);
        }
      });
    return names;
  }

  function labelOf(button, names) {
    return (names && names.get(button.entity_id)) || cleanName(button);
  }

  function pressAction(entityId) {
    return {
      action: "call-service",
      service: "button.press",
      service_data: { entity_id: entityId },
      target: { entity_id: entityId },
    };
  }

  function doorIcon(name) {
    if (/калит|ворот|gate/i.test(name)) return "mdi:gate";
    if (/въезд|выезд|шлагбаум|boom/i.test(name)) return "mdi:boom-gate";
    if (/лестн|stair/i.test(name)) return "mdi:stairs";
    if (/тамбур/i.test(name)) return "mdi:door-closed-lock";
    if (/подвал/i.test(name)) return "mdi:stairs-down";
    return "mdi:door";
  }

  function callCard(button, camera, callSensor, names) {
    if (!callSensor) return null;
    const cards = [
      { type: "markdown", content: "## Звонок — " + labelOf(button, names) },
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
      name: "Открыть",
      icon: "mdi:door-open",
      icon_height: "36px",
      tap_action: pressAction(button.entity_id),
    });
    return {
      type: "conditional",
      conditions: [{ entity: callSensor.entity_id, state: "on" }],
      card: { type: "vertical-stack", cards },
    };
  }

  // Камера слева, компактная «Открыть» справа — как на ручной панели.
  function doorGrid(withCamera, names) {
    const cards = [];
    sortDoorItems(withCamera, names).forEach(({ button, camera }) => {
      cards.push({
        type: "picture-entity",
        title: labelOf(button, names),
        entity: camera.entity_id,
        camera_view: "live",
        show_state: false,
      });
      cards.push({
        type: "button",
        name: "Открыть",
        icon: "mdi:door-open",
        icon_height: "36px",
        tap_action: pressAction(button.entity_id),
      });
    });
    if (!cards.length) return null;
    return { type: "grid", columns: 2, square: false, cards };
  }

  function entitiesCard(title, items, names, pins) {
    const entities = sortDoorItems(items, names).map(({ button }) => {
      const name = labelOf(button, names);
      return {
        type: "button",
        name,
        icon: doorIcon(name),
        action_name: "Открыть",
        tap_action: pressAction(button.entity_id),
      };
    });
    (pins || []).forEach((pin) => {
      entities.push({
        entity: pin.entity_id,
        name: "Код — " + cleanName(pin),
      });
    });
    if (!entities.length) return null;
    return { type: "entities", title, show_header_toggle: false, entities };
  }

  function otherLists(withoutCamera, pins, names, kind) {
    const byBase = new Map();
    sortDoorItems(withoutCamera, names).forEach((item) => {
      const base = cleanName(item.button);
      if (!byBase.has(base)) byBase.set(base, []);
      byBase.get(base).push(item);
    });
    const cards = [];
    const leftover = [];
    [...byBase.entries()]
      .sort((left, right) => left[0].localeCompare(right[0], "ru"))
      .forEach(([base, items]) => {
        if (items.length >= 3) cards.push(entitiesCard(base, items, names, []));
        else leftover.push(...items);
      });
    const leftoverTitle =
      kind === "parking" ? "Калитки и шлагбаумы" : kind === "storage" ? "Прочее" : "Калитки и входы";
    const leftoverCard = entitiesCard(leftoverTitle, leftover, names, pins);
    if (leftoverCard) cards.push(leftoverCard);
    return cards.filter(Boolean);
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
        type: "entities",
        show_header_toggle: false,
        entities: [
          {
            type: "button",
            name: "Дверь последнего звонка",
            icon: "mdi:phone-incoming",
            action_name: "Открыть",
            tap_action: pressAction(lastCallButton.entity_id),
          },
        ],
      });
    }
    return cards;
  }

  function siteHeader(building, kind) {
    return {
      type: "markdown",
      content: "**" + building + "** · " + kindLabel(kind),
    };
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

  class DomonapDashboardGenerator {
    static async generate(_config, hass) {
      const data = collect(hass);
      const groups = groupBySite(data.buttons);
      const allNames = displayNames(data.buttons);
      const views = [];
      const allCallCards = [];
      data.buttons.forEach((button) => {
        const call = callCard(button, cameraFor(button, data.cameras), callFor(button, data.calls), allNames);
        if (call) allCallCards.push(call);
      });
      const statusCards = lastCallCards(data.lastCall, data.lastCallButton);

      if (!groups.length) {
        views.push({
          title: "Domonap",
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
        let homeIndex = 0;
        groups.forEach((group, index) => {
          const names = displayNames(group.buttons);
          const withCamera = [];
          const withoutCamera = [];
          const pinEntities = [];
          group.buttons.forEach((button) => {
            const camera = cameraFor(button, data.cameras);
            const item = { button, camera };
            if (camera) withCamera.push(item);
            else withoutCamera.push(item);
            const pin = pinFor(button, data.pins);
            if (pin) pinEntities.push(pin);
          });
          const cards = [siteHeader(group.building, group.kind), ...statusCards, ...allCallCards];
          const grid = doorGrid(withCamera, names);
          if (grid) cards.push(grid);
          cards.push(...otherLists(withoutCamera, pinEntities, names, group.kind));
          if (!grid && !withoutCamera.length && !pinEntities.length) {
            cards.push({
              type: "markdown",
              content: "На этом адресе нет дверей с кнопкой открытия.",
            });
          }
          const icon = viewIcon(group.kind, group.kind === "home" ? homeIndex : 0);
          if (group.kind === "home") homeIndex += 1;
          views.push({
            title: group.title,
            // Префикс path держит порядок дом → паркинг → кладовки.
            path: kindRank(group.kind) + "-" + slugify(group.title, index),
            icon,
            cards,
          });
        });
      }

      views.push(cabinetView("Поддержка", "support", "mdi:headset", "support"));
      views.push(cabinetView("Аватары", "face", "mdi:face-recognition", "face"));
      return { title: "Domonap", views };
    }
  }

  window.__domonapDashboardGenerate = (config, hass) =>
    DomonapDashboardGenerator.generate(config, hass);
  window.__domonapDashboardShouldRegenerate = (config, oldHass, newHass) =>
    doorSignature(oldHass) !== doorSignature(newHass);
  window.__domonapDashboardSuggestions = () => ({
    title: "Domonap",
    icon: "mdi:doorbell-video",
  });

  window.customStrategies = window.customStrategies || [];
  if (!window.customStrategies.some((item) => item && item.type === "domonap")) {
    window.customStrategies.push({
      type: "domonap",
      strategyType: "dashboard",
      name: "Domonap",
      description: "Двери аккаунта DomoNAP, поддержка и проход по лицу",
      documentationURL: "https://github.com/troy9221/DomoNAP_HA",
    });
  }
})();
