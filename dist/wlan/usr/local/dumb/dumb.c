#include <stdatomic.h>
#include <errno.h>
#include <signal.h>
#include <unistd.h>
#include <pcap/pcap.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static struct {
    pthread_t threads[2];
    pcap_t *interfaces[2];
    atomic_int ready; // bit0: if0 ready, bit1: if1 ready
} ifs;

static inline int both_ready(void) {
    int r = atomic_load_explicit(&ifs.ready, memory_order_acquire);
    return (r & 0x3) == 0x3;
}

static void ph(unsigned char *ifp, const struct pcap_pkthdr *hdr, const unsigned char *data)
{
    unsigned int i = (unsigned int)((uintptr_t)ifp);
    unsigned int peer = i ^ 1;

    if (!(hdr && data)) return;

    // 상대 인터페이스 준비 여부 확인
    if (!both_ready() || ifs.interfaces[peer] == NULL) {
        return; // 아직 준비 전이면 드롭
    }

    int ret = pcap_inject(ifs.interfaces[peer], data, hdr->len);
    if (ret < 0) {
        // 에러는 로그만 남기고 계속
        const char *err = pcap_geterr(ifs.interfaces[peer]);
        fprintf(stderr, "pcap_inject(%u->%u) failed: %s\n", i, peer, err ? err : "unknown");
    }
}

static void *thr(void *ifp)
{
    unsigned int i = (unsigned int)((uintptr_t)ifp);

    // 입력 전용으로 설정 (장치가 지원 안 하면 무시될 수 있음)
    pcap_setdirection(ifs.interfaces[i], PCAP_D_IN);

    // 본인 준비 플래그 set
    atomic_fetch_or_explicit(&ifs.ready, (1 << i), memory_order_release);

    // 두 쪽 다 준비될 때까지 잠깐 대기 (busy-wait 최소화)
    for (int t = 0; t < 200; ++t) { // 최대 ~200ms
        if (both_ready()) break;
        usleep(1000);
    }

    int rc = pcap_loop(ifs.interfaces[i], 0, ph, (unsigned char *)ifp);
    if (rc == PCAP_ERROR || rc == PCAP_ERROR_BREAK) {
        const char *err = pcap_geterr(ifs.interfaces[i]);
        fprintf(stderr, "pcap_loop(%u) ended: %s\n", i, err ? err : "unknown");
    }
    return NULL;
}

int main(int argc, char **argv)
{
    char errbuf[PCAP_ERRBUF_SIZE];

    if (argc != 3) {
        fprintf(stderr, "Usage: %s <interface0> <interface1>\n", argv[0]);
        return 1;
    }

    atomic_init(&ifs.ready, 0);
    memset(ifs.interfaces, 0, sizeof(ifs.interfaces));

    // 1) 두 인터페이스를 모두 연다
    for (int i = 0; i < 2; ++i) {
        ifs.interfaces[i] = pcap_open_live(argv[i + 1], 65535, 1, 1, errbuf);
        if (!ifs.interfaces[i]) {
            fprintf(stderr, "FATAL: open %s failed: %s\n", argv[i + 1], errbuf);
            return 1;
        }
        // 필요 시 즉시모드
        // pcap_set_immediate_mode(ifs.interfaces[i], 1);
        // pcap_set_datalink(ifs.interfaces[i], DLT_EN10MB); // 강제 필요 시
    }

    // 2) 스레드를 그 다음에 시작
    for (int i = 0; i < 2; ++i) {
        if (pthread_create(&ifs.threads[i], NULL, thr, (void *)((uintptr_t)i))) {
            fprintf(stderr, "FATAL: pthread_create(%d) failed\n", i);
            return 1;
        }
    }

    for (int i = 0; i < 2; ++i) {
        pthread_join(ifs.threads[i], NULL);
    }
    return 0;
}
