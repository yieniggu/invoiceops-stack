# invoiceops-stack

Repositorio de infraestructura **único** de InvoiceOps. Su `compose.yml` usa `include:` de Docker
Compose para traer el compose de producción de cada repositorio, así que cada repo sigue siendo
dueño de sus servicios y este solo los junta en **un** proyecto Compose (`invoiceops`):

| Repositorio | Archivo incluido | Servicios |
| --- | --- | --- |
| `invoiceops-app` | `compose.production.yml` | `db`, `backend`, `web` (+ `migrate` y `bootstrap`, perfil `ops`) |
| `invoiceops-mlflow` | `compose.yml` + `compose.gateway.yml` | `postgres`, `minio`, `minio-init`, `mlflow`, `gateway` (+ `minio-tools`, perfil `tools`) |
| `invoiceops-model-api` | `compose.production.yml` | `model-api` |

`invoiceops-ml` no corre como servicio: es el paquete que `model-api` construye y que los notebooks instalan.

## Levantar todo

### Servidor (producción)

Los repositorios van lado a lado y **un único** archivo de secretos fuera de git:

```text
/opt/invoiceops/
├── app/  mlflow/  model-api/  ml/  stack/      # checkouts de invoiceops-*
└── invoiceops.env                              # chmod 600, secretos (plantilla: invoiceops-app/ops/production.env.example)
```

```bash
cd /opt/invoiceops/stack
docker compose --env-file /opt/invoiceops/invoiceops.env config -q
docker compose --env-file /opt/invoiceops/invoiceops.env build
docker compose --env-file /opt/invoiceops/invoiceops.env --profile ops run --rm migrate
docker compose --env-file /opt/invoiceops/invoiceops.env up -d
docker compose --env-file /opt/invoiceops/invoiceops.env ps
```

El primer administrador se crea una vez con
`docker compose --env-file /opt/invoiceops/invoiceops.env --profile ops run --rm bootstrap`
(interactivo; ver `invoiceops-app/DEPLOY.md`). Para migrar desde el proyecto que ya corre, ver
«Migración desde el proyecto actual de la app» en `dev/tickets/MLOPS-07_plan.md`.

**Rutas.** `compose.yml` busca los repos en `../app`, `../mlflow`, `../model-api` y (para el
contexto de build de `model-api`) `../ml`: el orden del servidor, sin configurar nada. Se pueden
cambiar con `INVOICEOPS_APP_DIR`, `INVOICEOPS_MLFLOW_DIR`, `INVOICEOPS_MODEL_API_DIR` e
`INVOICEOPS_ML_CONTEXT`. *Por qué variables y no enlaces simbólicos:* en local las carpetas se llaman
`invoiceops-*`; con variables, `local.example.env` (que va en git) ya trae los valores correctos y no
queda ningún paso manual por máquina. Con enlaces habría que recordar cinco `ln -s` y un enlace roto
falla de forma menos clara que una ruta de `include:` inexistente.

### Local (Mac del curso)

```bash
cd invoiceops-stack
dc() { docker compose -p invoiceops-local -f compose.yml -f compose.local.yml --env-file local.example.env "$@"; }
dc --profile ops build
dc up -d db
dc --profile ops run --rm migrate
dc --profile ops run --rm bootstrap
dc up -d --wait
```

`dc` pasa `-p invoiceops-local` de forma explícita: una variable `COMPOSE_PROJECT_NAME` exportada en el shell
(por ejemplo `invoiceops`) tiene prioridad sobre el `name:` de `compose.local.yml` y haría que cualquier
`down -v` apuntara al proyecto equivocado. La bandera `-p` gana siempre.

`compose.local.yml` cambia solo lo necesario: http con `GATEWAY_ALLOW_INSECURE_HTTP=1` en el gateway
y `NODE_ENV=development` en el backend (cookie sin `Secure`); puertos publicados **solo** en
`127.0.0.1` (app `:8100`, MLflow `:8101`); hostnames distintos (`http://invoiceops.localhost:8100` y
`http://mlflow-invoiceops.localhost:8101`) para que las cookies no se mezclen; sin túnel de Cloudflare
(la red `edge` se crea localmente); proyecto, volúmenes y redes propios (`invoiceops-local_*`), de modo
que jamás toca los nombres de producción. Los secretos de `local.example.env` son desechables (un escáner de
secretos debe tener ese archivo en su lista de permitidos; ningún otro `*.example` puede repetir sus valores,
lo verifica `tests/test_env_examples.py`).

