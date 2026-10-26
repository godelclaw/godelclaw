"""Our relay and gateway with NuNet's own mail client, nostr-mailbox (over nak).

A friend's agent mailing ours, end to end: alice runs nostr-mailbox against
our relay and writes to godel. The native gateway brings the letter into
godel's claw-mail inbox and publishes a delivered receipt, which alice's
mailbox counts for her sent letter. godel's answer goes out through the
gateway's outbox, alice's mailbox fetches it and answers with its own
receipts, and the gateway reports them to godel. Both gateways, the native
one and the Python one, run the same exchange.

Set NOSTR_MAILBOX to the nostr-mailbox script and NAK to a nak binary (its
directory goes first on PATH for the script), with CETTA_BIN and
CETTA_SPACE_SERVICE as for test_nostr_gateway_cetta. NOSTR_TEST_RELAY=native
puts the native relay under it, otherwise the Python fixture.
"""
import json
import os
import subprocess
import unittest
from pathlib import Path

import test_nostr_gateway_cetta as base

MAILBOX = Path(os.environ.get("NOSTR_MAILBOX", "nostr-mailbox"))
NAK = Path(os.environ.get("NAK", "nak"))
READY = base.HAVE_CRYPTO and base.BINARY.is_file() and base.SERVICE.is_file() and MAILBOX.is_file() and NAK.is_file()


@unittest.skipUnless(READY, "needs NOSTR_MAILBOX, NAK, coincurve, cryptography, websockets, CETTA_BIN and its space service")
class NostrMailboxTests(unittest.TestCase):
    dialect = "petta"

    def setUp(self):
        self.s = base.Setup(self)
        registry = self.s.directory / "mailbox-registry.json"
        registry.write_text(json.dumps({name: {"npub": "npub1" + name, "pubkey_hex": self.s.pub[name]}
                                        for name in ("alice", "godel")}))
        self.registry = registry

    def mailbox(self, *arguments):
        """nostr-mailbox as alice, against the test's relay."""
        environment = {**os.environ, "PATH": "%s:%s" % (NAK.resolve().parent, os.environ.get("PATH", "")),
                       "MAILBOX_AVATAR": "alice", "MAILBOX_ROOT": str(self.s.directory / "alice-mail"),
                       "MAILBOX_REGISTRY": str(self.registry), "NOSTR_MAILBOX_RELAY": self.s.relay.url,
                       "NOSTR_MAILBOX_NSEC": self.s.secret["alice"], "AVATAR_ROOT": str(self.s.directory),
                       "MAILBOX_TEAM": str(self.s.directory / "no-team.json"), "NO_COLOR": "1"}
        result = subprocess.run([str(MAILBOX), *arguments], capture_output=True, text=True, timeout=120,
                                env=environment, stdin=subprocess.DEVNULL)
        self.assertEqual(result.returncode, 0, result.stdout[-1500:] + result.stderr[-1500:])
        return result.stdout + result.stderr

    def test_a_nostr_mailbox_letter_reaches_godel_and_the_receipts_go_both_ways(self):
        self.exchange(self.s.native)

    def test_the_same_with_the_python_gateway(self):
        self.exchange(self.s.python)

    def exchange(self, gateway):
        s = self.s
        sent = self.mailbox("send", "godel", "--subject", "From NuNet's mailbox", "Hello godel.\nA second line.")
        [delivery] = s.eventually(lambda: (gateway(), s.bus("godel"))[1])
        self.assertEqual(delivery["from"], base.LOGIN + "/nostr.alice")
        self.assertIn("Subject: From NuNet's mailbox", delivery["text"])
        self.assertIn("Hello godel.\nA second line.", delivery["text"])
        self.assertIn(delivery["id"][:8], sent)
        # The gateway's delivered receipt counts in alice's mailbox.
        self.mailbox("fetch")
        self.assertRegex(self.mailbox("list", "--all"), r"delivered\s+%s\s+to godel" % delivery["id"][:8])
        # godel answers through the gateway; alice's mailbox opens it and acknowledges it.
        s.outbox("godel", "To: alice\nSubject: Re: From NuNet's mailbox\nIn-Reply-To: %s\n\nHello alice." % delivery["id"])
        gateway()
        fetched = self.mailbox("fetch")
        self.assertIn("claimed 1 new", fetched)
        listed = self.mailbox("list")
        self.assertRegex(listed, r"unread\s+[0-9a-f]{8}\s+godel\s+Re: From NuNet's mailbox")
        reply_id = listed.split("unread")[1].split()[0]
        shown = self.mailbox("read", reply_id)
        self.assertIn("Hello alice.", shown)
        # Its delivered and read receipts come back to godel.
        gateway()
        told = [d["text"] for d in s.bus("godel") if d["text"].startswith(("Delivered by", "Read by"))]
        self.assertTrue(any(t.startswith("Delivered by alice: Re: From NuNet's mailbox") for t in told), told)
        self.assertTrue(any(t.startswith("Read by alice: Re: From NuNet's mailbox") for t in told), told)


if __name__ == "__main__":
    unittest.main()
