#!/bin/bash

new_line="$1"

pattern="$2"

file_path="$3"

if [ ! -f "$file_path" ]; then
    echo "File not found: $file_path"
    exit 1
fi

if grep -q "$pattern" "$file_path"; then
    echo "Replaced line matching pattern '$pattern' with '$new_line' in the file: $file_path"
    sed -i "s?.*$pattern.*?$new_line?" "$file_path"
else
    echo "Pattern '$pattern' not found in the file: $file_path"
    /opt/pim/bin/add_line.sh "$new_line" "$3"
fi

