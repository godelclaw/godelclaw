"""Native receives through the real agent attention and checkpoint boundaries."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import subprocess
import select
import tempfile
import time
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
CETTA = Path(os.environ.get("CETTA_MAILBOX_ROOT", ROOT.parent / "CeTTa-ours"))
sys.path[:0] = [str(ROOT/"src"),str(ROOT/"channels"),str(CETTA/"tools"),str(CETTA/"tests")]
from test_native_mailbox import Fixture, SERVER, WORKER
from native_mailbox import Mailbox
from space_service_client import Client, E, S, ServiceError
from observed_strategy import Ledger
import mailbox_agent
import telegram
import input_ack
import helper


@unittest.skipUnless(SERVER and WORKER,"set CETTA_SPACE_SERVICE and CETTA_VALUE_WORKER")
class MailboxAgentTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.addCleanup(self.fixture.close)
        self.root = self.fixture.path
        self.config = {"version":1,"instance":"test","socket":self.fixture.socket,
                       "space":"test-inbox","tools":str(CETTA/"tools"),"worker":str(Path(WORKER).resolve()),
                       "channels":{"review":"codex","inbox":"local"},"preview_chars":32,"watch_ms":1000}
        env = mock.patch.dict(os.environ,METTACLAW_INSTANCE="test",METTACLAW_OBSERVED_STRATEGY="1",
                              METTACLAW_STRATEGY_PATH=str(self.root/"checkpoint.sqlite3"),
                              METTACLAW_TELEGRAM_CLIENT="metta",METTACLAW_MAILBOX_CONFIG="",
                              METTACLAW_WORKING_SET_PATH=str(self.root/"working.json"))
        env.start(); self.addCleanup(env.stop)
        self.ledger = Ledger(self.root/"checkpoint.sqlite3")
        self.events = []
        self.boxes = []
        self.addCleanup(self.close_boxes)
        helper.turn_commit_reset(); self.addCleanup(helper.turn_commit_reset)

    def close_boxes(self):
        for box in self.boxes:
            if not box.stopped.is_set(): box.close()

    def inbox(self, **changes):
        config = dict(self.config,**changes)
        box = mailbox_agent.Inbox(config,offer=lambda text,identity:self.events.append((text,identity)) or True)
        self.boxes.append(box)
        return box

    def wait(self, inbox, ticket="ticket", correlation="reply"):
        with mock.patch.object(inbox,"start",return_value=1):
            return inbox.wait(ticket,"review",correlation)

    def send(self, identity="message", correlation="reply", body="a reply", channel="review", sender="codex"):
        box = Mailbox(self.fixture.socket,self.config["space"],WORKER)
        try: return box.send(identity,channel,correlation,sender,body)
        finally: box.close()

    def ready(self, inbox):
        self.wait(inbox); self.send(); inbox.scan(inbox.box)

    def test_failed_paired_send_leaves_the_waiting_task_runnable(self):
        import task_context
        from observed_strategy import encoded
        self.ledger.accept("guide","request a review")
        commands=[["mailbox-wait","review-note","review","review-1"],["mailbox-send","sibling","review please","review-1"]]
        router=dict(advertised=[c[0] for c in commands],executable=[c[0] for c in commands],permitted=[c[0] for c in commands])
        snap=self.ledger.capture("guide","context",router,0)
        task_context.proposal(self.ledger,snap["serial"],"guide",commands,"not run")
        action=self.ledger.decide(snap["serial"],dict(kind="command",request="guide",owner=snap["owner"],tool="mailbox-wait",command=dict(commands=commands,limit=2)),0)
        inbox=self.inbox()
        with mock.patch.object(inbox,"start",return_value=1):inbox.wait("review-note","review","review-1",task="guide")
        self.assertEqual(self.ledger.request_record("guide")["status"],"active")
        with self.ledger.transaction() as db:
            db.execute("CREATE TABLE command_attempts(ticket TEXT,position INTEGER,command TEXT,result TEXT,PRIMARY KEY(ticket,position))")
            db.execute("INSERT INTO command_attempts VALUES (?,?,?,?)",(action,1,encoded(commands[1]),encoded(dict(disposition="returned",result="MAILBOX_SEND_FAILED: unavailable"))))
        mailbox_agent.finish_task_dispatch(self.ledger,"guide")
        inbox.scan(inbox.box)
        self.assertEqual(self.ledger.request_record("guide")["status"],"active")
        self.assertEqual(inbox.read("review-note")["state"],"waiting")
        # A real successful receipt now permits parking. Reopening the host
        # reconstructs that decision rather than dispatching the send again.
        with self.ledger.transaction() as db:
            db.execute("UPDATE command_attempts SET result=?",(encoded(dict(disposition="returned",result="MAILED sibling id=one")),))
        mailbox_agent.finish_task_dispatch(self.ledger,"guide")
        self.assertEqual(self.ledger.request_record("guide")["status"],"waiting-mailbox")
        inbox.close()
        restarted=self.inbox()
        restarted.scan(restarted.box)
        self.assertEqual(self.ledger.request_record("guide")["status"],"waiting-mailbox")

    def test_reply_recovers_sender_and_correlation_after_restart_and_settlement(self):
        first=self.inbox(siblings={"peer":"unused"})
        self.wait(first)
        self.send(body="From: peer\n\nreview this")
        first.scan(first.box)
        first.box.settle("ticket",first._owner("ticket"),1)
        first.close()
        restarted=self.inbox(siblings={"peer":"unused"})
        with mock.patch.object(mailbox_agent,"runtime",return_value=restarted), \
             mock.patch.object(mailbox_agent,"send",return_value="MAILED peer id=reply") as send:
            self.assertEqual(mailbox_agent.reply("ticket","review result"),"MAILED peer id=reply")
            send.assert_called_once_with("peer","review result","reply")
            self.assertTrue(mailbox_agent.reply("missing","reply").startswith("MAILBOX_REPLY_REFUSED"))
            self.assertEqual(send.call_count,1)

    def test_task_wait_recovers_between_native_commit_and_local_park(self):
        import task_context
        self.ledger.accept("guide","Write the guide, wait for review, revise it")
        task_context.begin(self.ledger,"guide","operator-event")
        inbox=self.inbox(auto_receive=["inbox"])
        # Simulate a crash after the native commit, before local scheduling.
        inbox.box.register("review-note","inbox","review-1","local",inbox._owner("review-note"),1,
            inbox.wire.E("MailboxResume",1,inbox.tag,"review-note"),context=("guide","Compare the review with the source"))
        inbox.close()
        restarted=self.inbox(auto_receive=["inbox"])
        restarted.scan(restarted.box)
        self.assertEqual(self.ledger.request_record("guide")["status"],"waiting-mailbox")
        self.assertEqual(task_context.projection(self.ledger,"guide")["waits"][0]["note"],"Compare the review with the source")
        self.send(correlation="review-1",channel="inbox",sender="local",body="Review: inspect the premise")
        restarted.scan(restarted.box)
        self.assertEqual(self.ledger.request_record("guide")["status"],"active")
        data=json.loads(self.events[-1][0])
        self.assertEqual((data["ticket"],data["task"]),("review-note","guide"))
        self.assertEqual(len(self.events),1)  # generic inbox did not steal it
        self.ledger.checkpoint({"saved_at":time.time()},[self.events[-1][1]])
        restarted.acknowledge([self.events[-1][1]])
        restarted.scan(restarted.box)
        self.assertFalse(restarted.task_arrivals)

    def test_cancelled_task_wait_does_not_park_again_on_duplicate(self):
        self.ledger.accept("guide","a task")
        inbox=self.inbox()
        with mock.patch.object(inbox,"start",return_value=1):
            self.assertEqual(inbox.wait("review-note","review","review-1",task="guide")["state"],"waiting")
            inbox.cancel("review-note")
            self.assertEqual(self.ledger.request_record("guide")["status"],"active")
            self.assertEqual(inbox.wait("review-note","review","review-1",task="guide")["state"],"cancelled")
            self.assertEqual(self.ledger.request_record("guide")["status"],"active")

    def test_arrival_reaches_attention_once_and_requires_a_checkpoint_to_settle(self):
        inbox=self.inbox(); self.ready(inbox)
        self.assertEqual(len(self.events),1)
        self.assertEqual(self.events[0][1],"mailbox:test:ticket")
        inbox.scan(inbox.box)
        self.assertEqual(len(self.events),1)
        self.assertEqual(inbox.acknowledge(["mailbox:test:ticket"]),0)
        self.assertEqual(inbox.read("ticket")["state"],"ready")
        self.ledger.checkpoint({"saved_at":time.time(),"loops":3},["mailbox:test:ticket"])
        self.assertEqual(inbox.acknowledge(["mailbox:test:ticket"]),1)
        self.assertEqual(inbox.read("ticket")["state"],"settled")
        self.assertEqual(inbox.acknowledge(["mailbox:test:ticket"]),1)

    def test_restart_before_checkpoint_reoffers_but_after_checkpoint_only_settles(self):
        first=self.inbox(); self.ready(first); first.close()
        second=self.inbox(); second.scan(second.box)
        self.assertEqual([row[1] for row in self.events],["mailbox:test:ticket"]*2)
        second.close()
        self.ledger.checkpoint({"saved_at":time.time(),"loops":4},["mailbox:test:ticket"])
        third=self.inbox(); third.scan(third.box)
        self.assertEqual(len(self.events),2)
        self.assertEqual(third.read("ticket")["state"],"settled")

    def test_checkpoint_failure_preserves_the_ready_reply(self):
        inbox=self.inbox(); self.ready(inbox)
        with mock.patch.object(self.ledger,"event",side_effect=RuntimeError("fault")):
            with self.assertRaises(RuntimeError):
                self.ledger.checkpoint({"saved_at":time.time()},["mailbox:test:ticket"])
        self.assertFalse(input_ack.already_committed("mailbox:test:ticket"))
        self.assertEqual(inbox.acknowledge(["mailbox:test:ticket"]),0)
        self.assertEqual(inbox.read("ticket")["state"],"ready")

    def test_source_ack_failure_is_deferred_and_watcher_can_settle_from_checkpoint(self):
        inbox=self.inbox(); self.ready(inbox)
        identity="mailbox:test:ticket"
        self.ledger.checkpoint({"saved_at":time.time()},[identity])
        with mock.patch.object(mailbox_agent,"runtime",return_value=inbox), \
             mock.patch.object(inbox,"acknowledge",side_effect=OSError("transport unavailable")):
            self.assertEqual(telegram.activity_source_ack(identity),0)
        self.assertEqual(inbox.read("ticket")["state"],"ready")
        inbox.scan(inbox.box)
        self.assertEqual(inbox.read("ticket")["state"],"settled")
        self.assertEqual(len(self.events),1)

    def test_service_absent_at_boot_starts_watching_after_recovery_without_a_tool_call(self):
        mailbox_agent.close()
        first=self.inbox(auto_receive=["inbox"])
        config=dict(self.config,auto_receive=["inbox"],storage_identity=first.identity,
                    health_path=str(self.root/"mailbox-health.json"))
        first.close(); self.fixture.stop(crash=True)
        path=self.root/"mailbox.json"; path.write_text(json.dumps(config))
        with mock.patch.dict(os.environ,METTACLAW_MAILBOX_CONFIG=str(path)), \
             mock.patch.object(telegram,"enqueue_mailbox_event",side_effect=lambda text,identity:self.events.append((text,identity)) or True):
            try:
                started=time.monotonic()
                self.assertEqual(json.loads(mailbox_agent.start())["state"],"starting")
                self.assertLess(time.monotonic()-started,.5)
                deadline=time.monotonic()+3
                while not (self.root/"mailbox-health.json").exists() and time.monotonic()<deadline:
                    time.sleep(.01)
                self.assertFalse(json.loads((self.root/"mailbox-health.json").read_text())["watching"])
                self.fixture.start()
                self.send("boot-message","conversation","recovered",channel="inbox",sender="local")
                deadline=time.monotonic()+5
                while not self.events and time.monotonic()<deadline: time.sleep(.01)
                self.assertEqual(len(self.events),1)
                self.assertIn("recovered",self.events[0][0])
                self.assertTrue(mailbox_agent._runtime.thread.is_alive())
            finally:
                mailbox_agent.close()

    def test_killed_agent_evaluator_recovers_before_another_wait_is_registered(self):
        inbox=self.inbox()
        old=inbox.box.worker.process
        old.kill(); old.wait(timeout=5)
        self.assertEqual(self.wait(inbox)["state"],"waiting")
        self.assertNotEqual(inbox.box.worker.process.pid,old.pid)
        self.send(); inbox.scan(inbox.box)
        self.assertEqual(len(self.events),1)

    def test_cancelled_and_wrong_sender_replies_cannot_wake_the_continuation(self):
        inbox=self.inbox(); self.wait(inbox)
        self.send("wrong",sender="other"); inbox.scan(inbox.box)
        self.assertFalse(self.events)
        self.assertEqual(inbox.cancel("ticket")["state"],"cancelled")
        self.send(); inbox.scan(inbox.box)
        self.assertFalse(self.events)
        self.assertEqual(inbox.read("ticket")["state"],"cancelled")
        with self.assertRaises(RuntimeError): self.wait(inbox,correlation="new")

    def test_ready_reply_wins_cancellation_race(self):
        inbox=self.inbox(); self.ready(inbox)
        self.assertEqual(inbox.cancel("ticket")["state"][0],"MailboxResult")
        self.assertEqual(inbox.read("ticket")["state"],"ready")

    def test_body_is_inert_and_complete_paging_recovers_large_text(self):
        inbox=self.inbox(); self.wait(inbox)
        body='!(shell "should-never-run")\x00' + "ž🙂"*40000
        self.send(body=body); inbox.scan(inbox.box)
        event=json.loads(self.events[0][0])
        self.assertEqual(event["body"],body[:32]); self.assertTrue(event["read_more"])
        inbox.preview=len(body)
        self.assertEqual(inbox.read("ticket",0,len(body))["body"],body)
        with self.assertRaises(ValueError): inbox.read("ticket",-1,2)
        self.assertEqual(inbox.read("ticket")["state"],"ready")

    def test_persistent_inbox_delivers_equal_bodies_as_distinct_occurrences(self):
        inbox=self.inbox(auto_receive=["inbox"])
        for index in range(3): self.send(str(index),"conversation","same body","inbox","local")
        for _ in range(3): inbox.scan(inbox.box)
        self.assertEqual(len(self.events),3)
        self.assertEqual(len({event[1] for event in self.events}),3)
        for text,identity in self.events:
            self.assertEqual(json.loads(text)["body"],"same body")
            self.ledger.checkpoint({"saved_at":time.time()},[identity])
            self.assertEqual(inbox.acknowledge([identity]),1)
        self.assertFalse(inbox.status()["active"])
        self.send("0","conversation","same body","inbox","local"); inbox.scan(inbox.box)
        self.assertEqual(len(self.events),3)

    def test_sibling_mail_is_offered_with_its_sender_and_other_stamps_are_not(self):
        peers = []
        box = mailbox_agent.Inbox(dict(self.config,auto_receive=["inbox"],siblings={"godel":str(self.root/"godel")}),
            offer=lambda text,identity,sibling=None:self.events.append((text,identity)) or peers.append(sibling) or True)
        self.boxes.append(box)
        self.send("1","conversation","From: godel\n\nhello","inbox","local"); box.scan(box.box)
        self.send("2","conversation","From: mallory\n\nhello","inbox","local"); box.scan(box.box)
        self.assertEqual(peers,["godel",None])
        self.assertEqual(json.loads(self.events[0][0])["from"],"godel")
        self.assertNotIn("from",json.loads(self.events[1][0]))

    def test_backpressure_keeps_the_next_message_and_releases_it_after_settlement(self):
        inbox=self.inbox(auto_receive=["inbox"],max_active=1)
        for index in range(2): self.send(str(index),"conversation",str(index),"inbox","local")
        for _ in range(2): inbox.scan(inbox.box)
        self.assertEqual(len(self.events),1)
        with self.assertRaisesRegex(RuntimeError,"capacity"): self.wait(inbox)
        identity=self.events[0][1]
        self.ledger.checkpoint({"saved_at":time.time()},[identity])
        inbox.acknowledge([identity]); inbox.scan(inbox.box)
        self.assertEqual(len(self.events),2)

    def test_complete_active_view_does_not_hide_an_interactive_wait_behind_128_others(self):
        inbox=self.inbox()
        for index in range(130): self.wait(inbox,str(index),str(index))
        self.send(correlation="129"); inbox.scan(inbox.box)
        self.assertEqual(json.loads(self.events[0][0])["ticket"],"129")
        self.assertEqual(len(inbox.status()["active"]),130)

    def test_semantics_or_authority_change_requires_migration(self):
        inbox=self.inbox(); self.wait(inbox); inbox.close()
        with mock.patch.object(mailbox_agent,"ABI","agent-input-v2"):
            with self.assertRaisesRegex(RuntimeError,"migration"): self.inbox()
        with self.assertRaisesRegex(RuntimeError,"migration"):
            self.inbox(channels={"review":"other","inbox":"local"})
        valid=self.inbox(); self.assertEqual(valid.read("ticket")["state"],"waiting")

    def test_pinned_storage_cannot_be_recreated_after_deletion_or_process_restart(self):
        inbox=self.inbox(); identity=inbox.identity; inbox.close()
        with Client(self.fixture.socket) as client:
            space=client.open(self.config["space"])
            client.call("drop",space.uuid,space.id,space.revision,client.hello()[4])
            before=client.list_spaces()
            with self.assertRaises(mailbox_agent._api(self.config)[1].ServiceError): self.inbox(storage_identity=identity)
            self.assertEqual(client.list_spaces(),before)

    def test_wait_returns_and_native_owner_restart_does_not_interrupt_other_work(self):
        health=self.root/"watcher-health.json"
        inbox=self.inbox(health_path=str(health))
        started=time.monotonic(); inbox.wait("ticket","review","reply")
        registered=time.monotonic()-started
        deadline=time.monotonic()+2
        while inbox.monitor is None and time.monotonic()<deadline: time.sleep(.01)
        self.fixture.restart()
        ticks=0
        for _ in range(10): time.sleep(.025); ticks+=1
        self.send()
        deadline=time.monotonic()+5
        while not self.events and time.monotonic()<deadline: time.sleep(.01)
        self.assertEqual(len(self.events),1,inbox.error)
        self.assertEqual(ticks,10)
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            state=json.loads(health.read_text())
            if state["watching"] and not state["error"]: break
            time.sleep(.01)
        self.assertTrue(state["watching"])
        self.assertEqual(state["error"],"")
        print("AGENT_MAILBOX "+json.dumps({"register_ms":round(registered*1000,3),"periodic_steps":ticks,"model_polls":0}))

    def test_existing_recall_client_cannot_replace_the_mailbox_protocol(self):
        legacy=types.ModuleType("space_service_client")
        legacy.Space=type("LegacySpace",(),{})  # an older client has no query_first
        with mock.patch.dict(sys.modules,{"space_service_client":legacy}):
            inbox=self.inbox(); self.ready(inbox)
            self.assertEqual(len(self.events),1)
            self.assertEqual(inbox.read("ticket")["body"],"a reply")
            self.assertIs(sys.modules["space_service_client"],legacy)
            self.assertIsNot(inbox.wire,legacy)

    def test_real_telegram_queue_and_checkpoint_source_ack_preserve_the_reply_target(self):
        inbox=self.inbox(); inbox.offer=mailbox_agent._offer
        with mock.patch.object(telegram,"_metta_events",[]), mock.patch.object(telegram,"_reply_chat_id","42"), \
             mock.patch.object(mailbox_agent,"_runtime",inbox), mock.patch.object(mailbox_agent,"runtime",return_value=inbox):
            self.ready(inbox)
            events=json.loads(telegram.metta_take_runtime_events())
            messages=[event for event in events if "update_id" in event]
            self.assertEqual(messages[0]["update_id"],"mailbox:test:ticket")
            self.assertEqual(telegram._reply_chat_id,"42")
            self.assertEqual(telegram.activity_source_ack("mailbox:test:ticket"),0)
            self.ledger.checkpoint({"saved_at":time.time()},["mailbox:test:ticket"])
            self.assertEqual(telegram.activity_source_ack("mailbox:test:ticket"),1)
            self.assertEqual(inbox.read("ticket")["state"],"settled")

    def test_malformed_or_foreign_source_ids_cannot_acknowledge_any_part_of_a_batch(self):
        for values in ("1,mailbox:other:ticket","1,mailbox:test:bad,ticket",["mailbox:test:x",False],
                       "1,mailbox:test:","mailbox:test:x:y","01,mailbox:test:x"):
            with mock.patch.object(mailbox_agent,"acknowledge") as ack, \
                 mock.patch.object(telegram,"handover_ack") as handover:
                with self.assertRaises(ValueError): telegram.activity_source_ack(values)
                ack.assert_not_called(); handover.assert_not_called()

    def test_manual_wait_cannot_occupy_the_persistent_arrival_ticket_namespace(self):
        inbox=self.inbox()
        with self.assertRaises(ValueError): self.wait(inbox,"inbox-reserved")
        self.assertEqual(inbox.status()["active"],[])

    @unittest.skipUnless(os.environ.get("MODE_TEST_CETTA_BIN"),"set MODE_TEST_CETTA_BIN")
    def test_metta_turn_receives_and_settles_only_after_its_successor_checkpoint(self):
        self.metta_ack()

    @unittest.skipUnless(os.environ.get("MODE_TEST_CETTA_BIN"),"set MODE_TEST_CETTA_BIN")
    def test_metta_ack_after_a_real_31_second_idle_preserves_the_engine(self):
        self.metta_ack(idle=31)

    @unittest.skipUnless(os.environ.get("MODE_TEST_CETTA_BIN"),"set MODE_TEST_CETTA_BIN")
    def test_metta_source_ack_exception_preserves_pending_input_and_loop(self):
        self.metta_ack(fault=True)

    def metta_ack(self,idle=0,fault=False):
        config_path=self.root/"mailbox.json"
        config_path.write_text(json.dumps(self.config))
        injected=""
        if fault:
            with tempfile.NamedTemporaryFile("w",suffix=".py",dir=ROOT,delete=False) as file:
                file.write('def arm():\n'
                           ' import telegram\n'
                           ' def fail(ids): raise RuntimeError("fixture source unavailable")\n'
                           ' telegram.activity_source_ack=fail\n'
                           ' return 1\n'
                           'def python_ack():\n'
                           ' import os,telegram\n'
                           ' os.environ["METTACLAW_TELEGRAM_CLIENT"]="python"\n'
                           ' def fail(): raise RuntimeError("fixture batch unavailable")\n'
                           ' telegram.ackActivityBatch=fail\n'
                           ' return 1\n')
                module=Path(file.name)
            self.addCleanup(module.unlink)
            injected=f'!(import! &self ./{module.name})\n!(py-call ({module.stem}.arm))\n'
        source=('!(import! &self ./src/helper.py)\n'
                '!(import! &self ./channels/telegram.py)\n'
                '!(import! &self ./src/skills.metta)\n'
                '!(import! &self ./channels/telegram.metta)\n'
                '!(mailbox-wait "ticket" "review" "reply")\n'
                '!(tg:sleep-until-message 5)\n'
                '!(tg:activity-batch)\n'
                '!(assert (== (tg:pending-count) 1))\n'
                '!(assert (== (tg:ack-activity-batch) 0))\n'
                '!(assert (== (py-call (input_ack.already_committed "mailbox:test:ticket")) 0))\n'
                '!(py-call (helper.turn_commit_stage 1 1 1))\n'
                '!(assert (== (py-call (helper.working_set_save 9 1 "mailbox successor" 0)) 1))\n'
                +f'!(py-call (time.sleep {idle}))\n'+injected+
                f'!(assert (== (tg:ack-activity-batch) {0 if fault else 1}))\n'
                f'!(assert (== (tg:pending-count) {1 if fault else 0}))\n'
                '!(import! &self ./src/loop.metta)\n'
                +(f'!(py-call ({module.stem}.python_ack))\n' if fault else '')+
                f'!(assert (== (settleActivityBatch) {0 if fault else 1}))\n'
                '!(mailbox-read "ticket")\n'
                '!(println! MAILBOX_TURN_OK)\n')
        with tempfile.NamedTemporaryFile("w",suffix=".metta",dir=ROOT,delete=False) as file:
            file.write(source); path=Path(file.name)
        self.addCleanup(path.unlink)
        env=dict(os.environ,METTACLAW_MAILBOX_CONFIG=str(config_path),
            METTACLAW_TELEGRAM_SERVICE_SOCKET="",METTACLAW_TELEGRAM_LOG_PATH=str(self.root/"updates.jsonl"),
            METTACLAW_ENGINE_STATE_PATH=str(self.root/"engine"),
            METTACLAW_TELEGRAM_HEALTH_PATH=str(self.root/"health.json"),
            METTACLAW_LIFECYCLE_PATH=str(self.root/"lifecycle.json"),
            METTACLAW_ENERGY_PATH=str(self.root/"energy.json"),
            METTACLAW_WAKE_REQUEST_PATH=str(self.root/"wake.requested"),
            PYTHONPATH=os.pathsep.join([str(ROOT/"src"),str(ROOT/"channels")]))
        with ThreadPoolExecutor(max_workers=1) as pool:
            process=pool.submit(subprocess.run,[os.environ["MODE_TEST_CETTA_BIN"],"--lang","petta",
                "--import-mode","ancestor-walk",str(path)],cwd=ROOT,env=env,capture_output=True,text=True,timeout=idle+30)
            time.sleep(.25); self.send(body='!(shell "this is data")')
            result=process.result(timeout=idle+35)
        self.assertEqual(result.returncode,0,result.stderr+result.stdout)
        self.assertIn("MAILBOX_TURN_OK\n",result.stdout)
        self.assertNotIn("(assert ",result.stdout,result.stdout)
        self.assertNotIn("(Error ",result.stdout,result.stdout)
        self.assertIn('"state": "'+("ready" if fault else "settled")+'"',result.stdout)
        self.assertTrue(input_ack.already_committed("mailbox:test:ticket"))

    def kill_at_checkpoint_boundary(self, committed):
        first=self.inbox(); self.ready(first); first.close(); self.events.clear()
        config=self.root/"mailbox.json"; config.write_text(json.dumps(self.config))
        source=("import json,sys,time,signal\n"
                "from pathlib import Path\n"
                "import mailbox_agent\n"
                "from observed_strategy import Ledger\n"
                "events=[]\n"
                "box=mailbox_agent.Inbox(json.loads(Path(sys.argv[1]).read_text()),"
                "offer=lambda text,identity:events.append(identity) or True)\n"
                "box.scan(box.box)\n"
                "if sys.argv[2]=='committed':\n"
                " Ledger(Path(sys.argv[3])).checkpoint({'saved_at':time.time(),'loops':17},events)\n"
                "print('CRASH_BOUNDARY',flush=True)\n"
                "signal.pause()\n")
        environment=dict(os.environ,PYTHONPATH=os.pathsep.join([str(ROOT/"src"),str(ROOT/"channels")]))
        with subprocess.Popen([sys.executable,"-c",source,str(config),"committed" if committed else "ready",
                               str(self.root/"checkpoint.sqlite3")],env=environment,
                              stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) as process:
            try:
                self.assertTrue(select.select([process.stdout],[],[],10)[0],"probe did not reach crash point")
                self.assertEqual(process.stdout.readline().strip(),"CRASH_BOUNDARY")
            finally:
                process.kill(); process.communicate(timeout=5)
        recovered=self.inbox(); recovered.scan(recovered.box)
        self.assertEqual(len(self.events),0 if committed else 1)
        self.assertEqual(recovered.read("ticket")["state"],"settled" if committed else "ready")

    def test_sigkill_before_checkpoint_recovers_the_unprocessed_arrival(self):
        self.kill_at_checkpoint_boundary(False)

    def test_sigkill_after_checkpoint_settles_without_a_second_presentation(self):
        self.kill_at_checkpoint_boundary(True)

    @unittest.skipUnless(os.environ.get("CETTA_CODEX_TRANSPORT"),"set installed Codex transport for wire fixture")
    def test_codex_subscription_survives_restart_and_never_polls_or_starts_a_model(self):
        from support.codex_app_server_fixture import WireAppServer
        wire=WireAppServer(self.root/"codex.sock"); self.addCleanup(wire.close)
        config={"channel":"review","app_server_socket":str(wire.path),
                "transport_module":os.environ["CETTA_CODEX_TRANSPORT"]}
        first=self.inbox(codex=config)
        first.watch_codex("review-1","thread","turn")
        deadline=time.monotonic()+5
        while not any(method=="thread/read" for method,_ in wire.calls) and time.monotonic()<deadline:
            time.sleep(.01)
        self.assertTrue(any(method=="thread/read" for method,_ in wire.calls),first.status())
        before=list(wire.calls)
        for index in range(4):
            with first.lock: first.box.space.add(first.wire.E("PeriodicDuty",index))
            time.sleep(.025)
        self.assertEqual(wire.calls,before)
        first.close()
        second=self.inbox(codex=config); second.start()
        deadline=time.monotonic()+5
        while sum(method=="thread/read" for method,_ in wire.calls)<2 and time.monotonic()<deadline:
            time.sleep(.01)
        turn={"id":"turn","status":"completed","items":[
            {"id":"progress","type":"agentMessage","phase":"commentary","text":"still working"},
            {"id":"answer","type":"agentMessage","phase":"final_answer","text":"done"}]}
        started=time.monotonic(); wire.complete(turn)
        deadline=time.monotonic()+5
        while not self.events and time.monotonic()<deadline: time.sleep(.01)
        self.assertEqual(len(self.events),1,second.status())
        second.preview=65536
        data=json.loads(second.read("review-1")["body"])
        self.assertEqual(data["messages"][0]["text"],"done")
        self.assertEqual(len(data["messages"]),1)
        self.assertEqual(second.box.space.match_count(second.wire.E("PeriodicDuty",second.wire.Var(0))),4)
        self.assertTrue(all(method in ("initialize","initialized","thread/resume","thread/read") for method,_ in wire.calls))
        self.ledger.checkpoint({"saved_at":time.time()},["mailbox:test:review-1"])
        second.acknowledge(["mailbox:test:review-1"])
        print("CODEX_AGENT_MAILBOX "+json.dumps({"arrival_to_attention_ms":round((time.monotonic()-started)*1000,3),
                                                "periodic_steps":4,"model_calls":0,"idle_transcript_polls":0}))

    @unittest.skipUnless(os.environ.get("CETTA_CODEX_TRANSPORT"),"set installed Codex transport for wire fixture")
    def test_known_codex_completion_survives_reconnect_until_transcript_backfill(self):
        from support.codex_app_server_fixture import WireAppServer
        wire=WireAppServer(self.root/"codex.sock"); self.addCleanup(wire.close)
        wire.turns=[{"id":"turn","status":"inProgress"}]
        config={"channel":"review","app_server_socket":str(wire.path),
                "transport_module":os.environ["CETTA_CODEX_TRANSPORT"]}
        inbox=self.inbox(codex=config); inbox.watch_codex("review-1","thread","turn")
        def reads(): return sum(method=="thread/read" for method,_ in wire.calls)
        deadline=time.monotonic()+5
        while reads()<1 and time.monotonic()<deadline: time.sleep(.01)
        wire.send({"method":"turn/completed","params":{"threadId":"thread",
                  "turn":{"id":"turn","status":"completed","itemsView":"summary"}}})
        deadline=time.monotonic()+5
        while reads()<4 and time.monotonic()<deadline: time.sleep(.01)
        self.assertGreaterEqual(reads(),4,inbox.status())
        self.assertEqual(self.events,[])
        # The full transcript appears later without a second completion event.
        wire.turns=[{"id":"turn","status":"completed","items":[
            {"id":"answer","type":"agentMessage","phase":"final_answer","text":"backfilled"}]}]
        deadline=time.monotonic()+5
        while not self.events and time.monotonic()<deadline: time.sleep(.01)
        self.assertEqual(len(self.events),1,inbox.status())
        inbox.preview=65536
        self.assertEqual(json.loads(inbox.read("review-1")["body"])["messages"][0]["text"],"backfilled")

    @unittest.skipUnless(os.environ.get("CETTA_CODEX_TRANSPORT"),"set installed Codex transport for wire fixture")
    def test_adding_and_cancelling_another_codex_watch_interrupts_the_idle_event_wait(self):
        from support.codex_app_server_fixture import WireAppServer
        wire=WireAppServer(self.root/"codex.sock"); self.addCleanup(wire.close)
        config={"channel":"review","app_server_socket":str(wire.path),
                "transport_module":os.environ["CETTA_CODEX_TRANSPORT"]}
        inbox=self.inbox(codex=config); inbox.watch_codex("one","thread","turn")
        def reads(): return sum(method=="thread/read" for method,_ in wire.calls)
        deadline=time.monotonic()+5
        while reads()<1 and time.monotonic()<deadline: time.sleep(.01)
        inbox.watch_codex("two","thread","turn2")
        deadline=time.monotonic()+5
        while reads()<2 and time.monotonic()<deadline: time.sleep(.01)
        self.assertGreaterEqual(reads(),2,inbox.status())
        inbox.cancel("one")
        deadline=time.monotonic()+5
        while reads()<3 and time.monotonic()<deadline: time.sleep(.01)
        self.assertGreaterEqual(reads(),3,inbox.status())
        wire.complete({"id":"turn2","status":"failed","error":{"message":"fixture failure"},"items":[]})
        deadline=time.monotonic()+5
        while not self.events and time.monotonic()<deadline: time.sleep(.01)
        self.assertEqual(len(self.events),1,inbox.status())
        self.assertEqual(json.loads(self.events[0][0])["ticket"],"two")
        inbox.preview=65536
        self.assertEqual(json.loads(inbox.read("two")["body"])["status"],"failed")


if __name__=="__main__": unittest.main()
