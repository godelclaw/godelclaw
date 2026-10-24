/* Fault injection: the journal deletion commits, but its acknowledgement is
 * lost. Only the subprocess with an explicitly armed fixture flag is affected. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

typedef struct sqlite3 sqlite3;
typedef struct sqlite3_stmt sqlite3_stmt;

/* Witness that the actual native receipt query ran after the lost ack. */
int sqlite3_prepare_v2(sqlite3 *db, const char *sql, int bytes,
                       sqlite3_stmt **statement, const char **tail) {
    static int (*original)(sqlite3 *, const char *, int, sqlite3_stmt **, const char **);
    if (!original) original = dlsym(RTLD_NEXT, "sqlite3_prepare_v2");
    const char *witness = getenv("LLM_TEST_RECEIPT_READ");
    if (witness && sql && strstr(sql, "SELECT CASE WHEN r.status='completed'")) {
        FILE *file = fopen(witness, "a");
        if (file) { fputs("receipt-query\n", file); fclose(file); }
    }
    return original(db, sql, bytes, statement, tail);
}

int unlink(const char *path) {
    static int (*original)(const char *);
    if (!original) original = dlsym(RTLD_NEXT, "unlink");
    const char *flag = getenv("LLM_TEST_COMMIT_FAULT");
    size_t length = strlen(path);
    if (flag && length > 8 && !strcmp(path + length - 8, "-journal") &&
        access(flag, F_OK) == 0) {
        int result = original(path);
        original(flag);
        if (result == 0) { errno = EIO; return -1; }
        return result;
    }
    return original(path);
}
