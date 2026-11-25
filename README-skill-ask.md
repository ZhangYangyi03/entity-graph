# entity-graph 怎么用（实用查询手册）

图文件：`clean/clean.connected.graph.json`（30,966 节点 / 63,244 边，已连岛）。
重建：`python -m entitygraph.cli connect --graph clean/clean.graph.json --out clean/clean.connected.graph.json`

## 六条现成命令

    python -m entitygraph.cli ask per-country            # 各国名下在轨物体，折叠同一国家的多套写法
    python -m entitygraph.cli ask per-operator           # 各运营商在轨数量
    python -m entitygraph.cli ask banks --top 10         # 美国各州银行资产（千美元）
    python -m entitygraph.cli ask board 脑机接口          # 概念板块成分 + 市值 + PE
    python -m entitygraph.cli ask issuer 脑机接口         # 板块成分 × 公告 × 估值 × 两融，按交易所代码归一
    python -m entitygraph.cli ask profile SpaceX         # 一个实体的全部关系
    python -m entitygraph.cli ask path 汉威科技 中国       # 两点最短路径

全部支持 `--format json` 给下游程序吃。

## 三条硬规矩（代码里已强制）

1. 按国家/板块汇总前必须走 `same_country_as` 折叠，否则 US 会被算两遍（13101 vs 26202）。
   用 `entitygraph.query.fold_map(g)`，不要自己数边。
2. 任何计数都要带分母。`satellites_by_country` 返回 population / classified / unclassified，
   classified + unclassified == population 有回归测试守着。
3. 拿不到的数据是 None，不是 0。"这家公司没有公告" 和 "公告里没提" 是两件事。

## 现在能答的问题

- 在轨航天态势：17,376 物体，按国家/运营商，SpaceX 11,184、OneWeb 651、千帆 257、国网 252
- A 股概念板块：脑机接口 50 家 6,984 亿、阿尔茨海默 36 家 4,280 亿，带 PE/市值
- 板块 × 公告：哪家公司真的发了脑机接口相关公告（世纪华通、创新医疗），而不是只被贴上概念
- 美国银行：4,217 家，26.45 万亿美元，按州可切
- 国家外交投票一致度：votes_with 11,252 条

## 现在答不了的（别拿它硬答）

- 跨域推理：卫星→脑机接口概念不连通。4 跳能到 SpaceX 只是因为"什么都连到美国"，
  不构成"银行和航天有关系"。
- 产业链：chain_stage 字段只有 dataset 3 个、trial 2 个填了，等于没建。
- 概念/思想层 933 个悬空节点，是文本不是图。
