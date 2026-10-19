//! Real file-layout fixtures exercise stale data, partial log writes and failures.
// Test setup uses unwrap to make a fixture-creation failure immediately visible.
#![allow(clippy::unwrap_used)]
use std::{fs, path::Path};

use tempfile::TempDir;
use thermal_monitor::telemetry::Sampler;

const NOW: i64 = 1_792_454_400_000; // 2026-10-20T00:00:00Z

fn put(root: &Path, relative: &str, value: &str) {
    let file = root.join(relative);
    fs::create_dir_all(file.parent().unwrap()).unwrap();
    fs::write(file, value).unwrap();
}

fn fixture() -> (TempDir, Sampler) {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    put(root, "proc/stat", "cpu 100 0 20 800 30 0 0 0 100 0\n");
    put(
        root,
        "proc/meminfo",
        "MemTotal: 1000 kB\nMemAvailable: 400 kB\n",
    );
    for (name, kind, temp) in [
        ("hwmon0", "coretemp", "51000"),
        ("hwmon1", "nvme", "59000"),
        ("hwmon2", "hp", "0"),
    ] {
        put(root, &format!("sys/class/hwmon/{name}/name"), kind);
        put(root, &format!("sys/class/hwmon/{name}/temp1_input"), temp);
    }
    put(
        root,
        "sys/class/hwmon/hwmon1/model",
        "Predator SSD GM7000 2TB\n",
    );
    put(root, "sys/class/hwmon/hwmon2/fan1_input", "2000");
    put(root, "sys/class/hwmon/hwmon2/fan2_input", "1800");
    put(
        root,
        "run/thermal-monitor/gputemps.json",
        &format!(
            r#"{{"timestamp":{NOW},"gpus":[{{"index":1,"core":99,"junction":100,"vram":101}},{{"index":0,"core":45,"junction":51,"vram":50}}]}}"#
        ),
    );
    put(
        root,
        "home/.local/state/thermal-monitor/gpu-20261020.jsonl",
        "broken first line\n{\"type\":\"sample\",\"timestamp\":\"2026-10-20T00:00:00Z\",\"gpus\":[{\"index\":\"0\",\"utilization.gpu\":\"0\"}]}\n{partial",
    );
    let sampler = Sampler::new(root.to_path_buf(), root.join("home"));
    (dir, sampler)
}

#[test]
fn complete_snapshot_selects_gpu_zero_and_keeps_idle_load() {
    let (dir, mut sampler) = fixture();
    let first = sampler.sample(NOW + 1000);
    assert_eq!(first.cpu_percent, None);
    assert_eq!(first.cpu_celsius, Some(51.0));
    assert_eq!(first.gpu_celsius, Some(45.0));
    assert_eq!(first.gpu_percent, Some(0.0));
    assert_eq!(first.hotspot_celsius, Some(51.0));
    assert_eq!(first.vram_celsius, Some(50.0));
    assert_eq!(first.fan_rpm, vec![Some(2000.0), Some(1800.0)]);
    assert_eq!(first.disks[0].name, "Predator SSD GM7000 2TB");
    put(dir.path(), "proc/stat", "cpu 120 0 30 850 50 0 0 0 200 0\n");
    assert_eq!(sampler.sample(NOW + 2000).cpu_percent, Some(30.0));
}

#[test]
fn stale_temperatures_expire_independently_of_gpu_load() {
    let (_dir, mut sampler) = fixture();
    let sample = sampler.sample(NOW + 15_001);
    assert_eq!(sample.gpu_celsius, None);
    assert_eq!(sample.hotspot_celsius, None);
    assert_eq!(sample.vram_celsius, None);
    assert_eq!(sample.gpu_percent, Some(0.0));
    assert_eq!(sampler.sample(NOW + 20_001).gpu_percent, None);
}

#[test]
fn future_or_zero_register_readings_are_not_shown_as_live_temperatures() {
    let (dir, mut sampler) = fixture();
    assert_eq!(sampler.sample(NOW - 1).gpu_celsius, None);
    put(
        dir.path(),
        "run/thermal-monitor/gputemps.json",
        &format!(
            r#"{{"timestamp":{NOW},"gpus":[{{"index":0,"core":45,"junction":null,"vram":0}}]}}"#
        ),
    );
    let sample = sampler.sample(NOW + 100);
    assert_eq!(sample.gpu_celsius, Some(45.0));
    assert_eq!(sample.hotspot_celsius, None);
    assert_eq!(sample.vram_celsius, None);
}

