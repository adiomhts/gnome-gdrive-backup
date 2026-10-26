#!/usr/bin/env python3
"""
GNOME Cloud Backup - GTK4 & Libadwaita Application
Modern, native GNOME desktop interface for backing up and restoring
system settings, GNOME Tweaks, extensions, SystemFiles, crontab, autostart, and cursor.
"""

import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Any

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")
from gi.repository import Gtk, Adw, Gio, GLib

from backup_engine import create_backup_archive, restore_backup, inspect_backup
from gdrive_sync import GDriveSync, format_bytes


class GnomeBackupWindow(Adw.ApplicationWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.set_title("GNOME Cloud Backup")
        self.set_default_size(860, 680)

        self.sync = GDriveSync()
        self.is_busy = False

        # Main Toast Overlay for notifications
        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)

        # Main box with header and viewstack
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toast_overlay.set_child(main_box)

        # Header Bar with Adw.ViewSwitcherTitle
        self.header_bar = Adw.HeaderBar()
        self.view_title = Adw.ViewSwitcherTitle()
        self.view_title.set_title("GNOME Cloud Backup")
        self.header_bar.set_title_widget(self.view_title)
        main_box.append(self.header_bar)

        # ViewStack for tabs
        self.stack = Adw.ViewStack()
        self.stack.set_vexpand(True)
        self.view_title.set_stack(self.stack)

        # 1. Backup Tab
        self.backup_page = self.build_backup_page()
        page1 = self.stack.add_titled(self.backup_page, "backup", "Резервное копирование")
        page1.set_icon_name("drive-cloud-symbolic")

        # 2. Restore Tab
        self.restore_page = self.build_restore_page()
        page2 = self.stack.add_titled(self.restore_page, "restore", "Восстановление")
        page2.set_icon_name("document-revert-symbolic")

        # 3. Automation Tab
        self.auto_page = self.build_automation_page()
        page3 = self.stack.add_titled(self.auto_page, "automation", "Автоматизация")
        page3.set_icon_name("alarm-symbolic")

        # Bottom Bar ViewSwitcher (visible on smaller windows / mobile)
        self.switcher_bar = Adw.ViewSwitcherBar()
        self.switcher_bar.set_stack(self.stack)
        main_box.append(self.stack)
        main_box.append(self.switcher_bar)

        # Bind switcher bar visibility
        self.view_title.connect("notify::title-visible", self.on_title_visible)

        # Initial check of Google Drive status
        GLib.idle_add(self.refresh_drive_status)
        GLib.idle_add(self.refresh_backup_list)

    def on_title_visible(self, widget, pspec):
        self.switcher_bar.set_reveal(widget.get_title_visible())

    def show_toast(self, message: str, timeout: int = 4):
        toast = Adw.Toast.new(message)
        toast.set_timeout(timeout)
        self.toast_overlay.add_toast(toast)

    # ==========================================
    # TAB 1: BACKUP PAGE
    # ==========================================
    def build_backup_page(self) -> Gtk.Widget:
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        clamp = Adw.Clamp()
        clamp.set_maximum_size(800)
        clamp.set_tightening_threshold(600)
        scrolled.set_child(clamp)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        box.set_margin_top(20)
        box.set_margin_bottom(24)
        box.set_margin_start(16)
        box.set_margin_end(16)
        clamp.set_child(box)

        # Google Drive status group
        drive_group = Adw.PreferencesGroup()
        drive_group.set_title("Облачное хранилище")
        drive_group.set_description("Синхронизация через подключенный аккаунт Google в GNOME")

        self.drive_row = Adw.ActionRow()
        self.drive_row.set_title("Google Диск")
        self.drive_row.set_subtitle("Проверка подключения...")
        self.drive_icon = Gtk.Image.new_from_icon_name("network-server-symbolic")
        self.drive_row.add_prefix(self.drive_icon)

        refresh_btn = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        refresh_btn.set_valign(Gtk.Align.CENTER)
        refresh_btn.set_tooltip_text("Обновить статус диска")
        refresh_btn.connect("clicked", lambda b: self.refresh_drive_status())
        self.drive_row.add_suffix(refresh_btn)

        open_drive_btn = Gtk.Button.new_from_icon_name("folder-open-symbolic")
        open_drive_btn.set_valign(Gtk.Align.CENTER)
        open_drive_btn.set_tooltip_text("Открыть Google Диск в Файлах")
        open_drive_btn.connect("clicked", self.on_open_drive_clicked)
        self.drive_row.add_suffix(open_drive_btn)

        drive_group.add(self.drive_row)
        box.append(drive_group)

        # Backup components group
        comp_group = Adw.PreferencesGroup()
        comp_group.set_title("Содержимое резервной копии")
        comp_group.set_description("Выберите компоненты, которые будут упакованы в архив")

        self.sw_dconf = Adw.SwitchRow()
        self.sw_dconf.set_title("Системные настройки и GNOME Tweaks")
        self.sw_dconf.set_subtitle("Темы оформления, шрифты, кнопки окон, горячие клавиши (dconf dump)")
        self.sw_dconf.set_active(True)
        comp_group.add(self.sw_dconf)

        self.sw_ext = Adw.SwitchRow()
        self.sw_ext.set_title("Расширения GNOME Shell")
        self.sw_ext.set_subtitle("Пользовательские и системные расширения с их параметрами")
        self.sw_ext.set_active(True)
        comp_group.add(self.sw_ext)

        self.sw_sysfiles = Adw.SwitchRow()
        self.sw_sysfiles.set_title("Папка ~/SystemFiles")
        self.sw_sysfiles.set_subtitle("Пользовательские скрипты и конфиги (папка asusctl исключается)")
        self.sw_sysfiles.set_active(True)
        comp_group.add(self.sw_sysfiles)

        self.sw_cron = Adw.SwitchRow()
        self.sw_cron.set_title("Расписание задач Crontab")
        self.sw_cron.set_subtitle("Все активные задачи текущего пользователя из crontab -l")
        self.sw_cron.set_active(True)
        comp_group.add(self.sw_cron)

        self.sw_autostart = Adw.SwitchRow()
        self.sw_autostart.set_title("Приложения в автозапуске")
        self.sw_autostart.set_subtitle("Конфигурации запуска из ~/.config/autostart/*.desktop")
        self.sw_autostart.set_active(True)
        comp_group.add(self.sw_autostart)

        self.sw_cursor = Adw.SwitchRow()
        self.sw_cursor.set_title("Курсор мыши и темы")
        self.sw_cursor.set_subtitle("Тема курсора из ~/.icons, размер и настройки gsettings")
        self.sw_cursor.set_active(True)
        comp_group.add(self.sw_cursor)

        box.append(comp_group)

        # Options group
        opt_group = Adw.PreferencesGroup()
        opt_group.set_title("Параметры сохранения")

        self.sw_keep_local = Adw.SwitchRow()
        self.sw_keep_local.set_title("Сохранить локальную копию в ~/Загрузки")
        self.sw_keep_local.set_subtitle("Оставить файл архива на диске в дополнение к облаку")
        self.sw_keep_local.set_active(False)
        opt_group.add(self.sw_keep_local)

        box.append(opt_group)

        # Action section
        action_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        action_box.set_margin_top(8)

        self.backup_btn = Gtk.Button(label="Создать и загрузить резервную копию")
        self.backup_btn.add_css_class("suggested-action")
        self.backup_btn.add_css_class("pill")
        self.backup_btn.set_size_request(-1, 50)
        self.backup_btn.connect("clicked", self.on_start_backup_clicked)
        action_box.append(self.backup_btn)

        # Progress bar
        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_show_text(True)
        self.progress_bar.set_text("Готов к работе")
        self.progress_bar.set_visible(False)
        action_box.append(self.progress_bar)

        box.append(action_box)

        # Log console
        log_expander = Adw.ExpanderRow()
        log_expander.set_title("Журнал операций")
        log_expander.set_subtitle("Подробный вывод процесса")
        log_expander.set_expanded(True)

        log_scroll = Gtk.ScrolledWindow()
        log_scroll.set_size_request(-1, 150)
        log_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        self.backup_log_view = Gtk.TextView()
        self.backup_log_view.set_editable(False)
        self.backup_log_view.set_monospace(True)
        self.backup_log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.backup_log_view.set_margin_start(8)
        self.backup_log_view.set_margin_end(8)
        self.backup_log_view.set_margin_top(8)
        self.backup_log_view.set_margin_bottom(8)
        log_scroll.set_child(self.backup_log_view)

        log_expander.add_row(log_scroll)
        box.append(log_expander)

        return scrolled

    # ==========================================
    # TAB 2: RESTORE PAGE
    # ==========================================
    def build_restore_page(self) -> Gtk.Widget:
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        clamp = Adw.Clamp()
        clamp.set_maximum_size(800)
        clamp.set_tightening_threshold(600)
        scrolled.set_child(clamp)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        box.set_margin_top(20)
        box.set_margin_bottom(24)
        box.set_margin_start(16)
        box.set_margin_end(16)
        clamp.set_child(box)

        # Cloud Backups list group
        self.backups_group = Adw.PreferencesGroup()
        self.backups_group.set_title("Доступные копии в Google Диске")
        self.backups_group.set_description("Папка GnomeBackups на вашем облачном диске")

        refresh_list_btn = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        refresh_list_btn.set_tooltip_text("Обновить список архивов")
        refresh_list_btn.connect("clicked", lambda b: self.refresh_backup_list())
        self.backups_group.set_header_suffix(refresh_list_btn)

        # Placeholder row when empty
        self.empty_backups_row = Adw.ActionRow()
        self.empty_backups_row.set_title("Поиск резервных копий...")
        self.empty_backups_row.set_subtitle("Пожалуйста, подождите или создайте новую копию")
        self.backups_group.add(self.empty_backups_row)

        box.append(self.backups_group)

        # Local file restore group
        local_group = Adw.PreferencesGroup()
        local_group.set_title("Локальный файл архива")
        local_group.set_description("Восстановление из ранее сохраненного файла на компьютере")

        local_row = Adw.ActionRow()
        local_row.set_title("Выбрать файл .tar.gz...")
        local_row.set_subtitle("Восстановить настройки без подключения к интернету")
        local_icon = Gtk.Image.new_from_icon_name("package-x-generic-symbolic")
        local_row.add_prefix(local_icon)

        browse_btn = Gtk.Button(label="Обзор...")
        browse_btn.set_valign(Gtk.Align.CENTER)
        browse_btn.connect("clicked", self.on_browse_local_file_clicked)
        local_row.add_suffix(browse_btn)

        local_group.add(local_row)
        box.append(local_group)

        # Restore Progress & Log
        self.restore_progress_bar = Gtk.ProgressBar()
        self.restore_progress_bar.set_show_text(True)
        self.restore_progress_bar.set_visible(False)
        box.append(self.restore_progress_bar)

        restore_log_expander = Adw.ExpanderRow()
        restore_log_expander.set_title("Журнал восстановления")
        restore_log_expander.set_subtitle("Лог процесса распаковки и применения")
        restore_log_expander.set_expanded(True)

        restore_log_scroll = Gtk.ScrolledWindow()
        restore_log_scroll.set_size_request(-1, 150)
        restore_log_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        self.restore_log_view = Gtk.TextView()
        self.restore_log_view.set_editable(False)
        self.restore_log_view.set_monospace(True)
        self.restore_log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.restore_log_view.set_margin_start(8)
        self.restore_log_view.set_margin_end(8)
        self.restore_log_view.set_margin_top(8)
        self.restore_log_view.set_margin_bottom(8)
        restore_log_scroll.set_child(self.restore_log_view)

        restore_log_expander.add_row(restore_log_scroll)
        box.append(restore_log_expander)

        return scrolled

    # ==========================================
    # TAB 3: AUTOMATION PAGE
    # ==========================================
    def build_automation_page(self) -> Gtk.Widget:
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        clamp = Adw.Clamp()
        clamp.set_maximum_size(800)
        clamp.set_tightening_threshold(600)
        scrolled.set_child(clamp)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        box.set_margin_top(20)
        box.set_margin_bottom(24)
        box.set_margin_start(16)
        box.set_margin_end(16)
        clamp.set_child(box)

        cron_group = Adw.PreferencesGroup()
        cron_group.set_title("Автоматическое расписание (Crontab)")
        cron_group.set_description("Регулярное автоматическое сохранение настроек на Google Диск")

        self.cron_status_row = Adw.ActionRow()
        self.cron_status_row.set_title("Текущее расписание")
        self.cron_status_row.set_subtitle("Проверка crontab...")
        cron_icon = Gtk.Image.new_from_icon_name("alarm-symbolic")
        self.cron_status_row.add_prefix(cron_icon)
        cron_group.add(self.cron_status_row)

        sched_daily_row = Adw.ActionRow()
        sched_daily_row.set_title("Ежедневный автобэкап в 21:00")
        sched_daily_row.set_subtitle("Автоматически создаст копию и загрузит на Google Диск каждый вечер")
        enable_daily_btn = Gtk.Button(label="Установить")
        enable_daily_btn.set_valign(Gtk.Align.CENTER)
        enable_daily_btn.connect("clicked", lambda b: self.set_cron_schedule("0 21 * * *"))
        sched_daily_row.add_suffix(enable_daily_btn)
        cron_group.add(sched_daily_row)

        sched_weekly_row = Adw.ActionRow()
        sched_weekly_row.set_title("Еженедельный автобэкап (Воскресенье, 20:00)")
        sched_weekly_row.set_subtitle("Создание копии раз в неделю")
        enable_weekly_btn = Gtk.Button(label="Установить")
        enable_weekly_btn.set_valign(Gtk.Align.CENTER)
        enable_weekly_btn.connect("clicked", lambda b: self.set_cron_schedule("0 20 * * 0"))
        sched_weekly_row.add_suffix(enable_weekly_btn)
        cron_group.add(sched_weekly_row)

        disable_cron_row = Adw.ActionRow()
        disable_cron_row.set_title("Отключить автоматическое расписание")
        disable_cron_row.set_subtitle("Удалить задачу автобэкапа из crontab")
        disable_btn = Gtk.Button(label="Отключить")
        disable_btn.add_css_class("destructive-action")
        disable_btn.set_valign(Gtk.Align.CENTER)
        disable_btn.connect("clicked", lambda b: self.remove_cron_schedule())
        disable_cron_row.add_suffix(disable_btn)
        cron_group.add(disable_cron_row)

        box.append(cron_group)

        cli_group = Adw.PreferencesGroup()
        cli_group.set_title("Использование из терминала (CLI)")
        cli_group.set_description("Скрипт можно запускать из консоли или собственных сценариев:")

        cli_cmd_row = Adw.ActionRow()
        cli_cmd_row.set_title("gnome-gdrive-backup --backup")
        cli_cmd_row.set_subtitle("Создает резервную копию и выгружает ее на Google Диск без графического интерфейса")
        copy_cli_btn = Gtk.Button.new_from_icon_name("edit-copy-symbolic")
        copy_cli_btn.set_valign(Gtk.Align.CENTER)
        copy_cli_btn.set_tooltip_text("Скопировать команду")
        copy_cli_btn.connect("clicked", lambda b: self.copy_to_clipboard("gnome-gdrive-backup --backup"))
        cli_cmd_row.add_suffix(copy_cli_btn)
        cli_group.add(cli_cmd_row)

        box.append(cli_group)

        GLib.idle_add(self.refresh_cron_status)
        return scrolled

    # ==========================================
    # LOGGING & PROGRESS HELPERS
    # ==========================================
    def append_log(self, text_view: Gtk.TextView, msg: str):
        buf = text_view.get_buffer()
        end_iter = buf.get_end_iter()
        buf.insert(end_iter, msg + "\n")
        # Scroll to bottom
        mark = buf.create_mark(None, buf.get_end_iter(), False)
        text_view.scroll_to_mark(mark, 0.05, True, 0.0, 1.0)

    def log_backup(self, msg: str):
        GLib.idle_add(self.append_log, self.backup_log_view, msg)

    def log_restore(self, msg: str):
        GLib.idle_add(self.append_log, self.restore_log_view, msg)

    def set_backup_progress(self, current: int, total: int):
        fraction = current / max(total, 1)
        cur_str = format_bytes(current)
        tot_str = format_bytes(total)
        pct = int(fraction * 100)
        GLib.idle_add(self._update_backup_progress_ui, fraction, f"Загрузка: {cur_str} / {tot_str} ({pct}%)")

    def _update_backup_progress_ui(self, fraction: float, text: str):
        self.progress_bar.set_fraction(fraction)
        self.progress_bar.set_text(text)

    def set_restore_progress(self, current: int, total: int):
        fraction = current / max(total, 1)
        cur_str = format_bytes(current)
        tot_str = format_bytes(total)
        pct = int(fraction * 100)
        GLib.idle_add(self._update_restore_progress_ui, fraction, f"Скачивание: {cur_str} / {tot_str} ({pct}%)")

    def _update_restore_progress_ui(self, fraction: float, text: str):
        self.restore_progress_bar.set_fraction(fraction)
        self.restore_progress_bar.set_text(text)

    # ==========================================
    # GOOGLE DRIVE & LIST LOGIC
    # ==========================================
    def refresh_drive_status(self):
        def _bg():
            accounts = self.sync.get_accounts()
            GLib.idle_add(self._update_drive_status_ui, accounts)
        threading.Thread(target=_bg, daemon=True).start()

    def _update_drive_status_ui(self, accounts):
        if accounts:
            acc = accounts[0]
            self.drive_row.set_subtitle(f"Подключен: {acc['email']}")
            self.drive_icon.set_from_icon_name("network-server-symbolic")
            self.drive_row.remove_css_class("error")
        else:
            self.drive_row.set_subtitle("Не подключен! Добавьте Google аккаунт в Параметрах GNOME")
            self.drive_icon.set_from_icon_name("dialog-warning-symbolic")

    def on_open_drive_clicked(self, btn):
        accounts = self.sync.get_accounts()
        if accounts:
            uri = accounts[0]["uri"]
            subprocess.run(["gio", "open", uri], check=False)
        else:
            subprocess.run(["gnome-control-center", "online-accounts"], check=False)

    def refresh_backup_list(self):
        self.empty_backups_row.set_title("Поиск резервных копий...")
        self.empty_backups_row.set_subtitle("Сканирование папки GnomeBackups в Google Диске...")

        def _bg():
            backups = self.sync.list_backups()
            GLib.idle_add(self._render_backup_list_ui, backups)
        threading.Thread(target=_bg, daemon=True).start()

    def _render_backup_list_ui(self, backups):
        # Remove previously added backup rows
        # We keep track of added rows
        if hasattr(self, "_active_backup_rows"):
            for row in self._active_backup_rows:
                self.backups_group.remove(row)
        self._active_backup_rows = []

        if not backups:
            self.empty_backups_row.set_visible(True)
            self.empty_backups_row.set_title("Резервные копии не найдены")
            self.empty_backups_row.set_subtitle("Перейдите на вкладку 'Резервное копирование', чтобы создать первую копию")
            return

        self.empty_backups_row.set_visible(False)

        for b in backups:
            row = Adw.ActionRow()
            row.set_title(b["display_name"])
            row.set_subtitle(f"Дата: {b['date_str']}  •  Размер: {b['size_str']}")

            icon = Gtk.Image.new_from_icon_name("package-x-generic-symbolic")
            row.add_prefix(icon)

            # Restore button
            restore_btn = Gtk.Button(label="Восстановить")
            restore_btn.add_css_class("suggested-action")
            restore_btn.set_valign(Gtk.Align.CENTER)
            restore_btn.connect("clicked", lambda btn, item=b: self.confirm_and_restore(item))
            row.add_suffix(restore_btn)

            # Delete button
            del_btn = Gtk.Button.new_from_icon_name("user-trash-symbolic")
            del_btn.add_css_class("flat")
            del_btn.set_valign(Gtk.Align.CENTER)
            del_btn.set_tooltip_text("Удалить копию с Google Диска")
            del_btn.connect("clicked", lambda btn, item=b: self.confirm_and_delete(item))
            row.add_suffix(del_btn)

            self.backups_group.add(row)
            self._active_backup_rows.append(row)

    # ==========================================
    # BACKUP EXECUTION
    # ==========================================
    def on_start_backup_clicked(self, btn):
        if self.is_busy:
            return

        options = {
            "dconf": self.sw_dconf.get_active(),
            "extensions": self.sw_ext.get_active(),
            "system_files": self.sw_sysfiles.get_active(),
            "crontab": self.sw_cron.get_active(),
            "autostart": self.sw_autostart.get_active(),
            "cursor": self.sw_cursor.get_active(),
        }

        if not any(options.values()):
            self.show_toast("Выберите хотя бы один компонент для сохранения!")
            return

        if not self.sync.is_connected():
            self.show_toast("Google Диск не подключен в GNOME!", timeout=5)
            self.log_backup("⚠ Google Диск не обнаружен. Откройте 'Параметры GNOME -> Сетевые учетные записи'.")
            return

        self.is_busy = True
        self.backup_btn.set_sensitive(False)
        self.progress_bar.set_visible(True)
        self.progress_bar.set_fraction(0.0)
        self.progress_bar.set_text("Подготовка архива...")

        keep_local = self.sw_keep_local.get_active()

        def _worker():
            try:
                now_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                filename = f"gnome-backup-{now_str}.tar.gz"
                tmp_archive = f"/tmp/{filename}"

                # 1. Create local archive
                self.log_backup(f"--- Создание новой резервной копии ({filename}) ---")
                create_backup_archive(tmp_archive, options=options, log_callback=self.log_backup)

                # 2. Upload to Google Drive
                GLib.idle_add(self._update_backup_progress_ui, 0.05, "Загрузка на Google Диск...")
                self.sync.upload_backup(
                    tmp_archive,
                    progress_callback=self.set_backup_progress,
                    log_callback=self.log_backup,
                )

                # 3. Optional copy to ~/Downloads
                if keep_local:
                    downloads_dir = Path.home() / "Загрузки"
                    if not downloads_dir.exists():
                        downloads_dir = Path.home() / "Downloads"
                    downloads_dir.mkdir(parents=True, exist_ok=True)
                    dest_local = downloads_dir / filename
                    import shutil
                    shutil.copy2(tmp_archive, dest_local)
                    self.log_backup(f"✓ Локальная копия сохранена в: {dest_local}")

                # 4. Clean up /tmp
                if os.path.exists(tmp_archive):
                    os.remove(tmp_archive)

                self.log_backup("🎉 Все операции завершены успешно!")
                GLib.idle_add(self._backup_finished, True, "Резервная копия успешно загружена в облако!")
            except Exception as e:
                self.log_backup(f"❌ Ошибка создания резервной копии: {e}")
                GLib.idle_add(self._backup_finished, False, f"Ошибка: {e}")

        threading.Thread(target=_worker, daemon=True).start()

    def _backup_finished(self, success: bool, message: str):
        self.is_busy = False
        self.backup_btn.set_sensitive(True)
        self.progress_bar.set_visible(False)
        self.show_toast(message)
        if success:
            self.refresh_backup_list()

    # ==========================================
    # RESTORE EXECUTION
    # ==========================================
    def confirm_and_restore(self, backup_item: Dict[str, Any]):
        dialog = Adw.MessageDialog.new(
            self,
            "Восстановить систему из резервной копии?",
            f"Будут применены настройки из архива '{backup_item['display_name']}' "
            f"от {backup_item['date_str']}.\n\n"
            f"Это обновит dconf/gnome tweaks, расширения, autostart, crontab, "
            f"курсор и файлы в ~/SystemFiles (папка asusctl будет сохранена).",
        )
        dialog.add_response("cancel", "Отмена")
        dialog.add_response("restore", "Восстановить")
        dialog.set_response_appearance("restore", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel")

        dialog.connect("response", lambda d, resp: self._on_confirm_restore_response(resp, backup_item))
        dialog.present()

    def _on_confirm_restore_response(self, response: str, backup_item: Dict[str, Any]):
        if response != "restore" or self.is_busy:
            return

        self.is_busy = True
        self.restore_progress_bar.set_visible(True)
        self.restore_progress_bar.set_fraction(0.0)
        self.restore_progress_bar.set_text("Скачивание архива из облака...")

        def _worker():
            try:
                local_tmp = f"/tmp/{backup_item['display_name']}"
                self.log_restore(f"--- Начало восстановления из {backup_item['display_name']} ---")

                # Download from GDrive
                self.sync.download_backup(
                    backup_item,
                    local_tmp,
                    progress_callback=self.set_restore_progress,
                    log_callback=self.log_restore,
                )

                # Restore
                GLib.idle_add(self._update_restore_progress_ui, 0.95, "Применение настроек...")
                restore_backup(local_tmp, log_callback=self.log_restore)

                # Clean up /tmp
                if os.path.exists(local_tmp):
                    os.remove(local_tmp)

                GLib.idle_add(self._restore_finished, True, "Настройки успешно восстановлены и применены!")
            except Exception as e:
                self.log_restore(f"❌ Ошибка восстановления: {e}")
                GLib.idle_add(self._restore_finished, False, f"Ошибка: {e}")

        threading.Thread(target=_worker, daemon=True).start()

    def _restore_finished(self, success: bool, message: str):
        self.is_busy = False
        self.restore_progress_bar.set_visible(False)
        self.show_toast(message, timeout=6)

    def confirm_and_delete(self, backup_item: Dict[str, Any]):
        dialog = Adw.MessageDialog.new(
            self,
            "Удалить резервную копию?",
            f"Вы уверены, что хотите удалить '{backup_item['display_name']}' с Google Диска?",
        )
        dialog.add_response("cancel", "Отмена")
        dialog.add_response("delete", "Удалить")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")

        def _on_resp(d, resp):
            if resp == "delete":
                def _bg():
                    try:
                        self.sync.delete_backup(backup_item)
                        GLib.idle_add(self.show_toast, "Резервная копия удалена")
                        GLib.idle_add(self.refresh_backup_list)
                    except Exception as e:
                        GLib.idle_add(self.show_toast, f"Ошибка удаления: {e}")
                threading.Thread(target=_bg, daemon=True).start()

        dialog.connect("response", _on_resp)
        dialog.present()

    def on_browse_local_file_clicked(self, btn):
        dialog = Gtk.FileDialog.new()
        dialog.set_title("Выберите файл резервной копии (.tar.gz)")

        filter_tar = Gtk.FileFilter.new()
        filter_tar.set_name("Архивы бэкапа (*.tar.gz, *.tgz)")
        filter_tar.add_pattern("*.tar.gz")
        filter_tar.add_pattern("*.tgz")

        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filter_tar)
        dialog.set_filters(filters)

        def _on_file_selected(d, res):
            try:
                gfile = d.open_finish(res)
                if gfile:
                    path = gfile.get_path()
                    if path:
                        self.confirm_and_restore_local(path)
            except Exception as e:
                pass

        dialog.open(self, None, _on_file_selected)

    def confirm_and_restore_local(self, file_path: str):
        filename = os.path.basename(file_path)
        dialog = Adw.MessageDialog.new(
            self,
            "Восстановить из локального файла?",
            f"Будут применены настройки из файла:\n{file_path}\n\n"
            f"Все системные настройки dconf, расширения, курсор и SystemFiles будут обновлены.",
        )
        dialog.add_response("cancel", "Отмена")
        dialog.add_response("restore", "Восстановить")
        dialog.set_response_appearance("restore", Adw.ResponseAppearance.SUGGESTED)

        def _resp(d, resp):
            if resp == "restore":
                self.is_busy = True
                self.restore_progress_bar.set_visible(True)
                self.restore_progress_bar.set_fraction(0.5)
                self.restore_progress_bar.set_text("Применение настроек...")

                def _worker():
                    try:
                        self.log_restore(f"--- Начало восстановления из локального файла {filename} ---")
                        restore_backup(file_path, log_callback=self.log_restore)
                        GLib.idle_add(self._restore_finished, True, "Настройки успешно восстановлены!")
                    except Exception as e:
                        self.log_restore(f"❌ Ошибка восстановления: {e}")
                        GLib.idle_add(self._restore_finished, False, f"Ошибка: {e}")

                threading.Thread(target=_worker, daemon=True).start()

        dialog.connect("response", _resp)
        dialog.present()

    # ==========================================
    # AUTOMATION / CRONTAB HELPERS
    # ==========================================
    def refresh_cron_status(self):
        res = subprocess.run(["crontab", "-l"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        lines = res.stdout.splitlines() if res.stdout else []
        backup_lines = [l for l in lines if "gnome-gdrive-backup" in l]
        if backup_lines:
            self.cron_status_row.set_subtitle(f"Активно: {backup_lines[0]}")
        else:
            self.cron_status_row.set_subtitle("Автоматическое расписание не настроено")

    def set_cron_schedule(self, cron_expr: str):
        res = subprocess.run(["crontab", "-l"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        lines = res.stdout.splitlines() if res.stdout else []
        # Filter out existing backup entries
        new_lines = [l for l in lines if "gnome-gdrive-backup" not in l]
        # Command to run
        cmd = f"{cron_expr} $HOME/.local/bin/gnome-gdrive-backup --backup >/dev/null 2>&1"
        new_lines.append(cmd)

        new_crontab = "\n".join(new_lines) + "\n"
        proc = subprocess.run(["crontab", "-"], input=new_crontab, text=True)
        if proc.returncode == 0:
            self.show_toast("Расписание crontab успешно обновлено!")
        else:
            self.show_toast("Не удалось обновить crontab")
        self.refresh_cron_status()

    def remove_cron_schedule(self):
        res = subprocess.run(["crontab", "-l"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        lines = res.stdout.splitlines() if res.stdout else []
        new_lines = [l for l in lines if "gnome-gdrive-backup" not in l]
        new_crontab = "\n".join(new_lines) + "\n"
        subprocess.run(["crontab", "-"], input=new_crontab, text=True)
        self.show_toast("Автоматическое расписание отключено")
        self.refresh_cron_status()

    def copy_to_clipboard(self, text: str):
        clipboard = self.get_display().get_clipboard()
        clipboard.set(text)
        self.show_toast("Команда скопирована в буфер обмена")


class GnomeBackupApplication(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="org.gnome.GnomeGDriveBackup",
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = GnomeBackupWindow(application=self)
        win.present()


def run_gui():
    app = GnomeBackupApplication()
    return app.run(None)


if __name__ == "__main__":
    run_gui()
