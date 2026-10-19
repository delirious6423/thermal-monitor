//! Bounded, failure-tolerant reads of Linux sensors and existing GPU telemetry.
use std::{
    fs::{self, File},
    io::{Read, Seek, SeekFrom},
    path::{Path, PathBuf},
};

use chrono::DateTime;
use serde::{Deserialize, Serialize};
use thiserror::Error;

const GPU_MAX_AGE_MS: i64 = 15_000;
const LOG_MAX_AGE_MS: i64 = 20_000;
const MAX_JSON_BYTES: u64 = 65_536;
const LOG_TAIL_BYTES: u64 = 16_384;

/// A malformed or inconsistent kernel statistics record.
#[derive(Debug, Error, PartialEq)]
pub enum ParseError {
    /// CPU totals are missing, malformed, or overflowed.
    #[error("invalid aggregate CPU statistics")]
    Cpu,
    /// Total or available memory is missing or inconsistent.
    #[error("invalid memory statistics")]
    Memory,
}

/// CPU counters with guest time excluded from the total to avoid double counting.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct CpuTicks {
    total: u64,
    idle: u64,
}

impl CpuTicks {
    /// Parse the aggregate `cpu` row from Linux `/proc/stat`.
    pub fn parse(text: &str) -> Result<Self, ParseError> {
        let mut words = text
            .lines()
            .next()
            .ok_or(ParseError::Cpu)?
            .split_whitespace();
        if words.next() != Some("cpu") {
            return Err(ParseError::Cpu);
        }
        let counters = words
            .take(8)
            .map(str::parse::<u64>)
            .collect::<Result<Vec<_>, _>>()
            .map_err(|_| ParseError::Cpu)?;
        if counters.len() < 4 {
            return Err(ParseError::Cpu);
        }
        let total = counters
            .iter()
            .try_fold(0_u64, |sum, n| sum.checked_add(*n))
            .ok_or(ParseError::Cpu)?;
        let idle = counters[3]
            .checked_add(counters.get(4).copied().unwrap_or(0))
            .ok_or(ParseError::Cpu)?;
        Ok(Self { total, idle })
    }

    /// Calculate a percentage, returning no reading after a reset or empty interval.
    pub fn percent_since(self, before: Self) -> Option<f64> {
        let total = self.total.checked_sub(before.total)?;
        let idle = self.idle.checked_sub(before.idle)?;
        let busy = total.checked_sub(idle)?;
        (total > 0).then(|| 100.0 * busy as f64 / total as f64)
    }
}

/// Memory quantities in KiB, matching `/proc/meminfo`.
#[derive(Clone, Debug, Serialize)]
pub struct Memory {
    /// Installed memory visible to the operating system.
    pub total_kib: u64,
    /// Total less MemAvailable; includes neither reclaimable caches nor buffers.
    pub used_kib: u64,
}

impl Memory {
    /// Parse total and available memory without treating malformed data as zero.
    pub fn parse(text: &str) -> Result<Self, ParseError> {
        let get = |key: &str| -> Option<u64> {
            let line = text.lines().find(|line| line.starts_with(key))?;
            let mut fields = line.split_whitespace();
            fields.next()?;
            let value = fields.next()?.parse().ok()?;
            (fields.next() == Some("kB")).then_some(value)
        };
        let total = get("MemTotal:").ok_or(ParseError::Memory)?;
        let available = get("MemAvailable:").ok_or(ParseError::Memory)?;
        if total == 0 || available > total {
            return Err(ParseError::Memory);
        }
        Ok(Self {
            total_kib: total,
            used_kib: total - available,
        })
    }
}

/// A named SSD composite temperature.
#[derive(Clone, Debug, Serialize)]
pub struct Disk {
    /// Hardware model when available; otherwise the hwmon identifier.
    pub name: String,
    /// Degrees Celsius; missing sensors remain absent.
    pub celsius: Option<f64>,
}

/// Dedicated GPU memory allocation, in the MiB reported by the NVIDIA driver.
#[derive(Clone, Debug, Serialize)]
pub struct GpuMemory {
    /// Allocated dedicated memory, including driver allocations.
    pub used_mib: f64,
    /// Total dedicated memory available to the GPU.
    pub total_mib: f64,
}

