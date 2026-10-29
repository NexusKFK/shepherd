from __future__ import annotations

import hashlib
import json
import math
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def finite(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")), None
    except (OSError, ValueError) as e:
        return None, f"{Path(path).name}：{type(e).__name__}，来源不可用"


class Sources:
    def __init__(self, root=None):
        self.home = Path(root or os.environ.get("TRADING_SOURCE_HOME", Path.home()))
        self.holdings = self.home / "tools/holdings_local"
        self.digest = self.home / "tools/daily_digest"
        self.snapshot_path = self.holdings / "state.json"
        # Optional module files: one environment variable each, otherwise the same layout under the source home.
        self.macro_state = self._optional("SHEPHERD_MACRO_STATE", "tools/macro_panel/out/state.json")
        self.weekly_state = self._optional("SHEPHERD_WEEKLY_STATE", "tools/weekly_watch/out/state.json")
        self.paper_state = self._optional("SHEPHERD_PAPER_STATE", "tools/paper_book/out/state.json")
        self.option_plans = self._optional("SHEPHERD_OPTION_PLANS", "tools/option_plans")
        self.econf_inbox = self._optional("SHEPHERD_ECONF_INBOX", "tools/schwab_confirms/inbox")
        self.core_earnings = self._optional("SHEPHERD_CORE_EARNINGS", "tools/daily_digest/core_earnings.json")

    def _optional(self, env, relative):
        value = os.environ.get(env)
        return Path(value).expanduser() if value else self.home / relative

    def found(self):
        """Which optional module files exist, so first-run setup can say what it found."""
        bars = self.digest / "cache/addscore"
        return {"calendar": (self.digest / "calendar_data.json").exists(),
                "optionPlans": any(self.option_plans.glob("*.json")) if self.option_plans.is_dir() else False,
                "preparation": any(bars.glob("????-??-??.editorial.json")) if bars.is_dir() else False,
                "macro": self.macro_state.exists(), "weekly": self.weekly_state.exists(),
                "paper": self.paper_state.exists(), "econf": self.econf_inbox.is_dir()}

    def snapshot(self):
        raw, error = read_json(self.snapshot_path)
        if error or not isinstance(raw, dict) or not isinstance(raw.get("positions"), list) or not isinstance(raw.get("accounts"), list):
            return {"positions": [], "accounts": [], "nlv": None, "asof": "未读取", "day": None,
                    "status": "unavailable", "errors": [error or "持仓快照格式不正确"], "quotes": {}, "legacy_plans": []}
        asof = str(raw.get("asof", "时间未知"))
        match = re.search(r"收盘价 (\d{4}-\d{2}-\d{2})", asof)
        day = match.group(1) if match else None
        try:
            stale = not day or (datetime.now(ET).date() - date.fromisoformat(day)).days > 4
        except ValueError:
            day, stale = None, True
        positions, errors = [], []
        for i, row in enumerate(raw["positions"]):
            if not isinstance(row, dict):
                errors.append(f"持仓第 {i+1} 行损坏")
                continue
            p = dict(row)
            p["id"] = str(p.get("id", f"row-{i}"))
            p["qty"] = abs(p["qty"]) if finite(p.get("qty")) else None
            p["side"] = p.get("side", "short" if finite(row.get("qty")) and row["qty"] < 0 else "long")
            if p["side"] not in ("long", "short"):
                p["confirm"] = True
            p["mult"] = p.get("mult") if finite(p.get("mult")) and p["mult"] > 0 else (100 if p.get("kind") == "option" else 1)
            p["fingerprint"] = hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            p["source"] = "snapshot"
            if p.get("kind") == "option" and p.get("expiry") and p["expiry"] < datetime.now(ET).date().isoformat():
                p["confirm"] = True
                p["reconcile_note"] = "到期日已过，需核对平仓或行权结果"
            positions.append(p)
        accounts = [a for a in raw["accounts"] if isinstance(a, dict)]
        complete = bool(accounts) and len(accounts) == len(raw["accounts"]) and all(finite(a.get("nlv")) and a["nlv"] >= 0 for a in accounts)
        nlv = sum(a["nlv"] for a in accounts) if complete else None
        if nlv is None:
            errors.append("账户净值未齐全，风险占总净值比例暂不可用")
        return {"positions": positions, "accounts": accounts, "nlv": nlv, "asof": asof, "day": day,
                "source_fingerprint": hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                "status": "stale" if stale else "available", "errors": errors,
                "quotes": raw.get("quotes", {}), "legacy_plans": raw.get("plans", []),
                "benchmark_quote": raw.get("benchmarkQuote") if isinstance(raw.get("benchmarkQuote"), dict) else None,
                "realized": raw.get("realized") if isinstance(raw.get("realized"), dict) else None}

    def history(self):
        """只读已存在的 TradingView 日线包，不在 App 打开时发起行情请求。"""
        folder = self.digest / "cache/addscore"
        files = sorted(folder.glob("????-??-??.bundle.json"), reverse=True)
        if not files:
            return {"series": {}, "day": None, "errors": ["没有可用的日线行情包"]}
        raw, error = read_json(files[0])
        if error or not isinstance(raw, dict) or not isinstance(raw.get("series"), dict):
            return {"series": {}, "day": None, "errors": [error or "日线行情包格式不正确"]}
        if raw.get("price_basis") != "regular_split_adjusted":
            return {"series": {}, "day": None, "errors": ["行情价格口径无法识别"]}
        cutoff = raw.get("signal_day")
        try:
            date.fromisoformat(cutoff)
        except (TypeError, ValueError):
            return {"series": {}, "day": None, "errors": ["行情日期无法识别"]}
        out, errors = {}, []
        for key, item in raw["series"].items():
            sym = key.split(":")[-1]
            try:
                bars = item["daily"]["bars"]
                points, previous = [], ""
                for bar in bars:
                    day = datetime.fromtimestamp(bar["t"], timezone.utc).astimezone(ET).date().isoformat()
                    if day > cutoff:
                        continue
                    close = bar["c"]
                    if not finite(close) or close <= 0 or day <= previous:
                        raise ValueError("价格或日期不合法")
                    points.append({"day": day, "close": float(close)})
                    previous = day
                if points and points[-1]["day"] == cutoff:
                    if sym in out:
                        errors.append(f"{sym} 有重复市场代码，未合并")
                        out.pop(sym, None)
                    else:
                        out[sym] = points
            except (KeyError, TypeError, ValueError, OverflowError, OSError):
                errors.append(f"{sym} 日线不完整")
        return {"series": out, "day": cutoff, "errors": errors, "source": "TradingView · 已有日报日线",
                "basis": "常规时段、仅拆股调整；不含股息再投资", "collected_at": raw.get("collected_at")}

    def brief(self):
        files = sorted((self.digest / "cache/addscore").glob("????-??-??.editorial.json"), reverse=True)
        result = {"items": [], "day": None, "status": "unavailable", "error": "盘前摘要尚未生成"}
        if not files:
            return result
        raw, error = read_json(files[0])
        if error or not isinstance(raw, dict) or not isinstance(raw.get("sheet"), dict):
            result["error"] = error or "盘前摘要格式不正确"
            return result
        sheet = raw["sheet"]
        rows = sheet.get("rows", [])
        if not isinstance(rows, list):
            result["error"] = "盘前摘要行格式不正确"
            return result
        result.update(day=sheet.get("signal_day"), report_day=raw.get("report_day"),
                      reviewed_at=raw.get("reviewed_at"), status="available", error=raw.get("error"))
        result["items"] = [{"symbol": r.get("symbol", ""), "facts": r.get("facts", {}), "close": r.get("close")}
                           for r in rows if isinstance(r, dict) and not r.get("quiet")][:10]
        try:
            if (datetime.now(ET).date()-date.fromisoformat(result["day"])).days > 4:
                result["status"] = "stale"
                result["error"] = "盘前资料超过 4 天未更新，以下仅供历史参考"
        except (ValueError, TypeError):
            result["status"] = "stale"
            result["error"] = "盘前资料日期无法识别，不能视作今日资料"
        return result

    def _core_earnings(self, errors):
        """大科技名单（M7、博通、台积电、SpaceX 等）：{代码: 一行}。文件不存在就是没有这个名单，不算错；读坏了要说。"""
        if not self.core_earnings.exists():
            return {}
        raw, error = read_json(self.core_earnings)
        if error or not isinstance(raw, dict) or not isinstance(raw.get("symbols"), dict):
            errors.append("大科技财报名单读取失败：" + (error or "格式不正确"))
            return {}
        return {str(k).upper(): v for k, v in raw["symbols"].items() if isinstance(v, dict)}

    def calendar(self, plans, custom, snapshot):
        today = datetime.now(ET).date()
        lo, hi = today - timedelta(days=7), today + timedelta(days=90)
        events, errors = [], []
        raw, error = read_json(self.digest / "calendar_data.json")
        if error or not isinstance(raw, dict) or not isinstance(raw.get("events"), list):
            errors.append(error or "宏观日历格式不正确")
        else:
            dates = []
            for e in raw["events"]:
                try:
                    d = date.fromisoformat(e["date"])
                    dates.append(d)
                    if lo <= d <= hi:
                        events.append({"id": "macro-"+str(len(events)), "day": d.isoformat(), "name": e["name"],
                                       "time": e.get("time", ""), "kind": e.get("kind", "宏观"), "source": "现有宏观日历", "symbol": ""})
                except (ValueError, KeyError, TypeError):
                    errors.append("宏观日历存在无效记录")
            if not dates or max(dates) < hi:
                errors.append("宏观日历未完整覆盖未来 90 天，空白日期不代表没有事件")
        held = {}
        for p in snapshot["positions"]:
            if p.get("sym") and not p.get("display_only") and p.get("delta_status") != "combo":
                held.setdefault(str(p["sym"]).upper(), []).append(p)
        core = self._core_earnings(errors)
        earnings, error = read_json(self.digest / "cache/earnings.json")
        cache_dates = {}
        if not error and isinstance(earnings, dict):
            for sym, day in (earnings.get("data") or {}).items():
                try:
                    cache_dates[str(sym).upper()] = date.fromisoformat(day)
                except (ValueError, TypeError):
                    continue
        elif not core:
            errors.append("尚无财报日缓存，可手动登记已确认日期")
        far = today + timedelta(days=200)          # 大科技名单的财报看得更远，不受 90 天窗口限制
        for sym in sorted(set(cache_dates) | set(core)):
            row = core.get(sym)
            pick = pick_earnings(row, cache_dates.get(sym), today, lo)
            limit = far if row else hi
            if pick is None or pick["day"] > limit:
                if row:
                    errors.append(f"{sym} 财报日期缺失或已过期，名单文件里的日期需要重新导入")
                continue
            mine = held.get(sym)
            event = {"id": "earnings-"+sym, "day": pick["day"].isoformat(), "time": pick["time"], "kind": "财报",
                     "source": pick["source"], "symbol": sym, "name": sym+" 财报" if (mine or row) else sym+" 财报日期待确认"}
            if row:
                event.update(core=True, core_name=row.get("name") or sym, confirmed=pick["confirmed"])
            if mine:
                # 我持有的公司：写清持仓和期权有没有跨过这次财报
                event.update(held=True, held_note=held_note(mine, pick["day"]))
            if not (mine or row):
                event["source"] = "已有财报缓存 · 需核对公司公告"
            events.append(event)
        for p in snapshot["positions"]:
            expiry = p.get("expiry")
            try:
                if expiry and lo <= date.fromisoformat(expiry) <= hi:
                    events.append({"id": "expiry-"+p["id"], "day": expiry, "name": f"{p.get('sym')} · {p.get('label') or '期权到期'}",
                                   "kind": "到期", "symbol": p.get("sym"), "time": "", "source": "持仓快照"})
            except ValueError:
                errors.append(f"{p.get('sym')} 到期日无效")
        for p in plans:
            if p["status"] in ("closed", "cancelled"):
                continue
            for field, kind in (("review_day", "复核"), ("expiry", "到期")):
                if p.get(field) and lo.isoformat() <= p[field] <= hi.isoformat():
                    events.append({"id": p["id"]+field, "day": p[field], "name": p["title"], "kind": kind,
                                   "time": "", "symbol": p["symbol"], "source": "交易计划", "plan_id": p["id"]})
        events.extend(e for e in custom if lo.isoformat() <= e["day"] <= hi.isoformat())
        return {"events": sorted(events, key=lambda e: (e["day"], e.get("time", ""))), "errors": errors}


def pick_earnings(row, cache_day, today, lo):
    """一家公司这次该用哪个财报日：名单文件的日期没过期就用它（TradingView，带是否已公告和具体时间），
    过期了改用日报缓存里的下一次日期（雅虎财经预估）；两样都没有才用名单里刚过去的那天。返回字典或 None。"""
    overlay = None
    if row:
        try:
            overlay = date.fromisoformat(row.get("date"))
        except (ValueError, TypeError):
            overlay = None
    if overlay and overlay >= today:
        time = ""
        if row.get("time_known") and row.get("at"):
            try:
                time = datetime.fromisoformat(row["at"].replace("Z", "+00:00")).astimezone(ET).strftime("%H:%M")
            except ValueError:
                time = ""
        confirmed = bool(row.get("confirmed"))
        return {"day": overlay, "time": time, "confirmed": confirmed,
                "source": "公司已公告日期（TradingView）" if confirmed else "预估日期（TradingView），以公司公告为准"}
    if cache_day and cache_day >= lo:
        return {"day": cache_day, "time": "", "confirmed": False, "source": "预估日期（雅虎财经），以公司公告为准"}
    if overlay and overlay >= lo:
        return {"day": overlay, "time": "", "confirmed": bool(row.get("confirmed")), "source": "刚发布过的财报（名单文件）"}
    return None


def held_note(positions, earnings_day):
    """一句话写清我在这家公司持有什么；有期权在财报日当天或之后才到期的，单独提醒。"""
    parts, crosses = [], []
    for p in positions:
        qty = p.get("qty")
        if not finite(qty):
            continue
        if p.get("kind") == "option":
            parts.append(f"{p.get('label') or '期权'} {'卖出' if p.get('side') == 'short' else '买入'} {qty:g} 张")
            try:
                if date.fromisoformat(p.get("expiry")) >= earnings_day:
                    crosses.append(p["expiry"][5:])
            except (ValueError, TypeError):
                pass
        else:
            parts.append(f"{'空头' if p.get('side') == 'short' else '持有'}正股 {qty:g} 股")
    text = "；".join(parts) or "持仓数量未知"
    if crosses:
        text += f"。注意：期权 {'、'.join(sorted(set(crosses)))} 到期，这次财报在到期之前（含到期当天）"
    return text


def broker_key(name):
    return "schwab" if str(name).lower() in ("tos", "schwab", "thinkorswim", "charles schwab") else name


def _leg_claimable(leg):
    qty, cost = leg.get("qty"), leg.get("open")
    return (leg.get("side") in ("long", "short")
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) and finite(v) for v in (qty, cost))
            and qty > 0 and qty == int(qty))


