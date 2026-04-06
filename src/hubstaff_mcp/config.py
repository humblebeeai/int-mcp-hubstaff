"""Configuration management using Pydantic."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    """Configuration from environment variables."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )
    hubstaff_refresh_token: str
    hubstaff_organization_id: str
    hubstaff_tasks_organization_id: str | None = None
    hubstaff_api_base_url: str = "https://api.hubstaff.com"
    hubstaff_tasks_base_url: str = "https://tasks.hubstaff.com"
    port: int = 8000
    
    @property
    def hubstaff_token(self) -> str:
        return self.hubstaff_refresh_token
    
    @property
    def hubstaff_org_id(self) -> str:
        return self.hubstaff_organization_id
    
    @property
    def hubstaff_tasks_org_id(self) -> str | None:
        return self.hubstaff_tasks_organization_id
    
    @property
    def base_url(self) -> str:
        return self.hubstaff_api_base_url


config = Config()
