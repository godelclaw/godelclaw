% Keep Python strings as MeTTa strings, including nested status tuples.
file_io_python_read(Path, Limit, Result) :-
    'py-call'(['fsops.read_text_result', Path, Limit], Result,
              [py_string_as(string)]).

file_io_python_lines(Path, Start, Count, Result) :-
    'py-call'(['fsops.line_window_result', Path, Start, Count], Raw, [py_string_as(string)]), file_io_python_booleans(Raw, Result).
file_io_python_tree(Path, Depth, Limit, Offset, Skips, Result) :-
    'py-call'(['fsops.tree_page_result', Path, Depth, Limit, Offset, Skips], Raw, [py_string_as(string)]), file_io_python_booleans(Raw, Result).
file_io_python_grep(Pattern, Path, Limit, Offset, Skips, Result) :-
    'py-call'(['fsops.grep_page_result', Pattern, Path, Limit, Offset, Skips], Raw, [py_string_as(string)]), file_io_python_booleans(Raw, Result).
file_io_python_update(Path, Op, First, Second, Backup, Result) :-
    'py-call'(['file_tools.update_result', Path, Op, First, Second, Backup], Raw, [py_string_as(string)]), file_io_python_booleans(Raw, Result).
file_io_python_exists(Path, Result) :-
    (exists_file(Path) -> Result = true; Result = false).

file_io_python_booleans(@(true), true) :- !.
file_io_python_booleans(@(false), false) :- !.
file_io_python_booleans(true, true) :- !.
file_io_python_booleans(false, false) :- !.
file_io_python_booleans([], []) :- !.
file_io_python_booleans([Head|Tail], [Converted|Rest]) :- !,
    file_io_python_booleans(Head, Converted), file_io_python_booleans(Tail, Rest).
file_io_python_booleans(Value, Value).
