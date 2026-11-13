"""Local UI preview over existing snapshots; never initializes or operates MT5."""
import os
from http.server import ThreadingHTTPServer
from .tracker import Handler

if __name__=='__main__':
    server=ThreadingHTTPServer(('127.0.0.1',8766),Handler)
    print(f'Dashboard preview: http://127.0.0.1:8766 | PID {os.getpid()}',flush=True)
    try: server.serve_forever()
    finally: server.server_close()
