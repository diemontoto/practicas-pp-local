# Manual de usuario

## 1. Ingreso y cuenta
Ingrese con su usuario y contraseña. Tras 5 intentos fallidos la cuenta se bloquea 15 minutos. La sesión se cierra tras 2 horas sin actividad. Cambie su contraseña desde el ícono del escudo (arriba a la derecha): mínimo 8 caracteres con letras y números.

**Consulta sin usuario.** En la pantalla de ingreso, el botón «Consultar seguimiento sin usuario» abre (dirección `/consulta/`) una vista pública de *Seguimiento y asistencia*: alumno, curso, práctica, empresa, tutor, fecha de inicio y fin, estado y avance de horas. Es de sólo lectura: no permite modificar nada, no muestra DNI, legajo ni datos de contacto, no tiene enlaces a las fichas y no da acceso a ninguna otra pantalla. Se habilita o deshabilita desde *Configuración → Datos institucionales y parámetros*.

## 2. Perfiles y permisos

| Módulo | Administrador | Coordinador de PP | Docente tutor | Consulta institucional |
|---|---|---|---|---|
| Alumnos | todo | ver, crear, editar | ver **sólo sus alumnos** | ver |
| Empresas | todo | ver, crear, editar | ver | ver |
| Docentes y tutores | todo | ver, crear, editar | — | ver |
| Prácticas | todo | ver, crear, editar | ver **sólo las suyas** | ver |
| Seguimiento y asistencia | todo | todo | registrar y ver (suyas) | ver |
| Documentación | todo | todo | ver | ver |
| Evaluaciones | todo | ver, cargar | cargar (suyos) | ver |
| Informes | todo | ver y descargar | ver | ver |
| Catálogos (cursos, divisiones, especialidades, tipos de documento) | todo | ver, editar | — | — |
| Usuarios, ciclos, configuración, respaldos, historial | **sólo administrador** | — | — | — |

La matriz puede ajustarse en **Configuración → Roles y permisos**. Los botones que no corresponden a su perfil no se muestran, y el servidor además rechaza cualquier intento de acceso directo.

## 3. Menú lateral

**Inicio / Dashboard.** Indicadores (alumnos, prácticas por estado, empresas, vacantes, documentación incompleta, horas pendientes), gráficos y alertas. Filtros por año lectivo, especialidad, curso y división. La campana muestra la cantidad de alertas.

**Alumnos.** Listado con búsqueda (sin distinguir acentos) y filtros. *Nuevo alumno* abre la ficha; los menores de 18 años requieren datos del responsable. Desde la ficha: historial de prácticas, horas, documentos, evaluaciones y **Ficha PDF**. *Importar Excel* acepta un archivo con la plantilla descargable: si el DNI ya existe se actualiza el alumno; la opción **Sólo validar** muestra los errores sin guardar nada.

**Empresas.** Datos, vacantes, actividad y **referentes** (contactos). La ficha muestra vacantes ocupadas/disponibles y el historial de prácticas y alumnos recibidos. La razón social y el CUIT (se valida el dígito verificador) no pueden repetirse.

**Prácticas Profesionalizantes.**
1. *Nueva práctica*: ciclo, empresa, tutor, referente, proyecto, fechas, horas, días y horarios. El número `PP-AAAA-NNNN` se asigna solo.
2. En la ficha, **Agregar alumnos** (uno o varios; se avisa si se superan las vacantes).
3. **Cambiar estado**: para *Habilitada* o *En curso* debe estar completa y aprobada la documentación obligatoria; el coordinador puede marcar **Forzar** y queda registrado. *Aprobada/Desaprobada* sólo se fijan en Evaluaciones.
4. **Reutilizar en otro ciclo** copia la práctica sin alumnos.

