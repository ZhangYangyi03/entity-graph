# 企业图：Fortune 名录 + 两家中国实体

这一层把「公司」建成节点，把「谁持有谁」建成边。所有节点和边都带 source 和 url，
没有一条是模型编的；手工录入的两条明确标注为未验证。

## 数据从哪来

   来源                 拿到什么                         覆盖
   Wikipedia 排名页      排名、国别、行业、营收           43 家（见下方 caveat）
   GLEIF                LEI、法定名、注册地址、状态      35/45 家
   SEC EDGAR            CIK、ticker、13D/13G 持股人     17 家
   manual               南大通用、协医                  2 家，明确标注未验证

运行：

    python build_companies.py            # 全量，约 30 分钟（SEC 逐份下载）
    python build_companies.py --offset N --limit 4 --shard   # 分段跑，写 shards/
    python merge_shards.py               # 合并分片成 companies.graph.json
    python verify_companies.py           # 检查图还守不守承诺

## 结果（本次实测）

   节点 72   边 19
   公司 62（其中 GLEIF 法定实体 35、SEC 申报主体 17、手工 2）
   持股人边 19 条，全部来自 SEC 13D/13G 封面页

几条真边：

   Jeffrey P. Bezos            -> Amazon              10.1%
   STATE STREET CORPORATION    -> Chevron              8.7%
   Warren E. Buffett           -> Chevron              6.7%
   STATE STREET CORPORATION    -> Ford Motor Company  8.38%
   Berkshire Hathaway          -> General Motors       4.1%

## 已知没做到的，写进图里而不是藏在 README

`companies.graph.json` 的 `caveats` 字段列出全部 11 条。要紧的几条：

1. 名录不是 500 家。Fortune 官网十名之后付费墙，维基的排名条目只放各年前十，
   实际能解析的是「按营收排名」表的 43 家。它是 500 强的头部，不是 500 强。
2. 母公司关系画不出来。GLEIF 现在对 `/direct-parent` 和 `/ultimate-parent` 一律返回 404，
   只给一个 reporting-exception 链接。所有权边因此只有 SEC 那条路，没有 GLEIF 那条。
3. 中石油对到的是 PetroChina Company Limited（上市子公司），不是
   China National Petroleum Corporation（母公司）：GLEIF 没有后者的英文记录。
4. 南大通用和协医没有 LEI，国家企业信用信息公示系统没有开放 API。两者都是手工录入，
   不带任何注册属性，也没有边。协医的法定主体名字还没确认，GLEIF 里只有近似名
   （枣庄市协盈、山西协诚医工），不构成精确匹配。
5. 没有任何一条边把南大通用或协医连到任何 Fortune 公司。没有来源能建立这个关系，
   所以没有编一条出来。

## 三个真实踩到的坑（修之前会污染图）

- SEC 的 submissions 索引列的是「这个 CIK 提交过的文件」，不是「关于这个 CIK 的文件」。
  Amazon 的 CIK 下躺着一份 2022 年的 13G，issuer 是 Rivian——Amazon 是申报人。
  不校验封面页的 issuer，Rivian 就会变成 Amazon 的股东。
- 13G 的封面页标签有两种：`NAME OF REPORTING PERSON` 和 `NAMES OF REPORTING PERSONS`。
  只认前者，Bezos 那 10.1% 就解析不出来（Amazon 走的是复数形式）。
- 按字符串做公司名匹配会把 "Amazon" 里的 "co" 当公司后缀剥掉，得到 "amaz"，
  然后一个都匹配不上。后缀只能从词尾剥。
