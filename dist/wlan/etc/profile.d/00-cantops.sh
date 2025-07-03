LOGDIR="/var/log/cantops"
CAPDIR="$LOGDIR/mgmt"
SCANDIR="$LOGDIR/scan"
STATDIR="$LOGDIR/stat"
LINKDIR="$LOGDIR/link"
SUMDIR="$LOGDIR/summary"
WPADIR="$LOGDIR/wpa"

STATDIR0="$STATDIR/mlan0"
LINKDIR0="$LINKDIR/mlan0"
SCANDIR0="$SCANDIR/mlan0"
CAPDIR0="$CAPDIR/mlan0"
WPADIR0="$WPADIR/mlan0"

STATDIR1="$STATDIR/mlan1"
LINKDIR1="$LINKDIR/mlan1"
SCANDIR1="$SCANDIR/mlan1"
CAPDIR1="$CAPDIR/mlan1"
WPADIR1="$WPADIR/mlan1"

alias dpkgif='dpkg -i --force-overwrite $1'
alias dpkglg='dpkg -l |grep $1'

alias slogf='tail -f $LOGDIR/sys.log'
alias slogg='cat $LOGDIR/sys.log | grep -i $1 -a'
alias slogt='cat $LOGDIR/sys.log | tail -n $1'
alias slogh='cat $LOGDIR/sys.log | head -n $1'
alias klogg='cat $LOGDIR/kern.log | grep -i $1 -a'
alias klogt='cat $LOGDIR/kern.log | tail -n $1'
alias klogh='cat $LOGDIR/kern.log | head -n $1'
alias klogf='tail -f $LOGDIR/kern.log'

alias plogg='cat $LOGDIR/ping.log | grep -i $1 -a'
alias plogt='cat $LOGDIR/ping.log | tail -n $1'
alias plogh='cat $LOGDIR/ping.log | head -n $1'
alias plogf='tail -f $LOGDIR/ping.log'

alias sslogg='cat $SUMDIR/summary.log | grep -i $1 -a'
alias sslogt='cat $SUMDIR/summary.log | tail -n $1'
alias sslogh='cat $SUMDIR/summary.log | head -n $1'
alias sslogf='tail -f $SUMDIR/summary.log'

alias llogg='cat $LOGDIR/logger.log | grep -i $1 -a'
alias llogt='cat $LOGDIR/logger.log | tail -n $1'
alias llogh='cat $LOGDIR/logger.log | head -n $1'
alias llogf='tail -f $LOGDIR/logger.log'

alias ulogg='cat $LOGDIR/ui.log | grep -i $1 -a'
alias ulogt='cat $LOGDIR/ui.log | tail -n $1'
alias ulogh='cat $LOGDIR/ui.log | head -n $1'
alias ulogf='tail -f $LOGDIR/ui.log'

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

alias wlogg='cat $WPADIR0/wpa.log | grep -i $1 -a'
alias wlogt='cat $WPADIR0/wpa.log |tail -n $1'
alias wlogh='cat $WPADIR0/wpa.log |head -n $1'
alias wlogf='tail -f $WPADIR0/wpa.log'

#alias wlogg0='cat $WPADIR0/wpa.log | grep -i $1 -a'
#alias wlogt0='cat $WPADIR0/wpa.log |tail -n $1'
#alias wlogh0='cat $WPADIR0/wpa.log |head -n $1'
#alias wlogf0='tail -f $WPADIR0/wpa.log'

alias wlogg1='cat $WPADIR1/wpa.log | grep -i $1 -a'
alias wlogt1='cat $WPADIR1/wpa.log |tail -n $1'
alias wlogh1='cat $WPADIR1/wpa.log |head -n $1'
alias wlogf1='tail -f $WPADIR1/wpa.log'

alias alogg='cat $SCANDIR0/ap.log | grep -i $1 -a'
alias alogt='cat $SCANDIR0/ap.log | tail -n $1'
alias alogh='cat $SCANDIR0/ap.log | head -n $1'
alias alogf='tail -f $SCANDIR0/ap.log'
alias blog='cat $SCANDIR0/beacon.json'
alias blogg='cat $SCANDIR0/beacon.json | grep -i $1 -a'
alias blogt='cat $SCANDIR0/beacon.json | tail -n $1'
alias blogh='cat $SCANDIR0/beacon.log | head -n $1'
alias blogf='tail -f $SCANDIR0/beacon.json'
alias flogg='cat $SCANDIR0/freq.log | grep -i $1 -a'
alias flogt='cat $SCANDIR0/freq.log | tail -n $1'
alias flogh='cat $SCANDIR0/freq.log | head -n $1'
alias flogf='tail -f $SCANDIR0/freq.log'