impl GpuMemory {
    fn parse(used: Option<f64>, total: Option<f64>) -> Option<Self> {
        let (used, total) = (used?, total?);
        (used.is_finite() && total.is_finite() && used >= 0.0 && total > 0.0 && used <= total)
            .then_some(Self {
                used_mib: used,
                total_mib: total,
            })
    }

    /// Human-readable allocation; percentage describes capacity, not bandwidth.
    pub fn label(&self) -> String {
        format!(
            "{:.0}% · {:.1} / {:.1} GiB",
            100.0 * self.used_mib / self.total_mib,
            self.used_mib / 1024.0,
            self.total_mib / 1024.0
        )
    }
}

/// One complete sampling result. Optional values are never displayed as live zero.
#[derive(Clone, Debug, Default, Serialize)]
pub struct Snapshot {
    /// CPU utilization over the previous sample interval.
    pub cpu_percent: Option<f64>,
    /// CPU package temperature in Celsius.
    pub cpu_celsius: Option<f64>,
    /// Average CPU package power from the read-only bridge, in watts.
    pub cpu_watts: Option<f64>,
    /// NVIDIA utilization from the existing logger.
    pub gpu_percent: Option<f64>,
    /// GPU power reported by the NVIDIA driver, in watts.
    pub gpu_watts: Option<f64>,
    /// Dedicated GPU memory allocation from the existing logger.
    pub gpu_memory: Option<GpuMemory>,
    /// GPU core temperature from the read-only bridge.
    pub gpu_celsius: Option<f64>,
    /// Experimental GPU hotspot temperature.
    pub hotspot_celsius: Option<f64>,
    /// Experimental VRAM temperature.
    pub vram_celsius: Option<f64>,
    /// Available fan RPM readings; absent fan readings remain absent.
    pub fan_rpm: Vec<Option<f64>>,
    /// All discovered NVMe composite temperature sensors.
    pub disks: Vec<Disk>,
    /// Memory utilization, if the kernel data is valid.
    pub memory: Option<Memory>,
}

/// Render an optional reading with the requested suffix.
pub fn reading(value: Option<f64>, suffix: &str) -> String {
    value.map_or_else(|| "N/A".into(), |v| format!("{v:.0}{suffix}"))
}

impl Snapshot {
    /// The compact label shown in the top bar.
    pub fn panel_label(&self) -> String {
        format!(
            "CPU {} {} {} · GPU {} {} {}",
            reading(self.cpu_percent, "%"),
            reading(self.cpu_celsius, "°"),
            reading(self.cpu_watts, "W"),
            reading(self.gpu_percent, "%"),
            reading(self.gpu_celsius, "°"),
            reading(self.gpu_watts, "W")
        )
    }

    /// Four primary readings, visible immediately when the menu opens.
    pub fn overview(&self) -> Vec<String> {
        vec![
            format!(
                "CPU   {} · {} · {}",
                reading(self.cpu_percent, "%"),
                reading(self.cpu_celsius, "°C"),
                reading(self.cpu_watts, " W")
            ),
            format!(
                "GPU   {} · {} · {}",
                reading(self.gpu_percent, "%"),
                reading(self.gpu_celsius, "°C"),
                reading(self.gpu_watts, " W")
            ),
            format!(
                "VRAM   {}",
                self.gpu_memory
                    .as_ref()
                    .map_or_else(|| "N/A".into(), GpuMemory::label)
            ),
            self.memory.as_ref().map_or_else(
                || "RAM   N/A".into(),
                |m| {
                    format!(
                        "RAM   {:.0}% · {:.1} / {:.1} GiB",
                        100.0 * m.used_kib as f64 / m.total_kib as f64,
                        m.used_kib as f64 / 1_048_576.0,
                        m.total_kib as f64 / 1_048_576.0
                    )
                },
            ),
        ]
    }

    /// Additional sensors, kept together below the main overview.
    pub fn details(&self) -> Vec<String> {
        let mut rows = vec![
            format!("GPU hotspot   {}", reading(self.hotspot_celsius, "°C")),
            format!("VRAM temperature   {}", reading(self.vram_celsius, "°C")),
            format!(
                "Fans   {}",
                if self.fan_rpm.is_empty() {
                    "N/A".into()
                } else {
                    self.fan_rpm
                        .iter()
                        .map(|v| reading(*v, " RPM"))
                        .collect::<Vec<_>>()
                        .join(" / ")
                }
            ),
        ];
        rows.extend(
            self.disks
                .iter()
                .map(|disk| format!("{}: {}", disk.name, reading(disk.celsius, "°C"))),
        );
        rows
    }
}

