#!/usr/bin/python3
"""Compact Ubuntu/GNOME indicator using the existing thermal telemetry."""
import importlib.util
from pathlib import Path
import signal
import subprocess

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('AyatanaAppIndicator3', '0.1')
from gi.repository import Gtk, GLib, AyatanaAppIndicator3 as AppIndicator

BASE = Path.home() / '.local/lib/thermal-monitor'
spec = importlib.util.spec_from_file_location('desktop_telemetry', BASE / 'desktop-widget.py')
telemetry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(telemetry)


class Monitor:
    def __init__(self):
        telemetry.DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.previous_cpu = None
        self.indicator = AppIndicator.Indicator.new(
            'thermal-monitor', 'utilities-system-monitor-symbolic',
            AppIndicator.IndicatorCategory.HARDWARE)
        self.indicator.set_title('Thermal Monitor')
        self.menu = Gtk.Menu()
        self.rows = {}
        for key in ['cpu', 'cpu_temp', 'gpu', 'gpu_temp', 'extra', 'fans', 'ssd1', 'ssd2', 'ram']:
            item = Gtk.MenuItem(label='Reading sensors…')
            self.menu.append(item)
            self.rows[key] = item
        self.menu.append(Gtk.SeparatorMenuItem())
        self.action('Show detailed graphs', lambda: self.graphs('start'))
        self.action('Hide detailed graphs', lambda: self.graphs('stop'))
        self.action('Open 7-day log folder', self.open_logs)
        self.menu.append(Gtk.SeparatorMenuItem())
        self.action('Quit top-bar monitor', Gtk.main_quit)
        self.menu.show_all()
        self.indicator.set_menu(self.menu)
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.update()
        GLib.timeout_add_seconds(5, self.update)

    def action(self, title, callback):
        item = Gtk.MenuItem(label=title)
        item.connect('activate', lambda _item: callback())
        self.menu.append(item)

    @staticmethod
    def graphs(action):
        subprocess.Popen(['/usr/bin/systemctl', '--user', action, 'thermal-desktop.service'])

    @staticmethod
    def open_logs():
        subprocess.Popen(['/usr/bin/xdg-open', str(Path.home() / '.local/state/thermal-monitor')])

    @staticmethod
    def value(key):
        try:
            return (telemetry.DATA / key).read_text().strip()
        except OSError:
            return 'N/A'

    def cpu_load(self):
        try:
            fields = [int(n) for n in Path('/proc/stat').read_text().splitlines()[0].split()[1:9]]
            current = (sum(fields), fields[3] + fields[4])
            previous, self.previous_cpu = self.previous_cpu, current
            if previous is not None and current[0] > previous[0]:
                return f'{100 * (1 - (current[1] - previous[1]) / (current[0] - previous[0])):.0f}%'
        except (OSError, ValueError, IndexError):
            pass
        return '…'

    @staticmethod
    def memory_usage():
        try:
            fields = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
            total = int(fields['MemTotal'].split()[0])
            used = total - int(fields['MemAvailable'].split()[0])
            return f'RAM: {100 * used / total:.0f}% ({used / 1048576:.1f} / {total / 1048576:.1f} GiB)'
        except (OSError, ValueError, KeyError):
            return 'RAM: N/A'

    def update(self):
        try:
            telemetry.sample()
            cpu = self.cpu_load()
            cpu_temp = self.value('cpu_label')
            gpu_values = self.value('gpu_label').split()
            gpu = gpu_values[0] if gpu_values else 'N/A'
            gpu_temp = gpu_values[-1] if len(gpu_values) >= 2 else 'N/A'
            text = f'CPU {cpu} {cpu_temp.replace("°C", "°")} · GPU {gpu} {gpu_temp.replace("°C", "°")}'
            self.indicator.set_label(text, 'CPU 100% 100° · GPU 100% 100°')
            labels = {
                'cpu': 'CPU utilization: ' + cpu,
                'cpu_temp': 'CPU temperature: ' + cpu_temp,
                'gpu': 'GPU utilization: ' + gpu,
                'gpu_temp': 'GPU temperature: ' + gpu_temp,
                'extra': self.value('extra'),
                'fans': self.value('fans'),
                'ssd1': self.value('ssd1'),
                'ssd2': self.value('ssd2'),
                'ram': self.memory_usage(),
            }
            for key, label in labels.items():
                self.rows[key].set_label(label)
        except Exception as error:
            self.indicator.set_label('Thermal monitor: N/A', '')
            print('Sensor update failed:', error, flush=True)
        return GLib.SOURCE_CONTINUE


if __name__ == '__main__':
    monitor = Monitor()
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, lambda: Gtk.main_quit())
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, lambda: Gtk.main_quit())
    Gtk.main()
