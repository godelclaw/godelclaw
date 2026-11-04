"""A scheduled action survives sleep/restart and is never replayed after a crash."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import helper

class RestTimerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        env=patch.dict(os.environ, METTACLAW_OBSERVED_STRATEGY='0',
                       METTACLAW_WORKING_SET_PATH=str(Path(self.tmp.name)/'working.json'))
        env.start();self.addCleanup(env.stop)

    def test_restart_retains_action_and_original_deadline(self):
        cmd=['shell', 'printf scheduled']
        with patch.object(helper.time,'time',return_value=100):
            helper.rest_schedule(60,cmd)
            helper.working_set_save(0,60,'earlier evidence',90,1,17)
        with patch.object(helper.time,'time',return_value=120):
            helper.working_boot()
            self.assertEqual(helper.boot_int('sleepInterval',1),40)
            self.assertEqual(helper.boot_int('banked_loops',0),17)
            self.assertEqual(helper.rest_pending(), ['continuation-value',cmd])
            self.assertEqual(helper.rest_due(1),0)
        with patch.object(helper.time,'time',return_value=160):
            self.assertEqual(helper.rest_due(0),1)

    def test_interrupted_action_is_reported_and_not_replayed(self):
        helper.rest_schedule(1,['send','hello'])
        helper.rest_claim()
        helper.working_boot()
        self.assertEqual(helper.rest_pending(),'no-continuation')
        self.assertIn('outcome unknown; not replayed',helper.boot_str('lastresults',''))

    def test_heartbeat_does_not_fire_a_later_action_early(self):
        with patch.object(helper.time,'time',return_value=100):
            helper.rest_schedule(5000,['help'])
            self.assertEqual(helper.wait_seconds(5000,90,30),20)
            self.assertEqual(helper.rest_due(1),0)
        helper.rest_cancel()
        self.assertEqual(helper.rest_pending(),'no-continuation')

    def test_timed_rest_outlasts_heartbeat_and_restart(self):
        with patch.object(helper.time,'time',return_value=100):
            helper.rest_cancel()
            helper.rest_hold(36000)
            self.assertEqual(helper.rest_holding(),1)
            # The heartbeat caps the wait; the rest's own end is still later.
            self.assertEqual(helper.wait_seconds(36000,100,1800),1800)
            helper.working_set_save(0,36000,'resting',100,0,43)
        with patch.object(helper.time,'time',return_value=30100):
            helper.working_boot()
            self.assertEqual(helper.boot_int('sleepInterval',1),6000)
            self.assertEqual(helper.rest_holding(),1)
            self.assertEqual(helper.wait_seconds(36000,30000,1800),1700)
            self.assertEqual(helper.wait_seconds(36000,30000,0),6000)
        with patch.object(helper.time,'time',return_value=36100):
            self.assertEqual(helper.rest_holding(),0)
            self.assertEqual(helper.rest_until(),0)

    def test_wake_or_new_rest_releases_the_deadline(self):
        helper.rest_hold(600)
        helper.rest_release()
        self.assertEqual(helper.rest_holding(),0)
        helper.rest_hold(600)
        helper.rest_cancel()
        self.assertEqual(helper.rest_holding(),0)
        helper.rest_release()
        self.assertEqual(helper.rest_holding(),0)

    def test_a_passed_deadline_never_forces_a_zero_wait(self):
        with patch.object(helper.time,'time',return_value=100):
            helper.rest_hold(10)
        with patch.object(helper.time,'time',return_value=500):
            self.assertEqual(helper.wait_seconds(60,500,1800),60)
