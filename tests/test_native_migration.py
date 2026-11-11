"""The native backlog must cover deployed sources and reject hidden bridges."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('native_runtime_audit', ROOT / 'scripts/audit_native_runtime.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class NativeMigrationTests(unittest.TestCase):
    def test_selected_python_has_one_explicit_migration_owner(self):
        result = audit.inventory(ROOT)
        self.assertEqual(result['unassigned'], [])
        self.assertEqual(result['overlapping'], [])
        self.assertFalse(result['runtime_python_independence_proven'])

    def test_comments_and_quoted_examples_cannot_create_or_hide_boundaries(self):
        imports, calls = audit.boundaries('''
; !(import! &self fake.py) (py-call (fake.run))
!(println! "(py-call (example.run)) ; example.py")
!(import! &self "./real module.py")
!(py-call (real.run "text ; with a comment character"))
!(import! &self (library fs))
!(import! &self $selected)
''')
        self.assertEqual([(i['target'], i['dynamic']) for i in imports],
                         [('./real module.py', False), ('$selected', True)])
        self.assertEqual([c['target'] for c in calls], ['real.run'])

    def test_python_files_cannot_disappear_behind_a_clean_metta_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'config').mkdir()
            (root / 'main.metta').write_text('!(println! "native syntax")\n')
            (root / 'run.sh').write_text('#!/bin/sh\nexec cetta main.metta\n')
            (root / 'config/runtime-files.json').write_text(json.dumps(['main.metta', 'service.py']))
            plan = dict(milestone='2026-11-15', reference_patterns=[], launchers=['run.sh'],
                        packages=[dict(id='services', python_files=['service.py'])])
            (root / 'config/native-migration.json').write_text(json.dumps(plan))
            result = audit.inventory(root)
            self.assertEqual(result['foreign_calls'], [])
            self.assertFalse(result['statically_closed'])
            plan['packages'][0]['python_files'] = []
            (root / 'config/native-migration.json').write_text(json.dumps(plan))
            self.assertEqual(audit.inventory(root)['unassigned'], ['service.py'])


if __name__ == '__main__':
    unittest.main()
