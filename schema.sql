-- =====================================================================
-- Sistema de Gestión de Prácticas Profesionalizantes (PP)
-- Esquema relacional normalizado (SQL estándar, probado en SQLite 3).
-- Convenciones:
--   * Todas las tablas "de negocio" tienen campos de auditoría:
--       creado_en, creado_por, modificado_en, modificado_por,
--       eliminado_en, eliminado_por   (eliminado_* = baja lógica)
--   * Las restricciones de unicidad son índices parciales
--     (WHERE eliminado_en IS NULL) para poder reutilizar un DNI/legajo
--     luego de una baja lógica.
--   * Fechas en formato ISO-8601 (TEXT): 'YYYY-MM-DD' y 'YYYY-MM-DD HH:MM:SS'.
-- =====================================================================

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------
-- 1. Seguridad: roles, permisos y usuarios
-- ---------------------------------------------------------------------
CREATE TABLE roles (
    id          INTEGER PRIMARY KEY,
    codigo      TEXT NOT NULL UNIQUE,            -- admin | coordinador | tutor | consulta
    nombre      TEXT NOT NULL,
    descripcion TEXT
);

CREATE TABLE permisos (
    id          INTEGER PRIMARY KEY,
    codigo      TEXT NOT NULL UNIQUE,            -- p.ej. alumnos.editar
    modulo      TEXT NOT NULL,
    descripcion TEXT NOT NULL
);

CREATE TABLE rol_permisos (
    rol_id     INTEGER NOT NULL REFERENCES roles(id)    ON DELETE CASCADE,
    permiso_id INTEGER NOT NULL REFERENCES permisos(id) ON DELETE CASCADE,
    PRIMARY KEY (rol_id, permiso_id)
);

CREATE TABLE usuarios (
    id                 INTEGER PRIMARY KEY,
    username           TEXT NOT NULL,
    nombre             TEXT NOT NULL,
    email              TEXT,
    password_hash      TEXT NOT NULL,
    rol_id             INTEGER NOT NULL REFERENCES roles(id),
    docente_id         INTEGER REFERENCES docentes(id),   -- vínculo del rol "tutor"
    activo             INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0,1)),
    debe_cambiar_clave INTEGER NOT NULL DEFAULT 0 CHECK (debe_cambiar_clave IN (0,1)),
    intentos_fallidos  INTEGER NOT NULL DEFAULT 0,
    bloqueado_hasta    TEXT,
    ultimo_acceso      TEXT,
    creado_en          TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    creado_por         INTEGER REFERENCES usuarios(id),
    modificado_en      TEXT,
    modificado_por     INTEGER REFERENCES usuarios(id),
    eliminado_en       TEXT,
    eliminado_por      INTEGER REFERENCES usuarios(id)
);
CREATE UNIQUE INDEX ux_usuarios_username ON usuarios(lower(username)) WHERE eliminado_en IS NULL;
CREATE INDEX ix_usuarios_rol ON usuarios(rol_id);

-- ---------------------------------------------------------------------
-- 2. Catálogos institucionales
-- ---------------------------------------------------------------------
CREATE TABLE ciclos_lectivos (
    id               INTEGER PRIMARY KEY,
    anio             INTEGER NOT NULL CHECK (anio BETWEEN 2000 AND 2100),
    nombre           TEXT NOT NULL,
    fecha_inicio     TEXT,
    fecha_fin        TEXT,
    horas_pp_default INTEGER NOT NULL DEFAULT 200 CHECK (horas_pp_default > 0),
    activo           INTEGER NOT NULL DEFAULT 0 CHECK (activo IN (0,1)),
    observaciones    TEXT,
    creado_en        TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    creado_por       INTEGER REFERENCES usuarios(id),
    modificado_en    TEXT,
    modificado_por   INTEGER REFERENCES usuarios(id),
    eliminado_en     TEXT,
    eliminado_por    INTEGER REFERENCES usuarios(id)
);
CREATE UNIQUE INDEX ux_ciclos_anio ON ciclos_lectivos(anio) WHERE eliminado_en IS NULL;

