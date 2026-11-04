/* Интерфейс: разбор JSON, проверка полноты, генерация и выгрузка карточки. */
(() => {
  "use strict";

  const state = {
    providers: [],
    examples: [],
    models: [],
    session: { has_key: false, masked: "" },
    data: { markdown: "", text: "", json: "" },
    tab: "markdown",
    canGenerate: true,
    hasResult: false,
    modelIndex: -1,
    busy: false,
    timer: null,
  };

  const $ = (id) => document.getElementById(id);

  const els = {
    exampleSelect: $("example-select"),
    productJson: $("product-json"),
    analyzeBtn: $("analyze-btn"),
    gaps: $("gaps"),
    provider: $("provider"),
    model: $("model"),
    modelList: $("model-list"),
    modelHint: $("model-hint"),
    refreshModels: $("refresh-models"),
    baseUrl: $("base-url"),
    baseUrlField: $("base-url-field"),
    apiKey: $("api-key"),
    apiKeyField: $("api-key-field"),
    keyStatus: $("key-status"),
    keyActions: $("key-actions"),
    forgetKey: $("forget-key"),
    toggleKey: $("toggle-key"),
    generateBtn: $("generate-btn"),
    generateLabel: $("generate-label"),
    spinner: $("generate-spinner"),
    progress: $("progress"),
    progressText: $("progress-text"),
    status: $("status"),
    empty: $("empty"),
    resultMd: $("result-md"),
    resultText: $("result-text"),
    resultJson: $("result-json"),
    resultMeta: $("result-meta"),
    resultWarnings: $("result-warnings"),
    copyBtn: $("copy-btn"),
    downloadBtn: $("download-btn"),
    downloadMenu: $("download-menu"),
    toast: $("toast"),
    eyeOn: document.querySelector(".icon-eye"),
    eyeOff: document.querySelector(".icon-eye-off"),
    tabs: Array.from(document.querySelectorAll(".tab")),
  };

  // ------------------------------------------------------------------ утилиты

  let toastTimer = null;

  /** У SVG нет HTML-свойства hidden — управляем атрибутом. */
  function setHidden(element, hidden) {
    if (!element) return;
    if (hidden) element.setAttribute("hidden", "");
    else element.removeAttribute("hidden");
  }

  function showToast(text) {
    if (!els.toast) return;
    els.toast.textContent = text;
    els.toast.hidden = false;
    if (toastTimer) window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => {
      els.toast.hidden = true;
    }, 2200);
  }

  function setStatus(text, kind = "") {
    els.status.textContent = text || "";
    els.status.className = "status" + (kind ? ` status--${kind}` : "");
  }

  function describeError(detail) {
    if (!detail) return "Неизвестная ошибка";
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((item) => (item && item.field ? `${item.field}: ${item.message}` : String(item)))
        .join("\n");
    }
    if (typeof detail === "object" && detail.message) return detail.message;
    return JSON.stringify(detail);
  }

  async function api(path, options = {}) {
    const { timeout = 180000, ...rest } = options;
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), timeout);
    let response;
    try {
      response = await fetch(path, {
        headers: { "Content-Type": "application/json" },
        signal: controller.signal,
        ...rest,
      });
    } catch (error) {
      if (error.name === "AbortError") {
        throw new Error(
          `Превышено время ожидания ответа (${Math.round(timeout / 1000)} с). ` +
            "Проверьте соединение, провайдера и ключ — и попробуйте снова."
        );
      }
      throw new Error(`Сервис недоступен: ${error.message}`);
    } finally {
      window.clearTimeout(timer);
    }
    let payload = null;
    try {
      payload = await response.json();
    } catch (error) {
      payload = null;
    }
    if (!response.ok) {
      throw new Error(describeError(payload && payload.detail ? payload.detail : response.statusText));
    }
    return payload;
  }

  function readProduct() {
    let parsed;
    try {
      parsed = JSON.parse(els.productJson.value);
    } catch (error) {
      throw new Error(`Некорректный JSON: ${error.message}`);
    }
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("Ожидается JSON-объект с полями name, manufacturer, specs, description");
    }
    return parsed;
  }

  function renderList(container, title, items, kind) {
    container.innerHTML = "";
    container.hidden = false;
    container.className = `gaps${kind === "warn" ? " gaps--warn" : ""}`;
    const heading = document.createElement("div");
    heading.className = "gaps__title";
    heading.textContent = title;
    container.appendChild(heading);
    if (items.length) {
      const list = document.createElement("ul");
      items.forEach((text) => {
        const item = document.createElement("li");
        item.textContent = text;
        list.appendChild(item);
      });
      container.appendChild(list);
    }
  }

  // --------------------------------------------------------------- провайдеры

  function currentProvider() {
    return state.providers.find((item) => item.id === els.provider.value) || null;
  }

  function syncProviderFields() {
    const provider = currentProvider();
    if (!provider) return;
    els.baseUrlField.hidden = !provider.needs_base_url;
    els.apiKeyField.hidden = !provider.needs_key;
    els.refreshModels.hidden = provider.id === "mock";
    els.model.placeholder = provider.default_model || "название модели";
  }

  async function loadProviders() {
    const data = await api("/api/providers");
    state.providers = data.providers || [];
    els.provider.innerHTML = "";
    state.providers.forEach((provider) => {
      const option = document.createElement("option");
      option.value = provider.id;
      option.textContent = provider.title;
      els.provider.appendChild(option);
    });
    const preferred = state.providers.find((item) => item.id === "openai_compatible");
    els.provider.value = (preferred || state.providers[0] || {}).id || "";
    syncProviderFields();
  }

  // ------------------------------------------------------------------ примеры

  async function loadExamples() {
    const data = await api("/api/examples");
    state.examples = data.examples || [];
    els.exampleSelect.innerHTML = '<option value="">Пример JSON…</option>';
    state.examples.forEach((example) => {
      const option = document.createElement("option");
      option.value = example.id;
      option.textContent = example.title;
      els.exampleSelect.appendChild(option);
    });
    if (state.examples.length) {
      els.productJson.value = JSON.stringify(state.examples[0].payload, null, 2);
    }
  }

  // ------------------------------------------------------------- сессия и ключ

  function renderSession(session) {
    state.session = session || { has_key: false, masked: "" };
    if (state.session.has_key) {
      els.keyStatus.textContent = `Ключ сохранён в сессии (${state.session.masked}). Он живёт в памяти сервера до перезапуска.`;
      els.keyActions.hidden = false;
      els.apiKey.placeholder = "Ключ уже сохранён — введите новый, чтобы заменить";
    } else {
      els.keyStatus.textContent =
        "Ключ хранится в памяти сервера до его перезапуска. В браузере и на диске не сохраняется.";
      els.keyActions.hidden = true;
      els.apiKey.placeholder = "Можно оставить пустым, если ключ задан в .env";
    }
  }

  async function loadSession() {
    try {
      const session = await api("/api/session");
      renderSession(session);
      if (session.provider) {
        els.provider.value = session.provider;
        syncProviderFields();
      }
      if (session.base_url) els.baseUrl.value = session.base_url;
      if (session.model) els.model.value = session.model;
    } catch (error) {
      /* сессия не критична для работы интерфейса */
    }
  }

  async function saveSession() {
    const apiKey = els.apiKey.value.trim();
    if (!apiKey) return;
    try {
      const session = await api("/api/session", {
        method: "POST",
        body: JSON.stringify({
          provider: els.provider.value,
          api_key: apiKey,
          model: els.model.value.trim(),
          base_url: els.baseUrl.value.trim(),
        }),
      });
      els.apiKey.value = "";
      renderSession(session);
    } catch (error) {
      setStatus(`Не удалось сохранить ключ в сессии: ${error.message}`, "error");
    }
  }

  async function forgetKey() {
    try {
      renderSession(await api("/api/session", { method: "DELETE" }));
      setStatus("Ключ удалён из сессии", "ok");
    } catch (error) {
      setStatus(error.message, "error");
    }
  }

  // ------------------------------------------------------------ модели комбобокс

  function filterModels(list, query) {
    const needle = query.toLowerCase().replace(/[-_\s.]/g, "");
    if (!needle) return list;
    return list.filter((name) => name.toLowerCase().replace(/[-_\s.]/g, "").includes(needle));
  }

  function closeModelList() {
    els.modelList.hidden = true;
    els.model.setAttribute("aria-expanded", "false");
    state.modelIndex = -1;
  }

  function chooseModel(name) {
    els.model.value = name;
    closeModelList();
  }

  function renderModelList() {
    const items = filterModels(state.models, els.model.value.trim());
    els.modelList.innerHTML = "";

    if (!items.length) {
      const empty = document.createElement("li");
      empty.className = "combobox__empty";
      empty.textContent = "Ничего не найдено — название можно ввести вручную";
      els.modelList.appendChild(empty);
    } else {
      items.slice(0, 80).forEach((name, index) => {
        const item = document.createElement("li");
        item.className =
          "combobox__item" + (index === state.modelIndex ? " combobox__item--active" : "");
        item.setAttribute("role", "option");
        item.dataset.value = name;

        const label = document.createElement("span");
        label.textContent = name;
        item.appendChild(label);

        if (name === els.model.value.trim()) {
          const tag = document.createElement("span");
          tag.className = "combobox__tag";
          tag.textContent = "выбрано";
          item.appendChild(tag);
        }

        item.addEventListener("mousedown", (event) => {
          event.preventDefault();
          chooseModel(name);
        });
        els.modelList.appendChild(item);
      });
    }

    els.modelList.hidden = false;
    els.model.setAttribute("aria-expanded", "true");
  }

  function setModels(models, hint) {
    state.models = models || [];
    if (hint) els.modelHint.textContent = hint;
    if (!els.modelList.hidden) renderModelList();
  }

  async function reloadModels(force = false) {
    const provider = currentProvider();
    if (!provider) return;
    els.modelHint.textContent = "Загружаем список моделей…";
    if (force) {
      els.refreshModels.classList.add("is-loading");
      els.refreshModels.disabled = true;
    }
    try {
      const data = await api("/api/models", {
        timeout: 45000,
        method: "POST",
        body: JSON.stringify({
          provider: els.provider.value,
          api_key: els.apiKey.value.trim(),
          base_url: els.baseUrl.value.trim(),
          refresh: force,
        }),
      });
      setModels(data.models, data.note || "Начните вводить — список отфильтруется. Можно указать своё название.");
      if (!els.model.value.trim()) {
        els.model.value = provider.default_model || (data.models && data.models[0]) || "";
      }
      if (data.source === "provider") {
        els.modelHint.textContent = "Список загружен у провайдера. Можно ввести и своё название.";
        if (force) showToast(`Список моделей обновлён: ${data.models.length}`);
      } else if (force) {
        showToast(data.note || "Показан справочник моделей");
      }
    } catch (error) {
      els.modelHint.textContent = `Не удалось загрузить список: ${error.message}`;
      if (force) showToast("Не удалось обновить список моделей");
    } finally {
      els.refreshModels.classList.remove("is-loading");
      els.refreshModels.disabled = false;
    }
  }

  // -------------------------------------------------------- проверка полноты

  async function analyze() {
    let product;
    try {
      product = readProduct();
    } catch (error) {
      setStatus(error.message, "error");
      return;
    }

    els.analyzeBtn.disabled = true;
    try {
      const report = await api("/api/analyze", {
        method: "POST",
        body: JSON.stringify({ product }),
      });

      const lines = [];
      if (report.missing && report.missing.length) {
        lines.push(...report.missing.map((field) => `${field} — данных нет`));
      }
      if (report.warnings && report.warnings.length) {
        lines.push(...report.warnings);
      }

      if (lines.length) {
        renderList(els.gaps, `Семейство: ${report.family_title}. Требует уточнения:`, lines, "warn");
      } else {
        renderList(els.gaps, "Данные полные — можно генерировать карточку", [], "ok");
      }

      state.canGenerate = report.can_generate !== false;
      els.generateBtn.disabled = !state.canGenerate || state.busy;
      setStatus(
        state.canGenerate
          ? "Проверка выполнена"
          : "Данных слишком мало: добавьте характеристики или описание",
        state.canGenerate ? "" : "error"
      );
    } catch (error) {
      setStatus(error.message, "error");
    } finally {
      els.analyzeBtn.disabled = false;
    }
  }

  // ---------------------------------------------------------------- генерация

  function setBusy(busy) {
    state.busy = busy;
    els.generateBtn.disabled = busy || !state.canGenerate;
    els.spinner.hidden = !busy;
    els.generateLabel.textContent = busy ? "Формируем карточку…" : "Сформировать карточку";
    els.progress.hidden = !busy;

    if (busy) {
      const started = Date.now();
      els.progressText.textContent = "Отправляем данные в модель…";
      state.timer = window.setInterval(() => {
        const seconds = Math.floor((Date.now() - started) / 1000);
        const phase =
          seconds >= 15
            ? "Модель пишет текст, это может занять до минуты"
            : "Отправляем данные в модель…";
        els.progressText.textContent = `${phase} · ${seconds} с`;
      }, 1000);
    } else if (state.timer) {
      window.clearInterval(state.timer);
      state.timer = null;
    }
  }

  function showResult(data) {
    state.data = {
      markdown: data.markdown || "",
      text: data.text || "",
      json: data.json || JSON.stringify(data.card, null, 2),
    };
    state.hasResult = true;
    els.empty.hidden = true;
    els.copyBtn.disabled = false;
    els.downloadBtn.disabled = false;

    els.resultMd.textContent = state.data.markdown;
    els.resultText.textContent = state.data.text;
    els.resultJson.textContent = state.data.json;
    selectTab(state.tab);

    const meta = data.meta || {};
    const parts = [];
    if (meta.provider_title) parts.push(meta.provider_title);
    if (meta.model) parts.push(meta.model);
    if (meta.family) parts.push(meta.family);
    if (typeof meta.elapsed_ms === "number") parts.push(`${(meta.elapsed_ms / 1000).toFixed(1)} с`);
    if (meta.attempts) parts.push(`обращений к модели: ${meta.attempts}`);
    els.resultMeta.textContent = parts.join(" · ");
    els.resultMeta.hidden = parts.length === 0;

    const warnings = (data.card && data.card.warnings) || [];
    if (warnings.length) {
      renderList(els.resultWarnings, "Замечания проверок", warnings, "warn");
    } else {
      els.resultWarnings.hidden = true;
    }
  }

  async function generate() {
    let product;
    try {
      product = readProduct();
    } catch (error) {
      setStatus(error.message, "error");
      return;
    }
    if (state.busy) return;

    const payload = {
      product,
      llm: {
        provider: els.provider.value,
        model: els.model.value.trim(),
        base_url: els.baseUrl.value.trim(),
        api_key: els.apiKey.value.trim(),
      },
    };

    setBusy(true);
    setStatus("");
    try {
      const data = await api("/api/generate", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      showResult(data);
      setStatus("Готово", "ok");
      await saveSession();
    } catch (error) {
      setStatus(error.message, "error");
    } finally {
      setBusy(false);
    }
  }

  // ------------------------------------------------------------------ вкладки

  function selectTab(name) {
    state.tab = name;
    els.tabs.forEach((tab) => {
      const active = tab.dataset.tab === name;
      tab.classList.toggle("tab--active", active);
      tab.setAttribute("aria-selected", String(active));
    });
    if (!state.hasResult) {
      els.empty.hidden = false;
      els.resultMd.hidden = true;
      els.resultText.hidden = true;
      els.resultJson.hidden = true;
      return;
    }
    els.empty.hidden = true;
    els.resultMd.hidden = name !== "markdown";
    els.resultText.hidden = name !== "text";
    els.resultJson.hidden = name !== "json";
  }

  async function copyResult() {
    const text = state.data[state.tab] || "";
    if (!text) return;
    const label = els.copyBtn.textContent;
    try {
      await navigator.clipboard.writeText(text);
      els.copyBtn.textContent = "Скопировано ✓";
      els.copyBtn.classList.add("btn--copied");
      showToast("Текст скопирован в буфер обмена");
      setStatus("Скопировано в буфер обмена", "ok");
      window.setTimeout(() => {
        els.copyBtn.textContent = label;
        els.copyBtn.classList.remove("btn--copied");
      }, 1800);
    } catch (error) {
      showToast("Браузер запретил копирование — выделите текст вручную");
      setStatus("Браузер запретил доступ к буферу обмена — выделите текст вручную", "error");
    }
  }

  const DOWNLOAD_FORMATS = {
    md: { field: "markdown", ext: "md", type: "text/markdown;charset=utf-8" },
    txt: { field: "text", ext: "txt", type: "text/plain;charset=utf-8" },
    json: { field: "json", ext: "json", type: "application/json;charset=utf-8" },
  };

  function fileBaseName() {
    try {
      const product = readProduct();
      const name = String(product.name || "card")
        .replace(/[^\p{L}\p{N}]+/gu, "-")
        .replace(/^-|-$/g, "");
      return name || "card";
    } catch (error) {
      return "card";
    }
  }

  function download(format) {
    const config = DOWNLOAD_FORMATS[format];
    const content = state.data[config.field];
    if (!config || !content) return;

    const blob = new Blob([content], { type: config.type });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${fileBaseName()}.${config.ext}`;
    link.click();
    URL.revokeObjectURL(url);
    setStatus(`Скачано: .${config.ext}`, "ok");
  }

  function toggleDownloadMenu(show) {
    const willShow = show === undefined ? els.downloadMenu.hidden : show;
    els.downloadMenu.hidden = !willShow;
    els.downloadBtn.setAttribute("aria-expanded", String(willShow));
  }

  // ------------------------------------------------------------------ события

  els.exampleSelect.addEventListener("change", () => {
    const example = state.examples.find((item) => item.id === els.exampleSelect.value);
    if (example) {
      els.productJson.value = JSON.stringify(example.payload, null, 2);
      els.gaps.hidden = true;
      setStatus("");
    }
  });

  els.provider.addEventListener("change", () => {
    syncProviderFields();
    const provider = currentProvider();
    els.model.value = provider ? provider.default_model || "" : "";
    reloadModels();
  });

  els.model.addEventListener("input", () => {
    state.modelIndex = -1;
    renderModelList();
  });
  els.model.addEventListener("focus", () => {
    if (state.models.length) renderModelList();
  });
  els.model.addEventListener("blur", () => {
    window.setTimeout(closeModelList, 120);
  });
  els.model.addEventListener("keydown", (event) => {
    const items = filterModels(state.models, els.model.value.trim()).slice(0, 80);
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (!items.length) return;
      event.preventDefault();
      const delta = event.key === "ArrowDown" ? 1 : -1;
      state.modelIndex = (state.modelIndex + delta + items.length) % items.length;
      renderModelList();
      const active = els.modelList.querySelector(".combobox__item--active");
      if (active) active.scrollIntoView({ block: "nearest" });
    } else if (event.key === "Enter") {
      if (state.modelIndex >= 0 && items[state.modelIndex]) {
        event.preventDefault();
        chooseModel(items[state.modelIndex]);
      } else {
        closeModelList();
      }
    } else if (event.key === "Escape") {
      closeModelList();
    }
  });

  els.refreshModels.addEventListener("click", () => reloadModels(true));

  els.toggleKey.addEventListener("click", () => {
    const visible = els.apiKey.type === "text";
    els.apiKey.type = visible ? "password" : "text";
    els.toggleKey.setAttribute("aria-pressed", String(!visible));
    els.toggleKey.setAttribute("aria-label", visible ? "Показать ключ" : "Скрыть ключ");
    setHidden(els.eyeOn, !visible);
    setHidden(els.eyeOff, visible);
  });

  els.forgetKey.addEventListener("click", forgetKey);
  els.analyzeBtn.addEventListener("click", analyze);
  els.generateBtn.addEventListener("click", generate);
  els.copyBtn.addEventListener("click", copyResult);
  els.tabs.forEach((tab) => tab.addEventListener("click", () => selectTab(tab.dataset.tab)));

  els.downloadBtn.addEventListener("click", () => toggleDownloadMenu());
  els.downloadMenu.addEventListener("click", (event) => {
    const button = event.target.closest("[data-format]");
    if (!button) return;
    download(button.dataset.format);
    toggleDownloadMenu(false);
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".menu-wrap")) toggleDownloadMenu(false);
  });

  Promise.all([loadProviders(), loadExamples()])
    .then(() => loadSession())
    .then(() => reloadModels())
    .catch((error) => setStatus(`Не удалось загрузить данные интерфейса: ${error.message}`, "error"));
})();
