const test = require('node:test');
const assert = require('node:assert/strict');
const { projectMount, flush } = require('./project-harness.cjs');
const schema = `<form class="js-schema-form" action="/schema"><textarea class="yaml-input" name="schema">type: object</textarea><pre class="yaml-highlight"><code></code></pre><button class="js-save-schema">Save</button><p class="schema-message layout"></p></form>`;
const instructions = `<form class="js-instructions" action="/instructions"><textarea name="instructions">Extract effects</textarea><p class="instruction-message layout"></p></form>`;

test('YAML editor highlights scalars and comments safely, inserts indentation, and synchronizes scrolling', async t => {
  const h = projectMount(schema); t.after(h.close); await flush();
  const input = h.document.querySelector('.yaml-input');
  input.value = 'type: object\n- count: -2.5\nflag: true\ntext: "quoted # value"\nblock: |-\n# comment\nplain <script>\ntext: "escaped \\" # value"\nsingle: \'a # b\'';
  input.dispatchEvent(new h.window.Event('input'));
  const code = h.document.querySelector('code');
  assert.equal(code.textContent, input.value + '\n');
  assert.equal(code.querySelectorAll('.yaml-number').length, 1);
  assert.equal(code.querySelectorAll('.yaml-literal').length, 2);
  assert.equal(code.querySelectorAll('.yaml-comment').length, 1);
  assert.equal(code.querySelectorAll('script').length, 0);
  input.setSelectionRange(0, 4);
  const tab = new h.window.KeyboardEvent('keydown', { key: 'Tab', cancelable: true });
  input.dispatchEvent(tab);
  assert.equal(tab.defaultPrevented, true);
  assert.ok(input.value.startsWith('  : object'));
  const ordinary = new h.window.KeyboardEvent('keydown', { key: 'a', cancelable: true });
  input.dispatchEvent(ordinary); assert.equal(ordinary.defaultPrevented, false);
  input.scrollTop = 70; input.scrollLeft = 20; input.dispatchEvent(new h.window.Event('scroll'));
  assert.equal(h.document.querySelector('.yaml-highlight').scrollTop, 70);
  assert.equal(h.document.querySelector('.yaml-highlight').scrollLeft, 20);
});

for (const [kind, html, formSelector, inputSelector, messageSelector] of [
  ['schema', schema, '.js-schema-form', '.yaml-input', '.schema-message'],
  ['instructions', instructions, '.js-instructions', '[name=instructions]', '.instruction-message'],
]) {
  test(`${kind} saves only changes and records the saved baseline`, async t => {
    const h = projectMount(html); t.after(h.close); await flush();
    await h.submit(formSelector);
    assert.match(h.document.querySelector(messageSelector).textContent, /unchanged/);
    assert.equal(h.calls.filter(([, request]) => request?.method === 'POST').length, 0);
    h.input(inputSelector, 'new value');
    await h.submit(formSelector);
    assert.match(h.document.querySelector(messageSelector).textContent, /saved/);
    assert.ok(h.document.querySelector(messageSelector).classList.contains('layout'));
    assert.equal(h.document.querySelector(inputSelector).defaultValue, 'new value');
    assert.equal(h.calls.at(-1)[1].body.get(kind), 'new value');
    await h.submit(formSelector);
    assert.equal(h.calls.filter(([, request]) => request?.method === 'POST').length, 1);
  });
  test(`${kind} resets results only after confirmation and handles server rejection`, async t => {
    for (const confirmed of [false, true]) {
      const h = projectMount(html, { confirm: confirmed, fetch: async (url, request) => request?.method === 'POST' ? { ok: true, json: async () => ({ reset: true }) } : undefined });
      t.after(h.close); await flush();
      h.document.querySelector(formSelector).dataset.hasResults = 'true'; h.input(inputSelector, 'changed');
      await h.submit(formSelector);
      const posts = h.calls.filter(([, request]) => request?.method === 'POST');
      assert.equal(posts.length, Number(confirmed));
      if (confirmed) {
        assert.equal(posts[0][1].body.get('confirm_reset'), 'yes');
        assert.match(h.document.querySelector(messageSelector).textContent, /cleared/);
        assert.equal(h.timers.length, 1);
        assert.equal(h.document.querySelector(formSelector).dataset.hasResults, 'false');
      }
    }
    const h = projectMount(html, { fetch: async (url, request) => request?.method === 'POST' ? { ok: false, json: async () => ({ detail: 'Invalid document' }) } : undefined });
    t.after(h.close); await flush(); h.input(inputSelector, 'invalid'); await h.submit(formSelector);
    assert.equal(h.document.querySelector(messageSelector).textContent, 'Invalid document');
    assert.ok(h.document.querySelector(messageSelector).classList.contains('u-error'));
    assert.notEqual(h.document.querySelector(inputSelector).defaultValue, 'invalid');
    assert.equal(h.document.querySelector(kind === 'schema' ? '.js-save-schema' : '.js-save-instructions').disabled, false);
  });
}

