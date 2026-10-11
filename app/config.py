import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()  # reads .env from the working directory; real environment variables win


@dataclass(frozen=True)
class Settings:
    # azure-openai = GPT deployed in Microsoft Foundry (Azure OpenAI)
    # foundry      = Claude deployed in Microsoft Foundry
    # anthropic    = Anthropic API directly
    llm_provider: str = os.getenv("LLM_PROVIDER", "azure-openai").strip().lower()
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    foundry_resource: str = os.getenv("FOUNDRY_RESOURCE", "")   # <name> in https://<name>.services.ai.azure.com
    foundry_api_key: str = os.getenv("FOUNDRY_API_KEY", "")
    # With Foundry this is the deployment name you chose in the portal (defaults to the model name)
    agent_model: str = os.getenv("AGENT_MODEL", "gpt-4o")
    # Claude Haiku 5.5+ thinks before every reply by default, which callers hear as a pause.
    # off = no thinking (fastest), adaptive = model decides, model-default = send nothing (Haiku 4.5)
    agent_thinking: str = os.getenv("AGENT_THINKING", "off").strip().lower()
    agent_effort: str = os.getenv("AGENT_EFFORT", "low").strip().lower()  # blank for Haiku 4.5
    pinecone_api_key: str = os.getenv("PINECONE_API_KEY", "")
    pinecone_index: str = os.getenv("PINECONE_INDEX", "call-agent-kb")
    pinecone_cloud: str = os.getenv("PINECONE_CLOUD", "aws")
    pinecone_region: str = os.getenv("PINECONE_REGION", "us-east-1")
    # Knowledge bases at or below this size go straight into the prompt (cached);
    # above it the contractor is switched to Pinecone retrieval automatically.
    kb_full_context_max_tokens: int = int(os.getenv("KB_FULL_CONTEXT_MAX_TOKENS", "30000"))
    session_secret: str = os.getenv("SESSION_SECRET", "dev-only-secret-change-me")
    public_base_url: str = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")
    twilio_auth_token: str = os.getenv("TWILIO_AUTH_TOKEN", "")
    ms_tenant_id: str = os.getenv("MS_TENANT_ID", "")
    ms_client_id: str = os.getenv("MS_CLIENT_ID", "")
    ms_client_secret: str = os.getenv("MS_CLIENT_SECRET", "")
    database_path: str = os.getenv("DATABASE_PATH", "data/app.db")

    @property
    def llm_configured(self) -> bool:
        if self.llm_provider in ("foundry", "azure-openai"):
            return bool(self.foundry_resource and self.foundry_api_key)
        return bool(self.anthropic_api_key)

    @property
    def graph_enabled(self) -> bool:
        return bool(self.ms_tenant_id and self.ms_client_id and self.ms_client_secret)


settings = Settings()