Dos variables del backend solo existen en local:

- `NODE_ENV=development`: la cookie de sesión pierde el flag `Secure` (por http el navegador no la enviaría) y
  el backend acepta un `MLFLOW_PUBLIC_URL` con `http://`. En producción, `NODE_ENV=production` hace la cookie
  `Secure` y exige `https://`.
- `AUTH_RATE_LIMIT_DISABLED=true`: apaga el límite de intentos de login (5 fallos por IP cada 15 min y bloqueo
  progresivo por RUT) para poder repetir el flujo sin bloquearse. El backend se niega a arrancar con esta
  variable si `NODE_ENV=production`.

- Guía paso a paso (profesor y estudiante): [LOCAL_E2E.md](LOCAL_E2E.md).
- Prueba automática de punta a punta: `python3 scripts/local_e2e_smoke.py` (levanta un proyecto aparte,
  recorre el flujo completo y termina con `down -v` de ese proyecto; `--skip-build` reutiliza imágenes).
  Siempre pasa `-p`, ignora `COMPOSE_PROJECT_NAME`/`COMPOSE_FILE`/`COMPOSE_PROFILES` del shell, exige que
  `--project` empiece con `invoiceops-e2e` y, si ya hay recursos de ese proyecto, se detiene sin tocarlos
  salvo que se pase `--reset`.
- Apagar conservando los datos:

  ```bash
  dc --profile ops down
  ```

- **Advertencia:** el siguiente bloque borra los volúmenes de `invoiceops-local` (base de la app, MLflow y
  MinIO). Úselo solo para empezar de cero:

  ```bash
  dc --profile ops down -v
  ```

### Continuidad de datos y redes en producción

El proyecto se llama `invoiceops`, igual que el actual de la app, y los recursos con nombre
explícito se conservan:

- Volumen de la base de la app: `invoiceops_postgres_data` (fijado con `name:` en
  `invoiceops-app/compose.production.yml`); Compose lo reutiliza, no crea uno vacío.
- Redes: `invoiceops_api` y `invoiceops_db` (interna) las sigue creando el proyecto; `aglai_edge` es
  externa (`INVOICEOPS_EDGE_NETWORK`, por defecto `aglai_edge`).
- Nombres que apunta `cloudflared`: `invoiceops-web:80` (alias de `web` en `aglai_edge`) y, nuevo,
  `invoiceops-mlflow-gateway:8080` (alias del `gateway` en `aglai_edge`; solo se agrega ese hostname en Cloudflare).
- Volúmenes nuevos de MLflow: `invoiceops_mlflow_postgres_data` e `invoiceops_mlflow_minio_data`.
- `tests/test_stack_config.py` verifica todo esto con `docker compose config`.

Propiedades de seguridad que se mantienen: ningún servicio interno publica puertos; `model-api` con
`mem_limit: 2g`, `cap_drop: [ALL]` y `no-new-privileges`; los secretos viven solo en el archivo de entorno.

### Qué se retiró

Los overlays locales de este repositorio modelaban el diseño antiguo (MLflow con usuario/clave propios
y Model API con tokens de administración, sin gateway ni SSO) y ya no corresponden a ningún servicio:

- `compose.yml` anterior (overlay de `invoiceops-mlflow/compose.yml`) → reemplazado por el `compose.yml` de `include:`.
- `compose.model-api.yml` (tercera capa con `MODEL_API_ADMIN_TOKEN` y credenciales de MLflow).
- `scripts/stack_ml_smoke.py` y `scripts/stack_model_api_smoke.py` (arneses del diseño antiguo).
- `tests/test_stack_ml_smoke.py`, `tests/test_stack_model_api_smoke.py`, `tests/test_stack_model_api_config.py`,
  `tests/stack_model_api_s3_checks.py` y el antiguo `tests/test_stack_config.py` (probaban lo anterior).
