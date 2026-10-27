# Runbook de recuperación P0 de InvoiceOps

Runbook progresivo para levantar InvoiceOps mientras se ejecuta el backlog
[`dev/recovery-p0`](../dev/recovery-p0/README.md). Reemplaza al runbook integrado
anterior (`LOCAL_INFRASTRUCTURE_RUNBOOK.md`, retirado), que dependía de MLflow, Model API, datos demo APP-07, evidencia y Anvil,
retirados de `invoiceops-app` en el slice S0.

> Hay un único entorno local y una única base de datos: `invoiceops`, en el proyecto Compose `invoiceops-dev`. `invoiceops_recovery_test` existe solo para `pnpm test:recovery:postgres`.

Este runbook es la lista de verificación progresiva por etapas; los procedimientos
detallados viven en [`LOCAL_RUN.md`](../invoiceops-app/LOCAL_RUN.md) y aquí se
enlazan en lugar de duplicarse. Los comandos se ejecutan desde
`apps/v2/invoiceops-stack`. Solo operaciones locales; ningún paso autoriza
recursos remotos.

## Cómo se mantiene este runbook

- Cada slice que cambie cómo se levanta, migra, inicia sesión o prueba la app
  actualiza este archivo en la misma entrega: mueve su etapa de **Pendiente** a
  **Vigente** y agrega sus checkpoints.
- Una etapa vigente debe haberse ejecutado de punta a punta en una base limpia.
- Las etapas pendientes describen lo que se espera, no lo que existe.

## Estado de etapas

| Etapa | Slice / ticket  | Qué agrega al recorrido                                                                                           | Estado      |
| ----- | --------------- | ----------------------------------------------------------------------------------------------------------------- | ----------- |
| E0    | S0              | App independiente: PostgreSQL + backend + frontend, login, administración, grupos, políticas, lectura de facturas | **Vigente** |
| E1    | RP0-01          | Base consolidada `invoiceops` y `pnpm recovery:local:reset` con guardas; rol normal `USER`                        | **Vigente** |
| E2    | RP0-02          | `/openapi.json` y rutas `/api/v1`                                                                                 | Pendiente   |
| E3    | RP0-03, RP0-04  | `pnpm recovery:bootstrap` reemplaza el alta manual; cambio obligatorio de contraseña                              | Pendiente   |
| E4    | RP0-05 a RP0-08 | Onboarding React, organizaciones, grupos con cuatro permisos, usuarios y membresías                               | Pendiente   |
| E5    | RP0-09 a RP0-15 | Alta idempotente, filtros, eliminación lógica y bandeja React de facturas                                         | Pendiente   |
| E6    | RP0-16, RP0-17  | Tokens operacionales emitidos y revocados desde React                                                             | Pendiente   |
| E7    | RP0-18, RP0-19  | Swagger `/docs` con Bearer y clientes curl/Python                                                                 | Pendiente   |
| E8    | RP0-20          | `pnpm test:recovery:e2e` sobre el recorrido completo G01–G15                                                      | Pendiente   |

MLflow, Model API, blockchain y el smoke integrado `stack08` quedan fuera de este
runbook. Si se reincorporan, será como etapas nuevas después de E8.

## Propiedad

| Recurso                                                 | Dueño              | Notas                                                                                                                                             |
| ------------------------------------------------------- | ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| Orden de etapas y checkpoints                           | `invoiceops-stack` | Este archivo                                                                                                                                      |
| PostgreSQL, esquema, backend, frontend, `.env`, Compose | `invoiceops-app`   | Proyecto Compose `invoiceops-dev`; `docker-compose.yml` + `compose.invoiceops-local.yml`; configuración privada en el `.env` de la raíz de la app |

Puertos (loopback): PostgreSQL `LOCAL_APP_DB_PORT` (del `.env`), Docker 5300
(backend) y 5174 (frontend), desarrollo con pnpm 4380 y 5380. El runbook no toca
el laboratorio anterior (`invoiceops-local-app`, `invoiceops-local-mlflow`).

## E0 y E1 — App independiente y base consolidada (vigente)

### E0.1 Preflight

