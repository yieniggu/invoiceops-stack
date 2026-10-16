# invoiceops-stack

**Estado vigente (2026-10-16): STACK-01 COMPLETED por validación manual declarada por el usuario (USER_REPORTED), no por PASS automatizado.** Los tres intentos fallidos se preservan como historia; sus recursos propios se retiraron mediante limpieza irreversible expresamente confirmada. Véase [cierre manual y limpieza final](#cierre-manual-y-limpieza-final). Stack pasa a **2/9 (22.2%)**; no se acredita preparación productiva. La receta sigue en [STACK-01: overlay local](#stack-01-overlay-local). Las afirmaciones del registro de STACK-00 sobre scaffold y autorización son históricas, no el estado actual.

## Registro histórico de STACK-00

Repositorio independiente para conectar los repositorios de InvoiceOps en desarrollo y demostraciones locales. **STACK-00 sólo crea una base documental e inerte:** no contiene lógica de negocio ni un stack ejecutable.

## Estructura y estado

| Archivo | Propósito |
| --- | --- |
| `compose.yml` | Nombre del proyecto y mapa de servicios vacío. |
| `.env.example` | Sólo comentarios; ninguna variable activa. |
| `.gitignore` | Excluye entornos locales y metadatos auxiliares. |
| `README.md` | Alcance, validación y continuidad. |

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

| Variable | Requisito y uso |
| --- | --- |
| `STACK_POSTGRES_PASSWORD` | Obligatoria, nueva y aleatoria. Se aplica a PostgreSQL y a ambas URI de MLflow. Usar al menos 32 caracteres de `[A-Za-z0-9_-]`, sin codificación porcentual. |
| `STACK_MINIO_ROOT_USER` | Obligatoria, nueva; identificador de 3–20 caracteres de `[A-Za-z0-9_-]`. Se comparte entre MinIO, inicializador y acceso S3 de MLflow. |
| `STACK_MINIO_ROOT_PASSWORD` | Obligatoria, nueva y aleatoria; al menos 32 caracteres de `[A-Za-z0-9_-]`. Se comparte entre esos mismos tres consumidores. |
| `MLFLOW_AUTH_ADMIN_USERNAME` | Obligatoria, heredada de la base. Identificador sin saltos de línea ni delimitadores INI; no usar el placeholder de la base. |
| `MLFLOW_AUTH_ADMIN_PASSWORD` | Obligatoria, heredada. Nueva y aleatoria; usar al menos 32 caracteres de `[A-Za-z0-9_-]`, sin placeholders. |
| `MLFLOW_FLASK_SERVER_SECRET_KEY` | Obligatoria, heredada. Nueva, aleatoria y estable entre recreaciones; al menos 32 caracteres de `[A-Za-z0-9_-]`, sin placeholders. |
| `STACK_MLFLOW_PORT` | Opcional; por defecto `5000`. Si se cambia, usar un puerto TCP libre entre 1 y 65535. Siempre se publica en `127.0.0.1`, nunca en todas las interfaces. |

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

| Evidencia | Resultado real |
| --- | --- |
| Comando principal | **FAIL**, salida 1; duración del arnés 0.358 s. |
| Endpoint local | Docker Server `27.4.0`, comprobación de versión correcta. |
| Fallo primario | `compose_version:exit_125_output_suppressed`: la configuración privada vacía no encontraba el plugin Compose. |
| Mutaciones operacionales | No se alcanzaron build, pull, inicio, workspace, run ni artefactos. No se generaron las credenciales del stack. |
| Salida | `NOT_NEEDED_NO_MUTATION_STARTED`; no se ejecutó `down` porque no se había iniciado ninguna mutación. |
| Corrección focalizada | `cliPluginsExtraDirs` apunta a los plugins existentes de Docker Desktop; la validación posterior, sin mutaciones, resolvió Compose `2.31.0-desktop.2` y Docker `27.4.0`. También se impide la selección implícita de helpers de credenciales. |
| Inventario posterior del proyecto exacto | 0 contenedores, 0 redes y 0 volúmenes. No hay volúmenes creados por esta ejecución que preservar o eliminar. |
| Reejecución operacional al cierre de ops01 | **NOT_RUN** tras la corrección; el informe original de fallo no se sobrescribió. El reintento posterior se documenta debajo. |

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

| Evidencia ops02 | Resultado real |
| --- | --- |
| Resultado principal | **FAIL**, salida 1; duración total 222.538 s. No se repitió. |
| Daemon y Compose | Socket local explícito; Docker `27.4.0`, Compose `2.31.0-desktop.2`. |
| Build e inicio | **PASS**: build 3.329 s; inicio 94.390 s, con espera de salud completada. |
| Recursos antes de consumir | 4 contenedores propios, 1 red propia y 2 volúmenes propios; identidades completas en el JSON. |
| Puertos publicados | Sólo MLflow: `127.0.0.1:18572` → `5000/tcp`. PostgreSQL y MinIO sin publicaciones al host. |
| Autenticación negativa | Anónimo **401**; usuario/contraseña inválidos **401**. |
| Fallo primario | `consumer_before:timeout_backend_cancellation_not_proven`, tras 90.021 s. |
| Límite del diagnóstico | Sin recibo del consumidor; no se conoce qué operación interna alcanzó ni la causa del bloqueo. No se atribuye a CR001, MinIO, OOM o DNS sin evidencia. |
| Artefactos y persistencia | **NOT_VERIFIED**: sin run ID, hash, confirmación de objeto MinIO ni recreación de MLflow. La ausencia de recibo no demuestra ausencia de escrituras parciales. |
| Apagado | **PASS_VOLUMES_PRESERVED**: `down --timeout 20` sin `-v`, 5.681 s; inventario final 0 contenedores, 0 redes y los mismos 2 nombres de volúmenes. |

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

**STACK-01 COMPLETED localmente por validación manual del usuario, 2026-10-16 (USER_REPORTED).** El solicitante declaró «completado, ya lo validé» y confirmó el alcance irreversible de limpieza únicamente para `stack01-proof-20261016-ops01`, `stack01-proof-20261016-ops02` y `stack01-proof-20261016-ops03`. No se reprodujo esa validación con herramientas y ningún smoke fallido se reclasifica como PASS.

S1/arnés y S2 están implementados; CR001 resuelto. S2 tiene evidencia offline previa de **32 PASS en 5.988 s** y selección del padre **17 PASS en 4.355 s**, no repetidas aquí. ops01 falló antes de mutaciones y ops02 por TIMEOUT del consumidor. El único ops03 terminó **FAIL / salida 1 en 160.221 s**: build **PASS 2.291 s**, inicio **PASS 39.553 s**, acceso anónimo/inválido **401/401**. El consumidor salió con código 1 tras **80.666 s**, no TIMEOUT del arnés; diario de **27 eventos**, último `run_query/after` seguido de `artifact_download/before` (`client.download_artifacts`). Causa **UNKNOWN**, excepción original suprimida. Apagado **PASS 28.974 s**, entonces con dos volúmenes preservados y sin contenedores ni redes.

El diario acredita progreso parcial de autenticación positiva/workspace/run/log/query, **no** recibo final, run ID acreditado, bytes/hash verificados, objeto MinIO directo, versión efectiva del servidor o persistencia tras recreación. Esos límites **NOT_VERIFIED** se mantienen como cualificación del cierre manual, no como reapertura ni preparación productiva.

| Limpieza confirmada | Resultado real |
| --- | --- |
| Contenedores/redes de los tres intentos y recursos ops01 | Ya ausentes; no se eliminaron contenedores ni redes. |
| Volúmenes ops02/ops03 | Eliminados los cuatro nombres exactos con sufijos `_minio_data` y `_postgres_data`, tras verificar proyecto y volumen en etiquetas Compose. |
| Imágenes ops02/ops03 | Eliminadas ambas etiquetas `-mlflow:latest` tras verificar proyecto/servicio; Docker informó objetos `sha256:c24049819e5b…` y `sha256:2210357ba28c…` eliminados. Sin force, eliminación de bases, capas compartidas o cachés. |
| Informes temporales autorizados | ops01/ops02 ya ausentes; ops03 eliminado tras preservar este resumen, por petición expresa. Ausencia de los tres confirmada. Ninguna ruta histórica implica un reporte retenido. |

Se usó exclusivamente `unix:///var/run/docker.sock`, `--host` explícito, entorno permitido y configuración Docker temporal anónima, retirada al salir. Inventario final objetivo: **0 contenedores, 0 redes, 0 volúmenes y 0 etiquetas de imagen**. Conjuntos de identificadores/nombres ajenos antes/después iguales: **44 contenedores, 15 redes, 36 volúmenes y 75 filas ID/etiqueta de imagen**; no prueba invariancia interna de sus datos. Sin errores ni reintentos de comandos, prune/reset, build/pull, arranque, nuevas pruebas funcionales, credenciales ambientales, SSH u operaciones remotas. Código, pruebas y Compose se conservaron; la eliminación de datos sintéticos no tiene rollback.

**Continuidad vigente:** Stack **2/9 (22.2%)**. Siguiente recomendación: **planificar STACK-02**, sujeto a aprobación propia y sin iniciarlo; dependencias conservadas API-06 (cerrado por excepción ACCEPTED_RISK, no PASS operativo) y STACK-01 (cierre manual). No se autoriza publicación, despliegue, staging, commit ni push. La receta operacional anterior sigue disponible sólo para una futura ejecución expresamente autorizada con proyecto y reporte nuevos; estos intentos eliminados no son instalaciones reutilizables.
