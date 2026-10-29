"""交易记录、定仓、风险覆盖与本机 HTTP 边界的行为测试。全部使用隔离账本。"""
import copy
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import timedelta
from pathlib import Path

from workbench.risk import calculate
from workbench.server import Application, make_server
from workbench.sources import Sources, merged_positions
from workbench.store import Invalid, Store, et_today, size_plan


def draft(**updates):
    return dict({"symbol": "TEST", "title": "测试计划", "kind": "stock", "side": "long", "broker": "IBKR",
                 "thesis": "记录入场理由", "invalidation": "跌破失效价", "exit_rule": "按退出条件执行",
                 "entry": 100, "stop": 95, "nlv": 10000, "risk_pct": 1}, **updates)


def fixture_history():
    start = et_today() - timedelta(days=280)
    bars = [{"day": (start+timedelta(days=i)).isoformat(), "close": 100*(1+.001*i+(.025 if i%11==0 else 0))} for i in range(280)]
    return {"series": {"TEST": bars, "SPY": bars}, "day": bars[-1]["day"], "errors": []}


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.store.save_risk_policy({"version": 0, "nlv": 10000, "risk_pct": 2})

    def tearDown(self):
        self.tmp.cleanup()

    def ready(self, **kwargs):
        if "nlv" in kwargs:
            policy = self.store.risk_policy()
            self.store.save_risk_policy(dict(policy, nlv=kwargs["nlv"]))
        p = self.store.save_plan(draft(**kwargs))
        return self.store.transition(p["id"], {"version": p["version"], "status": "ready"})

    def fill(self, p, action, qty, price, rid="one", fee=0):
        return self.store.fill(p["id"], {"version": p["version"], "request_id": rid, "action": action,
                              "qty": qty, "price": price, "fee": fee, "day": et_today().isoformat()})

    def test_long_cycle_partial_exit_fee_and_restart(self):
        p = self.ready()
        p = self.fill(p, "entry", 10, 100, fee=1)
        p = self.fill(p, "entry", 10, 110, "two", fee=1)
        self.assertEqual(p["avg_entry"], 105)
        p = self.fill(p, "exit", 5, 120, "three", fee=1)
        self.assertEqual(p["open_qty"], 15)
        self.assertEqual(p["realized_pnl"], 72)
        p = self.fill(p, "exit", 15, 90, "four", fee=2)
        self.assertEqual(p["status"], "closed")
        self.assertEqual(p["realized_pnl"], -155)
        self.assertEqual(Store(self.tmp.name).plans()[0]["realized_pnl"], -155)

    def test_short_option_multiplier(self):
        p = self.ready(kind="option", side="short", contract="TEST 2027-01 100/95 put credit spread", expiry="2027-03-19", mult=100, entry=2, max_loss=300, nlv=100000)
        p = self.fill(p, "entry", 2, 2)
        p = self.fill(p, "exit", 2, .5, "two", fee=4)
        self.assertEqual(p["realized_pnl"], 296)

    def test_identical_retry_is_idempotent(self):
        p = self.ready()
        a = self.fill(p, "entry", 10, 100)
        b = self.fill(p, "entry", 10, 100)
        self.assertEqual(len(b["fills"]), 1)
        self.assertEqual(a["version"], b["version"])
        with self.assertRaises(Invalid):
            self.fill(p, "entry", 11, 100)

    def test_concurrent_duplicate_entry_is_recorded_once(self):
        p = self.ready()
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.fill(p, "entry", 10, 100), range(4)))
        self.assertTrue(all(len(r["fills"]) == 1 for r in results))

    def test_invalid_exit_rolls_back(self):
        p = self.fill(self.ready(), "entry", 2, 100)
        with self.assertRaises(Invalid):
            self.fill(p, "exit", 3, 100, "two")
        self.assertEqual(self.store.plans()[0]["open_qty"], 2)
        self.assertEqual(len(self.store.plans()[0]["fills"]), 1)

    def test_void_latest_exit_restores_holding_without_erasing_history(self):
        p = self.fill(self.ready(), "entry", 2, 100)
        p = self.fill(p, "exit", 2, 110, "exit")
        self.assertEqual(p["status"], "closed")
        with self.assertRaises(Invalid):
            self.store.void_fill(p["id"], {"version":p["version"],"fill_id":p["fills"][0]["id"],"reason":"不能跳过后续成交"})
        p = self.store.void_fill(p["id"], {"version":p["version"],"fill_id":p["fills"][-1]["id"],"reason":"误填数量"})
        self.assertEqual(p["status"], "holding")
        self.assertEqual(p["open_qty"], 2)
        self.assertEqual(p["realized_pnl"], 0)
        self.assertTrue(p["fills"][-1]["voided"])
        self.assertEqual(len(p["fills"]), 2)

    def test_oversize_actual_fill_is_preserved_and_flagged(self):
        p=self.fill(self.ready(),"entry",50,100)
        self.assertEqual(p["open_qty"],50)
        self.assertTrue(p["execution_warnings"])

    def test_conflicting_edit_and_locked_filled_plan(self):
        p = self.store.save_plan(draft())
        self.store.save_plan(dict(p, title="修改一"))
        with self.assertRaises(Invalid):
            self.store.save_plan(dict(p, title="过期修改"))
        p = self.fill(self.ready(), "entry", 2, 100)
        with self.assertRaises(Invalid):
            self.store.save_plan(dict(p, stop=80))

    def test_ready_requires_reason_and_exit_conditions(self):
        p = self.store.save_plan(draft(thesis=""))
        with self.assertRaises(Invalid):
            self.store.transition(p["id"], {"version": p["version"], "status": "ready"})

    def test_future_execution_and_fractional_options_rejected(self):
        p = self.ready()
        with self.assertRaises(Invalid):
            self.store.fill(p["id"], {"request_id":"f", "action":"entry", "qty":1, "price":100, "day":"2099-01-01", "version":p["version"]})
        p = self.ready(kind="option", contract="TEST C", expiry="2027-03-19", entry=2, nlv=100000)
        with self.assertRaises(Invalid):
            self.fill(p, "entry", .5, 2)

    def test_opening_balance_preserves_provenance_and_deduplicates(self):
        pos = {"id":"legacy", "sym":"TEST", "kind":"stock", "side":"long", "broker":"IBKR", "qty":10, "open":100, "mark":105, "fingerprint":"x"}
        p = self.store.adopt(pos, {"qty":10,"open":100,"day":et_today().isoformat()}, "source time")
        self.assertTrue(p["fills"][0]["opening_balance"])
        with self.assertRaises(Invalid):
            self.store.adopt(pos, {"qty":10,"open":100,"day":et_today().isoformat()}, "source time")
        merged, errors = merged_positions({"positions":[pos]}, [p])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["qty"], 10)
        self.assertFalse(errors)
        missing, errors = merged_positions({"positions":[]},[p])
        self.assertTrue(missing[0]["confirm"])
        self.assertTrue(errors)

    def test_new_snapshot_does_not_hide_position_after_local_close(self):
        pos={"id":"old", "sym":"TEST", "kind":"stock", "side":"long", "broker":"IBKR", "qty":10, "fingerprint":"original"}
        p=self.store.adopt(pos,{"qty":10,"open":100,"day":et_today().isoformat()},"old time")
        p=self.fill(p,"exit",10,110,"exit")
        rows,errors=merged_positions({"positions":[dict(pos,fingerprint="new")]},[p])
        self.assertEqual(len(rows),1)
        self.assertTrue(rows[0]["confirm"])
        self.assertTrue(errors)

    def test_incomplete_numbers_and_nonfinite_rejected(self):
        for value in (float('nan'), float('inf'), -1, True):
            with self.assertRaises(Invalid):
                self.store.save_plan(draft(nlv=value))