CREATE TABLE especialidades (
    id             INTEGER PRIMARY KEY,
    codigo         TEXT,
    nombre         TEXT NOT NULL,
    horas_pp       INTEGER NOT NULL DEFAULT 200 CHECK (horas_pp > 0),
    activo         INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0,1)),
    creado_en      TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    creado_por     INTEGER REFERENCES usuarios(id),
    modificado_en  TEXT,
    modificado_por INTEGER REFERENCES usuarios(id),
    eliminado_en   TEXT,
    eliminado_por  INTEGER REFERENCES usuarios(id)
);
CREATE UNIQUE INDEX ux_especialidades_nombre ON especialidades(lower(nombre)) WHERE eliminado_en IS NULL;

CREATE TABLE cursos (
    id             INTEGER PRIMARY KEY,
    nombre         TEXT NOT NULL,                 -- "6° año"
    orden          INTEGER NOT NULL DEFAULT 0,
    requiere_pp    INTEGER NOT NULL DEFAULT 0 CHECK (requiere_pp IN (0,1)),
    activo         INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0,1)),
    creado_en      TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    creado_por     INTEGER REFERENCES usuarios(id),
    modificado_en  TEXT,
    modificado_por INTEGER REFERENCES usuarios(id),
    eliminado_en   TEXT,
    eliminado_por  INTEGER REFERENCES usuarios(id)
);
CREATE UNIQUE INDEX ux_cursos_nombre ON cursos(lower(nombre)) WHERE eliminado_en IS NULL;

CREATE TABLE divisiones (
    id             INTEGER PRIMARY KEY,
    nombre         TEXT NOT NULL,                 -- "1ª", "2ª", "A"...
    activo         INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0,1)),
    creado_en      TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    creado_por     INTEGER REFERENCES usuarios(id),
    modificado_en  TEXT,
    modificado_por INTEGER REFERENCES usuarios(id),
    eliminado_en   TEXT,
    eliminado_por  INTEGER REFERENCES usuarios(id)
);
CREATE UNIQUE INDEX ux_divisiones_nombre ON divisiones(lower(nombre)) WHERE eliminado_en IS NULL;

CREATE TABLE tipos_documentos (
    id             INTEGER PRIMARY KEY,
    nombre         TEXT NOT NULL,
    ambito         TEXT NOT NULL CHECK (ambito IN ('alumno','empresa','practica')),
    obligatorio    INTEGER NOT NULL DEFAULT 0 CHECK (obligatorio IN (0,1)),
    activo         INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0,1)),
    creado_en      TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    creado_por     INTEGER REFERENCES usuarios(id),
    modificado_en  TEXT,
    modificado_por INTEGER REFERENCES usuarios(id),
    eliminado_en   TEXT,
    eliminado_por  INTEGER REFERENCES usuarios(id)
);
CREATE UNIQUE INDEX ux_tipos_doc_nombre ON tipos_documentos(lower(nombre)) WHERE eliminado_en IS NULL;

