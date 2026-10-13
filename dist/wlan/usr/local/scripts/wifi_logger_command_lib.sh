#!/bin/sh

LOGGER_TIMEOUT_BIN="${LOGGER_TIMEOUT_BIN:-timeout}"

logger_run_bounded() {
    _logger_timeout_s=$1
    shift
    "$LOGGER_TIMEOUT_BIN" --foreground --signal=TERM --kill-after=1s \
        "${_logger_timeout_s}s" setsid sh -c '
            trap "kill -KILL 0" TERM
            exec 9<&0
            "$@" <&9 9<&- &
            _logger_child=$!
            exec 9<&-
            wait "$_logger_child"
        ' sh "$@"
}

logger_read_bounded() {
    _logger_timeout_s=$1
    _logger_path=$2
    logger_run_bounded "$_logger_timeout_s" cat -- "$_logger_path"
}
