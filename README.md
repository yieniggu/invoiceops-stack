# invoiceops-stack

Repositorio independiente para conectar los repositorios de InvoiceOps en desarrollo y demostraciones locales. **STACK-00 sólo crea una base documental e inerte:** no contiene lógica de negocio ni un stack ejecutable.

## Estructura y estado

| Archivo | Propósito |
| --- | --- |
| `compose.yml` | Nombre del proyecto y mapa de servicios vacío. |
| `.env.example` | Sólo comentarios; ninguna variable activa. |
| `.gitignore` | Excluye entornos locales y metadatos auxiliares. |
| `README.md` | Alcance, validación y continuidad. |

No hay servicios, imágenes, builds, puertos, redes, volúmenes ni automatización. No se modifican los repositorios vecinos. El repositorio Git se inicializa localmente en `main`, sin commits ni remotos.

## Validación estática

Desde este directorio, con Ruby y Psych ya disponibles:

```bash
ruby -rpsych -e 'expected = {"name"=>"invoiceops-stack", "services"=>{}}; actual = Psych.safe_load(File.read("compose.yml"), aliases: false); abort "Unexpected scaffold configuration" unless actual == expected'
```

Esto comprueba sintaxis YAML y el objeto esperado; **no valida la semántica de Docker Compose ni su ejecución**. No ejecutar `docker compose up` para STACK-00 ni interpretar el mapa vacío como preparación operativa. No se requieren pruebas funcionales porque no hay comportamiento ejecutable.

## Seguridad

No crear ni leer `.env` para validar esta base. No copiar credenciales: `.env` y `.env.*` están ignorados, salvo `.env.example`, que contiene sólo comentarios en inglés. También se ignoran `.codegraph/` y `.DS_Store`. Esta entrega no instala dependencias, accede a red ni autoriza operaciones remotas.

## Continuidad

Sólo después de cerrar STACK-00, el siguiente ticket recomendado es STACK-01 — Stack ML local, cuya dependencia formal es MLFLOW-01, ya registrada como completada. Requiere planificación y autorización propias antes de añadir o iniciar servicios. STACK-02 espera API-06 y STACK-01; no se adelanta ninguna integración ni despliegue remoto.

## Reversión

Antes de retirar esta base, comprobar que no existan cambios posteriores o trabajo ajeno. Si permanece intacta, retirar únicamente estos cuatro archivos y sus metadatos Git locales; revertir el contador y la prioridad vigentes del seguimiento y agregar una nota de reversión sin borrar el historial del cierre. No hay servicios, datos persistentes ni estado remoto que restaurar. Esta sección describe el procedimiento; no ejecuta la eliminación.
