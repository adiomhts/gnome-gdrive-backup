#!/usr/bin/env python3
"""
GNOME & System Backup Engine
Handles packaging and restoring:
- dconf / gsettings (all GNOME tweaks and system settings)
- GNOME Shell extensions (~/.local/share/gnome-shell/extensions)
- ~/SystemFiles (excluding asusctl)
- User crontab (crontab -l)
- Autostart entries (~/.config/autostart)
- Cursor themes (~/.icons, ~/.local/share/icons)
"""

import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Optional, Any


DEFAULT_OPTIONS = {
    "dconf": True,
    "extensions": True,
    "system_files": True,
    "crontab": True,
    "autostart": True,
    "cursor": True,
}


def log(msg: str, callback: Optional[Callable[[str], None]] = None):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line)
    if callback:
        callback(line)


def filter_asusctl(tarinfo: tarfile.TarInfo) -> Optional[tarfile.TarInfo]:
    """Excludes any path containing 'asusctl'."""
    parts = tarinfo.name.split("/")
    if "asusctl" in parts:
        return None
    return tarinfo


def get_cursor_theme_info() -> Dict[str, Any]:
    """Retrieves current cursor theme and size from gsettings."""
    theme = "Adwaita"
    size = 24
    try:
        res = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "cursor-theme"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        if res.stdout:
            theme = res.stdout.strip().strip("'\"")

        res_sz = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "cursor-size"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        if res_sz.stdout and res_sz.stdout.strip().isdigit():
            size = int(res_sz.stdout.strip())
    except Exception:
        pass
    return {"cursor_theme": theme, "cursor_size": size}


