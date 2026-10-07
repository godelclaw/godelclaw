:- use_module(library(http/json), [json_write_dict/3]).
:- use_module(library(filesex), [make_directory_path/1, chmod/2]).

%Gets shell command return, plus the process if time limit is not met, returning timeout_error:
shell(Cmd, Out) :-
    tmp_file_stream(text, TmpFile, TmpInit),
    close(TmpInit),
    open(TmpFile, write, TmpOut, [type(text)]),
    catch(
        setup_call_cleanup(
            process_create(
                path(timeout),
                ['-k', '1s', '5s', 'sh', '-c', Cmd],
                [ stdout(stream(TmpOut)),
                  stderr(stream(TmpOut)),
                  process(P)
                ]
            ),
            (
                process_wait(P, Status),
                close(TmpOut),
                read_file_to_string(TmpFile, Text, [])
            ),
            (
                catch(close(TmpOut), _, true),
                catch(delete_file(TmpFile), _, true)
            )
        ),
        E,
        (
            catch(close(TmpOut), _, true),
            catch(delete_file(TmpFile), _, true),
            throw(E)
        )
    ),
    ( Status = exit(124) -> Out = timeout_error
    ; Status = exit(137) -> Out = timeout_error
    ; Status = killed(_) -> Out = timeout_error
    ; Text == "" ->
        %% Empty output was indistinguishable from a failed command, so a
        %% mistyped shell string looked exactly like "no matches found".
        %% Say which one happened.
        ( Status = exit(0)
          -> Out = "SHELL_OK_NO_OUTPUT (command succeeded, printed nothing)"
        ; Status = exit(Code)
          -> format(string(Out),
                    "SHELL_ERROR exit ~w (command failed, printed nothing)",
                    [Code])
        ; Out = "SHELL_ERROR (command did not exit normally)"
        )
    ; Out = Text
    ).


first_char(Str, C) :- sub_string(Str, 0, 1, _, C).

%% Commands cross the MeTTa -> Prolog boundary under an explicit quote.  The
%% quote keeps them inert until this deterministic dispatcher evaluates each
%% item once.  The turn cache also makes reduction retries observationally
%% silent.
'run-command-batch-once'(Turn, RequestedLimit, Commands, Records) :-
    command_batch_limit(RequestedLimit, Limit),
    ( nb_current(mettaclaw_command_batch_cache,
                 command_batch(CachedTurn, CachedLimit, CachedCommands,
                               CachedRecords, _CachedErrors)),
      CachedTurn == Turn
    -> ( CachedLimit == Limit,
         CachedCommands =@= Commands
       -> copy_term(CachedRecords, Records)
       ;  Records = [['COMMAND_BATCH_REJECTED:',
                      "different limit or commands proposed after turn commitment"]]
       )
    ; admit_command_prefix(Limit, Commands, Admitted, Deferred),
      run_command_list_unless_stimulus(Turn, Admitted,
                                       ExecutedRecords, Errors),
      deferred_command_record(Turn, Limit, Deferred, DeferredRecords),
      append(ExecutedRecords, DeferredRecords, Records),
      nb_setval(mettaclaw_command_batch_cache,
                command_batch(Turn, Limit, Commands, Records, Errors))
    ), !.

'command-batch-errors'(Turn, Errors) :-
    ( nb_current(mettaclaw_command_batch_cache,
                 command_batch(CachedTurn, _Limit, _Commands, _Records,
                               CachedErrors)),
      CachedTurn == Turn
    -> copy_term(CachedErrors, Errors)
    ; Errors = []
    ), !.

command_batch_limit(Requested, Requested) :-
    integer(Requested),
    Requested > 0, !.
command_batch_limit(_, 1).

admit_command_prefix(0, Commands, [], Commands) :- !.
admit_command_prefix(_, [], [], []) :- !.
admit_command_prefix(Limit, [Command|Rest], [Command|Admitted], Deferred) :-
    Next is Limit - 1,
    admit_command_prefix(Next, Rest, Admitted, Deferred).

