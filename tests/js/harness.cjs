const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { JSDOM, VirtualConsole } = require('jsdom');
const filename = path.resolve(__dirname, '../../auto_zcurve/static/app.js');
const source = fs.readFileSync(filename, 'utf8');

function mount(html, { attributes = 'data-token="token" data-page="settings"', response = {}, ok = true, reject, invalidJson = false, confirm = true, fetch, setup, url = 'http://localhost/token/settings' } = {}) {
  const browserErrors = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', error => browserErrors.push(error));
  const dom = new JSDOM(`<!doctype html><html><body ${attributes}>${html}</body></html>`, { url, runScripts: 'outside-only', virtualConsole });
  const { window } = dom;
  const calls = [], timers = [], confirmations = [];
  window.matchMedia = () => ({ matches: false, addEventListener() {} });
  window.setTimeout = fn => { timers.push(fn); return timers.length; };
  window.confirm = message => { confirmations.push(message); return confirm; };
  window.fetch = async (...args) => {
    calls.push(args);
    if (fetch) return fetch(...args);
    if (reject) throw new Error(reject);
    return { ok, json: async () => { if (invalidJson) throw new Error('invalid JSON'); return response; } };
  };
  window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  window.HTMLDialogElement.prototype.close = function () { this.open = false; };
  setup?.(window);
  new vm.Script(source, { filename }).runInContext(dom.getInternalVMContext());
  const submit = async selector => {
    window.document.querySelector(selector).dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));
    await new Promise(resolve => setImmediate(resolve));
  };
  return { dom, window, document: window.document, calls, timers, confirmations, browserErrors, submit, close: () => window.close() };
}
module.exports = { mount };
