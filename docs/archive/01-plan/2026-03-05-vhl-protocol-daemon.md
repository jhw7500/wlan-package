# VHL Protocol Daemon Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** VHL 무선기판 제어 프로토콜을 구현하는 단일 C 데몬 (`vhld`) - Single Station 우선 지원

**Architecture:** eth0(유선)에서 UDP 소켓으로 VHL 요청을 수신하고, 시스템 정보를 수집하여 응답을 반환한다. wpa_cli 파이프를 통해 무선 이벤트를 감시하고, 설정된 Indication을 VHL로 비동기 통지한다. 단일 이벤트 루프(select/poll)로 UDP 소켓 + wpa_cli 파이프 + 타이머를 처리한다.

**Tech Stack:** C11, POSIX sockets, poll(), wpa_cli (popen), aarch64-linux-gnu cross-compiler

**Protocol Reference:** `docs/VHL_protocol_260219_Cantops_KR.docx`

---

## Directory Structure

```
dist/wlan/usr/local/vhl_daemon/
  vhld.c              # 단일 소스 파일
  Makefile             # 네이티브 + 크로스 빌드
  vhld.service         # systemd 서비스
  vhld.conf            # 설정 파일 (포트, 장치 정보 등)
  README.md            # 사용법
  tests/
    test_vhld.sh       # 통합 테스트 (socat 기반)
```

## Constants & Conventions

```c
#define VHL_PROTO_VER       1
#define VHL_CMD_REQUEST     0x01
#define VHL_CMD_ACK         0x02
#define VHL_CMD_INDICATION  0x03
#define VHL_MAX_PAYLOAD     1392
#define VHL_HDR_SIZE        8        /* ver(1) + type(1) + id(2) + seq(2) + len(2) */
#define VHL_DEFAULT_PORT    50000    /* conf에서 오버라이드 가능 */
#define VHL_MAX_STR         64       /* 63자 + NULL */
#define VHL_SSID_MAX        32       /* 31자 + NULL */
```

---

### Task 1: 프로젝트 스켈레톤 및 Makefile

**Files:**
- Create: `dist/wlan/usr/local/vhl_daemon/Makefile`
- Create: `dist/wlan/usr/local/vhl_daemon/vhld.c` (스켈레톤)
- Create: `dist/wlan/usr/local/vhl_daemon/vhld.conf`

**Step 1: Makefile 작성**

```makefile
CC       ?= gcc
CROSS_CC  = aarch64-linux-gnu-gcc
CFLAGS    = -Wall -Wextra -Werror -std=c11 -D_POSIX_C_SOURCE=200809L -O2
DEBUG_FLAGS = -g -DDEBUG -O0

TARGET    = vhld
SRC       = vhld.c

.PHONY: all release debug cross clean

all: release

release: $(SRC)
	$(CC) $(CFLAGS) -o $(TARGET) $(SRC)

debug: $(SRC)
	$(CC) $(CFLAGS) $(DEBUG_FLAGS) -o $(TARGET) $(SRC)

cross: $(SRC)
	$(CROSS_CC) $(CFLAGS) -o $(TARGET) $(SRC)

cross-debug: $(SRC)
	$(CROSS_CC) $(CFLAGS) $(DEBUG_FLAGS) -o $(TARGET) $(SRC)

clean:
	rm -f $(TARGET)
```

**Step 2: 설정 파일 작성**

```
# vhld.conf - VHL Protocol Daemon Configuration
# 줄의 맨 앞이 '#'이면 주석

# UDP 수신 포트
PORT=50000

# 바인드 인터페이스 (유선)
BIND_IFACE=eth0

# 장치 정보 (장치 정보 취득 응답용)
VENDOR_NAME=Cantops
DEVICE_MODEL=CTS-WLAN-01
HW_VERSION=1.0
SERIAL_NUMBER=CTS000001

# 무선 인터페이스
WLAN_IFACE=mlan0

# 로밍 SNR 임계값 (dB)
ROAM_SNR_THRESHOLD=20
```

**Step 3: vhld.c 최소 스켈레톤 작성**

```c
/*
 * vhld.c - VHL Protocol Daemon
 * Single Station implementation for CTS wireless board
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <signal.h>
#include <errno.h>
#include <stdint.h>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <net/if.h>
#include <poll.h>
#include <time.h>
#include <syslog.h>

static volatile sig_atomic_t g_running = 1;

static void signal_handler(int sig)
{
    (void)sig;
    g_running = 0;
}

int main(int argc, char *argv[])
{
    (void)argc; (void)argv;

    signal(SIGINT, signal_handler);
    signal(SIGTERM, signal_handler);

    openlog("vhld", LOG_PID | LOG_NDELAY, LOG_LOCAL0);
    syslog(LOG_INFO, "vhld starting");

    /* TODO: 구현 예정 */

    syslog(LOG_INFO, "vhld stopped");
    closelog();
    return 0;
}
```

**Step 4: 호스트에서 빌드 확인**

Run: `cd dist/wlan/usr/local/vhl_daemon && make`
Expected: `vhld` 바이너리 생성, 에러/경고 없음

**Step 5: Commit**

```bash
git add dist/wlan/usr/local/vhl_daemon/
git commit -m "feat(vhld): add VHL protocol daemon skeleton with Makefile and config"
```

---

### Task 2: 프로토콜 구조체 및 직렬화 함수

**Files:**
- Modify: `dist/wlan/usr/local/vhl_daemon/vhld.c`

**Step 1: 프로토콜 상수 및 구조체 추가**

vhld.c 상단, include 아래에 추가:

```c
/* ========== VHL Protocol Definitions ========== */

#define VHL_PROTO_VER       1
#define VHL_CMD_REQUEST     0x01
#define VHL_CMD_ACK         0x02
#define VHL_CMD_INDICATION  0x03

#define VHL_MAX_PAYLOAD     1392
#define VHL_HDR_SIZE        8
#define VHL_BUF_SIZE        (VHL_HDR_SIZE + VHL_MAX_PAYLOAD)

/* Request IDs */
#define VHL_REQ_GET_DEV_INFO      0x0001
#define VHL_REQ_GET_DEV_STATUS    0x0002
#define VHL_REQ_GET_WLAN_CONFIG   0x0003
#define VHL_REQ_SET_PASSWORD      0x8001
#define VHL_REQ_SET_IP_ADDR       0x8002
#define VHL_REQ_SET_WLAN_CONFIG   0x8003
#define VHL_REQ_SET_INDICATION    0x8004
#define VHL_REQ_RESET             0x80FF

/* Indication IDs */
#define VHL_IND_INIT_COMPLETE     0x0001
#define VHL_IND_WLAN_STATE        0x0002
#define VHL_IND_ROAMING           0x0004
#define VHL_IND_AP_DISCONNECT     0x0008
#define VHL_IND_FAULT_DETECT      0x0010
#define VHL_IND_DEVICE_RESET      0x0020
#define VHL_IND_KEEP_ALIVE        0x0080

/* Device Type */
#define VHL_DEV_SINGLE_STATION    0x0001

/* Device Status */
#define VHL_STATUS_BOOTING        0x0001
#define VHL_STATUS_SCANNING       0x0002
#define VHL_STATUS_CONNECTED      0x1000

/* String limits */
#define VHL_STR_MAX     64   /* 63 chars + NULL */
#define VHL_SSID_MAX    32   /* 31 chars + NULL */
#define VHL_MAC_LEN     6
#define VHL_IP_LEN      4

/* Common Header (8 bytes, Big Endian) */
struct vhl_header {
    uint8_t  version;
    uint8_t  cmd_type;
    uint16_t req_id;
    uint16_t seq_num;
    uint16_t length;
};

/* 설정 파일에서 읽어오는 데몬 설정 */
struct vhld_config {
    uint16_t port;
    char     bind_iface[IF_NAMESIZE];
    char     vendor_name[VHL_STR_MAX];
    char     device_model[VHL_STR_MAX];
    char     hw_version[VHL_STR_MAX];
    char     serial_number[VHL_STR_MAX];
    char     wlan_iface[IF_NAMESIZE];
    uint8_t  roam_snr_threshold;
};

/* Indication 수신 설정 (VHL에서 SET으로 지정) */
struct vhl_indication_cfg {
    int      enabled;
    uint16_t udp_port;
    uint32_t ip_addr;       /* network byte order */
    uint8_t  info_mask;     /* 통지 비트마스크 */
    uint8_t  keepalive_sec; /* Keep Alive 주기 (초) */
};
```

**Step 2: 헤더 직렬화/역직렬화 함수 추가**

```c
/* ========== Serialization Helpers ========== */

static void vhl_pack_header(uint8_t *buf, const struct vhl_header *hdr)
{
    buf[0] = hdr->version;
    buf[1] = hdr->cmd_type;
    buf[2] = (uint8_t)(hdr->req_id >> 8);
    buf[3] = (uint8_t)(hdr->req_id & 0xFF);
    buf[4] = (uint8_t)(hdr->seq_num >> 8);
    buf[5] = (uint8_t)(hdr->seq_num & 0xFF);
    buf[6] = (uint8_t)(hdr->length >> 8);
    buf[7] = (uint8_t)(hdr->length & 0xFF);
}

static int vhl_unpack_header(const uint8_t *buf, size_t len, struct vhl_header *hdr)
{
    if (len < VHL_HDR_SIZE)
        return -1;
    hdr->version  = buf[0];
    hdr->cmd_type = buf[1];
    hdr->req_id   = (uint16_t)((buf[2] << 8) | buf[3]);
    hdr->seq_num  = (uint16_t)((buf[4] << 8) | buf[5]);
    hdr->length   = (uint16_t)((buf[6] << 8) | buf[7]);
    return 0;
}

/* Big-endian helpers */
static void put_be16(uint8_t *p, uint16_t v)
{
    p[0] = (uint8_t)(v >> 8);
    p[1] = (uint8_t)(v & 0xFF);
}

static void put_be32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)(v >> 24);
    p[1] = (uint8_t)(v >> 16);
    p[2] = (uint8_t)(v >> 8);
    p[3] = (uint8_t)(v & 0xFF);
}

static uint16_t get_be16(const uint8_t *p)
{
    return (uint16_t)((p[0] << 8) | p[1]);
}

static uint32_t get_be32(const uint8_t *p)
{
    return (uint32_t)((p[0] << 24) | (p[1] << 16) | (p[2] << 8) | p[3]);
}

/* 문자열을 고정 길이 필드에 안전하게 복사 (NULL 종료 보장) */
static void put_str(uint8_t *dst, const char *src, size_t field_len)
{
    size_t slen = strlen(src);
    if (slen >= field_len)
        slen = field_len - 1;
    memcpy(dst, src, slen);
    memset(dst + slen, 0, field_len - slen);
}
```

