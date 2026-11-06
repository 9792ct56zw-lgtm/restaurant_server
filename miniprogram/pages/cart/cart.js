const api = require('../../utils/api');
const cart = require('../../utils/cart');

Page({
  data: {
    items: [],
    count: 0,
    dishTotal: '0.00',
    total: '0.00',
    remark: '',
    people: 2,
    utensils: 2,
    table: null,
    submitting: false,
  },

  onLoad() {
    const app = getApp();
    this.setData({ table: app.globalData.table });
    this.refresh();
  },

  refresh() {
    const items = cart.list().map((it) => ({
      ...it,
      optionsText: Object.keys(it.options || {})
        .map((k) => it.options[k])
        .join(' / '),
    }));
    const dishTotal = cart.total();
    this.setData({
      items,
      count: cart.count(),
      dishTotal: dishTotal.toFixed(2),
      // 合计 = 菜品合计 + 餐具费（每人 1 元，随用餐人数）
      total: (dishTotal + this.data.people).toFixed(2),
    });
  },

  onSub(e) {
    cart.sub(e.currentTarget.dataset.id, e.currentTarget.dataset.options || {});
    this.refresh();
  },

  onPlus(e) {
    const it = cart.list().find((x) => x.dishId === e.currentTarget.dataset.id
      && JSON.stringify(x.options || {}) === JSON.stringify(e.currentTarget.dataset.options || {}));
    if (it) cart.add({ id: it.dishId, name: it.name, price: it.price }, 1, it.options);
    this.refresh();
  },

  onClear() {
    wx.showModal({
      title: '清空购物车',
      content: '确定清空已选菜品吗？',
      success: (res) => {
        if (res.confirm) {
          cart.clear();
          this.refresh();
        }
      },
    });
  },

  onRemark(e) {
    this.setData({ remark: e.detail.value });
  },

  pickPeople(e) {
    // 餐具数量默认跟随用餐人数（仍可手动微调）；餐具费随人数重算
    const n = Number(e.currentTarget.dataset.n);
    this.setData({ people: n, utensils: n });
    this.refresh();
  },

  utensilSub() {
    if (this.data.utensils > 0) this.setData({ utensils: this.data.utensils - 1 });
  },

  utensilPlus() {
    this.setData({ utensils: this.data.utensils + 1 });
  },

  submit() {
    if (this.data.submitting) return;
    if (!this.data.table) {
      wx.showToast({ title: '未识别桌台，请扫码进入', icon: 'none' });
      return;
    }
    if (cart.count() === 0) {
      wx.showToast({ title: '购物车是空的', icon: 'none' });
      return;
    }
    this.setData({ submitting: true });
    wx.showLoading({ title: '提交订单…' });
    api.request({
      url: '/api/customer/orders',
      method: 'POST',
      data: {
        table_id: this.data.table.id,
        items: cart.toOrderItems(),
        remark: this.data.remark,
        people: this.data.people,
        utensils: this.data.utensils,
      },
    }).then((data) => {
      wx.hideLoading();
      cart.clear();
      const app = getApp();
      app.setOrder(data);
      wx.redirectTo({ url: '/pages/order/order?orderId=' + data.id });
    }).catch((e) => {
      wx.hideLoading();
      this.setData({ submitting: false });
      wx.showToast({ title: e.message, icon: 'none' });
    });
  },
});
