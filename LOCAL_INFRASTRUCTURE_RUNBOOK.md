# Runbook local integrado de InvoiceOps

`invoiceops-stack` orquesta el orden entre repositorios. No posee la base de datos de App ni la infraestructura de MLflow: `invoiceops-app` crea, migra y carga sus datos de demostración; `invoiceops-mlflow` crea únicamente MLflow, PostgreSQL de MLflow y MinIO. MLflow es una dependencia de inferencia, no el núcleo del arranque.

Todos los comandos de este documento se ejecutan desde `apps/v2/invoiceops-stack`. No ejecute Docker ni cree la configuración privada sin la autorización operacional local correspondiente. Este runbook no autoriza operaciones remotas.

## Propiedad y persistencia

| Recurso | Dueño | Crea | No crea | Persistencia |
| --- | --- | --- | --- | --- |
| Orden, red compartida y configuración de integración | `invoiceops-stack` | La configuración privada de integración y garantiza la red externa compartida `invoiceops-local-link` por ese nombre exacto | Servicios, bases de datos, volúmenes, modelos, contratos o redes con otro nombre | El archivo privado permanece en Stack; la red exacta existe hasta que un operador la retire explícitamente |
| Infraestructura MLflow | `invoiceops-mlflow` | MLflow, PostgreSQL de MLflow, MinIO, bucket y sus volúmenes Compose | Base de datos de App, datos demo, Model API, backend o frontend | Sus propios volúmenes del proyecto `invoiceops-local-mlflow` |
| Aplicación | `invoiceops-app` | PostgreSQL de App, esquema Prisma, datos demo, Model API, backend y frontend | PostgreSQL/MinIO de MLflow, modelos entrenados o red compartida | Sus propios volúmenes del proyecto `invoiceops-local-app` |
| Entrenamiento y promoción | `invoiceops-ml` y la receta local de MLflow | Runs, Gate y alias `champion` cuando la receta lo aprueba | Servicios Docker, base App o configuración de Stack | Runs, versiones y artefactos en los volúmenes de MLflow |
| Contrato y Anvil | `invoiceops-contracts` | Procesos y despliegues efímeros de su ruta propia | Un contrato persistente para este laboratorio | Sólo durante la ejecución efímera autorizada |

La red `invoiceops-local-link` es un recurso compartido que Stack garantiza por su nombre exacto para conectar el overlay de MLflow con el overlay de App. Stack sólo reutiliza o crea esa red exacta y no modifica redes con otro nombre. Cada proyecto Compose conserva sus volúmenes con su propio prefijo de proyecto. Stack coordina esos límites, pero no se adueña de la base de datos de App ni de PostgreSQL, MinIO o los artefactos de MLflow.

## Preflight y contexto de rutas

**Dueño:** Stack.
**Crea:** variables de shell no secretas.
**No crea:** archivos, redes, volúmenes ni servicios.
**Persistencia:** sólo la shell actual.
**Evidencia esperada:** se informa que los repositorios requeridos están disponibles.

```sh
STACK="$PWD"
APP="$STACK/../invoiceops-app"
MLFLOW="$STACK/../invoiceops-mlflow"
ML="$STACK/../invoiceops-ml"
API="$STACK/../invoiceops-model-api"
CONTRACTS="$STACK/../invoiceops-contracts"
LOCAL_ENV="$STACK/.env.invoiceops-local"
if [ -d "$APP" ] && [ -d "$MLFLOW" ] && [ -d "$ML" ] && [ -d "$API" ] && [ -d "$CONTRACTS" ]; then
  printf '%s\n' 'Preflight complete: all sibling repositories are available.'
else
  printf '%s\n' 'Preflight failed: a required sibling repository is missing.' >&2
fi
```

No continúe si el preflight informa una ausencia. Las variables usan rutas explícitas derivadas de Stack; no contienen secretos.

## Crear la red compartida y la configuración privada

## Decisión crítica — crear configuración privada y red compartida

- **Acción:** crear `$LOCAL_ENV` si falta y reutilizar o crear exclusivamente `invoiceops-local-link`.
- **Impacto:** genera secretos persistentes en Stack y puede crear una red Docker compartida.
- **Quién queda autorizado:** operador local con autorización para crear configuración privada y recursos Docker locales.
- **Cómo verificar o revertir:** confirme los mensajes del bloque; la reversión del archivo o de la red requiere una decisión operacional separada, no este runbook.

