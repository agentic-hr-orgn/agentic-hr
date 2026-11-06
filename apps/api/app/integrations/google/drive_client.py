from typing import Any

from app.integrations.base import DriveFile, DriveService


class GoogleDriveService(DriveService):
    """
    Real Google Drive service client.
    Scope: https://www.googleapis.com/auth/drive.file ONLY per ADR 006.
    """

    ALLOWED_SCOPE = "https://www.googleapis.com/auth/drive.file"

    def __init__(self, credentials: Any = None) -> None:
        self.credentials = credentials

    async def upload_file(
        self,
        name: str,
        content: bytes,
        mime_type: str,
        folder_id: str | None = None,
    ) -> DriveFile:
        if not self.credentials:
            raise RuntimeError(
                "Real Google Drive integration requires valid credentials. "
                "In Stage 1, ensure USE_FAKE_INTEGRATIONS=true."
            )
        raise NotImplementedError("Real Drive file upload active in later stages.")
