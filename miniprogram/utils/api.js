// 请求封装：自动带 token，401 时重登一次
const config = require('../config');

function request(options) {
  const app = getApp();
  return new Promise((resolve, reject) => {
    const doRequest = (token) => {
      wx.request({
        url: config.BASE_URL + options.url,
        method: options.method || 'GET',
        data: options.data || {},
        header: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: 'Bearer ' + token } : {}),
        },
        success: (res) => {
          if (res.statusCode >= 200 && res.statusCode < 300) {
            resolve(res.data);
          } else if (res.statusCode === 401 && !options.noAuth) {
            app.login().then((ok) => {
              if (ok) doRequest(app.globalData.token);
              else reject(res);
            });
          } else {
            const msg = (res.data && (res.data.detail || res.data.message)) || '请求失败';
            reject(new Error(msg));
          }
        },
        fail: (err) => reject(new Error('网络异常，请检查后端服务是否启动')),
      });
    };
    doRequest(options.noAuth ? '' : app.globalData.token);
  });
}

module.exports = { request, config };
