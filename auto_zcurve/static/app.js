(() => {
  const postForm = async (url, body) => {
    const response = await fetch(url, { method: "POST", body });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || "The request could not be completed.");
    return payload;
  };

  document.querySelectorAll(".js-credentials").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const message = form.querySelector(".form-message");
      message.textContent = "Saving…";
      try {
        const result = await postForm(form.action, new FormData(form));
        message.textContent = result.warning || (result.saved ? "Saved securely." : "Key is ready for this session.");
        message.className = `form-message ${result.warning ? "warning-text" : "success-text"}`;
        window.setTimeout(() => window.location.reload(), 650);
      } catch (error) {
        message.textContent = error.message;
        message.className = "form-message error-text";
      }
    });
  });

  const body = document.body;
  const projectId = body.dataset.projectId;
  const token = body.dataset.token;
  const escapeHtml = (value) => {
    const div = document.createElement("div");
    div.textContent = String(value);
    return div.innerHTML;
  };
  if (!token) return;

  if (!projectId) {
    const list = document.querySelector("[data-project-list]");
    const search = document.querySelector(".js-project-search");
    const pagination = document.querySelector("[data-project-pagination]");
    const count = document.querySelector("[data-project-count]");
    const pageSize = 10;
    let projects = [];
    let filteredProjects = [];
    let page = 1;

    const projectRow = (project) => {
      let badge = '<span class="badge neutral">In progress</span>';
      if (project.has_report) {
        badge = '<span class="badge success">Report ready</span>';
      } else if (project.failed_count) {
        badge = `<span class="badge danger">${project.failed_count} failed</span>`;
      }
      const articleLabel = project.pdf_count === 1 ? "article" : "articles";
      return `
        <a class="project-row" href="/${encodeURIComponent(token)}/projects/${encodeURIComponent(project.id)}">
          <span class="project-icon">${escapeHtml(project.name.slice(0, 1).toUpperCase())}</span>
          <span class="project-main">
            <strong>${escapeHtml(project.name)}</strong>
            <span>${project.pdf_count} ${articleLabel} · ${project.total_tokens} tokens</span>
          </span>
          ${badge}
          <span class="row-arrow" aria-hidden="true">→</span>
        </a>`;
    };

    const renderProjects = () => {
      const totalPages = Math.max(1, Math.ceil(filteredProjects.length / pageSize));
      page = Math.min(page, totalPages);
      const visible = filteredProjects.slice((page - 1) * pageSize, page * pageSize);
      list.innerHTML = visible.length
        ? visible.map(projectRow).join("")
        : '<div class="project-list-empty">No projects match this search.</div>';
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
        message.className = "form-message schema-message success-text";
        return;
      }

      const data = new FormData(schemaForm);
      if (schemaForm.dataset.hasResults === "true") {
        const confirmed = window.confirm(
          "Changing the extraction schema will permanently delete the current report, extraction results, and processing logs. Every PDF will need to be processed again. Continue?"
        );
        if (!confirmed) return;
        data.set("confirm_reset", "yes");
      }

      button.disabled = true;
      message.textContent = "Validating…";
      message.className = "form-message schema-message";
      try {
        const result = await postForm(schemaForm.action, data);
        input.defaultValue = input.value;
        schemaForm.dataset.hasResults = "false";
        message.textContent = result.reset
          ? "Schema saved. Previous results were cleared; run the analysis again."
          : "Schema is valid and saved.";
        message.className = "form-message schema-message success-text";
        if (result.reset) window.setTimeout(() => window.location.reload(), 800);
      } catch (error) {
        message.textContent = error.message;
        message.className = "form-message schema-message error-text";
      } finally {
        button.disabled = false;
      }
    });
    return;
  }

  const pageSize = 10;
  let articlePage = 1;
  let currentArticles = [];

  const renderArticlePage = () => {
    const tbody = document.querySelector("[data-article-body]");
    if (!tbody) return;
    const totalPages = Math.max(1, Math.ceil(currentArticles.length / pageSize));
    articlePage = Math.min(articlePage, totalPages);
    const start = (articlePage - 1) * pageSize;
    const visibleArticles = currentArticles.slice(start, start + pageSize);
    if (!visibleArticles.length) {
      tbody.innerHTML = '<tr class="empty-row"><td colspan="4">No articles yet. Add PDFs above to begin.</td></tr>';
    } else {
      tbody.innerHTML = visibleArticles.map((article) => `
        <tr>
          <td><span class="pdf-icon">PDF</span><span><strong>${escapeHtml(article.name)}</strong>${article.error ? `<small>${escapeHtml(article.error)}</small>` : ""}</span></td>
          <td><span class="status-label ${escapeHtml(article.status)}"><i></i>${escapeHtml(article.status.charAt(0).toUpperCase() + article.status.slice(1))}</span></td>
          <td>${article.effects || "—"}</td>
          <td>${article.total_tokens || "—"}</td>
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
    document.querySelector(".js-run").disabled = project.pdf_count === 0;
    document.querySelector(".js-retry").disabled = project.failed_count === 0;
  };

  document.querySelector(".js-page-previous")?.addEventListener("click", () => {
    if (articlePage > 1) articlePage -= 1;
    renderArticlePage();
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
      uploadMessage.className = "upload-message form-message success-text";
      fileInput.value = "";
      await refreshProject();
    } catch (error) {
      uploadMessage.textContent = error.message;
      uploadMessage.className = "upload-message form-message error-text";
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
        message.className = "form-message instruction-message success-text";
        return;
      }

      const data = new FormData(instructionsForm);
      if (instructionsForm.dataset.hasResults === "true") {
        const confirmed = window.confirm(
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
        message.className = "form-message instruction-message success-text";
        if (result.reset) window.setTimeout(() => window.location.reload(), 700);
      } catch (error) {
        message.textContent = error.message;
        message.className = "form-message instruction-message error-text";
      } finally {
        instructionsButton.disabled = false;
      }
    });
  }

  const runButton = document.querySelector(".js-run");
  const retryButton = document.querySelector(".js-retry");
  const regenerateButton = document.querySelector(".js-regenerate-report");
  const cancelButton = document.querySelector(".js-cancel");
  const progressArea = document.querySelector(".progress-area");
  const progressBar = document.querySelector("[data-progress-bar]");
  const progressCount = document.querySelector("[data-progress-count]");
  const progressMessage = document.querySelector("[data-progress-message]");
  const runMessage = document.querySelector(".run-message");
  let eventSource;

  const setRunning = (running) => {
    runButton.disabled = running;
    retryButton.disabled = running;
    regenerateButton.disabled = running;
    instructionsButton.disabled = running;
    cancelButton.hidden = !running;
    progressArea.hidden = !running;
  };

  const watchEvents = () => {
    eventSource?.close();
    eventSource = new EventSource(`${base}/events`);
    eventSource.addEventListener("progress", (event) => {
      const data = JSON.parse(event.data);
      const percent = data.total ? Math.round((data.completed / data.total) * 100) : 0;
      progressBar.style.width = `${percent}%`;
      progressCount.textContent = `${data.completed} / ${data.total}`;
      progressMessage.textContent = data.message;
    });
    eventSource.addEventListener("status", async (event) => {
      const data = JSON.parse(event.data);
      progressMessage.textContent = data.message;
      if (["complete", "failed", "cancelled"].includes(data.status)) {
        eventSource.close();
        setRunning(false);
        runMessage.textContent = data.message;
        runMessage.className = `form-message run-message ${data.status === "complete" ? "success-text" : "error-text"}`;
        await refreshProject();
        if (data.status === "complete") window.setTimeout(() => window.location.reload(), 500);
      }
    });
  };

  const start = async (action) => {
    runMessage.textContent = "";
    const data = action === "run" ? new FormData(document.querySelector(".js-run-settings")) : new FormData();
    try {
      await postForm(`${base}/${action}`, data);
      setRunning(true);
      watchEvents();
    } catch (error) {
      runMessage.textContent = error.message;
      runMessage.className = "form-message run-message error-text";
    }
  };
  runButton?.addEventListener("click", () => start("run"));
  retryButton?.addEventListener("click", () => start("retry"));
  regenerateButton?.addEventListener("click", () => start("regenerate-report"));
  cancelButton?.addEventListener("click", async () => {
    try {
      await postForm(`${base}/cancel`, new FormData());
      progressMessage.textContent = "Stopping after the current request…";
      cancelButton.disabled = true;
    } catch (error) {
      runMessage.textContent = error.message;
    }
  });
  document.querySelector(".js-open-folder")?.addEventListener("click", async () => {
    try {
      await postForm(`${base}/open-folder`, new FormData());
    } catch (error) {
      runMessage.textContent = error.message;
    }
  });
  if (progressArea && !progressArea.hidden) {
    setRunning(true);
    watchEvents();
  }
  refreshProject();
})();
