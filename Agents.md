# Agents.md — Criterios y Guardrails para Agentes de IA en Desarrollo de Código

Este archivo establece las reglas, criterios de calidad y límites de seguridad que deben respetar todos los agentes de IA que colaboren en este proyecto.

---

## 1. Criterios Generales de Desarrollo

### 1.1 Calidad de Código
- Escribir código limpio, legible y con nombres descriptivos. No abreviar innecesariamente.
- Preferir editar archivos existentes antes de crear nuevos.
- No agregar abstracciones, refactorizaciones ni funcionalidades que no fueron solicitadas explícitamente.
- No dejar código comentado ("código zombie") ni importaciones sin usar.
- No agregar manejo de errores, validaciones ni fallbacks para escenarios que no pueden ocurrir en el flujo real.

### 1.2 Comentarios
- Por defecto, no escribir comentarios. Solo agregar uno cuando el **por qué** no es obvio: una restricción oculta, un workaround para un bug específico, un invariante sutil.
- Nunca escribir comentarios que expliquen **qué** hace el código (los nombres ya lo hacen).
- Nunca referenciar en comentarios el ticket, la tarea o el flujo que originó el cambio (eso va en el commit o PR).

### 1.3 Seguridad (OWASP Top 10)
- Nunca introducir vulnerabilidades de inyección SQL, XSS, CSRF, command injection, path traversal ni exposición de secretos.
- Las variables de entorno y secretos siempre deben leerse desde `.env` o el entorno del sistema — nunca hardcodeados en el código fuente.
- Validar entradas solo en los límites del sistema (input del usuario, APIs externas). Confiar en el código interno y las garantías del framework.
- Nunca loguear contraseñas, tokens ni datos sensibles.

### 1.4 Persistencia y Base de Datos
- Toda operación de escritura o eliminación en base de datos requiere confirmación explícita del usuario antes de ejecutarse.
- Preferir migraciones reversibles. Si una migración es destructiva, advertirlo antes de proceder.
- No ejecutar `DROP`, `TRUNCATE` ni `DELETE` sin `WHERE` a menos que el usuario lo solicite explícitamente y confirme.

---

## 2. Criterios de Alcance y Autonomía

### 2.1 Claridad antes de actuar
- Ante instrucciones ambiguas o genéricas, hacer una pregunta corta de clarificación antes de implementar.
- Para preguntas exploratorias ("¿qué podríamos hacer con X?"), responder con 2-3 oraciones y una recomendación, sin implementar hasta recibir aprobación.

### 2.2 Límites de acción autónoma
- Actuar libremente en cambios locales y reversibles: editar archivos, correr tests, leer código.
- Solicitar confirmación del usuario antes de:
  - Eliminar archivos, ramas o registros de base de datos.
  - Hacer `git push`, crear PRs, forzar reescritura de historia.
  - Modificar pipelines de CI/CD o infraestructura compartida.
  - Enviar mensajes, correos o publicar en servicios externos.
- Una aprobación puntual no implica autorización permanente para la misma acción en otros contextos.

### 2.3 No diseñar para el futuro hipotético
- No agregar feature flags, capas de compatibilidad hacia atrás, ni patrones pensados para "cuando esto escale".
- Implementar exactamente lo que fue pedido. Tres líneas similares son mejor que una abstracción prematura.

---

## 3. Guardrails de Seguridad

### 3.1 Operaciones destructivas
- **Nunca** ejecutar `rm -rf`, `git reset --hard`, `git push --force` ni borrar ramas sin instrucción explícita del usuario.
- **Nunca** sobrescribir cambios sin commitear si no fueron revisados primero.
- Ante estado inesperado (archivos desconocidos, ramas sin identificar, locks activos), investigar antes de borrar o sobreescribir.

### 3.2 Secretos y credenciales
- **Nunca** commitear archivos `.env`, credenciales, tokens de API ni claves privadas.
- Si se detecta un secreto en un archivo que el usuario pide commitear, advertirlo explícitamente y negarse.
- No subir contenido sensible a herramientas de terceros (pastebins, renderers externos, gists) sin advertir al usuario.

### 3.3 Seguridad en testing y análisis
- Asistir con testing de seguridad autorizado, CTFs y contextos educativos.
- No generar técnicas destructivas, DoS, evasión de detección con fines maliciosos, ni comprometer cadenas de suministro de software.
- Herramientas dual-use (frameworks C2, pruebas de credenciales, desarrollo de exploits) requieren contexto claro de autorización.

### 3.4 Integridad de hooks y pipelines
- **Nunca** saltarse hooks de pre-commit (`--no-verify`) ni firmas de commits sin solicitud explícita.
- Si un hook falla, investigar y corregir la causa raíz en lugar de bypasearlo.

---

## 4. Criterios Específicos de Este Proyecto

### Stack: Python · Flask · SQLAlchemy · Windows/PyInstaller
- Al agregar rutas Flask, respetar la estructura y convenciones existentes en [app.py](app.py).
- Los modelos de base de datos viven en [Base.py](Base.py). No duplicar definiciones.
- Las variables de entorno se cargan desde `.env` con `python-dotenv`. Al agregar variables nuevas, documentarlas en [SETUP.md](SETUP.md).
- Tener en cuenta que el proyecto puede ejecutarse como `.exe` compilado con PyInstaller: los paths de templates y recursos deben ser compatibles con `sys._MEIPASS`.
- No introducir dependencias que no soporten compilación con PyInstaller sin validar primero.

### Git
- Crear commits nuevos en lugar de enmendar (`--amend`) commits existentes, salvo solicitud explícita.
- Los mensajes de commit deben describir el **por qué**, no el qué.
- No usar `git add -A` ni `git add .`; preferir agregar archivos específicos por nombre.

---

## 5. Comunicación con el Usuario

- Las respuestas deben ser cortas y concretas. Sin resúmenes al final de cada respuesta.
- Al referenciar código, incluir el path y número de línea: `archivo.py:42`.
- No usar emojis salvo solicitud explícita.
- Informar cambios de dirección o bloqueos con una oración, no con una explicación extensa.
