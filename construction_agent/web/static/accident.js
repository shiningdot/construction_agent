/* 单事故详情页。蓝图 05 §4.1。
 *
 * 服务端一次返回全部内容（字段 / 致因 / 图片 / 元信息），本文件只做渲染。
 * 所有文本走 textContent（见 dom.js 的说明）。
 */
(function () {
  'use strict';

  const root = document.getElementById('detail');
  const ACCIDENT_ID = root.dataset.accidentId;

  const SEV_CLASS = {
    '特别重大事故': 'sev-1', '重大事故': 'sev-2',
    '较大事故': 'sev-3', '一般事故': 'sev-4',
  };

  // 内容字段的中文标签。枚举列另有 `<col>_label`（服务端查 enum_definitions），
  // 渲染时优先用那个，这里只兜底显示列名对应的中文。
  const FIELD_LABELS = {
    title: '标题', date: '日期', province: '省份', city: '城市',
    accident_location_detail: '详细地点', accident_type: '事故类型',
    severity: '事故等级', deaths: '死亡人数', injuries: '受伤人数',
    summary: '摘要', direct_cause: '直接原因',
    project_name: '工程名称', work_task: '施工工序', accident_time: '事发时刻',
    worker_type: '作业人员类型', publish_date: '发布日期', district: '区县',
    search_enriched_text: '检索增强文本',
    material_involved: '涉及材料', contractor: '施工单位',
    equipment_involved: '涉及设备', direct_economic_loss: '直接经济损失',
    contractor_type: '施工单位资质',
    developer: '建设单位', fall_height: '坠落高度', building_structure: '建筑结构',
    supervision_company: '监理单位', weather: '天气',
  };

  // 不列 `source_count`：主库 2,623 条**全部等于 1**，是恒量不是信息，
  // 摆出来只会让人以为「每条事故只有一个来源」——那是这张表的填充方式，
  // 不是数据的性质（来源数要看 source_urls 表）。蓝图 05 待决项 H 的结论。
  const META_LABELS = {
    id: '记录 ID', source_name: '来源', source_url: '来源链接',
    crawl_date: '采集时间', report_type: '报告类型', text_type: '文本类型',
    classification_verdict: '分类判定路径',
    classification_confidence: '判定置信度',
    search_performed: '是否检索增强',
    pipeline_version: '管道版本',
    step1_duration_ms: 'S1 耗时', step2_duration_ms: 'S2 耗时',
    created_at: '入库时间',
  };

  const QUALITY_CLS = {
    direct_quote: 'ev-direct', indirect_infer: 'ev-indirect', weak_infer: 'ev-weak',
  };

  // 数值型字段右对齐显示；其余按文本
  const NUMERIC = {
    deaths: 1, injuries: 1, trapped_count: 1, fall_height: 1,
    direct_economic_loss: 1,
  };

  /* ── 小组件 ────────────────────────────────────────────── */

  function severityBadge(sev) {
    if (!sev) return el('span', { cls: 'badge sev-x', text: '未定级' });
    return el('span', { cls: 'badge ' + (SEV_CLASS[sev] || 'sev-x'), text: sev });
  }

  function qualityBadge(c) {
    return el('span', {
      cls: 'badge ' + (QUALITY_CLS[c.evidence_quality] || 'ev-weak'),
      text: c.quality_label || c.evidence_quality,
    });
  }

  function valueText(col, v) {
    if (v == null || String(v).trim() === '') return null;
    if (col === 'deaths' || col === 'injuries') return String(v);
    return String(v);
  }

  /* ── ① 头部 ────────────────────────────────────────────── */

  function renderHead(d) {
    const f = d.fields;
    const where = [f.province_label, f.city, f.district]
      .filter(function (x) { return x && String(x).trim() !== ''; });

    const line = el('div', { cls: 'detail-where' });
    line.appendChild(el('span', { text: where.join(' · ') || '地点未提及' }));
    if (f.accident_location_detail) {
      line.appendChild(el('span', {
        cls: 'raw', text: f.accident_location_detail,
        attrs: { title: f.accident_location_detail },
      }));
    }

    const casualties = el('div', { cls: 'detail-casualties' }, [
      el('span', { cls: 'cas' }, [
        el('b', { text: num(f.deaths) }), el('i', { text: '死亡' }),
      ]),
      el('span', { cls: 'cas' }, [
        el('b', { text: num(f.injuries) }), el('i', { text: '受伤' }),
      ]),
      el('span', { cls: 'cas' }, [
        el('b', { text: num(f.trapped_count) }), el('i', { text: '被困' }),
      ]),
    ]);

    return el('header', { cls: 'detail-head' }, [
      el('h1', { cls: 'detail-title', text: f.title || '（无标题）' }),
      el('div', { cls: 'detail-badges' }, [
        f.accident_type
          ? el('span', { cls: 'badge type-badge', text: f.accident_type })
          : el('span', { cls: 'badge sev-x', text: '类型未提及' }),
        severityBadge(f.severity),
        f.date ? el('span', { cls: 'detail-date', text: f.date })
               : blank('日期未提及'),
      ]),
      line,
      casualties,
    ]);
  }

  /* ── ② 摘要与直接原因 ──────────────────────────────────── */

  function textBlock(title, value, cls) {
    const box = el('div', { cls: 'text-block ' + (cls || '') });
    box.appendChild(el('h3', { text: title }));
    box.appendChild(value && String(value).trim() !== ''
      ? el('p', { text: value })
      : el('p', {}, [blank('原报告未提及')]));
    return box;
  }

  /* ── ③ 致因树 ──────────────────────────────────────────── */

  function citationNode(c) {
    const body = el('div', { cls: 'citation-body' });
    body.appendChild(el('div', { cls: 'citation-row' }, [
      el('span', { cls: 'citation-key', text: '证据' }),
      c.evidence
        ? el('span', { cls: 'citation-val', text: c.evidence })
        : el('span', { cls: 'citation-val missing', text: '未提供证据文本' }),
    ]));
    body.appendChild(el('div', { cls: 'citation-row' }, [
      el('span', { cls: 'citation-key', text: '推断' }),
      c.inference
        ? el('span', { cls: 'citation-val', text: c.inference })
        : el('span', { cls: 'citation-val missing', text: '未提供推断文本' }),
    ]));
    return body;
  }

  function causeCard(item) {
    // 同一编码的多条标注（实测 278 组）在这里展开成多条证据，
    // 不是被合并掉——它们是同一编码的不同证据来源。
    const inner = el('div', { cls: 'cause-inner' });
    item.citations.forEach(function (c) {
      inner.appendChild(citationNode(c));
    });

    const summaryLine = [
      el('span', { cls: 'cause-code', text: item.code }),
      el('span', { cls: 'cause-name', text: item.name || '' }),
      qualityBadge(item),
    ];
    if (item.citation_count > 1) {
      summaryLine.push(el('span', {
        cls: 'cause-count', text: '×' + item.citation_count,
        attrs: { title: '该编码在本事故中有 ' + item.citation_count + ' 条标注' },
      }));
    }

    return el('details', { cls: 'cause' }, [
      el('summary', {}, [el('div', { cls: 'cause-line' }, summaryLine)]),
      inner,
    ]);
  }

  function renderCauses(d) {
    const panel = el('section', { cls: 'panel' });
    panel.appendChild(el('div', { cls: 'panel-head' }, [
      el('h2', { text: '致因编码' }),
      el('div', { cls: 'result-count', text:
        d.causes.distinct + ' 类 · ' + d.causes.citations + ' 条标注' }),
    ]));

    if (!d.causes.items.length) {
      panel.appendChild(el('div', { cls: 'empty' }, [
        el('strong', { text: '本事故没有致因编码记录' }),
        el('span', { text: '主库共 5 条事故属于这种情况。' }),
      ]));
      return panel;
    }

    // 按层分组而非平铺：19 类连成一片读者无法对应 CAICS 的体系结构。
    // 层的先后由服务端排好（M→F→T→H→C），这里只按出现顺序分组。
    const order = [];
    const byLayer = {};
    d.causes.items.forEach(function (it) {
      if (!byLayer[it.layer]) { byLayer[it.layer] = []; order.push(it.layer); }
      byLayer[it.layer].push(it);
    });

    order.forEach(function (layer) {
      const items = byLayer[layer];
      const grid = el('div', { cls: 'cause-grid' });
      items.forEach(function (it) { grid.appendChild(causeCard(it)); });

      panel.appendChild(el('div', { cls: 'layer-group' }, [
        el('div', { cls: 'layer-head layer-' + layer }, [
          el('span', { cls: 'layer-key', text: layer }),
          el('span', { cls: 'layer-name', text: items[0].layer_label }),
          el('span', { cls: 'layer-n', text: items.length + ' 类' }),
        ]),
        grid,
      ]));
    });

    panel.appendChild(el('p', { cls: 'panel-note', text:
      '同一编码可有多条标注，各自带独立证据。点开任一编码查看证据原文与推断。' }));

    return panel;
  }

  /* ── ④ 字段明细 ────────────────────────────────────────── */

  function fieldRow(col, f) {
    const labelled = f[col + '_label'];
    const raw = f[col];
    const shown = labelled != null ? labelled : raw;

    let cell;
    if (col === 'province') {
      cell = f.province_label ? el('span', { text: f.province_label })
                              : blank();
    } else if (shown == null || String(shown).trim() === '') {
      cell = blank();
    } else if (NUMERIC[col]) {
      cell = el('span', { cls: 'mono', text: String(shown) });
    } else if (col === 'accident_location_detail') {
      cell = el('span', { cls: 'mono wrap', text: String(shown) });
    } else {
      cell = el('span', { text: String(shown) });
    }

    // 枚举列：中文名在左，编码以小字附在右，便于与库对账
    if (labelled != null && String(raw) !== String(labelled)) {
      cell = el('span', {}, [
        cell,
        el('span', { cls: 'enum-code', text: String(raw) }),
      ]);
    }

    return el('div', { cls: 'field-row' }, [
      el('span', { cls: 'field-key', text: FIELD_LABELS[col] || col }),
      el('span', { cls: 'field-val' }, [cell]),
    ]);
  }

  function renderFields(d) {
    const panel = el('section', { cls: 'panel' });
    panel.appendChild(el('div', { cls: 'panel-head' }, [
      el('h2', { text: '字段明细' }),
      el('div', { cls: 'result-count', text: '按有值率分档，折叠的不隐藏' }),
    ]));

    d.tiers.forEach(function (tier) {
      const body = el('div', { cls: 'field-list' });
      tier.fields.forEach(function (col) {
        body.appendChild(fieldRow(col, d.fields));
      });

      // A 档默认展开（必显），其余默认折叠，但**始终渲染**——
      // `weather` 只有 7.9%，其存在与否本身反映采集源的信息丰富度。
      panel.appendChild(el('details', { cls: 'tier tier-' + tier.key,
                                        attrs: tier.key === 'A' ? { open: '' } : {} }, [
        el('summary', {}, [
          el('span', { cls: 'tier-key', text: tier.key }),
          el('span', { cls: 'tier-label', text: tier.label }),
          el('span', { cls: 'tier-n', text: tier.available + ' / ' + tier.total }),
        ]),
        body,
      ]));
    });

    return panel;
  }

  /* ── ⑤ 图片 ────────────────────────────────────────────── */

  function renderImages(d) {
    if (!d.images.length) return null;
    const panel = el('section', { cls: 'panel' });
    panel.appendChild(el('div', { cls: 'panel-head' }, [
      el('h2', { text: '现场图片' }),
      el('div', { cls: 'result-count', text: d.images.length + ' 条分析记录' }),
    ]));

    const list = el('div', { cls: 'image-list' });
    d.images.forEach(function (im) {
      list.appendChild(el('div', { cls: 'image-row' }, [
        el('span', { cls: 'mono wrap', text: im.local_path || '—' }),
        el('span', { cls: 'image-tag', text: im.phase1_scene_type || '场景未标注' }),
        im.phase1_damage
          ? el('span', { cls: 'image-tag', text: im.phase1_damage }) : null,
      ]));
    });
    panel.appendChild(list);

    // 缩略图渲染待 image_manifest 重建方案（蓝图 06）——现有 local_path
    // 指向三版目录混存的位置，直接拼 URL 会有一半 404。
    panel.appendChild(el('p', { cls: 'panel-note', text:
      '图片文件路径对齐与缩略图见蓝图 06；此处只列已登记的分析记录。' }));

    return panel;
  }

  /* ── ⑥ 处理信息 ────────────────────────────────────────── */

  function renderMeta(d) {
    const body = el('div', { cls: 'field-list' });
    Object.keys(META_LABELS).forEach(function (k) {
      const v = d.meta[k];
      const text = v == null || String(v).trim() === '' ? null : String(v);
      body.appendChild(el('div', { cls: 'field-row' }, [
        el('span', { cls: 'field-key', text: META_LABELS[k] }),
        el('span', { cls: 'field-val' }, [
          text ? el('span', { cls: 'mono wrap', text: text }) : blank(),
        ]),
      ]));
    });

    return el('section', { cls: 'panel' }, [
      el('details', {}, [
        el('summary', {}, [
          el('span', { cls: 'tier-label', text: '处理信息' }),
          el('span', { cls: 'tier-n', text: '来源 / 判定路径 / 模型耗时' }),
        ]),
        body,
      ]),
    ]);
  }

  /* ── ⑦ 原文 ────────────────────────────────────────────── */

  // 文献轨的记录 raw_text 是整页 PDF 文本，主库最长 43,124 字。一次性
  // 铺进 DOM 会让这一段的排版与滚动都变钝，而其中绝大多数时候没人看。
  // 超过阈值先只渲染开头，想看全文再点——全文已经在响应里，不额外请求。
  const RAW_PREVIEW = 5000;

  function renderRaw(d) {
    if (!d.raw_text || !String(d.raw_text).trim()) return null;
    const full = String(d.raw_text);
    const long = full.length > RAW_PREVIEW;

    const pre = el('pre', {
      cls: 'raw-text',
      text: long ? full.slice(0, RAW_PREVIEW) : full,
    });

    const body = el('div', { cls: 'raw-body' }, [pre]);
    if (long) {
      const more = el('button', {
        cls: 'btn raw-more',
        text: `展开全文（还有 ${num(full.length - RAW_PREVIEW)} 字）`,
        attrs: { type: 'button' },
      });
      more.addEventListener('click', () => {
        pre.textContent = full;
        more.remove();
      });
      body.appendChild(more);
    }

    return el('section', { cls: 'panel' }, [
      el('details', {}, [
        el('summary', {}, [
          el('span', { cls: 'tier-label', text: '原文全文' }),
          el('span', {
            cls: 'tier-n',
            text: num(full.length) + ' 字'
                 + (long ? ` · 默认只显示前 ${num(RAW_PREVIEW)} 字` : ''),
          }),
        ]),
        body,
      ]),
    ]);
  }

  /* ── 主流程 ────────────────────────────────────────────── */

  function renderError(err) {
    clear(root);
    const box = el('div', { cls: 'panel' });
    if (err.status === 404) {
      box.appendChild(el('div', { cls: 'empty' }, [
        el('strong', { text: '找不到这条事故' }),
        el('span', { text: '记录 ' + ACCIDENT_ID + ' 不在当前数据库中。' }),
      ]));
    } else if (err.status === 503) {
      box.appendChild(el('div', { cls: 'empty' }, [
        el('strong', { text: '数据库不可用' }),
        el('span', { text: err.message || '请检查 CAIEMS_DB 指向的库文件。' }),
      ]));
    } else {
      box.appendChild(el('div', { cls: 'empty' }, [
        el('strong', { text: '加载失败' }),
        el('span', { text: err.message || String(err) }),
      ]));
    }
    // 返回控件与正常路径共用同一套类名（`.detail-back` > `.back-link`）。
    // 出错页和正常页的出口不该是两套结构——样式、验收脚本、将来的改动
    // 都只认这一套，分两套就一定会有一套先坏掉。
    box.appendChild(el('div', { cls: 'detail-back' }, [
      el('a', { cls: 'back-link', text: '← 返回列表', attrs: { href: '/' } }),
    ]));
    root.appendChild(box);
  }

  async function load() {
    let body;
    try {
      body = await fetchJson('/api/accidents/' + encodeURIComponent(ACCIDENT_ID));
    } catch (err) {
      renderError(err);
      return;
    }

    const d = body.data;
    document.title = (d.fields.title || '事故详情') + ' · CAIEMS';

    clear(root);
    root.appendChild(renderHead(d));

    const back = el('a', { cls: 'back-link', text: '← 返回列表',
                           attrs: { href: '/' } });
    back.addEventListener('click', function (e) {
      // 从列表点进来时用 history.back()，列表的筛选与页码在 URL hash 里，
      // 浏览器会原样恢复；直接进本页（无历史）时退回首页。
      if (window.history.length > 1) {
        e.preventDefault();
        window.history.back();
      }
    });
    root.appendChild(el('div', { cls: 'detail-back' }, [back]));

    root.appendChild(el('section', { cls: 'panel text-panel' }, [
      textBlock('摘要', d.fields.summary),
      textBlock('直接原因', d.fields.direct_cause),
    ]));

    root.appendChild(renderCauses(d));
    root.appendChild(renderFields(d));

    const images = renderImages(d);
    if (images) root.appendChild(images);

    root.appendChild(renderMeta(d));

    const raw = renderRaw(d);
    if (raw) root.appendChild(raw);

    // 底栏的数据来源提示：这条数据是网络轨还是文献轨，可追溯
    const chip = document.getElementById('dbChipText');
    if (chip) chip.textContent = '已加载 ' + ACCIDENT_ID;
  }

  load();
})();