**Dueño:** Stack.
**Crea:** `.env.invoiceops-local` dentro de Stack con permisos privados sólo si no existe, y la red externa exacta `invoiceops-local-link` sólo si no existe.
**No crea:** configuración en MLflow, servicios, volúmenes, bases de datos, modelos ni redes con otro nombre.
**Persistencia:** `.env.invoiceops-local` queda en Stack y la red exacta `invoiceops-local-link` queda en Docker hasta una retirada explícita.
**Evidencia esperada:** se informa de forma independiente si la configuración se creó o se conservó, y si la red exacta se creó o se reutilizó sin modificarla.

La regla existente `.env.*` de `invoiceops-stack/.gitignore` ya cubre exactamente `$STACK/.env.invoiceops-local`; no es necesario modificar `.gitignore`. Esta es la única ubicación nueva para la configuración compartida de integración.

`$MLFLOW/invoiceops-local.env` es configuración legacy creada por un runbook anterior. No la borre, no la reutilice y no la lea. La configuración y la red son guardas independientes: la ruta actual sólo crea `$LOCAL_ENV` cuando no existe y sólo reutiliza o crea la red exacta `invoiceops-local-link`.

```sh
if [ -e "$LOCAL_ENV" ]; then
  printf '%s\n' 'Stack local configuration already exists; it was not modified.'
else
  umask 077
  cat > "$LOCAL_ENV" <<EOF
LOCAL_MLFLOW_DB_PASSWORD=$(openssl rand -hex 32)
LOCAL_MINIO_USER=local-minio
LOCAL_MINIO_PASSWORD=$(openssl rand -hex 32)
MLFLOW_AUTH_ADMIN_USERNAME=local-admin
MLFLOW_AUTH_ADMIN_PASSWORD=$(openssl rand -hex 32)
MLFLOW_FLASK_SERVER_SECRET_KEY=$(openssl rand -hex 32)
LOCAL_MODEL_API_ADMIN_TOKEN=$(openssl rand -hex 32)
POSTGRES_USER=invoiceops
POSTGRES_PASSWORD=$(openssl rand -hex 32)
POSTGRES_DB=invoiceops
LOCAL_MLFLOW_PORT=5500
LOCAL_APP_DB_PORT=5545
LOCAL_BACKEND_PORT=5300
LOCAL_FRONTEND_PORT=5174
LOCAL_MODEL_API_PORT=5800
EOF
  printf '%s\n' 'Private Stack configuration was created.'
fi
if docker network inspect invoiceops-local-link >/dev/null 2>&1; then
  printf '%s\n' 'Shared network invoiceops-local-link already exists; it was not modified.'
else
  docker network create invoiceops-local-link >/dev/null
  printf '%s\n' 'Shared network invoiceops-local-link was created.'
fi
```

En una shell privada, cargue únicamente la nueva configuración de Stack antes de los pasos que consumen variables. No la imprima, copie ni inspeccione.

```sh
set -a
. "$LOCAL_ENV"
set +a
```

Si el archivo ya existía, cárguelo sin sobrescribirlo. Si la red exacta `invoiceops-local-link` ya existía, Stack la reutiliza sin modificarla; no asuma propiedad ni actúe sobre redes con otro nombre.

## Iniciar infraestructura de MLflow

## Decisión crítica — iniciar infraestructura persistente de MLflow

- **Acción:** iniciar PostgreSQL, MinIO y MLflow con sus volúmenes locales.
- **Impacto:** crea o reutiliza servicios y datos persistentes del proyecto MLflow.
- **Quién queda autorizado:** operador local autorizado para iniciar el laboratorio persistente.
- **Cómo verificar o revertir:** compruebe `ps` y el estado saludable; la parada documentada al final detiene los servicios y conserva los volúmenes.

**Dueño:** `invoiceops-mlflow`.
**Crea:** los servicios PostgreSQL de MLflow, MinIO, inicializador de bucket y MLflow, más sus volúmenes del proyecto `invoiceops-local-mlflow`.
**No crea:** base de App, migraciones Prisma, demo, Model API, backend ni frontend.
**Persistencia:** metadata y autorización de MLflow en su PostgreSQL; artefactos en su MinIO.
**Evidencia esperada:** `postgres`, `minio` y `mlflow` saludables; `minio-init` completado correctamente.

```sh
docker compose -p invoiceops-local-mlflow --env-file "$LOCAL_ENV" -f "$MLFLOW/compose.yml" -f "$MLFLOW/compose.invoiceops-local.yml" up --build -d
docker compose -p invoiceops-local-mlflow --env-file "$LOCAL_ENV" -f "$MLFLOW/compose.yml" -f "$MLFLOW/compose.invoiceops-local.yml" ps
```

El primer Compose incluye los dos archivos que pertenecen a MLflow. El overlay conecta MLflow a la red compartida de Stack y conserva los volúmenes propios del proyecto MLflow.

