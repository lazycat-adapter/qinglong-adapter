const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const test = require('node:test');

const manifest = JSON.parse(execFileSync('python3', ['-c',
  "import json,yaml; print(json.dumps(yaml.safe_load(open('lzc-manifest.yml'))))",
], { encoding: 'utf8' }));
const rules = manifest.application.injects ?? [];
const request = rules.find((rule) => rule.on === 'request');
const response = rules.find((rule) => rule.on === 'response');
const browser = rules.find((rule) => rule.on === 'browser');

const setup = { username: 'fixture-user', password: 'fixture-strong-secret' };
const token = { code: 200, data: { token: 'fixture-token' } };

function simulate(path, method, input, status, output, uid = 'fixture-uid') {
  const flow = new Map();
  const persist = new Map();
  const ctx = { safe_uid: uid, request: { path, method }, flow, persist };
  new Function('ctx', request.do)({ ...ctx, body: { getJSON: () => input } });
  new Function('ctx', response.do)({ ...ctx, status, body: { getJSON: () => output } });
  return persist;
}

test('login injection stays within the current authenticated LazyCat user', () => {
  assert.equal(rules.length, 3);
  assert.ok(rules.every((rule) => rule.auth_required === true));
  assert.deepEqual(browser.when, ['/login']);
  const { params, src } = browser.do[0];
  assert.equal(src, 'builtin://simple-inject-password');
  assert.equal(params.autoSubmit, true);
  assert.equal(params.requireUser, true);
  assert.equal(params.password.$persist, 'qinglong.password');
  assert.equal(Object.hasOwn(params.password, 'default'), false);
  assert.equal(simulate('/api/user/login', 'POST', setup, 200, token, '').size, 0);
});

test('learns only successful setup, login, 2FA, and account change', () => {
  for (const [path, method, output] of [
    ['/api/user/init', 'PUT', { code: 200 }],
    ['/api/user/login', 'POST', token],
    ['/api/user/two-factor/login', 'PUT', token],
    ['/api/user', 'PUT', { code: 200 }],
  ]) {
    const memory = simulate(path, method, setup, 200, output);
    assert.equal(memory.get('qinglong.username'), setup.username);
    assert.equal(memory.get('qinglong.password'), setup.password);
  }
});

test('rejects failed, unrelated, missing-token, and incomplete 2FA results', () => {
  for (const [path, method, status, output] of [
    ['/api/user/login', 'POST', 200, { code: 410 }],
    ['/api/user/login', 'POST', 200, { code: 420 }],
    ['/api/user/login', 'POST', 200, { code: 200 }],
    ['/api/user/two-factor/login', 'PUT', 200, { code: 200 }],
    ['/api/user/login', 'POST', 401, token],
    ['/api/user', 'GET', 200, { code: 200 }],
    ['/api/user/init/unrelated', 'PUT', 200, { code: 200 }],
    ['/api/user/init', 'PUT', 200, { code: 450 }],
    ['/api/user/login', 'POST', 200, null],
  ]) {
    assert.equal(simulate(path, method, setup, status, output).size, 0, path);
  }
});
