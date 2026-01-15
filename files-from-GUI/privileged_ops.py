"""
Privileged operations handler for UAC-requiring tasks.

This module contains functions that require Windows administrator privileges:
- Windows Firewall rule management
- Process termination (taskkill)

These operations should be called by the elevated NSSM service, not the GUI directly.
The GUI can invoke these via subprocess calls or IPC (command line args, named pipes, etc).

Usage from service:
    python -m miner_GUI.services.privileged_ops --add-firewall-rule --port 4050 --protocol tcp
    python -m miner_GUI.services.privileged_ops --add-firewall-rule --port 51820 --protocol udp --direction in
    python -m miner_GUI.services.privileged_ops --add-firewall-rule --port 51820 --protocol udp --direction out
    python -m miner_GUI.services.privileged_ops --kill-process --pid 12345
    python -m miner_GUI.services.privileged_ops --terminate-older-guis --miner-code BM --version 1.2.3
"""

from __future__ import annotations

import os
import subprocess
import sys
import argparse
from pathlib import Path
from typing import Optional, Tuple

from miner_GUI.utils.data import log_step

DEFAULT_TEQUILAPI_PORT = 4050
WIREGUARD_PORT = 51820


def add_firewall_rule(
    port: int,
    protocol: str = "tcp",
    direction: str = "in",
    rule_name: Optional[str] = None,
) -> Tuple[bool, str]:
    """Add Windows Firewall rule for the specified port.
    
    Args:
        port: Port number to open
        protocol: 'tcp' or 'udp'
        direction: 'in' (inbound), 'out' (outbound), or 'in,out' (both)
        rule_name: Custom rule name; auto-generated if None
    
    Returns:
        (success: bool, message: str)
    """
    if not sys.platform.startswith('win'):
        return True, "Not Windows; firewall rule skipped"
    
    if not rule_name:
        if port == DEFAULT_TEQUILAPI_PORT:
            rule_name = f"FryNetworks Mysterium API {port}"
        elif port == WIREGUARD_PORT:
            dir_label = "Inbound" if direction == "in" else "Outbound" if direction == "out" else "Both"
            rule_name = f"FryNetworks Mysterium WireGuard UDP {port} {dir_label}"
        else:
            rule_name = f"FryNetworks Port {port} {protocol.upper()}"
    
    try:
        # Handle both directions if specified
        directions = ["in", "out"] if direction == "in,out" else [direction]
        
        for dir_item in directions:
            cmd = [
                "netsh",
                "advfirewall",
                "firewall",
                "add",
                "rule",
                f"name={rule_name}" + ("" if dir_item == "in" else " (Outbound)"),
                f"dir={dir_item}",
                "action=allow",
                f"protocol={protocol.upper()}",
                f"localport={port}",
            ]
            
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=8,
                creationflags=subprocess.CREATE_NO_WINDOW,
                encoding="utf-8",
                errors="ignore",
            )
            
            if proc.returncode != 0:
                err_msg = proc.stderr or proc.stdout or str(proc.returncode)
                log_step(
                    "privileged_firewall_rule_failed",
                    {
                        "port": port,
                        "protocol": protocol,
                        "direction": dir_item,
                        "error": err_msg,
                    },
                )
                return False, f"Failed to add firewall rule for {dir_item} direction: {err_msg}"
        
        log_step(
            "privileged_firewall_rule_added",
            {"port": port, "protocol": protocol, "directions": directions},
        )
        return True, f"Firewall rule added for port {port}/{protocol}"
    
    except Exception as exc:
        error_str = str(exc)
        log_step(
            "privileged_firewall_rule_exception",
            {"port": port, "protocol": protocol, "error": error_str},
        )
        return False, f"Exception adding firewall rule: {error_str}"


