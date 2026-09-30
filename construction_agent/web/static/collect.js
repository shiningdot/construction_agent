/* 在线采集页（流水线阶段①）。蓝图 01 §5 的落地。

   六个面板：源清单 / 新建任务 / 任务队列 / 数据漏斗 / 原始数据 / 批量裁决条。
   数据全部走 /api/collect/*，页面本身只有骨架。

   两条贯穿全篇的规矩：

   1. **空值是常态**。采集阶段标题可能抓不到、发布日期可能埋在 PDF 里提不出来、
      闸门可能还没判。所有渲染走 blank()，没有任何一处会因为 null 崩溃。
   2. **不拼 innerHTML**。要渲染的是从政府站点抓回来的自由文本，里面出现
      `<script>` 是正常的数据，不是要执行的代码。raw_html 那一栏尤其要小心——
      它在抽屉里是以 `<pre>` + textContent 展示的源码，绝不解析。 */

(function () {
  'use strict';

  var POLL_MS = 2000;

  /* ── 状态 ───────────────────────────────────────────────── */

  var sources = [];          // 源清单（registry 的 24 条 + 库里的运行期状态）
  var picked = new Set();    // 选中的 source_key
  var tasks = [];
  var rawPage = 1;
  var rawMeta = { total: 0, page: 1, size: 50, pages: 1 };
  var rawRows = [];          // 当前页的行，批量操作用
  var checked = new Set();   // 原始数据里勾选的 raw_id
  var pollTimer = null;
  var dbReady = true;        // 工作库建了没。没建就不让点「开始采集」

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

  var STATUS_LABEL = {
    new: '未处理', gated: '已判定', promoted: '已送提取', archived: '已归档',
  };
  var VERDICT_LABEL = {
    confirmed: '相关', rejected: '不相关', review: '待人工',
  };
  var TASK_LABEL = {
    pending: '排队中', running: '运行中', cancelling: '取消中',
    done: '已完成', failed: '失败', cancelled: '已取消',
  };
  // `raw_accident_assets` 的两列。取值域写在 `staging_schema.py:111/115` 的列注释里，
  // 由 `scripts/check_enum_alignment.py` 核两侧是否一一对应——**加取值先加注释**，
  // 否则这里漏一个键就是静默漏一次翻译，页面上直接冒英文码。
  var ASSET_TYPE_LABEL = {
    pdf: 'PDF 文档', image: '图片', attachment: '附件',
  };
  var OCR_LABEL = {
    pending: '待处理', done: '已完成', failed: '失败', skipped: '已跳过',
  };

  function badge(text, cls, title) {
    if (!text) return blank();
    return el('span', { cls: 'badge ' + (cls || ''), text: text,
                        attrs: title ? { title: title } : null });
  }

  function statusBadge(s) {
    var cls = s === 'promoted' ? 'is-ok' : (s === 'new' ? '' : 'is-muted');
    return badge(STATUS_LABEL[s] || s, cls);
  }

  function verdictBadge(v, stage) {
    if (!v) return badge('未判定', 'is-muted');
    var cls = v === 'confirmed' ? 'is-ok' : (v === 'rejected' ? 'is-sev' : '');
    var who = stage === 'manual' ? '（人工）' : (stage === 'rule' ? '（规则）'
              : (stage === 'llm' ? '（模型）' : ''));
    return badge((VERDICT_LABEL[v] || v) + who, cls);
  }

  function notice(text) {
    var t = $('taskHint');
    if (t) t.textContent = text || '';
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
    n.appendChild(el('span', { text: ' 然后刷新本页。' +
      (hint ? '' : '') }));
  }

  /* ── ① 源清单 ───────────────────────────────────────────── */

  function loadSources() {
    return fetchJson('/api/collect/sources').then(function (body) {
      sources = body.data || [];
      dbReady = body.available !== false;
      if (!dbReady) showDbNotice(body.hint);
      renderSources();
    }).catch(function (e) {
      // 连源清单都拉不到（服务没起来 / 路由错了）——这时候点「开始采集」
      // 只会再吃一次同样的错，索性禁掉
      dbReady = false;
      $('srcCount').textContent = '加载失败：' + e.message;
      syncPickedCount();
    });
  }

  function renderSources() {
    var tbody = clear($('srcBody'));

    // 按分组排，组内保持 registry 的字典序。组名用后端给的中文标签，
    // 前端不再维护一份映射——两份映射一定会漂移。
    var groups = {};
    sources.forEach(function (s) {
      (groups[s.source_group] = groups[s.source_group] || []).push(s);
    });

    Object.keys(groups).forEach(function (g) {
      var list = groups[g];
      var label = list[0].group_label || g;
      var tr = el('tr', { cls: 'group-row' });
      tr.appendChild(el('td', { cls: 'group-cell',
        text: label + ' · ' + list.length + ' 个' ,
        attrs: { colspan: '8' } }));
      tbody.appendChild(tr);
      list.forEach(function (s) { tbody.appendChild(sourceRow(s)); });
    });

    var active = sources.filter(function (s) { return s.is_active; }).length;
    $('srcCount').textContent = sources.length + ' 个源，' + active + ' 个已启用';
    $('srcNote').textContent =
      '该跑哪几个由你勾选；一个源一次任务只跑一遍。「累计入库」是该源历次'
      + '采集真正写进工作库的条数（被去重或筛选拦下的不计）。';
    syncPickedCount();
  }

  function sourceRow(s) {
    var tr = el('tr', { cls: s.is_active ? '' : 'is-off' });

    var pick = el('input', { attrs: { type: 'checkbox' } });
    pick.checked = picked.has(s.source_key);
    pick.disabled = !s.is_active;
    pick.addEventListener('change', function () {
      if (pick.checked) picked.add(s.source_key);
      else picked.delete(s.source_key);
      syncPickedCount();
    });
    tr.appendChild(el('td', { cls: 'col-pick' }, [pick]));

    var name = el('td', { cls: 'col-src' }, [
      el('span', { cls: 'src-name', text: s.display_name }),
      el('span', { cls: 'mono src-key', text: s.source_key }),
    ]);
    if (s.note) name.title = s.note;
    tr.appendChild(name);

    var auth = el('td', { cls: 'col-auth' });
    if (s.entry_url) {
      auth.appendChild(el('a', {
        text: s.authority || '打开', cls: 'ext',
        attrs: { href: s.entry_url, target: '_blank',
                 rel: 'noopener noreferrer', title: s.entry_url },
      }));
    } else {
      auth.appendChild(el('span', { text: s.authority || '—' }));
    }
    tr.appendChild(auth);

    tr.appendChild(el('td', {
      cls: 'col-time',
      text: fmtTime(s.last_run_at) || '从未运行',
    }));
    tr.appendChild(el('td', { cls: 'col-num', text: num(s.total_raw) }));
    tr.appendChild(el('td', { cls: 'col-num', text: num(s.raw_pending) }));

    tr.appendChild(el('td', { cls: 'col-health' }, [healthBadge(s)]));

    var run = el('button', {
      cls: 'btn btn-sm', text: '单跑',
      attrs: { type: 'button', title: '只跑这一个源' },
    });
    run.addEventListener('click', function () {
      run.disabled = true;
      startTask([s.source_key]);
    });
    tr.appendChild(el('td', { cls: 'col-act' }, [run]));

    return tr;
  }

  function healthBadge(s) {
    if (s.never_run) return badge('未跑过', 'is-muted', '这个源还没有采集记录');
    if (s.last_run_ok === true) return badge('正常', 'is-ok');
    if (s.last_run_ok === false) {
      return badge('失败', 'is-sev', s.last_error || '上次运行失败');
    }
    return badge('—', 'is-muted');
  }

  function syncPickedCount() {
    var n = picked.size;
    $('pickedCount').textContent = n ? '已选 ' + n + ' 个源' : '尚未选择源';
    $('startBtn').disabled = n === 0 || !dbReady;
  }

  $('pickAll').addEventListener('click', function () {
    sources.forEach(function (s) { if (s.is_active) picked.add(s.source_key); });
    renderSources();
  });
  $('pickNone').addEventListener('click', function () {
    picked.clear();
    renderSources();
  });

  /* ── ② 新建任务 ─────────────────────────────────────────── */

  function startTask(keys) {
    var body = { source_keys: keys };
    var since = $('t_since').value;
    var until = $('t_until').value;
    var limit = $('t_limit').value;
    var kw = $('t_keywords').value.trim();
    if (since) body.since = since;
    if (until) body.until = until;
    if (limit) body.limit = Number(limit);
    if (kw) body.keywords = kw;
    body.concurrency = Number($('t_concurrency').value || 3);

    notice('正在提交…');
    return fetch('/api/collect/tasks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(body),
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (b) {
        if (!r.ok) throw new Error(b.message || ('HTTP ' + r.status));
        return b;
      });
    }).then(function () {
      notice('已提交，任务在后台跑。采集时长取决于站点，可以随时回来看进度。');
      awaitingResult = true;   // 结果取回来之前，轮询别停
      return loadTasks();
    }).catch(function (e) {
      notice('提交失败：' + e.message);
    });
  }

  $('taskForm').addEventListener('submit', function (ev) {
    ev.preventDefault();
    if (!picked.size) { notice('先在上面勾选至少一个源。'); return; }
    var keys = Array.from(picked);
    notice('正在提交 ' + keys.length + ' 个源的采集任务…');
    $('startBtn').disabled = true;
    startTask(keys).then(function () { syncPickedCount(); });
  });

  /* ── ③ 任务队列 ─────────────────────────────────────────── */

  /* 提交出去的任务，结果还没取回来。

     不刷新会怎样：用户点完「开始采集」，任务卡写着「入库 8」，而下方的原始
     数据表还是空的——以为白跑了。这是第三层验证（headless 真点击）才抓得到
     的那类问题：接口全对、数据也真进了库，只是**没人去取**。

     为什么不用「活动任务清零」当判据：任务可能在一次轮询间隔内就跑完
     （假采集器下就是这样），从头到尾没出现过 running，那个事件根本不会发生。

     为什么不用「任务快照变了」：轮询到没有活动任务就停了（见 schedulePoll），
     快照要变也得有第二次 loadTasks 才看得见——恰恰没有第二次。所以得让
     「等结果」这件事本身去续命：只要这个标志还在，轮询就不停，直到某一拍
     看见任务已经不是活动状态，才去把结果取回来。 */
  var awaitingResult = false;

  function loadTasks() {
    return fetchJson('/api/collect/tasks?limit=30').then(function (body) {
      tasks = body.data || [];
      renderTasks();
      if (awaitingResult && tasks.length && !hasActive()) {
        awaitingResult = false;
        loadFunnel();
        loadSources();
        loadRaw(rawPage);
      }
      schedulePoll();
    }).catch(function (e) {
      $('taskCount').textContent = '加载失败：' + e.message;
    });
  }

  function hasActive() {
    return tasks.some(function (t) {
      return t.status === 'running' || t.status === 'pending'
          || t.status === 'cancelling';
    });
  }

  /* 有活动任务、或者还在等一次结果回填时轮询；用 setTimeout 递归而不是
     setInterval——后者在网络慢的时候会把请求堆起来，越堆越多。

     awaitingResult 是必需的：没有它，任务快到第一拍就已完成的场合（假采集器、
     或者真源碰巧很快），轮询在第一拍就退出，原始数据区永远等不到刷新。 */
  function schedulePoll() {
    if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
    if (!hasActive() && !awaitingResult) return;
    pollTimer = setTimeout(function () { loadTasks(); }, POLL_MS);
  }

  function renderTasks() {
    var box = clear($('taskList'));
    $('taskCount').textContent = tasks.length
      ? tasks.length + ' 条（最近 30 条）' : '';

    if (!tasks.length) {
      box.appendChild(el('p', { cls: 'empty', text: '还没有采集任务。' }));
      return;
    }
    tasks.forEach(function (t) { box.appendChild(taskCard(t)); });
  }

  function taskCard(t) {
    var card = el('div', { cls: 'task-card is-' + t.status });

    var top = el('div', { cls: 'task-top' }, [
      el('span', { cls: 'mono task-id', text: t.task_id }),
      badge(TASK_LABEL[t.status] || t.status, 'is-' + t.status),
      el('span', { cls: 'task-scope', text: describeScope(t) }),
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

    // 进度条。分母是「选了几个源」而不是「有多少条事故」——适配器不报
    // 总数，采完才知道有多少条，按条数画进度条只能是假的。
    var pct = t.progress_total
      ? Math.round(t.progress_done / t.progress_total * 100) : 0;
    card.appendChild(el('div', { cls: 'task-progress' }, [
      el('div', { cls: 'task-progress-fill',
                  attrs: { style: 'width:' + pct + '%' } }),
    ]));

    var totals = t.totals || {};
    card.appendChild(el('div', { cls: 'task-totals' }, [
      el('span', { text: '源 ' + t.progress_done + '/' + t.progress_total }),
      el('span', { text: '收到 ' + num(totals.collected) }),
      el('span', { text: '入库 ' + num(totals.inserted) }),
      el('span', { text: '重复 ' + num(totals.dup) }),
      el('span', { text: '筛选掉 ' + num(totals.filtered) }),
    ]));

    var list = el('ul', { cls: 'task-sources' });
    t.source_keys.forEach(function (k) {
      var p = (t.progress || {})[k] || {};
      var name = displayName(k);
      var line = el('li', {});
      line.appendChild(el('span', { cls: 'ts-name', text: name }));
      line.appendChild(srcStateBadge(p));
      if (p.collected != null) {
        line.appendChild(el('span', { cls: 'ts-num',
          text: '收 ' + p.collected + ' · 入库 ' + (p.inserted || 0)
              + (p.dup ? ' · 重复 ' + p.dup : '')
              + (p.filtered ? ' · 筛选 ' + p.filtered : '') }));
      }
      if (p.ms) line.appendChild(el('span', { cls: 'ts-ms',
        text: (p.ms / 1000).toFixed(1) + 's' }));
      if (p.error) line.appendChild(el('span', { cls: 'ts-err',
        text: p.error, attrs: { title: p.error } }));
      list.appendChild(line);
    });
    card.appendChild(list);

    if (t.error) {
      card.appendChild(el('p', { cls: 'task-error', text: t.error }));
    }
    return card;
  }

  function srcStateBadge(p) {
    if (p.state === 'done') return badge('完成', 'is-ok');
    if (p.state === 'failed') return badge('失败', 'is-sev');
    if (p.state === 'running') return badge('进行中', '');
    if (p.state === 'skipped') return badge('未跑', 'is-muted');
    return badge('排队', 'is-muted');
  }

  function describeScope(t) {
    var keys = t.source_keys || [];
    if (!keys.length) return '（无源）';
    if (keys.length === 1) return displayName(keys[0]);
    return displayName(keys[0]) + ' 等 ' + keys.length + ' 个源';
  }

  function displayName(key) {
    var hit = sources.filter(function (s) { return s.source_key === key; })[0];
    return hit ? hit.display_name : key;
  }

  function cancelTask(t, btn) {
    btn.disabled = true;
    fetch('/api/collect/tasks/' + t.task_id + '/cancel', { method: 'POST' })
      .then(function (r) { return r.json().then(function (b) {
        return { ok: r.ok, body: b }; }); })
      .then(function (res) {
        if (!res.ok) { notice(res.body.message || '取消失败'); btn.disabled = false; }
        else { notice('已请求取消。' + (res.body.note || '')); }
        return loadTasks();
      })
      .catch(function (e) { notice('取消失败：' + e.message); btn.disabled = false; });
  }

  function deleteTask(t, btn) {
    btn.disabled = true;
    fetch('/api/collect/tasks/' + t.task_id, { method: 'DELETE' })
      .then(function (r) { return r.json().then(function (b) {
        return { ok: r.ok, body: b }; }); })
      .then(function (res) {
        if (!res.ok) { notice(res.body.message || '删除失败'); btn.disabled = false; }
        return loadTasks();
      })
      .catch(function (e) { notice('删除失败：' + e.message); btn.disabled = false; });
  }

  $('refreshTasks').addEventListener('click', function () { loadTasks(); });

  /* ── ④ 漏斗 ─────────────────────────────────────────────── */

  function loadFunnel() {
    return fetchJson('/api/collect/stats').then(function (body) {
      var d = (body.data) || {};
      var area = clear($('funnelArea'));
      $('funnelTotal').textContent = d.total ? '共 ' + num(d.total) + ' 条' : '';

      if (!body.available) {
        area.appendChild(el('p', { cls: 'empty', text: '工作库未建立。' }));
        return;
      }
      if (!d.total) {
        area.appendChild(el('p', { cls: 'empty',
          text: '还没有采集到的原始数据。' }));
      } else {
        var max = Math.max.apply(null, d.funnel.map(function (f) { return f.count; })) || 1;
        var bars = el('div', { cls: 'bars' });
        d.funnel.forEach(function (f) {
          bars.appendChild(el('div', { cls: 'bar-row' }, [
            el('span', { cls: 'bar-name', text: f.label }),
            el('span', { cls: 'bar-track' }, [
              el('span', { cls: 'bar-fill',
                attrs: { style: 'width:' + (f.count / max * 100) + '%' } }),
            ]),
            el('span', { cls: 'bar-count', text: num(f.count) }),
          ]));
        });
        area.appendChild(bars);
      }

      var note = $('funnelNote');
      clear(note);
      note.appendChild(el('span', {
        text: '「人工确认」只统计人工裁决过的，「闸门通过」不含人工。' }));
      (d.stages_missing || []).forEach(function (m) {
        note.appendChild(el('span', {
          cls: 'muted',
          text: ' 缺「' + m.label + '」一段：' + m.reason + '。' }));
      });
    }).catch(function (e) {
      $('funnelNote').textContent = '漏斗加载失败：' + e.message;
    });
  }

  /* ── ⑤ 原始数据 ─────────────────────────────────────────── */

  // 表单里的 id 前缀 f_ 是给同一个页面上的两个表单区分的（新建任务表单用
  // t_ 前缀），跟接口参数名无关，所以得显式映射一遍——不能靠去前缀。
  var RAW_PARAM = {
    f_source: 'source_key',
    f_status: 'status',
    f_verdict: 'gate_verdict',   // 「未判定」传 __none__，后端认这个哨兵
    f_q: 'q',
  };

  function rawQuery(page) {
    var p = new URLSearchParams();
    Object.keys(RAW_PARAM).forEach(function (id) {
      var v = $(id).value.trim();
      if (v) p.set(RAW_PARAM[id], v);
    });
    p.set('size', $('f_size').value);
    p.set('page', page || rawPage);
    return p.toString();
  }

  function loadRaw(page) {
    rawPage = page || 1;
    return fetchJson('/api/collect/raw?' + rawQuery(rawPage))
      .then(function (body) {
        rawRows = body.data || [];
        rawMeta = body.meta || rawMeta;
        renderRaw(body);
      })
      .catch(function (e) {
        $('rawCount').textContent = '加载失败：' + e.message;
      });
  }

  function renderRaw(body) {
    var tbody = clear($('rawBody'));
    var rows = body.data || [];

    if (!body.available) {
      $('rawCount').textContent = '工作库未建立';
      tbody.appendChild(emptyRow('工作库未建立，先跑 create_staging_db.py。'));
      clear($('rawPager'));
      return;
    }
    $('rawCount').textContent = rawMeta.total
      ? '命中 ' + num(rawMeta.total) + ' 条' : '无命中';

    if (!rows.length) {
      tbody.appendChild(emptyRow('没有符合条件的原始记录。'));
      clear($('rawPager'));
      return;
    }

    rows.forEach(function (r) { tbody.appendChild(rawRow(r)); });
    renderPager();
    renderBulkBar();
  }

  function emptyRow(text) {
    var tr = el('tr', {});
    tr.appendChild(el('td', { cls: 'empty-cell', text: text,
                              attrs: { colspan: '9' } }));
    return tr;
  }

  function rawRow(r) {
    var tr = el('tr', {});

    var cb = el('input', { attrs: { type: 'checkbox' } });
    cb.checked = checked.has(r.raw_id);
    cb.addEventListener('change', function () {
      if (cb.checked) checked.add(r.raw_id); else checked.delete(r.raw_id);
      renderBulkBar();
    });
    tr.appendChild(el('td', { cls: 'col-pick' }, [cb]));

    var title = el('td', { cls: 'col-title' });
    if (r.title) {
      title.appendChild(el('span', { text: r.title,
                                     attrs: { title: r.title } }));
    } else {
      // 标题抓不到很常见，用正文前 40 字顶上，并标出来这不是标题
      title.appendChild(blank('（无标题）', '原页面未给出标题或提取失败'));
      if (r.content_preview) {
        title.appendChild(el('span', { cls: 'muted preview',
          text: ' ' + String(r.content_preview).slice(0, 40) + '…' }));
      }
    }
    tr.appendChild(title);

    tr.appendChild(el('td', { cls: 'col-src-small',
                              text: displayName(r.source_key) }));

    var pub = el('td', { cls: 'col-date' });
    if (r.publish_date) pub.appendChild(el('span', { text: fmtDay(r.publish_date) }));
    else pub.appendChild(blank('—', '发布日期未能从正文提取，常见于 PDF 正文'));
    tr.appendChild(pub);

    tr.appendChild(el('td', { cls: 'col-time', text: fmtTime(r.fetch_time) || '—' }));
    tr.appendChild(el('td', {}, [statusBadge(r.status)]));
    tr.appendChild(el('td', {}, [verdictBadge(r.gate_verdict, r.gate_stage)]));

    var reason = el('td', { cls: 'col-reason' });
    if (r.gate_reason) {
      reason.appendChild(el('span', { text: r.gate_reason,
                                      attrs: { title: r.gate_reason } }));
    } else {
      reason.appendChild(blank());
    }
    tr.appendChild(reason);

    var view = el('button', { cls: 'btn btn-sm', text: '查看',
                              attrs: { type: 'button' } });
    view.addEventListener('click', function () { openDrawer(r.raw_id); });
    var act = el('td', { cls: 'col-act' }, [view]);
    if (r.has_assets) {
      act.insertBefore(el('span', { cls: 'asset-dot', text: '附件',
                                    attrs: { title: '这条记录登记了 PDF / 附件' } }),
                       view);
    }
    tr.appendChild(act);
    return tr;
  }

  function renderPager() {
    var box = clear($('rawPager'));
    var m = rawMeta;
    if (m.pages <= 1) return;

    function nav(label, page, disabled) {
      var b = el('button', { cls: 'btn btn-sm', text: label,
                             attrs: { type: 'button' } });
      b.disabled = !!disabled;
      b.addEventListener('click', function () { loadRaw(page); });
      return b;
    }
    box.appendChild(nav('‹ 上一页', m.page - 1, m.page <= 1));
    box.appendChild(el('span', { cls: 'info',
      text: '第 ' + m.page + ' / ' + m.pages + ' 页' }));
    box.appendChild(nav('下一页 ›', m.page + 1, m.page >= m.pages));
  }

  $('rawFilters').addEventListener('submit', function (ev) {
    ev.preventDefault();
    checked.clear();
    loadRaw(1);
  });
  $('rawReset').addEventListener('click', function () {
    ['f_source', 'f_status', 'f_verdict', 'f_q'].forEach(function (id) {
      $(id).value = '';
    });
    $('f_size').value = '50';
    checked.clear();
    loadRaw(1);
  });

  function fillSourceFilter() {
    var sel = $('f_source');
    while (sel.options.length > 1) sel.remove(1);
    sources.forEach(function (s) {
      sel.appendChild(el('option', { text: s.display_name,
                                     attrs: { value: s.source_key } }));
    });
  }

  /* ── ⑥ 批量裁决 ─────────────────────────────────────────── */

  function renderBulkBar() {
    // 只保留当前页还存在的选择：翻页后上一页的勾选不该继续参与裁决
    var visible = new Set(rawRows.map(function (r) { return r.raw_id; }));
    checked.forEach(function (id) { if (!visible.has(id)) checked.delete(id); });

    var bar = $('bulkBar');
    var n = checked.size;
    bar.hidden = n === 0;
    $('bulkCount').textContent = '已选 ' + n + ' 条';
  }

  Array.prototype.forEach.call(
    document.querySelectorAll('#bulkBar [data-verdict]'),
    function (btn) {
      btn.addEventListener('click', function () { gate(btn.dataset.verdict, btn); });
    });

  $('bulkClear').addEventListener('click', function () {
    checked.clear();
    loadRaw(rawPage);
  });

  var VERDICT_NOTE = {
    confirmed: '标为相关',
    rejected: '标为不相关',
    review: '退回待判',
  };

  function gate(verdict, btn) {
    var ids = Array.from(checked);
    if (!ids.length) return;
    var note = null;
    if (verdict !== 'review') {
      // 裁决理由是给后来的人看的：为什么这条算相关。允许留空但不默认填
      note = window.prompt(VERDICT_NOTE[verdict] + '（' + ids.length
        + ' 条）的理由，可留空：', '');
      if (note === null) return;      // 点了取消
      note = note.trim() || null;
    }

    btn.disabled = true;
    fetch('/api/collect/raw/gate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ raw_ids: ids, verdict: verdict, note: note }),
    }).then(function (r) { return r.json().then(function (b) {
      return { ok: r.ok, body: b }; }); })
      .then(function (res) {
        btn.disabled = false;
        if (!res.ok) { notice(res.body.message || '裁决失败'); return; }
        notice('已更新 ' + res.body.data.updated + ' 条'
               + (res.body.data.missing ? '（' + res.body.data.missing
                  + ' 条不存在）' : ''));
        checked.clear();
        loadRaw(rawPage);
        loadFunnel();
        loadSources();
      })
      .catch(function (e) { btn.disabled = false; notice('裁决失败：' + e.message); });
  }

  /* ── 抽屉：单条详情 ─────────────────────────────────────── */

  var drawerOpenId = null;

  function openDrawer(rawId) {
    fetchJson('/api/collect/raw/' + encodeURIComponent(rawId))
      .then(function (body) { renderDrawer(body.data); })
      .catch(function (e) {
        clear($('drawerBody')).appendChild(el('p', { cls: 'empty',
                                                    text: '加载失败：' + e.message }));
      });
    $('drawer').hidden = false;
    drawerOpenId = rawId;
  }

  function closeDrawer() {
    $('drawer').hidden = true;
    drawerOpenId = null;
  }

  $('drawerClose').addEventListener('click', closeDrawer);
  document.addEventListener('keydown', function (ev) {
    if (ev.key === 'Escape' && drawerOpenId) closeDrawer();
  });

  function field(label, value, hint) {
    var v = el('div', { cls: 'kv-val' });
    if (value == null || value === '') v.appendChild(blank('—', hint));
    else v.appendChild(el('span', { text: String(value) }));
    return el('div', { cls: 'kv' }, [
      el('div', { cls: 'kv-key', text: label }), v,
    ]);
  }

  function renderDrawer(d) {
    var body = clear($('drawerBody'));

    body.appendChild(el('div', { cls: 'drawer-title',
                                 text: d.title || '（无标题）' }));

    var grid = el('div', { cls: 'kv-grid' });
    grid.appendChild(field('来源', d.source ? d.source.name : d.source_key));
    grid.appendChild(field('状态', STATUS_LABEL[d.status] || d.status));
    grid.appendChild(field('发布日', fmtDay(d.publish_date),
                           '发布日期未能从正文提取，常见于 PDF 正文'));
    grid.appendChild(field('采集时间', fmtTime(d.fetch_time)));
    grid.appendChild(field('闸门判定',
      d.gate_verdict ? (VERDICT_LABEL[d.gate_verdict] || d.gate_verdict) : null,
      '还没有跑自动闸门，也没有人工裁决'));
    grid.appendChild(field('判定来源', d.gate_stage));
    grid.appendChild(field('判定置信', d.gate_confidence));
    grid.appendChild(field('规则提取日期', d.ext_date));
    grid.appendChild(field('规则提取省/市', [d.ext_province, d.ext_city]
      .filter(Boolean).join(' / ') || null));
    grid.appendChild(field('规则提取伤亡',
      (d.ext_deaths || d.ext_injuries)
        ? ('死亡 ' + (d.ext_deaths || 0) + ' · 受伤 ' + (d.ext_injuries || 0))
        : null));
    grid.appendChild(field('规则提取类型', d.ext_accident_type));
    grid.appendChild(field('综合置信度', d.confidence_overall));
    body.appendChild(grid);

    var urlRow = el('p', { cls: 'drawer-src' });
    if (d.source_url) {
      urlRow.appendChild(el('a', {
        text: d.source_url, cls: 'ext mono',
        attrs: { href: d.source_url, target: '_blank',
                 rel: 'noopener noreferrer' },
      }));
    } else {
      urlRow.appendChild(el('span', { text: '（无来源 URL）' }));
    }
    body.appendChild(urlRow);

    if (d.gate_reason) {
      body.appendChild(el('div', { cls: 'drawer-block' }, [
        el('h4', { text: '判定理由' }),
        el('p', { text: d.gate_reason }),
      ]));
    }

    var textBlock = el('div', { cls: 'drawer-block' }, [
      el('h4', { text: '正文' }),
    ]);
    if (d.content) {
      textBlock.appendChild(el('div', { cls: 'raw-text', text: d.content }));
    } else {
      textBlock.appendChild(el('p', { cls: 'empty',
        text: '正文为空——适配器抓到了页面，但没能从中提正文。' }));
    }
    body.appendChild(textBlock);

    // 原始 HTML 用 <pre> + textContent。这是抓回来的源码，里面出现
    // <script> 属于正常数据，绝不能当 HTML 解析。
    if (d.raw_html) {
      var det = el('details', { cls: 'drawer-block' }, [
        el('summary', { text: '原始 HTML（' + num(d.raw_html.length) + ' 字符）' }),
        el('pre', { cls: 'raw-html', text: d.raw_html }),
      ]);
      body.appendChild(det);
    }

    if (d.assets && d.assets.length) {
      var list = el('ul', { cls: 'asset-list' });
      d.assets.forEach(function (a) {
        var li = el('li', {});
        li.appendChild(el('span', { cls: 'asset-type',
          text: ASSET_TYPE_LABEL[a.asset_type] || a.asset_type }));
        if (a.url) {
          li.appendChild(el('a', { text: a.url, cls: 'ext mono',
            attrs: { href: a.url, target: '_blank',
                     rel: 'noopener noreferrer' } }));
        }
        if (a.ocr_status) {
          li.appendChild(el('span', { cls: 'muted',
            text: 'OCR ' + (OCR_LABEL[a.ocr_status] || a.ocr_status) }));
        }
        list.appendChild(li);
      });
      body.appendChild(el('div', { cls: 'drawer-block' }, [
        el('h4', { text: '附件（' + d.assets.length + '）' }), list,
      ]));
    }

    if (d.extra && Object.keys(d.extra).length) {
      var pre = el('pre', { cls: 'raw-html',
                            text: JSON.stringify(d.extra, null, 2) });
      body.appendChild(el('details', { cls: 'drawer-block' }, [
        el('summary', { text: '采集器额外字段' }), pre,
      ]));
    }
  }

  /* ── 启动 ───────────────────────────────────────────────── */

  loadSources().then(function () {
    fillSourceFilter();
  });
  loadTasks();
  loadFunnel();
  loadRaw(1);

  // 采集可能跑很久，回到标签页时刷新一下，免得看到的是十几分钟前的进度
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) { loadTasks(); }
  });
})();
