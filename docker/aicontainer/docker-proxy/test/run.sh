#!/bin/sh
set -eu

dp_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
test_dir=$(mktemp -d)
trap 'rm -rf "$test_dir"' EXIT
trap 'exit 1' HUP INT TERM

# Go tests need to share a directory with the package's unexported functions.
cp "$dp_dir"/src/*.go "$dp_dir"/test/*_test.go "$test_dir"/
go test "$@" "$test_dir"/*.go
