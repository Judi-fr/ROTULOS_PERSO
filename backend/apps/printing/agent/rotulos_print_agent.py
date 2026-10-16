#!/usr/bin/env python3
"""Agente de impresión de Rótulos — corre en una PC de la red de tus impresoras.

Le pide a la app los trabajos que mandaste a imprimir, los manda a la impresora
y avisa cómo salió. Solo necesita Python 3.8 o más nuevo (sin instalar nada
más) y el token que te dio la pantalla "Impresoras" al crear el agente.

    python3 rotulos_print_agent.py --server https://tu-dominio/api/v1 --token rpa_...

También toma el token de la variable ROTULOS_AGENT_TOKEN y el servidor de
ROTULOS_SERVER. Dejalo corriendo: consulta cada pocos segundos. Ctrl+C lo frena.

Cómo imprime, según lo que configuraste para cada impresora:
- "En la red": le manda el archivo directo a IP:puerto (las térmicas Zebra y
  muchas de red escuchan en el 9100). Funciona igual en Windows, Mac y Linux.
- "Instalada en esta PC": en Linux/Mac usa `lp` (CUPS). En Windows, ZPL va
  crudo a la impresora compartida (`copy /b`); PDF necesita SumatraPDF en el
  PATH. ROTULOS_PRINT_COMMAND reemplaza todo eso por tu propio comando, con
  {file} y {printer} (p. ej. 'lp -d {printer} {file}').
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

VERSION = "1.0.0"
NETWORK_TIMEOUT_SECONDS = 20
COMMAND_TIMEOUT_SECONDS = 120


class PrintFailed(Exception):
    """La impresora (o el comando) no aceptó el trabajo; el mensaje va a la app."""


class AgentRevoked(Exception):
    """El token no sirve: no tiene sentido seguir consultando."""


# --- Hablar con la app ----------------------------------------------------------


class Api:
    def __init__(self, server, token):
        self.server = server.rstrip("/")
        self.headers = {
            "Authorization": f"Agent {token}",
            "X-Agent-Version": f"{VERSION} {platform.system()} {platform.release()}"[:60],
            "Accept": "application/json",
        }

    def _request(self, method, path, body=None):
        data = None
        headers = dict(self.headers)
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(f"{self.server}{path}", data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=NETWORK_TIMEOUT_SECONDS) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            if error.code == 401:
                raise AgentRevoked(error.read().decode(errors="replace")) from error
            return error.code, error.read()

    def next_job(self):
        status, content = self._request("GET", "/printing/agent/jobs/next/")
        if status == 204:
            return None
        if status != 200:
            raise RuntimeError(f"La app respondió {status} al pedir trabajos: {content[:300]!r}")
        return json.loads(content)

    def job_file(self, job_id):
        status, content = self._request("GET", f"/printing/agent/jobs/{job_id}/file/")
        if status != 200:
            raise PrintFailed(f"No se pudo bajar el archivo (la app respondió {status}).")
        return content

    def report(self, job_id, ok, error=""):
        self._request("POST", f"/printing/agent/jobs/{job_id}/result/", {"ok": ok, "error": error[:2000]})


# --- Mandar a la impresora ------------------------------------------------------


def send_to_network(host, port, content):
    try:
        with socket.create_connection((host, int(port)), timeout=NETWORK_TIMEOUT_SECONDS) as connection:
            connection.sendall(content)
            connection.shutdown(socket.SHUT_WR)
    except OSError as error:
        raise PrintFailed(f"No se pudo conectar con la impresora en {host}:{port} ({error}).") from error


def system_command(file_path, printer_name, file_format, system=None):
    """El comando para imprimir ``file_path`` en la cola ``printer_name``."""
    custom = os.environ.get("ROTULOS_PRINT_COMMAND")
    if custom:
        return [part.format(file=file_path, printer=printer_name) for part in shlex.split(custom)]
    system = system or platform.system()
    if system == "Windows":
        if file_format == "zpl":
            target = printer_name if printer_name.startswith("\\\\") else f"\\\\localhost\\{printer_name}"
            return ["cmd", "/c", "copy", "/b", file_path, target]
        sumatra = shutil.which("SumatraPDF") or shutil.which("SumatraPDF.exe")
        if not sumatra:
            raise PrintFailed(
                "Para imprimir PDF en Windows instalá SumatraPDF y agregalo al PATH "
                "(o definí ROTULOS_PRINT_COMMAND)."
            )
        return [sumatra, "-print-to", printer_name, "-silent", file_path]
    command = ["lp", "-d", printer_name]
    if file_format == "zpl":
        command += ["-o", "raw"]
    return command + [file_path]


def send_to_system(printer_name, content, file_format):
    suffix = ".zpl" if file_format == "zpl" else ".pdf"
    handle, file_path = tempfile.mkstemp(prefix="rotulos-", suffix=suffix)
    try:
        with os.fdopen(handle, "wb") as temp:
            temp.write(content)
        command = system_command(file_path, printer_name, file_format)
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise PrintFailed(f"No se pudo ejecutar {command[0]}: {error}") from error
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise PrintFailed(f"{command[0]} terminó con error {result.returncode}: {detail[:500]}")
    finally:
        try:
            os.remove(file_path)
        except OSError:
            pass


def print_job(job, content):
    printer = job["printer"]
    if printer["connection"] == "network":
        send_to_network(printer["host"], printer["port"], content)
    else:
        send_to_system(printer["queue"], content, job["format"])


# --- El ciclo -------------------------------------------------------------------


def log(message):
    print(time.strftime("%H:%M:%S"), message, flush=True)


def handle_one(api):
    """Procesa un trabajo si hay. Devuelve si hubo."""
    job = api.next_job()
    if job is None:
        return False
    name = job["printer"]["name"]
    log(f"Trabajo {job['id']}: {job['label_count']} rótulo(s) a «{name}»...")
    try:
        print_job(job, api.job_file(job["id"]))
    except PrintFailed as error:
        log(f"  Error: {error}")
        api.report(job["id"], False, str(error))
    else:
        log("  Listo.")
        api.report(job["id"], True)
    return True


def run(api, interval, once=False):
    log(f"Agente de impresión {VERSION} conectado a {api.server}. Esperando trabajos...")
    failures = 0
    while True:
        try:
            had_job = handle_one(api)
            failures = 0
        except AgentRevoked:
            log("La app rechazó el token (¿lo revocaste?). Generá uno nuevo en Impresoras.")
            return 2
        except (urllib.error.URLError, OSError, RuntimeError, ValueError) as error:
            failures += 1
            had_job = False
            log(f"No se pudo hablar con la app ({error}). Reintento en un momento.")
        if once:
            return 0
        if not had_job:
            # Si la app no responde, espera cada vez más (hasta un minuto).
            time.sleep(min(60, interval * (2 ** min(failures, 4))) if failures else interval)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Agente de impresión de Rótulos.")
    parser.add_argument("--server", default=os.environ.get("ROTULOS_SERVER", ""), help="URL de la API, terminada en /api/v1")
    parser.add_argument("--token", default=os.environ.get("ROTULOS_AGENT_TOKEN", ""), help="Token del agente (rpa_...)")
    parser.add_argument("--interval", type=float, default=3.0, help="Segundos entre consultas (3)")
    parser.add_argument("--once", action="store_true", help="Consultar una sola vez y salir")
    args = parser.parse_args(argv)
    if not args.server or not args.token:
        parser.error("faltan --server y --token (o ROTULOS_SERVER / ROTULOS_AGENT_TOKEN)")
    try:
        return run(Api(args.server, args.token), args.interval, args.once)
    except KeyboardInterrupt:
        log("Agente detenido.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
