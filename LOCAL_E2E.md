# Guía local de punta a punta

Recorre en su Mac todo el flujo de la plataforma, primero como **profesor** y luego como **estudiante**:
login, activar ML, token de MLflow, notebooks 00–05, MLflow por SSO, despliegue desde «Modelos» y una
factura con su predicción. Todo corre en un proyecto Compose aparte (`invoiceops-local`), con puertos solo
en loopback y secretos desechables: no toca producción ni tiene túnel de Cloudflare.

> **¿Prefiere que lo haga una máquina?** `python3 scripts/local_e2e_smoke.py` ejecuta este mismo flujo por
> HTTP/CLI (con el proyecto `invoiceops-e2e`, puertos 18100/18101) y termina con `down -v` **solo sobre su
> propio proyecto**. Pasa siempre `-p invoiceops-e2e` e ignora `COMPOSE_PROJECT_NAME`, `COMPOSE_FILE` y
> `COMPOSE_PROFILES` de su shell; si ya existen recursos de ese proyecto se detiene sin tocarlos (use `--reset`
> para borrarlos a propósito). Esta guía es la versión para recorrerla con el navegador.

## 0. Requisitos

| Qué | Para qué |
| --- | --- |
| Docker Desktop con Compose v2.24 o superior (`docker compose version`) | `include:` y el stack |
| ~10 GB libres y ~6 GB de RAM para Docker | imágenes (backend, web, MLflow, MinIO, Model API) |
| `uv` o Python 3.12 | entorno de los notebooks (`invoiceops-ml` exige `>=3.12,<3.13`) |
| Los repos lado a lado: `invoiceops-app`, `invoiceops-mlflow`, `invoiceops-model-api`, `invoiceops-ml`, `invoiceops-stack` | rutas de `local.example.env` |
| Un navegador moderno (Chrome, Edge, Firefox) | Safari puede rechazar cookies `Secure` en `http`; aquí no se usan, pero use Chrome ante dudas |

Hostnames: la app vive en `http://invoiceops.localhost:8100` y MLflow en
`http://mlflow-invoiceops.localhost:8101`. Cualquier `*.localhost` resuelve a `127.0.0.1` sin tocar
`/etc/hosts`, y al ser hosts distintos las cookies de sesión **no se mezclan**.

Todos los comandos se ejecutan en `invoiceops-stack/`. Defina un atajo (se pierde al cerrar la terminal):

```bash
cd invoiceops-stack
dc() { docker compose -p invoiceops-local -f compose.yml -f compose.local.yml --env-file local.example.env "$@"; }
```

El `-p invoiceops-local` es explícito a propósito: una variable `COMPOSE_PROJECT_NAME` exportada en su shell
(por ejemplo `invoiceops`, el proyecto de producción) tiene prioridad sobre el `name:` de `compose.local.yml`
y haría que `dc down -v` apuntara a otro proyecto. La bandera `-p` gana siempre.

### Qué cambia `compose.local.yml` en el backend

| Variable | Efecto en local | Por qué no se usa en producción |
| --- | --- | --- |
| `NODE_ENV=development` | La cookie de sesión pierde el flag `Secure` (por http el navegador no la enviaría) y el backend acepta un `MLFLOW_PUBLIC_URL` con `http://`. | Con `NODE_ENV=production` la cookie es `Secure` y el origen de MLflow debe ser `https://`. |
| `AUTH_RATE_LIMIT_DISABLED=true` | Se apaga el límite de intentos de login (5 fallos por IP cada 15 min y bloqueo progresivo por RUT); el backend lo avisa en el log al arrancar. Evita que repetir el flujo bloquee al profesor o al estudiante. | El backend se niega a arrancar si esta variable está activa con `NODE_ENV=production`: sin límite, el login queda expuesto a fuerza bruta. |

## 1. Levantar el stack

```bash
dc --profile ops build
dc up -d db
dc --profile ops run --rm migrate
dc --profile ops run --rm bootstrap
dc up -d --wait
dc ps
```

**Bootstrap** (pregunta cuatro cosas; la contraseña no se ve al escribirla):

| Pregunta | Valor |
| --- | --- |
| `Name:` | `Profesor Local` |
| `RUT:` | `11111111-1` |
| `Temporary password:` y repetirla | `Temporal-Local-2026` |