**Step 3: 빌드 확인**

Run: `cd dist/wlan/usr/local/vhl_daemon && make clean && make`
Expected: 빌드 성공, 경고 없음

**Step 4: Commit**

```bash
git add dist/wlan/usr/local/vhl_daemon/vhld.c
git commit -m "feat(vhld): add VHL protocol structs and serialization helpers"
```

---

### Task 3: 설정 파일 파싱 및 UDP 소켓 초기화

**Files:**
- Modify: `dist/wlan/usr/local/vhl_daemon/vhld.c`

**Step 1: 설정 파일 파서 추가**

```c
/* ========== Configuration ========== */

static int config_load(struct vhld_config *cfg, const char *path)
{
    FILE *fp;
    char line[256];

    /* defaults */
    cfg->port = 50000;
    snprintf(cfg->bind_iface, sizeof(cfg->bind_iface), "eth0");
    snprintf(cfg->vendor_name, sizeof(cfg->vendor_name), "Unknown");
    snprintf(cfg->device_model, sizeof(cfg->device_model), "Unknown");
    snprintf(cfg->hw_version, sizeof(cfg->hw_version), "0.0");
    snprintf(cfg->serial_number, sizeof(cfg->serial_number), "000000");
    snprintf(cfg->wlan_iface, sizeof(cfg->wlan_iface), "mlan0");
    cfg->roam_snr_threshold = 20;

    fp = fopen(path, "r");
    if (!fp) {
        syslog(LOG_WARNING, "config not found: %s, using defaults", path);
        return 0;
    }

    while (fgets(line, sizeof(line), fp)) {
        char *p = line;
        char *key, *val;

        /* strip newline */
        p[strcspn(p, "\r\n")] = '\0';

        /* skip comment / empty */
        while (*p == ' ' || *p == '\t') p++;
        if (*p == '#' || *p == '\0')
            continue;

        key = p;
        val = strchr(p, '=');
        if (!val) continue;
        *val++ = '\0';

        if (strcmp(key, "PORT") == 0)
            cfg->port = (uint16_t)atoi(val);
        else if (strcmp(key, "BIND_IFACE") == 0)
            snprintf(cfg->bind_iface, sizeof(cfg->bind_iface), "%s", val);
        else if (strcmp(key, "VENDOR_NAME") == 0)
            snprintf(cfg->vendor_name, sizeof(cfg->vendor_name), "%s", val);
        else if (strcmp(key, "DEVICE_MODEL") == 0)
            snprintf(cfg->device_model, sizeof(cfg->device_model), "%s", val);
        else if (strcmp(key, "HW_VERSION") == 0)
            snprintf(cfg->hw_version, sizeof(cfg->hw_version), "%s", val);
        else if (strcmp(key, "SERIAL_NUMBER") == 0)
            snprintf(cfg->serial_number, sizeof(cfg->serial_number), "%s", val);
        else if (strcmp(key, "WLAN_IFACE") == 0)
            snprintf(cfg->wlan_iface, sizeof(cfg->wlan_iface), "%s", val);
        else if (strcmp(key, "ROAM_SNR_THRESHOLD") == 0)
            cfg->roam_snr_threshold = (uint8_t)atoi(val);
    }

    fclose(fp);
    syslog(LOG_INFO, "config loaded: port=%u iface=%s wlan=%s",
           cfg->port, cfg->bind_iface, cfg->wlan_iface);
    return 0;
}
```

**Step 2: UDP 소켓 초기화 함수 추가**

```c
/* ========== UDP Socket ========== */

static int udp_socket_create(uint16_t port, const char *iface)
{
    int fd;
    struct sockaddr_in addr;
    int optval = 1;

    fd = socket(AF_INET, SOCK_DGRAM, 0);
    if (fd < 0) {
        syslog(LOG_ERR, "socket: %s", strerror(errno));
        return -1;
    }

    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &optval, sizeof(optval));

    if (iface[0] != '\0') {
        if (setsockopt(fd, SOL_SOCKET, SO_BINDTODEVICE,
                       iface, strlen(iface)) < 0) {
            syslog(LOG_WARNING, "SO_BINDTODEVICE(%s): %s",
                   iface, strerror(errno));
            /* 바인드 실패해도 계속 진행 (개발 환경 배려) */
        }
    }

    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    addr.sin_port = htons(port);

    if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        syslog(LOG_ERR, "bind port %u: %s", port, strerror(errno));
        close(fd);
        return -1;
    }

    syslog(LOG_INFO, "UDP listening on port %u (iface=%s)", port, iface);
    return fd;
}
```

**Step 3: main 함수에 설정 로드 + 소켓 생성 연결**

```c
/* main() 내부를 교체 */
int main(int argc, char *argv[])
{
    const char *conf_path = "/usr/local/vhl_daemon/vhld.conf";
    struct vhld_config cfg;
    int udp_fd;

    if (argc > 1)
        conf_path = argv[1];

    signal(SIGINT, signal_handler);
    signal(SIGTERM, signal_handler);

    openlog("vhld", LOG_PID | LOG_NDELAY, LOG_LOCAL0);
    syslog(LOG_INFO, "vhld starting (config=%s)", conf_path);

    config_load(&cfg, conf_path);

    udp_fd = udp_socket_create(cfg.port, cfg.bind_iface);
    if (udp_fd < 0) {
        syslog(LOG_ERR, "failed to create UDP socket, exiting");
        closelog();
        return 1;
    }

    syslog(LOG_INFO, "vhld ready");

    /* TODO: 메인 루프 (Task 5에서 구현) */
    while (g_running) {
        sleep(1);
    }

    close(udp_fd);
    syslog(LOG_INFO, "vhld stopped");
    closelog();
    return 0;
}
```

**Step 4: 빌드 및 실행 테스트**

Run: `cd dist/wlan/usr/local/vhl_daemon && make clean && make && ./vhld ./vhld.conf &`
Run: `ss -ulnp | grep 50000`
Expected: `vhld`가 50000 포트에서 리스닝

Run: `kill %1`

**Step 5: Commit**

```bash
git add dist/wlan/usr/local/vhl_daemon/vhld.c
git commit -m "feat(vhld): add config parser and UDP socket initialization"
```

---

### Task 4: 시스템 정보 수집 함수

**Files:**
- Modify: `dist/wlan/usr/local/vhl_daemon/vhld.c`

**Step 1: MAC 주소, IP 주소 수집 함수 추가**