test('project tabs select initial hash, support keyboard wrapping, and update the address on click', async t => {
  const html = `<nav data-project-tabs><button data-tab-target="sources">Sources</button><button data-tab-target="overview">Overview</button><button data-tab-target="schema">Schema</button></nav><section data-tab-panel="sources"></section><section data-tab-panel="overview"></section><section data-tab-panel="schema"></section>`;
  const h = projectMount(html, { url: 'http://localhost/token/projects/project#schema' }); t.after(h.close); await flush();
  const active = () => h.document.querySelector('[aria-selected=true]').dataset.tabTarget;
  assert.equal(active(), 'schema');
  await h.click('[data-tab-target=sources]');
  assert.equal(h.window.location.hash, '#sources'); assert.equal(active(), 'sources');
  h.document.querySelector('[data-tab-target=sources]').focus();
  for (const [key, expected] of [['ArrowLeft', 'schema'], ['ArrowRight', 'sources'], ['End', 'schema'], ['Home', 'sources'], ['ArrowDown', 'overview'], ['ArrowUp', 'sources'], ['Enter', 'sources']]) {
    h.document.activeElement.dispatchEvent(new h.window.KeyboardEvent('keydown', { key, bubbles: true }));
    assert.equal(active(), expected);
    assert.equal(h.document.activeElement.dataset.tabTarget, expected);
    assert.equal(h.document.querySelector(`[data-tab-panel=${expected}]`).hidden, false);
  }
  assert.deepEqual(h.scrolls, [[0, 0]]);
});

test('standalone project tabs navigate to the project and handle focus without panels', async t => {
  const h = projectMount('<nav data-project-tabs><button data-tab-target="sources">Sources</button><button data-tab-target="overview">Overview</button></nav>');
  t.after(h.close); await flush();
  const nav = h.document.querySelector('nav');
  nav.dispatchEvent(new h.window.KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
  const sources = h.document.querySelector('[data-tab-target=sources]'); sources.focus();
  sources.dispatchEvent(new h.window.KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
  assert.equal(h.document.activeElement.dataset.tabTarget, 'overview');
  await h.click('[data-tab-target=overview]');
  assert.equal(h.window.location.hash, '#overview');
});

test('resetting edited extraction settings reloads after showing the result', async t => {
  for (const [html, selector, input] of [[schema, '.js-schema-form', '.yaml-input'], [instructions, '.js-instructions', '[name=instructions]']]) {
    const h = projectMount(html, { fetch: async (url, request) => request?.method === 'POST' ? { ok: true, json: async () => ({ reset: true }) } : undefined });
    t.after(h.close); await flush();
    h.document.querySelector(selector).dataset.hasResults = 'true';
    h.input(input, 'changed'); await h.submit(selector);
    assert.equal(h.timers.length, 1); assert.equal(h.browserErrors.length, 0);
    h.timers[0](); assert.match(h.browserErrors[0].message, /navigation/i);
  }
});

test('results open the overview tab and unknown tab targets leave selection unchanged', async t => {
  const h = projectMount('<nav data-project-tabs><button data-tab-target="sources">Sources</button><button data-tab-target="overview">Overview</button><button data-tab-target="missing">Missing</button></nav><section data-tab-panel="sources"></section><section data-tab-panel="overview"></section>', { attributes: 'data-token="token" data-project-id="project" data-has-results="true"' });
  t.after(h.close); await flush();
  assert.equal(h.document.querySelector('[aria-selected=true]').dataset.tabTarget, 'overview');
  await h.click('[data-tab-target=missing]');
  assert.equal(h.document.querySelector('[aria-selected=true]').dataset.tabTarget, 'overview');
});

test('hashes within unquoted YAML values are not comments', async t => {
  const h = projectMount(schema); t.after(h.close); await flush();
  h.input('.yaml-input', 'url: https://example.org/#part');
  assert.equal(h.document.querySelector('.yaml-comment'), null);
  assert.equal(h.document.querySelector('.yaml-highlight code').textContent, 'url: https://example.org/#part\n');
});
