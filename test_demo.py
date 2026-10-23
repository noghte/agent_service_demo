"""Real governance/SQLite/audit checks; HTTP responses are test fixtures."""
import ast
from collections import Counter
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from demo_support import TicketDemo, read_rows, redact, verify_audit
from ticket_contract import AGENT_ID, MODEL_ALIAS, TicketPlan, safe_error

ROOT = Path(__file__).resolve().parent
NOTEBOOK = json.loads((ROOT / 'ticket_agent_demo.ipynb').read_text())


def response(value, status=200):
    return SimpleNamespace(ok=status < 400, status_code=status, json=lambda: value)


def reply(value):
    return response({'type': 'ai', 'content': json.dumps(value), 'run_id': 'service-test-run'})


class DemoTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.demo = TicketDemo(ROOT, 'gpt-4.1-mini', 'http://127.0.0.1:8081',
                               'test-service-token', output=self.directory.name)
        self.addCleanup(self.demo.close)

    def transport(self, method, url, **kwargs):
        self.assertEqual(kwargs['headers'], {'Authorization': 'Bearer test-service-token'})
        self.assertNotIn('OPENAI_API_KEY', json.dumps(kwargs))
        if method == 'GET':
            self.assertTrue(url.endswith('/info'))
            return response({'agents': [{'key': AGENT_ID}], 'models': [MODEL_ALIAS]})
        self.assertTrue(url.endswith('/ticket-agent/invoke'))
        body = kwargs['json']
        self.assertEqual(body['model'], MODEL_ALIAS)
        context = json.loads(body['message'])
        return reply({'action': context['allowed_next_action'],
            'draft': 'POL-003 and POL-004 require Support/Operations review.'
                     if context['governance']['conflict'] else 'Test support draft.',
            'reason': 'Transport fixture; no production fake mode.'})

    def run_cell(self, tag, ns):
        cell = next(c for c in NOTEBOOK['cells'] if tag in c['metadata'].get('tags', []))
        exec(compile(''.join(cell['source']), f'notebook-{tag}', 'exec'), ns)

    def test_notebook_and_first_15_csv_rows(self):
        import nbformat
        nbformat.validate(nbformat.from_dict(NOTEBOOK))
        for cell in NOTEBOOK['cells']:
            if cell['cell_type'] == 'code':
                tree = ast.parse(''.join(cell['source']))
                self.assertFalse(any(isinstance(n, ast.Assert) for n in ast.walk(tree)))
        self.assertEqual([t['Ticket_ID'] for t in self.demo.tickets], [f'TKT-{100000+i}' for i in range(15)])
        self.assertEqual(len(self.demo.policies), 4)
        self.assertEqual(sum(p['effect'] == 'deny' for p in self.demo.policies), 1)

    def test_one_conflict_and_fourteen_allowed_drafts(self):
        contexts = [self.demo.context(ticket) for ticket in self.demo.tickets]
        self.assertEqual([c['allowed_next_action'] for c in contexts], ['escalate'] + ['draft_response'] * 14)
        self.assertEqual(sum(c['governance']['conflict'] for c in contexts), 1)
        self.assertEqual(contexts[0]['governance']['matched_policies'], ['POL-003', 'POL-004'])
        self.assertEqual(sum(c['governance']['decision'] == 'allow' for c in contexts), 14)
        for key in ('Customer_Email', 'Resolution_Time_Hours', 'Satisfaction_Score'):
            self.assertNotIn(key, json.dumps(contexts))

    def test_closure_boundaries_and_unknown_action(self):
        def allowed(category, days, action='close_ticket'):
            return self.demo.engine.evaluate(AGENT_ID, {'action': {'type': action}, 'facts': {
                'category': category, 'awaiting_customer': True, 'inactivity_days': days}}).allowed
        self.assertFalse(allowed('Technical', 4))
        self.assertTrue(allowed('Technical', 5))
        self.assertFalse(allowed('General Inquiry', 5))
        self.assertFalse(allowed('General Inquiry', 9))
        self.assertTrue(allowed('General Inquiry', 10))
        self.assertFalse(allowed('General Inquiry', 10, 'send_email'))

    def test_notebook_demo_exports_resume_memory_and_signed_audit(self):
        ns = {'demo': self.demo, 'tickets': self.demo.tickets, 'policies': self.demo.policies,
              'MODEL': self.demo.model, 'AGENT_ID': AGENT_ID, 'MODEL_ALIAS': MODEL_ALIAS,
              'OUTPUT': self.demo.output, 'json': json, 'Path': Path, 'tempfile': tempfile,
              'Counter': Counter,
              'JSON': lambda value, **kwargs: value, 'display': lambda *args: None,
              'verify_audit': verify_audit}
        with patch('demo_support.requests.request', side_effect=self.transport) as http:
            with contextlib.redirect_stdout(io.StringIO()):
                for tag in ('context-preview', 'service-check', 'run-batch', 'review-and-export', 'show-memory', 'audit-check'):
                    self.run_cell(tag, ns)
                self.run_cell('run-batch', ns)
                self.run_cell('audit-check', ns)
            self.assertEqual(sum(c.args[0] == 'POST' for c in http.call_args_list), 15)
            threads = [c.kwargs['json']['thread_id'] for c in http.call_args_list if c.args[0] == 'POST']
            self.assertEqual(len(set(threads)), 15)
        review = json.loads((self.demo.output / 'human_review.json').read_text())
        self.assertEqual([r['ticket_id'] for r in review], ['TKT-100000'])
        results = json.loads((self.demo.output / 'processed_tickets.json').read_text())
        self.assertEqual(sum(r['status'] == 'drafted' for r in results), 14)
        self.assertTrue(all(r['cached'] for r in ns['results']))
        self.assertTrue(self.demo.context(self.demo.tickets[1])['memory'])
        self.assertTrue(all(r['service_run_id'] == 'service-test-run' for r in results))
        self.assertEqual((self.demo.output / '.audit-key').stat().st_mode & 0o777, 0o600)
        rows = [json.loads(line) for line in self.demo.audit_path.read_text().splitlines()]
        self.assertEqual(sum(row['outcome'] == 'deny' for row in rows), 1)
        self.assertNotIn(self.demo.tickets[0]['Ticket_Description'], json.dumps(rows))
        checkpoint = ns['report']
        self.assertTrue(checkpoint['audit_valid'])
        self.assertFalse(checkpoint['tampered_copy_valid'])
        truncated = self.demo.output / 'truncated.jsonl'
        truncated.write_text('\n'.join(json.dumps(row) for row in rows[:-1])+'\n')
        self.assertFalse(verify_audit(truncated, self.demo.audit_key, checkpoint['entries'], checkpoint['head']))
        restored = TicketDemo(ROOT, self.demo.model, self.demo.service_url, self.demo.auth_secret, output=self.demo.output)
        self.addCleanup(restored.close)
        with patch('demo_support.requests.request', side_effect=AssertionError('Resume must not call the model')):
            self.assertTrue(restored.process(restored.tickets[0])['cached'])

    def test_input_or_model_change_invalidates_cached_decision(self):
        ticket = self.demo.tickets[1]
        with patch('demo_support.requests.request', side_effect=self.transport) as http:
            self.demo.process(ticket)
            self.assertTrue(self.demo.process(ticket)['cached'])
            changed = dict(ticket, Ticket_Description='A changed support request.')
            self.assertFalse(self.demo.process(changed)['cached'])
            self.demo.model = 'another-model'
            self.assertFalse(self.demo.process(changed)['cached'])
            self.assertEqual(http.call_count, 3)

    def test_invalid_audit_is_reported_without_creating_checkpoint(self):
        self.demo.log('TKT-100000', 'example', 'allow')
        row = json.loads(self.demo.audit_path.read_text())
        row['outcome'] = 'tampered'
        path = self.demo.output / 'modified-audit.jsonl'
        path.write_text(json.dumps(row) + '\n')
        self.demo.audit_path = path
        self.assertFalse(self.demo.audit_report()['audit_valid'])
        self.assertFalse((self.demo.output / f'checkpoint-{self.demo.run_id}.json').exists())

    def test_unsafe_or_invalid_model_response_never_saves_decision(self):
        valid = {'action': 'escalate', 'draft': 'Review POL-003 and POL-004.', 'reason': 'Conflict.'}
        values = [dict(valid, action='close_ticket'), dict(valid, action='draft_response'),
                  dict(valid, draft=''), dict(valid, reason=' '), dict(valid, draft='x'*4001),
                  dict(valid, reason=123), dict(valid, identity_verified=True), ['unexpected']]
        for value in values:
            with self.subTest(value_type=type(value).__name__):
                with patch('demo_support.requests.request', return_value=reply(value)):
                    with self.assertRaises(RuntimeError):
                        self.demo.process(self.demo.tickets[0])
        self.assertEqual(self.demo.db.execute('SELECT count(*) FROM decisions').fetchone()[0], 0)
        events = [json.loads(line) for line in self.demo.audit_path.read_text().splitlines()]
        self.assertEqual(events[-1]['outcome'], 'error')

    def test_malformed_service_responses_fail_closed(self):
        for body in ({}, {'type': 'human', 'content': '{}'}, {'type': 'ai', 'content': 'not JSON'},
                     {'type': 'ai', 'content': 'x' * 16001}, {'type': 'ai', 'content': 123}):
            with self.subTest(body_type=body.get('type')):
                with patch('demo_support.requests.request', return_value=response(body)):
                    with self.assertRaises(RuntimeError):
                        self.demo.process(self.demo.tickets[0])
        self.assertEqual(self.demo.db.execute('SELECT count(*) FROM decisions').fetchone()[0], 0)

    def test_model_failure_is_readable_redacted_and_audited(self):
        ticket = self.demo.tickets[0]
        failure = {'error_type': 'AuthenticationError', 'error':
                   f'Invalid test-openai-key for {ticket["Customer_Email"]}'}
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-openai-key'}):
            with patch('demo_support.requests.request', return_value=reply(failure)):
                with self.assertRaisesRegex(RuntimeError, 'TKT-100000.*AuthenticationError') as caught:
                    self.demo.process(ticket)
        self.assertNotIn('test-openai-key', str(caught.exception))
        self.assertNotIn(ticket['Customer_Email'], str(caught.exception))
        self.assertEqual(self.demo.db.execute('SELECT count(*) FROM decisions').fetchone()[0], 0)
        events = [json.loads(line) for line in self.demo.audit_path.read_text().splitlines()]
        self.assertEqual(events[-1]['outcome'], 'error')

    def test_http_status_body_timeout_and_non_json(self):
        with patch('demo_support.requests.request', return_value=response({'detail': 'Invalid test-service-token'}, 401)):
            with self.assertRaisesRegex(RuntimeError, 'HTTP 401') as caught:
                self.demo.request('GET', '/info')
            self.assertNotIn('test-service-token', str(caught.exception))
        def not_json():
            raise ValueError('Not JSON')
        raw = SimpleNamespace(ok=False, status_code=502, json=not_json, text='Bad gateway sk-secret')
        with patch('demo_support.requests.request', return_value=raw):
            with self.assertRaisesRegex(RuntimeError, 'HTTP 502.*Bad gateway') as caught:
                self.demo.request('GET', '/info')
            self.assertNotIn('sk-secret', str(caught.exception))
            raw.ok = True
            with self.assertRaises(ValueError):
                self.demo.request('GET', '/info')
        import requests
        with patch('demo_support.requests.request', side_effect=requests.ReadTimeout()):
            with self.assertRaisesRegex(RuntimeError, 'ReadTimeout'):
                self.demo.request('GET', '/info')

    def test_csv_validation_redaction_and_error_bound(self):
        path = self.demo.output / 'malformed.csv'
        for text in ('wrong\nx\n', 'id,text\nx\n', 'id,text\nx,a,extra\n'):
            path.write_text(text)
            with self.assertRaises(ValueError):
                read_rows(path, ['id', 'text'])
        ticket = self.demo.tickets[0]
        text = redact(f'{ticket["Customer_Name"]}: {ticket["Customer_Email"]}', ticket)
        self.assertNotIn(ticket['Customer_Name'], text)
        self.assertNotIn('@', text)
        self.assertLessEqual(len(safe_error('x'*5000)), 1500)
        with self.assertRaises(ValueError):
            TicketPlan.model_validate({'action': 'send_email', 'draft': 'Draft', 'reason': 'Reason'})


if __name__ == '__main__':
    unittest.main()
