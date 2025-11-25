LOGDIR="/var/log/cantops"
CAPDIR="$LOGDIR/mgmt"
SCANDIR="$LOGDIR/scan"
STATDIR="$LOGDIR/stat"
JSONDIR="$LOGDIR/json"
SUMDIR="$LOGDIR/summary"
WPADIR="$LOGDIR/wpa"

STATDIR0="$STATDIR/mlan0"
JSONDIR0="$JSONDIR/mlan0"
SCANDIR0="$SCANDIR/mlan0"
CAPDIR0="$CAPDIR/mlan0"
WPADIR0="$WPADIR/mlan0"

STATDIR1="$STATDIR/mlan1"
JSONDIR1="$JSONDIR/mlan1"
SCANDIR1="$SCANDIR/mlan1"
CAPDIR1="$CAPDIR/mlan1"
WPADIR1="$WPADIR/mlan1"

JSONDIR2="$JSONDIR/eth0"

alias dpkgif='dpkg -i --force-overwrite $1'
alias dpkglg='dpkg -l |grep $1'

alias dlogg='dmesg |grep -i $1 -a'
alias dlogf='dmesg -w'
alias dlogt='dmesg |tail -n $1'
alias dlogh='dmesg |head -n $1'
alias slog='cat $LOGDIR/sys.log'
alias slogf='tail -f $LOGDIR/sys.log'
alias slogg='cat $LOGDIR/sys.log | grep -i $1 -a'
alias slogt='cat $LOGDIR/sys.log | tail -n $1'
alias slogh='cat $LOGDIR/sys.log | head -n $1'
alias klogg='cat $LOGDIR/kern.log | grep -i $1 -a'
alias klogt='cat $LOGDIR/kern.log | tail -n $1'
alias klogh='cat $LOGDIR/kern.log | head -n $1'
alias klogf='tail -f $LOGDIR/kern.log'

alias plog='cat $LOGDIR/ping.log'
alias plogg='cat $LOGDIR/ping.log | grep -i $1 -a'
alias plogt='cat $LOGDIR/ping.log | tail -n $1'
alias plogh='cat $LOGDIR/ping.log | head -n $1'
alias plogf='tail -f $LOGDIR/ping.log'

alias sslog='cat $SUMDIR/summary.log'
alias sslogg='cat $SUMDIR/summary.log | grep -i $1 -a'
alias sslogt='cat $SUMDIR/summary.log | tail -n $1'
alias sslogh='cat $SUMDIR/summary.log | head -n $1'
alias sslogf='tail -f $SUMDIR/summary.log'

alias llog='cat $LOGDIR/logger.log'
alias llogg='cat $LOGDIR/logger.log | grep -i $1 -a'
alias llogt='cat $LOGDIR/logger.log | tail -n $1'
alias llogh='cat $LOGDIR/logger.log | head -n $1'
alias llogf='tail -f $LOGDIR/logger.log'

alias clog='cat $LOGDIR/cpu.log'
alias clogg='cat $LOGDIR/cpu.log | grep -i $1 -a'
alias clogt='cat $LOGDIR/cpu.log | tail -n $1'
alias clogh='cat $LOGDIR/cpu.log | head -n $1'
alias clogf='tail -f $LOGDIR/cpu.log'

alias nlog='cat $LOGDIR/link_stat.log'
alias nlogg='cat $LOGDIR/link_stat.log | grep -i $1 -a'
alias nlogt='cat $LOGDIR/link_stat.log | tail -n $1'
alias nlogh='cat $LOGDIR/link_stat.log | head -n $1'
alias nlogf='tail -f $LOGDIR/link_stat.log'

alias ulog='cat $LOGDIR/ui.log'
alias ulogg='cat $LOGDIR/ui.log | grep -i $1 -a'
alias ulogt='cat $LOGDIR/ui.log | tail -n $1'
alias ulogh='cat $LOGDIR/ui.log | head -n $1'
alias ulogf='tail -f $LOGDIR/ui.log'

alias mlog='cat $CAPDIR0/mgmt.log'
alias mlogg='cat $CAPDIR0/mgmt.log | grep -i $1 -a'
alias mlogt='cat $CAPDIR0/mgmt.log |tail -n $1'
alias mlogh='cat $CAPDIR0/mgmt.log |head -n $1'
alias mlogf='tail -f $CAPDIR0/mgmt.log'

#alias plogt='tcpdump -r $CAPDIR/tmp/pcap.pcap -tttt -e -n |tail -n $1'
#alias plogh='tcpdump -r $CAPDIR/tmp/pcap.pcap -tttt -e -n -c $1'
#alias plogg='tcpdump -r $CAPDIR/tmp/pcap.pcap -tttt -e- n |grep -i $1 -a'

alias rlogg='cat $WPADIR0/wpa.log |grep -i -E "ROAM|CTRL-EVENT-CONNECTED|Associated with|Trying to authenticate with|Trying to associate" | grep -i $1 -a'
alias rlogt='cat $WPADIR0/wpa.log |grep -i -E "ROAM|CTRL-EVENT-CONNECTED|Associated with|Trying to authenticate with|Trying to associate" | tail -n $1'
alias rlogh='cat $WPADIR0/wpa.log |grep -i -E "ROAM|CTRL-EVENT-CONNECTED|Associated with|Trying to authenticate with|Trying to associate" | head -n $1'
alias rlogf='tail -f $WPADIR0/mgmt.log |grep -i -E "ROAM|CTRL-EVENT-CONNECTED|Associated with|Trying to authenticate with|Trying to associate"'

