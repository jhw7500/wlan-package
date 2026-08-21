/*
 * vhld.c - VHL Protocol Daemon
 * Single Station implementation for CTS wireless board
 */

/* Section 1: Includes */
#define _GNU_SOURCE
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
#include <ctype.h>
#include <sys/wait.h>
#include <fcntl.h>

/* ------------------------------------------------------------------ */
/* Section 2: Protocol Definitions                                     */
/* ------------------------------------------------------------------ */

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
#define VHL_STR_MAX     64
#define VHL_SSID_MAX    32
#define VHL_MAC_LEN     6
#define VHL_IP_LEN      4

/* ------------------------------------------------------------------ */
/* Section 3: Structures                                               */
/* ------------------------------------------------------------------ */

struct vhl_header {
    uint8_t  version;
    uint8_t  cmd_type;
    uint16_t req_id;
    uint16_t seq_num;
    uint16_t length;
};

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

struct vhl_indication_cfg {
    int      enabled;
    uint16_t udp_port;
    uint32_t ip_addr;       /* network byte order */
    uint8_t  info_mask;
    uint8_t  keepalive_sec;
};

/* ------------------------------------------------------------------ */
/* Section 4: Signal handler                                           */
/* ------------------------------------------------------------------ */

static volatile sig_atomic_t g_running = 1;

static void signal_handler(int sig)
{
    (void)sig;
    g_running = 0;
}

/* ------------------------------------------------------------------ */
/* Section 5: Serialization helpers                                    */
/* ------------------------------------------------------------------ */

static void put_be16(uint8_t *buf, uint16_t val)
{
    buf[0] = (uint8_t)(val >> 8);
    buf[1] = (uint8_t)(val & 0xFF);
}

static void put_be32(uint8_t *buf, uint32_t val)
{
    buf[0] = (uint8_t)(val >> 24);
    buf[1] = (uint8_t)(val >> 16);
    buf[2] = (uint8_t)(val >> 8);
    buf[3] = (uint8_t)(val & 0xFF);
}

static uint16_t get_be16(const uint8_t *buf)
{
    return (uint16_t)((uint16_t)buf[0] << 8 | (uint16_t)buf[1]);
}

static void put_str(uint8_t *dst, const char *src, size_t field_size)
{
    size_t len = strlen(src);
    if (len >= field_size)
        len = field_size - 1;
    memcpy(dst, src, len);
    memset(dst + len, 0, field_size - len);
}

static void vhl_pack_header(uint8_t *buf, const struct vhl_header *hdr)
{
    buf[0] = hdr->version;
    buf[1] = hdr->cmd_type;
    put_be16(buf + 2, hdr->req_id);
    put_be16(buf + 4, hdr->seq_num);
    put_be16(buf + 6, hdr->length);
}

static void vhl_unpack_header(const uint8_t *buf, struct vhl_header *hdr)
{
    hdr->version  = buf[0];
    hdr->cmd_type = buf[1];
    hdr->req_id   = get_be16(buf + 2);
    hdr->seq_num  = get_be16(buf + 4);
    hdr->length   = get_be16(buf + 6);
}

/* ------------------------------------------------------------------ */
/* Section 6: Config loader                                            */
/* ------------------------------------------------------------------ */

static void config_set_defaults(struct vhld_config *cfg)
{
    memset(cfg, 0, sizeof(*cfg));
    cfg->port = 50000;
    snprintf(cfg->bind_iface, sizeof(cfg->bind_iface), "eth0");
    snprintf(cfg->vendor_name, sizeof(cfg->vendor_name), "Unknown");
    snprintf(cfg->device_model, sizeof(cfg->device_model), "Unknown");
    snprintf(cfg->hw_version, sizeof(cfg->hw_version), "1.0");
    snprintf(cfg->serial_number, sizeof(cfg->serial_number), "000000");
    snprintf(cfg->wlan_iface, sizeof(cfg->wlan_iface), "mlan0");
    cfg->roam_snr_threshold = 20;
}

static char *strip_whitespace(char *s)
{
    while (isspace((unsigned char)*s))
        s++;
    char *end = s + strlen(s);
    while (end > s && isspace((unsigned char)*(end - 1)))
        end--;
    *end = '\0';
    return s;
}

static void config_load(struct vhld_config *cfg, const char *path)
{
    FILE *fp;
    char line[256];

    config_set_defaults(cfg);

    fp = fopen(path, "r");
    if (!fp) {
        syslog(LOG_WARNING, "config file '%s' not found, using defaults", path);
        return;
    }

    while (fgets(line, sizeof(line), fp)) {
        char *p = strip_whitespace(line);
        if (*p == '\0' || *p == '#')
            continue;

        char *eq = strchr(p, '=');
        if (!eq)
            continue;

        *eq = '\0';
        char *key = strip_whitespace(p);
        char *val = strip_whitespace(eq + 1);

        if (strcasecmp(key, "port") == 0)
            cfg->port = (uint16_t)atoi(val);
        else if (strcasecmp(key, "bind_iface") == 0)
            snprintf(cfg->bind_iface, sizeof(cfg->bind_iface), "%s", val);
        else if (strcasecmp(key, "vendor_name") == 0)
            snprintf(cfg->vendor_name, sizeof(cfg->vendor_name), "%s", val);
        else if (strcasecmp(key, "device_model") == 0)
            snprintf(cfg->device_model, sizeof(cfg->device_model), "%s", val);
        else if (strcasecmp(key, "hw_version") == 0)
            snprintf(cfg->hw_version, sizeof(cfg->hw_version), "%s", val);
        else if (strcasecmp(key, "serial_number") == 0)
            snprintf(cfg->serial_number, sizeof(cfg->serial_number), "%s", val);
        else if (strcasecmp(key, "wlan_iface") == 0)
            snprintf(cfg->wlan_iface, sizeof(cfg->wlan_iface), "%s", val);
        else if (strcasecmp(key, "roam_snr_threshold") == 0)
            cfg->roam_snr_threshold = (uint8_t)atoi(val);
        else
            syslog(LOG_WARNING, "unknown config key: '%s'", key);
    }

    fclose(fp);
    syslog(LOG_INFO, "config loaded from '%s' (port=%u, iface=%s, wlan=%s)",
           path, cfg->port, cfg->bind_iface, cfg->wlan_iface);
}

