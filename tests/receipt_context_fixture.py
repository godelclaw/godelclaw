"""Real disposable receipt files and explicit native/Python view witnesses."""
import json
from pathlib import Path

import effect_receipts


ORIGINAL_VIEW = effect_receipts.view


def receipt_bytes(entries):
    return ''.join(json.dumps(entry, ensure_ascii=True, sort_keys=True) + '\n'
                   for entry in entries).encode('ascii')


def install_receipts(path, state):
    """None=absent, entries=JSONL, raw=literal file, denied=real read refusal."""
    path = Path(path)
    if path.is_file():
        path.chmod(0o600)
    if state is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if 'raw' in state:
        path.write_text(state['raw'], encoding='utf-8')
    else:
        path.write_bytes(receipt_bytes(state.get('entries', [
            {'turn': 7, 'disposition': 'returned', 'command': 'generated fixture',
             'result': 'generated result'}])))
    if state.get('denied'):
        path.chmod(0)


class ReceiptViewWitness:
    def __init__(self):
        self.native = False
        self.inside_python = lambda: False
        self.reset()

    def reset(self):
        self.calls = {'native': 0, 'python': 0}

    def install(self, native, inside_python=lambda: False):
        self.native = bool(native)
        self.inside_python = inside_python
        effect_receipts.view = self.view

    def oracle_view(self, *args, **kwargs):
        self.calls['python'] += 1
        return ORIGINAL_VIEW(*args, **kwargs)

    def view(self, *args, **kwargs):
        if self.native and not self.inside_python():
            self.calls['native'] += 1
            raise AssertionError('Python receipt view reached on native route')
        return self.oracle_view(*args, **kwargs)