def remove_firewall_rule(
    port: int,
    protocol: str = "tcp",
    rule_name: Optional[str] = None,
) -> Tuple[bool, str]:
    """Remove Windows Firewall rule by port or name.
    
    Args:
        port: Port number of rule to remove
        protocol: 'tcp' or 'udp'
        rule_name: Custom rule name; auto-generated if None
    
    Returns:
        (success: bool, message: str)
    """
    if not sys.platform.startswith('win'):
        return True, "Not Windows; firewall rule removal skipped"
    
    if not rule_name:
        if port == DEFAULT_TEQUILAPI_PORT:
            rule_name = f"FryNetworks Mysterium API {port}"
        elif port == WIREGUARD_PORT:
            rule_name = f"FryNetworks Mysterium WireGuard UDP {port}"
        else:
            rule_name = f"FryNetworks Port {port} {protocol.upper()}"
    
    try:
        cmd = [
            "netsh",
            "advfirewall",
            "firewall",
            "delete",
            "rule",
            f"name={rule_name}",
        ]
        
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=subprocess.CREATE_NO_WINDOW,
            encoding="utf-8",
            errors="ignore",
        )
        
        if proc.returncode != 0 and "not found" not in proc.stderr.lower():
            err_msg = proc.stderr or proc.stdout or str(proc.returncode)
            log_step(
                "privileged_firewall_rule_deletion_failed",
                {"rule_name": rule_name, "error": err_msg},
            )
            return False, f"Failed to remove firewall rule: {err_msg}"
        
        log_step("privileged_firewall_rule_removed", {"rule_name": rule_name})
        return True, f"Firewall rule removed: {rule_name}"
    
    except Exception as exc:
        error_str = str(exc)
        log_step(
            "privileged_firewall_rule_deletion_exception",
            {"rule_name": rule_name, "error": error_str},
        )
        return False, f"Exception removing firewall rule: {error_str}"


def kill_process(pid: int, force: bool = True) -> Tuple[bool, str]:
    """Terminate a process by PID.
    
    Args:
        pid: Process ID to terminate
        force: Use /F flag (force kill) if True
    
    Returns:
        (success: bool, message: str)
    """
    if not sys.platform.startswith('win'):
        # On non-Windows, try SIGTERM then SIGKILL
        try:
            import signal
            os.kill(pid, signal.SIGTERM)
            return True, f"Process {pid} terminated"
        except Exception as exc:
            return False, f"Failed to terminate process {pid}: {exc}"
    
    try:
        cmd = ["taskkill"]
        if force:
            cmd.append("/F")
        cmd.extend(["/PID", str(pid)])
        
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        
        if proc.returncode != 0:
            err_msg = proc.stderr or proc.stdout or str(proc.returncode)
            log_step(
                "privileged_kill_process_failed",
                {"pid": pid, "force": force, "error": err_msg},
            )
            return False, f"Failed to terminate process {pid}: {err_msg}"
        
        log_step("privileged_process_killed", {"pid": pid, "force": force})
        return True, f"Process {pid} terminated"
    
    except Exception as exc:
        error_str = str(exc)
        log_step(
            "privileged_kill_process_exception",
            {"pid": pid, "error": error_str},
        )
        return False, f"Exception terminating process {pid}: {error_str}"


def setup_mysterium_firewall() -> Tuple[bool, str]:
    """Set up all firewall rules needed for Mysterium (both TequilAPI and WireGuard).
    
    Returns:
        (success: bool, message: str)
    """
    messages = []
    all_success = True
    
    # TequilAPI port
    success, msg = add_firewall_rule(DEFAULT_TEQUILAPI_PORT, protocol="tcp")
    messages.append(f"TequilAPI: {msg}")
    all_success = all_success and success
    
    # WireGuard UDP port (both directions)
    success, msg = add_firewall_rule(WIREGUARD_PORT, protocol="udp", direction="in")
    messages.append(f"WireGuard (in): {msg}")
    all_success = all_success and success
    
    success, msg = add_firewall_rule(WIREGUARD_PORT, protocol="udp", direction="out")
    messages.append(f"WireGuard (out): {msg}")
    all_success = all_success and success
    
    return all_success, "; ".join(messages)