-- ---------------------------------------------------------------------
-- 3. Personas y organizaciones
-- ---------------------------------------------------------------------
CREATE TABLE alumnos (
    id                   INTEGER PRIMARY KEY,
    legajo               TEXT NOT NULL,
    apellido             TEXT NOT NULL,
    nombre               TEXT NOT NULL,
    dni                  TEXT NOT NULL,
    fecha_nacimiento     TEXT,
    domicilio            TEXT,
    telefono             TEXT,
    email                TEXT,
    curso_id             INTEGER NOT NULL REFERENCES cursos(id),
    division_id          INTEGER REFERENCES divisiones(id),
    especialidad_id      INTEGER REFERENCES especialidades(id),
    ciclo_id             INTEGER REFERENCES ciclos_lectivos(id),   -- año de cursada
    estado_academico     TEXT NOT NULL DEFAULT 'Regular'
                         CHECK (estado_academico IN ('Regular','Repitente','Libre','Egresado','Baja / Pase')),
    observaciones        TEXT,
    tutor_legal_nombre     TEXT,
    tutor_legal_parentesco TEXT,
    tutor_legal_telefono   TEXT,
    tutor_legal_email      TEXT,
    creado_en            TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    creado_por           INTEGER REFERENCES usuarios(id),
    modificado_en        TEXT,
    modificado_por       INTEGER REFERENCES usuarios(id),
    eliminado_en         TEXT,
    eliminado_por        INTEGER REFERENCES usuarios(id)
);
CREATE UNIQUE INDEX ux_alumnos_legajo ON alumnos(legajo) WHERE eliminado_en IS NULL;
CREATE UNIQUE INDEX ux_alumnos_dni    ON alumnos(dni)    WHERE eliminado_en IS NULL;
CREATE INDEX ix_alumnos_apellido ON alumnos(apellido, nombre);
CREATE INDEX ix_alumnos_curso    ON alumnos(curso_id, division_id);
CREATE INDEX ix_alumnos_esp      ON alumnos(especialidad_id);
CREATE INDEX ix_alumnos_ciclo    ON alumnos(ciclo_id);

CREATE TABLE docentes (
    id              INTEGER PRIMARY KEY,
    apellido        TEXT NOT NULL,
    nombre          TEXT NOT NULL,
    dni             TEXT NOT NULL,
    email           TEXT,
    telefono        TEXT,
    especialidad_id INTEGER REFERENCES especialidades(id),
    cargo           TEXT,
    activo          INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0,1)),
    creado_en       TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    creado_por      INTEGER REFERENCES usuarios(id),
    modificado_en   TEXT,
    modificado_por  INTEGER REFERENCES usuarios(id),
    eliminado_en    TEXT,
    eliminado_por   INTEGER REFERENCES usuarios(id)
);
CREATE UNIQUE INDEX ux_docentes_dni ON docentes(dni) WHERE eliminado_en IS NULL;

-- Asignación de alumnos a docentes tutores (por ciclo lectivo)
CREATE TABLE asignaciones_tutor (
    id          INTEGER PRIMARY KEY,
    docente_id  INTEGER NOT NULL REFERENCES docentes(id),
    alumno_id   INTEGER NOT NULL REFERENCES alumnos(id),
    ciclo_id    INTEGER NOT NULL REFERENCES ciclos_lectivos(id),
    creado_en   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    creado_por  INTEGER REFERENCES usuarios(id),
    UNIQUE (docente_id, alumno_id, ciclo_id)
);
CREATE INDEX ix_asig_alumno ON asignaciones_tutor(alumno_id, ciclo_id);

CREATE TABLE empresas (
    id              INTEGER PRIMARY KEY,
    razon_social    TEXT NOT NULL,
    nombre_fantasia TEXT,
    cuit            TEXT,
    direccion       TEXT,
    localidad       TEXT,
    telefono        TEXT,
    email           TEXT,
    actividad       TEXT,
    vacantes        INTEGER NOT NULL DEFAULT 0 CHECK (vacantes >= 0),
    observaciones   TEXT,
    activo          INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0,1)),
    creado_en       TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    creado_por      INTEGER REFERENCES usuarios(id),
    modificado_en   TEXT,
    modificado_por  INTEGER REFERENCES usuarios(id),
    eliminado_en    TEXT,
    eliminado_por   INTEGER REFERENCES usuarios(id)
);
CREATE UNIQUE INDEX ux_empresas_cuit ON empresas(cuit) WHERE eliminado_en IS NULL AND cuit IS NOT NULL;
CREATE INDEX ix_empresas_razon ON empresas(razon_social);

CREATE TABLE referentes_empresas (
    id             INTEGER PRIMARY KEY,
    empresa_id     INTEGER NOT NULL REFERENCES empresas(id),
    nombre         TEXT NOT NULL,
    cargo          TEXT,
    telefono       TEXT,
    email          TEXT,
    activo         INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0,1)),
    creado_en      TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    creado_por     INTEGER REFERENCES usuarios(id),
    modificado_en  TEXT,
    modificado_por INTEGER REFERENCES usuarios(id),
    eliminado_en   TEXT,
    eliminado_por  INTEGER REFERENCES usuarios(id)
);
CREATE INDEX ix_referentes_empresa ON referentes_empresas(empresa_id);

