from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class RuntimeMount(BaseModel):
    host_path: str
    container_path: str
    mode: Literal['ro', 'rw'] = 'ro'

    @field_validator('host_path', 'container_path')
    @classmethod
    def validate_absolute_path(cls, path: str) -> str:
        if not path.startswith('/'):
            raise ValueError('mount paths must be absolute')
        return path


class StartRequest(BaseModel):
    image: str
    command: List[str]
    working_dir: str
    environment: Dict[str, str]
    session_id: str
    resource_factor: float
    runtime_class: Optional[str] = None
    enable_gpu: bool = False
    workspace_mount_path: Optional[str] = None
    workspace_mount_path_in_sandbox: Optional[str] = None
    use_host_network: bool = False
    runtime_mounts: List[RuntimeMount] = Field(default_factory=list)


class StopRequest(BaseModel):
    runtime_id: str


class RuntimeRequest(BaseModel):
    runtime_id: str
