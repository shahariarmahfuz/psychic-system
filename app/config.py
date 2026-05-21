from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

    database_url: str
    app_secret: str
    encryption_key: str
    admin_token: str
    session_ttl_seconds: int = 90
    cleanup_interval_seconds: int = 30
    railway_cli_path: str = 'railway'
    slot_work_root: str = '/tmp/nekotunnel-railway-slots'
    service_name: str = 'final'
    tcp_internal_port: int = 8080
    frp_version: str = '0.57.0'


@lru_cache
def get_settings() -> Settings:
    return Settings()