def _structure_row(plan, legs):
    """已入账多腿计划的组合汇总行：仅展示，风险仍按各腿计量（与组合汇总同一约定）。"""
    unit = 0
    for leg in legs:
        unit = math.gcd(unit, int(leg["qty"]))
    sign = lambda leg: 1 if leg.get("side") == "long" else -1
    ratio = [(int(leg["qty"]) // unit) * sign(leg) for leg in legs]
    marks = [leg.get("mark") for leg in legs]
    net_open = sum(r * leg["open"] for r, leg in zip(ratio, legs))
    net_mark = (sum(r * leg["mark"] for r, leg in zip(ratio, legs))
                if all(finite(m) for m in marks) else None)
    first = legs[0]
    row = {"id": "planstruct-" + plan["id"], "plan_id": plan["id"], "sym": plan["symbol"],
           "label": plan["title"], "kind": "option", "side": "long" if net_open >= 0 else "short",
           "qty": unit, "mult": first.get("mult") or plan.get("mult") or 100,
           "open": plan["avg_entry"], "mark": net_mark, "broker": plan["broker"], "expiry": plan["expiry"],
           "delta_status": "combo", "component_ids": [leg["id"] for leg in legs],
           "source": "derived", "confirm": False}
    for key in ("account_id", "account", "broker_account", "underlying_price", "underlying_price_asof"):
        if first.get(key) is not None:
            row[key] = first[key]
    if finite(plan.get("stop")):
        row["trigs"] = [{"kind": "止损", "basis": "und", "level": plan["stop"], "dir": "<=" if plan["side"] == "long" else ">=",
                         "text": f"计划失效价 {plan['stop']:g}", "level0": plan["stop"], "moves": [], "source": "plan"}]
    return row


def _plan_structures(snapshot, plans, bound):
    """同标的、同到期、已入账的多腿期权计划自动认领快照腿（同日到期基本就是一个策略）。

    认领成功返回组合汇总行，占位行随之剔除；同一到期挂两张计划分不清腿、或腿不足两条/缺多空两向时
    不认领，保持原占位行与待核对提醒。真腿不带 plan_id——期权演算靠这个字段过滤占位行。
    """
    claims = {}
    for p in plans:
        if p["status"] == "holding" and p.get("kind") == "option" and not p.get("snapshot_id") and p.get("expiry"):
            key = (str(p["symbol"]).upper(), broker_key(p.get("broker")), p["expiry"])
            claims.setdefault(key, []).append(p)
    summaries, linked = [], set()
    for (sym, broker, expiry), group in claims.items():
        if len(group) != 1:
            continue
        legs = [s for s in snapshot["positions"]
                if s["id"] not in bound and s.get("kind") == "option" and s.get("delta_status") != "combo"
                and str(s.get("sym") or "").upper() == sym
                and broker_key(s.get("broker")) == broker and s.get("expiry") == expiry]
        if (len(legs) < 2 or len({s.get("side") for s in legs}) < 2
                or not all(_leg_claimable(s) for s in legs)):
            continue
        plan = group[0]
        linked.add(plan["id"])
        summaries.append(_structure_row(plan, legs))
    return summaries, linked


def merged_positions(snapshot, plans):
    """显式关联照旧接管快照条目；此外同标的同到期的已入账多腿计划自动认领快照腿并剔除占位行，
    其余情形不按标的自动吞并。"""
    bound = {p.get("snapshot_id") for p in plans if p.get("snapshot_id")}
    structures, linked = _plan_structures(snapshot, plans, bound)
    result = [dict(p) for p in snapshot["positions"] if p["id"] not in bound]
    result.extend(structures)
    conflicts = []
    by_id = {p["id"]: p for p in snapshot["positions"]}
    for p in plans:
        if p["id"] in linked:
            continue
        source = by_id.get(p.get("snapshot_id"), {})
        if p["status"] != "holding":
            if p["status"] == "closed" and source and source.get("qty") and source.get("fingerprint") != p.get("snapshot_fingerprint"):
                row = dict(source, confirm=True)
                result.append(row)
                conflicts.append(f"{p['symbol']}：本机已平仓，但新快照仍有余额，请核对")
            continue
        q = p["open_qty"]
        item = {"id": "plan-"+p["id"], "plan_id": p["id"], "sym": p["symbol"], "label": p["title"],
                "kind": p["kind"], "side": p["side"], "qty": q, "mult": p["mult"], "open": p["avg_entry"],
                "broker": p["broker"], "expiry": p["expiry"], "mark": source.get("mark"), "source": "local", "confirm": False}
        for key in ("contract", "broker_account", "snapshot_id"):
            if p.get(key):
                item[key] = p[key]
        if item["mark"] is None and p["kind"] == "stock" and finite((snapshot.get("quotes") or {}).get(p["symbol"])):
            item["mark"] = snapshot["quotes"][p["symbol"]]
        # A plan held only in this ledger still has its own stop: the plan's invalidation price.
        if source.get("trigs"):
            item["trigs"] = source["trigs"]
        elif finite(p.get("stop")):
            item["trigs"] = [{"kind": "止损", "basis": "und", "level": p["stop"], "dir": "<=" if p["side"] == "long" else ">=",
                              "text": f"计划失效价 {p['stop']:g}", "level0": p["stop"], "moves": [], "source": "plan"}]
        for key in ("delta", "delta_asof", "delta_source", "underlying_price", "underlying_price_asof", "underlying_price_source", "price_asof", "mark_asof", "mark_source", "mark_stale", "mark_missing_reason", "currency", "account_id", "account", "strike", "call_put", "right", "sector", "theme", "factor", "group"):
            if source.get(key) is not None:
                item[key] = source[key]
        if p.get("snapshot_id") and not source:
            item["confirm"] = True
            conflicts.append(f"{p['symbol']}：关联持仓已不在新快照中，请核对是否已在券商平仓")
        elif p.get("snapshot_id") and source and source.get("fingerprint") != p.get("snapshot_fingerprint") and source.get("qty") != q:
            item["confirm"] = True
            conflicts.append(f"{p['symbol']}：新快照数量与本机成交记录不一致，请核对")
        if not p.get("snapshot_id") and any(s.get("sym") == p["symbol"] and broker_key(s.get("broker")) == broker_key(p["broker"]) and s.get("kind") == p["kind"] for s in snapshot["positions"]):
            item["confirm"] = True
            conflicts.append(f"{p['symbol']}：快照与本机计划均有持仓，尚未关联；风险暂不计入该计划")
        result.append(item)
    return result, conflicts
