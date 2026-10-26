# invoiceops-stack

## Entrada de bootstrap local

La guía vigente para un entorno limpio y desechable es
[LOCAL_INFRASTRUCTURE_RUNBOOK.md](LOCAL_INFRASTRUCTURE_RUNBOOK.md). Este README
conserva contratos y evidencia de los arneses propietarios; el runbook central
define el orden entre repositorios, límites de secretos, validación integrada y
continuidad. No declara preparación ni validación remota.

## STACK-08 — smoke integrado local y offline

`scripts/stack_integrated_smoke.py` es un arnés **opt-in**: sólo actúa con
`--execute` y un proyecto nuevo `stack08-proof-*`. Construye explícitamente,
bajo ese proyecto, las capas locales de MLflow, Model API y App; después inicia
siempre con `--no-build --pull never`, verifica salud, prepara el fixture de
modelo, ejecuta una predicción y escribe un recibo sanitizado con permisos
`0600`. No se ejecuta automáticamente como parte de la validación estática.

### INT-06 — modo notebook → run → champion

`--notebook-champion` es un opt-in adicional y no cambia el contrato histórico
de `STACK-08`: sin esa bandera el arnés conserva el fixture existente. Con ella,
el arnés ejecuta exclusivamente el notebook canónico HGB de `../invoiceops-ml`,
obtiene el `run_id` desde una consulta estructurada de MLflow y llama al selector
canónico `invoiceops-promote-local`. Sólo continúa si el selector confirma Gate
PASS, `invoice-review-production@champion`, el mismo `run_id` y una versión no
vacía. Antes de crear Evidence o iniciar Anvil, exige que health y predict de
Model API, y el linaje de la decisión App, reporten exactamente ese `run_id` y
versión. El subproceso de notebook se ejecuta en su propio grupo de procesos;
ante timeout termina y recolecta ese grupo de forma acotada antes de fallar.
Requiere el entorno local ya preparado en
`../invoiceops-ml/.venv`; no instala dependencias ni interpreta stdout de un
notebook.

### Cierre local completo: STACK-08 (2026-10-24)

El subflujo de fixture MLflow queda **RESOLVED_LOCAL_VALIDATED**. El arnés
reenvía las credenciales de lector existentes sólo al bootstrap que crea el
usuario y su rol de lectura; para el único fixture que escribe experimento, run,
artefactos, versión y alias, reenvía temporalmente las credenciales de
administración sólo al contenedor efímero. Los valores nunca aparecen como
argumentos, recibos ni salida retenida.

La investigación aislada confirmó que el backend de App expone liveness JSON en
`/health/live`; la ruta anterior `/health` no satisfacía ese contrato. El arnés
verifica ahora esa ruta, crea una decisión respaldada por Model API, construye su
lote de evidencia, despliega el contrato en Anvil local y ejecuta
`anchor → reconcile`.

La ejecución fresca `stack08-proof-finalpass1024` aprobó todas las fases:
`preflight`, `build`, `anvil`, `contract`, `mlflow`, `model_api`, `app_schema`,
`app`, `prediction`, `evidence` y `anchor_reconcile`. El recibo
[`evidence/stack08-proof-finalpass1024.json`](evidence/stack08-proof-finalpass1024.json)
es `PASS`, no retiene salidas ni secretos, y la reinspección posterior confirmó la
ausencia de contenedores, red, volúmenes e imágenes derivadas del proyecto exacto.

**STACK-08 queda CLOSED / COMPLETED_LOCAL_VALIDATED**. Stack pasa a
**6/9 (66.7%)**. La evidencia acredita únicamente este entorno local aislado: no
certifica CI, publicación, despliegue, Anchor remoto ni una cadena pública.

El entorno hijo mantiene un `PATH` restringido y sin variables del operador.
Para localizar el CLI, el arnés acepta únicamente un ejecutable `docker`
ejecutable en directorios conocidos, incluido `/usr/local/bin`; no hereda el
`PATH` ambiental. Si no puede localizarlo, falla con `docker_cli_not_found`.

Antes de construir, el arnés inspecciona las bases declaradas en los Dockerfile.
Sin `--allow-network-build`, si falta una imagen base o una capa de dependencias
en la caché local termina con `offline_build_cache_miss`: el preflight offline
permanece *fail-closed*.

Para el build local autorizado que necesita recuperar esas entradas, use
`--execute --allow-network-build`. Este segundo flag es un opt-in explícito y
estrecho: permite continuar únicamente con ese build local. Bajo la autorización
operativa correspondiente, las dependencias previstas pueden contactar de forma
anónima Docker Hub, GHCR, PyPI y registry.npmjs.org. El arnés no implementa una
allowlist técnica de destinos; su aislamiento se limita a no iniciar login,
credenciales, push, despliegue remoto ni Anchor remoto, y a no leer `.env`. Anvil,
Foundry y Anchor se ejecutan exclusivamente dentro del proyecto local aislado. Tras el
build, el runtime conserva el proyecto `stack08-proof-*` aislado, inicia con
`--no-build --pull never` y el cleanup mantiene su comportamiento *fail-closed*.

No existe pipeline, deploy ni registry de imágenes de InvoiceOps en este
repositorio. Las etiquetas derivadas por Compose, por ejemplo
`stack08-proof-example-mlflow:latest`, son artefactos locales y efímeros del
proyecto de prueba; no son imágenes publicadas. Para reanudar, use otro proyecto
`stack08-proof-*` y otro recibo nuevo después de restaurar las bases/capas en
caché. El cleanup falla cerrado: elimina sólo identidades con la etiqueta Compose
del proyecto exacto y las etiquetas derivadas exactas tras comprobar por image ID
que ningún contenedor las referencia; no usa `down`, `prune` ni recursos
`invoiceops-local-*`.

**Último estado (2026-10-20): CR-013 RESOLVED_LOCAL_VERIFIED**, tras revisión
independiente. Todos los checks de cierre reciben contexto de cleanup; la prueba
reserva 120 s y el cierre se limita al deadline absoluto de 1800 s. Regresiones con
reloj virtual a 1679/1680/1690 s, post-`down` a 1795 s y sin comandos a 1800 s;
identidad e incertidumbre intactas. No se ejecutó un runtime real de 1800 s.
Stack completo **124/124 PASS**, sin skips, 16.893 s unittest/17.179 s proceso:
adaptador en memoria, 37 consultas reales Compose config y una versión exacta,
configuración anónima sin motor/servicios; no es un nuevo ops08 full-stack.

