#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <linux/if_packet.h>
#include <linux/if_ether.h>
#include <linux/wireless.h>
#include <net/if.h>  // if_nametoindex() 함수 사용을 위해 추가

#define INTERFACE "mlan0"

// 패킷을 16진수로 출력하는 함수
void print_packet_hex(const unsigned char *buffer, int len) {
    printf("Packet Dump (%d bytes):\n", len);
    for (int i = 0; i < len; i++) {
        printf("%02X ", buffer[i]);
        if ((i + 1) % 16 == 0) printf("\n");  // 16바이트 단위로 줄바꿈
    }
    printf("\n");
}

int main() {
    int sockfd;
    struct sockaddr_ll sll;
    unsigned char buffer[2048];

    // Raw 소켓 생성
    sockfd = socket(AF_PACKET, SOCK_RAW, htons(ETH_P_ALL));
    if (sockfd < 0) {
        perror("Socket creation failed");
        return 1;
    }

    // 인터페이스 바인딩
    memset(&sll, 0, sizeof(sll));
    sll.sll_family = AF_PACKET;
    sll.sll_protocol = htons(ETH_P_ALL);
    sll.sll_ifindex = if_nametoindex(INTERFACE);
    
    if (bind(sockfd, (struct sockaddr*)&sll, sizeof(sll)) < 0) {
        perror("Bind failed");
        close(sockfd);
        return 1;
    }

    printf("Listening on interface: %s\n", INTERFACE);

    // 패킷 수신 루프
    while (1) {
        ssize_t len = recv(sockfd, buffer, sizeof(buffer), 0);
        if (len < 0) {
            perror("Packet receive error");
            break;
        }

        printf("\n--- Received Packet ---\n");
        print_packet_hex(buffer, len);

        // 최소한의 길이 체크 (802.11 헤더를 분석할 수 있도록)
        if (len < 2) continue;

        // 802.11 Frame Control 필드 (첫 2바이트) 분석
        unsigned short frame_control = buffer[0] | (buffer[1] << 8);
        unsigned short frame_type = (frame_control >> 2) & 0x03;  // Type 필드 추출
        unsigned short frame_subtype = (frame_control >> 4) & 0x0F;  // SubType 필드 추출

        if (frame_type == 0) {  // Management Frame (Type = 00)
            printf("Management Frame received: ");

            switch (frame_subtype) {
                case 0: printf("Association Request\n"); break;
                case 1: printf("Association Response\n"); break;
                case 2: printf("Reassociation Request\n"); break;
                case 3: printf("Reassociation Response\n"); break;
                case 4: printf("Probe Request\n"); break;
                case 5: printf("Probe Response\n"); break;
                case 8: printf("Beacon\n"); break;
                case 11: printf("Authentication\n"); break;
                case 12: printf("Deauthentication\n"); break;
                case 13: printf("Action Frame\n"); break;
                default: printf("Unknown Management Frame (Subtype: %d)\n", frame_subtype); break;
            }
        } else {
            printf("Non-Management Frame received (Type: %d, Subtype: %d)\n", frame_type, frame_subtype);
        }
    }

    close(sockfd);
    return 0;
}