/* ------------------------------------------------------------------ */
/* Section 7: UDP socket                                               */
/* ------------------------------------------------------------------ */

static int udp_socket_create(uint16_t port, const char *iface)
{
    int fd;
    int opt = 1;
    struct sockaddr_in addr;

    fd = socket(AF_INET, SOCK_DGRAM, 0);
    if (fd < 0) {
        syslog(LOG_ERR, "socket: %s", strerror(errno));
        return -1;
    }

    if (setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt)) < 0)
        syslog(LOG_WARNING, "SO_REUSEADDR: %s", strerror(errno));

    if (setsockopt(fd, SOL_SOCKET, SO_BINDTODEVICE, iface,
                   (socklen_t)strlen(iface)) < 0)
        syslog(LOG_WARNING, "SO_BINDTODEVICE(%s): %s (non-fatal in dev env)",
               iface, strerror(errno));

    memset(&addr, 0, sizeof(addr));
    addr.sin_family      = AF_INET;
    addr.sin_port        = htons(port);
    addr.sin_addr.s_addr = INADDR_ANY;

    if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        syslog(LOG_ERR, "bind port %u: %s", port, strerror(errno));
        close(fd);
        return -1;
    }

    syslog(LOG_INFO, "UDP socket bound to port %u on %s", port, iface);
    return fd;
}

/* ------------------------------------------------------------------ */
/* Section 8: System info helpers                                      */
/* ------------------------------------------------------------------ */

static int sys_get_mac(const char *iface, uint8_t mac[6])
{
    char path[128];
    FILE *fp;
    unsigned int m[6];

    snprintf(path, sizeof(path), "/sys/class/net/%s/address", iface);
    fp = fopen(path, "r");
    if (!fp) {
        syslog(LOG_WARNING, "cannot read MAC for %s: %s", iface, strerror(errno));
        memset(mac, 0, 6);
        return -1;
    }

    if (fscanf(fp, "%x:%x:%x:%x:%x:%x",
               &m[0], &m[1], &m[2], &m[3], &m[4], &m[5]) != 6) {
        syslog(LOG_WARNING, "failed to parse MAC for %s", iface);
        fclose(fp);
        memset(mac, 0, 6);
        return -1;
    }
    fclose(fp);

    for (int i = 0; i < 6; i++)
        mac[i] = (uint8_t)m[i];
    return 0;
}

static int sys_get_ip_info(const char *iface, uint32_t *ip,
                           uint32_t *mask, uint32_t *gw)
{
    char cmd[128];
    char line[256];
    FILE *fp;

    *ip = 0;
    *mask = 0;
    *gw = 0;

    /* Get IP and mask */
    snprintf(cmd, sizeof(cmd),
             "ip -4 addr show dev %s 2>/dev/null | grep 'inet '", iface);
    fp = popen(cmd, "r");
    if (fp) {
        if (fgets(line, sizeof(line), fp)) {
            char addr_str[32];
            int prefix = 0;
            char *p = strstr(line, "inet ");
            if (p) {
                if (sscanf(p, "inet %31[^/]/%d", addr_str, &prefix) >= 1) {
                    struct in_addr a;
                    if (inet_pton(AF_INET, addr_str, &a) == 1)
                        *ip = a.s_addr;
                    if (prefix > 0 && prefix <= 32) {
                        uint32_t m = (prefix == 32) ? 0xFFFFFFFF
                                     : htonl(~((1U << (32 - prefix)) - 1));
                        *mask = m;
                    }
                }
            }
        }
        pclose(fp);
    }

    /* Get default gateway */
    snprintf(cmd, sizeof(cmd),
             "ip -4 route show dev %s default 2>/dev/null", iface);
    fp = popen(cmd, "r");
    if (fp) {
        if (fgets(line, sizeof(line), fp)) {
            char gw_str[32];
            char *p = strstr(line, "via ");
            if (p) {
                if (sscanf(p, "via %31s", gw_str) == 1) {
                    struct in_addr a;
                    if (inet_pton(AF_INET, gw_str, &a) == 1)
                        *gw = a.s_addr;
                }
            }
        }
        pclose(fp);
    }

    return 0;
}

