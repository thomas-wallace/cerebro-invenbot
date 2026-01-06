import os
from dotenv import load_dotenv
from llama_index.embeddings.openai import OpenAIEmbedding
from database import get_supabase_client

load_dotenv()

# Configuración: Define qué tablas leer y qué columnas usar para el texto
TABLES_CONFIG = [
    {
        "table_name": "proyectos",
        "id_col": "proyectoid",
        "vector_flag": "necesitavectorizacion",
        "template": "Proyecto: {nombreproyecto}\nProblema: {problemaejecutivo}\nSolución: {solucionpropuesta}",
        "metadata_cols": ["estado", "prioridad", "tiposervicio"]
    },
    {
        "table_name": "consultores",
        "id_col": "consultorid",
        "vector_flag": "activo", # Usamos 'activo' como filtro ya que no tiene flag de vectorización aún
        "template": "Consultor: {nombrecompleto}\nExpertise: {expertise}\nRol: {rolprincipal}\nSeniority: {nivelsenioridad}",
        "metadata_cols": ["nombrecompleto", "rolprincipal"]
    },
    {
        "table_name": "leccionesaprendidas",
        "id_col": "leccionid",
        "vector_flag": "necesitavectorizacion",
        "template": "Lección: {tituloleccion}\nDesafío: {desafio}\nSolución: {solucion}",
        "metadata_cols": ["categoria", "impacto"]
    }
]

async def process_generic(supabase, config, embed_model):
    print(f"--- Processing {config['table_name']} ---")
    
    # 1. Query Genérica
    try:
        response = supabase.table(config['table_name']).select("*").eq(config['vector_flag'], True).execute()
    except:
        return # Si falla (ej. no existe la tabla)
        
    rows = response.data
    if not rows:
        return

    for row in rows:
        # 2. Rellenar la plantilla dinámicamente
        # Convertimos keys a minúsculas para evitar errores
        row_lower = {k.lower(): v for k, v in row.items()}
        
        try:
            # .format(**row_lower) rellena los {corchetes} con los datos
            text_chunk = config['template'].format(**row_lower)
            
            # 3. Embedding
            embedding = await embed_model.aget_text_embedding(text_chunk)
            
            # 4. Metadatos dinámicos
            metadatos = {col: row_lower.get(col) for col in config['metadata_cols']}
            
            # 5. Insertar
            rag_data = {
                "fuentetabla": config['table_name'],
                "fuenteid": row_lower.get(config['id_col']),
                "textochunk": text_chunk.strip(),
                "embeddingvector": embedding,
                "metadatos": metadatos
            }
            supabase.table("rag_chunks").insert(rag_data).execute()
            
            # Update flag (skip for consultores as they don't have the flag yet)
            if config['table_name'] != "consultores":
                supabase.table(config['table_name']).update({config['vector_flag']: False}).eq(config['id_col'], row_lower.get(config['id_col'])).execute()
            
            print(f"Processed {config['table_name']} ID: {row_lower.get(config['id_col'])}")
            
        except KeyError as e:
            print(f"Error: La columna {e} no existe en la tabla {config['table_name']}")

async def main():
    supabase = get_supabase_client()
    embed_model = OpenAIEmbedding(model="text-embedding-3-small", dimensions=1536)
    
    # Bucle mágico: Procesa todas las tablas de la config
    for config in TABLES_CONFIG:
        await process_generic(supabase, config, embed_model)
        
    print("Ingestion complete.")

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())