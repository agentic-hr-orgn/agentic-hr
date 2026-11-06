import uuid

from app.integrations.base import DriveFile, DriveService


class FakeDriveService(DriveService):
    """
    Fake Google Drive service storing files in memory or synthetic storage.
    Enforces ADR 006 & docs/oauth-scopes.md: drive.file only, no broad Drive access.
    """

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.metadata: dict[str, DriveFile] = {}

    async def upload_file(
        self,
        name: str,
        content: bytes,
        mime_type: str,
        folder_id: str | None = None,
    ) -> DriveFile:
        file_id = f"fake_file_{uuid.uuid4().hex[:12]}"
        self.files[file_id] = content
        drive_file = DriveFile(
            file_id=file_id,
            name=name,
            mime_type=mime_type,
            size_bytes=len(content),
            web_view_link=f"https://drive.google.com/file/d/{file_id}/view?usp=fake",
        )
        self.metadata[file_id] = drive_file
        return drive_file
