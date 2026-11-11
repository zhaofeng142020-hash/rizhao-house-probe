import { createRouter, createWebHashHistory } from 'vue-router'

// 用 hash 路由，构建后由 FastAPI 直接托管时不需要额外配置 rewrite
const routes = [
  { path: '/', name: 'home', component: () => import('./views/Home.vue'), meta: { title: '今日可留意' } },
  { path: '/anomalies', name: 'anomalies', component: () => import('./views/Anomalies.vue'), meta: { title: '异动专区' } },
  { path: '/reports', name: 'reports', component: () => import('./views/PriceReports.vue'), meta: { title: '每日调价报告' } },
  { path: '/map', name: 'map', component: () => import('./views/MapView.vue'), meta: { title: '小区地图' } },
  { path: '/area', name: 'area', component: () => import('./views/AreaTarget.vue'), meta: { title: '改善 120-150㎡' } },
  { path: '/newhomes', name: 'newhomes', component: () => import('./views/NewHomes.vue'), meta: { title: '新房倒挂' } },
  { path: '/search', name: 'search', component: () => import('./views/SearchView.vue'), meta: { title: '搜索小区' } },
  { path: '/sources', name: 'sources', component: () => import('./views/SourcesView.vue'), meta: { title: '数据来源' } },
  { path: '/community/:id', name: 'community', component: () => import('./views/CommunityDetail.vue'), meta: { title: '小区详情' } },
  { path: '/listing/:id', name: 'listing', component: () => import('./views/ListingDetail.vue'), meta: { title: '房源详情' } },
  { path: '/auction/:id', name: 'auction', component: () => import('./views/AuctionDetail.vue'), meta: { title: '法拍详情' } },
  { path: '/deals', name: 'deals', component: () => import('./views/Deals.vue'), meta: { title: '成交锚点' } },
  { path: '/watchlist', name: 'watchlist', component: () => import('./views/Watchlist.vue'), meta: { title: '观察清单' } },
  { path: '/settings', name: 'settings', component: () => import('./views/Settings.vue'), meta: { title: '推送设置' } },
  { path: '/admin', name: 'admin', component: () => import('./views/Admin.vue'), meta: { title: '管理台' } },
  // 旧链接兼容：原来的「板块地图」就是现在的「小区地图」
  { path: '/market', redirect: '/map' },
]

export default createRouter({
  history: createWebHashHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})
