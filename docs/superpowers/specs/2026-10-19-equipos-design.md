# Comercios con equipo: dueño y empleados — diseño

**Fecha:** 2026-10-19 · **Estado:** aprobado por el usuario (2026-10-19) · **Reemplaza a:** la decisión del
2026-10-16 "organización = datos de la cuenta de UN usuario" (T#351-353).

## Por qué

Un comercio no es una persona. El dueño de la tienda tiene empleados que
imprimen rótulos, despachan y atienden pedidos, y hoy todos tendrían que usar
el mismo usuario y la misma clave: no se sabe quién hizo qué, y no se le puede
dar a alguien acceso a imprimir sin darle también acceso a desconectar la
tienda. El dueño quiere darle a cada empleado los accesos que necesita, y
nada más.

## Qué es (decidido con el usuario)

- **Comercio = equipo.** Cada comercio tiene **un dueño** y **cero o más
  empleados**. Todos entran con su propio usuario y su propia clave, y
  trabajan sobre las cosas **del comercio**: sus tiendas, pedidos,
  plantillas, rótulos, documentos, cuentas de transportistas, impresoras,
  tarifas y datos del comercio. Nadie tiene cosas "propias" fuera de su
  comercio.
- **Dueño y empleado se distinguen siempre**, aunque un empleado tenga todos
  los permisos. El dueño tiene siempre todo. Un empleado puede tener
  exactamente lo mismo que el dueño si el dueño lo configura así (perfil
  "Todo"), pero sigue figurando como empleado.
- **Solo el dueño puede** borrar el comercio y pasarle el título de dueño a
  otro miembro. Ni siquiera un empleado con "Todo" puede.
- **Un usuario pertenece a un solo comercio.** No se puede sumar como empleado
  a alguien que ya tiene un comercio propio con datos (tiendas o pedidos), y
  se le dice por qué. La excepción es un comercio vacío: todo el que se
  registra arranca con su comercio propio vacío, y si no tiene tiendas ni
  pedidos, al sumarlo a otro comercio el suyo se descarta.
- **Cómo entra un empleado.** Hay dos caminos y los dos valen:
  1. **El dueño**, desde una pantalla nueva "Mi equipo", carga el mail del
     empleado y le asigna los accesos. Siempre queda una **invitación
     pendiente que la persona tiene que aceptar** (decidido con el usuario
     tras la revisión): nadie entra a un comercio, ni ve sus datos, sin
     confirmarlo. Si ese mail ya tiene usuario, le llega un mail y al entrar
     a la app ve "Te invitaron al comercio X: Aceptar / Rechazar". Si no
     tiene usuario, el mail trae el link para registrarse, y después de
     registrarse con ese mail ve la misma invitación. Al aceptar se aplica la
     regla del comercio vacío.
  2. **El administrador de la plataforma**, desde la gestión de usuarios,
     puede poner a cualquier usuario en cualquier comercio, como dueño o como
     empleado.
- **Accesos: perfiles y personalizado.** Cada comercio tiene **perfiles**
  (conjuntos de accesos con nombre, p. ej. "Depósito", "Administración").
  Arranca con algunos sugeridos, y el dueño los crea, edita y borra. A cada
  empleado se le asigna:
  - **un perfil**: tiene exactamente esos accesos, y si el dueño cambia el
    perfil, cambia para todos los que lo tienen; o
  - **"personalizado"**: accesos tildados solo para esa persona. Puede partir
    de un perfil como punto de arranque, y desde ahí queda independiente.
- **Tiendas por empleado.** Para cada empleado el dueño elige "todas las
  tiendas" (por defecto) o tilda algunas. Un empleado limitado:
  - ve e imprime solo los pedidos de sus tiendas;
  - no ve ni configura las otras tiendas (remitente, logo, tarifas, checkout);
  - no ve una tienda conectada después hasta que se la habiliten. Con
    "todas", la ve al toque.

## Decisiones que tomé yo (el usuario las vio y dijo "está todo bien")

- **Los pedidos cargados a mano** (sin tienda) los ven todos los del equipo,
  aunque tengan tiendas limitadas.
- **La restricción por tienda es solo para pedidos y tiendas.** Plantillas,
  cuentas de transportistas, impresoras y datos del comercio son del comercio
  entero (lo que cada uno puede *hacer* con ellos lo deciden sus accesos).
- **La auditoría registra a la persona**, no al comercio.
- **"Actividad del equipo"** (decidido con el usuario tras la revisión): una
  pantalla del comercio, `actividad_equipo.html`, donde el dueño ve qué hizo
  cada miembro ("Juan despachó 12 pedidos", "María canceló el #1043"). Es la
  auditoría filtrada a la gente de su comercio y a sus datos, nunca la de
  otros comercios ni lo de la plataforma. La ve el dueño y quien tenga el
  acceso "Equipo".
- **Los roles globales dejan de usarse para los comerciantes.** "Admin" queda
  solo para el administrador de la plataforma (panel, auditoría, catálogos,
  monitoreo). Un comerciante es "comerciante" (el rol `subscriber` de hoy), y
  lo que puede hacer dentro de su comercio lo deciden sus accesos del equipo.
  Los roles `designer` y `operator` quedan para compatibilidad, sin uso nuevo.

## Decisiones tomadas tras la revisión (mías, para que el usuario las controle)

- **Para hacer algo dentro del comercio tienen que permitirlo las dos cosas:
  el rol global y los accesos del equipo** (cambiado al escribir el plan; antes
  decía "reemplazan"). El rol global es el techo que fija el administrador
  de la plataforma en `roles.html`; dentro de ese techo, el dueño reparte.
  Para que el dueño tenga todo, como dice el diseño, una migración le suma al
  rol `subscriber` los permisos que hoy le faltan (cargar a mano e importar
  pedidos), más los permisos nuevos en los que se parte "crear pedidos"
  (tiendas, transportistas, despachar...). Así siguen valiendo `roles.html`,
  los roles personalizados y sus tests. Siguen siendo solo por rol las cosas
  de la persona y de la plataforma: su perfil y su clave (`users.me.*`),
  escribir a soporte y todo lo de admin.
- **"Comercio vacío" quiere decir sin ningún dato propio**: ni tiendas, ni
  pedidos, ni plantillas, rótulos, documentos, cuentas de transportistas,
  impresoras, tarifas o claves de API. Solo se descarta lo que se crea solo
  al registrarse (el comercio en sí). Si tiene cualquier otra cosa, no se lo
  puede sumar a otro comercio, y el mensaje dice qué tiene. Nunca se borra
  nada del usuario.
- **El admin tampoco junta comercios.** Poner en un comercio a un usuario que
  ya es dueño de uno con datos se rechaza con la misma regla. Fusionar dos
  comercios no está previsto.
- **Empleado limitado a algunas tiendas:** un documento generado, un trabajo
  de impresión, un envío de transportista, un rótulo guardado o una planilla
  que incluya algún pedido de una tienda que no tiene **no se le muestra
  entero**. Las métricas y contadores que vea se calculan solo con sus
  pedidos. Para eso el documento generado guarda de qué tiendas son sus
  pedidos.
- **Los admins también tienen comercio.** Todo usuario pertenece a un
  comercio (la migración se lo crea a todos, admins incluidos). Ser admin de
  la plataforma es aparte y no cambia nada dentro de su comercio.

## Los accesos que el dueño reparte

Una lista nueva, en el idioma del comerciante, distinta del catálogo global de
permisos. Cada acceso dice qué habilita:

| Acceso | Habilita |
|---|---|
| Ver pedidos | Mis pedidos, direcciones, documentos generados |
| Imprimir rótulos | Lotes de rótulos, impresión directa, reimprimir |
| Despachar | Crear envíos en transportistas, cargar seguimiento, planilla de retiro, cambiar estado |
| Cancelar pedidos | Cancelar (uno o masivo) |
| Cargar e importar pedidos | Alta manual, importación CSV/Excel, planilla de Empretienda, seguimientos por planilla |
| Plantillas de rótulos | Editor, plantillas, versiones, rótulos guardados, importar rótulo por foto |
| Tiendas | Conectar, desconectar y configurar tiendas (remitente, logo, plantilla, transportista de despacho) |
| Transportistas y tarifas | Cuentas de transportistas, tarifas de envío, checkout, publicar tarifas |
| Impresoras | Agentes e impresoras |
| Datos del comercio | Razón social, CUIT, remitente, logo y preferencias |
| Equipo | Invitar y quitar empleados, perfiles y accesos (no puede tocar al dueño ni darse más de lo que tiene) |
| Exportar | Exportar pedidos |

Perfiles sugeridos con los que arranca cada comercio: **"Todo"** (todos los
accesos), **"Depósito"** (ver pedidos, imprimir rótulos, despachar) y
**"Atención"** (ver pedidos, cargar e importar, cancelar). El dueño los puede
cambiar.

**Regla de no escalar:** quien tiene el acceso "Equipo" sin ser dueño solo
puede dar accesos que él mismo tiene, y no puede cambiar al dueño ni a sí
mismo.

## Cómo se construye (lo decidido)

- **Modelo de datos — mi propuesta para el constructor, ver "Lo que queda".**
  Los datos de hoy cuelgan de un usuario (`owner`/`user` en 24 modelos). La
  forma más chica y segura: **los datos siguen colgando del usuario dueño del
  comercio**, y en cada consulta se reemplaza "el usuario que pide" por "el
  dueño de su comercio". Así no se tocan las restricciones de unicidad (p. ej.
  pedidos únicos por `(user, external_id)`) ni hay que migrar millones de
  filas. Los campos de autoría que ya existen (`created_by`,
  `uploaded_by`, el actor de la auditoría) siguen siendo la persona real.
