"""Schwab eConfirmation 解析、方向映射与导入状态的测试。全部使用隔离账本与临时文件夹。"""
import tempfile
import unittest
from datetime import date, timedelta
from email.message import EmailMessage
from pathlib import Path

from workbench.econf import (EconfBox, html_to_text, leg_action, money, parse_eml,
                             parse_file, parse_option_description, parse_text, quantity)
from workbench.store import Invalid, Store


SINGLE_EML = """From: Schwab <noreply@schwab.com>
Subject: Your eConf#1234567890 from Charles Schwab
MIME-Version: 1.0
Content-Type: text/html; charset=utf-8

<html><body><h2>eConf#1234567890</h2><table>
<tr><td>Symbol</td><td>KO Mar 19 '27 65 Call</td></tr>
<tr><td>Action</td><td>Buy to Open</td></tr>
<tr><td>Executed Time</td><td>09/18/26 10:32 AM ET</td></tr>
<tr><td>Quantity</td><td>1</td></tr>
<tr><td>Executed Price</td><td>$12.40</td></tr>
</table>
<p>Commission: $0.65</p>
<p>Total Fees: $0.04</p>
<p>Net Amount: $1,240.69</p>
<p>Account: 10000123</p>
</body></html>
"""

SPREAD_TEXT = """Your eConf#9876543210 from Charles Schwab
Account: 10000123
Trade Date: 09/18/26 11:05 AM ET
Buy to Open   Quantity: 1   IAU Mar19'27 50 Call   Price $8.30
Sell to Open  Quantity: 1   IAU Mar19'27 60 Call   Price $3.10
Commission: $1.30
Total Fees: $0.12
Net Amount: $521.42
"""

STOCK_TEXT = """Charles Schwab eConf#5550001111
Account: XXXX0123
Executed Date: 09/17/26 3:41 PM ET
Sell to close
Symbol: ADBE
Quantity: 8
Price: $412.50
Commission: $0.00
Net Amount: $3,300.00
"""

# Schwab 成交通知（Alert）的格式样例，内容虚构；这类通知没有单号，账户只给 3 位尾号
ALERT_TEXT = """From: Schwab Alerts <donotreply@mail.schwab.com>
Subject: Your trade was executed in your Schwab account ending in 123

Account ending: 123
Sep 21, 2026

We executed a trade in your account.

Here are the details for the trade executed on 09/21/2026 in your account ending in 123:
Settlement date:
09/22/2026
Action:
Bought
Quantity:
3
Symbol:
AMD
Unit price:
$152.40
Principal amount
$457.20*

Watch for your official trade confirmation via U.S. mail or email, detailing commissions and fees.
Thank you for investing with Schwab.
"""


def make_eml(body_html=None, body_text=None, forwarded=False):
    if forwarded:  # 真实邮箱「以附件形式转发」的 RFC822 形态
        inner = SINGLE_EML
        return ("From: owner@example.com\nTo: owner@example.com\n"
                "Subject: Fw: Your eConf#1234567890 from Charles Schwab\n"
                'MIME-Version: 1.0\nContent-Type: multipart/mixed; boundary="XX"\n\n'
                "--XX\nContent-Type: text/plain; charset=utf-8\n\n请查收确认单。\n"
                "--XX\nContent-Type: message/rfc822\nContent-Transfer-Encoding: 8bit\n\n"
                + inner
                + "\n--XX--\n").encode("utf-8")
    msg = EmailMessage()
    msg["From"] = "owner@example.com"
    msg["To"] = "owner@example.com"
    msg["Subject"] = "Fw: Your eConf#1234567890 from Charles Schwab"
    if body_html:
        msg.set_content(body_html, subtype="html")
    else:
        msg.set_content(body_text)
    return msg.as_bytes()


class NumberTests(unittest.TestCase):
    def test_money_variants(self):
        self.assertEqual(money("$1,234.56"), 1234.56)
        self.assertEqual(money("(12.00)"), -12.0)
        self.assertEqual(money("5.00 CR"), -5.0)
        self.assertEqual(money("$0.65"), 0.65)
        self.assertIsNone(money("n/a"))
        self.assertIsNone(money(None))

    def test_quantity_variants(self):
        self.assertEqual(quantity("1,000"), 1000)
        self.assertEqual(quantity("0.5"), 0.5)
        self.assertIsNone(quantity("一堆字"))


# (买卖, 张数或股数, 价格, 是否期权, 预期费用合计)：虚构的成交，覆盖买卖、最低 0.01 和期权三种情况。
# 用真实确认单逐笔核对的版本在私有测试 test_local_fees.py 里，不进公开版。
SAMPLE_FEES = [
    ("sell", 40, 52.10, False, 0.04), ("buy", 15, 120.0, False, 0.0), ("sell", 3, 98.5, False, 0.01),
    ("sell", 25, 410.0, False, 0.21), ("sell", 1, 3.2, True, 0.67), ("buy", 3, 1.5, True, 1.98),
    ("sell", 2, 12.0, True, 1.37),
]


