

from helpers.database import get_db_connection_dynamic
from helpers.utils import DYNAMIC_DB_NAME, get_user_profile_v2

user_id = '31ecf71f-bc8a-4745-84ff-06711db53bed'
# conn = get_db_connection_dynamic("FriskaAiCCM_EMDC")
# conn = get_db_connection_dynamic("FriskaAiCCM_REDC")
conn = get_db_connection_dynamic("FriskaAiCCM_HFWL")


cursor = conn.cursor()

profile = get_user_profile_v2(user_id=user_id, cursor=cursor)

print(profile)