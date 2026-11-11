#include "native/unicode_class_v1.h"
#include <stdio.h>

/* Build-time inspection of the exact provider selected by CETTA_SOURCE. */
int main(void) {
    const char *names[] = {"L", "N", "C", "Z"};
    printf("{\"version\":\"%s\",\"classes\":{", unicode_v1_version());
    for (unsigned i = 0; i < 4; ++i) {
        UnicodeV1Ranges ranges;
        if (!unicode_v1_property("gc", names[i], &ranges)) return 2;
        printf("%s\"%s\":[", i ? "," : "", names[i]);
        for (size_t j = 0; j < ranges.len; ++j)
            printf("%s[%u,%u]", j ? "," : "", ranges.items[j].low, ranges.items[j].high);
        putchar(']');
    }
    puts("}}");
    return 0;
}