- Las secciones históricas STACK-00, STACK-01 y STACK-02 de este README (su historia queda en git).

Se conservan sin cambios: Anvil (`compose.anvil.yml`, `scripts/stack_anvil_contract_deploy.py`, sus tests y la
sección STACK-04 de abajo) y [RECOVERY_RUNBOOK.md](RECOVERY_RUNBOOK.md) (recuperación P0 de la app).

### Orden de publicación y secreto de CI

Los repositorios se suben a GitHub en este orden: `invoiceops-app`, `invoiceops-mlflow`,
`invoiceops-ml` (push a `main`, CI en verde y tag `v0.2.0`), `invoiceops-model-api` (con la variable de
repositorio `INVOICEOPS_ML_REF=v0.2.0` definida **antes de su primer push**) y, al final, `invoiceops-stack`. El `ci.yml` de este
repositorio hace el checkout de los otros cinco (incluye `invoiceops-contracts`, cuyo README lee una prueba de consistencia), así que deben existir antes. El secreto de repositorio
`SIBLINGS_READ_TOKEN` (token fine-grained de solo lectura, `Contents: read`, sobre los cinco hermanos)
**debe existir antes del primer push de este repositorio**; sin él, el primer CI falla en el checkout de
`invoiceops-app`. Para `invoiceops-ml`, primero `main` y el CI en verde y solo después el tag (ver su README y
la sección «Tag de `invoiceops-ml`» de `dev/tickets/MLOPS-07_plan.md`). Los slugs de `ci.yml` deben ser los
remotos reales (`yieniggu/invoiceopps-app` lleva dos «p»); `tests/test_ci_workflow.py` lo comprueba.

### Antes de commitear

Ejecute siempre en local `python3 -m unittest discover -s tests` desde este repositorio, con los hermanos
lado a lado y `dev/tickets` presente: las pruebas de `dev/tickets` (higiene de los bloques de shell, enlaces,
orden de publicación, matriz del gateway) se omiten en CI porque esa carpeta no es un repositorio, así que
un fallo ahí solo se ve en su máquina.

El CI también ejecuta `ruff check tests scripts` (configuración mínima en `ruff.toml`, versión fijada en `ci.yml`); en local: `uvx ruff check tests scripts`.

### Pruebas

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

Las pruebas necesitan Docker (solo `docker compose config`, sin daemon) y los repositorios hermanos
`invoiceops-app`, `invoiceops-mlflow`, `invoiceops-model-api`, `invoiceops-ml` e `invoiceops-contracts` lado a lado. Fuera de CI, si
faltan, se omiten; con `CI` definida (como en GitHub Actions) **fallan**, para que un checkout incompleto no
pase en verde. `.github/workflows/ci.yml` hace el checkout de los hermanos (el repositorio privado
`invoiceops-model-api` exige el secreto `SIBLINGS_READ_TOKEN`, un token de solo lectura).
Las pruebas de `dev/tickets` (bloques de shell sin comentarios `#`, enlaces) se omiten si esa carpeta no está.

---

## STACK-04 AC-05 — operación futura de Anvil y despliegue local

Esta sección conserva dos estados con procedencia distinta. **Estado histórico
del slice estático (sustituido):** el smoke real **no fue ejecutado** durante ese
slice; no se inició Anvil, no se consultó RPC, no se desplegó contrato ni se creó,
inspeccionó o eliminó recurso Docker alguno. **Estado posterior vigente:** el
smoke local posterior quedó **CLOSED / COMPLETED_LOCAL_VALIDATED**. El cierre
sustituye aquel estado histórico de no ejecución, pero no reclasifica la evidencia
estática. El cierre no acredita CI, remoto, publicación o despliegue fuera del
host local.

### Preflight manual

Antes de una autorización operacional independiente, el operador debe confirmar
que dispone de Docker/Compose, Foundry (`cast`), Python 3 y de los repositorios
hermanos `invoiceops-stack` e `invoiceops-contracts` en las rutas esperadas. Debe
elegir un nombre nuevo que cumpla `stack04-proof-...`, un puerto TCP libre de
loopback y las dos direcciones públicas no nulas que exige el wrapper. No usar
`.env`, `.env.*`, claves privadas, cuentas remotas ni endpoints distintos de
`http://127.0.0.1:<puerto>`.

