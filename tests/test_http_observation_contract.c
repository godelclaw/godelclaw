#define _POSIX_C_SOURCE 200809L
#include "http_worker.h"
#include <curl/curl.h>
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

static uint64_t monotonic_ms(void) {
    struct timespec t;
    assert(clock_gettime(CLOCK_MONOTONIC, &t) == 0);
    return (uint64_t)t.tv_sec * 1000u + (uint64_t)t.tv_nsec / 1000000u;
}

static CettaHttpResult observe_until_complete(CettaHttpWorker *worker, uint64_t id) {
    uint64_t generation = 0, deadline = monotonic_ms() + 10000;
    CettaHttpResult result = {0};
    unsigned observations = 0;
    for (;;) {
        CettaHttpProgress progress;
        assert(cetta_http_worker_progress(worker, id, &progress));
        CettaHttpRead view;
        assert(cetta_http_worker_read(worker, id, 0, 0, &view) == HTTP_READ_OK);
        assert(view.id == id && view.bytes_size == 0);
        cetta_http_read_free(&view);
        ++observations;
        if (cetta_http_worker_take_id(worker, id, &result)) break;
        generation = cetta_http_worker_wait(worker, generation, 5);
        assert(monotonic_ms() < deadline);
    }
    assert(observations > 0 && result.id == id);
    CettaHttpResult duplicate = {0};
    assert(!cetta_http_worker_take_id(worker, id, &duplicate));
    printf("Observed %llu %u code=%d bytes=%zu\n",
           (unsigned long long)id, observations, result.transport_code, result.body_size);
    return result;
}

int main(int argc, char **argv) {
    assert(argc == 3);
    /* The application uses this public native code as its timeout discriminator. */
    assert(CURLE_OPERATION_TIMEDOUT == 28);
    CettaHttpWorker *worker = NULL;
    assert(cetta_http_worker_new(NULL, NULL, &worker) == HTTP_WORKER_OK);
    const char *paths[] = {"/progress", "/truncated", "/silent", "/hard", "/ready", "/idle"};
    for (uint64_t i = 0; i < 7; ++i) {
        char url[512];
        if (i == 6) assert(snprintf(url, sizeof(url), "%s", argv[2]) < (int)sizeof(url));
        else assert(snprintf(url, sizeof(url), "%s%s", argv[1], paths[i]) < (int)sizeof(url));
        CettaHttpRequest request = {
            .id = i + 1, .method = "POST", .url = url, .body = "one", .body_size = 3,
            .timeout_ms = i == 3 ? 180 : 3000, .connect_timeout_ms = 500,
            .first_byte_timeout_ms = i == 2 ? 80 : 1500,
            .idle_timeout_ms = i == 5 ? 80 : 500, .max_response_bytes = 4096, .proxy = ""
        };
        assert(cetta_http_worker_submit(worker, &request) == HTTP_WORKER_OK);
        if (i == 4) {
            /* Completion can precede the consumer's first observation. */
            CettaHttpProgress progress;
            uint64_t deadline = monotonic_ms() + 5000;
            do {
                assert(cetta_http_worker_progress(worker, request.id, &progress));
                if (progress.terminal) break;
                struct timespec pause = {0, 1000000};
                nanosleep(&pause, NULL);
                assert(monotonic_ms() < deadline);
            } while (true);
            CettaHttpRead view;
            assert(cetta_http_worker_read(worker, request.id, 0, 0, &view) == HTTP_READ_OK);
            assert(view.terminal && view.complete);
            cetta_http_read_free(&view);
        }
        CettaHttpResult result;
        if (i == 4) {
            assert(cetta_http_worker_take_id(worker, request.id, &result));
            CettaHttpResult duplicate = {0};
            assert(!cetta_http_worker_take_id(worker, request.id, &duplicate));
        } else result = observe_until_complete(worker, request.id);
        assert(result.started && !result.cancelled);
        if (i == 0 || i == 4) {
            assert(result.transport_code == CURLE_OK && result.status == 200);
            assert(result.body_size > 0);
        } else if (i == 2 || i == 3 || i == 5) {
            assert(result.transport_code == CURLE_OPERATION_TIMEDOUT);
            if (i == 3 || i == 5) assert(result.body_size > 0);
        } else {
            assert(result.transport_code != CURLE_OK);
            assert(result.transport_code != CURLE_OPERATION_TIMEDOUT);
            if (i == 1) assert(result.body_size > 0); /* a retained prefix is not success */
            if (i == 6) assert(result.transport_code == CURLE_COULDNT_CONNECT);
        }
        cetta_http_result_free(&result);
    }
    assert(cetta_http_worker_free(worker) == 0);
    puts("HTTP_OBSERVATION_CONTRACT_OK");
    return 0;
}
