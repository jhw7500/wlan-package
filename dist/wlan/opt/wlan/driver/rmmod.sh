#!/bin/bash
ifconfig mlan0 down
ifconfig mlan1 down
rmmod moal
rmmod mlan
