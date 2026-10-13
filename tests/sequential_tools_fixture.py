"""Exercise actual tool dispatch with local files and a deterministic provider."""
import json
import tempfile
from pathlib import Path
import helper, synthetic_llm, cognitive_health, strategy_host
_calls = 0


def setup():
    global _directory, _calls
    _calls = 0
    _directory = tempfile.TemporaryDirectory()
    path = Path(_directory.name)
    (path/'source').write_text('EARLY_FINDING:' + 'x'*500 + '\n')
    helper._working_boot_cache = {}
    helper.working_boot = lambda: 8
    def chat(*args):
        global _calls
        _calls += 1
        cognitive_health.turn_started('sequential-tools-fixture')
        cognitive_health.turn_completed(10)
        if _calls == 1:
            commands = [['read-file', str(path/'missing')], ['read-lines',str(path/'source'),1,1],
                        ['not-a-tool'], ['read-lines',str(path/'source')],
                        ['write-file',str(path/'written'),'first'],
                        ['append-file',str(path/'written'),' second'],
                        ['shell','sleep 6; printf SIX_SECOND_COMMAND'],
                        ['shell','printf PARTIAL_OUTPUT; sleep 2',1],
                        ['shell','printf FINAL_COMMAND']]
            return '(' + ' '.join('('+c[0]+' '+' '.join(json.dumps(a) for a in c[1:])+')' for c in commands) + ')'
        prompt=' '.join(str(a) for a in args)
        assert 'EARLY_FINDING' in prompt and 'FINAL_COMMAND' in prompt
        assert 'PARTIAL_OUTPUT' in prompt
        assert (path/'written').read_text() == 'first second\n'
        return '((help "shell"))'
    synthetic_llm.chat=chat
    return 1


def check():
    import task_context
    assert _calls == 2
    view=task_context.projection(strategy_host.ledger(),strategy_host._focus)
    assert len(view['transcript']) == 10, len(view['transcript'])
    assert 'pending' not in view
    assert 'SIX_SECOND_COMMAND' in view['transcript'][6]['result']
    assert 'PARTIAL_OUTPUT' in view['transcript'][7]['result']
    assert 'SHELL_TIMEOUT' in view['transcript'][7]['result']
    assert 'FINAL_COMMAND' in view['transcript'][8]['result']
    return 1
