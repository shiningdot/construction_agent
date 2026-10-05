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

  /* el('div', { id: 'x', cls: 'x', text: '…', attrs: { title: '…' } }, [child, …])

     `id` 单独列出来支持，是因为它最容易踩：opts 只认写死的那几个键，多写的
     键会被**静默丢掉**——不报错、不告警，元素就是没拿到 id，之后 `$('x')`
     返回 null 再一路 null 下去。审核页的致因树与操作回执就这么丢过一次，
     而且是"页面看着正常、只是没内容"的那种丢法。 */
  /* URL 属性的协议白名单。

     本页要写进 href/src 的值来自采集到的原始网页与主库字段——直接
     setAttribute 等于把数据当代码执行（javascript: 可执行脚本、data: 可
     换出文档）。故凡带协议头而不是 http(s)/mailto/tel/blob 的值，一律
     **不写该属性**：渲染成纯文本，与 textContent 那条纪律是同一件事的两面。

     下面这段与另一份副本必须逐字相同（dom.js ↔ overview.js，两份 el() 是
     历史遗留的等价拷贝，统一留到 M1 收尾时做）——check_2595 的 A4 段核它。 */
  const URL_ATTRS = ['href', 'src', 'srcset', 'action', 'formaction', 'poster',
                     'xlink:href', 'data', 'background', 'cite', 'ping',
                     'manifest', 'longdesc', 'usemap'];
  const SAFE_SCHEMES = ['http', 'https', 'mailto', 'tel', 'blob'];
  function safeUrlValue(v) {
    const s = String(v).replace(/[\x00-\x20]/g, '');
    const m = /^([a-z][a-z0-9+.\-]*):/i.exec(s);
    if (!m) return String(v);              // 无协议头 ⇒ 相对路径，安全
    return SAFE_SCHEMES.indexOf(m[1].toLowerCase()) >= 0 ? s : null;
  }

  function el(tag, opts, children) {
    const node = document.createElement(tag);
    const o = opts || {};
    if (o.id) node.id = o.id;
    if (o.cls) node.className = o.cls;
    if (o.text != null) node.textContent = String(o.text);
    if (o.attrs) {
      Object.keys(o.attrs).forEach(function (k) {
        const v = o.attrs[k];
        if (v == null) return;
        if (URL_ATTRS.indexOf(String(k).toLowerCase()) >= 0 &&
            safeUrlValue(v) == null) return;      // 危险协议 ⇒ 不写属性
        node.setAttribute(k, String(v));
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

  /* 顶栏右下角的库状态徽标。它显示的是**最终库**（`/api/health`），跟在
     哪个页面无关，所以放在公共文件里由 dom.js 加载时自动填一次——
     collector 页不读最终库，但用户仍该看到「库在不在、索引齐不齐」。

     `overview.js` 里还留着一份等价的内联实现（早于本文件）——**概览页的
     `{% block scripts %}` 里根本没引 dom.js**，它自带了 `$`/`num`/`jsonGet`
     全套（自包含 IIFE），而本文件是把工具挂到 global。两种模式并存，合并
     是架构选择不是缺陷修复，留到 M1 收尾时做；**两处的库状态呈现必须一起改**
     （第四十一轮就是两处一起改的）。

     第四十一轮之前这里无条件写 `h.tables.accidents`：`/api/health` 在空壳库
     上报 `ok: true` + `tables: {}`，于是徽标显示「xxx.db · NaN 条」，并因为
     `missing_indexes` 有值而报成「缺 3 个索引」、把用户引去跑 add_indexes.py
     ——**指错方向**（库连 `accidents` 表都没有）。后端改成缺表即 503 之后
     这条路径走不到了，但 `is-error` 那一支仍然**只能靠 JS 抛异常掉进 catch
     才出现**：`db-chip` 的三种颜色，后端原先只给得出两种。 */
  function applyDbDown(chip, text, info) {
    chip.className = 'db-chip is-error';
    text.textContent = '数据库不可用';
    /* 三种来路（库文件不存在 / 不是 SQLite 文件 / 缺表）的文案由后端在 503
       体里给出（`message` + `hint`），前端照原样显示。**不在前端另编一套**
       ——编出来的那套正是「指错方向」的来源。`fetchJson` 已把响应体挂在
       `e.body` 上。 */
    const body = (info && info.body) || info || {};
    chip.title = body.hint ? (body.message + '\n' + body.hint)
                           : (body.message || (info && info.message) || '');
  }

  function loadDbChip() {
    const chip = document.getElementById('dbChip');
    const text = document.getElementById('dbChipText');
    if (!chip || !text) return;
    fetchJson('/api/health').then(function (h) {
      if (h.ok === false) return applyDbDown(chip, text, h);   // 兜底，正常走不到
      text.textContent = h.db_path.split('/').pop() + ' · '
                         + num((h.tables || {}).accidents) + ' 条';
      const missing = h.missing_indexes || [];
      chip.title = h.db_path + (missing.length
        ? '\n缺索引：' + missing.join(', ') + '\n' + (h.hint || '')
        : '\n索引齐备');
      chip.className = 'db-chip ' + (missing.length ? 'is-warn' : 'is-ok');
      if (missing.length) {
        const foot = document.getElementById('footMeta');
        if (foot) {
          foot.textContent = '⚠ 缺 ' + missing.length
            + ' 个索引，列表筛选会明显变慢（scripts/add_indexes.py）';
        }
      }
    }).catch(function (e) {
      applyDbDown(chip, text, e);
    });
  }

  global.$ = $;
  global.clear = clear;
  global.el = el;
  global.num = num;
  global.fetchJson = fetchJson;
  global.blank = blank;
  global.loadDbChip = loadDbChip;

  loadDbChip();
})(window);
