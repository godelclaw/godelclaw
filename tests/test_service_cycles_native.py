"""Operational cycles commit one outcome without changing search answer bags."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

import test_command_dispatch_engine as dispatch_fixture

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get('CETTA_BIN'), 'set CETTA_BIN to the native engine')
class ServiceCyclesTest(unittest.TestCase):
    def probe(self, module, driver, body):
        # Load the actual recursive driver with synthetic effectful cycles.
        # Imported equations have separate ownership; removing a local rule
        # would not replace that imported definition in the real evaluator.
        source = (ROOT / module).read_text()
        start = source.index('(= (' + driver)
        depth = 0
        for end in range(start, len(source)):
            depth += (source[end] == '(') - (source[end] == ')')
            if depth == 0:
                break
        definition = source[start:end + 1]
        definition = definition.replace('(py-call (helper.recycle_requested))', '0')
        with tempfile.TemporaryDirectory(prefix='service-cycles-') as folder:
            path = Path(folder) / 'probe.metta'
            path.write_text('!(import! &self system)\n' + definition + '\n' + body)
            env = dispatch_fixture.CommandDispatchEngineTest()._cetta_env()
            env.update(METTACLAW_ENGINE_STATE_PATH=str(Path(folder) / 'engine'),
                       METTACLAW_SKIP_INITIALIZE='1',
                       METTACLAW_RECYCLE_REQUEST_PATH=str(Path(folder) / 'recycle'),
                       METTACLAW_WORKING_SET_PATH=str(Path(folder) / 'working.json'),
                       METTACLAW_STRATEGY_PATH=str(Path(folder) / 'strategy.db'),
                       METTACLAW_HISTORY_PATH=str(Path(folder) / 'history.metta'),
                       CETTA_PETTA_CHOICE_KIND_TRACE='1')
            result = subprocess.run([env['CETTA_BIN'], '--lang', 'petta',
                '--import-mode', 'ancestor-walk', str(path)], cwd=ROOT, env=env,
                capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr[-3000:])
            self.assertIn('CYCLES_DONE', result.stdout)
            self.assertNotIn('REPLAYED_ALTERNATIVE', result.stdout)
            depths = [int(n) for n in re.findall(r'\[petta-choice-kind\].*?depth=(\d+)', result.stderr)]
            self.assertLessEqual(max(depths, default=0), 16, result.stderr[-3000:])

    def test_responder_commits_each_cycle(self):
        self.probe('channels/telegram_control.metta', 'tc:serve)', '''
!(bind! &cycle-count (new-state 0))
(= (fixture:choice) first)
(= (fixture:choice) second)
(= (tc:cycle)
   (if (>= (get-state &cycle-count) 500)
     (progn (println! CYCLES_DONE) (system:exit 0))
     (let $next (+ (get-state &cycle-count) 1)
       (let $_ (change-state! &cycle-count $next)
         (and True (case (fixture:choice)
           ((first True) ($other (progn (println! REPLAYED_ALTERNATIVE) False)))))))))
!(tc:serve)
''')

    def test_agent_loop_commits_successor_but_keeps_search_bag(self):
        self.probe('src/loop.metta', 'processLoop ', '''
(= (fixture:choice) first)
(= (fixture:choice) second)
(= (processCycle $n)
   (if (>= $n 500)
     (progn (println! CYCLES_DONE) (system:exit 0))
     (let $found (collapse (superpose (evidence-a evidence-b evidence-a)))
       (let $_ (and True (case (fixture:choice)
                   ((first True) ($other (progn (println! REPLAYED_ALTERNATIVE) False)))))
         (if (== (size-atom $found) 3) (+ $n 1) (system:exit 2))))))
!(processLoop 0)
''')


if __name__ == '__main__':
    unittest.main()
