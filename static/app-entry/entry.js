(async function () {
  const code = new URLSearchParams(location.hash.slice(1)).get('code');
  history.replaceState(null, '', location.pathname);
  const status = document.getElementById('status');
  if (!code) { status.textContent = '登录链接无效，请从 APP 重新进入档口。'; return; }
  try {
    const response = await fetch('./exchange', {
      method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-Catalog-App': '1'},
      body: JSON.stringify({code}),
    });
    if (!response.ok) throw Error('登录链接已失效，请从 APP 重新进入档口。');
    const data = await response.json();
    if (data.redirectUrl !== '/?app_session=1') throw Error('登录返回地址无效，请从 APP 重新进入。');
    location.replace(data.redirectUrl);
  } catch (error) {
    status.textContent = error.message || '连接失败，请从 APP 重新进入档口。';
  }
})();
