#!/bin/bash
FILE_NAME='wlan.deb'
sshpass -p 'jhw' rsync -av --progress \
-e 'ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null' \
jhw@192.168.0.2:/home/jhw/ai/opencode/projects/wlan-package/release/$FILE_NAME .