-- ---------------------------------------------------------------------
-- 4. Prácticas profesionalizantes
-- ---------------------------------------------------------------------
CREATE TABLE practicas (
    id                 INTEGER PRIMARY KEY,
    numero             TEXT NOT NULL,                       -- PP-2026-0001
    ciclo_id           INTEGER NOT NULL REFERENCES ciclos_lectivos(id),
    empresa_id         INTEGER NOT NULL REFERENCES empresas(id),
    especialidad_id    INTEGER REFERENCES especialidades(id),
    tutor_id           INTEGER REFERENCES docentes(id),
    referente_id       INTEGER REFERENCES referentes_empresas(id),
    proyecto           TEXT NOT NULL,
    fecha_inicio       TEXT,
    fecha_fin_estimada TEXT,
    fecha_fin_real     TEXT,
    horas_requeridas   INTEGER NOT NULL DEFAULT 200 CHECK (horas_requeridas > 0),
    dias_horarios      TEXT,
    dias_semana        TEXT NOT NULL DEFAULT '1,2,3,4,5',   -- días de práctica: 1 = lunes ... 7 = domingo
    horas_dia          REAL NOT NULL DEFAULT 4 CHECK (horas_dia > 0 AND horas_dia <= 14),
    objetivos          TEXT,
    actividades        TEXT,
    observaciones      TEXT,
    estado             TEXT NOT NULL DEFAULT 'Pendiente de asignación'
        CHECK (estado IN ('Pendiente de asignación','En proceso de asignación','Documentación pendiente',
                          'Habilitada para comenzar','En curso','Suspendida','Finalizada','Aprobada','Desaprobada')),
    creado_en          TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    creado_por         INTEGER REFERENCES usuarios(id),
    modificado_en      TEXT,
    modificado_por     INTEGER REFERENCES usuarios(id),
    eliminado_en       TEXT,
    eliminado_por      INTEGER REFERENCES usuarios(id),
    CHECK (fecha_fin_estimada IS NULL OR fecha_inicio IS NULL OR fecha_fin_estimada >= fecha_inicio)
);
CREATE UNIQUE INDEX ux_practicas_numero ON practicas(numero);
CREATE INDEX ix_practicas_ciclo   ON practicas(ciclo_id, estado);
CREATE INDEX ix_practicas_empresa ON practicas(empresa_id);
CREATE INDEX ix_practicas_tutor   ON practicas(tutor_id);

-- Participación de cada alumno en una práctica (relación N:M con datos propios)
CREATE TABLE alumnos_practicas (
    id                  INTEGER PRIMARY KEY,
    practica_id         INTEGER NOT NULL REFERENCES practicas(id),
    alumno_id           INTEGER NOT NULL REFERENCES alumnos(id),
    horas_requeridas    INTEGER NOT NULL CHECK (horas_requeridas > 0),
    estado              TEXT NOT NULL DEFAULT 'Pendiente de asignación'
        CHECK (estado IN ('Pendiente de asignación','En proceso de asignación','Documentación pendiente',
                          'Habilitada para comenzar','En curso','Suspendida','Finalizada','Aprobada','Desaprobada')),
    fecha_inicio        TEXT,
    fecha_fin_real      TEXT,
    calificacion_final  REAL CHECK (calificacion_final IS NULL OR calificacion_final BETWEEN 1 AND 10),
    resultado           TEXT CHECK (resultado IS NULL OR resultado IN ('Aprobado','Desaprobado')),
    observaciones_finales TEXT,
    fecha_resultado     TEXT,
    creado_en           TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    creado_por          INTEGER REFERENCES usuarios(id),
    modificado_en       TEXT,
    modificado_por      INTEGER REFERENCES usuarios(id),
    eliminado_en        TEXT,
    eliminado_por       INTEGER REFERENCES usuarios(id)
);
CREATE UNIQUE INDEX ux_ap_practica_alumno ON alumnos_practicas(practica_id, alumno_id) WHERE eliminado_en IS NULL;
CREATE INDEX ix_ap_alumno ON alumnos_practicas(alumno_id);

