CC ?= cc
CROSS_CC ?= aarch64-linux-gnu-gcc
REQUIRED_CFLAGS = -Wall -Wextra -Werror -std=c11 -O2
TARGET = ftpcmd_start_log
SOURCE = ftpcmd_start_log.c

.PHONY: release cross clean

release: $(SOURCE)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(REQUIRED_CFLAGS) $(LDFLAGS) -o $(TARGET) $(SOURCE) $(LDLIBS)

cross: $(SOURCE)
	$(CROSS_CC) $(CPPFLAGS) $(CFLAGS) $(REQUIRED_CFLAGS) $(LDFLAGS) -o $(TARGET) $(SOURCE) $(LDLIBS)

clean:
	rm -f $(TARGET)
