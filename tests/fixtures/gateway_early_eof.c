#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

/* A real child whose streams end before its lifetime. The optional counted
 * effect happens before EOF, so interruption must preserve uncertainty. */
int main(int argc, char **argv) {
    if (argc != 4) return 2;
    char *end;
    long milliseconds = strtol(argv[1], &end, 10);
    if (*end || milliseconds < 0 || milliseconds > 10000) return 3;
    if (strcmp(argv[2], "-")) {
        int fd = open(argv[2], O_WRONLY | O_CREAT | O_APPEND, 0600);
        if (fd < 0 || write(fd, "effect\n", 7) != 7 || fsync(fd) || close(fd)) return 4;
    }
    if (close(STDOUT_FILENO) || close(STDERR_FILENO)) return 5;
    int marker = open(argv[3], O_WRONLY | O_CREAT | O_EXCL, 0600);
    if (marker < 0 || write(marker, "closed\n", 7) != 7 || close(marker)) return 7;
    struct timespec pause = {milliseconds / 1000, (milliseconds % 1000) * 1000000};
    while (nanosleep(&pause, &pause)) if (errno != EINTR) return 6;
    return 0;
}
