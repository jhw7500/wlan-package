#define _DEFAULT_SOURCE
#define _POSIX_C_SOURCE 200809L

#include <stddef.h>
#include <stdio.h>
#include <syslog.h>
#include <time.h>

enum {
    COMMAND_MAX = 64,
};

static int ascii_is_digit(unsigned char character)
{
    return character >= '0' && character <= '9';
}

static int ascii_is_command_character(unsigned char character)
{
    return (character >= 'A' && character <= 'Z') ||
           (character >= 'a' && character <= 'z') ||
           ascii_is_digit(character) || character == '_' || character == '-';
}

static int command_is_valid(const char *command)
{
    size_t index;

    if (command[0] == '\0') {
        return 0;
    }
    for (index = 0; index <= COMMAND_MAX; ++index) {
        unsigned char character = (unsigned char)command[index];

        if (character == '\0') {
            return 1;
        }
        if (!ascii_is_command_character(character)) {
            return 0;
        }
    }
    return 0;
}

static int format_timestamp(char *output, size_t output_size)
{
    struct timespec now;
    struct tm local;
    char date[32];
    char iso_offset[10];
    long offset;
    long offset_magnitude;
    long offset_hours;
    long offset_minutes;
    long offset_seconds;
    int length;

    if (clock_gettime(CLOCK_REALTIME, &now) != 0 ||
        localtime_r(&now.tv_sec, &local) == NULL ||
        strftime(date, sizeof(date), "%Y-%m-%dT%H:%M:%S", &local) == 0) {
        return 0;
    }

    offset = local.tm_gmtoff;
    if (offset <= -86400L || offset >= 86400L) {
        return 0;
    }
    offset_magnitude = offset < 0 ? -offset : offset;
    offset_hours = offset_magnitude / 3600L;
    offset_minutes = (offset_magnitude % 3600L) / 60L;
    offset_seconds = offset_magnitude % 60L;

    if (offset_seconds == 0) {
        length = snprintf(iso_offset, sizeof(iso_offset), "%c%02ld:%02ld",
                          offset < 0 ? '-' : '+', offset_hours, offset_minutes);
    } else {
        length = snprintf(iso_offset, sizeof(iso_offset), "%c%02ld:%02ld:%02ld",
                          offset < 0 ? '-' : '+', offset_hours, offset_minutes,
                          offset_seconds);
    }
    if (length < 0 || (size_t)length >= sizeof(iso_offset)) {
        return 0;
    }

    length = snprintf(output, output_size, "%s.%03ld%s", date,
                      now.tv_nsec / 1000000L, iso_offset);
    return length >= 0 && (size_t)length < output_size;
}

int main(int argc, char **argv)
{
    char timestamp[64];

    if (argc != 2 || !command_is_valid(argv[1])) {
        return 2;
    }
    tzset();
    if (!format_timestamp(timestamp, sizeof(timestamp))) {
        return 0;
    }

    syslog(LOG_LOCAL0 | LOG_INFO, "ftpcmd_start command=%s time=%s",
           argv[1], timestamp);
    return 0;
}
