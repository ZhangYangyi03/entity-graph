"""抓 GLEIF 企业记录（注册国 + 直接/最终母公司），可断点续取。

为什么要单独跑：GLEIF 每条记录要 1+2 次请求，110 家实测 6 线程约 4 分钟；放进 cli 的主
流程会让整张图的构建时间取决于一个外部注册库的响应速度。这里先跑，cli 只吃缓存。

    python tools/fetch_gleif.py            # 补到全部为止，每轮打印进度
"""
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, repo)
os.chdir(repo)

from entitygraph import geopolitics as G
from entitygraph.merge_world import load_graph

g = load_graph("all.graph.json")
leis = [n.attrs["lei"] for n in g.nodes.values()
        if n.kind == "company_gleif" and (n.attrs or {}).get("lei")]

for rnd in range(20):
    have = G._load("gleif_records.json", {})
    todo = [l for l in leis if l not in have]
    print("round %d: %d/%d cached, %d to go" % (rnd, len(leis) - len(todo), len(leis), len(todo)), flush=True)
    if not todo:
        print("ALL DONE", flush=True)
        break
    with ThreadPoolExecutor(6) as ex:
        list(ex.map(G.gleif_record, todo))
    time.sleep(2)
print("EXIT", flush=True)