class FeeEstimateTests(unittest.TestCase):
    def test_default_schedule(self):
        from workbench.econf import estimate_fee
        for side, qty, price, option, expected in SAMPLE_FEES:
            self.assertEqual(estimate_fee(side, qty, price, option), expected, (side, qty, price, option))

    def test_unusable_leg_has_no_estimate(self):
        from workbench.econf import estimate_fee
        self.assertIsNone(estimate_fee(None, 1, 1.0, False))
        self.assertIsNone(estimate_fee("buy", None, 1.0, False))
        self.assertIsNone(estimate_fee("sell", 1, None, False))


class ParseTests(unittest.TestCase):
    def test_single_option_leg_from_html_layout(self):
        p = parse_text(html_to_text(SINGLE_EML.split("\n\n", 1)[1]))
        self.assertEqual(p["conf"], "1234567890")
        self.assertEqual(p["account"], "10000123")
        self.assertEqual(p["trade_day"], "2026-09-18")
        self.assertEqual(p["executed_clock"], "10:32 ET")
        self.assertEqual(len(p["legs"]), 1)
        leg = p["legs"][0]
        self.assertEqual((leg["side"], leg["effect"]), ("buy", "open"))
        self.assertEqual(leg["qty"], 1)
        self.assertEqual(leg["price"], 12.40)
        self.assertEqual(leg["description"], "KO Mar 19 '27 65 Call")
        self.assertEqual(leg["option"]["root"], "KO")
        self.assertEqual(leg["option"]["expiry"], "2027-03-19")
        self.assertEqual(leg["option"]["strike"], 65.0)
        self.assertEqual(leg["option"]["call_put"], "call")
        self.assertEqual(p["commission"], 0.65)
        self.assertEqual(p["fees"], 0.04)
        self.assertEqual(p["net_amount"], 1240.69)
        self.assertEqual([w for w in p["warnings"] if "成交日期" in w], [])

    def test_two_leg_spread(self):
        p = parse_text(SPREAD_TEXT)
        self.assertEqual(p["conf"], "9876543210")
        self.assertEqual(len(p["legs"]), 2)
        self.assertEqual(p["legs"][0]["side"], "buy")
        self.assertEqual(p["legs"][1]["side"], "sell")
        self.assertEqual(p["legs"][0]["option"]["strike"], 50.0)
        self.assertEqual(p["legs"][1]["option"]["expiry"], "2027-03-19")
        self.assertEqual(p["fees"], 0.12)

    def test_stock_close_and_masked_account(self):
        p = parse_text(STOCK_TEXT)
        self.assertEqual(p["trade_day"], "2026-09-17")
        leg = p["legs"][0]
        self.assertEqual((leg["side"], leg["effect"]), ("sell", "close"))
        self.assertEqual(leg["description"], "ADBE")
        self.assertEqual(leg["option"], {})
        self.assertEqual(p["account"], "XXXX0123")

    def test_alert_notification_template(self):
        """真实 Schwab 成交通知：过去式动作、值在标签下一行、3 位尾号、脚注星号、Principal 本金。"""
        p = parse_text(ALERT_TEXT)
        self.assertIsNone(p["conf"])
        self.assertEqual(p["account"], "123")
        self.assertEqual(p["trade_day"], "2026-09-21")
        self.assertIsNone(p["net_amount"])
        self.assertEqual(p["principal_amount"], 457.20)
        self.assertEqual(p["commission"], None)
        self.assertEqual(len(p["legs"]), 1)
        leg = p["legs"][0]
        self.assertEqual(leg["side"], "buy")
        self.assertIsNone(leg["effect"])
        self.assertEqual(leg["qty"], 3)
        self.assertEqual(leg["price"], 152.40)
        self.assertEqual(leg["description"], "AMD")
        # 通知类邮件本来就没有 eConf# 单号、没写开平仓、没给费用：这三样都正常，不再当成提示（owner 2026-10-27）
        self.assertEqual(p["warnings"], [])
        self.assertEqual(p["fee_basis"], "默认费率估算")
        self.assertEqual(leg["fee_estimate"], 0.0)                    # 股票买入不收费

    def test_missing_fields_reported_loudly(self):
        p = parse_text("some random email body without confirmation")
        self.assertIsNone(p["conf"])
        self.assertEqual(p["legs"], [])
        self.assertFalse(any("eConf#" in w for w in p["warnings"]))     # 没有单号很正常，不报
        self.assertTrue(any("成交腿" in w for w in p["warnings"]))

    def test_option_description_variants(self):
        d = parse_option_description("SPY 03/19/2027 600.00 P")
        self.assertEqual((d["root"], d["expiry"], d["strike"], d["call_put"]),
                         ("SPY", "2027-03-19", 600.0, "put"))
        self.assertEqual(parse_option_description("普通正股描述"), {})


