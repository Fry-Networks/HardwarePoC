import psutil
import winreg
import time
from typing import Optional

def is_installed(program_name: str) -> bool:
    """Check if a program is installed on Windows by looking in the registry."""
    reg_paths = [
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"
    ]
    for path in reg_paths:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
                for i in range(winreg.QueryInfoKey(key)[0]):
                    subkey_name = winreg.EnumKey(key, i)
                    with winreg.OpenKey(key, subkey_name) as subkey:
                        try:
                            display_name = winreg.QueryValueEx(subkey, "DisplayName")[0]
                            if program_name.lower() in display_name.lower():
                                return True
                        except FileNotFoundError:
                            continue
        except FileNotFoundError:
            continue
    return False

def is_running(process_name: str) -> bool:
    """Check if a process is currently running."""
    for proc in psutil.process_iter(attrs=['name']):
        try:
            normalized = proc.info['name'].replace("-", "").lower()
            if process_name.replace("-", "").lower() in normalized:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False

def check_olostep_status() -> dict:
    """Check if Olostep is installed and running. Returns PoI status dict."""
    result = {
        "installed": is_installed("Olostep"),
        "running": is_running("Olostep"),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    }
    return result

# For AEM miners only: PoI monitoring entry point
def monitor_poi_for_aem() -> Optional[dict]:
    """Monitor Proof of Installed for Olostep (AEM only). Returns status dict or None."""
    # This function can be called from the main service loop for AEM miners
    try:
        status = check_olostep_status()
        # Here you could add logic to upload/report status, e.g. to API or log
        return status
    except Exception as e:
        # Log error if needed
        return None
