from typing import TYPE_CHECKING, Any

from openhands.storage.files import FileStore
from openhands.storage.local import LocalFileStore
from openhands.storage.memory import InMemoryFileStore

if TYPE_CHECKING:
    from openhands.storage.google_cloud import GoogleCloudFileStore
    from openhands.storage.s3 import S3FileStore


def __getattr__(name: str) -> Any:
    if name == 'GoogleCloudFileStore':
        from openhands.storage.google_cloud import GoogleCloudFileStore

        globals()[name] = GoogleCloudFileStore
        return GoogleCloudFileStore
    if name == 'S3FileStore':
        from openhands.storage.s3 import S3FileStore

        globals()[name] = S3FileStore
        return S3FileStore
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


def get_file_store(file_store: str, file_store_path: str | None = None) -> FileStore:
    if file_store == 'local':
        if file_store_path is None:
            raise ValueError('file_store_path is required for local file store')
        return LocalFileStore(file_store_path)
    elif file_store == 's3':
        from openhands.storage.s3 import S3FileStore

        return S3FileStore(file_store_path)
    elif file_store == 'google_cloud':
        from openhands.storage.google_cloud import GoogleCloudFileStore

        return GoogleCloudFileStore(file_store_path)
    return InMemoryFileStore()


__all__ = [
    'FileStore',
    'GoogleCloudFileStore',
    'InMemoryFileStore',
    'LocalFileStore',
    'S3FileStore',
    'get_file_store',
]
