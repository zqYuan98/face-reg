const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const { test } = require('node:test');

// Executes the actual inline setup code. Business requests are stubbed; no business server is contacted.
// Set ADMIN_FRONTEND_VUE_RUNTIME to a local Vue 3.5.43 global runtime to enable the renderer regression.
// Without that explicit fixture it is skipped; this suite never downloads dependencies.
const source = path.resolve(__dirname, '../../src/main/resources/static/index.html');
const html = fs.readFileSync(source, 'utf8');
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
assert.equal(scripts.length, 1);

const response = (data, code = 200, status = code) => ({
  ok: status >= 200 && status < 300, status,
  json: async () => ({ code, msg: code >= 400 ? '模拟错误' : 'ok', data })
});
const page = (records = [], total = records.length, pageNumber = 1, size = 20) =>
  response({ records, total, page: pageNumber, size });
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const settle = async () => { for (let i = 0; i < 8; i++) await new Promise(setImmediate); };

function harness(customFetch, login = false) {
  const calls = [], messages = [], revoked = [], replacements = [], timers = new Map();
  let mounted, unmount, exposed, timerId = 0, objectId = 0;
  const context = {
    console, URLSearchParams, FormData, AbortController, Headers,
    URL: {
      createObjectURL() { return `blob:fixture-${++objectId}`; },
      revokeObjectURL(url) { revoked.push(url); }
    },
    setTimeout(callback, delay) { timers.set(++timerId, { callback, delay }); return timerId; },
    clearTimeout(id) { timers.delete(id); },
    window: { location: { replace(url) { this.href = url; replacements.push(url); } } }, ElementPlusIconsVue: {},
    ElementPlus: {
      ElMessage: Object.fromEntries(['error', 'success', 'warning'].map(type => [type, message => messages.push({ type, message })])),
      ElMessageBox: { confirm: async () => true }
    },
    fetch: async (url, options = {}) => {
      calls.push({ url, options });
      if (customFetch) {
        const result = customFetch(url, options, calls.length);
        if (result !== undefined) return result;
      }
      const parsed = new URL(url, 'http://fixture.invalid');
      if (parsed.pathname === '/api/me') return response({ username: 'fixture-admin' });
      if (parsed.pathname === '/api/csrf') return response({ token: 'fixture-csrf', headerName: 'X-CSRF-TOKEN' });
      if (parsed.pathname.endsWith('/count')) return response(parsed.pathname.includes('users') ? 1207 : 81);
      if (parsed.pathname.endsWith('/options')) return response({ items: [], nextCursor: null, hasMore: false });
      if (parsed.pathname.endsWith('/page')) return page([], 0, Number(parsed.searchParams.get('page')), Number(parsed.searchParams.get('size')));
      return response(null);
    },
    Vue: {
      ref: value => ({ value }), reactive: value => value,
      computed: getter => ({ get value() { return getter(); } }),
      onMounted: callback => { mounted = callback; },
      onBeforeUnmount: callback => { unmount = callback; },
      createApp: ({ setup }) => ({ component() {}, use() {}, mount() { exposed = setup(); } })
    }
  };
  const loginSource = path.resolve(__dirname, '../../src/main/resources/static/login.html');
  const script = login ? [...fs.readFileSync(loginSource, 'utf8').matchAll(/<script>([\s\S]*?)<\/script>/g)][0][1] : scripts[0][1];
  vm.runInNewContext(script, context, { filename: login ? loginSource : source });
  return {
    app: exposed, calls, messages, revoked, replacements, location: context.window.location,
    mount: () => mounted(), unmount: () => unmount(),
    pendingTimerDelays: () => [...timers.values()].map(timer => timer.delay),
    flushTimers() { const scheduled = [...timers.values()]; timers.clear(); scheduled.forEach(timer => timer.callback()); }
  };
}

