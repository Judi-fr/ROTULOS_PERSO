# Despliegue automatizado: plan (T#181-185)

**Estado (2026-10-19): planeado, sin código.** Se decidió dejar solo el CI
(`backend/azure-pipelines.yml` y `frontend/azure-pipelines.yml`) hasta que
exista un servidor. Hoy el despliegue se hace a mano con
`docker-compose.prod.yml` (ver CLAUDE.md, "Deploying"). Este documento dice qué
hay que crear y cómo quedaría el pipeline el día que se haga.

## Lo que hace falta antes (decisiones y cuentas)

| Qué | Para qué | Opciones |
|---|---|---|
| Un servidor (VPS) por entorno | Correr el compose | Uno solo al principio (prod). Stage después, si hace falta. La RAM necesaria hay que medirla con la carga real: son 6 servicios. |
| Dominio apuntando al servidor | Caddy saca el certificado solo | Ya contemplado en `Caddyfile` |
| Registro de imágenes | No compilar en el servidor y poder volver a una versión exacta | Azure Container Registry (pago), GitHub Container Registry (gratis con el repo) o Docker Hub. **Sin registro también se puede**: compilar en el servidor (ver más abajo). |
| Conexión de servicio SSH en Azure DevOps | Que el pipeline entre al servidor | Project settings → Service connections → SSH |
| Conexión con GitHub en Azure DevOps | Que los pipelines lean el repo | Se crea al dar de alta el primer pipeline |

## Entornos y aprobaciones (T#182)

- En Azure DevOps → Pipelines → **Environments**: `stage` y `prod`.
- `prod` con **Approvals and checks** → aprobación de una persona. El pipeline
  se frena antes de desplegar hasta que alguien aprueba desde la web o el mail.
- `stage` sin aprobación: se despliega solo con cada merge a `master`.
- Cada etapa de deploy declara `environment: prod`, y Azure DevOps guarda el
  historial de qué versión se desplegó, cuándo y quién la aprobó.

## Secretos y variables (T#184)

- Un **Variable group** por entorno (`rotulos-stage`, `rotulos-prod`) en
  Pipelines → Library, con las claves marcadas como secretas: `SECRET_KEY`,
  `POSTGRES_PASSWORD`, `FERNET_KEY`, `TIENDANUBE_CLIENT_SECRET`,
  `SHOPIFY_CLIENT_SECRET`, `EMAIL_HOST_PASSWORD`, `ANTHROPIC_API_KEY`, etc.
  Lista completa: `backend/.env.example` y la cabecera de `docker-compose.prod.yml`.
- El paso de deploy escribe el `.env` del servidor a partir del grupo, con
  permisos `600`. Los secretos nunca van al repo ni al log: Azure DevOps los
  enmascara.
- **`FERNET_KEY` no se rota nunca a la ligera**: cifra los tokens de las
  tiendas y las claves de los transportistas. Si cambia, todas las tiendas
  quedan desconectadas.

## Etapa de release con migraciones (T#183)

Orden en el servidor, en una etapa `Deploy` que depende de `Image`:

1. **Backup de la base y de media** antes de tocar nada:
   `scripts/backup.sh antes-de-<versión>` (ver "Backups" más abajo).
2. **Traer la versión nueva**: con registro, `docker compose pull`. Sin
   registro, copiar el código de ese commit y `docker compose build`.
3. **Migrar en un paso aparte**, antes de reemplazar la API:
   `docker compose run --rm api python manage.py migrate --noinput`. Si falla,
   el deploy se corta acá y sigue andando la versión anterior.
   Hoy la API migra al arrancar (`command` del compose). Con un deploy
   automático conviene sacar el `migrate` de ahí, para que una migración
   fallida no deje la API en un bucle de reinicios.
4. `docker compose up -d` (API, worker, monitor, web).
5. **Verificación**: esperar a que `/api/v1/health/ready/` devuelva 200 y que
   `/api/v1/health/system/` no devuelva 503. Si no pasa en 2 minutos, el
   pipeline falla y avisa (y entra el rollback del paso siguiente).
