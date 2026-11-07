const LABELS = { wuba: '58同城', anjuke: '安居客', ke: '贝壳' };
const URLS = {
  wuba: 'https://rizhao.58.com/ershoufang/',
  anjuke: 'https://m.anjuke.com/rz/sale/',
  ke: 'https://rz.ke.com/ershoufang/',
};

function render(data) {
  const cfg = data;
  const box = document.getElementById('list');
  box.innerHTML = '';
  for (const key of Object.keys(LABELS)) {
    const st = cfg[`status.${key}`] || {};
    const row = document.createElement('div');
    row.className = 'row';
    const left = document.createElement('div');
    left.style.minWidth = '0';
    left.innerHTML = `<div><span class="dot ${st.ok ? 'ok' : 'bad'}"></span>
       <span class="name">${LABELS[key]}</span></div>
       <div class="note">${st.note || '尚未同步'}</div>`;
    const btn = document.createElement('button');
    btn.textContent = '同步';
    btn.onclick = async () => {
      btn.disabled = true; btn.textContent = '…';
      await chrome.runtime.sendMessage({ type: 'sync-one', site: key });
      const fresh = await chrome.runtime.sendMessage({ type: 'status' });
      render(fresh);
    };
    row.appendChild(left); row.appendChild(btn);
    box.appendChild(row);
  }
  if (!cfg.token) {
    const warn = document.createElement('div');
    warn.className = 'note';
    warn.style.color = '#e11d48';
    warn.textContent = '还没填管理口令，请先点「设置」';
    box.appendChild(warn);
  }
}

chrome.runtime.sendMessage({ type: 'status' }, render);

document.getElementById('syncAll').onclick = async (e) => {
  e.target.disabled = true; e.target.textContent = '同步中…';
  await chrome.runtime.sendMessage({ type: 'sync' });
  render(await chrome.runtime.sendMessage({ type: 'status' }));
  e.target.disabled = false; e.target.textContent = '立即同步全部会话';
};
document.getElementById('open58').onclick = () => chrome.tabs.create({ url: URLS.wuba });
document.getElementById('opts').onclick = () => chrome.runtime.openOptionsPage();


document.getElementById('pushPage').onclick = async () => {
  const msg = document.getElementById('pushMsg');
  msg.textContent = '正在读取当前标签页…';
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !/58\.com/.test(tab.url || '')) {
    msg.textContent = '请先打开 58 同城日照二手房页面';
    return;
  }
  try {
    const r = await chrome.tabs.sendMessage(tab.id, { type: 'push-page' });
    msg.textContent = r ? r.note : '页面脚本未就绪，请刷新页面后重试';
  } catch (e) {
    msg.textContent = '页面脚本未就绪，请刷新 58 页面后重试';
  }
};


document.getElementById('pullWuba').onclick = async () => {
  const msg = document.getElementById('pushMsg');
  try {
    await chrome.runtime.sendMessage({ type: 'pull-wuba' });
    msg.textContent = '已打开 58 页面；房源列表正常显示后会自动回传一次';
  } catch (e) { msg.textContent = '启动失败：' + e.message; }
};
