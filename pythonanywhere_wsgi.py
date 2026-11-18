# Contenido para el archivo WSGI de PythonAnywhere (pestaña Web → "WSGI configuration file").
# Reemplace TU_USUARIO y la clave secreta, y pegue TODO esto en lugar del contenido que trae.
import os
import sys

USUARIO = "TU_USUARIO"
CODIGO = f"/home/{USUARIO}/practicas-pp"
DATOS = f"/home/{USUARIO}/pp-data"          # base de datos, documentos, copias de seguridad

if CODIGO not in sys.path:
    sys.path.insert(0, CODIGO)

os.environ["PP_INSTANCE"] = DATOS
os.environ["PP_HTTPS"] = "1"
os.environ["PP_SECRET_KEY"] = "PEGUE_AQUI_UNA_CLAVE_LARGA_Y_ALEATORIA"

from app import create_app  # noqa: E402

application = create_app()
