"""Exercise the trusted launcher relay with real Unix sockets, without Docker/WSL."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import tempfile
import threading
import time
import unittest


RELAY = Path(__file__).resolve().parents[1] / "wsl-containers/wsl-docker-relay/docker-socket-relay.cjs"


def receive_all(connection):
    chunks = []
    while chunk := connection.recv(65536):
        chunks.append(chunk)
    return b"".join(chunks)


@unittest.skipUnless(shutil.which("node"), "Node.js is required for the Unix socket relay")
class WslDockerRelayTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="relay-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.upstream = str(self.root / "upstream.sock")
        self.path = str(self.root / "relay.sock")
        self.errors = []

    def listen(self, handler):
        listener = socket.socket(socket.AF_UNIX)
        listener.bind(self.upstream)
        listener.listen()
        listener.settimeout(0.05)
        stopped = threading.Event()
        workers = []

        def handle(connection):
            with connection:
                connection.settimeout(5)
                try:
                    handler(connection)
                except Exception as error:
                    self.errors.append(error)

        def accept():
            while not stopped.is_set():
                try:
                    connection, _ = listener.accept()
                except socket.timeout:
                    continue
                worker = threading.Thread(target=handle, args=(connection,), daemon=True)
                workers.append(worker)
                worker.start()

        thread = threading.Thread(target=accept, daemon=True)
        thread.start()

        def cleanup():
            stopped.set()
            thread.join(2)
            listener.close()
            for worker in workers:
                worker.join(6)
                self.assertFalse(worker.is_alive(), "Upstream connection did not close")
            self.assertEqual(self.errors, [])

        self.addCleanup(cleanup)

    def start_relay(self):
        process = subprocess.Popen(
            ["node", str(RELAY), "serve", self.path, self.upstream],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

        def cleanup():
            if process.poll() is None:
                process.terminate()
            try:
                process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                self.fail("Relay failed to stop")

        self.addCleanup(cleanup)
        deadline = time.monotonic() + 3
        while not Path(self.path).exists():
            if process.poll() is not None:
                self.fail(process.communicate()[1].decode())
            self.assertLess(time.monotonic(), deadline, "Relay did not listen")
            time.sleep(0.01)
        return process

    def connect(self):
        connection = socket.socket(socket.AF_UNIX)
        connection.settimeout(5)
        connection.connect(self.path)
        self.addCleanup(connection.close)
        return connection

    def check(self, timeout="300"):
        return subprocess.run(
            ["node", str(RELAY), "check", self.path, timeout],
            capture_output=True, text=True, timeout=4,
        )

    def test_ping_reaches_engine_and_socket_is_private(self):
        requests = []

        def handler(connection):
            requests.append(connection.recv(4096))
            connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK")

        self.listen(handler)
        self.start_relay()
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(requests), 1)
        self.assertTrue(requests[0].startswith(b"GET /_ping HTTP/1.1\r\n"))
        self.assertEqual(stat.S_IMODE(Path(self.path).stat().st_mode), 0o600)

    def test_hijacked_binary_stream_preserves_half_close_and_backpressure(self):
        payload = bytes(range(256)) * 16384
        upgrade = b"HTTP/1.1 101 UPGRADED\r\nConnection: Upgrade\r\nUpgrade: tcp\r\n\r\n"

        def handler(connection):
            request = b""
            while not request.endswith(b"\r\n\r\n"):
                request += connection.recv(1)
            self.assertIn(b"POST /containers/test/attach", request)
            connection.sendall(upgrade)
            data = receive_all(connection)
            self.assertEqual(data, payload)
            # Send output after the client has closed stdin, like Docker attach.
            connection.sendall(data[::-1])

        self.listen(handler)
        self.start_relay()
        connection = self.connect()
        connection.sendall(b"POST /containers/test/attach HTTP/1.1\r\nConnection: Upgrade\r\n\r\n")
        response = b""
        while not response.endswith(b"\r\n\r\n"):
            response += connection.recv(1)
        self.assertEqual(response, upgrade)
        connection.sendall(payload)
        connection.shutdown(socket.SHUT_WR)
        self.assertEqual(receive_all(connection), payload[::-1])

    def test_concurrent_clients_do_not_mix_streams(self):
        self.listen(lambda connection: connection.sendall(receive_all(connection)[::-1]))
        self.start_relay()

        def round_trip(index):
            data = str(index).encode() * 10000
            with socket.socket(socket.AF_UNIX) as connection:
                connection.settimeout(5)
                connection.connect(self.path)
                connection.sendall(data)
                connection.shutdown(socket.SHUT_WR)
                self.assertEqual(receive_all(connection), data[::-1])

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(round_trip, range(8)))

    def test_missing_upstream_closes_client_and_does_not_crash_relay(self):
        process = self.start_relay()
        connection = self.connect()
        self.assertEqual(connection.recv(1), b"")
        self.assertIsNone(process.poll())
        result = self.check()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Docker relay check failed", result.stderr)
        self.assertIsNone(process.poll())

    def test_readiness_rejects_wrong_service(self):
        def handler(connection):
            connection.recv(4096)
            connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nNO")

        self.listen(handler)
        self.start_relay()
        result = self.check()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("did not return OK", result.stderr)

    def test_termination_closes_active_connections_and_removes_socket(self):
        accepted = threading.Event()

        def handler(connection):
            accepted.set()
            self.assertEqual(receive_all(connection), b"")

        self.listen(handler)
        process = self.start_relay()
        connection = self.connect()
        self.assertTrue(accepted.wait(2))
        process.terminate()
        process.wait(timeout=3)
        self.assertEqual(process.returncode, 0)
        self.assertEqual(connection.recv(1), b"")
        self.assertFalse(Path(self.path).exists())

    def test_existing_socket_is_not_unlinked(self):
        self.start_relay()
        inode = Path(self.path).stat().st_ino
        result = subprocess.run(
            ["node", str(RELAY), "serve", self.path, self.upstream],
            capture_output=True, text=True, timeout=3,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already exists", result.stderr)
        self.assertEqual(Path(self.path).stat().st_ino, inode)


if __name__ == "__main__":
    unittest.main()
