/* Bookmark Hub 的 Service Worker：只缓存应用外壳，绝不缓存接口数据。
 *
 * 为什么接口一律不缓存：/api/* 的响应里有收藏、签到配置与运行状态，属于账号
 * 主人的私有数据。把它们留在 CacheStorage 里，等于在磁盘上多留一份不受管理
 * 密码保护的副本——换个人打开同一台设备的浏览器就能读到。
 *
 * 外壳（/ 与 /static/*）走 stale-while-revalidate：先用缓存秒开，后台再更新，
 * 下一次访问就是新版本。CACHE_VERSION 改了会清掉所有旧缓存。
 *
 * 内置壁纸（/static/wallpapers/*）例外，走 cache-first：起始页每开一个标签页都要用它，
 * 文件几乎不变，没必要每次都回源校验；重画了壁纸就把 CACHE_VERSION +1。壁纸不预热，
 * 用到哪张缓存哪张。上传的自定义壁纸在 /api/wallpaper，和其它接口一样不经过这里，
 * 由带内容哈希的地址 + HTTP 长缓存负责。
 */
// v17：外壳新增「AI 聊天」标签页（HaloWebUI 的 iframe），响应头里多了 frame-src、frame-ancestors 回到 'none'。
// 缓存里的旧外壳带着旧的 CSP 头，不换版本号的话，框会被旧头挡住。
// v18：外壳新增「发送到 AI 聊天 / 存入笔记」的编辑框与笔记搜索。外壳走的是
// stale-while-revalidate，不换号也会在下一次访问更新；换掉只是让部署后第一次打开就是新的。
// v19：「AI 聊天」的框声明 color-scheme: light dark，HaloWebUI 的深浅色改为跟随浏览器（不再被壁纸带成深色）。
// v20：外壳新增「笔记」标签页（WebObsidian 的 iframe），响应头的 frame-src / connect-src 多了笔记的源。
// v23：设置页改成设置行、状态图标换成线条图标；外壳走 stale-while-revalidate，换号只是让部署后第一次打开就是新的。
// v24：界面换成 v4 设计系统（中性色 + 靛蓝强调色、倒 L 画布、壁纸里的浮动玻璃侧栏、轻页头）。
// v25：「纸与墨」配色（黑白界面 + 信号蓝、暖中性色）、网址面板化、手机浮动底栏。
// v26：图标（favicon / PWA / 主屏）换成墨色方块 + 纸色书签，和侧栏品牌标一致。
// v27：设置页分节（左标题右内容）、弹窗操作条贴底、按压 / 弹出动效。
// v28：首页工具条收进一条胶囊、搜索下拉选中行改中性色。
// v29：键盘选中行的回车提示、标题 / 说明文字的换行均衡。
// v30：分组 / 看板页头左侧的分组图标块。
const CACHE_VERSION = 'bh-shell-v30';
const SHELL = [
  '/',
  '/static/app-v3.css',
  '/static/manifest.webmanifest',
  '/static/icon-192.png',
  '/static/icon-512.png',
];

self.addEventListener('install', (event) => {
  // 预热失败（比如某个文件 404）不该让整个 SW 装不上，逐个忽略错误。
  event.waitUntil(
    caches.open(CACHE_VERSION)
      .then((cache) => Promise.all(SHELL.map((url) => cache.add(url).catch(() => null))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE_VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// 既没缓存又断网时必须回一个真正的 Response：respondWith(undefined) 会抛
// TypeError，控制台里看到的是一个和「断网」无关的报错，徒增排查成本。
function offlineResponse() {
  return new Response('离线：该资源尚未缓存。', {
    status: 503,
    statusText: 'Offline',
    headers: { 'Content-Type': 'text/plain; charset=utf-8' },
  });
}

function isShellRequest(url) {
  return url.pathname === '/' || url.pathname.startsWith('/static/');
}

function isWallpaperRequest(url) {
  return url.pathname.startsWith('/static/wallpapers/');
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  // 跨域、接口、图标代理一律交给网络，SW 不插手也不留痕。
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/')) return;
  if (!isShellRequest(url)) return;

  // 带查询串的导航（分享目标 / 书签小工具带 ?url=…）：每次都是不同的 URL，
  // 写进缓存就等于分享一次多一条，缓存会无限长。这里只读不写，
  // 离线时回退到已缓存的外壳。
  if (url.search) {
    event.respondWith(fetch(request).catch(() => caches.match('/').then((r) => r || offlineResponse())));
    return;
  }

  if (isWallpaperRequest(url)) {
    event.respondWith(
      caches.open(CACHE_VERSION).then((cache) => cache.match(request).then((cached) => cached
        || fetch(request).then((response) => {
          if (response && response.ok && response.type === 'basic') cache.put(request, response.clone());
          return response;
        }).catch(() => offlineResponse())))
    );
    return;
  }

  event.respondWith(
    caches.open(CACHE_VERSION).then((cache) => cache.match(request).then((cached) => {
      const network = fetch(request).then((response) => {
        if (response && response.ok && response.type === 'basic') cache.put(request, response.clone());
        return response;
      }).catch(() => cached || offlineResponse());
      // 有缓存先给缓存（秒开），同时后台刷新；没缓存就等网络。
      return cached || network;
    }))
  );
});
