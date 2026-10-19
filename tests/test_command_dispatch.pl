:- use_module(library(plunit)).
:- dynamic probe_count/1, effect_receipt/4, stopped/0, trace_event/1, capture_mode/1.
:- consult('../src/skills.pl').

eval([probe], N) :- assertz(trace_event(command)), retract(probe_count(P)), N is P+1, assertz(probe_count(N)).
eval([failed], "read failed: missing").
eval([throws], _) :- throw(test_error).
eval([stop], ok) :- assertz(stopped).
'py-call'(['skills_help.command_error', _], "").
'py-call'(['strategy_host.stopped'], 1) :- stopped.
'py-call'(['helper.normalize_string', V], V).
'py-call'(['effect_receipts.record_command', T, D, C, R], 1) :-
    assertz(trace_event(record)), assertz(effect_receipt(T,D,C,R)).
'py-call'(['effect_receipts.capture_public_epoch', T], R) :-
    assertz(trace_event(capture(T))),
    ( capture_mode(throw) -> throw(capture_failed)
    ; capture_mode(fail) -> fail
    ; R = 1 ).
reset_probe :- retractall(probe_count(_)), assertz(probe_count(0)),
    retractall(effect_receipt(_,_,_,_)), retractall(stopped),
    retractall(trace_event(_)), retractall(capture_mode(_)),
    catch(nb_delete(mettaclaw_command_batch), _, true).

:- begin_tests(command_dispatch).
test(reentry_returns_receipts_without_repeating) :-
    reset_probe,
    'run-command-batch-once'(7,1,[[probe],[probe]],A),
    'run-command-batch-once'(7,1,[[probe],[probe]],B),
    assertion(A == B), probe_count(2),
    aggregate_all(count,effect_receipt(7,returned,_,_),2).
test(failure_and_exception_do_not_drop_suffix) :-
    reset_probe,
    'run-command-batch-once'(8,1,[[failed],[probe],[throws],[probe]],Records),
    probe_count(2),length(Records,4),
    assertion(effect_receipt(8,exception,[throws],_)).
test(only_explicit_stop_interrupts) :-
    reset_probe,
    'run-command-batch-once'(9,100,[[probe],[stop],[probe]],Records),
    probe_count(1),length(Records,3),
    assertion(effect_receipt(9,withheld,[probe],"operator stopped cognition")).
test(capture_runs_once_per_turn_before_the_first_command) :-
    reset_probe,
    'run-command-batch-once'(10,1,[[probe],[probe]],A),
    'run-command-batch-once'(10,1,[[probe],[probe]],B),
    assertion(A == B),
    findall(E, trace_event(E), Events),
    assertion(Events == [capture(10),command,record,command,record]).
test(capture_failure_or_exception_leaves_commands_and_receipts_unchanged) :-
    forall(member(Mode, [fail, throw]),
      ( reset_probe, assertz(capture_mode(Mode)),
        'run-command-batch-once'(11,1,[[probe],[throws],[probe]],Records),
        length(Records,3), probe_count(2),
        findall(D, effect_receipt(11,D,_,_), Ds),
        assertion(Ds == [returned,exception,returned]),
        findall(E, trace_event(E), Events),
        assertion(Events == [capture(11),command,record,record,command,record]) )).
:- end_tests(command_dispatch).
