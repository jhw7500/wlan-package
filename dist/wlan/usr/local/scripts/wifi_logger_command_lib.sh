#!/bin/sh

LOGGER_TIMEOUT_BIN="${LOGGER_TIMEOUT_BIN:-timeout}"

logger_run_bounded() {
    _logger_timeout_s=$1
    shift
    "$LOGGER_TIMEOUT_BIN" --signal=TERM --kill-after=1s \
        "${_logger_timeout_s}s" "$@"
}

logger_read_bounded() {
    _logger_timeout_s=$1
    _logger_path=$2
    logger_run_bounded "$_logger_timeout_s" cat -- "$_logger_path"
}
