"""
Optimized prompts for Invenzis Intelligence Brain.

These prompts are designed to be strict and focused:
- SQL generation prompts produce ONLY SQL, no explanations
- Synthesis prompts produce user-friendly responses
- Classification prompts determine query intent
"""

# =============================================================================
# DATABASE SCHEMA (for SQL generation context)
# =============================================================================

DATABASE_SCHEMA = """
-- CONSULTORES: Empleados de Invenzis
consultores (
    consultorid INTEGER PRIMARY KEY,
    nombrecompleto VARCHAR NOT NULL,    -- "Juan Pérez García"
    email VARCHAR UNIQUE NOT NULL,      -- "jperez@invenzis.com"
    rolprincipal VARCHAR,               -- "Consultor SAP FI", "Project Manager"
    nivelsenioridad VARCHAR,            -- 'Junior', 'Semi-Senior', 'Senior', 'Lead', 'Architect'
    expertise JSONB,                    -- ["SAP FI", "SAP CO", "S/4HANA"] - BUSCAR CON: expertise::text ILIKE '%valor%'
    certificaciones JSONB,              -- Certificaciones SAP
    aniosexperiencia NUMERIC,
    disponibilidad VARCHAR,             -- 'Disponible', 'Asignado Parcial', 'Asignado Completo'
    activo BOOLEAN,
    ubicacion VARCHAR                   -- País/Ciudad del consultor
)

-- CLIENTES: Empresas cliente de Invenzis
clientes (
    clienteid INTEGER PRIMARY KEY,
    nombrecliente VARCHAR NOT NULL,     -- "Walmart Uruguay"
    industria VARCHAR NOT NULL,         -- "Retail", "Agricultura", "Finanzas", "Librería"
    ubicacion VARCHAR,
    pais VARCHAR,
    activo BOOLEAN
)

-- PROYECTOS: Proyectos de implementación/consultoría
proyectos (
    proyectoid INTEGER PRIMARY KEY,
    codigoproyecto VARCHAR UNIQUE,      -- "PRY-2024-001"
    clienteid INTEGER REFERENCES clientes(clienteid),
    nombreproyecto VARCHAR NOT NULL,
    tiposervicio VARCHAR,               -- 'Implementación', 'Upgrade', 'Soporte', 'Migración'
    estado VARCHAR,                     -- 'Planificación', 'En Ejecución', 'Completado', 'Cancelado'
    prioridad VARCHAR,                  -- 'Alta', 'Media', 'Baja', 'Crítica'
    problemaejecutivo TEXT,             -- Descripción del problema del cliente
    solucionpropuesta TEXT,             -- Solución implementada
    fechainicio DATE,
    fechafinestimada DATE
)

-- PROYECTOEQUIPO: Asignaciones de consultores a proyectos (TABLA CLAVE PARA JOINs)
proyectoequipo (
    proyectoequipoid INTEGER PRIMARY KEY,
    proyectoid INTEGER REFERENCES proyectos(proyectoid),
    consultorid INTEGER REFERENCES consultores(consultorid),
    rolenproyecto VARCHAR NOT NULL,     -- "Líder Técnico", "Consultor Funcional"
    tipoasignacion VARCHAR,             -- 'Full-Time', 'Part-Time', 'Por Demanda'
    fechaasignacion DATE,
    fechadesasignacion DATE,            -- NULL si sigue activo en el proyecto
    activo BOOLEAN                      -- true = asignación vigente
)

-- TAREAS: Tareas/tickets de trabajo
tareas (
    tareaid INTEGER PRIMARY KEY,
    tareadescripcion TEXT,
    proyectoid INTEGER REFERENCES proyectos(proyectoid),
    usuarioasignadopersonid INTEGER REFERENCES consultores(consultorid),
    reportadortareapersonaid INTEGER REFERENCES consultores(consultorid),
    tareaprioridad VARCHAR,
    tareaestatus VARCHAR
)

-- OFICINAS: Oficinas regionales
oficinas (
    oficinaid VARCHAR PRIMARY KEY,      -- "UY", "AR", "MX"
    oficinadescripcion VARCHAR          -- "Uruguay", "Argentina"
)

RELACIONES CLAVE:
- consultores ←→ proyectoequipo ←→ proyectos ←→ clientes
- Para saber en qué proyectos trabaja alguien: JOIN proyectoequipo
- Para saber quién trabaja en un proyecto: JOIN consultores via proyectoequipo
"""

# =============================================================================
# SQL GENERATION PROMPT (CRITICAL - MUST PRODUCE ONLY SQL)
# =============================================================================