Qué esperar: `dc ps` muestra `db`, `backend`, `web`, `postgres`, `minio`, `mlflow`, `gateway` y `model-api`
en `healthy`/`running` (`minio-init` termina en `Exited (0)`, es normal). Solo `web` (`127.0.0.1:8100`) y
`gateway` (`127.0.0.1:8101`) publican puertos, ambos solo en loopback. Comprobación rápida:

```bash
curl -s http://invoiceops.localhost:8100/healthz
curl -s http://mlflow-invoiceops.localhost:8101/_gateway/healthz
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8101/api/2.0/mlflow/experiments/search
```

## 2. Profesor: login como administrador

1. Abra `http://invoiceops.localhost:8100`.
2. RUT `11111111-1`, contraseña `Temporal-Local-2026`.
3. Qué esperar: la app lo obliga a **cambiar la contraseña** (la temporal vence en 24 h). Use
   `Profesor-Local-2026`. Después entra al panel de administrador de plataforma.

## 3. Profesor: habilitar ML para una organización y dar `deployModels`

1. **Organización.** Administración → Organizaciones → crear: nombre `Curso Demo`, slug `curso-demo`, primer
   administrador `11111111-1` (usted).
2. **Activar ML.** Abra la organización y encienda el interruptor **«Sección Modelos»** (viene apagado).
   Qué esperar: el menú «Modelos» aparece para los miembros de la organización.
3. **Crear al estudiante.** Administración → Usuarios → crear: nombre `Estudiante Local`, RUT `22222222-2`,
   contraseña temporal `Temporal-Alumno-2026`, membresía en `Curso Demo` con rol `USER` y los permisos
   **leer facturas**, **crear facturas** y **«Desplegar modelos»** (`deployModels`).
   Qué esperar: el usuario queda creado con «debe cambiar contraseña». Si olvidó `deployModels`, edite la
   membresía: sin ese permiso el botón «Desplegar» queda bloqueado (403).

Cierre sesión (o use una ventana privada para el estudiante: ambos usuarios comparten el host).

## 4. Estudiante: generar el token de MLflow

1. En una ventana privada abra `http://invoiceops.localhost:8100` y entre con `22222222-2` /
   `Temporal-Alumno-2026`; cambie la contraseña a `Estudiante-Local-2026`.
2. Menú «Tokens» → **Emitir token**: organización `Curso Demo`, alcance **`mlflow:write`** (incluye lectura),
   vencimiento ≤ 30 días.
3. Qué esperar: aparece el secreto `iop_…` **una sola vez**. Cópielo; lo usará en el paso 5. No lo pegue en
   un notebook ni lo suba a git.

## 5. Estudiante: notebooks 00–05 contra el gateway local

Mientras el tag `v0.2.0` de `invoiceops-ml` no exista en GitHub, los notebooks no pueden instalarlo desde allí:
se instala desde el **checkout local** con `INVOICEOPS_ML_SPEC` (el notebook 00 lo usa en lugar de la línea
`DEFAULT_PACKAGE_SPEC`).

**Trabaje sobre una copia de los notebooks, nunca dentro del repositorio.** Al ejecutarlos Jupyter guarda las
salidas (rutas de su disco, resultados) en el `.ipynb`; en el repositorio eso ensuciaría el árbol y podría
subirse a git por error. Los notebooks del repositorio se mantienen sin salidas (`nbstripout`).

```bash
mkdir -p ~/invoiceops-curso && cd ~/invoiceops-curso
cp -R /ruta/a/invoiceops-ml/notebooks ./notebooks
uv venv --python 3.12 --seed .venv && source .venv/bin/activate
pip install -q jupyterlab ipykernel

export MLFLOW_TRACKING_URI=http://127.0.0.1:8101
export MLFLOW_TRACKING_TOKEN='iop_…pegue-aquí-su-token…'
export INVOICEOPS_ML_SPEC="$(cd /ruta/a/invoiceops-ml && pwd)"
export INVOICEOPS_WORKDIR="$HOME/invoiceops-curso/work"
export MLFLOW_DISABLE_AGENT_HINT=1

jupyter lab notebooks
```

Ejecute en orden (Run → Run All Cells en cada uno). Qué esperar:

