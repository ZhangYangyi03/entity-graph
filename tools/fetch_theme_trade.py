
import sys, os, time
repo = r"D:\Users\china\Desktop\项目_开发\entity-graph"
sys.path.insert(0, repo); os.chdir(repo)
import entitygraph.geopolitics as G
from entitygraph.geopolitics import COUNTRY_ETFS
PRIO = ["US","CN","SE","DE","JP","GB","FR","IT","NL","KR","IN","BR","CA","MX","AU","CH",
        "ES","PL","TR","RU","SG","TH","MY","ID","VN","ZA","AE","SA","IL","NO","DK","FI",
        "BE","AT","PT","GR","IE","CZ","HU","RO","CL","AR","CO","PE","NZ","PH","PK","EG",
        "NG","KE","UA","SK","SI","HR","RS","BG","LT","LV","EE","IS","LU","MA","TN","GH",
        "BD","LK","NP","KZ","QA","KW","JO","OM","BH","CY","MT","HK","TW","ET","TZ","UG"]
def covered():
    c = G._load("trade_flows.json", {})
    return {i for i in PRIO if c.get("%s:%d" % (i, G.TRADE_YEAR), {}).get("rows")}
for rnd in range(400):
    cv = covered()
    todo = [i for i in PRIO if i not in cv]
    print("round %d: %d/%d covered, next %s" % (rnd, len(cv), len(PRIO), todo[:4]), flush=True)
    if not todo:
        print("ALL DONE", flush=True); break
    got = 0
    for iso in todo:
        r = G.trade_rows(iso)
        if r.get("rows"):
            got += 1; print("OK  ", iso, len(r["rows"]), flush=True)
        else:
            print("--  ", iso, str(r.get("error"))[:60], flush=True)
        time.sleep(40)
    if got == 0:
        print("quota exhausted, sleeping 20 min", flush=True)
        time.sleep(1200)
print("EXIT", flush=True)
