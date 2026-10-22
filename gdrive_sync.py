#!/usr/bin/env python3
"""
Google Drive GVfs/Gio Sync Layer
Manages cloud storage operations with zero third-party dependencies,
using the native GNOME GVfs Google Drive integration.
"""

import os
import re
from datetime import datetime
from typing import Callable, Dict, List, Optional, Any

import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib


def format_bytes(bytes_count: int) -> str:
    """Formats bytes into human-readable string."""
    if bytes_count < 1024:
        return f"{bytes_count} B"
    elif bytes_count < 1024 * 1024:
        return f"{bytes_count / 1024:.1f} KB"
    elif bytes_count < 1024 * 1024 * 1024:
        return f"{bytes_count / (1024 * 1024):.1f} MB"
    else:
        return f"{bytes_count / (1024 * 1024 * 1024):.2f} GB"


class GDriveSync:
    def __init__(self, folder_name: str = "GnomeBackups"):
        self.folder_name = folder_name
        self.volume_monitor = Gio.VolumeMonitor.get()

    def get_accounts(self) -> List[Dict[str, Any]]:
        """
        Discovers all Google Drive mounts currently connected in GNOME.
        """
        accounts = []
        mounts = self.volume_monitor.get_mounts()
        for mount in mounts:
            root = mount.get_root()
            uri = root.get_uri()
            if uri.startswith("google-drive://"):
                name = mount.get_name()
                # Extract email from uri or display name
                email_match = re.search(r"google-drive://([^/]+)", uri)
                email = email_match.group(1) if email_match else name
                accounts.append({
                    "id": uri,
                    "name": name,
                    "email": email,
                    "uri": uri,
                    "mount": mount,
                })
        return accounts

    def is_connected(self) -> bool:
        """Returns True if at least one Google Drive account is mounted."""
        return len(self.get_accounts()) > 0

    def get_my_drive_folder(self, account_uri: Optional[str] = None) -> Gio.File:
        """
        Finds the 'My Drive' / 'Мой диск' folder for the given account.
        """
        accounts = self.get_accounts()
        if not accounts:
            raise ConnectionError(
                "Google Диск не подключен! Откройте 'Параметры GNOME -> Сетевые учетные записи' "
                "и войдите в свой Google аккаунт."
            )

        target_acc = accounts[0]
        if account_uri:
            for acc in accounts:
                if acc["uri"] == account_uri or acc["id"] == account_uri:
                    target_acc = acc
                    break

        root = Gio.File.new_for_uri(target_acc["uri"])

        # Enumerate root to find My Drive
        try:
            en = root.enumerate_children(
                "standard::name,standard::display-name,standard::type",
                Gio.FileQueryInfoFlags.NONE,
                None,
            )
        except Exception as e:
            raise ConnectionError(f"Не удалось прочитать содержимое Google Диска: {e}")

        mydrive = None
        while True:
            info = en.next_file(None)
            if not info:
                break
            # Skip 'Shared with me' / 'Доступные мне'
            name = info.get_name()
            disp = info.get_display_name()
            if "shared" in name.lower() or "shared" in disp.lower():
                continue
            mydrive = root.get_child(name)
            break

        if not mydrive:
            # Fallback to root itself
            mydrive = root

        return mydrive

    def get_or_create_backup_folder(self, account_uri: Optional[str] = None) -> Gio.File:
        """
        Gets or creates the 'GnomeBackups' folder inside My Drive.
        """
        mydrive = self.get_my_drive_folder(account_uri)

        # Check if folder exists
        try:
            en = mydrive.enumerate_children(
                "standard::name,standard::display-name,standard::type",
                Gio.FileQueryInfoFlags.NONE,
                None,
            )
            while True:
                info = en.next_file(None)
                if not info:
                    break
                if info.get_display_name() == self.folder_name:
                    return mydrive.get_child(info.get_name())
        except Exception:
            pass

        # If not found, create it
        new_folder = mydrive.get_child(self.folder_name)
        try:
            new_folder.make_directory(None)
        except Exception as e:
            # If already exists or error, return child
            pass
        return new_folder

    def list_backups(self, account_uri: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Lists all backup archives in the GnomeBackups folder on Google Drive.
        """
        try:
            backup_folder = self.get_or_create_backup_folder(account_uri)
            en = backup_folder.enumerate_children(
                "standard::name,standard::display-name,standard::size,time::modified",
                Gio.FileQueryInfoFlags.NONE,
                None,
            )
        except Exception as e:
            print(f"Error enumerating backups: {e}")
            return []

        backups = []
        while True:
            info = en.next_file(None)
            if not info:
                break
            display_name = info.get_display_name()
            internal_name = info.get_name()

            if not (display_name.endswith(".tar.gz") or display_name.endswith(".tgz")):
                continue

            size = info.get_size()
            mtime_dt = info.get_modification_date_time()
            iso_time = mtime_dt.format_iso8601() if mtime_dt else ""
            date_str = mtime_dt.to_local().format("%Y-%m-%d %H:%M:%S") if mtime_dt else "Неизвестно"
            file_item = backup_folder.get_child(internal_name)

            backups.append({
                "id": internal_name,
                "display_name": display_name,
                "size": size,
                "size_str": format_bytes(size),
                "mtime": iso_time,
                "date_str": date_str,
                "uri": file_item.get_uri(),
                "file": file_item,
            })

        # Sort newest first
        backups.sort(key=lambda x: x["mtime"], reverse=True)
        return backups

    def upload_backup(
        self,
        local_path: str,
        account_uri: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> str:
        """
        Uploads local archive to Google Drive with real-time byte progress.
        """
        if not os.path.isfile(local_path):
            raise FileNotFoundError(f"Локальный файл не найден: {local_path}")

        file_name = os.path.basename(local_path)
        total_size = os.path.getsize(local_path)

        if log_callback:
            log_callback(f"Подключение к Google Диску ({format_bytes(total_size)})...")

        backup_folder = self.get_or_create_backup_folder(account_uri)
        target_file = backup_folder.get_child(file_name)
        src_file = Gio.File.new_for_path(local_path)

        if log_callback:
            log_callback(f"Загрузка '{file_name}' в облако...")

        def on_progress(current, total, data):
            # GVfs total might occasionally be 0, fallback to total_size
            act_total = total if total > 0 else total_size
            if progress_callback:
                progress_callback(current, act_total)

        src_file.copy(
            target_file,
            Gio.FileCopyFlags.OVERWRITE,
            None,
            on_progress,
            None,
        )

        if log_callback:
            log_callback(f"✓ '{file_name}' успешно загружен на Google Диск!")

        return target_file.get_uri()

    def download_backup(
        self,
        backup_item_or_uri: Any,
        local_dest_path: str,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> str:
        """
        Downloads a backup archive from Google Drive to local_dest_path.
        """
        if isinstance(backup_item_or_uri, str):
            src_file = Gio.File.new_for_uri(backup_item_or_uri)
        elif hasattr(backup_item_or_uri, "get_uri"):
            src_file = backup_item_or_uri
        elif isinstance(backup_item_or_uri, dict) and "file" in backup_item_or_uri:
            src_file = backup_item_or_uri["file"]
        else:
            raise ValueError(f"Недопустимый объект источника: {backup_item_or_uri}")

        dest_file = Gio.File.new_for_path(local_dest_path)
        os.makedirs(os.path.dirname(os.path.abspath(local_dest_path)), exist_ok=True)

        if log_callback:
            log_callback(f"Скачивание резервной копии из облака в {local_dest_path}...")

        def on_progress(current, total, data):
            if progress_callback:
                progress_callback(current, total)

        src_file.copy(
            dest_file,
            Gio.FileCopyFlags.OVERWRITE,
            None,
            on_progress,
            None,
        )

        if log_callback:
            log_callback(f"✓ Архив успешно загружен локально: {format_bytes(os.path.getsize(local_dest_path))}")

        return local_dest_path

    def delete_backup(self, backup_item_or_uri: Any) -> bool:
        """Deletes a backup file from Google Drive."""
        if isinstance(backup_item_or_uri, str):
            src_file = Gio.File.new_for_uri(backup_item_or_uri)
        elif hasattr(backup_item_or_uri, "get_uri"):
            src_file = backup_item_or_uri
        elif isinstance(backup_item_or_uri, dict) and "file" in backup_item_or_uri:
            src_file = backup_item_or_uri["file"]
        else:
            raise ValueError(f"Недопустимый объект: {backup_item_or_uri}")

        src_file.delete(None)
        return True
