"""Landing-page digest: which facts want a look today, how fresh each source is, what is next.

Selection and ordering only. Every line restates a field the engine already computed (heat, factor
clusters, expiries, source ages); nothing here is a recommendation, nothing is written back, and a
source that is missing or old is reported as such, never as "nothing to do".
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from . import paper as paper_book
from . import weekly_watch
from .exposure import EXEMPT_MARK

SEVERITY_ORDER = {"red": 0, "amber": 1, "info": 2}
PREP_CHECKS = 3
MACRO_LAG_TOLERANCE = 2  # the macro job runs after the US close (Asia morning); one day of slack is normal
ET = ZoneInfo("America/New_York")
DISPLAY_TZ = None  # next-refresh times are shown in the Mac's own time zone (tests pin it)
BRIEF_READY = time(9, 0)  # the US digest starts 08:45 ET and is written by about 08:50

# When each source is refreshed (launchd schedules; holidays not modelled). Shown on hover so a green dot
# also says how long it stays current and how often it is worth a look.
SCHEDULES = {
    "snapshot": (ET, (0, 1, 2, 3, 4), (time(9, 45), time(16, 5)),
                 "每个交易日两次：开盘校准（美东 09:45）和收盘（美东 16:05）"),
    "market": (ET, (0, 1, 2, 3, 4), (time(16, 5),), "每个交易日收盘后（美东 16:05）随持仓快照写入"),
    "macro": (ET, (0, 1, 2, 3, 4), (time(19, 50),),
              "每个交易日美东收盘后（19:50）。趋势格每天更新；灯按收完的一周确认，连续两周同色才换档，每周看一次即可"),
    "brief": (ET, (0, 1, 2, 3, 4), (time(8, 45),), "每个交易日美东 08:45 生成，用前一个交易日的收盘"),
}


def _day(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def missed_trading_days(day, today):
    """Weekdays strictly between `day` and `today` (holidays are not modelled: approximate by design)."""
    if day is None or today is None or day >= today:
        return 0
    return sum(1 for i in range(1, (today - day).days) if (day + timedelta(days=i)).weekday() < 5)


def next_run(key, now):
    """Next scheduled refresh after `now`, as wall time in the Mac's time zone, e.g. "10-13 20:45"."""
    tz, weekdays, times, _ = SCHEDULES[key]
    local = now.astimezone(tz)
    for offset in range(8):
        day = local.date() + timedelta(days=offset)
        if day.weekday() not in weekdays:
            continue
        for t in times:
            at = datetime.combine(day, t, tz)
            if at > now:
                return at.astimezone(DISPLAY_TZ).strftime("%m-%d %H:%M")
    return ""


def expected_brief_day(now):
    """Close the latest digest should be built on: the trading day before the latest report day."""
    local = now.astimezone(ET)
    report = local.date() if local.time() >= BRIEF_READY else local.date() - timedelta(days=1)
    while report.weekday() >= 5:
        report -= timedelta(days=1)
    signal = report - timedelta(days=1)
    while signal.weekday() >= 5:
        signal -= timedelta(days=1)
    return signal


def _money(value):
    return f"${value:,.0f}"


def _modules(state):
    """Whether an optional module is on; a state without the modules block (older tests) counts every one as on."""
    enabled = (state.get("modules") or {}).get("enabled")
    return (lambda key: True) if not isinstance(enabled, dict) else (lambda key: bool(enabled.get(key)))