static uint16_t sys_get_operstate(const char *iface)
{
    char path[128];
    char state[32];
    FILE *fp;

    snprintf(path, sizeof(path), "/sys/class/net/%s/operstate", iface);
    fp = fopen(path, "r");
    if (!fp)
        return VHL_STATUS_BOOTING;

    if (!fgets(state, sizeof(state), fp)) {
        fclose(fp);
        return VHL_STATUS_BOOTING;
    }
    fclose(fp);

    /* Strip newline */
    state[strcspn(state, "\n")] = '\0';

    if (strcmp(state, "up") == 0)
        return VHL_STATUS_CONNECTED;
    if (strcmp(state, "dormant") == 0)
        return VHL_STATUS_SCANNING;

    return VHL_STATUS_BOOTING;
}

static int sys_get_wlan_freq(const char *iface)
{
    char cmd[128];
    char line[256];
    FILE *fp;
    int freq = 0;

    snprintf(cmd, sizeof(cmd),
             "wpa_cli -i %s status 2>/dev/null | grep '^freq='", iface);
    fp = popen(cmd, "r");
    if (fp) {
        if (fgets(line, sizeof(line), fp)) {
            char *p = strchr(line, '=');
            if (p)
                freq = atoi(p + 1);
        }
        pclose(fp);
    }
    return freq;
}

static int sys_get_wlan_snr(const char *iface)
{
    char cmd[128];
    char line[256];
    FILE *fp;
    int rssi = 0;
    int noise = 0;

    snprintf(cmd, sizeof(cmd),
             "wpa_cli -i %s signal_poll 2>/dev/null", iface);
    fp = popen(cmd, "r");
    if (fp) {
        while (fgets(line, sizeof(line), fp)) {
            if (strncmp(line, "RSSI=", 5) == 0)
                rssi = atoi(line + 5);
            else if (strncmp(line, "NOISE=", 6) == 0)
                noise = atoi(line + 6);
        }
        pclose(fp);
    }

    if (noise == 0)
        noise = -95;  /* default noise floor */

    return rssi - noise;
}

static int sys_get_wlan_ssid(const char *iface, char *ssid, size_t size)
{
    char cmd[128];
    char line[256];
    FILE *fp;

    memset(ssid, 0, size);

    snprintf(cmd, sizeof(cmd),
             "wpa_cli -i %s status 2>/dev/null | grep '^ssid='", iface);
    fp = popen(cmd, "r");
    if (fp) {
        if (fgets(line, sizeof(line), fp)) {
            char *p = strchr(line, '=');
            if (p) {
                p++;
                p[strcspn(p, "\n")] = '\0';
                snprintf(ssid, size, "%s", p);
            }
        }
        pclose(fp);
    }

    return ssid[0] ? 0 : -1;
}

static int sys_get_fw_version(char *buf, size_t size)
{
    char line[256];
    FILE *fp;

    memset(buf, 0, size);

    fp = popen("mlanutl mlan0 version 2>/dev/null", "r");
    if (!fp)
        return -1;

    while (fgets(line, sizeof(line), fp)) {
        char *p = strstr(line, "FW Version");
        if (p) {
            /* Find the colon separator */
            char *colon = strchr(p, ':');
            if (colon) {
                colon++;
                while (*colon == ' ')
                    colon++;
                colon[strcspn(colon, "\n")] = '\0';
                snprintf(buf, size, "%s", colon);
            } else {
                p[strcspn(p, "\n")] = '\0';
                snprintf(buf, size, "%s", p);
            }
            pclose(fp);
            return 0;
        }
    }
    pclose(fp);
    return -1;
}

/* ------------------------------------------------------------------ */
/* Section 8.5: Safe command execution (no shell interpretation)       */
/* ------------------------------------------------------------------ */

static int run_cmd(const char *const argv[])
{
    pid_t pid = fork();
    if (pid < 0) {
        syslog(LOG_ERR, "fork: %s", strerror(errno));
        return -1;
    }
    if (pid == 0) {
        int devnull = open("/dev/null", O_WRONLY);
        if (devnull >= 0) {
            dup2(devnull, STDOUT_FILENO);
            dup2(devnull, STDERR_FILENO);
            close(devnull);
        }
        execvp(argv[0], (char *const *)argv);
        _exit(127);
    }
    int status;
    if (waitpid(pid, &status, 0) < 0)
        return -1;
    return (WIFEXITED(status) && WEXITSTATUS(status) == 0) ? 0 : -1;
}

/* ------------------------------------------------------------------ */
/* Section 9: Response sender                                          */
/* ------------------------------------------------------------------ */

static int vhl_send_ack(int fd, struct sockaddr_in *dst,
                        uint16_t req_id, uint16_t seq,
                        const uint8_t *payload, uint16_t payload_len)
{
    uint8_t buf[VHL_BUF_SIZE];
    struct vhl_header hdr;

    if (payload_len > VHL_MAX_PAYLOAD) {
        syslog(LOG_ERR, "payload too large: %u", payload_len);
        return -1;
    }

    hdr.version  = VHL_PROTO_VER;
    hdr.cmd_type = VHL_CMD_ACK;
    hdr.req_id   = req_id;
    hdr.seq_num  = seq;
    hdr.length   = payload_len;

    vhl_pack_header(buf, &hdr);
    if (payload && payload_len > 0)
        memcpy(buf + VHL_HDR_SIZE, payload, payload_len);

    ssize_t sent = sendto(fd, buf, VHL_HDR_SIZE + payload_len, 0,
                          (struct sockaddr *)dst, sizeof(*dst));
    if (sent < 0) {
        syslog(LOG_ERR, "sendto: %s", strerror(errno));
        return -1;
    }

    return 0;
}