#[test]
fn one_broken_sensor_does_not_hide_other_readings() {
    let (dir, mut sampler) = fixture();
    put(dir.path(), "sys/class/hwmon/hwmon0/temp1_input", "-274000");
    put(dir.path(), "proc/meminfo", "invalid");
    put(dir.path(), "sys/class/hwmon/hwmon2/fan1_input", "NaN");
    let sample = sampler.sample(NOW + 100);
    assert_eq!(sample.cpu_celsius, None);
    assert!(sample.memory.is_none());
    assert_eq!(sample.gpu_celsius, Some(45.0));
    assert_eq!(sample.fan_rpm, vec![None, Some(1800.0)]);
    assert_eq!(sample.disks[0].celsius, Some(59.0));
}

#[test]
fn missing_all_inputs_is_a_valid_unknown_snapshot() {
    let dir = tempfile::tempdir().unwrap();
    let mut sampler = Sampler::new(dir.path().into(), dir.path().join("home"));
    let sample = sampler.sample(NOW);
    assert_eq!(sample.panel_label(), "CPU N/A N/A N/A · GPU N/A N/A N/A");
    assert!(sample.disks.is_empty());
}

#[test]
fn large_logs_read_only_the_bounded_tail() {
    let (dir, mut sampler) = fixture();
    let path = dir
        .path()
        .join("home/.local/state/thermal-monitor/gpu-20261020.jsonl");
    let original = fs::read_to_string(&path).unwrap();
    fs::write(path, format!("{}\n{original}", "x".repeat(100_000))).unwrap();
    assert_eq!(sampler.sample(NOW + 100).gpu_percent, Some(0.0));
}

#[test]
fn vram_reads_allocated_capacity_and_expires_with_the_gpu_log() {
    let (dir, mut sampler) = fixture();
    let record = serde_json::json!({"type":"sample", "timestamp":"2026-10-20T00:00:00Z", "gpus":[
        {"index":"1", "memory.used":"1", "memory.total":"2"},
        {"index":"0", "utilization.gpu":"0", "memory.used":"4096", "memory.total":"16384"}
    ]});
    put(
        dir.path(),
        "home/.local/state/thermal-monitor/gpu-20261020.jsonl",
        &format!("{record}\n{{partial"),
    );
    let snapshot = sampler.sample(NOW + 1000);
    assert_eq!(snapshot.gpu_memory.unwrap().label(), "25% · 4.0 / 16.0 GiB");
    assert_eq!(snapshot.gpu_percent, Some(0.0));
    assert!(sampler.sample(NOW + 20_001).gpu_memory.is_none());
}

#[test]
fn power_readings_are_independent_validated_and_expire() {
    let (dir, mut sampler) = fixture();
    let frame = serde_json::json!({"timestamp": NOW, "cpu_package_watts":24.4, "gpus":[]});
    put(
        dir.path(),
        "run/thermal-monitor/gputemps.json",
        &frame.to_string(),
    );
    let record = serde_json::json!({"type":"sample", "timestamp":"2026-10-20T00:00:00Z", "gpus":[
        {"index":"0", "utilization.gpu":"N/A", "power.draw":"35.6"}
    ]});
    let log = "home/.local/state/thermal-monitor/gpu-20261020.jsonl";
    put(dir.path(), log, &record.to_string());
    let sample = sampler.sample(NOW);
    assert_eq!(sample.cpu_watts, Some(24.4));
    assert_eq!(sample.gpu_watts, Some(35.6));
    assert_eq!(sample.gpu_percent, None);
    assert_eq!(sample.panel_label(), "CPU N/A 51° 24W · GPU N/A N/A 36W");
    assert_eq!(sample.overview()[0], "CPU   N/A · 51°C · 24 W");
    assert_eq!(sample.overview()[1], "GPU   N/A · N/A · 36 W");
    assert_eq!(sampler.sample(NOW - 1).cpu_watts, None);
    assert_eq!(sampler.sample(NOW - 1).gpu_watts, None);
    assert_eq!(sampler.sample(NOW + 15_001).cpu_watts, None);
    assert_eq!(sampler.sample(NOW + 15_001).gpu_watts, Some(35.6));
    assert_eq!(sampler.sample(NOW + 20_001).gpu_watts, None);
    for value in [
        serde_json::json!(0),
        serde_json::json!(-1),
        serde_json::Value::Null,
    ] {
        let mut frame = frame.clone();
        frame["cpu_package_watts"] = value.clone();
        put(
            dir.path(),
            "run/thermal-monitor/gputemps.json",
            &frame.to_string(),
        );
        let mut record = record.clone();
        record["gpus"][0]["power.draw"] = value.clone();
        put(dir.path(), log, &record.to_string());
        let sample = sampler.sample(NOW);
        let expected = if value == 0 { Some(0.0) } else { None };
        assert_eq!(sample.cpu_watts, expected);
        assert_eq!(sample.gpu_watts, expected);
    }
    for value in ["N/A", "NaN", "inf"] {
        let mut record = record.clone();
        record["gpus"][0]["power.draw"] = value.into();
        put(dir.path(), log, &record.to_string());
        assert_eq!(sampler.sample(NOW).gpu_watts, None);
    }
}