No avance si la fila de `mlflow` muestra `health: starting`. Espere manualmente unos segundos y vuelva a ejecutar el siguiente comando desde Stack:

```sh
docker compose -p invoiceops-local-mlflow --env-file "$LOCAL_ENV" -f "$MLFLOW/compose.yml" -f "$MLFLOW/compose.invoiceops-local.yml" ps
```

Tras cada espera manual breve, vuelva a consultar `ps`. Continúe únicamente cuando `mlflow` figure como `healthy`; no ejecute todavía la verificación HTTP mientras esté iniciando.

## Verificar la frontera de MLflow

**Dueño:** `invoiceops-mlflow`.
**Crea:** nada.
**No crea:** runs, versiones, aliases, Workspaces ni recursos de App.
**Persistencia:** ninguna.
**Evidencia esperada:** `401`, que acredita rechazo de acceso anónimo y no acredita entrenamiento, promoción ni inferencia.

```sh
curl -sS -o /dev/null -w '%{http_code}\n' "http://127.0.0.1:${LOCAL_MLFLOW_PORT:-5500}/"
```

Ejecute este `curl` sólo después de confirmar que `mlflow` está `healthy`. Espere `401`, que acredita el rechazo de acceso anónimo. Si, aun estando `healthy`, el comando no devuelve `401`, inspeccione los logs de MLflow antes de continuar:

```sh
docker compose -p invoiceops-local-mlflow --env-file "$LOCAL_ENV" -f "$MLFLOW/compose.yml" -f "$MLFLOW/compose.invoiceops-local.yml" logs mlflow
```

## Iniciar sólo la base de datos de App

## Decisión crítica — iniciar base de datos persistente de App

- **Acción:** iniciar sólo `db` de App.
- **Impacto:** crea o reutiliza el volumen persistente de datos de App.
- **Quién queda autorizado:** operador local autorizado para iniciar el laboratorio persistente.
- **Cómo verificar o revertir:** confirme que sólo `db` se inició; la parada documentada al final conserva el volumen.

**Dueño:** `invoiceops-app`.
**Crea:** solamente el servicio `db` y su volumen del proyecto `invoiceops-local-app`.
**No crea:** backend, frontend, Model API, migraciones, demo ni servicios MLflow.
**Persistencia:** los datos de App quedan en el volumen de App; `down` sin volúmenes los conserva.
**Evidencia esperada:** sólo se crea o inicia `db`.

```sh
docker compose -p invoiceops-local-app --env-file "$LOCAL_ENV" -f "$APP/docker-compose.yml" -f "$APP/compose.invoiceops-local.yml" up -d db
```

## Migrar y cargar la demostración de App

## Decisión crítica — migrar esquema y restablecer datos demo

- **Acción:** aplicar migraciones Prisma y cargar o restablecer APP-07.
- **Impacto:** modifica de forma persistente el esquema y los datos de la base local de App; las dos facturas demo vuelven a `PENDING`.
- **Quién queda autorizado:** operador local autorizado para cambiar la base de datos local `invoiceops`.
- **Cómo verificar o revertir:** confirme la salida de Prisma y `APP-07 local demonstration data is ready.`; este runbook no publica una reversión de migraciones, y repetir la demo sólo restablece sus datos sintéticos.

**Dueño:** `invoiceops-app`.
**Crea:** el esquema Prisma y los datos sintéticos APP-07 en la base de App.
**No crea:** organizaciones mediante HTTP/UI, grupos, un administrador global, un modelo ni recursos MLflow.
**Persistencia:** esquema y datos demo permanecen en el volumen de App.
**Evidencia esperada:** Prisma aplica las migraciones pendientes y la demo informa `APP-07 local demonstration data is ready.`

El comando `local-demonstration` crea o restablece exclusivamente los datos sintéticos APP-07 en la base de datos local `invoiceops`. Sirve para probar decisiones repetibles: al volver a ejecutarlo, restablece sólo las dos facturas indicadas a estado `PENDING`.

| Recurso | Datos |
| --- | --- |
| Organización | `APP-07 Local Demonstration` / slug `app-07-local-demonstration` |
| Usuario | `APP-07 Demonstration User`, RUT `111111111` |
| Membership | `STUDENT` en esa organización |
| Política | `ml-policy-v1`, ownership individual del usuario, threshold `0.8` |
| Facturas PENDING | `DEMO-RULE-AUTO-POLICY-MANUAL` y `DEMO-RULE-MANUAL-POLICY-AUTO` |

No crea administrador global, grupos, invitados u otros usuarios, modelo o champion, evidence batches, anclajes ni Anvil. Está limitado a `NODE_ENV=development` y a la base de datos local `invoiceops`.

