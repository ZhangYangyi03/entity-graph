
## cnmarket.py — 行情与情报数据接入（2026-11-04 实测）

目的：把"逐笔/盘口/龙虎榜/大宗/两融/分析师预期/公告PDF/ESG/另类数据"这些华尔街级别
的数据面，落到 entity-graph 的节点-边上，每条边带 source + url + fetched_at。

产物：`cn_market.graph.json`（20 只样本股跑一遍：1463 节点 / 2268 边 / 1.46 MB）
代码：`entitygraph/cnmarket.py`，`python -c "from entitygraph import cnmarket"` 可用。

### 能拿到（本机实测 OK）
| 数据 | 接口 | 实测 |
|---|---|---|
| 逐笔成交（当日） | 腾讯 gtimg `appn=detail&action=data&c=sh600000&p=N` | 现在每只 450~520 笔；约 45 分钟滚动缓冲 |
| 盘口 | 东财 push2 `qt/stock/get` | 价格/开高低/量额 OK；**免费档五档 10:20 后给 0** |
| 主力资金流 | 东财 push2 `f62/f184` | OK，200 只/次 |
| 龙虎榜 | 东财 `RPT_DAILYBILLBOARD_DETAILSNEW` | OK，含买卖席位名（BUY_SEAT/SELL_SEAT） |
| 大宗交易 | 东财 `RPT_DATA_BLOCKTRADE` | OK，含买卖方营业部 |
| 两融余额 | 东财 `RPTA_WEB_RZRQ_GGMX` | OK，融资余额/融券余量/融券余额 |
| 分析师一致预期 | 东财 `RPT_WEB_RESPREDICT` | OK，评级家数 + 三年 EPS |
| 券商研报 | 东财 `reportapi` | OK，标题/机构/评级 |
| 公告列表 | 东财 `np-anotice-stock` | OK |
| 公告原文 PDF | 巨潮 `hisAnnouncement/query`（POST） | OK，下载验证 `%PDF-1.7` |
| 快照（市值/行业/PE/PB） | 东财 `qt/clist/get` | OK，100~200 只/次 |

### 拿不到，以及为什么（不粉饰）
- **历史逐笔**：A 股免费源没有。新浪的 `CN_Transactions` 只剩 `detailPages` 元数据、无成交明细；腾讯只给当日滚动缓冲；
  巨潮/东财无逐笔档。要回测级 tick 只能买 Level-2 或拿交易所授权。
- **十档盘口 / 委托队列**：付费行情才能拿。免费档连五档都不稳定（实测 10:20 后买一卖一返回 0）。
- **ESG**：华证 smartesg `api/esgratings/*` 需要 token（401）；东财 datacenter 全部 `RPT_ESG_*` 返回"报表配置不存在"。
- **信用卡消费**：卡组织/银联数据不对个人开放，商业渠道要企业资质。
- **按公司的招聘数据**：BOSS 直聘城市接口公开，但按公司抓岗位需登录态。
- **卫星影像**：TLE 星历免费（satcat.py），Planet/Sentinel 影像需账号，本机到部分站点 TLS 握手失败。

### 两个踩过的坑（写下来免得下次再踩）
1. **巨潮 orgId 没有规律**：浦发 `gssh0600000`，宁德时代 `GD165627`，平安 `9900002221`，比亚迪 `gshk0001211`。
   按 `gssz0+代码` 拼出来的 id 对 300750/002594/601318/688981 全部返回 0 条，而且**不报错**，就是空列表。
   必须走 `topSearch/query` 按代码查出真实 orgId。空列表和"这家公司没公告"是两件事，这里差点写成后者的假话。
2. **`_em_dc` 的 filter 参数不要用 `>=` 做日期过滤**：`(TRADE_DATE>='2026-11-04')` 返回 0 行，
   东财按精确匹配处理。要取最近一期，只按 `TRADE_DATE` 倒序排，再在本地筛日期。

### 还没做的
- 这些数据目前只做进产物和事实，**没有喂回 quant 的信号**（M153 第 2 步）。
- 逐笔只能当日抓，要建成历史库得每天开着抓一次（一个定时任务的事，还没排）。