- **Modelos nuevos** (en `apps.accounts`): `Team` (dueño, nombre), `TeamMember`
  (usuario único: un solo comercio; dueño o empleado; perfil o accesos
  personalizados; todas las tiendas o una lista), `TeamProfile` (nombre y
  accesos, por comercio) y `TeamInvitation` (mail, accesos y tiendas
  elegidos, quién invitó, vencimiento).
- **Una sola forma de preguntar** "¿de qué comercio es este usuario?" y
  "¿puede hacer X?", llamada desde todas las vistas, igual que hoy
  `user_has_permission`. Nada de copiar la regla en cada vista.
- **La seguridad es del backend.** El frontend solo oculta botones; cada vista
  vuelve a chequear el acceso y la tienda. Un empleado que pide por la API un
  pedido de una tienda que no tiene recibe 404 (como si no existiera, igual
  que hoy un pedido ajeno).
- **Migración de lo que ya existe:** cada usuario que no es admin pasa a ser
  dueño de un comercio propio, con sus datos, sin perder nada. Para un
  comerciante que trabaja solo, nada cambia.
- **Pantallas nuevas, una por función** (regla del proyecto): `equipo.html`
  (miembros, invitaciones pendientes, accesos y tiendas de cada uno) y
  `perfiles_equipo.html` (los perfiles del comercio). En la gestión de
  usuarios del admin, ver y cambiar el comercio de un usuario.
