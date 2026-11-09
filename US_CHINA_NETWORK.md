美国华裔/亚裔政客 · 对华交流机构 · 中国政治与商业民间机构 关系图谱
生成时间：2026-11-09T08:36:19Z

一、这张图里有什么
  节点 1008 个，边 1485 条，全部节点与边都带 source/url/fetched_at。
  person         871
  category        41
  employer        34
  org_cn          28
  org_us          15
  us_state        15
  misc             2
  party            2

二、关系类型（按可核查强度）
  board_member_of               76
  member_of                    174
  affiliated_with_employer      34
  associated_with               31
  overseen_by                    6
  member_of_party              191
  represents_state              64
  affiliation_claim              2
  mentioned_in                  42
  mentions_org                  28
  classified_as                837

三、关于"亲华派"
  没有可靠公开标签能判定谁亲华，所以本图不做立场判断。
  只用"是否出现在对华交流机构名册"作为可核查的代理指标：
  · 有对华机构关联的人：245 个（其中政治人物 8 个）
  · 这表示"参与过/被列在名册里"，不等于亲华、不等于受谁控制、也不等于忠诚判断。

四、覆盖边界（没做到的部分）
  · 没有"亲华派"的可靠标签：原始材料（Wikipedia 分类、国会名单、机构官网名册）不含政治立场标注。本图只用"参与对华交流机构"作为可核查的代理指标（attrs.engagement_with_china_orgs），不把它等同于亲华或忠诚判断。
  · mentions_org 是共现，不是关系断言：机构 wiki 正文里出现另一机构全名即建 mentions_org 边，attrs.status=co_mentioned，并附 evidence 原文片段；可能是合作、批评、列举或历史叙述，需人工看 evidence 再定性。
  · mentioned_in 是页面内 wiki 链接，不等于任职：机构页面 [[人名]] 链接只说明该页提到此人；board_member_of / member_of 才来自机构官网名册。
  · 覆盖不全：人名来自 Wikipedia 分类成员(597)+人物正文(626)+亚裔国会议员名单(93)，去重后 628；不是"美国所有"政客，且中文/地方级人物明显缺。
  · 中国机构只有 11 家：cn_orgs.json 覆盖全国性人民团体/民间外交/商业机构，未含地方侨务、商会、高校与统战系统全部实体。
  · 时间快照：原料抓取于本机构建前，无逐条抓取时间戳，统一记为构建时间；官网名册可能已换届。
  · 区分政客与名册人士：机构官网名册含大量商界/学界人士（如 I.M. Pei、Eric S. Yuan）。attrs.is_politician 只依据 Wikipedia 政治类分类或国会议员名单；engagement_with_china_orgs 现在只标在政治人物上。
  · 任职单位来自机构官网名册：affiliated_with_employer 只表示名册把该人和该单位写在一起，不等于现职雇佣；单位名从 context 文本切分，可能含缩写或拼接误差。
  · 新增 12 家中国机构来自维基百科导言：cn_orgs_extra.json 用 Wikipedia API extracts 抓首段，associated_with 由导言里出现上级机构名生成，属文本推断而非官方组织关系认定；未命中的候选（如 China Public Diplomacy Association）不收录。

五、数据文件
  us_china_politics.graph.json  主图（nodes/edges/meta/caveats）
  shards/us_china_raw/          29 份原始素材（含本轮新增的机构名录与候选）（维基正文、机构官网 HTML、名册 JSON）

六、已知缺口（按重要性）
  1. 不是"美国所有"华裔/亚裔政客：来源为维基分类 597 人 + 人物正文 626 人 + 亚裔国会议员名单 93 人，去重 628；地方级、中文名人物明显缺。
  2. 对华立场类关系（捐款、投票、访华、涉华法案表态）没有接入 FEC / Congress.gov / 官方记录。
  3. 中国机构 25 家，未含地方侨务、商会、高校与统战系统全部实体。
  4. 共现边（mentioned_in / mentions_org）只是文本出现，不是关系断言，需看 evidence 原文再定性。