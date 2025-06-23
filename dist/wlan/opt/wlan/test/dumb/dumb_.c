
#ifndef _BSD_SOURCE
#define _BSD_SOURCE
#endif

#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <pcap.h>
#include <time.h>
#include <string.h>
#include <arpa/inet.h>
#include <netinet/if_ether.h>

#define MGMT_TYPE 0x00

const char* mgmt_subtype_str(uint8_t subtype) {
    switch (subtype) {
        case 0x00: return "Assoc Req";
        case 0x01: return "Assoc Resp";
        case 0x02: return "Reassoc Req";
        case 0x03: return "Reassoc Resp";
        case 0x04: return "Probe Req";
        case 0x05: return "Probe Resp";
        case 0x08: return "Beacon";
        case 0x09: return "ATIM";
        case 0x0a: return "Disassoc";
        case 0x0b: return "Auth";
        case 0x0c: return "Deauth";
        case 0x0d: return "Action";
        case 0x0e: return "Action No Ack";
        default:   return "Unknown";
    }
}

void mac_addr_to_str(const uint8_t *addr, char *buf) {
    sprintf(buf, "%02x:%02x:%02x:%02x:%02x:%02x",
            addr[0], addr[1], addr[2], addr[3], addr[4], addr[5]);
}

void print_hex_dump(const u_char *data, int len) {
    for (int i = 0; i < len; i++) {
        if (i % 16 == 0) printf("%04x  ", i);
        printf("%02x ", data[i]);
        if (i % 16 == 15 || i == len - 1) printf("\n");
    }
}

#if 0
void packet_handler(u_char *args, const struct pcap_pkthdr *header, const u_char *packet)
{
    //if (header->caplen < 8) return;

    // Radiotap  ^w  ^m^t     ^}    ^t  ^| (Little Endian)
    uint16_t radiotap_len = packet[2] | (packet[3] << 8);
    if (header->caplen < radiotap_len + 24) return;

    const u_char *frame = packet + radiotap_len;

    uint8_t fc = frame[0];
    uint8_t type = (fc >> 2) & 0x03;
    uint8_t subtype = (fc >> 4) & 0x0f;
    uint8_t flags = frame[1];
    int retry = (flags & 0x08) ? 1 : 0;

    if (type != 0x00) return; // Management frame  ^l

    printf("=== Packet ===\n");
    printf("Captured length: %u bytes\n", header->caplen);
    printf("Timestamp: %ld.%06ld\n", header->ts.tv_sec, header->ts.tv_usec);

    // Hex dump
    print_hex_dump(packet, header->caplen);

    printf("\n");
    fflush(stdout);
}
#endif
#if 1
void packet_handler(u_char *args, const struct pcap_pkthdr *header, const u_char *packet)
{
    if (header->caplen < 8) return;

    // Radiotap 헤더 길이 추출 (Little Endian)
    uint16_t radiotap_len = packet[2] | (packet[3] << 8);
    if (header->caplen < radiotap_len + 24) return;

    const u_char *frame = packet + radiotap_len;

    uint8_t fc = frame[0];
    uint8_t type = (fc >> 2) & 0x03;
    uint8_t subtype = (fc >> 4) & 0x0f;
    uint8_t flags = frame[1];
    int retry = (flags & 0x08) ? 1 : 0;

    if (type != 0x00) return; // Management frame만

    const uint8_t *sa = &frame[10];
    const uint8_t *da = &frame[4];

    char sa_str[18], da_str[18];
    mac_addr_to_str(sa, sa_str);
    mac_addr_to_str(da, da_str);

    // RSSI는 radiotap 필드에서 추출 (bit 5: antenna signal)
    // 정확한 위치는 radiotap 필드 bitmap에 따라 달라짐
    // 아래는 대략적으로 tshark를 기반으로 한 offset 예시 (보정 필요)
    int8_t rssi = (int8_t)packet[radiotap_len - 2];  // 실제 환경에 맞게 조정
    int8_t nf = -95; // 고정 또는 추후 파싱
    int snr = rssi - nf;

    uint16_t seq_ctrl = (frame[22] << 8) | frame[23];
    uint16_t seq = seq_ctrl >> 4;

    // 타임스탬프
    char timestamp[64];
    struct tm *lt = localtime(&header->ts.tv_sec);
    strftime(timestamp, sizeof(timestamp), "%Y-%m-%d %H:%M:%S", lt);

    printf("%s.%03ld %-15s (%2d) : SA=%s DA=%s RSSI=%4d NF=%4d SNR=%3d Retry=%d   Seq=%d\n",
        timestamp, header->ts.tv_usec / 1000,
        mgmt_subtype_str(subtype), subtype,
        sa_str, da_str, rssi, nf, snr, retry, seq);

    fflush(stdout);
}
#endif

int main(int argc, char *argv[]) {
    if (argc != 2) {
        fprintf(stderr, "Usage: %s <interface>\n", argv[0]);
        return 1;
    }

    char errbuf[PCAP_ERRBUF_SIZE];
    pcap_t *handle = pcap_open_live(argv[1], BUFSIZ, 1, 10, errbuf);
    if (!handle) {
        fprintf(stderr, "Failed to open interface %s: %s\n", argv[1], errbuf);
        return 1;
    }

    // monitor mode가 아니면 mgmt 프레임 안 들어올 수 있음
    pcap_loop(handle, 0, packet_handler, NULL);

    pcap_close(handle);
    return 0;
}