- **Datos del comercio:** `AccountSettings` pasa a ser del comercio (del
  dueño, por la regla de arriba). La pantalla `organizacion.html` la edita
  quien tenga el acceso "Datos del comercio".

## Objetivos

- Que el dueño pueda sumar empleados y darle a cada uno exactamente los
  accesos y las tiendas que necesita.
- Que ningún empleado pueda ver ni tocar nada fuera de lo que se le dio, ni
  siquiera llamando a la API a mano.
- Que un comerciante que trabaja solo no note ningún cambio.

## No objetivos (por ahora)

- Que una persona trabaje para dos comercios a la vez (selector de comercio).
  El diseño no lo impide para más adelante.
- Facturación por cantidad de empleados.
- Restringir por tienda las plantillas, transportistas o impresoras.

## Anti-objetivos (sería un fracaso aunque "funcione")

- Que un empleado vea pedidos o datos de compradores de otro comercio, o de una
  tienda que no tiene habilitada.
- Que un comerciante existente pierda datos o acceso con la migración.
- Que un empleado pueda darse más permisos a sí mismo o sacar al dueño.
- Duplicar la lógica de permisos por vista en vez de una sola función.

## Lo que queda para el constructor

- Confirmar o descartar el modelo "los datos siguen colgando del dueño" frente
  a poner un `team` en cada modelo. Con el primero, **pasar la titularidad**
  implica reasignar las filas del dueño viejo al nuevo en una transacción (es
  una operación rara).
- Qué hacer con los datos de quien deja un comercio: le quedan en el
  comercio, y él arranca con un comercio propio vacío.
- Los textos de los mails de invitación, el vencimiento de una invitación y el
  reenvío.
- Cómo afectan los accesos a los flujos que entran sin sesión del comerciante:
  webhooks de tiendas, links de impresión firmados de Shopify, WooCommerce y
  Tiendanube, agente de impresión. Esos actúan **en nombre del comercio**, no
  de una persona.
- Qué acciones del equipo se auditan (sumar, quitar, cambiar accesos: todas
  deberían) y con qué categoría.
- Los tests, entre ellos los de aislamiento: un empleado de un comercio nunca
  ve nada de otro, y un empleado limitado nunca ve nada de otras tiendas.
