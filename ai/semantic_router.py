import csv
import json
import logging
import os
import threading
from typing import Dict, List, Optional, Tuple

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)


class FastSemanticRouter:

    
    def __init__(
        self, 
        csv_path: str = "datasets/query_classification.csv",
        model_name: str = "all-MiniLM-L6-v2",
        similarity_threshold: float = 0.85,
        fallback_tool: str = "general_query"
    ):

        self.csv_path = csv_path
        self.similarity_threshold = similarity_threshold
        self.fallback_tool = fallback_tool
        
        self._write_lock = threading.Lock()
        
        self.csv_to_enum_mapping = {
            "workout_plan_adjuster": "workout_adjuster_tool",
            "database_persistence": "database_persistence_tool",
        }
        
        logger.info(f"[FastSemanticRouter] Initializing with model '{model_name}'...")
        
        try:
            self.encoder = SentenceTransformer(model_name)
            self.embedding_dim = self.encoder.get_sentence_embedding_dimension()
            logger.info(f"[FastSemanticRouter] Loaded encoder with dimension {self.embedding_dim}")
        except Exception as e:
            logger.error(f"[FastSemanticRouter] Failed to load encoder: {e}")
            raise
        
        self.exact_match_dict: Dict[str, str] = {}
        
        self.faiss_index = None
        self.index_queries: List[str] = []  
        self.index_tools: List[str] = []  
        
        self._load_csv()
        
        logger.info(f"[FastSemanticRouter] Initialization complete. "
                   f"Exact matches: {len(self.exact_match_dict)}, "
                   f"FAISS index size: {len(self.index_queries)}")
    
    def _normalize_tool_name(self, tool_name: str) -> str:
        tool_lower = tool_name.strip().lower()
        return self.csv_to_enum_mapping.get(tool_lower, tool_lower)
    
    def _load_csv(self) -> None:

        if not os.path.exists(self.csv_path):
            logger.warning(f"[FastSemanticRouter] CSV not found at {self.csv_path}. Starting with empty database.")
            self._initialize_empty_faiss()
            return
        
        try:
            queries_for_embedding = []
            tools_for_embedding = []
            
            with open(self.csv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                
                for row in reader:
                    tool_name = row.get('Tool_Name', '').strip()
                    user_query = row.get('User_Query', '').strip()
                    
                    if not tool_name or not user_query:
                        continue
                    
                    tool_normalized = self._normalize_tool_name(tool_name)
                    
                    query_key = user_query.lower().strip()
                    self.exact_match_dict[query_key] = tool_normalized
                    
                    queries_for_embedding.append(user_query)
                    tools_for_embedding.append(tool_normalized)
            
            if queries_for_embedding:
                self._build_faiss_index(queries_for_embedding, tools_for_embedding)
            else:
                self._initialize_empty_faiss()
                
            logger.info(f"[FastSemanticRouter] Loaded {len(self.exact_match_dict)} queries from CSV")
            
        except Exception as e:
            logger.error(f"[FastSemanticRouter] Error loading CSV: {e}")
            self._initialize_empty_faiss()
    
    def _initialize_empty_faiss(self) -> None:
        self.faiss_index = faiss.IndexFlatIP(self.embedding_dim)
        self.index_queries = []
        self.index_tools = []
        logger.info("[FastSemanticRouter] Initialized empty FAISS index")
    
    def _build_faiss_index(self, queries: List[str], tools: List[str]) -> None:

        try:
            embeddings = self.encoder.encode(
                queries,
                convert_to_numpy=True,
                show_progress_bar=False,
                normalize_embeddings=True 
            )
            
            self.faiss_index = faiss.IndexFlatIP(self.embedding_dim)
            self.faiss_index.add(embeddings.astype('float32'))
            
            self.index_queries = queries
            self.index_tools = tools
            
            logger.info(f"[FastSemanticRouter] Built FAISS index with {len(queries)} vectors")
            
        except Exception as e:
            logger.error(f"[FastSemanticRouter] Error building FAISS index: {e}")
            self._initialize_empty_faiss()
    
    def _search_faiss(self, query: str) -> Tuple[Optional[str], float]:

        if self.faiss_index is None or self.faiss_index.ntotal == 0:
            return None, 0.0
        
        try:
            query_embedding = self.encoder.encode(
                [query],
                convert_to_numpy=True,
                show_progress_bar=False,
                normalize_embeddings=True
            )
            
            similarities, indices = self.faiss_index.search(
                query_embedding.astype('float32'), 
                k=1
            )
            
            if len(indices) > 0 and len(indices[0]) > 0:
                idx = indices[0][0]
                similarity = float(similarities[0][0])
                
                if similarity >= self.similarity_threshold:
                    matched_tool = self.index_tools[idx]
                    matched_query = self.index_queries[idx]
                    logger.info(f"[FastSemanticRouter] FAISS match: '{query}' -> '{matched_query}' "
                               f"(similarity: {similarity:.4f}) -> {matched_tool}")
                    return matched_tool, similarity
                else:
                    logger.info(f"[FastSemanticRouter] FAISS similarity too low: {similarity:.4f} < {self.similarity_threshold}")
                    return None, similarity
            
            return None, 0.0
            
        except Exception as e:
            logger.error(f"[FastSemanticRouter] Error in FAISS search: {e}")
            return None, 0.0
    
    def _learn_new_query(self, query: str, tool: str) -> None:

        try:
            query_clean = query.strip()
            query_key = query_clean.lower().strip()
            
            if query_key in self.exact_match_dict:
                logger.info(f"[FastSemanticRouter] Query already in memory, skipping learning: '{query_clean}'")
                return
            
            self.exact_match_dict[query_key] = tool
            
            query_embedding = self.encoder.encode(
                [query_clean],
                convert_to_numpy=True,
                show_progress_bar=False,
                normalize_embeddings=True
            )
            
            if self.faiss_index is not None:
                self.faiss_index.add(query_embedding.astype('float32'))
                self.index_queries.append(query_clean)
                self.index_tools.append(tool)
            
            with self._write_lock:
                with open(self.csv_path, 'a', encoding='utf-8', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow([tool.upper(), query_clean, "AUTO_LEARNED"])
            
            logger.info(f"[FastSemanticRouter] Learned new query: '{query_clean}' -> {tool}")
            
        except Exception as e:
            logger.error(f"[FastSemanticRouter] Error learning query: {e}")
    
    async def route_query(
        self, 
        query: str,
        llm_fallback_func=None,
        **llm_kwargs
    ) -> str:

        try:
            query_clean = query.strip()
            query_key = query_clean.lower().strip()

            if query_key in self.exact_match_dict:
                matched_tool = self.exact_match_dict[query_key]
                logger.debug(f"[FastSemanticRouter] Layer 1 (Exact): '{query_clean}' -> {matched_tool}")
                return matched_tool

            matched_tool, similarity = self._search_faiss(query_clean)
            if matched_tool:
                logger.debug(f"[FastSemanticRouter] Layer 2 (FAISS): '{query_clean}' -> {matched_tool} "
                           f"(similarity: {similarity:.4f})")
                return matched_tool

            if llm_fallback_func:
                logger.debug(f"[FastSemanticRouter] Layer 3 (LLM): Calling fallback for '{query_clean}'")
                
                llm_result = await llm_fallback_func(query=query_clean, **llm_kwargs)
                
                if hasattr(llm_result, 'data'):
                    llm_tool = llm_result.data
                elif isinstance(llm_result, str):
                    llm_tool = llm_result
                else:
                    llm_tool = str(llm_result)
                
                llm_tool = llm_tool.strip().lower()
                
                if llm_tool and llm_tool not in ["other", "error", "", self.fallback_tool]:
                    self._learn_new_query(query_clean, llm_tool)
                
                logger.debug(f"[FastSemanticRouter] Layer 3 (LLM): '{query_clean}' -> {llm_tool}")
                return llm_tool
            

            logger.warning(f"[FastSemanticRouter] Layer 4 (Failsafe): No LLM fallback provided, "
                          f"returning fallback tool '{self.fallback_tool}'")
            return self.fallback_tool
            
        except Exception as e:

            logger.error(f"[FastSemanticRouter] Layer 4 (Exception): {e}. "
                        f"Returning failsafe tool '{self.fallback_tool}'")
            return self.fallback_tool
    
    def get_stats(self) -> Dict:
        return {
            "exact_matches": len(self.exact_match_dict),
            "faiss_index_size": self.faiss_index.ntotal if self.faiss_index else 0,
            "similarity_threshold": self.similarity_threshold,
            "embedding_dim": self.embedding_dim
        }
