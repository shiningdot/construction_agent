/* 顶栏高度的唯一真值来源（WCAG 2.4.11 与吸顶表头的停靠线都靠它）。
 *
 * 页面顶部有两个吸顶元素：`.topbar`（`sticky; top: 0`）与 `.grid thead th`
 * （`sticky; top: var(--topbar-h)`）。后者的停靠线应当就是前者的高度，而
 * **顶栏高度是响应式的**——实测 Chromium：1440px 宽时 55px；900px 宽时品牌
 * 文字与导航折行，变成 78px；更窄的断点又会变。CSS 里写死的 55px 在窄屏
 * 就失效，两处一起坏：
 *
 *   1. 表头停到 55px，而顶栏占着 0–78px ⇒ 表头被顶栏压住 23px；
 *   2. `--scroll-pad`（= 顶栏 + 表头）算成 94px 而实际需要 117px ⇒ 键盘
 *      用户 Tab 到的元素停在 94px，仍落在顶栏之下（WCAG 2.4.11 违规）。
 *
 * 与其在 CSS 里猜一个数，不如量出来写回自定义属性：`--scroll-pad` 是
 * `calc(var(--topbar-h) + var(--thead-h))`，变量一改全站自动重算。
 *
 * CSS 里那个 `--topbar-h: 55px` 因此只是**首帧初值**——脚本禁用时回落到它，
 * 此时表头略错位但不影响可读性。`--thead-h` 不在这里同步：表头高度由 padding
 * 与字号决定（39px），不随视口宽度变，且数据未加载时 `.grid thead th` 还不
 * 存在，读它会得到 0 反而把变量写坏。
 *
 * 用 `ResizeObserver` 而不是只听 `resize` 事件：顶栏折行是**布局**结果，字号
 * 设置、系统字体回退、浏览器缩放都可能让它变高而不触发窗口 resize。
 */

(function (global) {
  'use strict';

  var root = document.documentElement;
  var bar = document.querySelector('.topbar');
  if (!root || !bar) return;

  function sync() {
    var h = Math.round(bar.getBoundingClientRect().height);
    // 隐藏或尚未布局时高度为 0，写进去会让表头贴到页顶，故只在有效时写
    if (h > 0) root.style.setProperty('--topbar-h', h + 'px');
  }

  if (typeof global.ResizeObserver === 'function') {
    new global.ResizeObserver(sync).observe(bar);
  }
  global.addEventListener('resize', sync);
  global.addEventListener('orientationchange', sync);
  sync();
})(window);