```c
/* ========== System Info Helpers ========== */

static int sys_get_mac(const char *iface, uint8_t mac[6])
{
    char path[128];
    FILE *fp;
    unsigned int m[6];

    snprintf(path, sizeof(path), "/sys/class/net/%s/address", iface);
    fp = fopen(path, "r");
    if (!fp) return -1;

    if (fscanf(fp, "%x:%x:%x:%x:%x:%x",
               &m[0], &m[1], &m[2], &m[3], &m[4], &m[5]) != 6) {
        fclose(fp);
        return -1;
    }
    fclose(fp);

    for (int i = 0; i < 6; i++)
        mac[i] = (uint8_t)m[i];
    return 0;
}

static int sys_get_ip_info(const char *iface, uint32_t *ip, uint32_t *mask, uint32_t *gw)
{
    char cmd[128], line[256];
    FILE *fp;

    *ip = 0; *mask = 0; *gw = 0;

    /* IP address + prefix */
    snprintf(cmd, sizeof(cmd),
             "ip -4 addr show dev %s 2>/dev/null | grep 'inet ' | awk '{print $2}'",
             iface);
    fp = popen(cmd, "r");
    if (fp) {
        if (fgets(line, sizeof(line), fp)) {
            struct in_addr addr;
            int prefix = 24;
            char *slash = strchr(line, '/');
            if (slash) {
                *slash = '\0';
                prefix = atoi(slash + 1);
            }
            line[strcspn(line, "\r\n")] = '\0';
            if (inet_aton(line, &addr))
                *ip = ntohl(addr.s_addr);
            /* prefix to mask */
            if (prefix > 0 && prefix <= 32)
                *mask = (0xFFFFFFFFU << (32 - prefix));
        }
        pclose(fp);
    }

    /* default gateway */
    snprintf(cmd, sizeof(cmd),
             "ip route show dev %s default 2>/dev/null | awk '{print $3}'",
             iface);
    fp = popen(cmd, "r");
    if (fp) {
        if (fgets(line, sizeof(line), fp)) {
            struct in_addr addr;
            line[strcspn(line, "\r\n")] = '\0';
            if (inet_aton(line, &addr))
                *gw = ntohl(addr.s_addr);
        }
        pclose(fp);
    }

    return 0;
}

static int sys_get_operstate(const char *iface)
{
    char path[128], buf[32];
    FILE *fp;

    snprintf(path, sizeof(path), "/sys/class/net/%s/operstate", iface);
    fp = fopen(path, "r");
    if (!fp) return VHL_STATUS_BOOTING;

    if (!fgets(buf, sizeof(buf), fp)) {
        fclose(fp);
        return VHL_STATUS_BOOTING;
    }
    fclose(fp);

    buf[strcspn(buf, "\r\n")] = '\0';

    if (strcmp(buf, "up") == 0)
        return VHL_STATUS_CONNECTED;
    return VHL_STATUS_SCANNING;
}

/* wpa_cli를 사용하여 현재 접속 주파수를 MHz 단위로 반환 */
static uint16_t sys_get_wlan_freq(const char *iface)
{
    char cmd[128], line[256];
    FILE *fp;

    snprintf(cmd, sizeof(cmd), "wpa_cli -i %s status 2>/dev/null | grep '^freq='", iface);
    fp = popen(cmd, "r");
    if (!fp) return 0;

    if (fgets(line, sizeof(line), fp)) {
        char *val = strchr(line, '=');
        if (val) {
            pclose(fp);
            return (uint16_t)atoi(val + 1);
        }
    }
    pclose(fp);
    return 0;
}

/* wpa_cli를 사용하여 현재 SNR 값을 반환 */
static int8_t sys_get_wlan_snr(const char *iface)
{
    char cmd[128], line[256];
    FILE *fp;
    int rssi = -100, noise = -95;

    snprintf(cmd, sizeof(cmd),
             "wpa_cli -i %s signal_poll 2>/dev/null", iface);
    fp = popen(cmd, "r");
    if (!fp) return 0;

    while (fgets(line, sizeof(line), fp)) {
        if (strncmp(line, "RSSI=", 5) == 0)
            rssi = atoi(line + 5);
        else if (strncmp(line, "NOISE=", 6) == 0)
            noise = atoi(line + 6);
    }
    pclose(fp);

    return (int8_t)(rssi - noise);
}

/* wpa_cli를 사용하여 현재 SSID를 반환 */
static int sys_get_wlan_ssid(const char *iface, char *ssid, size_t ssid_size)
{
    char cmd[128], line[256];
    FILE *fp;

    ssid[0] = '\0';
    snprintf(cmd, sizeof(cmd), "wpa_cli -i %s status 2>/dev/null | grep '^ssid='", iface);
    fp = popen(cmd, "r");
    if (!fp) return -1;

    if (fgets(line, sizeof(line), fp)) {
        char *val = strchr(line, '=');
        if (val) {
            val++;
            val[strcspn(val, "\r\n")] = '\0';
            snprintf(ssid, ssid_size, "%s", val);
        }
    }
    pclose(fp);
    return 0;
}

/* 펌웨어 버전 문자열 가져오기 */
static int sys_get_fw_version(char *buf, size_t size)
{
    FILE *fp;
    char line[256];

    fp = popen("mlanutl mlan0 version 2>/dev/null | grep 'FW Version'", "r");
    if (!fp) {
        snprintf(buf, size, "unknown");
        return -1;
    }

    if (fgets(line, sizeof(line), fp)) {
        char *ver = strstr(line, ":");
        if (ver) {
            ver++;
            while (*ver == ' ') ver++;
            ver[strcspn(ver, "\r\n")] = '\0';
            snprintf(buf, size, "%s", ver);
            pclose(fp);
            return 0;
        }
    }
    pclose(fp);
    snprintf(buf, size, "unknown");
    return -1;
}
```

**Step 2: 빌드 확인**

Run: `cd dist/wlan/usr/local/vhl_daemon && make clean && make`
Expected: 빌드 성공

**Step 3: Commit**

```bash
git add dist/wlan/usr/local/vhl_daemon/vhld.c
git commit -m "feat(vhld): add system info collection helpers (MAC, IP, freq, SNR, SSID)"
```

---

### Task 5: 메인 이벤트 루프 및 Request 디스패처

**Files:**
- Modify: `dist/wlan/usr/local/vhl_daemon/vhld.c`

**Step 1: ACK 응답 전송 헬퍼 추가**

```c
/* ========== Response Sender ========== */

static int vhl_send_ack(int fd, struct sockaddr_in *dst, uint16_t req_id,
                        uint16_t seq_num, const uint8_t *payload, uint16_t payload_len)
{
    uint8_t buf[VHL_BUF_SIZE];
    struct vhl_header hdr = {
        .version  = VHL_PROTO_VER,
        .cmd_type = VHL_CMD_ACK,
        .req_id   = req_id,
        .seq_num  = seq_num,
        .length   = payload_len,
    };

    if (VHL_HDR_SIZE + payload_len > sizeof(buf))
        return -1;

    vhl_pack_header(buf, &hdr);
    if (payload_len > 0 && payload)
        memcpy(buf + VHL_HDR_SIZE, payload, payload_len);

    ssize_t sent = sendto(fd, buf, VHL_HDR_SIZE + payload_len, 0,
                          (struct sockaddr *)dst, sizeof(*dst));
    if (sent < 0) {
        syslog(LOG_ERR, "sendto: %s", strerror(errno));
        return -1;
    }
    return 0;
}
```

**Step 2: Request 디스패처 (스텁) 추가**

```c
/* ========== Command Handlers (forward declarations) ========== */

static int handle_get_dev_info(int fd, struct sockaddr_in *src,
                               uint16_t seq, const struct vhld_config *cfg);
static int handle_get_dev_status(int fd, struct sockaddr_in *src,
                                 uint16_t seq, const struct vhld_config *cfg);
static int handle_get_wlan_config(int fd, struct sockaddr_in *src,
                                  uint16_t seq, const struct vhld_config *cfg);
static int handle_set_password(int fd, struct sockaddr_in *src,
                               uint16_t seq, const uint8_t *payload, uint16_t len);
static int handle_set_ip_addr(int fd, struct sockaddr_in *src,
                              uint16_t seq, const uint8_t *payload, uint16_t len,
                              const struct vhld_config *cfg);
static int handle_set_wlan_config(int fd, struct sockaddr_in *src,
                                  uint16_t seq, const uint8_t *payload, uint16_t len,
                                  const struct vhld_config *cfg);
static int handle_set_indication(int fd, struct sockaddr_in *src,
                                 uint16_t seq, const uint8_t *payload, uint16_t len,
                                 struct vhl_indication_cfg *ind_cfg);
static int handle_reset(int fd, struct sockaddr_in *src, uint16_t seq);

/* ========== Request Dispatcher ========== */

static void dispatch_request(int fd, struct sockaddr_in *src,
                             const uint8_t *pkt, ssize_t pkt_len,
                             struct vhld_config *cfg,
                             struct vhl_indication_cfg *ind_cfg)
{
    struct vhl_header hdr;
    const uint8_t *payload;
    uint16_t payload_len;

    if (vhl_unpack_header(pkt, (size_t)pkt_len, &hdr) < 0) {
        syslog(LOG_WARNING, "invalid packet (too short: %zd)", pkt_len);
        return;
    }

    if (hdr.version != VHL_PROTO_VER) {
        syslog(LOG_WARNING, "unsupported protocol version: %u", hdr.version);
        return;
    }

    if (hdr.cmd_type != VHL_CMD_REQUEST) {
        syslog(LOG_WARNING, "unexpected cmd_type: 0x%02x", hdr.cmd_type);
        return;
    }

    payload = pkt + VHL_HDR_SIZE;
    payload_len = hdr.length;

    if (VHL_HDR_SIZE + payload_len > (uint16_t)pkt_len) {
        syslog(LOG_WARNING, "payload length mismatch");
        return;
    }

    syslog(LOG_DEBUG, "REQ id=0x%04x seq=%u len=%u from %s:%u",
           hdr.req_id, hdr.seq_num, payload_len,
           inet_ntoa(src->sin_addr), ntohs(src->sin_port));

    switch (hdr.req_id) {
    case VHL_REQ_GET_DEV_INFO:
        handle_get_dev_info(fd, src, hdr.seq_num, cfg);
        break;
    case VHL_REQ_GET_DEV_STATUS:
        handle_get_dev_status(fd, src, hdr.seq_num, cfg);
        break;
    case VHL_REQ_GET_WLAN_CONFIG:
        handle_get_wlan_config(fd, src, hdr.seq_num, cfg);
        break;
    case VHL_REQ_SET_PASSWORD:
        handle_set_password(fd, src, hdr.seq_num, payload, payload_len);
        break;
    case VHL_REQ_SET_IP_ADDR:
        handle_set_ip_addr(fd, src, hdr.seq_num, payload, payload_len, cfg);
        break;
    case VHL_REQ_SET_WLAN_CONFIG:
        handle_set_wlan_config(fd, src, hdr.seq_num, payload, payload_len, cfg);
        break;
    case VHL_REQ_SET_INDICATION:
        handle_set_indication(fd, src, hdr.seq_num, payload, payload_len, ind_cfg);
        break;
    case VHL_REQ_RESET:
        handle_reset(fd, src, hdr.seq_num);
        break;
    default:
        syslog(LOG_WARNING, "unknown request id: 0x%04x", hdr.req_id);
        break;
    }
}
```

**Step 3: 메인 루프를 poll() 기반으로 교체**

```c
/* main() 의 while 루프를 교체 */

    struct vhl_indication_cfg ind_cfg = {0};

    while (g_running) {
        struct pollfd pfd = { .fd = udp_fd, .events = POLLIN };
        int ret = poll(&pfd, 1, 1000); /* 1초 타임아웃 */

        if (ret < 0) {
            if (errno == EINTR) continue;
            syslog(LOG_ERR, "poll: %s", strerror(errno));
            break;
        }

        if (ret == 0) {
            /* TODO: Keep Alive, 리소스 모니터링 (Task 9, 10에서 구현) */
            continue;
        }

        if (pfd.revents & POLLIN) {
            uint8_t buf[VHL_BUF_SIZE];
            struct sockaddr_in src;
            socklen_t src_len = sizeof(src);

            ssize_t n = recvfrom(udp_fd, buf, sizeof(buf), 0,
                                 (struct sockaddr *)&src, &src_len);
            if (n < 0) {
                if (errno == EINTR) continue;
                syslog(LOG_ERR, "recvfrom: %s", strerror(errno));
                continue;
            }

            dispatch_request(udp_fd, &src, buf, n, &cfg, &ind_cfg);
        }
    }
```