-- Inasistencias: las horas corren solas según el calendario de la práctica y cada registro descuenta `horas`.
-- ('Presente' se conserva sólo para registros históricos anteriores al cálculo automático.)
CREATE TABLE asistencias (
    id                  INTEGER PRIMARY KEY,
    alumno_practica_id  INTEGER NOT NULL REFERENCES alumnos_practicas(id),
    fecha               TEXT NOT NULL,
    tipo                TEXT NOT NULL DEFAULT 'Presente'
        CHECK (tipo IN ('Presente','Ausente justificado','Ausente injustificado')),
    hora_entrada        TEXT,
    hora_salida         TEXT,
    horas               REAL NOT NULL DEFAULT 0 CHECK (horas >= 0 AND horas <= 24),
    observaciones       TEXT,
    creado_en           TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    creado_por          INTEGER REFERENCES usuarios(id),
    modificado_en       TEXT,
    modificado_por      INTEGER REFERENCES usuarios(id),
    eliminado_en        TEXT,
    eliminado_por       INTEGER REFERENCES usuarios(id)
);
CREATE UNIQUE INDEX ux_asistencia_dia ON asistencias(alumno_practica_id, fecha) WHERE eliminado_en IS NULL;

-- Días sin actividad: feriados y recesos (generales), cierres de una práctica y suspensiones de un alumno.
-- En esas fechas no corren las horas automáticas. fecha_hasta NULL = período abierto (suspensión vigente).
CREATE TABLE dias_sin_actividad (
    id                  INTEGER PRIMARY KEY,
    fecha_desde         TEXT NOT NULL,
    fecha_hasta         TEXT,
    motivo              TEXT NOT NULL,
    practica_id         INTEGER REFERENCES practicas(id),
    alumno_practica_id  INTEGER REFERENCES alumnos_practicas(id),
    creado_en           TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    creado_por          INTEGER REFERENCES usuarios(id),
    modificado_en       TEXT,
    modificado_por      INTEGER REFERENCES usuarios(id),
    eliminado_en        TEXT,
    eliminado_por       INTEGER REFERENCES usuarios(id),
    CHECK (fecha_hasta IS NULL OR fecha_hasta >= fecha_desde)
);
CREATE INDEX ix_dsa_fechas ON dias_sin_actividad(fecha_desde, fecha_hasta);

-- Visitas, llamados, reuniones e informes de seguimiento
CREATE TABLE seguimiento (
    id                  INTEGER PRIMARY KEY,
    practica_id         INTEGER NOT NULL REFERENCES practicas(id),
    alumno_practica_id  INTEGER REFERENCES alumnos_practicas(id),
    fecha               TEXT NOT NULL,
    tipo                TEXT NOT NULL DEFAULT 'Visita'
        CHECK (tipo IN ('Visita','Llamado','Reunión','Informe','Otro')),
    docente_id          INTEGER REFERENCES docentes(id),
    referente_id        INTEGER REFERENCES referentes_empresas(id),   -- referente de la empresa que participó
    descripcion         TEXT NOT NULL,
    creado_en           TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    creado_por          INTEGER REFERENCES usuarios(id),
    modificado_en       TEXT,
    modificado_por      INTEGER REFERENCES usuarios(id),
    eliminado_en        TEXT,
    eliminado_por       INTEGER REFERENCES usuarios(id)
);
CREATE INDEX ix_seguimiento_practica ON seguimiento(practica_id, fecha);