test('a failed avatar stays visible and changing its photo URL creates a fresh image with real Vue', {
  skip: process.env.ADMIN_FRONTEND_VUE_RUNTIME ? false : 'Set ADMIN_FRONTEND_VUE_RUNTIME to a local Vue 3.5.43 global runtime'
}, async () => {
  const runtimePath = process.env.ADMIN_FRONTEND_VUE_RUNTIME;
  const runtime = fs.readFileSync(runtimePath, 'utf8');
  const context = { console, setTimeout, clearTimeout };
  vm.runInNewContext(runtime, context, { filename: runtimePath });
  assert.equal(context.Vue.version, '3.5.43', 'use the pinned Vue regression fixture');
  const { createRenderer, compile, reactive, nextTick } = context.Vue;

  // Vue compiles the production template and performs the real vnode reconciliation.
  // This small host adapter preserves image style/event state just as a reused DOM node does.
  // No photo is fetched: error/load events are injected so the case is deterministic.
  const renderer = createRenderer({
    createElement: tag => ({ tag, props: {}, style: {}, children: [], parent: null }),
    patchProp: (element, name, previous, value) => {
      element.props[name] = value;
      if (name === 'style') element.style = { ...(value || {}) };
    },
    insert: (element, parent, anchor = null) => {
      if (element.parent) element.parent.children = element.parent.children.filter(child => child !== element);
      element.parent = parent;
      const index = anchor ? parent.children.indexOf(anchor) : -1;
      if (index < 0) parent.children.push(element);
      else parent.children.splice(index, 0, element);
    },
    remove: element => {
      element.parent.children = element.parent.children.filter(child => child !== element);
      element.parent = null;
    },
    setElementText: (element, text) => { element.text = text; },
    createText: text => ({ text }), createComment: text => ({ text }),
    setText: (element, text) => { element.text = text; },
    setComment: (element, text) => { element.text = text; },
    parentNode: element => element.parent,
    nextSibling: element => element.parent?.children[element.parent.children.indexOf(element) + 1] || null
  });
  const trigger = (element, type) => {
    const handler = element.props[`on${type}`] || element.props[`on${type[0].toUpperCase()}${type.slice(1)}`];
    if (typeof handler === 'function') handler({ target: element });
    else if (typeof handler === 'string') new Function('event', handler).call(element, { target: element });
  };
  const avatarTemplate = html.match(/<img\b(?=[^>]*\bclass="small-avatar")[^>]*>/)[0];
  const scope = reactive({ row: { id: 7, photoUrl: 'failed.jpg' } });
  const root = { children: [] };
  const app = renderer.createApp({ setup: () => ({ scope }), render: compile(avatarTemplate) });
  app.mount(root);
  const failedImage = root.children[0];
  trigger(failedImage, 'error');
  scope.row.photoUrl = 'replacement + new.jpg';
  await nextTick();
  const replacement = root.children[0];
  assert.notEqual(replacement, failedImage, 'replacing a photo must discard the failed image node');
  assert.equal(replacement.props.src, '/uploads/replacement%20%2B%20new.jpg');
  trigger(replacement, 'load');
  assert.notEqual(replacement.style.display, 'none', 'the replacement must not inherit a hidden state');
  assert.notEqual(failedImage.style.display, 'none', 'a load error must leave the native fallback visible');
  assert.ok(failedImage.props.alt, 'the native error placeholder must have understandable alternate text');
  app.unmount();
});

test('initial load requests only the active users page; group table and global counts are lazy', async () => {
  const h = harness();
  await h.mount();
  assert.deepEqual(h.calls.map(call => call.url), ['/api/me', '/api/users/page?page=1&size=20']);
  assert.equal(h.app.authenticated.value, true);
  await h.app.handleSelect('groups');
  assert.equal(h.calls.at(-1).url, '/api/groups/page?page=1&size=20');
  h.app.groups.value = [{ id: 1, groupName: '仅一行' }];
  await h.app.handleSelect('overview');
  assert.equal(h.app.totalUserCount.value, 1207);
  assert.equal(h.app.totalGroupCount.value, 81);
  assert.equal(h.calls.filter(call => call.url.endsWith('/count')).length, 2);
  assert.equal(h.calls.some(call => call.url.includes('/list')), false);
});

test('edit success refreshes users once, preserves pagination, and does not refetch counts or groups', async () => {
  const h = harness();
  h.app.userPager.page = 3;
  h.app.openUserDialog({ id: 8, name: '测试用户', groupId: 701, groupName: '页外分组' });
  const before = h.calls.length;
  await h.app.submitUser();
  assert.deepEqual(h.calls.slice(before).map(call => call.url), [
    '/api/csrf', '/api/users/update/8', '/api/users/page?page=3&size=20'
  ]);
  assert.equal(h.app.userDialog.visible, false);
  assert.equal(h.app.userSubmitting.value, false);
});