#[derive(Deserialize)]
struct GpuFrame {
    timestamp: i64,
    cpu_package_watts: Option<f64>,
    gpus: Vec<GpuTemperatures>,
}

#[derive(Deserialize)]
struct GpuTemperatures {
    index: u32,
    core: Option<f64>,
    junction: Option<f64>,
    vram: Option<f64>,
}

fn fresh(timestamp: i64, now: i64, maximum: i64) -> bool {
    now.checked_sub(timestamp)
        .is_some_and(|age| (0..=maximum).contains(&age))
}

fn valid_temperature(value: Option<f64>) -> Option<f64> {
    value.filter(|value| value.is_finite() && (-40.0..=200.0).contains(value))
}

fn valid_power(value: Option<f64>) -> Option<f64> {
    value.filter(|value| value.is_finite() && *value >= 0.0)
}

fn read_text(path: &Path) -> Option<String> {
    let mut text = String::new();
    File::open(path)
        .ok()?
        .take(MAX_JSON_BYTES)
        .read_to_string(&mut text)
        .ok()?;
    Some(text)
}

fn read_number(path: &Path) -> Option<f64> {
    read_text(path)?
        .trim()
        .parse::<f64>()
        .ok()
        .filter(|n| n.is_finite())
}

#[derive(Default)]
struct GpuLogSample {
    percent: Option<f64>,
    watts: Option<f64>,
    memory: Option<GpuMemory>,
}

fn log_sample(text: &str, now: i64) -> Option<GpuLogSample> {
    for line in text.lines().rev() {
        let Ok(record) = serde_json::from_str::<serde_json::Value>(line) else {
            continue;
        };
        if record.get("type").and_then(|v| v.as_str()) != Some("sample") {
            continue;
        }
        let timestamp = DateTime::parse_from_rfc3339(record.get("timestamp")?.as_str()?)
            .ok()?
            .timestamp_millis();
        if !fresh(timestamp, now, LOG_MAX_AGE_MS) {
            return None;
        }
        let gpu = record
            .get("gpus")?
            .as_array()?
            .iter()
            .find(|gpu| gpu.get("index").and_then(|v| v.as_str()) == Some("0"))?;
        let number = |key: &str| {
            gpu.get(key).and_then(|v| {
                v.as_f64()
                    .or_else(|| v.as_str().and_then(|s| s.parse::<f64>().ok()))
            })
        };
        return Some(GpuLogSample {
            percent: number("utilization.gpu")
                .filter(|v| v.is_finite() && (0.0..=100.0).contains(v)),
            memory: GpuMemory::parse(number("memory.used"), number("memory.total")),
            watts: valid_power(number("power.draw")),
        });
    }
    None
}

/// Stateful sampler. `root` redirects only telemetry reads, enabling VM fixtures.
pub struct Sampler {
    root: PathBuf,
    home: PathBuf,
    previous_cpu: Option<CpuTicks>,
}

impl Sampler {
    /// Construct a sampler without touching files or changing system settings.
    pub fn new(root: PathBuf, home: PathBuf) -> Self {
        Self {
            root,
            home,
            previous_cpu: None,
        }
    }

