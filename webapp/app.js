/* ORBIT Mini App.
   Работает в двух режимах:
   • внутри Telegram — берёт данные с сервера ORBIT и принимает оплату Stars;
   • в обычном браузере (нет initData) — демо-режим с примером данных,
     чтобы можно было посмотреть интерфейс без Telegram. */

(() => {
  "use strict";

  const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  const params = new URLSearchParams(location.search);
  const initData = (tg && tg.initData) || "";
  const DEMO = !initData;

  const $ = (id) => document.getElementById(id);
  const dateFmt = new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long" });

  const state = { catalog: null, me: null };

  // ---------------------------------------------------------------- тема
  const theme =
    params.get("theme") ||
    (tg && tg.colorScheme) ||
    (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
  document.documentElement.dataset.theme = theme;

  if (tg) {
    tg.ready();
    tg.expand();
    try {
      const bg = getComputedStyle(document.documentElement).getPropertyValue("--bg").trim();
      tg.setHeaderColor(bg);
      tg.setBackgroundColor(bg);
    } catch (_) { /* старые клиенты Telegram */ }
  }

  const haptic = (kind = "light") => {
    try { tg && tg.HapticFeedback.impactOccurred(kind); } catch (_) {}
  };

  // ---------------------------------------------------------------- данные
  async function api(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: { "Content-Type": "application/json", "X-Telegram-Init-Data": initData },
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `Сервер ответил ${response.status}`);
    return data;
  }

  const DEMO_CATALOG = {
    hub_name: "ORBIT",
    bundle: {
      code: "all", name: "Всё включено", price_stars: 349, separate_total: 447, savings: 98,
      tagline: "Все приложения ORBIT одной подпиской — и все будущие тоже",
    },
    apps: [
      { code: "mera", name: "MERA", tagline: "Запись к мастерам и память о каждом клиенте", status: "live", price_stars: 249, link: "https://t.me/mera_business_bot?startapp=orbit", monogram: "M",
        colors: { from: "#3A2A1E", to: "#16100C", accent: "#D8B27A" },
        features: ["Онлайн-запись клиентов без звонков", "Память о клиенте: что любит, когда был, что напомнить", "Автопилот: готовые действия на каждый день", "Статистика и склад в одном месте"] },
      { code: "velora", name: "VELORA", tagline: "Привычки и цели с ИИ-коучем", status: "live", price_stars: 99, link: "", monogram: "V",
        colors: { from: "#2A2D30", to: "#0E0F10", accent: "#43E08A" },
        features: ["Трекер привычек и целей", "ИИ-коуч с разбором недели", "Голосом: скажи — ИИ разложит по задачам", "Напоминания и отчёты"] },
      { code: "delta", name: "DELTA", tagline: "Расходы, доходы и курсы валют", status: "live", price_stars: 99, link: "", monogram: "Δ",
        colors: { from: "#2B1D5C", to: "#0D0820", accent: "#9C84FF" },
        features: ["Учёт расходов и доходов в любой валюте", "Курсы ₽, $, €, ¥ и крипты", "Запись трат с iPhone в одно касание", "Общий бюджет на двоих"] },
      { code: "next", name: "Новое приложение", tagline: "Уже в работе. Подписчики «Всё включено» получат его первыми", status: "soon", price_stars: 99, link: "", monogram: "+",
        colors: { from: "#1C1C1F", to: "#0B0B0D", accent: "#8A8A93" }, features: [] },
    ],
  };

  function demoMe() {
    const in12 = new Date(Date.now() + 12 * 864e5).toISOString();
    const off = (app) => ({ app, active: false, plan: null, expires_at: null, source: null });
    return {
      user: { id: 0, first_name: "Арсений", username: null },
      is_admin: false,
      access: {
        mera: off("mera"),
        velora: { app: "velora", active: true, plan: "velora", expires_at: in12, source: "app" },
        delta: off("delta"),
        next: off("next"),
      },
      subscriptions: [{ plan: "velora", title: "VELORA", expires_at: in12, auto_renew: true, active: true }],
    };
  }

  async function load() {
    try {
      state.catalog = await api("/api/catalog");
    } catch (error) {
      if (!DEMO) return showFatal("Не удалось загрузить каталог. Проверь подключение и открой ORBIT ещё раз.");
      state.catalog = DEMO_CATALOG;
    }
    if (DEMO) {
      state.me = demoMe();
    } else {
      try {
        state.me = await api("/api/me");
      } catch (error) {
        return showFatal(error.message);
      }
    }
    render();
    $("page").setAttribute("aria-busy", "false");
    openFromStartParam();
  }

  // ---------------------------------------------------------------- отрисовка
  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  const paint = (node, colors) => {
    node.style.setProperty("--from", colors.from);
    node.style.setProperty("--to", colors.to);
    node.style.setProperty("--accent", colors.accent);
  };

  const accessOf = (code) => (state.me.access && state.me.access[code]) || { active: false };
  const until = (iso) => (iso ? dateFmt.format(new Date(iso)) : "");

  function render() {
    const { catalog, me } = state;
    const live = catalog.apps.filter((a) => a.status === "live");
    const connected = live.filter((a) => accessOf(a.code).active);
    const bundleActive = (me.subscriptions || []).some((s) => s.plan === catalog.bundle.code && s.active);

    document.title = catalog.hub_name;
    $("wordmark").textContent = catalog.hub_name;

    const name = (me.user && me.user.first_name) || "друг";
    $("core").textContent = name.trim().charAt(0).toUpperCase() || "·";
    $("hello").textContent = `Привет, ${name}`;
    $("helloSub").textContent = me.is_admin
      ? "Режим владельца: все приложения открыты"
      : connected.length
        ? `Подключено ${connected.length} из ${live.length}`
        : "Выбери приложение или подключи всё сразу";

    const pill = $("statusPill");
    pill.hidden = !(DEMO || bundleActive || me.is_admin);
    pill.textContent = DEMO ? "Демо" : me.is_admin ? "Владелец" : catalog.bundle.name;

    renderOrbit(catalog.apps);
    renderBundle(catalog.bundle, bundleActive || me.is_admin);
    renderApps(catalog.apps);
    renderSubs(me.subscriptions || []);
  }

  function renderOrbit(apps) {
    const orbit = $("orbit");
    orbit.querySelectorAll(".planet").forEach((p) => p.remove());
    apps.forEach((app, i) => {
      // Раскладываем планеты по эллипсу (такому же, как в SVG).
      const angle = ((210 + (i * 360) / apps.length) * Math.PI) / 180;
      const x = 50 + 46.875 * Math.cos(angle);
      const y = 50 + 39 * Math.sin(angle);
      const active = accessOf(app.code).active;
      const planet = el("button", "planet" + (app.status === "soon" ? " off soon" : active ? "" : " off"));
      planet.type = "button";
      planet.style.setProperty("--x", `${x}%`);
      planet.style.setProperty("--y", `${y}%`);
      planet.style.setProperty("--i", i);
      paint(planet, app.colors);
      planet.append(el("span", "", app.monogram));
      planet.setAttribute(
        "aria-label",
        `${app.name}: ${app.status === "soon" ? "скоро" : active ? "подключено" : "не подключено"}`
      );
      planet.addEventListener("click", () => openSheet(app.code));
      orbit.append(planet);
    });
  }

  function renderBundle(bundle, active) {
    const box = $("bundle");
    box.hidden = active;
    if (active) return;
    $("bundleName").textContent = bundle.name;
    $("bundleTagline").textContent = bundle.tagline;
    $("bundlePrice").textContent = bundle.price_stars;
    $("bundleWas").textContent = bundle.savings > 0 ? `вместо ${bundle.separate_total} ⭐ по отдельности` : "";
    $("bundleBtn").onclick = () => buy(bundle.code);
  }

  function renderApps(apps) {
    const list = $("apps");
    list.replaceChildren();
    apps.forEach((app) => {
      const access = accessOf(app.code);
      const row = el("button", "app-row");
      row.type = "button";
      paint(row, app.colors);

      const tile = el("span", "tile", app.monogram);
      const text = el("span", "");
      text.append(el("div", "app-name", app.name), el("div", "app-tag", app.tagline));

      const side = el("span", "app-side");
      if (app.status === "soon") {
        side.append(el("span", "soon", "Скоро"));
      } else if (access.active) {
        side.append(el("div", "on", "Подключено"));
        if (access.expires_at) side.append(el("div", "app-tag", `до ${until(access.expires_at)}`));
      } else {
        side.append(el("span", "price", `${app.price_stars} ⭐`), el("div", "app-tag", "в месяц"));
      }

      row.append(tile, text, side);
      row.addEventListener("click", () => openSheet(app.code));
      const li = el("li");
      li.append(row);
      list.append(li);
    });
  }

  function renderSubs(subs) {
    const block = $("subsBlock");
    const list = $("subs");
    const visible = subs.filter((s) => s.active);
    block.hidden = visible.length === 0;
    list.replaceChildren();
    visible.forEach((sub) => {
      const row = el("div", "sub-row");
      const info = el("div");
      info.append(
        el("div", "sub-name", sub.title),
        el(
          "div",
          sub.auto_renew ? "sub-meta" : "sub-meta warn",
          sub.auto_renew
            ? `Продлится ${until(sub.expires_at)}`
            : `Продление отключено, доступ до ${until(sub.expires_at)}`
        )
      );
      const button = el(
        "button",
        "btn btn-ghost btn-small",
        sub.auto_renew ? "Отключить продление" : "Возобновить"
      );
      button.type = "button";
      button.addEventListener("click", () => toggleRenew(sub, button));
      row.append(info, button);
      const li = el("li");
      li.append(row);
      list.append(li);
    });
  }

  // ---------------------------------------------------------------- шторка
  function openSheet(code) {
    const { catalog } = state;
    const app = catalog.apps.find((a) => a.code === code);
    if (!app) return;
    haptic();
    const access = accessOf(code);
    const sheet = $("sheet");

    paint(sheet, app.colors);
    $("sheetMono").textContent = app.monogram;
    $("sheetName").textContent = app.name;
    $("sheetTagline").textContent = app.tagline;

    const status = $("sheetStatus");
    status.className = "sheet-status" + (access.active ? " on" : "");
    if (app.status === "soon") {
      status.textContent = `Приложение готовится. Оно войдёт в «${catalog.bundle.name}» без доплаты.`;
    } else if (access.source === "admin") {
      status.textContent = "Открыто: ты владелец.";
    } else if (access.active) {
      const via = access.source === "bundle" ? ` по подписке «${catalog.bundle.name}»` : "";
      status.textContent = `Подключено${via}${access.expires_at ? ` до ${until(access.expires_at)}` : ""}.`;
    } else {
      status.textContent = `${app.price_stars} ⭐ в месяц. Можно отключить в любой момент.`;
    }

    const features = $("sheetFeatures");
    features.replaceChildren(...app.features.map((f) => el("li", "", f)));
    features.hidden = app.features.length === 0;

    const actions = $("sheetActions");
    actions.replaceChildren();
    const bundleActive = (state.me.subscriptions || []).some((s) => s.plan === catalog.bundle.code && s.active);
    const add = (label, cls, handler, disabled = false) => {
      const b = el("button", `btn ${cls}`, label);
      b.type = "button";
      b.disabled = disabled;
      if (handler) b.addEventListener("click", handler);
      actions.append(b);
    };

    if (app.status === "soon") {
      if (!bundleActive) add(`Подключить «${catalog.bundle.name}» — ${catalog.bundle.price_stars} ⭐`, "btn-primary", () => buy(catalog.bundle.code));
    } else if (access.active) {
      if (app.link) add(`Открыть ${app.name}`, "btn-primary", () => openApp(app));
      else add("Ссылка на приложение скоро появится", "btn-ghost", null, true);
    } else {
      add(`Подписаться за ${app.price_stars} ⭐`, "btn-primary", () => buy(app.code));
      if (!bundleActive) {
        add(`Или всё сразу — ${catalog.bundle.price_stars} ⭐`, "btn-ghost", () => buy(catalog.bundle.code));
      }
    }

    $("backdrop").hidden = false;
    sheet.hidden = false;
    $("sheetClose").focus({ preventScroll: true });
    if (tg) {
      tg.BackButton.show();
      tg.BackButton.onClick(closeSheet);
    }
  }

  function closeSheet() {
    $("sheet").hidden = true;
    $("backdrop").hidden = true;
    if (tg) {
      tg.BackButton.offClick(closeSheet);
      tg.BackButton.hide();
    }
  }

  $("sheetClose").addEventListener("click", closeSheet);
  $("backdrop").addEventListener("click", closeSheet);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheet(); });

  // ---------------------------------------------------------------- действия
  function openApp(app) {
    haptic("medium");
    if (tg && app.link.startsWith("https://t.me/")) tg.openTelegramLink(app.link);
    else window.open(app.link, "_blank", "noopener");
  }

  async function buy(plan) {
    haptic("medium");
    if (DEMO) return toast("В демо-режиме оплата не проводится. Открой ORBIT в Telegram.");
    try {
      const { link } = await api("/api/invoice", { method: "POST", body: JSON.stringify({ plan }) });
      tg.openInvoice(link, (status) => {
        if (status === "paid") {
          toast("Оплачено. Подключаю…");
          closeSheet();
          refreshAfterPayment();
        } else if (status === "failed") {
          toast("Оплата не прошла. Попробуй ещё раз.");
        }
      });
    } catch (error) {
      toast(error.message);
    }
  }

  // Сообщение об оплате приходит боту за пару секунд — подождём его.
  async function refreshAfterPayment() {
    const before = JSON.stringify(state.me.access);
    for (let attempt = 0; attempt < 8; attempt += 1) {
      await new Promise((r) => setTimeout(r, 1500));
      try {
        state.me = await api("/api/me");
      } catch (_) { continue; }
      if (JSON.stringify(state.me.access) !== before) {
        render();
        toast("Готово, подписка подключена.");
        try { tg.HapticFeedback.notificationOccurred("success"); } catch (_) {}
        return;
      }
    }
    render();
    toast("Оплата прошла. Если доступ не появился — закрой и открой ORBIT.");
  }

  function toggleRenew(sub, button) {
    const turningOff = sub.auto_renew;
    const question = turningOff
      ? `Отключить продление «${sub.title}»? Доступ останется до ${until(sub.expires_at)}.`
      : `Снова включить продление «${sub.title}»?`;
    const run = async (ok) => {
      if (!ok) return;
      if (DEMO) return toast("В демо-режиме подписки не меняются.");
      button.disabled = true;
      try {
        await api(`/api/subscriptions/${encodeURIComponent(sub.plan)}/${turningOff ? "cancel" : "resume"}`, { method: "POST" });
        state.me = await api("/api/me");
        render();
        toast(turningOff ? "Продление отключено." : "Продление включено.");
      } catch (error) {
        button.disabled = false;
        toast(error.message);
      }
    };
    if (tg && tg.showConfirm) tg.showConfirm(question, run);
    else run(window.confirm(question));
  }

  function openFromStartParam() {
    const raw = (tg && tg.initDataUnsafe && tg.initDataUnsafe.start_param) || params.get("plan") || "";
    const code = raw.startsWith("buy_") ? raw.slice(4) : raw;
    if (!code) return;
    if (code === state.catalog.bundle.code) {
      $("bundleBtn").focus();
      $("bundle").scrollIntoView({ behavior: "smooth", block: "center" });
    } else {
      openSheet(code);
    }
  }

  // ---------------------------------------------------------------- служебное
  let toastTimer;
  function toast(message) {
    const node = $("toast");
    node.textContent = message;
    node.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { node.hidden = true; }, 3200);
  }

  function showFatal(message) {
    $("hello").textContent = "ORBIT не загрузился";
    $("helloSub").textContent = message;
    $("page").setAttribute("aria-busy", "false");
  }

  load();
})();
