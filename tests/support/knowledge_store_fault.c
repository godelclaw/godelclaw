/* Test-only fsync seam for the native publication/readback boundary.
 * A one-shot file selects an ordinary sync failure or process death. The
 * worker is disposable and has no provider, credential or live-agent access.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static int wrote_record;
static int renamed_root;

static int armed(void) {
    const char *flag = getenv("GODELCLAW_TEST_STORE_FAULT");
    return flag && !access(flag, F_OK);
}

ssize_t write(int fd, const void *bytes, size_t size) {
    ssize_t (*original)(int, const void *, size_t) = NULL;
    void *symbol = dlsym(RTLD_NEXT, "write");
    _Static_assert(sizeof original == sizeof symbol, "function pointer width");
    memcpy(&original, &symbol, sizeof original);
    if (!original) { errno = EIO; return -1; }
    ssize_t answer = original(fd, bytes, size);
    struct stat info;
    if (answer > 0 && armed() && !fstat(fd, &info) && S_ISREG(info.st_mode))
        wrote_record = 1;
    return answer;
}

int renameat(int source, const char *old_name, int target, const char *new_name) {
    int (*original)(int, const char *, int, const char *) = NULL;
    void *symbol = dlsym(RTLD_NEXT, "renameat");
    _Static_assert(sizeof original == sizeof symbol, "function pointer width");
    memcpy(&original, &symbol, sizeof original);
    if (!original) { errno = EIO; return -1; }
    int answer = original(source, old_name, target, new_name);
    if (!answer && armed() && !strcmp(new_name, "root.csl")) renamed_root = 1;
    return answer;
}

int fsync(int fd) {
    int (*original)(int) = NULL;
    void *symbol = dlsym(RTLD_NEXT, "fsync");
    _Static_assert(sizeof original == sizeof symbol, "function pointer width");
    memcpy(&original, &symbol, sizeof original);
    if (!original) { errno = EIO; return -1; }
    const char *flag = getenv("GODELCLAW_TEST_STORE_FAULT");
    if (!flag) return original(fd);
    int control = open(flag, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (control < 0) return original(fd);
    char mode[32] = {0};
    ssize_t length = read(control, mode, sizeof mode - 1);
    close(control);
    if (length <= 0) return original(fd);
    struct stat info;
    if (fstat(fd, &info)) return original(fd);
    int directory = ((!strcmp(mode, "directory") || !strcmp(mode, "crash")) && renamed_root)
        || !strcmp(mode, "readback");
    int regular = (!strcmp(mode, "file") || !strcmp(mode, "crash-before")) && wrote_record;
    if ((!directory || !S_ISDIR(info.st_mode)) && (!regular || !S_ISREG(info.st_mode)))
        return original(fd);
    if (unlink(flag)) return original(fd);
    wrote_record = 0;
    renamed_root = 0;
    if (!strcmp(mode, "crash-before")) _exit(87);
    if (!strcmp(mode, "file")) { errno = EIO; return -1; }
    int answer = original(fd);
    if (answer) return answer;
    if (!strcmp(mode, "crash")) _exit(86);
    errno = EIO;
    return -1;
}
