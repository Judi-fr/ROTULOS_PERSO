#!/usr/bin/env python3
"""Agente de impresión de Rótulos — corre en una PC de la red de tus impresoras.

Le pide a la app los trabajos que mandaste a imprimir, los manda a la impresora
y avisa cómo salió. Solo necesita Python 3.8 o más nuevo (sin instalar nada
más) y el token que te dio la pantalla "Impresoras" al crear el agente.

    python3 rotulos_print_agent.py --server https://tu-dominio/api/v1 --token rpa_...

También toma el token de la variable ROTULOS_AGENT_TOKEN y el servidor de
ROTULOS_SERVER, o de un archivo rotulos_agent.json ({"server": ..., "token":
...}, el que baja la pantalla "Impresoras") puesto al lado del programa o en
la carpeta de configuración del usuario. Si no encuentra ninguno, los pregunta
y los guarda ahí: así el .exe de Windows anda con doble clic.

En Windows, --install-startup lo deja arrancando solo al prender la PC
(--remove-startup lo saca). Dejalo corriendo: consulta cada pocos segundos.
Ctrl+C lo frena.

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

VERSION = "1.1.0"
CONFIG_NAME = "rotulos_agent.json"
STARTUP_NAME = "RotulosAgente.bat"
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


# --- Configuración (para el .exe, que se abre con doble clic) -------------------


def frozen():
    """Si corre empaquetado (el .exe hecho con PyInstaller)."""
    return bool(getattr(sys, "frozen", False))


def program_path():
    return os.path.abspath(sys.executable if frozen() else __file__)


def user_config_dir(system=None, environ=None):
    system = system or platform.system()
    environ = os.environ if environ is None else environ
    if system == "Windows":
        base = environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
        return os.path.join(base, "Rotulos")
    base = environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "rotulos")


def config_candidates(explicit=""):
    """Dónde se busca rotulos_agent.json, en orden."""
    paths = [explicit, os.environ.get("ROTULOS_AGENT_CONFIG", "")]
    paths.append(os.path.join(os.path.dirname(program_path()), CONFIG_NAME))
    paths.append(os.path.join(user_config_dir(), CONFIG_NAME))
    return [path for path in paths if path]


def load_config(explicit=""):
    """``(datos, ruta)`` del primer archivo que exista, o ``({}, None)``."""
    for path in config_candidates(explicit):
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError) as error:
            raise SystemExit(f"No se pudo leer {path}: {error}")
        if not isinstance(data, dict):
            raise SystemExit(f"{path} no tiene el formato esperado.")
        return data, path
    return {}, None


def save_config(data, path=None):
    path = path or os.path.join(user_config_dir(), CONFIG_NAME)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    return path


def ask_config(read=None):
    """Pide servidor y token por consola (primera vez con doble clic)."""
    read = read or input
    print("Configurá el agente con los datos de la pantalla «Impresoras» de la app.")
    server = read("Servidor (termina en /api/v1): ").strip()
    token = read("Token del agente (rpa_...): ").strip()
    return {"server": server, "token": token}


# --- Arrancar con Windows -------------------------------------------------------


def startup_dir(environ=None):
    environ = os.environ if environ is None else environ
    base = environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    return os.path.join(base, "Microsoft", "Windows", "Start Menu", "Programs", "Startup")


def startup_script(program=None):
    """El .bat de la carpeta Inicio: abre el agente minimizado. Con el .py, lo
    corre con el mismo Python que lo está corriendo ahora."""
    program = program or program_path()
    if program.lower().endswith(".exe"):
        command = f'start "Rotulos" /min "{program}"'
    else:
        command = f'start "Rotulos" /min "{sys.executable}" "{program}"'
    return f"@echo off\r\n{command}\r\n"


def install_startup(environ=None, program=None):
    folder = startup_dir(environ)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, STARTUP_NAME)
    with open(path, "w", encoding="ascii", errors="replace", newline="") as handle:
        handle.write(startup_script(program))
    return path


def remove_startup(environ=None):
    path = os.path.join(startup_dir(environ), STARTUP_NAME)
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


def pause_if_double_clicked():
    """El .exe abierto con doble clic cierra su ventana al terminar: sin esto
    el mensaje de error se ve un instante y nadie sabe qué pasó."""
    if frozen() and sys.stdin and sys.stdin.isatty():
        try:
            input("Apretá Enter para cerrar.")
        except EOFError:
            pass


def main(argv=None):
    parser = argparse.ArgumentParser(description="Agente de impresión de Rótulos.")
    parser.add_argument("--server", default=os.environ.get("ROTULOS_SERVER", ""), help="URL de la API, terminada en /api/v1")
    parser.add_argument("--token", default=os.environ.get("ROTULOS_AGENT_TOKEN", ""), help="Token del agente (rpa_...)")
    parser.add_argument("--config", default="", help=f"Archivo de configuración ({CONFIG_NAME})")
    parser.add_argument("--interval", type=float, default=3.0, help="Segundos entre consultas (3)")
    parser.add_argument("--once", action="store_true", help="Consultar una sola vez y salir")
    parser.add_argument("--install-startup", action="store_true", help="Windows: arrancar solo al prender la PC")
    parser.add_argument("--remove-startup", action="store_true", help="Windows: dejar de arrancar solo")
    args = parser.parse_args(argv)

    if args.install_startup or args.remove_startup:
        if platform.system() != "Windows":
            parser.error("--install-startup y --remove-startup son solo para Windows")
        if args.install_startup:
            log(f"Listo: el agente va a arrancar solo al prender la PC ({install_startup()}).")
        else:
            log("Listo: el agente ya no arranca solo." if remove_startup() else "No estaba configurado para arrancar solo.")
        return 0

    config, config_path = load_config(args.config)
    server = args.server or str(config.get("server") or "")
    token = args.token or str(config.get("token") or "")
    if (not server or not token) and sys.stdin and sys.stdin.isatty():
        asked = ask_config()
        server, token = server or asked["server"], token or asked["token"]
        if server and token:
            log(f"Configuración guardada en {save_config({'server': server, 'token': token})}.")
            if platform.system() == "Windows" and input("¿Arrancar solo al prender la PC? (s/n): ").strip().lower().startswith("s"):
                log(f"Va a arrancar solo ({install_startup()}).")
    elif config_path:
        log(f"Configuración: {config_path}")
    if not server or not token:
        parser.error(f"faltan --server y --token (o ROTULOS_SERVER / ROTULOS_AGENT_TOKEN, o {CONFIG_NAME})")
    try:
        return run(Api(server, token), args.interval, args.once)
    except KeyboardInterrupt:
        log("Agente detenido.")
        return 0


if __name__ == "__main__":
    try:
        code = main()
    except SystemExit as exit_:
        code = exit_.code
        if code not in (0, None):
            if isinstance(code, str):
                print(code, file=sys.stderr)
                code = 1
            pause_if_double_clicked()
        sys.exit(code)
    if code:
        pause_if_double_clicked()
    sys.exit(code)