test('old responses and old finally blocks cannot overwrite a newer query or loading state', async () => {
  const first = deferred(), second = deferred(), third = deferred();
  let read = 0;
  const h = harness(url => url.includes('/users/page') ? [first, second, third][read++].promise : undefined);
  const oldRead = h.app.loadUsers();
  const newRead = h.app.changeUserPage(2);
  assert.equal(h.calls[0].options.signal.aborted, true);
  first.resolve(page([{ id: 'obsolete' }], 1));
  await oldRead;
  assert.equal(h.app.usersLoading.value, true);
  assert.equal(h.app.users.value.length, 0);
  second.resolve(page([{ id: 'new' }], 41, 2));
  await newRead;
  assert.equal(h.app.users.value[0].id, 'new');
  assert.equal(h.app.usersLoading.value, false);
  const lastRead = h.app.changeUserPage(3);
  third.reject(new Error('503 unavailable'));
  await lastRead;
  assert.equal(h.app.users.value.length, 0);
  assert.match(h.app.usersError.value, /503 unavailable/);
  assert.equal(h.app.canSelectUser(), false);
  assert.equal(h.app.usersLoading.value, false);
  assert.equal(h.messages.filter(message => message.type === 'error').length, 1);
  assert.equal(h.calls.length, 3, 'failed reads must not retry automatically');
});

test('filters reset to page one, page/size changes clear page-only selection and do not duplicate requests', async () => {
  const h = harness();
  let cleared = 0;
  h.app.userTable.value = { clearSelection() { cleared++; } };
  h.app.userPager.page = 8;
  h.app.selectedUsers.value = [{ id: 91 }];
  h.app.searchForm.name = '  李%_  ';
  h.app.searchForm.groupId = 9;
  await h.app.applyUsersFilter();
  const query = new URL(h.calls.at(-1).url, 'http://fixture.invalid').searchParams;
  assert.equal(query.get('page'), '1');
  assert.equal(query.get('name'), '李%_');
  assert.equal(query.get('groupId'), '9');
  assert.equal(h.app.selectedUsers.value.length, 0);
  await h.app.changeUserSize(100);
  await h.app.changeUserPage(1); // Element Plus may emit this after size changes.
  assert.equal(h.calls.length, 2);
  assert.match(h.calls.at(-1).url, /size=100/);
  assert.equal(cleared, 2);
  h.app.users.value = [{ id: 4 }];
  h.app.handleSelectionChange([{ id: 4 }, { id: 91 }]);
  assert.equal(h.app.selectedUsers.value.length, 1);
  assert.equal(h.app.selectedUsers.value[0].id, 4);
});

test('delete on an empty last page falls back exactly once; normal refresh never runs that fallback', async () => {
  const h = harness(url => {
    if (url.includes('/users/page?page=3')) return page([], 21, 3);
    if (url.includes('/users/page?page=2')) return page([{ id: 21 }], 21, 2);
  });
  h.app.userPager.page = 3;
  await h.app.handleDeleteUser({ id: 22, name: '模拟删除' });
  assert.deepEqual(h.calls.map(call => call.url), [
    '/api/csrf', '/api/users/22', '/api/users/page?page=3&size=20', '/api/users/page?page=2&size=20'
  ]);
  assert.equal(h.app.userPager.page, 2);
  assert.equal(h.app.users.value[0].id, 21);
  assert.equal(h.app.totalUserCount.value, null);
});

test('cursor options are independent, debounce searches, retain off-page selections, and ignore stale responses', async () => {
  const old = deferred();
  const h = harness(url => {
    if (url === '/api/groups/options?afterId=0&limit=20') return old.promise;
    if (url.includes('/options?afterId=0') && url.includes('keyword=A')) {
      return response({ items: [{ id: 4, groupName: 'A4' }], nextCursor: 4, hasMore: true });
    }
    if (url.includes('/options?afterId=4')) {
      return response({ items: [{ id: 9, groupName: 'A9' }], nextCursor: null, hasMore: false });
    }
  });
  const opening = h.app.showFilterGroups(true);
  h.app.filterGroupOptions.selected = { id: 700, groupName: '当前分组' };
  h.app.searchFilterGroups('A');
  assert.equal(h.calls.length, 1, 'remote search is debounced');
  assert.equal(h.calls[0].options.signal.aborted, true);
  h.flushTimers();
  await settle();
  assert.equal(h.app.filterGroupItems.value[0].id, 700);
  assert.equal(h.app.filterGroupItems.value[1].id, 4);
  await h.app.loadMoreFilterGroups();
  assert.deepEqual(Array.from(h.app.filterGroupOptions.items, group => group.id), [4, 9]);
  old.resolve(response({ items: [{ id: 99, groupName: '过期结果' }], nextCursor: null, hasMore: false }));
  await opening;
  assert.deepEqual(Array.from(h.app.filterGroupOptions.items, group => group.id), [4, 9]);
  assert.equal(h.app.editGroupOptions.items.length, 0);
  assert.equal(h.app.groups.value.length, 0);
  h.app.openUserDialog({ id: 1, name: '用户', groupId: 808, groupName: '独立编辑分组' });
  assert.equal(h.app.editGroupItems.value[0].groupName, '独立编辑分组');
  assert.equal(h.app.filterGroupItems.value[0].id, 700);
});