SQL_GENERATION_PROMPT = """Genera ÚNICAMENTE código SQL válido para PostgreSQL.

REGLAS ABSOLUTAS:
1. Responde SOLO con el código SQL
2. NO incluyas explicaciones, comentarios ni texto adicional
3. NO uses markdown ni bloques de código
4. La respuesta debe empezar DIRECTAMENTE con SELECT o WITH
5. Usa ILIKE para búsquedas de texto (case-insensitive)
6. Para campos JSONB como expertise, usa: expertise::text ILIKE '%valor%'

ESQUEMA DE BASE DE DATOS:
{schema}

EJEMPLOS DE QUERIES CORRECTAS:

-- Buscar consultor por nombre (SIEMPRE incluir estos campos)
SELECT consultorid, nombrecompleto, email, rolprincipal, ubicacion 
FROM consultores 
WHERE LOWER(nombrecompleto) ILIKE '%constanza%' AND activo = true;

-- Proyectos de un consultor (JOIN con proyectoequipo)
SELECT p.nombreproyecto, pe.rolenproyecto, c.nombrecliente, p.estado
FROM consultores co
JOIN proyectoequipo pe ON co.consultorid = pe.consultorid
JOIN proyectos p ON pe.proyectoid = p.proyectoid
LEFT JOIN clientes c ON p.clienteid = c.clienteid
WHERE LOWER(co.nombrecompleto) ILIKE '%martin%' AND pe.activo = true;

-- Equipo de un proyecto
SELECT co.nombrecompleto, co.email, pe.rolenproyecto
FROM proyectos p
JOIN proyectoequipo pe ON p.proyectoid = pe.proyectoid
JOIN consultores co ON pe.consultorid = co.consultorid
WHERE LOWER(p.nombreproyecto) ILIKE '%proyecto%' AND pe.activo = true;

-- Clientes por industria
SELECT clienteid, nombrecliente, industria, pais 
FROM clientes 
WHERE LOWER(industria) ILIKE '%libreria%' AND activo = true;

-- Consultores expertos en una tecnología (campo JSONB)
SELECT consultorid, nombrecompleto, email, rolprincipal, expertise 
FROM consultores 
WHERE expertise::text ILIKE '%sap fi%' AND activo = true;

-- Búsqueda con variaciones de nombre (para typos)
SELECT consultorid, nombrecompleto, email, rolprincipal, ubicacion 
FROM consultores 
WHERE (LOWER(nombrecompleto) ILIKE '%morales%' OR LOWER(nombrecompleto) ILIKE '%moales%') 
AND activo = true;

PREGUNTA DEL USUARIO: {question}
"""

# =============================================================================
# SQL ERROR RETRY PROMPT
# =============================================================================

SQL_RETRY_PROMPT = """La consulta SQL anterior falló. Genera una nueva consulta corregida.

ERROR ANTERIOR: {error}

SQL ANTERIOR QUE FALLÓ:
{failed_sql}

ESQUEMA DE BASE DE DATOS:
{schema}

REGLAS:
1. Responde SOLO con SQL corregido
2. NO incluyas explicaciones
3. Corrige el error específico mencionado
4. Si una columna no existe, omítela
5. Si una tabla no existe, usa una alternativa del schema

PREGUNTA ORIGINAL: {question}
"""

# =============================================================================
# SYNTHESIS PROMPT (for final user response)
# =============================================================================

SYNTHESIS_PROMPT = """Eres el asistente de conocimiento de Invenzis, una consultora SAP.

DATOS ENCONTRADOS:
{query_results}

REGLAS DE RESPUESTA:
1. Responde en español, de forma concisa y profesional
2. Si hay datos, preséntalos de forma clara y estructurada
3. Usa **negritas** para resaltar nombres importantes
4. Si NO hay datos, di honestamente: "No encontré información sobre [tema] en los registros."
5. NUNCA inventes datos que no estén en los resultados
6. Para consultores, siempre menciona: nombre, email, rol y ubicación
7. No menciones detalles técnicos sobre cómo buscaste la información
8. Si hay múltiples resultados, preséntalos como lista con viñetas

PREGUNTA DEL USUARIO: {question}

RESPUESTA:"""

# =============================================================================
# QUERY CLASSIFICATION PROMPT
# =============================================================================

CLASSIFICATION_PROMPT = """Clasifica la siguiente pregunta en una de estas categorías:

CATEGORÍAS:
- CONSULTANT_SEARCH: Preguntas sobre personas/consultores (quién es, quién sabe de, experto en)
- PROJECT_SEARCH: Preguntas sobre proyectos o asignaciones (en qué trabaja, proyectos de)
- CLIENT_SEARCH: Preguntas sobre clientes o empresas (clientes de, industria)
- KNOWLEDGE_SEARCH: Preguntas sobre lecciones aprendidas, problemas/soluciones pasadas
- HYBRID: Requiere combinar información de múltiples fuentes

Responde SOLO con la categoría, nada más.

PREGUNTA: {question}
CATEGORÍA:"""

# =============================================================================
# CONDENSE QUESTION PROMPT (for chat memory)
# =============================================================================

CONDENSE_QUESTION_PROMPT = """Reformula la pregunta de seguimiento para que sea independiente del historial.

REGLAS:
1. Si el usuario menciona un nombre parcial, busca en el historial el nombre completo
2. Si dice "él/ella/esa persona", reemplaza con el nombre mencionado antes
3. Si dice "ese proyecto", reemplaza con el nombre del proyecto del historial
4. Mantén la intención original de la pregunta

HISTORIAL:
{chat_history}

PREGUNTA DE SEGUIMIENTO: {question}

PREGUNTA REFORMULADA:"""

# =============================================================================
# NO RESULTS RESPONSES
# =============================================================================

NO_RESULTS_CONSULTANT = """No encontré información sobre "{name}" en los registros de consultores. 

Esto puede deberse a:
- El nombre está escrito diferente en el sistema
- La persona no está registrada como consultor activo

¿Podrías verificar el nombre completo o darme más detalles?"""

NO_RESULTS_PROJECT = """No encontré proyectos relacionados con "{query}".

¿Podrías especificar:
- El nombre del consultor completo
- O el nombre del proyecto/cliente?"""

NO_RESULTS_GENERIC = """No encontré información sobre eso en los registros de Invenzis. ¿Podrías reformular tu pregunta o darme más detalles?"""