/* ------------------------------------------------------------------ */
/* Section 9.5: Result helper & Indication system                      */
/* ------------------------------------------------------------------ */

static int vhl_send_result(int fd, struct sockaddr_in *dst, uint16_t req_id,
                           uint16_t seq, uint16_t result, uint16_t error_cause)
{
    uint8_t payload[4];
    put_be16(payload, result);
    put_be16(payload + 2, error_cause);
    return vhl_send_ack(fd, dst, req_id, seq, payload, sizeof(payload));
}

static int vhl_send_indication(const struct vhl_indication_cfg *ind,
                               uint16_t ind_id,
                               const uint8_t *payload, uint16_t payload_len);

static int ind_init_complete(const struct vhl_indication_cfg *ind,
                             uint32_t status);
static int ind_wlan_state(const struct vhl_indication_cfg *ind,
                          uint16_t status, uint16_t freq);
static int ind_roaming(const struct vhl_indication_cfg *ind,
                       uint8_t snr, uint16_t freq);
static int ind_ap_disconnect(const struct vhl_indication_cfg *ind,
                             uint16_t msg_id, uint16_t reason,
                             const uint8_t mac[6]);
static int ind_fault_detect(const struct vhl_indication_cfg *ind,
                            uint16_t congestion_id, uint32_t val);
static int ind_keep_alive(const struct vhl_indication_cfg *ind);

static int vhl_send_indication(const struct vhl_indication_cfg *ind,
                               uint16_t ind_id,
                               const uint8_t *payload, uint16_t payload_len)
{
    static uint16_t seq_counter;
    int fd;
    struct sockaddr_in dst;
    uint8_t buf[VHL_BUF_SIZE];
    struct vhl_header hdr;
    ssize_t sent;

    if (!ind->enabled)
        return 0;

    /* Check if this indication is enabled in info_mask */
    if (!(ind->info_mask & ind_id))
        return 0;

    if (payload_len > VHL_MAX_PAYLOAD)
        return -1;

    fd = socket(AF_INET, SOCK_DGRAM, 0);
    if (fd < 0) {
        syslog(LOG_ERR, "indication socket: %s", strerror(errno));
        return -1;
    }

    memset(&dst, 0, sizeof(dst));
    dst.sin_family      = AF_INET;
    dst.sin_port        = htons(ind->udp_port);
    dst.sin_addr.s_addr = ind->ip_addr;

    hdr.version  = VHL_PROTO_VER;
    hdr.cmd_type = VHL_CMD_INDICATION;
    hdr.req_id   = ind_id;
    hdr.seq_num  = seq_counter++;
    hdr.length   = payload_len;

    vhl_pack_header(buf, &hdr);
    if (payload && payload_len > 0)
        memcpy(buf + VHL_HDR_SIZE, payload, payload_len);

    sent = sendto(fd, buf, VHL_HDR_SIZE + payload_len, 0,
                  (struct sockaddr *)&dst, sizeof(dst));
    close(fd);

    if (sent < 0) {
        syslog(LOG_ERR, "indication sendto: %s", strerror(errno));
        return -1;
    }

    return 0;
}

__attribute__((unused))
static int ind_init_complete(const struct vhl_indication_cfg *ind,
                             uint32_t status)
{
    uint8_t payload[4];
    put_be32(payload, status);
    return vhl_send_indication(ind, VHL_IND_INIT_COMPLETE,
                               payload, sizeof(payload));
}

static int ind_wlan_state(const struct vhl_indication_cfg *ind,
                          uint16_t status, uint16_t freq)
{
    uint8_t payload[4];
    put_be16(payload, status);
    put_be16(payload + 2, freq);
    return vhl_send_indication(ind, VHL_IND_WLAN_STATE,
                               payload, sizeof(payload));
}

static int ind_roaming(const struct vhl_indication_cfg *ind,
                       uint8_t snr, uint16_t freq)
{
    uint8_t payload[3];
    payload[0] = snr;
    put_be16(payload + 1, freq);
    return vhl_send_indication(ind, VHL_IND_ROAMING,
                               payload, sizeof(payload));
}

static int ind_ap_disconnect(const struct vhl_indication_cfg *ind,
                             uint16_t msg_id, uint16_t reason,
                             const uint8_t mac[6])
{
    uint8_t payload[10];
    put_be16(payload, msg_id);
    put_be16(payload + 2, reason);
    memcpy(payload + 4, mac, 6);
    return vhl_send_indication(ind, VHL_IND_AP_DISCONNECT,
                               payload, sizeof(payload));
}

static int ind_fault_detect(const struct vhl_indication_cfg *ind,
                            uint16_t congestion_id, uint32_t val)
{
    uint8_t payload[6];
    put_be16(payload, congestion_id);
    put_be32(payload + 2, val);
    return vhl_send_indication(ind, VHL_IND_FAULT_DETECT,
                               payload, sizeof(payload));
}

__attribute__((unused))
static int ind_device_reset(const struct vhl_indication_cfg *ind,
                            uint16_t reset_cause)
{
    uint8_t payload[2];
    put_be16(payload, reset_cause);
    return vhl_send_indication(ind, VHL_IND_DEVICE_RESET,
                               payload, sizeof(payload));
}

