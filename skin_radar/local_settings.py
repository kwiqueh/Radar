"""Local configuration, stored with restricted permissions; never returned with secrets."""

import json
import os
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from skin_radar.config import Settings, VenueFees


class SetupUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    mode: str | None = None
    enabled_providers: list[str] | None = None
    api_terms_accepted: bool | None = None
    usd_rub: str | None = None
    eur_rub: str | None = None
    fx_verified_at: str | None = None
    venue_fees: dict[str, VenueFees] | None = None
    csfloat_api_key: str | None = Field(default=None, repr=False, max_length=4096)


def load_local_settings(settings: Settings) -> Settings:
    path = settings.local_config_file
    if not path.is_file():
        return settings
    try:
        updates = SetupUpdate.model_validate_json(path.read_text(encoding="utf-8"))
        return Settings.model_validate(
            {**settings.model_dump(), **updates.model_dump(exclude_none=True)}
        )
    except (ValueError, OSError):
        raise ValueError(
            "Cannot read settings.local.json; check its format without sharing secrets"
        ) from None


def save_local_settings(path: Path, update: SetupUpdate) -> None:
    previous = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    values = {**previous, **update.model_dump(mode="json", exclude_none=True)}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".radar-settings-", delete=False
        ) as file:
            temporary = Path(file.name)
            os.chmod(temporary, 0o600)
            json.dump(values, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
