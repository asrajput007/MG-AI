import asyncio
import logging
from typing import Any, List, Optional, Tuple
from helpers.database import get_db_connection_dynamic

logger = logging.getLogger(__name__)


async def execute_query(
    database_name: str,
    query: str,
    params: Optional[Tuple] = None,
    fetch_type: str = "all",
    commit: bool = False
) -> Any:
    """
    Execute a database query in a thread pool to avoid blocking the event loop.
    
    Args:
        database_name: Database name
        query: SQL query
        params: Query parameters tuple
        fetch_type: "all", "one", "scalar", or "none"
        commit: Whether to commit the transaction
    
    Returns:
        Query results based on fetch_type
    """
    loop = asyncio.get_event_loop()
    
    # def _execute():
    #     conn = None
    #     cursor = None
    #     try:
    #         conn = get_db_connection_dynamic(database_name)
    #         cursor = conn.cursor()
            
    #         if params:
    #             cursor.execute(query, params)
    #         else:
    #             cursor.execute(query)
            
    #         if commit:
    #             conn.commit()
            
    #         if fetch_type == "all":
    #             return cursor.fetchall()
    #         elif fetch_type == "one":
    #             return cursor.fetchone()
    #         elif fetch_type == "scalar":
    #             row = cursor.fetchone()
    #             return row[0] if row else None
    #         else:  # "none"
    #             return None
                
    #     except Exception as e:
    # logger.error(f"Database error: {e}")
    #         if conn:
    #             try:
    #                 conn.rollback()
    #             except:
    #                 pass
    #         raise
    #     finally:
    #         if cursor:
    #             try:
    #                 cursor.close()
    #             except:
    #                 pass
    #         # if conn:
    #         #     return_connection(conn, database_name, "dynamic")
    
    def _execute():
        conn = None
        cursor = None
        try:
            conn = get_db_connection_dynamic(database_name)
            cursor = conn.cursor()
    
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
    
            result = None
    
            # ✅ FETCH FIRST
            if fetch_type == "all":
                result = cursor.fetchall()
            elif fetch_type == "one":
                result = cursor.fetchone()
            elif fetch_type == "scalar":
                row = cursor.fetchone()
                result = row[0] if row else None
            else:
                result = None
    
            # ✅ THEN COMMIT
            if commit:
                conn.commit()
    
            return result
    
        except Exception as e:
            if conn:
                conn.rollback()
            raise
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    return await loop.run_in_executor(None, _execute)



async def execute_many(
    database_name: str,
    query: str,
    params_list: List[Tuple],
    commit: bool = True
) -> int:
    """
    Execute a query multiple times with different parameters (bulk insert/update).
    
    Args:
        database_name: Database name
        query: SQL query
        params_list: List of parameter tuples
        commit: Whether to commit the transaction
    
    Returns:
        Number of rows affected
    """
    loop = asyncio.get_event_loop()
    
    def _execute():
        conn = None
        cursor = None
        try:
            conn = get_db_connection_dynamic(database_name)
            cursor = conn.cursor()
            
            cursor.executemany(query, params_list)
            
            if commit:
                conn.commit()
            
            return cursor.rowcount
                
        except Exception as e:
            logger.error(f"Database bulk operation error: {e}")
            if conn:
                try:
                    conn.rollback()
                except:
                    pass
            raise
        finally:
            if cursor:
                try:
                    cursor.close()
                except:
                    pass
            

    return await loop.run_in_executor(None, _execute)


async def execute_transaction(
    database_name: str,
    operations: List[Tuple[str, Optional[Tuple]]]
) -> bool:
    """
    Execute multiple queries in a single transaction.
    
    Args:
        database_name: Database name
        operations: List of (query, params) tuples
    
    Returns:
        True if successful, raises exception otherwise
    """
    loop = asyncio.get_event_loop()
    
    def _execute():
        conn = None
        cursor = None
        try:
            conn = get_db_connection_dynamic(database_name)
            cursor = conn.cursor()
            
            for query, params in operations:
                if params:
                    cursor.execute(query, params)
                else:
                    cursor.execute(query)
            
            conn.commit()
            return True
                
        except Exception as e:
            logger.error(f"Transaction error: {e}")
            if conn:
                try:
                    conn.rollback()
                except:
                    pass
            raise
        finally:
            if cursor:
                try:
                    cursor.close()
                except:
                    pass
    
    return await loop.run_in_executor(None, _execute)
