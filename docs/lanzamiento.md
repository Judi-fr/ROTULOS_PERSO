# Lanzamiento: lista en orden

Para pasar de la laptop a un servidor con el primer cliente real. Los detalles de
cada paso están en `docs/despliegue.md` y `docs/cambio-de-dominio.md`; esto es el orden.

## Decisiones antes de empezar
- [ ] **Servidor.** Mínimo 2 vCPU, 4 GB de RAM y 80 GB de disco: son seis contenedores
      (Postgres, API con 3 workers de gunicorn, worker, monitor, frontend y Caddy) y los PDF
      y logos quedan en disco.
- [ ] **Dominio** definitivo, con el DNS apuntando al servidor. Sin HTTPS no hay Tiendanube.
- [ ] **Mail** para producción: SMTP con un remitente del dominio de la empresa.
- [ ] **Textos legales:** `privacidad.html` y `terminos.html` son borradores. Faltan los datos de
      la empresa (comentarios `COMPLETAR` dentro de los dos archivos: ya no se ven en la página) y la revisión de alguien de la empresa. Esto incluye
      decidir en qué país quedan los datos de los compradores.
- [ ] **Repo público:** decidir si `backend/db.sqlite3-*` y `.claude/settings.local.json` siguen en el
      repo, o si el repo pasa a privado. Si alguna clave de esas bases se reutilizó, cambiarla.

## Servidor
- [ ] Docker con Compose v2 (`docker compose`, sin guion).
- [ ] `.env` de producción completo. **Guardar `FERNET_KEY` y `SECRET_KEY` fuera del servidor:** sin
      `FERNET_KEY` no se pueden leer los tokens de las tiendas ni las claves de los transportistas.
- [ ] `docker compose -f docker-compose.prod.yml up -d --build`.
- [ ] Crear el admin con `python manage.py createsuperuser`, usando el email como usuario (el login entra por email).
- [ ] Cron de `scripts/backup.sh`, con copia fuera del servidor (`BACKUP_S3_URI`).
- [ ] **Probar una restauración** con `scripts/restore.sh` antes de tener datos reales.

## Comprobar que anda
- [ ] `https://<dominio>/api/v1/health/system/` responde 200.
- [ ] "Estado del sistema" en verde, y "Mandar un mail de prueba" llega.
- [ ] `/media/documents/` responde 404 (los PDF con datos de compradores no se sirven directo).
- [ ] Las páginas traen `X-Frame-Options` (`curl -I https://<dominio>/index.html`). **Este bloque del
      Caddyfile todavía no corrió en un Caddy real.**
- [ ] Un recorrido completo: registro → comercio → pedidos de ejemplo → lote de rótulos.
- [ ] Crear una cuenta en un monitor externo (UptimeRobot o Better Stack) contra `health/system/`:
      es lo único que avisa si se cae el servidor entero.

## Plataformas
Seguir `docs/cambio-de-dominio.md` (Shopify, Tiendanube, Google) y correr
`python manage.py update_store_urls --dry-run`, y después sin `--dry-run`.

## Primer cliente
- [ ] Probar con cuentas reales **solo** lo que use ese cliente (su plataforma y su transportista).
      Lo marcado "A CONFIRMAR" en `CLAUDE.md` es lo escrito sin cuenta real.
- [ ] Si imprime directo: probar el agente en su PC con Windows y en su impresora.
- [ ] Mirarlo usar la app sin explicarle nada y anotar dónde se traba.
- [ ] Guía para pasarle: `docs/guia-primer-cliente.md`.

## Después del lanzamiento (no frena salir)
- Disparadores automáticos del CI: `Judi-fr` da admin o instala la GitHub App de Azure Pipelines.
- Despliegue automático: `docs/despliegue.md`, "Despliegue automático: puesta en marcha".
- Firmar el `.exe` del agente, para evitar el aviso de Windows (el certificado es pago).
- Ordenar Plantillas y Rótulos, según lo que se vea con el primer cliente.
