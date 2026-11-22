; Python runs only for an explicitly requested coding-tool program.
; The stateless callables transport tool arguments to the captured native
; component worker; no provider, input, memory or checkpoint policy runs here.
!(import! &self ./components_cetta.metta)
(= (component:manifest (ComponentSetV1 $refs $omitted $errors))
   (JsonObject ((JsonPair "tools" (JsonArray (map-atom $refs $ref
     (case $ref
       (((NativeComponentV1 $name $description $parameters $implementation $digest)
         (JsonObject ((JsonPair "name" $name) (JsonPair "description" $description)
           (JsonPair "parameters" (JsonArray $parameters)) (JsonPair "sha256" $digest)
           (JsonPair "builtin" (case $implementation (((ComponentBuiltinV1 $name) $name) ($other ""))))
           (JsonPair "source" (case $implementation (((ComponentModuleV1 $source) $source) ($other "")))))))))))))))
(= (component:from-manifest $value)
   (case (sf:get $value "tools" (JsonArray ()))
     (((JsonArray $rows)
       (ComponentSetV1 (map-atom $rows $row
         (let* (($name (sf:get $row "name" "")) ($description (sf:get $row "description" ""))
                ($parameters (mode-host:array $row "parameters")) ($builtin (sf:get $row "builtin" ""))
                ($source (sf:get $row "source" "")) ($digest (sf:get $row "sha256" ""))
                ($implementation (if (== $builtin "") (ComponentModuleV1 $source) (ComponentBuiltinV1 $builtin))))
           (NativeComponentV1 $name $description $parameters $implementation $digest))) 0 ""))
      ($bad (ComponentSetV1 () 0 "invalid manifest")))))
(= (component:python $code) (component:python $code (component:capture-tools)))
(= (component:python $code $snapshot)
   (let* (($root (artifact:directory)) ($_ (fs:make-directories $root 448)) ($id (system:uuid4-hex))
          ($manifest (path:join $root (str:join "" (".python-tools-" $id ".json"))))
          ($script (path:join $root (str:join "" (".python-code-" $id ".txt"))))
          ($stdout (path:join $root (str:concat ".python-out-" $id)))
          ($stderr (path:join $root (str:concat ".python-err-" $id)))
          ($tools (sf:get (component:manifest $snapshot) "tools" (JsonArray ())))
          ($preamble (str:join "" ("_manifest = " (json:stringify-python $manifest) "\n"
             "_tools = __import__('json').loads(" (json:stringify-python (turn:json $tools)) ")\n"
             "_code = " (json:stringify-python $code) "\n" (component:python-prefix))))
          ($saved (sf:replace $manifest (turn:json (component:manifest $snapshot)) 384 False))
          ($program (sf:replace $script $preamble 384 False)))
     (if (or (!= $saved True) (!= $program True)) (Error python-tool (Fault staging-failed))
       (let* (($binary (system:getenv-or-default "METTACLAW_PYTHON_TOOL_BIN" "python3"))
              ($cwd (system:getcwd))
              ($argv ("/usr/bin/env" $binary $script))
              ($timeout (component:dynamic-timeout-ms))
              ($child (catch (io:submit (proc:request $argv $cwd inherit $timeout $stdout $stderr))))
              ($ended (case $child
                (((io:pending $handle) (rpc:await $handle (+ (system:monotonic-ns) (* (+ $timeout 1000) 1000000))))
                 ($bad (Error python-tool (Fault launch-failed))))))
              ($out (artifact:retain-file $stdout)) ($err (artifact:retain-file $stderr))
              ($known (case $ended (((proc:finished $_ exited $_ $_ $_) True) ($other False))))
              ($_ (if $known (fs:remove $manifest) ())) ($_ (if $known (fs:remove $script) ())))
         (if (or (artifact:failed? $out) (artifact:failed? $err))
           (Error python-tool (Incomplete (output-not-retained (diagnostic:text ($child $ended $out $err) 600))))
           (case $ended
             (((proc:finished $_ exited 0 $_ $_) (artifact:file-view $out 4000))
              ($other (str:join "" ("Python tool result and effects are unknown; inspect before retrying.\n"
                (artifact:file-view $out 3500) "\n" (artifact:file-view $err 800)
                "\nInvocation: " $script "; captured tools: " $manifest))))))))))
(= (component:python-prefix)
   "import os as _os, sys as _sys, json as _json, subprocess as _sub\nimport io as _io, contextlib as _contextlib, traceback as _traceback, threading as _threading\n_worker = None\n_worker_lock = _threading.RLock()\ndef _bind_native_tool(_name, _parameters):\n    def _call(*_args, **_kwargs):\n        global _worker\n        if len(_args) > len(_parameters):\n            raise TypeError(\"too many tool arguments\")\n        for _key, _value in zip(_parameters, _args):\n            if _key in _kwargs:\n                raise TypeError(\"duplicate tool argument\")\n            _kwargs[_key] = _value\n        with _worker_lock:\n            if _worker is None:\n                _binary = _os.environ.get(\"CETTA_BIN\", \"cetta\")\n                _entry = _os.path.join(_os.environ[\"METTACLAW_RUNTIME_ROOT\"], \"modes/component_worker_cetta.metta\")\n                _worker = _sub.Popen([_binary, \"--no-results\", \"--lang\", \"petta\", \"--profile\", \"extended\",\n                                      \"--import-mode\", \"ancestor-walk\", _entry, \"--\", \"serve\", _manifest],\n                                     stdin=_sub.PIPE, stdout=_sub.PIPE, stderr=_sub.DEVNULL, text=True, bufsize=1)\n            _worker.stdin.write(_json.dumps({\"tool\": _name, \"arguments\": _kwargs}) + \"\\n\")\n            _worker.stdin.flush()\n            while True:\n                _line = _worker.stdout.readline()\n                if not _line:\n                    raise RuntimeError(\"native tool result and effects are unknown; inspect before retrying\")\n                if _line.startswith(\"CLAW_COMPONENT \"):\n                    _answer = _json.loads(_line[15:])\n                    if not _answer.get(\"ok\"):\n                        return \"Tool execution failed: \" + str(_answer.get(\"error\", \"native component failed\"))\n                    return _answer.get(\"result\")\n                _sys.stdout.write(_line)\n    return _call\n_scope = {\"__builtins__\": __builtins__, \"os\": _os, \"sys\": _sys, \"json\": _json,\n          \"re\": __import__(\"re\"), \"subprocess\": _sub, \"print\": print}\nfor _tool in _tools:\n    if _tool[\"name\"] != \"python\":\n        _scope[_tool[\"name\"]] = _bind_native_tool(_tool[\"name\"], _tool[\"parameters\"])\n_buffer = _io.StringIO()\ntry:\n    with _contextlib.redirect_stdout(_buffer), _contextlib.redirect_stderr(_buffer):\n        exec(compile(_code, \"<python>\", \"exec\"), _scope)\n    _output = _buffer.getvalue()\n    if not _output:\n        _output = str(_scope[\"result\"]) if \"result\" in _scope else \"[python: no output]\"\nexcept Exception:\n    _output = \"[python ERROR]\\n\" + _traceback.format_exc() + \"\\n[captured stdout: \" + _buffer.getvalue() + \"]\"\nfinally:\n    if _worker is not None:\n        try:\n            _worker.stdin.close()\n            _worker.wait(timeout=0.5)\n        except Exception:\n            _worker.terminate()\n            try:\n                _worker.wait(timeout=0.5)\n            except Exception:\n                _worker.kill()\n                _worker.wait()\nprint(_output, end=\"\")\n")
