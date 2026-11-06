const api = require('../../utils/api');
const cfg = require('../../config');

const ITEM_STATUS = {
  pending: { text: '待出餐', cls: 'chip-wait' },
  out: { text: '已出餐', cls: 'chip-done' },
  add_pending: { text: '加菜待确认', cls: 'chip-add' },
};

Page({
  data: {
    loading: true,
    order: null,
    steps: [],
    statusCard: null,
    items: [],
    utensilFee: '0.00',
    total: '0.00',
    timer: null,
  },

  onLoad(options) {
    this.orderId = options.orderId ? Number(options.orderId) : 0;
    this.socket = null;
    this.refresh();
    this.startWatch();
  },

  onUnload() {
    this.stopWatch();
  },

  onShow() {
    // 从补单页回来时刷新
    if (this.orderId) this.refresh();
  },

  getPath() {
    if (this.orderId) return '/api/customer/orders/' + this.orderId;
    return '/api/customer/orders/active?table_id=' + (getApp().globalData.table ? getApp().globalData.table.id : 0);
  },

  refresh() {
    api.request({ url: this.getPath() })
      .then((data) => {
        this.apply(data);
        this.setData({ loading: false });
        const app = getApp();
        app.setOrder(data);
      })
      .catch((e) => {
        this.setData({ loading: false });
        wx.showToast({ title: e.message, icon: 'none' });
      });
  },

  apply(data) {
    const items = (data.items || []).map((it) => ({
      ...it,
      statusText: (ITEM_STATUS[it.status] || {}).text || it.status,
      statusCls: (ITEM_STATUS[it.status] || {}).cls || '',
      optionsText: Object.keys(it.options || {}).map((k) => it.options[k]).join(' / '),
    }));
    this.setData({
      order: data,
      steps: data.steps || [],
      statusCard: data.status_card,
      items,
      utensilFee: (data.utensil_fee || 0).toFixed(2),
      // 合计 = 菜品合计 + 餐具费
      total: ((data.total_amount || 0) + (data.utensil_fee || 0)).toFixed(2),
    });
  },

  // 轮询 + WebSocket 双通道
  startWatch() {
    if (!this.orderId) return;
    this.pollTimer = setInterval(() => {
      if (this.data.order && ['paid', 'cancelled'].indexOf(this.data.order.status) >= 0) return;
      this.refresh();
    }, 6000);
    this.connectWs();
  },

  connectWs() {
    if (!this.orderId) return;
    const task = wx.connectSocket({
      url: cfg.WS_URL + '/ws/order:' + this.orderId,
      fail: () => {},
    });
    task.onMessage((msg) => {
      try {
        const data = JSON.parse(msg.data);
        if (data.type === 'status' && data.order) this.apply(data.order);
      } catch (e) {}
    });
    task.onClose(() => {
      if (this.data.order && ['paid', 'cancelled'].indexOf(this.data.order.status) >= 0) return;
      setTimeout(() => this.connectWs(), 3000);
    });
  },

  stopWatch() {
    if (this.pollTimer) clearInterval(this.pollTimer);
  },

  // 补单加菜
  goAdd() {
    const app = getApp();
    if (!this.data.order || ['paid', 'cancelled'].indexOf(this.data.order.status) >= 0) {
      wx.showToast({ title: '订单已结束，无法加菜', icon: 'none' });
      return;
    }
    app.globalData.addMode = true;
    app.setOrder(this.data.order);
    wx.navigateTo({ url: '/pages/menu/menu' });
  },

  // 取消订单
  goCancel() {
    if (!this.data.order || ['paid', 'cancelled'].indexOf(this.data.order.status) >= 0) return;
    wx.showModal({
      title: '取消订单',
      content: '确定取消本次订单吗？',
      success: (res) => {
        if (!res.confirm) return;
        api.request({
          url: '/api/customer/orders/' + this.data.order.id + '/cancel',
          method: 'POST',
          data: { reason: '顾客取消' },
        }).then((data) => {
          this.apply(data);
          wx.showToast({ title: '订单已取消', icon: 'none' });
        }).catch((e) => wx.showToast({ title: e.message, icon: 'none' }));
      },
    });
  },

  backMenu() {
    wx.reLaunch({ url: '/pages/menu/menu' });
  },
});
