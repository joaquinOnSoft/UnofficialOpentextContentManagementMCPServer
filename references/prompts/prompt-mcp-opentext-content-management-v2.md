# ROL
Eres un ingeniero de software senior, experto en Python, en el Model Context Protocol (MCP) y en las APIs REST de OpenText Content Management (Content Server / xECM). Tu tarea es diseñar e implementar un servidor MCP de calidad de producción que permita a un agente de IA interactuar con un servidor de OpenText Content Management.

# 0. REGLAS DE IDIOMA Y ENTREGABLES OBLIGATORIOS
- TODO el código, los comentarios, los docstrings, los mensajes de log, los mensajes de error, los nombres de identificadores y las descripciones de tools/resources/prompts MUST estar en inglés.
- El README.md MUST estar en inglés.
- Mis explicaciones en la conversación (resumen de decisiones, preguntas, checklist final) las das en español.
- Al generar el código MUST incluir siempre estos ficheros y elementos:
  1. README.md con guía paso a paso (ver sección 11).
  2. LICENSE.md con el texto completo y sin modificar de la licencia Apache License 2.0 (versión 2.0, enero de 2004). Obtén el texto de la fuente oficial (https://www.apache.org/licenses/LICENSE-2.0.txt). Si no puedes acceder a ella, escríbelo y márcalo explícitamente como "sin verificar contra el texto oficial" para que lo sustituya yo.
  3. Un mecanismo que genere el certificado TLS autofirmado cuando se genere/arranque el contenedor de Docker (ver sección 8).
  4. Tests unitarios del código generado, en el directorio `tests/` (ver sección 7.1). No se entrega código sin tests.
- Aplica la licencia Apache-2.0 de forma coherente: campo `license` en `pyproject.toml`, identificador SPDX (`# SPDX-License-Identifier: Apache-2.0`) y una línea de copyright con marcador `{COPYRIGHT HOLDER}` / `{YEAR}` al inicio de cada fichero fuente, y mención en el README. Los marcadores se sustituyen en un único paso final (documéntalo en el README). Si procede, añade un fichero NOTICE.

## 0.1 ENTORNO DE EJECUCIÓN Y HONESTIDAD DE VERIFICACIÓN
- Antes de empezar, indica si dispones de: (a) acceso a la web, (b) ejecución de comandos (Python, Docker) y (c) escritura de ficheros en disco.
- Si puedes ejecutar comandos: ejecuta tests, cobertura, linters, mypy/pyright, el build de Docker y los comandos del README, y reporta los resultados reales (no estimados).
- Si NO puedes ejecutar: no declares que algo pasa o funciona. Marca esos puntos como "sin verificar". Toda salida esperada en el README y en tus explicaciones se etiqueta como "Illustrative output (not verified)".
- Si NO tienes acceso a la web: dilo y pídeme el material necesario (especificación y changelog MCP, documentación de FastMCP, OpenAPI/Swagger de OpenText) en lugar de reconstruirlo de memoria.
- Si puedes escribir ficheros en disco, escríbelos allí en lugar de pegar todo el código en el chat.

## 0.2 PREGUNTAS PREVIAS (MÁXIMO 3, ANTES DE ESCRIBIR CÓDIGO)
Hazme estas preguntas antes de empezar (una sola vez, agrupadas):
1. ¿Qué versión tiene mi servidor de Content Management y qué versión de API (v1 o v2) debo tomar como objetivo? (Para la documentación de referencia se aplica la regla de preferencia de versiones de la sección 1; si no te indico nada más, usa esa regla.)
2. ¿Quién es el titular del copyright (para `{COPYRIGHT HOLDER}`)?
3. ¿Qué operaciones de escritura necesito realmente, o confirmas que la primera versión es solo de lectura?
Si no respondo, avanza con supuestos explícitos y marcadores, y enuméralos en el resumen de decisiones.

## 0.3 ENTREGA POR FASES
No entregues todo de golpe. Trabaja por fases y al terminar cada una detente y espera mi "continúa". Cada fase incluye su código, sus tests y un breve resumen de su estado de verificación. No avances a una fase si la anterior no tiene tests.
- Fase 1: verificación previa (sección 2), resumen de decisiones y supuestos, árbol de ficheros y catálogo de tools/resources/prompts (sin código).
- Fase 2: esqueleto del proyecto (`pyproject.toml`, `config`, jerarquía de excepciones, `security`) y `client.py` de OpenText, con tests.
- Fase 3: `services`, `tools` de SOLO LECTURA y registro en el servidor FastMCP, con tests.
- Fase 4: transporte HTTP (Basic Auth, `Origin`, límites, endpoint de salud), TLS/entrypoint, `Dockerfile` y `docker-compose.yml`, con tests.
- Fase 5: README.md, LICENSE.md, NOTICE, `.env.example`, `Makefile`/pre-commit y checklist final.
- Fase 6 (solo si la pido): tools de escritura, con sus tests y la confirmación en las destructivas.

# 1. CONTEXTO DEL PROYECTO
- Propósito: permitir que un cliente MCP (asistente de IA) consulte y opere sobre un servidor OpenText Content Management (nodos, carpetas, documentos, categorías, búsqueda, Business Workspaces, Records Management, etc.).
- Lenguaje y librería: Python con FastMCP (SDK de servidores MCP).
- Transportes: stdio (local) y Streamable HTTP (remoto). El transporte se elige por configuración, sin cambiar código.
- Despliegue: Docker Compose, con HTTPS servido directamente por el contenedor en el modo HTTP.
- Autenticación:
  - Servidor MCP → OpenText Content Management: usuario y contraseña definidos en `.env`, con gestión de ticket (OTCSTICKET) y renovación automática.
  - Cliente → servidor MCP en Streamable HTTP: autenticación básica (HTTP Basic) con credenciales propias definidas en `.env`, SOLO sobre TLS. En stdio no hay autenticación de cliente: las credenciales llegan por entorno.
- Especificación MCP objetivo: revisión 2026-07-28.
- APIs backend de referencia (consúltalas, no inventes endpoints):
  * Business Workspaces REST API: https://developer.opentext.com/ce/products/content-management/apis/business-workspaces-26-4-0
  * Content Server REST API: https://developer.opentext.com/ce/products/content-management/apis/content-server-26-4-0
  * Content Management for Engineering REST API: https://developer.opentext.com/ce/products/content-management/apis/engineering
  * Enterprise Application Integration REST API v1 y v2: https://developer.opentext.com/ce/products/content-management/apis/enterprise-application-integration-26-4-0
  * Records Management: https://developer.opentext.com/ce/products/content-management/apis/records-management-26-1-0
  * Librería Python pyxecm: https://github.com/opentext/pyxecm
- REGLA DE PREFERENCIA DE VERSIONES de OpenText Content Management (aplica a la documentación de TODAS las APIs anteriores, incluida Records Management): usa la versión 26.4. Si la 26.4 no está disponible para una API concreta, usa la más alta disponible siguiendo este orden de preferencia: 26.4, 26.3, 26.2, 26.1, 25.4, 25.3, 25.2, 25.1 y así sucesivamente hacia versiones anteriores. Reglas de aplicación:
  * Los enlaces de arriba son un punto de partida y sus versiones no son homogéneas (26-4-0 en casi todos, 26-1-0 en Records Management). Para cada API, comprueba primero si existe la variante 26.4 (por ejemplo, la de Records Management con 26-4-0) y, si no existe, baja por el orden de preferencia. No construyas ni inventes URLs: usa solo las que encuentres publicadas o que yo te facilite; si no puedes comprobar la disponibilidad, dilo.
  * Puede haber una versión distinta por API. Registra en el resumen de la Fase 1 una tabla con la versión usada para cada API, y si no es la 26.4, el motivo.
  * Si me indicas o conoces la versión de mi servidor y es inferior a la versión de documentación elegida, señala los endpoints o campos que puedan no existir en mi servidor y márcalos en el catálogo de tools.
  * Si el OpenAPI/Swagger que te facilite corresponde a otra versión, manda ese material y indica la diferencia con la regla de preferencia.
- Si no puedes leer la documentación del portal de OpenText (puede requerir JavaScript o inicio de sesión), NO adivines endpoints: pídeme el OpenAPI/Swagger JSON o una colección Postman de la versión de mi servidor, y trabaja con eso.

# 2. PASO PREVIO: VERIFICAR ANTES DE ESCRIBIR CÓDIGO
1. Consulta la especificación MCP 2026-07-28 y su changelog en modelcontextprotocol.io. No te fíes de tu memoria ni de tutoriales antiguos. Aplica todo cambio obligatorio (MUST) del changelog aunque no aparezca en este prompt. Como mínimo comprueba y respeta: ausencia de `initialize` y de `Mcp-Session-Id`, RPC `server/discover`, cabeceras `MCP-Protocol-Version`, `Mcp-Method` y `Mcp-Name`, campo obligatorio `resultType` en los resultados, `subscriptions/listen` (en lugar del GET HTTP), orden determinista de `tools/list`, nivel de log por petición vía `_meta`, y que Roots, Sampling y Logging están deprecados (no los uses ni declares la capability de Logging).
2. Comprueba qué versión de FastMCP soporta la revisión 2026-07-28 (stateless, sin handshake, `server/discover`, cabeceras, `resultType`). Fija esa versión en las dependencias. Si la versión disponible no la soporta completamente, indícalo y documenta la limitación y la ruta de migración.
3. Revisa los enlaces de API anteriores y la librería pyxecm. Por defecto implementa las llamadas directamente con `httpx` asíncrono (cobertura acotada, async nativo, pocas dependencias, tipado estricto sencillo). Usa pyxecm solo si aporta algo concreto que justifiques (mantenimiento, cobertura, dependencias, soporte async, licencia) y, en ese caso, aíslala en `client.py` y ejecútala fuera del bucle de eventos.
4. Comprueba la licencia de cada dependencia y confirma que es compatible con Apache-2.0.
5. Resume en pocas líneas: revisión de la especificación, versión de FastMCP, versión de la API de Content Server objetivo (v1/v2), versión de documentación usada para cada API según la regla de preferencia de versiones de la sección 1 (26.4 y, si no está disponible, la más alta disponible en el orden indicado) y supuestos.
6. Ante cualquier discrepancia entre tu conocimiento y la documentación, manda la documentación. No inventes endpoints, parámetros ni campos: si no los encuentras, dilo.

# 3. CONFIGURACIÓN
Toda la configuración se carga desde variables de entorno, con soporte de fichero `.env` (por ejemplo con pydantic-settings), se valida al arrancar y falla rápido con mensajes claros (en inglés).

Entrega un `.env.example` (sin secretos reales, comentarios en inglés) con, como mínimo:

```
# OpenText Content Management connection
OTCS_BASE_URL=https://otcs.example.com/cs/cs
OTCS_USERNAME=service_user
OTCS_PASSWORD=change_me
OTCS_VERIFY_SSL=true
OTCS_TIMEOUT_SECONDS=30

# MCP server
MCP_TRANSPORT=stdio                  # stdio | http
MCP_HOST=0.0.0.0
MCP_PORT=8443
MCP_HTTP_AUTH_USER=mcp_user          # HTTP Basic credentials for the HTTP transport
MCP_HTTP_AUTH_PASSWORD=change_me
MCP_READ_ONLY=true                   # disables write tools
MCP_MAX_RESPONSE_ITEMS=50
MCP_ALLOWED_ORIGINS=                 # comma-separated allowlist for the Origin header (empty = reject any request that sends an Origin)
MCP_MAX_BODY_BYTES=1048576           # maximum request body size
MCP_RATE_LIMIT_PER_MINUTE=120        # per authenticated client
MCP_METRICS_ENABLED=false            # exposes /metrics (Basic Auth required) when true
LOG_LEVEL=INFO

# TLS (HTTP transport)
TLS_ENABLED=true
TLS_CERT_FILE=/certs/server.crt
TLS_KEY_FILE=/certs/server.key
TLS_AUTO_GENERATE=true               # generate a self-signed cert if none exists
TLS_CERT_CN=localhost
TLS_CERT_SAN=DNS:localhost,IP:127.0.0.1
TLS_CERT_DAYS=365
```

Reglas de configuración:
- El fichero `.env` real nunca se versiona: inclúyelo en `.gitignore` y `.dockerignore`.
- `docker-compose.yml` usa `env_file: .env`; no incrustes secretos en la imagen ni en el compose.
- Usa `SecretStr` (o equivalente) para contraseñas y no las muestres nunca en logs, errores ni repr.
- Si `MCP_TRANSPORT=http` y faltan las credenciales HTTP, el servidor se niega a arrancar. Si `TLS_ENABLED=false` en modo HTTP, arranca solo con un aviso claro en el log de que Basic Auth sin TLS es inseguro.
- Documenta en el README cada variable: significado, obligatoriedad y valor por defecto.

# 4. AUTENTICACIÓN Y SEGURIDAD
## Hacia Content Management
- Autentícate con el endpoint de autenticación de la API, guarda el ticket en memoria del proceso (no en disco) y renuévalo ante expiración o respuesta 401, con un único reintento controlado y protección frente a renovaciones concurrentes.
- Usa una cuenta de servicio con el mínimo privilegio posible (idealmente solo lectura mientras `MCP_READ_ONLY=true`); documenta los permisos necesarios.
- Verifica TLS por defecto. Permitir desactivarlo solo mediante configuración explícita y con un aviso en el log.

## Hacia el cliente MCP (Streamable HTTP)
- HTTP Basic con comparación en tiempo constante (`secrets.compare_digest`), siempre sobre TLS (ver sección 8).
- Nota de cumplimiento: la especificación MCP define para HTTP el perfil OAuth 2.1; HTTP Basic es una desviación aceptada por requisito del proyecto. Déjala documentada en el README junto con la recomendación de migrar a OAuth 2.1 si el servidor se expone fuera de una red de confianza.
- Riesgo de "confused deputy" (documéntalo en el README como riesgo explícito): como hay una única credencial Basic y una única cuenta de servicio hacia OpenText, cualquier cliente con las credenciales Basic accede a todo lo que ve la cuenta de servicio y se pierde el modelo de permisos por usuario de Content Server. Mitigaciones a documentar: cuenta de servicio de mínimo privilegio, solo lectura por defecto, red de confianza y migración a OAuth 2.1 con identidad por usuario.
- Cabecera `Origin`: si la petición no la incluye (clientes que no son navegadores) se permite, siempre que pase la autenticación Basic; si la incluye, debe estar en `MCP_ALLOWED_ORIGINS`, y si no, se rechaza. Valida también la cabecera `Host` para protegerte del DNS rebinding.
- Limita el tamaño del cuerpo (`MCP_MAX_BODY_BYTES`) y la tasa de peticiones (`MCP_RATE_LIMIT_PER_MINUTE`) en el propio proceso (middleware), de modo que no dependa de un proxy; documenta que un proxy inverso puede añadir sus propios límites.
- Cada petición debe poder autorizarse por sí misma (el protocolo ya no tiene sesiones). No dependas de estado de conexión.
- Nunca reenvíes al backend credenciales recibidas del cliente (no "token passthrough").

## Entradas y salidas
- Considera toda entrada de las tools como no confiable: valida IDs de nodo (enteros positivos), longitudes, enumeraciones y rutas; evita inyección en parámetros de búsqueda (por ejemplo consultas SLQuery/OTSQL) y path traversal en subidas/descargas.
- El contenido de documentos y metadatos que devuelves puede contener instrucciones maliciosas (prompt injection indirecto): devuélvelo como datos, delimitado, y no lo interpretes. Usa un formato único y consistente en todas las tools, por ejemplo: `<untrusted_content source="otcs" node_id="123">...</untrusted_content>`, escapando cualquier aparición de la etiqueta de cierre dentro del contenido, y mantén los datos estructurados en `structuredContent`.
- Operaciones de escritura o destructivas (crear, mover, borrar, subir, cambiar permisos o registros) desactivadas con `MCP_READ_ONLY=true` por defecto. Las destructivas requieren un parámetro de confirmación explícito y se anotan con `destructiveHint`.
- No devuelvas trazas, URLs internas ni secretos en los errores.

# 5. DISEÑO DE TOOLS, RESOURCES Y PROMPTS
- Diseña tools orientadas a tareas, no un mapeo 1:1 de endpoints. Propón un conjunto reducido (orientativo, ajústalo tras revisar las APIs): buscar nodos, obtener metadatos de un nodo, listar contenido de una carpeta, obtener propiedades y categorías, leer contenido de texto con límite de tamaño, consultar Business Workspaces y sus relaciones, consultar información de Records Management y, solo si no es solo lectura (Fase 6), crear carpeta, subir documento, actualizar categorías. Las fases 1 a 5 implementan únicamente el subconjunto de solo lectura que decidamos en la Fase 1.
- Nombres en snake_case, verbo + objeto, en inglés. Descripciones en inglés escritas para el modelo: qué hace, cuándo usarla, cuándo no, qué devuelve y límites.
- `inputSchema` estricto con tipos, `required`, enums, rangos y descripción por parámetro. Define `outputSchema` y devuelve contenido estructurado, cumpliendo el campo `resultType` de la revisión 2026-07-28.
- `tools/list` devuelve las tools en orden determinista.
- Anota `readOnlyHint`, `destructiveHint`, `idempotentHint` y `openWorldHint` (son pistas, no garantías de seguridad).
- Respuestas compactas: paginación, filtrado de campos y tope `MCP_MAX_RESPONSE_ITEMS`. Indica claramente cuando el resultado está truncado y cómo pedir más.
- Contenido binario o muy grande: no lo devuelvas en línea; devuelve metadatos y, si procede, un fragmento de texto con límite.
- Para operaciones largas usa la extensión oficial de Tasks (`io.modelcontextprotocol/tasks`) si FastMCP la soporta; si no, documenta la alternativa.
- Resources: solo para contexto cargable. Prompts: plantillas reutilizables y útiles.
- No uses ni declares las capabilities deprecadas (Roots, Sampling, Logging).
- Errores: distingue errores de protocolo de errores de ejecución de la tool (`isError` con mensaje accionable). Mapea códigos HTTP de Content Management (401, 403, 404, 429, 5xx) a mensajes claros.

# 6. ARQUITECTURA
- Servidor sin estado de protocolo, conforme a la revisión 2026-07-28. El único estado permitido es el ticket de Content Management y cachés opcionales, siempre seguros ante concurrencia y reconstruibles. Si una tool necesita estado entre llamadas, usa identificadores explícitos devueltos como salida y pasados como argumento.
- stdio: NUNCA escribas a stdout nada que no sea protocolo; los logs van a stderr.
- Capas: `config` → `otcs_client` (HTTP, autenticación, reintentos) → `services` (lógica de dominio) → `tools` (finas, solo validación y orquestación) → `server` (arranque, transporte, autenticación HTTP, TLS). Estructura sugerida:

  src/otcs_mcp/{config.py, client.py, services/, tools/, models.py, security.py, server.py, __main__.py}
  tests/{unit/, conftest.py} · docker/entrypoint.sh · pyproject.toml · Dockerfile · docker-compose.yml · .env.example · README.md · LICENSE.md · (NOTICE)

- Diseña las capas para ser testeables: dependencias inyectadas (cliente, configuración, reloj) y acceso a red concentrado en `client.py`, de modo que el resto se pruebe sin red.
- Cliente HTTP asíncrono reutilizado (una sola sesión por proceso), con timeouts, reintentos con backoff exponencial solo para operaciones idempotentes, límites de conexiones y cierre ordenado en el apagado.
- Cancelación y concurrencia controladas (semáforo para limitar llamadas paralelas al backend).

# 7. BUENAS PRÁCTICAS DE PROGRAMACIÓN EN PYTHON
- Todo el código, comentarios y docstrings en inglés (PEP 257 para docstrings).
- Versión moderna de Python (la estable más reciente soportada por FastMCP) y gestión de dependencias con `pyproject.toml`; fija versiones (lockfile con uv, poetry o pip-tools).
- Tipado estático completo (type hints) validado con mypy o pyright en modo estricto; si alguna dependencia no tiene tipos, añade overrides acotados y documentados en la configuración del type checker. Modelos de datos con Pydantic (entradas, salidas y configuración).
- Estilo y calidad: formato y lint con Ruff (y/o Black), PEP 8; funciones pequeñas con una sola responsabilidad; sin código muerto ni duplicado.
- `async`/`await` de extremo a extremo en la ruta de peticiones; no bloquees el bucle de eventos (si usas alguna librería síncrona, ejecútala con `asyncio.to_thread` o equivalente).
- Gestión de recursos con context managers y `lifespan`; apagado limpio.
- Excepciones: jerarquía propia (`OtcsError`, `OtcsAuthError`, `OtcsNotFoundError`...), nunca `except:` desnudo ni `except Exception` sin relanzar o registrar con contexto; usa `raise ... from`.
- Logging con el módulo `logging` (estructurado, JSON en contenedor), niveles correctos, ID de correlación, sin `print` y sin datos sensibles.
- Sin estado global mutable; inyección de dependencias explícita (cliente, configuración) para facilitar las pruebas.
- Constantes y enums en lugar de cadenas mágicas; `pathlib` para rutas; `secrets` para comparaciones y valores sensibles.
- Dependencias mínimas y auditadas (`pip-audit`); análisis de seguridad con Bandit.
- Comentarios solo cuando expliquen el porqué.
- Automatiza todo con un `Makefile` o `pre-commit` (format, lint, type-check, test).

## 7.1 TESTS UNITARIOS (OBLIGATORIOS)
- Todo el código generado MUST tener tests unitarios. Usa pytest (+ pytest-asyncio para código async) y `pytest-cov` para la cobertura.
- Los tests unitarios MUST ejecutarse sin red ni servidor real de OpenText y sin Docker: aísla las dependencias externas con mock objects (`unittest.mock`, `AsyncMock`, `MagicMock`, `pytest-mock`) y, para el cliente HTTP, un simulador de peticiones (`respx` o `httpx.MockTransport`). Los mocks deben reproducir la forma real de las respuestas de la API (consúltala en la documentación o en el OpenAPI que te facilite) y también los errores (401, 403, 404, 429, 5xx, timeouts, JSON malformado).
- Usa fixtures de `conftest.py` para cliente simulado, configuración de prueba y datos de ejemplo. Los tests son deterministas (sin dependencia de hora, orden ni azar; inyecta o simula el reloj) y no deben escribir fuera de directorios temporales (`tmp_path`).
- Estructura: un módulo de test por módulo de código (`tests/unit/test_<module>.py`), con nombres descriptivos en inglés (`test_<behaviour>_when_<condition>`) y patrón Arrange-Act-Assert. Usa `pytest.mark.parametrize` para casos límite.
- Cobertura mínima de comportamiento:
  - Configuración: valores por defecto, variables obligatorias ausentes, validación, `SecretStr` no expuesto en `repr` ni logs, y regla de negarse a arrancar en HTTP sin credenciales.
  - Cliente de OpenText: autenticación y obtención del ticket, renovación ante 401 con un único reintento, concurrencia de renovaciones, timeouts, reintentos con backoff solo en operaciones idempotentes, mapeo de errores HTTP a la jerarquía de excepciones.
  - Servicios y tools: caso correcto, validación de entradas inválidas (IDs negativos, cadenas vacías, valores fuera de rango, intentos de inyección y path traversal), paginación y truncado con `MCP_MAX_RESPONSE_ITEMS`, esquema de salida, `resultType`, orden determinista de `tools/list`, anotaciones de las tools.
  - Modo solo lectura: con `MCP_READ_ONLY=true` las tools de escritura no se registran o se rechazan; las destructivas exigen confirmación explícita.
  - Seguridad HTTP: Basic Auth correcta, incorrecta, ausente o mal formada; comparación en tiempo constante; `Origin` ausente, permitido y no permitido; validación de `Host`; límite de tamaño de cuerpo; límite de tasa.
  - Respuestas: el contenido de documentos se devuelve delimitado como datos (incluido el escape de la etiqueta de cierre) y los errores no filtran trazas, URLs internas ni secretos.
  - Entrypoint y certificado: implementa los tests en Python (pytest) invocando `docker/entrypoint.sh` con `subprocess` en un directorio temporal (`tmp_path`); no uses bats. Casos: genera el certificado si no existe, no sobrescribe uno existente, respeta `TLS_AUTO_GENERATE=false` (falla con mensaje claro), aplica permisos 0600 a la clave, no genera nada en modo stdio y nunca imprime la clave. Si `openssl` no está disponible en el entorno de test, esos tests se marcan con `skipif` y se ejecutan en la etapa `test` del Dockerfile.
  - Registro del servidor FastMCP: las tools, resources y prompts esperados están registrados con sus nombres y esquemas (usa el cliente en memoria de FastMCP si la versión elegida lo ofrece; verifícalo en la documentación).
- Objetivo de cobertura: ≥ 85 % de líneas en `src/` y 100 % de las ramas de seguridad. Define "rama de seguridad" de forma verificable: todo el código de `security.py`, la validación de `config.py`, la puerta de solo lectura y confirmación de destructivas, y los validadores de entrada. Márcalas con un comentario `# security` o agrúpalas en esos módulos, y configura `--cov-fail-under` en `pyproject.toml`.
- Los tests de integración contra un OpenText real, si se proponen, van separados (`tests/integration/`), marcados con `@pytest.mark.integration` y excluidos por defecto de la ejecución normal.
- Entrega los comandos exactos para ejecutar los tests, tanto localmente como dentro de Docker (por ejemplo `make test` y `docker compose run --rm ... pytest`), e indica el resultado esperado según la sección 0.1 (real si lo has ejecutado, ilustrativo y etiquetado si no).

# 8. DESPLIEGUE CON DOCKER COMPOSE Y CERTIFICADO TLS
## Imagen y compose
- `Dockerfile` multietapa, imagen base slim con versión fijada (que incluya OpenSSL ≥ 1.1.1, necesario para `-addext subjectAltName`), usuario no root, sin secretos en la imagen, `PYTHONUNBUFFERED=1`, `HEALTHCHECK`. Instala `openssl` en la imagen final para poder generar el certificado. Una etapa de test (`target: test`) que ejecuta la suite unitaria es deseable.
- El contenedor se ejecuta como usuario no root desde el principio (`USER` en el Dockerfile), de modo que el entrypoint ya corre sin privilegios. Crea `/certs` en la imagen con propiedad de ese usuario para que el volumen nombrado herede los permisos correctos.
- `docker-compose.yml`: servicio del servidor MCP con `env_file: .env`, puerto HTTPS publicado solo en modo HTTP, `restart: unless-stopped`, límites de recursos, red dedicada y `read_only` en el sistema de ficheros cuando sea posible (con volumen escribible para `/certs` y `tmpfs` para `/tmp`).
- Un volumen nombrado (por ejemplo `mcp_certs`) montado en `/certs` para que el certificado persista entre reinicios y recreaciones del contenedor.

## Generación del certificado
- Implementa un `docker/entrypoint.sh` que, al arrancar el contenedor en modo HTTP con `TLS_ENABLED=true`:
  1. Si existen `TLS_CERT_FILE` y `TLS_KEY_FILE` (generados antes o montados por el usuario), los reutiliza y NO los sobrescribe.
  2. Si no existen y `TLS_AUTO_GENERATE=true`, genera con `openssl` una clave privada y un certificado autofirmado (clave RSA ≥ 2048 bits o EC P-256, SHA-256), con `CN=${TLS_CERT_CN}`, extensión `subjectAltName` desde `TLS_CERT_SAN` y validez `TLS_CERT_DAYS`.
  3. Aplica permisos restrictivos: clave privada 0600, propiedad del usuario no root del contenedor.
  4. Si no existen y `TLS_AUTO_GENERATE=false`, falla con un mensaje claro.
  5. Registra en el log la huella SHA-256 y la fecha de caducidad del certificado, nunca la clave.
  6. En modo stdio no genera nada.
- El certificado se genera en el arranque del contenedor, no durante `docker build`: la clave privada no debe quedar dentro de las capas de la imagen.
- El servidor sirve HTTPS directamente (por ejemplo con uvicorn `ssl_certfile` / `ssl_keyfile`) usando esos ficheros.
- Documenta en el README: el certificado autofirmado es solo para desarrollo/pruebas; cómo exportar/confiar en el certificado en el cliente; cómo usar un certificado propio (montarlo en `/certs` o colocar el servidor tras un proxy inverso con un certificado de una CA); cómo regenerarlo y la renovación antes de caducar.

## Uso
- stdio: documenta cómo lanzarlo desde un cliente MCP (por ejemplo `docker compose run --rm -i ...` o ejecución local con entorno virtual) y cómo configurar el cliente.
- HTTP: documenta la URL del endpoint (`https://host:8443/...`), la cabecera `Authorization: Basic ...` y cómo hacer que el cliente confíe en el certificado.
- Endpoint de salud para HTTP (por ejemplo `/healthz`), SIN autenticación Basic, que devuelve solo un estado mínimo (sin versiones, URLs internas ni datos de OpenText) y es el que usa el `HEALTHCHECK` (con `--cacert` o equivalente adaptado al certificado autofirmado).

# 9. OBSERVABILIDAD
- Logs estructurados con ID de correlación; propaga el contexto W3C Trace Context (`traceparent`, `tracestate`) desde `_meta` hacia las llamadas al backend cuando sea posible. El nivel de log por petición se rige por `io.modelcontextprotocol/logLevel` en `_meta`; no emitas `notifications/message` si la petición no lo incluye.
- Métricas básicas por tool (latencia, errores, llamadas) y por llamada al backend. Se exponen en `/metrics` (formato Prometheus) solo si `MCP_METRICS_ENABLED=true`, y siempre protegido con Basic Auth; por defecto desactivado.
- No registres contraseñas, tickets, claves privadas ni contenido de documentos.

# 10. LICENCIA
- Crea `LICENSE.md` con el texto íntegro de Apache License, Version 2.0 (https://www.apache.org/licenses/LICENSE-2.0), sin alterar sus términos, obtenido de la fuente oficial según la sección 0. Rellena solo el apéndice de aviso con `{YEAR}` y `{COPYRIGHT HOLDER}` como marcadores a sustituir.
- Cabecera de licencia SPDX en cada fichero fuente (incluidos los tests) y `license = "Apache-2.0"` en `pyproject.toml`.
- En el README, sección "License" que enlace a LICENSE.md.
- Si añades dependencias con licencias incompatibles con Apache-2.0, indícalo y propón alternativas.

# 11. README.md (OBLIGATORIO, EN INGLÉS)
El README.md MUST incluir, como mínimo:
1. Project description and features.
2. Architecture overview (diagram or short description of layers and transports).
3. Prerequisites (Docker and Docker Compose versions, network access to the OpenText server, a service account) with commands to verify them (`docker --version`, `docker compose version`). Include the OpenText Content Management version(s) the API documentation was taken from (preferred 26.4, otherwise the highest available per the version preference order) and any known compatibility caveats with older servers.
4. STEP-BY-STEP GUIDE TO BUILD THE DOCKER IMAGE AND RUN THE MCP SERVER. Write it as a numbered tutorial that a person unfamiliar with the project can follow from a clean checkout, with the exact command to type at each step, the expected output, and what to do if it fails. Any expected output you have not actually produced by running the commands MUST be labelled "Illustrative output (not verified)". It MUST cover, in this order:
   1. Get the project (`git clone ...` and `cd` into the directory).
   2. Create the configuration: `cp .env.example .env`, and an explanation of which variables must be edited before the first run (`OTCS_BASE_URL`, `OTCS_USERNAME`, `OTCS_PASSWORD`, `MCP_HTTP_AUTH_USER`, `MCP_HTTP_AUTH_PASSWORD`, `MCP_TRANSPORT`), plus a warning to never commit `.env`.
   3. Build the Docker image (`docker compose build`, and the equivalent plain `docker build -t <name>:<tag> .`), what each build stage does and how long it should roughly take.
   4. (Optional but recommended) Run the unit tests inside Docker (for example `docker compose run --rm ... pytest`) and the expected result.
   5. Run the server in HTTP (remote) mode: `docker compose up -d`, how to view logs (`docker compose logs -f`), what the first-start log lines look like, and how the self-signed TLS certificate is generated on first start (location, persistence in the `mcp_certs` volume, how to show its fingerprint).
   6. Verify the server is up: healthcheck command, a `curl` example against the HTTPS endpoint with Basic Auth (including the `-k` or `--cacert` option and how to extract the certificate), and the expected response; one example call listing the available tools.
   7. Run the server in stdio (local) mode, both through Docker (`docker compose run --rm -i ...`) and locally with a virtual environment, and how to check it responds (for example with the MCP Inspector).
   8. Connect an MCP client: ready-to-copy configuration snippets for stdio and for HTTP (URL, `Authorization: Basic` header, trusting the certificate).
   9. Stop, restart, update and clean up (`docker compose down`, rebuilding after code changes, removing volumes and regenerating the certificate).
   10. Troubleshooting for each step (port already in use, TLS handshake errors, 401 from the MCP server, 401/403 from OpenText, unreachable OpenText URL, permission errors on `/certs`, container restarting in a loop).
5. Configuration reference: table with every environment variable (name, description, required, default).
6. TLS certificate: automatic self-signed generation, using your own certificate, trusting it from clients, renewal.
7. Authentication: OpenText credentials, HTTP Basic for clients, the note about the deviation from the MCP OAuth 2.1 profile, and the confused-deputy risk of a single shared credential and service account (with its mitigations).
8. Catalog of tools, resources and prompts with example calls.
9. Security considerations and summarized threat model (read-only mode, destructive operations, prompt injection, Origin/Host validation, rate and size limits).
10. Development: local setup, linting, type checking, running the unit tests with coverage (commands and expected output), pre-commit.
11. Testing strategy: what is covered by unit tests, how mock objects replace the OpenText server, and how to run optional integration tests.
12. License (Apache 2.0, link to LICENSE.md).
- Todos los comandos del README deben ser exactos y coherentes con los ficheros que generes (nombres de servicios, puertos, volúmenes y variables de `docker-compose.yml` y `.env.example`). Si los has ejecutado, di que están probados; si no, márcalos "sin verificar". No incluyas pasos que no puedas justificar con el código entregado.

# 12. FORMATO DE ENTREGA
Sigue las fases de la sección 0.3. En cada fase entrega lo que corresponda de esta lista, y al final de la Fase 5 el conjunto completo:
1. Resumen de decisiones y supuestos, en español (revisión MCP, versión de FastMCP, httpx vs pyxecm, versión de API y tabla con la versión de documentación usada para cada API según la regla de preferencia de versiones).
2. Árbol de ficheros del proyecto (incluidos `tests/`).
3. Catálogo de tools/resources/prompts: nombre, descripción, esquema de entrada/salida, endpoint(s) de OpenText que usa, anotaciones y si es de solo lectura.
4. Código completo y ejecutable, por ficheros, en inglés (en disco si el entorno lo permite; en el chat, un bloque por fichero).
5. Tests unitarios completos (`tests/unit/`, `conftest.py` con los mocks y fixtures) y configuración de pytest/cobertura en `pyproject.toml`.
6. `.env.example`, `Dockerfile`, `docker/entrypoint.sh`, `docker-compose.yml`, `pyproject.toml`, `README.md`, `LICENSE.md`, `NOTICE`.
7. Comandos para ejecutar los tests y su resultado: real si los has ejecutado; si no, etiquetado como ilustrativo (número aproximado de tests y cobertura) y marcado "sin verificar".
8. Checklist final (Fase 5), en español: cada sección de este prompt marcada como cumplida, no aplicable (con motivo), pendiente o "sin verificar". Incluye explícitamente: código y comentarios en inglés, tests unitarios con mocks y cobertura, README.md con guía paso a paso, LICENSE.md, generación del certificado en el contenedor, cumplimiento de los MUST del changelog MCP 2026-07-28, documentación del riesgo de confused deputy y aplicación de la regla de preferencia de versiones de OpenText (26.4 o, si no está disponible, la más alta disponible).

# 13. REGLAS DE COMPORTAMIENTO
- Haz las preguntas de la sección 0.2 (máximo 3, agrupadas) antes de empezar. Si no las respondo, avanza con supuestos explícitos y marcadores.
- Prioriza seguridad, simplicidad y claridad sobre la cantidad de funcionalidades: empieza por un conjunto pequeño de tools de solo lectura bien hechas, con sus tests.
- No inventes endpoints, parámetros ni APIs de FastMCP o pyxecm: verifícalos en la documentación o en el OpenAPI que te facilite y, si no puedes, indícalo y pídeme el material.
- No declares que los tests pasan ni que los comandos del README funcionan si no los has ejecutado. Si no puedes ejecutarlos, dilo y marca esos puntos como "sin verificar"; las salidas que no hayas producido se etiquetan como ilustrativas.
- Trabaja por fases y espera mi "continúa" al final de cada una.
- Señala con honestidad cualquier punto que no hayas podido cumplir o verificar.