test('selected group detail queries cannot replace another user dialog selection', async () => {
  const old = deferred();
  const h = harness(url => url === '/api/groups/800' ? old.promise : undefined);
  h.app.openUserDialog({ id: 1, name: '甲', groupId: 800 });
  h.app.openUserDialog({ id: 2, name: '乙', groupId: 900, groupName: '乙分组' });
  old.resolve(response({ id: 800, groupName: '甲分组' }));
  await settle();
  assert.equal(h.app.editGroupItems.value[0].id, 900);
  assert.equal(h.calls[0].options.signal.aborted, true);
});

test('partial batch deletion displays exact counts and retries only failures, including HTTP 422', async () => {
  let batchCall = 0;
  const h = harness((url, options) => {
    if (!url.endsWith('/deleteBatch')) return;
    const ids = JSON.parse(options.body);
    batchCall++;
    if (batchCall === 1) {
      assert.deepEqual(ids, [1, 2, 3]);
      return response({ requestedCount: 3, uniqueCount: 3, successCount: 2, failureCount: 1, deletedCount: 1,
        successfulIds: [1, 2], alreadyAbsentIds: [2], failures: [{ id: 3, errorCode: 'BUSY', message: '请稍后重试' }] }, 207);
    }
    assert.deepEqual(ids, [3], 'successful and already-absent IDs must never be retried');
    return response({ requestedCount: 1, uniqueCount: 1, successCount: 0, failureCount: 1, deletedCount: 0,
      successfulIds: [], alreadyAbsentIds: [], failures: [{ id: 3, errorCode: 'BUSY', message: '仍在处理中' }] }, 422);
  });
  h.app.selectedUsers.value = [{ id: 1 }, { id: 2 }, { id: 3 }];
  await h.app.handleBatchDelete();
  assert.match(h.app.batchSummary.value, /成功 2 项（本次删除 1 项、已不存在 1 项），失败 1 项/);
  assert.equal(h.app.selectedUsers.value.length, 0);
  await h.app.retryFailedDeletes();
  assert.equal(h.app.batchResult.value.failureCount, 1);
  assert.equal(h.messages.filter(message => message.type === 'error').length, 0);
  assert.equal(h.calls.filter(call => call.url.includes('/users/page')).length, 1, 'all-failed retry does not reload users');
});

test('mutations reject duplicate submissions; object URLs are revoked on replacement, close, and unmount', async () => {
  const saving = deferred();
  const h = harness(url => url.endsWith('/users/add') ? saving.promise : undefined);
  h.app.openUserDialog();
  h.app.userForm.name = '新用户';
  h.app.handleFileChange({ raw: new Blob(['one'], { type: 'image/png' }) });
  h.app.handleFileChange({ raw: new Blob(['two'], { type: 'image/png' }) });
  assert.deepEqual(h.revoked, ['blob:fixture-1']);
  const first = h.app.submitUser();
  await h.app.submitUser();
  await settle();
  assert.equal(h.calls.filter(call => call.url.endsWith('/users/add')).length, 1);
  saving.resolve(response(null));
  await first;
  assert.deepEqual(h.revoked, ['blob:fixture-1', 'blob:fixture-2']);
  h.app.openUserDialog();
  h.app.handleFileChange({ raw: new Blob(['three']) });
  h.unmount();
  assert.equal(h.revoked.at(-1), 'blob:fixture-3');
});

