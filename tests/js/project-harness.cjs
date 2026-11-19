const { mount } = require('./harness.cjs');
const flush = () => new Promise(resolve => setImmediate(resolve));
const emptyProject = { articles: [], pdf_count: 0, total_tokens: 0, successful_count: 0, failed_count: 0 };
const controls = `
<form class="js-run-settings"><select id="extractor" name="extractor_id">
<option value="lab" data-runnable="true" selected>Lab</option>
<option value="nokey" data-runnable="false">No key</option>
<option value="local" data-runnable="true">Local</option></select>
<p class="project-settings-message"></p>
<div data-extractor-deleted></div>
<dl data-extractor-summary="lab"></dl><dl data-extractor-summary="nokey" hidden></dl><dl data-extractor-summary="local" hidden></dl>
<a data-edit-extractor-link href="/token/settings#extractor-lab">Edit extractor</a></form>
<div data-run-credential-prompt hidden><a></a></div><div data-run-actions></div>
<button class="js-run" data-has-pdfs="true">Run</button><button class="js-retry" data-has-failures="true">Retry</button>
<button class="js-regenerate-report">Report</button><button class="js-save-instructions">Save instructions</button>
<button class="js-cancel" hidden>Cancel</button>
<button class="js-open-folder">Open folder</button><p class="run-message"></p>
<div class="progress-area" hidden><div data-progress-summary><div data-progress-bar><span></span></div><span data-progress-count></span><span data-progress-message></span></div></div>
<div data-connection-lost hidden><h2 data-connection-lost-title></h2><p data-connection-lost-message></p></div>
<div data-slow-extraction-warning hidden></div><span data-pdf-count></span><span data-token-count></span>
<div data-articles-table-view><table><tbody data-article-body></tbody></table></div>
<div data-articles-detail-view hidden><h3 data-response-title></h3><div data-response-body></div><button data-response-back>Back</button></div>
<div data-article-pagination><button class="js-page-previous">Previous</button><span data-page-status></span><button class="js-page-next">Next</button></div>
<button class="js-sort-articles" data-sort-key="name">Name</button><button class="js-sort-articles" data-sort-key="effects">Effects</button>`;

function projectMount(html = '', options = {}) {
  const sources = [], intervals = [], clearedIntervals = [], alerts = [], scrolls = [];
  const harness = mount(controls + html, {
    attributes: 'data-token="token" data-project-id="project"',
    url: 'http://localhost/token/projects/project',
    ...options,
    fetch: async (url, request) => {
      if (options.fetch) {
        const result = await options.fetch(url, request);
        if (result) return result;
      }
      return { ok: true, json: async () => url.includes('/api/projects/') ? (options.project || emptyProject) : {} };
    },
    setup(window) {
      window.alert = message => alerts.push(message);
      window.scrollTo = (...args) => scrolls.push(args);
      window.setInterval = fn => { intervals.push(fn); return intervals.length; };
      window.clearInterval = id => clearedIntervals.push(id);
      window.EventSource = class {
        static CONNECTING = 0; static OPEN = 1; static CLOSED = 2;
        constructor(url) { this.url = url; this.listeners = {}; this.closed = false; this.readyState = 0; sources.push(this); }
        addEventListener(name, listener) { this.listeners[name] = listener; }
        close() { this.closed = true; }
        async emit(name, data) { await this.listeners[name]({ data: JSON.stringify(data) }); }
        fail(readyState) { this.readyState = readyState; this.listeners.error({}); }
      };
      options.setup?.(window);
    },
  });
  return { ...harness, sources, intervals, clearedIntervals, alerts, scrolls,
    select: value => { const select = harness.document.querySelector('#extractor'); select.value = value; select.dispatchEvent(new harness.window.Event('change')); },
    click: async selector => { harness.document.querySelector(selector).click(); await flush(); },
    input: (selector, value, event = 'input') => { const input = harness.document.querySelector(selector); input.value = value; input.dispatchEvent(new harness.window.Event(event)); },
  };
}
module.exports = { projectMount, flush, emptyProject };
