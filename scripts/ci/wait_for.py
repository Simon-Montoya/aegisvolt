"""Espera a que un host:puerto acepte conexiones (p. ej. el servicio floci del pipeline)."""
import socket
import sys
import time

host, port, timeout = sys.argv[1], int(sys.argv[2]), float(sys.argv[3] if len(sys.argv) > 3 else 60)
deadline = time.time() + timeout
while time.time() < deadline:
    with socket.socket() as s:
        s.settimeout(2)
        if s.connect_ex((host, port)) == 0:
            print(f"{host}:{port} listo")
            sys.exit(0)
    time.sleep(1)
sys.exit(f"{host}:{port} no respondió en {timeout}s")
