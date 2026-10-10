#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>
volatile unsigned long result;
__attribute__((noinline)) void hot_leaf(void) { for (unsigned long i=0;i<200000;i++) result += i * 17; }
__attribute__((noinline)) void hot_parent(void) { hot_leaf(); }
int main(int argc, char **argv) {
    double duration = argc > 1 ? atof(argv[1]) : 12;
    /* Optional readiness gate: start the hot phase only after capture is ready. */
    if (argc > 2) { while (access(argv[2], F_OK) != 0) usleep(1000); }
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC,&ts);
    double begin = ts.tv_sec + ts.tv_nsec / 1e9;
    for (;;) {
        clock_gettime(CLOCK_MONOTONIC,&ts);
        double elapsed=ts.tv_sec+ts.tv_nsec/1e9-begin;
        if (elapsed >= duration) break;
        if (elapsed > 2 && elapsed < 5) hot_parent(); else usleep(1000);
    }
    return 0;
}
