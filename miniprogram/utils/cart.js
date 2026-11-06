// 购物车：按「菜品 + 选项组合」为条目，存内存 + 本地缓存
const STORAGE_KEY = 'cart_items';

function keyOf(dishId, options) {
  const opts = options || {};
  const keys = Object.keys(opts).sort();
  const parts = keys.map((k) => k + '=' + opts[k]);
  return dishId + '::' + (parts.length ? parts.join('|') : '');
}

function load() {
  try {
    return wx.getStorageSync(STORAGE_KEY) || {};
  } catch (e) {
    return {};
  }
}

function save(items) {
  wx.setStorageSync(STORAGE_KEY, items);
}

function all() {
  return load();
}

function add(dish, qty, options) {
  const items = load();
  const key = keyOf(dish.id, options);
  const cur = items[key] || {
    dishId: dish.id, name: dish.name, price: dish.price,
    options: options || {}, qty: 0,
  };
  cur.qty += qty;
  items[key] = cur;
  save(items);
  return items;
}

function sub(dishId, options) {
  const items = load();
  const key = keyOf(dishId, options);
  if (items[key]) {
    items[key].qty -= 1;
    if (items[key].qty <= 0) delete items[key];
  }
  save(items);
  return items;
}

function remove(dishId, options) {
  const items = load();
  delete items[keyOf(dishId, options)];
  save(items);
  return items;
}

function clear() {
  save({});
}

function count() {
  const items = load();
  return Object.keys(items).reduce((s, k) => s + items[k].qty, 0);
}

function total() {
  const items = load();
  return Object.keys(items).reduce((s, k) => s + items[k].price * items[k].qty, 0);
}

function list() {
  const items = load();
  return Object.keys(items).map((k) => ({
    key: k,
    dishId: items[k].dishId,
    name: items[k].name,
    price: items[k].price,
    options: items[k].options,
    qty: items[k].qty,
    subtotal: items[k].price * items[k].qty,
  }));
}

// 转为下单接口格式
function toOrderItems() {
  return list().map((it) => ({
    dish_id: it.dishId,
    qty: it.qty,
    options: Object.keys(it.options).length ? it.options : null,
  }));
}

module.exports = { add, sub, remove, clear, count, total, list, toOrderItems, keyOf };
