"""Multi-tenant platform store: tenants, gateway users, resume CMS."""

from fs_gateway.platform.resume_schema import DEFAULT_SECTION_SPECS, empty_section_content
from fs_gateway.platform.store import MemoryPlatformStore, PlatformStore, PostgresPlatformStore

__all__ = [
    "DEFAULT_SECTION_SPECS",
    "MemoryPlatformStore",
    "PlatformStore",
    "PostgresPlatformStore",
    "empty_section_content",
]
