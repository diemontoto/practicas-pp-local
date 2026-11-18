# Publicar en PythonAnywhere (cuenta gratuita)

Resultado: `https://TU_USUARIO.pythonanywhere.com`, con los datos guardados en disco permanente (512 MB).

**Límites de la cuenta gratuita:** 1 aplicación, 1 proceso, 100 segundos de CPU por día, la aplicación vence al mes (hay que extenderla), sin dominio propio, sin SSH y sin tareas programadas en cuentas nuevas. Sirve para uso liviano; con muchos usuarios simultáneos se puede agotar la CPU diaria.

## 1. Subir el código a GitHub
Desde VS Code: `git push` del repositorio (ver pasos anteriores). El repositorio puede ser privado: en ese caso use un token personal al clonar, o suba el ZIP desde la pestaña **Files** de PythonAnywhere.

## 2. Crear la cuenta
En pythonanywhere.com → *Pricing & signup* → **Create a Beginner account**. Anote su nombre de usuario.

## 3. Traer el código e instalar dependencias
Pestaña **Consoles → Bash**:
```bash
git clone https://github.com/TU_USUARIO_GITHUB/practicas-pp.git
cd practicas-pp
python3.12 -m venv ~/venv-pp          # si 3.12 no existe, use python3.11 o 3.13
source ~/venv-pp/bin/activate
pip install Flask openpyxl reportlab
```
(No hace falta instalar `gunicorn` ni `waitress`: PythonAnywhere trae su propio servidor.)

## 4. Crear el administrador
En la misma consola:
```bash
export PP_INSTANCE=$HOME/pp-data
python manage.py create-admin admin       # pide la contraseña (8+ caracteres, letras y números)
```
Opcional, sólo para probar con datos ficticios: `python manage.py seed-demo`.

## 5. Crear la aplicación web
Pestaña **Web → Add a new web app** → *Manual configuration* → misma versión de Python que usó en el paso 3.
1. **Virtualenv:** `/home/TU_USUARIO/venv-pp`
2. **WSGI configuration file:** haga clic en el enlace, borre todo y pegue el contenido de `pythonanywhere_wsgi.py` (editando `TU_USUARIO` y la clave secreta; genere una con `python -c "import secrets;print(secrets.token_hex(32))"`).
3. **Static files** (ahorra CPU): URL `/static/` → Directory `/home/TU_USUARIO/practicas-pp/app/static`
4. Botón verde **Reload**.

Entre en `https://TU_USUARIO.pythonanywhere.com`. Si algo falla, revise el **Error log** en la pestaña Web.

## 6. Mantenimiento
- **Extender el vencimiento:** cada mes entre a la pestaña Web y pulse *Run until 1 month from today*. Si se olvida, la aplicación se apaga (los datos no se borran de inmediato).
- **Copias de seguridad:** como no hay tareas programadas, hágalas desde el sistema (**Configuración → Copias de seguridad → Crear copia**) y **descárguelas** a su computadora al menos una vez por semana.
- **Actualizar el sistema:** en la consola Bash: `cd ~/practicas-pp && git pull`, y luego **Reload** en la pestaña Web. Los datos (`~/pp-data`) no se tocan.
- **Espacio:** los 512 MB incluyen código, entorno virtual, base, documentos y copias. Borre copias viejas desde el sistema.
