"""Optional modules: which pages and data sources the user turned on.

The core (overview, holdings, risk, plans, fills and review, data) is always on. Everything that
needs an outside data file, or that only some users want, is a module the user picks on first run.
The choice lives in the ledger's kv table under "modules". An install that confirmed its sizing
net value before modules existed is treated as onboarded with every module on, so upgrading
changes nothing for it.
"""
from __future__ import annotations

from .store import Invalid

CORE_PAGES = ("overview", "holdings", "risk", "plans", "journal", "data")

# key, title, one-line summary, what it needs, default for a new user, pages it owns
CATALOG = (
    ("calendar", "日历", "宏观数据、财报、期权到期和计划复核日排在一张议程里；也能手动加事件。",
     "到期日和复核日自动来；宏观日历与财报日期需要数据文件（可选）。", True, ("calendar",)),
    ("optionPlans", "期权计划", "把期权计划卡和已持有的期权画成可拖动的盈亏图（日期、波动率、价格范围）。",
     "已持有的期权自动画；计划卡是你自己写的 JSON 文件（可选）。", True, ("optionPlans",)),
    ("preparation", "盘前准备", "每天开盘前的检查清单和笔记；有盘前观察文件时一并列出。",
     "检查清单不需要数据；盘前观察需要每日生成的数据文件（可选）。", False, ("preparation",)),
    ("macro", "宏观灯", "用一盏红黄绿灯调节新开仓的总热度上限和每个板块的上限，只管新仓的大小。",
     "需要宏观面板数据文件（每周更新）。", False, ()),
    ("weekly", "周线观察", "几只宽基 ETF 的周线和四条周 EMA，列出预先约定的预警条件。",
     "需要周线观察数据文件。", False, ("weekly",)),
    ("paper", "纸盘", "只观察、不下单的模拟交易，按收盘估值，不进真实账本和风险统计。",
     "需要纸盘估值数据文件。", False, ("paper",)),
    ("econf", "券商确认单导入", "把 Schwab 成交确认邮件解析成成交提案，人工核对后入账。",
     "需要把确认邮件存进投喂文件夹（可配 Mail 规则自动存）。", False, ()),
)
KEYS = tuple(m[0] for m in CATALOG)


def catalog(found=None):
    found = found or {}
    return [{"key": k, "title": t, "summary": s, "needs": n, "default": d, "pages": list(p),
             "data_found": found.get(k)} for k, t, s, n, d, p in CATALOG]


def current(store):
    saved = store.get("modules")
    if isinstance(saved, dict) and isinstance(saved.get("enabled"), dict):
        enabled = {k: bool(saved["enabled"].get(k, False)) for k in KEYS}
        return {"onboarded": bool(saved.get("onboarded")), "enabled": enabled}
    # First read of this ledger since modules exist: decide once and keep it, so confirming R
    # during the walkthrough cannot turn a new ledger into an "old" one.
    if store.risk_policy().get("nlv") is not None:  # an install from before modules existed
        value = {"onboarded": True, "enabled": {k: True for k in KEYS}}
    else:
        value = {"onboarded": False, "enabled": {k: d for k, _, _, _, d, _ in CATALOG}}
    store.put("modules", value)
    return value


def save(store, data):
    enabled = data.get("enabled")
    if not isinstance(enabled, dict) or any(k not in KEYS or not isinstance(v, bool) for k, v in enabled.items()):
        raise Invalid("模块设置格式不正确")
    value = {"onboarded": data.get("onboarded", True) is True,
             "enabled": {k: bool(enabled.get(k, False)) for k in KEYS}}
    store.put("modules", value)
    return value


def hidden_pages(enabled):
    return sorted({p for k, _, _, _, _, pages in CATALOG if not enabled.get(k) for p in pages})
