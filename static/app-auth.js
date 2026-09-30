/* Shared authentication adapter; product and review behavior stays in index.html. */
(function (window) {
  'use strict';
  window.createCatalogAuth = function (prefix) {
    const query = new URLSearchParams(window.location.search);
    const storageKey = 'catalog-service-token:' + (prefix || '/');
    const modeKey = 'catalog-app-mode:' + (prefix || '/');
    const read = key => { try { return window.sessionStorage.getItem(key) || ''; } catch (_) { return ''; } };
    const write = (key, value) => { try { window.sessionStorage.setItem(key, value); } catch (_) {} };
    const remove = key => { try { window.sessionStorage.removeItem(key); } catch (_) {} };
    const legacy = query.get('t') || '';
    const appMode = query.get('app_session') === '1' || (!legacy && read(modeKey) === '1');
    let token = '';
    if (appMode) {
      write(modeKey, '1');
      remove(storageKey);
      remove('catalog-service-token:' + prefix); // historical iframe storage key
    } else {
      remove(modeKey);
      token = legacy || read(storageKey) || read('catalog-service-token:' + prefix);
      if (legacy) write(storageKey, legacy);
    }
    if (legacy || query.has('app_session')) {
      query.delete('t'); query.delete('app_session');
      const rest = query.toString();
      window.history.replaceState(null, '', window.location.pathname + (rest ? '?' + rest : ''));
    }
    let pendingSession;
    function session() {
      if (!pendingSession) pendingSession = window.fetch(prefix + '/app-entry/session', {
        credentials: 'same-origin', headers: {'X-Catalog-App': '1'}, cache: 'no-store',
      }).then(async response => {
        if (!response.ok) throw Error('登录已失效，请从 APP 重新进入档口');
        return response.json();
      }).catch(error => { pendingSession = null; throw error; });
      return pendingSession;
    }
    function url(value) {
      if (!appMode || /^(?:data:|blob:)/i.test(value)) return value;
      const parsed = new URL(value, window.location.href);
      if (parsed.origin !== window.location.origin) return value;
      for (const key of ['token', 't', 'auth']) parsed.searchParams.delete(key);
      return parsed.pathname + parsed.search + parsed.hash;
    }
    async function request(value, options) {
      options = options || {};
      const parsed = new URL(value, window.location.href);
      if (parsed.origin !== window.location.origin) throw Error('不允许向其他地址发送管理凭证');
      const headers = new Headers(options.headers || {});
      if (appMode) {
        headers.delete('X-Service-Token');
        headers.delete('X-Ticket-Token');
        headers.set('X-Catalog-App', '1');
        const state = await session();
        if (!['GET','HEAD','OPTIONS'].includes((options.method || 'GET').toUpperCase())) headers.set('X-CSRF-Token', state.csrfToken);
      } else {
        headers.set('X-Service-Token', token);
      }
      const response = await window.fetch(url(value), {...options, headers, credentials: 'same-origin'});
      if (appMode && response.status === 401) {
        pendingSession = null;
        throw Error('登录已失效，请从 APP 重新进入档口');
      }
      return response;
    }
    return {appMode, token, fetch: request, url, session};
  };
})(window);