MLFLOW-07 conserva **COMPLETED_LOCAL_VALIDATED**, MLflow **7/9 (77.8%)**; Stack
**3/9 (33.3%)** sin tickets adicionales. [Ledger final de los ocho hallazgos,
gates y continuidad](../dev/tickets/Implementado.md#cierre-final-de-remediación-local--cr-013-a-cr-019-y-cr-030-2026-10-20).
CR-030 separa preparación explícita de imágenes y arranque sólo caché; nueva
prueba local `147273ec3819672ab9fd67b6` y preparación fallida
`642a7929350b5bbeb94e3ce2` auditadas sin recursos propios restantes. Ese fallo
conserva causa UNKNOWN y `cleaned_unverified`, no PASS. Los ocho hallazgos están
RESOLVED_LOCAL_VERIFIED, CR-017 en alcance de mitigación sin causa histórica probada.
Las revisiones y conteos inferiores son históricos; CR-013 no sigue pendiente.
Si se requiere, siguiente paso: delta `ff-change-review` del lote completo;
preparar commit sólo a petición del usuario, sin staging ni commit automático.
Producto requiere decisión por APP-11/CR-004 → APP-12 → APP-13 → STACK-03,
Remix y remoto diferidos. No se inicia trabajo ni operación nueva.

**Estado vigente:** STACK-02 permanece **COMPLETED localmente — VALIDATED**. STACK-03
está **CLOSED / COMPLETED_LOCAL_VALIDATED** por el recibo sanitizado y rastreado
[`evidence/stack03/STACK-03-CR-035-PASS.json`](evidence/stack03/STACK-03-CR-035-PASS.json),
SHA-256 `0bb4db101a92b968c904b4d2bc36cd06c981653e6e967dc629501518b54ac608`:
el proyecto nuevo `stack03-proof-cr035aa` aprobó todas las fases y cuatro aserciones
de flujo; su cierre retiró exactamente 8 contenedores, 1 red, 3 volúmenes y 5
imágenes propios. Esquema `STACK03-receipt-v3` enlaza revisión/hash deterministas
del arnés/Compose y de los contextos consumidos de App, Model API y MLflow. Stack queda
en **4/9 (44.4%)**. STACK-01 mantiene
**USER_REPORTED** y API-06 **ACCEPTED_RISK**.

## STACK-03 — runbook local de operador

### Ruta rápida

1. Confirme los prerrequisitos y entregue las variables al proceso mediante un gestor
   seguro; no use `.env*`, argumentos con secretos ni `env` para inspeccionarlas.
2. Cargue las cinco capas exactamente en el orden indicado y use un proyecto y puertos
   loopback nuevos.
3. Para la verificación completa autorizada, ejecute el arnés una vez con un recibo
   nuevo en `evidence/stack03/`; lea el estado y `cleanup` del JSON, no stdout/stderr.

### Prerrequisitos y orden de composición

Se requieren los repositorios hermanos `invoiceops-mlflow`, `invoiceops-model-api`
e `invoiceops-app`, Docker/Compose local funcional y Python 3. El orden es estricto:

1. `../invoiceops-mlflow/compose.yml` — base MLflow;
2. `./compose.yml` — overlay Stack;
3. `./compose.model-api.yml` — overlay Model API;
4. `../invoiceops-app/docker-compose.yml` — servicios App;
5. `./compose.invoiceops-app.yml` — cuarta capa propiedad de STACK-03.

Use siempre `--project-directory ../invoiceops-mlflow` y `--env-file /dev/null`.
Las publicaciones de App y Model API son exclusivamente loopback; PostgreSQL y MinIO
no se publican. La App espera la salud de Model API y recibe solo la URL e ID de
modelo, nunca credenciales o topología de MLflow.

### Propiedad de variables

| Clase | Propietario | Variables | Regla |
| --- | --- | --- | --- |
| Infraestructura/MLflow | operador y capas 1–2 | `STACK_POSTGRES_PASSWORD`, `STACK_MINIO_ROOT_USER`, `STACK_MINIO_ROOT_PASSWORD`, `MLFLOW_AUTH_ADMIN_USERNAME`, `MLFLOW_AUTH_ADMIN_PASSWORD`, `MLFLOW_FLASK_SERVER_SECRET_KEY` | Secretos del proceso; no se guardan ni imprimen. |
| Model API | operador y capa 3 | `STACK_MODEL_API_MLFLOW_USERNAME`, `STACK_MODEL_API_MLFLOW_PASSWORD`, `MODEL_API_ADMIN_TOKEN`, `STACK_MLFLOW_PORT`, `STACK_MODEL_API_PORT` | Credenciales y puertos de la frontera Model API; no se transfieren a App. |
| App | capa 4 | `STACK_APP_POSTGRES_PASSWORD`, `STACK_APP_BACKEND_PORT`, `STACK_APP_FRONTEND_PORT`, `STACK_APP_TEST_DB_PORT`, `MODEL_API_URL`, `MODEL_API_MODEL_ID` | Solo `MODEL_API_URL` e `MODEL_API_MODEL_ID` son configuración de inferencia App; ambas fallan cerradas si faltan/vacías. |
| Arnés | `scripts/stack_invoiceops_app_smoke.py` | proyecto, puertos y secretos efímeros propios | Genera valores en memoria y no acepta identidad reutilizada. |

### Validación aislada

La validación estática no inicia servicios:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest \
  tests/test_stack_invoiceops_app_config.py tests/test_stack_invoiceops_app_smoke.py -v
```

Una ejecución completa requiere autorización operacional independiente, un proyecto
`stack03-proof-...`, puertos libres distintos y un nombre de recibo que no exista:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/stack_invoiceops_app_smoke.py --execute \
  --project stack03-proof-example01 --backend-port 18640 --frontend-port 18641 \
  --report "$PWD/evidence/stack03/STACK-03-example01.json"
```

El arnés deriva los puertos internos MLflow/Model API desde el puerto backend, usa
solo el socket Unix local y configuración Docker temporal anónima. Un recibo válido
es JSON `STACK03-receipt-v3`, con timestamp UTC, revisión/hash de entradas no
secretos y hash/revisión por cada contexto construido (App, Model API y MLflow),
además del arnés/Compose, estados de fases/aserciones, límites de salida y resultado
de limpieza.
No contiene tokens, cookies, variables, cuerpos, logs, rutas absolutas ni IDs de
recursos. Un `FAIL` conserva únicamente ese recibo seguro y **no cierra** STACK-03.

### Aserciones e interpretación

Un `PASS` exige: preflight y build, arranque de base/MLflow/Model API, fixture y
health de modelo, migración/fixture de DB de App, login, listado de facturas,
metadata de decisión y fallback al detener Model API. Además debe indicar
`cleanup.status: PASS_ALL_OWNED_RESOURCES_REMOVED`. Cualquier fase o aserción no
`PASS`, o cualquier cleanup distinto, es evidencia incompleta y requiere revisión;
no se debe inferir éxito desde healthchecks aislados.

### Propiedad, cleanup y rollback

Antes de iniciar, el arnés rechaza recursos etiquetados con el mismo proyecto. Tras
registrar el recibo, elimina solo contenedores, red, volúmenes e imágenes cuyo nombre
o etiqueta Compose coincide con el proyecto exacto; vuelve a verificar la ausencia.
No usa `prune`, `reset`, `down`, `down -v` ni eliminación amplia, y no toca recursos
ajenos. Los reportes temporales no se retienen: el único artefacto durable es el
recibo sanitizado rastreable en `evidence/stack03/`.

Para rollback de código, retire únicamente `compose.invoiceops-app.yml`, el arnés,
sus pruebas, recibos y esta sección mediante una revisión posterior. No restaure el
README completo ni borre recursos Docker manualmente salvo las identidades exactas
verificadas de una ejecución autorizada. No existe rollback de los datos sintéticos
ya eliminados.

### Límites y procedencia histórica

No hay despliegue, publicación de imágenes, operación remota, cambio de contrato,
migración productiva ni certificación de CI/remoto. La evidencia acredita solo el
host y los bytes locales descritos por el hash del recibo.

Los recibos `STACK-03-20261023T000000Z.json` y `STACK-03-CR-031.json` conservan
la procedencia histórica previa. El primero es un intento **FAIL**:
la enumeración de imágenes usó IDs abreviados y el cleanup automático quedó
`UNKNOWN_REQUIRES_REVIEW`; sus recursos exactos se retiraron posteriormente tras
verificar su etiqueta. No se borra esa procedencia ni se reclasifica como PASS. El
recibo CR-031 posterior con estado `PASS` lo sustituyó temporalmente. El recibo
versionado CR-035 `PASS` con procedencia completa es la **evidencia vigente**.

**Delta de revisión (2026-10-19): READY_WITH_FOLLOW_UP.** CR012 **RESOLVED_LOCAL**;
CR013 **corrección local autorizada, pendiente de revisión independiente (MEDIUM)**,
sin riesgo aceptado implícito. CR001–CR011 conservan sus resoluciones; no queda `MUST_FIX`,
pero no están cerrados todos los hallazgos. [Ledger vigente](#cierre-de-hallazgos-stack-02).
ops08 es evidencia histórica anterior al launcher CR012, no una nueva prueba de
estos bytes. [Cierre técnico y gates HOST/Linux](../invoiceops-model-api/README.md#privacidad-de-stdoutstderr-del-worker-cr-012).

## Registro histórico de STACK-00

Repositorio independiente para conectar los repositorios de InvoiceOps en desarrollo y demostraciones locales. **STACK-00 sólo crea una base documental e inerte:** no contiene lógica de negocio ni un stack ejecutable.

## Estructura y estado

| Archivo        | Propósito                                        |
| -------------- | ------------------------------------------------ |
| `compose.yml`  | Nombre del proyecto y mapa de servicios vacío.   |
| `.env.example` | Sólo comentarios; ninguna variable activa.       |
| `.gitignore`   | Excluye entornos locales y metadatos auxiliares. |
| `README.md`    | Alcance, validación y continuidad.               |

No hay servicios, imágenes, builds, puertos, redes, volúmenes ni automatización. No se modifican los repositorios vecinos. **STACK-00 está COMPLETED en su alcance local inerte.**

**Entrega del repositorio:** la evidencia Git local aportada por el padre registra el commit `9c744bd` (`chore(stack): initialize local integration scaffold`), con `main` y la referencia local `origin/main` en ese mismo commit y árbol limpio antes de esta actualización documental. `origin` apunta a `git@github.com:yieniggu/invoiceops-stack.git`. El usuario confirmó la creación y publicación del repositorio privado en [GitHub](https://github.com/yieniggu/invoiceops-stack): **USER_REPORTED**, no verificado mediante API ni consulta remota. La coincidencia de referencias locales no acredita el contenido remoto actual.

## Validación estática

Desde este directorio, con Ruby y Psych ya disponibles:

```bash
ruby -rpsych -e 'expected = {"name"=>"invoiceops-stack", "services"=>{}}; actual = Psych.safe_load(File.read("compose.yml"), aliases: false); abort "Unexpected scaffold configuration" unless actual == expected'
```

Esto comprueba sintaxis YAML y el objeto esperado; **no valida la semántica de Docker Compose ni su ejecución**. No ejecutar `docker compose up` para STACK-00 ni interpretar el mapa vacío como preparación operativa. No se requieren pruebas funcionales porque no hay comportamiento ejecutable.

## Seguridad

No crear ni leer `.env` para validar esta base. No copiar credenciales: `.env` y `.env.*` están ignorados, salvo `.env.example`, que contiene sólo comentarios en inglés. También se ignoran `.codegraph/` y `.DS_Store`. La publicación del repositorio fue autorizada específicamente y realizada por el usuario; no autoriza publicar imágenes ni desplegar o iniciar servicios. Esta conciliación documental no instala dependencias, accede a red ni realiza nuevas operaciones remotas.

## Continuidad

Con STACK-00 cerrado, el siguiente ticket recomendado es STACK-01 — Stack ML local, cuya dependencia formal es MLFLOW-01, ya registrada como completada. STACK-01 no está autorizado ni iniciado: requiere planificación y autorización propias antes de añadir o iniciar servicios. STACK-02 espera API-06 y STACK-01; no se adelanta ninguna integración ni despliegue remoto.

## Reversión

Tras la publicación reportada, cualquier retirada requiere autorización propia: revisar primero cambios posteriores y trabajo ajeno, preparar una reversión mediante un commit posterior revisado (por ejemplo, un revert cuando corresponda) y conciliar contador y prioridad con una nota sin borrar el historial del cierre. No reescribir historia compartida ni eliminar automáticamente el remoto o los metadatos Git locales. No hay estado operativo del scaffold que restaurar; el repositorio publicado debe tratarse como estado compartido. Esta sección documenta el procedimiento, no ejecuta una reversión ni autoriza commit o push.

## STACK-01: overlay local

`compose.yml` ahora es un **overlay**, no un Compose autónomo. Reutiliza los servicios `mlflow`, `postgres`, `minio` (object-storage) y `minio-init` de `../invoiceops-mlflow/compose.yml`; no copia imágenes, healthchecks, dependencias ni lógica de arranque. El repositorio hermano es obligatorio hasta STACK-05: el contexto de build sigue siendo `../invoiceops-mlflow`, con `Dockerfile.mlflow` y su entrypoint original.

### Ruta rápida: validación sin servicios

Desde `invoiceops-stack`, con Python 3 y Docker Compose disponibles:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p 'test_stack_config.py' -v
```

Las pruebas usan sólo la biblioteca estándar y `docker compose config --format json`: fusionan los archivos reales, sin daemon, builds, pulls ni servicios. El subprocess recibe únicamente `PATH` y valores sintéticos definidos por las pruebas; usa `--env-file /dev/null`, no lee archivos de entorno ni imprime el JSON con credenciales. Compose ausente o incompatible produce fallo, nunca una omisión que aparente éxito.

Se requiere Compose con soporte de [`!override`](https://docs.docker.com/reference/compose-file/merge/#replace-value); la versión validada para esta receta es **2.31.0-desktop.2**. No sustituir esa etiqueta por una lista normal: acumularía publicaciones al cambiar el puerto. No se afirma compatibilidad con versiones anteriores no verificadas.

### Contrato del entorno del operador

Para operación futura, el operador debe suministrar variables **exportadas en el proceso**, mediante su mecanismo seguro de gestión de secretos. No usar archivos de entorno, valores de ejemplo ni contraseñas literales en comandos o historial. No ejecutar `env`, volcar el entorno, ni guardar o compartir la configuración interpolada: contiene secretos.

| Variable                         | Requisito y uso                                                                                                                                              |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `STACK_POSTGRES_PASSWORD`        | Obligatoria, nueva y aleatoria. Se aplica a PostgreSQL y a ambas URI de MLflow. Usar al menos 32 caracteres de `[A-Za-z0-9_-]`, sin codificación porcentual. |
| `STACK_MINIO_ROOT_USER`          | Obligatoria, nueva; identificador de 3–20 caracteres de `[A-Za-z0-9_-]`. Se comparte entre MinIO, inicializador y acceso S3 de MLflow.                       |
| `STACK_MINIO_ROOT_PASSWORD`      | Obligatoria, nueva y aleatoria; al menos 32 caracteres de `[A-Za-z0-9_-]`. Se comparte entre esos mismos tres consumidores.                                  |
| `MLFLOW_AUTH_ADMIN_USERNAME`     | Obligatoria, heredada de la base. Identificador sin saltos de línea ni delimitadores INI; no usar el placeholder de la base.                                 |
| `MLFLOW_AUTH_ADMIN_PASSWORD`     | Obligatoria, heredada. Nueva y aleatoria; usar al menos 32 caracteres de `[A-Za-z0-9_-]`, sin placeholders.                                                  |
| `MLFLOW_FLASK_SERVER_SECRET_KEY` | Obligatoria, heredada. Nueva, aleatoria y estable entre recreaciones; al menos 32 caracteres de `[A-Za-z0-9_-]`, sin placeholders.                           |
| `STACK_MLFLOW_PORT`              | Opcional; por defecto `5000`. Si se cambia, usar un puerto TCP libre entre 1 y 65535. Siempre se publica en `127.0.0.1`, nunca en todas las interfaces.      |

Compose rechaza variables obligatorias ausentes o vacías (`:?`), pero **no valida estas reglas de caracteres, longitud, aleatoriedad ni unicidad**. El operador debe comprobarlas antes de una ejecución autorizada. La contraseña PostgreSQL se interpola sin escape en las URI: caracteres como `@`, `/`, `:`, `%`, `?` o `#` pueden romper su interpretación o la del INI. El alfabeto indicado evita ese riesgo; no introducir una contraseña arbitraria suponiendo que Compose la codificará. La prueba con una contraseña URL-safe verifica la propagación y el parseo, no certifica contraseñas reales. Tampoco se garantiza que Compose rechace cualquier puerto numérico fuera de rango: debe revisarse antes del arranque.

Las credenciales deben ser distintas de las del stack base y de otras instalaciones. Usuario y base PostgreSQL permanecen en `mlflow` para preservar el healthcheck. Se reemplazan todos los valores internos por defecto de PostgreSQL/MinIO, incluidas ambas URI y las claves S3. El bucket sigue siendo `mlflow-artifacts`; los endpoints internos siguen siendo `postgres:5432` y `http://minio:9000`.

### Receta explícita de configuración

Con el entorno del operador ya preparado, desde `invoiceops-stack`:

```bash
docker --host unix:///var/run/docker.sock compose -p invoiceops-stack \
  --project-directory ../invoiceops-mlflow --env-file /dev/null \
  -f ../invoiceops-mlflow/compose.yml -f ./compose.yml config --quiet
```

El orden base → overlay y `--project-directory` son obligatorios. No usar `docker compose` sin estos argumentos, ni añadir el overlay `compose.invoiceops-local.yml` del hermano: esta receta no adopta su red fija. `-p invoiceops-stack` separa la red `invoiceops-stack_default` y los volúmenes `invoiceops-stack_postgres_data` y `invoiceops-stack_minio_data`, sin recursos externos ni nombres globales. Para otra instancia, cambiar explícitamente `-p` y el puerto, y proporcionar credenciales independientes; no reutilizar volúmenes del proyecto base.

Sólo MLflow publica un puerto. PostgreSQL, MinIO y su consola no se publican al host. MLflow conserva Basic Auth, workspaces, migraciones previas a los workers y el entrypoint con permisos restrictivos del archivo INI. El transporte sigue siendo HTTP local: `MLFLOW_HOST=0.0.0.0` dentro del contenedor y puerto interno `5000`; no se añade TLS ni se promete aislamiento frente a otros procesos del host.

### Arnés operacional: proyecto nuevo y exclusivo

`scripts/stack_ml_smoke.py` prepara una prueba sintética, sin entrenamiento ni promoción de modelos. Requiere los repositorios hermanos y el Python ya instalado en `../invoiceops-ml/.venv/bin/python`. No instala dependencias. Su única conexión Docker es `unix:///var/run/docker.sock`; si no responde, termina sin buscar otros contextos.

El arnés genera credenciales URL-safe sólo en memoria. Todos los hijos reciben un entorno permitido explícitamente, sin sesiones SSH, proxies ni credenciales heredadas. Docker usa un directorio temporal privado de configuración, sin contextos ni builders guardados. Su mapa `auths` contiene sólo una entrada anónima vacía: evita que Docker seleccione automáticamente un helper del llavero. En macOS se habilita únicamente la ruta conocida de plugins de Docker Desktop, sin leer `~/.docker/config.json` ni instalar plugins. La configuración temporal no contiene secretos y se retira al salir.

El preflight rechaza recursos con el proyecto o nombres preexistentes, incluidos redes, volúmenes e imagen de build. Sólo admite proyectos `stack01-proof-…` y puertos libres entre 1024 y 65535, publicados en loopback. La comprobación del puerto no reserva su uso después del preflight: una colisión posterior debe producir fallo, no elegir otro puerto silenciosamente.

Para una ejecución posterior **expresamente autorizada**, desde este directorio, con proyecto y reporte nuevos:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/stack_ml_smoke.py \
  --project stack01-proof-unique123 --port 18572 \
  --report /var/folders/y3/m1xh54ds39x7c15dzzngkh1r0000gn/T/opencode/stack01-next-proof.json
```

No repetir automáticamente un fallo ni reutilizar su reporte: la creación del JSON es exclusiva. El arnés compila MLflow, inicia la base y el overlay, exige HTTP 401 para acceso anónimo e inválido y ejecuta el consumidor real con `PYTHONPATH=../invoiceops-ml/src`. Usa `mlflow_config_from_env` y `configure_mlflow`, provisiona un workspace sintético mediante el SDK y crea un único run con un artefacto de 27 bytes. El consumidor sólo recibe URI, usuario, contraseña y workspace de MLflow; nunca claves S3 del host.

La subida/descarga exige URI `mlflow-artifacts:/`, igualdad de bytes y SHA-256. `MLFLOW_ENABLE_PROXY_MULTIPART_UPLOAD=false` se limita al consumidor para evitar URLs S3 internas; no altera el transporte base. Una comprobación dentro del contenedor MLflow lee el objeto de su bucket MinIO usando la configuración ya propia del servidor, sin extraer credenciales. Luego se recrea **sólo MLflow**, se comparan identidades de los otros contenedores, red y volúmenes, y se vuelve a leer el mismo run y artefacto.

Cada comando tiene timeout: build 600 s, inicio 240 s con espera Compose de 180 s, consumidor 90 s, recreación 150 s con espera de 120 s y apagado 90 s. El SDK limita las peticiones a 10 s y desactiva sus reintentos HTTP. No hay bucles de reintento del arnés. Un timeout del CLI **no prueba cancelación del backend Docker**: el reporte lo indica y el bloque de salida comprueba el proyecto e intenta `down --timeout 20`, siempre **sin `-v`**. Si la propiedad no puede verificarse, no actúa a ciegas y registra el apagado como fallido para revisión. Ni un timeout ni un inventario vacío garantizan ausencia de trabajo tardío del daemon.

El reporte conserva etapas, códigos y duraciones, versiones, snapshots seleccionados de recursos propios, pruebas de autenticación y recibos del consumidor cuando se alcanzan esas etapas. Nunca guarda stdout/stderr crudos, argumentos con secretos ni entornos. Las identidades de volúmenes son sus nombres Docker: no existe un ID independiente e inmutable como el de los contenedores. No se afirma que los inventarios demuestren que todo el host quedó sin cambios.

### Diagnóstico S2: evidencia offline, sin nuevo smoke

> Registro de S2 previo a ops03 y al cierre manual. La autorización y el resultado posterior se concilian al final; los límites de la evidencia offline no cambian.

El consumidor conserva ahora un diario privado independiente del recibo final, tanto antes como después de la recreación. **S2 sólo aporta validación offline; no identifica retrospectivamente la operación bloqueada en ops02 ni completa STACK-01.** No se ejecutó Docker, no se inspeccionaron sus residuos y no se autorizan aquí nuevos intentos.

El padre crea un archivo temporal propio con permisos `0600` bajo `TEMP_ROOT`, pasa únicamente su descriptor mediante `STACK_SMOKE_PROGRESS_FD` y `pass_fds`, y lo recoge y cierra tras éxito, salida fallida o timeout. El consumidor escribe mediante `os.write`, sin añadir otro `print`: el recibo sigue siendo la única impresión explícita del consumidor. El diario sólo contiene objetos JSONL con las claves `phase` y `state`; no incluye mensajes del SDK, excepciones, URI, argumentos, entornos ni credenciales.

Las fases distinguen `sdk_import`, `helper_import`, `configuration`, `configure`, `workspace_create`, `workspace_select`, `client`, `experiment`, `run_start`, `artifact_source`, `artifact_log`, `run_end`, `run_query`, `artifact_download`, `local_validation` y `receipt`. Cada fase emite `before` y `after`; `workspace_metadata` se verifica dentro de la evaluación del recibo, entre `receipt/before` y `receipt/after`. La lectura de persistencia omite creación de workspace, experimento, inicio/cierre y escritura del artefacto: son **34 eventos al crear y 22 al releer**. Así, el último `before` sin su `after` localiza la operación pendiente, pero no demuestra la causa de su bloqueo ni la ausencia de efectos parciales.

En `commands[].progress` sólo se conservan eventos validados y una razón cerrada: `COMPLETE`, `EMPTY`, `INVALID_RECORD`, `INVALID_SEQUENCE`, `TRUNCATED`, `RECORD_LIMIT`, `TOTAL_LIMIT` o `EVENT_LIMIT`. Se rechazan claves duplicadas/adicionales, tipos o enums distintos, UTF-8 inválido y secuencias repetidas, omitidas o invertidas. Se conserva el prefijo válido. `COMPLETE` significa diario sintáctica y secuencialmente válido, incluso si acaba en un prefijo: **no equivale a PASS funcional**. Para aceptar un recibo se exige además la secuencia completa de su rama y el contrato previo de workspace, bytes, hash y persistencia.

El lector solicita como máximo **32769 bytes** (32 KiB más un byte detector), admite hasta **64 registros** y **512 bytes por registro incluido LF**. El exceso total tiene prioridad sobre el exceso de eventos; ambos conservan sólo el prefijo validado dentro del presupuesto. Los límites exactos no eximen de validar estructura y orden: ninguna secuencia válida necesita 64 eventos ni 32 KiB. Estos límites acotan la lectura y el resultado retenido, **no imponen una cuota de disco al descriptor heredado**. La captura original de stdout/stderr en memoria sigue sin un límite de bytes nuevo; no se persiste su contenido crudo. Un fallo de lectura/cierre se reduce a `INVALID_RECORD`, sin reemplazar el fallo primario ni impedir el intento de apagado sujeto a propiedad. Si no puede crearse el diario, no se lanza el consumidor.

Validación S2: **17 pruebas de progreso** y **32 pruebas en la selección offline del módulo**, incluida la regresión CR001 con SDK `3.16.0` instalado y conexiones bloqueadas. Se ejecutan hijos sintéticos reales para salida, fallo y timeout, comprueban descriptor `0600`, cierre y proceso ya terminado; también se ejecuta el consumidor completo con dobles sólo en las fronteras SDK/helper. Se prueban límites exactos, EOF truncado, errores del diario, rechazo de recibos con progreso incompleto y apagado simulado sin `-v`, incluido rechazo por propiedad. No son pruebas runtime de servicios.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests python3 -B -m unittest -v test_stack_ml_smoke.ConsumerProgressEvidenceTests
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests python3 -B -c 'import unittest; import test_stack_ml_smoke as m; suite=unittest.TestSuite(); loader=unittest.TestLoader(); [suite.addTest(case) for group in loader.loadTestsFromModule(m) for case in group if case._testMethodName != "test_isolated_cli_config_is_anonymous_and_resolves_real_compose"]; result=unittest.TextTestRunner(verbosity=2).run(suite); raise SystemExit(not result.wasSuccessful())'
```

Esta selección omite explícitamente **una** prueba que ejecuta Docker CLI; no introduce un skip persistente ni representa la suite completa de Stack. Los límites de 90 s del consumidor, HTTP de 10 s, cero reintentos y `backend_cancelled=NOT_PROVEN` permanecen intactos. Artefactos reales, MinIO y persistencia tras recreación siguen **NOT_VERIFIED**. S3 queda diferido y requiere autorización propia.

La reversión exclusiva de S2 retira la instrumentación/colección de `scripts/stack_ml_smoke.py`, las ampliaciones S2 de `tests/test_stack_ml_smoke.py` y esta sección, preservando las pruebas S1, CR001, el arnés previo y todos los cambios documentales anteriores. No restaurar archivos completos ni eliminar recursos.

### Evidencia operacional

> Historia de ops01/ops02: las rutas de reportes siguientes se conservan como comandos históricos, no como archivos disponibles. ops01/ops02 ya estaban ausentes al limpiar; ops03 se retira por petición del usuario. Los residuos antes preservados fueron eliminados según el [cierre final](#cierre-manual-y-limpieza-final).

Primer intento, **ops01**, ejecutado una vez el **2026-10-16**:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/stack_ml_smoke.py \
  --project stack01-proof-20261016-ops01 --port 18571 \
  --report /var/folders/y3/m1xh54ds39x7c15dzzngkh1r0000gn/T/opencode/stack01-proof-20261016-ops01.json
```

| Evidencia                                  | Resultado real                                                                                                                                                                                                                                |
| ------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Comando principal                          | **FAIL**, salida 1; duración del arnés 0.358 s.                                                                                                                                                                                               |
| Endpoint local                             | Docker Server `27.4.0`, comprobación de versión correcta.                                                                                                                                                                                     |
| Fallo primario                             | `compose_version:exit_125_output_suppressed`: la configuración privada vacía no encontraba el plugin Compose.                                                                                                                                 |
| Mutaciones operacionales                   | No se alcanzaron build, pull, inicio, workspace, run ni artefactos. No se generaron las credenciales del stack.                                                                                                                               |
| Salida                                     | `NOT_NEEDED_NO_MUTATION_STARTED`; no se ejecutó `down` porque no se había iniciado ninguna mutación.                                                                                                                                          |
| Corrección focalizada                      | `cliPluginsExtraDirs` apunta a los plugins existentes de Docker Desktop; la validación posterior, sin mutaciones, resolvió Compose `2.31.0-desktop.2` y Docker `27.4.0`. También se impide la selección implícita de helpers de credenciales. |
| Inventario posterior del proyecto exacto   | 0 contenedores, 0 redes y 0 volúmenes. No hay volúmenes creados por esta ejecución que preservar o eliminar.                                                                                                                                  |
| Reejecución operacional al cierre de ops01 | **NOT_RUN** tras la corrección; el informe original de fallo no se sobrescribió. El reintento posterior se documenta debajo.                                                                                                                  |

Al cierre de ops01, la corrección de resolución del plugin estaba validada por una prueba real de `compose version` con configuración aislada, no por otro smoke completo. Los tests de límites, propiedad, recibos y apagado comprueban el arnés, pero **no sustituyen evidencia runtime de MinIO, autenticación, workspaces o persistencia**. El SDK local inspeccionado es `3.16.0`; la imagen base declara MLflow `3.16.1`, cuya versión ejecutándose no se verificó en ops01 ni ops02.

#### Corrección CR001 y único reintento ops02

La verificación independiente detectó un error del recibo: en el SDK instalado `3.16.0`, `mlflow.get_workspace(name)` exige el nombre y devuelve metadatos `Workspace`, no la selección activa ni una cadena. Se confirmó mediante inspección dirigida de firmas y código instalado; el ejemplo de documentación que llama `get_workspace()` sin argumentos no corresponde a ese contrato ejecutable.

La corrección mantiene el workspace obligatorio y `mlflow.set_workspace(workspace)` antes de las operaciones. Al construir el recibo comprueba que el helper real sigue resolviendo el nombre requerido desde el entorno activo, consulta `mlflow.get_workspace(workspace).name`, exige coincidencia de metadatos y serializa ese nombre. No importa APIs privadas ni cambia autenticación, almacenamiento, transporte o versiones. La igualdad de metadatos por sí sola no se interpreta como prueba de selección activa.

La regresión ejecuta la expresión real del recibo y su función auxiliar extraídas de `CONSUMER` contra el SDK instalado, con `set_workspace`, helpers y entidad `Workspace` reales. Sólo sustituye la llamada pública del cliente que consultaría metadatos; bloquea conexiones y rechaza tanto selección distinta como metadatos de otro workspace. Falló antes de la corrección y pasó después. La suite completa de Stack pasó **23 pruebas en 4.253 s**; la del hermano MLflow pasó **5 pruebas en 0.021 s**. Los checks de diff también pasaron antes del reintento.

Se consumió una sola vez el reintento autorizado:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/stack_ml_smoke.py \
  --project stack01-proof-20261016-ops02 --port 18572 \
  --report /var/folders/y3/m1xh54ds39x7c15dzzngkh1r0000gn/T/opencode/stack01-proof-20261016-ops02.json
```

| Evidencia ops02            | Resultado real                                                                                                                                                 |
| -------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Resultado principal        | **FAIL**, salida 1; duración total 222.538 s. No se repitió.                                                                                                   |
| Daemon y Compose           | Socket local explícito; Docker `27.4.0`, Compose `2.31.0-desktop.2`.                                                                                           |
| Build e inicio             | **PASS**: build 3.329 s; inicio 94.390 s, con espera de salud completada.                                                                                      |
| Recursos antes de consumir | 4 contenedores propios, 1 red propia y 2 volúmenes propios; identidades completas en el JSON.                                                                  |
| Puertos publicados         | Sólo MLflow: `127.0.0.1:18572` → `5000/tcp`. PostgreSQL y MinIO sin publicaciones al host.                                                                     |
| Autenticación negativa     | Anónimo **401**; usuario/contraseña inválidos **401**.                                                                                                         |
| Fallo primario             | `consumer_before:timeout_backend_cancellation_not_proven`, tras 90.021 s.                                                                                      |
| Límite del diagnóstico     | Sin recibo del consumidor; no se conoce qué operación interna alcanzó ni la causa del bloqueo. No se atribuye a CR001, MinIO, OOM o DNS sin evidencia.         |
| Artefactos y persistencia  | **NOT_VERIFIED**: sin run ID, hash, confirmación de objeto MinIO ni recreación de MLflow. La ausencia de recibo no demuestra ausencia de escrituras parciales. |
| Apagado                    | **PASS_VOLUMES_PRESERVED**: `down --timeout 20` sin `-v`, 5.681 s; inventario final 0 contenedores, 0 redes y los mismos 2 nombres de volúmenes.               |

Residuos conservados de ops02, sin autorizar su eliminación:

- `stack01-proof-20261016-ops02_minio_data`
- `stack01-proof-20261016-ops02_postgres_data`
- Imagen propia de build: `stack01-proof-20261016-ops02-mlflow:latest`

Los volúmenes se identifican por nombre, no mediante un ID inmutable independiente. Sus datos pueden incluir escrituras parciales; las credenciales generadas no se conservaron. El informe ops01 sigue intacto y el informe ops02 conserva el fallo y los snapshots. No se ejecutaron nuevos intentos, inspecciones de secretos ni cambios funcionales tras el timeout. El siguiente diagnóstico requiere alcance y autorización propios; no se cierra STACK-01 por las pruebas estáticas o el arranque satisfactorio.

Para las puertas locales:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p 'test_*.py' -v
git diff --check && git diff --cached --check
```

La suite de Stack combina configuración Compose real sin daemon, pruebas de seguridad del arnés y resolución real del plugin sin iniciar servicios. La suite del hermano MLflow se ejecuta desde ese repositorio con el mismo comando unittest; no se modifica su código.

### Apagado, residuos y recuperación

En una ejecución que alcance servicios, el reporte debe mostrar `PASS_VOLUMES_PRESERVED`, ningún contenedor/red propio y los mismos nombres de volúmenes antes y después de `down`. La imagen propia de build también puede permanecer; no se elimina automáticamente. Un resultado fallido exige revisar `snapshots`, `shutdown_failure` y las etapas antes de aprobar cualquier limpieza. No se autoriza purga, reset, eliminación de imágenes o volúmenes ni actuación sobre otros proyectos.

Las credenciales de este smoke no se guardan: los volúmenes sintéticos conservados no constituyen una instalación reutilizable ni un respaldo recuperable sin esas credenciales. No reutilizar el proyecto ni intentar reparar autenticación borrando datos. La limpieza futura de recursos conservados requiere aprobación explícita y comparación con el inventario sanitizado del reporte.

### Continuidad y reversión del slice

> Procedimiento y estado históricos anteriores al cierre manual: las instrucciones de conservar reportes y las afirmaciones de residuos no son inventario vigente ni autorizan retirar código. El cierre final siguiente prevalece.

El alcance acumulado comprende `compose.yml`, este README, `tests/test_stack_config.py`, el nuevo `scripts/stack_ml_smoke.py` y `tests/test_stack_ml_smoke.py`. El slice operacional no modifica Compose ni los hermanos. El registro histórico previo se conserva íntegro. STACK-01 permanece abierto: el seguimiento Stack sigue en **1/9 (11.1%)**, sin marcar Implementado ni modificar el flujo. STACK-02 y las imágenes publicadas de STACK-05 quedan fuera de alcance.

Para retirar sólo este slice, revisar trabajo posterior, restaurar el scaffold `name: invoiceops-stack` / `services: {}`, retirar la nueva prueba y únicamente las adiciones documentales de STACK-01, preservando el README dirty previo. No usar un restore global del README. Esto no autoriza eliminación de datos, cambios de seguimiento, commits, publicación ni operaciones remotas; si posteriormente existen servicios o datos, su reversión requiere autorización y evaluación propias.

Para retirar únicamente el arnés operacional, retirar sus dos archivos nuevos y sus adiciones documentales, manteniendo el overlay y la prueba estática de slice1. Conservar ambos reportes de fallo fuera del repositorio. La reversión de código no elimina recursos Docker: ops01 no creó recursos; ops02 conserva los dos volúmenes y la imagen propia indicados arriba. Su limpieza exige autorización posterior.

### Cierre manual y limpieza final

> Cierre de 2026-10-16. Sus resultados y conteos se conservan; STACK-02 al final define la continuidad vigente.

**STACK-01 COMPLETED localmente por validación manual del usuario, 2026-10-16 (USER_REPORTED).** El solicitante declaró «completado, ya lo validé» y confirmó el alcance irreversible de limpieza únicamente para `stack01-proof-20261016-ops01`, `stack01-proof-20261016-ops02` y `stack01-proof-20261016-ops03`. No se reprodujo esa validación con herramientas y ningún smoke fallido se reclasifica como PASS.

S1/arnés y S2 están implementados; CR001 resuelto. S2 tiene evidencia offline previa de **32 PASS en 5.988 s** y selección del padre **17 PASS en 4.355 s**, no repetidas aquí. ops01 falló antes de mutaciones y ops02 por TIMEOUT del consumidor. El único ops03 terminó **FAIL / salida 1 en 160.221 s**: build **PASS 2.291 s**, inicio **PASS 39.553 s**, acceso anónimo/inválido **401/401**. El consumidor salió con código 1 tras **80.666 s**, no TIMEOUT del arnés; diario de **27 eventos**, último `run_query/after` seguido de `artifact_download/before` (`client.download_artifacts`). Causa **UNKNOWN**, excepción original suprimida. Apagado **PASS 28.974 s**, entonces con dos volúmenes preservados y sin contenedores ni redes.

El diario acredita progreso parcial de autenticación positiva/workspace/run/log/query, **no** recibo final, run ID acreditado, bytes/hash verificados, objeto MinIO directo, versión efectiva del servidor o persistencia tras recreación. Esos límites **NOT_VERIFIED** se mantienen como cualificación del cierre manual, no como reapertura ni preparación productiva.

| Limpieza confirmada                                      | Resultado real                                                                                                                                                                                                                |
| -------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Contenedores/redes de los tres intentos y recursos ops01 | Ya ausentes; no se eliminaron contenedores ni redes.                                                                                                                                                                          |
| Volúmenes ops02/ops03                                    | Eliminados los cuatro nombres exactos con sufijos `_minio_data` y `_postgres_data`, tras verificar proyecto y volumen en etiquetas Compose.                                                                                   |
| Imágenes ops02/ops03                                     | Eliminadas ambas etiquetas `-mlflow:latest` tras verificar proyecto/servicio; Docker informó objetos `sha256:c24049819e5b…` y `sha256:2210357ba28c…` eliminados. Sin force, eliminación de bases, capas compartidas o cachés. |
| Informes temporales autorizados                          | ops01/ops02 ya ausentes; ops03 eliminado tras preservar este resumen, por petición expresa. Ausencia de los tres confirmada. Ninguna ruta histórica implica un reporte retenido.                                              |

Se usó exclusivamente `unix:///var/run/docker.sock`, `--host` explícito, entorno permitido y configuración Docker temporal anónima, retirada al salir. Inventario final objetivo: **0 contenedores, 0 redes, 0 volúmenes y 0 etiquetas de imagen**. Conjuntos de identificadores/nombres ajenos antes/después iguales: **44 contenedores, 15 redes, 36 volúmenes y 75 filas ID/etiqueta de imagen**; no prueba invariancia interna de sus datos. Sin errores ni reintentos de comandos, prune/reset, build/pull, arranque, nuevas pruebas funcionales, credenciales ambientales, SSH u operaciones remotas. Código, pruebas y Compose se conservaron; la eliminación de datos sintéticos no tiene rollback.

**Continuidad vigente:** Stack **2/9 (22.2%)**. Siguiente recomendación: **planificar STACK-02**, sujeto a aprobación propia y sin iniciarlo; dependencias conservadas API-06 (cerrado por excepción ACCEPTED_RISK, no PASS operativo) y STACK-01 (cierre manual). No se autoriza publicación, despliegue, staging, commit ni push. La receta operacional anterior sigue disponible sólo para una futura ejecución expresamente autorizada con proyecto y reporte nuevos; estos intentos eliminados no son instalaciones reutilizables.

## STACK-02: Model API local

**COMPLETED — VALIDATED (2026-10-19).** El overlay `compose.model-api.yml` añade Model API al stack ML local. ops08 acredita ahora el flujo estándar completo con PostgreSQL/MinIO normales, sin sustituir artifacts por sistema de archivos. La recuperación de 2026-10-17 sigue siendo evidencia histórica, no el fundamento exclusivo del estado vigente. Véase el [cierre ops08](#cierre-vigente-stack-02--prueba-estándar-ops08).

### Configuración: tercera capa, sin prueba operacional implícita

> El diagnóstico ops01 y su retirada siguientes son históricos: sus frases sobre CR007 pendiente describen ese momento. El resultado vigente es ops08 PASS; ningún FAIL anterior se reclasifica.

Desde `invoiceops-stack`, el orden obligatorio es **base MLflow → overlay Stack → overlay Model API**, manteniendo `--project-directory ../invoiceops-mlflow` y `--env-file /dev/null`. Ejemplo de validación de configuración para una operación futura autorizada, **no ejecutado en esta conciliación**:

```bash
docker --host unix:///var/run/docker.sock compose -p invoiceops-stack \
  --project-directory ../invoiceops-mlflow --env-file /dev/null \
  -f ../invoiceops-mlflow/compose.yml -f ./compose.yml \
  -f ./compose.model-api.yml config --quiet
```

Requiere ambos repositorios hermanos MLflow y Model API, además del contrato de secretos de STACK-01: credenciales PostgreSQL/MinIO/MLflow y secreto del servidor obligatorios, suministrados por el operador sólo al proceso. Model API requiere tracking/registry apuntando al MLflow interno `http://mlflow:5000`, credencial lectora y workspace correspondientes al modelo, y token administrativo dedicado `MODEL_API_ADMIN_TOKEN`; no reutilizar indiscriminadamente la cuenta administradora como lector ni imprimir configuración interpolada. No leer ni copiar archivos de entorno para validar esta receta.

La allowlist MLflow admite el host interno exacto `mlflow:5000` y hosts loopback con los puertos explícitos correspondientes, sin wildcard ni bypass. Las publicaciones al host se mantienen en loopback; PostgreSQL y MinIO no se publican. No cambiar las credenciales, permisos ni políticas de confianza para resolver un timeout.

**Diagnóstico del intento fresco del 19-10-2026, sin cierre de CR007.** `stack02-proof-20261019-ops01.json` conserva FAIL en `base_start`. La inspección autorizada posterior verificó los cuatro contenedores propios: inicializador terminado con código 0, PostgreSQL/MinIO sanos y el mismo MLflow finalmente sano, sin reinicios ni OOM. MLflow comenzó a las **18:23:25.470 UTC**, anunció Uvicorn a las **18:27:57.515** (**272,044 s**) y completó el arranque de cuatro workers a las **18:28:14.3** (**288,8 s**). Se observaron después sondeos de los tres servicios con demoras simultáneas de **26–36 s**. La VM disponía de **8,218 GB**, con **29** contenedores activos, unos **6,75 GB** de memoria agregada y sólo **180 kB** de swap libre de **1 GB**. Presión de memoria PSI media de 300 s: **32,26 % some / 21,77 % full**; I/O: **45,01 % some / 20,51 % full**. Estos datos acreditan contención y disponibilidad tardía, no un error fatal de migración ni una atribución exacta del retraso entre migraciones e imports. MLflow propio consumía **2,067 GiB**, con **14** procesos y **210** PIDs según Docker. El stderr original de Compose fue suprimido y los eventos históricos no se recuperaron: no se reconstruye su mensaje exacto. No se repitió el arnés ni se modificó el informe fallido.

**Tolerancia local de arranque autorizada expresamente por el usuario.** La nueva instrucción amplía únicamente la espera base de MLflow de **180 a 330 s**, con watchdog del comando de **360 s** (30 s de margen CLI); no es aprobación de la opción retirada de un worker. Sólo la tercera capa opcional añade `mlflow.healthcheck.start_period: 330s`, para no contar fallos iniciales contra los reintentos durante esa gracia; un sondeo exitoso puede declarar salud antes. Conserva íntegros el comando, sus **4 workers** actuales y el resto del healthcheck (sondeo, intervalo **5 s**, timeout **5 s**, **12** reintentos). MLflow base y STACK-01 de dos capas conservan `start_period: 10s` y sus presupuestos originales. Los **289 s** observados bajo contención severa justifican este margen de tolerancia del entorno, no una corrección de la causa exacta anterior a Uvicorn, un arranque más rápido ni un SLO. La secuencia tracking → auth → servidor, autenticación, jobs y dependencias no cambian. Compose debe terminar con salida 0; un fallo de espera sigue siendo FAIL y un timeout exterior mantiene incertidumbre y bloquea el apagado automático. No se modifican Docker Desktop ni cargas ajenas. El FAIL original permanece inmutable; todavía no hay PASS de una ejecución fresca con este ajuste, ni cierre de CR007.

La retirada autorizada posterior validó de nuevo IDs, nombres, labels, rutas Compose, fechas de creación, imágenes, publicaciones y montajes; la red sólo contenía miembros propios. Se detuvieron y retiraron los **4** contenedores y la **1** red mediante sus IDs exactos, sin reconstruir secretos efímeros ni ejecutar Compose con otro entorno. Comprobación final: **0 contenedores**, **0 redes**, los mismos **2 volúmenes** y las **5 imágenes** referenciadas conservados. No se eliminaron volúmenes, imágenes ni caché; el informe original mantiene su estado histórico de apagado desconocido. La limpieza posterior no convierte el intento en PASS.

### Receta del arnés nuevo: requiere autorización operacional separada

La receta estándar ya se ejecutó una vez con autorización en ops08 y aprobó las once pruebas; no se ejecuta en esta conciliación documental. Cualquier repetición requiere autorización propia, proyecto e informe nuevos. No reutilizar ops08 ni otros intentos anteriores.

| Entrada de Compose                                                                           | Contrato                                                                                         |
| -------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| `STACK_MODEL_API_MLFLOW_USERNAME`, `STACK_MODEL_API_MLFLOW_PASSWORD`                         | Lector dedicado obligatorio; no cuenta administradora.                                           |
| `MODEL_API_ADMIN_TOKEN`                                                                      | Token administrativo dedicado obligatorio; no credencial MLflow.                                 |
| `STACK_MODEL_API_PORT`                                                                       | Puerto loopback de Model API; default `5800`; el arnés exige `--api-port` explícito.             |
| `STACK_MLFLOW_PORT`                                                                          | Puerto loopback de MLflow; default `5000`; el arnés exige `--mlflow-port` explícito y diferente. |
| `STACK_POSTGRES_PASSWORD`, `STACK_MINIO_ROOT_USER`, `STACK_MINIO_ROOT_PASSWORD`              | Credenciales de infraestructura obligatorias de STACK-01.                                        |
| `MLFLOW_AUTH_ADMIN_USERNAME`, `MLFLOW_AUTH_ADMIN_PASSWORD`, `MLFLOW_FLASK_SERVER_SECRET_KEY` | Bootstrap/autenticación del servidor obligatorios; nunca credenciales de serving.                |

Para configuración manual, suministrar estos valores sólo al proceso desde un mecanismo aprobado, sin leer archivos `.env*`, imprimir configuración interpolada ni incluir secretos en argumentos. El arnés nuevo **genera sus propias credenciales efímeras**, no consume credenciales del operador. Usa un `HOME`/`DOCKER_CONFIG` temporal anónimo, sin contextos ni sesiones ambientales. Requiere Python y ejecutables Docker/Compose ya instalados y de confianza; no instala herramientas ni controla Docker Desktop.

Ejemplo ejecutable **sólo después de autorización propia** y desde `invoiceops-stack`: definir `PROOF_REPORT_DIR` como un directorio privado existente del operador, fuera de Git, con ruta absoluta canónica; el archivo del informe no debe existir. La salida compartible es sólo el reporte sanitizado, no stdout/stderr crudos, configuración interpolada ni logs privados. Cambiar proyecto/puertos si no están libres. El prefijo por sí solo no acredita propiedad.

```bash
python3 -B scripts/stack_model_api_smoke.py --execute \
  --project stack02-proof-new-authorized01 \
  --docker-host unix:///var/run/docker.sock \
  --mlflow-port 15500 --api-port 15800 \
  --report "${PROOF_REPORT_DIR}/stack02-proof-new-authorized01.json"
```

El parser admite `--docker-binary /ruta/absoluta/docker`, `--compose-binary /ruta/absoluta/docker-compose` y `--temp-root /directorio/absoluto/existente`. Sin overrides descubre únicamente ejecutables mediante `PATH` y ubicaciones conocidas de instalación; usa el temporal de la plataforma, sin rutas personales heredadas de STACK-01. Un ejecutable ausente/no ejecutable produce fallo explícito, no PASS ni skip. Las pruebas de configuración permiten `STACK_MODEL_API_COMPOSE_BINARY` como override absoluto equivalente y sólo ejecutan `config`, con valores sintéticos y salida capturada.

**Bootstrap y auditoría.** En workspace `default`, sin `MLFLOW_WORKSPACE`, el arnés crea un experimento propio, el modelo `invoice-review-production`, un usuario no administrador y un rol con exactamente dos permisos `READ`: ID exacto del experimento y nombre exacto del modelo registrado. No concede `USE`, comodines ni administración al lector. Comprueba permisos y asignación exactos mediante `list_role_permissions`/`list_user_roles` y `get_user`; el recibo sólo acredita `reader_nonadmin`/`exact_read_role`, no publica secretos. La fase lectora verifica lecturas y hashes de artifacts contra la fixture y MinIO, además de siete denegaciones de escritura **403**. Para auditoría posterior, contrastar esos mismos IDs/permisos en la instancia propia mediante un canal autorizado; no inferir privilegios de un simple health 200.

**Presupuestos y disponibilidad.** El overlay local mantiene refresh `MODEL_REFRESH_TIMEOUT_SECONDS=120`; el default global sigue en **30 s**. Startup incorpora `MODEL_STARTUP_TIMEOUT_SECONDS=180` (default global **180 s**, finito y positivo obligatorio): carga y deserializa en un worker propio y registra intento/éxito/fallo sanitizado. El estimador permanece en ese worker; el API sólo recibe primitivas JSON acotadas por pipes no bloqueantes, sin `Connection.recv()` ni unpickle del modelo. Un champion ausente/fallido deja `/health` en **200** tras terminar startup, pero `/models/invoice-review/health` y predict en **503**, sin cambiar sus schemas. Un refresh fallido conserva el worker activo completo.

**Corrección CR009 verificada:** los tests con hijos reales comprueban tramas parciales/corruptas/tardías, escritura bloqueada, deserialización e inferencia bloqueadas y recolección tras cancelación repetida. La inferencia tiene **10 s**, incluida cola del RPC. Las leases preservan modelo/run anteriores durante refresh; el drenaje del retirado tiene **10 s** y existen como máximo **3 × entradas del catálogo** de owners en total (activos, candidato y retirados), con reserva atómica antes de crearlos. La limpieza añade hasta **0,3 s** de esperas por worker; si el SO no confirma terminación, se informa `cleanup_unverified`, se conserva su ocupación de capacidad y se reintenta al cerrar. Shutdown reserva **0,5 + 0,3 × owners s** de esperas. Dimensionar memoria para la coexistencia de workers; no es un sandbox ni un límite RSS. ops08 añade startup real exitoso con champion; las regresiones offline no se presentan como fallos inyectados dentro de ese smoke.

Los imports del módulo anteriores al lifespan **no están cubiertos** por el deadline de startup. El watchdog externo sigue siendo responsabilidad del arnés/supervisor: readiness conserva **331 s = 150 s** de margen de import (observación previa de **134.635 s**) **+ 180 s** de startup **+ 1 s** de margen final (hasta **0,3 s** de limpieza y **0,7 s** de sondeo/planificación). No se incrementó el presupuesto. Cada GET limita tanto el timeout HTTP interno como el watchdog del hijo al presupuesto restante; una respuesta recibida al alcanzar o superar el deadline se rechaza. Un timeout de ese GET no se confunde con una mutación de backend indeterminada. El healthcheck de Model API en Compose usa `start_period: 331s` y comprueba liveness en `/health`, para que la dependencia no quede bloqueada antes de crear la fixture. La readiness del modelo se conserva en `/models/invoice-review/health` después de la fixture y exige `200` antes de las pruebas que dependen del modelo. No es un SLO ni prueba de cancelación del backend; si el catálogo crece, revisar este presupuesto por cada carga secuencial.

Los refresh positivo y negativo usan consumidor HTTP **125 s** (120 del proveedor + margen de respuesta); el HTTP embebido tiene watchdog externo **132 s**. El release real recibe `MODEL_RELEASE_HTTP_TIMEOUT_SECONDS=125` (default conserva **10 s**, máximo admitido **125 s**) y su proceso exterior dispone de **400 s** para tres solicitudes. No usar 5/10 s heredados para este caso local. Todo se limita además al deadline total **1800 s**, reservando **120 s** para cierre; no se reintentan las fases de prueba. HTTP **503** observado es terminal; timeout, transporte indeterminado, recibo inválido o salida fallida durante una mutación no demuestran cancelación y bloquean el `down` automático.

**Lectura del resultado.** El arnés exige **11 pruebas** en `proof_status`, con schema `STACK02-v1` y los mismos flags de invocación. Tras el snapshot de los cuatro contenedores base, `minio_idempotency` verifica propiedad/ID/imagen y estado detenido exitoso de `minio-init`, ejecuta una sola vez `docker start --attach <ID propio verificado>` (máximo **30 s**) y exige otra vez estado `exited`, ExitCode **0** y todas las identidades propias iguales. Reutiliza el contenedor y su entorno generado: no crea recursos ni otro bucket; ejercita de nuevo el bootstrap MC existente contra el bucket ya creado. El éxito del comando por sí solo no acredita la prueba.

Luego comprueba sin champion: liveness 200, modelo/predict 503 y refresh sin token/con token inválido 401. Después del primer release exitoso, `startup_champion` ejecuta **un único** `compose restart --timeout 10 model-api` (watchdog **30 s**), con las tres capas habituales y propiedad/IDs/imágenes verificados antes y después. Reutiliza readiness absoluta de **331 s**, exige health/predict **200/200**, run/versión canónicos y probabilidad válida; guarda la evidencia en `proofs.startup_champion`. Es una prueba deliberada de startup con champion, **no** un reintento ni recuperación para ocultar fallos. No reinicia MLflow, Docker Desktop ni servicios ajenos. Después, al quitar su alias espera refresh 503 y modelo activo 200, restaura el alias y repite release. Los presupuestos total **1800 s**, build **600 s** y reserva de cierre **120 s** no cambian; ninguna fase obtiene tiempo adicional si el restante se agota.

Sólo devuelve salida **0 / PASS** con las once pruebas y cierre propios aprobados. Salida 1/FAIL, pruebas UNKNOWN o `UNKNOWN_REQUIRES_REVIEW` no son cierre exitoso; un timeout de cualquiera de las dos mutaciones nuevas mantiene incertidumbre y bloquea `down` automático. `backend_cancellation: NOT_PROVEN` se conserva incluso en PASS: expresa una capacidad general no demostrada, no un fallo cuando todas las fases terminan. La recuperación histórica y los informes FAIL anteriores no se convierten en PASS. ops08 satisface ahora CR007; esta documentación no autoriza repetirlo.

**Limpieza y rollback del arnés.** Usa siempre las tres capas y el mismo project-directory, proyecto y socket. Antes de `down --timeout 20`, valida nombres, labels Compose, config_files, working_dir, bindings, imágenes e IDs inmutables contra snapshots propios; nunca `-v`, borrado de imágenes, prune ni selección masiva por prefijo. Preserva los dos volúmenes y las imágenes. Si hay incertidumbre, no ejecuta `down`: revisar manualmente procesos y estado antes de autorizar acciones dirigidas por IDs verificados. Si se perdió el entorno efímero, **no reconstruir secretos ni invocar a ciegas Compose**; contrastar el informe con inspección autorizada de identidades/labels/rutas/puertos y acreditar que no queda trabajo activo antes de autorizar retirada dirigida de contenedores/red propios. Los volúmenes/imágenes retenidos requieren autorización separada para eliminarlos.

> Diagnósticos históricos ops04–ops07: las conclusiones siguientes de «no fresh PASS» y los estados de recursos se refieren a esos intentos. No describen ops08 ni reabren CR007; las mejoras de observabilidad no son hallazgos nuevos pendientes.

**CR007 ownership queries and timeout diagnosis (code evidence only).** Discovery deduplicates the **union of separate** name/reference and project-label queries, not an AND. Postfiltering retains exact project/`-`/`_` prefixes, unlabeled collisions and label-only foreign names, including images in preflight. [Name filters](https://github.com/docker/cli/blob/v27.4.0/docs/reference/commandline/volume_ls.md) match substrings; [image references](https://github.com/moby/moby/blob/v27.4.0/daemon/images/image_list.go) use familiar-reference patterns. Separate `publish` queries preserve host-port checks ([Docker 27.4: `PublicPort`](https://github.com/moby/moby/blob/v27.4.0/daemon/list.go)).

Ordinary SDK checks replace global listings with three batch inspections of **recorded** container/network IDs and volume names. Names, Compose provenance, images, loopback bindings, mounts and network membership remain checked; SDK exec targets the verified ID. Project-wide `up`/`restart`/`down` still require complete scoped discovery. Extra/replaced resources or unavailable discovery block cleanup with `UNKNOWN_REQUIRES_REVIEW`, never blind `down`. Scoped queries are not immune to daemon contention.

**Container inventory projection.** On ops04 with Docker **27.4.0**, the same scoped `ps -a` filter timed out with `{{json .}}` at **8.020 s**, while ID-only returned four rows in **0.388 s** and explicit `ID/Names/Labels/Ports` JSON in **0.906 s**. Full JSON with `--size=false` also timed out (**8.013 s**): size calculation alone is not established as the cause. [Full-context serialization evaluates formatter getters](https://github.com/docker/cli/blob/v27.4.0/cli/command/formatter/reflect.go), and [template preprocessing can request size data](https://github.com/docker/cli/blob/v27.4.0/cli/command/container/list.go). Container inventory now requests only the four required fields; union/deduplication, name/label/image/port collision checks, complete discovery and exact inspection guards are unchanged. Its operation label remains `inventory_containers`. In the current execution order, `base_start` inventory occurs **after** successful Compose `up`; a missing snapshot does not prove no resources were created. No timeout budget was increased, and this diagnostic comparison is not a fresh proof run.

Later, a minimal scoped cleanup query and direct Unix-socket Engine `/version` request also timed out at **8 s**. This limits the diagnosis: the projection avoids the demonstrated full-context path, but cannot resolve all daemon contention. Four owned containers, one network and two volumes had been verified by scoped discovery/exact inspection beforehand; subsequent cleanup stopped at read-only preconditions, with **no stop/remove command dispatched**. Their final state is not certified absent, and the immutable ops04 report remains unchanged.

Read-only Docker inventory/inspection and Compose `config` now default to **60 s**, preserving explicit overrides and total-deadline clamping, based on the observed **30.022 s timeout + 10 s pause + 6.778 s successful recheck** (not a single 46.8-second query or a root-cause/faster-server claim), with no automatic retry or mutation-budget change.

The bounded `operation` field records only whitelisted `category`, `status` (`completed`, `failed`, `timeout`) and monotonic `elapsed_seconds`; cleanup preserves the primary failure's operation. A phase `bootstrap` timeout labeled `inspect_networks` does **not** imply SDK dispatch. No argv, child output, credentials or provider exception is recorded. All eleven proofs, schema/CLI, four workers and existing budgets stay unchanged. The historical blocking operation remains unproven; this is not a fresh PASS or closure of CR007.

**Reader SDK compatibility.** [MLflow 3.16.0 response verification](https://github.com/mlflow/mlflow/blob/v3.16.0/mlflow/utils/rest_utils.py) maps a plain-text HTTP 403 to `MlflowException`, not its `RestException` subclass. Basic-auth has a plain-text forbidden-response path. The reader now accepts either exception shape **only when its status is exactly 403**; artifact uploads still require an actual HTTPError response with status 403. Offline tests exercise all seven real SDK write methods, reject other statuses/transport failures/successful writes, and execute the complete reader branch with five-file hash checks. Download APIs, exact two READ grants, permissions, trust and budgets are unchanged. A controlled real-SDK download also survives a denied optional logged-model search; that probe alone is not a proven cause of ops03.

On any nonzero SDK action exit, `sdk_diagnostic` may retain one validated `STACK02_DIAGNOSTIC=` marker with only a constant `stage`, whitelisted `exception` category and numeric `http_status` (0 if unavailable). The all-action exception hook additionally reports `source_line`, bounded to the embedded SDK source, and suppresses raw unhandled tracebacks. Fixture stages distinguish imports, fit, save, trust validation, reload, probability shape, upload, run status, version creation and alias assignment; reader contexts retain their existing fine-grained stages. Unknown/oversized/duplicate markers, extra fields and markers from non-SDK commands are ignored; the failure remains FAIL. No raw message, traceback, URI or credential is persisted. Ops03 and ops05 precise failing calls remain unproven because their exceptions were suppressed; immutable FAIL reports are not reclassified by improved diagnostics.

**Direct S3 diagnostics and import isolation.** The objects action now imports boto3, not MLflow or `MlflowClient`. Its receipt explicitly identifies `transport: direct_s3`, the executing `boto3` version, and `mlflow_installed` from package metadata; it does not claim an MLflow SDK call. Fixture and reader still execute the real MLflow SDK. The internal validator requires the same complete fixture manifest, workspace and installed MLflow version; the CLI, `STACK02-v1` schema and eleven required proofs are unchanged. Successful object receipts are retained in `proofs.objects`.

S3 progress distinguishes client construction, listing, GET headers, body reads and final manifest equality. The exception hook recognizes a fixed botocore class allowlist and reads `ClientError.response` as a dictionary. Generated S3 service exceptions such as `NoSuchKey` are normalized through their actual botocore `ClientError` base class. It retains only an allowed HTTP status and `error_code` enum (`other` otherwise), never the response, error message, request ID, URL or credentials. Malformed metadata is rejected or reduced to an unknown value; failures still fail. Connect **5 s**, read **10 s**, one total attempt and the overall SDK **150 s** deadline are unchanged: a historical timeout has not been established.

The additional offline gate `tests/stack_model_api_s3_checks.py` runs in an **already-installed** boto3/botocore runtime, accepting JSON `{"source": "<actual embedded SDK source>"}` on stdin. It tests real exception prototypes and the complete direct-S3 branch with actual boto3/StreamingBody, replacing only external transport and forbidding socket connections. It verifies five hashes, rejects corruption and detects accidental MLflow imports. This gate is separate because the existing host API virtual environment does not contain boto3/botocore; no dependency is installed and the normal host suite remains daemon-free.

A failed release child may additionally retain `release_diagnostic`: only an exact known method/path and constant failure phrase from the existing release script are accepted. The bounded record identifies `refresh`, `health` or `predict`, an allowed category and HTTP status (0 when absent). It distinguishes terminal HTTP 503 from a consumer timeout without retaining arbitrary stderr. Failure and backend-uncertainty handling remain conservative; this diagnostic does not itself authorize cleanup or turn release into PASS.

#### Historia ops07: evidencia parcial inicial y recuperación posterior

> La tabla siguiente describe el punto intermedio anterior a la recuperación final; no es el inventario ni el estado vigente. El cierre estándar actual está en [ops08](#cierre-vigente-stack-02--prueba-estándar-ops08).

The immutable standard `stack02-proof-20261019-ops07.json` remains **FAIL** (exit 1, **332.268 s**, five of eleven proofs). Its original `get_object` exception is not recoverable from the suppressed output. Exact snapshot ownership, provenance, bindings, mounts, network membership and images were verified before later probes. Both a minimal S3 probe (**0.622 s**) and the old MLflow-importing objects path (**4.576 s**) subsequently matched all five original hashes, so neither a historical timeout nor an import-induced transport failure is established. No S3 timeout was increased.

| New evidence on the same ops07 instance | Observed result                                                                                                                                                                                                                                               |
| --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Current direct-S3 branch                | Five original fixture hashes, boto3 **1.37.34**, **1.113 s**.                                                                                                                                                                                                 |
| Actual MLflow **3.16.0** reader         | Five download hashes and all seven exact **403** denials, **54.379 s**.                                                                                                                                                                                       |
| Real release                            | Failed after **120.584 s**; own API access log confirms refresh **503**.                                                                                                                                                                                      |
| Isolated owned loader diagnostic        | Repeated artifact requests returned HTTP **200** headers; provider failed with `ModelLoadFailure` reason `timeout` after **121.589 s**. The repeating download's underlying exception is not identified.                                                      |
| Narrow single-file SDK diagnostic       | Original `MLmodel` hash matched in **26.414 s**, without extending its request deadline or adding retries.                                                                                                                                                    |
| Subsequent real release                 | Failed at the consumer deadline; bounded diagnostic is `refresh / timeout / 0`. Continuation wall time **132.893 s**; this does not prove backend cancellation or a terminal second refresh response.                                                         |
| Estado intermedio entonces observado    | Process health **200**, model health **503**, no active model-worker process in that scan; exec states accounted for. Then **5 containers / 1 network / 2 volumes / 2 built images** remained. No restart, alias removal or shutdown had yet been dispatched. |

Los informes privados `stack02-proof-20261019-ops07-resolution.json` y `stack02-proof-20261019-ops07-resolution-completion.json` conservan sus fallos y los UNKNOWN de aquel punto. El informe posterior `stack02-proof-20261019-ops07-loader-resolution.json` acredita las once comprobaciones compuestas como **RECOVERY_PASS_CAUSE_UNDETERMINED**, no una ejecución estándar fresca. Una aserción de host inválida de la sonda diagnóstica rechazaba respuestas MinIO firmadas legítimas **HTTP 206**, provocando reintentos inyectados; se retiró y se restauraron los bytes de API del contenedor. La `ConnectionError` observada en el loader real no tiene causa subyacente establecida. La recuperación no demuestra una corrección de raíz del SDK o la red productiva; ops08 aporta después la prueba estándar independiente. Estos basenames locales no versionados no son enlaces Git portables.

Validación histórica de esas correcciones: **81 pruebas host PASS (40.639 s)** y **4 de frontera S3 PASS (5.033 s)**, con RED real de aislamiento de imports y diagnóstico de release. Los timeouts intermedios siguen siendo intentos fallidos, no reintentos ocultos. La selección host posterior del padre aprobó **81 en 18.809 s** antes de ops08; no se suman ambas ejecuciones ni se presentan como pruebas nuevas de esta edición. Pins, schemas, confianza, permisos y cuatro workers MLflow permanecen intactos; el [cierre vigente](#cierre-vigente-stack-02--prueba-estándar-ops08) concilia el resto de gates.

**Progreso ante bloqueos del SDK.** El hijo emite `STACK02_PROGRESS=process_start` con vaciado inmediato antes de los imports y después una etapa constante al comenzar la decodificación de stdin, los imports, la configuración y cada operación lógica. Un fallo o timeout del SDK puede conservar únicamente la última etapa permitida en `sdk_last_stage`, también desde la salida parcial en bytes de `TimeoutExpired`. No se almacenan mensajes del proveedor, URL ni credenciales; un marcador no convierte un fallo en PASS ni acredita cancelación. Las operaciones exitosas siguen validando sus recibos originales. No se amplían presupuestos ni se añaden reintentos.

En el diagnóstico conservado `diag06`, las sondas mínimas mediante `docker exec -i` completaron tanto `json.load` con EOF como la decodificación de una línea: no se confirmó un bloqueo por cierre de stdin. Una lectura autenticada acreditó HTTP 200 y el experimento ya creado por el bootstrap que había agotado el watchdog. La recuperación verificó el rol propio con exactamente dos permisos READ y usuario no administrador; sólo se rotó la contraseña perdida de ese usuario sintético mediante la API administrativa normal. La fixture real produjo un run FINISHED, versión 1 y cinco archivos con hashes, sin cambiar el pipeline ni la confianza. Un intento lector posterior falló en `download` con `MlflowException`/404; una instrumentación adicional del transporte completó la misma rama con las cinco descargas y siete denegaciones. La implementación instalada de `RunsArtifactRepository.download_artifacts` puede suprimir excepciones internas y terminar con 404: este dato no identifica por sí solo la excepción original de ese intento. Los informes fallidos permanecen intactos; ninguna de estas recuperaciones constituye una prueba estándar fresca ni demuestra la causa histórica de ops05.

Rollback de código: retirar únicamente las correcciones propias de este lote tras revisión del diff, sin sobrescribir cambios heredados ni los ajustes H2 de MLflow que mantienen credenciales fuera de argv. Omitir la tercera capa deshabilita Model API en una futura invocación; no detiene una instancia ya creada ni autoriza ninguna operación. No revertir deadlines a la carga síncrona sin límite como mitigación operacional.

### Evidencia de recuperación y límites

> Primer cierre histórico, 2026-10-17. Se preservan hechos, límites y conteos; la evidencia estándar fresca de 2026-10-19 se añade al final y sustituye el estado actual de «sólo recuperación».

| Comprobación ya ejecutada | Resultado y alcance                                                                                                                                                                                       |
| ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Champion local            | `invoice-review-production@champion`, versión `1`, run `f674cd0a06cd40abb633f5a0ef94c91a`.                                                                                                                |
| Health/predict            | HTTP **200/200**, misma versión/run, probabilidad `0.000059535190842276645`.                                                                                                                              |
| Release real              | Salida **0 en 57.039 s**; tras restaurar el alias, **PASS en 10.294 s**.                                                                                                                                  |
| Fallo controlado          | Alias ausente: refresh **503**, modelo activo conserva health/predict **200**; alias restaurado después.                                                                                                  |
| Lector y artifacts        | REST real, siete denegaciones **403** y cinco hashes MinIO iguales a descargas HTTP independientes. Diagnóstico SDK con timeout; manifiesto local original no recuperado, sin igualdad triple acreditada. |
| Gate independiente        | **PASS / RECOVERY_VALIDATED**, riesgo HIGH, sin bloqueantes para los dos criterios originales; no certifica arnés nuevo completo ni producción.                                                           |

Informe sanitizado local no versionado: `stack02-proof-20261017-ops02-recovery.json` en la raíz local `diplomado`, schema `STACK02-recovery-v1`, estado `PASS_RECOVERY_EXISTING_INSTANCE`. Los informes retenidos `stack02-proof-20261017-ops01.json` y `stack02-proof-20261017-ops02.json` conservan **FAIL**, sin sobrescritura; estos basenames no son enlaces portables del repositorio.

La fixture ahora declara dependencias pip ya instaladas y evita el subproceso redundante de inferencia de `save_model`; su plazo original de **150 s** no se amplió. Fit/save/roundtrip corregido **84.064 s** y carga directa **85.267 s**; imports fríos **57.653/40.790/134.635 s**, bajo presión de memoria de la VM y swap agotado. El último excede 120 s: **fiabilidad del arnés nuevo en frío pendiente**, sin atribuir una causa universal OMP ni prometer SLO. No hubo reinicio de Docker Desktop en la recuperación; el reinicio manual anterior del usuario había restablecido el daemon.

Spotchecks finales del responsable: **20 pruebas smoke PASS en 0.196 s** y **11 de configuración PASS en 2.573 s**; revisión estática independiente sin debilitamiento de aserciones. Son evidencia recibida, no pruebas repetidas en esta actualización documental.

### Limpieza y continuidad del cierre histórico de 2026-10-17

Tras validar identidades se retiraron **cinco contenedores y una red propios**; se preservaron **dos volúmenes nombrados y dos imágenes construidas**. No se tocaron proyectos ajenos según las operaciones reportadas; no se afirma igualdad global de inventarios ni que los volúmenes sintéticos sean una instalación reutilizable. Su eliminación futura requiere autorización propia.

Stack **3/9 (33.3%)**, sin completar STACK-08 ni INT-06. Siguiente recomendación: **planificar y definir validación local de MLFLOW-07**, cuyas dependencias MLFLOW-04 y APP-04 están completadas, sin iniciar provisioning compartido/remoto. STACK-03 espera APP-13, que depende de APP-12 y su cierre pendiente de APP-11; STACK-04 espera además APP-11. El [flujo vigente](../dev/tickets/08_flujo_ideal_de_tickets.md#continuidad-vigente--prueba-estándar-stack-02-2026-10-19) mantiene los bloqueos y el diferimiento remoto. API-06 conserva ACCEPTED_RISK y STACK-01 USER_REPORTED. Esta conciliación no autoriza servicios, pruebas operativas, publicación, staging, commit ni push.

### Cierre vigente STACK-02 — prueba estándar ops08

**VALIDATED, 2026-10-19: ejecución estándar fresca PASS, 11/11 pruebas.** El padre ejecutó una vez el `main` habitual, sin wrapper diagnóstico, hooks ni cambios de fuente: salida **0**, duración monotónica observada **556.121 s**. Esta edición sólo concilia documentación; no vuelve a ejecutar runtime. Fuente: `stack02-proof-20261019-ops08.json`, **145 líneas**, schema `STACK02-v1`, `status: PASS`, en la raíz local `diplomado`; es un informe local no versionado, no un enlace Git portable. La duración y salida proceden de la observación del proceso, no de campos del JSON.

Orden público exacto: `preflight → build → base_start → minio_idempotency → bootstrap → api_start → missing_champion → fixture → objects → reader → release → startup_champion → missing_alias → restored`.

| Pruebas y recibos                                          | Evidencia y límite                                                                                                                                                                                                                                                                                                                                      |
| ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `preflight`, `bootstrap`, `minio_idempotency`              | PASS. Inicializador `exited`, código **0**, mismo ID que el snapshot inicial tras repetirlo una vez. Bootstrap valida lector no administrador y dos grants READ exactos.                                                                                                                                                                                |
| `fixture`, `objects`                                       | PASS. Run **FINISHED**, SDK **3.16.0**, workspace `default`, versión **1**, run `d1dac16fa153465e8222c786c5a07fd9`. Los **cinco hashes** de `proofs.fixture.manifest` y `proofs.objects.manifest` coinciden. Objects usa **direct_s3 / boto3 1.37.34**; `mlflow_installed: 3.16.1` es metadata instalada, no ejecución de `MlflowClient` en esa acción. |
| `reader`                                                   | PASS de la rama SDK real que exige cinco hashes descargados y siete denegaciones exactas 403. El JSON no serializa las respuestas individuales del lector ni cada 403: acredita las aserciones ejecutadas mediante `proof_status.reader`.                                                                                                               |
| `missing_champion`, `release`, `missing_alias`, `restored` | Todos PASS por comprobaciones ejecutadas: ausencia inicial, release real, refresh 503 con alias ausente y conservación del modelo activo 200, restauración y release final. Esos HTTP negativos no son campos crudos individuales del JSON.                                                                                                             |
| `startup_champion`                                         | PASS; recibo crudo health/predict **200/200**, versión **1**, mismo run, probabilidad **0.000059535190842276645**. ID API idéntico al snapshot inicial después del reinicio deliberado.                                                                                                                                                                 |
| Cierre                                                     | `PASS_VOLUMES_IMAGES_PRESERVED`; snapshot `stopped` con **0 contenedores / 0 redes**, exactamente los dos volúmenes propios conservados. No certifica invariancia global de recursos ajenos.                                                                                                                                                            |

Residuos ops08 preservados, sin autorización de borrado:

- `stack02-proof-20261019-ops08_minio_data` y `stack02-proof-20261019-ops08_postgres_data`.
- Imagen MLflow: `sha256:93e96654ecfdf635e800ec52d37c4d93a15d380191c2ee74aab601b104b24520`.
- Imagen API: `sha256:4b4b6d3129dbaa3c794b82bc17b86bb8a13ef0193a9fb6338b9391aa88ac6371`.

#### Cierre de hallazgos STACK-02

> Snapshot histórico: CR-013 ya está RESOLVED_LOCAL_VERIFIED en el último estado
> superior y en el ledger final central. Se conservan aquí la revisión y sus
> conteos originales; no constituyen el estado pendiente vigente.

CR001–CR005 mantienen sus resoluciones previas, sin reclasificación. No quedan hallazgos obligatorios abiertos de CR006–CR011 en este lote:

| Hallazgo | Estado y resolución verificada                                                                                                                                                                                                                                                                                                                    |
| -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| CR006    | **CERRADO / VERIFICADO**: transporte 125 s, watchdog 132 s y release exterior 400 s; distingue HTTP terminal de mutación incierta, sin `down` ciego. Default del consumidor 10 s y máximo 125 s intactos.                                                                                                                                         |
| CR007    | **CERRADO / VERIFICADO**: ops08 estándar fresco PASS 11/11; no sólo recuperación. Se conservan los FAIL originales y las causas históricas desconocidas.                                                                                                                                                                                          |
| CR008    | **CERRADO / VERIFICADO**: runbook anterior con flags, variables exactas, bootstrap/lector, tres capas, binarios confiables, temporal privado, auditoría, reportes, residuos y rollback que preserva H2.                                                                                                                                           |
| CR009    | **CERRADO / VERIFICADO**: owner/worker y proxy JSON 16 KiB, IPC no bloqueante, startup 180 s, inferencia/drenaje 10 s, capacidad preasignada atómicamente 3 × catálogo, leases y cierre acotado con owners no verificados retenidos. Regresiones offline y startup con champion real en ops08.                                                    |
| CR010    | **CERRADO / VERIFICADO**: sin dependencia nueva de TEMP_ROOT personal; overrides absolutos de Docker/Compose/temporal, HOME/config anónimos. Binario ausente es bloqueo de entorno, no PASS; sin descubrir contextos ni autenticación ambiental.                                                                                                  |
| CR011    | **CERRADO / VERIFICADO**: readiness 331 = 150 + 180 + 1 s, HTTP/hijo limitados al tiempo restante y rechazo de recibos tardíos. No promete tiempo real del SO.                                                                                                                                                                                    |
| CR012    | **RESOLVED_LOCAL (HIGH)**: stdout/stderr del hijo a `DEVNULL` antes de exec mediante `QuietSpawnContext`/`Popen`, sin redirigir FD del proceso HTTP ni ampliar contratos/dependencias. Revisión independiente y gates HOST/Linux acotados; [evidencia y límites](../invoiceops-model-api/README.md#privacidad-de-stdoutstderr-del-worker-cr-012). |
| CR013    | **CORRECCIÓN LOCAL — PENDIENTE DE REVISIÓN INDEPENDIENTE (MEDIUM)**: contexto `cleanup=True` propagado a inventario, propiedad e imágenes durante cierre; límite absoluto 1800 s y guardas de incertidumbre/identidad intactos. No es `ACCEPTED_RISK` ni cierre independiente.                                                                    |

**Veredicto vigente del lote: READY_WITH_FOLLOW_UP**, no `READY` sin reservas ni
todos los hallazgos cerrados. El bloqueo HIGH de CR012 que motivó
`CHANGES_REQUESTED` está resuelto localmente; CR013 espera revisión independiente.

**Seguimiento CR013 de STACK-02:** corrección local separada autorizada después
del cierre CR012. Antes, las consultas de cierre descontaban otros **120 s** y
podían rechazar el precheck o postcheck a **1690/1800 s** pese a quedar **110 s**.
Ahora `shutdown` propaga explícitamente el contexto de cierre por `owned` →
`inventory`/inspecciones y `built_images`, además de `down`. Cada comando se limita
al tiempo real restante hasta **1800 s**; al alcanzar ese límite no se despacha
otro comando. Setup y prueba siguen reservando **120 s**. Identidad incierta o
timeout siguen impidiendo un `down` ciego, sin force ni borrado de volúmenes/imágenes.
Las regresiones usan el Harness real con reloj virtual y Runtime CLI sintético,
antes/en/después de **1680 s**, post-`down`, clamping y **1800 s**; no son prueba
operacional ni verificación independiente. No cambian tickets, conteos ni ops08.

**Gates recibidos, no reejecutados ni sumados como una única corrida:** API **131 PASS en 41.31 s** (incluye **49** de worker), Ruff/formato API PASS; release **17 PASS** anteriores sin cambios; Stack **81 PASS en 18.809 s** antes de ops08; frontera boto3 **4 PASS en 5.033 s**; MLflow H2/guarda de contexto **27 PASS** anteriores sin cambios. El Ruff exploratorio Stack de 28 observaciones no es gate configurado ni se declara limpio: observaciones no bloqueantes según revisión, sin nuevas exenciones ni `noqa`.

**Historial y límites:** permanecen intactos los FAIL de 2026-10-17 ops01/02 y de 2026-10-19 ops01/02/03/04/05/07, además de diag06 y las recuperaciones. ops08 prueba este flujo completo actual, no una raíz histórica corregida, fiabilidad universal en frío, SLO productivo, sandbox/RSS ni atestación de argv de procesos cortos. `backend_cancellation=NOT_PROVEN` conserva su significado general; no invalida las fases completadas. No cambia autenticación, pins, confianza, transporte S3 normal ni defaults globales.

**Continuidad:** App **12/15 (80%)**; ML **14/16 (87.5%)**; MLflow **6/9 (66.7%)**; Model API **7/9 (77.8%)**; Contracts **3/7 (42.9%)**; Stack **3/9 (33.3%)**; integración **2/7 (28.6%)**. Sólo planificar MLFLOW-07; no se completa App, Anvil, STACK-08 ni INT-06 ni se autoriza otro ticket, provisioning o acceso remoto. [Seguimiento vigente](../dev/tickets/Implementado.md#cierre-vigente-stack-02--prueba-estándar-completa-2026-10-19).

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