**Seguimiento y asistencia.** Lista de alumnos con avance. **La asistencia es automática**: desde la fecha de inicio de la asignación hasta la fecha de finalización, cada día de práctica suma la jornada (los *días de práctica* y las *horas por día* se definen en la práctica). **Sólo se cargan las inasistencias**, que descuentan horas: horas cumplidas = horas programadas a la fecha − horas de inasistencias. *Inasistencias del día*: pantalla en lote por práctica; todos figuran presentes y se marca a quien faltó (justificado/injustificado), con la jornada completa o sólo una parte. Volver a «Presente» quita la inasistencia. En el detalle del alumno: barra de progreso, horas programadas, descontadas y pendientes, inasistencias y **visitas / contactos** con la empresa. Cada visita u observación (también desde la ficha de la práctica, en «Visitas y observaciones recientes») puede indicar el **referente de la empresa** que participó: se elige entre los referentes activos de la empresa de esa práctica.

**Cuándo corren las horas.** Sólo con la asignación «En curso» (o ya finalizada), en los días de práctica, hasta hoy y nunca después de la fecha de finalización. No corren: antes del inicio, en los **feriados y recesos** (Configuración → *Feriados y recesos*; pueden ser generales o de una sola práctica, de un día o un período) ni mientras el alumno está «Suspendida» (al reanudar vuelven a correr desde ese día). No se pueden cargar inasistencias en días que no son de práctica.

**Documentación.** Repositorio con filtros. *Subir documento*: elija el tipo (el tipo define si corresponde a un alumno, empresa o práctica), el vínculo y el archivo (PDF, imágenes, Word, Excel; hasta 15 MB). Cada documento se **aprueba u observa**; puede tener vencimiento. El **Control de documentación** muestra una matriz alumno × tipo con el indicador *Completa / Incompleta / Pendiente de revisión*.

**Docentes y tutores.** Ficha del docente y **asignación de alumnos** por ciclo. Un usuario «Docente tutor» sólo ve los alumnos asignados a su ficha.

**Evaluaciones.** Por cada alumno: evaluación del **tutor** y del **referente** de la empresa (seis criterios de 1 a 10: desempeño técnico, responsabilidad, puntualidad, trabajo en equipo, resolución de problemas, aplicación de conocimientos). Con ambas cargadas se sugiere la nota y se cierra con **Resultado final** (Aprobado/Desaprobado), nota y observaciones. Se pueden reabrir. **Informe PDF** individual de evaluación.

**Informes y estadísticas.** 11 informes (alumnos, por especialidad, asignados a empresas, prácticas en curso, finalizadas, horas por alumno, inasistencias, documentación pendiente, evaluaciones, empresas, estadísticas anuales). Se ven en pantalla y se descargan en **PDF o Excel** con encabezado de la escuela, fecha de emisión y filtros aplicados.

**Usuarios** y **Configuración** (administrador): usuarios y perfiles, datos de la escuela, parámetros (nota mínima, días de aviso de vencimiento, umbral de ritmo), ciclos, catálogos, roles, **copias de seguridad** e **historial** de operaciones.

## 4. Alertas
- *Documentación pendiente*: alumnos con práctica asignada y documentos obligatorios sin aprobar.
- *Prácticas por vencer / vencidas*: fecha de fin estimada dentro de los días configurados o ya superada.
- *Horas insuficientes*: el avance de horas está por detrás del avance del plazo más que el umbral configurado.
- *Listas para finalizar*: horas completas y pendientes de cierre.

## 5. Flujo recomendado de un ciclo
1. Administrador: crear/activar el ciclo, importar alumnos, cargar empresas y referentes, docentes y usuarios.
2. Coordinador: crear prácticas, agregar alumnos, asignar tutores, subir y revisar documentación.
3. Cuando la documentación está completa: pasar a *Habilitada* y luego *En curso*.
4. Tutor: registrar inasistencias y visitas durante la práctica (la asistencia corre sola).
5. Al finalizar: evaluaciones del tutor y del referente → resultado final → informes.

## 6. Preguntas frecuentes
- **Me equivoqué y di de baja un registro.** Las bajas son lógicas: en el listado marque *Ver dados de baja* y use **Restaurar**.
- **No puedo dar de baja una empresa/alumno.** Tiene registros dependientes (prácticas, asistencias); primero deben darse de baja o finalizar.
- **El tutor no ve a un alumno.** Verifique que esté asignado en la ficha del docente (o que la práctica tenga a ese docente como tutor) y que el usuario esté vinculado al docente.
- **Olvidé mi contraseña.** Un administrador edita su usuario y define una nueva (puede obligar a cambiarla).
