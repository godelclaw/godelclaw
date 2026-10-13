:- use_module(library(http/json), [json_write_dict/3]).
:- use_module(library(filesex), [make_directory_path/1, chmod/2]).

first_char(Str, C) :- sub_string(Str, 0, 1, _, C).

%% Commands cross the MeTTa -> Prolog boundary under an explicit quote.  The
%% quote keeps them inert until this deterministic dispatcher evaluates each
%% item once.  The turn cache also makes reduction retries observationally
%% silent.
'run-command-batch-once'(Turn, _RequestedLimit, Commands, Records) :-
    integer(Turn), is_list(Commands),
    ( nb_current(mettaclaw_command_batch, command_batch(Turn, Commands, ExistingRecords, _))
    -> Records = ExistingRecords
    ; nb_current(mettaclaw_command_batch, command_batch(Turn, _, _, _))
    -> Records = [['COMMAND_BATCH_REJECTED:', turn_arguments_changed]]
    ; run_command_list_once(Turn, Commands, Records, Errors),
      nb_setval(mettaclaw_command_batch, command_batch(Turn, Commands, Records, Errors))
    ).

'command-batch-errors'(Turn, Errors) :-
    ( nb_current(mettaclaw_command_batch, command_batch(Turn, _, _, ExistingErrors))
    -> Errors = ExistingErrors
    ; Errors = []
    ).

run_command_list_once(_, [], [], []).
run_command_list_once(Turn, [Command|Rest], [Record|Records], Errors) :-
    run_command_once(Turn, Command, Record, CommandErrors),
    run_command_list_once(Turn, Rest, Records, RestErrors),
    append(CommandErrors, RestErrors, Errors).

run_command_once(Turn, Command, Record, Errors) :-
    ( getenv('METTACLAW_OBSERVED_STRATEGY', '1')
    -> 'py-call'(['strategy_host.reserve_command', Turn, Command], _)
    ; true ),
    ( catch('py-call'(['strategy_host.stopped'], 1), _, fail)
    -> Record = ['COMMAND_NOT_RUN:', [Command, "operator stopped cognition"]],
       Errors = [], record_command_receipt(Turn, withheld, Command, "operator stopped cognition")
    ; catch(( once(command_effect(Command, Value))
              -> (Value == Command -> Status = failed ; Status = ok(Value))
              ; Status = failed ), Exception, Status = exception(Exception)),
      command_status(Status, Command, ResultValue, Errors),
      normalize_command_value(ResultValue, NormalizedValue),
      Record = ['COMMAND_RETURN:', [Command, NormalizedValue]],
      command_disposition(Status, Disposition),
      record_command_receipt(Turn, Disposition, Command, NormalizedValue)
    ).

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
command_effect([rest, Seconds, [quote, Command]], Value) :- !,
    schedule_command(Seconds, Command, Value).
command_effect([rest, Seconds, Command], Value) :-
    is_list(Command), !, schedule_command(Seconds, Command, Value).

command_effect(Command, Value) :-
    'py-call'(['skills_help.command_error', Command], Error),
    ( (Error == ""; Error == '') -> eval(Command, Value) ; Value = Error ).

schedule_command(Seconds, Command, ['RESTING', Seconds, 'scheduled', Command]) :-
    'py-call'(['helper.rest_schedule', Seconds, Command], _),
    eval(['loop-directive-set', 'rest-request', 1], _),
    eval(['loop-directive-set', 'sleep-interval', Seconds], _),
    eval(['loop-directive-set', 'pending-continuation',
          [quote, ['continuation-value', Command]]], _).

shadow_effect_outcome([handled, Value], Value) :- !.
shadow_effect_outcome(["handled", Value], Value) :- !.
shadow_effect_outcome(Outcome,
                      ['Error', invalid_shadow_effect_outcome, Outcome]).

command_status(ok(Value), _Command, Value, []).
command_status(failed, Command, ['Error', command_failed, "Unknown tool or no result; use (help) for available tools", Command],
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
