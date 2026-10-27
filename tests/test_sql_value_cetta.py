"""Pure MeTTa numeric text conversion against the pinned SQLite rules."""
import math
import os
from pathlib import Path
import random
import sqlite3
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
CETTA_ROOT=Path(os.environ.get('CETTA_CHANNEL_ROOT',ROOT.parent/'CeTTa-ours'))
WORKER=os.environ.get('CETTA_VALUE_WORKER')
if WORKER:
    sys.path.insert(0,str(CETTA_ROOT/'tools'))
    from space_service_client import E,S,BigInt,FloatBits
    from value_worker_client import Worker


@unittest.skipUnless(WORKER,'set CETTA_VALUE_WORKER to a build with exact numerical parts')
class SqlValueTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='sql-value-')
        self.addCleanup(self.temp.cleanup)
        with patch.dict(os.environ,{'HOME':self.temp.name,'PATH':os.environ.get('PATH','/usr/bin:/bin')},clear=True):
            self.worker=Worker(WORKER,ROOT/'src/sql_value_cetta.metta','sql-value:call',language='petta')
        self.addCleanup(self.worker.close)

    def text(self,value):
        bits=struct.unpack('<Q',struct.pack('<d',value))[0]
        return self.worker.call(E('FloatText',FloatBits(bits)))

    def test_fixed_reference_edges_and_value_kinds(self):
        edges=[(0.0,'0.0'),(-0.0,'0.0'),(1.0,'1.0'),(-1.0,'-1.0'),(7.75,'7.75'),
               (1e-7,'1.0e-07'),(1e-5,'1.0e-05'),(1e16,'10000000000000000.0'),
               (1e30,'1.0e+30'),(1.2345678901234567,'1.2345678901234567'),
               (math.nextafter(0.0,1.0),'4.9406564584124654e-324'),
               (float.fromhex('0x1.fffffffffffffp+1023'),'1.7976931348623157e+308'),
               (math.inf,'Inf'),(-math.inf,'-Inf'),(math.nan,S('JsonNull'))]
        for value,wanted in edges:
            with self.subTest(bits=struct.pack('<d',value).hex()):self.assertEqual(self.text(value),wanted)
        self.assertEqual(self.worker.call(E('FloatText',1)),E('Binary64RefusedV1',S('expected-float')))

    def test_all_required_power_values_use_exact_integer_arithmetic(self):
        for power in range(-348,348):
            binary=(power*108853)//32768
            a,b=(10**power,1) if power>=0 else (1,10**-power)
            shift=63-binary
            if shift>=0:a <<= shift
            else:b <<= -shift
            actual=self.worker.call(E('PowerHigh',power))
            self.assertIsInstance(actual,BigInt)
            self.assertEqual(int(actual.decimal),a//b)

    @unittest.skipUnless(sqlite3.sqlite_version_info>=(3,53,0),'SQLite oracle needs the 17-digit default')
    def test_binary64_boundaries_and_generated_values_match_sqlite(self):
        database=sqlite3.connect(':memory:');self.addCleanup(database.close)
        rng=random.Random(2710532)
        values=[]
        for exponent in range(-1074,1024):
            center=math.ldexp(1.0,exponent)
            values.extend((center,-center,math.nextafter(center,0.0),math.nextafter(center,math.inf)))
        for _ in range(4096):values.append(struct.unpack('<d',rng.getrandbits(64).to_bytes(8,'little'))[0])
        for value in values:
            expected=database.execute('SELECT CAST(? AS TEXT)',(value,)).fetchone()[0]
            actual=self.text(value)
            self.assertEqual(actual,S('JsonNull') if expected is None else expected,
                             'binary64 bits '+struct.pack('<d',value).hex())


if __name__=='__main__':unittest.main()
