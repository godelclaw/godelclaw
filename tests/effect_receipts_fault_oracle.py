"""Unchanged oracle under disposable syscall faults; fixture clock only."""
import datetime
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import effect_receipts
import public_worklog

class Clock(datetime.datetime):
    @classmethod
    def now(cls,tz=None):
        return cls.fromtimestamp(1704067200.125,datetime.timezone.utc)

time.tzset()
public_worklog.datetime.datetime=Clock
mode=sys.argv[1]
try:
    if mode=='receipt':
        result=effect_receipts._append({'schema':1,'command':'fixture','result':'r'})
    else:
        result=public_worklog.transact(Path(os.environ['METTACLAW_PUBLIC_WORK_DIR']),mode,'fixture note',7)
    outcome=['value',result]
except Exception as exc:
    outcome=['error',type(exc).__name__,str(exc)]
print(json.dumps(outcome,ensure_ascii=True),flush=True)
