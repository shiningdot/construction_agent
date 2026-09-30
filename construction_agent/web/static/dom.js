/* 展示层公共 DOM 工具。

   全部走 textContent 写入，不拼 innerHTML——本页要渲染的是从事故报道里
   抽取的自由文本，里面出现 `<`、`&` 或成对的尖括号都很正常，拼字符串
   等于把数据当代码执行。

   `overview.js` 至今还带着一份等价的内联副本（早于本文件存在）。两份
   实现目前不会同时加载，故不冲突；统一留到 M1 收尾时做。 */

(function (global) {
  'use strict';

  function $(id) {
    return document.getElementById(id);
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
    return node;
  }

  /* el('div', { cls: 'x', text: '…', attrs: { title: '…' } }, [child, …]) */
  function el(tag, opts, children) {
    const node = document.createElement(tag);
    const o = opts || {};
    if (o.cls) node.className = o.cls;
    if (o.text != null) node.textContent = String(o.text);
    if (o.attrs) {
      Object.keys(o.attrs).forEach(function (k) {
        const v = o.attrs[k];
        if (v != null) node.setAttribute(k, String(v));
      });
    }
    (children || []).forEach(function (c) {
      if (c) node.appendChild(c);
    });
    return node;
  }

  function num(n) {
    if (n == null) return '—';
    return Number(n).toLocaleString('zh-CN');
  }

  /* 按 HTTP 错误码给出可读提示——直接甩「加载失败」用户无从判断是
     库里没有这条、还是服务没起来。 */
  function fetchJson(url) {
    return fetch(url, { headers: { Accept: 'application/json' } })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (body) {
          if (!r.ok) {
            const e = new Error(body.message || ('HTTP ' + r.status));
            e.status = r.status;
            e.body = body;
            throw e;
          }
          return body;
        });
      });
  }

  /* 空值统一显示占位符，并带 hover 说明——「原报告未提及」与「字段为 0」
     是两件事，页面上要能分开（蓝图 05 §4.1）。 */
  function blank(text, hint) {
    return el('span', {
      cls: 'blank',
      text: text || '—',
      attrs: { title: hint || '原报告未提及' },
    });
  }

  global.$ = $;
  global.clear = clear;
  global.el = el;
  global.num = num;
  global.fetchJson = fetchJson;
  global.blank = blank;
})(window);