```sh
export DATABASE_URL="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@127.0.0.1:${LOCAL_APP_DB_PORT:-5545}/${POSTGRES_DB}?schema=public"
pnpm -C "$APP" --filter @invoiceops/backend exec prisma migrate deploy
NODE_ENV=development pnpm -C "$APP" --filter @invoiceops/backend local-demonstration
```

`DATABASE_URL` sólo existe en la shell privada y apunta a la base de App publicada en loopback. No la muestre ni la escriba en otro archivo.

## Obtener identificadores de demostración

**Dueño:** `invoiceops-app`.
**Crea:** variables de shell `USER_ID` y `ORG_ID`.
**No crea:** usuarios, organizaciones, memberships, grupos ni roles.
**Persistencia:** sólo la shell actual.
**Evidencia esperada:** ambos UUID quedan disponibles para el bootstrap del administrador.

```sh
USER_ID=$(docker compose -p invoiceops-local-app --env-file "$LOCAL_ENV" -f "$APP/docker-compose.yml" -f "$APP/compose.invoiceops-local.yml" exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc 'SELECT id FROM "User" WHERE rut = '\''111111111'\''')
ORG_ID=$(docker compose -p invoiceops-local-app --env-file "$LOCAL_ENV" -f "$APP/docker-compose.yml" -f "$APP/compose.invoiceops-local.yml" exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc 'SELECT id FROM "Organization" WHERE slug = '\''app-07-local-demonstration'\''')
if [ -n "$USER_ID" ] && [ -n "$ORG_ID" ]; then
  printf '%s\n' 'Demonstration identifiers are available for administrator bootstrap.'
else
  printf '%s\n' 'Demonstration identifiers are missing; do not bootstrap an administrator.' >&2
fi
```

No continúe si faltan identificadores. La demo crea el usuario sintético y la organización fija, pero no concede privilegios administrativos.

## Entrenar y promover el champion

## Decisión crítica — promover el champion de inferencia

- **Acción:** ejecutar la receta propietaria que puede asignar el alias `invoice-review-production@champion`.
- **Impacto:** persiste runs, artefactos, versión y el alias que consumirá el release smoke local.
- **Quién queda autorizado:** operador local autorizado para ejecutar la receta de entrenamiento y Gate de MLflow.
- **Cómo verificar o revertir:** conserve y valide `RUN_ID` y `MODEL_VERSION`; la receta propietaria define cualquier reversión, por lo que este runbook no inventa una promoción inversa.

**Dueño:** `invoiceops-ml` junto con la receta propietaria de `invoiceops-mlflow`.
**Crea:** candidatos, Gate, versión y alias `invoice-review-production@champion` sólo cuando la receta completa lo aprueba.
**No crea:** servicios Docker, datos demo, grupos ni configuración Stack.
**Persistencia:** runs, artefactos, versión y alias quedan en los volúmenes de MLflow.
**Evidencia esperada:** `RUN_ID` y `MODEL_VERSION` corresponden al mismo HGB aprobado y al alias `champion`.

La fuente propietaria vigente es [`$MLFLOW/LOCAL_INVOICEOPS_RUNBOOK.md`](../invoiceops-mlflow/LOCAL_INVOICEOPS_RUNBOOK.md), sección **“Train existing local candidates; promote only a Gate PASS”**. Siga esa sección completa en una shell privada con el contexto ya obtenido en este runbook. No copie su configuración legacy ni ejecute su preparación de entorno: use `$LOCAL_ENV` cargado desde Stack y conserve este directorio como punto de inicio.

La receta ejecuta el candidato RF sólo como evidencia, corre los notebooks canónicos y promueve exclusivamente un `HistGradientBoosting` con ownership exacto, artefacto `model`, estado finalizado y Gate aprobado. Si no hay candidato elegible, no inicie Model API, backend ni frontend. Guarde `RUN_ID` y `MODEL_VERSION` en la shell actual sin mostrarlos junto con secretos.

## Checkpoint de terminal obligatorio — promoción disponible para el release smoke

Antes del release smoke, confirme en la misma shell que ejecutó la promoción que ambos identificadores existen:

```sh
if [ -n "${RUN_ID:-}" ] && [ -n "${MODEL_VERSION:-}" ]; then
  printf '%s\n' 'Promotion checkpoint passed: RUN_ID and MODEL_VERSION are available in this shell.'
else
  printf '%s\n' 'Promotion checkpoint failed: RUN_ID or MODEL_VERSION is unavailable in this shell.' >&2
fi
```

No continúe al release smoke si este checkpoint falla. El error `Missing required environment variable: MODEL_RELEASE_EXPECTED_MODEL_VERSION` significa que la promoción no se ejecutó o no dejó sus variables en la shell actual; no indica que el backend esté caído.

