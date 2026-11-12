/* Test fixture, preloaded into a test process: fsync fails with EIO for
 * descriptors opened under FSYNC_FAULT_TARGET. FSYNC_FAULT_MODE=all fails
 * every such fsync (a failure before a rename publishes); directories fails
 * only directory fsyncs (the rename happened, its durability is unknown).
 * Every other call goes to libc unchanged. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <string.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <unistd.h>

static unsigned char tracked[65536];

static int under_target(const char *path) {
    const char *target = getenv("FSYNC_FAULT_TARGET");
    return target && *target && path && !strncmp(path, target, strlen(target));
}
static void track(int fd, const char *path) {
    if (fd >= 0 && fd < 65536) tracked[fd] = (unsigned char)under_target(path);
}
static mode_t mode_of(int flags, va_list args) {
    return (flags & (O_CREAT | O_TMPFILE)) ? (mode_t)va_arg(args, int) : 0;
}

int open(const char *path, int flags, ...) {
    static int (*real)(const char *, int, ...);
    if (!real) real = dlsym(RTLD_NEXT, "open");
    va_list args; va_start(args, flags); mode_t mode = mode_of(flags, args); va_end(args);
    int fd = real(path, flags, mode); track(fd, path); return fd;
}
int open64(const char *path, int flags, ...) {
    static int (*real)(const char *, int, ...);
    if (!real) real = dlsym(RTLD_NEXT, "open64");
    va_list args; va_start(args, flags); mode_t mode = mode_of(flags, args); va_end(args);
    int fd = real(path, flags, mode); track(fd, path); return fd;
}
int openat(int dir, const char *path, int flags, ...) {
    static int (*real)(int, const char *, int, ...);
    if (!real) real = dlsym(RTLD_NEXT, "openat");
    va_list args; va_start(args, flags); mode_t mode = mode_of(flags, args); va_end(args);
    int fd = real(dir, path, flags, mode); track(fd, path); return fd;
}
int openat64(int dir, const char *path, int flags, ...) {
    static int (*real)(int, const char *, int, ...);
    if (!real) real = dlsym(RTLD_NEXT, "openat64");
    va_list args; va_start(args, flags); mode_t mode = mode_of(flags, args); va_end(args);
    int fd = real(dir, path, flags, mode); track(fd, path); return fd;
}

static int injected(int fd) {
    if (fd < 0 || fd >= 65536 || !tracked[fd]) return 0;
    const char *mode = getenv("FSYNC_FAULT_MODE");
    if (!mode) return 0;
    if (!strcmp(mode, "all")) return 1;
    struct stat st;
    return !strcmp(mode, "directories") && fstat(fd, &st) == 0 && S_ISDIR(st.st_mode);
}
int fsync(int fd) {
    static int (*real)(int);
    if (!real) real = dlsym(RTLD_NEXT, "fsync");
    if (injected(fd)) { errno = EIO; return -1; }
    return real(fd);
}
int fdatasync(int fd) {
    static int (*real)(int);
    if (!real) real = dlsym(RTLD_NEXT, "fdatasync");
    if (injected(fd)) { errno = EIO; return -1; }
    return real(fd);
}