def create_backup_archive(
    output_path: str,
    options: Optional[Dict[str, bool]] = None,
    log_callback: Optional[Callable[[str], None]] = None,
) -> str:
    """
    Creates a full system and GNOME settings backup archive at output_path.
    Returns the absolute path to the generated archive.
    """
    opts = dict(DEFAULT_OPTIONS)
    if options:
        opts.update(options)

    start_time = time.time()
    log("Начало создания резервной копии...", log_callback)

    cursor_info = get_cursor_theme_info()
    manifest = {
        "version": "1.0",
        "created_at": datetime.now().isoformat(),
        "created_timestamp": int(time.time()),
        "hostname": os.uname().nodename,
        "username": os.environ.get("USER", "user"),
        "cursor": cursor_info,
        "options": opts,
        "contents": {},
    }

    with tempfile.TemporaryDirectory(prefix="gnome_backup_") as tmpdir:
        tmp_path = Path(tmpdir)

        # 1. dconf & Tweaks
        if opts.get("dconf"):
            log("Экспорт системных настроек и GNOME Tweaks (dconf dump)...", log_callback)
            dconf_file = tmp_path / "dconf_dump.ini"
            try:
                with open(dconf_file, "w", encoding="utf-8") as f:
                    subprocess.run(["dconf", "dump", "/"], stdout=f, check=True)
                manifest["contents"]["dconf"] = True
                log("✓ Системные настройки dconf сохранены", log_callback)
            except Exception as e:
                log(f"⚠ Ошибка экспорта dconf: {e}", log_callback)

        # 2. Crontab
        if opts.get("crontab"):
            log("Экспорт пользовательских задач cron (crontab -l)...", log_callback)
            crontab_file = tmp_path / "crontab.txt"
            res = subprocess.run(
                ["crontab", "-l"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            with open(crontab_file, "wb") as f:
                f.write(res.stdout)
            manifest["contents"]["crontab"] = True
            log("✓ Задачи cron сохранены", log_callback)

        # 3. Write manifest.json
        manifest_file = tmp_path / "manifest.json"
        with open(manifest_file, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

        # 4. Create tar.gz archive
        log("Упаковка файлов в архив...", log_callback)
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with tarfile.open(output_path, "w:gz") as tar:
            # Manifest
            tar.add(manifest_file, arcname="manifest.json")

            # dconf
            if opts.get("dconf") and (tmp_path / "dconf_dump.ini").exists():
                tar.add(tmp_path / "dconf_dump.ini", arcname="dconf_dump.ini")

            # crontab
            if opts.get("crontab") and (tmp_path / "crontab.txt").exists():
                tar.add(tmp_path / "crontab.txt", arcname="crontab.txt")

            # Autostart
            if opts.get("autostart"):
                autostart_dir = Path.home() / ".config" / "autostart"
                if autostart_dir.is_dir():
                    log("Добавление приложений автозапуска (~/.config/autostart)...", log_callback)
                    tar.add(autostart_dir, arcname="autostart")
                    log("✓ Автозапуск добавлен", log_callback)

            # GNOME Shell Extensions
            if opts.get("extensions"):
                ext_dir = Path.home() / ".local" / "share" / "gnome-shell" / "extensions"
                if ext_dir.is_dir():
                    log("Добавление расширений GNOME Shell (~/.local/share/gnome-shell/extensions)...", log_callback)
                    tar.add(ext_dir, arcname="extensions")
                    log("✓ Расширения GNOME Shell добавлены", log_callback)

            # SystemFiles (excluding asusctl)
            if opts.get("system_files"):
                sysfiles_dir = Path.home() / "SystemFiles"
                if sysfiles_dir.is_dir():
                    log("Добавление папки ~/SystemFiles (исключая asusctl)...", log_callback)
                    tar.add(sysfiles_dir, arcname="SystemFiles", filter=filter_asusctl)
                    log("✓ Папка ~/SystemFiles (без asusctl) добавлена", log_callback)

            # Cursor themes
            if opts.get("cursor"):
                log("Добавление курсоров и настроек темы...", log_callback)
                icons_dir = Path.home() / ".icons"
                if icons_dir.is_dir():
                    tar.add(icons_dir, arcname="icons")

                # Also check ~/.local/share/icons/<theme> if current theme is not in ~/.icons
                cur_theme = cursor_info.get("cursor_theme")
                if cur_theme and cur_theme != "Adwaita":
                    local_theme = Path.home() / ".local" / "share" / "icons" / cur_theme
                    if local_theme.is_dir():
                        tar.add(local_theme, arcname=f"local_icons/{cur_theme}")
                log("✓ Темы курсоров добавлены", log_callback)

    elapsed = time.time() - start_time
    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    log(f"Архив успешно создан: {output_path} ({size_mb:.2f} МБ) за {elapsed:.1f} сек.", log_callback)
    return os.path.abspath(output_path)


def inspect_backup(archive_path: str) -> Dict[str, Any]:
    """Inspects a backup archive and returns its manifest."""
    if not os.path.isfile(archive_path):
        raise FileNotFoundError(f"Файл архива не найден: {archive_path}")

    with tarfile.open(archive_path, "r:gz") as tar:
        try:
            member = tar.getmember("manifest.json")
            f = tar.extractfile(member)
            if f:
                return json.loads(f.read().decode("utf-8"))
        except KeyError:
            pass

        # If no manifest, summarize from member names
        names = tar.getnames()
        return {
            "version": "legacy",
            "created_at": datetime.fromtimestamp(os.path.getmtime(archive_path)).isoformat(),
            "hostname": "unknown",
            "username": "unknown",
            "cursor": {"cursor_theme": "unknown", "cursor_size": 24},
            "options": {
                "dconf": "dconf_dump.ini" in names,
                "crontab": "crontab.txt" in names,
                "autostart": any(n.startswith("autostart") for n in names),
                "extensions": any(n.startswith("extensions") for n in names),
                "system_files": any(n.startswith("SystemFiles") for n in names),
                "cursor": any(n.startswith("icons") or n.startswith("local_icons") for n in names),
            },
        }


def restore_backup(
    archive_path: str,
    options: Optional[Dict[str, bool]] = None,
    log_callback: Optional[Callable[[str], None]] = None,
) -> bool:
    """
    Restores settings and files from archive_path according to options.
    """
    if not os.path.isfile(archive_path):
        raise FileNotFoundError(f"Файл архива не найден: {archive_path}")

    opts = dict(DEFAULT_OPTIONS)
    if options:
        opts.update(options)

    log(f"Начало восстановления из {os.path.basename(archive_path)}...", log_callback)

    with tempfile.TemporaryDirectory(prefix="gnome_restore_") as tmpdir:
        tmp_path = Path(tmpdir)
        log("Распаковка архива во временную директорию...", log_callback)
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(path=tmp_path)

        manifest_file = tmp_path / "manifest.json"
        manifest = {}
        if manifest_file.exists():
            try:
                with open(manifest_file, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
            except Exception:
                pass

        # 1. SystemFiles (preserving existing asusctl)
        if opts.get("system_files") and (tmp_path / "SystemFiles").is_dir():
            log("Восстановление ~/SystemFiles (папка asusctl не перезаписывается)...", log_callback)
            dest_sysfiles = Path.home() / "SystemFiles"
            dest_sysfiles.mkdir(parents=True, exist_ok=True)
            for item in (tmp_path / "SystemFiles").iterdir():
                if item.name == "asusctl":
                    continue
                dest_item = dest_sysfiles / item.name
                if item.is_dir():
                    if dest_item.exists():
                        shutil.rmtree(dest_item)
                    shutil.copytree(item, dest_item)
                else:
                    shutil.copy2(item, dest_item)
            log("✓ Папка ~/SystemFiles восстановлена", log_callback)

        # 2. GNOME Shell Extensions
        if opts.get("extensions") and (tmp_path / "extensions").is_dir():
            log("Восстановление расширений GNOME Shell...", log_callback)
            dest_ext = Path.home() / ".local" / "share" / "gnome-shell" / "extensions"
            dest_ext.mkdir(parents=True, exist_ok=True)
            for ext in (tmp_path / "extensions").iterdir():
                dest_item = dest_ext / ext.name
                if dest_item.exists():
                    shutil.rmtree(dest_item)
                shutil.copytree(ext, dest_item)
            log("✓ Расширения GNOME Shell восстановлены", log_callback)

        # 3. Autostart
        if opts.get("autostart") and (tmp_path / "autostart").is_dir():
            log("Восстановление автозапуска (~/.config/autostart)...", log_callback)
            dest_auto = Path.home() / ".config" / "autostart"
            dest_auto.mkdir(parents=True, exist_ok=True)
            for item in (tmp_path / "autostart").iterdir():
                dest_item = dest_auto / item.name
                shutil.copy2(item, dest_item)
            log("✓ Автозапуск восстановлен", log_callback)

        # 4. Cursor & Icons
        if opts.get("cursor"):
            log("Восстановление тем курсоров...", log_callback)
            if (tmp_path / "icons").is_dir():
                dest_icons = Path.home() / ".icons"
                dest_icons.mkdir(parents=True, exist_ok=True)
                for item in (tmp_path / "icons").iterdir():
                    dest_item = dest_icons / item.name
                    if item.is_dir():
                        if dest_item.exists():
                            shutil.rmtree(dest_item)
                        shutil.copytree(item, dest_item)
                    else:
                        shutil.copy2(item, dest_item)

            if (tmp_path / "local_icons").is_dir():
                dest_local_icons = Path.home() / ".local" / "share" / "icons"
                dest_local_icons.mkdir(parents=True, exist_ok=True)
                for item in (tmp_path / "local_icons").iterdir():
                    dest_item = dest_local_icons / item.name
                    if item.is_dir():
                        if dest_item.exists():
                            shutil.rmtree(dest_item)
                        shutil.copytree(item, dest_item)

            # Apply cursor settings if in manifest
            cursor_info = manifest.get("cursor", {})
            cursor_theme = cursor_info.get("cursor_theme")
            cursor_size = cursor_info.get("cursor_size")
            if cursor_theme and cursor_theme != "unknown":
                log(f"Применение темы курсора '{cursor_theme}' (размер {cursor_size})...", log_callback)
                subprocess.run(
                    ["gsettings", "set", "org.gnome.desktop.interface", "cursor-theme", cursor_theme],
                    check=False,
                )
                if cursor_size:
                    subprocess.run(
                        ["gsettings", "set", "org.gnome.desktop.interface", "cursor-size", str(cursor_size)],
                        check=False,
                    )
            log("✓ Темы курсоров и параметры применены", log_callback)

        # 5. Crontab
        if opts.get("crontab") and (tmp_path / "crontab.txt").is_file():
            log("Восстановление задач cron...", log_callback)
            cron_content = (tmp_path / "crontab.txt").read_bytes()
            if cron_content.strip():
                try:
                    subprocess.run(
                        ["crontab", str(tmp_path / "crontab.txt")],
                        check=True,
                    )
                    log("✓ Задачи cron успешно загружены в crontab", log_callback)
                except Exception as e:
                    log(f"⚠ Ошибка загрузки crontab: {e}", log_callback)

        # 6. dconf & Tweaks
        if opts.get("dconf") and (tmp_path / "dconf_dump.ini").is_file():
            log("Применение всех системных настроек и GNOME Tweaks (dconf load /)...", log_callback)
            try:
                with open(tmp_path / "dconf_dump.ini", "r", encoding="utf-8") as f:
                    subprocess.run(["dconf", "load", "/"], stdin=f, check=True)
                log("✓ Системные настройки и расширения dconf успешно применены!", log_callback)
            except Exception as e:
                log(f"⚠ Ошибка применения dconf: {e}", log_callback)

    log("🎉 Восстановление успешно завершено!", log_callback)
    return True
