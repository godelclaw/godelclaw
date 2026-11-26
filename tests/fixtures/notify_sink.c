/* A service manager's notification socket, for tests: bind a datagram socket
 * at PATH, append each message received to OUT on a line of its own, and exit
 * 0 after COUNT messages or 3 when TIMEOUT_MS passes first.
 *
 *   notify_sink PATH OUT COUNT TIMEOUT_MS
 */
#define _POSIX_C_SOURCE 200809L
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

static long long now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (long long)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}

int main(int argc, char **argv) {
    if (argc != 5) {
        fprintf(stderr, "usage: notify_sink PATH OUT COUNT TIMEOUT_MS\n");
        return 2;
    }
    struct sockaddr_un address;
    memset(&address, 0, sizeof address);
    address.sun_family = AF_UNIX;
    if (strlen(argv[1]) >= sizeof address.sun_path) return 2;
    strcpy(address.sun_path, argv[1]);
    int fd = socket(AF_UNIX, SOCK_DGRAM, 0);
    if (fd < 0 || bind(fd, (struct sockaddr *)&address, sizeof address) != 0) return 2;
    FILE *out = fopen(argv[2], "a");
    if (!out) return 2;
    long count = strtol(argv[3], NULL, 10);
    long long deadline = now_ms() + strtoll(argv[4], NULL, 10);
    char buffer[512];
    for (long received = 0; received < count;) {
        long long left = deadline - now_ms();
        if (left <= 0) return 3;
        struct pollfd ready = {.fd = fd, .events = POLLIN};
        if (poll(&ready, 1, (int)left) <= 0) continue;
        ssize_t n = recv(fd, buffer, sizeof buffer - 1, 0);
        if (n < 0) return 2;
        fwrite(buffer, 1, (size_t)n, out);
        fputc('\n', out);
        fflush(out);
        received++;
    }
    fclose(out);
    return 0;
}
