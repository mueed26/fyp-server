

from supabase import create_client,Client #to set up the client 
from dotenv import load_dotenv
import os

load_dotenv()

#set up the urls
supabase_url=os.getenv("SUPABASE_API_URL")
supabase_key=os.getenv("SUPABASE_SECRET_KEY")

if not supabase_url or not supabase_key:
    raise ValueError("MISSING SUPABASE CREDENTIALS IN ENVIRONMENT VARIABLES")

#invoke the create client function 
supabase:Client=create_client(supabase_url,supabase_key)