**Step 4: 빌드 확인 (스텁만 있으므로 링크 에러 예상 - 다음 Task에서 해결)**

이 단계에서는 핸들러 스텁이 구현되지 않았으므로, 빈 스텁을 임시 추가:

```c
/* Temporary stubs - will be implemented in Tasks 6-8 */
static int handle_get_dev_info(int fd, struct sockaddr_in *src,
                               uint16_t seq, const struct vhld_config *cfg)
{ (void)fd; (void)src; (void)seq; (void)cfg; return 0; }
static int handle_get_dev_status(int fd, struct sockaddr_in *src,
                                 uint16_t seq, const struct vhld_config *cfg)
{ (void)fd; (void)src; (void)seq; (void)cfg; return 0; }
static int handle_get_wlan_config(int fd, struct sockaddr_in *src,
                                  uint16_t seq, const struct vhld_config *cfg)
{ (void)fd; (void)src; (void)seq; (void)cfg; return 0; }
static int handle_set_password(int fd, struct sockaddr_in *src,
                               uint16_t seq, const uint8_t *payload, uint16_t len)
{ (void)fd; (void)src; (void)seq; (void)payload; (void)len; return 0; }
static int handle_set_ip_addr(int fd, struct sockaddr_in *src,
                              uint16_t seq, const uint8_t *payload, uint16_t len,
                              const struct vhld_config *cfg)
{ (void)fd; (void)src; (void)seq; (void)payload; (void)len; (void)cfg; return 0; }
static int handle_set_wlan_config(int fd, struct sockaddr_in *src,
                                  uint16_t seq, const uint8_t *payload, uint16_t len,
                                  const struct vhld_config *cfg)
{ (void)fd; (void)src; (void)seq; (void)payload; (void)len; (void)cfg; return 0; }
static int handle_set_indication(int fd, struct sockaddr_in *src,
                                 uint16_t seq, const uint8_t *payload, uint16_t len,
                                 struct vhl_indication_cfg *ind_cfg)
{ (void)fd; (void)src; (void)seq; (void)payload; (void)len; (void)ind_cfg; return 0; }
static int handle_reset(int fd, struct sockaddr_in *src, uint16_t seq)
{ (void)fd; (void)src; (void)seq; return 0; }
```

Run: `cd dist/wlan/usr/local/vhl_daemon && make clean && make`
Expected: 빌드 성공

**Step 5: Commit**

```bash
git add dist/wlan/usr/local/vhl_daemon/vhld.c
git commit -m "feat(vhld): add main event loop with poll() and request dispatcher"
```

---

### Task 6: Get 계열 커맨드 핸들러 구현 (0x0001, 0x0002, 0x0003)

**Files:**
- Modify: `dist/wlan/usr/local/vhl_daemon/vhld.c` (스텁 핸들러를 실제 구현으로 교체)

**Step 1: handle_get_dev_info 구현**

```c
/* 장치 정보 취득 (0x0001) */
static int handle_get_dev_info(int fd, struct sockaddr_in *src,
                               uint16_t seq, const struct vhld_config *cfg)
{
    /*
     * Response payload layout (Single Station):
     *   vendor_name     : 64 bytes
     *   device_model    : 64 bytes
     *   fw_version      : 64 bytes
     *   hw_version      : 64 bytes
     *   serial_number   : 64 bytes
     *   device_type     :  2 bytes (0x0001)
     *   eth_mac         :  6 bytes
     *   wlan1_mac       :  6 bytes
     *   ip_addr         :  4 bytes
     *   subnet_mask     :  4 bytes
     *   default_gw      :  4 bytes
     *   Total           : 346 bytes
     */
    uint8_t payload[346];
    uint8_t *p = payload;
    char fw_ver[VHL_STR_MAX];
    uint8_t mac[6];
    uint32_t ip, mask, gw;

    memset(payload, 0, sizeof(payload));

    /* 문자열 필드들 */
    put_str(p, cfg->vendor_name, VHL_STR_MAX);      p += VHL_STR_MAX;
    put_str(p, cfg->device_model, VHL_STR_MAX);      p += VHL_STR_MAX;

    sys_get_fw_version(fw_ver, sizeof(fw_ver));
    put_str(p, fw_ver, VHL_STR_MAX);                 p += VHL_STR_MAX;

    put_str(p, cfg->hw_version, VHL_STR_MAX);        p += VHL_STR_MAX;
    put_str(p, cfg->serial_number, VHL_STR_MAX);     p += VHL_STR_MAX;

    /* device type: Single Station */
    put_be16(p, VHL_DEV_SINGLE_STATION);              p += 2;

    /* Ethernet MAC */
    if (sys_get_mac(cfg->bind_iface, mac) == 0)
        memcpy(p, mac, 6);
    p += 6;

    /* WLAN1 MAC */
    if (sys_get_mac(cfg->wlan_iface, mac) == 0)
        memcpy(p, mac, 6);
    p += 6;

    /* IP / Mask / GW (eth0 기준) */
    sys_get_ip_info(cfg->bind_iface, &ip, &mask, &gw);
    put_be32(p, ip);    p += 4;
    put_be32(p, mask);  p += 4;
    put_be32(p, gw);    p += 4;

    return vhl_send_ack(fd, src, VHL_REQ_GET_DEV_INFO, seq,
                        payload, (uint16_t)(p - payload));
}
```

**Step 2: handle_get_dev_status 구현**

```c
/* 장치 상태 취득 (0x0002) */
static int handle_get_dev_status(int fd, struct sockaddr_in *src,
                                 uint16_t seq, const struct vhld_config *cfg)
{
    /*
     * Response payload (Single Station):
     *   device_type       : 2 bytes
     *   status            : 2 bytes
     *   wlan1_freq        : 2 bytes (MHz)
     *   wlan1_snr         : 1 byte  (dB)
     *   interface_priority: 2 bytes (MHz)
     *   Total             : 9 bytes
     */
    uint8_t payload[9];
    uint8_t *p = payload;

    memset(payload, 0, sizeof(payload));

    put_be16(p, VHL_DEV_SINGLE_STATION);                       p += 2;
    put_be16(p, (uint16_t)sys_get_operstate(cfg->wlan_iface)); p += 2;
    put_be16(p, sys_get_wlan_freq(cfg->wlan_iface));           p += 2;
    *p = (uint8_t)sys_get_wlan_snr(cfg->wlan_iface);          p += 1;
    put_be16(p, sys_get_wlan_freq(cfg->wlan_iface));           p += 2;

    return vhl_send_ack(fd, src, VHL_REQ_GET_DEV_STATUS, seq,
                        payload, (uint16_t)(p - payload));
}
```

**Step 3: handle_get_wlan_config 구현**

```c
/* 무선 설정 취득 (0x0003) */
static int handle_get_wlan_config(int fd, struct sockaddr_in *src,
                                  uint16_t seq, const struct vhld_config *cfg)
{
    /*
     * Response payload (Single Station):
     *   device_type       : 2 bytes
     *   interface_priority: 2 bytes
     *   wlan1_freq        : 2 bytes
     *   wlan1_mode        : 1 byte
     *   wlan1_bandwidth   : 1 byte
     *   ssid              : 32 bytes
     *   Total             : 40 bytes
     */
    uint8_t payload[40];
    uint8_t *p = payload;
    char ssid[VHL_SSID_MAX];
    uint16_t freq;

    memset(payload, 0, sizeof(payload));

    freq = sys_get_wlan_freq(cfg->wlan_iface);

    put_be16(p, VHL_DEV_SINGLE_STATION); p += 2;
    put_be16(p, freq);                   p += 2;  /* interface priority */
    put_be16(p, freq);                   p += 2;  /* wlan1 freq */

    /* mode: 주파수 대역에 따라 추론 */
    if (freq >= 5925)
        *p = 0x08; /* 6GHz ax */
    else if (freq >= 5000)
        *p = 0x06; /* 5GHz ax */
    else
        *p = 0x03; /* 2.4GHz ax */
    p += 1;

    /* bandwidth: 기본 80MHz (실제 값은 iw info에서 조회 필요 - 추후 개선) */
    *p = 0x04; /* 80MHz */
    p += 1;

    sys_get_wlan_ssid(cfg->wlan_iface, ssid, sizeof(ssid));
    put_str(p, ssid, VHL_SSID_MAX);      p += VHL_SSID_MAX;

    return vhl_send_ack(fd, src, VHL_REQ_GET_WLAN_CONFIG, seq,
                        payload, (uint16_t)(p - payload));
}
```

**Step 4: 빌드 확인**

Run: `cd dist/wlan/usr/local/vhl_daemon && make clean && make`
Expected: 빌드 성공

**Step 5: Commit**

```bash
git add dist/wlan/usr/local/vhl_daemon/vhld.c
git commit -m "feat(vhld): implement Get commands (dev_info, dev_status, wlan_config)"
```

---

### Task 7: Set 계열 커맨드 핸들러 구현 (0x8001~0x8004, 0x80FF)

**Files:**
- Modify: `dist/wlan/usr/local/vhl_daemon/vhld.c` (스텁 핸들러를 실제 구현으로 교체)

**Step 1: 공통 Result 응답 헬퍼**

