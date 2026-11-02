/* Интерфейс: разбор JSON, проверка полноты, генерация карточки. */
(() => {
  "use strict";

  const state = {
    providers: [],
    examples: [],
    markdown: "",
    json: "",
    tab: "markdown",
    canGenerate: true,
  };

  const $ = (id) => document.getElementById(id);

  const els = {
    exampleSelect: $("example-select"),
    productJson: $("product-json"),
    analyzeBtn: $("analyze-btn"),
    gaps: $("gaps"),
    provider: $("provider"),
    model: $("model"),
    baseUrl: $("base-url"),
    baseUrlField: $("base-url-field"),
    apiKey: $("api-key"),
    apiKeyField: $("api-key-field"),
    toggleKey: $("toggle-key"),
    generateBtn: $("generate-btn"),
    status: $("status"),
    empty: $("empty"),
    resultMd: $("result-md"),
    resultJson: $("result-json"),
    resultMeta: $("result-meta"),
    resultWarnings: $("result-warnings"),
    copyBtn: $("copy-btn"),
    downloadBtn: $("download-btn"),
    tabs: Array.from(document.querySelectorAll(".tab")),
  };

  // ------------------------------------------------------------------ утилиты

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
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
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
    els.model.placeholder = provider.default_model || "";
    if (!els.model.value) els.model.value = provider.default_model || "";
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
    els.model.value = "";
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
      els.generateBtn.disabled = !state.canGenerate;
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

  function showResult(data) {
    state.markdown = data.markdown || "";
    state.json = data.json || JSON.stringify(data.card, null, 2);
    els.empty.hidden = true;
    els.resultMd.hidden = false;
    els.resultJson.hidden = true;
    els.resultMd.textContent = state.markdown;
    els.resultJson.textContent = state.json;
    els.copyBtn.disabled = false;
    els.downloadBtn.disabled = false;

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

    const provider = currentProvider();
    const payload = {
      product,
      llm: {
        provider: els.provider.value,
        model: els.model.value.trim(),
        base_url: els.baseUrl.value.trim(),
        api_key: els.apiKey.value,
      },
    };

    els.generateBtn.disabled = true;
    setStatus(
      provider && provider.id === "mock"
        ? "Собираем карточку в офлайн-режиме…"
        : "Отправляем данные в модель — это может занять несколько секунд…"
    );

    try {
      const data = await api("/api/generate", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      showResult(data);
      setStatus("Готово", "ok");
    } catch (error) {
      setStatus(error.message, "error");
    } finally {
      els.generateBtn.disabled = !state.canGenerate;
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
    els.resultMd.hidden = name !== "markdown";
    els.resultJson.hidden = name !== "json";
  }

  async function copyResult() {
    const text = state.tab === "json" ? state.json : state.markdown;
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setStatus("Скопировано в буфер обмена", "ok");
    } catch (error) {
      setStatus("Браузер запретил доступ к буферу обмена — выделите текст вручную", "error");
    }
  }

  function downloadResult() {
    if (!state.markdown) return;
    let name = "product-card.md";
    try {
      const product = readProduct();
      name = `${String(product.name || "card").replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-|-$/g, "")}.md`;
    } catch (error) {
      /* имя не критично — оставим значение по умолчанию */
    }
    const blob = new Blob([state.markdown], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = name;
    link.click();
    URL.revokeObjectURL(url);
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
    els.model.value = "";
    syncProviderFields();
  });

  els.toggleKey.addEventListener("click", () => {
    const visible = els.apiKey.type === "text";
    els.apiKey.type = visible ? "password" : "text";
    els.toggleKey.setAttribute("aria-pressed", String(!visible));
    els.toggleKey.setAttribute("aria-label", visible ? "Показать ключ" : "Скрыть ключ");
  });

  els.analyzeBtn.addEventListener("click", analyze);
  els.generateBtn.addEventListener("click", generate);
  els.copyBtn.addEventListener("click", copyResult);
  els.downloadBtn.addEventListener("click", downloadResult);
  els.tabs.forEach((tab) => tab.addEventListener("click", () => selectTab(tab.dataset.tab)));

  Promise.all([loadProviders(), loadExamples()])
    .then(() => setStatus(""))
    .catch((error) => setStatus(`Не удалось загрузить данные интерфейса: ${error.message}`, "error"));
})();
