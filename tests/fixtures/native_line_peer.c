/* A scripted loopback TCP line peer (an IRC server, for instance) for native
 * tests of a client that keeps one connection open. CeTTa owns orchestration
 * and assertions.
 *
 *   native_line_peer DIRECTORY
 *
 * It listens on 127.0.0.1, writes the port to DIRECTORY/port and serves one
 * client at a time. Every line the client sends is appended, without its
 * CR LF, to DIRECTORY/received. Lines appended to DIRECTORY/inject are sent
 * to the client, each with CR LF, as they appear. When DIRECTORY/drop
 * appears the peer removes it and closes the client's connection; it then
 * waits for the next client, whose lines go on in the same files. A client
 * line PING :X is answered PONG :X when DIRECTORY/answer-ping exists. */
#define _POSIX_C_SOURCE 200809L
#define _DEFAULT_SOURCE
#include <arpa/inet.h>
#include <errno.h>
#include <netinet/in.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <unistd.h>

static const char *root;
static void fail(const char *what) { perror(what); exit(2); }
static void path_of(char *out, size_t size, const char *name) {
    if (snprintf(out, size, "%s/%s", root, name) >= (int)size) { errno = ENAMETOOLONG; fail("path"); }
}
static int exists(const char *name) { char path[4096]; path_of(path, sizeof(path), name); return access(path, F_OK) == 0; }
static void append(const char *name, const char *text, size_t length) {
    char path[4096]; path_of(path, sizeof(path), name);
    FILE *out = fopen(path, "ab"); if (!out) fail(path);
    fwrite(text, 1, length, out); fputc('\n', out);
    if (fclose(out)) fail("close");
}
static int send_all(int fd, const char *data, size_t length) {
    while (length) {
        ssize_t n = send(fd, data, length, MSG_NOSIGNAL);
        if (n < 0) { if (errno == EINTR) continue; return 0; }
        data += n; length -= (size_t)n;
    }
    return 1;
}
/* Send the complete lines of DIRECTORY/inject past *offset. */
static int forward(int client, long *offset) {
    char path[4096]; path_of(path, sizeof(path), "inject");
    FILE *in = fopen(path, "rb"); if (!in) return 1;
    if (fseek(in, *offset, SEEK_SET)) { fclose(in); return 1; }
    char line[8192];
    while (fgets(line, sizeof(line), in)) {
        size_t n = strlen(line);
        if (!n || line[n - 1] != '\n') break;
        *offset += (long)n;
        line[n - 1] = 0;
        if (!send_all(client, line, n - 1) || !send_all(client, "\r\n", 2)) { fclose(in); return 0; }
    }
    fclose(in);
    return 1;
}

int main(int argc, char **argv) {
    if (argc != 2) { fprintf(stderr, "native_line_peer DIRECTORY\n"); return 2; }
    signal(SIGPIPE, SIG_IGN);
    root = argv[1];
    int server = socket(AF_INET, SOCK_STREAM, 0); if (server < 0) fail("socket");
    int on = 1; setsockopt(server, SOL_SOCKET, SO_REUSEADDR, &on, sizeof(on));
    struct sockaddr_in address = {.sin_family = AF_INET, .sin_addr.s_addr = htonl(INADDR_LOOPBACK), .sin_port = 0};
    if (bind(server, (struct sockaddr *)&address, sizeof(address)) || listen(server, 4)) fail("listen");
    socklen_t width = sizeof(address);
    if (getsockname(server, (struct sockaddr *)&address, &width)) fail("getsockname");
    char path[4096]; path_of(path, sizeof(path), "port");
    FILE *port = fopen(path, "w"); if (!port) fail(path);
    fprintf(port, "%u\n", ntohs(address.sin_port)); if (fclose(port)) fail("port");
    long offset = 0;
    for (;;) {
        int client = accept(server, NULL, NULL);
        if (client < 0) { if (errno == EINTR) continue; fail("accept"); }
        static char buffer[65536]; size_t used = 0;
        for (;;) {
            if (exists("drop")) { path_of(path, sizeof(path), "drop"); unlink(path); break; }
            if (!forward(client, &offset)) break;
            struct pollfd wait = {.fd = client, .events = POLLIN};
            int ready = poll(&wait, 1, 20);
            if (ready < 0) { if (errno == EINTR) continue; fail("poll"); }
            if (!ready) continue;
            ssize_t n = recv(client, buffer + used, sizeof(buffer) - used - 1, 0);
            if (n <= 0) break;
            used += (size_t)n;
            char *start = buffer, *end;
            while ((end = memchr(start, '\n', used - (size_t)(start - buffer)))) {
                size_t length = (size_t)(end - start);
                if (length && start[length - 1] == '\r') length--;
                append("received", start, length);
                if (length > 6 && !strncmp(start, "PING :", 6) && exists("answer-ping")) {
                    if (!send_all(client, "PONG ", 5) || !send_all(client, start + 5, length - 5) || !send_all(client, "\r\n", 2)) break;
                }
                start = end + 1;
            }
            used -= (size_t)(start - buffer);
            memmove(buffer, start, used);
            if (used >= sizeof(buffer) - 1) used = 0;
        }
        close(client);
    }
}
