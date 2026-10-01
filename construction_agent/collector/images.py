"""详情页「内容图」的抽取——判据与实现，各源共用一份。

两层判据，缺一不可：

- **URL 层**（`is_page_image`）：「这张图属于这一页吗」——按目录/前缀/图名。
- **DOM 层**（`is_attachment_icon`）：「这张图在页面上是什么角色」——附件行里
  的图标也属于这一页，但它不是内容（第二十六轮，`sc_em` 逼出来的）。

原先这段逻辑只写在 `sources/trs_cms_base.py` 里（第十八/十九轮）。`nmg_em`
与 `gd_em` 不是 TRS 系、拿不到那个基类，但它们同样有图片型报告——`nmg_em`
实测一个源就有 **121 张内容图**，是全项目最多的一块；`gd_em` 有 152 张，
而且它的图**不在页面同目录下**，逼出了第三条判据（见 `is_page_image`）。
所以把判据提出来共用，而不是各源再抄一份。

（这个文件与 `pdf_text.py` 是同一种东西：一份被几个源共用、又差点被复制成
多份的工具。`pdf_text.py` 的 docstring 记着它当年被抄成三份、而另外三个模块
调它却没有定义的事。）
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, NavigableString

IMG_EXT = re.compile(r"\.(png|jpe?g|gif|bmp|webp|tiff?)$", re.I)

# 附件扩展名。用于 `is_attachment_icon`——**不是**用来决定下载什么，只是用来
# 认出「附件行」这个结构。所以白名单宽一点没关系：认错一个只影响那张图是否
# 被当图标，不会引发下载。
ATTACH_EXT = re.compile(
    r"\.(pdf|docx?|xlsx?|pptx?|zip|rar|7z|wps|et|ofd|csv|txt)$", re.I)


def page_article_id(page_url: str) -> str:
    """页面 URL 末段里的文章 ID。

        …/content/post_3554784.html   →  3554784
        …/202608/t20260821_7356773.html  →  7356773
        …/dw/1234.html                →  1234

    规则是「末段去扩展名，再取最后一个下划线之后」。取「之后」而不是直接
    用整段，是因为国内政府站的末段普遍带前缀（`post_`、`t20260821_`），
    而图片名只留 ID。没有下划线时这一步是恒等变换，无副作用。
    """
    last = urlsplit(page_url).path.rsplit("/", 1)[-1]
    return last.rsplit(".", 1)[0].rsplit("_", 1)[-1]


def is_page_image(page_url: str, img_url: str) -> bool:
    """这张图是不是**「这一页自己的图」**——即内容图而非站点模板图。

    判据是「图片 URL 与详情页 URL **同目录**」（或以页面路径为前缀）。看起来
    平平无奇，但它是**试错试出来的**，而且前一个判据是当场被自己的数据推翻的。

    ### 推翻的那个判据

    直觉上内容图 = **扫描页组**：一份报告转成一串连续编号的图。`sc_em` 实测
    `…/b3eebb40…/images/0001.jpg` … `0018.jpg`，正是这个形态。于是先立的是
    「同目录 + 编号/时间戳连续」。

    `tj_em` 立刻给了反例——它有 **10 张同目录、时间戳连续递增**的 TRS 图：

        /qdfy/tytb/202008/W020200824411649201603.png
        /qdfy/tytb/202008/W020200824411649313338.png
        /qdfy/tytb/202008/W020200824411649395384.png   …

    形态与 `sc_em` 的扫描页组一模一样。差别只在「它每一页都有」——而
    **「每一页都有」这个观察要求先看到多页，生产链路是单页采集**。所以那个判据
    不是不准，是在生产的输入上根本算不出来。

    （当时专门核过：`tj_em` 那四条「样本」确实是四个不同的页面，URL 与
    `<title>` 两两不同，是 4·15 / 10·24 / 6·10 / 6·25 四起不同事故，却各挂
    同一批 10 张图——模板图确证。）

    ### 换成的这一条

    把两边的 URL 摆在一起看，内容图与模板图是分开的：

        gz_em   页 /gk/zfxxgk/fdzdgk/dcbg_5888759/202608/t2026…html
                图 /gk/zfxxgk/fdzdgk/dcbg_5888759/202608/W020260828….png  同目录
        tj_em   页 /ZWGK6939/SGDCBG354/202608/t2026…html
                图 /qdfy/tytb/202008/W020200824….png                    不同目录

    **它单页可用**：只要详情页 URL 与图片 URL。

    ### 怎么验的

    用「跨页恒定」——一条来源完全不同的判据（要对比多页才能算）——去对这条
    （只看单页就能算）。两者一致，「同目录」就是这个形态的可靠代理。
    10 个源、289 个图 URL 上，**矛盾格（同目录 **且** 每页都有）为 0**；
    逐源看「同目录数 + 跨页恒定数」恰等于图 URL 总数。

    失败方向也是对的：这条判据**宁可漏，不会误登记**。普查里唯一的例外是一张
    「异目录且只在一页出现」的图——漏一张扫描页不等于整条记录丢失；反过来若把
    模板图当内容图登记，那才是给下游塞噪音。

    两种写法都认：

    - **同目录**：`…/202608/page.html` ↔ `…/202608/W020….png`（TRS 常态）
    - **以页面路径为前缀**：`…/b3eebb….html` ↔ `…/b3eebb…/images/0001.jpg`
      （`sc_em` 形态：正文目录就是文章 hash 本身）

    host 不同直接判否（图片放在 CDN 的站会全部漏判，但目前 10 个源没有一个
    这样——真遇到了，这个函数的注释就是查因的入口）。

    ### 第三条判据：图片名主干 == 页面 ID

    「同目录」有个前提——**图片和页面在同一台服务器、同一套目录结构下**。
    `gd_em` 不满足，而且不是边缘情况，是它的全部：

        页面  /gk/zdlyxxgk/sgdcbg/content/post_3554784.html
        图    /img/0/670/670670/3554784.jpg

    图片集中放在 `/img/0/{千位}/{千位细分}/` 下。2026-10-01 用它当前列表
    跑了一遍（54 个详情页）：源自己用 `class="nfw-cms-img"` 标出的内容图
    共 **152 张**，「同目录」命中 **0**——不是漏一两个，是一张都没认出来。

    但两者共享一个更强的东西：

        post_3554784.html  →  3554784.jpg
        post_3263123.html  →  3263123.jpg
        post_3257780.html  →  3257780.png

    **图片名主干就是页面 ID。** 同一次实测：新判据命中 **152 / 152，漏 0**。

    它不会把模板图收进来，而且这一点是**结构性的、不靠调参**：模板图的
    定义就是「跨页恒定」（每个页面都挂同一张），而页面 ID 每页不同，所以
    同一张模板图不可能同时等于多个页面的 ID。同一次实测里，跨页恒定的
    3 张模板图（`www.gd.gov.cn/defres/ipv.png` 等）共出现 162 个「页×图」
    格，新判据误纳 **0 格**。这个论证不依赖具体站点，对任何源都成立——

    **图片名那侧只做全等，不剥前缀**。页面那侧剥（`post_` 不是 ID 的
    一部分），图片这侧不剥：否则 `logo_3554784.png` 会剥出 `3554784`
    而被误纳成内容图。宁可漏一两张图名带后缀的（如 `3554784_ORIGIN.jpg`
    会漏），也不给下游塞模板图——这是这个模块一贯的方向。
    """
    p, i = urlsplit(page_url), urlsplit(img_url)
    if p.netloc != i.netloc:
        return False
    if p.path.rsplit("/", 1)[0] == i.path.rsplit("/", 1)[0]:
        return True
    if i.path.startswith(p.path.rsplit(".", 1)[0] + "/"):
        return True
    return i.path.rsplit("/", 1)[-1].rsplit(".", 1)[0] == page_article_id(page_url)


def is_attachment_icon(tag) -> bool:
    """这张图是不是**「附件行里的图标」**——不是内容，是装饰。

    形态（`sc_em` 实测，2026-10-01 第二十六轮）：

        <p>附件：<img border="0" src="…/<hex>/images/pdf.gif">
                <a href="…/<hex>/files/…报告.pdf" target="_blank">报告名称</a></p>

    为什么「同目录 / 以页面路径为前缀」那条判据接不住它：这个站的附件图标
    **就放在文章自己的资源目录里**——和它 24 张扫描页同一个 `images/` 目录。
    对判据来说两者没有任何区别。而这张图标会被一路带下去：`image_urls` →
    `raw_accident_assets` 一行 `asset_type='image'` → 阶段②的 OCR 队列。
    **图片通路的目的就是「让阶段②知道哪些图要 OCR」，塞个图标进去就是塞噪音。**

    ### 判据为什么用「紧邻」而不是「父容器内」

    先试过「图片的直接父元素里有附件链接」——**它会误伤**。`nmg_em` 的详情页
    把内容图直接放在 `<body>` 下（外面没有 `<p>` 包着），于是 `<body>` 成了它们的
    直接父元素；那一页只要有一行附件链接，全页的图就都被判成图标了。父容器越
    往上冒，越容易把「页面上有附件」错读成「这张图是附件图标」。

    收紧成两条**结构上贴在一起**的形态，任一命中即算：

        ① 图片的下一个**紧邻**节点是指向附件的 `<a>` —— 上面那个 `<p>` 就是这形态
        ② 图片被指向附件的 `<a>` 包着              —— `<a href="…pdf"><img …></a>`

    **只看下一个，不看上一个。** 左邻是附件链接、右邻是内容图的排布排除不掉，
    而实测形态里图标总是在链接**之前**（先图标后文字）。这是这个模块一贯的方向：
    宁可漏一张图标，不误伤一张内容图——漏一张图标只是多 OCR 一个 200 字节的
    GIF，误伤一张内容图是**丢掉一整页报告**。

    ### 「紧邻」不能用 `find_next_sibling()`

    这一条是**被既有测试当场抓住的**，不是想出来的。最初的写法是
    `tag.find_next_sibling()`——它只找 Tag、自动跳过文本节点，看起来正合适。
    跑全量测试时 `gd_em` 的两条挂了：

        <div class="article_con">
          <img class="nfw-cms-img" src="/img/0/1/2/1.jpg">   ← 内容图
          ……正文正文正文（几千字）……
          <a href="/attachment/0/1/2/3.pdf">附件：评估报告</a>
        </div>

    `find_next_sibling()` **跨过整段正文**找到了文件末尾那个附件链接，于是这张
    内容图被判成图标。教训是：在一个 `<div>` 里，「下一个标签是附件链接」和
    「紧挨着附件链接」完全是两回事。

    所以改成只看 `next_sibling`（下一个**任意**节点），且只允许中间隔**纯空白**
    文本——正因为 `sc_em` 的原文是两个标签之间**一个字都没有**：

        <img border="0" src="…/images/pdf.gif"><a href="…/files/…报告.pdf">

    留纯空白的中断，是因为站点换个格式写成 `<img …> <a …>`（一个空格）就会漏，
    而这是同一段排版，不该因为一个空格就换结论。

    ### 边界

    - 中间隔了 `<br>`、`<span>` 或**任何非空白文本**就认不出（结构上已不「紧邻」）
    - 图标与附件链接分别包在各自的 `<span>` 里也认不出
    - **实测只有 `sc_em` 命中**：8 个 TRS 源 × 3 个详情页里，判据放行的 14 张图
      中，只有它那 1 张（`pdf.gif`，同页出现两次）符合；`sh_em` / `sx_em` 各有
      附件链接却 0 张放行图，`gz_em` 9 张、`hain_em` 3 张放行图全部不命中。
    """
    nxt = tag.next_sibling
    while isinstance(nxt, NavigableString) and not nxt.strip():
        nxt = nxt.next_sibling
    if nxt is not None and getattr(nxt, "name", None) == "a" and \
            ATTACH_EXT.search(urlsplit(nxt.get("href") or "").path):
        return True
    par = tag.parent
    if par is not None and par.name == "a" and \
            ATTACH_EXT.search(urlsplit(par.get("href") or "").path):
        return True
    return False


def extract_page_images(page_url: str, soup: BeautifulSoup,
                        max_n: int = 200) -> list[str]:
    """从详情页 DOM 里抽出内容图 URL（去重、保序、按 `max_n` 截断）。

    **`max_n` 的默认值是 200 而不是 50**（2026-10-01 第二十一轮改）。原先是
    50，写的时候以为「一条详情页挂不了多少图」；端到端跑 `gd_em` 才发现
    有一条记录**正好落了 50 张**——正好等于上限，那不是「刚好 50 张」，是
    被截断的。单独抓那一页数了：**59 张**，截掉 9 张（15%）。

    那一条还是**纯图片型**（正文 0 字，一条扫描件报告转成的图组），缺页就是
    缺内容。上限的语义写在下面「只登记 URL、不下载」那段里——它防的是
    **病态页面**（某页挂了整站图片墙），而 50 对扫描件报告来说是**常态
    截断**，把防线划在了正常数据里。200 仍能挡住病态页面：一份报告 200 页
    已经极端，而 URL 只是字符串，200 个约 12 KB。

    **只登记 URL，不下载**。与 PDF 那边刻意不同：PDF 必须下载才能提出正文
    （正文就在里面），而图片的正文要 OCR，那是阶段②的事；采集层的职责只是
    **让阶段②知道哪些图要 OCR**。下载反而有害——`nmg_em` 一个源实测 121 张
    内容图，全下就是把采集任务变成图片搬运工。

    `<source>` 与 `<img>` 一起收：`srcset` 响应式图常写成 `<source src="…">`。

    **`src` 与 `data-src` 是「先看 `src`，看不上再看 `data-src`」，不是「取第一个
    非空的」**。懒加载页面里 `src` 常常**非空但无用**——一个内联 base64 占位符
    （`data:image/gif;base64,R0lGOD…`），真实地址在 `data-src`。取第一个非空的
    写法会拿到那个占位符，然后顺手把它跳过——**结果是整页的图一张都不剩，
    而且看起来跟「这页确实没图」一模一样**。改成逐个候选试，第一个**合格**
    的才要；一个标签最多产出一个 URL。

    `data:` 开头的内联 base64 跳过（不是 URL，登记了也没法取）。

    **附件行里的图标跳过**（`is_attachment_icon`，第二十六轮）。判据是结构和
    位置的，不看文件名——所以站点换成 `doc.gif` 或干脆换个名字也照样认得。
    放在 `is_page_image` **之前**判：图标连判据都不该进，省得它去挤 `max_n`。
    """
    out: list[str] = []
    if max_n <= 0:
        return out
    for tag in soup.find_all(["img", "source"]):
        if is_attachment_icon(tag):
            continue
        for attr in ("src", "data-src"):
            src = (tag.get(attr) or "").strip()
            if not src or src.startswith("data:"):
                continue
            full = urljoin(page_url, src).split("#")[0].split("?")[0]
            if not IMG_EXT.search(full) or not is_page_image(page_url, full):
                continue
            if full not in out:
                out.append(full)
            break                      # 这个标签已经产出过，不再看 data-src
    return out[:max_n]
