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
