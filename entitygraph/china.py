"""China registry: Qichacha Open Platform (企查查开放平台, 合规付费 API).

与「爬取天眼查/企查查官网」不同，这里是企查查开放平台（openapi.qcc.com）的官方
授权 API，通过 AppKey + SecretKey 合法获取工商数据。本模块把它封装成 entitygraph
的数据源：

  * 按统一社会信用代码（credit code）或企业名称查询工商信息
  * 法定代表人（legal representative）、股东（shareholders，含持股比例）、对外投资
  * 覆盖企业 / 事业单位 / 国企（机构类型字段会标注）

密钥通过环境变量提供（不写入代码、不随仓库提交）：
  QCC_APP_KEY     企查查开放平台应用 AppKey（"账号安全"页面查询）
  QCC_SECRET_KEY  企查查开放平台应用 SecretKey

认证协议（企查查开放平台 HTTP JSON API，非 MCP SSE）：
  Headers:
    Token      = Md5(key + Timespan + SecretKey) 加密的 32 位大写字符串
    Timespan   = 精确到秒的 Unix 时间戳
  Query:
    key        = AppKey
    searchKey  = 统一社会信用代码 或 企业名称

无 key 时返回空并抛出 NoKey，由调用方记入 notes（与 korea.DartNoKey 同构）。

历史说明：本模块最初用天眼查开放平台的推测字段实现，后切换到企查查——因为企查查
开放平台的返回字段有公开、逐字段的文档，可审计、可对齐；天眼查字段需登录后台查看。
"""
from __future__ import annotations

import hashlib
import os
import time
import urllib.parse
import urllib.request

from .fetch import get, q


API_HOST = "https://api.qichacha.com"
DEFAULT_TIMEOUT = 30.0


class QccNoKey(RuntimeError):
    """Raised when QCC_APP_KEY / QCC_SECRET_KEY are not set; mirrors korea.DartNoKey."""


# 保留旧的异常名作为别名，避免破坏已接入的 build.py 调用点。
TianyanchaNoKey = QccNoKey


def _credentials() -> tuple[str, str]:
    key = os.environ.get("QCC_APP_KEY") or os.environ.get("TIANYANCHA_TOKEN")
    secret = os.environ.get("QCC_SECRET_KEY") or os.environ.get("TIANYANCHA_SECRET")
    if not key:
        raise QccNoKey("QCC_APP_KEY not set; China registry source skipped")
    if not secret:
        # 企查查要求 key+secret 签名；只给 key 无法生成有效 Token。
        raise QccNoKey("QCC_SECRET_KEY not set; China registry source skipped")
    return key, secret


def _call(path: str, params: dict) -> dict:
    """GET a Qichacha Open API endpoint with the signed Token header."""
    key, secret = _credentials()
    ts = str(int(time.time()))
    token = hashlib.md5((key + ts + secret).encode("utf-8")).hexdigest().upper()

    qs = urllib.parse.urlencode(params)
    url = f"{API_HOST}{path}?{qs}"
    headers = {"Token": token, "Timespan": ts}
    return get(url, ttl=86400.0, timeout=DEFAULT_TIMEOUT, headers=headers)


def _result(data: dict) -> dict | None:
    """企查查 wraps results in {"Status":"200","Result":{...}}."""
    if not isinstance(data, dict):
        return None
    if str(data.get("Status")) != "200":
        return None
    r = data.get("Result")
    return r if isinstance(r, dict) else None


def _pick(data: dict, *keys: str) -> str:
    for k in keys:
        v = data.get(k)
        if v not in (None, ""):
            return str(v).strip()
    return ""


def baseinfo(keyword: str) -> dict | None:
    """企业工商信息（ApiCode 735 或 410）。返回照面信息 + 股东 + 主要人员等。

    企查查「企业工商详情」(735) 一次返回 Partners(股东)/Employees(主要人员)/Branches
    等；这里用 410「企业工商信息」拿照面字段，股东走独立接口 holders()。
    """
    r = _result(_call("/ECIInfoVerify/GetBasicDetailsByName", {"key": _appkey(), "searchKey": keyword}))
    return r