```c
static int vhl_send_result(int fd, struct sockaddr_in *dst, uint16_t req_id,
                           uint16_t seq, uint16_t result, uint16_t error_cause)
{
    uint8_t payload[4];
    put_be16(payload, result);
    put_be16(payload + 2, error_cause);
    return vhl_send_ack(fd, dst, req_id, seq, payload, sizeof(payload));
}
```

**Step 2: handle_set_password 구현**

```c
/* 패스워드 설정 (0x8001) */
static int handle_set_password(int fd, struct sockaddr_in *src,
                               uint16_t seq, const uint8_t *payload, uint16_t len)
{
    /*
     * Request payload:
     *   old_password: 64 bytes (NULL terminated)
     *   new_password: 64 bytes (NULL terminated)
     */
    const char *old_pw, *new_pw;

    if (len < VHL_STR_MAX * 2)
        return vhl_send_result(fd, src, VHL_REQ_SET_PASSWORD, seq, 0x0001, 0x0003);

    old_pw = (const char *)payload;
    new_pw = (const char *)(payload + VHL_STR_MAX);

    /* NULL 종료 검증 */
    if (memchr(old_pw, '\0', VHL_STR_MAX) == NULL)
        return vhl_send_result(fd, src, VHL_REQ_SET_PASSWORD, seq, 0x0001, 0x0003);
    if (memchr(new_pw, '\0', VHL_STR_MAX) == NULL)
        return vhl_send_result(fd, src, VHL_REQ_SET_PASSWORD, seq, 0x0001, 0x0005);

    /*
     * TODO: wpa_supplicant의 패스워드 변경 구현
     * 현재는 OK를 반환 (실제 변경 로직은 타겟 테스트 후 추가)
     */
    syslog(LOG_INFO, "password change requested (stub OK)");
    return vhl_send_result(fd, src, VHL_REQ_SET_PASSWORD, seq, 0x0000, 0x0000);
}
```

**Step 3: handle_set_ip_addr 구현**

```c
/* IP 주소 변경 (0x8002) - 리셋 없이 즉시 적용 */
static int handle_set_ip_addr(int fd, struct sockaddr_in *src,
                              uint16_t seq, const uint8_t *payload, uint16_t len,
                              const struct vhld_config *cfg)
{
    uint32_t new_ip, new_mask, new_gw;
    char cmd[256];
    int prefix;
    uint32_t tmp;

    if (len < 12)
        return vhl_send_result(fd, src, VHL_REQ_SET_IP_ADDR, seq, 0x0001, 0x0001);

    new_ip   = get_be32(payload);
    new_mask = get_be32(payload + 4);
    new_gw   = get_be32(payload + 8);

    /* Netmask 유효성 검증: 연속된 1비트 후 연속된 0비트여야 함 */
    tmp = new_mask;
    if (tmp != 0) {
        tmp = ~tmp + 1; /* 마지막 0 비트 찾기 */
        if ((tmp & (tmp - 1)) != 0 && new_mask != 0xFFFFFFFF)
            return vhl_send_result(fd, src, VHL_REQ_SET_IP_ADDR, seq, 0x0001, 0x0001);
    }

    /* Gateway가 같은 서브넷인지 확인 */
    if (new_gw != 0 && (new_ip & new_mask) != (new_gw & new_mask))
        return vhl_send_result(fd, src, VHL_REQ_SET_IP_ADDR, seq, 0x0001, 0x0002);

    /* mask -> prefix 변환 */
    prefix = 0;
    for (tmp = new_mask; tmp; tmp <<= 1) prefix++;

    /* ip 명령으로 즉시 변경 */
    snprintf(cmd, sizeof(cmd), "ip addr flush dev %s && "
             "ip addr add %u.%u.%u.%u/%d dev %s",
             cfg->bind_iface,
             (new_ip >> 24) & 0xFF, (new_ip >> 16) & 0xFF,
             (new_ip >> 8) & 0xFF, new_ip & 0xFF,
             prefix, cfg->bind_iface);

    if (system(cmd) != 0) {
        syslog(LOG_ERR, "ip addr change failed");
        return vhl_send_result(fd, src, VHL_REQ_SET_IP_ADDR, seq, 0x0001, 0x0001);
    }

    if (new_gw != 0) {
        snprintf(cmd, sizeof(cmd), "ip route add default via %u.%u.%u.%u dev %s",
                 (new_gw >> 24) & 0xFF, (new_gw >> 16) & 0xFF,
                 (new_gw >> 8) & 0xFF, new_gw & 0xFF,
                 cfg->bind_iface);
        system(cmd); /* GW 설정 실패는 경고만 */
    }

    syslog(LOG_INFO, "IP changed: %u.%u.%u.%u/%d gw %u.%u.%u.%u",
           (new_ip >> 24) & 0xFF, (new_ip >> 16) & 0xFF,
           (new_ip >> 8) & 0xFF, new_ip & 0xFF, prefix,
           (new_gw >> 24) & 0xFF, (new_gw >> 16) & 0xFF,
           (new_gw >> 8) & 0xFF, new_gw & 0xFF);

    return vhl_send_result(fd, src, VHL_REQ_SET_IP_ADDR, seq, 0x0000, 0x0000);
}
```

**Step 4: handle_set_wlan_config 구현**

```c
/* 무선 설정 변경 (0x8003) - 리셋 없이 적용 */
static int handle_set_wlan_config(int fd, struct sockaddr_in *src,
                                  uint16_t seq, const uint8_t *payload, uint16_t len,
                                  const struct vhld_config *cfg)
{
    /*
     * Request payload (Single Station):
     *   device_type       : 2 bytes
     *   interface_priority: 2 bytes
     *   wlan1_freq        : 2 bytes
     *   wlan1_mode        : 1 byte
     *   wlan1_bandwidth   : 1 byte
     *   ssid              : 32 bytes
     *   Total             : 40 bytes
     */
    uint16_t dev_type, freq;
    char ssid[VHL_SSID_MAX];
    char cmd[256];

    if (len < 40)
        return vhl_send_result(fd, src, VHL_REQ_SET_WLAN_CONFIG, seq, 0x0001, 0x0001);

    dev_type = get_be16(payload);
    freq     = get_be16(payload + 4); /* wlan1_freq */

    /* Single Station이 아닌 경우 에러 */
    if (dev_type != VHL_DEV_SINGLE_STATION)
        return vhl_send_result(fd, src, VHL_REQ_SET_WLAN_CONFIG, seq, 0x0001, 0x0002);

    /* 주파수 유효성 검증 (기본적인 범위 체크) */
    if (freq != 0 && (freq < 2400 || (freq > 2500 && freq < 5000) ||
                       (freq > 5900 && freq < 5925) || freq > 7125))
        return vhl_send_result(fd, src, VHL_REQ_SET_WLAN_CONFIG, seq, 0x0001, 0x0001);

    /* SSID 추출 및 검증 */
    memcpy(ssid, payload + 8, VHL_SSID_MAX);
    if (memchr(ssid, '\0', VHL_SSID_MAX) == NULL)
        return vhl_send_result(fd, src, VHL_REQ_SET_WLAN_CONFIG, seq, 0x0001, 0x0004);

    /* ASCII 검증 */
    for (int i = 0; ssid[i] != '\0'; i++) {
        if ((unsigned char)ssid[i] < 0x20 || (unsigned char)ssid[i] > 0x7E)
            return vhl_send_result(fd, src, VHL_REQ_SET_WLAN_CONFIG, seq, 0x0001, 0x0003);
    }

    /* wpa_cli로 SSID/주파수 변경 */
    if (ssid[0] != '\0') {
        snprintf(cmd, sizeof(cmd),
                 "wpa_cli -i %s set_network 0 ssid '\"%s\"'", cfg->wlan_iface, ssid);
        if (system(cmd) != 0)
            syslog(LOG_WARNING, "wpa_cli set ssid failed");
    }

    if (freq != 0) {
        snprintf(cmd, sizeof(cmd),
                 "wpa_cli -i %s set_network 0 freq_list %u", cfg->wlan_iface, freq);
        if (system(cmd) != 0)
            syslog(LOG_WARNING, "wpa_cli set freq failed");
    }

    /* 네트워크 재연결 */
    snprintf(cmd, sizeof(cmd), "wpa_cli -i %s reassociate", cfg->wlan_iface);
    system(cmd);

    syslog(LOG_INFO, "WLAN config changed: ssid=%s freq=%u", ssid, freq);
    return vhl_send_result(fd, src, VHL_REQ_SET_WLAN_CONFIG, seq, 0x0000, 0x0000);
}
```

**Step 5: handle_set_indication 구현**

```c
/* Indication 수신 설정 (0x8004) */
static int handle_set_indication(int fd, struct sockaddr_in *src,
                                 uint16_t seq, const uint8_t *payload, uint16_t len,
                                 struct vhl_indication_cfg *ind_cfg)
{
    /*
     * Request payload:
     *   indication_udp_port : 2 bytes
     *   indication_info     : 1 byte  (비트마스크)
     *   keepalive_period    : 1 byte  (초)
     *   indication_ip_addr  : 4 bytes
     *   Total               : 8 bytes
     */
    uint8_t info_mask;
    uint8_t valid_mask = 0x01 | 0x02 | 0x04 | 0x08 | 0x10 | 0x20 | 0x80;

    if (len < 8)
        return vhl_send_result(fd, src, VHL_REQ_SET_INDICATION, seq, 0x0001, 0x0001);

    info_mask = payload[2];

    /* 미할당 비트 확인 */
    if (info_mask & ~valid_mask)
        return vhl_send_result(fd, src, VHL_REQ_SET_INDICATION, seq, 0x0001, 0x0001);

    /* 설정 저장 */
    ind_cfg->udp_port     = get_be16(payload);
    ind_cfg->info_mask    = info_mask;
    ind_cfg->keepalive_sec = payload[3];
    ind_cfg->ip_addr      = get_be32(payload + 4);
    ind_cfg->enabled      = (info_mask != 0);

    syslog(LOG_INFO, "Indication configured: port=%u mask=0x%02x ka=%us ip=0x%08x",
           ind_cfg->udp_port, ind_cfg->info_mask,
           ind_cfg->keepalive_sec, ntohl(ind_cfg->ip_addr));

    return vhl_send_result(fd, src, VHL_REQ_SET_INDICATION, seq, 0x0000, 0x0000);
}
```