test('mutation invalidates an in-flight count and a late count cannot restore the stale value', async () => {
  const oldCount = deferred();
  let countCalls = 0;
  const h = harness(url => {
    if (url === '/api/users/count') return ++countCalls === 1 ? oldCount.promise : response(9);
  });
  const entering = h.app.handleSelect('overview');
  await settle();
  await h.app.handleDeleteUser({ id: 10, name: '计数变化' });
  assert.equal(h.app.totalUserCount.value, 9);
  oldCount.resolve(response(10));
  await entering;
  assert.equal(h.app.totalUserCount.value, 9);
  assert.equal(h.calls.find(call => call.url === '/api/users/count').options.signal.aborted, true);
});

test('400, 409, 429 and 503 reads produce one actionable error without automatic retries', async () => {
  for (const status of [400, 409, 429, 503]) {
    const h = harness(url => url.includes('/users/page') ? response(null, status) : undefined);
    await h.mount();
    await settle();
    assert.equal(h.calls.length, 2);
    assert.equal(h.messages.filter(message => message.type === 'error').length, 1);
    assert.equal(h.app.usersLoading.value, false);
  }
});

test('failed filters clear stale rows and block stale actions until a successful refresh', async () => {
  let failing = true;
  const h = harness(url => url.includes('/page') && failing ? response(null, 503) : undefined);
  h.app.users.value = [{ id: 7, name: '原分组人员' }];
  h.app.selectedUsers.value = h.app.users.value;
  h.app.searchForm.groupId = 8;
  await h.app.applyUsersFilter();
  assert.equal(h.app.users.value.length, 0);
  assert.equal(h.app.selectedUsers.value.length, 0);
  assert.ok(h.app.usersError.value);
  await h.app.handleDeleteUser({ id: 7, name: '原分组人员' });
  h.app.openUserDialog({ id: 7, name: '原分组人员' });
  assert.equal(h.app.userDialog.visible, false);
  assert.equal(h.calls.some(call => call.options.method === 'DELETE'), false);
  h.app.groups.value = [{ id: 3, groupName: '原分组' }];
  await h.app.changeGroupPage(2);
  assert.equal(h.app.groups.value.length, 0);
  assert.ok(h.app.groupsError.value);
  await h.app.handleDeleteGroup({ id: 3, groupName: '原分组' });
  assert.equal(h.calls.some(call => call.options.method === 'DELETE'), false);
  failing = false;
  await h.app.loadUsers();
  await h.app.loadGroups();
  assert.equal(h.app.usersError.value, '');
  assert.equal(h.app.groupsError.value, '');
  assert.equal(h.app.canSelectUser(), true);
});

test('startup waits for the server session and never loads admin data after 401', async () => {
  const me = deferred();
  const h = harness(url => url === '/api/me' ? me.promise : undefined);
  const startup = h.mount();
  assert.deepEqual(h.calls.map(call => call.url), ['/api/me']);
  assert.equal(h.app.authenticated.value, false);
  me.resolve(response(null, 401));
  await startup;
  assert.equal(h.location.href, 'login.html');
  assert.equal(h.calls.length, 1);
  assert.equal(h.app.authenticated.value, false);
  assert.equal(h.calls[0].options.credentials, 'same-origin');
});

test('concurrent writes share one CSRF request and attach same-origin credentials', async () => {
  const token = deferred();
  const h = harness(url => url === '/api/csrf' ? token.promise : undefined);
  h.app.openUserDialog({ id: 7, name: '编辑' });
  h.app.groupForm.groupName = '新分组';
  const userWrite = h.app.submitUser();
  const groupWrite = h.app.submitGroup();
  assert.deepEqual(h.calls.map(call => call.url), ['/api/csrf']);
  token.resolve(response({ token: 'shared-token', headerName: 'X-CSRF-TOKEN' }));
  await Promise.all([userWrite, groupWrite]);
  const writes = h.calls.filter(call => call.options.method === 'POST');
  assert.equal(writes.length, 2);
  for (const call of writes) {
    assert.equal(call.options.headers.get('X-CSRF-TOKEN'), 'shared-token');
    assert.equal(call.options.credentials, 'same-origin');
  }
  assert.equal(h.calls.filter(call => call.url === '/api/csrf').length, 1);
});

