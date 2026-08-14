"""
Supabase client accessors.

Two clients are exposed:
- get_service_client(): uses the service-role key. Full read/write.
  ONLY used server-side (ingestion jobs, writes). Never expose this
  key or this client to the frontend.
- get_anon_client(): uses the anon key, for any read path where you
  want RLS to apply exactly as it would for the frontend, useful for
  testing policies from the backend.
"""
from functools import lru_cache

from supabase import Client, create_client

from app.config import get_settings


@lru_cache
def get_service_client() -> Client:
    settings = get_settings()
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


@lru_cache
def get_anon_client() -> Client:
    settings = get_settings()
    return create_client(settings.supabase_url, settings.supabase_anon_key)
