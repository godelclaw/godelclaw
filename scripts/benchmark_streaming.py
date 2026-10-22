#!/usr/bin/env python3
"""Offline display latency and native SSE scaling; never contacts a provider."""
import argparse
import json
from pathlib import Path
import statistics
import subprocess
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'ui/worklog')]
from stream_sink import Writer,Projection
from viewer import Reader


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--cetta',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path);args=p.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    root=args.output/'latency';root.mkdir(exist_ok=True)
    writer=Writer(root,'benchmark');reader=Reader(root);projection=Projection()
    latencies=[]
    def produce():
        for n in range(60):
            writer.emit(dict(kind='text.delta',item='r',channel='reasoning-summary',text='Evidence '+str(n)+'\n'))
            time.sleep(.021)
    thread=threading.Thread(target=produce);thread.start()
    while thread.is_alive() or writer.queue.unfinished_tasks:
        for record in reader.read():
            projection.apply(record);latencies.append(time.time()-record['time'])
        time.sleep(.15)
    thread.join()
    for record in reader.read():projection.apply(record);latencies.append(time.time()-record['time'])
    samples=[]
    for n in (512,1024,2048):
        wire=('data: '+json.dumps(dict(type='text',text='λ'*128))+'\n\n')*n
        chunks=[wire[i:i+4096] for i in range(0,len(wire),4096)]
        form='!(import! &self '+json.dumps(str(ROOT/'src/stream_sse.metta'))+')\n'
        form+='(= (bench $xs $state $n) (if (== $xs ()) $n (let (SSEBatch $next $out) (sse:feed $state (car-atom $xs) 16777216) (bench (cdr-atom $xs) $next (+ $n (size-atom $out))))))\n'
        form+='!(println! (COUNT (bench ('+' '.join(map(json.dumps,chunks))+') (sse:new) 0)))\n'
        entry=args.output/f'framing-{n}.metta';entry.write_text(form)
        times=[]
        for _ in range(3):
            start=time.perf_counter()
            run=subprocess.run([str(args.cetta),'--lang','petta',str(entry)],capture_output=True,text=True,check=True,timeout=30)
            if f'(COUNT {n})' not in run.stdout:raise RuntimeError(run.stdout[-1000:])
            times.append(time.perf_counter()-start)
        samples.append(dict(events=n,bytes=len(wire.encode()),seconds=times,median=statistics.median(times)))
    result=dict(display_samples=len(latencies),display_p95_ms=sorted(latencies)[int(.95*(len(latencies)-1))]*1000,
        display_max_ms=max(latencies)*1000,native_framing=samples,
        scope='Local queue, reducer and 150ms reader cadence; provider latency and terminal rendering excluded.')
    (args.output/'results.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))


if __name__=='__main__':main()
