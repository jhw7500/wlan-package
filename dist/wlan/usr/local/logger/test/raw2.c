#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <netinet/if_ether.h>
#include <netpacket/packet.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <sys/time.h>
#include <stdint.h>
#include <endian.h>
#include <net/if.h>
#include <sys/ioctl.h>

// IEEE 802.11 헤더 구조체
struct ieee80211_hdr {
    uint16_t frame_control;
    uint16_t duration_id;
    uint8_t addr1[6];
    uint8_t addr2[6];
    uint8_t addr3[6];
    uint16_t seq_ctrl;
};

// MAC 주소 출력 함수
void print_mac(const uint8_t *mac) {
    printf("%02X:%02X:%02X:%02X:%02X:%02X", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
}

// IEEE 802.11 프레임 타입 추출
#define IEEE80211_FC_TYPE(fc) (((fc) & 0x0C) >> 2)
#define IEEE80211_FC_SUBTYPE(fc) (((fc) & 0xF0) >> 4)

int main() {
    int sockfd;
    uint8_t buffer[4096];
    struct sockaddr saddr;
    int saddr_size = sizeof(saddr);

    // RAW 소켓 생성
    sockfd = socket(AF_PACKET, SOCK_RAW, htons(ETH_P_ALL));
    if (sockfd < 0) {
        perror("Socket Error");
        return 1;
    }

    // 특정 인터페이스 (mlan0)에 바인딩
    struct ifreq ifr;
    memset(&ifr, 0, sizeof(ifr));
    strncpy(ifr.ifr_name, "mlan0", IFNAMSIZ - 1);

    if (ioctl(sockfd, SIOCGIFINDEX, &ifr) < 0) {
        perror("ioctl error");
        return 1;
    }

    struct sockaddr_ll sll;
    memset(&sll, 0, sizeof(sll));
    sll.sll_family = AF_PACKET;
    sll.sll_ifindex = ifr.ifr_ifindex;
    sll.sll_protocol = htons(ETH_P_ALL);

    if (bind(sockfd, (struct sockaddr *)&sll, sizeof(sll)) < 0) {
        perror("bind error");
        return 1;
    }

    printf("Listening for packets on mlan0...\n");

    while (1) {
        int data_size = recvfrom(sockfd, buffer, sizeof(buffer), 0, &saddr, (socklen_t *)&saddr_size);
        if (data_size < 0) {
            perror("Recv Error");
            return 1;
        }

        struct timeval tv;
        gettimeofday(&tv, NULL);
        printf("\n📡 Packet captured at %ld.%06ld sec\n", tv.tv_sec, tv.tv_usec);

        struct ieee80211_hdr *hdr = (struct ieee80211_hdr *)buffer;

        // 바이트 순서 변환 (Big-endian → Little-endian)
        uint16_t fc = le16toh(hdr->frame_control);
        uint8_t frame_type = IEEE80211_FC_TYPE(fc);
        uint8_t frame_subtype = IEEE80211_FC_SUBTYPE(fc);
        uint16_t seq_num = le16toh(hdr->seq_ctrl) >> 4;

        // MAC 주소 출력
        printf("📍 Destination MAC: ");
        print_mac(hdr->addr1);
        printf("\n📍 Source MAC: ");
        print_mac(hdr->addr2);

        // 프레임 타입 및 시퀀스 번호 출력
        printf("\n📂 Frame Type: %d (Subtype: %d)", frame_type, frame_subtype);
        printf("\n📌 Sequence Number: %d\n", seq_num);
    }

    close(sockfd);
    return 0;
}
