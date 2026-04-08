#!/bin/bash
set -e

# Check if source directories exist
if [ ! -d /usr/local/scripts ]; then
    echo "Error: Source directory /usr/local/scripts does not exist"
    exit 1
fi

if [ ! -d /usr/local/logger ]; then
    echo "Error: Source directory /usr/local/logger does not exist"
    exit 1
fi

# Check if destination directories exist
if [ ! -d dist/wlan/usr/local/scripts ]; then
    echo "Error: Destination directory dist/wlan/usr/local/scripts does not exist"
    exit 1
fi

if [ ! -d dist/wlan/usr/local/logger ]; then
    echo "Error: Destination directory dist/wlan/usr/local/logger does not exist"
    exit 1
fi

echo "Copying files from /usr/local/scripts to dist/wlan/usr/local/scripts..."
cp -a /usr/local/scripts/. dist/wlan/usr/local/scripts/

echo "Copying files from /usr/local/logger to dist/wlan/usr/local/logger..."
cp -a /usr/local/logger/. dist/wlan/usr/local/logger/

echo "Update completed successfully"