## Iniciar Model API, backend y frontend

## Decisión crítica — iniciar servicios de aplicación

- **Acción:** iniciar Model API, backend y frontend contra los datos locales existentes.
- **Impacto:** crea o reutiliza contenedores de aplicación y puede consumir el champion promovido.
- **Quién queda autorizado:** operador local autorizado para iniciar el laboratorio persistente.
- **Cómo verificar o revertir:** `up --wait` debe completar; la parada documentada al final detiene estos servicios y conserva los datos.

**Dueño:** `invoiceops-app`; la imagen y el release smoke pertenecen a `invoiceops-model-api`.
**Crea:** Model API, backend y frontend de App, usando la base App existente y MLflow mediante la red compartida.
**No crea:** una nueva base App, servicios MLflow, datos demo, un modelo, un alias ni grupos.
**Persistencia:** conserva los volúmenes existentes de App y MLflow; estos tres servicios no añaden volúmenes propios en este flujo.
**Evidencia esperada:** `up --wait` completa y acredita únicamente el liveness del proceso Model API.

```sh
docker compose -p invoiceops-local-app --env-file "$LOCAL_ENV" -f "$APP/docker-compose.yml" -f "$APP/compose.invoiceops-local.yml" up --build --wait --wait-timeout 180 model-api backend frontend
```

No ejecute los checks siguientes si Compose no informa servicios saludables. El healthcheck de Compose de Model API consulta `GET /health` y sólo acredita el liveness del proceso; no valida `invoice-review` ni la presencia de un champion. La carga del champion se acredita después mediante el release smoke y `GET /models/invoice-review/health`.

### Checkpoint en App — liveness de UI y sesión demo

- **Prerrequisito:** `model-api`, `backend` y `frontend` terminaron `up --wait` correctamente; se cargaron los datos demo APP-07.
- **URL local:** `http://127.0.0.1:5174`.
- **Acción del usuario:** abra la URL, inicie sesión con RUT `111111111` y contraseña `app-07-local-demonstration`, y abra **Mi perfil**.
- **Resultado observable:** la interfaz carga y **Mi perfil** muestra la identidad del usuario demo y su membresía de `APP-07 Local Demonstration` sin pedir autenticación de nuevo.
- **Qué acredita / no acredita:** acredita liveness de frontend/backend a través de la UI y una sesión cookie válida. No acredita que exista, se haya cargado o se pueda invocar un modelo.

## Bootstrap del administrador global

## Decisión crítica — elevar el usuario demo a administrador global

- **Acción:** elevar a `USER_ID`, creado por el seed como `STUDENT`, a administrador global de plataforma.
- **Impacto:** el privilegio persiste en la base local; esa persona verá **Administración** y podrá crear organizaciones, aprovisionar usuarios y administrar grupos en cualquier organización.
- **Quién queda autorizado:** sólo el operador local que decide usar el bootstrap rápido para el usuario demo.
- **Cómo verificar o revertir:** confirme el checkpoint de App y la capacidad administrativa observable; es válido sólo para laboratorio rápido. Para cambiar de persona, use la transferencia explícita al nuevo usuario; no hay aquí un comando publicado de eliminación.

**Dueño:** `invoiceops-app`.
**Crea:** el primer registro de administrador de plataforma para `USER_ID`.
**No crea:** una organización, un grupo, usuarios MLflow ni permisos MLflow.
**Persistencia:** el privilegio se guarda en la base de App.
**Evidencia esperada:** el CLI completa y el usuario puede autenticarse y consultar organizaciones de plataforma.

```sh
pnpm -C "$APP" --filter @invoiceops/backend platform-administrator bootstrap "$USER_ID" --confirm-bootstrap
```

Si ya hay administrador, no repita este bootstrap. Una transferencia posterior exige el comando propietario `platform-administrator transfer <userId> --confirm-transfer`; es la alternativa actual para cambiar de persona.

### Checkpoint en App — privilegio de administrador de plataforma

- **Prerrequisito:** el bootstrap terminó correctamente; frontend y backend están iniciados; la sesión corresponde al usuario demo bootstrapado.
- **URL local:** `http://127.0.0.1:5174`.
- **Acción del usuario:** inicie sesión con RUT `111111111` y contraseña `app-07-local-demonstration`; confirme la insignia **Administrador de plataforma** en **Mi perfil** y abra **Administración**.
- **Resultado observable:** la insignia es visible y **Administración** muestra **Membresías organizacionales**; esa área no aparece para un usuario sin ese privilegio.
- **Qué acredita / no acredita:** acredita que la sesión autenticada recibió el privilegio de plataforma y que la UI expone su área administrativa. La pantalla por sí sola no prueba que pueda crear organizaciones, aprovisionar usuarios o administrar grupos: esas capacidades se prueban sólo al completar una operación autorizada y observar el recurso o cambio resultante. No acredita transferencia de administrador ni acceso a datos de negocio fuera del contexto autorizado.