def _appkey() -> str:
    return os.environ.get("QCC_APP_KEY") or os.environ.get("TIANYANCHA_TOKEN") or ""


def holders(keyword: str) -> list[dict]:
    """股东信息（工商登记，ApiCode 731）。含 StockName/StockPercent/StockType。"""
    r = _result(_call("/ECIPartners/GetList", {"key": _appkey(), "searchKey": keyword}))
    if not r:
        return []
    return r.get("Partners") or r.get("Data") or []


def invests(keyword: str) -> list[dict]:
    """对外投资（ApiCode 716）。被投公司 + 投资比例 + 法定代表人。"""
    r = _result(_call("/ECIIndustry/GetList", {"key": _appkey(), "searchKey": keyword}))
    if not r:
        return []
    return r.get("Data") or r.get("OutInvestment") or []


def _holders_normalized(keyword: str) -> list[dict]:
    """把企查查股东原始字段规整成 entitygraph 内部统一的 shareholders 结构。"""
    out = []
    for h in holders(keyword):
        name = _pick(h, "StockName", "PartnerName", "StockName", "name")
        if not name:
            continue
        out.append({
            "name": name,
            "type": _pick(h, "StockType", "Type", "type"),
            "capital": _pick(h, "ShouldCapi", "SubscribedCapital", "capital"),
            "percent": _pick(h, "StockPercent", "StockPercent", "percent"),
            "credit_code": _pick(h, "CreditCode", "creditCode"),
        })
    return out


def _invests_normalized(keyword: str) -> list[dict]:
    out = []
    for inv in invests(keyword):
        name = _pick(inv, "Name", "Name", "companyName", "name")
        if not name:
            continue
        out.append({
            "name": name,
            "credit_code": _pick(inv, "CreditCode", "creditCode", "unifiedSocialCreditCode"),
            "percent": _pick(inv, "FundedRatio", "StockPercent", "percent", "rate"),
            "legal_person": _pick(inv, "OperName", "legalPersonName", "legalPerson"),
        })
    return out


def lookup(keyword: str, *, with_holders: bool = True,
           with_invests: bool = True) -> list[dict]:
    """把一个中国实体解析成 entitygraph 兼容的 reg_hits 记录列表。

    返回结构与 registries.lookup_name 一致：
        [{"id": 统一社会信用代码, "name": 名称, "country": "CN",
          "address": ..., "attributes": {...}, "url": ..., "officers": [...]}]

    股东/对外投资/法定代表人会放进 attributes，供 build 层构造节点与边。
    """
    _credentials()  # fail fast if no key
    info = baseinfo(keyword)
    if not info:
        return []

    credit = _pick(info, "CreditCode", "creditCode", "No", "unifiedSocialCreditCode")
    name = _pick(info, "Name", "name", "companyName")
    if not credit and not name:
        return []

    legal = _pick(info, "OperName", "LegalPerson", "legalPersonName", "legalPerson")
    kind = _pick(info, "EconKind", "CompanyType", "companyType") or "company"

    attrs: dict = {
        "credit_code": credit,
        "legal_person": legal,
        "org_type": kind,
        "reg_status": _pick(info, "Status", "regStatus"),
        "reg_capital": _pick(info, "RegistCapi", "regCapital"),
        "estiblish_time": _pick(info, "StartDate", "estiblishTime"),
    }
    officers: list[dict] = []
    if legal:
        officers.append({"name": legal, "role": "legal_representative"})

    if with_holders:
        attrs["shareholders"] = _holders_normalized(keyword)
    if with_invests:
        attrs["investments"] = _invests_normalized(keyword)

    url = f"https://www.qcc.com/search?key={q(name or keyword)}"
    return [{
        "id": credit or f"cn-name:{name}",
        "name": name or keyword,
        "country": "CN",
        "address": _pick(info, "Address", "address", "regLocation"),
        "attributes": attrs,
        "url": url,
        "officers": officers,
    }]


def china_search(name: str, limit: int = 3) -> list[dict]:
    """按名称搜索（供 build 按名称入口调用）。企查查接口已支持名称查询。"""
    try:
        hits = lookup(name, with_holders=True, with_invests=True)
    except QccNoKey:
        raise
    except Exception:
        return []
    return hits[:limit]