| Notebook | Resultado |
| --- | --- |
| `00_setup` | Instala `invoiceops-ml` desde su carpeta (si lo instala en un kernel ya iniciado, avisa que reinicie el kernel y lo vuelva a ejecutar); imprime `Servidor MLflow: http://127.0.0.1:8101`, `Token configurado: sí`, `Conexión correcta` y `Workspace asignado: org-<id>` (lo pide al gateway con su token; en un MLflow sin gateway dice «no disponible»). Si falla aquí, es casi siempre el token o el puerto (ver tabla de fallas). |
| `01_dataset` | Genera el dataset sintético, muestra el desbalance y los splits sin fuga temporal; guarda en `invoiceops_work/`. |
| `02_training_pipeline` | Entrena el modelo base y los candidatos; los guarda con `skops` y verifica la recarga. |
| `03_quality_gates` | Aplica el gate: algunos modelos `PASS`, y una demostración deliberada de `FAIL`. |
| `04_mlflow` | Crea un run por modelo y **registra solo los que pasan el gate** como `invoice-rejected-classifier` `vN`. Cada versión registrada lleva el tag `val_auc`. |
| `05_deploy` | Verifica localmente la versión registrada con mayor `val_auc` (o la que escriba en `VERSION`) e imprime la versión que seleccionó: anótela, es la que se despliega en el paso 7. Si prefiere comprobarlo en la interfaz de MLflow: Models → `invoice-rejected-classifier` → la versión → pestaña de tags, donde se lee `val_auc`. |

(El `05_deploy` solo verifica y explica el despliegue; el despliegue en sí se hace desde la app, en el paso 7 de esta guía.)

Alternativa sin Jupyter, la que usa el smoke: ejecutar los cinco con `nbclient`
(`python scripts/local_e2e_smoke.py` ya lo hace por usted).

## 6. Estudiante: abrir MLflow desde la app (SSO) y ver solo su workspace

1. En la app: «Modelos» → contexto `Curso Demo` → **«Abrir MLflow en este contexto»** (abre una pestaña nueva).
2. Qué pasa por debajo: la pestaña va a `mlflow-invoiceops.localhost:8101`, el gateway no tiene sesión y
   redirige a `invoiceops.localhost:8100/api/v1/mlflow/authorize`; la app (con su cookie) devuelve un código de
   un solo uso al gateway y este abre la UI de MLflow. Cada host guarda su propia cookie.
3. Qué esperar: la UI de MLflow con **un solo workspace**, `org-<id de Curso Demo>`; ve el experimento y el
   modelo registrado del paso 5, y ningún dato de otras organizaciones.

## 7. Estudiante: desplegar desde «Modelos»

1. «Modelos» → contexto `Curso Demo`: el catálogo lista `invoice-rejected-classifier` con sus versiones.
2. **Desplegar** la versión que verificó el notebook 05 (mayor `val_auc`). El servidor ejecuta el gate oficial en la Model API (puede tardar unos
   segundos).
3. Qué esperar: el panel **«Modelo aprobado»** y, en «Despliegues», el estado `ACTIVE`. Un despliegue nuevo
   queda con el puntaje **apagado**: encienda el interruptor de la fila para que se puntúen facturas nuevas.
   Si ve «Modelo rechazado», la lista de razones del gate dice por qué.

## 8. Estudiante: crear una factura y ver su predicción

1. Facturas → Nueva factura: contexto `Curso Demo`, número `FAC-LOCAL-001`, proveedor `Proveedor local`,
   monto `125000`, estado `PENDING`, fechas coherentes (vencimiento ≥ emisión).
2. Abra el detalle de la factura. En 2–10 segundos aparece la sección **«Predicciones»** con la versión del
   modelo, el run y la **probabilidad de rechazo** (entre 0 y 1). Es el worker del backend el que consulta a
   la Model API. Una factura de un grupo recibe una predicción por cada modelo activo del grupo y de la
   organización; el panel indica «Grupo» u «Organización» en cada una.
3. Qué esperar: `modelVersion` = la versión que desplegó en el paso 7. Los estados de una predicción son `PENDING` (en cola o reintentando), `SUCCEEDED`, `FAILED` (agotó los reintentos) y `SKIPPED` (se omitió a propósito: el despliegue ya no está activo o con el puntaje apagado, ML apagado en la organización, la factura se borró o el despliegue fue rechazado). Si queda `PENDING`, `FAILED` o `SKIPPED`, mire «Fallas comunes».

## 9. Apagar y limpiar

Para detener el stack **conservando** la base de datos y los modelos:

```bash
dc --profile ops down
```

