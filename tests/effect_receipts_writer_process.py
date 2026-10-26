"""Unchanged Python shared-file writer for isolated mixed-process tests."""
import fcntl
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import effect_receipts
import public_worklog


def main():
    mode,count,trace=sys.argv[1],int(sys.argv[2]),Path(sys.argv[3])
    root=Path(os.environ['METTACLAW_PUBLIC_WORK_DIR'])
    time.tzset()
    original=fcntl.flock
    current=[0]
    def flock(fd,operation):
        result=original(fd,operation)
        if operation==fcntl.LOCK_EX:
            with trace.open('ab',buffering=0) as output:
                output.write(('python:%d\n'%current[0]).encode())
        return result
    fcntl.flock=flock
    print('READY',flush=True)
    for i in range(count):
        current[0]=i
        if mode=='receipts':
            effect_receipts.time.time=lambda i=i:1704067200.0+i
            result=effect_receipts.record_atlas_result('fixture-server','atlas_query',
                {'receipt':{'receipt_id':'python:%d'%i},'view_id':'view:%d'%i,
                 'state_revision':'fixture-revision','status':'fixture'})
            assert result==1
        elif mode=='worklog':
            result=public_worklog.transact(root,'add','python:%d'%i,7)
            assert result['accepted']
        else:
            result=public_worklog.transact(root,'on' if i%2==0 else 'off')
        print('RESULT '+json.dumps(result),flush=True)
        # Pause outside the lock, so the native writer, which starts its loop later,
        # gets the lock between Python's operations instead of after all of them.
        time.sleep(0.03)
    print('DONE',flush=True)


if __name__=='__main__':main()
