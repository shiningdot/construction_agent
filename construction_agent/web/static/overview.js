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

  const MULTI_KEYS = ['province', 'severity', 'accident_type'];

  const state = {
    q: '',
    province: [],
    severity: [],
    accident_type: [],
    year_from: '',
    year_to: '',
    has_causes: false,
    sort: 'date_desc',
    page: 1,
    size: 50,
  };

  let overviewData = null;

  // ══════════════════════════════════════════════════════════
  // 小工具
  // ══════════════════════════════════════════════════════════

  function el(tag, opts, children) {
    const node = document.createElement(tag);
    opts = opts || {};
    if (opts.cls) node.className = opts.cls;
    if (opts.text != null) node.textContent = opts.text;
    if (opts.attrs) {
      for (const k in opts.attrs) node.setAttribute(k, opts.attrs[k]);
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
    state.size = Math.min(200, Math.max(1, parseInt(p.get('size') || '50', 10) || 50));
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
    if (state.size !== 50) p.set('size', String(state.size));

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

  function loadHealth() {
    const chip = $('dbChip');
    const text = $('dbChipText');
    return jsonGet('/api/health')
      .then((h) => {
        const file = h.db_path.split('/').pop();
        const rows = h.tables.accidents;
        text.textContent = `${file} · ${num(rows)} 条`;
        chip.title = h.db_path + (h.missing_indexes.length
          ? `\n缺索引：${h.missing_indexes.join(', ')}\n${h.hint}` : '\n索引齐备');
        chip.className = 'db-chip ' + (h.missing_indexes.length ? 'is-warn' : 'is-ok');
        if (h.missing_indexes.length) {
          $('footMeta').textContent =
            `⚠ 缺 ${h.missing_indexes.length} 个索引，列表筛选会明显变慢（scripts/add_indexes.py）`;
        }
      })
      .catch((e) => {
        chip.className = 'db-chip is-error';
        text.textContent = '数据库不可用';
        chip.title = e.message;
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
  };

  function renderFilters(opts) {
    const host = $('filterChips');
    clear(host);

    ['province', 'severity', 'accident_type'].forEach((key) => {
      const values = opts[key] || [];
      if (!values.length) return;

      const chips = el('div', { cls: 'chips' }, [
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

  function loadFilters() {
    return jsonGet('/api/filters').then(renderFilters).catch(() => {});
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
      const titleCell = el('td', { cls: 'title-cell' }, [
        el('div', { cls: 'title-main', text: it.title || '（无标题）' }),
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

  function renderQuality(ov, audit) {
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
    return jsonGet('/api/province/audit')
      .then((a) => renderQuality(ov, a))
      .catch(() => {});
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

    $('resetBtn').addEventListener('click', () => {
      MULTI_KEYS.forEach((k) => { state[k] = []; });
      state.q = ''; state.year_from = ''; state.year_to = '';
      state.has_causes = false; state.sort = 'date_desc'; state.page = 1;
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
