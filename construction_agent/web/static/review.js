/* 暂存审核页（流水线阶段③）。

   这一页的每个写操作都必须让人先看见后果，所以三处刻意做得比"顺手"更麻烦：

   - 单条改动前，原文与 33 个字段在同一个抽屉里，不用来回切页核对
   - 批量通过只对零告警开放，有告警的会被列出来并说明原因，不静默跳过
   - 并入最终库必须两段式：先预演拿令牌，再确认。令牌与选择集绑定，
     中途改了选择会作废重来

   选择状态放在内存集合里、翻页不丢：审核是跨页进行的动作，翻一页清一次
   选择等于逼着人一页一页地过。 */

(function () {
  'use strict';

  var PAGE = 1;
  var picked = new Set();     // 勾选的 stage_id，跨页保持
  var rowsById = {};          // 当前页 stage_id → 行数据
  var pendingPlan = null;     // 预演结果 {stage_ids, confirm_token, ...}
  var taxonomy = [];          // CAICS 19 类，抽屉里"新增编码"用
  var origCauses = [];        // 抽屉打开时服务端给的编码，用来判断"改没改"
  var enumLabels = {};        // 编码列 → 中文名，抽屉打开时由接口一次下发

  // 层序就是因果泄漏顺序 M→F→T→H→C，与 step2_caics_encoder 的 sort_order
  // 对齐。默认字典序会排成 C/F/H/M/T，跟理论叙述正好倒过来。
  var LAYER_ORDER = ['M', 'F', 'T', 'H', 'C'];
  var LAYER_NAME = {
    M: '管理根基', F: '防线失效', T: '触发条件', H: '人的行为', C: '后果放大',
  };
  var EV_LABEL = {
    direct_quote: '直引', indirect_infer: '推断', weak_infer: '弱推断',
  };
  var ST_LABEL = {
    pending: '待审核', approved: '已通过', edited: '已修改', rejected: '已退回',
  };
  // 体裁取值域是封闭的六个，给审核人看 `single` 不如看「单个事故」。
  // 相关性那一栏不翻：它存的其实是分类**来源码**（batch_direct / pdf_direct…），
  // 不是"是不是施工事故"，硬套中文会把语义讲错，不如原样呈上。
  var TT_LABEL = {
    single: '单个事故', multi: '多事故', regulatory: '通报文件',
    aggregate: '统计汇总', training: '培训材料', other: '其它',
  };

  // 抽屉里放在「关键字段」之外的字段折叠起来——31 个输入框平铺出来，
  // 真正要改的那几个会被淹掉
  var PRIMARY = ['title', 'date', 'accident_time', 'location', 'city',
    'district', 'accident_type', 'severity', 'deaths', 'injuries',
    'trapped_count', 'project_type', 'construction_phase', 'summary'];
  var FIELD_LABEL = {
    title: '标题', date: '日期', accident_time: '时刻', location: '省（原始）',
    city: '市', district: '区县', accident_location_detail: '详细地点',
    project_name: '项目名', project_type: '项目类型',
    construction_phase: '施工阶段', building_structure: '结构形式',
    accident_type: '事故类型', severity: '严重程度', deaths: '死亡',
    injuries: '受伤', trapped_count: '被困', collapse_subtype: '坍塌子类',
    fall_height: '坠落高度', work_task: '作业任务',
    equipment_involved: '涉事设备', material_involved: '涉事材料',
    worker_type: '工人类别', weather: '天气', contractor: '施工单位',
    contractor_type: '施工单位类型', developer: '建设单位',
    supervision_company: '监理单位', direct_economic_loss: '直接经济损失',
    direct_cause: '直接原因', summary: '摘要', report_type: '报告类型',
  };
  var NUM_FIELDS = { deaths: 1, injuries: 1, trapped_count: 1, fall_height: 1,
                     direct_economic_loss: 1 };

  // ── 请求 ─────────────────────────────────────────────────────

  function req(url, method, body) {
    var opts = { method: method || 'GET', headers: { Accept: 'application/json' } };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    return fetch(url, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (b) {
        if (!r.ok || b.ok === false) {
          var e = new Error(b.message || ('HTTP ' + r.status));
          e.status = r.status;
          e.body = b;
          throw e;
        }
        return b;
      });
    });
  }

  var get = function (u) { return req(u, 'GET'); };
  var post = function (u, b) { return req(u, 'POST', b); };

  // ── 小工具 ───────────────────────────────────────────────────

  function fmtTime(s) {
    if (!s) return '';
    return String(s).replace('T', ' ').slice(0, 16);
  }

  function badge(text, cls, title) {
    if (text == null || text === '') return blank();
    return el('span', { cls: 'badge ' + (cls || ''), text: text,
                        attrs: title ? { title: title } : null });
  }

  function statusBadge(s) {
    return badge(ST_LABEL[s] || s, 'st-' + (s || 'pending'));
  }

  /* 告警徽标。阻断级（标题空、无编码）用红，其余用黄——层级不同，
     投进同一个颜色里就等于没有分级。

     恒返回**数组**（空态也是单元素数组），调用方一律当 children 摊平使用。 */
  function warnBadges(list, labels) {
    if (!list || !list.length) {
      return [badge('零告警', 'is-ok', '没有触发任何告警规则')];
    }
    var BLOCK = { empty_title: 1, no_causes: 1 };
    return list.map(function (c, i) {
      return badge(labels[i] || c, BLOCK[c] ? 'sev-1' : 'sev-3', c);
    });
  }

  function evBadge(q) {
    var cls = q === 'direct_quote' ? 'ev-direct'
            : q === 'indirect_infer' ? 'ev-indirect' : 'ev-weak';
    return badge(EV_LABEL[q] || q || '—', cls);
  }

  function notice(msg, kind) {
    var box = $('dbNotice');
    box.className = 'notice' + (kind ? ' notice-' + kind : '');
    box.textContent = msg;
    box.hidden = false;
  }

  function hideNotice() { $('dbNotice').hidden = true; }

  // ── ① 审核漏斗 ───────────────────────────────────────────────

  function loadStats() {
    return get('/api/review/stats').then(function (b) {
      var d = b.data || {};
      if (!d.available) {
        notice(d.hint || '工作库未建，审核功能不可用。', 'warn');
        return;
      }
      hideNotice();
      clear($('funnelRow'));
      (d.funnel || []).forEach(function (f, i) {
        $('funnelRow').appendChild(el('div',
          { cls: 'stat' + (i === 3 ? ' is-accent' : '') }, [
          el('div', { cls: 'stat-label', text: f.stage }),
          el('div', { cls: 'stat-value', text: num(f.count) }),
        ]));
      });

      var st = d.by_status || {};
      $('funnelMeta').textContent = Object.keys(ST_LABEL)
        .map(function (k) { return ST_LABEL[k] + ' ' + (st[k] || 0); })
        .join(' · ');

      clear($('warnChips'));
      var chips = [{ code: '', label: '全部', count: null }]
        .concat(d.by_warning || []);
      chips.forEach(function (w) {
        var on = ($('f_warn').value === (w.code === '' ? '' : w.code));
        var btn = el('button', {
          cls: 'chip' + (on ? ' is-on' : ''), attrs: { type: 'button' },
        }, [
          el('span', { text: w.label }),
          w.count == null ? null : el('span', { cls: 'n', text: num(w.count) }),
        ]);
        btn.addEventListener('click', function () {
          // 「全部」清空告警条件；具体告警码则按该码筛选
          $('f_warn').value = (w.code === '') ? '' : w.code;
          PAGE = 1;
          loadQueue();
          loadStats();
        });
        $('warnChips').appendChild(btn);
      });
    }).catch(function (e) {
      notice('审核漏斗加载失败：' + e.message, 'warn');
    });
  }

  function loadHistory() {
    return get('/api/review/history').then(function (b) {
      var rows = b.data || [];
      clear($('histBody'));
      if (!rows.length) {
        $('histBody').appendChild(emptyRow(5, '还没有并入记录'));
      }
      rows.forEach(function (r) {
        var ids = [];
        try { ids = JSON.parse(r.stage_ids || '[]'); } catch (e) { ids = []; }
        var tr = el('tr');
        tr.appendChild(el('td', { cls: 'col-date mono', text: fmtTime(r.created_at) }));
        tr.appendChild(el('td', { cls: 'col-num', text: num(r.promoted) }));
        tr.appendChild(el('td', { cls: 'col-num', text: num(r.causes_written) }));
        tr.appendChild(el('td', { cls: 'mono small',
          text: String(r.backup_path || '').split('/').pop() || '—',
          attrs: { title: r.backup_path || '' } }));
        tr.appendChild(el('td', {}, [r.rolled_back_at
          ? badge('已回滚 ' + fmtTime(r.rolled_back_at), 'st-rejected')
          : badge('生效中', 'st-approved', ids.length + ' 条暂存记录')]));
        $('histBody').appendChild(tr);
      });
    }).catch(function () { /* 历史读不到不影响主流程 */ });
  }

  /* 空态行：列数要跟着表头走，写死了以后加列就会错位 */
  function emptyRow(cols, text) {
    var tr = el('tr');
    tr.appendChild(el('td', {
      cls: 'empty', attrs: { colspan: String(cols) },
    }, [el('strong', { text: text || '没有数据' })]));
    return tr;
  }

  // ── ② 队列 ───────────────────────────────────────────────────

  function query() {
    var p = [];
    var st = $('f_status').value;
    var w = $('f_warn').value;
    var tt = $('f_text').value;
    var q = $('f_q').value.trim();
    if (st) p.push('review_status=' + encodeURIComponent(st));
    if (w) p.push('warnings=' + encodeURIComponent(w));
    if (tt) p.push('text_type=' + encodeURIComponent(tt));
    if (q) p.push('q=' + encodeURIComponent(q));
    p.push('size=' + $('f_size').value);
    p.push('page=' + PAGE);
    return '/api/review/queue?' + p.join('&');
  }

  function loadQueue() {
    return get(query()).then(function (b) {
      if (!b.available) {
        notice(b.hint || '工作库未建，审核功能不可用。', 'warn');
        return;
      }
      renderQueue(b);
    }).catch(function (e) {
      notice('队列加载失败：' + e.message, 'warn');
    });
  }

  function renderQueue(b) {
    var rows = b.data || [];
    var meta = b.meta || {};
    rowsById = {};
    rows.forEach(function (r) { rowsById[r.stage_id] = r; });

    $('qCount').textContent = '本页 ' + num(rows.length) + ' 条';
    if (meta.scan_truncated) {
      // 超上限是数据规模问题，不该悄悄发生——说清楚少了什么
      $('qCount').textContent += '（总数只统计了前 5000 条）';
    }

    var body = clear($('qBody'));
    if (!rows.length) {
      body.appendChild(emptyRow(10, '没有符合条件的暂存记录'));
    }
    rows.forEach(function (r) {
      var tr = el('tr', { cls: 'is-pickable' });
      if (picked.has(r.stage_id)) tr.className += ' is-picked';

      var cb = el('input', { attrs: { type: 'checkbox' } });
      cb.checked = picked.has(r.stage_id);
      cb.addEventListener('change', function () { applyPick(tr, cb, r.stage_id, cb.checked); });

      tr.appendChild(el('td', { cls: 'col-pick' }, [cb]));

      var btn = el('button', { cls: 'title-main', text: r.title || '（无标题）',
                               attrs: { type: 'button' } });
      btn.addEventListener('click', function (ev) {
        ev.stopPropagation();
        openDrawer(r.stage_id);
      });
      tr.appendChild(el('td', { cls: 'title-cell' }, [
        el('div', { cls: 'title-line' }, [btn]),
        r.relevance_verdict && r.relevance_verdict !== 'construction'
          ? el('div', { cls: 'title-meta', text: '相关性：' + r.relevance_verdict })
          : null,
      ]));

      tr.appendChild(el('td', { cls: 'col-date',
                                text: fmtTime(r.date) || '—' }));
      tr.appendChild(el('td', {}, [badge(r.accident_type || '—')]));
      tr.appendChild(el('td', { cls: 'col-num', text: r.deaths == null ? '—' : num(r.deaths) }));
      tr.appendChild(el('td', { cls: 'col-num', text: r.injuries == null ? '—' : num(r.injuries) }));
      tr.appendChild(el('td', { cls: 'col-num mono',
        text: r.cause_count ? String(r.cause_count) : '0',
        attrs: { title: 'CAICS 编码条数；0 条是阻断级告警' } }));
      // warnBadges 返回的是**节点数组**，要摊平当 children 传进去；
      // 套一层 [ ] 会让 appendChild 拿到数组而不是元素
      tr.appendChild(el('td', { cls: 'warn-cell' },
        warnBadges(r.warnings, r.warning_labels)));
      tr.appendChild(el('td', {}, [
        statusBadge(r.review_status),
        r.promoted ? badge('已并入', 'is-ok') : null,
      ]));

      var act = el('td', { cls: 'col-act' }, [

        el('button', { cls: 'btn btn-sm', text: '审核',
                       attrs: { type: 'button' } }),
      ]);
      act.firstChild.addEventListener('click', function (ev) {
        ev.stopPropagation();
        openDrawer(r.stage_id);
      });
      tr.appendChild(act);

      // 除标题与勾选框外整行可点 = 勾选。这表有 10 列，
      // 非逼着人去够最左边那个小方框没道理。
      tr.addEventListener('click', function (ev) {
        if (ev.target === cb) return;
        if (ev.target.closest('button')) return;
        applyPick(tr, cb, r.stage_id, !cb.checked);
      });

      body.appendChild(tr);
    });

    renderPager(meta);
    syncBulk();
    syncCleanBtn();
  }

  function applyPick(tr, cb, stageId, on) {
    cb.checked = on;
    if (on) picked.add(stageId); else picked.delete(stageId);
    tr.className = 'is-pickable' + (on ? ' is-picked' : '');
    syncBulk();
  }

  function renderPager(meta) {
    var box = clear($('qPager'));
    if (!meta.pages || meta.pages <= 1) {
      if (meta.total) {
        box.appendChild(el('span', { cls: 'info',
          text: '共 ' + num(meta.total) + ' 条' }));
      }
      return;
    }
    var add = function (label, page, opts) {
      var o = opts || {};
      var b = el('button', { text: label, attrs: { type: 'button' } });
      if (o.current) b.className = 'is-current';
      if (o.disabled) b.disabled = true;
      b.addEventListener('click', function () {
        PAGE = page;
        loadQueue();
        window.scrollTo({ top: $('queuePanel').offsetTop - 16, behavior: 'smooth' });
      });
      box.appendChild(b);
    };
    add('‹', Math.max(1, meta.page - 1), { disabled: meta.page <= 1 });
    var pages = [];
    for (var i = 1; i <= meta.pages; i++) {
      if (i === 1 || i === meta.pages || Math.abs(i - meta.page) <= 2) pages.push(i);
      else if (pages[pages.length - 1] !== '…') pages.push('…');
    }
    pages.forEach(function (p) {
      if (p === '…') box.appendChild(el('span', { cls: 'gap', text: '…' }));
      else add(String(p), p, { current: p === meta.page });
    });
    add('›', Math.min(meta.pages, meta.page + 1),
        { disabled: meta.page >= meta.pages });
    box.appendChild(el('span', { cls: 'info',
      text: '第 ' + meta.page + '/' + meta.pages + ' 页 · 共 '
            + num(meta.total) + ' 条' }));
  }

  // ── ③ 勾选 ───────────────────────────────────────────────────

  function syncBulk() {
    var bar = $('bulkBar');
    bar.hidden = picked.size === 0;
    $('bulkCount').textContent = '已选 ' + picked.size + ' 条';
    var ready = 0;
    picked.forEach(function (id) {
      var r = rowsById[id];
      // 跨页选中的看不到告警情况，只能按"当前页已知"给个下界提示，
      // 真正的准入门槛在服务端（batch-approve 会逐条判）
      if (r && !r.warnings.length
          && (r.review_status === 'pending' || r.review_status === 'rejected')) ready++;
    });
    $('bulkHint').textContent = ready
      ? '（本页其中 ' + ready + ' 条零告警，可通过）' : '';
  }

  function syncCleanBtn() {
    var has = $('f_warn').value === 'none';
    $('qClean').textContent = has ? '回到全部' : '跳到零告警';
  }

  // ── ④ 抽屉：单条审核 ─────────────────────────────────────────

  var drawerStageId = null;   // 抽屉此刻显示的是哪一条；关掉即置空

  function openDrawer(stageId) {
    drawerStageId = stageId;
    $('drawer').hidden = false;
    $('drawerTitle').textContent = '读取中…';
    clear($('drawerBody'));
    $('drawerBody').appendChild(el('div', { cls: 'empty-note', text: '正在读取…' }));
    get('/api/review/items/' + encodeURIComponent(stageId)).then(function (b) {
      renderDrawer(b.data);
    }).catch(function (e) {
      $('drawerTitle').textContent = '读取失败';
      clear($('drawerBody'));
      $('drawerBody').appendChild(el('div', { cls: 'notice notice-warn',
                                              text: e.message }));
    });
  }

  function closeDrawer() {
    drawerStageId = null;
    $('drawer').hidden = true;
    clear($('drawerBody'));
  }

  function renderDrawer(d) {
    var body = clear($('drawerBody'));
    $('drawerTitle').textContent = (d.fields.title || '（无标题）');
    enumLabels = d.enum_labels || {};

    // 告警与状态
    body.appendChild(el('div', { cls: 'warn-box' }, [
      el('div', { cls: 'warn-line' }, warnBadges(d.warnings, d.warning_labels)),
      el('div', { cls: 'warn-meta', text:
        '审核状态：' + (ST_LABEL[d.review.status] || d.review.status)
        + (d.promoted ? ' · 已并入最终库' : '')
        + (d.review.reviewed_at ? ' · ' + fmtTime(d.review.reviewed_at) : '') }),
      d.review.note ? el('div', { cls: 'warn-meta', text: '备注：' + d.review.note }) : null,
    ]));

    // 闸门
    var g = d.gates || {};
    body.appendChild(el('div', { cls: 'kv-grid' }, [
      kv('体裁（S1）', (TT_LABEL[g.text_type] || g.text_type || '—')
         + (g.text_type_confidence ? ' · ' + g.text_type_confidence : '')),
      kv('相关性（S2）', (g.relevance_verdict || '—') + (g.relevance_confidence ? ' · ' + g.relevance_confidence : '')),
      kv('原文来源', d.raw.source_key || '—'),
      kv('发布日', fmtTime(d.raw.publish_date) || '—'),
    ]));

    // 字段编辑区
    var editBox = el('details', { cls: 'edit-box', attrs: { open: 'open' } });
    editBox.appendChild(el('summary', { text: '字段（可改）' }));
    var grid = el('div', { cls: 'kv-grid edit-grid' });
    d.editable.forEach(function (f) {
      grid.appendChild(fieldInput(f, d.fields[f]));
    });
    editBox.appendChild(grid);
    body.appendChild(editBox);

    // 致因树
    body.appendChild(causeEditor(d.causes));

    // 原文
    var raw = el('details', { cls: 'raw-box' });
    raw.appendChild(el('summary', { text: '原文（' +
      String((d.raw.content || '').length) + ' 字）' }));
    if (d.raw.source_url) {
      var a = el('a', { cls: 'mono small', text: d.raw.source_url,
                        attrs: { href: d.raw.source_url, target: '_blank',
                                 rel: 'noopener noreferrer' } });
      raw.appendChild(el('p', {}, [a]));
    }
    raw.appendChild(el('pre', { cls: 'raw-text',
                                text: d.raw.content || '（无正文）' }));
    body.appendChild(raw);

    // 操作区
    var noteIn = el('input', { attrs: { type: 'text', id: 'noteIn',
                                        placeholder: '备注（退回时必填）' } });
    var acts = el('div', { cls: 'drawer-acts' }, [
      el('button', { cls: 'btn btn-primary', text: '通过',
                     attrs: { type: 'button', id: 'actApprove' } }),
      el('button', { cls: 'btn', text: '保存修改',
                     attrs: { type: 'button', id: 'actSave' } }),
      el('button', { cls: 'btn', text: '退回',
                     attrs: { type: 'button', id: 'actReject' } }),
    ]);
    body.appendChild(acts);
    body.appendChild(noteIn);
    // id 与 hidden 都走 attrs：`el()` 的 opts 只认 id/cls/text/attrs，
    // 顶层写 hidden 会被静默丢掉，那样这个回执框一出生就是可见的空白条
    var msg = el('p', { cls: 'task-error', attrs: { id: 'drawerMsg', hidden: '' } });
    body.appendChild(msg);

    $('actApprove').addEventListener('click', function () {
      act(d, { action: 'approve', note: noteIn.value });
    });
    $('actReject').addEventListener('click', function () {
      if (!noteIn.value.trim()) {
        // 前端先拦一道：退回没原因等于把判断成本转嫁给下一个人
        return showDrawerMsg('退回必须填写原因', true);
      }
      act(d, { action: 'reject', note: noteIn.value });
    });
    $('actSave').addEventListener('click', function () {
      var edits = collectEdits(d);
      var causes = collectCauses();
      if (!Object.keys(edits).length && causes === null) {
        return showDrawerMsg('没有改动', true);
      }
      var payload = { action: 'edit', note: noteIn.value, edits: edits };
      if (causes !== null) payload.edits.causes = causes;
      act(d, payload);
    });
  }

  function kv(key, val) {
    return el('div', { cls: 'kv' }, [
      el('div', { cls: 'kv-key', text: key }),
      el('div', { cls: 'kv-val', text: val == null || val === '' ? '—' : String(val) }),
    ]);
  }

  /* 编码列的中文名。

     `work_task` / `building_structure` / `equipment_involved` / `material_involved`
     / `worker_type` 五列库里存的是码（`WT04`、`BLD03`…），S3 也按码输出。审核员
     要判的恰恰是「S3 判的 `WT04` 对不对」——只给码，等于让人先背下 11 个 WT 码
     再来核对。详情页早就翻了（`detail.py`），这一页此前没翻。

     对照表由接口一次下发（`enum_labels`），前端不维护编码表。这就是蓝图 03 §4.2
     给告警码定的那条原则：**前端渲染标签用后端给的对照表**，少了它，遇到一个没
     见过的码就只能显示裸码。 */
  /* 这一列有没有对照表。**空对象也算没有**——`{}` 在 JS 里是 truthy，只看
     `enumLabels[field]` 会让「枚举表里一条都没有」的库把每个码都标成
     「枚举表里没有这个码」，全是噪声。 */
  function hasEnumTable(field) {
    var t = enumLabels[field];
    return !!t && Object.keys(t).length > 0;
  }

  function enumNames(field, value) {
    var table = enumLabels[field];
    var raw = value == null ? '' : String(value).trim();
    if (!hasEnumTable(field) || !raw) return null;
    // 多值用顿号分隔（S3 提示词要求「多人多设备用顿号」），逗号与分号一并容忍
    return raw.split(/[、,;；\s]+/).filter(Boolean).map(function (code) {
      return { code: code, name: table[code] || null };
    });
  }

  function fillHint(node, field, value) {
    clear(node);
    var parts = enumNames(field, value);
    if (!parts) return;
    parts.forEach(function (p, i) {
      if (i) node.appendChild(el('span', { cls: 'kv-hint-sep', text: '、' }));
      if (p.name) {
        node.appendChild(el('span', { text: p.name }));
      } else {
        // 查不到名字的码**原样亮出来**，不吞掉也不换兜底词：异常编码该自己冒头
        node.appendChild(el('span', { cls: 'kv-hint-bad',
          text: p.code + '（枚举表里没有这个码）' }));
      }
    });
  }

  function fieldInput(field, value) {
    var input = el('input', {
      attrs: {
        type: NUM_FIELDS[field] ? 'number' : 'text',
        'data-field': field,
        id: 'fld_' + field,
      },
    });
    input.value = value == null ? '' : String(value);
    var wrap = el('div', { cls: 'kv' }, [
      el('div', { cls: 'kv-key', text: FIELD_LABEL[field] || field }),
      input,
    ]);
    // 提示只在编码列上出现（判据是接口下发了这一列**非空**的对照表）
    if (hasEnumTable(field)) {
      var hint = el('div', { cls: 'kv-hint' });
      fillHint(hint, field, value);
      // 改了码，中文名要跟着变——否则提示停在旧值上，比没有提示更误导
      input.addEventListener('input', function () {
        fillHint(hint, field, input.value);
      });
      wrap.appendChild(hint);
    }
    return wrap;
  }

  /* 只回传真正改过的字段。整包回传会把「用户没碰过的字段」也一并写一遍，
     一旦 S3 输出与人工值有差异（比如空白与 NULL），就成了一次无声覆盖。 */
  function collectEdits(d) {
    var out = {};
    d.editable.forEach(function (f) {
      var input = $('fld_' + f);
      if (!input) return;
      var before = d.fields[f] == null ? '' : String(d.fields[f]);
      if (input.value !== before) out[f] = input.value;
    });
    return out;
  }

  // ── 致因树编辑 ───────────────────────────────────────────────

  var causeState = [];   // 抽屉打开时的编码快照，编辑后回写
  var causeListEl = null;  // 当前列表节点，直接持引用（原因见 renderCauses）

  function causeEditor(causes) {
    origCauses = causes || [];
    causeState = (causes || []).map(function (c) {
      return { code: c.code, name: c.name, evidence: c.evidence || '',
               inference: c.inference || '',
               evidence_quality: c.evidence_quality || 'direct_quote' };
    });

    var box = el('div', { cls: 'cause-edit-box' });
    box.appendChild(el('div', { cls: 'edit-head' }, [
      el('span', { text: '致因编码（' + causeState.length + ' 条）' }),
    ]));
    var list = el('div', { cls: 'ce-list', id: 'causeList' });
    causeListEl = list;
    box.appendChild(list);
    box.appendChild(addCauseRow());
    renderCauses();
    return box;
  }

  function renderCauses() {
    // 用持有的引用而不是 `$('causeList')`：causeEditor 是在这个节点**挂进
    // 文档之前**调用 renderCauses 的，此刻 getElementById 查不到游离节点，
    // 首次打开会整片空白——而之后点一次删除又能渲染出来，症状看着像随机。
    var list = causeListEl;
    if (!list) return;
    clear(list);
    if (!causeState.length) {
      list.appendChild(el('div', { cls: 'empty-note', text: '没有编码' }));
      return;
    }
    var byLayer = {};
    causeState.forEach(function (c, i) {
      var L = (c.code || '?')[0];
      (byLayer[L] || (byLayer[L] = [])).push({ c: c, i: i });
    });
    Object.keys(byLayer).sort(function (a, b) {
      // 认不出的层名排到最后，而不是被静默丢掉
      var ia = LAYER_ORDER.indexOf(a), ib = LAYER_ORDER.indexOf(b);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    }).forEach(function (L) {
      var g = el('div', { cls: 'cause-layer layer-' + L });
      g.appendChild(el('div', { cls: 'cause-layer-head' }, [
        el('span', { cls: 'cause-layer-key', text: L }),
        el('span', { cls: 'cause-layer-name',
                     text: LAYER_NAME[L] || '未知层' }),
      ]));
      byLayer[L].forEach(function (it) { g.appendChild(causeRow(it.c, it.i)); });
      list.appendChild(g);
    });
  }

  function causeRow(c, idx) {
    var ev = el('select', { cls: 'ev-select' });
    ['direct_quote', 'indirect_infer', 'weak_infer'].forEach(function (k) {
      var o = el('option', { text: EV_LABEL[k], attrs: { value: k } });
      if (c.evidence_quality === k) o.selected = true;
      ev.appendChild(o);
    });
    ev.addEventListener('change', function () { c.evidence_quality = ev.value; });

    var evi = el('textarea', { cls: 'ev-text', attrs: { rows: '2',
      placeholder: '证据：原文中的原话' } });
    evi.value = c.evidence || '';
    evi.addEventListener('input', function () { c.evidence = evi.value; });

    var inf = el('textarea', { cls: 'ev-text', attrs: { rows: '2',
      placeholder: '推断：为什么这条原文指向该编码' } });
    inf.value = c.inference || '';
    inf.addEventListener('input', function () { c.inference = inf.value; });

    var del = el('button', { cls: 'btn btn-sm', text: '删除',
                             attrs: { type: 'button' } });
    del.addEventListener('click', function () {
      causeState.splice(idx, 1);
      renderCauses();
      // 计数变了要跟着改，否则「12 条」是骗人的
      var head = document.querySelector('.cause-edit-box .edit-head span');
      if (head) head.textContent = '致因编码（' + causeState.length + ' 条）';
    });

    return el('div', { cls: 'cause-row' }, [
      el('div', { cls: 'cause-row-head' }, [
        el('span', { cls: 'cause-code', text: c.code || '—' }),
        el('span', { cls: 'cause-name', text: c.name || '' }),
        ev, del,
      ]),
      evi, inf,
    ]);
  }

  function addCauseRow() {
    var sel = el('select', { cls: 'ev-select' });
    sel.appendChild(el('option', { text: '选择编码…', attrs: { value: '' } }));
    taxonomy.forEach(function (t) {
      sel.appendChild(el('option', { text: t.code + ' ' + t.name,
                                     attrs: { value: t.code } }));
    });
    var btn = el('button', { cls: 'btn btn-sm', text: '+ 添加',
                             attrs: { type: 'button' } });
    btn.addEventListener('click', function () {
      var code = sel.value;
      if (!code) return;
      var hit = taxonomy.filter(function (t) { return t.code === code; })[0];
      causeState.push({ code: code, name: hit ? hit.name : '',
                        evidence: '', inference: '',
                        evidence_quality: 'direct_quote' });
      renderCauses();
      var head = document.querySelector('.cause-edit-box .edit-head span');
      if (head) head.textContent = '致因编码（' + causeState.length + ' 条）';
    });
    return el('div', { cls: 'cause-add' }, [sel, btn]);
  }

  /* 返回 null 表示「没有改动」，调用方据此决定是否提交 */
  function collectCauses() {
    if (causeState.length !== origCauses.length) return causeState.slice();
    for (var i = 0; i < causeState.length; i++) {
      var a = causeState[i], b = origCauses[i];
      if (a.code !== b.code || a.evidence !== (b.evidence || '')
          || a.inference !== (b.inference || '')
          || a.evidence_quality !== b.evidence_quality) {
        return causeState.slice();
      }
    }
    return null;
  }

  function showDrawerMsg(text, isErr) {
    var box = $('drawerMsg');
    if (!box) return;
    box.className = 'task-error' + (isErr ? '' : ' is-ok');
    box.textContent = text;
    box.hidden = false;
  }

  function act(d, payload) {
    var buttons = ['actApprove', 'actSave', 'actReject'];
    buttons.forEach(function (id) { var b = $(id); if (b) b.disabled = true; });
    post('/api/review/items/' + encodeURIComponent(d.stage_id), payload)
      .then(function (b) {
        showDrawerMsg('已' + (payload.action === 'approve' ? '通过'
                    : payload.action === 'reject' ? '退回' : '保存')
                    + (b.data.changed ? '（改动：' + b.data.changed.join('、') + '）' : ''),
                    false);
        // 改完立刻回读一次：让用户看到的是服务端实际存下的值，
        // 而不是自己刚敲进去的那串字符串
        loadQueue();
        loadStats();
        // 回读是为了让人看到服务端实际存下的值。但用户完全可能在这 350ms 里
        // 已经点了关闭或翻到了下一条——那就别再把它弹回来，那是程序在跟人
        // 抢控制权。只在自己还显示着同一条时才刷新。
        setTimeout(function () {
          if (drawerStageId !== d.stage_id) return;
          openDrawer(d.stage_id);
        }, 350);
      })
      .catch(function (e) {
        showDrawerMsg(e.message, true);
      })
      .then(function () {
        buttons.forEach(function (id) { var b = $(id); if (b) b.disabled = false; });
      });
  }

  // ── ⑤ 批量通过 ───────────────────────────────────────────────

  function batchApprove() {
    var ids = Array.from(picked);
    if (!ids.length) return;
    $('bulkApprove').disabled = true;
    post('/api/review/batch-approve', { stage_ids: ids })
      .then(function (b) {
        var d = b.data;
        var msg = '通过 ' + d.approved + '/' + d.requested + ' 条';
        if (d.skipped && d.skipped.length) {
          // 跳过的必须说出来：只报「12 条过了 9 条」，用户无从复核那 3 条
          msg += '；跳过 ' + d.skipped.length + ' 条：'
               + d.skipped.slice(0, 3).map(function (s) {
                   return (s.title || s.stage_id) + '（' + s.reason + '）';
                 }).join('；')
               + (d.skipped.length > 3 ? ' 等' : '');
        }
        notice(msg, d.skipped && d.skipped.length ? 'warn' : null);
        picked.clear();
        loadQueue();
        loadStats();
      })
      .catch(function (e) { notice('批量通过失败：' + e.message, 'warn'); })
      .then(function () { $('bulkApprove').disabled = false; });
  }

  // ── ⑥ 并入 ───────────────────────────────────────────────────

  function openPromote() {
    var ids = Array.from(picked);
    if (!ids.length) return;
    pendingPlan = null;
    $('promoteRun').disabled = true;
    $('promoteErr').hidden = true;
    clear($('promoteBody'));
    $('promoteBody').appendChild(el('div', { cls: 'empty-note',
                                             text: '正在预演…' }));
    $('promoteModal').hidden = false;
    post('/api/review/promote', { stage_ids: ids, dry_run: true })
      .then(function (b) { renderPlan(b.data, ids); })
      .catch(function (e) { failPlan(e.message); });
  }

  function renderPlan(d, ids) {
    var p = d.plan || {};
    pendingPlan = { stage_ids: ids, token: d.confirm_token,
                    expires_at: d.expires_at };
    var body = clear($('promoteBody'));

    body.appendChild(el('div', { cls: 'kv-grid' }, [
      kv('新增事故', p.accidents_insert),
      kv('覆盖已有', p.accidents_replace),
      kv('写入编码行', p.causes_total),
      kv('令牌有效期至', fmtTime(d.expires_at)),
    ]));

    if (p.index_check && !p.index_check.ok) {
      body.appendChild(el('div', { cls: 'notice notice-warn', text:
        '最终库缺索引 ' + (p.index_check.missing || []).join('、')
        + '，并入前请先运行 scripts/add_indexes.py' }));
    }

    var conflicts = p.conflicts || [];
    if (conflicts.length) {
      body.appendChild(el('div', { cls: 'notice notice-warn',
        text: '有 ' + conflicts.length + ' 条不能并入，本次已全部拦下（不会部分写入）：' }));
      var ul = el('ul', { cls: 'conflict-list' });
      conflicts.slice(0, 20).forEach(function (c) {
        ul.appendChild(el('li', { text: (c.title || c.stage_id) + '：' + c.reason }));
      });
      body.appendChild(ul);
      if (conflicts.length > 20) {
        body.appendChild(el('div', { cls: 'empty-note',
          text: '……另有 ' + (conflicts.length - 20) + ' 条' }));
      }
    } else if (d.ready) {
      body.appendChild(el('div', { cls: 'notice', text:
        '预演通过。确认后将备份最终库并写入，全程可回滚。' }));
    }
    $('promoteRun').disabled = !d.ready;
  }

  function failPlan(msg) {
    $('promoteErr').textContent = msg;
    $('promoteErr').hidden = false;
    $('promoteRun').disabled = true;
  }

  function confirmPromote() {
    if (!pendingPlan) return;
    $('promoteRun').disabled = true;
    post('/api/review/promote', {
      stage_ids: pendingPlan.stage_ids, dry_run: false,
      confirm_token: pendingPlan.token,
    }).then(function (b) {
      var d = b.data;
      $('promoteModal').hidden = true;
      notice('已并入 ' + d.promoted + ' 条事故、' + d.causes_written
             + ' 行编码；备份：' + String(d.backup).split('/').pop());
      // 选择条立刻收起：并入已经落定，让「已选 2 条」继续挂在那儿会让人
      // 以为还能对这批再操作一次。队列刷新是异步的，不能拿它当收起的时机。
      picked.clear();
      syncBulk();
      loadQueue();
      loadStats();
      loadHistory();
    }).catch(function (e) { failPlan(e.message); });
  }

  function doRollback() {
    if (!window.confirm('回滚将删除最近一次并入写入的事故与编码。继续？')) return;
    $('rollbackBtn').disabled = true;
    post('/api/review/rollback', {})
      .then(function (b) {
        var d = b.data;
        $('rollbackHint').textContent = '已回滚 ' + d.removed_accidents
          + ' 条事故、' + d.removed_causes + ' 行编码';
        loadQueue();
        loadStats();
        loadHistory();
      })
      .catch(function (e) { $('rollbackHint').textContent = e.message; })
      .then(function () { $('rollbackBtn').disabled = false; });
  }

  // ── 绑定 ─────────────────────────────────────────────────────

  function bind() {
    $('qFilters').addEventListener('submit', function (ev) {
      ev.preventDefault();
      PAGE = 1;
      loadQueue();
    });
    $('qReset').addEventListener('click', function () {
      $('f_status').value = 'pending';
      $('f_warn').value = '';
      $('f_text').value = '';
      $('f_q').value = '';
      PAGE = 1;
      loadQueue();
      loadStats();
    });
    $('qClean').addEventListener('click', function () {
      $('f_warn').value = $('f_warn').value === 'none' ? '' : 'none';
      PAGE = 1;
      loadQueue();
      syncCleanBtn();
    });
    $('f_size').addEventListener('change', function () { PAGE = 1; loadQueue(); });
    $('qRefresh').addEventListener('click', function () {
      loadQueue(); loadStats(); loadHistory();
    });

    /* ⚠️ 用 `new Event('change')` 合成的事件**不会翻转 checked**，而 change
       处理器是读 `cb.checked` 来判断「用户想选还是想取消」的。所以每次
       dispatch 之前必须自己把 checked 置到目标值——不然「全选本页」会把
       已选的取消掉、把没选的留在原地，「清空」更是反过来全勾上。 */
    function setAll(target) {
      document.querySelectorAll('#qBody tr').forEach(function (tr) {
        var cb = tr.querySelector('input[type=checkbox]');
        if (!cb || cb.checked === target) return;
        cb.checked = target;
        cb.dispatchEvent(new Event('change'));
      });
    }

    $('qAll').addEventListener('click', function () { setAll(true); });
    $('qNone').addEventListener('click', function () { setAll(false); });

    $('bulkApprove').addEventListener('click', batchApprove);
    $('bulkPromote').addEventListener('click', openPromote);
    $('bulkClear').addEventListener('click', function () {
      picked.clear();
      document.querySelectorAll('#qBody tr').forEach(function (tr) {
        var cb = tr.querySelector('input[type=checkbox]');
        if (cb) cb.checked = false;
        tr.className = 'is-pickable';
      });
      syncBulk();
    });

    $('drawerClose').addEventListener('click', closeDrawer);
    document.addEventListener('keydown', function (ev) {
      if (ev.key !== 'Escape') return;
      if (!$('promoteModal').hidden) { $('promoteModal').hidden = true; return; }
      if (!$('drawer').hidden) closeDrawer();
    });

    $('promoteCancel').addEventListener('click', function () {
      $('promoteModal').hidden = true;
    });
    $('promoteRun').addEventListener('click', confirmPromote);

    $('histToggle').addEventListener('click', function () {
      var box = $('histBox');
      box.hidden = !box.hidden;
      $('histToggle').textContent = box.hidden ? '并入历史' : '收起历史';
      if (!box.hidden) loadHistory();
    });
    $('rollbackBtn').addEventListener('click', doRollback);
  }

  // ── 启动 ─────────────────────────────────────────────────────

  // 19 类清单从提取页早已提供的端点上取，不在这里再存一份——
  // 类名与版本只有一个真相来源
  get('/api/extract/taxonomy').then(function (b) {
    taxonomy = ((b.data || {}).caics || {}).classes || [];
  }).catch(function () { /* 取不到只是"新增编码"下拉为空，不影响审核 */ });

  bind();
  loadStats();
  loadQueue();
})();
