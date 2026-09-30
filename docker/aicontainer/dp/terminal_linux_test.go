package main

import (
	"fmt"
	"os"
	"syscall"
	"testing"
	"unsafe"
)

func TestTerminalRawModeAndRestore(t *testing.T) {
	master, err := os.OpenFile("/dev/ptmx", os.O_RDWR|syscall.O_NOCTTY, 0)
	if err != nil {
		t.Skipf("PTY unavailable: %v", err)
	}
	defer master.Close()
	var unlocked int32
	if _, _, errno := syscall.Syscall(syscall.SYS_IOCTL, master.Fd(), syscall.TIOCSPTLCK, uintptr(unsafe.Pointer(&unlocked))); errno != 0 {
		t.Fatal(errno)
	}
	var number uint32
	if _, _, errno := syscall.Syscall(syscall.SYS_IOCTL, master.Fd(), syscall.TIOCGPTN, uintptr(unsafe.Pointer(&number))); errno != 0 {
		t.Fatal(errno)
	}
	slave, err := os.OpenFile(fmt.Sprintf("/dev/pts/%d", number), os.O_RDWR|syscall.O_NOCTTY, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer slave.Close()
	if !isTerminal(slave.Fd()) {
		t.Fatal("PTY was not recognized as a terminal")
	}
	before, err := terminalState(slave.Fd())
	if err != nil {
		t.Fatal(err)
	}
	restore, err := makeRaw(slave.Fd())
	if err != nil {
		t.Fatal(err)
	}
	defer restore()
	raw, err := terminalState(slave.Fd())
	if err != nil {
		t.Fatal(err)
	}
	if raw.Lflag&(syscall.ICANON|syscall.ECHO|syscall.ISIG) != 0 || raw.Oflag&syscall.OPOST != 0 || raw.Cc[syscall.VMIN] != 1 {
		t.Fatalf("terminal is not in raw mode: %+v", raw)
	}
	restore()
	after, err := terminalState(slave.Fd())
	if err != nil {
		t.Fatal(err)
	}
	if *before != *after {
		t.Fatalf("terminal state not restored: before=%+v, after=%+v", before, after)
	}
	size := struct{ Rows, Columns, X, Y uint16 }{Rows: 45, Columns: 120}
	if _, _, errno := syscall.Syscall(syscall.SYS_IOCTL, slave.Fd(), syscall.TIOCSWINSZ, uintptr(unsafe.Pointer(&size))); errno != 0 {
		t.Fatal(errno)
	}
	if rows, columns := terminalSize(slave.Fd()); rows != 45 || columns != 120 {
		t.Fatalf("terminal size = %dx%d, want 45x120", rows, columns)
	}
}

func TestNonTerminal(t *testing.T) {
	file, err := os.Open(os.DevNull)
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	if isTerminal(file.Fd()) {
		t.Fatal("/dev/null was recognized as a terminal")
	}
	if rows, columns := terminalSize(file.Fd()); rows != 24 || columns != 80 {
		t.Fatalf("fallback size = %dx%d, want 24x80", rows, columns)
	}
}
