package main

import (
	"syscall"
	"unsafe"
)

// dp is built for Linux containers. Using the kernel's terminal interface keeps
// the executable independent of external Go modules and helper processes.
func terminalState(fd uintptr) (*syscall.Termios, error) {
	var state syscall.Termios
	_, _, errno := syscall.Syscall(syscall.SYS_IOCTL, fd, syscall.TCGETS, uintptr(unsafe.Pointer(&state)))
	if errno != 0 {
		return nil, errno
	}
	return &state, nil
}

func setTerminalState(fd uintptr, state *syscall.Termios) error {
	_, _, errno := syscall.Syscall(syscall.SYS_IOCTL, fd, syscall.TCSETS, uintptr(unsafe.Pointer(state)))
	if errno != 0 {
		return errno
	}
	return nil
}

func isTerminal(fd uintptr) bool {
	_, err := terminalState(fd)
	return err == nil
}

func makeRaw(fd uintptr) (func(), error) {
	previous, err := terminalState(fd)
	if err != nil {
		return nil, err
	}
	raw := *previous
	raw.Iflag &^= syscall.IGNBRK | syscall.BRKINT | syscall.PARMRK | syscall.ISTRIP | syscall.INLCR | syscall.IGNCR | syscall.ICRNL | syscall.IXON
	raw.Oflag &^= syscall.OPOST
	raw.Lflag &^= syscall.ECHO | syscall.ECHONL | syscall.ICANON | syscall.ISIG | syscall.IEXTEN
	raw.Cflag &^= syscall.CSIZE | syscall.PARENB
	raw.Cflag |= syscall.CS8
	raw.Cc[syscall.VMIN] = 1
	raw.Cc[syscall.VTIME] = 0
	if err := setTerminalState(fd, &raw); err != nil {
		return nil, err
	}
	return func() { _ = setTerminalState(fd, previous) }, nil
}

func terminalSize(fd uintptr) (rows, columns uint16) {
	var size struct{ Rows, Columns, X, Y uint16 }
	_, _, errno := syscall.Syscall(syscall.SYS_IOCTL, fd, syscall.TIOCGWINSZ, uintptr(unsafe.Pointer(&size)))
	if errno != 0 || size.Rows == 0 {
		size.Rows = 24
	}
	if errno != 0 || size.Columns == 0 {
		size.Columns = 80
	}
	return size.Rows, size.Columns
}
