const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { JSDOM } = require('jsdom');
const filename = path.resolve(__dirname, '../../auto_zcurve/static/app.js');
const source = fs.readFileSync(filename, 'utf8');

function page({ saved, dark = false, storageFails = false } = {}) {
  const dom = new JSDOM('<!doctype html><html><body><button class="js-theme-option" data-theme-value="system"></button><button class="js-theme-option" data-theme-value="light"></button><button class="js-theme-option" data-theme-value="dark"></button></body></html>', { url: 'http://localhost/token/', runScripts: 'outside-only' });
  const media = { matches: dark, addEventListener: (_event, fn) => { media.change = fn; } };
  dom.window.matchMedia = () => media;
  if (saved) dom.window.localStorage.setItem('auto-zcurve-theme', saved);
  if (storageFails) Object.defineProperty(dom.window, 'localStorage', { get() { throw new Error('blocked'); } });
  new vm.Script(source, { filename }).runInContext(dom.getInternalVMContext());
  return { dom, media, root: dom.window.document.documentElement };
}

test('theme defaults to system and follows system changes', () => {
  const { dom, media, root } = page({ dark: true });
  try {
    assert.equal(root.dataset.themePreference, 'system');
    assert.equal(root.classList.contains('dark'), true);
    media.matches = false;
    media.change();
    assert.equal(root.classList.contains('dark'), false);
  } finally { dom.window.close(); }
});

test('explicit theme persists and ignores system changes', () => {
  const { dom, media, root } = page({ saved: 'light', dark: true });
  try {
    assert.equal(root.style.colorScheme, 'light');
    const dark = dom.window.document.querySelector('[data-theme-value="dark"]');
    dark.click();
    assert.equal(root.style.colorScheme, 'dark');
    assert.equal(dark.getAttribute('aria-checked'), 'true');
    assert.equal(dom.window.localStorage.getItem('auto-zcurve-theme'), 'dark');
    media.matches = false;
    media.change();
    assert.equal(root.style.colorScheme, 'dark');
  } finally { dom.window.close(); }
});

test('invalid saved themes and unavailable storage fall back safely', () => {
  for (const options of [{ saved: 'invalid' }, { storageFails: true }]) {
    const { dom, root } = page(options);
    try {
      assert.equal(root.dataset.themePreference, 'system');
      dom.window.document.querySelector('[data-theme-value="light"]').click();
      assert.equal(root.style.colorScheme, 'light');
    } finally { dom.window.close(); }
  }
});