def write_config_file(
    relative_path: str,
    content: str,
    create_dirs: bool = True,
) -> Tuple[bool, str]:
    r"""Write JSON configuration file to ProgramData directory.
    
    Args:
        relative_path: Path relative to C:\ProgramData\FryNetworks\miner-{CODE}\
                      e.g., "config/device_config.json" or "status/status-20260108.json"
        content: JSON string content to write
        create_dirs: Whether to create parent directories
    
    Returns:
        (success: bool, message: str)
    """
    try:
        if not sys.platform.startswith('win'):
            # On non-Windows, use /var/lib location
            from miner_GUI.utils.data import data_dir_gui
            base_dir = data_dir_gui()
        else:
            # Windows: Use ProgramData
            from miner_GUI.utils.data import data_dir_gui
            base_dir = data_dir_gui()
        
        full_path = base_dir / relative_path
        
        # Create parent directories if needed
        if create_dirs:
            full_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write atomically (write to temp file first, then rename)
        temp_path = full_path.with_suffix(full_path.suffix + '.tmp')
        with open(temp_path, 'w', encoding='utf-8') as f:
            f.write(content)
        temp_path.replace(full_path)
        
        log_step(
            "privileged_config_file_written",
            {"path": str(full_path)},
        )
        return True, f"Config file written: {relative_path}"
    
    except Exception as exc:
        error_str = str(exc)
        log_step(
            "privileged_write_config_file_failed",
            {"path": relative_path, "error": error_str},
        )
        return False, f"Failed to write config file {relative_path}: {error_str}"