alias wlog='cat $WPADIR0/wpa.log'
alias wlogg='cat $WPADIR0/wpa.log | grep -i $1 -a'
alias wlogt='cat $WPADIR0/wpa.log |tail -n $1'
alias wlogh='cat $WPADIR0/wpa.log |head -n $1'
alias wlogf='tail -f $WPADIR0/wpa.log'

alias wlog1='cat $WPADIR1/wpa.log'
alias wlogg1='cat $WPADIR1/wpa.log | grep -i $1 -a'
alias wlogt1='cat $WPADIR1/wpa.log |tail -n $1'
alias wlogh1='cat $WPADIR1/wpa.log |head -n $1'
alias wlogf1='tail -f $WPADIR1/wpa.log'

alias alog='cat $SCANDIR0/ap.log'
alias alogg='cat $SCANDIR0/ap.log | grep -i $1 -a'
alias alogt='cat $SCANDIR0/ap.log | tail -n $1'
alias alogh='cat $SCANDIR0/ap.log | head -n $1'
alias alogf='tail -f $SCANDIR0/ap.log'

alias blog='cat $JSONDIR0/beacon.json'
alias blogg='cat $JSONDIR0/beacon.json | grep -i $1 -a'
alias blogt='cat $JSONDIR0/beacon.json | tail -n $1'
alias blogh='cat $JSONDIR0/beacon.log | head -n $1'
alias blogf='tail -f $JSONDIR0/beacon.json'

alias flog='cat $SCANDIR0/freq.log'
alias flogg='cat $SCANDIR0/freq.log | grep -i $1 -a'
alias flogt='cat $SCANDIR0/freq.log | tail -n $1'
alias flogh='cat $SCANDIR0/freq.log | head -n $1'
alias flogf='tail -f $SCANDIR0/freq.log'

alias tlog='cat $STATDIR0/stat.log'
alias tlogg='cat $STATDIR0/stat.log | grep -i $1 -a'
alias tlogt='cat $STATDIR0/stat.log | tail -n $1'
alias tlogh='cat $STATDIR0/stat.log | head -n $1'
alias tlogf='tail -f $STATDIR0/stat.log'

alias ilog='cat $JSONDIR0/link.json'
alias ilogg='cat $JSONDIR0/link.json | grep -i $1 -a'
alias ilogt='cat $JSONDIR0/link.json | tail -n $1'
alias ilogh='cat $JSONDIR0/link.json | head -n $1'
alias ilogf='tail -f $JSONDIR0/link.json'

alias tlog1='cat $STATDIR1/stat.log'
alias tlogg1='cat $STATDIR1/stat.log | grep -i $1 -a'
alias tlogt1='cat $STATDIR1/stat.log | tail -n $1'
alias tlogh1='cat $STATDIR1/stat.log | head -n $1'
alias tlogf1='tail -f $STATDIR1/stat.log'

alias ilog1='cat $JSONDIR1/link.json'
alias ilogg1='cat $JSONDIR1/link.json | grep -i $1 -a'
alias ilogt1='cat $JSONDIR1/link.json | tail -n $1'
alias ilogh1='cat $JSONDIR1/link.json | head -n $1'
alias ilogf1='tail -f $JSONDIR1/link.json'

alias alog1='cat $SCANDIR1/ap.log'
alias alogg1='cat $SCANDIR1/ap.log | grep -i $1 -a'
alias alogt1='cat $SCANDIR1/ap.log | tail -n $1'
alias alogh1='cat $SCANDIR1/ap.log | head -n $1'
alias alogf1='tail -f $SCANDIR1/ap.log'

alias blog1='cat $JSONDIR1/beacon.json'
alias blogg1='cat $JSONDIR1/beacon.json | grep -i $1 -a'
alias blogt1='cat $JSONDIR1/beacon.json | tail -n $1'
alias blogh1='cat $JSONDIR1/beacon.json | head -n $1'
alias blogf1='tail -f $JSONDIR1/beacon.log'

alias flog1='cat $SCANDIR1/freq.log'
alias flogg1='cat $SCANDIR1/freq.log | grep -i $1 -a'
alias flogt1='cat $SCANDIR1/freq.log | tail -n $1'
alias flogh1='cat $SCANDIR1/freq.log | head -n $1'
alias flogf1='tail -f $SCANDIR1/freq.log'

alias ilog2='cat $JSONDIR2/link.json'
alias ilogg2='cat $JSONDIR2/link.json | grep -i $1 -a'
alias ilogt2='cat $JSONDIR2/link.json | tail -n $1'
alias ilogh2='cat $JSONDIR2/link.json | head -n $1'
alias ilogf2='tail -f $JSONDIR2/link.json'

alias psg='ps -ef |grep -i $1'
alias sdr='systemctl daemon-reload'
alias sst='systemctl status $1'
alias sss='systemctl start $1'
alias ssp='systemctl stop $1'
alias ssr='systemctl restart $1'
alias sse='systemctl enable $1'
alias ssd='systemctl disable $1'
alias ssg='systemctl |grep -i $1'
alias ssenc='systemctl enable --now wifi_checker@mlan0'
alias ssdnc='systemctl disable --now wifi_checker@mlan0'
alias camchk1='i2ctransfer -f -y 1 w2@0x48 0x00 0x13 r1'
alias camchk2='i2ctransfer -f -y 2 w2@0x48 0x00 0x13 r1'
alias jo='journalctl -o short-iso-precise'