class SizingTests(unittest.TestCase):
    def test_stock_risk_budget_and_stop_direction(self):
        self.assertEqual(size_plan(draft())["qty"], 20)
        with self.assertRaises(Invalid):
            size_plan(draft(stop=101))
        with self.assertRaises(Invalid):
            size_plan(draft(side="short", stop=95))

    def test_long_option_uses_full_premium(self):
        r = size_plan(draft(kind="option", entry=2, mult=100, nlv=100000))
        self.assertEqual(r["qty"], 5)
        self.assertEqual(r["unit_risk"], 200)

    def test_short_option_requires_defined_maximum(self):
        with self.assertRaises(Invalid):
            size_plan(draft(kind="option", side="short", entry=2))


class RiskTests(unittest.TestCase):
    def position(self, **updates):
        return dict({"id":"p", "sym":"TEST", "qty":10, "kind":"stock", "side":"long"}, **updates)

    def test_opposite_exposures_cancel_scenario_by_scenario(self):
        r = calculate([self.position(),self.position(id="short",side="short")],fixture_history(),10000)
        self.assertEqual(r["var95"], 0)
        self.assertEqual(r["portfolio_var95"], 0)

    def test_scale_and_var_tail_order(self):
        r = calculate([self.position()],fixture_history(),10000)
        double = calculate([self.position(qty=20)],fixture_history(),10000)
        self.assertAlmostEqual(double["var95"],2*r["var95"],places=1)
        self.assertGreaterEqual(r["var99"],r["var95"])
        self.assertGreaterEqual(r["es95"],r["var95"])

    def test_options_and_pending_positions_never_count_as_zero_risk(self):
        r = calculate([self.position(),self.position(id="opt",kind="option"),self.position(id="pending",confirm=True)],fixture_history(),None)
        self.assertIsNotNone(r["var95"])
        self.assertIsNone(r["portfolio_var95"])
        self.assertIsNone(r["var_pct"])
        self.assertEqual(len(r["excluded"]),2)

    def test_short_history_does_not_estimate(self):
        hist = fixture_history();hist["series"]["TEST"]=hist["series"]["TEST"][-100:]
        r = calculate([self.position()],hist,10000)
        self.assertIsNone(r["var95"])

    def test_return_intervals_must_match(self):
        h=fixture_history();h["series"]["OTHER"]=h["series"]["TEST"][::2]
        r=calculate([self.position(),self.position(id="b",sym="OTHER")],h,10000)
        self.assertIsNone(r["portfolio_var95"])


