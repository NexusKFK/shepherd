import tempfile
import unittest
from datetime import date, timedelta

from workbench.expiry import settle_expired
from workbench.store import Store, et_today


def open_option(store, *, expiry, contract="Oct23'26 60C", side="long", qty=2, price=1.80, symbol="CAT", day=None):
    return store.econf_open({"request_id": f"r-{symbol}-{contract}-{side}", "symbol": symbol, "title": f"{symbol} {contract}",
                             "kind": "option", "side": side, "qty": qty, "price": price, "day": day or et_today().isoformat(),
                             "expiry": expiry, "contract": contract})


def history(symbol, day, close):
    return {"series": {symbol: [{"day": day, "close": close}]}}


class ExpirySettleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.today = et_today()
        self.expiry = (self.today - timedelta(days=3)).isoformat()

    def tearDown(self):
        self.tmp.cleanup()

    def one(self, plan_id):
        return next(p for p in self.store.plans() if p["id"] == plan_id)

    def test_worthless_long_call_closes_at_zero(self):
        p = open_option(self.store, expiry=self.expiry)
        out = settle_expired(self.store, self.store.plans(), history("CAT", self.expiry, 55.4), self.today)
        self.assertEqual([x["price"] for x in out], [0.0])
        closed = self.one(p["id"])
        self.assertEqual(closed["status"], "closed")
        exit_fill = [f for f in closed["fills"] if f["action"] == "exit"][0]
        self.assertEqual((exit_fill["qty"], exit_fill["price"]), (2.0, 0.0))
        self.assertIn("没有平仓成交，按到期收盘价算", exit_fill["note"])

    def test_in_the_money_put_settles_at_intrinsic_value(self):
        p = open_option(self.store, expiry=self.expiry, contract="Oct23'26 25P", side="short", qty=2, price=1.0, symbol="T")
        settle_expired(self.store, self.store.plans(), history("T", self.expiry, 23.5), self.today)
        exit_fill = [f for f in self.one(p["id"])["fills"] if f["action"] == "exit"][0]
        self.assertEqual(exit_fill["price"], 1.5)               # 25 − 23.5

    def test_not_expired_or_missing_close_or_unreadable_contract_stay_open(self):
        future = (self.today + timedelta(days=5)).isoformat()
        a = open_option(self.store, expiry=future, contract="Nov20'26 100C", symbol="AAA")
        b = open_option(self.store, expiry=self.expiry, contract="Oct23'26 100C", symbol="BBB")
        c = open_option(self.store, expiry=self.expiry, contract="XLE 蝶式", symbol="CCC")
        hist = {"series": {"AAA": [{"day": future, "close": 1}], "CCC": [{"day": self.expiry, "close": 1}]}}   # BBB 没有那天的收盘
        self.assertEqual(settle_expired(self.store, self.store.plans(), hist, self.today), [])
        for plan in (a, b, c):
            self.assertEqual(self.one(plan["id"])["status"], "holding")

    def test_exit_day_is_not_earlier_than_a_late_opening_balance(self):
        late = self.today.isoformat()                            # 到期后才接入余额：成交日晚于到期日
        p = open_option(self.store, expiry=self.expiry, day=late)
        out = settle_expired(self.store, self.store.plans(), history("CAT", self.expiry, 55.0), self.today)
        self.assertEqual(len(out), 1)
        exit_fill = [f for f in self.one(p["id"])["fills"] if f["action"] == "exit"][0]
        self.assertEqual(exit_fill["day"], late)
        self.assertIn(self.expiry, exit_fill["note"])            # 真正的到期日写在说明里

    def test_second_run_does_nothing(self):
        open_option(self.store, expiry=self.expiry)
        h = history("CAT", self.expiry, 55.0)
        self.assertEqual(len(settle_expired(self.store, self.store.plans(), h, self.today)), 1)
        self.assertEqual(settle_expired(self.store, self.store.plans(), h, self.today), [])


if __name__ == "__main__":
    unittest.main()
