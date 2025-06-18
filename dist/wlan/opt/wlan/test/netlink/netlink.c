#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <linux/netlink.h>
#include <linux/genetlink.h>
#include <sys/socket.h>

#define NL80211_GENL_NAME "nl80211"

int main() {
    int sock = socket(AF_NETLINK, SOCK_RAW, NETLINK_GENERIC);
    if (sock < 0) {
        perror("socket");
        return 1;
    }

    struct sockaddr_nl addr = {0};
    addr.nl_family = AF_NETLINK;
    addr.nl_groups = 1;

    if (bind(sock, (struct sockaddr*)&addr, sizeof(addr)) < 0) {
        perror("bind");
        close(sock);
        return 1;
    }

    while (1) {
        char buffer[8192];
        int len = recv(sock, buffer, sizeof(buffer), 0);
        if (len > 0) {
            printf("Received Netlink Message (%d bytes)\n", len);
        }
    }

    close(sock);
    return 0;
}