**Step 6: handle_reset 구현**

```c
/* 리셋 요구 (0x80FF) */
static int handle_reset(int fd, struct sockaddr_in *src, uint16_t seq)
{
    /* 헤더만 응답 후 reboot 실행 */
    vhl_send_ack(fd, src, VHL_REQ_RESET, seq, NULL, 0);

    syslog(LOG_WARNING, "reset requested by VHL, rebooting in 1 second");
    sync();
    sleep(1);
    system("reboot");

    return 0;
}
```

**Step 7: 빌드 확인**

Run: `cd dist/wlan/usr/local/vhl_daemon && make clean && make`
Expected: 빌드 성공

**Step 8: Commit**

```bash
git add dist/wlan/usr/local/vhl_daemon/vhld.c
git commit -m "feat(vhld): implement Set commands (password, IP, WLAN, indication, reset)"
```

---

### Task 8: Indication 통지 발송 시스템

**Files:**
- Modify: `dist/wlan/usr/local/vhl_daemon/vhld.c`

**Step 1: Indication 전송 함수 추가**

```c
/* ========== Indication Sender ========== */

static int vhl_send_indication(const struct vhl_indication_cfg *ind_cfg,
                               uint16_t ind_id, const uint8_t *payload,
                               uint16_t payload_len)
{
    static uint16_t ind_seq = 0;
    uint8_t buf[VHL_BUF_SIZE];
    struct vhl_header hdr;
    struct sockaddr_in dst;
    int fd;
    ssize_t sent;

    if (!ind_cfg->enabled)
        return 0;

    /* 해당 Indication이 설정되어 있는지 확인 */
    if (!(ind_cfg->info_mask & ind_id) && ind_id != VHL_IND_KEEP_ALIVE)
        return 0;

    hdr.version  = VHL_PROTO_VER;
    hdr.cmd_type = VHL_CMD_INDICATION;
    hdr.req_id   = ind_id;
    hdr.seq_num  = ind_seq++;
    hdr.length   = payload_len;

    vhl_pack_header(buf, &hdr);
    if (payload_len > 0 && payload)
        memcpy(buf + VHL_HDR_SIZE, payload, payload_len);

    fd = socket(AF_INET, SOCK_DGRAM, 0);
    if (fd < 0) return -1;

    memset(&dst, 0, sizeof(dst));
    dst.sin_family = AF_INET;
    dst.sin_addr.s_addr = ind_cfg->ip_addr;
    dst.sin_port = htons(ind_cfg->udp_port);

    sent = sendto(fd, buf, VHL_HDR_SIZE + payload_len, 0,
                  (struct sockaddr *)&dst, sizeof(dst));
    close(fd);

    if (sent < 0) {
        syslog(LOG_ERR, "indication send failed: %s", strerror(errno));
        return -1;
    }

    syslog(LOG_DEBUG, "IND id=0x%04x seq=%u sent to %s:%u",
           ind_id, hdr.seq_num,
           inet_ntoa(dst.sin_addr), ind_cfg->udp_port);
    return 0;
}
```

**Step 2: 개별 Indication 발송 함수들 추가**

```c
/* 장치 초기 설정 완료 통지 (0x0001) */
static void ind_init_complete(const struct vhl_indication_cfg *ind_cfg, uint32_t status)
{
    uint8_t payload[4];
    put_be32(payload, status);
    vhl_send_indication(ind_cfg, VHL_IND_INIT_COMPLETE, payload, 4);
}

/* 무선 접속 상태 변화 통지 (0x0002) */
static void ind_wlan_state(const struct vhl_indication_cfg *ind_cfg,
                           uint16_t status, uint16_t freq_mhz)
{
    uint8_t payload[4];
    put_be16(payload, status);
    put_be16(payload + 2, freq_mhz);
    vhl_send_indication(ind_cfg, VHL_IND_WLAN_STATE, payload, 4);
}

/* 로밍 통지 (0x0004) */
static void ind_roaming(const struct vhl_indication_cfg *ind_cfg,
                        uint8_t snr, uint16_t freq_mhz)
{
    uint8_t payload[3];
    payload[0] = snr;
    put_be16(payload + 1, freq_mhz);
    vhl_send_indication(ind_cfg, VHL_IND_ROAMING, payload, 3);
}

/* AP 연결 끊김 수신 통지 (0x0008) */
static void ind_ap_disconnect(const struct vhl_indication_cfg *ind_cfg,
                              uint16_t msg_id, uint16_t reason,
                              const uint8_t ap_mac[6])
{
    uint8_t payload[10];
    put_be16(payload, msg_id);
    put_be16(payload + 2, reason);
    memcpy(payload + 4, ap_mac, 6);
    vhl_send_indication(ind_cfg, VHL_IND_AP_DISCONNECT, payload, 10);
}

/* 장치 장애 검출 통지 (0x0010) */
static void ind_fault_detect(const struct vhl_indication_cfg *ind_cfg,
                             uint16_t congestion_id, uint32_t current_val)
{
    uint8_t payload[6];
    put_be16(payload, congestion_id);
    put_be32(payload + 2, current_val);
    vhl_send_indication(ind_cfg, VHL_IND_FAULT_DETECT, payload, 6);
}

/* 장치 리셋 통지 (0x0020) */
static void ind_device_reset(const struct vhl_indication_cfg *ind_cfg,
                             uint16_t reset_cause)
{
    uint8_t payload[2];
    put_be16(payload, reset_cause);
    vhl_send_indication(ind_cfg, VHL_IND_DEVICE_RESET, payload, 2);
}

/* Keep Alive 통지 (0x0080) */
static void ind_keep_alive(const struct vhl_indication_cfg *ind_cfg)
{
    uint8_t payload[VHL_SSID_MAX];
    time_t now = time(NULL);
    struct tm *tm = localtime(&now);
    char ts[32];

    strftime(ts, sizeof(ts), "%Y-%m-%d %H:%M:%S", tm);
    memset(payload, 0, sizeof(payload));
    put_str(payload, ts, VHL_SSID_MAX);

    vhl_send_indication(ind_cfg, VHL_IND_KEEP_ALIVE, payload, VHL_SSID_MAX);
}
```

**Step 3: 빌드 확인**

Run: `cd dist/wlan/usr/local/vhl_daemon && make clean && make`
Expected: 빌드 성공

**Step 4: Commit**

```bash
git add dist/wlan/usr/local/vhl_daemon/vhld.c
git commit -m "feat(vhld): add Indication message sender system (7 types)"
```

---

### Task 9: Keep Alive 타이머 및 리소스 모니터링

**Files:**
- Modify: `dist/wlan/usr/local/vhl_daemon/vhld.c`

**Step 1: 리소스 모니터링 함수 추가**

```c
/* ========== Resource Monitoring ========== */

static int sys_get_cpu_usage(void)
{
    static unsigned long prev_total = 0, prev_idle = 0;
    FILE *fp;
    char line[256];
    unsigned long user, nice, sys_t, idle, iowait, irq, softirq;
    unsigned long total, diff_total, diff_idle;
    int usage;

    fp = fopen("/proc/stat", "r");
    if (!fp) return 0;

    if (!fgets(line, sizeof(line), fp)) { fclose(fp); return 0; }
    fclose(fp);

    if (sscanf(line, "cpu %lu %lu %lu %lu %lu %lu %lu",
               &user, &nice, &sys_t, &idle, &iowait, &irq, &softirq) != 7)
        return 0;

    total = user + nice + sys_t + idle + iowait + irq + softirq;
    diff_total = total - prev_total;
    diff_idle  = idle - prev_idle;
    prev_total = total;
    prev_idle  = idle;

    if (diff_total == 0) return 0;
    usage = (int)((diff_total - diff_idle) * 100 / diff_total);
    return usage;
}

static int sys_get_mem_usage(void)
{
    FILE *fp;
    char line[256];
    unsigned long total = 0, available = 0;

    fp = fopen("/proc/meminfo", "r");
    if (!fp) return 0;

    while (fgets(line, sizeof(line), fp)) {
        if (sscanf(line, "MemTotal: %lu kB", &total) == 1) continue;
        if (sscanf(line, "MemAvailable: %lu kB", &available) == 1) break;
    }
    fclose(fp);

    if (total == 0) return 0;
    return (int)((total - available) * 100 / total);
}

#define RESOURCE_THRESHOLD_CPU  90
#define RESOURCE_THRESHOLD_MEM  90

static void check_resources(const struct vhl_indication_cfg *ind_cfg)
{
    int cpu, mem;

    if (!ind_cfg->enabled || !(ind_cfg->info_mask & VHL_IND_FAULT_DETECT))
        return;

    cpu = sys_get_cpu_usage();
    if (cpu > RESOURCE_THRESHOLD_CPU) {
        syslog(LOG_WARNING, "CPU congestion: %d%%", cpu);
        ind_fault_detect(ind_cfg, 0x0001, (uint32_t)cpu);
    }

    mem = sys_get_mem_usage();
    if (mem > RESOURCE_THRESHOLD_MEM) {
        syslog(LOG_WARNING, "Memory congestion: %d%%", mem);
        ind_fault_detect(ind_cfg, 0x0002, (uint32_t)mem);
    }
}
```

**Step 2: 메인 루프에 Keep Alive 및 리소스 체크 통합**

main() 의 while 루프를 교체:

