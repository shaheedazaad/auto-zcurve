(() => {
  let syncRunReadiness = () => {};
  let isRunning = false;

  const themeStorageKey = "auto-zcurve-theme";
  const systemTheme = window.matchMedia("(prefers-color-scheme: dark)");
  const savedTheme = () => {
    try {
      const value = localStorage.getItem(themeStorageKey) || "system";
      return ["system", "light", "dark"].includes(value) ? value : "system";
    } catch (_) {
      return "system";
    }
  };
  /* Status messages: only ever toggle the state class. Assigning .className
     wholesale (as this file used to, in 26 places) also wiped the structural
     classes the layout depends on. */
  const MESSAGE_STATES = ["u-success", "u-error", "u-warning"];
  const setMessageState = (element, value) => {
    if (!element) return;
    element.classList.remove(...MESSAGE_STATES);
    const text = String(value || "");
    if (text.includes("success-text")) element.classList.add("u-success");
    else if (text.includes("error-text")) element.classList.add("u-error");
    else if (text.includes("warning-text")) element.classList.add("u-warning");
  };

  const applyTheme = (preference, { persist = false } = {}) => {
    const resolved = preference === "system" ? (systemTheme.matches ? "dark" : "light") : preference;
    document.documentElement.classList.toggle("dark", resolved === "dark");
    document.documentElement.dataset.themePreference = preference;
    document.documentElement.style.colorScheme = resolved;
    if (persist) {
      try { localStorage.setItem(themeStorageKey, preference); } catch (_) {}
    }
    document.querySelectorAll(".js-theme-option").forEach((option) => {
      option.setAttribute("aria-checked", option.dataset.themeValue === preference ? "true" : "false");
    });
  };
  applyTheme(savedTheme());
  document.querySelectorAll(".js-theme-option").forEach((option) => {
    option.addEventListener("click", () => applyTheme(option.dataset.themeValue, { persist: true }));
  });
  systemTheme.addEventListener?.("change", () => {
    if (savedTheme() === "system") applyTheme("system");
  });

  const confirmDestructive = (message, options = {}) => Promise.resolve(
    window.confirm(`${options.title || "Clear current results?"}\n\n${message}`)
  );

  const postForm = async (url, body) => {
    const response = await fetch(url, { method: "POST", body });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || "The request could not be completed.");
    return payload;
  };

  const body = document.body;
  const projectId = body.dataset.projectId;
  const page = body.dataset.page;
  const token = body.dataset.token;
  const escapeHtml = (value) => {
    const div = document.createElement("div");
    div.textContent = String(value);
    return div.innerHTML.replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  };
  const highlightJson = (value) => {
    const escaped = escapeHtml(value);
    return escaped.replace(/(&quot;.*?&quot;)(\s*:)|(&quot;.*?&quot;)|(\b(?:true|false|null)\b)|(-?\b\d+(?:\.\d+)?\b)/g,
      (match, key, colon, string, literal, number) => {
        if (key) return `<span class="json-key">${key}</span>${colon}`;
        if (string) return `<span class="json-string">${string}</span>`;
        if (literal) return `<span class="json-literal">${literal}</span>`;
        return `<span class="json-number">${number}</span>`;
      });
  };
  const formatResponse = (value) => {
    try {
      const parsed = JSON.parse(value);
      const pretty = JSON.stringify(parsed, null, 2);
      return `<span class="json-viewer">${highlightJson(pretty)}</span>`;
    } catch (_) {
      return escapeHtml(value);
    }
  };
  const articleStatusBadge = (status, jsonRepaired) => {
    const states = {
      ok: { variant: "success", label: jsonRepaired ? "JSON repaired" : "Done" },
      needs_review: { variant: "warning", label: "Needs review" },
      error: { variant: "destructive", label: "Failed" },
      ready: { variant: "outline", label: "Ready" },
    };
    const state = states[status] || states.ready;
    return `<span class="badge article-status" data-variant="${state.variant}">${state.label}</span>`;
  };
  const articleMessage = (article) => {
    if (article.error) return `<small class="article-message u-error">${escapeHtml(article.error)}</small>`;
    const parserWarning = article.warning ? `<small class="article-message u-warning">${escapeHtml(article.warning)}</small>` : "";
    if (article.status === "needs_review") {
      const reasons = (article.review_reasons || []).join("; ") || "no reason was recorded";
      const retry = article.retry_error ? ` · Last retry failed: ${article.retry_error}` : "";
      return `<small class="article-message u-warning">Needs review: ${escapeHtml(reasons + retry)}</small>${parserWarning}`;
    }
    return parserWarning;
  };
  const articleRowState = (status) => ({ ok: "ok", error: "error", needs_review: "needs-review" })[status] || "ready";
  const articleFileIcon = '<span class="file-icon article-file-icon" aria-hidden="true"><svg width="16" height="16" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" fill="none"><path d="M14 3v4a1 1 0 0 0 1 1h4"/><path d="M5 12v-7a2 2 0 0 1 2 -2h7l5 5v12a2 2 0 0 1 -2 2h-5"/><path d="M3 15h6v4a2 2 0 0 1 -2 2h-2a2 2 0 0 1 -2 -2z"/><path d="M6 15v6"/></svg></span>';
  if (!token) return;

  if (page === "settings") {
    const settingsLinks = Array.from(document.querySelectorAll(".settings-nav a"));
    const syncSettingsNavigation = () => {
      settingsLinks.forEach((link) => {
        if (link.hash === (location.hash || "#extractors")) link.setAttribute("aria-current", "location");
        else link.removeAttribute("aria-current");
      });
    };
    window.addEventListener("hashchange", syncSettingsNavigation);
    syncSettingsNavigation();
    document.querySelectorAll(".js-app-settings").forEach((settingsForm) => {
      settingsForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (!settingsForm.reportValidity()) return;
        const button = settingsForm.querySelector("button[type=submit]");
        const message = settingsForm.querySelector(".form-message");
        button.disabled = true;
        message.textContent = "Saving…";
        setMessageState(message, "form-message settings-message mb-0");
        try {
          await postForm(settingsForm.action, new FormData(settingsForm));
          message.textContent = "Settings saved.";
          setMessageState(message, "form-message settings-message mb-0 success-text");
        } catch (error) {
          message.textContent = error.message;
          setMessageState(message, "form-message settings-message mb-0 error-text");
        } finally {
          button.disabled = false;
        }
      });
    });

    const listMessage = document.querySelector("[data-extractor-list-message]");
    const reportList = (text, state) => {
      if (!listMessage) return;
      listMessage.textContent = text;
      setMessageState(listMessage, `form-message ${state}`);
    };
    const reloadSoon = (delay = 600) => window.setTimeout(() => window.location.reload(), delay);

    const extractorDialog = document.querySelector("#extractor-dialog");
    const extractorForm = extractorDialog?.querySelector(".js-extractor-form");
    if (extractorDialog && extractorForm) {
      const field = (name) => extractorForm.elements.namedItem(name);
      const providerField = field("provider");
      const modelField = field("model");
      const message = extractorForm.querySelector(".form-message");
      const modelOptions = extractorForm.querySelector("#extractor-model-options");
      const modelHint = extractorForm.querySelector("[data-model-hint]");
      const loadModels = extractorForm.querySelector("[data-load-models]");
      const endpointList = extractorForm.querySelector("#extractor-openrouter-endpoints");
      const endpointStatus = extractorForm.querySelector("[data-openrouter-provider-status]");
      const keyActions = extractorForm.querySelector("[data-key-actions]");
      const keyLabel = extractorForm.querySelector("[data-key-label]");
      const modelHints = {
        gemini: "A Gemini model ID, such as gemini-3.6-flash. Gemini receives the original PDF, or an XML file as text.",
        openrouter: "An exact OpenRouter model ID (vendor/model). It must support structured outputs.",
        openai_compatible: "The exact model ID your server serves.",
      };
      let editing = null;
      // Only the latest model-list request may update the suggestions, so a slow
      // answer for an earlier provider or extractor can't overwrite them.
      let modelRequest = 0;

      const showProviderFields = () => {
        const provider = providerField.value;
        modelRequest += 1;
        loadModels.disabled = false;
        extractorForm.querySelectorAll("[data-provider-fields]").forEach((group) => {
          const active = group.dataset.providerFields === provider;
          group.hidden = !active;
          group.querySelectorAll("input, select").forEach((input) => { input.disabled = !active; });
        });
        modelHint.textContent = modelHints[provider];
        modelOptions.innerHTML = "";
        loadModels.hidden = provider === "openai_compatible";
        modelField.placeholder = provider === "openrouter" ? "vendor/model" : "Model ID";
      };

      // Only the latest provider-list request may update the list, so a slow
      // answer for an earlier model or extractor can't overwrite it.
      let endpointRequest = 0;
      const openExtractor = (extractor) => {
        editing = extractor;
        endpointRequest += 1;
        extractorForm.reset();
        modelOptions.innerHTML = "";
        endpointList.innerHTML = "";
        message.textContent = "";
        setMessageState(message, "form-message");
        extractorForm.action = extractor ? `/${token}/extractors/${extractor.id}` : extractorForm.dataset.createAction;
        extractorDialog.querySelector("[data-extractor-dialog-title]").textContent = extractor ? `Edit ${extractor.name}` : "Add extractor";
        providerField.disabled = Boolean(extractor);
        extractorForm.querySelector("[data-provider-locked]").hidden = !extractor;
        if (extractor) {
          Object.entries(extractor.settings).forEach(([name, value]) => {
            const input = field(name);
            if (!input) return;
            input.value = name === "openrouter_allow_fallbacks" ? (value ? "allow" : "deny") : (value ?? "");
          });
        }
        const hasKey = Boolean(extractor?.has_key);
        keyActions.hidden = !hasKey;
        const keyBadge = keyActions.querySelector("[data-key-badge]");
        if (keyBadge) {
          const persisted = extractor?.key_saved !== false;
          keyBadge.textContent = persisted ? "Key saved" : "Key for this session";
          keyBadge.dataset.variant = persisted ? "success" : "outline";
        }
        keyLabel.textContent = hasKey ? "Replace API key (optional)" : "API key";
        showProviderFields();
        extractorDialog.showModal();
        field("name").focus();
      };

      document.querySelector("[data-new-extractor]")?.addEventListener("click", () => openExtractor(null));
      document.querySelectorAll("[data-edit-extractor]").forEach((button) => {
        button.addEventListener("click", () => openExtractor(JSON.parse(button.closest("[data-extractor-row]").dataset.extractor)));
      });
      extractorForm.querySelector("[data-close-extractor-dialog]").addEventListener("click", () => extractorDialog.close());
      providerField.addEventListener("change", showProviderFields);

      /* Loading suggestions contacts the provider and may open the credential
         store, so it only happens when asked for. A saved extractor uses its
         saved key; otherwise the key typed into the form is sent for this one
         request and never stored (OpenRouter's catalog also loads without one). */
      loadModels.addEventListener("click", async () => {
        const request = ++modelRequest;
        loadModels.disabled = true;
        modelHint.textContent = "Loading models…";
        try {
          let response;
          if (editing?.has_key) {
            response = await fetch(`/${token}/api/extractors/${encodeURIComponent(editing.id)}/models`, { cache: "no-store" });
          } else {
            const data = new FormData();
            data.set("provider", providerField.value);
            data.set("api_key", field("api_key").value.trim());
            response = await fetch(`/${token}/api/models`, { method: "POST", body: data, cache: "no-store" });
          }
          const payload = await response.json().catch(() => ({}));
          if (request !== modelRequest) return;
          if (!response.ok) throw new Error(payload.detail || "Models could not be loaded.");
          modelOptions.innerHTML = payload.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join("");
          modelHint.textContent = payload.length
            ? `${payload.length} model${payload.length === 1 ? "" : "s"} available. Start typing to filter.`
            : "The provider returned no models for this key.";
        } catch (error) {
          if (request === modelRequest) modelHint.textContent = error.message;
        } finally {
          if (request === modelRequest) loadModels.disabled = false;
        }
      });

      modelField.addEventListener("change", async () => {
        const model = modelField.value.trim();
        if (providerField.value !== "openrouter" || !model) return;
        const request = ++endpointRequest;
        // The previous model's providers may not serve this one.
        endpointList.innerHTML = "";
        endpointStatus.textContent = "Loading providers…";
        const query = new URLSearchParams({ model, extractor_id: editing?.id || "" });
        try {
          const response = await fetch(`/${token}/api/models/openrouter/endpoints?${query}`, { cache: "no-store" });
          const payload = await response.json().catch(() => ({}));
          if (request !== endpointRequest) return;
          if (!response.ok) throw new Error(payload.detail || "Providers could not be loaded.");
          endpointList.innerHTML = payload.map((item) => `<option value="${escapeHtml(item.tag)}">${escapeHtml(item.name)}</option>`).join("");
          endpointStatus.textContent = payload.length
            ? `${payload.length} provider${payload.length === 1 ? " serves" : "s serve"} this model with structured output. Leave empty to let OpenRouter choose.`
            : "No provider currently serves this model with structured output.";
        } catch (error) {
          if (request === endpointRequest) endpointStatus.textContent = error.message;
        }
      });

      extractorForm.querySelector("[data-remove-key]").addEventListener("click", async () => {
        const confirmed = await confirmDestructive(`Remove the saved API key for “${editing.name}”?`, { title: "Remove API key?" });
        if (!confirmed) return;
        try {
          await postForm(`/${token}/extractors/${encodeURIComponent(editing.id)}/key/delete`, new FormData());
          message.textContent = "Key removed.";
          setMessageState(message, "form-message success-text");
          reloadSoon();
        } catch (error) {
          message.textContent = error.message;
          setMessageState(message, "form-message error-text");
        }
      });

      extractorForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (!extractorForm.reportValidity()) return;
        const button = extractorForm.querySelector("button[type=submit]");
        const data = new FormData(extractorForm);
        const apiKey = String(data.get("api_key") || "").trim();
        button.disabled = true;
        message.textContent = "Saving…";
        setMessageState(message, "form-message");
        try {
          if (editing) data.delete("api_key");
          const result = await postForm(extractorForm.action, data);
          const warnings = [result.warning];
          if (editing && apiKey) {
            const keyData = new FormData();
            keyData.set("api_key", apiKey);
            warnings.push((await postForm(`/${token}/extractors/${encodeURIComponent(editing.id)}/key`, keyData)).warning);
          }
          const warning = warnings.filter(Boolean).join(" ");
          field("api_key").value = "";
          message.textContent = warning || "Extractor saved.";
          setMessageState(message, `form-message ${warning ? "warning-text" : "success-text"}`);
          reloadSoon(warning ? 2400 : 600);
        } catch (error) {
          message.textContent = error.message;
          setMessageState(message, "form-message error-text");
        } finally {
          button.disabled = false;
        }
      });

      const linked = location.hash.startsWith("#extractor-") ? document.getElementById(location.hash.slice(1)) : null;
      if (linked?.dataset.extractor) {
        // Clear the hash so reloading after a save doesn't reopen the dialog.
        history.replaceState(null, "", location.pathname + location.search);
        openExtractor(JSON.parse(linked.dataset.extractor));
      }
    }

    document.querySelectorAll(".js-extractor-action").forEach((form) => {
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        try {
          const result = await postForm(form.action, new FormData(form));
          // Reopen the page on the copy so its edit dialog opens straight away.
          window.location.hash = `extractor-${result.extractor.id}`;
          reloadSoon(0);
        } catch (error) {
          reportList(error.message, "error-text");
        }
      });
    });

    document.querySelectorAll(".js-delete-extractor").forEach((form) => {
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const name = form.dataset.extractorName || "this extractor";
        let using;
        try {
          const response = await fetch(`/${token}/api/extractors/${encodeURIComponent(form.dataset.extractorId)}/projects`, { cache: "no-store" });
          const payload = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(payload.detail || "The projects using this extractor could not be listed.");
          using = payload.projects || [];
        } catch (error) {
          reportList(error.message, "error-text");
          return;
        }
        const affects = using.length
          ? `These projects use it and will need another extractor: ${using.join(", ")}.`
          : "No projects use it.";
        const confirmed = await confirmDestructive(
          `Delete “${name}” and its saved API key? ${affects}`,
          { title: "Delete this extractor?" }
        );
        if (!confirmed) return;
        try {
          const result = await postForm(form.action, new FormData(form));
          const projects = result.projects || [];
          const notes = [
            result.warning,
            projects.length ? `Choose another extractor for: ${projects.join(", ")}.` : "",
          ].filter(Boolean).join(" ");
          reportList(`Deleted “${name}”. ${notes}`.trim(), notes ? "warning-text" : "success-text");
          reloadSoon(notes ? 3000 : 600);
        } catch (error) {
          reportList(error.message, "error-text");
        }
      });
    });
    return;
  }

  const newProjectDialog = document.querySelector("#new-project-dialog");
  document.querySelector("[data-new-project-toggle]")?.addEventListener("click", () => {
    newProjectDialog?.showModal();
    newProjectDialog?.querySelector("[name=name]")?.focus();
  });
  document.querySelector("[data-close-new-project]")?.addEventListener("click", () => {
    newProjectDialog?.close();
  });

  if (!projectId) {
    const list = document.querySelector("[data-project-list]");
    const search = document.querySelector(".js-project-search");
    const pagination = document.querySelector("[data-project-pagination]");
    const count = document.querySelector("[data-project-count]");
    const sort = document.querySelector("#project-sort");
    const pageSize = 10;
    let projects = [];
    let filteredProjects = [];
    let page = 1;

    /* Must stay byte-identical to the row markup in home.html -- this replaces
       the server-rendered list on load, so any difference reads as a flicker. */
    const projectRow = (project) => {
      let badge = '<span class="badge" data-variant="outline">In progress</span>';
      if (project.has_report) {
        badge = '<span class="badge" data-variant="success">Report ready</span>';
      } else if (project.failed_count) {
        badge = `<span class="badge" data-variant="destructive">${project.failed_count} failed</span>`;
      }
      const articleLabel = project.pdf_count === 1 ? "article" : "articles";
      return `
        <a class="row project-home-row" data-project-row href="/${encodeURIComponent(token)}/projects/${encodeURIComponent(project.id)}">
<span class="project-folder-icon"><svg class="workflow-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 7V5h6l2 2h10v13H3V7Z" /></svg></span>
          <span class="row-main">
            <strong class="row-title">${escapeHtml(project.name)}</strong>
            <span class="row-meta">${project.pdf_count} ${articleLabel} · ${project.total_tokens} tokens</span>
          </span>
          ${badge}
        </a>`;
    };

    const renderProjects = () => {
      const totalPages = Math.max(1, Math.ceil(filteredProjects.length / pageSize));
      page = Math.min(page, totalPages);
      const visible = filteredProjects.slice((page - 1) * pageSize, page * pageSize);
      list.innerHTML = visible.length
        ? visible.map(projectRow).join("")
        : `<div class="empty"><p class="empty-title">${projects.length ? 'No matching projects' : 'No projects yet'}</p><p class="empty-text">${projects.length ? 'Try a different project name.' : 'Create a project to add journal articles and start extracting results.'}</p></div>`;
      count.textContent = `${filteredProjects.length} project${filteredProjects.length === 1 ? "" : "s"}`;
      pagination.hidden = filteredProjects.length === 0;
      pagination.querySelector("[data-project-page]").textContent = `${(page - 1) * pageSize + 1}–${Math.min(page * pageSize, filteredProjects.length)} of ${filteredProjects.length} · Page ${page} of ${totalPages}`;
      pagination.querySelector(".js-project-previous").disabled = page <= 1;
      pagination.querySelector(".js-project-next").disabled = page >= totalPages;
    };

    const applyProjectSearch = () => {
      const query = search.value.trim().toLocaleLowerCase();
      filteredProjects = projects.filter((project) => project.name.toLocaleLowerCase().includes(query));
      if (sort?.value === "name") filteredProjects.sort((a, b) => a.name.localeCompare(b.name));
      else if (sort?.value === "oldest") filteredProjects.reverse();
      page = 1;
      renderProjects();
    };

    search?.addEventListener("input", applyProjectSearch);
    sort?.addEventListener("change", applyProjectSearch);
    pagination?.querySelector(".js-project-previous")?.addEventListener("click", () => {
      if (page > 1) page -= 1;
      renderProjects();
    });
    pagination?.querySelector(".js-project-next")?.addEventListener("click", () => {
      const totalPages = Math.max(1, Math.ceil(filteredProjects.length / pageSize));
      if (page < totalPages) page += 1;
      renderProjects();
    });

    fetch(`/${token}/api/projects`, { cache: "no-store" })
      .then((response) => response.ok ? response.json() : Promise.reject(new Error("Project list unavailable")))
      .then((payload) => {
        projects = payload;
        applyProjectSearch();
      })
      .catch(() => {});
    return;
  }

  const base = `/${token}/projects/${projectId}`;

  const tabButtons = Array.from(document.querySelectorAll("[data-tab-target]"));
  const tabPanels = Array.from(document.querySelectorAll("[data-tab-panel]"));
  if (tabButtons.length) {
    const validTabs = tabPanels.length
      ? tabPanels.map((panel) => panel.dataset.tabPanel)
      : tabButtons.map((button) => button.dataset.tabTarget);

    const activateTab = (name, { focus = false } = {}) => {
      if (!validTabs.includes(name)) return;
      tabButtons.forEach((button) => {
        const selected = button.dataset.tabTarget === name;
        button.setAttribute("aria-selected", selected ? "true" : "false");
        button.classList.toggle("is-active", selected);
        if (selected && focus) button.focus();
      });
      tabPanels.forEach((panel) => {
        panel.hidden = panel.dataset.tabPanel !== name;
      });
    };

    tabButtons.forEach((button) => {
      button.addEventListener("click", () => {
        const target = button.dataset.tabTarget;
        if (tabPanels.length) {
          activateTab(target);
          history.replaceState?.(null, "", `${base}#${target}`);
          window.scrollTo(0, 0);
        } else {
          window.location.href = `${base}#${target}`;
        }
      });
    });

    document.querySelector("[data-project-tabs]")?.addEventListener("keydown", (event) => {
      const currentIndex = tabButtons.indexOf(document.activeElement);
      if (currentIndex === -1) return;
      let nextIndex = null;
      if (event.key === "ArrowRight" || event.key === "ArrowDown") nextIndex = (currentIndex + 1) % tabButtons.length;
      else if (event.key === "ArrowLeft" || event.key === "ArrowUp") nextIndex = (currentIndex - 1 + tabButtons.length) % tabButtons.length;
      else if (event.key === "Home") nextIndex = 0;
      else if (event.key === "End") nextIndex = tabButtons.length - 1;
      if (nextIndex === null) return;
      event.preventDefault();
      const target = tabButtons[nextIndex].dataset.tabTarget;
      if (tabPanels.length) activateTab(target, { focus: true });
      else tabButtons[nextIndex].focus();
    });

    if (tabPanels.length) {
      const hash = window.location.hash.replace("#", "");
      const hasResults = document.body.dataset.hasResults === "true";
      activateTab(validTabs.includes(hash) ? hash : (hasResults ? "overview" : "sources"));
    }
  }

  const renameDialog = document.querySelector("#rename-project-dialog");
  document.querySelector("[data-open-rename-dialog]")?.addEventListener("click", () => {
    const input = renameDialog?.querySelector("[name=name]");
    renameDialog?.showModal();
    input?.focus();
    input?.select();
  });
  document.querySelector("[data-close-rename-dialog]")?.addEventListener("click", () => {
    renameDialog?.close();
  });
  document.querySelector(".js-rename-project")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector("button[type=submit]");
    const message = form.querySelector(".form-message");
    button.disabled = true;
    message.textContent = "Saving…";
    setMessageState(message, "form-message");
    try {
      await postForm(form.action, new FormData(form));
      window.location.reload();
    } catch (error) {
      message.textContent = error.message;
      setMessageState(message, "form-message error-text");
      button.disabled = false;
    }
  });

  document.querySelector(".js-reset-project")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const confirmed = await confirmDestructive(
      "This permanently removes all extraction results, reports, plots, and processing logs. Source files, instructions, and the extraction schema will be kept.",
      { title: "Clear all project outputs?", confirmLabel: "Clear all outputs" }
    );
    if (!confirmed) return;
    try {
      await postForm(form.action, new FormData(form));
      window.location.reload();
    } catch (error) {
      window.alert(error.message);
    }
  });

  document.querySelector(".js-delete-project")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const name = form.dataset.projectName || "this project";
    const confirmed = await confirmDestructive(
      `This permanently deletes “${name}”, including its source files, settings, and all results. This cannot be undone.`,
      { title: "Delete this project?", confirmLabel: "Delete project" }
    );
    if (!confirmed) return;
    try {
      await postForm(form.action, new FormData(form));
      window.location.assign(`/${token}/`);
    } catch (error) {
      window.alert(error.message);
    }
  });

  const schemaForm = document.querySelector(".js-schema-form");
  if (schemaForm) {
    const input = schemaForm.querySelector(".yaml-input");
    const highlight = schemaForm.querySelector(".yaml-highlight");
    const code = highlight.querySelector("code");
    const button = schemaForm.querySelector(".js-save-schema");
    const message = schemaForm.querySelector(".schema-message");

    const commentStart = (line) => {
      let quote = null;
      let escaped = false;
      for (let index = 0; index < line.length; index += 1) {
        const character = line[index];
        if (quote === '"' && character === "\\" && !escaped) {
          escaped = true;
          continue;
        }
        if ((character === '"' || character === "'") && !escaped) {
          quote = quote === character ? null : (quote || character);
        }
        if (character === "#" && quote === null && (index === 0 || /\s/.test(line[index - 1]))) {
          return index;
        }
        escaped = false;
      }
      return -1;
    };

    const highlightValue = (value) => {
      const match = value.match(/^(\s*)(.*)$/);
      const prefix = escapeHtml(match[1]);
      const scalar = match[2];
      if (/^(true|false|null|~)$/i.test(scalar)) {
        return `${prefix}<span class="yaml-literal">${escapeHtml(scalar)}</span>`;
      }
      if (/^[-+]?(?:\d+\.?\d*|\.\d+)$/.test(scalar)) {
        return `${prefix}<span class="yaml-number">${escapeHtml(scalar)}</span>`;
      }
      if (/^(?:"(?:[^"\\]|\\.)*"|'[^']*')$/.test(scalar)) {
        return `${prefix}<span class="yaml-string">${escapeHtml(scalar)}</span>`;
      }
      if (/^[>|][-+]?\d*$/.test(scalar)) {
        return `${prefix}<span class="yaml-literal">${escapeHtml(scalar)}</span>`;
      }
      return escapeHtml(value);
    };

    const highlightLine = (line) => {
      const commentIndex = commentStart(line);
      const body = commentIndex >= 0 ? line.slice(0, commentIndex) : line;
      const comment = commentIndex >= 0 ? line.slice(commentIndex) : "";
      const key = body.match(/^(\s*)(-\s+)?([A-Za-z_][A-Za-z0-9_.-]*)(\s*:\s*)(.*)$/);
      let rendered;
      if (key) {
        rendered = `${escapeHtml(key[1])}${escapeHtml(key[2] || "")}<span class="yaml-key">${escapeHtml(key[3])}</span><span class="yaml-punctuation">${escapeHtml(key[4])}</span>${highlightValue(key[5])}`;
      } else {
        rendered = highlightValue(body);
      }
      if (comment) rendered += `<span class="yaml-comment">${escapeHtml(comment)}</span>`;
      return rendered;
    };

    const renderHighlight = () => {
      code.innerHTML = `${input.value.split("\n").map(highlightLine).join("\n")}\n`;
      highlight.scrollTop = input.scrollTop;
      highlight.scrollLeft = input.scrollLeft;
    };

    input.addEventListener("input", renderHighlight);
    input.addEventListener("scroll", () => {
      highlight.scrollTop = input.scrollTop;
      highlight.scrollLeft = input.scrollLeft;
    });
    input.addEventListener("keydown", (event) => {
      if (event.key !== "Tab") return;
      event.preventDefault();
      const start = input.selectionStart;
      input.setRangeText("  ", start, input.selectionEnd, "end");
      renderHighlight();
    });
    renderHighlight();

    schemaForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (input.value === input.defaultValue) {
        message.textContent = "The schema is unchanged.";
        setMessageState(message, "form-message schema-message success-text");
        return;
      }

      const data = new FormData(schemaForm);
      if (schemaForm.dataset.hasResults === "true") {
        const confirmed = await confirmDestructive(
          "Changing the extraction schema will permanently delete the current report, extraction results, and processing logs. Every article will need to be processed again. Continue?"
        );
        if (!confirmed) return;
        data.set("confirm_reset", "yes");
      }

      button.disabled = true;
      message.textContent = "Validating…";
      setMessageState(message, "form-message schema-message");
      try {
        const result = await postForm(schemaForm.action, data);
        input.defaultValue = input.value;
        schemaForm.dataset.hasResults = "false";
        message.textContent = result.reset
          ? "Schema saved. Previous results were cleared; run the analysis again."
          : "Schema is valid and saved.";
        setMessageState(message, "form-message schema-message success-text");
        if (result.reset) window.setTimeout(() => window.location.reload(), 800);
      } catch (error) {
        message.textContent = error.message;
        setMessageState(message, "form-message schema-message error-text");
      } finally {
        button.disabled = false;
      }
    });
  }

  const pageSize = 10;
  let articlePage = 1;
  let currentArticles = [];
  let articleSortKey = "name";
  let articleSortDirection = 1;

  const renderArticlePage = () => {
    const tbody = document.querySelector("[data-article-body]");
    if (!tbody) return;
    const totalPages = Math.max(1, Math.ceil(currentArticles.length / pageSize));
    articlePage = Math.min(articlePage, totalPages);
    const start = (articlePage - 1) * pageSize;
    const sortedArticles = [...currentArticles].sort((left, right) => {
      const a = left[articleSortKey] ?? "";
      const b = right[articleSortKey] ?? "";
      const result = typeof a === "number" || typeof b === "number"
        ? Number(a || 0) - Number(b || 0)
        : String(a).localeCompare(String(b), undefined, { sensitivity: "base" });
      return result * articleSortDirection;
    });
    const visibleArticles = sortedArticles.slice(start, start + pageSize);
    if (!visibleArticles.length) {
      tbody.innerHTML = '<tr class="empty-row"><td colspan="5" class="table-empty-cell">No articles yet. Add PDF or XML files from the Sources tab to begin.</td></tr>';
    } else {
      tbody.innerHTML = visibleArticles.map((article) => `
        <tr class="article-row article-row-${articleRowState(article.status)}">
          <td>${articleFileIcon}<span><strong class="article-name" title="${escapeHtml(article.name)}">${escapeHtml(article.name)}</strong>${articleMessage(article)}</span></td>
          <td>${articleStatusBadge(article.status, article.json_repaired)}</td>
          <td>${article.effects || "—"}</td>
          <td class="u-muted u-sm u-nowrap">${article.input_tokens || 0} in · ${article.output_tokens || 0} out</td>
          <td><div class="cluster">${article.raw_response ? `<button class="btn js-view-response" data-variant="outline" data-size="sm" type="button" data-source-name="${escapeHtml(article.name)}">View output</button>` : ""}${article.status !== "ready" ? `<button class="btn js-delete-response" data-variant="ghost" data-size="sm" type="button" data-source-name="${escapeHtml(article.name)}">Delete</button>` : ""}</div></td>
        </tr>`).join("");
    }
    const pagination = document.querySelector("[data-article-pagination]");
    if (pagination) {
      pagination.hidden = currentArticles.length <= pageSize;
      pagination.querySelector("[data-page-status]").textContent = `Page ${articlePage} of ${totalPages}`;
      pagination.querySelector(".js-page-previous").disabled = articlePage <= 1;
      pagination.querySelector(".js-page-next").disabled = articlePage >= totalPages;
    }
  };

  let sourcePage = 1;
  let recentUploads = new Set();
  const renderSourcePage = () => {
    const tbody = document.querySelector("[data-source-body]");
    if (!tbody) return;
    const sources = [...currentArticles].sort((left, right) =>
      String(right.added_at || "").localeCompare(String(left.added_at || "")) || left.name.localeCompare(right.name));
    const totalPages = Math.max(1, Math.ceil(sources.length / pageSize));
    sourcePage = Math.min(sourcePage, totalPages);
    const visible = sources.slice((sourcePage - 1) * pageSize, sourcePage * pageSize);
    tbody.innerHTML = visible.length
      ? visible.map((source) => `
        <tr class="source-row${recentUploads.has(source.name) ? " source-row-new" : ""}">
          <td>${articleFileIcon}<strong class="article-name" title="${escapeHtml(source.name)}">${escapeHtml(source.name)}</strong>${recentUploads.has(source.name) ? ' <span class="badge" data-variant="success">New</span>' : ""}</td>
          <td class="u-muted u-sm u-nowrap">${escapeHtml(source.size_display || "")}</td>
          <td class="u-muted u-sm u-nowrap">${escapeHtml(source.added_display || "")}</td>
          <td><a class="btn" data-variant="outline" data-size="sm" href="/${token}/projects/${projectId}/sources/${encodeURIComponent(source.name)}" target="_blank" rel="noopener">Open</a></td>
        </tr>`).join("")
      : '<tr class="empty-row"><td colspan="4" class="table-empty-cell">No articles yet. Drop PDF or XML files above to add them to this project.</td></tr>';
    const pagination = document.querySelector("[data-source-pagination]");
    if (pagination) {
      pagination.hidden = sources.length <= pageSize;
      pagination.querySelector("[data-source-page-status]").textContent = `Page ${sourcePage} of ${totalPages}`;
      pagination.querySelector(".js-source-previous").disabled = sourcePage <= 1;
      pagination.querySelector(".js-source-next").disabled = sourcePage >= totalPages;
    }
  };
  document.querySelector(".js-source-previous")?.addEventListener("click", () => {
    if (sourcePage > 1) sourcePage -= 1;
    renderSourcePage();
  });
  document.querySelector(".js-source-next")?.addEventListener("click", () => {
    sourcePage += 1;
    renderSourcePage();
  });

  const renderArticles = (project) => {
    currentArticles = project.articles;
    renderArticlePage();
    renderSourcePage();
    document.querySelector("[data-pdf-count]").textContent = `${project.pdf_count} file${project.pdf_count === 1 ? "" : "s"}`;
    document.querySelector("[data-token-count]").textContent = project.total_tokens;
    const extracted = project.successful_count + (project.needs_review_count || 0);
    const articleCount = document.querySelector("[data-article-count]");
    if (articleCount) articleCount.textContent = `${project.pdf_count} article${project.pdf_count === 1 ? "" : "s"}`;
    const processedCount = document.querySelector("[data-processed-count]");
    if (processedCount) {
      processedCount.textContent = `${extracted} processed${project.needs_review_count ? ` (${project.needs_review_count} need review)` : ""}`;
    }
    const currentRunButton = document.querySelector(".js-run");
    currentRunButton.dataset.hasPdfs = project.pdf_count > 0 ? "true" : "false";
    const remaining = Math.max(0, project.pdf_count - extracted);
    currentRunButton.innerHTML = extracted > 0 && remaining > 0
      ? `Continue processing ${remaining} article${remaining === 1 ? "" : "s"} <span>→</span>`
      : "Run analysis <span>→</span>";
    const currentRetryButton = document.querySelector(".js-retry");
    currentRetryButton.dataset.hasFailures = project.failed_count > 0 || project.needs_review_count > 0 ? "true" : "false";
    syncRunReadiness();
  };

  document.querySelector(".js-page-previous")?.addEventListener("click", () => {
    if (articlePage > 1) articlePage -= 1;
    renderArticlePage();
  });
  document.querySelectorAll(".js-sort-articles").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.sortKey;
      if (articleSortKey === key) articleSortDirection *= -1;
      else { articleSortKey = key; articleSortDirection = 1; }
      articlePage = 1;
      renderArticlePage();
    });
  });
  const articlesTableView = document.querySelector("[data-articles-table-view]");
  const articlesDetailView = document.querySelector("[data-articles-detail-view]");
  const responseTitle = document.querySelector("[data-response-title]");
  const responseBody = document.querySelector("[data-response-body]");
  const showArticlesTable = () => {
    if (!articlesTableView || !articlesDetailView) return;
    articlesDetailView.hidden = true;
    articlesTableView.hidden = false;
  };
  document.querySelector("[data-response-back]")?.addEventListener("click", showArticlesTable);
  document.querySelector("[data-article-body]")?.addEventListener("click", (event) => {
    const button = event.target.closest(".js-view-response");
    if (!button) return;
    const article = currentArticles.find((item) => item.name === button.dataset.sourceName);
    if (!article || !articlesTableView || !articlesDetailView || !responseBody) return;
    responseTitle.textContent = `${article.name} · Provider output`;
    responseBody.innerHTML = `<div class="response-panels"><div><strong>Original provider response</strong><pre>${formatResponse(article.raw_response || "")}</pre></div>${article.repaired_response ? `<div><strong>Repaired JSON</strong><pre>${formatResponse(article.repaired_response)}</pre></div>` : ""}</div>`;
    articlesTableView.hidden = true;
    articlesDetailView.hidden = false;
  });
  document.querySelector("[data-article-body]")?.addEventListener("click", async (event) => {
    const button = event.target.closest(".js-delete-response");
    if (!button) return;
    const sourceName = button.dataset.sourceName;
    if (!window.confirm(`Delete the saved response for “${sourceName}”? The source file will remain and can be rerun.`)) return;
    button.disabled = true;
    const data = new FormData();
    data.set("source_name", sourceName);
    try {
      await postForm(`/${token}/projects/${projectId}/responses/delete`, data);
      await refreshProject();
    } catch (error) {
      button.disabled = false;
      window.alert(error.message);
    }
  });
  document.querySelector(".js-page-next")?.addEventListener("click", () => {
    const totalPages = Math.max(1, Math.ceil(currentArticles.length / pageSize));
    if (articlePage < totalPages) articlePage += 1;
    renderArticlePage();
  });

  const refreshProject = async () => {
    const response = await fetch(`/${token}/api/projects/${projectId}`, { cache: "no-store" });
    if (!response.ok) return;
    renderArticles(await response.json());
  };

  const upload = document.querySelector(".js-upload");
  const fileInput = upload?.querySelector("input[type=file]");
  const uploadMessage = document.querySelector(".upload-message");
  if (upload && fileInput) {
    ["dragenter", "dragover"].forEach((name) => upload.addEventListener(name, (event) => {
      event.preventDefault();
      upload.classList.add("dragging");
    }));
    ["dragleave", "drop"].forEach((name) => upload.addEventListener(name, (event) => {
      event.preventDefault();
      upload.classList.remove("dragging");
    }));
    upload.addEventListener("drop", (event) => handleFiles(event.dataTransfer.files));
    fileInput.addEventListener("change", () => handleFiles(fileInput.files));
  }

  async function handleFiles(files) {
    if (!files?.length) return;
    const data = new FormData();
    Array.from(files).forEach((file) => data.append("files", file));
    uploadMessage.textContent = `Adding ${files.length} file${files.length === 1 ? "" : "s"}…`;
    try {
      const result = await postForm(upload.action, data);
      uploadMessage.textContent = `Added ${result.saved.length} file${result.saved.length === 1 ? "" : "s"}.`;
      setMessageState(uploadMessage, "upload-message form-message success-text");
      fileInput.value = "";
      recentUploads = new Set(result.saved);
      sourcePage = 1;
      await refreshProject();
    } catch (error) {
      uploadMessage.textContent = error.message;
      setMessageState(uploadMessage, "upload-message form-message error-text");
    }
  }

  const instructionsForm = document.querySelector(".js-instructions");
  const instructionsButton = document.querySelector(".js-save-instructions");
  if (instructionsForm) {
    instructionsForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const textarea = instructionsForm.querySelector("textarea[name=instructions]");
      const message = instructionsForm.querySelector(".instruction-message");
      if (textarea.value === textarea.defaultValue) {
        message.textContent = "Instructions are unchanged.";
        setMessageState(message, "form-message instruction-message success-text");
        return;
      }

      const data = new FormData(instructionsForm);
      if (instructionsForm.dataset.hasResults === "true") {
        const confirmed = await confirmDestructive(
          "Changing extraction instructions will permanently delete the current report, extraction results, and processing logs. Every article will need to be processed again. Continue?"
        );
        if (!confirmed) return;
        data.set("confirm_reset", "yes");
      }

      instructionsButton.disabled = true;
      message.textContent = "Saving…";
      try {
        const result = await postForm(instructionsForm.action, data);
        textarea.defaultValue = textarea.value;
        instructionsForm.dataset.hasResults = "false";
        message.textContent = result.reset
          ? "Instructions saved. Previous results were cleared; run the analysis again."
          : "Instructions saved.";
        setMessageState(message, "form-message instruction-message success-text");
        if (result.reset) window.setTimeout(() => window.location.reload(), 700);
      } catch (error) {
        message.textContent = error.message;
        setMessageState(message, "form-message instruction-message error-text");
      } finally {
        instructionsButton.disabled = false;
      }
    });
  }

  const runButton = document.querySelector(".js-run");
  const retryButton = document.querySelector(".js-retry");
  const regenerateButton = document.querySelector(".js-regenerate-report");
  const schemaButton = document.querySelector(".js-save-schema");
  const cancelButton = document.querySelector(".js-cancel");
  const progressArea = document.querySelector(".progress-area");
  const progressBar = document.querySelector("[data-progress-bar]");
  const progressCount = document.querySelector("[data-progress-count]");
  const progressMessage = document.querySelector("[data-progress-message]");
  const runMessage = document.querySelector(".run-message");
  const slowExtractionWarning = document.querySelector("[data-slow-extraction-warning]");
  const connectionLost = document.querySelector("[data-connection-lost]");
  const connectionLostTitle = document.querySelector("[data-connection-lost-title]");
  const connectionLostMessage = document.querySelector("[data-connection-lost-message]");
  // Browsers retry a dropped stream on their own, but keep retrying forever
  // when the app is down; after this many failures, ask for a reload instead.
  const MAX_STREAM_RETRIES = 3;
  let eventSource;
  let streamFailures = 0;
  let slowExtractionTimer;
  let articleRefreshTimer;

  const setRunning = (running) => {
    isRunning = running;
    runButton.disabled = running;
    retryButton.disabled = running;
    regenerateButton.disabled = running;
    instructionsButton.disabled = running;
    if (schemaButton) schemaButton.disabled = running;
    cancelButton.hidden = !running;
    cancelButton.disabled = false;
    progressArea.hidden = !running;
    if (slowExtractionTimer) window.clearTimeout(slowExtractionTimer);
    slowExtractionTimer = undefined;
    if (!running && slowExtractionWarning) slowExtractionWarning.hidden = true;
    if (articleRefreshTimer) window.clearInterval(articleRefreshTimer);
    articleRefreshTimer = running ? window.setInterval(() => refreshProject(), 2500) : undefined;
    if (!running) syncRunReadiness();
  };

  const showConnectionLost = (closed) => {
    connectionLostTitle.textContent = "Connection lost";
    connectionLostMessage.textContent = closed
      ? "Live progress has stopped updating. Reload this page. If that does not work, restart auto-zcurve and open the new link it prints."
      : "Live progress stopped updating. Retrying…";
    connectionLost.hidden = false;
  };

  const clearConnectionLost = () => {
    streamFailures = 0;
    connectionLost.hidden = true;
  };

  const watchEvents = () => {
    eventSource?.close();
    clearConnectionLost();
    eventSource = new EventSource(`${base}/events`);
    eventSource.addEventListener("error", () => {
      streamFailures += 1;
      showConnectionLost(eventSource.readyState === EventSource.CLOSED || streamFailures >= MAX_STREAM_RETRIES);
    });
    eventSource.addEventListener("progress", (event) => {
      const data = JSON.parse(event.data);
      clearConnectionLost();
      const percent = data.total ? Math.round((data.completed / data.total) * 100) : 0;
      progressBar.querySelector("span").style.width = `${percent}%`;
      progressBar.setAttribute("aria-valuenow", String(percent));
      progressCount.textContent = `${data.completed} / ${data.total}`;
      progressMessage.textContent = data.message;
      if (data.completed > 0 && slowExtractionWarning) slowExtractionWarning.hidden = true;
      if (data.completed === 0 && !slowExtractionTimer) {
        slowExtractionTimer = window.setTimeout(() => {
          if (slowExtractionWarning) slowExtractionWarning.hidden = false;
        }, 60_000);
      }
    });
    eventSource.addEventListener("status", async (event) => {
      const data = JSON.parse(event.data);
      clearConnectionLost();
      progressMessage.textContent = data.message;
      if (["complete", "failed", "cancelled"].includes(data.status)) {
        eventSource.close();
        setRunning(false);
        runMessage.textContent = data.message;
        setMessageState(runMessage, `form-message run-message ${data.status === "complete" ? "success-text" : "error-text"}`);
        await refreshProject();
        if (data.status === "complete") window.setTimeout(() => window.location.reload(), 500);
      }
    });
  };

  const extractorSelect = document.querySelector("#extractor");
  const credentialPrompt = document.querySelector("[data-run-credential-prompt]");
  const modelPrompt = document.querySelector("[data-run-model-prompt]");
  const runActions = document.querySelector("[data-run-actions]");
  const editExtractorLink = document.querySelector("[data-edit-extractor-link]");
  const projectSettingsMessage = document.querySelector(".project-settings-message");

  const start = async (action) => {
    runMessage.textContent = "";
    const data = new FormData();
    if (["run", "retry"].includes(action) && extractorSelect?.value) data.set("extractor_id", extractorSelect.value);
    try {
      await postForm(`${base}/${action}`, data);
      setRunning(true);
      watchEvents();
    } catch (error) {
      runMessage.textContent = error.message;
      setMessageState(runMessage, "form-message run-message error-text");
    }
  };

  /* Run and Retry are offered once the chosen extractor has a model and a key, or needs no key. */
  syncRunReadiness = () => {
    const selected = extractorSelect?.selectedOptions?.[0];
    const chosen = Boolean(selected?.value);
    const canRun = chosen && selected.dataset.runnable === "true";
    const needs = chosen && !canRun ? (selected.dataset.needs || "key") : "";
    if (credentialPrompt) credentialPrompt.hidden = needs !== "key";
    if (modelPrompt) modelPrompt.hidden = needs !== "model";
    if (runActions) runActions.hidden = !canRun;
    runButton.disabled = isRunning || runButton.dataset.hasPdfs !== "true" || !canRun;
    retryButton.disabled = isRunning || retryButton.dataset.hasFailures !== "true" || !canRun;
  };

  extractorSelect?.addEventListener("change", async () => {
    const extractorId = extractorSelect.value;
    document.querySelectorAll("[data-extractor-summary]").forEach((summary) => {
      summary.hidden = summary.dataset.extractorSummary !== extractorId;
    });
    const deletedNotice = document.querySelector("[data-extractor-deleted]");
    if (deletedNotice) deletedNotice.hidden = true;
    if (editExtractorLink) {
      editExtractorLink.hidden = false;
      editExtractorLink.href = `/${token}/settings#extractor-${encodeURIComponent(extractorId)}`;
    }
    document.querySelectorAll("[data-setup-extractor-link]").forEach((link) => {
      link.href = `/${token}/settings#extractor-${encodeURIComponent(extractorId)}`;
    });
    syncRunReadiness();
    const data = new FormData();
    data.set("extractor_id", extractorId);
    try {
      await postForm(`${base}/settings`, data);
      projectSettingsMessage.textContent = "This project will use this extractor.";
      setMessageState(projectSettingsMessage, "form-message project-settings-message success-text");
    } catch (error) {
      projectSettingsMessage.textContent = error.message;
      setMessageState(projectSettingsMessage, "form-message project-settings-message error-text");
    }
  });
  syncRunReadiness();
  runButton?.addEventListener("click", () => start("run"));
  retryButton?.addEventListener("click", () => start("retry"));
  regenerateButton?.addEventListener("click", () => start("regenerate-report"));
  cancelButton?.addEventListener("click", async () => {
    try {
      await postForm(`${base}/cancel`, new FormData());
      progressMessage.textContent = "Cancelling…";
      cancelButton.disabled = true;
    } catch (error) {
      runMessage.textContent = error.message;
    }
  });
  document.querySelector(".js-open-folder")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    const originalLabel = button.textContent;
    try {
      await postForm(`${base}/open-folder`, new FormData());
      button.textContent = "Folder opened";
      window.setTimeout(() => { button.textContent = originalLabel; }, 1800);
    } catch (error) {
      window.alert(error.message);
    }
  });
  if (progressArea && !progressArea.hidden) {
    setRunning(true);
    watchEvents();
  }
  refreshProject();
})();
