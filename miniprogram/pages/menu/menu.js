const api = require('../../utils/api');
const cart = require('../../utils/cart');
const config = require('../../config');

const GRADS = [
  'linear-gradient(135deg,#E85518,#FF9B5C)',
  'linear-gradient(135deg,#C2410C,#F97316)',
  'linear-gradient(135deg,#16A34A,#4ADE80)',
  'linear-gradient(135deg,#B45309,#F59E0B)',
  'linear-gradient(135deg,#DC2626,#F87171)',
  'linear-gradient(135deg,#7C3AED,#A78BFA)',
  'linear-gradient(135deg,#0EA5E9,#67E8F9)',
  'linear-gradient(135deg,#DB2777,#F9A8D4)',
];

const SPICY = ['辣', '麻', '水煮', '香辣', '酸辣'];

Page({
  data: {
    loading: true,
    shop: null,
    categories: [],      // [{id, name, dishes:[{..., grad, qty}]}]
    activeCat: '',
    catScrollInto: '',
    dishScrollInto: '',
    cartCount: 0,
    cartTotal: '0.00',
    table: null,
    addMode: false,
    orderId: 0,
    // 规格弹层
    specShow: false,
    spec: null,          // { name, price, groups: [[label, value, selected]], grad }
    specQty: 1,
    specSelected: {},    // 选项分组 -> 选中值
  },

  onLoad(options) {
    const app = getApp();
    this.setData({ addMode: app.globalData.addMode, orderId: app.globalData.order ? app.globalData.order.id : 0 });
    this.resolveTable(options);
    this.loadMenu();
  },

  onShow() {
    this.refreshCart();
    // 补单模式下若订单已结束，退出补单模式
    const app = getApp();
    if (this.data.addMode && !app.globalData.order) {
      this.setData({ addMode: false });
    }
  },

  resolveTable(options) {
    const app = getApp();
    const scene = options && options.scene;
    const tableId = options && options.table_id;
    if (app.globalData.table) {
      this.setData({ table: app.globalData.table });
      return;
    }
    if (scene) {
      api.request({ url: '/api/customer/tables/by-scene/' + encodeURIComponent(scene), noAuth: true })
        .then((table) => {
          app.setTable(table);
          this.setData({ table });
        })
        .catch((e) => {
          wx.showModal({ title: '桌台码无效', content: e.message, showCancel: false });
        });
    } else if (tableId) {
      api.request({ url: '/api/customer/tables/by-scene/' + tableId, noAuth: true })
        .then((table) => {
          app.setTable(table);
          this.setData({ table });
        })
        .catch(() => {});
    }
  },

  loadMenu() {
    api.request({ url: '/api/customer/menu', noAuth: true })
      .then((data) => {
        const cats = data.categories.map((c) => ({
          id: c.id,
          name: c.name,
          dishes: c.dishes.map((d, i) => ({
            ...d,
            image: d.image && d.image.indexOf('http') !== 0 ? config.BASE_URL + d.image : (d.image || ''),
            grad: GRADS[(d.id + i) % GRADS.length],
            first: d.name.charAt(0),
            badge: c.name === '本店特色' ? '招牌' : (SPICY.some((s) => d.name.indexOf(s) >= 0) ? '辣' : ''),
          })),
        }));
        this.setData({
          shop: data.shop,
          categories: cats,
          activeCat: cats.length ? cats[0].id : '',
          loading: false,
        });
        this.refreshCart();
        // 渲染完成后量算分区偏移；后续图片加载完成（onImgLoad）会再次重算
        setTimeout(() => this.calcSecTops(), 120);
      })
      .catch((e) => {
        this.setData({ loading: false });
        wx.showToast({ title: e.message, icon: 'none' });
      });
  },

  refreshCart() {
    const items = cart.list();
    const map = {};
    items.forEach((it) => {
      map[it.dishId] = (map[it.dishId] || 0) + it.qty;
    });
    const categories = this.data.categories.map((c) => ({
      ...c,
      dishes: c.dishes.map((d) => ({ ...d, qty: map[d.id] || 0 })),
    }));
    this.setData({
      categories,
      cartCount: cart.count(),
      cartTotal: cart.total().toFixed(2),
    });
  },

  tapCategory(e) {
    const id = e.currentTarget.dataset.id;
    this.setData({ activeCat: id, dishScrollInto: 'sec-' + id });
    // 动画结束后清除 scroll-into-view，保证再次点击同一分类也能触发滚动；随后重算分区偏移
    setTimeout(() => {
      if (this.data.dishScrollInto) this.setData({ dishScrollInto: '' });
    }, 500);
    setTimeout(() => this.calcSecTops(), 600);
  },

  onDishScroll(e) {
    // 滚动时高亮当前分类（取滚动位置匹配分区；分区偏移未量算时跳过，避免指针乱跳）
    const scrollTop = e.detail.scrollTop;
    const cats = this.data.categories;
    if (!cats.length || !this._secTops) return;
    let current = cats[0].id;
    cats.forEach((c) => {
      const top = this._secTops['sec-' + c.id];
      if (top !== undefined && scrollTop >= top - 10) current = c.id;
    });
    if (current !== this.data.activeCat) this.setData({ activeCat: current });
  },

  onDishScrollEnd() {
    this.calcSecTops();
  },

  onImgLoad() {
    // 菜品图片异步加载会改变分区高度，加载完成后重算偏移（去抖）
    clearTimeout(this._imgTimer);
    this._imgTimer = setTimeout(() => this.calcSecTops(), 200);
  },

  calcSecTops() {
    const q = wx.createSelectorQuery().in(this);
    q.select('.dish-list').boundingClientRect();
    q.select('.dish-list').scrollOffset();
    q.selectAll('.dish-sec').boundingClientRect();
    q.exec((res) => {
      const svRect = res[0] || {};
      const offset = res[1] || {};
      const rects = res[2] || [];
      if (!rects.length) return;
      // boundingClientRect 是视口坐标：内容偏移 = 分区视口位置 − 滚动区视口位置 + 当前滚动量
      const base = (svRect.top || 0) - (offset.scrollTop || 0);
      const tops = {};
      rects.forEach((r, i) => {
        const id = 'sec-' + this.data.categories[i].id;
        tops[id] = r.top - base;
      });
      this._secTops = tops;
    });
  },

  // ---- 加菜逻辑 ----
  onAdd(e) {
    const dish = e.currentTarget.dataset.dish;
    if (dish.option_template && Object.keys(dish.option_template).length) {
      this.openSpec(dish);
    } else {
      cart.add(dish, 1, {});
      this.refreshCart();
    }
  },

  onSub(e) {
    const dish = e.currentTarget.dataset.dish;
    cart.sub(dish.id, {});
    this.refreshCart();
  },

  onPlus(e) {
    const dish = e.currentTarget.dataset.dish;
    cart.add(dish, 1, {});
    this.refreshCart();
  },

  openSpec(dish) {
    const specSelected = {};
    const groups = Object.keys(dish.option_template).map((label) => ({
      label,
      values: dish.option_template[label].map((v, i) => ({ label: v, selected: i === 0 })),
    }));
    groups.forEach((g) => { specSelected[g.label] = g.values[0].label; });
    this.setData({
      specShow: true,
      spec: { name: dish.name, price: dish.price, grad: dish.grad, emoji: dish.emoji || '' },
      specGroups: groups,
      specSelected,
      specQty: 1,
      specDishId: dish.id,
    });
  },

  tapSpecValue(e) {
    const { label, value } = e.currentTarget.dataset;
    const specSelected = { ...this.data.specSelected, [label]: value };
    const specGroups = this.data.specGroups.map((g) => ({
      ...g,
      values: g.values.map((v) => ({ ...v, selected: g.label === label ? v.label === value : v.selected })),
    }));
    this.setData({ specSelected, specGroups });
  },

  specSub() {
    if (this.data.specQty > 1) this.setData({ specQty: this.data.specQty - 1 });
  },

  specPlus() {
    this.setData({ specQty: this.data.specQty + 1 });
  },

  closeSpec() {
    this.setData({ specShow: false });
  },

  noop() {},

  confirmSpec() {
    const dish = { id: this.data.specDishId, name: this.data.spec.name, price: this.data.spec.price };
    cart.add(dish, this.data.specQty, this.data.specSelected);
    this.setData({ specShow: false });
    this.refreshCart();
    wx.showToast({ title: '已加入', icon: 'success', duration: 600 });
  },

  // ---- 购物车栏 ----
  goCart() {
    if (this.data.cartCount === 0) {
      wx.showToast({ title: '请先选择菜品', icon: 'none' });
      return;
    }
    if (this.data.addMode) {
      this.submitAdd();
      return;
    }
    if (!this.data.table) {
      wx.showToast({ title: '请从桌台码进入点餐', icon: 'none' });
      return;
    }
    wx.navigateTo({ url: '/pages/cart/cart' });
  },

  // 补单加菜：直接提交
  submitAdd() {
    const app = getApp();
    const orderId = this.data.orderId || (app.globalData.order && app.globalData.order.id);
    if (!orderId) {
      wx.showToast({ title: '订单已结束', icon: 'none' });
      return;
    }
    wx.showLoading({ title: '提交加菜…' });
    api.request({
      url: '/api/customer/orders/' + orderId + '/add',
      method: 'POST',
      data: { items: cart.toOrderItems() },
    }).then((data) => {
      wx.hideLoading();
      cart.clear();
      app.setOrder(data);
      wx.navigateBack();
    }).catch((e) => {
      wx.hideLoading();
      wx.showToast({ title: e.message, icon: 'none' });
    });
  },
});
