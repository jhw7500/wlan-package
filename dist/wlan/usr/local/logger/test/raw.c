#include <stdio.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <netpacket/packet.h>
#include <net/ethernet.h>
#include <netinet/if_ether.h>
#include <netinet/ip.h>
#include <string.h>
#include <unistd.h>

#define BUF_SIZE 2048

int main() {
    int sockfd;
    char buffer[BUF_SIZE];
    struct sockaddr saddr;
    int saddr_size;

    // RAW 소켓 생성
    // 소켓을 생성한 후 특정 인터페이스에 바인딩
    //setsockopt(sockfd, SOL_SOCKET, SO_BINDTODEVICE, "mlan0", strlen("mlan0"));

    sockfd = socket(AF_PACKET, SOCK_RAW, htons(ETH_P_ALL));
    setsockopt(sockfd, SOL_SOCKET, SO_BINDTODEVICE, "mlan0", strlen("mlan0"));
    if (sockfd < 0) {
        perror("Socket Error");
        return 1;
    }

    while (1) {
        saddr_size = sizeof(saddr);
        int data_size = recvfrom(sockfd, buffer, BUF_SIZE, 0, &saddr, (socklen_t*)&saddr_size);
        if (data_size < 0) {
            perror("Recv Error");
            return 1;
        }

        // 패킷 데이터 출력
        printf("Received Packet Size: %d bytes\n", data_size);
    }

    close(sockfd);
    return 0;
}
