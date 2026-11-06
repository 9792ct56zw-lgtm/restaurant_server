// 顾客端入口：登录 + 全局状态（购物车、桌台、当前订单）
const api = require('./utils/api');
const cart = require('./utils/cart');

App({
  globalData: {
    token: '',
    customer: null,
    table: null,      // { id, name, area, seats }
    order: null,      // 当前进行中订单（进度页数据）
    menu: null,       // 菜单缓存
    addMode: false,   // 是否处于「补单加菜」模式
  },

  onLaunch() {
    this.login();
  },

  login() {
    return new Promise((resolve) => {
      wx.login({
        success: (res) => {
          if (!res.code) { resolve(false); return; }
          api.request({
            url: '/api/customer/login',
            method: 'POST',
            data: { code: res.code },
            noAuth: true,
          }).then((data) => {
            this.globalData.token = data.token;
            this.globalData.customer = data.customer;
            wx.setStorageSync('token', data.token);
            resolve(true);
          }).catch(() => resolve(false));
        },
        fail: () => resolve(false),
      });
    });
  },

  // 确保已登录（未登录则重试一次）
  ensureLogin() {
    if (this.globalData.token) return Promise.resolve(true);
    return this.login();
  },

  setTable(table) {
    this.globalData.table = table;
  },

  setOrder(order) {
    this.globalData.order = order;
  },

  clearOrder() {
    this.globalData.order = null;
    this.globalData.addMode = false;
    cart.clear();
  },
});