class MailTests(unittest.TestCase):
    def test_plain_eml(self):
        p = parse_eml(make_eml(body_html=SINGLE_EML.split("\n\n", 1)[1]))
        self.assertIn("1234567890", p)

    def test_forwarded_nested_eml_reaches_original(self):
        p = parse_eml(make_eml(forwarded=True))
        self.assertIn("1234567890", p)
        self.assertIn("Buy to Open", p)


class LegActionTests(unittest.TestCase):
    def test_matrix(self):
        self.assertEqual(leg_action({"side": "buy", "effect": "open"}, "long"), "entry")
        self.assertEqual(leg_action({"side": "sell", "effect": "close"}, "long"), "exit")
        self.assertEqual(leg_action({"side": "sell", "effect": "open"}, "short"), "entry")
        self.assertEqual(leg_action({"side": "buy", "effect": "close"}, "short"), "exit")
        with self.assertRaises(Invalid):
            leg_action({"side": "sell", "effect": "open"}, "long")
        with self.assertRaises(Invalid):
            leg_action({"side": "buy", "effect": "open"}, "short")


class ImportFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "ledger")
        self.store.save_risk_policy({"nlv": 100000, "risk_pct": 2, "version": 0})
        self.inbox = Path(self.tmp.name) / "inbox"
        self.inbox.mkdir()
        self.box = EconfBox(self.inbox, self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def drop(self, name, content):
        if isinstance(content, bytes):
            (self.inbox / name).write_bytes(content)
        else:
            (self.inbox / name).write_text(content, encoding="utf-8")

    def ready_plan(self, **kw):
        p = self.store.save_plan(dict({"symbol": "KO", "title": "已有计划", "kind": "option", "side": "long",
                                       "broker": "Schwab", "thesis": "t", "invalidation": "i", "exit_rule": "e",
                                       "entry": 5, "nlv": 100000, "risk_pct": 1, "mult": 100,
                                       "expiry": "2027-03-19",
                                       "contract": "KO Mar 19 '27 65 Call"}, **kw))
        return self.store.transition(p["id"], {"version": p["version"], "status": "ready"})

    def test_scan_record_into_existing_plan_idempotent(self):
        self.drop("conf.eml", make_eml(body_html=SINGLE_EML.split("\n\n", 1)[1]))
        view = self.box.view()
        self.assertEqual(view["pending"], 1)
        f = view["files"][0]
        leg = f["proposal"]["legs"][0]
        plan = self.ready_plan()
        out = self.box.record({"sha": f["sha"], "file": f["file"], "leg": leg["index"],
                               "plan_id": plan["id"], "version": plan["version"],
                               "qty": leg["qty"], "price": leg["price"], "fee": 0.69,
                               "confirmed": True})
        self.assertEqual(out["files"][0]["status"], "recorded")
        updated = self.store.get_plan(plan["id"])
        self.assertEqual(updated["open_qty"], 1)
        self.assertEqual(updated["fills"][0]["fee"], 0.69)
        self.assertTrue(updated["fills"][0]["note"].startswith("[券商确认单]"))
        with self.store.connection() as c:
            rid = c.execute("SELECT request_id FROM fills").fetchone()[0]
        self.assertEqual(rid, "econf-1234567890-" + f["sha"] + "-0")
        # 重复登记同一腿被拒绝；账本不产生第二条成交
        with self.assertRaises(Invalid):
            self.box.record({"sha": f["sha"], "file": f["file"], "leg": leg["index"],
                             "plan_id": plan["id"], "version": updated["version"],
                             "qty": leg["qty"], "price": leg["price"], "confirmed": True})
        self.assertEqual(len(self.store.get_plan(plan["id"])["fills"]), 1)

    def test_record_requires_explicit_confirm(self):
        self.drop("conf.txt", SPREAD_TEXT)
        view = self.box.view()
        f = view["files"][0]
        plan = self.ready_plan()
        with self.assertRaises(Invalid):
            self.box.record({"sha": f["sha"], "file": f["file"], "leg": 0, "plan_id": plan["id"],
                             "version": plan["version"], "qty": 1, "price": 8.30})
        self.assertEqual(self.box.view()["files"][0]["status"], "pending")

    def test_new_plan_import_marks_holding_and_audits(self):
        self.drop("stock.txt", STOCK_TEXT.replace("Sell to close", "Buy to Open"))
        f = self.box.view()["files"][0]
        leg = f["proposal"]["legs"][0]
        out = self.box.record({"sha": f["sha"], "file": f["file"], "leg": leg["index"],
                               "confirmed": True, "new_plan": True, "fee": 0,
                               "plan": {"symbol": "ADBE", "title": "ADBE 第2档", "kind": "stock",
                                        "side": "long", "broker": "Schwab", "mult": 1}})
        updated = self.box.view()["files"][0]
        self.assertEqual(updated["status"], "recorded")
        plans = [p for p in self.store.plans() if p.get("source") == "econf"]
        self.assertEqual(len(plans), 1)
        p = plans[0]
        self.assertEqual(p["status"], "holding")
        self.assertEqual(p["open_qty"], 8)
        self.assertEqual(p["fills"][0]["price"], 412.50)
        self.assertEqual(p["broker"], "Schwab")
        with self.store.connection() as c:
            kinds = [r["kind"] for r in c.execute("SELECT kind FROM audit")]
        self.assertIn("econf_opened", kinds)
        self.assertIn("econf_recorded", kinds)

    def test_dismiss_and_errors_surface(self):
        self.drop("bad.txt", "完全无关的内容")
        (self.inbox / "notes.md").write_text("不是确认单，应被忽略")
        view = self.box.view()
        self.assertEqual(len(view["errors"]), 0)
        self.assertEqual(view["pending"], 1)  # 可读但没有确认单要素：以 warnings 形式显形
        self.assertTrue(view["files"][0]["proposal"]["warnings"])
        self.drop("spread.txt", SPREAD_TEXT)
        f = next(x for x in self.box.view(force=True)["files"] if x["file"] == "spread.txt")
        out = self.box.dismiss({"sha": f["sha"], "file": f["file"], "note": "已手工登记"})
        dismissed = next(x for x in out["files"] if x["file"] == "spread.txt")
        self.assertEqual(dismissed["status"], "dismissed")
        self.assertEqual(out["pending"], 1)
        with self.assertRaises(Invalid):
            self.box.record({"sha": f["sha"], "file": f["file"], "leg": 0, "confirmed": True,
                             "new_plan": True, "plan": {"symbol": "IAU", "title": "x"}})

    def test_unparsable_fields_block_until_day_filled(self):
        text = "eConf#7770002222 from Charles Schwab\nBuy to Open\nQuantity: 2\nSomething call\n"
        self.drop("partial.txt", text)
        f = self.box.view()["files"][0]
        leg = f["proposal"]["legs"][0]
        self.assertIsNone(leg["price"])
        self.assertTrue(any("成交日期" in w for w in f["proposal"]["warnings"]))
        with self.assertRaises(Invalid):
            self.box.record({"sha": f["sha"], "file": f["file"], "leg": leg["index"],
                             "confirmed": True, "new_plan": True, "fee": 0,
                             "plan": {"symbol": "X", "title": "x", "kind": "option"}})

    def test_alert_without_conf_records_with_sha_based_rid(self):
        self.drop("alert.txt", ALERT_TEXT)
        f = self.box.view()["files"][0]
        leg = f["proposal"]["legs"][0]
        out = self.box.record({"sha": f["sha"], "file": f["file"], "leg": leg["index"],
                               "confirmed": True, "new_plan": True, "fee": 0, "action": "entry",
                               "plan": {"symbol": "AMD", "title": "AMD 3股", "kind": "stock",
                                        "side": "long", "broker": "Schwab", "mult": 1}})
        plans = [p for p in self.store.plans() if p.get("source") == "econf"]
        self.assertEqual(len(plans), 1)
        with self.store.connection() as c:
            rid = c.execute("SELECT request_id FROM fills").fetchone()[0]
        self.assertTrue(rid.startswith("econf-无单号-" + f["sha"] + "-0"))

    def test_same_day_qty_price_leg_flags_dupe_against_existing_fill(self):
        self.drop("alert.txt", ALERT_TEXT)
        f = self.box.view()["files"][0]
        plan = self.store.save_plan({"symbol": "AMD", "title": "已有", "kind": "stock", "side": "long",
                                     "broker": "Schwab", "thesis": "t", "invalidation": "i",
                                     "exit_rule": "e", "entry": 320, "stop": 300, "nlv": 100000,
                                     "risk_pct": 1})
        plan = self.store.transition(plan["id"], {"version": plan["version"], "status": "ready"})
        self.store.fill(plan["id"], {"version": plan["version"], "request_id": "manual-1",
                                     "action": "entry", "qty": 3, "price": 152.40,
                                     "fee": 0, "day": "2026-09-21"})
        leg = self.box.view()["files"][0]["proposal"]["legs"][0]
        self.assertTrue(leg["dupe"])


if __name__ == "__main__":
    unittest.main()
