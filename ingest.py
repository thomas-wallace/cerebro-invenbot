import os
import asyncio
from typing import List, Dict, Any
from dotenv import load_dotenv
from llama_index.embeddings.openai import OpenAIEmbedding
from database import get_supabase_client

load_dotenv()

# Initialize Embedding Model
embed_model = OpenAIEmbedding(model="text-embedding-3-small", dimensions=1536)

async def process_proyectos(supabase):
    print("--- Processing Proyectos ---")
    # Postgres creates tables/columns in lowercase unless quoted. 
    # Supabase client usually expects the exact name in DB.
    response = supabase.table("proyectos").select("*").eq("necesitavectorizacion", True).execute()
    proyectos = response.data
    
    if not proyectos:
        print("No Proyectos to process.")
        return

    for proj in proyectos:
        # Use .get() with lowercase keys if the dict returned by supabase is lowercase
        # It usually returns what is in the DB.
        nombre_proyecto = proj.get('nombreproyecto') or proj.get('NombreProyecto')
        proyecto_id = proj.get('proyectoid') or proj.get('ProyectoID')
        
        print(f"Processing Project: {nombre_proyecto}")
        
        # Create Rich Text Chunk
        text_chunk = f"""
        Proyecto: {nombre_proyecto}
        Cliente: {proj.get('clienteid') or proj.get('ClienteID')} (ID)
        Problema: {proj.get('problemaejecutivo') or proj.get('ProblemaEjecutivo') or ''}
        Solución: {proj.get('solucionpropuesta') or proj.get('SolucionPropuesta') or ''}
        Objetivos: {proj.get('objetivosproyecto') or proj.get('ObjetivosProyecto') or ''}
        """
        
        # Generate Embedding
        embedding = await embed_model.aget_text_embedding(text_chunk)
        
        # Insert into RAG_Chunks (assuming lowercase table/cols too)
        rag_data = {
            "fuentetabla": "Proyectos",
            "fuenteid": proyecto_id,
            "proyectoid_ref": proyecto_id,
            "textochunk": text_chunk.strip(),
            "embeddingvector": embedding,
            "metadatos": {
                "tiposervicio": proj.get("tiposervicio") or proj.get("TipoServicio"),
                "estado": proj.get("estado") or proj.get("Estado"),
                "prioridad": proj.get("prioridad") or proj.get("Prioridad")
            }
        }
        
        try:
            supabase.table("rag_chunks").insert(rag_data).execute()
            # Update Source Flag
            supabase.table("proyectos").update({"necesitavectorizacion": False}).eq("proyectoid", proyecto_id).execute()
            print(f"Successfully processed Project ID: {proyecto_id}")
        except Exception as e:
            print(f"Error processing Project ID {proyecto_id}: {e}")

async def process_lecciones(supabase):
    print("--- Processing Lecciones Aprendidas ---")
    response = supabase.table("leccionesaprendidas").select("*").eq("necesitavectorizacion", True).execute()
    lecciones = response.data
    
    if not lecciones:
        print("No Lecciones to process.")
        return

    for leccion in lecciones:
        titulo = leccion.get('tituloleccion') or leccion.get('TituloLeccion')
        leccion_id = leccion.get('leccionid') or leccion.get('LeccionID')
        proyecto_id = leccion.get('proyectoid') or leccion.get('ProyectoID')

        print(f"Processing Leccion: {titulo}")
        
        # Create Rich Text Chunk
        text_chunk = f"""
        Lección: {titulo}
        Contexto: {leccion.get('contexto') or leccion.get('Contexto') or ''}
        Desafío: {leccion.get('desafio') or leccion.get('Desafio') or ''}
        Solución: {leccion.get('solucion') or leccion.get('Solucion') or ''}
        Mejor Práctica: {leccion.get('mejorpractica') or leccion.get('MejorPractica') or ''}
        """
        
        # Generate Embedding
        embedding = await embed_model.aget_text_embedding(text_chunk)
        
        # Insert into RAG_Chunks
        rag_data = {
            "fuentetabla": "LeccionesAprendidas",
            "fuenteid": leccion_id,
            "proyectoid_ref": proyecto_id,
            "textochunk": text_chunk.strip(),
            "embeddingvector": embedding,
            "metadatos": {
                "categoria": leccion.get("categoria") or leccion.get("Categoria"),
                "impacto": leccion.get("impacto") or leccion.get("Impacto"),
                "replicable": leccion.get("replicable") or leccion.get("Replicable")
            }
        }
        
        try:
            supabase.table("rag_chunks").insert(rag_data).execute()
            # Update Source Flag
            supabase.table("leccionesaprendidas").update({"necesitavectorizacion": False}).eq("leccionid", leccion_id).execute()
            print(f"Successfully processed Leccion ID: {leccion_id}")
        except Exception as e:
            print(f"Error processing Leccion ID {leccion_id}: {e}")

async def main():
    supabase = get_supabase_client()
    await process_proyectos(supabase)
    await process_lecciones(supabase)
    print("Ingestion complete.")

if __name__ == "__main__":
    asyncio.run(main())
