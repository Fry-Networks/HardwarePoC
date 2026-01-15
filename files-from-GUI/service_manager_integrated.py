"""
Service management module for handling miner service installation, control, and monitoring.

This module integrates with the FryNetworks standalone installer to provide enhanced
External API authentication, lease acquisition, and cross-platform service management.
"""
from typing import Dict, Any

"""Minimal ServiceManager stub for the GUI (integrated variant).

This module intentionally avoids performing any service installation or
management. The installer project handles service lifecycle operations.
"""

from typing import Dict, Any


class ServiceManager:
    def __init__(self):
        pass

    def get_service_status(self) -> str:
        return "UNSUPPORTED"

    def get_service_info(self) -> Dict[str, Any]:
        return {"status": "UNSUPPORTED", "managed_by": "external_installer"}

    def install_service(self, *args, **kwargs) -> bool:
        return False

    def uninstall_service(self, *args, **kwargs) -> bool:
        return False

    def start_service(self, *args, **kwargs) -> bool:
        return False

    def stop_service(self, *args, **kwargs) -> bool:
        return False

    def get_recent_logs(self, *args, **kwargs) -> Dict[str, str]:
        return {"error": "Service logs unavailable in GUI; use installer project."}