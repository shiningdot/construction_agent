/* CAIEMS 致因分析页（蓝图 05 §4.2）。
 *
 * 七段：口径条 → 筛选条 → 五层结构 → 19 类明细 → 编码共现 → 证据等级 → 年代趋势。
 *
 * 三条实现约定：
 *
 *   1. **筛选变化即重算，不做前端过滤**。数据源是 SQLite 聚合，前端过滤
 *      意味着要把 16,323 行全量下发——那是旧版 /api/dataset 21.5 MB 的老路。
 *   2. **两个计数口径并排显示**。`count` 是标注条数（行数口径），`accidents`
 *      是出现过该类的事故数（去重口径），差 338（蓝图 05 §2.3）。哪一个在
 *      用必须写在数字旁边，否则读者会以为算错了。
 *   3. **文案只说「常同时出现」**。共现是标注结构，不是致因关系——
 *      CLAUDE.md 定的那几个禁用词，页面上一个都不能出现。
 */
(() => {
  'use strict';

  const { $, clear, el, num, fetchJson } = window;

  // ══════════════════════════════════════════════════════════
  // 状态
  // ══════════════════════════════════════════════════════════

  const MULTI_KEYS = [
    'province', 'severity', 'accident_type', 'project_type', 'construction_phase',
  ];

  const FIELD_LABELS = {
    province: '省份',
    severity: '事故等级',
    accident_type: '事故类型',
    project_type: '工程类型',
    construction_phase: '施工工序',
  };

  const state = {
    province: [], severity: [], accident_type: [],
    project_type: [], construction_phase: [],
    year_from: '', year_to: '',
    order: 'count',
  };

  let framework = null;   // { layers: [{key,label,count,categories}], total, … }
  let reqSeq = 0;         // 丢弃过期响应（连点筛选时后发先至）

  const $pct = (v) => (v * 100).toFixed(1) + '%';

  /* SVG 元素必须走 createElementNS，createElement('svg') 建出来的是
     HTML 未知元素，不会渲染。 */
  const SVG_NS = 'http://www.w3.org/2000/svg';
  function svg(tag, attrs, children) {
    const node = document.createElementNS(SVG_NS, tag);
    Object.keys(attrs || {}).forEach((k) => {
      if (attrs[k] != null) node.setAttribute(k, String(attrs[k]));
    });
    (children || []).forEach((c) => { if (c) node.appendChild(c); });
    return node;
  }

  // ══════════════════════════════════════════════════════════
  // hash ↔ 状态（与列表页同一套约定，便于互相跳转带条件）
  // ══════════════════════════════════════════════════════════

  function readHash() {
    const p = new URLSearchParams(location.hash.replace(/^#/, ''));
    state.year_from = p.get('year_from') || '';
    state.year_to = p.get('year_to') || '';
    state.order = p.get('order') === 'lift' ? 'lift' : 'count';
    MULTI_KEYS.forEach((k) => { state[k] = p.getAll(k).filter(Boolean); });
  }

  function writeHash() {
    const p = new URLSearchParams();
    MULTI_KEYS.forEach((k) => state[k].forEach((v) => p.append(k, v)));
    if (state.year_from) p.set('year_from', state.year_from);
    if (state.year_to) p.set('year_to', state.year_to);
    if (state.order === 'lift') p.set('order', 'lift');
    const next = p.toString();
    history.replaceState(null, '', next ? '#' + next : location.pathname);
  }

  function queryString() {
    const p = new URLSearchParams();
    MULTI_KEYS.forEach((k) => state[k].forEach((v) => p.append(k, v)));
    if (state.year_from) p.set('year_from', state.year_from);
    if (state.year_to) p.set('year_to', state.year_to);
    return p.toString();
  }

  function hasFilter() {
    return MULTI_KEYS.some((k) => state[k].length) ||
           !!state.year_from || !!state.year_to;
  }

  // ══════════════════════════════════════════════════════════
  // ① 口径条
  // ══════════════════════════════════════════════════════════

  function statCell(label, value, hint, cls) {
    return el('div', { cls: 'scope-cell' + (cls ? ' ' + cls : '') }, [
      el('span', { cls: 'scope-num', text: num(value) }),
      el('span', { cls: 'scope-label', text: label, attrs: { title: hint || label } }),
    ]);
  }

  function renderScope(s) {
    const host = clear($('scopeBar'));
    host.appendChild(statCell('条事故', s.accidents, '当前筛选范围内的事故记录数'));
    host.appendChild(statCell(
      '条致因标注', s.causes,
      '行数口径：caics_causes 里的记录条数。同一事故同一类别下可以有多条独立证据'
    ));
    host.appendChild(statCell(
      '个事故×类别', s.pairs,
      '去重口径：COUNT(DISTINCT accident_id, code)。比标注条数少 '
      + num(s.causes - s.pairs) + ' 个，差额来自同一类别下的多条证据'
    ));
    host.appendChild(statCell(
      '条事故有编码', s.covered,
      '至少有一条 CAICS 编码的事故数'
    ));
    if (s.without_causes > 0) {
      host.appendChild(statCell(
        '条事故无编码', s.without_causes,
        '这些事故不出现在下方任何一张图里', 'is-warn'
      ));
    }

    const eq = $('scopeEcho');
    eq.textContent = s.filtered
      ? `已筛选 · ${num(s.accidents)} 条事故 / ${num(s.causes)} 条标注`
      : `全库 · ${num(s.accidents)} 条事故 / ${num(s.causes)} 条标注`;
  }

  // ══════════════════════════════════════════════════════════
  // ② 筛选条
  // ══════════════════════════════════════════════════════════

  function renderFilters(opts) {
    const host = clear($('filterChips'));

    MULTI_KEYS.forEach((key) => {
      const values = (opts[key] || []).filter((v) => v.count > 0);
      if (!values.length) return;

      const picked = state[key];
      const groups = el('div', { cls: 'chips' }, [
        el('span', { cls: 'chips-label', text: FIELD_LABELS[key] }),
      ]);
      if (picked.length) {
        groups.appendChild(el('span', {
          cls: 'chips-picked',
          text: `已选 ${picked.length}`,
          attrs: { title: picked.join('、') },
        }));
      }

      values.forEach((v) => {
        const on = picked.indexOf(v.value) > -1;
        const chip = el('label', {
          cls: 'chip' + (on ? ' is-on' : ''),
          attrs: {
            'data-key': key,
            'data-value': v.value,
            title: `${v.label}：${num(v.count)} 条`
                   + (on ? '（点击取消）' : '（点击选中）'),
          },
        }, [
          el('input', { attrs: { type: 'checkbox' } }),
          el('span', { text: v.label }),
          el('span', { cls: 'n', text: num(v.count) }),
        ]);
        chip.querySelector('input').checked = on;
        chip.querySelector('input').addEventListener('change', () => {
          toggleMulti(key, v.value);
        });
        groups.appendChild(chip);
      });

      host.appendChild(groups);
    });

    // 年份
    $('f_year_from').value = state.year_from;
    $('f_year_to').value = state.year_to;
  }

  function toggleMulti(key, value) {
    const arr = state[key];
    const i = arr.indexOf(value);
    if (i > -1) arr.splice(i, 1); else arr.push(value);
    writeHash();
    // 重绘整个 chips 区（含「已选 N」）而不是逐个改 class——选项的计数
    // 是全库口径，不随本页筛选变化，不必重新请求 /api/filters
    renderFilters(optsCache);
    loadAll();
  }

  // ══════════════════════════════════════════════════════════
  // ③ 五层结构
  // ══════════════════════════════════════════════════════════

  function barRow(cfg) {
    const { code, name, width, value, sub, cls } = cfg;
    // 计数为 0 时不画条。`.hrow-fill` 有 min-width 保证极小值可见，
    // 但 0 走这条规则会留下一小截竖线，看着像「有一点点」。
    const zero = !width ? ' is-zero' : '';
    return el('div', { cls: 'hrow ' + cls + zero }, [
      el('span', { cls: 'hrow-code', text: code, attrs: { title: code } }),
      el('span', {
        cls: 'hrow-name', text: name, attrs: { title: name },
      }),
      el('span', { cls: 'hrow-track' }, [
        el('span', {
          cls: 'hrow-fill',
          attrs: { style: `width:${width}%` },
        }),
      ]),
      el('span', { cls: 'hrow-val', text: value, attrs: { title: cfg.valHint } }),
      el('span', { cls: 'hrow-sub', text: sub, attrs: { title: cfg.subHint } }),
    ]);
  }

  function renderLayer(byLayer, total) {
    const host = clear($('layerBars'));
    const peak = Math.max(1, ...byLayer.map((l) => l.count));

    byLayer.forEach((l) => {
      host.appendChild(barRow({
        code: l.key,
        name: l.label,
        width: (l.count / peak) * 100,
        value: num(l.count),
        sub: `${$pct(total ? l.count / total : 0)} · ${num(l.accidents)} 条事故`,
        cls: 'layer-' + l.key,
        valHint: `${l.label}：${num(l.count)} 条致因标注（行数口径）`,
        subHint: `占全部致因标注 ${$pct(total ? l.count / total : 0)}（行数口径）；`
               + `去重口径有 ${num(l.accidents)} 条事故出现过该层任一类别`,
      }));
    });

    $('layerNote').textContent =
      `条数口径合计 ${num(total)} 条 · 层序固定不随计数变化`;
  }

  // ══════════════════════════════════════════════════════════
  // ④ 19 类明细
  // ══════════════════════════════════════════════════════════

  function renderCategory(byCategory, total, covered) {
    const host = clear($('categoryBars'));
    const got = {};
    byCategory.forEach((c) => { got[c.code] = c; });

    // 以 framework 的类目为全集（全库出现过的 19 类）：筛选后计数归零的
    // 类别仍列出、显示为 0，否则「这一类在这个子集里完全没有」就看不出来。
    // 注意 distribution 的 by_category 只含计数非零的类，不能拿它当全集。
    //
    // **顺序也照搬 framework**（层内按全库计数降序），这里不重排：筛选时
    // 位置固定、条长变化，才看得出改条件后哪一类掉得最多。
    const all = [];
    (framework ? framework.layers : []).forEach((layer) => {
      layer.categories.forEach((c) => {
        all.push({
          code: c.code, name: c.name, layer: layer.key,
          layer_label: layer.label,
          count: (got[c.code] || {}).count || 0,
          accidents: (got[c.code] || {}).accidents || 0,
          share: (got[c.code] || {}).share || 0,
        });
      });
    });

    const peak = Math.max(1, ...all.map((c) => c.count));

    all.forEach((c) => {
      const row = barRow({
        code: c.code,
        name: c.name,
        width: (c.count / peak) * 100,
        value: num(c.count),
        sub: `${$pct(covered ? c.accidents / covered : 0)} 的事故`,
        cls: 'layer-' + c.layer,
        valHint: `${c.name}：${num(c.count)} 条致因标注（行数口径）`,
        subHint: `去重口径：${num(c.accidents)} 条事故涉及该类，`
               + `占有编码事故数 ${num(covered)} 的 `
               + $pct(covered ? c.accidents / covered : 0),
      });
      // 标注占比并排显示，与事故占比区分口径
      row.querySelector('.hrow-val').appendChild(el('span', {
        cls: 'share',
        text: $pct(c.share),
        attrs: { title: '占全部致因标注的比例（行数口径，与右侧事故占比不同口径）' },
      }));
      host.appendChild(row);
    });
  }

  // ══════════════════════════════════════════════════════════
  // ⑤ 共现
  // ══════════════════════════════════════════════════════════

  function renderCooc(data) {
    const list = clear($('coocList'));

    if (!data.pairs.length) {
      list.appendChild(el('p', { cls: 'empty-note', text: '当前条件下没有达标编码对。' }));
    } else {
      const peak = Math.max(1, ...data.pairs.map((p) => p.count));
      data.pairs.forEach((p) => {
        list.appendChild(el('div', { cls: 'cooc-row' }, [
          el('span', { cls: 'cooc-pair' }, [
            el('span', { cls: 'cooc-code layer-' + p.a.charAt(0), text: p.a }),
            el('span', { cls: 'cooc-plus', text: '+' }),
            el('span', { cls: 'cooc-code layer-' + p.b.charAt(0), text: p.b }),
          ]),
          el('span', { cls: 'cooc-names', text: `${p.a_name} × ${p.b_name}`,
                       attrs: { title: `${p.a_name} × ${p.b_name}` } }),
          el('span', { cls: 'cooc-track' }, [
            el('span', { cls: 'cooc-fill', attrs: { style: `width:${(p.count / peak) * 100}%` } }),
          ]),
          el('span', { cls: 'cooc-n', text: num(p.count),
                       attrs: { title: `共现事故数（去重口径）：${num(p.count)} 条事故同时含这两类` } }),
          el('span', { cls: 'cooc-share', text: $pct(p.joint_share),
                       attrs: { title: `占 ${num(data.scope.accidents)} 条有编码事故的比例` } }),
          el('span', { cls: 'cooc-lift', text: 'lift ' + p.lift.toFixed(2),
                       attrs: { title: 'lift = 实际共现率 / 独立期望率。1 表示与独立出现无异，'
                                     + '>1 表示两者比各自独立时更常一起出现' } }),
        ]));
      });
    }

    $('coocNote').textContent =
      `共现反映的是标注结构，不表示致因之间的关系。`
      + `按共现事故数排序看主结构；切到 lift 找「出现不多但强绑定」的对`
      + `（共 ${num(data.pair_total)} 对，此处列出 ${num(data.pairs.length)} 对，`
      + `少于 ${data.filter.min_count} 条事故支撑的对不进入排序）。`;

    renderMatrix(data);
  }

  function renderMatrix(data) {
    const host = clear($('coocMatrix'));
    const codes = data.codes;
    if (!codes.length) return;

    const CELL = 21, LBL = 26, PAD = 6;
    const size = codes.length;
    const w = LBL + size * CELL + PAD;
    const h = LBL + size * CELL + PAD;
    const peak = Math.max(1, ...data.matrix.map((row) => Math.max(...row)));

    const root = svg('svg', {
      viewBox: `0 0 ${w} ${h}`, width: w, height: h,
      class: 'cooc-svg', role: 'img',
      'aria-label': '编码共现热力图',
    });

    // 层带：列头按层着色，让 19 类聚成 5 段
    codes.forEach((code, i) => {
      const layer = data.layers[i];
      root.appendChild(svg('rect', {
        x: LBL + i * CELL, y: 0, width: CELL, height: LBL - 6,
        class: 'cooc-head layer-' + layer,
      }));
      root.appendChild(svg('text', {
        x: LBL + i * CELL + CELL / 2, y: LBL - 10,
        class: 'cooc-axis', 'text-anchor': 'middle',
      }, [document.createTextNode(code)]));

      root.appendChild(svg('rect', {
        x: 0, y: LBL + i * CELL, width: LBL - 6, height: CELL,
        class: 'cooc-head layer-' + layer,
      }));
      root.appendChild(svg('text', {
        x: LBL - 10, y: LBL + i * CELL + CELL / 2 + 3,
        class: 'cooc-axis', 'text-anchor': 'end',
      }, [document.createTextNode(code)]));
    });

    data.matrix.forEach((row, i) => {
      row.forEach((v, j) => {
        const r = svg('rect', {
          x: LBL + j * CELL, y: LBL + i * CELL,
          width: CELL - 1, height: CELL - 1,
          class: 'cooc-cell',
          // 色深按 sqrt 压：M1 参与的格子动辄上千，线性映射会把其余全压成白。
          // 对角（v=0）走浅底——rect 不写 fill 会被 SVG 默认填成黑色，
          // 21 个黑块看着像「最强的共现」。
          fill: v
            ? `rgba(13,124,107,${(Math.sqrt(v / peak) * 0.92).toFixed(3)})`
            : '#FAF8F4',
        });
        r.appendChild(svg('title', {}, [document.createTextNode(
          i === j
            ? `${codes[i]} ${data.names[i]}：对角为自身，无共现含义`
            : `${codes[i]} × ${codes[j]}：${num(v)} 条事故同时含这两类`
        )]));
        root.appendChild(r);
      });
    });

    host.appendChild(root);
    host.appendChild(el('p', {
      cls: 'cooc-legend',
      text: `色深按共现事故数的平方根映射（最深 ${num(peak)} 条）；`
          + `对角恒为 0，不表示共现。`,
    }));
  }

  // ══════════════════════════════════════════════════════════
  // ⑥ 证据等级
  // ══════════════════════════════════════════════════════════

  const EV_CLS = {
    direct_quote: 'ev-direct',
    indirect_infer: 'ev-indirect',
    weak_infer: 'ev-weak',
  };

  function renderEvidence(list, unknown, total) {
    const host = clear($('evidenceBar'));
    const rows = list.filter((e) => e.count > 0);

    if (!rows.length && !unknown) {
      host.appendChild(el('p', { cls: 'empty-note', text: '当前条件下没有证据记录。' }));
      return;
    }

    // 堆叠条：一条里按三级切分
    const stack = el('div', { cls: 'ev-stack' });
    rows.forEach((e) => {
      stack.appendChild(el('span', {
        cls: 'ev-seg ' + (EV_CLS[e.key] || ''),
        attrs: {
          style: `width:${total ? (e.count / total) * 100 : 0}%`,
          title: `${e.label}：${num(e.count)} 条（${$pct(total ? e.count / total : 0)}）`,
        },
      }));
    });
    host.appendChild(stack);

    const legend = el('div', { cls: 'ev-legend' });
    rows.forEach((e) => {
      legend.appendChild(el('span', { cls: 'ev-item' }, [
        el('i', { cls: 'ev-dot ' + (EV_CLS[e.key] || '') }),
        el('span', { text: e.label }),
        el('b', { text: num(e.count) }),
        el('span', { cls: 'muted', text: $pct(total ? e.count / total : 0) }),
        el('span', {
          cls: 'muted',
          text: `${num(e.accidents)} 条事故`,
          attrs: { title: '去重口径：出现过该等级标注的不同事故数' },
        }),
      ]));
    });

    // 落在三级之外的取值单独列出，不并进任何一档
    if (unknown) {
      legend.appendChild(el('span', { cls: 'ev-item is-warn' }, [
        el('i', { cls: 'ev-dot' }),
        el('span', { text: '未归类等级' }),
        el('b', { text: num(unknown) }),
        el('span', { cls: 'muted', text: '不在三级之内，未并入任何一档' }),
      ]));
    }
    host.appendChild(legend);
  }

  // ══════════════════════════════════════════════════════════
  // ⑦ 年代趋势
  // ══════════════════════════════════════════════════════════

  const LAYER_COLOR = {
    M: '#5B6ABF', F: '#C77D3A', T: '#3E8E7E', H: '#B5544A', C: '#7A6BA8',
  };

  function renderTimeline(t) {
    const host = clear($('timelineChart'));
    const years = t.years;

    if (!years.length) {
      host.appendChild(el('p', { cls: 'empty-note', text: '当前条件下没有带日期的记录。' }));
      $('timelineNote').textContent = '';
      return;
    }

    const W = 900, H = 300, ML = 46, MR = 14, MT = 14, MB = 34;
    const iw = W - ML - MR, ih = H - MT - MB;
    const n = years.length;
    const peakAcc = Math.max(1, ...t.accidents, ...t.by_layer.flatMap((l) => l.counts));
    const x = (i) => ML + (n === 1 ? iw / 2 : (i / (n - 1)) * iw);
    const y = (v) => MT + ih - (v / peakAcc) * ih;

    const root = svg('svg', {
      viewBox: `0 0 ${W} ${H}`, class: 'trend-svg', role: 'img',
      'aria-label': '每年涉及各层致因的事故数',
    });

    // 横向网格 + 刻度
    const ticks = 4;
    for (let k = 0; k <= ticks; k++) {
      const v = Math.round((peakAcc / ticks) * k);
      const yy = y(v);
      root.appendChild(svg('line', {
        x1: ML, y1: yy, x2: W - MR, y2: yy, class: 'trend-grid',
      }));
      root.appendChild(svg('text', {
        x: ML - 8, y: yy + 4, class: 'trend-axis', 'text-anchor': 'end',
      }, [document.createTextNode(num(v))]));
    }

    // 每年总事故数：底色柱，作为分母（占比读不出结构时，样本量要能看见）
    const bw = Math.max(2, (iw / n) * 0.55);
    t.accidents.forEach((v, i) => {
      root.appendChild(svg('rect', {
        x: x(i) - bw / 2, y: y(v), width: bw, height: MT + ih - y(v),
        class: 'trend-base',
      }, [svg('title', {}, [document.createTextNode(
        `${years[i]} 年：${num(v)} 条事故（当年全部记录）`
      )])]));
    });

    // 五层折线
    t.by_layer.forEach((layer) => {
      const pts = layer.counts.map((v, i) => `${x(i)},${y(v)}`).join(' ');
      root.appendChild(svg('polyline', {
        points: pts, fill: 'none',
        stroke: LAYER_COLOR[layer.key] || '#8A827A',
        'stroke-width': 1.8, 'stroke-linejoin': 'round',
      }));
      // 悬停点：折线本身太细，抓不住
      layer.counts.forEach((v, i) => {
        root.appendChild(svg('circle', {
          cx: x(i), cy: y(v), r: v ? 2.4 : 0,
          fill: LAYER_COLOR[layer.key] || '#8A827A',
        }, [svg('title', {}, [document.createTextNode(
          `${years[i]} 年 · ${layer.label}：${num(v)} 条事故涉及该层`
        )])]));
      });
    });

    // X 轴：49 个年份全标会糊成一片，每 5 年一个
    years.forEach((yy, i) => {
      if (i % 5 !== 0 && i !== n - 1) return;
      root.appendChild(svg('text', {
        x: x(i), y: H - 12, class: 'trend-axis', 'text-anchor': 'middle',
      }, [document.createTextNode(yy)]));
    });

    host.appendChild(root);

    const legend = el('div', { cls: 'trend-legend' });
    t.by_layer.forEach((l) => {
      legend.appendChild(el('span', { cls: 'ev-item' }, [
        el('i', { cls: 'trend-dot', attrs: { style: `background:${LAYER_COLOR[l.key]}` } }),
        el('span', { text: l.label }),
      ]));
    });
    legend.appendChild(el('span', { cls: 'ev-item' }, [
      el('i', { cls: 'trend-dot is-base' }),
      el('span', { text: '当年全部事故数（分母）' }),
    ]));
    host.appendChild(legend);

    const span = `${years[0]}–${years[n - 1]}`;
    $('timelineNote').textContent =
      `${span}，共 ${num(n)} 年`
      + (t.without_date
          ? `；另有 ${num(t.without_date)} 条事故没有可用日期，未落入任何年份`
          : '')
      + '。1991–2004 为 0 是记录断档，不是那十四年没有事故。';
  }

  // ══════════════════════════════════════════════════════════
  // 加载
  // ══════════════════════════════════════════════════════════

  function setBusy(on) {
    $('causes').classList.toggle('is-loading', !!on);
  }

  function showEmpty(on) {
    $('emptyVeil').hidden = !on;
  }

  function loadAll() {
    const qs = queryString();
    const tail = qs ? '?' + qs : '';
    const seq = ++reqSeq;
    setBusy(true);

    return Promise.all([
      fetchJson('/api/causes/distribution' + tail),
      fetchJson('/api/causes/cooccurrence' + tail
                + (qs ? '&' : '?') + 'order=' + state.order + '&top=15'),
      fetchJson('/api/causes/timeline' + tail),
    ]).then(([dRes, cRes, tRes]) => {
      if (seq !== reqSeq) return;   // 已有更新的请求在飞，丢弃这次

      const scope = dRes.scope;
      const dist = dRes.data;
      renderScope(scope);
      renderLayer(dist.by_layer, dist.total_causes);
      renderCategory(dist.by_category, dist.total_causes, scope.covered);
      renderCooc(cRes.data);
      renderEvidence(dist.evidence_quality, dist.evidence_unknown, dist.total_causes);
      renderTimeline(tRes.data);

      const empty = scope.causes === 0;
      showEmpty(empty);
      $('footMeta').textContent =
        `${num(scope.causes)} 条致因标注 · 覆盖 ${num(scope.covered)} 条事故`;
      setBusy(false);
    }).catch((e) => {
      if (seq !== reqSeq) return;
      setBusy(false);
      $('scopeEcho').textContent = '加载失败：' + e.message;
    });
  }

  // ══════════════════════════════════════════════════════════
  // 启动
  // ══════════════════════════════════════════════════════════

  function bind() {
    $('causesFilters').addEventListener('submit', (e) => {
      e.preventDefault();
      state.year_from = $('f_year_from').value.trim();
      state.year_to = $('f_year_to').value.trim();
      writeHash();
      loadAll();
    });

    const reset = () => {
      MULTI_KEYS.forEach((k) => { state[k] = []; });
      state.year_from = ''; state.year_to = '';
      writeHash();
      renderFilters(optsCache);
      loadAll();
    };
    $('resetBtn').addEventListener('click', reset);
    $('emptyReset').addEventListener('click', reset);

    $('coocTabs').addEventListener('click', (e) => {
      const btn = e.target.closest('button[data-order]');
      if (!btn) return;
      state.order = btn.dataset.order;
      $('coocTabs').querySelectorAll('button')
        .forEach((b) => b.classList.toggle('is-on', b === btn));
      writeHash();
      loadAll();
    });

    // 浏览器前进/后退要能还原视图——筛选条件在 hash 里，本页可分享
    window.addEventListener('hashchange', () => {
      readHash();
      renderFilters(optsCache);
      $('f_year_from').value = state.year_from;
      $('f_year_to').value = state.year_to;
      $('coocTabs').querySelectorAll('button').forEach((b) => {
        b.classList.toggle('is-on', b.dataset.order === state.order);
      });
      loadAll();
    });
  }

  let optsCache = {};

  function init() {
    readHash();
    bind();
    $('coocTabs').querySelectorAll('button').forEach((b) => {
      b.classList.toggle('is-on', b.dataset.order === state.order);
    });

    // 体系定义只取一次：19 类是全集，不随筛选变化
    fetchJson('/api/causes/framework')
      .then((r) => {
        framework = r.data;
        return fetchJson('/api/filters');
      })
      .then((opts) => {
        optsCache = opts;
        renderFilters(opts);
        return loadAll();
      })
      .catch((e) => {
        $('scopeEcho').textContent = '加载失败：' + e.message;
        $('emptyVeil').hidden = false;
      });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