```c
    struct vhl_indication_cfg ind_cfg = {0};
    time_t last_keepalive = 0;
    time_t last_resource_check = 0;

    /* 초기 설정 완료 통지 */
    ind_init_complete(&ind_cfg, 0x00000001);

    while (g_running) {
        struct pollfd pfd = { .fd = udp_fd, .events = POLLIN };
        int ret = poll(&pfd, 1, 1000); /* 1초 타임아웃 */
        time_t now = time(NULL);

        if (ret < 0) {
            if (errno == EINTR) continue;
            syslog(LOG_ERR, "poll: %s", strerror(errno));
            break;
        }

        /* UDP 수신 처리 */
        if (ret > 0 && (pfd.revents & POLLIN)) {
            uint8_t buf[VHL_BUF_SIZE];
            struct sockaddr_in src;
            socklen_t src_len = sizeof(src);

            ssize_t n = recvfrom(udp_fd, buf, sizeof(buf), 0,
                                 (struct sockaddr *)&src, &src_len);
            if (n > 0)
                dispatch_request(udp_fd, &src, buf, n, &cfg, &ind_cfg);
        }

        /* Keep Alive */
        if (ind_cfg.enabled && ind_cfg.keepalive_sec > 0 &&
            (now - last_keepalive) >= ind_cfg.keepalive_sec) {
            ind_keep_alive(&ind_cfg);
            last_keepalive = now;
        }

        /* 리소스 모니터링 (10초마다) */
        if ((now - last_resource_check) >= 10) {
            check_resources(&ind_cfg);
            last_resource_check = now;
        }
    }
```

**Step 3: 빌드 확인**

Run: `cd dist/wlan/usr/local/vhl_daemon && make clean && make`
Expected: 빌드 성공

**Step 4: Commit**

```bash
git add dist/wlan/usr/local/vhl_daemon/vhld.c
git commit -m "feat(vhld): add Keep Alive timer and resource monitoring"
```

---

### Task 10: wpa_cli 이벤트 모니터링 (연결/끊김/로밍)

**Files:**
- Modify: `dist/wlan/usr/local/vhl_daemon/vhld.c`

**Step 1: wpa_cli 모니터 파이프 구조체 및 시작/파싱 함수**

```c
/* ========== WPA Event Monitor ========== */

struct wpa_monitor {
    FILE *fp;
    int   fd;   /* fileno for poll() */
};

static int wpa_monitor_start(struct wpa_monitor *mon, const char *iface)
{
    char cmd[128];

    snprintf(cmd, sizeof(cmd), "stdbuf -oL wpa_cli -i %s 2>/dev/null", iface);
    mon->fp = popen(cmd, "r");
    if (!mon->fp) {
        syslog(LOG_ERR, "wpa_cli popen failed: %s", strerror(errno));
        return -1;
    }
    mon->fd = fileno(mon->fp);

    /* wpa_cli 인터랙티브 모드에서 이벤트 수신 시작 */
    /* 초기 프롬프트 무시 - non-blocking 읽기로 처리 */

    syslog(LOG_INFO, "wpa_cli monitor started for %s", iface);
    return 0;
}

static void wpa_monitor_stop(struct wpa_monitor *mon)
{
    if (mon->fp) {
        pclose(mon->fp);
        mon->fp = NULL;
        mon->fd = -1;
    }
}

/*
 * wpa_cli 이벤트 라인을 파싱하여 Indication 발송
 *
 * 예상 이벤트 형식:
 *   <3>CTRL-EVENT-CONNECTED - Connection to aa:bb:cc:dd:ee:ff completed ...
 *   <3>CTRL-EVENT-DISCONNECTED bssid=aa:bb:cc:dd:ee:ff reason=3 ...
 *   <3>CTRL-EVENT-SIGNAL-CHANGE above=0 signal=-75 noise=-95 ...
 */
static void wpa_parse_event(const char *line,
                            const struct vhl_indication_cfg *ind_cfg,
                            const struct vhld_config *cfg)
{
    const char *event;
    uint16_t freq;

    /* <N> 접두사 건너뛰기 */
    event = strchr(line, '>');
    if (event) event++;
    else event = line;

    /* skip whitespace */
    while (*event == ' ') event++;

    if (strstr(event, "CTRL-EVENT-CONNECTED")) {
        freq = sys_get_wlan_freq(cfg->wlan_iface);
        syslog(LOG_INFO, "WLAN connected (freq=%u)", freq);
        ind_wlan_state(ind_cfg, 0x0001, freq);
    }
    else if (strstr(event, "CTRL-EVENT-DISCONNECTED")) {
        freq = sys_get_wlan_freq(cfg->wlan_iface);
        syslog(LOG_INFO, "WLAN disconnected");
        ind_wlan_state(ind_cfg, 0x0002, freq);

        /* reason code 파싱 */
        const char *reason_str = strstr(event, "reason=");
        if (reason_str) {
            int reason = atoi(reason_str + 7);
            /* Deauth/Disassoc 판단 (reason 기반 추론) */
            const char *bssid_str = strstr(event, "bssid=");
            uint8_t ap_mac[6] = {0};
            if (bssid_str) {
                unsigned int m[6];
                if (sscanf(bssid_str + 6, "%x:%x:%x:%x:%x:%x",
                           &m[0], &m[1], &m[2], &m[3], &m[4], &m[5]) == 6) {
                    for (int i = 0; i < 6; i++)
                        ap_mac[i] = (uint8_t)m[i];
                }
            }
            /* Deauthentication=0x000c를 기본으로 사용 */
            ind_ap_disconnect(ind_cfg, 0x000c, (uint16_t)reason, ap_mac);
        }
    }
    else if (strstr(event, "CTRL-EVENT-SIGNAL-CHANGE")) {
        /* SNR 하락 감지 */
        const char *sig = strstr(event, "signal=");
        const char *noi = strstr(event, "noise=");
        if (sig && noi) {
            int signal_dbm = atoi(sig + 7);
            int noise_dbm  = atoi(noi + 6);
            int snr = signal_dbm - noise_dbm;

            if (snr < cfg->roam_snr_threshold) {
                freq = sys_get_wlan_freq(cfg->wlan_iface);
                syslog(LOG_INFO, "Roaming trigger: SNR=%d < threshold=%d",
                       snr, cfg->roam_snr_threshold);
                ind_roaming(ind_cfg, (uint8_t)snr, freq);
            }
        }
    }
}
```

**Step 2: 메인 루프에 wpa_cli 파이프 통합**

main() 수정: wpa_monitor 초기화 + poll에 fd 추가

```c
    /* UDP 소켓 이후, 메인 루프 이전에 추가 */
    struct wpa_monitor wpa_mon = { .fp = NULL, .fd = -1 };
    if (wpa_monitor_start(&wpa_mon, cfg.wlan_iface) < 0)
        syslog(LOG_WARNING, "wpa_cli monitor unavailable, events disabled");

    /* ... */

    /* 메인 루프의 poll 부분을 2-fd로 확장 */
    while (g_running) {
        struct pollfd pfds[2];
        int nfds = 1;
        time_t now;
        int ret;

        pfds[0].fd = udp_fd;
        pfds[0].events = POLLIN;

        if (wpa_mon.fd >= 0) {
            pfds[1].fd = wpa_mon.fd;
            pfds[1].events = POLLIN;
            nfds = 2;
        }

        ret = poll(pfds, nfds, 1000);
        now = time(NULL);

        if (ret < 0) {
            if (errno == EINTR) continue;
            syslog(LOG_ERR, "poll: %s", strerror(errno));
            break;
        }

        /* UDP 수신 */
        if (pfds[0].revents & POLLIN) {
            uint8_t buf[VHL_BUF_SIZE];
            struct sockaddr_in src;
            socklen_t src_len = sizeof(src);

            ssize_t n = recvfrom(udp_fd, buf, sizeof(buf), 0,
                                 (struct sockaddr *)&src, &src_len);
            if (n > 0)
                dispatch_request(udp_fd, &src, buf, n, &cfg, &ind_cfg);
        }

        /* wpa_cli 이벤트 */
        if (nfds > 1 && (pfds[1].revents & POLLIN)) {
            char line[512];
            if (fgets(line, sizeof(line), wpa_mon.fp))
                wpa_parse_event(line, &ind_cfg, &cfg);
        }

        /* Keep Alive */
        if (ind_cfg.enabled && ind_cfg.keepalive_sec > 0 &&
            (now - last_keepalive) >= ind_cfg.keepalive_sec) {
            ind_keep_alive(&ind_cfg);
            last_keepalive = now;
        }

        /* 리소스 모니터링 */
        if ((now - last_resource_check) >= 10) {
            check_resources(&ind_cfg);
            last_resource_check = now;
        }
    }

    /* cleanup */
    wpa_monitor_stop(&wpa_mon);
    close(udp_fd);
```

**Step 3: 빌드 확인**

Run: `cd dist/wlan/usr/local/vhl_daemon && make clean && make`
Expected: 빌드 성공

**Step 4: Commit**

```bash
git add dist/wlan/usr/local/vhl_daemon/vhld.c
git commit -m "feat(vhld): add wpa_cli event monitor for WLAN state/roaming indications"
```

---

### Task 11: systemd 서비스 및 빌드 통합

**Files:**
- Create: `dist/wlan/usr/local/vhl_daemon/vhld.service`
- Modify: `build.sh` (vhld 빌드 추가)

**Step 1: systemd 서비스 파일 작성**

```ini
[Unit]
Description=VHL Protocol Daemon
After=network.target wifi_init.service
Wants=wifi_init.service

[Service]
Type=simple
ExecStart=/usr/local/vhl_daemon/vhld /usr/local/vhl_daemon/vhld.conf
Restart=on-failure
RestartSec=5
StandardOutput=null
StandardError=journal
SyslogIdentifier=vhld

[Install]
WantedBy=multi-user.target
```

