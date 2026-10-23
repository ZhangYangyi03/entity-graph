
"""company_data.py -- 免key公司/股权数据统一入口（2026-10-23 实测全通）
用法:
  python company_data.py subsidiaries 0001067983        # EDGAR 10-K Exhibit 21 子公司清单
  python company_data.py holders 0001067983             # SC 13D/13G 5%+ 实益所有人
  python company_data.py insiders 0001067983            # Form 4 内部人交易
  python company_data.py chain "Berkshire Hathaway"     # GLEIF LEI 母公司链
  python company_data.py sanction "Gazprom"             # OpenSanctions 命中
"""
import sys, json, re, urllib.request, urllib.parse, os

UA = {"User-Agent": "Yiz Research 2936745@qq.com", "Accept": "*/*"}

def _get(url, raw=False, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        b = r.read()
    return b.decode("utf-8", "ignore") if raw else json.loads(b)

def _cik(c):
    return "CIK" + str(c).strip().zfill(10)

def _filing_index(cik, form):
    """返回该 CIK 指定 form 的最近 filings: [(date, accession, ticker)]"""
    sub = _get(f"https://data.sec.gov/submissions/{_cik(cik)}.json")
    r = sub["filings"]["recent"]
    return [(r["filingDate"][i], r["accessionNumber"][i])
            for i, f in enumerate(r["form"]) if f == form]

def subsidiaries(cik):
    """EDGAR 10-K Exhibit 21 = 全部子公司（名称 + 注册地）"""
    from html import unescape
    date, acc = _filing_index(cik, "10-K")[0]
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-','')}"
    idx = _get(base + "/index.json")
    names = [i["name"] for i in idx["directory"]["item"]]
    ex = [n for n in names if re.search(r"ex-?21|ex21", n, re.I)]
    if not ex:
        sys.exit("no Exhibit 21 in latest 10-K")
    h = _get(f"{base}/{ex[0]}", raw=True)
    t = re.sub(r"<[^>]+>", "|", h)
    t = unescape(re.sub(r"&#160;?", " ", t))
    cells = [c.strip() for c in t.split("|") if c.strip()]
    # 跳过表头，成对取 (公司名, 注册地)
    try:
        s = next(i for i, c in enumerate(cells) if re.search(r"Company Name", c, re.I))
    except StopIteration:
        s = 0
    rows = []
    for i in range(s + 2, len(cells) - 1, 2):
        name, dom = cells[i], cells[i + 1]
        if len(name) > 90 or re.match(r"^(EX-|\d+$)", name):
            continue
        rows.append({"name": name, "domicile": dom})
    return {"cik": cik, "filing": date, "accession": acc, "count": len(rows), "subsidiaries": rows}

def holders(cik):
    """SC 13D / 13G = 5%+ 实益所有人"""
    out = []
    for form in ("SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A"):
        for date, acc in _filing_index(cik, form)[:40]:
            out.append({"form": form, "date": date, "accession": acc,
                        "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-','')}/"})
    return {"cik": cik, "count": len(out), "filings": out}

def insiders(cik):
    fl = _filing_index(cik, "4")
    return {"cik": cik, "count": len(fl),
            "filings": [{"date": d, "accession": a} for d, a in fl[:100]]}

def chain(legal_name):
    """GLEIF: 公司 -> 直接母公司 -> 最终母公司（可逐层上溯）"""
    q = urllib.parse.quote(legal_name)
    r = _get(f"https://api.gleif.org/api/v1/lei-records?filter%5Bentity.legalName%5D={q}&page%5Bsize%5D=5")
    res = []
    for it in r.get("data", []):
        a = it["attributes"]["entity"]
        lei = it["id"]
        node = {"lei": lei, "name": a["legalName"]["name"],
                "country": a["legalAddress"].get("country"),
                "city": a["legalAddress"].get("city"), "parents": []}
        for rel in ("direct-parent", "ultimate-parent"):
            try:
                p = _get(f"https://api.gleif.org/api/v1/lei-records/{lei}/{rel}")
                d = p.get("data") or {}
                if d:
                    node["parents"].append({"rel": rel, "lei": d.get("id"),
                                            "name": d.get("attributes", {}).get("relationship", {}).get("registration", {}).get("name")})
            except Exception:
                pass
        res.append(node)
    return {"query": legal_name, "matches": res}

def sanction(name):
    q = urllib.parse.quote(name)
    try:
        return _get(f"https://api.opensanctions.org/search/default?q={q}&limit=3")
    except Exception as e:
        return {"error": "needs key: " + str(e)[:80],
                "free_alternative": f"https://data.opensanctions.org/datasets/latest/default/targets.simple.csv"}

# ---------------------------------------------------------------------------
# 2026-10-23 第二轮扩充：五轮实测新增的免 key 源
# ---------------------------------------------------------------------------

def figi(id_type, id_value):
    """openfigi: ISIN/CUSIP/TICKER -> FIGI + 交易所（免 key，25 req/min）"""
    req = urllib.request.Request(
        "https://api.openfigi.com/v3/mapping",
        data=json.dumps([{"idType": id_type, "idValue": id_value}]).encode(),
        headers={**UA, "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def state_reg(state, q="", limit=5):
    """美国州注册库（Socrata 全量，免 key）。state in co/ny/ct/or"""
    ep = {"co": "https://data.colorado.gov/resource/4ykn-tg5h.json",
          "ny": "https://data.ny.gov/resource/n9v6-gdp6.json",
          "ct": "https://data.ct.gov/resource/n7gp-d28j.json",
          "or": "https://data.oregon.gov/resource/tckn-sxa6.json"}[state]
    url = f"{ep}?$limit={int(limit)}"
    if q:
        url += "&$q=" + urllib.parse.quote(q)
    return _get(url)

def littlesis(q):
    """美国政商关系图：实体检索 + 关系"""
    hits = _get("https://littlesis.org/api/entities/search?q=" + urllib.parse.quote(q))
    out = {"query": q, "entities": []}
    for e in (hits.get("data") or [])[:3]:
        eid = e.get("id")
        rel = _get(f"https://littlesis.org/api/entities/{eid}/relationships")
        out["entities"].append({"id": eid, "name": (e.get("attributes") or {}).get("name"),
                                "types": (e.get("attributes") or {}).get("types"),
                                "relationships": len((rel.get("data") or []))})
    return out

def lei_children(lei):
    """GLEIF 子公司链 + 最终母公司"""
    out = {"lei": lei}
    for rel in ("direct-parent", "ultimate-parent", "direct-children"):
        try:
            d = _get(f"https://api.gleif.org/api/v1/lei-records/{lei}/{rel}")
            data = d.get("data")
            if rel == "direct-children":
                out[rel] = [x.get("id") for x in (data or [])][:20]
                out[rel + "_total"] = (d.get("meta", {}).get("pagination", {}) or {}).get("total")
            elif data:
                out[rel] = data.get("id")
        except Exception as e:
            out[rel] = "ERR " + str(e)[:60]
    return out

def fr_company(q, tries=3):
    """法国：公司 + 实益所有人。这个 API 有软限速，429 要退避重试。"""
    import time as _t
    url = ("https://recherche-entreprises.api.gouv.fr/search?q="
           + urllib.parse.quote(q) + "&per_page=3")
    last = None
    for i in range(tries):
        try:
            return _get(url)
        except Exception as e:
            last = e
            _t.sleep(4 * (i + 1))
    raise last

def fr_bodacc(siren):
    """法国商业法院公告"""
    return _get("https://bodacc-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/"
                "annonces-commerciales/records?where=registre%3D%22" + urllib.parse.quote(siren) + "%22&limit=5")

def no_roles(orgnr):
    """挪威：股东/董事名册"""
    return _get(f"https://data.brreg.no/enhetsregisteret/api/enheter/{orgnr}/roller")

def ofac(name):
    """OFAC SDN 全量 CSV 里按名字过滤（1.2 万条，本地过滤足够快）"""
    import csv as _csv
    req = urllib.request.Request(
        "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.CSV",
        headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        txt = r.read().decode("utf-8", "ignore")
    rows = list(_csv.reader(txt.splitlines()))
    n = urllib.parse.quote(name).lower()
    hits = [r_ for r_ in rows if len(r_) > 1 and name.lower() in (r_[1] or "").lower()]
    return {"query": name, "total_sdn_rows": len(rows), "hits": hits[:10]}

def sec_form_index(form_date="20261022", quarter="2026/QTR4", kind="daily"):
    """EDGAR 按 form 类型全量清单：daily=form.YYYYMMDD.idx, quarter=form.idx"""
    if kind == "daily":
        url = f"https://www.sec.gov/Archives/edgar/daily-index/{quarter}/form.{form_date}.idx"
    else:
        url = f"https://www.sec.gov/Archives/edgar/full-index/{quarter}/form.idx"
    txt = _get(url, raw=True)
    return {"url": url, "lines": len(txt.splitlines()), "head": txt[:800]}

def efts(q, forms=None, limit=5):
    """EDGAR 全文检索（10-K/13D/Form D/NPORT-EX 都走这里）"""
    url = "https://efts.sec.gov/LATEST/search-index?q=" + urllib.parse.quote(q)
    if forms:
        url += "&forms=" + urllib.parse.quote(forms)
    d = _get(url)
    hits = d.get("hits", {})
    return {"query": q, "forms": forms, "total": hits.get("total"),
            "items": [h.get("_source") for h in (hits.get("hits") or [])[:limit]]}

def xbrl_frame(tag, period, taxonomy="us-gaap", unit="USD"):
    """全美股横截面：某 tag 某季度的所有公司值"""
    return _get(f"https://data.sec.gov/api/xbrl/frames/{taxonomy}/{tag}/{unit}/{period}.json")

def selftest():
    tests = [
        ("figi", lambda: figi("TICKER", "AAPL")),
        ("state_co", lambda: state_reg("co", limit=1)),
        ("state_ny", lambda: state_reg("ny", limit=1)),
        ("littlesis", lambda: littlesis("blackstone")),
        ("lei_children", lambda: lei_children("529900T8BM49AURSDO55")),
        ("fr_company", lambda: fr_company("airbus")),
        ("fr_bodacc", lambda: fr_bodacc("383474814")),
        ("no_roles", lambda: no_roles("923609016")),
        ("sec_form_index", lambda: sec_form_index()),
        ("efts", lambda: efts("apple", "10-K", 1)),
        ("xbrl_frame", lambda: xbrl_frame("Revenues", "CY2025Q3")),
    ]
    ok = 0
    for name, fn in tests:
        try:
            r = fn()
            s = json.dumps(r, ensure_ascii=False)
            print(f"OK   {name:14s} {len(s):7d} chars  {s[:110]}")
            ok += 1
        except Exception as e:
            print(f"FAIL {name:14s} {type(e).__name__}: {str(e)[:90]}")
    print(f"\n{ok}/{len(tests)} passed")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(1)
    cmd, arg = sys.argv[1], sys.argv[2]
    fn = {"subsidiaries": subsidiaries, "holders": holders, "insiders": insiders,
          "chain": chain, "sanction": sanction}[cmd]
    print(json.dumps(fn(arg), ensure_ascii=False, indent=1)[:6000])