alias tlogg='cat $STATDIR0/stat.log | grep -i $1 -a'
alias tlogt='cat $STATDIR0/stat.log | tail -n $1'
alias tlogh='cat $STATDIR0/stat.log | head -n $1'
alias tlogf='tail -f $STATDIR0/stat.log'

alias ilog='cat $LINKDIR0/link.json'
alias ilogg='cat $LINKDIR0/link.json | grep -i $1 -a'
alias ilogt='cat $LINKDIR0/link.json | tail -n $1'
alias ilogh='cat $LINKDIR0/link.json | head -n $1'
alias ilogf='tail -f $LINKDIR0/link.json'

alias tlogg1='cat $STATDIR1/stat.log | grep -i $1 -a'
alias tlogt1='cat $STATDIR1/stat.log | tail -n $1'
alias tlogh1='cat $STATDIR1/stat.log | head -n $1'
alias tlogf1='tail -f $STATDIR1/stat.log'

alias ilog1='cat $LINKDIR1/link.json'
alias ilogg1='cat $LINKDIR1/link.json | grep -i $1 -a'
alias ilogt1='cat $LINKDIR1/link.json | tail -n $1'
alias ilogh1='cat $LINKDIR1/link.json | head -n $1'
alias ilogf1='tail -f $LINKDIR1/link.json'

alias alogg0='cat $SCANDIR0/ap.log | grep -i $1 -a'
alias alogt0='cat $SCANDIR0/ap.log | tail -n $1'
alias alogh0='cat $SCANDIR0/ap.log | head -n $1'
alias alogf0='tail -f $SCANDIR0/ap.log'
alias blog0='cat $SCANDIR0/beacon.json'
alias blogg0='cat $SCANDIR0/beacon.json | grep -i $1 -a'
alias blogt0='cat $SCANDIR0/beacon.json | tail -n $1'
alias blogh0='cat $SCANDIR0/beacon.json | head -n $1'
alias blogf0='tail -f $SCANDIR0/beacon.json'
alias flogg0='cat $SCANDIR0/freq.log | grep -i $1 -a'
alias flogt0='cat $SCANDIR0/freq.log | tail -n $1'
alias flogh0='cat $SCANDIR0/freq.log | head -n $1'
alias flogf0='tail -f $SCANDIR0/freq.log'

alias alogg1='cat $SCANDIR1/ap.log | grep -i $1 -a'
alias alogt1='cat $SCANDIR1/ap.log | tail -n $1'
alias alogh1='cat $SCANDIR1/ap.log | head -n $1'
alias alogf1='tail -f $SCANDIR1/ap.log'
alias blog1='cat $SCANDIR1/beacon.json'
alias blogg1='cat $SCANDIR1/beacon.json | grep -i $1 -a'
alias blogt1='cat $SCANDIR1/beacon.json | tail -n $1'
alias blogh1='cat $SCANDIR1/beacon.json | head -n $1'
alias blogf1='tail -f $SCANDIR1/beacon.log'
alias flogg1='cat $SCANDIR1/freq.log | grep -i $1 -a'
alias flogt1='cat $SCANDIR1/freq.log | tail -n $1'
alias flogh1='cat $SCANDIR1/freq.log | head -n $1'
alias flogf1='tail -f $SCANDIR1/freq.log'

alias psg='ps -ef |grep -i $1'
alias sdr='systemctl daemon-reload'
alias sst='systemctl status $1'
alias sss='systemctl start $1'
alias ssp='systemctl stop $1'
alias ssr='systemctl restart $1'
alias sse='systemctl enable $1'
alias ssd='systemctl disable $1'
alias ssg='systemctl |grep -i $1'
alias camchk1='i2ctransfer -f -y 1 w2@0x48 0x00 0x13 r1'
alias camchk2='i2ctransfer -f -y 2 w2@0x48 0x00 0x13 r1'
alias jo='journalctl -o short-iso-precise'

