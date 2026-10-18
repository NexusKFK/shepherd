"""Regression cases for the receipt import audit; no writes to real accounts."""
import json
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from test_econf import ALERT_TEXT, SINGLE_EML, SPREAD_TEXT, STOCK_TEXT
from workbench.econf import EconfBox, MAX_FILE_BYTES, leg_action, money, parse_eml, parse_file, parse_text, quantity
from workbench.server import Application
from workbench.sources import merged_positions
from workbench.store import Invalid, Store


class ParserReviewTests(unittest.TestCase):
    def test_24_hour_time_and_source_timezone(self):
        p = parse_text(STOCK_TEXT.replace('3:41 PM ET', '15:41 PT'))
        self.assertEqual(p['executed_clock'], '15:41 PT')
        self.assertEqual(parse_text(STOCK_TEXT.replace('3:41 PM ET', '15:41'))['executed_clock'], '15:41 时区未注明')
        self.assertEqual(parse_text(STOCK_TEXT.replace('3:41 PM ET', '25:90 PM ET'))['executed_clock'], '')

    def test_mime_alternatives_do_not_duplicate_fills(self):
        m = EmailMessage()
        m['Subject'] = 'Schwab confirmation'
        m.set_content(ALERT_TEXT)
        m.add_alternative('<html><body><pre>' + ALERT_TEXT + '</pre></body></html>', subtype='html')
        self.assertEqual(len(parse_text(parse_eml(m.as_bytes()))['legs']), 1)

    def test_pdf_uses_own_document_not_outer_subject_or_alert(self):
        m = EmailMessage()
        m['Subject'] = 'eConf#9999999999'
        m.set_content(ALERT_TEXT)
        m.add_attachment(b'%PDF', maintype='application', subtype='pdf', filename='trade.pdf')
        with patch('workbench.econf.pdf_to_text', return_value=STOCK_TEXT):
            p = parse_text(parse_eml(m.as_bytes()))
        self.assertEqual(p['conf'], '5550001111')
        self.assertEqual(len(p['legs']), 1)
        self.assertEqual(p['legs'][0]['description'], 'ADBE')

    def test_corrections_and_mixed_confirmations_require_manual_reconciliation(self):
        self.assertTrue(parse_text('Subject: Corrected trade confirmation\n' + STOCK_TEXT)['review_required'])
        self.assertTrue(parse_text(STOCK_TEXT + '\neConf#2222222222')['review_required'])

    def test_nonfinite_numbers_are_missing_not_json_poison(self):
        for s in ('nan', 'inf', '1e999'):
            self.assertIsNone(money(s))
            self.assertIsNone(quantity(s))

    def test_missing_quantity_does_not_steal_price(self):
        p = parse_text(ALERT_TEXT.replace('Quantity:\n3', 'Quantity:\nUnavailable'))
        self.assertIsNone(p['legs'][0]['qty'])

    def test_buy_to_close_cannot_enter_long_plan(self):
        with self.assertRaises(Invalid):
            leg_action({'side': 'buy', 'effect': 'close'}, 'long')
        with self.assertRaises(Invalid):
            leg_action({'side': 'buy', 'effect': None}, 'long')


class ReceiptReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.inbox = self.root / 'inbox'
        self.inbox.mkdir()
        self.store = Store(self.root / 'data')
        self.box = EconfBox(self.inbox, self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def drop(self, content=ALERT_TEXT, name='alert.txt'):
        (self.inbox / name).write_text(content)
        return next(f for f in self.box.view(force=True)['files'] if f['file'] == name)

    def request(self, f, **changes):
        body = dict(sha=f['sha'], file=f['file'], leg=0, confirmed=True, new_plan=True, fee=0,
                    plan=dict(symbol='AMD', title='AMD alert', kind='stock', side='long', broker='Schwab', mult=1))
        body.update(changes)
        return body

    def test_wrong_broker_symbol_and_direction_are_rejected(self):
        f = self.drop()
        for change in ({'broker': 'IBKR'}, {'symbol': 'INTC'}, {'side': 'short'}, {'kind': 'option'}):
            r = self.request(f)
            r['plan'].update(change)
            with self.assertRaises(Invalid):
                self.box.record(r)
        self.assertEqual(self.store.plans(), [])

    def test_close_receipt_cannot_create_position(self):
        f = self.drop(STOCK_TEXT)
        r = self.request(f)
        r['plan']['symbol'] = 'ADBE'
        with self.assertRaisesRegex(Invalid, '平仓成交'):
            self.box.record(r)
        self.assertEqual(self.store.plans(), [])

    def test_explicit_direction_cannot_override_buy_sell(self):
        f = self.drop()
        self.box.record(self.request(f))
        p = self.store.plans()[0]
        f = self.drop(ALERT_TEXT.replace('3\nSymbol', '2\nSymbol'), 'another.txt')
        with self.assertRaisesRegex(Invalid, '方向'):
            self.box.record(self.request(f, new_plan=False, plan_id=p['id'], version=p['version'], action='exit'))
        self.assertEqual(self.store.plans()[0]['open_qty'], 3)

    def test_option_contract_mismatch_rejected_even_if_acknowledged(self):
        f = self.drop(SINGLE_EML.split('\n\n', 1)[1], 'option.html')
        p = dict(symbol='KO', title='option', kind='option', side='long', broker='Schwab', mult=100,
                 contract="KO Mar 19 '27 70 Call", expiry='2027-03-19')
        with self.assertRaisesRegex(Invalid, '合约'):
            self.box.record(self.request(f, plan=p, instrument_confirmed=True))

    def test_unknown_fee_requires_user_value(self):
        f = self.drop()
        r = self.request(f)
        del r['fee']
        with self.assertRaisesRegex(Invalid, '费用'):
            self.box.record(r)
        self.assertEqual(self.store.plans(), [])

    def test_changed_forward_headers_require_duplicate_ack(self):
        f = self.drop()
        self.box.record(self.request(f))
        copy = self.drop('Forwarded again\n' + ALERT_TEXT, 'copy.txt')
        self.assertTrue(copy['proposal']['legs'][0]['dupe'])
        with self.assertRaisesRegex(Invalid, '重复'):
            self.box.record(self.request(copy))
        self.assertEqual(len(self.store.plans()), 1)
        # Legitimately identical fills must still be expressible with explicit review.
        self.box.record(self.request(copy, duplicate_confirmed=True))
        self.assertEqual(len(self.store.plans()), 2)

    def test_duplicate_hint_does_not_match_other_ticker(self):
        f = self.drop()
        self.box.record(self.request(f))
        other = self.drop(ALERT_TEXT.replace('AMD', 'INTC'), 'msft.txt')
        self.assertFalse(other['proposal']['legs'][0]['dupe'])

    def test_audit_failure_rolls_back_fill_plan_and_receipt(self):
        f = self.drop()
        original = self.store.audit
        def audit(c, kind, ref, body):
            if kind == 'econf_recorded':
                raise RuntimeError('simulate disk failure')
            return original(c, kind, ref, body)
        with patch.object(self.store, 'audit', side_effect=audit):
            with self.assertRaises(RuntimeError):
                self.box.record(self.request(f))
        self.assertEqual(self.store.plans(), [])
        self.assertIsNone(self.store.get('econf:file:' + f['sha']))
        self.box.record(self.request(f))
        self.assertEqual(len(self.store.plans()), 1)

    def test_same_leg_concurrently_records_once(self):
        f = self.drop()
        def run(_):
            try:
                EconfBox(self.inbox, self.store).record(self.request(f))
                return 'saved'
            except Invalid:
                return 'rejected'
        with ThreadPoolExecutor(2) as pool:
            self.assertCountEqual(pool.map(run, range(2)), ['saved', 'rejected'])
        self.assertEqual(len(self.store.plans()), 1)

    def test_partial_pending_count_dismiss_restore_and_preserved_history(self):
        f = self.drop(SPREAD_TEXT)
        p = dict(symbol='IAU', title='IAU call', kind='option', side='long', broker='Schwab', mult=100,
                 contract="IAU Mar19'27 50 Call", expiry='2027-03-19')
        self.box.record(self.request(f, plan=p, fee=.71))
        view = self.box.summary()
        self.assertEqual((view['pending'], view['pending_legs'], view['files'][0]['status']), (1, 1, 'partial'))
        self.box.dismiss({'sha': f['sha']})
        self.assertEqual(self.box.summary()['pending'], 0)
        view = self.box.dismiss({'sha': f['sha']}, restore=True)
        self.assertEqual(view['files'][0]['status'], 'partial')
        self.assertEqual(len(view['files'][0]['recorded']), 1)

    def test_imported_execution_is_correctable_and_view_tracks_void(self):
        f = self.drop()
        self.box.record(self.request(f))
        p = self.store.plans()[0]
        self.assertFalse(p['fills'][0].get('opening_balance'))
        self.store.void_fill(p['id'], {'version': p['version'], 'fill_id': p['fills'][0]['id'], 'reason': 'wrong import'})
        self.assertEqual(self.box.summary()['files'][0]['proposal']['legs'][0]['status'], 'voided')
        self.assertEqual(self.store.plans()[0]['open_qty'], 0)
        with self.assertRaises(Invalid):
            self.box.record(self.request(f))

    def test_legacy_econf_opening_flag_migrated_only_for_econf(self):
        f = self.drop()
        self.box.record(self.request(f))
        with self.store.connection() as c:
            r = c.execute('SELECT id, body FROM fills').fetchone()
            body = json.loads(r['body']); body['opening_balance'] = True
            c.execute('UPDATE fills SET body=? WHERE id=?', (json.dumps(body), r['id']))
        store = Store(self.root / 'data')
        self.assertFalse(store.plans()[0]['fills'][0].get('opening_balance'))
        Store(self.root / 'data')
        with store.connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM audit WHERE kind='econf_execution_flag_corrected'").fetchone()[0], 1)

    def ingest(self, files, executions=()):
        (self.root / 'ingest_state.json').write_text(json.dumps({'files': files, 'executions': list(executions)}))

    def test_auto_ingested_receipt_is_not_pending(self):
        self.drop(name='a.txt')
        self.drop(STOCK_TEXT, name='b.txt')
        self.ingest({'s1': {'file': 'a.txt', 'status': 'done'}, 's2': {'file': 'b.txt', 'status': 'done'}},
                    [{'file': 'a.txt', 'status': 'applied'}, {'file': 'b.txt', 'status': 'pending'}])
        v = self.box.view(force=True)
        by = {f['file']: f for f in v['files']}
        self.assertEqual(by['a.txt']['status'], 'auto')
        self.assertEqual([l['status'] for l in by['a.txt']['proposal']['legs']], ['auto'])
        self.assertEqual(by['b.txt']['status'], 'pending')
        self.assertEqual((v['pending'], v['pending_legs']), (1, 1))
        with self.assertRaisesRegex(Invalid, '自动入账'):
            self.box.record(self.request(by['a.txt']))

    def test_ingest_review_or_missing_state_stays_pending(self):
        self.drop(name='a.txt')
        self.assertEqual(self.box.view(force=True)['pending'], 1)
        self.ingest({'s1': {'file': 'a.txt', 'status': 'done'}, 's1#2': {'file': 'a.txt', 'status': 'review'}})
        self.assertEqual(self.box.view(force=True)['pending'], 1)
        (self.root / 'ingest_state.json').write_text('{half')
        v = self.box.view(force=True)
        self.assertEqual(v['pending'], 1)
        self.assertTrue(any('自动入账' in e for e in v['errors']))

    def test_local_record_and_dismiss_win_over_auto(self):
        f = self.drop(name='a.txt')
        self.box.record(self.request(f))
        g = self.drop(STOCK_TEXT, name='b.txt')
        self.box.dismiss({'sha': g['sha'], 'file': 'b.txt'})
        self.ingest({'s1': {'file': 'a.txt', 'status': 'done'}, 's2': {'file': 'b.txt', 'status': 'done'}})
        by = {f['file']: f['status'] for f in self.box.view(force=True)['files']}
        self.assertEqual(by, {'a.txt': 'recorded', 'b.txt': 'dismissed'})

    def test_reading_missing_inbox_does_not_create_it(self):
        p = self.root / 'missing'
        self.assertEqual(EconfBox(p, self.store).summary()['files'], [])
        self.assertFalse(p.exists())

    def test_large_file_and_pdf_timeout_visible_other_files_survive(self):
        (self.inbox / 'large.eml').write_bytes(b'x' * (MAX_FILE_BYTES + 1))
        (self.inbox / 'bad.pdf').write_bytes(b'%PDF')
        self.drop()
        with patch('workbench.econf.pdf_to_text', side_effect=subprocess.TimeoutExpired('pdf', 15)):
            view = self.box.view(force=True)
        self.assertEqual(len(view['errors']), 2)
        self.assertEqual(view['pending'], 1)

    def test_schwab_alias_does_not_double_count_tos_snapshot(self):
        f = self.drop()
        self.box.record(self.request(f))
        snapshot = {'positions': [dict(id='old', sym='AMD', broker='ToS', kind='stock', qty=3)]}
        rows, conflicts = merged_positions(snapshot, self.store.plans())
        self.assertTrue(conflicts)
        self.assertTrue(rows[-1]['confirm'])

    def test_appearance_survives_new_application_instance(self):
        app = Application(self.root / 'app', self.root, self.inbox)
        app.post('/api/appearance', {'theme': 'light'})
        self.assertEqual(Application(self.root / 'app', self.root, self.inbox).state()['appearance']['theme'], 'light')
        with self.assertRaises(Invalid):
            app.post('/api/appearance', {'theme': 'unknown'})


if __name__ == '__main__':
    unittest.main()