def freshness(state, today, now=None):
    now = now or datetime.combine(today, time(12), ET)
    snapshot, exposure, brief = state.get("snapshot") or {}, state.get("exposure") or {}, state.get("brief") or {}
    macro = exposure.get("macro_panel") or {}
    on = _modules(state)
    rows = []

    presence = state.get("source_presence") or {}
    day = _day(snapshot.get("day"))
    lag = missed_trading_days(day, today)
    if presence.get("snapshot") is False:  # never connected: optional, not a broken job
        rows.append(("snapshot", "持仓", "", "off", "未接入持仓快照（可选：在「数据与留存」导入持仓 JSON）"))
    elif snapshot.get("status") != "available":
        rows.append(("snapshot", "持仓", snapshot.get("day") or "", "bad", "来源不可用，显示历史留存"))
    else:
        rows.append(("snapshot", "持仓", snapshot.get("day") or "", "warn" if lag else "ok",
                     f"落后 {lag} 个交易日" if lag else ""))

    market_day = exposure.get("market_day") or ""
    lag = missed_trading_days(_day(market_day), today)
    if not market_day and presence.get("market") is False:
        rows.append(("market", "行情", "", "off", "未接入日线（可选：风险页的 Beta、VaR 要用，格式见 docs/data-formats.md）"))
    elif not market_day:
        rows.append(("market", "行情", "", "bad", "没有行情日期"))
    else:
        rows.append(("market", "行情", market_day, "warn" if lag else "ok", f"落后 {lag} 个交易日" if lag else ""))

    if not on("macro"):
        pass
    elif macro.get("status") == "ok":
        lag = missed_trading_days(_day(macro.get("data_day")), today)
        rows.append(("macro", "宏观", macro.get("data_day") or "", "warn" if lag >= MACRO_LAG_TOLERANCE else "ok",
                     f"落后 {lag} 个交易日" if lag >= MACRO_LAG_TOLERANCE else ""))
    else:
        rows.append(("macro", "宏观", "", "bad", macro.get("notice") or "宏观面板不可用"))

    brief_day = brief.get("day") or ""
    expected = expected_brief_day(now)
    if not on("preparation"):
        brief_day, expected = None, None
    behind = sum(1 for i in range(1, (expected - _day(brief_day)).days + 1)
                 if (_day(brief_day) + timedelta(days=i)).weekday() < 5) if _day(brief_day) else 0
    if expected is None:
        pass
    elif not brief_day:
        rows.append(("brief", "盘前", "", "warn", brief.get("error") or "没有盘前资料"))
    elif behind:
        rows.append(("brief", "盘前", brief_day, "warn", f"漏了 {behind} 期（应有基于 {expected.isoformat()} 收盘的一期）"))
    elif brief.get("status") == "stale":
        rows.append(("brief", "盘前", brief_day, "warn", brief.get("error") or "盘前资料过期"))
    else:
        rows.append(("brief", "盘前", brief_day, "ok", ""))
    out = []
    for row in rows:
        item = dict(zip(("key", "label", "day", "level", "note"), row))
        item.update(cadence=SCHEDULES[item["key"]][3], next=next_run(item["key"], now))
        out.append(item)
    return out


def _expiring(state, today):
    for p in state.get("positions") or []:
        if p.get("display_only") or p.get("kind") != "option":
            continue
        expiry = _day(p.get("expiry"))
        if expiry is None:
            continue
        days = (expiry - today).days
        if days > 3:
            continue
        qty = abs(p.get("qty") or 0)
        when = "已过到期日" if days < 0 else "今天到期" if days == 0 else "明天到期" if days == 1 else f"{days} 天后到期"
        if p.get("side") == "short":
            detail = f"空头 {qty:g} 张 · 临近到期留意指派"
        else:
            value = (p.get("mark") or 0) * qty * (p.get("mult") or 100)
            detail = f"多头 {qty:g} 张 · 快照价 {p.get('mark'):g} · 权利金现值约 {_money(value)}" if p.get("mark") is not None \
                else f"多头 {qty:g} 张"
        yield ("red" if days <= 1 else "amber", f"{p.get('sym')} {p.get('label')} {when}", detail, "holdings")


