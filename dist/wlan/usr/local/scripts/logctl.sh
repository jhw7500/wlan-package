#!/bin/bash

systemctl $1 logger_ap
systemctl $1 logger_scan
systemctl $1 logger_cap
systemctl $1 looger_link
