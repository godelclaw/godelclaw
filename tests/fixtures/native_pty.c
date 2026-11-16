#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/wait.h>
#include <termios.h>
#include <unistd.h>

/* Transport fixture: a real controlling terminal, resize, bytes, termination
 * and physical termios observations. It contains no console policy. */
int main(int argc, char **argv) {
    if (argc < 3) return 2;
    int master = posix_openpt(O_RDWR | O_NOCTTY | O_CLOEXEC | O_NONBLOCK);
    if (master < 0 || grantpt(master) || unlockpt(master)) return 3;
    int slave = open(ptsname(master), O_RDWR | O_NOCTTY | O_CLOEXEC);
    FILE *capture = fopen(argv[1], "wb");
    if (slave < 0 || !capture) return 4;
    struct termios original;
    if (tcgetattr(slave, &original)) return 5;
    struct winsize size = {.ws_col = 80, .ws_row = 24};
    if (ioctl(slave, TIOCSWINSZ, &size)) return 6;
    pid_t child = fork();
    if (child < 0) return 7;
    if (!child) {
        if (setsid() < 0 || ioctl(slave, TIOCSCTTY, 0)) _exit(8);
        for (int fd = 0; fd < 3; ++fd) if (dup2(slave, fd) < 0) _exit(9);
        close(master); close(slave); fclose(capture);
        execv(argv[2], argv + 2); _exit(10);
    }
    char line[128]; int status = 0, ended = 0; size_t total = 0;
    while (fgets(line, sizeof(line), stdin)) {
        char operation; unsigned a = 0, b = 0;
        if (sscanf(line, "%c %u %u", &operation, &a, &b) < 1) break;
        if (operation == 'W') {
            size_t remaining = a; unsigned char bytes[8192];
            while (remaining) {
                size_t take = remaining < sizeof(bytes) ? remaining : sizeof(bytes);
                if (fread(bytes, 1, take, stdin) != take) return 11;
                for (size_t at = 0; at < take;) {
                    ssize_t n = write(master, bytes + at, take - at);
                    if (n > 0) at += (size_t)n;
                    else if (errno == EINTR) continue;
                    else if (errno == EAGAIN) { struct pollfd p = {master, POLLOUT, 0}; if (poll(&p, 1, 1000) <= 0) return 12; }
                    else return 13;
                }
                remaining -= take;
            }
        } else if (operation == 'S') {
            size.ws_col = (unsigned short)a; size.ws_row = (unsigned short)b;
            if (ioctl(master, TIOCSWINSZ, &size)) return 14;
        } else if (operation == 'K') {
            if (kill(child, (int)a) && errno != ESRCH) return 15;
        } else if (operation == 'R') {
            struct pollfd p = {master, POLLIN, 0};
            int ready = poll(&p, 1, (int)a);
            if (ready > 0) {
                unsigned char bytes[65536];
                for (;;) {
                    ssize_t n = read(master, bytes, sizeof(bytes));
                    if (n <= 0) break;
                    if (fwrite(bytes, 1, (size_t)n, capture) != (size_t)n) return 16;
                    total += (size_t)n;
                }
                fflush(capture);
            }
        }
        if (!ended && waitpid(child, &status, WNOHANG) == child) ended = 1;
        struct termios current;
        int restored = !tcgetattr(slave, &current) &&
            current.c_iflag == original.c_iflag && current.c_oflag == original.c_oflag &&
            current.c_lflag == original.c_lflag && current.c_cflag == original.c_cflag &&
            !memcmp(current.c_cc, original.c_cc, NCCS);
        printf("{\"bytes\":%zu,\"ended\":%s,\"restored\":%s,\"status\":%d}\n",
            total, ended ? "true" : "false", restored ? "true" : "false", status);
        fflush(stdout);
    }
    if (!ended) { kill(child, SIGTERM); for (int i = 0; i < 100 && !ended; ++i) { usleep(10000); ended = waitpid(child, &status, WNOHANG) == child; } }
    if (!ended) { kill(child, SIGKILL); waitpid(child, &status, 0); }
    fclose(capture); close(slave); close(master); return 0;
}
