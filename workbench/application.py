from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

from .risk import calculate
from .sources import ET, Sources, merged_positions
from .store import Invalid, Store, iso_day, now, text, number
from .econf import EconfBox
from .context import enrich_positions, operation_positions, supplemental_history
from .exposure import FACTOR_BUCKETS, HEAT_CAP_R, analyze_exposure, factor_clusters, market_scenario, portfolio_heat, standard_r
from . import macro as macro_panel
from . import paper as paper_book
from . import weekly_watch
from . import optionviz
from . import overview
from . import modules
from .strategies import recognize_operations
from .position_display import display_positions, partition_positions

class Application:
    def __init__(self, directory, source_home=None, econf_inbox=None):
        self.store = Store(directory)
        self.sources = Sources(source_home)
        imported = self.store.get("source:holdings", {})
        if imported.get("path"):
            self.sources.snapshot_path = Path(imported["path"])
        self.econf = EconfBox(Path(econf_inbox) if econf_inbox else self.sources.econf_inbox, self.store)

    def state(self):
        plans = self.store.plans()
        snap = self.sources.snapshot()
        source_path = self.sources.snapshot_path
        self.store.retain_snapshot(snap, source_path)
        if snap["status"] == "unavailable":
            saved = self.store.saved_snapshot()
            if saved:
                saved.update(status="stale", cached=True,
                             errors=snap["errors"] + ["上游持仓不可用，展示最近留存的历史快照；不是当前账户。"])
                snap = saved
        history = supplemental_history(self.sources.history(), self.store.directory / "market-data.json")
        positions, conflicts = merged_positions(snap, plans)
        inputs = self.store.get("risk:inputs", {})
        positions = display_positions(enrich_positions(positions, snap, inputs))
        measured_positions, _ = partition_positions(positions)
        with self.store.connection() as c:
            receipts = [json.loads(r[0]) for r in c.execute("SELECT body FROM kv WHERE key LIKE 'econf:file:%'")]
        exposure = analyze_exposure(positions, history, snap["nlv"], benchmark_quote=snap.get("benchmark_quote"))
        strategies = recognize_operations(operation_positions(measured_positions, plans, receipts))
        if history.get("day_source") == "supplement":
            exposure["notices"].append(
                f"行情截止日 {history['day']} 取自补充日线（{history.get('supplement_source', '未知来源')}）；"
                f"主行情包停在 {history.get('primary_day') or '未知'}，本页未混用两者。")
        exposure["snapshot_status"] = snap["status"]
        if snap["status"] != "available":
            exposure["status"] = snap["status"]
            exposure["notices"].insert(0, "持仓快照过期或不可用；以下敞口仅供历史参考，不能代表当前账户。")
        if conflicts:
            exposure["notices"].insert(0, "持仓对账存在冲突，覆盖范围尚不完整：" + "；".join(conflicts))
        exposure["market_scenario"] = market_scenario(exposure, snap["nlv"], self.store.risk_policy())
        mods = modules.current(self.store)
        on = mods["enabled"]
        macro = macro_panel.load(self.sources.macro_state) if on["macro"] else \
            {"status": "disabled", "notice": "宏观灯模块未开启，桶上限与热度上限按静态值。"}
        exposure["macro_panel"] = macro
        exposure["factor_clusters"] = factor_clusters(exposure, history, snap["nlv"], self.store.risk_policy(),
                                                      buckets=macro_panel.scaled_buckets(FACTOR_BUCKETS, macro))
        exposure["portfolio_heat"] = portfolio_heat(positions, exposure, self.store.risk_policy(),
                                                    cap_r=macro_panel.heat_cap(HEAT_CAP_R, macro))
        if conflicts:
            exposure["market_scenario"]["notices"].insert(0, "持仓对账存在冲突，情景覆盖范围尚不完整。")
        risk = calculate(measured_positions, history, snap["nlv"], snap["day"])
        risk["errors"].extend(conflicts)
        if snap["status"] != "available":
            risk["status"] = "stale" if snap["status"] == "stale" else "unavailable"
            risk["errors"].append("持仓快照过期或不可用，不能代表当前组合")
        day = datetime.now(ET).date().isoformat()
        try:
            econf = self.econf.summary() if on["econf"] else {"files": [], "pending": 0, "errors": [], "inbox": "", "disabled": True}
        except Exception:
            econf = {"files": [], "pending": 0, "errors": ["确认单文件夹读取失败"], "inbox": ""}
        result = {"schema": "shepherd-workbench/1", "day": day, "generated_at": now(), "snapshot": snap,
                "plans": plans, "positions": positions, "risk": risk, "brief": self.sources.brief(),
                "calendar": self.sources.calendar(plans, self.store.events(), snap),
                "preparation": self.store.get("prep:"+day, {"checks": [], "note": ""}),
                "data_directory": str(self.store.directory), "conflicts": conflicts, "econf": econf,
                "appearance": self.store.get("appearance", {"theme": "dark"}),
                "risk_policy": self.store.risk_policy(), "risk_inputs": inputs,
                "exposure": exposure, "strategies": strategies, "paper": paper_book.load(self.sources.paper_state) if on["paper"] else {"status": "disabled", "trades": []},
                "weekly_watch": weekly_watch.load(self.sources.weekly_state) if on["weekly"] else {"status": "disabled", "symbols": {}, "triggers": []},
                "source_presence": {"snapshot": self.sources.snapshot_path.exists() or bool(self.store.snapshot_history()),
                                    "market": (self.sources.digest / "cache/addscore").is_dir()
                                    or (self.store.directory / "market-data.json").exists()},
                "modules": dict(mods, catalog=modules.catalog(self.sources.found()), hidden_pages=modules.hidden_pages(on)),
                "storage": {"ledger_path": str(self.store.path), "source_path": str(source_path),
                            "history": self.store.snapshot_history(), "local_plans": len(plans),
                            "local_fills": sum(len(p["fills"]) for p in plans),
                            "snapshot_positions": len(snap["positions"])}}
        try:
            result["option_plans"] = optionviz.build(positions, datetime.now(ET).date(), standard_r(self.store.risk_policy()),
                                                     directory=self.sources.option_plans)
        except Exception as error:  # the page must say it failed, not show an empty list
            result["option_plans"] = {"status": "error", "cards": [], "held": [], "notices": [f"期权计划页生成失败：{type(error).__name__}"]}
        try:
            result["overview"] = overview.build(result)
        except Exception as error:  # the landing page must say it failed, not show an empty "all clear"
            result["overview"] = {"error": f"总览生成失败：{type(error).__name__}", "freshness": [], "attention": [], "upcoming": []}
        return result

    def refresh(self):
        state = self.state()
        r = state["risk"]
        r["change95"] = None
        if r["var95"] is None:
            return state
        fingerprint = hashlib.sha256(json.dumps({"covered": r["covered"], "excluded": r["excluded"],
             "day": r["market_day"], "snapshot": state["snapshot"]["asof"], "var": r["var95"]},sort_keys=True).encode()).hexdigest()
        observation = {"fingerprint":fingerprint,"at":now(),"var95":r["var95"],
                       "scope":sorted([p["symbol"],p["broker"]] for p in r["covered"])}
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            row=c.execute("SELECT body FROM kv WHERE key='risk:observations'").fetchone()
            stored=json.loads(row[0]) if row else []
            if not stored or stored[-1]["fingerprint"]!=fingerprint:
                stored=(stored+[observation])[-2:]
                c.execute("INSERT INTO kv VALUES('risk:observations',?) ON CONFLICT(key) DO UPDATE SET body=excluded.body",(json.dumps(stored),))
            if len(stored)==2 and stored[0]["scope"]==stored[1]["scope"]:
                r["change95"]=round(stored[1]["var95"]-stored[0]["var95"],2)
        return state

    def post(self, path, data):
        if path == "/api/import-snapshot":
            source = Path(text(data.get("path"), "持仓快照路径", 4000, True)).expanduser()
            if not source.is_file() or source.stat().st_size > 8 * 1024 * 1024:
                raise Invalid("请选择不超过 8MB 的持仓 JSON 文件")
            raw = source.read_bytes()
            value = json.loads(raw)
            if not isinstance(value, dict) or not isinstance(value.get("positions"), list) or not isinstance(value.get("accounts"), list):
                raise Invalid("快照需包含 positions 和 accounts 数组；请参考开源示例格式")
            folder = self.store.directory / "imports"
            folder.mkdir(exist_ok=True, mode=0o700)
            dest = folder / (hashlib.sha256(raw).hexdigest() + ".json")
            if not dest.exists():
                temp = dest.with_suffix(".tmp")
                temp.write_bytes(raw); os.chmod(temp, 0o600); temp.replace(dest)
            self.store.put("source:holdings", {"path": str(dest), "original": str(source), "imported_at": now()})
            self.sources.snapshot_path = dest
            return {"saved": True}
        if path == "/api/use-local-source":
            self.store.put("source:holdings", {})
            self.sources.snapshot_path = self.sources.holdings / "state.json"
            return {"saved": True}
        if path == "/api/appearance":
            if data.get("theme") not in ("dark", "light"):
                raise Invalid("外观只能选择 dark 或 light")
            self.store.put("appearance", {"theme": data["theme"]})
            return {"saved": True}
        if path == "/api/refresh":
            return self.refresh()
        if path == "/api/econf-scan":
            return self.econf.view(force=True)
        if path == "/api/econf-record":
            return self.econf.record(data)
        if path == "/api/econf-dismiss":
            return self.econf.dismiss(data)
        if path == "/api/econf-restore":
            return self.econf.dismiss(data, restore=True)
        if path == "/api/size":
            return self.store.size(data)
        if path == "/api/modules":
            return modules.save(self.store, data)
        if path == "/api/risk-policy":
            return self.store.save_risk_policy(data)
        if path == "/api/exposure-input":
            snapshot = self.sources.snapshot()
            positions, _ = merged_positions(snapshot, self.store.plans())
            pid = text(data.get("position_id"), "持仓编号", 200, True)
            if pid not in {p["id"] for p in positions}:
                raise Invalid("持仓已变化，请重新读取后补充风险数据")
            asof = iso_day(data.get("asof"))
            if asof > datetime.now(ET).date().isoformat():
                raise Invalid("风险数据时间不能在未来")
            note = {"asof": asof, "source": text(data.get("source"), "数据来源", 300, True),
                    "delta": number(data.get("delta"), "每份买方合约 Delta", -1, optional=True),
                    "underlying_price": number(data.get("underlying_price"), "标的价格", .000001, optional=True),
                    "sector": text(data.get("sector"), "行业", 100),
                    "theme": text(data.get("theme"), "风险主题", 100),
                    "factor": text(data.get("factor"), "因子簇", 100),
                    "account_id": text(data.get("account_id"), "账户识别别名", 100),
                    "operation_id": text(data.get("operation_id"), "已核对操作批次", 100),
                    "executed_at": text(data.get("executed_at"), "成交时点", 80), "updated_at": now()}
            if note["delta"] is not None and note["delta"] > 1:
                raise Invalid("每份买方合约 Delta 必须在 -1 到 1 之间；不是整仓 Delta")
            if note["executed_at"]:
                try:
                    stamp = datetime.fromisoformat(note["executed_at"].replace("Z", "+00:00"))
                    if stamp.tzinfo is None or stamp > datetime.now(ET):
                        raise ValueError()
                except ValueError:
                    raise Invalid("成交时点须为带时区的 ISO 时间，且不能在未来") from None
            with self.store.connection() as c:
                c.execute("BEGIN IMMEDIATE")
                row = c.execute("SELECT body FROM kv WHERE key='risk:inputs'").fetchone()
                inputs = json.loads(row[0]) if row else {}
                inputs[pid] = note
                c.execute("INSERT INTO kv VALUES('risk:inputs',?) ON CONFLICT(key) DO UPDATE SET body=excluded.body",
                          (json.dumps(inputs, ensure_ascii=False),))
                self.store.audit(c, "risk_input_saved", pid, note)
            return {"saved": True}
        if path == "/api/plans":
            return self.store.save_plan(data)
        if path == "/api/status":
            return self.store.transition(data.get("id"), data)
        if path == "/api/fills":
            return self.store.fill(data.get("id"), data)
        if path == "/api/void-fill":
            return self.store.void_fill(data.get("id"), data)
        if path == "/api/review":
            return self.store.review(data.get("id"), data)
        if path == "/api/adopt":
            snap = self.sources.snapshot()
            pos = next((p for p in snap["positions"] if p["id"] == data.get("id")), None)
            if not pos or pos["fingerprint"] != data.get("fingerprint"):
                raise Invalid("持仓快照已变化，请刷新后核对")
            if data.get("confirmed") is not True:
                raise Invalid("请先核对券商中的实际持仓与成本")
            return self.store.adopt(pos, data, snap["asof"])
        if path == "/api/preparation":
            day = iso_day(data.get("day"))
            checks = data.get("checks", [])
            if not isinstance(checks, list) or any(x not in ("snapshot", "events", "plans") for x in checks):
                raise Invalid("盘前检查项无效")
            self.store.put("prep:"+day, {"checks": checks, "note": text(data.get("note"), "盘前笔记", 8000)})
            return {"saved": True}
        if path == "/api/events":
            return self.store.add_event(data)
        if path == "/api/event-delete":
            id = text(data.get("id"), "事件编号", 100, True)
            with self.store.connection() as c:
                r = c.execute("SELECT body FROM events WHERE id=?", (id,)).fetchone()
                if not r:
                    raise Invalid("找不到自定义事件")
                self.store.audit(c, "event_deleted", id, json.loads(r[0]))
                c.execute("DELETE FROM events WHERE id=?", (id,))
            return {"deleted": True}
        if path == "/api/backup":
            folder = self.store.directory / "backups"
            folder.mkdir(exist_ok=True, mode=0o700)
            path = folder / ("交易记录-"+datetime.now().strftime("%Y%m%d-%H%M%S-%f")+".json")
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(self.store.export(), ensure_ascii=False, indent=2), encoding="utf-8")
            os.chmod(temp, 0o600)
            temp.replace(path)
            return {"path": str(path), "name": path.name}
        raise Invalid("未知操作")
