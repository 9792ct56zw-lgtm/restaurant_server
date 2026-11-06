// 扫码进入页：微信扫一扫 → 解析桌台 → 进入点餐菜单
// 开发调试兜底：开发者工具无法扫摄像头时，直接点选桌台进入
const api = require('../../utils/api');

Page({
  data: {
    shopName: '北相高革胡卜馆',
    tables: [],
    loading: true,
    scanning: false,
  },

  onLoad(options) {
    // 开发工具「编译模式」传 scene=A01 时直接进入菜单
    if (options && options.scene) {
      this.resolveScene(decodeURIComponent(options.scene));
      return;
    }
    this.loadTables();
  },

  loadTables() {
    api.request({ url: '/api/customer/tables', noAuth: true })
      .then((tables) => this.setData({ tables, loading: false }))
      .catch(() => this.setData({ loading: false }));
  },

  onScan() {
    if (this.data.scanning) return;
    this.setData({ scanning: true });
    wx.scanCode({
      onlyFromCamera: false,
      success: (res) => {
        const raw = (res.result || '').trim();
        this.setData({ scanning: false });
        if (!raw) {
          wx.showToast({ title: '未识别到桌台码', icon: 'none' });
          return;
        }
        this.resolveScene(raw);
      },
      fail: () => {
        this.setData({ scanning: false });
        // 开发者工具/模拟器不支持扫码时提示用手动选桌
        wx.showToast({ title: '扫码失败 · 可在下方选择桌台', icon: 'none', duration: 2000 });
      },
    });
  },

  onPickTable(e) {
    const id = e.currentTarget.dataset.id;
    const name = e.currentTarget.dataset.name;
    api.request({ url: '/api/customer/tables/by-scene/' + encodeURIComponent(name), noAuth: true })
      .then((table) => {
        const app = getApp();
        app.setTable(table);
        wx.navigateTo({ url: '/pages/menu/menu?scene=' + encodeURIComponent(name) });
      })
      .catch((err) => wx.showToast({ title: err.message || '桌台不可用', icon: 'none' }));
  },

  resolveScene(scene) {
    // 兼容两种内容：纯桌号「A01」或「scene=A01」
    let code = scene.trim();
    const m = code.match(/scene\s*=\s*([A-Za-z0-9_-]+)/);
    if (m) code = m[1];
    wx.showLoading({ title: '正在进入点餐…' });
    api.request({ url: '/api/customer/tables/by-scene/' + encodeURIComponent(code), noAuth: true })
      .then((table) => {
        wx.hideLoading();
        const app = getApp();
        app.setTable(table);
        wx.navigateTo({ url: '/pages/menu/menu?scene=' + encodeURIComponent(code) });
      })
      .catch((err) => {
        wx.hideLoading();
        wx.showModal({
          title: '桌台码无效',
          content: (err.message || '') + '，可手动选择桌台进入。',
          showCancel: false,
        });
        this.loadTables();
      });
  },
});
