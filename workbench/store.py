from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager, nullcontext
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .discipline import execution_discipline


class Invalid(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def number(value, label, minimum=0, optional=False):
    if value in (None, "") and optional:
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise Invalid(f"请填写{label}") from None
    if isinstance(value, bool) or not math.isfinite(n) or n < minimum:
        raise Invalid(f"{label}必须是大于等于 {minimum:g} 的有限数值")
    return n


def text(value, label, limit=4000, required=False):
    s = str(value or "").strip()
    if len(s) > limit or (required and not s):
        raise Invalid(f"{label}不能为空且最多 {limit} 字" if required else f"{label}最多 {limit} 字")
    return s


def et_today():
    """The US trading date. Fills, balances and risk notes are dated in ET; the Mac's own date
    (anywhere in Asia, say) can already be "tomorrow" while the US session is still open."""
    return datetime.now(ZoneInfo("America/New_York")).date()


def iso_day(value, optional=False):
    if not value and optional:
        return ""
    try:
        return date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError):
        raise Invalid("日期格式应为 YYYY-MM-DD") from None


def size_plan(p):
    """风险百分比在输入端为百分数；期权只使用明确的最大亏损。"""
    nlv = number(p.get("nlv"), "账户净值", 0.01)
    pct = number(p.get("risk_pct"), "风险比例", 0.001)
    if pct > 100:
        raise Invalid("风险比例不能超过 100%")
    entry = number(p.get("entry"), "计划入场价", 0.000001)
    side, kind = p.get("side", "long"), p.get("kind", "stock")
    if side not in ("long", "short") or kind not in ("stock", "option"):
        raise Invalid("不支持的交易方向或资产类型")
    mult = 1 if kind == "stock" else number(p.get("mult", 100), "合约乘数", 1)
    if kind == "stock":
        stop = number(p.get("stop"), "失效价", 0.000001)
        if (side == "long" and stop >= entry) or (side == "short" and stop <= entry):
            raise Invalid("做多的失效价应低于入场价；做空应高于入场价")
        unit = abs(entry - stop)
        basis = "入场价到失效价的距离；跳空和滑点可能使实际亏损更高"
    elif side == "long":
        unit = entry * mult
        basis = "买方期权按全部权利金损失定仓"
    else:
        unit = number(p.get("max_loss"), "每组最大亏损", 0.01)
        basis = "按你填写的每组最大亏损；必须是已确认的定义风险结构"
    budget = nlv * pct / 100
    qty = math.floor((budget + 1e-9) / unit)
    warnings = []
    if pct > 3:
        warnings.append("单笔风险比例超过 3%")
    if kind == "stock" and qty * entry > nlv:
        warnings.append("名义仓位超过填写的账户净值")
    if not qty:
        warnings.append("当前风险预算不足以建立 1 个单位")
    return {"budget": round(budget, 2), "unit_risk": unit, "qty": qty,
            "planned_risk": round(qty * unit, 2), "notional": round(qty * entry * mult, 2),
            "basis": basis, "warnings": warnings}


