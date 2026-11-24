
import sys, os, time
repo = r"D:\Users\china\Desktop\项目_开发\entity-graph"
sys.path.insert(0, repo); os.chdir(repo)
import entitygraph.geopolitics as G

PRIO = ["US","CN","DE","JP","GB","FR","IT","NL","KR","SE","IN","BR","CA","MX","AU","CH",
        "ES","PL","TR","RU","SG","TH","MY","ID","VN","ZA","AE","SA","IL","NO","DK","FI",
        "BE","AT","PT","GR","IE","CZ","HU","RO","CL","AR","CO","PE","NZ","PH","PK","EG",
        "NG","KE","UA","SK","SI","HR","RS","BG","LT","LV","EE","IS","LU","MA","TN","GH",
        "BD","LK","NP","KZ","QA","KW","JO","OM","BH","CY","MT","HK","TW","ET","TZ","UG"]

def covered():
    c = G._load("trade_flows.json", {})
    return {i for i in PRIO if c.get("%s:%d" % (i, G.TRADE_YEAR), {}).get("rows")}

for rnd in range(200):
    cv = covered()
    todo = [i for i in PRIO if i not in cv]
    print("round %d: %d/%d covered, next %s" % (rnd, len(cv), len(PRIO), todo[:4]), flush=True)
    if not todo:
        print("ALL DONE", flush=True); break
    got = 0
    for iso in todo:
        for attempt in range(3):
            r = G.trade_rows(iso)
            err = str(r.get("error") or "")
            if r.get("rows"):
                got += 1
                print("OK  ", iso, len(r["rows"]), flush=True)
                break
            if "限流" in err:
                print("..  ", iso, err[:50], "backoff", flush=True)
                time.sleep(90); continue
            print("--  ", iso, err[:60], flush=True)
            break
        time.sleep(5)
    if got == 0:
        print("quota exhausted, sleeping 15 min", flush=True)
        time.sleep(900)
print("EXIT", flush=True)
