import itertools
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'ui/worklog')]
import stream_sink
from stream_lifecycle import TRANSITIONS
from viewer import Reader,clean,lines


class ProjectionLaws(unittest.TestCase):
    def test_generated_lifecycle_is_the_real_projection_transition(self):
        events={"ProviderText":("text.delta","provider"),"ProviderTool":("tool.forming","provider"),
            "ExecutorStart":("tool.started","executor"),"ExecutorFinish":("tool.finished","executor"),
            "TerminalFinish":("text.snapshot","terminal"),"Interrupt":("attempt.finished","terminal")}
        for (state,event),expected in TRANSITIONS.items():
            with self.subTest(state=state,event=event):
                p=stream_sink.Projection()
                base=dict(producer="p",sequence=1,session="s",item="i",attempt="a",kind="tool.forming")
                p.apply(base)
                key=("a","i",None,"")
                item=p.sessions["s"]["items"][key]
                item.update(lifecycle=state,terminal=state=="Finished")
                kind,source=events[event]
                p.apply({**base,"sequence":2,"kind":kind,"source":source,"text":"x"})
                self.assertEqual(item["lifecycle"],expected)
                before=dict(item)
                self.assertFalse(p.apply({**base,"sequence":2,"kind":kind,"source":source,"text":"x"}))
                self.assertEqual(item,before)

    @unittest.skipUnless(os.environ.get("CETTA_BIN"),"set CETTA_BIN")
    def test_native_table_matches_all_source_rows_exactly(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/"trace.metta"
            forms=['!(import! &self '+json.dumps(str(ROOT/'src/stream_lifecycle.metta'))+')']
            forms += [f'!(println! (TRACE {s} {e} (display:step {s} {e})))' for s,e in TRANSITIONS]
            source.write_text('\n'.join(forms))
            result=subprocess.run([os.environ['CETTA_BIN'],'--lang','petta',str(source)],capture_output=True,text=True,check=True)
            self.assertEqual([r for r in result.stdout.splitlines() if r.startswith('(TRACE ')],
                [f'(TRACE {s} {e} {t})' for (s,e),t in TRANSITIONS.items()])

    def test_provider_cannot_claim_a_running_execution(self):
        p=stream_sink.Projection()
        p.apply(dict(producer="p",sequence=1,session="s",kind="tool.started",source="provider",item="i",status="running"))
        self.assertNotEqual(next(iter(p.sessions['s']['items'].values()))['status'],'running')

    def test_finished_receipts_do_not_become_uncertain_when_the_worker_exits(self):
        p=stream_sink.Projection();base=dict(producer='p',session='s',pid=1,process_start='fixture')
        p.apply({**base,'sequence':1,'kind':'patch','source':'executor','item':'patch','status':'applied','text':'Success'})
        p.apply({**base,'sequence':2,'kind':'tool.forming','attempt':'a','item':'tool','name':'exec_command'})
        p.apply({**base,'sequence':3,'kind':'attempt.finished','attempt':'a','source':'terminal','status':'completed'})
        p.refresh_owners()
        items=p.sessions['s']['items']
        self.assertEqual(items[('', 'patch',None,'')]['status'],'applied')
        self.assertEqual(items[('a','tool',None,'')]['status'],'proposed')

    def test_terminal_reconciles_missing_and_changed_items(self):
        p=stream_sink.Projection();base=dict(producer="p",session="s",attempt="a")
        p.apply({**base,"sequence":1,"kind":"text.delta","item":"old","channel":"commentary","text":"wrong"})
        p.apply({**base,"sequence":2,"kind":"attempt.finished","source":"terminal","status":"completed",
            "texts":[dict(item="final",channel="commentary",text="correct")]})
        text=[v for v in p.sessions['s']['items'].values() if v.get('channel')]
        self.assertEqual([v['text'] for v in text],['correct'])
        p.apply({**base,"sequence":3,"kind":"text.delta","item":"final","channel":"commentary","text":"late"})
        self.assertEqual(text[0]['text'],'correct')

    def test_epoch_invalidation_reconnect_and_terminal_sanitization(self):
        with tempfile.TemporaryDirectory() as d:
            w=stream_sink.Writer(d,'s')
            w.emit(dict(kind='text.delta',item='i',text='first\x1b[2J'))
            self.assertTrue(w.flush(2))
            a,b=Reader(d),Reader(d)
            self.assertEqual(a.read(),b.read());self.assertEqual(a.read(),[])
            Path(d,'control.json').write_text(json.dumps(dict(enabled=False,epoch='off')))
            self.assertEqual(w.emit(dict(kind='text.delta',item='i',text='hidden')),0)
            Path(d,'control.json').write_text(json.dumps(dict(enabled=True,epoch='new')))
            self.assertEqual(w.emit(dict(kind='text.delta',item='i',text='stale',epoch='initial')),0)
            self.assertEqual(Reader(d).read(),[])
            w.emit(dict(kind='text.delta',item='i',text='new'));w.flush(2)
            self.assertEqual([e['text'] for e in Reader(d).read()],['new'])
        self.assertEqual(clean('old\rnew\x1b[2J\x1b]52;c;SECRET\x07\u202e'), 'new ')

    def test_queue_pressure_preserves_terminal_and_bounds_display(self):
        with tempfile.TemporaryDirectory() as d:
            w=stream_sink.Writer(d,'s',capacity=2)
            for n in range(500): w.emit(dict(kind='text.delta',item='i',text='x'*100))
            w.emit(dict(kind='attempt.finished',item='a',attempt='a',status='completed',texts=[]))
            self.assertTrue(w.flush(2))
            rows=Reader(d).read()
            self.assertTrue(any(r['kind']=='attempt.finished' for r in rows))
            self.assertTrue(w.dropped or any(r.get('display_gap') for r in rows))
            p=stream_sink.Projection(max_items=4,tail_chars=50)
            for n in range(100):p.apply(dict(producer='p',sequence=n+1,session='s',kind='text.delta',item=str(n),text='x'*100))
            self.assertEqual(len(p.sessions['s']['items']),4)
            self.assertTrue(all(len(v['text'])==50 for v in p.sessions['s']['items'].values()))


if __name__=='__main__':unittest.main()
