"""详情页「内容图」的抽取——判据与实现，各源共用一份。

原先这段逻辑只写在 `sources/trs_cms_base.py` 里（第十八/十九轮）。`nmg_em`
与 `gd_em` 不是 TRS 系、拿不到那个基类，但它们同样有图片型报告——`nmg_em`
实测一个源就有 **121 张内容图**，是全项目最多的一块。所以把判据提出来共用，
而不是各源再抄一份。

（这个文件与 `pdf_text.py` 是同一种东西：一份被几个源共用、又差点被复制成
多份的工具。`pdf_text.py` 的 docstring 记着它当年被抄成三份、而另外三个模块
调它却没有定义的事。）
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

IMG_EXT = re.compile(r"\.(png|jpe?g|gif|bmp|webp|tiff?)$", re.I)


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
    """
    p, i = urlsplit(page_url), urlsplit(img_url)
    if p.netloc != i.netloc:
        return False
    if p.path.rsplit("/", 1)[0] == i.path.rsplit("/", 1)[0]:
        return True
    return i.path.startswith(p.path.rsplit(".", 1)[0] + "/")


def extract_page_images(page_url: str, soup: BeautifulSoup,
                        max_n: int = 50) -> list[str]:
    """从详情页 DOM 里抽出内容图 URL（去重、保序、按 `max_n` 截断）。

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
    """
    out: list[str] = []
    if max_n <= 0:
        return out
    for tag in soup.find_all(["img", "source"]):
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