deferred_command_record(_, _, [], []) :- !.
deferred_command_record(Turn, Limit, Deferred,
                        [['COMMAND_BATCH_DEFERRED:',
                          [limit, Limit, commands, Deferred]]]) :-
    record_suffix_receipt(Turn, deferred, Deferred, batch_limit).

run_command_list_unless_stimulus(_, [], [], []).
run_command_list_unless_stimulus(Turn, Commands, Records, Errors) :-
    ( command_stimulus_free(Turn, Commands)
    -> Commands = [Command|Rest],
       run_command_once(Turn, Command, Record, CommandErrors),
       ( command_result_permits_suffix(Command, Record)
       -> run_command_list_unless_stimulus(Turn, Rest,
                                           RestRecords, RestErrors)
       ;  partition_after_stimulus(Rest, Independent, Dependent),
          failed_effect_record(Turn, Command, Dependent,
                               InterruptedRecords),
          run_command_list_once(Turn, Independent,
                                IndependentRecords, RestErrors),
          append(InterruptedRecords, IndependentRecords, RestRecords)
       ),
       Records = [Record|RestRecords],
       append(CommandErrors, RestErrors, Errors)
    ; partition_after_stimulus(Commands, Independent, Dependent),
      stimulus_interrupted_record(Turn, Dependent, InterruptedRecords),
      run_command_list_once(Turn, Independent,
                            IndependentRecords, Errors),
      append(InterruptedRecords, IndependentRecords, Records)
    ).

partition_after_stimulus(Commands, Independent, Dependent) :-
    catch('py-call'(['action_graph.partition_after_stimulus', Commands],
                    [Independent, Dependent]), _, fail), !.
partition_after_stimulus(Commands, [], Commands).

command_result_permits_suffix(Command,
                              ['COMMAND_RETURN:', [Command, Result]]) :-
    known_fallible_effect(Command), !,
    successful_effect_result(Command, Result).
command_result_permits_suffix(Command, _Record) :-
    \+ known_fallible_effect(Command).

known_fallible_effect(['delete-my-recent'|_]).
known_fallible_effect(['delete-message-exact'|_]).
known_fallible_effect([send|_]).
known_fallible_effect(['send-telegram-chat'|_]).
known_fallible_effect(['send-file'|_]).
known_fallible_effect(['send-image'|_]).

successful_effect_result(['delete-message-exact'|_], Result) :- !,
    effect_result_prefix(Result, "deleted message ").
successful_effect_result(['delete-my-recent'|_], Result) :- !,
    effect_result_string(Result, Text),
    split_string(Text, "|", " ", Parts),
    Parts \== [],
    forall(member(Part, Parts),
           effect_result_prefix(Part, "deleted message ")).
successful_effect_result([send|_], Result) :- !,
    effect_result_prefix(Result, "sent ").
successful_effect_result(['send-telegram-chat'|_], Result) :- !,
    effect_result_prefix(Result, "sent ").
successful_effect_result(['send-file'|_], Result) :- !,
    effect_result_prefix(Result, "sent ").
successful_effect_result(['send-image'|_], Result) :- !,
    effect_result_prefix(Result, "sent ").

effect_result_prefix(Result, Prefix) :-
    effect_result_string(Result, Text),
    string_lower(Text, Lower),
    string_lower(Prefix, LowerPrefix),
    sub_string(Lower, 0, _, _, LowerPrefix).

effect_result_string(Result, Result) :- string(Result), !.
effect_result_string(Result, Text) :- atom(Result), atom_string(Result, Text).

failed_effect_record(_, _, [], []) :- !.
failed_effect_record(Turn, Command, Commands,
                     [['COMMAND_BATCH_INTERRUPTED:',
                       [reason, failed_effect, after, Command,
                        commands, Commands]]]) :-
    record_suffix_receipt(Turn, withheld, Commands, failed_effect).

stimulus_interrupted_record(_, [], []) :- !.
stimulus_interrupted_record(Turn, Commands,
                            [['COMMAND_BATCH_INTERRUPTED:',
                              [reason, new_stimulus, commands, Commands]]]) :-
    record_suffix_receipt(Turn, withheld, Commands, new_stimulus).