### Convención de proyecto Compose

Para cada ejecución autorizada, el operador define un identificador nuevo y no
secreto, por ejemplo:

```bash
STACK04_PROJECT=stack04-proof-example01
STACK_ANVIL_PORT=18545
```

`compose.anvil.yml` conserva `name: invoiceops-anvil` como valor estático del
archivo, pero **toda** invocación de este procedimiento debe llevar
`-p "$STACK04_PROJECT"`. La bandera tiene prioridad sobre `name:` y fuerza que
preflight, ejecución y cleanup compartan exactamente la identidad
`stack04-proof-*`; no usar `COMPOSE_PROJECT_NAME` ni omitir `-p`.

Antes de arrancar, comprobar únicamente que no haya contenedores, redes o
volúmenes ya etiquetados con ese proyecto exacto. Una colisión, un puerto ocupado,
la falta de un binario o una identidad de cadena distinta de `31337` bloquean el
procedimiento: no se cambia de proyecto, puerto, RPC ni contrato de forma
silenciosa.

El preflight usa el mismo identificador forzado:

```bash
docker --host unix:///var/run/docker.sock compose -p "$STACK04_PROJECT" \
  --env-file /dev/null -f ./compose.anvil.yml ps --all
```

### Orden manual futuro

Con autorización explícita y variables no secretas entregadas sólo al proceso
que las necesite, la secuencia es:

1. Iniciar exclusivamente `compose.anvil.yml` con el proyecto y puerto elegidos;
   no se une a las capas MLflow, Model API ni App:

   ```bash
   docker --host unix:///var/run/docker.sock compose -p "$STACK04_PROJECT" \
     --env-file /dev/null -f ./compose.anvil.yml up -d
   ```
2. Esperar la salud de Anvil y comprobar que `cast chain-id` contra el endpoint
   loopback devuelve exactamente `31337`.
3. Ejecutar primero el wrapper sin `--execute` para validar el plan local. Sólo
   después, y en la misma autorización, usar `--execute` con las direcciones
   públicas aprobadas para delegar al script local existente de Contracts.
4. Registrar el resultado operativo fuera de este README y evaluar el cleanup
   antes de cerrar la ejecución.

La composición usa el proyecto elegido como frontera de propiedad. Los recursos
propios son exclusivamente los contenedores, red y volúmenes que Compose etiquete
con ese proyecto exacto. La imagen base de Foundry, cachés, recursos de otros
proyectos y cualquier recurso sin esa etiqueta no pertenecen a este procedimiento
y no deben eliminarse.

### Límites, cleanup y rollback

El smoke estático sólo revisa bytes locales; no acredita disponibilidad del daemon,
salud real, publicación de puertos, chain ID, cuentas desbloqueadas, despliegue,
receipt, dirección de contrato ni integración de App. Tampoco autoriza red,
instalaciones, migraciones, broadcasts, despliegues remotos o cambios de secretos.

Al finalizar una operación futura, el cleanup sólo es aceptable si el inventario
del proyecto exacto confirma que los recursos propios esperados fueron retirados
y que no se actuó sobre recursos ajenos. Ante inventario incompleto, etiquetas
inconsistentes, timeout, error de Compose o duda de propiedad, detenerse y
conservar la evidencia para revisión: no usar `prune`, borrado forzado, `-v`,
eliminación de imágenes compartidas ni limpieza por prefijos amplios.

Tras verificar esa propiedad, el cleanup usa el mismo proyecto forzado y no
elimina volúmenes:

```bash
docker --host unix:///var/run/docker.sock compose -p "$STACK04_PROJECT" \
  --env-file /dev/null -f ./compose.anvil.yml down
```

El rollback operacional consiste en detener y retirar únicamente los recursos
verificados del proyecto de prueba, sin borrar volúmenes ni imágenes. El rollback
de este slice es revertir esta sección y su prueba estática asociada en un cambio
posterior revisado; no restaura ni modifica App, Contracts, Solidity o la
composición Anvil. Si se llegó a desplegar un contrato, el rollback de código no
revierte su estado de cadena: la red local debe tratarse como evidencia separada.
