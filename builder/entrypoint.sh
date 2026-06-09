#!/usr/bin/env bash

# Gather and execute parts from ~/.entrypoint.d/ in alphabetical (numeric prefix) order.
# - .sh  files are sourced so they share this process and can set variables used below.
# - .txt files are printed to stdout (useful for banners or license notices).
# The folder is searched in $HOME of the active user. Only IMAGE_MAIN_USER will have it
# (the Dockerfile copies entrypoint.d/ into that user's home during build).
# For any other user the directory won't exist and we fall through to a plain exec "$@".
shopt -s nullglob extglob
_ENTRYPOINT_DIR="${HOME}/.entrypoint.d"

if [ ! -d "${_ENTRYPOINT_DIR}" ]; then
    exec "$@"
fi

declare -a _PARTS=( "${_ENTRYPOINT_DIR}"/*@(.txt|.sh) )
shopt -u nullglob extglob

for _file in "${_PARTS[@]}"; do
    case "${_file}" in
        *.txt) cat "${_file}" ;;
        *.sh)  source "${_file}" ;;
    esac
done