test('403 write is not replayed; a manual retry fetches a new CSRF token', async () => {
  const h = harness(url => url.includes('/users/update/') ? response(null, 403) : undefined);
  h.app.openUserDialog({ id: 7, name: '编辑' });
  await h.app.submitUser();
  assert.deepEqual(h.calls.map(call => call.url), ['/api/csrf', '/api/users/update/7']);
  assert.equal(h.app.userDialog.visible, true);
  assert.equal(h.messages.filter(message => message.type === 'error').length, 1);
  await h.app.submitUser();
  assert.equal(h.calls.filter(call => call.url === '/api/csrf').length, 2);
  assert.equal(h.calls.filter(call => call.url === '/api/users/update/7').length, 2);
});

test('logout is a CSRF-protected server mutation and only then redirects', async () => {
  const ending = deferred();
  const h = harness(url => url === '/api/logout' ? ending.promise : undefined);
  const logout = h.app.handleCommand('logout');
  await settle();
  await h.app.handleCommand('logout');
  assert.equal(h.location.href, undefined);
  const write = h.calls.find(call => call.url === '/api/logout');
  assert.equal(write.options.method, 'POST');
  assert.equal(write.options.headers.get('X-CSRF-TOKEN'), 'fixture-csrf');
  assert.equal(h.calls.filter(call => call.url === '/api/logout').length, 1);
  ending.resolve(response(null));
  await logout;
  assert.equal(h.location.href, 'login.html');
});

test('login prevents duplicate submissions and navigates immediately after the protected POST', async () => {
  const firstToken = deferred();
  let tokenReads = 0;
  const h = harness(url => {
    if (url === '/api/csrf' && ++tokenReads === 1) return firstToken.promise;
  }, true);
  h.app.form.username = 'admin';
  h.app.form.password = 'test-password';
  const login = h.app.handleLogin();
  await h.app.handleLogin();
  assert.equal(h.calls.length, 1);
  firstToken.resolve(response({ token: 'pre-login', headerName: 'X-CSRF-TOKEN' }));
  await login;
  assert.deepEqual(h.calls.map(call => call.url), ['/api/csrf', '/api/login']);
  assert.equal(h.calls[1].options.headers['X-CSRF-TOKEN'], 'pre-login');
  assert.equal(h.calls.every(call => call.options.credentials === 'same-origin'), true);
  assert.equal(JSON.parse(h.calls[1].options.body).password, 'test-password');
  assert.equal(h.app.form.password, '');
  assert.equal(h.location.href, 'index.html');
  assert.deepEqual(h.replacements, ['index.html']);
  await h.app.handleLogin();
  assert.equal(h.calls.filter(call => call.url === '/api/login').length, 1);
  assert.equal(h.app.loading.value, false);
});

test('failed login shows the server error and never automatically retries credentials', async () => {
  const h = harness(url => url === '/api/login' ? response(null, 401) : undefined, true);
  h.app.form.username = 'admin';
  h.app.form.password = 'incorrect';
  await h.app.handleLogin();
  assert.deepEqual(h.calls.map(call => call.url), ['/api/csrf', '/api/login']);
  assert.equal(h.location.href, undefined);
  assert.equal(h.messages.filter(message => message.type === 'error').length, 1);
  assert.equal(h.app.loading.value, false);
  assert.equal(h.app.form.username, 'admin');
  assert.equal(h.app.form.password, 'incorrect');
  assert.equal(h.replacements.length, 0);
});

test('opening login with an existing session uses an uncached same-origin probe and replaces the login page', async () => {
  const h = harness(undefined, true);
  h.app.form.password = 'unsent-form-value';
  await h.mount();
  assert.deepEqual(h.calls.map(call => call.url), ['/api/me']);
  assert.equal(h.calls[0].options.credentials, 'same-origin');
  assert.equal(h.calls[0].options.cache, 'no-store');
  assert.deepEqual(h.replacements, ['index.html']);
  assert.equal(h.app.form.password, '');
  assert.equal(h.app.checkingSession.value, false);
  assert.equal(h.app.sessionError.value, '');
  assert.equal(h.pendingTimerDelays().length, 0);
});

test('a 401 session probe leaves the login form available without treating it as a login failure', async () => {
  const h = harness(url => url === '/api/me' ? response(null, 401) : undefined, true);
  await h.mount();
  assert.deepEqual(h.calls.map(call => call.url), ['/api/me']);
  assert.equal(h.app.checkingSession.value, false);
  assert.equal(h.app.sessionError.value, '');
  assert.equal(h.replacements.length, 0);
  assert.equal(h.messages.length, 0);
  h.app.form.username = 'admin';
  h.app.form.password = 'fixture-password';
  await h.app.handleLogin();
  assert.deepEqual(h.calls.map(call => call.url), ['/api/me', '/api/csrf', '/api/login']);
  assert.deepEqual(h.replacements, ['index.html']);
});

