#!/bin/bash

pattern=$1
file_path=$2

if [ ! -f "$file_path" ]; then
    echo "File not found: $file_path"
    exit 1
fi

echo "Pattern to check: '$pattern', File: $file_path"

escaped_pattern=$(echo "$pattern" | sed 's/[&/\]/\\&/g')

if grep -qF "$pattern" "$file_path"; then
    echo "Line exists"
    #sed -i "/$escaped_pattern/d" "$file_path"
    #echo "Removed line: '$pattern' from the file: $file_path"
else
    echo "Line does not exist"
    printf '%s\n' "$pattern" >> "$file_path"
    echo "Added line: '$pattern' to the file: $file_path"
fi
