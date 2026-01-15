"""Service modules for business logic and background operations."""

from .worker import Worker
from .single_instance import SingleInstance
from .service_manager import ServiceManager

# The update manager is handled by the external Fry installer repository.
# Do not import or expose it here to avoid packaging/import issues.
__all__ = ['Worker', 'SingleInstance', 'ServiceManager']