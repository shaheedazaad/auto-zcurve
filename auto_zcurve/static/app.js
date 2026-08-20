(() => {
  let syncProviderCredentialState = () => {};
  let refreshProviderModels = () => {};

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

  const markProviderKeyReady = (provider, saved) => {
    const option = Array.from(document.querySelectorAll("#provider option"))
      .find((item) => item.value === provider);
    const providerControl = document.querySelector("#provider");
    const target = option || (providerControl?.value === provider ? providerControl : null);
    if (!target) return;
    target.dataset.keyReady = "true";
    if (saved) target.dataset.savedKey = "true";
    syncProviderCredentialState();
  };

  /* A credential form lives either inline in a provider row or inside that
     provider's dialog; the status line it reports into is the nearest one. */
  const credentialMessage = (form) =>
    form.querySelector(".form-message")
    || form.closest("dialog")?.querySelector(".form-message")
    || form.closest("[data-credential-row]")?.querySelector(".form-message")
    || form.closest(".row")?.querySelector(".form-message");

  /* Key entry happens in a native <dialog> so provider rows stay one line. */
  document.querySelectorAll("[data-open-key-dialog]").forEach((button) => {
    button.addEventListener("click", () => {
      const dialog = document.getElementById(`key-dialog-${button.dataset.openKeyDialog}`);
      if (!dialog) return;
      dialog.showModal();
      dialog.querySelector("[name=api_key]")?.focus();
    });
  });
  document.querySelectorAll("[data-close-key-dialog]").forEach((button) => {
    button.addEventListener("click", () => button.closest("dialog")?.close());
  });

  document.querySelectorAll(".js-credentials").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const message = credentialMessage(form);
      message.textContent = "Saving…";
      try {
        const formData = new FormData(form);
        const result = await postForm(form.action, formData);
        message.textContent = result.warning || (result.saved ? "Saved securely." : "Key is ready for this session.");
        setMessageState(message, `form-message ${result.warning ? "warning-text" : "success-text"}`);
        form.querySelector("[name=api_key]").value = "";
        if (document.body.dataset.projectId) {
          window.setTimeout(() => markProviderKeyReady(result.provider || formData.get("provider"), result.saved), 650);
        } else {
          window.setTimeout(() => window.location.reload(), 650);
        }
      } catch (error) {
        message.textContent = error.message;
        setMessageState(message, "form-message error-text");
      }
    });
  });

  document.querySelectorAll(".js-load-credentials").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const message = credentialMessage(form);
      message.textContent = "Opening credential store…";
      try {
        const formData = new FormData(form);
        const result = await postForm(form.action, formData);
        message.textContent = "Saved key unlocked for this session.";
        setMessageState(message, "form-message success-text");
        if (document.body.dataset.projectId) {
          window.setTimeout(() => {
            markProviderKeyReady(result.provider || formData.get("provider"), true);
            refreshProviderModels({ preserveValue: true });
          }, 650);
        } else {
          window.setTimeout(() => window.location.reload(), 650);
        }
      } catch (error) {
        message.textContent = error.message;
        setMessageState(message, "form-message error-text");
      }
    });
  });

  const body = document.body;
  const projectId = body.dataset.projectId;
  const page = body.dataset.page;
  const token = body.dataset.token;
  const escapeHtml = (value) => {
    const div = document.createElement("div");
    div.textContent = String(value);
    return div.innerHTML;
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
      error: { variant: "destructive", label: "Failed" },
      ready: { variant: "outline", label: "Ready" },
    };
    const state = states[status] || states.ready;
    return `<span class="badge article-status" data-variant="${state.variant}">${state.label}</span>`;
  };
  const articleFileIcon = '<span class="file-icon article-file-icon" aria-hidden="true"><svg width="16" height="16" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" fill="none"><path d="M14 3v4a1 1 0 0 0 1 1h4"/><path d="M5 12v-7a2 2 0 0 1 2 -2h7l5 5v12a2 2 0 0 1 -2 2h-5"/><path d="M3 15h6v4a2 2 0 0 1 -2 2h-2a2 2 0 0 1 -2 -2z"/><path d="M6 15v6"/></svg></span>';
  if (!token) return;

  if (page === "settings") {
    const settingsForm = document.querySelector(".js-app-settings");
    settingsForm?.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!settingsForm.reportValidity()) return;
      const button = settingsForm.querySelector("button[type=submit]");
      const message = settingsForm.querySelector(".form-message");
      button.disabled = true;
      message.textContent = "Saving…";
      setMessageState(message, "form-message settings-message mb-0");
      try {
        const result = await postForm(settingsForm.action, new FormData(settingsForm));
        message.textContent = "Settings saved.";
        setMessageState(message, "form-message settings-message mb-0 success-text");
      } catch (error) {
        message.textContent = error.message;
        setMessageState(message, "form-message settings-message mb-0 error-text");
      } finally {
        button.disabled = false;
      }
    });

    document.querySelectorAll(".js-delete-credentials").forEach((form) => {
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const label = form.dataset.providerLabel || "provider";
        const confirmed = await confirmDestructive(
          `Remove the saved ${label} key and forget it for this session?`
        );
        if (!confirmed) return;
        const message = credentialMessage(form);
        try {
          await postForm(form.action, new FormData(form));
          message.textContent = "Key removed.";
          setMessageState(message, "form-message success-text");
          window.setTimeout(() => window.location.reload(), 500);
        } catch (error) {
          message.textContent = error.message;
          setMessageState(message, "form-message error-text");
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
        <a class="row" data-project-row href="/${encodeURIComponent(token)}/projects/${encodeURIComponent(project.id)}">
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
        : '<div class="empty"><p class="empty-title">No matching projects</p><p class="empty-text">Try a different project name.</p></div>';
      count.textContent = `${filteredProjects.length} project${filteredProjects.length === 1 ? "" : "s"}`;
      pagination.hidden = filteredProjects.length <= pageSize;
      pagination.querySelector("[data-project-page]").textContent = `Page ${page} of ${totalPages}`;
      pagination.querySelector(".js-project-previous").disabled = page <= 1;
      pagination.querySelector(".js-project-next").disabled = page >= totalPages;
    };

    const applyProjectSearch = () => {
      const query = search.value.trim().toLocaleLowerCase();
      filteredProjects = projects.filter((project) => project.name.toLocaleLowerCase().includes(query));
      page = 1;
      renderProjects();
    };

    search?.addEventListener("input", applyProjectSearch);
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
      activateTab(validTabs.includes(hash) ? hash : "overview");
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
      "This permanently removes all extraction results, reports, plots, and processing logs. PDFs, instructions, and the extraction schema will be kept.",
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
      `This permanently deletes “${name}”, including its PDFs, settings, and all results. This cannot be undone.`,
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
          "Changing the extraction schema will permanently delete the current report, extraction results, and processing logs. Every PDF will need to be processed again. Continue?"
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
      tbody.innerHTML = '<tr class="empty-row"><td colspan="5" class="table-empty-cell">No articles yet. Add PDFs from the Sources tab to begin.</td></tr>';
    } else {
      tbody.innerHTML = visibleArticles.map((article) => `
        <tr class="article-row article-row-${article.status === "error" ? "error" : article.status === "ok" ? "ok" : "ready"}">
          <td>${articleFileIcon}<span><strong class="article-name" title="${escapeHtml(article.name)}">${escapeHtml(article.name)}</strong>${article.error ? `<small class="article-message u-error">${escapeHtml(article.error)}</small>` : article.warning ? `<small class="article-message u-warning">${escapeHtml(article.warning)}</small>` : ""}</span></td>
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

  const renderArticles = (project) => {
    currentArticles = project.articles;
    renderArticlePage();
    document.querySelector("[data-pdf-count]").textContent = `${project.pdf_count} PDFs`;
    document.querySelector("[data-token-count]").textContent = project.total_tokens;
    const currentRunButton = document.querySelector(".js-run");
    currentRunButton.dataset.hasPdfs = project.pdf_count > 0 ? "true" : "false";
    const remaining = Math.max(0, project.pdf_count - project.successful_count);
    currentRunButton.innerHTML = project.successful_count > 0 && remaining > 0
      ? `Continue processing ${remaining} PDF${remaining === 1 ? "" : "s"} <span>→</span>`
      : "Run analysis <span>→</span>";
    const currentRetryButton = document.querySelector(".js-retry");
    currentRetryButton.dataset.hasFailures = project.failed_count > 0 ? "true" : "false";
    syncProviderCredentialState();
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
    if (!window.confirm(`Delete the saved response for “${sourceName}”? The PDF will remain and can be rerun.`)) return;
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
    uploadMessage.textContent = `Adding ${files.length} PDF${files.length === 1 ? "" : "s"}…`;
    try {
      const result = await postForm(upload.action, data);
      uploadMessage.textContent = `Added ${result.saved.length} PDF${result.saved.length === 1 ? "" : "s"}.`;
      setMessageState(uploadMessage, "upload-message form-message success-text");
      fileInput.value = "";
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
          "Changing extraction instructions will permanently delete the current report, extraction results, and processing logs. Every PDF will need to be processed again. Continue?"
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
  let eventSource;
  let slowExtractionTimer;
  let articleRefreshTimer;

  const setRunning = (running) => {
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
  };

  const watchEvents = () => {
    eventSource?.close();
    eventSource = new EventSource(`${base}/events`);
    eventSource.addEventListener("progress", (event) => {
      const data = JSON.parse(event.data);
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

  const start = async (action) => {
    runMessage.textContent = "";
    const settingsForm = document.querySelector(".js-run-settings");
    if (["run", "retry"].includes(action) && !settingsForm.reportValidity()) return;
    if (["run", "retry"].includes(action) && !(await validateOpenRouterModel())) return;
    const data = ["run", "retry"].includes(action) ? new FormData(settingsForm) : new FormData();
    try {
      await postForm(`${base}/${action}`, data);
      setRunning(true);
      watchEvents();
    } catch (error) {
      runMessage.textContent = error.message;
      setMessageState(runMessage, "form-message run-message error-text");
    }
  };
  const providerSelect = document.querySelector("#provider");
  const modelInput = document.querySelector("#model");
  const modelOptions = document.querySelector("#gemini-model-options");
  const modelHint = document.querySelector("[data-model-hint]");
  const modelValidation = document.querySelector("[data-model-validation]");
  const openRouterWarning = document.querySelector("[data-openrouter-warning]");
  const saveProjectSettingsButton = document.querySelector(".js-save-project-settings");
  const projectSettingsMessage = document.querySelector(".project-settings-message");
  const parallelInput = document.querySelector("#parallel-requests");
  const delayInput = document.querySelector("#request-delay-sec");
  let projectSettingsTouched = false;
  parallelInput?.addEventListener("input", () => { projectSettingsTouched = true; });
  delayInput?.addEventListener("input", () => { projectSettingsTouched = true; });
  const providerLabel = document.querySelector("[data-provider-label]");
  const credentialPrompt = document.querySelector("[data-run-credential-prompt]");
  const runActions = document.querySelector("[data-run-actions]");
  const keyHeading = document.querySelector("[data-key-heading]");
  const keyPromptCopy = document.querySelector("[data-key-prompt-copy]");
  const unlockButton = document.querySelector("[data-unlock-button]");
  const keyInput = document.querySelector("[data-key-input]");
  const unlockForm = document.querySelector("[data-unlock-form]");
  const macosKeyNote = document.querySelector("[data-macos-key-note]");
  let currentModelCatalog = [];

  const updateModelHint = () => {
    if (!modelHint || !providerSelect || !modelInput) return;
    const selectedModel = currentModelCatalog.find((item) => item.id === modelInput.value.trim());
    if (providerSelect.value === "gemini") {
      modelHint.textContent = "Gemini receives the original PDF and returns schema-validated structured data.";
    } else {
      modelHint.textContent = "Enter an exact OpenRouter model ID. It must be validated before it can run.";
    }
  };

  syncProviderCredentialState = () => {
    if (!providerSelect) return;
    const selected = providerSelect.selectedOptions?.[0] || providerSelect;
    const label = selected.textContent?.trim() || "Gemini";
    const keyReady = selected.dataset.keyReady === "true";
    const savedKey = selected.dataset.savedKey === "true";
    if (providerLabel) providerLabel.textContent = label;
    if (credentialPrompt) credentialPrompt.hidden = keyReady;
    if (runActions) runActions.hidden = !keyReady;
    if (keyHeading) keyHeading.textContent = `Unlock or add a ${label} API key to run`;
    if (keyPromptCopy) keyPromptCopy.textContent = savedKey
      ? "Your saved key must be unlocked again after restarting the app."
      : `No ${label} key is configured for this session.`;
    if (unlockButton) unlockButton.textContent = `Unlock saved ${label} key`;
    if (keyInput) keyInput.placeholder = `Paste ${label} API key`;
    document.querySelectorAll("[data-credential-provider]").forEach((input) => {
      input.value = selected.value;
    });
    if (unlockForm) unlockForm.hidden = !savedKey;
    if (macosKeyNote) macosKeyNote.hidden = !savedKey;
    runButton.disabled = runButton.dataset.hasPdfs !== "true" || !keyReady;
    retryButton.disabled = retryButton.dataset.hasFailures !== "true" || !keyReady;
  };

  const refreshModels = async ({ preserveValue = false } = {}) => {
    if (!providerSelect || !modelInput || !modelOptions) return;
    const provider = providerSelect.value;
    const previousValue = modelInput.value.trim();
    const isOpenRouter = provider === "openrouter";
    syncProviderCredentialState();
    if (openRouterWarning) openRouterWarning.hidden = !isOpenRouter;
    modelInput.removeAttribute("list");
    if (!isOpenRouter) modelInput.setAttribute("list", "gemini-model-options");
    if (isOpenRouter) {
      currentModelCatalog = [];
      modelOptions.innerHTML = "";
      modelInput.placeholder = "vendor/model";
      if (!projectSettingsTouched && parallelInput && delayInput) {
        parallelInput.value = "1";
        delayInput.value = "0";
      }
      if (modelValidation) modelValidation.textContent = "Validation checks OpenRouter's live model metadata when you save or run.";
      updateModelHint();
      return;
    }
    const selectedProvider = providerSelect.selectedOptions?.[0] || providerSelect;
    if (selectedProvider.dataset.keyReady !== "true") {
      currentModelCatalog = [];
      modelOptions.innerHTML = "";
      modelInput.placeholder = "Unlock the API key to load models";
      updateModelHint();
      return;
    }
    runButton.disabled = true;
    modelInput.value = "";
    modelInput.placeholder = "Loading compatible models…";
    try {
      const response = await fetch(`/${token}/api/models/${encodeURIComponent(provider)}`, { cache: "no-store" });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || "Compatible models could not be loaded.");
      currentModelCatalog = payload;
      modelOptions.innerHTML = payload.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join("");
      modelInput.placeholder = "Enter a model ID";
      const previousIsCompatible = payload.some((item) => item.id === previousValue);
      if (preserveValue && previousIsCompatible) {
        modelInput.value = previousValue;
        runMessage.textContent = "";
      } else if (preserveValue && previousValue) {
        runMessage.textContent = `The previously selected model (${previousValue}) cannot be used for synchronous PDF extraction. Choose a compatible model.`;
        setMessageState(runMessage, "form-message run-message warning-text");
      } else {
        runMessage.textContent = "";
      }
      updateModelHint();
      syncProviderCredentialState();
    } catch (error) {
      currentModelCatalog = [];
      modelOptions.innerHTML = "";
      modelInput.placeholder = "Model catalog unavailable";
      runMessage.textContent = error.message;
      setMessageState(runMessage, "form-message run-message error-text");
      runButton.disabled = true;
      updateModelHint();
    }
  };
  refreshProviderModels = refreshModels;
  providerSelect?.addEventListener("change", () => refreshModels());
  modelInput?.addEventListener("input", updateModelHint);
  modelInput?.addEventListener("change", () => {
    updateModelHint();
    if (!projectSettingsTouched) {
      const selected = currentModelCatalog.find((item) => item.id === modelInput.value.trim());
      if (selected && parallelInput && delayInput) {
        parallelInput.value = String(selected.parallel_requests ?? parallelInput.value);
        delayInput.value = String(selected.request_delay_sec ?? delayInput.value);
      }
    }
  });
  const validateOpenRouterModel = async () => {
    if (providerSelect?.value !== "openrouter" || !modelInput?.value.trim()) return true;
    if (modelValidation) modelValidation.textContent = "Validating OpenRouter model…";
    const data = new FormData();
    data.set("model", modelInput.value.trim());
    try {
      const response = await fetch(`/${token}/api/models/openrouter/validate`, { method: "POST", body: data });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || "OpenRouter model validation failed.");
      if (modelValidation) modelValidation.textContent = "Validated for native PDF and structured output.";
      return true;
    } catch (error) {
      if (modelValidation) modelValidation.textContent = error.message;
      return false;
    }
  };
  modelInput?.addEventListener("blur", validateOpenRouterModel);
  syncProviderCredentialState();
  refreshModels({ preserveValue: true });
  runButton?.addEventListener("click", () => start("run"));
  retryButton?.addEventListener("click", () => start("retry"));
  saveProjectSettingsButton?.addEventListener("click", async () => {
    const settingsForm = document.querySelector(".js-run-settings");
    if (!settingsForm?.reportValidity()) return;
    if (!(await validateOpenRouterModel())) return;
    try {
      await postForm(`${base}/settings`, new FormData(settingsForm));
      if (projectSettingsMessage) {
        projectSettingsMessage.textContent = "Project settings saved.";
        setMessageState(projectSettingsMessage, "form-message project-settings-message success-text");
      }
    } catch (error) {
      if (projectSettingsMessage) {
        projectSettingsMessage.textContent = error.message;
        setMessageState(projectSettingsMessage, "form-message project-settings-message error-text");
      }
    }
  });
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
