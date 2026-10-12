import http.server
import os
from pathlib import Path
import re
import socket
import threading
from urllib.parse import unquote


def get_local_ip() -> str:
    """Fetch machine's LAN IP address."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Dummy connect to determine primary local network route
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


class RangeRequestHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP handler supporting HTTP 206 Partial Content (Byte Ranges) for video seeking."""

    def translate_path(self, path: str) -> str:
        # Decode %20 and special characters properly
        path = unquote(path)
        return super().translate_path(path)

    def send_head(self):
        """Handle Range requests cleanly for instant video seek."""
        path = self.translate_path(self.path)
        if os.path.isdir(path):
            return super().send_head()

        try:
            f = open(path, "rb")
        except OSError:
            self.send_error(404, "File not found")
            return None

        range_header = self.headers.get("Range")
        if not range_header:
            return super().send_head()

        try:
            fs = os.fstat(f.fileno())
            file_len = fs.st_size
            m = re.match(r"bytes=(\d+)-(\d*)", range_header)
            if not m:
                return super().send_head()

            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else file_len - 1

            if start >= file_len or end >= file_len or start > end:
                self.send_error(416, "Requested Range Not Satisfiable")
                f.close()
                return None

            length = (end - start) + 1
            self.send_response(206)
            self.send_header("Content-Type", self.guess_type(path))
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_len}")
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()

            f.seek(start)
            # Wrap file object to limit read bytes
            return RangeFileWrapper(f, length)
        except Exception:
            f.close()
            raise

    def log_message(self, format, *args):
        # Silence console log spam during video streaming
        pass


class RangeFileWrapper:
    """Wrapper that emits exactly the requested byte slice."""
    def __init__(self, file_obj, length: int):
        self.file_obj = file_obj
        self.remaining = length

    def read(self, size=-1):
        if self.remaining <= 0:
            return b""
        read_size = self.remaining if size < 0 else min(size, self.remaining)
        chunk = self.file_obj.read(read_size)
        self.remaining -= len(chunk)
        return chunk

    def close(self):
        self.file_obj.close()


class LANMediaServer:
    def __init__(self, port: int = 8080):
        self.port = port
        self.serving_dir = None
        self.server = None
        self.thread = None
        self.is_running = False

    def start(self, directory: Path):
        self.stop()
        self.serving_dir = Path(directory).resolve()

        class DirectoryHandler(RangeRequestHandler):
            def __init__(handler_self, *args, **kwargs):
                super().__init__(*args, directory=str(self.serving_dir), **kwargs)

        self.server = http.server.ThreadingHTTPServer(("0.0.0.0", self.port), DirectoryHandler)
        self.server.timeout = 2.0
        self.server.daemon_threads = True

        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.is_running = True

    def stop(self):
        if self.server:
            try:
                self.server.shutdown()
                self.server.server_close()
            except Exception:
                pass
        self.server = None
        self.thread = None
        self.is_running = False


lan_server = LANMediaServer()
