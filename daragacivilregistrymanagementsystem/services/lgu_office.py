"""Daraga Civil Registry office server (client hardware, October 2026)."""
from __future__ import annotations

import os
import socket
from pathlib import Path

# DxDiag: Windows Server 2022 Standard, Dell PowerEdge T360, Xeon E-2414, 16 GB RAM
LGU_SERVER_COMPUTER_NAME = "WIN-HIL0JNDFJIIN"
LGU_SERVER_OS = "Windows Server 2022 Standard"
LGU_SERVER_MODEL = "Dell PowerEdge T360"
LGU_BACKUP_SHARE = "CivilRegistryBackups"
LGU_DATA_SHARE = "CivilRegistryData"
LGU_LOCAL_BACKUP_DIR = Path(r"C:\CivilRegistryBackups")
LGU_LOCAL_DATA_DIR = Path(r"C:\CivilRegistryData")


def lgu_server_computer_name() -> str:
    return (os.environ.get("DARAGA_LGU_SERVER_NAME") or LGU_SERVER_COMPUTER_NAME).strip() or LGU_SERVER_COMPUTER_NAME


def this_computer_name() -> str:
    try:
        return (socket.gethostname() or "").strip() or "this-computer"
    except OSError:
        return "this-computer"


def is_lgu_office_server() -> bool:
    return this_computer_name().upper() == lgu_server_computer_name().upper()


def lgu_backup_unc() -> str:
    return f"\\\\{lgu_server_computer_name()}\\{LGU_BACKUP_SHARE}"


def lgu_data_unc() -> str:
    return f"\\\\{lgu_server_computer_name()}\\{LGU_DATA_SHARE}"


def default_backup_path_for_this_computer() -> str:
    """Official archive: Daraga server disk, or the office share from any other PC."""
    env = (os.environ.get("DARAGA_BACKUP_ROOT") or "").strip().strip('"')
    if env:
        return env
    if is_lgu_office_server():
        return str(LGU_LOCAL_BACKUP_DIR)
    return lgu_backup_unc()