run_command_list_once(_, [], [], []).
run_command_list_once(Turn, [Command|Rest],
                      [Record|RestRecords], Errors) :-
    run_command_once(Turn, Command, Record, CommandErrors),
    run_command_list_once(Turn, Rest, RestRecords, RestErrors),
    append(CommandErrors, RestErrors, Errors).

%% Input newer than the turn's frontier withholds only what it could change:
%% a conversation effect into a chat whose new input the model has not read,
%% together with its dependent suffix.  Input in another chat, and commands
%% that do not speak, leave the chosen batch running.  Without the in-process
%% per-command check (CeTTa, shadow backends) any newer input still withholds.
command_stimulus_free(Turn, _) :-
    effect_turn_stimulus_free(Turn), !.
command_stimulus_free(Turn, [Command|_]) :-
    catch('py-call'(['telegram.effect_command_unaffected', Turn, Command],
                    Value), _, fail),
    stimulus_free_value(Value), !.

effect_turn_stimulus_free(Turn) :-
    ( getenv('METTACLAW_STIMULUS_FRONTIER_PATH', _)
    -> stimulus_frontier_free(Turn)
    ;  true
    ),
    effect_turn_stimulus_value(Turn, Value),
    ( Value == unavailable
    -> true
    ; stimulus_free_value(Value)
    ), !.

effect_turn_stimulus_value(Turn, Value) :-
    ( getenv('METTACLAW_EFFECT_BACKEND', 'tmux-shadow')
    -> Goal = 'py-call'(['effect_backend.turn_stimulus_free', Turn], Value)
    ;  Goal = 'py-call'(['telegram.effect_turn_stimulus_free', Turn], Value)
    ),
    ( catch(Goal, _, fail) -> true ; Value = unavailable ).

%% SWI-PeTTa and CeTTa's lib-prolog bridge preserve the same truth value with
%% slightly different scalar representations.  Admit only explicit true/one
%% witnesses; everything else still fails closed as a newer stimulus.
stimulus_free_value(Value) :-
    ( Value == 1
    ; Value == 1.0
    ; Value == true
    ; Value == 'True'
    ; Value == "1"
    ; Value == "true"
    ; Value == "True"
    ).

stimulus_frontier_free(Turn) :-
    getenv('METTACLAW_STIMULUS_FRONTIER_PATH', Path),
    catch(read_file_to_string(Path, Text, []), _, fail),
    split_string(Text, " \t\r\n", " \t\r\n", Parts),
    Parts = ["1", TurnText, CapturedText, LiveText],
    number_string(RecordedTurn, TurnText),
    number_string(Captured, CapturedText),
    number_string(Live, LiveText),
    RecordedTurn =:= Turn,
    Captured =:= Live.

run_command_once(Turn, Command,
                 ['COMMAND_WITHHELD:', [Command, "strategy requires reconsideration"]], []) :-
    getenv('METTACLAW_OBSERVED_STRATEGY', '1'),
    \+ catch('py-call'(['strategy_host.reserve_command', Turn, Command], 1), _, fail), !.
run_command_once(Turn, Command,
                 ['COMMAND_RETURN:', [Command, Normalized]], Errors) :-
    catch(( once(command_effect(Command, Raw)) -> Status = ok(Raw)
                                              ; Status = failed ),
          Exception,
          Status = exception(Exception)),
    command_status(Status, Command, Value, Errors),
    normalize_command_value(Value, RawNormalized),
    ( getenv('METTACLAW_OBSERVED_STRATEGY', '1')
    -> 'py-call'(['strategy_host.annotate_result', Command, RawNormalized], Normalized)
    ; Normalized = RawNormalized
    ),
    command_disposition(Status, Disposition),
    record_command_receipt(Turn, Disposition, Command, Normalized).

command_disposition(ok(_), returned).
command_disposition(failed, no_result).
command_disposition(exception(_), exception).