    /// Read one snapshot. Errors in one sensor never suppress the others.
    pub fn sample(&mut self, now_ms: i64) -> Snapshot {
        let mut result = Snapshot::default();
        let current =
            read_text(&self.root.join("proc/stat")).and_then(|s| CpuTicks::parse(&s).ok());
        result.cpu_percent = current
            .zip(self.previous_cpu)
            .and_then(|(now, old)| now.percent_since(old));
        self.previous_cpu = current;
        result.memory =
            read_text(&self.root.join("proc/meminfo")).and_then(|s| Memory::parse(&s).ok());
        if let Ok(sensors) = fs::read_dir(self.root.join("sys/class/hwmon")) {
            let mut sensors = sensors
                .filter_map(Result::ok)
                .map(|e| e.path())
                .collect::<Vec<_>>();
            sensors.sort();
            for sensor in sensors.into_iter().take(128) {
                let Some(name) = read_text(&sensor.join("name")) else {
                    continue;
                };
                let temp =
                    valid_temperature(read_number(&sensor.join("temp1_input")).map(|v| v / 1000.0));
                match name.trim() {
                    "coretemp" | "k10temp" => {
                        if result.cpu_celsius.is_none() {
                            result.cpu_celsius = temp;
                        }
                    }
                    "hp" => result.fan_rpm.extend((1..=2).map(|i| {
                        read_number(&sensor.join(format!("fan{i}_input"))).filter(|v| *v >= 0.0)
                    })),
                    "nvme" => {
                        let name = fs::canonicalize(&sensor)
                            .ok()
                            .and_then(|path| {
                                path.ancestors()
                                    .take(8)
                                    .find_map(|parent| read_text(&parent.join("model")))
                            })
                            .map(|name| name.trim().to_string())
                            .filter(|name| !name.is_empty())
                            .unwrap_or_else(|| {
                                sensor.file_name().map_or_else(
                                    || "NVMe SSD".into(),
                                    |n| n.to_string_lossy().into_owned(),
                                )
                            });
                        result.disks.push(Disk {
                            name,
                            celsius: temp,
                        });
                    }
                    _ => {}
                }
            }
        }
        if let Some(frame) = read_text(&self.root.join("run/thermal-monitor/gputemps.json"))
            .and_then(|s| serde_json::from_str::<GpuFrame>(&s).ok())
            .filter(|frame| fresh(frame.timestamp, now_ms, GPU_MAX_AGE_MS))
        {
            result.cpu_watts = valid_power(frame.cpu_package_watts);
            if let Some(gpu) = frame.gpus.iter().find(|gpu| gpu.index == 0) {
                // Zero from an undocumented GPU register means unavailable, not 0°C.
                result.gpu_celsius = valid_temperature(gpu.core).filter(|v| *v > 0.0);
                result.hotspot_celsius = valid_temperature(gpu.junction).filter(|v| *v > 0.0);
                result.vram_celsius = valid_temperature(gpu.vram).filter(|v| *v > 0.0);
            }
        }
        let gpu = self.gpu_log_sample(now_ms).unwrap_or_default();
        result.gpu_percent = gpu.percent;
        result.gpu_watts = gpu.watts;
        result.gpu_memory = gpu.memory;
        result
    }

    fn gpu_log_sample(&self, now: i64) -> Option<GpuLogSample> {
        let logs = fs::read_dir(self.home.join(".local/state/thermal-monitor")).ok()?;
        let latest = logs
            .filter_map(Result::ok)
            .map(|entry| entry.path())
            .filter(|path| {
                path.file_name()
                    .and_then(|n| n.to_str())
                    .is_some_and(|name| {
                        name.len() == 18
                            && name.starts_with("gpu-")
                            && name.ends_with(".jsonl")
                            && name.as_bytes()[4..12].iter().all(u8::is_ascii_digit)
                    })
            })
            .max()?;
        let mut file = File::open(latest).ok()?;
        let size = file.metadata().ok()?.len();
        file.seek(SeekFrom::Start(size.saturating_sub(LOG_TAIL_BYTES)))
            .ok()?;
        let mut tail = Vec::new();
        file.take(LOG_TAIL_BYTES).read_to_end(&mut tail).ok()?;
        log_sample(&String::from_utf8_lossy(&tail), now)
    }
}