```sh
STACK="$PWD"
APP="$STACK/../invoiceops-app"
dev_compose() {
  docker compose -p invoiceops-dev --env-file "$APP/.env" \
    -f "$APP/docker-compose.yml" -f "$APP/compose.invoiceops-local.yml" "$@"
}
PREFLIGHT_OK=0
if [ -d "$APP" ] && command -v docker >/dev/null && command -v pnpm >/dev/null; then
  PREFLIGHT_OK=1
  echo "Preflight OK."
else
  echo "Preflight falló: falta invoiceops-app, docker o pnpm. Detente y corrige antes de continuar." >&2
fi
```

**Detente si** ves `Preflight falló` o `PREFLIGHT_OK` no vale `1`: ningún paso
siguiente es válido hasta corregirlo. (No uses `exit` en una shell interactiva:
cerraría tu terminal.)

### E0.2 Configuración privada

Sigue el paso 1 de [`LOCAL_RUN.md`](../invoiceops-app/LOCAL_RUN.md): crea
`$APP/.env` solo si no existe, sin imprimirlo ni copiarlo. Luego carga las
variables y construye `DATABASE_URL` hacia `invoiceops`:

```sh
set -a; . "$APP/.env"; set +a
export DATABASE_URL="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@127.0.0.1:${LOCAL_APP_DB_PORT}/invoiceops?schema=public"
```

Repite este bloque y el de E0.1 en cada shell nueva.

### E0.3 Dependencias y base de desarrollo (reset E1)

```sh
pnpm -C "$APP" install
pnpm -C "$APP" generate
dev_compose up -d --wait db
printf '%s\n' "${DATABASE_URL#*@}"   # esperado: 127.0.0.1:<LOCAL_APP_DB_PORT>/invoiceops?schema=public
pnpm -C "$APP" recovery:local:reset -- --confirm-reset=invoiceops
```

> Borra todos los datos de `invoiceops`; no lo ejecutes si quieres conservarlos.
> Precondición: `DATABASE_URL` debe apuntar a `127.0.0.1:$LOCAL_APP_DB_PORT/invoiceops`.
> El `printf` muestra solo `host:puerto/base` sin credenciales; si no coincide,
> no continúes.

**Checkpoint:** `db` saludable; el reset imprime `reset: consolidated migration
applied`. Las guardas del reset están en el paso 3 de
[`LOCAL_RUN.md`](../invoiceops-app/LOCAL_RUN.md). Reemplaza a `prisma migrate
deploy` sobre la base de E0. El rol normal de organización es `USER` (ya no
existe `STUDENT`).

### E0.4 Primer usuario y administrador de plataforma

```sh
pnpm -C "$APP" recovery:bootstrap
```

Con `DATABASE_URL` cargada (E0.2) y la base recién reseteada (E0.3). El comando es
interactivo: pide nombre, RUT (válido, por ejemplo `11111111-1`) y la contraseña
temporal dos veces, con entrada oculta; nunca la acepta por argumentos ni por
variables de entorno y exige una terminal (sin TTY aborta). En una sola
transacción crea el usuario con contraseña temporal de 24 horas y cambio
obligatorio, y la designación única de administrador de plataforma. Solo corre
contra una `DATABASE_URL` local.

- Repetirlo con el mismo RUT responde «already initialized» (la salida del comando está en inglés) y no cambia nada; con
  otro RUT se rechaza (no transfiere autoridad). Es seguro reejecutarlo.
- `pnpm -C "$APP" recovery:bootstrap --existing-user` designa a un usuario
  existente por RUT, solo si tiene una credencial utilizable (una contraseña
  temporal vencida no cuenta); no pide contraseña.
- Si cierras la entrada (Ctrl-D) o interrumpes (Ctrl-C), termina con código 1 sin
  cambiar nada.

**Detente si** el comando termina con error: no continúes a E0.5 sin
administrador designado. La contraseña temporal vence en 24 horas; si vence, repite
E0.3 y este paso. Detalle en el paso 4 de
[`LOCAL_RUN.md`](../invoiceops-app/LOCAL_RUN.md).

### E0.5 Backend y frontend

```sh
dev_compose up --build -d --wait
curl -fsS "http://127.0.0.1:${LOCAL_BACKEND_PORT}/health/live"
curl -fsS "http://127.0.0.1:${LOCAL_BACKEND_PORT}/health/ready"
```

