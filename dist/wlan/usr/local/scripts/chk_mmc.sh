#!/bin/bash
tag=$(basename "$0")
RES=$(cat /sys/kernel/debug/mmc2/mmc2:0001/ext_csd)
typea="${RES:536:2}"
typeb="${RES:538:2}"
typead=$((16#$typea))
typebd=$((16#$typeb))
typead=$((typead*10))
typebd=$((typebd*10))
#echo "Type A: ${typead} (?$typead%)"
#echo "Type B: ${typebd} (?$typebd%)"
#logger -p local0.notice [$tag:$LINENO] mmc Type A:$typead% Type B:$typebd%
if [ $typead -ge 80 ] || [ $typebd -ge 80 ]; then
    logger -p local0.emerg "[$tag:$LINENO] mmc Type A:$typead% Type B:$typebd%"
elif [ $typead -ge 60 ] || [ $typebd -ge 60 ]; then
    logger -p local0.crit "[$tag:$LINENO] mmc Type A:$typead% Type B:$typebd%"
elif [ $typead -ge 40 ] || [ $typebd -ge 40 ]; then
    logger -p local0.error "[$tag:$LINENO] mmc Type A:$typead% Type B:$typebd%"
else
    logger -p local0.notice "[$tag:$LINENO] mmc Type A:$typead% Type B:$typebd%"
fi
