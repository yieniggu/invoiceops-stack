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

| Etapa | Slice / ticket | Qué agrega al recorrido | Estado |
| --- | --- | --- | --- |
| E0 | S0 | App independiente: PostgreSQL + backend + frontend, login, administración, grupos, políticas, lectura de facturas | **Vigente** |
| E1 | RP0-01 | Base consolidada `invoiceops` y `pnpm recovery:local:reset` con guardas; rol normal `USER` | **Vigente** |
| E2 | RP0-02 | `/openapi.json` y rutas `/api/v1` | Pendiente |
| E3 | RP0-03, RP0-04 | `pnpm recovery:bootstrap` reemplaza el alta manual; cambio obligatorio de contraseña | Pendiente |
| E4 | RP0-05 a RP0-08 | Onboarding React, organizaciones, grupos con cuatro permisos, usuarios y membresías | Pendiente |
| E5 | RP0-09 a RP0-15 | Alta idempotente, filtros, eliminación lógica y bandeja React de facturas | Pendiente |
| E6 | RP0-16, RP0-17 | Tokens operacionales emitidos y revocados desde React | Pendiente |
| E7 | RP0-18, RP0-19 | Swagger `/docs` con Bearer y clientes curl/Python | Pendiente |
| E8 | RP0-20 | `pnpm test:recovery:e2e` sobre el recorrido completo G01–G15 | Pendiente |

MLflow, Model API, blockchain y el smoke integrado `stack08` quedan fuera de este
runbook. Si se reincorporan, será como etapas nuevas después de E8.

## Propiedad

| Recurso | Dueño | Notas |
| --- | --- | --- |
| Orden de etapas y checkpoints | `invoiceops-stack` | Este archivo |
| PostgreSQL, esquema, backend, frontend, `.env`, Compose | `invoiceops-app` | Proyecto Compose `invoiceops-dev`; `docker-compose.yml` + `compose.invoiceops-local.yml`; configuración privada en el `.env` de la raíz de la app |

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

### E0.4 Primer usuario y administrador de plataforma (provisional hasta E3)

```zsh
read -rs "NEW_PASSWORD?Contraseña del primer usuario: "; echo
export NEW_PASSWORD
USER_ID=$(pnpm -C "$APP" --filter @invoiceops/backend exec tsx -e '
(async () => {
  const { createDatabase } = await import("./src/database.ts");
  const { provisionInternalUser } = await import("./src/internal-user-provisioning.ts");
  const db = createDatabase(process.env.DATABASE_URL);
  try {
    const user = await provisionInternalUser(db.prisma, {
      name: "Administrador local",
      rut: "11111111-1",
      password: process.env.NEW_PASSWORD,
    });
    console.log(user.id);
  } finally {
    await db.prisma.$disconnect();
  }
})();' | tail -n 1)
unset NEW_PASSWORD
echo "userId: $USER_ID"
if [ -n "$USER_ID" ]; then pnpm -C "$APP" --filter @invoiceops/backend platform-administrator bootstrap "$USER_ID" --confirm-bootstrap; else echo "Detente: no se obtuvo USER_ID" >&2; fi
```

Sintaxis zsh. En bash, usa `read -rsp "Contraseña del primer usuario: " NEW_PASSWORD; echo`.
**Detente si** `USER_ID` queda vacío: el alta falló y no debes ejecutar el
bootstrap. Ejecútalo una sola vez por base.

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

| # | Acción | Resultado observable | No acredita |
| --- | --- | --- | --- |
| C1 | Login con `11111111-1` y la contraseña de E0.4 | Se abre la app | Onboarding obligatorio (E3) |
| C2 | **Mi perfil** | Insignia de administrador de plataforma; aparece **Administración** | Acceso a facturas |
| C3 | En **Administración**, crear una organización y aprovisionar un usuario | Ambos aparecen tras refrescar | Flujo P0 de organizaciones (E4) |
| C4 | Crear un grupo y agregar integrantes | El grupo aparece en **Mis grupos** | Los cuatro permisos por grupo (E4) |
| C5 | Abrir **Facturas** | Bandeja vacía, sin errores | Alta de facturas (E5) |
| C6 | Navegación | No existen **Recursos**, **Evidencia y lotes** ni **Evaluación ML** | — |

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
> **Límite de login.** `POST /auth/login` admite 5 intentos por IP cada 15
> minutos. Las banderas `AUTH_RATE_LIMIT_*` aplican al proceso del backend
> (`pnpm dev`); el overlay de Docker no las reenvía y solo el valor `true`
> desactiva el límite. Detalle en
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

Esperado: `pnpm recovery:bootstrap` reemplaza E0.4; el primer login exige cambiar
a otra contraseña antes de cualquier otra acción.

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

| Síntoma | Causa probable | Solución |
| --- | --- | --- |
| `port is already allocated` | Laboratorio anterior u otro servicio en el puerto | Detenlo o cambia `LOCAL_*_PORT` en el `.env` de `invoiceops-app` |
| `DATABASE_URL must be configured` | Shell sin E0.2 cargado | Repite la carga de variables |
| `/health/ready` falla | Migraciones sin aplicar | Repite E0.3 |
| Mutaciones devuelven 403 | Acceso directo al backend sin `Origin` del frontend | Usa la URL del frontend |
| Login devuelve 429 `Demasiados intentos` | Superaste el límite de intentos por IP (5 cada 15 min) | Espera 15 minutos, reinicia el backend, o en desarrollo usa `AUTH_RATE_LIMIT_DISABLED=true pnpm dev` (ver [`LOCAL_RUN.md`](../invoiceops-app/LOCAL_RUN.md#límite-de-intentos-de-login-desarrollo)) |
