"""Bounded Strands providers using the worker's AWS credential chain."""

from datetime import timedelta

from ordertowork.config import Settings
from strands.models.model import Model


def create_bedrock_model(settings: Settings) -> Model:
    if not settings.bedrock_model_id:
        raise ValueError("Bedrock model is not configured")
    if settings.bedrock_endpoint == "mantle":
        import httpx
        from strands.models.openai import OpenAIModel

        return OpenAIModel(
            model_id=settings.bedrock_model_id,
            bedrock_mantle_config={
                "region": settings.aws_region,
                # Strands mints a token on every request using refreshed role credentials.
                "expiry": timedelta(minutes=15),
            },
            client_args={
                "project": settings.bedrock_mantle_project_id,
                "timeout": httpx.Timeout(
                    60, connect=5, read=min(60, settings.agent_timeout_seconds)
                ),
                # Durable job retries are counted by our daily paid-attempt budget.
                "max_retries": 0,
            },
            params={
                "max_completion_tokens": settings.bedrock_max_output_tokens,
                "temperature": 0,
                "parallel_tool_calls": False,
            },
        )

    from botocore.config import Config
    from strands.models import BedrockModel

    return BedrockModel(
        model_id=settings.bedrock_model_id,
        region_name=settings.aws_region,
        max_tokens=settings.bedrock_max_output_tokens,
        temperature=0,
        boto_client_config=Config(
            connect_timeout=5,
            read_timeout=min(60, settings.agent_timeout_seconds),
            retries={"mode": "standard", "total_max_attempts": 2},
        ),
    )
