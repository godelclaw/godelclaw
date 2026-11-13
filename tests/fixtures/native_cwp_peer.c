/* A scripted CWP1 peer on a private Unix seqpacket socket, for native tests
 * of clients of the channel service. CeTTa owns orchestration and assertions.
 *
 *   native_cwp_peer SOCKET DIRECTORY
 *
 * One request per connection, served in order. Each request is logged as one
 * line of DIRECTORY/requests: CODE, a tab, the ID, a tab, the body. The reply
 * to a request with code N is read from DIRECTORY/reply-N at that moment,
 * "CODE NAME" with NAME optional; without that file the reply is the
 * service's ordinary one: NEXT 64, SUBMIT 66, WAIT 64 with a fixed token.
 * DIRECTORY/delay-N, a number of milliseconds, holds the reply that long. The
 * socket is ready once DIRECTORY/ready exists. */
#define _POSIX_C_SOURCE 200809L
#define _DEFAULT_SOURCE
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#define PACKET_MAX (6u + 64u + 65536u)
#define TOKEN "0123456789abcdef0123456789abcdef.1"

static void fail(const char *what) { perror(what); exit(2); }

static int read_text(const char *directory, const char *name, char *out, size_t size) {
    char path[4096];
    if (snprintf(path, sizeof(path), "%s/%s", directory, name) >= (int)sizeof(path)) return 0;
    FILE *in = fopen(path, "r");
    if (!in) return 0;
    size_t n = fread(out, 1, size - 1, in);
    fclose(in);
    out[n] = 0;
    while (n && (out[n - 1] == '\n' || out[n - 1] == ' ')) out[--n] = 0;
    return 1;
}

static void pause_ms(long milliseconds) {
    struct timespec t = {milliseconds / 1000, (milliseconds % 1000) * 1000000L};
    while (nanosleep(&t, &t) && errno == EINTR) {}
}

int main(int argc, char **argv) {
    if (argc != 3) { fprintf(stderr, "usage: native_cwp_peer SOCKET DIRECTORY\n"); return 2; }
    const char *directory = argv[2];
    struct sockaddr_un address = {.sun_family = AF_UNIX};
    if (strlen(argv[1]) >= sizeof(address.sun_path)) { errno = ENAMETOOLONG; fail("socket"); }
    strcpy(address.sun_path, argv[1]);
    int listener = socket(AF_UNIX, SOCK_SEQPACKET, 0);
    if (listener < 0) fail("socket");
    unlink(argv[1]);
    if (bind(listener, (struct sockaddr *)&address, sizeof(address))) fail("bind");
    if (chmod(argv[1], 0600)) fail("chmod");
    if (listen(listener, 16)) fail("listen");
    char path[4096];
    snprintf(path, sizeof(path), "%s/ready", directory);
    FILE *ready = fopen(path, "w");
    if (!ready || fclose(ready)) fail("ready");
    static unsigned char packet[PACKET_MAX], reply[PACKET_MAX];
    for (;;) {
        int peer = accept(listener, NULL, NULL);
        if (peer < 0) { if (errno == EINTR) continue; fail("accept"); }
        ssize_t n = recv(peer, packet, sizeof(packet), 0);
        if (n < 6 || memcmp(packet, "CWP1", 4) || 6u + packet[5] > (size_t)n) { close(peer); continue; }
        unsigned code = packet[4], length = packet[5];
        char id[65] = {0};
        memcpy(id, packet + 6, length);
        snprintf(path, sizeof(path), "%s/requests", directory);
        FILE *log = fopen(path, "a");
        if (!log) fail("requests");
        fprintf(log, "%u\t%s\t%.*s\n", code, id, (int)((size_t)n - 6u - length), (const char *)packet + 6 + length);
        if (fclose(log)) fail("requests");
        char name[64], text[256] = {0};
        snprintf(name, sizeof(name), "delay-%u", code);
        if (read_text(directory, name, text, sizeof(text))) pause_ms(atol(text));
        unsigned answer = code == 4 ? 66 : 64;
        char token[65] = {0};
        if (code == 5) strcpy(token, TOKEN);
        snprintf(name, sizeof(name), "reply-%u", code);
        if (read_text(directory, name, text, sizeof(text))) {
            char *space = strchr(text, ' ');
            answer = (unsigned)atoi(text);
            token[0] = 0;
            if (space) snprintf(token, sizeof(token), "%s", space + 1);
        }
        size_t token_length = strlen(token);
        memcpy(reply, "CWP1", 4);
        reply[4] = (unsigned char)answer;
        reply[5] = (unsigned char)token_length;
        memcpy(reply + 6, token, token_length);
        if (send(peer, reply, 6 + token_length, MSG_NOSIGNAL) < 0 && errno != EPIPE) fail("send");
        close(peer);
    }
}
