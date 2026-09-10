#!/bin/bash

# === Path Configuration ===
out_file="$1"
srctree="$2"
overlay_dir=$(echo "$srctree" | sed 's|//|/|g')

if [[ "$overlay_dir" == ..* ]]; then
    overlay_dir="$overlay_dir"
else
    overlay_dir=$(echo "$overlay_dir" | sed 's|\(.*\)\1|\1|')
fi

overlay_dir=$(echo "$overlay_dir" | sed 's|/modules$||; s|$|/modules|')

> "$out_file"

# === Write File Header ===
cat <<'EOF' > "$out_file"
#include "overlay_files.h"
#include <linux/stddef.h>
#include <linux/zstd.h>

EOF

# === Associative Arrays for Storing Info ===
declare -A name_map    # name -> array_name
declare -A count_map   # name -> element_count
declare -A orig_size_map   # name -> original_size

# === Temporary File ===
tmp_xxd="/tmp/overlay_xxd_$$.c"
tmp_comp="/tmp/overlay_comp_$$.ko"

# === Process All .ko Files ===
shopt -s nullglob
ko_files=("$overlay_dir"/*.ko)
file_idx=0

for ko in "${ko_files[@]}"; do
    [ -f "$ko" ] || continue

    base=$(basename "$ko")
    name="${base%.ko}" # Remove .ko extension
    # The kernel build system (scripts/Makefile.lib:123) replaces hyphens with underscores in module names,
    # and performs the same process here to maintain consistency with info->name.
    name="${name//-/_}"
    array_name="${name//[^a-zA-Z0-9]/_}_data" # Valid C identifier
    orig_size=$(stat -c%s "$ko") # Get the original size

    # Use zstd for maximum compression
    if ! /usr/bin/zstd -22 -f "$ko" -o "$tmp_comp"; then
        echo "zstd compression failed: $ko" >&2
        continue
    fi

    # Generate byte array
    if ! /usr/bin/xxd -i "$tmp_comp" > "$tmp_xxd.raw" 2>/dev/null; then
        echo "xxd failed: $ko" >&2
        continue
    fi

    len_line=$(grep -E 'unsigned int[[:space:]]+.*_len[[:space:]]*=' "$tmp_xxd.raw")
    count=$(echo "$len_line" | awk -F'=' '{print $2}' | awk -F';' '{print $1}' | tr -d ' ')

    if [ -z "$count" ]; then
        echo "Failed to extract _len from xxd output: $ko" >&2
        rm -f "$tmp_xxd.raw"
        continue
    fi

    # Replacements:
    # 1. Replace the array definition line
    # 2. Convert 0x... to 0x...U
    # 3. Remove the _len definition line
    sed -E \
        -e "s/unsigned char[[:space:]]+([^[]+)[[:space:]]*\[([^]]*)\]/const unsigned char ${array_name}[\2]/" \
        -e 's/0x([0-9a-fA-F]{2})/0x\1U/g' \
        -e 's/-/_/g' \
        -e '/unsigned int[[:space:]]+.*_len[[:space:]]*=/d' \
        "$tmp_xxd.raw" > "$tmp_xxd" || { echo "sed failed: $ko" >&2; rm -f "$tmp_xxd.raw"; continue; }

    rm -f "$tmp_xxd.raw" "$tmp_comp"

    ((file_idx++))

    # Record
    name_map["$name"]="$array_name"
    count_map["$name"]=$count
    orig_size_map["$name"]=$orig_size

    # Append array definition to output file
    cat "$tmp_xxd" >> "$out_file"
    echo >> "$out_file"
done

# === Generate overlay_file_list array ===
cat <<EOF >> "$out_file"
// Descriptor table for all overlay modules
const struct overlay_file overlay_file_list[] = {
EOF

if (( file_idx == 0 )); then
    echo "No .ko files found in $overlay_dir"
else
    for name in "${!name_map[@]}"; do
        array_name="${name_map[$name]}"
        count="${count_map[$name]}"
        printf '    { .name = "%s", .data = %s, .len = %d, .orig_size = %d },\n' \
               "$name" "$array_name" "$count" "$orig_size" >> "$out_file"
    done
fi

cat <<EOF >> "$out_file"
};

const int overlay_file_list_count = $file_idx;

EOF

# === Clean up temporary files ===
rm -f "$tmp_xxd"

# === Uniform line breaks are LF ===
if command -v dos2unix >/dev/null 2>&1; then
    dos2unix "$out_file" 2>/dev/null || true
else
    tr -d '\r' < "$out_file" > "${out_file}.tmp" 2>/dev/null && \
        mv "${out_file}.tmp" "$out_file"
fi

echo "Generated $out_file: $file_idx overlay file(s) processed."