static int ind_keep_alive(const struct vhl_indication_cfg *ind)
{
    uint8_t payload[32];
    time_t now = time(NULL);
    struct tm tm;
    int len;

    memset(payload, 0, sizeof(payload));
    localtime_r(&now, &tm);
    len = snprintf((char *)payload, sizeof(payload),
                   "%04d-%02d-%02d %02d:%02d:%02d",
                   tm.tm_year + 1900, tm.tm_mon + 1, tm.tm_mday,
                   tm.tm_hour, tm.tm_min, tm.tm_sec);
    if (len < 0 || (size_t)len >= sizeof(payload))
        payload[0] = '\0';
    return vhl_send_indication(ind, VHL_IND_KEEP_ALIVE,
                               payload, sizeof(payload));
}

/* ========== Resource Monitoring ========== */

#define RESOURCE_THRESHOLD_CPU  90
#define RESOURCE_THRESHOLD_MEM  90

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

/* ========== WPA Event Monitor ========== */

struct wpa_monitor {
    FILE *fp;
    int   fd;
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

static void wpa_parse_event(const char *line,
                            const struct vhl_indication_cfg *ind_cfg,
                            const struct vhld_config *cfg)
{
    const char *event;
    uint16_t freq;

    /* Skip <N> prefix */
    event = strchr(line, '>');
    if (event) event++;
    else event = line;
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

        const char *reason_str = strstr(event, "reason=");
        if (reason_str) {
            int reason = atoi(reason_str + 7);
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
            ind_ap_disconnect(ind_cfg, 0x000c, (uint16_t)reason, ap_mac);
        }
    }
    else if (strstr(event, "CTRL-EVENT-SIGNAL-CHANGE")) {
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

/* ------------------------------------------------------------------ */
/* Section 10: Command handlers                                        */
/* ------------------------------------------------------------------ */

static int handle_get_dev_info(int fd, struct sockaddr_in *src,
                               uint16_t seq, const struct vhld_config *cfg)
{
    uint8_t payload[346];
    uint8_t *p = payload;
    char fw_version[VHL_STR_MAX];
    uint8_t mac[6];
    uint32_t ip, mask, gw;

    memset(payload, 0, sizeof(payload));

    /* vendor_name: 64 bytes */
    put_str(p, cfg->vendor_name, VHL_STR_MAX);
    p += VHL_STR_MAX;

    /* device_model: 64 bytes */
    put_str(p, cfg->device_model, VHL_STR_MAX);
    p += VHL_STR_MAX;

    /* fw_version: 64 bytes */
    if (sys_get_fw_version(fw_version, sizeof(fw_version)) < 0)
        snprintf(fw_version, sizeof(fw_version), "unknown");
    put_str(p, fw_version, VHL_STR_MAX);
    p += VHL_STR_MAX;

    /* hw_version: 64 bytes */
    put_str(p, cfg->hw_version, VHL_STR_MAX);
    p += VHL_STR_MAX;

    /* serial_number: 64 bytes */
    put_str(p, cfg->serial_number, VHL_STR_MAX);
    p += VHL_STR_MAX;

    /* device_type: 2 bytes */
    put_be16(p, VHL_DEV_SINGLE_STATION);
    p += 2;

    /* eth_mac: 6 bytes */
    sys_get_mac(cfg->bind_iface, mac);
    memcpy(p, mac, 6);
    p += 6;

    /* wlan1_mac: 6 bytes */
    sys_get_mac(cfg->wlan_iface, mac);
    memcpy(p, mac, 6);
    p += 6;

    /* ip_addr, subnet_mask, default_gw: 4+4+4 bytes (already network order) */
    sys_get_ip_info(cfg->wlan_iface, &ip, &mask, &gw);
    memcpy(p, &ip, 4);
    p += 4;
    memcpy(p, &mask, 4);
    p += 4;
    memcpy(p, &gw, 4);

    return vhl_send_ack(fd, src, VHL_REQ_GET_DEV_INFO, seq,
                        payload, sizeof(payload));
}

static int handle_get_dev_status(int fd, struct sockaddr_in *src,
                                 uint16_t seq, const struct vhld_config *cfg)
{
    uint8_t payload[9];
    uint16_t status, freq;
    int snr;

    memset(payload, 0, sizeof(payload));

    status = sys_get_operstate(cfg->wlan_iface);
    freq   = (uint16_t)sys_get_wlan_freq(cfg->wlan_iface);
    snr    = sys_get_wlan_snr(cfg->wlan_iface);

    put_be16(payload, VHL_DEV_SINGLE_STATION);       /* device_type */
    put_be16(payload + 2, status);                    /* status */
    put_be16(payload + 4, freq);                      /* wlan1_freq */
    payload[6] = (snr > 0) ? (uint8_t)snr : 0;       /* wlan1_snr */
    put_be16(payload + 7, freq);                      /* interface_priority */

    return vhl_send_ack(fd, src, VHL_REQ_GET_DEV_STATUS, seq,
                        payload, sizeof(payload));
}

static int handle_get_wlan_config(int fd, struct sockaddr_in *src,
                                  uint16_t seq, const struct vhld_config *cfg)
{
    uint8_t payload[40];
    uint16_t freq;
    uint8_t mode, bw;
    char ssid[VHL_SSID_MAX];

    memset(payload, 0, sizeof(payload));

    freq = (uint16_t)sys_get_wlan_freq(cfg->wlan_iface);

    /* Infer mode from frequency */
    if (freq >= 5925)
        mode = 0x08;  /* 6GHz ax */
    else if (freq >= 5000)
        mode = 0x06;  /* 5GHz ax */
    else
        mode = 0x03;  /* 2.4GHz ax */

    bw = 0x04;  /* default 80MHz */

    sys_get_wlan_ssid(cfg->wlan_iface, ssid, sizeof(ssid));

    put_be16(payload, VHL_DEV_SINGLE_STATION);        /* device_type */
    put_be16(payload + 2, freq);                      /* interface_priority */
    put_be16(payload + 4, freq);                      /* wlan1_freq */
    payload[6] = mode;                                /* wlan1_mode */
    payload[7] = bw;                                  /* wlan1_bandwidth */
    put_str(payload + 8, ssid, VHL_SSID_MAX);         /* ssid */

    return vhl_send_ack(fd, src, VHL_REQ_GET_WLAN_CONFIG, seq,
                        payload, sizeof(payload));
}

static int handle_set_password(int fd, struct sockaddr_in *src,
                               uint16_t seq,
                               const uint8_t *payload, uint16_t len)
{
    if (len < 128) {
        syslog(LOG_WARNING, "set_password: payload too short (%u)", len);
        return vhl_send_result(fd, src, VHL_REQ_SET_PASSWORD, seq,
                               0x0001, 0x0000);
    }

    /* Check NULL termination of old_password (first 64 bytes) */
    if (memchr(payload, '\0', VHL_STR_MAX) == NULL)
        return vhl_send_result(fd, src, VHL_REQ_SET_PASSWORD, seq,
                               0x0001, 0x0003);

    /* Check NULL termination of new_password (next 64 bytes) */
    if (memchr(payload + VHL_STR_MAX, '\0', VHL_STR_MAX) == NULL)
        return vhl_send_result(fd, src, VHL_REQ_SET_PASSWORD, seq,
                               0x0001, 0x0005);

    /* TODO: Apply password change via wpa_supplicant */
    syslog(LOG_INFO, "set_password: accepted (apply not yet implemented)");

    return vhl_send_result(fd, src, VHL_REQ_SET_PASSWORD, seq,
                           0x0000, 0x0000);
}

static int handle_set_ip_addr(int fd, struct sockaddr_in *src,
                              uint16_t seq,
                              const uint8_t *payload, uint16_t len,
                              const struct vhld_config *cfg)
{
    uint32_t ip_net, mask_net, gw_net;
    uint32_t mask_host, gw_host, ip_host;
    char ip_str[INET_ADDRSTRLEN], mask_str[INET_ADDRSTRLEN];
    char gw_str[INET_ADDRSTRLEN];
    char cmd[256];
    int prefix;

    if (len < 12) {
        syslog(LOG_WARNING, "set_ip_addr: payload too short (%u)", len);
        return vhl_send_result(fd, src, VHL_REQ_SET_IP_ADDR, seq,
                               0x0001, 0x0000);
    }

    memcpy(&ip_net, payload, 4);
    memcpy(&mask_net, payload + 4, 4);
    memcpy(&gw_net, payload + 8, 4);

    mask_host = ntohl(mask_net);
    gw_host   = ntohl(gw_net);
    ip_host   = ntohl(ip_net);

    /* Validate mask: must be contiguous 1-bits */
    if (mask_host != 0) {
        uint32_t inv = ~mask_host;
        if ((inv & (inv + 1)) != 0)
            return vhl_send_result(fd, src, VHL_REQ_SET_IP_ADDR, seq,
                                   0x0001, 0x0001);
    }

    /* Validate gateway in same subnet */
    if (gw_host != 0) {
        if ((ip_host & mask_host) != (gw_host & mask_host))
            return vhl_send_result(fd, src, VHL_REQ_SET_IP_ADDR, seq,
                                   0x0001, 0x0002);
    }

    /* Convert to strings */
    inet_ntop(AF_INET, &ip_net, ip_str, sizeof(ip_str));
    inet_ntop(AF_INET, &mask_net, mask_str, sizeof(mask_str));
    inet_ntop(AF_INET, &gw_net, gw_str, sizeof(gw_str));

    /* Count prefix length from mask */
    prefix = 0;
    if (mask_host != 0) {
        uint32_t m = mask_host;
        while (m & 0x80000000U) {
            prefix++;
            m <<= 1;
        }
    }

    /* Apply: flush and add new address */
    snprintf(cmd, sizeof(cmd),
             "ip addr flush dev %s && ip addr add %s/%d dev %s",
             cfg->wlan_iface, ip_str, prefix, cfg->wlan_iface);
    if (system(cmd) != 0)
        syslog(LOG_WARNING, "set_ip_addr: failed to apply address");

    /* Set default route if gateway is non-zero */
    if (gw_host != 0) {
        snprintf(cmd, sizeof(cmd),
                 "ip route add default via %s dev %s 2>/dev/null || "
                 "ip route replace default via %s dev %s",
                 gw_str, cfg->wlan_iface, gw_str, cfg->wlan_iface);
        if (system(cmd) != 0)
            syslog(LOG_WARNING, "set_ip_addr: failed to set default route");
    }

    syslog(LOG_INFO, "set_ip_addr: applied %s/%d gw %s on %s",
           ip_str, prefix, gw_str, cfg->wlan_iface);

    return vhl_send_result(fd, src, VHL_REQ_SET_IP_ADDR, seq,
                           0x0000, 0x0000);
}

static int handle_set_wlan_config(int fd, struct sockaddr_in *src,
                                  uint16_t seq,
                                  const uint8_t *payload, uint16_t len,
                                  const struct vhld_config *cfg)
{
    uint16_t dev_type, freq;
    char ssid[VHL_SSID_MAX + 1];

    if (len < 40) {
        syslog(LOG_WARNING, "set_wlan_config: payload too short (%u)", len);
        return vhl_send_result(fd, src, VHL_REQ_SET_WLAN_CONFIG, seq,
                               0x0001, 0x0000);
    }

    dev_type = get_be16(payload);
    /* skip priority(2) */
    freq     = get_be16(payload + 4);
    /* skip mode(1) + bw(1) */

    /* Validate device_type */
    if (dev_type != VHL_DEV_SINGLE_STATION)
        return vhl_send_result(fd, src, VHL_REQ_SET_WLAN_CONFIG, seq,
                               0x0001, 0x0002);

    /* Validate freq range */
    if (freq != 0 && (freq < 2400 || (freq > 2500 && freq < 5000) ||
                      freq > 7125))
        return vhl_send_result(fd, src, VHL_REQ_SET_WLAN_CONFIG, seq,
                               0x0001, 0x0001);

    /* Extract SSID (32 bytes at offset 8) */
    memcpy(ssid, payload + 8, VHL_SSID_MAX);
    ssid[VHL_SSID_MAX] = '\0';

    /* Validate SSID NULL termination */
    if (memchr(payload + 8, '\0', VHL_SSID_MAX) == NULL)
        return vhl_send_result(fd, src, VHL_REQ_SET_WLAN_CONFIG, seq,
                               0x0001, 0x0004);

    /* Validate SSID ASCII printable */
    for (size_t i = 0; ssid[i] != '\0'; i++) {
        if (ssid[i] < 0x20 || ssid[i] > 0x7E)
            return vhl_send_result(fd, src, VHL_REQ_SET_WLAN_CONFIG, seq,
                                   0x0001, 0x0003);
    }

    /* Apply via opc_wlan_apply.sh (execvp - no shell interpretation):
     * conf 파일을 직접 편집(ssid/전역+블록 공통 freq_list)하고 wpa_cli reconfigure 로
     * 영속+동적적용을 한 번에 처리한다. ssid 와 freq 를 한 번의 호출로 묶어 단일
     * reconfigure(끊김 1회)로 적용한다. 스크립트가 ssid 를 따옴표로 감싸므로
     * 원문만 전달한다.
     * (set_network+reassociate 폐기 이유: wpa_supplicant v2.10 save_config 가
     *  freq_list 를 직렬화하지 않아 영속이 깨지고, reassociate 는 현재 BSS 를
     *  유지한 채 freq_list 를 재평가하지 않아 freq 변경이 즉시 반영되지 않는다.) */
    {
        char freq_str[16];
        const char *argv[12];
        int n = 0;
        int have_ssid = (ssid[0] != '\0');
        argv[n++] = "/usr/local/scripts/opc_wlan_apply.sh";
        argv[n++] = cfg->wlan_iface;
        if (have_ssid) {        /* 빈 ssid 는 conf 의 ssid 를 비우지 않도록 생략 */
            argv[n++] = "ssid";
            argv[n++] = ssid;
        }
        if (freq != 0) {
            snprintf(freq_str, sizeof(freq_str), "%u", freq);
            argv[n++] = "freq";
            argv[n++] = freq_str;
        }
        argv[n] = NULL;
        if (!have_ssid && freq == 0)
            syslog(LOG_INFO, "set_wlan_config: nothing to apply (empty ssid, freq=0)");
        else if (run_cmd(argv) != 0)
            syslog(LOG_WARNING, "set_wlan_config: opc_wlan_apply.sh failed");
    }

    syslog(LOG_INFO, "set_wlan_config: ssid='%s' freq=%u on %s",
           ssid, freq, cfg->wlan_iface);

    return vhl_send_result(fd, src, VHL_REQ_SET_WLAN_CONFIG, seq,
                           0x0000, 0x0000);
}

static int handle_set_indication(int fd, struct sockaddr_in *src,
                                 uint16_t seq,
                                 const uint8_t *payload, uint16_t len,
                                 struct vhl_indication_cfg *ind_cfg)
{
    uint8_t info_mask;
    uint8_t valid_mask;

    if (len < 8) {
        syslog(LOG_WARNING, "set_indication: payload too short (%u)", len);
        return vhl_send_result(fd, src, VHL_REQ_SET_INDICATION, seq,
                               0x0001, 0x0000);
    }

    info_mask  = payload[2];
    valid_mask = 0x01 | 0x02 | 0x04 | 0x08 | 0x10 | 0x20 | 0x80;

    /* Check for invalid mask bits */
    if (info_mask & (uint8_t)~valid_mask)
        return vhl_send_result(fd, src, VHL_REQ_SET_INDICATION, seq,
                               0x0001, 0x0001);

    ind_cfg->udp_port      = get_be16(payload);
    ind_cfg->info_mask     = info_mask;
    ind_cfg->keepalive_sec = payload[3];
    memcpy(&ind_cfg->ip_addr, payload + 4, 4);
    ind_cfg->enabled       = (info_mask != 0);

    syslog(LOG_INFO, "set_indication: mask=0x%02x port=%u keepalive=%us",
           ind_cfg->info_mask, ind_cfg->udp_port, ind_cfg->keepalive_sec);

    return vhl_send_result(fd, src, VHL_REQ_SET_INDICATION, seq,
                           0x0000, 0x0000);
}

static int handle_reset(int fd, struct sockaddr_in *src, uint16_t seq)
{
    /* Send ACK with no payload */
    vhl_send_ack(fd, src, VHL_REQ_RESET, seq, NULL, 0);

    syslog(LOG_INFO, "reset requested, rebooting...");
    sync();
    sleep(1);
    if (system("reboot") != 0)
        syslog(LOG_ERR, "reboot command failed");

    return 0;
}

/* ------------------------------------------------------------------ */
/* Section 11: Request dispatcher                                      */
/* ------------------------------------------------------------------ */

static void dispatch_request(int fd, struct sockaddr_in *src,
                             const uint8_t *buf, ssize_t n,
                             const struct vhld_config *cfg,
                             struct vhl_indication_cfg *ind_cfg)
{
    struct vhl_header hdr;

    if (n < VHL_HDR_SIZE) {
        syslog(LOG_WARNING, "packet too short (%zd bytes)", n);
        return;
    }

    vhl_unpack_header(buf, &hdr);

    if (hdr.version != VHL_PROTO_VER) {
        syslog(LOG_WARNING, "unsupported protocol version %u", hdr.version);
        return;
    }

    if (hdr.cmd_type != VHL_CMD_REQUEST) {
        syslog(LOG_WARNING, "unexpected cmd_type 0x%02x", hdr.cmd_type);
        return;
    }

    if (hdr.length > VHL_MAX_PAYLOAD) {
        syslog(LOG_WARNING, "payload length %u exceeds max", hdr.length);
        return;
    }

    if ((ssize_t)(VHL_HDR_SIZE + hdr.length) > n) {
        syslog(LOG_WARNING, "truncated packet: header says %u payload, got %zd total",
               hdr.length, n);
        return;
    }

    const uint8_t *payload = buf + VHL_HDR_SIZE;
    uint16_t payload_len = hdr.length;

    syslog(LOG_DEBUG, "req_id=0x%04x seq=%u len=%u from %s:%u",
           hdr.req_id, hdr.seq_num, hdr.length,
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
        syslog(LOG_WARNING, "unknown req_id 0x%04x", hdr.req_id);
        break;
    }
}

/* ------------------------------------------------------------------ */
/* Section 12: main                                                    */
/* ------------------------------------------------------------------ */

int main(int argc, char *argv[])
{
    const char *conf_path = "/usr/local/vhl_daemon/vhld.conf";
    struct vhld_config cfg;
    struct vhl_indication_cfg ind_cfg = {0};
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

    /* wpa_cli event monitor */
    struct wpa_monitor wpa_mon = { .fp = NULL, .fd = -1 };
    if (wpa_monitor_start(&wpa_mon, cfg.wlan_iface) < 0)
        syslog(LOG_WARNING, "wpa_cli monitor unavailable, events disabled");

    time_t last_keepalive = 0;
    time_t last_resource_check = 0;

    syslog(LOG_INFO, "vhld ready");

    /* 초기화 완료 통지 (info_mask와 상관없이 항상 전송) */
    ind_init_complete(&ind_cfg, 0x00000001);  /* 초기 설정 완료 */

    while (g_running) {
        struct pollfd pfds[2];
        int nfds = 1;
        int ret;
        time_t now;

        pfds[0].fd = udp_fd;
        pfds[0].events = POLLIN;

        if (wpa_mon.fd >= 0) {
            pfds[1].fd = wpa_mon.fd;
            pfds[1].events = POLLIN;
            nfds = 2;
        }

        ret = poll(pfds, (nfds_t)nfds, 1000);
        now = time(NULL);

        if (ret < 0) {
            if (errno == EINTR) continue;
            syslog(LOG_ERR, "poll: %s", strerror(errno));
            break;
        }

        /* UDP request handling */
        if (pfds[0].revents & POLLIN) {
            uint8_t buf[VHL_BUF_SIZE];
            struct sockaddr_in src;
            socklen_t src_len = sizeof(src);

            ssize_t n = recvfrom(udp_fd, buf, sizeof(buf), 0,
                                 (struct sockaddr *)&src, &src_len);
            if (n > 0)
                dispatch_request(udp_fd, &src, buf, n, &cfg, &ind_cfg);
        }

        /* wpa_cli event handling */
        if (nfds > 1 && (pfds[1].revents & (POLLIN | POLLHUP | POLLERR))) {
            char line[512];
            if (fgets(line, sizeof(line), wpa_mon.fp)) {
                wpa_parse_event(line, &ind_cfg, &cfg);
            } else {
                /* EOF or error: wpa_cli exited, disable to prevent busy loop */
                syslog(LOG_WARNING, "wpa_cli monitor EOF, disabling");
                wpa_monitor_stop(&wpa_mon);
            }
        }

        /* Keep Alive */
        if (ind_cfg.enabled && ind_cfg.keepalive_sec > 0 &&
            (now - last_keepalive) >= ind_cfg.keepalive_sec) {
            ind_keep_alive(&ind_cfg);
            last_keepalive = now;
        }

        /* Resource monitoring (every 10 seconds) */
        if ((now - last_resource_check) >= 10) {
            check_resources(&ind_cfg);
            last_resource_check = now;
        }
    }

    wpa_monitor_stop(&wpa_mon);
    close(udp_fd);
    syslog(LOG_INFO, "vhld stopped");
    closelog();
    return 0;
}