-- ---------------------------------------------------------------------
-- 5. Documentación y evaluación
-- ---------------------------------------------------------------------
CREATE TABLE documentos (
    id                INTEGER PRIMARY KEY,
    tipo_documento_id INTEGER NOT NULL REFERENCES tipos_documentos(id),
    alumno_id         INTEGER REFERENCES alumnos(id),
    empresa_id        INTEGER REFERENCES empresas(id),
    practica_id       INTEGER REFERENCES practicas(id),
    nombre_original   TEXT NOT NULL,
    archivo           TEXT NOT NULL UNIQUE,        -- nombre en disco (uuid)
    mime              TEXT,
    tamano            INTEGER NOT NULL DEFAULT 0,
    estado            TEXT NOT NULL DEFAULT 'Pendiente de revisión'
        CHECK (estado IN ('Pendiente de revisión','Aprobado','Observado')),
    vencimiento       TEXT,
    observaciones     TEXT,
    creado_en         TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    creado_por        INTEGER REFERENCES usuarios(id),
    modificado_en     TEXT,
    modificado_por    INTEGER REFERENCES usuarios(id),
    eliminado_en      TEXT,
    eliminado_por     INTEGER REFERENCES usuarios(id),
    CHECK (alumno_id IS NOT NULL OR empresa_id IS NOT NULL OR practica_id IS NOT NULL)
);
CREATE INDEX ix_doc_alumno   ON documentos(alumno_id);
CREATE INDEX ix_doc_empresa  ON documentos(empresa_id);
CREATE INDEX ix_doc_practica ON documentos(practica_id);

CREATE TABLE evaluaciones (
    id                    INTEGER PRIMARY KEY,
    alumno_practica_id    INTEGER NOT NULL REFERENCES alumnos_practicas(id),
    evaluador             TEXT NOT NULL CHECK (evaluador IN ('Tutor','Referente')),
    evaluador_nombre      TEXT,
    fecha                 TEXT NOT NULL,
    desempeno_tecnico     INTEGER NOT NULL CHECK (desempeno_tecnico     BETWEEN 1 AND 10),
    responsabilidad       INTEGER NOT NULL CHECK (responsabilidad       BETWEEN 1 AND 10),
    puntualidad           INTEGER NOT NULL CHECK (puntualidad           BETWEEN 1 AND 10),
    trabajo_equipo        INTEGER NOT NULL CHECK (trabajo_equipo        BETWEEN 1 AND 10),
    resolucion_problemas  INTEGER NOT NULL CHECK (resolucion_problemas  BETWEEN 1 AND 10),
    aplicacion_conocimientos INTEGER NOT NULL CHECK (aplicacion_conocimientos BETWEEN 1 AND 10),
    promedio              REAL NOT NULL,
    observaciones         TEXT,
    creado_en             TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    creado_por            INTEGER REFERENCES usuarios(id),
    modificado_en         TEXT,
    modificado_por        INTEGER REFERENCES usuarios(id),
    eliminado_en          TEXT,
    eliminado_por         INTEGER REFERENCES usuarios(id)
);
CREATE UNIQUE INDEX ux_eval_ap_evaluador ON evaluaciones(alumno_practica_id, evaluador) WHERE eliminado_en IS NULL;

-- ---------------------------------------------------------------------
-- 6. Trazabilidad y configuración
-- ---------------------------------------------------------------------
CREATE TABLE historial_cambios (
    id             INTEGER PRIMARY KEY,
    fecha          TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    usuario_id     INTEGER REFERENCES usuarios(id),
    usuario_nombre TEXT,
    tabla          TEXT NOT NULL,
    registro_id    INTEGER,
    accion         TEXT NOT NULL,        -- crear | modificar | baja | restaurar | login | importar | backup ...
    detalle        TEXT,                 -- JSON con valores / diferencias
    ip             TEXT
);
CREATE INDEX ix_hist_registro ON historial_cambios(tabla, registro_id);
CREATE INDEX ix_hist_fecha    ON historial_cambios(fecha);
CREATE INDEX ix_hist_usuario  ON historial_cambios(usuario_id);

CREATE TABLE configuracion (
    clave TEXT PRIMARY KEY,
    valor TEXT
);
