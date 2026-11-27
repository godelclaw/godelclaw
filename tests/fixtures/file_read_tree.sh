#!/bin/sh
# The files the whole-file read tests read, written byte for byte under $1:
# Unicode with CRLF, a 10 kB line, an empty file, NUL, malformed UTF-8 of
# every kind, every pair of bytes, 64 KiB from a fixed generator, 4 MiB
# without a newline, and a three-byte file for the budget boundary.
set -eu
work=$1
mkdir -p "$work"
printf 'G\303\266del \342\200\224 \316\273, \360\237\214\270\r\n' > "$work/unicode"
{ head -c 10000 /dev/zero | tr '\0' x; printf '\nend\n'; } > "$work/long-line"
: > "$work/empty"
printf 'before\000after\000\n' > "$work/nul"
printf '\300\257\355\240\200\360\200\200\200\342\202!\360\237\222' > "$work/malformed"
# Every byte as an octal escape, computed once: the loops below never fork.
set -- $(i=0; while [ "$i" -lt 256 ]; do printf '%03o ' "$i"; i=$((i + 1)); done)
for ox in "$@"; do
    for oy in "$@"; do
        printf "\\$ox\\$oy|"
    done
done > "$work/all-byte-pairs"
# 65,536 bytes: the high byte of a 31-bit linear congruential sequence.
state=7
n=0
while [ "$n" -lt 65536 ]; do
    state=$(( (state * 1103515245 + 12345) % 2147483648 ))
    b=$(( (state / 65536) % 256 ))
    printf "\\$((b / 64))$((b / 8 % 8))$((b % 8))"
    n=$((n + 1))
done > "$work/random"
head -c 4194304 /dev/zero | tr '\0' x > "$work/large"
printf 'abc' > "$work/boundary"
mkdir -p "$work/directory"
printf 'unreadable' > "$work/denied"
chmod 0 "$work/denied"
