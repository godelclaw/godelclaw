% A caller can override the default, including zero for no deadline.
shell(Cmd, Out) :-
    ( getenv('METTACLAW_SHELL_TIMEOUT_SECONDS', Raw)
    -> atom_number(Raw, Seconds) ; Seconds = 120 ),
    shell(Cmd, Seconds, Out).
shell(Cmd, RawSeconds, Out) :-
    ( number(RawSeconds) -> Seconds = RawSeconds
    ; atom_number(RawSeconds, Seconds) ),
    must_be(number, Seconds),
    (Seconds >= 0 -> true ; domain_error(nonnegative_timeout, Seconds)),
    ( Seconds =:= 0
    -> Program = sh, Args = ['-c', Cmd]
    ; Program = timeout, number_string(Seconds, Deadline),
      Args = ['-k', '5s', Deadline, 'sh', '-c', Cmd] ),
    tmp_file_stream(text, TmpFile, TmpOut),
    setup_call_cleanup(true,
        ( setup_call_cleanup(
            process_create(path(Program), Args,
                           [stdout(stream(TmpOut)), stderr(stream(TmpOut)), process(PID)]),
            wait_shell(PID, Status), close(TmpOut)),
          read_file_to_string(TmpFile, Text, []),
          ( memberchk(Status, [exit(124), exit(137), killed(9), killed(15)])
          -> format(string(Out), "SHELL_TIMEOUT after ~w seconds\n~s", [Seconds, Text])
          ; Status = exit(Code), Code =\= 0
          -> format(string(Out), "SHELL_ERROR exit ~w\n~s", [Code, Text])
          ; Text == "" -> Out = "SHELL_OK_NO_OUTPUT (exit 0)"
          ; Out = Text ) ),
        ( catch(close(TmpOut), _, true), catch(delete_file(TmpFile), _, true) )).

% An explicitly running child is legitimate waiting, not a stalled evaluator.
wait_shell(PID, Status) :-
    catch('py-call'(['process_supervision.progress', 'shell-wait', 60], _), _, true),
    process_wait(PID, Current, [timeout(30)]),
    ( Current == timeout -> wait_shell(PID, Status) ; Status = Current ).