#[cfg(test)]
// Fixture assertions deliberately unwrap so unexpected setup/parse failures fail tests.
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;

    #[test]
    fn cpu_excludes_guest_and_iowait_from_busy_time() {
        let before = CpuTicks::parse("cpu 100 0 20 800 30 0 0 0 50 10\n").unwrap();
        let after = CpuTicks::parse("cpu 120 0 30 850 50 0 0 0 100 20\n").unwrap();
        assert_eq!(after.percent_since(before), Some(30.0));
    }

    #[test]
    fn cpu_reset_and_zero_interval_are_unknown() {
        let a = CpuTicks::parse("cpu 10 0 0 90").unwrap();
        let b = CpuTicks::parse("cpu 1 0 0 9").unwrap();
        assert_eq!(a.percent_since(a), None);
        assert_eq!(b.percent_since(a), None);
        assert!(CpuTicks::parse("cpu nope 0 0 90").is_err());
        assert!(CpuTicks::parse("cpu 18446744073709551615 1 0 0").is_err());
    }

    #[test]
    fn memory_rejects_inconsistent_and_missing_fields() {
        assert_eq!(
            Memory::parse("MemTotal: 100 kB\nMemAvailable: 40 kB")
                .unwrap()
                .used_kib,
            60
        );
        assert!(Memory::parse("MemTotal: 100 kB\nMemAvailable: 110 kB").is_err());
        assert!(Memory::parse("MemTotal: 100 kB").is_err());
        assert!(Memory::parse("MemTotal: 0 kB\nMemAvailable: 0 kB").is_err());
    }

    #[test]
    fn freshness_rejects_future_stale_and_overflowing_timestamps() {
        assert!(fresh(1000, 2000, 1000));
        assert!(!fresh(1000, 2001, 1000));
        assert!(!fresh(2001, 2000, 1000));
        assert!(!fresh(i64::MIN, i64::MAX, 1000));
    }

    #[test]
    fn log_tail_handles_partial_writes_without_reviving_old_data() {
        let valid = r#"{"type":"sample","timestamp":"2026-10-20T00:00:00+08:00","gpus":[{"index":"0","utilization.gpu":"0"}]}"#;
        let now = DateTime::parse_from_rfc3339("2026-10-20T00:00:05+08:00")
            .unwrap()
            .timestamp_millis();
        assert_eq!(
            log_sample(&format!("garbage\n{valid}\n{{partial"), now).and_then(|s| s.percent),
            Some(0.0)
        );
        assert!(log_sample(valid, now + 21_000).is_none());
        assert_eq!(
            log_sample(
                &format!("{valid}\n{{\"type\":\"sample\",\"gpu_error\":\"offline\"}}"),
                now
            )
            .and_then(|s| s.percent),
            None
        );
    }

    #[test]
    fn label_distinguishes_zero_utilization_and_missing_temperature() {
        let sample = Snapshot {
            cpu_percent: Some(0.0),
            ..Snapshot::default()
        };
        assert_eq!(sample.panel_label(), "CPU 0% N/A N/A · GPU N/A N/A N/A");
        assert_eq!(valid_temperature(Some(f64::NAN)), None);
        assert_eq!(valid_temperature(Some(-274.0)), None);
    }

    #[test]
    fn vram_capacity_rejects_invalid_data_but_keeps_empty_memory() {
        assert_eq!(
            GpuMemory::parse(Some(4096.0), Some(16384.0))
                .unwrap()
                .label(),
            "25% · 4.0 / 16.0 GiB"
        );
        assert_eq!(
            GpuMemory::parse(Some(0.0), Some(16384.0)).unwrap().label(),
            "0% · 0.0 / 16.0 GiB"
        );
        for (used, total) in [
            (1.0, 0.0),
            (-1.0, 8.0),
            (9.0, 8.0),
            (f64::NAN, 8.0),
            (1.0, f64::INFINITY),
        ] {
            assert!(GpuMemory::parse(Some(used), Some(total)).is_none());
        }
        assert!(GpuMemory::parse(None, Some(8.0)).is_none());
    }

    #[test]
    fn gpu_load_and_vram_capacity_fail_independently() {
        let text = r#"{"type":"sample","timestamp":"2026-10-20T00:00:00Z","gpus":[{"index":"0","utilization.gpu":"N/A","memory.used":"4096","memory.total":"16384"}]}"#;
        let now = DateTime::parse_from_rfc3339("2026-10-20T00:00:00Z")
            .unwrap()
            .timestamp_millis();
        let sample = log_sample(text, now).unwrap();
        assert_eq!(sample.percent, None);
        assert_eq!(sample.memory.unwrap().label(), "25% · 4.0 / 16.0 GiB");
        let sample = log_sample(&text.replace("N/A", "0").replace("4096", "20000"), now).unwrap();
        assert_eq!(sample.percent, Some(0.0));
        assert!(sample.memory.is_none());
        assert!(log_sample(text, now + 20_001).is_none());
    }
}