## Verificar release, App y frontend

**Dueño:** Model API para release; App para liveness y decisión.
**Crea:** el refresh temporal del proceso Model API cuando el smoke lo solicita.
**No crea:** un nuevo champion, versiones, migraciones ni datos demo.
**Persistencia:** el alias y los datos previos no cambian; el proceso Model API activo puede recargar el mismo champion.
**Evidencia esperada:** el smoke valida `refresh → health → predict` con el `RUN_ID` y `MODEL_VERSION` promovidos; liveness devuelve `{"status":"ok"}`.

```sh
export MODEL_API_BASE_URL="http://127.0.0.1:${LOCAL_MODEL_API_PORT:-5800}"
export MODEL_API_ADMIN_TOKEN="$LOCAL_MODEL_API_ADMIN_TOKEN"
export MODEL_RELEASE_EXPECTED_MODEL_VERSION="$MODEL_VERSION"
export MODEL_RELEASE_EXPECTED_RUN_ID="$RUN_ID"
MODEL_RELEASE_HTTP_TIMEOUT_SECONDS=45 python3 "$API/scripts/model_release_smoke.py"
curl -fsS "http://127.0.0.1:${LOCAL_BACKEND_PORT:-5300}/health/live"
```

Abra `http://127.0.0.1:${LOCAL_FRONTEND_PORT:-5174}` y autentíquese con el usuario demo. En **Facturas**, abra `DEMO-RULE-AUTO-POLICY-MANUAL`, seleccione **Evaluación ML** y aplique la política `ml-policy-v1`. Una decisión de factura demo debe informar `policyProbabilitySource=MODEL_API`, una probabilidad finita y el mismo run y versión del champion. `MODEL_API_FALLBACK` no acredita inferencia.

### Checkpoint en App — decisión respaldada por modelo

- **Prerrequisito:** el release smoke terminó correctamente, el checkpoint de liveness de App pasó y la factura demo sigue `PENDING` antes de decidirla.
- **URL local:** `http://127.0.0.1:5174`.
- **Acción del usuario:** con el contexto individual de `APP-07 Local Demonstration`, abra la factura `DEMO-RULE-AUTO-POLICY-MANUAL`, use **Evaluación ML** con `ml-policy-v1` y consulte la decisión y su auditoría.
- **Resultado observable:** la UI muestra una probabilidad finita, origen **Modelo mediante servidor** (`MODEL_API`) e identificadores de modelo, ejecución y versión coherentes con el champion promovido. Si muestra `MODEL_API_FALLBACK`, la decisión no satisface este checkpoint.
- **Qué acredita / no acredita:** acredita que esa decisión visible recibió inferencia de Model API y conserva su linaje en App. No acredita entrenamiento, promoción, disponibilidad futura del modelo ni inferencia para otras facturas o propietarios.

## Ruta smoke efímera

## Decisión crítica — ejecutar smoke integrado desechable

- **Acción:** ejecutar pruebas y el smoke que crea recursos Docker aislados, Anvil, contrato y evidencia efímera.
- **Impacto:** inicia y luego limpia recursos propios de `stack08-proof-*`; conserva el recibo sanitizado indicado.
- **Quién queda autorizado:** operador local autorizado para ejecutar recursos Docker y procesos efímeros del laboratorio.
- **Cómo verificar o revertir:** compruebe que todas las fases, incluido `cleanup`, figuran `PASS` en el recibo; no hay reversión manual documentada porque el arnés limpia sus propios recursos.

