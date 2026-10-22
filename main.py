#!/usr/bin/env python3
"""
GNOME Cloud Backup - CLI & GUI Entrypoint
Provides both command line interface and GTK4/Libadwaita application.
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

# Add script directory to python path
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

from backup_engine import create_backup_archive, restore_backup, inspect_backup
from gdrive_sync import GDriveSync, format_bytes


def run_cli_backup(args):
    sync = GDriveSync()

    if not args.no_upload and not sync.is_connected():
        print("❌ Ошибка: Google Диск не подключен в GNOME!")
        print("Подключите учетную запись в 'Параметры GNOME -> Сетевые учетные записи' или используйте флаг --no-upload")
        sys.exit(1)

    now_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"gnome-backup-{now_str}.tar.gz"

    if args.output:
        local_archive = os.path.abspath(args.output)
    else:
        local_archive = f"/tmp/{filename}"

    options = {
        "dconf": not args.skip_dconf,
        "extensions": not args.skip_extensions,
        "system_files": not args.skip_systemfiles,
        "crontab": not args.skip_cron,
        "autostart": not args.skip_autostart,
        "cursor": not args.skip_cursor,
    }

    print(f"📦 Создание резервной копии: {local_archive}")
    create_backup_archive(local_archive, options=options)

    if not args.no_upload:
        print(f"☁️ Загрузка резервной копии на Google Диск...")

        def print_progress(current, total):
            pct = int(current * 100 / max(total, 1))
            sys.stdout.write(f"\r  Прогресс: {format_bytes(current)} / {format_bytes(total)} ({pct}%)")
            sys.stdout.flush()

        dest_uri = sync.upload_backup(local_archive, progress_callback=print_progress)
        print(f"\n✅ Успешно выгружено в облако!")

        if not args.keep_local and not args.output:
            if os.path.exists(local_archive):
                os.remove(local_archive)
    else:
        print(f"✅ Локальный архив сохранен в: {local_archive}")


def run_cli_list(args):
    sync = GDriveSync()
    if not sync.is_connected():
        print("❌ Google Диск не подключен в GNOME.")
        sys.exit(1)

    print("☁️ Поиск резервных копий на Google Диске (папка GnomeBackups)...")
    backups = sync.list_backups()
    if not backups:
        print("Резервные копии не найдены.")
        return

    print(f"\nНайдено копий: {len(backups)}\n")
    print(f"{'#':<3} {'Имя файла':<36} {'Размер':<12} {'Дата создания'}")
    print("-" * 75)
    for idx, b in enumerate(backups, 1):
        print(f"{idx:<3} {b['display_name']:<36} {b['size_str']:<12} {b['date_str']}")
    print("-" * 75)


def run_cli_restore(args):
    target = args.restore
    sync = GDriveSync()

    # Check if target is a local file
    if os.path.isfile(target):
        print(f"📦 Восстановление из локального файла: {target}")
        archive_path = os.path.abspath(target)
        restore_backup(archive_path)
        print("✅ Восстановление успешно завершено!")
        return

    # Check on Google Drive
    if not sync.is_connected():
        print("❌ Google Диск не подключен в GNOME.")
        sys.exit(1)

    backups = sync.list_backups()
    matched = None
    if target == "latest":
        if backups:
            matched = backups[0]
    else:
        for b in backups:
            if b["display_name"] == target or b["id"] == target:
                matched = b
                break

    if not matched:
        print(f"❌ Копия '{target}' не найдена на Google Диске.")
        print("Используйте команду --list для просмотра доступных копий или 'latest' для последней.")
        sys.exit(1)

    local_tmp = f"/tmp/{matched['display_name']}"
    print(f"☁️ Скачивание копии '{matched['display_name']}' ({matched['size_str']})...")

    def print_progress(current, total):
        pct = int(current * 100 / max(total, 1))
        sys.stdout.write(f"\r  Скачивание: {format_bytes(current)} / {format_bytes(total)} ({pct}%)")
        sys.stdout.flush()

    sync.download_backup(matched, local_tmp, progress_callback=print_progress)
    print(f"\n📦 Применение настроек...")
    restore_backup(local_tmp)

    if os.path.exists(local_tmp):
        os.remove(local_tmp)
    print("✅ Настройки успешно применены!")


def main():
    parser = argparse.ArgumentParser(
        description="GNOME Cloud Backup - резервное копирование и восстановление настроек GNOME на Google Диск."
    )
    parser.add_argument("--gui", action="store_true", help="Запустить графический интерфейс GTK4 / Libadwaita")
    parser.add_argument("--backup", action="store_true", help="Создать резервную копию и загрузить на Google Диск")
    parser.add_argument("--list", action="store_true", help="Показать список доступных копий на Google Диске")
    parser.add_argument("--restore", metavar="NAME_OR_PATH", help="Восстановить из файла или облачной копии (или 'latest')")
    parser.add_argument("--no-upload", action="store_true", help="Не загружать в облако (только локальный архив)")
    parser.add_argument("--keep-local", action="store_true", help="Сохранить локальную копию после выгрузки")
    parser.add_argument("--output", "-o", metavar="PATH", help="Путь для сохранения локального архива")

    # Filter arguments
    parser.add_argument("--skip-dconf", action="store_true", help="Не включать системные настройки dconf/tweaks")
    parser.add_argument("--skip-extensions", action="store_true", help="Не включать расширения GNOME Shell")
    parser.add_argument("--skip-systemfiles", action="store_true", help="Не включать папку ~/SystemFiles")
    parser.add_argument("--skip-cron", action="store_true", help="Не включать crontab")
    parser.add_argument("--skip-autostart", action="store_true", help="Не включать автозапуск")
    parser.add_argument("--skip-cursor", action="store_true", help="Не включать тему курсора")

    args = parser.parse_args()

    # If no flags passed or --gui explicitly, launch GUI
    if len(sys.argv) == 1 or args.gui:
        try:
            from app_gui import run_gui
            return run_gui()
        except Exception as e:
            print(f"Предупреждение: Не удалось запустить графический интерфейс ({e}).")
            print("Переход в режим командной строки (--help для справки).")
            parser.print_help()
            sys.exit(1)

    if args.backup:
        run_cli_backup(args)
    elif args.list:
        run_cli_list(args)
    elif args.restore:
        run_cli_restore(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
