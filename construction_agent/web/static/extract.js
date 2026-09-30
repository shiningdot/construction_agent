/* 在线提取页（流水线阶段②）。蓝图 02 §5 的落地。

   四块：理论版本 / 待提取队列 / 任务队列 / 暂存结果，外加一个详情抽屉与一个
   成本确认弹层。数据全走 /api/extract/*，页面本身只有骨架。

   两条贯穿全篇的规矩（与 collect.js 一致）：

   1. **空值是常态**。S3 的 31 个字段里，weather 只有 7.9%、supervision_company
      只有 10.2% 有值。所有渲染走 blank()，没有任何一处会因为 null 崩溃。
   2. **不拼 innerHTML**。要渲染的是事故报道原文与模型给出的证据句，里面出现
      `<` 或 `<script>` 是正常的数据，不是要执行的代码。

   还有一条本页特有的：**成本确认是一道闸门**。点「建提取任务」只弹预估，
   一次 LLM 调用都不发；弹层上点「确认执行」才真的开始跑。 */

(function () {
  'use strict';

  var POLL_MS = 2500;

  /* ── 状态 ───────────────────────────────────────────────── */

  var pending = [];              // 待提取队列（最多 200 条）
  var pendingTotal = 0;
  var picked = new Set();        // 勾选的 raw_id
  var tasks = [];
  var activeSeen = new Set();    // 上一轮轮询时还在跑的任务 id（用来发现「刚跑完」）
  var results = [];
  var resMeta = { total: 0, page: 1, size: 50, pages: 1 };
  var currentStage = null;       // 抽屉正开着的那条
  var pollTimer = null;
  var dbReady = true;
  var currentEst = {};           // 弹层上那份预估，提交失败后要留着

  var STATUS_LABEL = {
    pending: '排队中', running: '运行中', cancelling: '取消中',
    done: '已完成', failed: '失败', cancelled: '已取消',
  };
  var TEXT_LABEL = {
    single: '单个事故', multi: '多事故', regulatory: '通报文件',
    aggregate: '统计汇总', training: '培训材料', other: '其它',
  };
  var REL_LABEL = {
    construction: '施工相关', non_construction: '非施工', insufficient: '信息不足',
  };
  var LAYER_LABEL = {
    M: '管理根基', F: '防线失效', T: '触发条件', H: '人的行为', C: '后果放大',
  };
  // 层序就是因果泄漏顺序 M→F→T→H→C（对齐 step2_caics_encoder 的 sort_order）。
  // 不能靠默认字典序——那样排出来是 C/F/H/M/T，跟理论叙述正好倒过来。
  var LAYER_ORDER = ['M', 'F', 'T', 'H', 'C'];

  /* ── 小工具 ─────────────────────────────────────────────── */

  function fmtTime(s) {
    if (!s) return null;
    var d = new Date(s);
    if (isNaN(d.getTime())) return String(s).slice(0, 16).replace('T', ' ');
    return d.toLocaleString('zh-CN', {
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', hour12: false,
    });
  }

  function fmtDay(s) {
    return s ? String(s).slice(0, 10) : null;
  }

  function fmtNum(n) {
    if (n == null) return '—';
    return Number(n).toLocaleString('zh-CN');
  }

  /* token 量级跨六个数量级，一律折成万/百万，别让用户去数零 */
  function fmtTokens(n) {
    if (!n) return '0';
    if (n >= 1e6) return (n / 1e6).toFixed(2) + 'M';
    if (n >= 1e4) return (n / 1e4).toFixed(1) + '万';
    return fmtNum(n);
  }

  /* 写操作。dom.js 的 fetchJson 只做 GET，POST/DELETE 得自己来。
     错误统一抛 Error(message)，调用处只写一个 catch，不用各自判 ok。 */
  function send(url, method, body) {
    var opts = {
      method: method,
      headers: { Accept: 'application/json' },
    };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    return fetch(url, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (b) {
        if (!r.ok || b.ok === false) {
          throw new Error(b.message || ('HTTP ' + r.status));
        }
        return b;
      });
    });
  }

  function badge(text, cls, title) {
    if (!text) return blank();
    return el('span', { cls: 'badge ' + (cls || ''), text: text,
                        attrs: title ? { title: title } : null });
  }

  function statusBadge(s) {
    var cls = s === 'done' ? 'is-ok' : (s === 'failed' || s === 'cancelled'
              ? 'is-sev' : (s === 'running' ? 'is-accent' : 'is-muted'));
    return badge(STATUS_LABEL[s] || s, cls);
  }

  function textTypeBadge(t) {
    if (!t) return blank();
    return badge(TEXT_LABEL[t] || t, t === 'single' ? 'is-ok' : '');
  }

  function relBadge(v) {
    if (!v) return blank();
    var cls = v === 'construction' ? 'is-ok' : (v === 'non_construction'
              ? 'is-sev' : 'is-warn');
    return badge(REL_LABEL[v] || v, cls);
  }

  /* 证据三级：直引 / 推断 / 弱推断。中文名由后端给（LEVEL_LABELS），
     前端不再维护第二份映射。 */
  function eqBadge(q, label) {
    if (!q) return blank();
    var cls = q === 'direct_quote' ? 'is-ok'
            : (q === 'indirect_infer' ? 'is-warn' : 'is-muted');
    return badge(label || q, cls);
  }

  function showDbNotice(hint) {
    var n = $('dbNotice');
    if (!n) return;
    n.hidden = false;
    clear(n);
    n.appendChild(el('strong', { text: '工作库尚未建立。' }));
    n.appendChild(el('span', {
      text: ' 本页读写的是 caiems_staging.db，它还不存在。在项目根目录执行：' }));
    n.appendChild(el('code', { text: 'python3 scripts/create_staging_db.py' }));
    n.appendChild(el('span', { text: ' 然后刷新本页。' }));
  }

  function emptyRow(tbody, cols, text) {
    tbody.appendChild(el('tr', {}, [
      el('td', { cls: 'empty-cell', text: text, attrs: { colspan: String(cols) } }),
    ]));
  }

  /* ── ① 理论版本 ─────────────────────────────────────────── */

  function loadTaxonomy() {
    return fetchJson('/api/extract/taxonomy').then(function (body) {
      var d = body.data || {};
      var caics = d.caics || {};
      var chips = clear($('taxChips'));

      chips.appendChild(el('span', { cls: 'chip is-base',
        text: '编码体系 ' + (caics.version || '—') }));
      chips.appendChild(el('span', { cls: 'chip',
        text: (caics.total || 0) + ' 类 / 5 层' }));
      chips.appendChild(el('span', { cls: 'chip',
        text: '证据三级：直引 · 推断 · 弱推断' }));

      // 枚举版本从 pipeline_metadata 读，是「这次跑出来的东西依哪一版词表」
      // 的唯一凭证——不显示出来，事后没法判断批次间词表有没有换过
      var enums = d.enums || {};
      Object.keys(enums).forEach(function (k) {
        chips.appendChild(el('span', { cls: 'chip is-muted',
          text: k + ' ' + enums[k], attrs: { title: 'MECE 枚举版本' } }));
      });

      $('taxMeta').textContent = caics.total
        ? '主库现有 ' + fmtNum(totalCauses(caics.classes)) + ' 条编码' : '';

      var tbody = clear($('taxBody'));
      var byLayer = {};
      (caics.classes || []).forEach(function (c) {
        (byLayer[(c.code || ' ')[0]] = byLayer[(c.code || ' ')[0]] || [])
          .push(c);
      });
      Object.keys(byLayer).sort(function (a, b) {
        // 认不出的层名（理论换代时可能出现）排到最后，而不是被静默丢掉
        var ia = LAYER_ORDER.indexOf(a), ib = LAYER_ORDER.indexOf(b);
        return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
      }).forEach(function (L) {
        byLayer[L].forEach(function (c, i) {
          var tr = el('tr', {});
          if (i === 0) {
            tr.appendChild(el('td', {
              cls: 'layer-cell layer-' + L,
              text: L + ' · ' + (LAYER_LABEL[L] || ''),
              attrs: { rowspan: String(byLayer[L].length) },
            }));
          }
          tr.appendChild(el('td', { cls: 'mono', text: c.code }));
          tr.appendChild(el('td', { text: c.name }));
          tr.appendChild(el('td', { cls: 'col-num', text: fmtNum(c.n) }));
          tbody.appendChild(tr);
        });
      });
    }).catch(function (e) {
      $('taxMeta').textContent = '理论版本读取失败：' + e.message;
      emptyRow(clear($('taxBody')), 4, '读不到主库，编码表暂不可用。');
    });
  }

  function totalCauses(classes) {
    return (classes || []).reduce(function (a, c) { return a + (c.n || 0); }, 0);
  }

  /* ── ② 待提取队列 ───────────────────────────────────────── */

  function loadPending() {
    return fetchJson('/api/extract/pending').then(function (body) {
      dbReady = body.available !== false;
      if (!dbReady) {
        showDbNotice(body.hint);
        pending = [];
        pendingTotal = 0;
      } else {
        pending = (body.data || {}).items || [];
        pendingTotal = (body.data || {}).total || pending.length;
      }
      // 列表刷新后，选中集里已经不存在的 id 要丢掉，否则「已选 3 条」
      // 里可能有一条早就不是待提取状态了
      var alive = new Set(pending.map(function (r) { return r.raw_id; }));
      var kept = new Set();
      picked.forEach(function (id) { if (alive.has(id)) kept.add(id); });
      picked = kept;
      renderPending();
    }).catch(function (e) {
      dbReady = false;
      $('pendCount').textContent = '加载失败：' + e.message;
      emptyRow(clear($('pendBody')), 6, '待提取队列加载失败：' + e.message);
    });
  }

  function renderPending() {
    var tbody = clear($('pendBody'));
    $('pendCount').textContent = pending.length
      ? pending.length + ' 条' + (pendingTotal > pending.length
        ? '（共 ' + fmtNum(pendingTotal) + ' 条，显示前 ' + pending.length + '）'
        : '')
      : '';

    if (!pending.length) {
      emptyRow(tbody, 6, pendingTotal
        ? '队列里的都显示完了。' : '没有待提取的原文——先去「在线采集」把相关记录标出来。');
      syncBulk();
      return;
    }

    pending.forEach(function (r) {
      var tr = el('tr', { cls: 'is-pickable' });
      if (picked.has(r.raw_id)) tr.className += ' is-picked';

      var cb = el('input', { attrs: { type: 'checkbox' } });
      cb.checked = picked.has(r.raw_id);

      function applyPicked(on) {
        cb.checked = on;
        if (on) picked.add(r.raw_id); else picked.delete(r.raw_id);
        tr.className = 'is-pickable' + (on ? ' is-picked' : '');
        syncBulk();
      }

      cb.addEventListener('change', function () { applyPicked(cb.checked); });
      // 整行可点：这表有 6 列，非逼着人去够最左边那个小方框没道理。
      // 点方框本身时不重复处理（交给上面的 change）
      tr.addEventListener('click', function (ev) {
        if (ev.target === cb) return;
        applyPicked(!cb.checked);
      });
      tr.appendChild(el('td', { cls: 'col-pick' }, [cb]));

      tr.appendChild(el('td', { cls: 'col-title' }, [
        el('span', { text: r.title || '（无标题）' }),
      ]));
      tr.appendChild(el('td', { cls: 'col-src' }, [
        el('span', { cls: 'mono src-key', text: r.source_key || '' }),
      ]));
      tr.appendChild(el('td', { cls: 'col-date',
        text: fmtDay(r.publish_date) || blank() }));
      tr.appendChild(el('td', { cls: 'col-num', text: fmtNum(r.content_len) }));
      tr.appendChild(el('td', { cls: 'col-num',
        text: r.assets ? fmtNum(r.assets) : '—' }));
      tbody.appendChild(tr);
    });

    $('pendPager').textContent = '';
    syncBulk();
  }

  function syncBulk() {
    var bar = $('bulkBar');
    bar.hidden = picked.size === 0;
    $('bulkCount').textContent = '已选 ' + picked.size + ' 条';
    // 精确预估在弹层里算（每勾一条就打一次接口太吵），这里只说下一步会发生什么
    $('bulkEst').textContent = picked.size
      ? '下一步会给出用量与费用预估，确认后才真正调用模型' : '';
  }

  /* ── ③ 任务队列 ─────────────────────────────────────────── */

  function loadTasks() {
    return fetchJson('/api/extract/tasks').then(function (body) {
      var next = body.data || [];
      var nowActive = new Set();
      var justFinished = false;
      next.forEach(function (t) {
        if (isActive(t)) nowActive.add(t.task_id);
        else if (activeSeen.has(t.task_id)) justFinished = true;
      });
      activeSeen = nowActive;
      tasks = next;
      renderTasks();
      syncPoll();
      // 任务从「在跑」变成「跑完」的那一刻刷一次结果表：跑完提取最想看的
      // 就是刚出来那批，不该逼用户自己去点「查询」才发现它已经在了
      if (justFinished) refreshResultsIfClean();
    }).catch(function (e) {
      $('taskCount').textContent = '加载失败：' + e.message;
      clear($('taskList')).appendChild(
        el('p', { cls: 'empty', text: '任务列表加载失败：' + e.message }));
    });
  }

  function isActive(t) {
    return t.status === 'running' || t.status === 'pending'
        || t.status === 'cancelling';
  }

  /* 有任务在跑才轮询。都跑完了还每 2.5 秒打一次接口，纯属浪费。 */
  function syncPoll() {
    var active = tasks.some(isActive);
    if (active && !pollTimer) {
      pollTimer = setInterval(loadTasks, POLL_MS);
    } else if (!active && pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  /* 自动刷新只在「用户没在筛」时做。带着筛选条件还去刷，会把人家正在看的
     那一页悄悄换掉。 */
  function refreshResultsIfClean() {
    if ($('f_task').value || $('f_text').value || $('f_rel').value
        || $('f_cause').value || $('f_q').value.trim()) return;
    loadResults();
  }

  function renderTasks() {
    var box = clear($('taskList'));
    $('taskCount').textContent = tasks.length
      ? tasks.length + ' 条（最近 ' + tasks.length + ' 条）' : '';

    var sel = $('f_task');
    var keep = sel.value;
    clear(sel);
    sel.appendChild(el('option', { attrs: { value: '' }, text: '全部' }));
    tasks.forEach(function (t) {
      sel.appendChild(el('option', {
        attrs: { value: t.task_id },
        text: t.task_id + ' · ' + (STATUS_LABEL[t.status] || t.status)
              + ' · ' + t.items + ' 条',
      }));
    });
    sel.value = keep;

    if (!tasks.length) {
      box.appendChild(el('p', { cls: 'empty', text: '还没有提取任务。' }));
      return;
    }
    tasks.forEach(function (t) { box.appendChild(taskCard(t)); });
  }

  function taskCard(t) {
    var card = el('div', { cls: 'task-card is-' + t.status });
    var p = t.progress || {};

    var top = el('div', { cls: 'task-top' }, [
      el('span', { cls: 'mono task-id', text: t.task_id }),
      statusBadge(t.status),
      el('span', { cls: 'task-scope',
                   text: t.items + ' 条原文 · 并发 ' + (t.concurrency || '—') }),
      el('span', { cls: 'task-time', text: fmtTime(t.created_at) || '' }),
    ]);

    var actions = el('div', { cls: 'task-actions' });
    if (t.status === 'running' || t.status === 'pending'
        || t.status === 'cancelling') {
      var cancel = el('button', { cls: 'btn btn-sm', text: '取消',
                                  attrs: { type: 'button' } });
      cancel.disabled = t.status === 'cancelling';
      cancel.addEventListener('click', function () { cancelTask(t, cancel); });
      actions.appendChild(cancel);
    } else {
      var del = el('button', { cls: 'btn btn-sm', text: '删除',
                               attrs: { type: 'button' } });
      del.addEventListener('click', function () { deleteTask(t, del); });
      actions.appendChild(del);
    }
    top.appendChild(actions);
    card.appendChild(top);

    // 进度条的分母是「选了几条原文」，这个数是真知道的——提取不像采集，
    // 没有「抓完才知道有多少」的问题，百分比是准的
    card.appendChild(el('div', { cls: 'task-progress' }, [
      el('div', { cls: 'task-progress-fill',
                  attrs: { style: 'width:' + (p.percent || 0) + '%' } }),
    ]));

    card.appendChild(el('div', { cls: 'task-totals' }, [
      el('span', { text: '已跑 ' + (p.done || 0) + '/' + (p.total || t.items) }),
      el('span', { text: '入库 ' + fmtNum(t.staged) }),
      el('span', { text: '失败 ' + fmtNum(p.failed) }),
      el('span', { text: '用量 ' + fmtTokens(usage(t).tokens) + ' token' }),
      el('span', { text: '约 ¥' + (t.usage.cost_cny || 0).toFixed(2),
                   attrs: { title: '按估算单价折算，非结算依据' } }),
    ]));

    // 四段占比：直接从 token 用量看哪一段最贵，比看耗时准
    var byType = t.by_type || {};
    var keys = Object.keys(byType);
    if (keys.length) {
      var chips = el('div', { cls: 'chips' });
      chips.appendChild(el('span', { cls: 'chips-label', text: '类型分布' }));
      keys.forEach(function (k) {
        chips.appendChild(el('span', { cls: 'chip',
          text: (TEXT_LABEL[k] || k) + ' ' + byType[k] }));
      });
      // 被闸门拦下的原文不会进暂存区，这个差额不解释清楚，
      // 用户会以为「选了 10 条怎么只有 7 条」
      var blocked = (t.items || 0) - (t.staged || 0) - (p.failed || 0);
      if (blocked > 0) {
        chips.appendChild(el('span', { cls: 'chip is-muted',
          text: '闸门拦下 ' + blocked + '（非施工 / 非事故文本）',
          attrs: { title: 'S1 类型闸门与 S2 相关性闸门拦下的记录不建暂存行，'
                          + '这是设计如此，不是失败' } }));
      }
      card.appendChild(chips);
    }

    var u = usage(t);
    if (u.dev_pct != null) {
      var dev = Math.abs(u.dev_pct);
      card.appendChild(el('div', { cls: 'task-totals' }, [
        el('span', { cls: 'muted', text:
          '预估 ' + fmtTokens(u.est_so_far) + ' / 实际 ' + fmtTokens(u.tokens)
          + ' token（偏差 ' + (u.dev_pct > 0 ? '+' : '') + u.dev_pct + '%）',
          attrs: { title: '预估是量级概念，实际用量以调用回执为准' } }),
      ]));
    }

    var errors = t.errors || [];
    if (errors.length) {
      var ul = el('ul', { cls: 'task-sources' });
      errors.slice(0, 20).forEach(function (e) {
        var line = el('li', {});
        line.appendChild(el('span', { cls: 'ts-name',
          text: e.title || e.raw_id || '（无标题）' }));
        line.appendChild(badge(e.stage || '失败', 'is-sev'));
        line.appendChild(el('span', { cls: 'ts-err', text: e.reason || '',
                                      attrs: { title: e.reason || '' } }));
        ul.appendChild(line);
      });
      card.appendChild(ul);
      if (errors.length > 20) {
        card.appendChild(el('p', { cls: 'panel-note',
          text: '另有 ' + (errors.length - 20) + ' 条失败未列出。' }));
      }
    }

    if (t.error) {
      card.appendChild(el('p', { cls: 'task-error', text: t.error }));
    }
    return card;
  }

  function usage(t) {
    var u = (t && t.usage) || {};
    return {
      tokens: u.tokens || 0,
      est_so_far: u.est_tokens_so_far || 0,
      dev_pct: u.deviation_pct,
    };
  }

  function cancelTask(t, btn) {
    btn.disabled = true;
    send('/api/extract/tasks/' + t.task_id + '/cancel', 'POST')
      .catch(function (e) { alert('取消失败：' + e.message); })
      .then(loadTasks);
  }

  function deleteTask(t, btn) {
    if (!window.confirm('删除任务 ' + t.task_id + '？\n\n'
        + '已经提取出来的 ' + (t.staged || 0) + ' 条暂存产出会保留，'
        + '不受影响。')) return;
    btn.disabled = true;
    send('/api/extract/tasks/' + t.task_id, 'DELETE')
      .catch(function (e) { alert('删除失败：' + e.message); })
      .then(loadTasks);
  }

  /* ── ④ 成本确认弹层 ─────────────────────────────────────── */

  /* 弹层里的错误条与按钮状态解耦：提交失败时预估还在上面，用户能直接重试，
     不该因为一次网络抖动就把两个按钮全禁掉。 */
  function costError(msg) {
    var n = $('costErr');
    n.hidden = !msg;
    n.textContent = msg || '';
  }

  function openCost() {
    if (!picked.size) return;
    currentEst = {};
    costError('');
    showModal(true);
    clear($('costBody')).appendChild(el('p', { cls: 'empty', text: '正在估算…' }));
    $('costRun').disabled = true;
    $('costQueue').disabled = true;

    // 预估走服务端：系数只有一份，写在后端执行器里
    fetchJson('/api/extract/estimate?n=' + picked.size)
      .then(function (body) {
        renderCost(body.data || {});
        costError('');
      })
      .catch(function (e) {
        renderCost({});
        costError('估算失败：' + e.message);
      });
  }

  function renderCost(est) {
    var box = clear($('costBody'));
    var n = est.items || picked.size;

    if (!est.est_tokens) {
      // 估不出来就不让点——这两个按钮都会真的花钱
      box.appendChild(el('p', { cls: 'empty',
        text: '没拿到预估，暂时不能提交。' }));
      $('costRun').disabled = true;
      $('costQueue').disabled = true;
      return;
    }
    currentEst = est;

    box.appendChild(el('p', { cls: '',
      text: '将对选中的 ' + n + ' 条原文跑完整链路 S1→S2→S3→S4。' }));

    var kv = el('div', { cls: 'kv-grid' });
    [
      ['原文条数', n + ' 条'],
      ['预估用量', fmtTokens(est.est_tokens) + ' token'
        + '（输入 ' + fmtTokens(est.est_tokens_in)
        + ' / 输出 ' + fmtTokens(est.est_tokens_out) + '）'],
      ['预估费用', '约 ¥' + (est.est_cost_cny || 0).toFixed(2)],
      ['阶段', (est.stages || []).join(' · ')],
    ].forEach(function (pair) {
      kv.appendChild(el('span', { cls: 'kv-key', text: pair[0] }));
      kv.appendChild(el('span', { cls: 'kv-val', text: pair[1] }));
    });
    box.appendChild(kv);

    box.appendChild(el('p', { cls: 'panel-note', text: est.note || '' }));
    box.appendChild(el('p', { cls: 'panel-note',
      text: '点「确认执行」才会真正调用模型；点「仅建任务」只排进队列，'
          + '之后可以在任务列表里再启动。' }));

    $('costRun').disabled = false;
    $('costQueue').disabled = false;
  }

  function createTask(startNow, btn) {
    btn.disabled = true;
    var ids = Array.from(picked);
    send('/api/extract/tasks', 'POST', { raw_ids: ids })
      .then(function (body) {
        var tid = (body.data || {}).task_id;
        if (!startNow) return tid;
        // 两步式：建任务不花钱，start 才花。用户点了「确认执行」才走这一步
        return send('/api/extract/tasks/' + tid + '/start', 'POST')
          .then(function () { return tid; });
      })
      .then(function () {
        showModal(false);
        picked.clear();
        syncBulk();
        // 任务一建起来马上拉一次，别等下一个轮询周期才看见
        return loadTasks().then(function () {
          if (startNow) loadPending();
        });
      })
      .catch(function (e) {
        // 出错时弹层不关——预估还在上面，用户能直接重试
        costError('提交失败：' + e.message);
        btn.disabled = false;
      });
  }

  function showModal(on) {
    var m = $('costModal');
    m.hidden = !on;
  }

  /* ── ⑤ 暂存结果 ─────────────────────────────────────────── */

  function resQuery() {
    var p = [];
    function add(k, v) { if (v) p.push(k + '=' + encodeURIComponent(v)); }
    add('task_id', $('f_task').value);
    add('text_type', $('f_text').value);
    add('relevance_verdict', $('f_rel').value);
    add('q', $('f_q').value.trim());
    add('size', $('f_size').value);
    if ($('f_cause').value === '1') p.push('no_cause=1');
    return p.join('&');
  }

  function loadResults(page) {
    var q = resQuery();
    var url = '/api/extract/results?page=' + (page || 1) + (q ? '&' + q : '');
    return fetchJson(url).then(function (body) {
      if (body.available === false) {
        showDbNotice(body.hint);
        results = [];
        resMeta = { total: 0, page: 1, size: 50, pages: 1 };
      } else {
        results = body.data || [];
        resMeta = body.meta || resMeta;
      }
      renderResults();
    }).catch(function (e) {
      $('resCount').textContent = '加载失败：' + e.message;
      emptyRow(clear($('resBody')), 11, '暂存结果加载失败：' + e.message);
    });
  }

  function renderResults() {
    var tbody = clear($('resBody'));
    $('resCount').textContent = resMeta.total
      ? '共 ' + fmtNum(resMeta.total) + ' 条 · 第 ' + resMeta.page
        + '/' + resMeta.pages + ' 页' : '';

    if (!results.length) {
      emptyRow(tbody, 11, resMeta.total === 0
        ? '还没有提取产出。跑一个任务，结果会出现在这里。'
        : '当前筛选条件下没有记录。');
      renderPager($('resPager'), resMeta, loadResults);
      return;
    }

    results.forEach(function (r) {
      var tr = el('tr', { cls: 'is-clickable' });
      tr.appendChild(el('td', { cls: 'col-title' }, [
        el('span', { text: r.title || '（无标题）' }),
      ]));
      tr.appendChild(el('td', {}, [textTypeBadge(r.text_type)]));
      tr.appendChild(el('td', { text: r.severity || blank() }));
      tr.appendChild(el('td', { cls: 'col-num',
        text: r.deaths != null ? fmtNum(r.deaths) : '—' }));
      tr.appendChild(el('td', { cls: 'col-num',
        text: r.injuries != null ? fmtNum(r.injuries) : '—' }));
      tr.appendChild(el('td', {
        text: [r.location, r.city].filter(Boolean).join(' · ') || blank() }));
      tr.appendChild(el('td', { cls: 'col-num' }, [
        r.cause_count
          ? el('span', { cls: 'badge is-ok', text: String(r.cause_count) })
          : badge('0', 'is-sev', '这一条没编出致因，点开看原因'),
      ]));
      tr.appendChild(el('td', { cls: 'col-num mono',
        text: r.s3_ms ? (r.s3_ms / 1000).toFixed(1) + 's' : '—' }));
      tr.appendChild(el('td', { cls: 'col-num mono',
        text: r.s4_ms ? (r.s4_ms / 1000).toFixed(1) + 's' : '—' }));
      // 入库时间让人一眼看出哪批是刚提的；也只到分钟，秒级差异没意义
      tr.appendChild(el('td', { cls: 'col-date',
        text: fmtTime(r.created_at) || '—' }));

      var open = el('button', { cls: 'btn btn-sm', text: '查看',
                               attrs: { type: 'button' } });
      open.addEventListener('click', function (ev) {
        ev.stopPropagation();
        openDetail(r.stage_id);
      });
      tr.appendChild(el('td', { cls: 'col-act' }, [open]));
      tr.addEventListener('click', function () { openDetail(r.stage_id); });
      tbody.appendChild(tr);
    });

    renderPager($('resPager'), resMeta, loadResults);
  }

  function renderPager(box, meta, go) {
    clear(box);
    if (!meta.pages || meta.pages <= 1) return;
    var prev = el('button', { cls: 'btn btn-sm', text: '上一页',
                              attrs: { type: 'button' } });
    prev.disabled = meta.page <= 1;
    prev.addEventListener('click', function () { go(meta.page - 1); });
    var next = el('button', { cls: 'btn btn-sm', text: '下一页',
                              attrs: { type: 'button' } });
    next.disabled = meta.page >= meta.pages;
    next.addEventListener('click', function () { go(meta.page + 1); });
    box.appendChild(prev);
    box.appendChild(el('span', { cls: 'muted',
      text: '第 ' + meta.page + ' / ' + meta.pages + ' 页' }));
    box.appendChild(next);
  }

  /* ── ⑥ 详情抽屉 ─────────────────────────────────────────── */

  function openDetail(stageId) {
    currentStage = stageId;
    var drawer = $('drawer');
    drawer.hidden = false;
    var body = clear($('drawerBody'));
    body.appendChild(el('p', { cls: 'empty', text: '加载中…' }));
    $('drawerTitle').textContent = '暂存产出 ' + stageId;

    fetchJson('/api/extract/results/' + stageId).then(function (b) {
      renderDetail(b.data || {});
    }).catch(function (e) {
      clear(body).appendChild(el('p', { cls: 'task-error',
        text: '加载失败：' + e.message }));
    });
  }

  function renderDetail(d) {
    var body = clear($('drawerBody'));
    var f = d.fields || {};
    var g = d.gates || {};
    var m = d.meta || {};

    // ── 闸门判定：这一条是怎么走到提取这一步的 ──
    var gate = el('div', { cls: 'drawer-block' });
    gate.appendChild(el('div', { cls: 'drawer-title', text: '闸门判定' }));
    gate.appendChild(el('div', { cls: 'chips' }, [
      el('span', { cls: 'chips-label', text: '类型' }),
      textTypeBadge(g.text_type),
      el('span', { cls: 'muted', text: g.text_type_reason || '' }),
    ]));
    var chips2 = el('div', { cls: 'chips' }, [
      el('span', { cls: 'chips-label', text: '相关性' }),
      relBadge(g.relevance_verdict),
      el('span', { cls: 'muted', text: (g.relevance_stage
        ? '（' + g.relevance_stage + '）' : '') + (g.relevance_reason || '') }),
    ]);
    gate.appendChild(chips2);
    body.appendChild(gate);

    // ── 31 个基础字段 ──
    var fields = el('div', { cls: 'drawer-block' });
    fields.appendChild(el('div', { cls: 'drawer-title', text: '基础字段（S3）' }));
    var list = el('div', { cls: 'field-list' });
    Object.keys(f).forEach(function (k) {
      var v = f[k];
      if (v === null || v === undefined || v === '') return;
      var row = el('div', { cls: 'field-row' }, [
        el('span', { cls: 'field-key', text: k }),
      ]);
      // summary / direct_cause 这类是模型写的长句，按段落显示
      var long = String(v).length > 60;
      row.appendChild(el('span', { cls: 'field-val' + (long ? ' is-long' : ''),
                                   text: String(v) }));
      list.appendChild(row);
    });
    // 有值率低的字段大多是空的，要明说「多少列没值」，否则用户会以为漏了
    var missing = Object.keys(f).filter(function (k) {
      var v = f[k];
      return v === null || v === undefined || v === '';
    });
    if (missing.length) {
      list.appendChild(el('div', { cls: 'field-row' }, [
        el('span', { cls: 'field-key', text: '未提及' }),
        el('span', { cls: 'field-val muted', text:
          missing.length + ' 个字段原报告没写（' + missing.join('、') + '）' }),
      ]));
    }
    fields.appendChild(list);
    body.appendChild(fields);

    // ── 致因编码与证据链 ──
    var causes = el('div', { cls: 'drawer-block' });
    causes.appendChild(el('div', { cls: 'drawer-title',
      text: 'CAICS 致因编码（' + (d.causes || []).length + ' 条）' }));
    if (!d.causes || !d.causes.length) {
      causes.appendChild(el('p', { cls: 'task-error',
        text: '这一条没有编出致因（S4 返回空或失败）。看下方「过程」里的 S4 耗时，'
            + '若为 0 则说明这一步没跑成。' }));
    } else {
      var ul = el('ul', { cls: 'cause-list' });
      d.causes.forEach(function (c) {
        var li = el('li', { cls: 'cause-item' });
        li.appendChild(el('div', { cls: 'cause-head' }, [
          el('span', { cls: 'chip layer-' + (c.code || ' ')[0],
                       text: c.code || '' }),
          el('span', { cls: 'cause-name', text: c.name || '' }),
          eqBadge(c.evidence_quality, c.evidence_quality_label),
        ]));
        if (c.evidence) {
          li.appendChild(el('div', { cls: 'cause-line' }, [
            el('span', { cls: 'cause-key', text: '证据' }),
            el('span', { cls: 'cause-text', text: c.evidence }),
          ]));
        }
        if (c.inference) {
          li.appendChild(el('div', { cls: 'cause-line' }, [
            el('span', { cls: 'cause-key', text: '推理' }),
            el('span', { cls: 'cause-text', text: c.inference }),
          ]));
        }
        ul.appendChild(li);
      });
      causes.appendChild(ul);
      causes.appendChild(el('p', { cls: 'panel-note',
        text: '证据等级由证据句本身判定：句中带「原文未直接」的记为弱推断，'
            + '带「推断 / 表明」的记为推断，其余为直引。' }));
    }
    body.appendChild(causes);

    // ── 对照原文 ──
    if (d.raw) {
      var raw = el('div', { cls: 'drawer-block' });
      raw.appendChild(el('div', { cls: 'drawer-title', text: '原始文本' }));
      raw.appendChild(el('div', { cls: 'drawer-src' }, [
        el('span', { cls: 'mono', text: d.raw.source_key || '' }),
        el('span', { cls: 'muted',
          text: fmtDay(d.raw.publish_date) || '发布日期未提取到' }),
      ]));
      // 原文是抓回来的自由文本，只用 textContent 放进 <pre>，绝不解析
      raw.appendChild(el('pre', { cls: 'raw-text',
        text: d.raw.content || '（原文为空）' }));
      body.appendChild(raw);
    }

    // ── 过程元数据 ──
    var meta = el('div', { cls: 'drawer-block' });
    meta.appendChild(el('div', { cls: 'drawer-title', text: '过程' }));
    var kv = el('div', { cls: 'kv-grid' });
    [
      ['S3 模型', m.s3_model], ['S3 耗时', m.s3_ms ? m.s3_ms + ' ms' : null],
      ['S3 用量', tokLine(m.s3_tokens_in, m.s3_tokens_out)],
      ['S4 模型', m.s4_model], ['S4 耗时', m.s4_ms ? m.s4_ms + ' ms' : null],
      ['S4 用量', tokLine(m.s4_tokens_in, m.s4_tokens_out)],
      ['入库时间', fmtTime(m.created_at)],
      ['任务', d.task_id],
    ].forEach(function (pair) {
      if (pair[1] == null || pair[1] === '') return;
      kv.appendChild(el('span', { cls: 'kv-key', text: pair[0] }));
      kv.appendChild(el('span', { cls: 'kv-val', text: String(pair[1]) }));
    });
    meta.appendChild(kv);
    // 模型名写进数据里是为了事后能追溯批次用的哪个档位——不显示出来
    // 就白记了
    meta.appendChild(el('p', { cls: 'panel-note',
      text: '模型名记录的是本次实际调用的档位，不是当前配置值；'
          + '重跑后会被新值覆盖。' }));
    body.appendChild(meta);
  }

  function tokLine(i, o) {
    if (!i && !o) return null;
    return fmtTokens((i || 0) + (o || 0)) + ' token（入 ' + fmtTokens(i || 0)
         + ' / 出 ' + fmtTokens(o || 0) + '）';
  }

  function closeDrawer() {
    $('drawer').hidden = true;
    currentStage = null;
  }

  function rerun() {
    if (!currentStage) return;
    if (!window.confirm('重跑这条暂存产出？\n\n'
        + '当前的记录与它下面的致因编码会先删掉，再重新跑一遍 S1–S4。'
        + '重跑会再次调用模型，产生新的费用。')) return;
    var btn = $('rerunBtn');
    btn.disabled = true;
    send('/api/extract/results/' + currentStage + '/rerun', 'POST')
      .then(function (body) {
        var d = body.data || {};
        closeDrawer();
        loadResults(resMeta.page);
        loadPending();
        alert('重跑完成，新的 stage_id 是 ' + (d.stage_id || '—')
              + '，产出编码 ' + (d.causes || 0) + ' 条。');
      })
      .catch(function (e) { alert('重跑失败：' + e.message); })
      .then(function () { btn.disabled = false; });
  }

  /* ── 事件绑定 ───────────────────────────────────────────── */

  function bind() {
    $('pendAll').addEventListener('click', function () {
      pending.forEach(function (r) { picked.add(r.raw_id); });
      renderPending();
    });
    $('pendNone').addEventListener('click', function () {
      picked.clear();
      renderPending();
    });
    $('pendRefresh').addEventListener('click', function () {
      loadPending().then(loadTasks);
    });
    $('bulkClear').addEventListener('click', function () {
      picked.clear();
      renderPending();
    });
    $('bulkPlan').addEventListener('click', openCost);

    $('costRun').addEventListener('click', function () {
      createTask(true, this);
    });
    $('costQueue').addEventListener('click', function () {
      createTask(false, this);
    });
    $('costCancel').addEventListener('click', function () { showModal(false); });

    $('refreshTasks').addEventListener('click', loadTasks);
    $('drawerClose').addEventListener('click', closeDrawer);
    $('rerunBtn').addEventListener('click', rerun);

    $('taxToggle').addEventListener('click', function () {
      var box = $('taxClasses');
      box.hidden = !box.hidden;
      this.textContent = box.hidden ? '展开 19 类' : '收起';
    });

    $('resFilters').addEventListener('submit', function (e) {
      e.preventDefault();
      // 换了筛选条件就回第一页——否则会停在一个新条件下并不存在的页码上
      loadResults(1);
    });
    $('resReset').addEventListener('click', function () {
      $('resFilters').reset();
      loadResults(1);
    });

    // 抽屉开着时按 Esc 关掉
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') {
        if (!$('costModal').hidden) showModal(false);
        else if (!$('drawer').hidden) closeDrawer();
      }
    });
  }

  /* ── 启动 ───────────────────────────────────────────────── */

  bind();
  loadTaxonomy();
  loadPending().then(loadTasks);
  loadResults(1);
})();
