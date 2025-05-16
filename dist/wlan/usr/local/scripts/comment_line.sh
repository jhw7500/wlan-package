#!/bin/bash

new_line="$1"

file_path="$2"

if [ ! -f "$file_path" ]; then
    echo "File not found: $file_path"
    exit 1
fi

if ! grep -q "^#*$new_line" "$file_path"; then
    sed -i -e "?$new_line?s?^?#?" "$file_path"
    echo "Added '#' in front of '$1' in the file: $file_path"
else
    echo "'$new_line' already comment line"
fi