def read_config_file(relative_path: str) -> Tuple[bool, str, Optional[str]]:
    r"""Read JSON configuration file from ProgramData directory.
    
    Args:
        relative_path: Path relative to C:\ProgramData\FryNetworks\miner-{CODE}\
    
    Returns:
        (success: bool, message: str, content: Optional[str])
        content is None if read failed
    """
    try:
        from miner_GUI.utils.data import data_dir_gui
        base_dir = data_dir_gui()
        full_path = base_dir / relative_path
        
        if not full_path.exists():
            return False, f"File not found: {relative_path}", None
        
        with open(full_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        log_step(
            "privileged_config_file_read",
            {"path": str(full_path)},
        )
        return True, f"Config file read: {relative_path}", content
    
    except Exception as exc:
        error_str = str(exc)
        log_step(
            "privileged_read_config_file_failed",
            {"path": relative_path, "error": error_str},
        )
        return False, f"Failed to read config file {relative_path}: {error_str}", None


def write_measurement_file(
    group: str,
    encrypted_data: bytes,
) -> Tuple[bool, str]:
    """Write encrypted measurement data to ProgramData directory.
    
    Args:
        group: Measurement group identifier (e.g., "mysterium", "bright")
        encrypted_data: Already-encrypted bytes from Fernet
    
    Returns:
        (success: bool, message: str)
    """
    try:
        from miner_GUI.utils.data import data_dir_gui
        base_dir = data_dir_gui()
        
        # Create measurements directory
        measurements_dir = base_dir / "measurements"
        measurements_dir.mkdir(parents=True, exist_ok=True)
        
        # Write to measurements-{GROUP}-latest.json.enc
        file_name = f"measurements-{group}-latest.json.enc"
        full_path = measurements_dir / file_name
        
        # Write atomically
        temp_path = full_path.with_suffix(full_path.suffix + '.tmp')
        with open(temp_path, 'wb') as f:
            f.write(encrypted_data)
        temp_path.replace(full_path)
        
        log_step(
            "privileged_measurement_file_written",
            {"group": group, "path": str(full_path)},
        )
        return True, f"Measurement file written for {group}"
    
    except Exception as exc:
        error_str = str(exc)
        log_step(
            "privileged_write_measurement_failed",
            {"group": group, "error": error_str},
        )
        return False, f"Failed to write measurement file for {group}: {error_str}"


def main():
    """CLI entry point for service to call privileged operations."""
    parser = argparse.ArgumentParser(description="Privileged operations handler")
    
    # Firewall operations
    parser.add_argument(
        "--add-firewall-rule",
        action="store_true",
        help="Add a firewall rule",
    )
    parser.add_argument(
        "--remove-firewall-rule",
        action="store_true",
        help="Remove a firewall rule",
    )
    parser.add_argument(
        "--port",
        type=int,
        help="Port number for firewall rule",
    )
    parser.add_argument(
        "--protocol",
        choices=["tcp", "udp"],
        default="tcp",
        help="Protocol for firewall rule",
    )
    parser.add_argument(
        "--direction",
        choices=["in", "out", "in,out"],
        default="in",
        help="Direction for firewall rule",
    )
    parser.add_argument(
        "--rule-name",
        help="Custom name for firewall rule",
    )
    
    # Process operations
    parser.add_argument(
        "--kill-process",
        action="store_true",
        help="Kill a process by PID",
    )
    parser.add_argument(
        "--pid",
        type=int,
        help="Process ID to kill",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=True,
        help="Force kill the process",
    )
    
    # Setup operations
    parser.add_argument(
        "--setup-mysterium-firewall",
        action="store_true",
        help="Setup all firewall rules for Mysterium",
    )
    
    # File I/O operations
    parser.add_argument(
        "--write-config-file",
        action="store_true",
        help="Write configuration file to ProgramData",
    )
    parser.add_argument(
        "--read-config-file",
        action="store_true",
        help="Read configuration file from ProgramData",
    )
    parser.add_argument(
        "--write-measurement-file",
        action="store_true",
        help="Write encrypted measurement file to ProgramData",
    )
    parser.add_argument(
        "--path",
        help="Relative path for config file operations (e.g., config/device_config.json)",
    )
    parser.add_argument(
        "--content",
        help="Content to write (JSON string)",
    )
    parser.add_argument(
        "--group",
        help="Measurement group identifier (e.g., mysterium, bright)",
    )
    parser.add_argument(
        "--data-file",
        help="Path to binary data file for measurement write",
    )
    
    args = parser.parse_args()
    
    if args.add_firewall_rule:
        if not args.port:
            print("Error: --port required for --add-firewall-rule")
            sys.exit(1)
        success, msg = add_firewall_rule(
            args.port,
            protocol=args.protocol,
            direction=args.direction,
            rule_name=args.rule_name,
        )
        print(msg)
        sys.exit(0 if success else 1)
    
    elif args.remove_firewall_rule:
        if not args.port:
            print("Error: --port required for --remove-firewall-rule")
            sys.exit(1)
        success, msg = remove_firewall_rule(
            args.port,
            protocol=args.protocol,
            rule_name=args.rule_name,
        )
        print(msg)
        sys.exit(0 if success else 1)
    
    elif args.kill_process:
        if not args.pid:
            print("Error: --pid required for --kill-process")
            sys.exit(1)
        success, msg = kill_process(args.pid, force=args.force)
        print(msg)
        sys.exit(0 if success else 1)
    
    elif args.setup_mysterium_firewall:
        success, msg = setup_mysterium_firewall()
        print(msg)
        sys.exit(0 if success else 1)
    
    elif args.write_config_file:
        if not args.path or not args.content:
            print("Error: --path and --content required for --write-config-file")
            sys.exit(1)
        success, msg = write_config_file(args.path, args.content)
        print(msg)
        sys.exit(0 if success else 1)
    
    elif args.read_config_file:
        if not args.path:
            print("Error: --path required for --read-config-file")
            sys.exit(1)
        success, msg, content = read_config_file(args.path)
        print(msg)
        if content:
            print(content)
        sys.exit(0 if success else 1)
    
    elif args.write_measurement_file:
        if not args.group or not args.data_file:
            print("Error: --group and --data-file required for --write-measurement-file")
            sys.exit(1)
        try:
            with open(args.data_file, 'rb') as f:
                encrypted_data = f.read()
            success, msg = write_measurement_file(args.group, encrypted_data)
            print(msg)
            sys.exit(0 if success else 1)
        except Exception as e:
            print(f"Error reading data file: {e}")
            sys.exit(1)
    
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