**Step 2: build.sh에 vhld 빌드 단계 추가**

build.sh 맨 끝 (tar 생성 직전)에 추가:

```bash
# Build vhld (VHL Protocol Daemon)
echo "Building vhld..."
VHLD_DIR="${BASEDIR}/dist/wlan/usr/local/vhl_daemon"
if [ -f "${VHLD_DIR}/Makefile" ]; then
    cd "${VHLD_DIR}"
    make clean
    if [ "${HOST_ARCH}" = "aarch64" ] || [ "${HOST_ARCH}" = "arm64" ]; then
        make release || { echo "Error: Failed to build vhld"; exit 1; }
    else
        make cross || { echo "Error: Failed to cross-build vhld"; exit 1; }
    fi
    cd "${BASEDIR}"
    echo "vhld build completed"
else
    echo "Warning: vhld Makefile not found, skipping"
fi
```

**Step 3: postinst에 vhld 서비스 등록 추가**

dist/wlan/DEBIAN/postinst에 추가 (기존 서비스 등록 섹션 근처):

```bash
# VHL daemon
if [ -f /usr/local/vhl_daemon/vhld.service ]; then
    cp /usr/local/vhl_daemon/vhld.service /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable vhld.service
    systemctl start vhld.service || true
fi
```

**Step 4: 빌드 확인**

Run: `cd /home/jhw/ai/opencode/projects/wlan-package/dist/wlan/usr/local/vhl_daemon && make clean && make`
Expected: 빌드 성공

**Step 5: Commit**

```bash
git add dist/wlan/usr/local/vhl_daemon/vhld.service build.sh
git commit -m "feat(vhld): add systemd service and integrate with build.sh"
```

---

### Task 12: 통합 테스트 스크립트 (socat 기반)

**Files:**
- Create: `dist/wlan/usr/local/vhl_daemon/tests/test_vhld.sh`

**Step 1: socat 기반 테스트 스크립트 작성**

```bash
#!/bin/bash
# test_vhld.sh - VHL Protocol Daemon 통합 테스트
# 요구사항: socat, xxd, vhld가 실행 중이어야 함
#
# 사용법:
#   ./test_vhld.sh [port]     (기본: 50000)
#   ./test_vhld.sh localhost 50000

set -euo pipefail

HOST="${1:-127.0.0.1}"
PORT="${2:-50000}"
PASS=0
FAIL=0

log_ok()   { echo "  [PASS] $1"; PASS=$((PASS + 1)); }
log_fail() { echo "  [FAIL] $1"; FAIL=$((FAIL + 1)); }

# VHL 패킷 생성: ver=1, type, req_id, seq, payload(hex)
make_packet() {
    local type="$1"   # 01=req, 03=ind
    local req_id="$2" # 4 hex chars
    local seq="$3"    # 4 hex chars
    local payload_hex="${4:-}"
    local payload_len

    if [ -z "$payload_hex" ]; then
        payload_len="0000"
    else
        local byte_count=$(( ${#payload_hex} / 2 ))
        payload_len=$(printf "%04x" "$byte_count")
    fi

    echo "01${type}${req_id}${seq}${payload_len}${payload_hex}"
}

# UDP 전송 및 응답 수신 (1초 타임아웃)
send_recv() {
    local hex="$1"
    echo -n "$hex" | xxd -r -p | \
        socat -T1 - UDP:${HOST}:${PORT} 2>/dev/null | xxd -p | tr -d '\n'
}

echo "=== VHL Protocol Daemon Test ==="
echo "Target: ${HOST}:${PORT}"
echo ""

# Test 1: Get Device Info (0x0001)
echo "[Test 1] Get Device Info (0x0001)"
pkt=$(make_packet "01" "0001" "0001" "")
resp=$(send_recv "$pkt")
if [ -n "$resp" ] && [ "${resp:0:2}" = "01" ] && [ "${resp:2:2}" = "02" ]; then
    log_ok "응답 수신됨 (ACK, len=${#resp} hex chars)"
else
    log_fail "응답 없음 또는 잘못된 형식"
fi

# Test 2: Get Device Status (0x0002)
echo "[Test 2] Get Device Status (0x0002)"
pkt=$(make_packet "01" "0002" "0002" "")
resp=$(send_recv "$pkt")
if [ -n "$resp" ] && [ "${resp:2:2}" = "02" ]; then
    log_ok "상태 응답 수신"
else
    log_fail "응답 없음"
fi

# Test 3: Get WLAN Config (0x0003)
echo "[Test 3] Get WLAN Config (0x0003)"
pkt=$(make_packet "01" "0003" "0003" "")
resp=$(send_recv "$pkt")
if [ -n "$resp" ] && [ "${resp:2:2}" = "02" ]; then
    log_ok "무선 설정 응답 수신"
else
    log_fail "응답 없음"
fi

# Test 4: Set Indication Config (0x8004)
echo "[Test 4] Set Indication Config (0x8004)"
# port=60000(EA60), mask=0x85(init+roam+keepalive), period=5, ip=127.0.0.1(7F000001)
payload="EA600085057F000001"
pkt=$(make_packet "01" "8004" "0004" "$payload")
resp=$(send_recv "$pkt")
if [ -n "$resp" ]; then
    result="${resp:16:4}"
    if [ "$result" = "0000" ]; then
        log_ok "Indication 설정 성공 (Result=OK)"
    else
        log_fail "Indication 설정 실패 (Result=$result)"
    fi
else
    log_fail "응답 없음"
fi

# Test 5: Invalid version
echo "[Test 5] Invalid Protocol Version"
resp=$(echo -n "FF010001000100000000" | xxd -r -p | \
    socat -T1 - UDP:${HOST}:${PORT} 2>/dev/null | xxd -p | tr -d '\n')
if [ -z "$resp" ]; then
    log_ok "잘못된 버전에 대해 무응답 (정상)"
else
    log_fail "잘못된 버전에도 응답함"
fi

# Test 6: Sequence number echo
echo "[Test 6] Sequence Number Echo"
pkt=$(make_packet "01" "0001" "ABCD" "")
resp=$(send_recv "$pkt")
if [ -n "$resp" ]; then
    echo_seq="${resp:8:4}"
    if [ "$echo_seq" = "abcd" ]; then
        log_ok "시퀀스 번호 정확히 반환 (0xABCD)"
    else
        log_fail "시퀀스 번호 불일치: expected abcd, got $echo_seq"
    fi
else
    log_fail "응답 없음"
fi

echo ""
echo "=== Results: ${PASS} passed, ${FAIL} failed ==="
exit $FAIL
```

**Step 2: 테스트 스크립트 실행 권한**

Run: `chmod +x dist/wlan/usr/local/vhl_daemon/tests/test_vhld.sh`

**Step 3: 로컬 테스트 실행**

Run: `cd dist/wlan/usr/local/vhl_daemon && make clean && make && ./vhld ./vhld.conf &`
Run: `sleep 1 && ./tests/test_vhld.sh 127.0.0.1 50000`
Run: `kill %1`
Expected: 최소 Test 1, 2, 3, 5, 6 통과

**Step 4: Commit**

```bash
git add dist/wlan/usr/local/vhl_daemon/tests/
git commit -m "test(vhld): add socat-based integration test script"
```

---

### Task 13: .gitignore 및 README

**Files:**
- Create: `dist/wlan/usr/local/vhl_daemon/.gitignore`
- Create: `dist/wlan/usr/local/vhl_daemon/README.md`

**Step 1: .gitignore**

```
vhld
*.o
```

**Step 2: README.md**

```markdown
# vhld - VHL Protocol Daemon

VHL 무선기판 제어 프로토콜 데몬. eth0(유선)으로 VHL과 UDP 통신을 수행하여
장치 정보/상태 조회, 무선 설정 변경, 이벤트 통지 등을 처리한다.

## Build

```bash
# 호스트(x86) 빌드
make

# 크로스 빌드 (aarch64)
make cross

# 디버그 빌드
make debug
```

## Usage

```bash
vhld [config_file]
# 기본: /usr/local/vhl_daemon/vhld.conf
```

## Protocol

- Transport: UDP/IP (Big Endian)
- 기본 포트: 50000 (vhld.conf에서 변경 가능)
- 프로토콜 사양: docs/VHL_protocol_260219_Cantops_KR.docx

## Test

```bash
# socat 필요
./tests/test_vhld.sh [host] [port]
```
```

**Step 3: Commit**

```bash
git add dist/wlan/usr/local/vhl_daemon/.gitignore dist/wlan/usr/local/vhl_daemon/README.md
git commit -m "docs(vhld): add README and gitignore"
```

---

## Task 의존성 요약

```
Task 1  (스켈레톤)
  └─ Task 2  (프로토콜 구조체)
       └─ Task 3  (설정 파서 + UDP 소켓)
            ├─ Task 4  (시스템 정보 수집)
            │    └─ Task 6  (Get 핸들러)
            │         └─ Task 7  (Set 핸들러)
            │              └─ Task 8  (Indication 발송)
            │                   ├─ Task 9  (Keep Alive + 리소스)
            │                   └─ Task 10 (wpa_cli 이벤트)
            └─ Task 5  (메인 루프 + 디스패처)
Task 11 (systemd + 빌드 통합) — Task 10 이후
Task 12 (통합 테스트) — Task 10 이후
Task 13 (문서) — 언제든
```

## 향후 확장 (이번 범위 밖)

- **Dual Station 지원**: WLAN2 필드 추가, device_type 0x0002 처리
- **bandwidth 정확한 조회**: `iw dev mlan0 info` 파싱
- **패스워드 실제 변경**: wpa_supplicant.conf 수정 로직
- **SNMP 확장 MIB**: 프로토콜 문서의 SNMP 파트 구현
- **IP 변경 시 소켓 재바인드**: IP 변경 후 UDP 소켓 자동 재생성
