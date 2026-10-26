"""Diagnose the actual unchanged policy input crossing, no turn/provider run."""
import argparse
from pathlib import Path
import resource
import tempfile

from effect_receipts_capability_checkpoint import LIMIT
from effect_receipts_support import IMPORTS
from mode_state_support import Qualification


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--evidence', type=Path, required=True)
args = parser.parse_args()
resource.setrlimit(resource.RLIMIT_AS, (LIMIT, LIMIT))
args.evidence.mkdir(parents=True, exist_ok=False)
with tempfile.TemporaryDirectory(prefix='mode06-policy-crossing-') as directory:
    c = Qualification(args.evidence.resolve(), Path(directory))
    c.snapshot_sources('tests/mode_state_policy_crossing_check.py','src/loop_policy.metta',
                       'src/open_assemblage.metta','src/mode_state.metta')
    with c.phase('policy-crossing-diagnostic'):
        source = IMPORTS + '''
!(import! &self ./src/loop_modes.py)
!(import! &self ./src/mode_state.metta)
!(import! &self ./src/open_assemblage.metta)
!(import! &self ./src/loop_policy.metta)
!(println! (SymbolGrant (collapse (policy-burst-budget coding))))
!(println! (StringGrant (collapse (policy-burst-budget "coding"))))
!(println! (PythonCrossing (py-call (loop_modes.process_mode))))
!(println! (PythonType (get-type (py-call (loop_modes.process_mode)))))
!(println! (PythonGrant (collapse (policy-burst-budget (py-call (loop_modes.process_mode))))))
!(println! (NativeCrossing (mode:process)))
!(println! (NativeType (get-type (mode:process))))
!(println! (NativeGrant (collapse (policy-burst-budget (mode:process)))))
'''
        output = c.run('actual-policy-crossings', source, {'METTACLAW_ACTIVE_LOOP_MODE':'coding'})
        c.save('result.json', {'stdout':output,
               'scope':'diagnostic includes explicitly labeled Python oracle calls, not a native no-fallback gate',
               'full_project06_complete':False})