class SourceTests(unittest.TestCase):
    def test_missing_sources_show_missing_and_do_not_initialize_fake_data(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as db:
            s=Application(db,root).state()
            self.assertEqual(s["snapshot"]["status"],"unavailable")
            self.assertEqual(s["positions"],[])
            self.assertIsNone(s["risk"]["var95"])
            self.assertTrue(s["calendar"]["errors"])

    def test_signed_short_quantity_and_incomplete_net_value(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'tools/holdings_local';p.mkdir(parents=True)
            (p/'state.json').write_text(json.dumps({"asof":"收盘价 "+et_today().isoformat(),"positions":[{"id":"s","qty":-6,"side":"short","kind":"option"}],"accounts":[{"nlv":100},{"nlv":None}]}))
            s=Sources(root).snapshot()
            self.assertEqual(s["positions"][0]["qty"],6)
            self.assertEqual(s["positions"][0]["side"],"short")
            self.assertIsNone(s["nlv"])


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.server=make_server(Application(self.tmp.name,self.tmp.name),token='test-session')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.tmp.cleanup()

    def request(self,path,data=None,headers=None):
        h={'Cookie':'desk=test-session','Content-Type':'application/json','X-Desk-Request':'1'}
        h.update(headers or {})
        req=urllib.request.Request(self.base+path, data=json.dumps(data).encode() if data is not None else None,headers=h)
        try:
            with urllib.request.urlopen(req) as res:return res.status,json.loads(res.read())
        except urllib.error.HTTPError as e:
            with e:return e.code,json.loads(e.read())

    def test_authentication_origin_and_dns_rebinding(self):
        self.assertEqual(self.request('/api/state',headers={'Cookie':''})[0],401)
        self.assertEqual(self.request('/api/plans',draft(),{'Origin':'https://evil.example'})[0],403)
        self.assertEqual(self.request('/api/state',headers={'Host':'evil.example'})[0],403)
        self.assertEqual(self.request('/api/plans',draft(),{'X-Desk-Request':''})[0],403)

    def test_end_to_end_create_retrieve_backup_and_audit(self):
        code,p=self.request('/api/plans',draft())
        self.assertEqual(code,200)
        self.assertEqual(self.request('/api/state')[1]['plans'][0]['id'],p['id'])
        code,b=self.request('/api/backup',{})
        self.assertEqual(code,200)
        saved=json.loads(Path(b['path']).read_text())
        self.assertEqual(len(saved['tables']['plans']),1)
        self.assertEqual(saved['tables']['audit'][0]['kind'],'plan_created')


if __name__=='__main__':unittest.main()


def test_assistant_plan_records_opened_fill_from_draft(tmp_path):
    """助手起草（source=assistant）的计划：核对通过后停在 draft，允许补登记已发生的真实入场成交。

    owner 2026-10-28：定仓闸门（1R/0.9R）只约束新开计划；已开仓交易按实际成交入账，超预算告警不拦截。
    """
    from workbench.store import Store, Invalid
    s = Store(tmp_path)
    p = s.import_plan({"symbol": "TEST", "title": "t", "thesis": "x", "invalidation": "y",
                       "exit_rule": "z", "broker": "ToS", "kind": "option", "side": "long",
                       "entry": 42.2, "stop": 29.5, "max_loss": 4220.0, "planned_qty": 1, "mult": 100})
    assert p["status"] == "pending" and p["source"] == "assistant"
    d = s.transition(p["id"], {"version": p["version"], "status": "draft"})
    # 编辑不洗掉 source
    upd = {k: d[k] for k in ("symbol", "title", "thesis", "invalidation", "exit_rule", "broker",
                             "review_note", "contract", "kind", "side", "entry", "stop",
                             "max_loss", "planned_qty", "estimated_costs", "mult", "expiry")}
    upd.update(id=d["id"], version=d["version"])
    d2 = s.save_plan(upd)
    assert d2.get("source") == "assistant"
    # draft + assistant + 无成交 → 补登记入场 → holding
    h = s.fill(d2["id"], {"request_id": "r1", "action": "entry", "qty": 1, "price": 42.2,
                          "fee": 0, "day": "2026-10-27", "note": "n", "version": d2["version"]})
    assert h["status"] == "holding" and h["open_qty"] == 1 and h["avg_entry"] == 42.2
    # 普通新建（无 source）的 draft 仍然被定仓闸门拦住
    q = s.save_plan({"symbol": "TEST2", "title": "t", "thesis": "x", "invalidation": "y",
                     "exit_rule": "z", "broker": "ToS", "kind": "stock", "side": "long",
                     "entry": 100, "stop": 90, "planned_qty": 10})
    try:
        s.fill(q["id"], {"request_id": "r2", "action": "entry", "qty": 10, "price": 100,
                         "fee": 0, "day": "2026-10-27", "note": "n", "version": q["version"]})
        raise SystemExit("应当被定仓闸门拦住")
    except Invalid as e:
        assert "请先完成计划和定仓" in str(e)