**Advertencia:** el bloque siguiente borra los volúmenes del proyecto `invoiceops-local` (la base de la app, MLflow y MinIO: usuarios, facturas y modelos registrados). Ejecútelo solo si quiere empezar de cero.

```bash
dc --profile ops down -v
```

`down -v` solo afecta a `invoiceops-local_*` (el `-p invoiceops-local` del atajo lo garantiza): los volúmenes y
redes de producción tienen otros nombres (`invoiceops_postgres_data`, `invoiceops_api`, …) y no existen en su
Mac. Para empezar de cero basta volver al paso 1 tras un `down -v`.

## Fallas comunes

| Síntoma | Causa y arreglo |
| --- | --- |
| `docker compose` dice `include` no reconocido | Compose antiguo: actualice Docker Desktop (v2.24+). |
| `no such file or directory` en la ruta de un `include` | Los repos no están lado a lado con los nombres `invoiceops-*`; ajuste `INVOICEOPS_*_DIR` en `local.example.env`. |
| `required variable … is missing` | Se olvidó `--env-file local.example.env` (use el atajo `dc`). |
| `port is already allocated` (8100/8101) | Otro proceso usa el puerto: cambie `LOCAL_APP_PORT`/`LOCAL_MLFLOW_PORT` **y** los cuatro orígenes (`PUBLIC_ORIGIN`, `APP_PUBLIC_URL`, `MLFLOW_PUBLIC_URL`, `GATEWAY_PUBLIC_ORIGIN`) en `local.example.env`. |
| `exec format error` o builds lentísimos | Plataforma incorrecta: `LOCAL_PLATFORM=linux/arm64` en Apple Silicon, `linux/amd64` en Intel. |
| `mlflow` tarda y `up --wait` caduca | Primera migración de MLflow: repita `dc up -d --wait`; mire `dc logs mlflow`. |
| `pull access denied` de MinIO | Las imágenes `pgsty/minio` y `pgsty/mc` se descargan de Docker Hub por tag y digest; sin red o con un proxy que las bloquee, defina `MINIO_IMAGE`/`MINIO_MC_IMAGE`. |
| La app dice «Mlflow sign-in is not configured» o `authorize` da 503 | Falta `MLFLOW_PUBLIC_URL` en el env del backend: use siempre `local.example.env`. |
| MLflow en el navegador da 400 al volver de la app (`redirect_uri`) | `MLFLOW_PUBLIC_URL` y `GATEWAY_PUBLIC_ORIGIN` no son idénticos (incluido el puerto). |
| La UI de MLflow pide entrar una y otra vez / 403 tras 2 intentos | Cookies viejas o sesión de la app caducada: cierre sesión en ambos hosts (borre cookies de `*.localhost`) y vuelva a entrar por la app. |
| La app responde 403 `FORBIDDEN_ORIGIN` a un `curl` | Las escrituras con cookie exigen `Origin: http://invoiceops.localhost:8100`. |
| Notebook 00: `MLFLOW_TRACKING_URI usa http:// y hay un token definido` | Usó `mlflow-invoiceops.localhost`: use `http://127.0.0.1:8101`. |
| Notebook 00: `No se pudo conectar` / 401 | Token mal copiado, sin `mlflow:write`, vencido, o revocado. Emita otro. |
| Notebook 00: `pip install` falla con la URL de GitHub / `v0.2.0` | Falta `INVOICEOPS_ML_SPEC` (el tag aún no existe): apúntelo al checkout local. |
| `Could not find a version … requires-python` | El entorno no es Python 3.12 (`uv venv --python 3.12`). |
| «Desplegar» responde 403 | La membresía del estudiante no tiene `deployModels` (paso 3). |
| «Desplegar» responde 503 | La Model API o el gateway no están sanos: `dc ps`, `dc logs model-api gateway`. |
| «Modelo rechazado» | El gate oficial rechazó la versión; las razones están en el panel. Registre otra versión (notebook 04). |
| La factura se queda sin predicción | El despliegue está `ACTIVE` pero con el puntaje apagado (interruptor del paso 7), o la Model API se reinició (`loaded: false`): vuelva a encenderlo. |
| `model-api` se reinicia (OOM) | Docker Desktop con menos de ~4 GB: súbalo en Settings → Resources. |
| `dc down -v` no borra todo | Olvidó `--profile ops` (los contenedores `migrate`/`bootstrap` de `run` ya se eliminan solos, pero el perfil evita avisos) o usó otro `LOCAL_PROJECT`. |
