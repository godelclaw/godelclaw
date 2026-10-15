"""Developer-only runner: synthetic fixtures and measurements; all selectors run in MeTTa."""
import argparse
import json
import os
from pathlib import Path
import re
import statistics
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests'))
import test_semantic_memory_engine as fixture


def cases(test):
    scenarios = json.loads((Path(__file__).with_name('scenarios.json')).read_text())
    forms = []
    for case in scenarios['cases']:
        candidates = ' '.join('(StudyCandidate ' + ' '.join(map(json.dumps, row)) + ')'
                              for row in case['candidates'])
        forms.append('!(println! (SCENARIO %s (ax:compare-arms (ax:arms) %s (%s) 1)))'
                     % (json.dumps(case['id']), scenarios['mix'], candidates))
    output = test.run_metta('\n'.join(forms))
    results = []
    for case in scenarios['cases']:
        line = next(line for line in output.splitlines() if line.startswith('(SCENARIO ' + json.dumps(case['id'])))
        winners = {arm: {'id': identity, 'score': float(score), 'hit': identity == case['relevant']}
                   for arm, score, identity in re.findall(r'\(StudyRanking "([^"]+)" \(\(([^ ]+) "([^"]+)"\)\)\)', line)}
        if len(winners) != 4:
            raise AssertionError(line)
        results.append({'case': case['id'], 'relevant': case['relevant'], 'arms': winners})
    return results


def benchmark(test, source, label, repetitions):
    previous = fixture.ROOT
    fixture.ROOT = source
    test.env['METTACLAW_ATTENTION_PATH'] = str(test.home / (label + '-attention.metta'))
    try:
        output = test.run_metta('''
!(attention-policy "exposure" 0)
!(attention-policy "bootstrap-neighbours" 0)
!(semantic:query "alpha" 0 "similarity")
(= (study:repeat $profile $vector $n)
   (if (== $n 0) True
     (let $_ (am:select $profile $vector 10 "hybrid")
       (study:repeat $profile $vector (- $n 1)))))
(= (study:measure $profile $vector $n)
   (let* (($start (system:monotonic-ns))
          ($_ (study:repeat $profile $vector $n))
          ($elapsed (- (system:monotonic-ns) $start)))
     (/ $elapsed (* $n 1000000))))
!(case (sm:open) (((MStore $space $profile $dim)
    (let* (($_ (sm:refresh $space $profile $dim))
           ($vector (tensor:from f64 (3) (1 0 0)))
           ($_ (study:repeat $profile $vector 3)))
      (println! (TIMING (study:measure $profile $vector ''' + str(repetitions) + ''')))))
    ($other (println! (FAILED $other)))))
''', timeout=90)
        return float(re.search(r'\(TIMING ([^)]+)\)', output).group(1))
    finally:
        fixture.ROOT = previous


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--baseline-root', type=Path)
    parser.add_argument('--repetitions', type=int, default=25)
    args = parser.parse_args()
    if not all(os.environ.get(k) for k in ('CETTA_BIN', 'CETTA_SPACE_SERVICE', 'CETTA_VALUE_WORKER')):
        parser.error('set CETTA_BIN, CETTA_SPACE_SERVICE and CETTA_VALUE_WORKER as for native memory tests')
    test = fixture.MemoryTests()
    test.setUp()
    try:
        result = {'scope': 'Synthetic mechanism probes, not evidence of general task improvement',
                  'cases': cases(test)}
        test.collection.add([f'p{i}' for i in range(100)],
                            [[1, (i+1)/101, .1] for i in range(100)],
                            [f'synthetic {i}' for i in range(100)], [{} for _ in range(100)])
        samples = {'new': []}
        if args.baseline_root:
            samples['baseline'] = []
        for _ in range(3):
            if args.baseline_root:
                samples['baseline'].append(benchmark(test, args.baseline_root.resolve(), 'old', args.repetitions))
            samples['new'].append(benchmark(test, ROOT, 'new', args.repetitions))
        result['warm_selector_ms'] = {key: {'samples': values, 'median': statistics.median(values)}
                                      for key, values in samples.items()}
        result['timing_scope'] = '102 synthetic 3D memories, warm native selector plus snapshot save; excludes embedding and provider calls'
    finally:
        test.doCleanups()
    text = json.dumps(result, indent=2) + '\n'
    if args.output:
        args.output.write_text(text)
    print(text)


if __name__ == '__main__':
    main()