def clean_plan(raw):
    p = {k: text(raw.get(k), k, required=k in ("symbol", "title"), limit=180 if k in ("title", "symbol") else 4000)
         for k in ("symbol", "title", "thesis", "invalidation", "exit_rule", "broker", "review_note", "contract")}
    p["symbol"] = p["symbol"].upper()
    if not re.fullmatch(r"[A-Z0-9.^:/_-]{1,32}", p["symbol"]):
        raise Invalid("标的代码格式不正确")
    p["kind"], p["side"] = raw.get("kind", "stock"), raw.get("side", "long")
    if p["kind"] not in ("stock", "option") or p["side"] not in ("long", "short"):
        raise Invalid("不支持的资产类型或方向")
    for k in ("nlv", "risk_pct", "entry", "stop", "max_loss"):
        p[k] = number(raw.get(k), k, 0, optional=True)
    p["planned_qty"] = number(raw.get("planned_qty"), "完整开仓计划数量", 0, optional=True)
    if p["planned_qty"] is not None and not p["planned_qty"].is_integer():
        raise Invalid("完整开仓计划数量必须是整数；分批成交另行登记")
    p["estimated_costs"] = number(raw.get("estimated_costs"), "预估费用与滑点预留", 0, optional=True) or 0
    p["mult"] = 1 if p["kind"] == "stock" else number(raw.get("mult", 100), "合约乘数", 1)
    p["review_day"] = iso_day(raw.get("review_day"), optional=True)
    p["expiry"] = iso_day(raw.get("expiry"), optional=True)
    p["status"] = "draft"
    return p


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / "trading.sqlite3"
        with self.connection() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY, body TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS fills(id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, plan_id TEXT NOT NULL REFERENCES plans(id), body TEXT NOT NULL, created_at TEXT NOT NULL, voided INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL, ref TEXT, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS source_snapshots(id TEXT PRIMARY KEY, observed_at TEXT NOT NULL, source TEXT NOT NULL, body TEXT NOT NULL);
            """)
            if "voided" not in {r["name"] for r in c.execute("PRAGMA table_info(fills)")}:
                c.execute("ALTER TABLE fills ADD COLUMN voided INTEGER NOT NULL DEFAULT 0")
            # v1 eConf imports accidentally used the opening-balance flag. These
            # are actual executions and must remain correctable like other fills.
            for row in c.execute("SELECT f.id, f.body FROM fills f JOIN plans p ON p.id=f.plan_id WHERE json_extract(p.body,'$.source')='econf'").fetchall():
                body = json.loads(row["body"])
                if body.pop("opening_balance", False):
                    c.execute("UPDATE fills SET body=? WHERE id=?", (json.dumps(body, ensure_ascii=False), row["id"]))
                    self.audit(c, "econf_execution_flag_corrected", row["id"], {})
        os.chmod(self.path, 0o600)

    @contextmanager
    def connection(self):
        c = sqlite3.connect(self.path, timeout=15)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        try:
            with c:
                yield c
        finally:
            c.close()

    @staticmethod
    def audit(c, kind, ref, body):
        c.execute("INSERT INTO audit(at,kind,ref,body) VALUES(?,?,?,?)", (now(), kind, ref, json.dumps(body, ensure_ascii=False)))

    @staticmethod
    def _risk_policy(c):
        row = c.execute("SELECT body FROM kv WHERE key='risk:policy'").fetchone()
        policy = {"risk_pct": 2.0, "max_risk_pct": 4.0, "min_utilization": .9,
                  "nlv": None, "version": 0}
        if row:
            policy.update(json.loads(row[0]))
        return policy

    def risk_policy(self):
        with self.connection() as c:
            return self._risk_policy(c)

    def save_risk_policy(self, raw):
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            old = self._risk_policy(c)
            if type(raw.get("version")) is not int or raw["version"] != old["version"]:
                raise Invalid("全局风险设置已更新，请刷新后重试")
            nlv = number(raw.get("nlv", old["nlv"]), "组合净值", .01)
            pct = number(raw.get("risk_pct", old["risk_pct"]), "标准风险比例", .001)
            if pct > 4:
                raise Invalid("标准单笔风险比例不能超过 4%；只能在全局设置中调整")
            policy = {"risk_pct": pct, "max_risk_pct": 4.0, "min_utilization": .9,
                      "nlv": nlv, "version": old["version"] + 1, "updated_at": now()}
            c.execute("INSERT INTO kv VALUES('risk:policy',?) ON CONFLICT(key) DO UPDATE SET body=excluded.body",
                      (json.dumps(policy, ensure_ascii=False),))
            self.audit(c, "risk_policy_saved", "risk:policy", {"before": old, "after": policy})
            return policy

    @staticmethod
    def _size(raw, policy):
        result = {"qty": 0, "target_qty": 0, "planned_qty": None, "budget": None,
                  "unit_risk": None, "planned_risk": None, "notional": None,
                  "cost_reserve": None, "price_risk": None,
                  "utilization": None, "eligible": False, "basis": "",
                  "warnings": [], "blockers": [], "policy": dict(policy)}
        if policy["nlv"] is None:
            result["blockers"].append("请先在全局风险设置中确认组合净值，再计算标准 1R")
            return result
        try:
            inputs = dict(raw, nlv=policy["nlv"], risk_pct=policy["risk_pct"])
            base = size_plan(inputs)
            # Never round the allowed budget upward or round an integer lot up.
            budget = math.floor(policy["nlv"] * policy["risk_pct"] + 1e-9) / 100
            reserve = number(raw.get("estimated_costs"), "预估费用与滑点预留", 0, optional=True) or 0
            target = max(0, math.floor((budget - reserve + 1e-9) / base["unit_risk"]))
            requested = number(raw.get("planned_qty"), "完整开仓计划数量", 0, optional=True)
            if requested is not None and not requested.is_integer():
                raise Invalid("完整开仓计划数量必须是整数；分批成交另行登记")
            planned = target if requested is None else int(requested)
            price_risk = planned * base["unit_risk"]
            risk = price_risk + reserve
            mult = 1 if raw.get("kind", "stock") == "stock" else float(raw.get("mult", 100))
            notional = planned * float(raw["entry"]) * mult
            result.update(qty=target, target_qty=target, planned_qty=planned, budget=budget,
                          unit_risk=base["unit_risk"], planned_risk=round(risk, 2),
                          cost_reserve=reserve, price_risk=round(price_risk, 2),
                          notional=round(notional, 2), basis=base["basis"],
                          utilization=risk / budget if budget else 0)
            if reserve > budget + 1e-8:
                result["blockers"].append("预估费用与滑点预留已超过标准 1R，请核对成本或保持观察")
            if target < 1:
                result["blockers"].append("标准 1R 扣除费用与滑点预留后不足以建立 1 个单位，保持观察")
            elif target * base["unit_risk"] + reserve < budget * policy["min_utilization"] - 1e-8:
                result["blockers"].append("整数单位与标准 1R 不适配，向下取整后不足 90%；保持观察，不向上超预算")
            if risk > budget + 1e-8:
                result["blockers"].append("完整开仓计划超过 1R，不能进入待执行")
            elif risk < budget * policy["min_utilization"] - 1e-8:
                result["blockers"].append("完整开仓计划不足 0.9R，保持观察；预先分批仍应填写完整目标数量")
            if raw.get("kind", "stock") == "stock" and notional > policy["nlv"]:
                result["warnings"].append("名义仓位超过组合净值；不要为了凑满 1R 强行使用杠杆，应核对可用资金或保持观察")
            if policy["risk_pct"] > 3:
                result["warnings"].append("全局标准单笔风险超过 3%，请留意多笔交易同时失效的损失")
            if not reserve:
                result["warnings"].append("定仓未预留交易费用与滑点；成交费用计入实际执行风险，止损不保证成交价格")
            else:
                result["warnings"].append("费用与滑点预留为人工估计；实际成交价格与费用用于执行偏差，止损不保证成交价格")
        except (Invalid, OverflowError) as exc:
            result["blockers"].append(str(exc) if isinstance(exc, Invalid) else "定仓数值过大，请核对输入")
        result["eligible"] = not result["blockers"]
        return result

    def size(self, raw):
        with self.connection() as c:
            result = self._size(raw, self._risk_policy(c))
            warnings, blockers = self._plan_conflicts(c, raw)
            result["warnings"].extend(warnings)
            result["blockers"].extend(blockers)
            result["eligible"] = not result["blockers"]
            return result

    @staticmethod
    def _plan_conflicts(c, plan):
        """Exact duplicates share an operation; other contracts need review.

        A symbol alone is not an independent options-strategy identifier.
        Do not invent one from an arbitrary date or similar-looking contract.
        """
        def broker(value):
            value = str(value or "").strip().upper()
            return "SCHWAB" if value in ("TOS", "THINKORSWIM", "SCHWAB") else value

        def contract(value):
            return " ".join(str(value or "").upper().split())

        warnings, blockers = [], []
        symbol = str(plan.get("symbol") or "").strip().upper()
        if not symbol:
            return warnings, blockers
        for row in c.execute("SELECT id,body FROM plans"):
            other = json.loads(row["body"])
            if row["id"] == plan.get("id") or other.get("status") not in ("ready", "holding") or other.get("symbol") != symbol:
                continue
            same_instrument = (other.get("kind") == plan.get("kind", "stock") and
                               other.get("side") == plan.get("side", "long") and
                               (plan.get("kind", "stock") == "stock" or
                                (contract(other.get("contract")) == contract(plan.get("contract")) and
                                 other.get("expiry") == plan.get("expiry"))))
            if same_instrument and broker(other.get("broker")) == broker(plan.get("broker")):
                blockers.append(f"同账户同向证券已有未结束计划「{other.get('title', symbol)}」，请在原计划登记分批或加仓，共用原始 1R")
            else:
                warnings.append(f"{symbol} 已有未结束交易「{other.get('title', symbol)}」；核对是否同一交易的跨账户／换腿操作，不能借新计划重置 1R")
        return warnings, blockers

    def _get(self, c, id):
        r = c.execute("SELECT * FROM plans WHERE id=?", (id,)).fetchone()
        if not r:
            raise Invalid("找不到这笔交易计划")
        p = json.loads(r["body"])
        p.update(id=id, version=r["version"], created_at=r["created_at"], updated_at=r["updated_at"])
        p["fills"] = [dict(json.loads(f["body"]), id=f["id"], request_id=f["request_id"], recorded_at=f["created_at"], voided=bool(f["voided"])) for f in c.execute("SELECT * FROM fills WHERE plan_id=? ORDER BY rowid", (id,))]
        balance, cost, realized = 0, 0, 0
        for f in p["fills"]:
            if f["voided"]:
                continue
            q, price = f["qty"], f["price"]
            if f["action"] == "entry":
                cost += q * price
                balance += q
            else:
                avg = cost / balance
                realized += (price - avg) * q * p["mult"] * (1 if p["side"] == "long" else -1)
                cost -= avg * q
                balance -= q
            realized -= f["fee"]
        p.update(open_qty=round(balance, 8), avg_entry=cost / balance if balance > 1e-8 else None,
                 realized_pnl=round(realized, 2))
        p["discipline"] = execution_discipline(p)
        p["execution_warnings"] = p["discipline"]["warnings"]
        return p

    def plans(self):
        with self.connection() as c:
            return [self._get(c, r["id"]) for r in c.execute("SELECT id FROM plans ORDER BY updated_at DESC")]

    def save_plan(self, raw):
        p = clean_plan(raw)
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            policy = self._risk_policy(c)
            p.update(nlv=policy["nlv"], risk_pct=policy["risk_pct"])
            id = raw.get("id") or uuid.uuid4().hex
            if raw.get("id"):
                old = self._get(c, id)
                if raw.get("version") != old["version"]:
                    raise Invalid("计划已在另一窗口修改，请刷新后重试")
                if old["fills"] or old["status"] in ("closed", "cancelled"):
                    raise Invalid("已有成交的计划保留原始依据；请在交易记录中补充复盘")
                if old["status"] == "ready":
                    raise Invalid("待执行计划已冻结原始 1R 与依据；需要改变计划请取消后另建")
                self.audit(c, "plan_edited", id, {"before": old, "after": p})
                c.execute("UPDATE plans SET body=?, version=version+1, updated_at=? WHERE id=?", (json.dumps(p, ensure_ascii=False), now(), id))
            else:
                c.execute("INSERT INTO plans(id,body,created_at,updated_at) VALUES(?,?,?,?)", (id, json.dumps(p, ensure_ascii=False), now(), now()))
                self.audit(c, "plan_created", id, p)
            return self._get(c, id)

    def transition(self, id, raw):
        status = raw.get("status")
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            p = self._get(c, id)
            if p["version"] != raw.get("version"):
                raise Invalid("计划已更新，请刷新后重试")
            if status == "ready" and p["status"] == "draft":
                if not p["thesis"] or not p["invalidation"] or not p["exit_rule"] or not p["broker"]:
                    raise Invalid("请补齐交易理由、失效条件、退出安排和账户")
                if p["kind"] == "option" and (not p["expiry"] or not p["contract"]):
                    raise Invalid("期权计划需要到期日与完整合约／结构说明")
                policy = self._risk_policy(c)
                sizing = self._size(p, policy)
                warnings, blockers = self._plan_conflicts(c, p)
                sizing["warnings"].extend(warnings)
                sizing["blockers"].extend(blockers)
                sizing["eligible"] = not sizing["blockers"]
                if not sizing["eligible"]:
                    raise Invalid("；".join(sizing["blockers"]))
                sizing.update(schema="fixed-r/1", frozen_at=now())
                p.update(nlv=policy["nlv"], risk_pct=policy["risk_pct"], planned_qty=sizing["planned_qty"])
                p["sizing"] = sizing
            elif status == "cancelled" and p["status"] in ("draft", "ready"):
                pass
            else:
                raise Invalid("当前状态不能执行此操作")
            p["status"] = status
            self._update(c, p)
            self.audit(c, "status_changed", id, {"status": status})
            return self._get(c, id)

    def _update(self, c, p):
        body = {k: v for k, v in p.items() if k not in ("id", "version", "created_at", "updated_at", "fills", "open_qty", "avg_entry", "realized_pnl", "execution_warnings", "discipline")}
        c.execute("UPDATE plans SET body=?,version=version+1,updated_at=? WHERE id=?", (json.dumps(body, ensure_ascii=False), now(), p["id"]))

    def fill(self, id, raw, *, connection=None):
        rid = text(raw.get("request_id"), "成交请求编号", 100, required=True)
        f = {"action": raw.get("action"), "qty": number(raw.get("qty"), "成交数量", 0.000001),
             "price": number(raw.get("price"), "成交价", 0), "fee": number(raw.get("fee", 0), "费用", 0),
             "day": iso_day(raw.get("day")), "note": text(raw.get("note"), "成交说明")}
        if f["action"] not in ("entry", "exit"):
            raise Invalid("成交类型不正确")
        if f["day"] > et_today().isoformat():
            raise Invalid("不能把未来日期登记为已成交")
        with (nullcontext(connection) if connection is not None else self.connection()) as c:
            if connection is None:
                c.execute("BEGIN IMMEDIATE")
            prior = c.execute("SELECT * FROM fills WHERE request_id=?", (rid,)).fetchone()
            if prior:
                if prior["plan_id"] != id or json.loads(prior["body"]) != f:
                    raise Invalid("重复请求与已保存的成交不一致")
                return self._get(c, id)
            p = self._get(c, id)
            if p["version"] != raw.get("version"):
                raise Invalid("交易记录已更新，请刷新后重试")
            if p["kind"] == "option" and not f["qty"].is_integer():
                raise Invalid("期权成交数量必须是整数")
            active_fills = [x for x in p["fills"] if not x["voided"]]
            if active_fills and f["day"] < max(x["day"] for x in active_fills):
                raise Invalid("成交日期不能早于这笔交易已登记的成交")
            if f["action"] == "entry":
                if f["price"] <= 0:
                    raise Invalid("入场成交价必须大于零")
                if p["status"] not in ("ready", "holding"):
                    raise Invalid("请先完成计划和定仓，再登记入场成交")
                # 真实成交不拦截超预算事实，超出时单独告警，不能伪装没有成交。
                p["status"] = "holding"
            else:
                if p["status"] != "holding" or f["qty"] > p["open_qty"] + 1e-8:
                    raise Invalid("退出数量不能超过本计划剩余持仓")
                p["status"] = "closed" if abs(f["qty"] - p["open_qty"]) < 1e-8 else "holding"
            c.execute("INSERT INTO fills(id,request_id,plan_id,body,created_at) VALUES(?,?,?,?,?)", (uuid.uuid4().hex, rid, id, json.dumps(f, ensure_ascii=False), now()))
            self._update(c, p)
            self.audit(c, "fill_recorded", id, f)
            return self._get(c, id)

    def void_fill(self, id, raw):
        reason = text(raw.get("reason"), "更正原因", 2000, required=True)
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            p = self._get(c, id)
            if p["version"] != raw.get("version"):
                raise Invalid("交易记录已更新，请刷新后重试")
            active = [f for f in p["fills"] if not f["voided"]]
            if not active or active[-1]["id"] != raw.get("fill_id") or active[-1].get("opening_balance"):
                raise Invalid("只能撤销最近一条尚未更正的成交，期初余额需要单独核对")
            f = active[-1]
            c.execute("UPDATE fills SET voided=1 WHERE id=?", (f["id"],))
            updated = self._get(c, id)
            updated["status"] = "holding" if updated["open_qty"] > 1e-8 else (("cancelled" if p.get("source") == "econf" else "ready") if not [x for x in updated["fills"] if not x["voided"]] else "closed")
            self._update(c, updated)
            self.audit(c, "fill_voided", id, {"fill_id": f["id"], "reason": reason, "original": f})
            return self._get(c, id)

    def review(self, id, raw):
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            p = self._get(c, id)
            if p["version"] != raw.get("version"):
                raise Invalid("记录已更新，请刷新后重试")
            p["review_note"] = text(raw.get("note"), "复盘", 8000, required=True)
            self._update(c, p)
            self.audit(c, "review_saved", id, {"note": p["review_note"]})
            return self._get(c, id)

    def adopt(self, position, raw, asof):
        """人工确认的期初余额，不把导入伪装为新下单。"""
        q = number(raw.get("qty"), "已核对持仓数量", 0.000001)
        price = number(raw.get("open"), "已核对平均成本", 0.000001)
        if iso_day(raw.get("day")) > et_today().isoformat():
            raise Invalid("余额核对日期不能在未来")
        p = clean_plan({"symbol": position.get("sym"), "title": position.get("label") or position.get("sym"),
                        "kind": position.get("kind"), "side": position.get("side"), "mult": position.get("mult"),
                        "broker": position.get("broker"), "expiry": position.get("expiry"), "contract": position.get("label"),
                        "thesis": raw.get("thesis", "从原持仓快照接入"), "invalidation": raw.get("invalidation", ""),
                        "exit_rule": raw.get("exit_rule", ""), "entry": price})
        if p["kind"] == "option" and not q.is_integer():
            raise Invalid("期权持仓数量必须是整数")
        p.update(status="holding", snapshot_id=position["id"], snapshot_fingerprint=position["fingerprint"],
                 imported_rules=position.get("trigs", []), imported_asof=asof)
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            for r in c.execute("SELECT body FROM plans"):
                if json.loads(r[0]).get("snapshot_id") == position["id"]:
                    raise Invalid("此持仓已经接入，请在持仓记录中管理")
            id = uuid.uuid4().hex
            c.execute("INSERT INTO plans(id,body,created_at,updated_at) VALUES(?,?,?,?)", (id, json.dumps(p, ensure_ascii=False), now(), now()))
            f = {"action": "entry", "qty": q, "price": price, "fee": 0, "day": iso_day(raw.get("day")),
                 "note": "接入已核对的期初持仓余额；不是新成交", "opening_balance": True}
            c.execute("INSERT INTO fills(id,request_id,plan_id,body,created_at) VALUES(?,?,?,?,?)", (uuid.uuid4().hex, "opening-"+id, id, json.dumps(f, ensure_ascii=False), now()))
            self.audit(c, "opening_balance", id, {"position": position, "asof": asof, "confirmed": f})
            return self._get(c, id)

    def get_plan(self, plan_id):
        with self.connection() as c:
            return self._get(c, plan_id)

    def econf_open(self, raw, *, connection=None):
        """券商确认单导入：确认单是已成事实，直接登记为持仓计划与入场成交，
        不走定仓校验、不伪装成新下单；判据字段允许留空，事后在复盘里补。"""
        rid = text(raw.get("request_id"), "确认单防重编号", 120, required=True)
        symbol = text(raw.get("symbol"), "标的代码", 32, required=True).upper()
        if not re.fullmatch(r"[A-Z0-9.^:/_-]{1,32}", symbol):
            raise Invalid("标的代码格式不正确")
        kind, side = raw.get("kind", "option"), raw.get("side", "long")
        if kind not in ("stock", "option") or side not in ("long", "short"):
            raise Invalid("不支持的资产类型或方向")
        mult = 1 if kind == "stock" else number(raw.get("mult", 100), "合约乘数", 1)
        qty = number(raw.get("qty"), "成交数量", 0.000001)
        if kind == "option" and not qty.is_integer():
            raise Invalid("期权成交数量必须是整数")
        price = number(raw.get("price"), "成交价", 0.000001)
        fee = number(raw.get("fee", 0), "费用", 0)
        day = iso_day(raw.get("day"))
        if day > et_today().isoformat():
            raise Invalid("不能把未来日期登记为已成交")
        p = {"symbol": symbol, "title": text(raw.get("title"), "标题", 180, required=True),
             "thesis": text(raw.get("thesis"), "交易理由", 4000) or f"从券商确认单 eConf#{raw.get('conf')} 导入，判据待补",
             "invalidation": text(raw.get("invalidation"), "失效条件", 4000),
             "exit_rule": text(raw.get("exit_rule"), "退出安排", 4000),
             "broker": text(raw.get("broker"), "账户", 60) or "Schwab",
             "review_note": "", "contract": text(raw.get("contract"), "合约", 180),
             "kind": kind, "side": side, "mult": mult,
             "nlv": raw.get("nlv"), "risk_pct": None, "entry": price, "stop": None, "max_loss": None,
             "review_day": iso_day(raw.get("review_day"), optional=True),
             "expiry": iso_day(raw.get("expiry"), optional=True), "status": "holding",
             "source": "econf", "conf": text(raw.get("conf"), "单号", 40),
             "broker_account": text(raw.get("broker_account"), "券商账户标识", 60)}
        f = {"action": "entry", "qty": qty, "price": price, "fee": fee, "day": day,
             "note": text(raw.get("note"), "来源说明", 400) or "券商确认单导入"}
        with (nullcontext(connection) if connection is not None else self.connection()) as c:
            if connection is None:
                c.execute("BEGIN IMMEDIATE")
            prior = c.execute("SELECT * FROM fills WHERE request_id=?", (rid,)).fetchone()
            if prior:
                raise Invalid("这条确认单成交已经登记过")
            id = uuid.uuid4().hex
            c.execute("INSERT INTO plans(id,body,created_at,updated_at) VALUES(?,?,?,?)", (id, json.dumps(p, ensure_ascii=False), now(), now()))
            c.execute("INSERT INTO fills(id,request_id,plan_id,body,created_at) VALUES(?,?,?,?,?)", (uuid.uuid4().hex, rid, id, json.dumps(f, ensure_ascii=False), now()))
            self.audit(c, "econf_opened", id, {"plan": p, "fill": f})
            return self._get(c, id)

    def get(self, key, default=None):
        with self.connection() as c:
            row = c.execute("SELECT body FROM kv WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def put(self, key, data):
        with self.connection() as c:
            c.execute("INSERT INTO kv VALUES(?,?) ON CONFLICT(key) DO UPDATE SET body=excluded.body", (key, json.dumps(data, ensure_ascii=False)))
            self.audit(c, "saved", key, data)

    def events(self):
        with self.connection() as c:
            return [dict(json.loads(r["body"]), id=r["id"]) for r in c.execute("SELECT * FROM events")]

    def add_event(self, raw):
        e = {"day": iso_day(raw.get("day")), "name": text(raw.get("name"), "事件名称", 180, True),
             "symbol": text(raw.get("symbol"), "关联标的", 32).upper(), "time": text(raw.get("time"), "时间", 40),
             "kind": "自定义", "source": "手工登记", "created_at": now()}
        id = uuid.uuid4().hex
        with self.connection() as c:
            c.execute("INSERT INTO events VALUES(?,?)", (id, json.dumps(e, ensure_ascii=False)))
            self.audit(c, "event_created", id, e)
        return dict(e, id=id)

    def export(self):
        with self.connection() as c:
            return {"schema": "shepherd-trading-backup/1", "exported_at": now(),
                    "tables": {t: [dict(r) for r in c.execute(f"SELECT * FROM {t}")] for t in ("plans", "fills", "audit", "kv", "events", "source_snapshots")}}

    def retain_snapshot(self, snapshot, source):
        """Keep every distinct valid source version; never create ledger fills."""
        if snapshot.get("status") == "unavailable":
            return
        import hashlib
        body = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
        fingerprint = snapshot.get("source_fingerprint") or hashlib.sha256(body.encode()).hexdigest()
        with self.connection() as c:
            c.execute("INSERT OR IGNORE INTO source_snapshots VALUES(?,?,?,?)", (fingerprint, now(), str(source), body))

    def snapshot_history(self):
        with self.connection() as c:
            rows = c.execute("SELECT * FROM source_snapshots ORDER BY rowid DESC").fetchall()
        return [dict(id=r["id"], observed_at=r["observed_at"], source=r["source"],
                     day=(s := json.loads(r["body"])).get("day"), asof=s.get("asof"),
                     positions=len(s.get("positions", []))) for r in rows]

    def saved_snapshot(self, snapshot_id=None):
        with self.connection() as c:
            row = (c.execute("SELECT body FROM source_snapshots WHERE id=?", (snapshot_id,)).fetchone()
                   if snapshot_id else c.execute("SELECT body FROM source_snapshots ORDER BY rowid DESC LIMIT 1").fetchone())
        return json.loads(row[0]) if row else None