**Dueño:** Stack.
**Crea:** un proyecto aislado `stack08-proof-*`, sus recursos Docker propios, datos sintéticos, Anvil, contrato, evidencia y recibo sanitizado.
**No crea:** una instalación persistente, configuración reutilizable, grupos operables ni un entorno que deba continuar tras el smoke.
**Persistencia:** sólo el recibo sanitizado en `evidence/`; el arnés limpia sus recursos propios.
**Evidencia esperada:** todas las fases y `cleanup` en `PASS` dentro del recibo.

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests/test_stack_invoiceops_app_config.py tests/test_stack_invoiceops_app_smoke.py -v
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/stack_integrated_smoke.py --execute --notebook-champion --project stack08-proof-local001 --receipt "$STACK/evidence/stack08-proof-local001.json"
```

Esta ruta es desechable y usa un owner individual sintético. No sustituye el laboratorio persistente ni acredita administración, selección de modelo o serving por grupo.

## Grupos, ML, evidencia y anclaje

### Grupos y ownership

## Decisión crítica — crear o administrar grupos organizacionales

- **Acción:** crear grupos o cambiar sus memberships dentro de una organización existente.
- **Impacto:** persiste grupos y memberships en la base de App y puede cambiar el acceso al contexto de trabajo grupal.
- **Quién queda autorizado:** administrador global en cualquier organización, o administrador organizacional sólo en sus organizaciones; `STUDENT` no crea ni administra grupos.
- **Cómo verificar o revertir:** confirme el código HTTP documentado y el roster o grupo resultante; revierta una membership con el endpoint de quitar integrante cuando corresponda. Este runbook no publica eliminación de grupos.

| Rol | Organizaciones y usuarios | Grupos | Contexto de trabajo grupal |
| --- | --- | --- | --- |
| Administrador global | Crea organizaciones y aprovisiona usuarios con alcance transversal | Administra grupos en cualquier organización | Puede administrar una organización sin adquirir automáticamente contexto de trabajo del grupo; necesita ser integrante y seleccionar el grupo para trabajar en él |
| Administrador organizacional | No crea organizaciones ni aprovisiona usuarios fuera de su alcance | Crea y administra grupos sólo dentro de sus organizaciones | Requiere membresía y selección del grupo para su contexto de trabajo |
| Usuario normal / `STUDENT` | No crea organizaciones ni aprovisiona usuarios | No crea ni administra grupos | Puede usar únicamente los contextos para los que tenga membresía y selección válida |

**Dueño:** `invoiceops-app`.
**Crea:** grupos y memberships sólo por administradores autorizados de una organización existente.
**No crea:** organizaciones desde UI/API, invitaciones, un champion por grupo ni usuarios MLflow automáticamente.
**Persistencia:** grupos y memberships quedan en la base de App.
**Evidencia esperada:** `GET /groups` muestra las memberships del actor y las mutaciones devuelven sus códigos HTTP documentados.

Un administrador global bootstrapado puede administrar la organización demo, pero no obtiene por ello contexto de trabajo de un grupo: debe ser integrante y seleccionarlo. Los grupos usan `Group.id` como identidad canónica: el contexto técnico es `ownerType=group` y `ownerId=<GROUP_ID>`. `Group.name` no es identidad técnica. Este runbook no documenta comandos de creación de organizaciones ni aprovisionamiento de usuarios.

| Operación | Endpoint | Éxito |
| --- | --- | --- |
| Listar mis grupos | `GET /groups` | `200` con grupos donde el actor es integrante |
| Catálogo administrable | `GET /organizations/:organizationId/groups?limit=50` | `200` paginado |
| Crear grupo | `POST /organizations/:organizationId/groups` | `201` y `group.id` |
| Añadir integrante | `POST /organizations/:organizationId/groups/:groupId/members` | `200` y roster actualizado |
| Quitar integrante | `DELETE /organizations/:organizationId/groups/:groupId/members/:userId` | `204` |

### Checkpoint en App — contexto y membresía de grupo

- **Prerrequisito:** existe un grupo creado en `APP-07 Local Demonstration` y el usuario autenticado es integrante; el creador ya es integrante al crear el grupo.
- **URL local:** `http://127.0.0.1:5174`.
- **Acción del usuario:** abra **Mis grupos**, confirme que el grupo aparece en sus membresías y seleccione **Trabajar con…** para ese grupo.
- **Resultado observable:** el grupo se muestra en la lista de membresías y el resumen **Contexto de trabajo** mantiene visibles la organización y el propietario grupal seleccionado al navegar.
- **Qué acredita / no acredita:** acredita que la UI recibió la membresía del actor y aplicó el contexto de grupo seleccionado. No acredita identidad técnica por nombre, aprovisionamiento MLflow, champion por grupo ni permisos para miembros ajenos.

### ML y evidencia

Los recursos owner-scoped de ML conservan los tags `organization_slug`, `owner_type`, `owner_id` y `created_by_rut`. Para grupos, los nombres canónicos son `group/<Group.id>/invoice-risk` y `group-<Group.id>-invoice-review`, con rol `group-<Group.id>`. El champion del laboratorio sigue siendo global: `invoice-review-production@champion`; no existe selección ni serving por grupo.

Una decisión App crea un evidence record. La App permite leer `GET /evidence-records`, `GET /evidence-batches` y pruebas de lote, bajo contexto válido de organización y owner. No existe una operación UI/HTTP publicada para crear batches.

### Checkpoint en App — lectura de evidencia, lotes y pruebas

