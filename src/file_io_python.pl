% Keep Python strings as MeTTa strings, including nested status tuples.
file_io_python_read(Path, Limit, Result) :-
    'py-call'(['fsops.read_text_result', Path, Limit], Result,
              [py_string_as(string)]).
