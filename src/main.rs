//! Command-line wiring for the read-only top-bar monitor.
use std::{
    env,
    io::{self, Write},
    path::PathBuf,
    process::Command,
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

use anyhow::{Context, Result};
use clap::Parser;
use thermal_monitor::{
    telemetry::Sampler,
    tray::{Action, TrayHost},
};

#[derive(Parser)]
#[command(version, about = "Compact CPU/GPU temperatures in the Linux top bar")]
struct Arguments {
    /// Print one JSON sample without opening a tray or changing settings.
    #[arg(long)]
    once: bool,
    /// Redirect telemetry reads for fixture-based tests.
    #[arg(long, env = "THERMAL_MONITOR_ROOT", default_value = "/")]
    root: PathBuf,
    /// Home directory containing the existing seven-day GPU logs.
    #[arg(long)]
    home: Option<PathBuf>,
    /// Sampling interval in seconds.
    #[arg(long, default_value_t = 5, value_parser = clap::value_parser!(u64).range(1..=3600))]
    interval_seconds: u64,
}

fn now_ms() -> Result<i64> {
    let duration = SystemTime::now().duration_since(UNIX_EPOCH)?;
    i64::try_from(duration.as_millis()).context("system time exceeds supported range")
}

fn launch(action: Action, home: &std::path::Path) -> Result<()> {
    let mut command = match action {
        Action::ShowGraphs | Action::HideGraphs => {
            let mut command = Command::new("/usr/bin/systemctl");
            command.args([
                "--user",
                "--no-block",
                if action == Action::ShowGraphs {
                    "start"
                } else {
                    "stop"
                },
                "thermal-desktop.service",
            ]);
            command
        }
        Action::OpenLogs => {
            let mut command = Command::new("/usr/bin/xdg-open");
            command.arg(home.join(".local/state/thermal-monitor"));
            command
        }
        Action::Quit => return Ok(()),
    };
    let mut child = command.spawn().context("launch menu action")?;
    // Reap children without letting a slow file manager block the sampling loop.
    thread::spawn(move || {
        if let Err(error) = child.wait() {
            tracing::warn!(%error, "wait for menu action");
        }
    });
    Ok(())
}

fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_writer(io::stderr)
        .with_target(false)
        .init();
    let args = Arguments::parse();
    let home = args
        .home
        .or_else(|| env::var_os("HOME").map(PathBuf::from))
        .context("HOME is not set")?;
    let mut sampler = Sampler::new(args.root, home.clone());
    let initial = sampler.sample(now_ms()?);
    if args.once {
        // JSON is deliberate command output, not diagnostic logging.
        serde_json::to_writer(io::stdout().lock(), &initial)?;
        writeln!(io::stdout())?;
        return Ok(());
    }
    let mut tray = zbus::block_on(TrayHost::connect(initial))?;
    let interval = Duration::from_secs(args.interval_seconds);
    let mut next = Instant::now();
    loop {
        let now = Instant::now();
        if now >= next {
            if let Err(error) = zbus::block_on(tray.register_if_needed()) {
                tracing::warn!(%error, "tray registration will be retried");
            }
            zbus::block_on(tray.publish(sampler.sample(now_ms()?)))?;
            next = Instant::now() + interval;
        }
        if let Some(action) = tray.wait(next.saturating_duration_since(Instant::now()))? {
            if action == Action::Quit {
                break;
            }
            if let Err(error) = launch(action, &home) {
                tracing::warn!(%error, "menu action failed");
            }
        }
    }
    Ok(())
}
