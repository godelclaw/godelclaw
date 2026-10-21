#!/usr/bin/env python3
"""Project existing provider rows into the finite model without repairing them."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import retry_wake_model as model
from native_lifetime import TERMINAL_RECONCILE_GRACE_SECONDS


def inspect(ledger, now=None):
    source = ROOT / 'verification/RetryWake.module'
    if hashlib.sha256(source.read_bytes()).hexdigest() != model.MODULE_SHA256:
        raise ValueError('the generated model does not match the module source')
    # Missing or inaccessible state is an error; never create an empty ledger
    # and report it as healthy. No recover(), schema migration or provider call.
    connection = sqlite3.connect(Path(ledger).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        connection.execute('BEGIN')
        rows = connection.execute('SELECT n.id,n.state,n.updated,r.status '
            'FROM native_calls n LEFT JOIN requests r ON r.id=n.id '
            "WHERE n.state IN ('claimed','submitted') ORDER BY n.created").fetchall()
    finally:
        connection.close()
    now = time.time() if now is None else now
    observations = []
    for identity, call, updated, request in rows:
        result = model.reconcile(request, call)
        known = result is not None
        terminal = model.TERMINAL.get(request, False)
        age = max(0, now-float(updated))
        settling = bool(terminal and model.ACTIVE.get(call, False) and
                        age < TERMINAL_RECONCILE_GRACE_SECONDS)
        observations.append({'id':identity, 'request':request, 'call':call,
            'model_result':result, 'modelled':known,
            'terminal_request':terminal, 'age_seconds':age, 'settlement_window':settling,
            'contradiction':bool(terminal and model.ACTIVE.get(call, False) and not settling)})
    return {'scope':'read-only terminal-request diagnostics; no action authority',
            'module_sha256':model.MODULE_SHA256, 'observations':observations}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger', type=Path, default=os.environ.get('METTACLAW_LLM_LEDGER_PATH'))
    args=parser.parse_args()
    if args.ledger is None: parser.error('supply --ledger or METTACLAW_LLM_LEDGER_PATH')
    print(json.dumps(inspect(args.ledger), sort_keys=True))


if __name__ == '__main__': main()
