const KEYS = ['base', 'sharecode', 'token', 'auto'];

chrome.storage.local.get({
  base: 'https://rizhao-house-radar.app.workbuddy.host',
  sharecode: '', token: '', auto: true,
}, (cfg) => {
  for (const k of KEYS) {
    const el = document.getElementById(k);
    if (k === 'auto') el.checked = !!cfg[k];
    else el.value = cfg[k] || '';
  }
});

document.getElementById('save').onclick = () => {
  const data = {};
  for (const k of KEYS) {
    const el = document.getElementById(k);
    data[k] = k === 'auto' ? el.checked : el.value.trim();
  }
  chrome.storage.local.set(data, async () => {
    document.getElementById('msg').textContent = '已保存，正在同步…';
    await chrome.runtime.sendMessage({ type: 'sync' });
    document.getElementById('msg').textContent = '已保存并完成一次同步';
  });
};
