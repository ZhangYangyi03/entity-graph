# 通用网页扩面（2026-11-09）

## 为什么要做

entity-graph 之前只有针对结构化 JSON/API 的专用适配器：声明了 54 个域名，
`.cache` 里实际只命中 18 个，图里主力来源只有 9 个（CelesTrak、FDIC、
SIX、World Bank、RBA、ECB、东财、巨潮、SEC）。新闻、论坛、预印本、
政府开放数据、招聘、电商、社交这些类别一条都没有。

autoforge 在 2026-11-09 集成了 crawler 三引擎（fast / stealth / render）
之后，抓取能力面远大于已清洗的数据面。缺的不是引擎，是把任意 HTML
落成带溯源节点的入口。`entitygraph/web.py` 就是那个入口。

## 新增

- `entitygraph/web.py`：`crawl()` 结果 -> `webpage` 节点 + `website` 节点 +
  `links_to` 边。优先复用 `autoforge.crawler`；没有 autoforge 时退化为
  stdlib 并发抓取，纯安装 entitygraph 也能用。
- `entitygraph.cli web`：`python -m entitygraph.cli web URL... --file 清单
  --mode fast|stealth|render --out web.graph.json --merge 旧图`。
- `tests/test_web.py`：9 项离线测试，覆盖溯源、失败显式化、反爬壳识别、
  相对链接、URL 归一化、CLI 落盘。
- `web_sources.txt`：25 个清单外站点，按类别分组。
- `web.graph.json`：两轮实测合并产物。

## 溯源规则（沿用本仓库的不成文宪法）

每个节点和边都带 `source` / `url` / `fetched_at`；`Graph.add_edge` 对无
source 的边直接抛 `ValueError`。因此抓取失败不静默丢弃，而是进
`caveats`，并区分三种原因：

    http status  非 2xx（401/403/404）
    fetch error  TLS、超时、协议错误
    blocked      2xx 但其实是机器人检查页或空壳

`blocked` 很重要：Amazon 返回 202/2007 字节的空壳，若按 2xx 入库就是
把反爬占位页洗成数据。同理 `title` 含 "Just a moment" 的 Cloudflare 页。

## 实测结果

    fast     14/25 入库   17.5s   1.4 req/s   89 个外链站点
    stealth  17/25 入库   16.9s   1.5 req/s   救回 crates.io / esa.int / who.int
    合并     108 节点 / 95 边：17 webpage + 91 website

新覆盖的 17 个页面域名（此前清单里一个都没有）：

    arxiv.org, bbc.com, catalog.data.gov, crates.io, data.europa.eu,
    data.gov, data.gov.uk, data.worldbank.org, en.wikipedia.org, esa.int,
    github.com, nasa.gov, news.ycombinator.com, pypi.org, reddit.com,
    who.int, zhihu.com

通过链接展开还带出 91 个 `website` 节点，是下一轮可继续抓的种子，
不需要人手写适配器。

## 仍然攻不下来的 8 个（明示，不假装覆盖）

    reuters.com       401
    apnews.com        403
    stackoverflow.com 403
    nature.com        curl (28) 超时
    paperswithcode.com curl (28) 超时
    indeed.com        403
    amazon.com        202 反爬空壳
    imf.org           403

这些不是引擎缺陷：换任何开源栈，出口 IP 信誉、登录态、验证码、
Cloudflare/WAF 风控都一样挡。三条路可走且都不靠"更强的爬虫"：
一是换成这些站点的官方 API / 开放数据集（Reuters 无免费档，
WHO/World Bank/IMF 都有结构化 API）；二是用本机已登录的浏览器会话
（browser_* 工具，需要操作者本人登录）；三是换合法镜像/聚合源。

## 复现

    cd entity-graph
    python -m entitygraph.cli web --file web_sources.txt --mode fast \
        --out web_fast.graph.json
    python -m entitygraph.cli web --file web_sources.txt --mode stealth \
        --out web.graph.json --merge web_fast.graph.json
    python -m pytest tests -q        # 158 passed
