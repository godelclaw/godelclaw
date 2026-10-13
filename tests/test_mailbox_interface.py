"""Replyable messages and automatic asks against real native mailbox storage."""
import json
import os
from pathlib import Path
import sys
import subprocess
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
CETTA=Path(os.environ.get("CETTA_MAILBOX_ROOT",ROOT.parent/"CeTTa-ours"))
TOOLS=Path(os.environ.get("CETTA_MAILBOX_TOOLS",CETTA/"tools"))
sys.path[:0]=[str(ROOT/"src"),str(ROOT/"channels"),str(TOOLS),str(CETTA/"tests")]
from test_native_mailbox import Fixture,SERVER,WORKER
from mailbox_bridge import DirectoryBridge,DeliveryUncertain,deliver
from native_mailbox import Mailbox
import mailbox_agent as agent
from observed_strategy import Ledger


@unittest.skipUnless(SERVER and WORKER,"set CETTA_SPACE_SERVICE and CETTA_VALUE_WORKER")
class InterfaceTests(unittest.TestCase):
    def setUp(self):
        self.fixture=Fixture();self.addCleanup(self.fixture.close)
        self.root=self.fixture.path
        env=mock.patch.dict(os.environ,METTACLAW_OBSERVED_STRATEGY="1",
            METTACLAW_STRATEGY_PATH=str(self.root/"ledger.db"),METTACLAW_MAILBOX_CONFIG="")
        env.start();self.addCleanup(env.stop)
        self.store=Ledger(self.root/"ledger.db")
        Ledger(self.root/"lila-ledger.db")
        self.store.accept("guide","Write, await review, revise")
        self.events={"godel":[],"lila":[]}
        self.boxes={};self.bridges={}
        for name,peer in (("godel","lila"),("lila","godel")):
            config={"version":1,"instance":name,"name":name,"socket":self.fixture.socket,
                "space":name,"tools":str(TOOLS),"worker":WORKER,
                "channels":{"inbox":"local"},"auto_receive":["inbox"],
                "siblings":{peer:str(self.root/peer)},"contacts":{"codex":str(self.root/"codex")}}
            box=agent.Inbox(config,committed=lambda _:False,
                offer=lambda text,identity,n=name,**kw:self.events[n].append((json.loads(text),identity,kw)) or True)
            self.boxes[name]=box;self.addCleanup(box.close)
            p=mock.patch.object(box,"start",return_value=1);p.start();self.addCleanup(p.stop)
            writer=Mailbox(self.fixture.socket,name,WORKER);self.addCleanup(writer.close)
            bridge=DirectoryBridge(writer,[{"name":"inbox","sender":"local","directory":str(self.root/name)}])
            self.bridges[name]=bridge;self.addCleanup(bridge.close)

    def receive(self,name):
        path=self.root/("ledger.db" if name=="godel" else "lila-ledger.db")
        with mock.patch.dict(os.environ,METTACLAW_STRATEGY_PATH=str(path)):
            self.bridges[name].scan();self.boxes[name].scan(self.boxes[name].box)
        return self.events[name][-1][0]

    def answer(self,message,text="review"):
        with mock.patch.object(agent,"runtime",return_value=self.boxes["lila"]):
            result=agent.reply(message,text)
        self.assertTrue(result.startswith("MAILED godel message="),result)
        return result

    def test_immediate_reply_matches_generated_receive_and_uses_one_handle(self):
        publish=agent._publish
        def immediate(*args,**kwargs):
            result=publish(*args,**kwargs)
            question=self.receive("lila")
            self.assertEqual(question["from"],"godel")
            self.assertNotIn("ticket",question);self.assertNotIn("correlation",question)
            # Do not intercept publication of the reply recursively.
            with mock.patch.object(agent,"_publish",side_effect=publish):
                self.answer(question["message"])
            self.receive("godel")
            return result
        with mock.patch.object(agent,"_publish",side_effect=immediate):
            result=self.boxes["godel"].ask("lila","Review the page","Revise using the review","request-one",task="guide")
        event=self.events["godel"][0][0]
        self.assertEqual(result["state"],"ready")
        self.assertEqual(event["task"],"guide");self.assertEqual(event["resume_note"],"Revise using the review")
        self.assertEqual(event["in_reply_to"],result["message"])
        self.assertEqual(self.boxes["godel"].read(event["message"])["body"],"review")
        self.assertEqual(self.boxes["godel"].read(result["message"])["body"],"review")

    def test_restart_preserves_ask_without_republication_and_duplicate_input(self):
        box=self.boxes["godel"]
        box.ask("lila","Review","Revise","request-two",task="guide")
        question=self.receive("lila")
        config=box.config;box.close();self.fixture.restart()
        for bridge in self.bridges.values():bridge.mailbox.reconnect()
        restarted=agent.Inbox(config,committed=lambda _:False,
            offer=lambda text,identity,**kw:self.events["godel"].append((json.loads(text),identity,kw)) or True)
        self.boxes["godel"]=restarted;self.addCleanup(restarted.close)
        with mock.patch.object(agent,"_publish") as publish:
            self.assertEqual(restarted.ask("lila","Review","Revise","request-two",task="guide")["delivery"],"published")
            publish.assert_not_called()
        self.answer(question["message"]);self.receive("godel")
        reply=json.loads(next((self.root/"godel"/"received").glob("*.json")).read_text())
        deliver(self.root/"godel",reply["id"],reply["correlation"],reply["sender"],reply["body"],headers=reply["headers"])
        self.receive("godel")
        self.assertEqual(len(self.events["godel"]),1)
        restarted.cancel(self.events["godel"][0][0]["message"])
        self.assertEqual(restarted.read("request-two")["state"],"ready")

    def test_failed_and_unknown_publications_do_not_park_or_repeat(self):
        box=self.boxes["godel"]
        for identity,error,state in (("failed",OSError("disk unavailable"),"failed"),
                                      ("unknown",DeliveryUncertain("lost fsync receipt"),"unknown")):
            with mock.patch.object(agent,"_publish",side_effect=error):
                result=box.ask("lila","Review","Resume",identity,task="guide")
            self.assertEqual(result["state"],state)
            self.assertNotEqual(self.store.request_record("guide")["status"],"waiting-mailbox")
            with mock.patch.object(agent,"_publish") as publish:
                self.assertEqual(box.ask("lila","Review","Resume",identity,task="guide")["delivery"],state)
                publish.assert_not_called()
        self.assertEqual(box.ask("lila","New question","Continue","fresh",task="guide")["delivery"],"published")

    def test_two_questions_to_same_peer_resume_separate_tasks_out_of_order(self):
        self.store.accept("other","Another task")
        box=self.boxes["godel"]
        box.ask("lila","one","first continuation","first",task="guide")
        one=self.receive("lila")
        box.ask("lila","two","second continuation","second",task="other")
        two=self.receive("lila")
        self.answer(two["message"],"second answer");self.receive("godel")
        self.assertEqual(self.events["godel"][-1][0]["task"],"other")
        self.assertEqual(box.read("first")["state"],"waiting")
        self.answer(one["message"],"first answer");self.receive("godel")
        self.assertEqual(self.events["godel"][-1][0]["task"],"guide")
        self.assertEqual(box.read("first")["body"],"first answer")
        self.assertEqual(box.read("second")["body"],"second answer")

    def test_operator_message_return_address_survives_settlement(self):
        deliver(self.root/"godel","operator-note","operator-note","local","Codex note",
            headers={"from":"codex","return_address":"codex","in_reply_to":None})
        note=self.receive("godel")
        self.assertEqual(note["from"],"codex");self.assertFalse(self.events["godel"][-1][2])
        box=self.boxes["godel"];ticket=box.ticket_for(note["message"])
        box.box.settle(ticket,box._owner(ticket),1)
        with mock.patch.object(agent,"runtime",return_value=box):
            self.assertTrue(agent.reply(note["message"],"Answer").startswith("MAILED codex message="))
        reply=json.loads(next((self.root/"codex").glob("*.json")).read_text())
        self.assertEqual(reply["headers"]["in_reply_to"],"operator-note")
        self.assertEqual(reply["correlation"],"operator-note")

    def test_crash_before_publication_receipt_never_resends_and_remains_runnable(self):
        box=self.boxes["godel"]
        intent=box.wire.E("MailboxAsk",1,"lila","Review","Continue")
        box.wait("interrupted","inbox","interrupted",intent=intent,task="guide",note="Continue")
        config=box.config;box.close()
        restarted=agent.Inbox(config,committed=lambda _:False,offer=lambda *a,**kw:True)
        self.addCleanup(restarted.close)
        with mock.patch.object(agent,"_publish") as publish:
            result=restarted.ask("lila","Review","Continue","interrupted",task="guide")
            self.assertEqual(result["delivery"],"unknown");publish.assert_not_called()
        restarted.codex=mock.Mock()
        restarted.scan(restarted.box)
        self.assertNotEqual(self.store.request_record("guide")["status"],"waiting-mailbox")
        self.assertEqual(restarted.status()["active"][0]["delivery"],"unknown")

    def test_reply_reports_failed_and_unknown_publication(self):
        deliver(self.root/"godel","answerable","answerable","local","Question",
            headers={"from":"codex","return_address":"codex","in_reply_to":None})
        note=self.receive("godel")
        for error,state in ((OSError("disk unavailable"),"FAILED"),
                            (DeliveryUncertain("lost publication receipt"),"UNKNOWN")):
            with mock.patch.object(agent,"runtime",return_value=self.boxes["godel"]), \
                 mock.patch.object(agent,"_publish",side_effect=error):
                self.assertTrue(agent.reply(note["message"],"Answer").startswith("MAILBOX_REPLY_"+state+":"))

    @unittest.skipUnless(os.environ.get("CETTA_BIN"),"set CETTA_BIN")
    def test_real_metta_ask_skill_publishes_and_retains_the_resume_note(self):
        config=self.root/"godel.json";config.write_text(json.dumps(self.boxes["godel"].config))
        program=ROOT/('.mailbox-interface-'+self.root.name+'.metta')
        program.write_text('!(import! &self ./src/helper.py)\n!(import! &self ./src/mailbox_agent.py)\n'
            '!(import! &self ./src/skills.metta)\n'
            '!(println! (mailbox-ask "lila" "Native review" "Continue with the native reply" "native-ask"))\n'
            '!(println! ASK_NATIVE_OK)\n')
        self.addCleanup(program.unlink,missing_ok=True)
        env=dict(os.environ,METTACLAW_MAILBOX_CONFIG=str(config),METTACLAW_INSTANCE="godel",
                 PYTHONPATH=os.pathsep.join([str(ROOT/"src"),str(ROOT/"channels")]))
        run=subprocess.run([os.environ["CETTA_BIN"],"--lang","petta","--import-mode","ancestor-walk",str(program)],
            cwd=ROOT,env=env,capture_output=True,text=True,timeout=30)
        self.assertEqual(run.returncode,0,run.stdout+run.stderr)
        self.assertIn('"delivery": "published"',run.stdout)
        question=self.receive("lila")
        self.assertEqual(question["message"],"native-ask")
        box=self.boxes["godel"]
        intent=box.box._operation(lambda rev:box.box._one(box.box._rows(
            box.wire.E("MailboxIntent",1,"native-ask",box.tag,box.wire.Var(0)),rev)))
        self.assertEqual(intent[4][4],"Continue with the native reply")


if __name__=="__main__":unittest.main()