**Checkpoint:** `db`, `backend` y `frontend` saludables; `health/live` devuelve
`{"status":"ok"}` y `health/ready` responde 200.

### E0.6 Checkpoints en la UI

URL: `http://127.0.0.1:5174` (o `LOCAL_FRONTEND_PORT`).

| #   | Acción                                                                                                                                      | Resultado observable                                                     | No acredita                        |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ | ---------------------------------- |
| C1  | Login con el RUT y la contraseña temporal de E0.4                                                                                           | Aparece «Cambia tu contraseña temporal»; tras cambiarla se abre la app   | Pruebas de onboarding (E3)         |
| C2  | **Mi perfil**                                                                                                                               | Insignia de administrador de plataforma; **Organizaciones** ofrece «Crear organización»      | Acceso a facturas                  |
| C3  | En `/organizaciones/nueva`, crear una organización con el RUT de su primer ADMIN; como ese ADMIN, en `/organizaciones/:id`, aprovisionar un usuario | Ambos aparecen tras refrescar; la contraseña temporal se ve una sola vez | Flujo P0 de organizaciones (E4)    |
| C4  | En **Grupos** (`/grupos`), crear un grupo (`/organizaciones/:id/grupos/nuevo`) y, en su detalle, marcar permisos y miembros                                                                | El grupo y sus permisos persisten al recargar                            | Los cuatro permisos por grupo (E4) |
| C5  | Abrir **Facturas**                                                                                                                          | Bandeja vacía, sin errores                                               | Alta de facturas (E5)              |
| C6  | Navegación                                                                                                                                  | No existen **Recursos**, **Evidencia y lotes** ni **Evaluación ML**      | —                                  |

### E0.7 Gates de la app

```sh
pnpm -C "$APP" lint && pnpm -C "$APP" typecheck && pnpm -C "$APP" test:unit && pnpm -C "$APP" build
env -u DATABASE_URL -u TEST_DATABASE_URL pnpm -C "$APP" test:integration
```

`test:integration` levanta su propio PostgreSQL efímero, no usa esta base y se niega a correr si `DATABASE_URL` está definida (de ahí `env -u`).

### E0.8 Pasar a desarrollo con recarga en vivo

Las imágenes de E0.5 son de producción: el backend ejecuta `dist/` compilado y el
frontend sirve el build de Vite con nginx, sin watch ni HMR. Para ajustar código,
conserva la base y reemplaza backend y frontend por los procesos de desarrollo:

```sh
dev_compose stop backend frontend
pnpm -C "$APP" dev
```

`pnpm dev` usa la `DATABASE_URL` de E0.2 (cárgala si es una shell nueva).
`tsx watch` reinicia el backend en `http://127.0.0.1:4380` y Vite aplica HMR en
`http://127.0.0.1:5380`, con el proxy hacia el backend. Si cambias el esquema
Prisma, ejecuta `pnpm -C "$APP" generate` y migra antes de probar.

Para volver al empaquetado: detén `pnpm dev` (Ctrl+C) y repite E0.5.