record_command_receipt(Turn, Disposition, Command, Result) :-
    ( catch('py-call'(['effect_receipts.record_command', Turn, Disposition,
                       Command, Result], _), _, fail)
    -> true
    ;  fallback_command_receipt(Turn, Disposition, Command, Result)
    ).

record_suffix_receipt(Turn, Disposition, Commands, Reason) :-
    ( catch('py-call'(['effect_receipts.record_suffix', Turn, Disposition,
                       Commands, Reason], _), _, fail)
    -> true
    ;  fallback_suffix_receipt(Turn, Disposition, Commands, Reason)
    ).

fallback_command_receipt(Turn, Disposition, Command, Result) :-
    receipt_text(Disposition, DispositionText),
    receipt_text(Command, CommandText),
    receipt_text(Result, ResultText),
    get_time(RecordedAt),
    append_fallback_receipt(_{
        schema: 1,
        recorded_at: RecordedAt,
        turn: Turn,
        disposition: DispositionText,
        command: CommandText,
        result: ResultText
    }).

fallback_suffix_receipt(Turn, Disposition, Commands, Reason) :-
    receipt_text(Disposition, DispositionText),
    receipt_text(Commands, CommandsText),
    receipt_text(Reason, ReasonText),
    get_time(RecordedAt),
    append_fallback_receipt(_{
        schema: 1,
        recorded_at: RecordedAt,
        turn: Turn,
        disposition: DispositionText,
        commands: CommandsText,
        reason: ReasonText
    }).

receipt_text(Value, Text) :-
    with_output_to(string(Text),
                   write_term(Value, [quoted(true), max_depth(40)])).

append_fallback_receipt(Entry) :-
    ( getenv('METTACLAW_EFFECT_RECEIPT_PATH', Path), Path \== ''
    -> file_directory_name(Path, Directory),
       make_directory_path(Directory),
       setup_call_cleanup(
           open(Path, append, Stream,
                [encoding(utf8), create([]), lock(write)]),
           ( chmod(Path, 0o600),
             json_write_dict(Stream, Entry, [width(0)]),
             nl(Stream),
             flush_output(Stream)
           ),
           close(Stream))
    ; true
    ).

%% Qualification replaces only the effect provider beneath the real parser
%% and batch dispatcher. Shadow mode is fail-closed: its Python provider has
%% no passthrough result, so an unsupported command cannot reach eval/2.
command_effect(Command, Value) :-
    getenv('METTACLAW_EFFECT_BACKEND', 'tmux-shadow'), !,
    'py-call'(['effect_backend.dispatch', Command], Outcome),
    shadow_effect_outcome(Outcome, Value).
command_effect(Command, _Value) :-
    ( ( getenv('METTACLAW_EFFECT_BACKEND_STATE', State), State \== '' )
    ; ( getenv('METTACLAW_EFFECT_BACKEND', Backend), Backend \== '',
        Backend \== 'tmux-shadow' )
    ), !,
    throw(error(permission_error(execute, effect_backend, Command),
                context(command_effect/2,
                        'inconsistent shadow effect configuration'))).
command_effect(Command, Value) :-
    eval(Command, Value).

shadow_effect_outcome([handled, Value], Value) :- !.
shadow_effect_outcome(["handled", Value], Value) :- !.
shadow_effect_outcome(Outcome,
                      ['Error', invalid_shadow_effect_outcome, Outcome]).

command_status(ok(Value), _Command, Value, []).
command_status(failed, Command, ['Error', command_failed],
               [['command_returned_no_result', Command,
                 "command returned no result"]]).
command_status(exception(Exception), Command, ['Error', Message],
               [['command_raised_exception', Command, Message]]) :-
    message_to_string(Exception, Message).

normalize_command_value(Value, Normalized) :-
    ( catch('py-call'(['helper.normalize_string', Value], Normalized), _, fail)
    -> true
    ; with_output_to(string(Normalized),
                     write_term(Value, [quoted(true)]))
    ).

gc(true) :-
    garbage_collect,
    garbage_collect_atoms,
    trim_stacks.
