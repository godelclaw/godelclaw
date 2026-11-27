#!/bin/sh
# The file tree the file-skill tests read, written byte for byte under $1:
# mixed line endings, invalid UTF-8, NUL, a 10 kB line, an empty file, nested
# directories, an ignored .git, and file, directory and broken links.
set -eu
work=$1
mkdir -p "$work/tree/a/nested" "$work/tree/z" "$work/tree/.git"
{ printf 'first\r\n\316\273 \360\237\214\270\rthird\n\n'; head -c 10000 /dev/zero | tr '\0' x; printf '\377\nlast'; } > "$work/text"
: > "$work/empty"
printf 'needle\n' > "$work/tree/a/nested/deep"
printf 'NEEDLE\n' > "$work/tree/z/leaf"
printf 'needle\n' > "$work/tree/.git/ignored"
printf 'needle one\n\316\273_\357\274\221\357\274\222\nneedle two   \n[broken\n' > "$work/tree/b"
printf 'binary\000needle\n' > "$work/tree/c"
printf 'needle\377\rnext\r\n' > "$work/tree/d"
ln -s "$work/tree/b" "$work/tree/link-file"
ln -s "$work/tree/a" "$work/tree/link-dir"
ln -s "$work/tree/absent" "$work/tree/broken-link"
printf 'previous\r\n' > "$work/write"
chmod 640 "$work/write"
printf 'G\303\266del\000x\n' > "$work/nul-input"
printf 'old\r\nend\r' > "$work/crlf-input"
printf 'bad\377' > "$work/bad-input"
