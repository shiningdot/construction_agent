/* CAIEMS 概览与检索页。
 *
 * 分工：Jinja 只出静态骨架，本文件负责把 /api/* 的数据填进去。
 * 好处是筛选、翻页、排序都只走一次 JSON 往返，不整页刷新。
 *
 * 两条实现约定：
 *   1. 所有来自数据库的文本一律用 textContent 写入，不拼 innerHTML。
 *      事故标题里出现 < 或 & 不算罕见，拼接就会变成注入点。
 *   2. 筛选状态写进 location.hash，刷新与分享链接都能还原视图。
 */
(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);

  // ══════════════════════════════════════════════════════════
  // 状态
  // ══════════════════════════════════════════════════════════

  // 多选筛选维度。**必须与 /api/filters 里"带 count 的那些键"一一对应**：
  // 这里少一维，后端多给的那组值就白算了——蓝图 04 §256 的筛选条画了五格
  // （关键词 / 地区 / 等级 / 类型 / 工程类型），原先只渲染前三维，
  // 工程类型与施工阶段两维在后端与致因页都有、唯独列表页漏了。
  // （`/api/filters` 比这五个多返回一个 `sort`，那是下拉选项集、不是多选维度，
  //  故"一一对应"的口径是"带 count 的键"，不是"返回的全部键"。）
  const MULTI_KEYS = [
    'province', 'severity', 'accident_type', 'project_type', 'construction_phase',
  ];

  /* 分页尺寸的两个界。**必须与后端 `accidents_query.py` 的同名常量一致**
     ——前端在 `readHash` 里 clamp 一次、后端在 `ListParams.from_request` 里再
     clamp 一次，两份数字对不上时，hash 里写的 size 与列表实际返回的条数就会
     不一致（且不一致的方向是"前端以为改了、后端没认"）。
     §2.5.73「同一个语义的 N 个实现」的账：三处引用（state 初值 / readHash /
     writeHash 的省略判断）共用这一个来源，check_2599 的 A6 段跨语言核它与
     后端常量相等。 */
  const DEFAULT_SIZE = 50;
  const MAX_SIZE = 200;

  const state = {
    q: '',
    province: [],
    severity: [],
    accident_type: [],
    project_type: [],
    construction_phase: [],
    year_from: '',
    year_to: '',
    has_causes: false,
    sort: 'date_desc',
    page: 1,
    size: DEFAULT_SIZE,
  };

  let overviewData = null;

  // ══════════════════════════════════════════════════════════
  // 小工具
  // ══════════════════════════════════════════════════════════

  /* URL 属性的协议白名单。

     本页要写进 href/src 的值来自采集到的原始网页与主库字段——直接
     setAttribute 等于把数据当代码执行（javascript: 可执行脚本、data: 可
     换出文档）。故凡带协议头而不是 http(s)/mailto/tel/blob 的值，一律
     **不写该属性**：渲染成纯文本，与 textContent 那条纪律是同一件事的两面。

     下面这段与另一份副本必须逐字相同（dom.js ↔ overview.js；两份 el() 是
     历史遗留的等价拷贝，**且已双向分叉**，合并一事登记在蓝图 07 的 U1 表）
     ——check_2595 的 A4 段核"逐字相同"这一条。 */
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
    opts = opts || {};
    if (opts.cls) node.className = opts.cls;
    if (opts.text != null) node.textContent = opts.text;
    if (opts.attrs) {
      for (const k in opts.attrs) {
        if (URL_ATTRS.indexOf(String(k).toLowerCase()) >= 0 &&
            safeUrlValue(opts.attrs[k]) == null) continue;   // 危险协议 ⇒ 不写属性
        node.setAttribute(k, opts.attrs[k]);
      }
    }
    if (opts.on) {
      for (const k in opts.on) node.addEventListener(k, opts.on[k]);
    }
    (children || []).forEach((c) => { if (c) node.appendChild(c); });
    return node;
  }

  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

  function num(v) {
    return (v == null) ? '—' : Number(v).toLocaleString('zh-CN');
  }

  function jsonGet(url) {
    return fetch(url, { headers: { Accept: 'application/json' } })
      .then((r) => r.json().then((body) => {
        if (!r.ok) throw Object.assign(new Error(body.message || r.statusText), { body });
        return body;
      }));
  }

  // ══════════════════════════════════════════════════════════
  // hash ↔ 状态
  // ══════════════════════════════════════════════════════════

  function readHash() {
    const p = new URLSearchParams(location.hash.replace(/^#/, ''));
    state.q = p.get('q') || '';
    state.year_from = p.get('year_from') || '';
    state.year_to = p.get('year_to') || '';
    state.has_causes = p.get('has_causes') === '1';
    state.sort = p.get('sort') || 'date_desc';
    state.page = Math.max(1, parseInt(p.get('page') || '1', 10) || 1);
    state.size = Math.min(MAX_SIZE, Math.max(
      1, parseInt(p.get('size') || String(DEFAULT_SIZE), 10) || DEFAULT_SIZE));
    MULTI_KEYS.forEach((k) => { state[k] = p.getAll(k).filter(Boolean); });
  }

  function writeHash() {
    const p = new URLSearchParams();
    if (state.q) p.set('q', state.q);
    MULTI_KEYS.forEach((k) => state[k].forEach((v) => p.append(k, v)));
    if (state.year_from) p.set('year_from', state.year_from);
    if (state.year_to) p.set('year_to', state.year_to);
    if (state.has_causes) p.set('has_causes', '1');
    if (state.sort !== 'date_desc') p.set('sort', state.sort);
    if (state.page > 1) p.set('page', String(state.page));
    if (state.size !== DEFAULT_SIZE) p.set('size', String(state.size));

    const next = p.toString();
    const url = next ? '#' + next : location.pathname;
    history.replaceState(null, '', url);
  }

  function toQuery() {
    const p = new URLSearchParams();
    if (state.q) p.set('q', state.q);
    MULTI_KEYS.forEach((k) => state[k].forEach((v) => p.append(k, v)));
    if (state.year_from) p.set('year_from', state.year_from);
    if (state.year_to) p.set('year_to', state.year_to);
    if (state.has_causes) p.set('has_causes', 'true');
    p.set('sort', state.sort);
    p.set('page', String(state.page));
    p.set('size', String(state.size));
    return p.toString();
  }

  // ══════════════════════════════════════════════════════════
  // 健康检查
  // ══════════════════════════════════════════════════════════

  /* 库不可用时徽标的呈现。`dom.js` 里有一份等价实现（`applyDbDown`）——
     概览页的 `{% block scripts %}` 没有引 dom.js，本文件是自包含 IIFE，
     所以这份不能省。**两处必须一起改**，第四十一轮就是两处一起改的：
     改前两份都无条件读 `h.tables.accidents`，而 `/api/health` 在空壳库上
     报 `ok: true` + `tables: {}`，徽标于是显示「NaN 条」并报成「缺索引」，
     把用户引去跑 add_indexes.py——**指错方向**。 */
  function applyDbDown(chip, text, info) {
    chip.className = 'db-chip is-error';
    text.textContent = '数据库不可用';
    // 三种来路的文案由后端在 503 体里给出（`message` + `hint`），照原样显示
    const body = (info && info.body) || info || {};
    chip.title = body.hint ? `${body.message}\n${body.hint}`
                           : (body.message || (info && info.message) || '');
  }

  function loadHealth() {
    const chip = $('dbChip');
    const text = $('dbChipText');
    return jsonGet('/api/health')
      .then((h) => {
        if (h.ok === false) return applyDbDown(chip, text, h);   // 兜底
        const file = h.db_path.split('/').pop();
        const missing = h.missing_indexes || [];
        text.textContent = `${file} · ${num((h.tables || {}).accidents)} 条`;
        chip.title = h.db_path + (missing.length
          ? `\n缺索引：${missing.join(', ')}\n${h.hint}` : '\n索引齐备');
        chip.className = 'db-chip ' + (missing.length ? 'is-warn' : 'is-ok');
        if (missing.length) {
          $('footMeta').textContent =
            `⚠ 缺 ${missing.length} 个索引，列表筛选会明显变慢（scripts/add_indexes.py）`;
        }
      })
      .catch((e) => {
        applyDbDown(chip, text, e);
      });
  }

  // ══════════════════════════════════════════════════════════
  // 统计卡
  // ══════════════════════════════════════════════════════════

  function renderStats(ov) {
    const t = ov.totals;
    const years = ov.year.filter((y) => y.count > 0);
    const span = years.length ? `${years[0].key}–${years[years.length - 1].key}` : '—';
    const cov = t.accidents ? ((t.causes_covered / t.accidents) * 100).toFixed(1) : '0';

    const cards = [
      { label: '事故总数', value: num(t.accidents), sub: `${span} · ${years.length} 个年份有记录` },
      { label: '死亡人数', value: num(t.deaths),
        sub: `${num(t.deaths_recorded)} 条有死亡记录` },
      { label: '受伤人数', value: num(t.injuries),
        sub: `${num(t.injuries_recorded)} 条有受伤记录` },
      { label: '涉伤亡事故', value: num(t.casualty_any),
        sub: `占全部 ${t.accidents ? ((t.casualty_any / t.accidents) * 100).toFixed(0) : 0}%` },
      { label: '致因编码', value: num(t.causes), accent: true,
        sub: `覆盖 ${num(t.causes_covered)} 条（${cov}%）` },
      { label: '平均每事故', value: (t.causes_covered ? (t.causes / t.causes_covered).toFixed(1) : '—'),
        sub: '条 CAICS 编码' },
      { label: '数据来源', value: num(ov.source.length), sub: '个采集渠道' },
    ];

    const host = $('stats');
    clear(host);
    cards.forEach((c) => {
      host.appendChild(el('div', { cls: 'stat' + (c.accent ? ' is-accent' : '') }, [
        el('div', { cls: 'stat-label', text: c.label }),
        el('div', { cls: 'stat-value', text: c.value }),
        el('div', { cls: 'stat-sub', text: c.sub }),
      ]));
    });
  }

  // ══════════════════════════════════════════════════════════
  // 分布图
  // ══════════════════════════════════════════════════════════

  const CHARTS = [
    { key: 'severity', label: '事故等级',
      note: '等级取自原文表述。空字符串计为「未标注」，不并入一般事故。',
      color: (r) => `var(--sev-${({ '特别重大事故': 1, '重大事故': 2, '较大事故': 3, '一般事故': 4 })[r.key] || 'x'})` },
    { key: 'accident_type', label: '事故类型', limit: 14,
      note: '类型由 Step1 提取归一。显示前 14 类，其余归入「其他」。' },
    { key: 'caics_layer', label: 'CAICS 层',
      note: '五层编码量。M 管理 > H 人因 > F 设施 > T 技术 > C 合规。',
      color: (r) => `var(--layer-${r.key})` },
    { key: 'evidence_quality', label: '证据等级',
      note: '证据三级分层：直接引用 / 间接推断 / 弱推断。对应 caics_causes.evidence_quality。',
      color: (r) => `var(--ev-${({ direct_quote: 'direct', indirect_infer: 'indirect', weak_infer: 'weak' })[r.key] || 'weak'})` },
    { key: 'year', label: '年份分布', kind: 'spark',
      note: '缺记录的年份补零显示——1991–2004 是采集断档，不是零事故。' },
    { key: 'source', label: '数据来源',
      note: '按采集渠道。名单网站、案例集与地方应急厅各占一块。' },
  ];

  let activeChart = 'severity';

  function renderChartTabs() {
    const host = $('chartTabs');
    clear(host);
    CHARTS.forEach((c) => {
      host.appendChild(el('button', {
        cls: c.key === activeChart ? 'is-active' : '',
        text: c.label,
        attrs: { type: 'button', role: 'tab' },
        on: { click: () => { activeChart = c.key; renderChartTabs(); renderChart(); } },
      }));
    });
  }

  function renderChart() {
    if (!overviewData) return;
    const cfg = CHARTS.find((c) => c.key === activeChart);
    const host = $('chartArea');
    $('chartNote').textContent = cfg.note || '';
    clear(host);

    const rows = overviewData[cfg.key] || [];
    if (!rows.length) {
      host.appendChild(el('div', { cls: 'empty', text: '无数据' }));
      return;
    }
    host.appendChild(cfg.kind === 'spark' ? buildSpark(rows) : buildBars(rows, cfg));
  }

  function buildBars(rows, cfg) {
    let items = rows.filter((r) => r.count > 0);
    if (cfg.limit && items.length > cfg.limit) {
      const head = items.slice(0, cfg.limit);
      const tail = items.slice(cfg.limit);
      head.push({ key: `其他（${tail.length} 类）`, count: tail.reduce((s, r) => s + r.count, 0) });
      items = head;
    }
    const max = Math.max(...items.map((r) => r.count), 1);

    const box = el('div', { cls: 'bars' });
    items.forEach((r) => {
      const fill = el('div', { cls: 'bar-fill' });
      fill.style.width = ((r.count / max) * 100).toFixed(2) + '%';
      if (cfg.color) {
        const c = cfg.color(r);
        if (c && c.indexOf('undefined') === -1) fill.style.background = c;
      }
      box.appendChild(el('div', { cls: 'bar-row' }, [
        el('div', { cls: 'bar-name', text: r.label || r.key, attrs: { title: r.label || r.key } }),
        el('div', { cls: 'bar-track' }, [fill]),
        el('div', { cls: 'bar-count', text: num(r.count) }),
      ]));
    });
    return box;
  }

  // 年份用内联 SVG：49 个点，横条完全画不下，且断档需要视觉上留白。
  function buildSpark(rows) {
    const W = 1000, H = 170, PAD_B = 26, PAD_T = 12, GAP = 2;
    const max = Math.max(...rows.map((r) => r.count), 1);
    const step = W / rows.length;
    const barW = Math.max(2, step - GAP);

    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.setAttribute('class', 'spark');
    svg.setAttribute('preserveAspectRatio', 'none');

    const mk = (t, attrs) => {
      const n = document.createElementNS('http://www.w3.org/2000/svg', t);
      for (const k in attrs) n.setAttribute(k, attrs[k]);
      return n;
    };

    // 基线
    svg.appendChild(mk('line', {
      x1: 0, y1: H - PAD_B, x2: W, y2: H - PAD_B, class: 'axis',
    }));

    const plotH = H - PAD_B - PAD_T;
    rows.forEach((r, i) => {
      const h = r.count > 0 ? Math.max(1.5, (r.count / max) * plotH) : 1.5;
      const rect = mk('rect', {
        x: (i * step).toFixed(2),
        y: (H - PAD_B - h).toFixed(2),
        width: barW.toFixed(2),
        height: h.toFixed(2),
        rx: 1,
      });
      if (r.count === 0) rect.setAttribute('class', 'is-gap');
      const title = mk('title', {});
      title.textContent = `${r.key}: ${r.count} 条` + (r.count === 0 ? '（无记录）' : '');
      rect.appendChild(title);
      svg.appendChild(rect);
    });

    // 横轴刻度：每 5 年
    rows.forEach((r, i) => {
      const y = parseInt(r.key, 10);
      if (y % 5 !== 0) return;
      svg.appendChild(mk('text', {
        x: (i * step + barW / 2).toFixed(2), y: H - 8,
        'text-anchor': 'middle',
      })).textContent = r.key;
    });

    const box = el('div');
    box.appendChild(svg);
    return box;
  }

  // ══════════════════════════════════════════════════════════
  // 筛选器
  // ══════════════════════════════════════════════════════════

  const FIELD_LABELS = {
    province: '地区',
    severity: '等级',
    accident_type: '类型',
    project_type: '工程类型',
    construction_phase: '施工阶段',
  };

  function renderFilters(opts) {
    const host = $('filterChips');
    clear(host);

    // 直接走 MULTI_KEYS：原先把这行写成上文的字面副本，两处一旦不同步，
    // chips 区与 URL/hash、重置逻辑认的维度就不一样了（改这里就等于改全部）。
    MULTI_KEYS.forEach((key) => {
      const values = opts[key] || [];
      if (!values.length) return;

      // `data-key` 是给核查脚本的锚点：没有它，chips 只能按「第几个」定位，
      // 「后端给了五个维度、前端只画了三个」这种缺口就永远测不出来。
      const chips = el('div', { cls: 'chips', attrs: { 'data-key': key } }, [
        el('span', { cls: 'chips-label', text: FIELD_LABELS[key] }),
      ]);

      values.forEach((v) => {
        const on = state[key].indexOf(v.value) > -1;
        const chip = el('label', {
          cls: 'chip' + (on ? ' is-on' : ''),
          attrs: { title: `${v.label}：${num(v.count)} 条` },
        }, [
          el('input', { attrs: { type: 'checkbox' } }),
          el('span', { text: v.label }),
          el('span', { cls: 'n', text: num(v.count) }),
        ]);
        chip.querySelector('input').checked = on;
        chip.querySelector('input').addEventListener('change', () => {
          toggleMulti(key, v.value);
        });
        chips.appendChild(chip);
      });

      host.appendChild(chips);
    });

    // 排序选项
    const sortSel = $('f_sort');
    clear(sortSel);
    (opts.sort || []).forEach((s) => {
      sortSel.appendChild(el('option', {
        text: s.label, attrs: { value: s.value, selected: s.value === state.sort ? '' : null },
      }));
    });
    sortSel.value = state.sort;
  }

  function toggleMulti(key, value) {
    const arr = state[key];
    const i = arr.indexOf(value);
    if (i > -1) arr.splice(i, 1); else arr.push(value);
    state.page = 1;
    writeHash();
    loadFilters();   // 重绘 chips 的选中态
    loadList();
  }

  function syncFormFromState() {
    $('f_q').value = state.q;
    $('f_year_from').value = state.year_from;
    $('f_year_to').value = state.year_to;
    $('f_has_causes').checked = state.has_causes;
    $('f_sort').value = state.sort;
  }

  /* 丢掉 filters 里没有的筛选值。

     场景是分享链接过期：数据更新后省份改名，链接里的旧值就不认了。后端对
     未识别的省份键是**静默忽略**的（`province.filter_clause` 的 docstring 写着
     理由：过期的键不该让页面崩），列表会照常返回全部 2,623 条。于是页面停在
     自相矛盾的状态——URL 说筛了某个省，列表却是全部，而且没有任何提示。

     这里把 state 收敛到「筛选器真有的值」，然后重写 hash、重载列表，让它自洽。
     判据与后端一致（都以 /api/filters 为准）：后端 meta 回显的也是生效键，
     两边说的是同一件事。 */
  function pruneUnknown(opts) {
    if (!MULTI_KEYS.some((k) => opts[k])) return;   // 响应结构不对，别乱清
    let changed = false;
    MULTI_KEYS.forEach((key) => {
      const allowed = new Set((opts[key] || []).map((v) => v.value));
      const kept = state[key].filter((v) => allowed.has(v));
      if (kept.length !== state[key].length) {
        state[key] = kept;
        changed = true;
      }
    });
    if (changed) {
      state.page = 1;
      writeHash();
      loadList();
    }
  }

  function loadFilters() {
    return jsonGet('/api/filters').then((opts) => {
      pruneUnknown(opts);
      renderFilters(opts);
    }).catch((e) => {
      // 筛选器选项是辅助功能，失败不该拦住列表——但也不能一声不吭：chips
      // 空着时用户分不清"这里本来就没有可选值"和"加载挂了"。写进 chips 区，
      // 与本页列表侧的失败提示（init 末端的 chartArea）同一范式。
      $('filterChips').textContent = '筛选选项加载失败：' + e.message;
    });
  }

  // ══════════════════════════════════════════════════════════
  // 列表
  // ══════════════════════════════════════════════════════════

  const SEV_CLASS = {
    '特别重大事故': 'sev-1', '重大事故': 'sev-2',
    '较大事故': 'sev-3', '一般事故': 'sev-4',
  };

  function severityBadge(sev) {
    if (!sev) return el('span', { cls: 'badge sev-x', text: '未标注' });
    return el('span', {
      cls: 'badge ' + (SEV_CLASS[sev] || 'sev-x'),
      text: sev, attrs: { title: sev },
    });
  }

  function placeCell(item) {
    const inner = [el('span', { text: item.province_label || '未标注' })];
    if (item.province_raw) {
      inner.push(el('span', {
        cls: 'raw', text: item.province_raw,
        attrs: { title: '原始值：' + item.province_raw },
      }));
    }
    return el('td', { cls: 'place' }, inner);
  }

  // 工序：中文名来自后端查 `enum_definitions`，前端不硬编码编码表。
  // 无值（主库 421 条，16%）显示占位符；悬停给出编码，便于与库对账。
  function taskCell(item) {
    if (!item.work_task_label) {
      return el('td', { cls: 'col-task num-none', text: '—' });
    }
    return el('td', {
      cls: 'col-task',
      text: item.work_task_label,
      // 列宽受限会被省略，悬停必须能看到完整名称——只给编码等于逼用户去猜
      attrs: {
        title: item.work_task_code
          ? `${item.work_task_label}（${item.work_task_code}）`
          : item.work_task_label,
      },
    });
  }

  function numericCell(v) {
    if (v == null) return el('td', { cls: 'col-num num-none', text: '—' });
    if (v === 0) return el('td', { cls: 'col-num num-zero', text: '0' });
    return el('td', { cls: 'col-num', text: num(v) });
  }

  function renderList(data) {
    const body = $('gridBody');
    clear(body);

    $('resultCount').innerHTML = '';
    const rc = $('resultCount');
    rc.appendChild(document.createTextNode('共 '));
    rc.appendChild(el('b', { text: num(data.meta.total) }));
    rc.appendChild(document.createTextNode(' 条'));

    if (!data.items.length) {
      const tr = el('tr');
      tr.appendChild(el('td', { attrs: { colspan: '9' } }, [
        el('div', { cls: 'empty' }, [
          el('strong', { text: '没有匹配的事故' }),
          el('span', { text: '试试放宽年份范围，或取消部分地区 / 等级筛选。' }),
        ]),
      ]));
      body.appendChild(tr);
      renderPager(data.meta);
      return;
    }

    data.items.forEach((it) => {
      // 标题是指向详情页的链接。用 <a href> 而不是点击事件：中键新开、
      // 右键复制链接、浏览器前进后退都跟着能用，而详情页的「返回列表」
      // 正靠 history.back() 回到带筛选状态的这一页
      const titleCell = el('td', { cls: 'title-cell' }, [
        el('a', {
          cls: 'title-main',
          text: it.title || '（无标题）',
          attrs: { href: '/accident/' + encodeURIComponent(it.id || '') },
        }),
        el('div', { cls: 'title-meta', text: it.id || '' }),
      ]);

      const tr = el('tr', {}, [
        titleCell,
        el('td', { text: it.date || '—' }),
        placeCell(it),
        el('td', { text: it.accident_type || '—' }),
        taskCell(it),
        el('td', {}, [severityBadge(it.severity)]),
        numericCell(it.deaths),
        numericCell(it.injuries),
        it.cause_count
          ? el('td', { cls: 'col-num', text: String(it.cause_count) })
          : el('td', { cls: 'col-num num-zero', text: '0' }),
      ]);
      body.appendChild(tr);
    });

    renderPager(data.meta);
  }

  function renderPager(meta) {
    const host = $('pager');
    clear(host);
    if (meta.pages <= 1) {
      if (meta.total > 0) {
        host.appendChild(el('span', { cls: 'info', text: `共 ${num(meta.total)} 条` }));
      }
      return;
    }

    const go = (p) => {
      state.page = p;
      writeHash();
      loadList();
      window.scrollTo({ top: $('searchPanel').offsetTop - 70, behavior: 'smooth' });
    };

    const btn = (text, page, opts) => {
      opts = opts || {};
      const b = el('button', {
        text, attrs: { type: 'button' },
        on: opts.disabled ? {} : { click: () => go(page) },
      });
      if (opts.cls) b.className = opts.cls;
      if (opts.disabled) b.disabled = true;
      return b;
    };

    host.appendChild(btn('上一页', meta.page - 1, { disabled: meta.page <= 1 }));

    // 页码窗口：首页、末页固定，当前页前后各 2 页
    const win = new Set([1, meta.pages]);
    for (let p = meta.page - 2; p <= meta.page + 2; p++) {
      if (p >= 1 && p <= meta.pages) win.add(p);
    }
    const pages = [...win].sort((a, b) => a - b);

    let prev = 0;
    pages.forEach((p) => {
      if (p - prev > 1) host.appendChild(el('span', { cls: 'gap', text: '…' }));
      host.appendChild(btn(String(p), p, { cls: p === meta.page ? 'is-current' : '' }));
      prev = p;
    });

    host.appendChild(btn('下一页', meta.page + 1, { disabled: meta.page >= meta.pages }));
    host.appendChild(el('span', {
      cls: 'info',
      text: `第 ${meta.page} / ${meta.pages} 页 · 每页 ${meta.size} 条 · 共 ${num(meta.total)} 条`,
    }));
  }

  function loadList() {
    return jsonGet('/api/accidents?' + toQuery())
      .then(renderList)
      .catch((e) => {
        const body = $('gridBody');
        clear(body);
        body.appendChild(el('tr', {}, [
          el('td', { attrs: { colspan: '8' } }, [
            el('div', { cls: 'empty' }, [
              el('strong', { text: '查询失败' }),
              el('span', { text: e.message }),
            ]),
          ]),
        ]));
      });
  }

  // ══════════════════════════════════════════════════════════
  // 数据质量
  // ══════════════════════════════════════════════════════════

  const KIND_LABELS = {
    renormalized: '地址已归一',
    overseas: '境外',
    unanchored: '认不出来',
  };

  function renderQuality(ov, audit, img) {
    const dq = ov.data_quality;
    const pg = ov.province_group;

    $('qualityHint').textContent =
      `${pg.distinct_raw_values} 个原始取值 → ${pg.standard.length} 个省级单位 + 3 个分组`;

    const host = $('qualityBody');
    clear(host);

    const cards = [
      { h: '省份归一', v: num(pg.by_group.standard),
        p: `原始 province 列有 ${pg.distinct_raw_values} 个取值，其中 ${audit.by_kind.renormalized || 0} 条是地址级脏值，已归入所属省份。` },
      { h: '境外及港澳台', v: num(pg.by_group.overseas),
        p: '按来源可追溯，不作为省份参与筛选统计。' },
      { h: '无法归一', v: num(pg.by_group.unnormalized + pg.by_group.empty),
        p: '含 province 为空的记录。筛选器单列一组，不并入任何省份。' },
      { h: '年份缺失', v: num(dq.year_unlabeled),
        p: '无日期或格式异常，不计入年份序列。' },
    ];

    // 图片覆盖率是同一类信息：这条数据线收了多少、缺口在哪。放进质量面板
    // 而不是单开一块，是因为它和上面四张回答的是同一个问题——「这批数据
    // 有多全」。工作库没建时整张卡不出现，而不是显示一个 0：没有图片索引
    // 与「一张图都没有」是两件事，混在一起会谎报采集完整度。
    if (img && img.available) {
      cards.push({
        h: '现场图片', v: num(img.files),
        p: `覆盖 ${num(img.accidents_with_images)} 条事故`
           + `（${(img.coverage * 100).toFixed(1)}%）；其中 `
           + `${num(img.accidents_with_images_legacy_only)} 条只有早期目录`
           + `（images/、imageV2/）里的图，没有最终版。`,
      });
    }

    const grid = el('div', { cls: 'quality-grid' });
    cards.forEach((c) => {
      grid.appendChild(el('div', { cls: 'qcard' }, [
        el('h3', { text: c.h }),
        el('div', { cls: 'v', text: c.v }),
        el('p', { text: c.p }),
      ]));
    });
    host.appendChild(grid);

    // 逐条明细
    const table = el('table', { cls: 'audit-table' });
    table.appendChild(el('thead', {}, [
      el('tr', {}, [
        el('th', { text: '原始值' }),
        el('th', { text: '条数' }),
        el('th', { text: '归一结果' }),
        el('th', { text: '判定' }),
      ]),
    ]));

    const tb = el('tbody');
    audit.rows.forEach((r) => {
      tb.appendChild(el('tr', {}, [
        el('td', { cls: 'raw', text: r.raw }),
        el('td', { text: num(r.count) }),
        el('td', { text: r.resolved === '__empty__' ? '（空）' : r.resolved }),
        el('td', {}, [el('span', { cls: 'kind', text: KIND_LABELS[r.kind] || r.kind })]),
      ]));
    });
    table.appendChild(tb);
    host.appendChild(table);
  }

  function loadQuality(ov) {
    // 两个端点并行取，但**层级不同**：`/api/province/audit` 顶层直接就是统计量
    // （`by_kind` / `rows`），`/api/images/stats` 的统计量在响应体的 `data` 下
    // （`{ok, data:{files, available, ...}}`）。早先这条没解 `data` 这一层，
    // `img.available` 恒为 `undefined`，整张「现场图片」卡因此从来没出现过。
    // 图片统计失败（工作库没建）不该拖垮整个质量面板，所以那一路单独 catch 成
    // null，由 renderQuality 决定要不要出卡。
    return Promise.all([
      jsonGet('/api/province/audit'),
      jsonGet('/api/images/stats').then((r) => (r && r.data) || null)
        .catch(() => null),
    ]).then(([a, img]) => renderQuality(ov, a, img))
      .catch((e) => {
        // 走到这里只剩 `/api/province/audit` 失败（图片统计那一路已被上面单独
        // 接成 null）。同样不能静默：面板空白分不清"没有数据"和"加载失败"。
        $('qualityBody').textContent = '质量面板加载失败：' + e.message;
      });
  }

  // ══════════════════════════════════════════════════════════
  // 启动
  // ══════════════════════════════════════════════════════════

  function bindForm() {
    $('filters').addEventListener('submit', (e) => {
      e.preventDefault();
      state.q = $('f_q').value.trim();
      state.year_from = $('f_year_from').value.trim();
      state.year_to = $('f_year_to').value.trim();
      state.has_causes = $('f_has_causes').checked;
      state.sort = $('f_sort').value;
      state.page = 1;
      writeHash();
      loadList();
    });

    $('f_sort').addEventListener('change', () => {
      state.sort = $('f_sort').value;
      state.page = 1;
      writeHash();
      loadList();
    });

    /* 重置 = 把 state 的每一个键写回文件顶部 `const state = {…}` 的初值。
       **这份清单必须与那份声明一一对应**：漏一个键，那个维度就"重置不掉"，
       而 `writeHash()` 又会把非默认值写回 hash —— 用户以为全清了，URL 里却还
       留着它。`size` 就是这么漏的（§2.5.99 实测），而它偏偏是**唯一没有控件**
       的维度（页面 21 个 id 里没有 `f_size`），用户连"看见它还在"的办法都没有。
       check_2599 的 A5 段钉住"重置清单键集 == state 声明键集"。 */
    $('resetBtn').addEventListener('click', () => {
      MULTI_KEYS.forEach((k) => { state[k] = []; });
      state.q = ''; state.year_from = ''; state.year_to = '';
      state.has_causes = false; state.sort = 'date_desc'; state.page = 1;
      state.size = DEFAULT_SIZE;
      writeHash();
      syncFormFromState();
      loadFilters();
      loadList();
    });

    window.addEventListener('hashchange', () => {
      readHash();
      syncFormFromState();
      loadFilters();
      loadList();
    });
  }

  function init() {
    readHash();
    syncFormFromState();
    renderChartTabs();
    bindForm();

    loadHealth();
    loadFilters();
    loadList();

    jsonGet('/api/overview')
      .then((ov) => {
        overviewData = ov;
        renderStats(ov);
        renderChart();
        loadQuality(ov);
        $('footMeta').textContent =
          `${ov.totals.accidents.toLocaleString('zh-CN')} 条事故 · ` +
          `${ov.totals.causes.toLocaleString('zh-CN')} 条致因编码`;
      })
      .catch((e) => {
        $('chartArea').textContent = '概览加载失败：' + e.message;
      });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
