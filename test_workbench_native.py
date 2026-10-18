"""Native stdio and durable snapshot behavior. Synthetic accounts only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from workbench.application import Application
from workbench.bridge import dispatch
from workbench.store import Invalid


def snapshot(qty=5):
    return {'asof': '收盘价 2026-09-22', 'accounts': [{'broker': 'Demo', 'nlv': 10000}],
            'positions': [{'id': 'demo-one', 'sym': 'DEMO', 'kind': 'stock', 'broker': 'Demo',
                           'qty': qty, 'open': 100, 'mark': 105, 'side': 'long'}]}


class SnapshotRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / 'source/tools/holdings_local/state.json'
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps(snapshot()))
        self.app = Application(self.root/'data', self.root/'source', self.root/'inbox')

    def tearDown(self):
        self.temp.cleanup()

    def test_first_read_retains_holdings_without_creating_fills(self):
        state = self.app.state()
        self.assertEqual(len(state['positions']), 1)
        self.assertEqual(state['plans'], [])
        self.assertEqual(state['storage']['local_fills'], 0)
        self.assertEqual(len(state['storage']['history']), 1)
        self.assertEqual(len(self.app.state()['storage']['history']), 1)

    def test_versions_survive_restart_and_appear_in_backup(self):
        self.app.state()
        self.path.write_text(json.dumps(snapshot(8)))
        self.app.state()
        reloaded = Application(self.root/'data', self.root/'source', self.root/'inbox')
        rows = reloaded.store.snapshot_history()
        self.assertEqual(len(rows), 2)
        self.assertEqual(reloaded.store.saved_snapshot(rows[-1]['id'])['positions'][0]['qty'], 5)
        self.assertEqual(len(reloaded.store.export()['tables']['source_snapshots']), 2)

    def test_broken_source_shows_explicit_historical_fallback(self):
        self.app.state()
        self.path.write_text('broken')
        state = self.app.state()
        self.assertEqual(len(state['positions']), 1)
        self.assertTrue(state['snapshot']['cached'])
        self.assertEqual(state['snapshot']['status'], 'stale')
        self.assertEqual(state['exposure']['status'], 'stale')
        self.assertEqual(len(state['storage']['history']), 1)
        self.assertTrue(any('历史快照' in e for e in state['snapshot']['errors']))

    def test_import_is_private_copy_and_does_not_rewrite_ledger(self):
        external = self.root/'chosen.json'
        external.write_text(json.dumps(snapshot(12)))
        self.app.post('/api/import-snapshot', {'path': str(external)})
        external.write_text('now unavailable')
        state = Application(self.root/'data', self.root/'source', self.root/'inbox').state()
        self.assertEqual(state['positions'][0]['qty'], 12)
        self.assertEqual(state['plans'], [])
        self.assertEqual(Path(state['storage']['source_path']).stat().st_mode & 0o777, 0o600)
        self.app.post('/api/use-local-source', {})
        self.assertEqual(self.app.state()['positions'][0]['qty'], 5)

    def test_invalid_import_does_not_switch_source(self):
        broken = self.root/'bad.json'; broken.write_text('{"positions":[]}')
        with self.assertRaises(Invalid):
            self.app.post('/api/import-snapshot', {'path':str(broken)})
        self.assertEqual(self.app.state()['positions'][0]['qty'], 5)

    def test_bridge_allowlist(self):
        with self.assertRaises(Invalid):
            dispatch(self.app, {'id':'x','method':'execute','params':{}})
        with self.assertRaises(Invalid):
            dispatch(self.app, {'id':'x','method':'plans','params':[]})

    def test_stdio_protocol_and_real_ledger_roundtrip_without_sockets(self):
        commands = [
            {'id':'one','method':'risk-policy','params':{'version':0,'nlv':10000,'risk_pct':2}},
            {'id':'two','method':'plans','params':{'symbol':'DEMO','title':'Native demo','kind':'stock','side':'long','broker':'Demo','entry':100,'stop':95}},
            {'id':'three','method':'state'},
            {'id':'four','method':'execute'},
        ]
        result = subprocess.run([sys.executable,'-B','-c',"import socket,runpy;socket.socket=lambda *a,**k:(_ for _ in ()).throw(RuntimeError('Network forbidden in native test'));runpy.run_module('workbench.bridge',run_name='__main__')",'--data-dir',str(self.root/'stdio'),
                                 '--source-home',str(self.root/'source'),'--econf-inbox',str(self.root/'inbox')],
                                input=''.join(json.dumps(c)+'\n' for c in commands),text=True,capture_output=True,check=True)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([r['id'] for r in rows], ['one','two','three','four'])
        self.assertEqual(rows[2]['result']['plans'][0]['symbol'], 'DEMO')
        self.assertEqual(len(rows[2]['result']['positions']), 1)
        self.assertIn('error', rows[3])
        self.assertEqual(len(Application(self.root/'stdio',self.root/'source').store.plans()),1)

if __name__ == '__main__':
    unittest.main()
