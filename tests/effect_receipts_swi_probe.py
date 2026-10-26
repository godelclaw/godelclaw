"""Counted SWI oracle route with harmless retained dispatcher-host fixtures."""
import json
from pathlib import Path
import sys
import types

counts={}

def setup():
    import effect_receipts,public_worklog,strategy_host,stream_sink,skills_help
    for module,names in [(effect_receipts,('record_command','record_suffix','view')),
                         (public_worklog,('transact','clean'))]:
        for name in names:
            original=getattr(module,name)
            def wrapped(*args,_fn=original,_name=name,**kwargs):
                counts[_name]=counts.get(_name,0)+1
                return _fn(*args,**kwargs)
            setattr(module,name,wrapped)
    strategy_host.stopped=lambda:0
    skills_help.command_error=lambda command:''
    helper=types.ModuleType('helper');helper.normalize_string=lambda value:str(value)
    sys.modules['helper']=helper
    stream_sink.receipt=lambda *args:1
    return 1

def record(path,value):
    Path(path).write_text(json.dumps({'counts':counts,'view':str(value)},ensure_ascii=True))
    return 1