- **Prerrequisito:** se tomó una decisión de factura en el contexto activo, existe un evidence record accesible y hay un lote accesible que incluya un record; la UI no crea batches.
- **URL local:** `http://127.0.0.1:5174`.
- **Acción del usuario:** abra **Evidencia y lotes**, filtre o abra el record de la decisión, abra un lote disponible y solicite la prueba de un record incluido.
- **Resultado observable:** la UI muestra el detalle del record, el detalle del lote y la prueba solicitada bajo el mismo contexto de organización y propietario; si no hay lote, la vista lo informa sin ofrecer creación.
- **Qué acredita / no acredita:** acredita las lecturas autorizadas de records, batches y pruebas que la UI expone. No acredita creación de batches, firma, anclaje, reconciliación ni finalidad de cadena.

### Anclaje

## Decisión crítica — anclar o reconciliar evidencia

- **Acción:** enviar una raíz de evidencia o reconciliar su observación para un batch y owner existentes.
- **Impacto:** puede crear observaciones, reservas o transacciones según la configuración autorizada.
- **Quién queda autorizado:** operador con `APP_SESSION_TOKEN`, valores privados requeridos y autorización para el batch, organización y owner indicados.
- **Cómo verificar o revertir:** compruebe la salida acotada del CLI y las observaciones de reconcile; `observedSuccess` no es finalidad y este runbook no publica una reversión de cadena.

**Dueño:** `invoiceops-app` para el CLI de evidencia y `invoiceops-contracts` para Anvil/contrato.
**Crea:** el CLI puede enviar o reconciliar una raíz sólo cuando ya existen batch, reserva, sesión y configuración privada autorizada.
**No crea:** batches desde este runbook, contratos persistentes, explorer local ni finalidad de cadena.
**Persistencia:** observaciones y reservas pertenecen a App; un despliegue Anvil manual pertenece a Contracts.
**Evidencia esperada:** resultados acotados del CLI y observaciones de reconcile; `observedSuccess` no equivale a finalidad.

```sh
pnpm -C "$APP" --filter @invoiceops/backend evidence-anchor anchor --batch-id BATCH_ID --organization-id ORG_ID --owner-type group --owner-id GROUP_ID
pnpm -C "$APP" --filter @invoiceops/backend evidence-anchor reconcile --batch-id BATCH_ID --organization-id ORG_ID --owner-type group --owner-id GROUP_ID --reservation-id RESERVATION_ID
```

Estos comandos requieren `APP_SESSION_TOKEN` y, para `anchor`, los valores privados de EVM. No incluya secretos en argumentos, registros ni archivos compartidos. La receta de Anvil y `EvidenceRootAnchor` pertenece a [`$CONTRACTS/README.md`](../invoiceops-contracts/README.md). No hay explorer local ni flujo persistente publicado de despliegue y asociación de contrato.

## Brechas vigentes

- Este runbook no documenta comandos de creación de organizaciones, aprovisionamiento de usuarios ni invitaciones.
- No existe promoción, alias champion, selección o serving por grupo.
- No hay UI/HTTP para crear evidence batches ni ejecutar anclaje o reconciliación.
- No hay procedimiento persistente publicado para desplegar y asociar un contrato local.
- No hay explorer local de transacciones, eventos o raíces.
- El smoke efímero no acredita un flujo E2E grupal.

## Parada segura del laboratorio persistente

## Decisión crítica — detener el laboratorio persistente

- **Acción:** detener los servicios Compose de App y MLflow sin eliminar volúmenes.
- **Impacto:** interrumpe la disponibilidad local de App, Model API y MLflow, pero conserva sus datos persistentes.
- **Quién queda autorizado:** operador local autorizado para detener el laboratorio compartido en esa máquina.
- **Cómo verificar o revertir:** confirme que ambos Compose ya no muestran servicios en ejecución; reinicie las secciones de arranque correspondientes para volver a levantar el laboratorio.

**Dueño:** cada proyecto Compose.
**Crea:** nada.
**No crea:** borrado de volúmenes, red compartida, artefactos ni recursos de otros proyectos.
**Persistencia:** los volúmenes de App y MLflow se conservan.
**Evidencia esperada:** servicios detenidos sin eliminar datos.

```sh
docker compose -p invoiceops-local-app --env-file "$LOCAL_ENV" -f "$APP/docker-compose.yml" -f "$APP/compose.invoiceops-local.yml" down
docker compose -p invoiceops-local-mlflow --env-file "$LOCAL_ENV" -f "$MLFLOW/compose.yml" -f "$MLFLOW/compose.invoiceops-local.yml" down
```

No use eliminación de volúmenes, limpieza amplia ni acciones sobre la red compartida sin una decisión operacional separada.