test('an in-flight session probe blocks duplicate probes and login submissions, then releases the form', async () => {
  const pending = deferred();
  const h = harness(url => url === '/api/me' ? pending.promise : undefined, true);
  h.app.form.username = 'admin';
  h.app.form.password = 'fixture-password';
  const probing = h.mount();
  await h.app.checkSession();
  await h.app.handleLogin();
  assert.deepEqual(h.calls.map(call => call.url), ['/api/me']);
  assert.equal(h.app.checkingSession.value, true);
  assert.deepEqual(h.pendingTimerDelays(), [5000]);
  pending.resolve(response(null, 401));
  await probing;
  assert.equal(h.app.checkingSession.value, false);
  await h.app.handleLogin();
  assert.equal(h.calls.filter(call => call.url === '/api/login').length, 1);
});

test('session probe errors and malformed identities are recoverable with an explicit retry', async () => {
  for (const failure of [() => Promise.reject(new Error('offline')), () => response(null, 503),
    () => response({ username: '' }), () => ({ ok: true, status: 200, json: async () => { throw new Error('bad JSON'); } })]) {
    let failing = true;
    const h = harness(url => url === '/api/me' ? failing ? failure() : response(null, 401) : undefined, true);
    h.app.form.username = 'admin';
    h.app.form.password = 'unsubmitted-fixture';
    await h.mount();
    assert.ok(h.app.sessionError.value);
    assert.equal(h.app.checkingSession.value, false);
    assert.equal(h.replacements.length, 0);
    assert.equal(h.app.form.password, 'unsubmitted-fixture');
    failing = false;
    await h.app.checkSession();
    assert.equal(h.calls.length, 2, 'only an explicit retry should make another probe');
    assert.equal(h.app.sessionError.value, '');
    assert.equal(h.app.checkingSession.value, false);
    assert.equal(h.pendingTimerDelays().length, 0);
  }
});

test('session probe timeout releases login even when fetch ignores abort, and a late result cannot override success', async () => {
  const oldProbe = deferred();
  const h = harness(url => url === '/api/me' ? oldProbe.promise : undefined, true);
  const probing = h.mount();
  h.flushTimers();
  await probing;
  assert.equal(h.calls[0].options.signal.aborted, true);
  assert.match(h.app.sessionError.value, /超时/);
  assert.equal(h.app.checkingSession.value, false);
  h.app.form.username = 'admin';
  h.app.form.password = 'fixture-password';
  await h.app.handleLogin();
  assert.deepEqual(h.replacements, ['index.html']);
  oldProbe.resolve(response({ username: 'stale-identity' }));
  await settle();
  assert.deepEqual(h.replacements, ['index.html'], 'the late probe must not navigate again');
  assert.equal(h.app.sessionError.value, '');
  assert.equal(h.app.loading.value, false);
  assert.equal(h.app.form.password, '');
});

test('a timed-out session probe cannot finish a newer probe or navigate while it is pending', async () => {
  const first = deferred(), second = deferred();
  let reads = 0;
  const h = harness(url => url === '/api/me' ? [first, second][reads++].promise : undefined, true);
  const firstRun = h.mount();
  h.flushTimers();
  await firstRun;
  const secondRun = h.app.checkSession();
  first.resolve(response({ username: 'obsolete' }));
  await settle();
  assert.equal(h.app.checkingSession.value, true);
  assert.equal(h.replacements.length, 0);
  second.resolve(response(null, 401));
  await secondRun;
  assert.equal(h.app.checkingSession.value, false);
  assert.equal(h.app.sessionError.value, '');
});

test('unmount cancels the session probe and suppresses its late navigation', async () => {
  const pending = deferred();
  const h = harness(url => url === '/api/me' ? pending.promise : undefined, true);
  const probing = h.mount();
  h.unmount();
  assert.equal(h.calls[0].options.signal.aborted, true);
  assert.equal(h.pendingTimerDelays().length, 0);
  pending.resolve(response({ username: 'late' }));
  await probing;
  assert.equal(h.replacements.length, 0);
  assert.equal(h.app.sessionError.value, '');
});