6. Guardar en el servidor qué versión quedó activa (`releases/current`) y cuál
   era la anterior (`releases/previous`).

## Estrategia de rollback (T#185)

- **Código**: volver a la imagen anterior (`releases/previous`) con
  `docker compose up -d`. Con imágenes etiquetadas por commit (el CI ya las
  etiqueta así) es inmediato. Se dispara a mano re-corriendo el deploy de la
  versión anterior desde Azure DevOps, o con un script `rollback.sh` en el
  servidor.
- **Base de datos**: la regla es que **toda migración sea compatible hacia
  atrás**, para que la versión anterior del código ande con la base nueva.
  Por ejemplo, agregar una columna con valor por defecto; borrar o renombrar
  una columna va en dos releases. Así un rollback de código casi nunca
  necesita tocar la base.
- Si una migración no se puede deshacer y rompe datos: restaurar el backup
  del paso 1 con `scripts/restore.sh <db-....dump> [<media-....tar.gz>]`. Se pierde lo que entró entre el deploy y
  la restauración (pedidos de las tiendas incluidos). Por eso es el último
  recurso, y conviene volver a sincronizar las tiendas después (la
  reconciliación de WooCommerce/VTEX/Magento lo hace sola; en Tiendanube y
  Shopify hay que reimportar los pedidos de la tienda).
- Probar el rollback una vez en `stage` antes de necesitarlo en `prod`.

## Backups (hecho 2026-10-29)

`scripts/backup.sh` y `scripts/restore.sh`, en la raíz del repo, se corren en el
servidor desde esa carpeta (usan `docker-compose.prod.yml`).

- **Qué guarda**: la base (`pg_dump -Fc`, `db-<fecha>[-etiqueta].dump`) y la
  carpeta media entera (`media-<fecha>[-etiqueta].tar.gz`: PDFs de lotes,
  logos, imágenes de plantillas). Un backup se escribe como `.partial`, se
  verifica (`pg_restore --list`, `gzip -t`) y recién ahí toma su nombre.
  Permisos `600`: tienen datos de compradores.
- **Dónde**: `backups/` (o `BACKUP_DIR`), con los últimos `BACKUP_KEEP` (14)
  de cada tipo. Con `BACKUP_S3_URI` además se copian a S3 (hace falta el `aws`
  CLI con permiso de escritura en el bucket; la retención allá, con una regla
  de ciclo de vida). **Un backup que solo vive en el mismo disco no sirve si se
  pierde la máquina**: en AWS, S3 o snapshots del volumen EBS.
- **Cuándo**: todos los días por cron
  (`0 4 * * * cd /ruta/al/repo && scripts/backup.sh >> backups/backup.log 2>&1`)
  y antes de cada deploy con una etiqueta.
- **Restaurar**: `scripts/restore.sh <dump> [<media>]` valida los archivos,
  pide escribir `RESTAURAR`, hace un backup `antes-de-restaurar` de lo que hay,
  para API/worker/monitor, restaura en una sola transacción y los vuelve a
  levantar (también si falla).
- Probado el 2026-10-29 contra un Postgres 16 real con los datos de
  `seed_demo` (backup, rotación, restaurar después de borrar pedidos y archivos,
  archivo inválido, cancelar). No probado todavía en el servidor con
  `docker compose` de verdad: hacerlo una vez en cuanto esté.
- Si la base termina en RDS, `pg_dump` deja de correr en el contenedor `db`:
  hay que adaptar el script (o usar los snapshots automáticos de RDS).

## Lo que ya está y no hay que rehacer

- CI de backend y frontend, con imágenes como artefacto y etiquetadas por commit.
- Sondas `health/`, `health/ready/` y `health/system/`.
- Alertas por mail si algo se cae después del deploy (`apps.monitoring`).
- `docker-compose.prod.yml` + `Caddyfile` probados como despliegue manual.
