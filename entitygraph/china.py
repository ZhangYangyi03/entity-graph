"""China registry: Tianyancha Open Platform (合规付费 API).

与「爬取天眼查」不同，这里是天眼查开放平台（open.tianyancha.com）的官方 API，
通过付费授权合法获取工商数据。本模块把它封装成 entitygraph 的数据源：

  * 按统一社会信用代码（credit code）或公司名称查询企业工商信息
  * 法定代表人（legal representative）、股东（shareholders）、对外投资（investments）
  * 覆盖企业 / 事业单位 / 国企（机构类型字段 kind 会标注）

密钥通过环境变量提供（不写入代码、不随仓库提交）：
  TIANYANCHA_TOKEN   天眼查开放平台的 access token（Authorization: <token>）

天眼查开放平台常见接口（v2，HTTPS POST/GET，返回 JSON）：
  baseinfo       /services/open/ic/baseinfo/2.0         企业工商信息
  shareholder    /services/open/ic/holder/2.0           股东信息
  invest         /services/open/ic/invest/2.0           对外投资
  branch         /services/open/ic/branch/2.0           分支机构

无 token 时返回空并抛出 NoKey，由调用方记入 notes（与 korea.DartNoKey 同构）。
"""
from __future__ import annotations

import os
import urllib.parse
import urllib.request

from .fetch import get, q


API_HOST = "https://open.api.tianyancha.com"
DEFAULT_TIMEOUT = 30.0


class TianyanchaNoKey(RuntimeError):
    """Raised when TIANYANCHA_TOKEN is not set; mirrors korea.DartNoKey."""


def _token() -> str:
    tok = os.environ.get("TIANYANCHA_TOKEN") or os.environ.get("TIANYANCHA_API_KEY")
    if not tok:
        raise TianyanchaNoKey("TIANYANCHA_TOKEN not set; Tianyancha source skipped")
    return tok


def _call(path: str, params: dict) -> dict:
    """GET a Tianyancha Open API endpoint with the auth header."""
    tok = _token()
    qs = urllib.parse.urlencode(params)
    url = f"{API_HOST}{path}?{qs}"
    headers = {"Authorization": tok}
    return get(url, ttl=86400.0, timeout=DEFAULT_TIMEOUT, headers=headers)


def _pick(data: dict, *keys: str) -> str:
    for k in keys:
        v = data.get(k)
        if v not in (None, ""):
            return str(v).strip()
    return ""


def _result(data: dict) -> dict | None:
    """Tianyancha wraps results in {"error_code":0, "result": {...}}."""
    if not isinstance(data, dict):
        return None
    if data.get("error_code") not in (0, "0", None):
        return None
    r = data.get("result")
    return r if isinstance(r, dict) else None


def baseinfo(keyword: str) -> dict | None:
    """按公司名或统一社会信用代码查工商基本信息。keyword 会被智能匹配。"""
    r = _result(_call("/services/open/ic/baseinfo/2.0", {"keyword": keyword}))
    return r


def holders(keyword: str) -> list[dict]:
    """股东信息（含持股比例、认缴/实缴出资额）。"""
    r = _result(_call("/services/open/ic/holder/2.0", {"keyword": keyword}))
    if not r:
        return []
    return r.get("items") or r.get("holders") or []


def invests(keyword: str) -> list[dict]:
    """对外投资（被投公司名 + 投资比例 + 法定代表人）。"""
    r = _result(_call("/services/open/ic/invest/2.0", {"keyword": keyword}))
    if not r:
        return []
    return r.get("items") or r.get("invests") or []


def lookup(keyword: str, *, with_holders: bool = True,
           with_invests: bool = True) -> list[dict]:
    """把一个中国实体解析成 entitygraph 兼容的 reg_hits 记录列表。

    返回结构与 registries.lookup_name 一致：
        [{"id": 统一社会信用代码, "name": 名称, "country": "CN",
          "address": ..., "attributes": {...}, "url": ..., "officers": [...]}]

    股东/对外投资/法定代表人会放进 attributes，供 build 层构造节点与边。
    """
    _token()  # fail fast if no key
    info = baseinfo(keyword)
    if not info:
        return []

    credit = _pick(info, "regNumber", "creditCode", "credit_code", "unifiedSocialCreditCode")
    name = _pick(info, "name", "companyName", "company_name")
    if not credit and not name:
        return []

    legal = _pick(info, "legalPersonName", "legal_person_name", "legalRepresentative", "legalPerson")
    kind = _pick(info, "orgType", "org_type", "companyOrgType", "company_org_type") or "company"

    attrs: dict = {
        "credit_code": credit,
        "legal_person": legal,
        "org_type": kind,
        "reg_status": _pick(info, "regStatus", "reg_status"),
        "reg_capital": _pick(info, "regCapital", "reg_capital"),
        "estiblish_time": _pick(info, "estiblishTime", "estiblish_time", "startDate"),
    }
    officers: list[dict] = []
    if legal:
        officers.append({"name": legal, "role": "legal_representative"})

    if with_holders:
        hs = []
        for h in holders(keyword):
            hn = _pick(h, "name", "holderName", "shareholderName", "shareholder_name")
            if not hn:
                continue
            hs.append({
                "name": hn,
                "type": _pick(h, "type", "holderType", "holder_type"),
                "capital": _pick(h, "capital", "subscribedCapital"),
                "percent": _pick(h, "percent", "rate", "shareholdingRatio"),
                "paid": _pick(h, "paidInCapital", "actualCapital"),
            })
        attrs["shareholders"] = hs

    if with_invests:
        ivs = []
        for inv in invests(keyword):
            ivn = _pick(inv, "name", "companyName", "company_name")
            if not ivn:
                continue
            ivs.append({
                "name": ivn,
                "credit_code": _pick(inv, "regNumber", "creditCode", "unifiedSocialCreditCode"),
                "percent": _pick(inv, "percent", "rate", "investmentRatio"),
                "legal_person": _pick(inv, "legalPersonName", "legalPerson"),
            })
        attrs["investments"] = ivs

    url = f"https://www.tianyancha.com/search?key={q(name or keyword)}"
    return [{
        "id": credit or f"cn-name:{name}",
        "name": name or keyword,
        "country": "CN",
        "address": _pick(info, "regLocation", "reg_location", "address"),
        "attributes": attrs,
        "url": url,
        "officers": officers,
    }]


def china_search(name: str, limit: int = 3) -> list[dict]:
    """按名称搜索（供 build 按名称入口调用）。天眼查 baseinfo 已支持名称查询。"""
    try:
        hits = lookup(name, with_holders=True, with_invests=True)
    except TianyanchaNoKey:
        raise
    except Exception:
        return []
    return hits[:limit]
