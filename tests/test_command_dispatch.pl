:- use_module(library(plunit)).
:- dynamic probe_count/1, effect_receipt/4, stopped/0.
:- consult('../src/skills.pl').

eval([probe], N) :- retract(probe_count(P)), N is P+1, assertz(probe_count(N)).
eval([failed], "read failed: missing").
eval([throws], _) :- throw(test_error).
eval([stop], ok) :- assertz(stopped).
'py-call'(['skills_help.command_error', _], "").
'py-call'(['strategy_host.stopped'], 1) :- stopped.
'py-call'(['helper.normalize_string', V], V).
'py-call'(['effect_receipts.record_command', T, D, C, R], 1) :- assertz(effect_receipt(T,D,C,R)).
reset_probe :- retractall(probe_count(_)), assertz(probe_count(0)),
    retractall(effect_receipt(_,_,_,_)), retractall(stopped),
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
:- end_tests(command_dispatch).
