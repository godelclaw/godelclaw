"""Reached JSON/name/bridge and TextIO buffer edge parity; no live state."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

from effect_receipts_support import IMPORTS, emit, limit, rows
from mode_state_modules_check import poison
from mode_state_native_check import CAPTURE, SNAPSHOT, bridge, captured, literal, python
from mode_state_support import Qualification


def text(c):
    with c.phase('strict-buffered-text-and-oserror-domains'):
        fixture=poison(c);records=[]
        cases=[('long-invalid',b' '*8192+b'\xff'),('two-chunks-invalid',b' '*16384+b'\xe2(\xa1'),
               ('pending-eof',b' '*8191+b'\xe2'),('cr-boundary-invalid',b' '*8191+b'\r\xff'),
               ('early-line-late-invalid',b'cetta\n'+b'a'*8190+b'\xff'),
               ('pending-after-line',b'cetta\n'+b'a'*8185+b'\xc3'),
               ('crlf-boundary',b' '*8186+b'cetta\r\npetta'),
               ('valid-cr',b'cetta\rpetta\r\n'),('bom',b'\xef\xbb\xbfcetta\n'),
               ('multibyte-invalid',b'\xe2\x82x'),('surrogate-utf8',b'\xed\xa0\x80'),
               ('invalid-prefix',b'\xf5\x80\x80\x80')]
        for number,(label,raw) in enumerate(cases):
            (c.directory/'engine-state').write_bytes(raw)
            # Iteration in _last_failure has a distinct incremental decode
            # domain from JSON read(), even when the final field is unused.
            (c.directory/'engine-failure').write_bytes(b'engine=cetta\nreason=fixture\n'+raw)
            expected=python(c,'text-oracle-%02d'%number,'''import json,engine_modes
def capture(call):
    try:return call()
    except Exception as exc:return {'error':type(exc).__name__,'message':str(exc)}
print(json.dumps([capture(engine_modes.selected_engine),capture(engine_modes.engine_view)],ensure_ascii=True))
''')
            source=IMPORTS+fixture+'!(import! &self ./src/engine_modes.metta)\n'+CAPTURE
            actual=rows(c.run('text-native-%02d'%number,source+''.join(emit(captured(x)) for x in
                            ('(engine:selected)','(engine:view)'))))
            pair={'label':label,'hex':raw.hex(),'actual':actual,'expected':expected}
            c.save('text-%02d.json'%number,pair);assert actual==expected,pair;records.append(pair)
        blocker=c.directory/'blocker';blocker.write_text('not a directory')
        for number,path in enumerate((blocker/'state',c.directory/('x'*300))):
            env={'METTACLAW_ENGINE_STATE_PATH':str(path),'METTACLAW_ENGINE_FAILURE_PATH':str(path)}
            expected=python(c,'oserror-oracle-%02d'%number,'import json,engine_modes\nprint(json.dumps([engine_modes.selected_engine(),engine_modes.engine_view()]))\n',env)
            actual=rows(c.run('oserror-native-%02d'%number,source+emit(captured('(engine:selected)'))+emit(captured('(engine:view)')),env))
            pair={'path':str(path),'actual':actual,'expected':expected}
            c.save('oserror-%02d.json'%number,pair);assert actual==expected,pair;records.append(pair)
        c.save('text-result.json',{'status':'PASS_STRICT_TEXT_BUFFER_DOMAINS','cases':records})


def values(c):
    with c.phase('all-whitespace-json-and-real-bridge-values'):
        fixture=poison(c);records=[]
        raws=[b'{}',b'[]',b'null',b'true',b'3',b'"coding"',b'{',b'\xff',b'\xef\xbb\xbf{}',
              b'{"mode":"coding"}\r',b'{"mode":"omega","mode":"coding"}',
              b'{"mode":true}',b'{"mode":null}',b'{"mode":["coding"]}',b'{"mode":{"x":"coding"}}',
              b'{"mode":NaN}',b'{"mode":Infinity}',b'{"mode":"\\ud800"}']
        for schema in (1,1.0,True,False,0,2,None,'1',[],{},float('nan'),float('inf')):
            raws.append(json.dumps({'schema':schema,'state':'running'}).encode())
        raws += [b'{"schema":0,"schema":true,"state":"running"}',
                 b'{"state":"stopped","state":"running","schema":1}',
                 b'{"schema":1,"state":"running","extra":0}',b'{"schema":1,"state":true}']
        spaces=[chr(n) for n in range(sys.maxunicode+1) if chr(n).isspace()]
        raws += [json.dumps({'mode':cp+'ITER-CODING'+cp}).encode() for cp in spaces]
        for number,raw in enumerate(raws):
            for name in ('loop-mode.json','lifecycle.json'): (c.directory/name).write_bytes(raw)
            expected=python(c,'value-oracle-%03d'%number,'''import json,loop_modes,lifecycle
print(json.dumps([loop_modes.current_mode(),loop_modes.mode_view(),lifecycle.cognition_enabled(),lifecycle.view()],ensure_ascii=True))
''')
            source=IMPORTS+fixture+'!(import! &self ./src/mode_state.metta)\n'
            actual=rows(c.run('value-native-%03d'%number,source+''.join(map(emit,
                  ('(loop-mode:current)','(mode:view)','(lifecycle:enabled)','(lifecycle:view)')))))
            assert actual==expected,(raw,actual,expected)
            records.append({'hex':raw.hex(),'actual':actual})
        process_values=[cp+'CoDiNg'+cp for cp in spaces]+['agent','default','generic','claw23','iter-coding',
                    'unknown','','İ','coding\udcff','\udc80coding']
        (c.directory/'loop-mode.json').write_text('{"mode":"omega"}')
        for number,value in enumerate(process_values):
            env={'METTACLAW_ACTIVE_LOOP_MODE':value}
            expected=python(c,'process-oracle-%02d'%number,'import json,loop_modes\nprint(json.dumps(loop_modes.process_mode()))\n',env)
            actual=rows(c.run('process-native-%02d'%number,source+emit('(str:symbol->string (mode:process))'),env))
            c.save('process-case-%02d.json'%number,{'input':value,'actual':actual,'expected':expected})
            assert actual==[expected],(value,actual,expected)
        waits=['rested 5s','rested ','rested','RESTED 5s','woken',0,False,True,[],['rested 5s']]
        expected=python(c,'wait-bridge-oracle','import json,loop_modes\nprint(json.dumps([loop_modes.wait_timed_out(v) for v in '+repr([bridge(v) for v in waits])+']))\n')
        actual=rows(c.run('wait-bridge-native',source+''.join(emit('(mode:wait-timed-out %s)'%literal(v)) for v in waits)))
        assert actual==expected,(actual,expected)
        c.save('values-result.json',{'status':'PASS_VALUE_AND_WHITESPACE_DOMAINS','json_states':records,
               'whitespace_codepoints':[ord(x) for x in spaces],'process_inputs':process_values,'wait_inputs':waits,
               'wait_results':actual,'bridge_domain':'actual number/string/list/Bool-to-string crossings, not arbitrary Python objects'})


def os_paths(c):
    with c.phase('surrogateescaped-os-paths-and-printed-errors'):
        fixture=poison(c);records=[];namespace={};exec(SNAPSHOT,namespace)
        for number,name in enumerate(('state\udcff','state\udced\udca0\udc80',"é\udc80日'")):
            for case in ('success','readonly','target-directory'):
                for actor,native,oracle in (('loop','(mode:set "coding")','loop_modes.set_mode("coding")'),
                                           ('fuel','(fuel:set "carry")','fuel_modes.set_fuel("carry")')):
                    root=c.directory/'os-path-state'
                    env={'METTACLAW_LOOP_MODE_PATH':str(root/name),'METTACLAW_FUEL_MODE_PATH':str(root/name),
                         'METTACLAW_RECYCLE_REQUEST_PATH':''}
                    observed=[]
                    for lane in ('oracle','native'):
                        if root.exists():root.chmod(0o700);shutil.rmtree(root)
                        root.mkdir()
                        if case=='readonly':root.chmod(0o500)
                        if case=='target-directory':(root/name).mkdir()
                        label='os-path-%d-%s-%s-%s'%(number,case,actor,lane)
                        if lane=='oracle':
                            value=python(c,label,'''import contextlib,io,json,loop_modes,fuel_modes
printed=io.StringIO()
with contextlib.redirect_stdout(printed):result='''+oracle+'''
print(json.dumps({'result':result,'printed':printed.getvalue().splitlines()}))
''',env)
                        else:
                            source=IMPORTS+fixture+'!(import! &self ./src/mode_state.metta)\n!(import! &self ./src/fuel_cetta.metta)\n'+CAPTURE
                            output=c.run(label,source+emit(captured(native)),env)
                            result=rows(output);assert len(result)==1,output
                            value={'result':result[0],'printed':[line for line in output.splitlines()
                                        if line.startswith(('[loop-mode]','[fuel-mode]'))]}
                        value['files']=namespace['snapshot'](root);observed.append(value)
                    row={'name':name,'case':case,'actor':actor,'oracle':observed[0],'native':observed[1]}
                    c.save(label+'-pair.json',row);assert observed[0]==observed[1],row;records.append(row)
        root.chmod(0o700)
        c.save('os-path-results.json',{'status':'PASS_OS_PATH_ERROR_REPR','cases':records,
               'boundary':'raw filesystem bytes retained; UTF-8 surrogateescape ONLY for Python error-path repr'})


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--evidence',type=Path,required=True)
    p.add_argument('--phase',choices=('text','values','os-paths'),required=True)
    args=p.parse_args();limit();args.evidence.mkdir(parents=True,exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='mode06-edges-') as directory:
        c=Qualification(args.evidence.resolve(),Path(directory));c.snapshot_sources('tests/mode_state_edges_check.py','src/mode_io_cetta.metta','src/loop_modes_cetta.metta')
        {'text':text,'values':values,'os-paths':os_paths}[args.phase](c)


if __name__=='__main__':main()
