"""The optional .module consumer is tested on a module-enabled CeTTa binary."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get('CETTA_MODULE_BIN'), 'set CETTA_MODULE_BIN to a module-enabled engine')
class ModuleAPITests(unittest.TestCase):
    def test_module_authored_schema_admits_live_fields_and_keeps_the_core_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            probe = Path(directory) / 'module.metta'
            probe.write_text('''
!(import! &self ''' + json.dumps(str(ROOT / 'src/attention_parameters.metta')) + ''')
!(import! &self ''' + json.dumps(str(ROOT / 'src/api_module_cetta.metta')) + ''')
(= (fixture:count $xs) (case $xs ((LNil 0) ((LCons $head $tail) (+ 1 (fixture:count $tail))))))
!(let* (($schema (api-module:schema ''' + json.dumps(str(ROOT / 'src/api/ComponentAPI.module')) + '''))
        ($layer (api-module:attach $schema "attention"))
        ($joined (module:join-layers $layer $layer))
        ($fields (module:layer-fields $joined (SyntaxOriginV1 "attention" "public-api-v1")))
        ($base (module:base-core $joined))
        ($bad (module:attach-layer $base (SyntaxOriginV1 "bad" "public-api-v1") $schema "Field"
          (LCons (CommandField "bad" "bad" "zero" 0 "(bad)" "wrong scalar type") LNil))))
   (println! (MODULE (fixture:count $fields)
     (== $base (GSLTLanguageDefWireV1 "ComponentMetadata" LNil LNil LNil LNil)) $bad)))
!(println! (LATER (size-atom (api:module-fields "attention"))))
''')
            result = subprocess.run([os.environ['CETTA_MODULE_BIN'], '--lang', 'petta',
                                     '--import-mode', 'ancestor-walk', str(probe)],
                                    cwd=ROOT, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('(MODULE 23 true (ModuleRefusedV1', result.stdout)
        self.assertIn('(LATER 23)', result.stdout)
        self.assertNotIn('(Error ', result.stdout)


if __name__ == '__main__':
    unittest.main()
