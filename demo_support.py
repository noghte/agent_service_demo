"""CSV, governance, persistence, audit, and HTTP plumbing for the short demo notebook."""
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import tempfile
import uuid
from itertools import islice

import requests
from agentmesh.governance import AuditLog, FileAuditSink, HashChainVerifier, Policy, PolicyEngine, PolicyRule

from ticket_contract import AGENT_ID, AGENT_INSTRUCTIONS, MODEL_ALIAS, TicketPlan, safe_error


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def read_rows(path, columns, limit=None):
    with Path(path).open(encoding='utf-8-sig', newline='') as file:
        reader = csv.DictReader(file)
        if not set(columns) <= set(reader.fieldnames or []):
            raise ValueError(f'{path}: required columns are missing')
        rows = list(islice(reader, limit))
    if not rows or any(None in r or any(not isinstance(r.get(c), str) or not r[c].strip() for c in columns) for r in rows):
        raise ValueError(f'{path}: empty or malformed required data')
    return rows


def redact(text, ticket):
    for field in ('Customer_Name', 'Customer_Email'):
        text = re.sub(re.escape(ticket[field]), '[redacted]', text, flags=re.IGNORECASE)
    return re.sub(r'[A-Za-z0-9_.+%-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', '[email]', text)


class TicketDemo:
    def __init__(self, root, model, service_url, auth_secret, output=None):
        self.root, self.model = Path(root), model
        self.service_url, self.auth_secret = service_url.rstrip('/'), auth_secret
        self.output = Path(output) if output else self.root / 'demo_output'
        self.output.mkdir(parents=True, exist_ok=True)
        self.run_id = uuid.uuid4().hex
        self.tickets = read_rows(self.root / 'tickets.csv', ['Ticket_ID', 'Customer_Name', 'Customer_Email',
            'Ticket_Subject', 'Ticket_Description', 'Issue_Category'], limit=15)
        self.policies = read_rows(self.root / 'policies.csv', ['policy_id', 'policy_text', 'effect', 'condition', 'owner'])
        if len({t['Ticket_ID'] for t in self.tickets}) != len(self.tickets) or len({p['policy_id'] for p in self.policies}) != len(self.policies):
            raise ValueError('Duplicate ticket or policy IDs')
        self.policy_hash = fingerprint(self.policies)
        self.rules = [PolicyRule(name=p['policy_id'], description=p['policy_text'],
            condition=p['condition'], action=p['effect']) for p in self.policies]
        self.engine = PolicyEngine(conflict_strategy='deny_overrides')
        self.engine.load_policy(Policy(name='ticket-demo', agent=AGENT_ID, default_action='deny', rules=self.rules))
        self.db = sqlite3.connect(self.output / 'state.sqlite3')
        self.db.execute('CREATE TABLE IF NOT EXISTS decisions (ticket_id TEXT PRIMARY KEY, fingerprint TEXT, category TEXT, status TEXT, result TEXT)')
        key_file = self.output / '.audit-key'
        if not key_file.exists():
            fd = os.open(key_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'wb') as file:
                file.write(secrets.token_bytes(32))
        self.audit_key = key_file.read_bytes()
        if len(self.audit_key) < 32:
            raise ValueError('Invalid audit key')
        self.audit_path = self.output / f'audit-{self.run_id}.jsonl'
        self.audit_sink = FileAuditSink(self.audit_path, self.audit_key)
        self.audit = AuditLog(sink=self.audit_sink)
        self.agent_hash = fingerprint([(self.root / 'agents/ticket_agent.py').read_text(), AGENT_INSTRUCTIONS,
                                       TicketPlan.model_json_schema(), (self.root / 'TOOLKIT_REVISION').read_text().strip()])

    def request(self, method, path, **kwargs):
        try:
            response = requests.request(method, self.service_url + path, timeout=(10, 120),
                headers={'Authorization': f'Bearer {self.auth_secret}'}, **kwargs)
        except requests.RequestException as error:
            raise RuntimeError(f'Agent service request failed ({type(error).__name__}); run python run_service.py') from None
        try:
            body = response.json()
        except ValueError:
            body = response.text
        if not response.ok:
            detail = body.get('detail', body) if isinstance(body, dict) else body
            detail = safe_error(detail).replace(self.auth_secret, '[redacted]') if self.auth_secret else safe_error(detail)
            raise RuntimeError(f'Agent service HTTP {response.status_code}: {detail}') from None
        if not isinstance(body, dict):
            raise ValueError('Agent service must return a JSON object')
        return body

    def log(self, ticket_id, action, outcome, **data):
        self.audit.log(event_type='ticket_decision', agent_did=AGENT_ID, action=action,
            resource=ticket_id, outcome=outcome, trace_id=self.run_id,
            data={'policy_hash': self.policy_hash, 'model': self.model, 'agent_hash': self.agent_hash, **data})

    def context(self, ticket):
        closure_demo = ticket['Ticket_ID'] == 'TKT-100000'
        facts = {'category': ticket['Issue_Category'], 'awaiting_customer': closure_demo,
                 'inactivity_days': 7 if closure_demo else 0}
        requested = 'close_ticket' if closure_demo else 'draft_response'
        evaluation = {'action': {'type': requested}, 'facts': facts}
        matched = [rule for rule in self.rules if rule.evaluate(evaluation)]
        verdict = self.engine.evaluate(AGENT_ID, evaluation)
        memory = [{'status': s, 'count': n} for s, n in self.db.execute(
            'SELECT status, count(*) FROM decisions WHERE category=? GROUP BY status LIMIT 3', (ticket['Issue_Category'],))]
        return {'ticket': {'id': ticket['Ticket_ID'], 'category': ticket['Issue_Category'],
                'subject': redact(ticket['Ticket_Subject'], ticket), 'description': redact(ticket['Ticket_Description'], ticket)},
            'policies': [{'id': p['policy_id'], 'text': p['policy_text']} for p in self.policies],
            'trusted_facts': facts, 'memory': memory, 'requested_action': requested,
            'governance': {'decision': verdict.action, 'conflict': {r.action for r in matched} == {'allow', 'deny'},
                'matched_policies': [r.name for r in matched]},
            'allowed_next_action': requested if verdict.allowed else 'escalate'}

    def process(self, ticket):
        context = self.context(ticket)
        key = fingerprint([ticket, context['trusted_facts'], self.policies, self.model, self.agent_hash])
        saved = self.db.execute('SELECT fingerprint, result FROM decisions WHERE ticket_id=?', (ticket['Ticket_ID'],)).fetchone()
        if saved and saved[0] == key:
            self.log(ticket['Ticket_ID'], 'resume', 'skipped')
            return {**json.loads(saved[1]), 'cached': True}
        self.log(ticket['Ticket_ID'], context['requested_action'], context['governance']['decision'],
                 **context['governance'], context_hash=fingerprint(context))
        thread_id = f'{self.run_id}-{ticket["Ticket_ID"]}'
        try:
            reply = self.request('POST', f'/{AGENT_ID}/invoke', json={'message': json.dumps(context),
                'model': MODEL_ALIAS, 'thread_id': thread_id, 'user_id': 'ticket-demo'})
            if reply.get('type') != 'ai' or not isinstance(reply.get('content'), str) or len(reply['content']) > 16000:
                raise ValueError('Expected one bounded AI message from the agent service')
            value = json.loads(reply['content'])
            if isinstance(value, dict) and 'error' in value:
                raise RuntimeError(f'Model call failed ({value.get("error_type", "error")}): {safe_error(value["error"])}')
            plan = TicketPlan.model_validate(value).model_dump()
            if plan['action'] != context['allowed_next_action'] or not all(plan[k].strip() for k in ('draft', 'reason')):
                raise ValueError('LLM returned an invalid action or empty response')
            plan = {**plan, 'draft': redact(plan['draft'], ticket), 'reason': redact(plan['reason'], ticket)}
            verdict = self.engine.evaluate(AGENT_ID, {'action': {'type': plan['action']}, 'facts': context['trusted_facts']})
            self.log(ticket['Ticket_ID'], plan['action'], verdict.action, matched_rule=verdict.matched_rule,
                     proposal_hash=fingerprint(plan), thread_id=thread_id, service_run_id=reply.get('run_id'))
            if not verdict.allowed:
                raise ValueError('Final action denied; no simulated tool executed')
        except Exception as error:
            self.log(ticket['Ticket_ID'], 'plan', 'error', error_type=type(error).__name__, thread_id=thread_id)
            message = redact(safe_error(error), ticket)
            raise RuntimeError(f'{ticket["Ticket_ID"]}: {message}') from None
        result = {'ticket_id': ticket['Ticket_ID'], 'category': ticket['Issue_Category'],
            'status': {'escalate': 'needs_human_review', 'draft_response': 'drafted'}[plan['action']],
            **plan, 'policy_ids': context['governance']['matched_policies'], 'conflict': context['governance']['conflict'],
            'thread_id': thread_id, 'service_run_id': reply.get('run_id'), 'cached': False}
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO decisions VALUES (?,?,?,?,?)',
                (ticket['Ticket_ID'], key, ticket['Issue_Category'], result['status'], json.dumps(result)))
        self.log(ticket['Ticket_ID'], plan['action'], 'simulated_success', status=result['status'])
        return result

    def close(self):
        self.db.close()
        self.audit_sink.close()

    def audit_report(self):
        rows = [json.loads(line) for line in self.audit_path.read_text().splitlines()]
        checkpoint = {'entries': len(rows), 'head': rows[-1]['content_hash']}
        valid = verify_audit(self.audit_path, self.audit_key, checkpoint['entries'], checkpoint['head'])
        with tempfile.TemporaryDirectory() as directory:
            changed = json.loads(json.dumps(rows))
            changed[0]['outcome'] = 'tampered'
            copy_path = Path(directory) / 'audit.jsonl'
            copy_path.write_text('\n'.join(json.dumps(row) for row in changed) + '\n')
            tampered_valid = verify_audit(copy_path, self.audit_key, checkpoint['entries'], checkpoint['head'])
        if valid:
            (self.output / f'checkpoint-{self.run_id}.json').write_text(json.dumps(checkpoint, indent=2))
        return {**checkpoint, 'audit_valid': valid, 'tampered_copy_valid': tampered_valid}


def verify_audit(path, key, count, head):
    try:
        rows = [json.loads(line) for line in Path(path).read_text().splitlines()]
        valid, errors = HashChainVerifier().verify_file(path, key)
        return valid and not errors and len(rows) == count and bool(rows) and rows[-1]['content_hash'] == head
    except (ValueError, KeyError, TypeError, OSError):
        return False
