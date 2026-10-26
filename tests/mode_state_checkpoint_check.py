"""Serial source-qualified project06 gates; retain every actual child outcome."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from effect_receipts_support import ROOT, limit
from mode_state_support import Qualification


def source_names():
    names = [p.relative_to(ROOT).as_posix() for folder in ('src','channels')
             for p in sorted((ROOT/folder).glob('*.metta'))]
    names += ['src/'+n+'.py' for n in ('engine_modes','loop_modes','lifecycle','fuel_modes','attention')]
    names += [p.relative_to(ROOT).as_posix() for p in sorted((ROOT/'tests').glob('mode_state*.py'))]
    names += ['config/runtime-files.json','tests/test_small_views_native.py',
              'tests/test_mode_state_support.py','tests/effect_receipts_bundle.py',
              'tests/effect_receipts_support.py','tests/effect_receipts_capability_checkpoint.py','tests/isolated_env.py']
    return names


def run(c, name, command, env, cwd=ROOT, timeout=300):
    start = time.perf_counter_ns()
    child = subprocess.run(list(map(str,command)),cwd=cwd,env=env,capture_output=True,
                           timeout=timeout,preexec_fn=limit)
    elapsed = time.perf_counter_ns()-start
    c.record_process(name,command,child,env,cwd=cwd)
    c.save(name+'-timing.json',{'elapsed_ns':elapsed,'not_turn_cost':True})
    return child.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--phase',choices=('final-source','regressions'),required=True)
    parser.add_argument('--swipl',type=Path)
    parser.add_argument('--swi-root',type=Path)
    args=parser.parse_args();limit()
    args.evidence.mkdir(parents=True,exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='mode06-final-') as directory:
        c=Qualification(args.evidence.resolve(),Path(directory))
        with c.phase(args.phase+'-partial-checkpoint'):
            c.snapshot_sources(*source_names())
            env=c.environment()
            if args.swi_root:
                env['PETTA_ROOT']=str(args.swi_root)
            if args.swipl:
                env['PATH']=str(args.swipl.parent)+os.pathsep+env['PATH']
            for key in ('MODE_STATE_ENGINE_SHA256','MODE_STATE_MANIFEST_SHA256',
                        'MODE_STATE_LEAN_ROOT','MODE_STATE_LEAN_BIN'):
                if key in os.environ: env[key]=os.environ[key]
            exits={}
            if args.phase=='final-source':
                assert args.swipl and args.swi_root
                suites=[('dependencies','mode_state_artifact_check.py',[]),
                        ('active-fuel','mode_state_artifact_check.py',['--phase','active-fuel','--swipl',args.swipl,'--swi-root',args.swi_root]),
                        ('modules','mode_state_modules_check.py',[]),
                        ('boundaries','mode_state_modules_check.py',['--phase','boundaries']),
                        ('sequences','mode_state_sequences_check.py',[]),
                        ('callers','mode_state_callers_check.py',[]),
                        ('close-order','mode_state_close_order_check.py',[]),
                        ('protocol','mode_state_protocol_check.py',[]),
                        ('mixed','mode_state_mixed_check.py',[]),
                        ('handler-errors','mode_state_handler_errors_check.py',[]),
                        ('switches','mode_state_switches_check.py',[]),
                        ('swi','mode_state_swi_check.py',['--swipl',args.swipl,'--swi-root',args.swi_root]),
                        ('text-edges','mode_state_edges_check.py',['--phase','text']),
                        ('value-edges','mode_state_edges_check.py',['--phase','values']),
                        ('os-paths','mode_state_edges_check.py',['--phase','os-paths']),
                        ('dispatch','mode_state_dispatch_check.py',[]),
                        ('lean','mode_state_lean_check.py',[]),
                        ('cold-cost','mode_state_cost_check.py',[])]
                for name,script,extra in suites:
                    target=c.evidence/name
                    command=[sys.executable,ROOT/'tests'/script,'--evidence',target,*extra]
                    exits[name]=run(c,'driver-'+name,command,env,timeout=600)
                    assert exits[name]==0,(name,exits[name])
                ordering=json.loads((c.evidence/'close-order/result.json').read_text())
                assert ordering['status']=='PASS_ENGINE_CLOSE_PATH_CHMOD_ORDER'
            else:
                package=c.directory/'package';package.mkdir()
                hashes={}
                import hashlib
                for part in ('src','channels','tests','verification','config','scripts','systemd'):
                    if (ROOT/part).is_dir():
                        shutil.copytree(ROOT/part,package/part,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
                for p in ROOT.glob('*.metta'):shutil.copy2(p,package/p.name)
                for name in json.loads((ROOT/'config/runtime-files.json').read_text()):
                    source,target=ROOT/name,package/name
                    assert source.is_file(),name
                    target.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copy2(source,target)
                # Boot-order regressions need the package name, never a real
                # Chroma import/client/collection initializer. Fixture only.
                chroma=package/'repos/petta_lib_chromadb'
                chroma.mkdir(parents=True)
                (chroma/'lib_chromadb.metta').write_text('; Generated inert import fixture; no Chroma/provider initialization.\n')
                (chroma/'lib_chromadb.py').write_text('raise AssertionError("real Chroma Python import forbidden in mode-state tests")\n')
                for path in sorted(package.rglob('*')):
                    if path.is_file():hashes[path.relative_to(package).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
                c.save('staged-source-hashes.json',hashes)
                env['PYTHONPATH']=os.pathsep.join(str(package/part) for part in ('tests','src','channels'))
                env['GOAL_BOOKKEEPING_ENGINE_SHA256']=env.get('MODE_STATE_ENGINE_SHA256','')
                env['GOAL_BOOKKEEPING_MANIFEST_SHA256']=env.get('MODE_STATE_MANIFEST_SHA256','')
                env['GOAL_BOOKKEEPING_EVIDENCE']=str(c.evidence/'goal-receipts')
                env['CONTEXT_BUNDLE_EVIDENCE']=str(c.evidence/'context-receipts')
                suites=[('static',['test_mode_state_support','test_effect_receipts_bundle','test_effect_receipts_source']),
                        ('prior-mode-fuel-state',['test_fuel_cetta','test_engine_modes','test_loop_modes','test_lifecycle','test_native_state']),
                        ('prior-goal-callers',['test_goal_bookkeeping_callers','test_goal_bookkeeping_load','test_goal_views_native']),
                        ('prior-small-views',['test_small_views_native'])]
                for name,modules in suites:
                    command=[sys.executable,'-m','unittest','-v',*modules]
                    if name=='prior-small-views':
                        c.save('unselected-prior-model-test.json',{
                            'test':'test_small_views_native.SmallViewsTest.test_capability_lean_projection_replay',
                            'reason':'legacy +toolchain selector not invoked; no downloader/selector fallback; direct exact fuel/lifecycle models qualified separately',
                            'status':'UNRUN, not a unittest skip or passing proof'})
                        command=[sys.executable,'-c',
                            'import unittest,sys,test_small_views_native as m; '
                            'names=unittest.defaultTestLoader.getTestCaseNames(m.SmallViewsTest); '
                            'suite=unittest.TestSuite(m.SmallViewsTest(n) for n in names '
                            'if n!="test_capability_lean_projection_replay"); '
                            'r=unittest.TextTestRunner(verbosity=2).run(suite); '
                            'sys.exit(not r.wasSuccessful())']
                    exits[name]=run(c,name,command,env,cwd=package,timeout=300)
            c.save('result.json',{'status':'PASS_CHECKED_SUBSET' if all(v==0 for v in exits.values()) else 'FAILED_CHECKED_SUBSET',
                   'full_project06_complete':False,'phase':args.phase,'actual_exits':exits,
                   'limits':'selected phase only; inspect child semantic results and separate regression/model limitations; suite durations are not turn costs; failures/skips retained verbatim'})
    return 0


if __name__=='__main__':sys.exit(main())