> **Formato del RUT.** La API usa `19034790-7`; la UI lo muestra y acepta como
> `19.034.790-7`. El cuerpo no puede empezar con 0: `01234567-4` se rechaza.
>
> **Límite de login.** `POST /api/v1/auth/login` limita solo los intentos
> **fallidos**: por IP, 5 cada 15 minutos (ventana fija) y por RUT, 5 y luego un
> bloqueo progresivo (1 minuto, 2, 4… hasta 15) que un login correcto reinicia.
> Con `pnpm dev` las banderas `AUTH_RATE_LIMIT_DISABLED`,
> `AUTH_RATE_LIMIT_MAX_ATTEMPTS` y `AUTH_RATE_LIMIT_WINDOW_MS` aplican al proceso
> del backend: `AUTH_RATE_LIMIT_DISABLED` acepta `true`, `false` o vacío y
> cualquier otro valor detiene el arranque. Docker no reenvía esas banderas; el
> `docker-compose.yml` base solo fija `AUTH_TRUST_PROXY_HOPS=1` (nginx es el único
> proxy) para que la clave de IP sea el cliente real y no el proxy. El backend
> sigue publicado en loopback para `/health/*`: un proceso local puede falsificar
> `X-Forwarded-For` llamándolo directo (riesgo residual aceptado en desarrollo). Detalle en
> [`LOCAL_RUN.md`](../invoiceops-app/LOCAL_RUN.md#límite-de-intentos-de-login-desarrollo).

### E1 — Base consolidada (RP0-01)

La base de E0 con migraciones históricas **no es compatible**. El recorrido es
E0.3: `pnpm recovery:local:reset -- --confirm-reset=invoiceops` (borra todos los
datos de `invoiceops`; comprueba antes el destino como indica E0.3) con
`DATABASE_URL` apuntando a `invoiceops`. El backend en Compose usa esa misma
base (la define `docker-compose.yml`). `invoiceops_recovery_test` se usa solo en
`pnpm test:recovery:postgres`. Pruebas:
`pnpm test:recovery:unit` y, con `RECOVERY_TEST_DATABASE_URL` hacia
`invoiceops_recovery_test`, `pnpm test:recovery:postgres`.

## Etapas pendientes (se completan al entregar cada slice)

### E2 — Contratos y OpenAPI (RP0-02)

Esperado: `curl http://127.0.0.1:<backend>/openapi.json` devuelve OpenAPI 3.1;
las rutas funcionales pasan a `/api/v1` y nginx/Vite las reenvían.

### E3 — Bootstrap y onboarding (RP0-03, RP0-04)

Esperado: `pnpm recovery:bootstrap` (E0.4) crea al administrador con contraseña
temporal; el primer login exige cambiarla por otra antes de cualquier otra acción.

### E4 — Administración P0 (RP0-05 a RP0-08)

Esperado: checkpoints de organización con primer ADMIN explícito, grupo con
`readInvoices`, `createInvoices`, `deleteOwnInvoices` y `deleteOthersInvoices`,
usuario temporal y membresías múltiples.

### E5 — Facturas (RP0-09 a RP0-15)

Esperado: alta CLP en pesos con `Idempotency-Key`, filtros, paginación,
eliminación lógica y bandeja React.

### E6 — Tokens (RP0-16, RP0-17)

Esperado: emitir token en React, ver el secreto una sola vez y revocarlo.

### E7 — Swagger y clientes (RP0-18, RP0-19)

Esperado: `/docs` con Authorize Bearer; `pnpm test:recovery:swagger` y
`pnpm test:recovery:clients`.

### E8 — Recorrido integrado (RP0-20)

Esperado: `pnpm test:recovery:e2e` cubre G01–G15 con todo lo externo apagado.

## Parar y reiniciar

```sh
# Detener conservando datos
dev_compose down

# Borrar los datos del proyecto invoiceops-dev (incluye la base invoiceops)
dev_compose down -v
```

Advertencia: `down -v` borra todos los datos de `invoiceops`; no lo ejecutes si quieres conservarlos. Tras `down -v`, repite E0.3 y E0.4. No borres volúmenes de otros proyectos
Compose ni la red `invoiceops-local-link`.

## Problemas frecuentes

| Síntoma                                     | Causa probable                                                                                             | Solución                                                                                                                                                                                                              |
| ------------------------------------------- | ---------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `port is already allocated`                 | Laboratorio anterior u otro servicio en el puerto                                                          | Detenlo o cambia `LOCAL_*_PORT` en el `.env` de `invoiceops-app`                                                                                                                                                      |
| `DATABASE_URL must be configured`           | Shell sin E0.2 cargado                                                                                     | Repite la carga de variables                                                                                                                                                                                          |
| `/health/ready` falla                       | Migraciones sin aplicar                                                                                    | Repite E0.3                                                                                                                                                                                                           |
| Mutaciones devuelven 403 `FORBIDDEN_ORIGIN` | Acceso directo al backend sin `Origin` del frontend                                                        | Usa la URL del frontend                                                                                                                                                                                               |
| Login devuelve 429 `RATE_LIMITED`           | Superaste el límite de intentos fallidos por IP (5 cada 15 min) o por RUT (bloqueo progresivo desde 1 min) | Espera el bloqueo (hasta 15 minutos), reinicia el backend, o en desarrollo usa `AUTH_RATE_LIMIT_DISABLED=true pnpm dev` (ver [`LOCAL_RUN.md`](../invoiceops-app/LOCAL_RUN.md#límite-de-intentos-de-login-desarrollo)) |