def attention(state, today, fresh):
    items = []

    def add(severity, title, detail="", page="risk"):
        items.append({"severity": severity, "title": title, "detail": detail, "page": page})

    exposure = state.get("exposure") or {}
    heat = exposure.get("portfolio_heat") or {}
    for item in heat.get("items") or []:
        if item.get("flag"):
            add("red", f"{item.get('symbol')} {item.get('label')}：{item['flag']}", item.get("stop") or "", "holdings")
    for severity, title, detail, page in _expiring(state, today):
        add(severity, title, detail, page)

    for cluster in (exposure.get("factor_clusters") or {}).get("clusters") or []:
        status = cluster.get("status")
        if status not in ("over", "near") or cluster.get("stress_loss_r") is None:
            continue
        base, cap = cluster.get("cap_base_r"), cluster.get("cap_r")
        note = f"宏观灯已把上限 {base:g}→{cap:g} R" if base is not None and cap is not None and abs(base - cap) > 0.001 else ""
        add("red" if status == "over" else "amber",
            f"{cluster.get('factor')}压力 {cluster['stress_loss_r']:.2f} / {cap:g} R "
            f"{'达到或超过上限' if status == 'over' else '接近上限'}", note)

    missing = (heat.get("unknown") or []) + (heat.get("unbounded") or [])
    if missing:
        add("amber", f"{len(missing)} 组未计入热度", "、".join(dict.fromkeys(str(x.get("symbol")) for x in missing)))
    if heat.get("status") == "r_unavailable":
        add("amber", "定仓净值未确认，热度与情景无法折算成 R", "", "plans")

    no_stop = heat.get("no_stop") or []
    chase = sum(x.get("value_usd") or 0 for x in no_stop)
    if chase > 0:
        add("amber", f"无止损正股 {_money(chase)}", "、".join(x["symbol"] for x in no_stop), "holdings")

    for row in fresh:
        if row["level"] == "bad":
            add("red", f"{row['label']}数据：{row['note']}", "", "data" if row["key"] == "snapshot" else "risk")
        elif row["level"] == "warn" and row["key"] != "brief":
            add("amber", f"{row['label']}数据 {row['day']} · {row['note']}", "", "data" if row["key"] == "snapshot" else "risk")

    coverage = (exposure.get("market_scenario") or {}).get("coverage") or {}
    if coverage.get("total") and (coverage.get("used") or 0) < coverage["total"]:
        add("amber", f"大盘情景仅覆盖 {coverage.get('used') or 0} / {coverage['total']} 条", "Beta 样本或价格日期不齐", "risk")

    on = _modules(state)
    econf = state.get("econf") or {}
    if on("econf") and econf.get("pending"):
        add("amber", f"{econf['pending']} 份券商确认单待核对", f"共 {econf.get('pending_legs', 0)} 条成交腿，自动入账没有处理", "journal")
    to_confirm = [p for p in state.get("positions") or [] if p.get("confirm") and not p.get("display_only")]
    if to_confirm:
        add("amber", f"{len(to_confirm)} 条持仓待核对", "、".join(dict.fromkeys(str(p.get("sym")) for p in to_confirm)), "holdings")
    if state.get("conflicts"):
        add("amber", "持仓与账本对账有冲突", "；".join(map(str, state["conflicts"]))[:200], "holdings")

    if on("paper"):
        for severity, title, detail in paper_book.attention(state.get("paper")):
            add(severity, title, detail, "paper")
    if on("weekly"):
        for severity, title, detail in weekly_watch.attention(state.get("weekly_watch")):
            add(severity, title, detail, "weekly")

    brief = next((r for r in fresh if r["key"] == "brief"), None)
    if brief and brief["level"] == "warn":  # a missing digest is a broken job, not a quiet day
        add("amber", "盘前资料：" + brief["note"], "", "preparation")
    checks = len((state.get("preparation") or {}).get("checks") or [])
    if on("preparation") and checks < PREP_CHECKS:
        add("info", f"盘前检查 {checks} / {PREP_CHECKS} 未完成", "", "preparation")
    return sorted(items, key=lambda x: SEVERITY_ORDER[x["severity"]])


def upcoming(state, today, limit=6):
    rows = []
    for event in (state.get("calendar") or {}).get("events") or []:
        day = _day(event.get("day"))
        if day is None or day < today:
            continue
        rows.append({"day": event["day"], "days": (day - today).days, "name": event.get("name") or "",
                     "kind": event.get("kind") or "", "time": event.get("time") or "", "symbol": event.get("symbol") or "",
                     "tentative": "待确认" in (event.get("name") or "")})
    rows.sort(key=lambda r: (r["day"], r["time"]))
    return rows[:limit]


def build(state, today=None, now=None):
    """`now` (aware) drives the brief due-time and next-refresh hints; it defaults to the real clock unless a
    test pins `today`, in which case midday ET of that day is used."""
    if now is None and today is None:
        now = datetime.now(ET)
    today = today or _day(state.get("day")) or now.astimezone(ET).date()
    fresh = freshness(state, today, now)
    return {"freshness": fresh, "attention": attention(state, today, fresh), "upcoming": upcoming(state, today)}
