// Called by the Python integration test with HTML and API data from TestClient.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const { JSDOM, VirtualConsole } = require('jsdom');

async function main() {
  const fixture = JSON.parse(fs.readFileSync(0, 'utf8'));
  const errors = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', error => errors.push(error.message));
  const dom = new JSDOM(fixture.html, { url: `http://127.0.0.1${fixture.path}`, runScripts: 'outside-only', virtualConsole });
  try {
    const { window } = dom;
    window.matchMedia = () => ({ matches: false, addEventListener() {} });
    window.scrollTo = () => {};
    window.addEventListener('error', event => errors.push(event.message));
    window.fetch = async url => {
      const route = new URL(url, window.location.href).pathname;
      assert.ok(Object.hasOwn(fixture.responses, route), `Unexpected request: ${route}`);
      return { ok: true, json: async () => fixture.responses[route] };
    };
    const filename = path.resolve(__dirname, '../../auto_zcurve/static/app.js');
    new vm.Script(fs.readFileSync(filename, 'utf8'), { filename }).runInContext(dom.getInternalVMContext());
    await new Promise(resolve => setImmediate(resolve));
    const { document } = window;
    assert.deepEqual(errors, []);
    assert.equal(document.body.dataset.token, 'token');
    if (fixture.page === 'project') {
      assert.equal(document.querySelector('[data-pdf-count]').textContent, '1 PDFs');
      assert.equal(document.querySelector('.article-name').textContent, "A 'quoted' paper.pdf");
      assert.equal(document.querySelector('.js-run').disabled, true, 'A missing credential must prevent runs');
      document.querySelector('[data-tab-target=schema]').click();
      assert.equal(document.querySelector('[data-tab-panel=schema]').hidden, false);
      assert.ok(document.querySelector('.yaml-highlight code .yaml-key'));
      document.querySelector('[data-tab-target=sources]').click();
      assert.equal(document.querySelector('[data-tab-panel=sources]').hidden, false);
    } else if (fixture.page === 'home') {
      assert.match(document.querySelector('[data-project-list]').textContent, /Template integration/);
    } else {
      assert.ok(document.querySelector('.js-app-settings'));
      assert.ok(document.querySelector('.settings-nav [aria-current=location]'));
    }
    assert.deepEqual(errors, []);
  } finally {
    dom.window.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
