//! StatusNotifierItem and DBusMenu, including Ubuntu's numeric-label extension.
use std::{
    collections::BTreeMap,
    sync::{Arc, Mutex, mpsc},
    time::Duration,
};

use anyhow::{Context, Result};
use zbus::{
    Connection,
    connection::Builder,
    fdo,
    wire::{Dict, OwnedObjectPath, OwnedVariant, Str, StructureBuilder, Variant},
};

use crate::telemetry::Snapshot;

/// Stable bus name; acquiring it also prevents duplicate Rust instances.
pub const BUS_NAME: &str = "org.delirious6423.ThermalMonitor";
const ITEM_PATH: &str = "/StatusNotifierItem";
const ITEM_INTERFACE: &str = "org.kde.StatusNotifierItem";
const MENU_PATH: &str = "/MenuBar";
const MENU_INTERFACE: &str = "com.canonical.dbusmenu";
const LABEL_GUIDE: &str = "CPU 100% 100° · GPU 100% 100°";
type Properties = BTreeMap<String, OwnedVariant>;
type Layout = (i32, Properties, Vec<OwnedVariant>);
type Shared = Arc<Mutex<State>>;
type Pixmaps = Vec<(i32, i32, Vec<u8>)>;
type ToolTip = (String, Pixmaps, String, String);

struct State {
    snapshot: Snapshot,
    revision: u32,
    graphs_visible: bool,
}

fn state_snapshot(state: &Shared) -> Snapshot {
    state
        .lock()
        .map(|state| state.snapshot.clone())
        .unwrap_or_default()
}

fn string(value: impl Into<String>) -> OwnedVariant {
    Str::from(value.into()).into()
}

// Embedded ARGB thermometer: no missing theme icon or additional image toolkit.
fn thermometer() -> Pixmaps {
    let mut pixels = Vec::with_capacity(32 * 32 * 4);
    for row in 0..32 {
        for column in 0..32 {
            let (x, y) = (
                (f64::from(column) + 0.5) / 2.0,
                (f64::from(row) + 0.5) / 2.0,
            );
            let stem = (x - 8.0).powi(2) + (y - y.clamp(3.0, 11.0)).powi(2);
            let bulb = (x - 8.0).powi(2) + (y - 12.0).powi(2);
            let outline = (stem <= 4.0 || bulb <= 10.24) && !(stem < 1.0 || bulb < 4.41);
            let mercury = bulb < 2.56 || ((x - 8.0).abs() < 0.5 && (6.0..12.0).contains(&y));
            pixels.extend_from_slice(if outline || mercury {
                &[255, 255, 255, 255]
            } else {
                &[0, 0, 0, 0]
            });
        }
    }
    vec![(32, 32, pixels)]
}

/// Fixed menu actions; sensor text can never become an executable command.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Action {
    /// Start the existing Conky service.
    ShowGraphs,
    /// Stop the existing Conky service.
    HideGraphs,
    /// Open the local telemetry directory in the file manager.
    OpenLogs,
    /// Exit this user-level indicator.
    Quit,
}

fn action(id: i32) -> Option<Action> {
    match id {
        1001 => Some(Action::ShowGraphs),
        1002 => Some(Action::HideGraphs),
        1003 => Some(Action::OpenLogs),
        1005 => Some(Action::Quit),
        _ => None,
    }
}

struct Indicator {
    state: Shared,
}

#[zbus::interface(name = "org.kde.StatusNotifierItem")]
impl Indicator {
    #[zbus(property)]
    fn id(&self) -> &str {
        "thermal-monitor"
    }
    #[zbus(property)]
    fn title(&self) -> &str {
        "Thermal Monitor"
    }
    #[zbus(property)]
    fn category(&self) -> &str {
        "Hardware"
    }
    #[zbus(property)]
    fn status(&self) -> &str {
        "Active"
    }
    #[zbus(property)]
    fn icon_name(&self) -> &str {
        ""
    }
    #[zbus(property)]
    fn icon_theme_path(&self) -> &str {
        ""
    }
    #[zbus(property)]
    fn icon_pixmap(&self) -> Pixmaps {
        thermometer()
    }
    #[zbus(property)]
    fn overlay_icon_name(&self) -> &str {
        ""
    }
    #[zbus(property)]
    fn attention_icon_name(&self) -> &str {
        ""
    }
    #[zbus(property)]
    fn attention_movie_name(&self) -> &str {
        ""
    }
    #[zbus(property)]
    fn item_is_menu(&self) -> bool {
        true
    }
    #[zbus(property)]
    fn window_id(&self) -> u32 {
        0
    }
    #[zbus(property)]
    fn menu(&self) -> fdo::Result<OwnedObjectPath> {
        OwnedObjectPath::try_from(MENU_PATH).map_err(|e| fdo::Error::Failed(e.to_string()))
    }
    #[zbus(property, name = "XAyatanaLabel")]
    fn label(&self) -> String {
        state_snapshot(&self.state).panel_label()
    }
    #[zbus(property, name = "XAyatanaLabelGuide")]
    fn label_guide(&self) -> &str {
        LABEL_GUIDE
    }
    #[zbus(property)]
    fn tool_tip(&self) -> ToolTip {
        let snapshot = state_snapshot(&self.state);
        (
            self.icon_name().into(),
            thermometer(),
            self.title().into(),
            format!("{}\n{}", snapshot.panel_label(), snapshot.overview()[2]),
        )
    }
    fn activate(&self, _x: i32, _y: i32) {}
    fn secondary_activate(&self, _x: i32, _y: i32) {}
    fn context_menu(&self, _x: i32, _y: i32) {}
    fn scroll(&self, _delta: i32, _orientation: &str) {}
}

struct Menu {
    state: Shared,
    events: mpsc::Sender<Action>,
}

impl Menu {
    fn nodes(&self) -> Vec<(i32, Properties)> {
        Self::nodes_for(&self.state)
    }

    fn nodes_for(state: &Shared) -> Vec<(i32, Properties)> {
        let snapshot = state_snapshot(state);
        let mut nodes = snapshot
            .overview()
            .into_iter()
            .enumerate()
            .map(|(i, label)| {
                // The sampler caps hwmon entries at 128, so menu IDs fit i32.
                (
                    i as i32 + 1,
                    BTreeMap::from([
                        ("label".into(), string(label)),
                        ("enabled".into(), false.into()),
                    ]),
                )
            })
            .collect::<Vec<_>>();
        nodes.push((
            500,
            BTreeMap::from([
                ("label".into(), string("More sensors")),
                ("children-display".into(), string("submenu")),
                ("enabled".into(), true.into()),
            ]),
        ));
        nodes.extend(
            snapshot
                .details()
                .into_iter()
                .enumerate()
                .map(|(index, label)| {
                    (
                        index as i32 + 10,
                        BTreeMap::from([
                            ("label".into(), string(label)),
                            ("enabled".into(), false.into()),
                        ]),
                    )
                }),
        );
        nodes.push((1000, BTreeMap::from([("type".into(), string("separator"))])));
        let graphs_visible = state.lock().map(|s| s.graphs_visible).unwrap_or(false);
        let graphs = if graphs_visible {
            (1002, "Hide graphs")
        } else {
            (1001, "Show graphs")
        };
        for (id, label) in [graphs, (1003, "Open 7-day logs"), (1005, "Quit indicator")] {
            if id == 1005 {
                nodes.push((1004, BTreeMap::from([("type".into(), string("separator"))])));
            }
            nodes.push((
                id,
                BTreeMap::from([
                    ("label".into(), string(label)),
                    ("enabled".into(), true.into()),
                ]),
            ));
        }
        nodes
    }

    fn parent(id: i32) -> i32 {
        if (10..500).contains(&id) { 500 } else { 0 }
    }

    fn layout(&self, id: i32, depth: i32, names: &[String]) -> fdo::Result<Layout> {
        let props = if id == 0 {
            BTreeMap::from([("children-display".into(), string("submenu"))])
        } else {
            self.nodes()
                .into_iter()
                .find(|(key, _)| *key == id)
                .map(|(_, props)| props)
                .ok_or_else(|| fdo::Error::InvalidArgs("unknown menu item".into()))?
        };
        let mut children = vec![];
        if depth != 0 {
            let next_depth = if depth < 0 { -1 } else { depth - 1 };
            for (key, _) in self
                .nodes()
                .into_iter()
                .filter(|(key, _)| Self::parent(*key) == id)
            {
                let (child_id, child_props, grandchildren) = self.layout(key, next_depth, names)?;
                let structure = StructureBuilder::new()
                    .add_field(child_id)
                    .append_field(Variant::Dict(Dict::from(child_props)))
                    .add_field(grandchildren)
                    .build()
                    .map_err(|e| fdo::Error::Failed(e.to_string()))?;
                children.push(
                    Variant::Structure(structure)
                        .try_to_owned()
                        .map_err(|e| fdo::Error::Failed(e.to_string()))?,
                );
            }
        }
        Ok((id, Self::filtered(props, names), children))
    }

    fn filtered(props: Properties, names: &[String]) -> Properties {
        props
            .into_iter()
            .filter(|(key, _)| names.is_empty() || names.contains(key))
            .collect()
    }

    fn dispatch(&self, id: i32, event: &str) {
        if event == "clicked"
            && let Some(action) = action(id)
            && let Err(error) = self.events.send(action)
        {
            tracing::warn!(%error, "menu receiver closed");
        }
    }
}

#[zbus::interface(name = "com.canonical.dbusmenu")]
impl Menu {
    #[zbus(property)]
    fn version(&self) -> u32 {
        3
    }
    #[zbus(property)]
    fn text_direction(&self) -> &str {
        "ltr"
    }
    #[zbus(property)]
    fn status(&self) -> &str {
        "normal"
    }
    #[zbus(property)]
    fn icon_theme_path(&self) -> Vec<String> {
        vec![]
    }

    fn get_layout(
        &self,
        parent_id: i32,
        recursion_depth: i32,
        property_names: Vec<String>,
    ) -> fdo::Result<(u32, Layout)> {
        let revision = self
            .state
            .lock()
            .map_err(|e| fdo::Error::Failed(e.to_string()))?
            .revision;
        Ok((
            revision,
            self.layout(parent_id, recursion_depth, &property_names)?,
        ))
    }

    fn get_group_properties(
        &self,
        ids: Vec<i32>,
        property_names: Vec<String>,
    ) -> Vec<(i32, Properties)> {
        self.nodes()
            .into_iter()
            .filter(|(id, _)| ids.is_empty() || ids.contains(id))
            .map(|(id, props)| (id, Self::filtered(props, &property_names)))
            .collect()
    }

    fn get_property(&self, id: i32, name: &str) -> fdo::Result<OwnedVariant> {
        self.nodes()
            .into_iter()
            .find(|(key, _)| *key == id)
            .and_then(|(_, mut props)| props.remove(name))
            .ok_or_else(|| fdo::Error::InvalidArgs("unknown menu property".into()))
    }

    fn event(&self, id: i32, event_id: &str, _data: OwnedVariant, _timestamp: u32) {
        self.dispatch(id, event_id);
    }

    fn event_group(&self, events: Vec<(i32, String, OwnedVariant, u32)>) -> Vec<i32> {
        let ids = self
            .nodes()
            .into_iter()
            .map(|(id, _)| id)
            .collect::<Vec<_>>();
        let mut errors = vec![];
        for (id, name, _, _) in events {
            if ids.contains(&id) {
                self.dispatch(id, &name);
            } else {
                errors.push(id);
            }
        }
        errors
    }

    #[zbus(signal)]
    async fn layout_updated(
        emitter: &zbus::object_server::SignalEmitter<'_>,
        revision: u32,
        parent: i32,
    ) -> zbus::Result<()>;

    #[zbus(signal)]
    async fn items_properties_updated(
        emitter: &zbus::object_server::SignalEmitter<'_>,
        updated: Vec<(i32, Properties)>,
        removed: Vec<(i32, Vec<String>)>,
    ) -> zbus::Result<()>;

    fn about_to_show(&self, _id: i32) -> bool {
        false
    }
    fn about_to_show_group(&self, ids: Vec<i32>) -> (Vec<i32>, Vec<i32>) {
        let valid = self
            .nodes()
            .into_iter()
            .map(|(id, _)| id)
            .collect::<Vec<_>>();
        (
            vec![],
            ids.into_iter()
                .filter(|id| *id != 0 && !valid.contains(id))
                .collect(),
        )
    }
}

/// Owns the session-bus connection and receives the dropdown's fixed actions.
pub struct TrayHost {
    connection: Connection,
    state: Shared,
    events: mpsc::Receiver<Action>,
    watcher_owner: Option<String>,
}

impl TrayHost {
    /// Export both interfaces without requiring a running tray host yet.
    pub async fn connect(snapshot: Snapshot) -> Result<Self> {
        let state = Arc::new(Mutex::new(State {
            snapshot,
            revision: 1,
            graphs_visible: false,
        }));
        let (sender, events) = mpsc::channel();
        let connection = Builder::session()
            .name(BUS_NAME)
            .allow_name_replacements(false)
            .replace_existing_names(false)
            .method_timeout(Duration::from_secs(3))
            .serve_at(
                ITEM_PATH,
                Indicator {
                    state: state.clone(),
                },
            )
            .serve_at(
                MENU_PATH,
                Menu {
                    state: state.clone(),
                    events: sender,
                },
            )
            .build()
            .await
            .context("export the thermal monitor on the session bus")?;
        Ok(Self {
            connection,
            state,
            events,
            watcher_owner: None,
        })
    }

    /// Register initially and again if the shell's tray watcher restarts.
    pub async fn register_if_needed(&mut self) -> Result<bool> {
        let reply = match self
            .connection
            .call_method(
                Some("org.freedesktop.DBus"),
                "/org/freedesktop/DBus",
                Some("org.freedesktop.DBus"),
                "GetNameOwner",
                &("org.kde.StatusNotifierWatcher",),
            )
            .await
        {
            Ok(reply) => reply,
            Err(zbus::Error::MethodError(name, _, _))
                if name.as_str() == "org.freedesktop.DBus.Error.NameHasNoOwner" =>
            {
                self.watcher_owner = None;
                return Ok(false);
            }
            Err(zbus::Error::FDO(error))
                if matches!(error.as_ref(), fdo::Error::NameHasNoOwner(_)) =>
            {
                self.watcher_owner = None;
                return Ok(false);
            }
            Err(error) => return Err(error.into()),
        };
        let owner: String = reply.body().deserialize()?;
        // GNOME can discard a registration while cleaning up an earlier
        // process with the same well-known name. Its own bus owner stays the same.
        let listed = if self.watcher_owner.as_ref() == Some(&owner) {
            let reply = self
                .connection
                .call_method(
                    Some("org.kde.StatusNotifierWatcher"),
                    "/StatusNotifierWatcher",
                    Some("org.freedesktop.DBus.Properties"),
                    "Get",
                    &(
                        "org.kde.StatusNotifierWatcher",
                        "RegisteredStatusNotifierItems",
                    ),
                )
                .await?;
            let value: OwnedVariant = reply.body().deserialize()?;
            Vec::<String>::try_from(value)?.iter().any(|item| {
                item == BUS_NAME
                    || item
                        .strip_prefix(BUS_NAME)
                        .is_some_and(|suffix| suffix.starts_with('/') || suffix.starts_with('@'))
            })
        } else {
            false
        };
        if !listed {
            self.connection
                .call_method(
                    Some("org.kde.StatusNotifierWatcher"),
                    "/StatusNotifierWatcher",
                    Some("org.kde.StatusNotifierWatcher"),
                    "RegisterStatusNotifierItem",
                    &(BUS_NAME,),
                )
                .await?;
            self.watcher_owner = Some(owner);
            tracing::info!("top-bar indicator registered");
        }
        Ok(true)
    }

    /// Publish dynamic label and menu changes using standard D-Bus notifications.
    pub async fn publish(&self, snapshot: Snapshot) -> Result<()> {
        let label = snapshot.panel_label();
        let old_labels = Menu::nodes_for(&self.state)
            .into_iter()
            .filter_map(|(id, mut props)| {
                props
                    .remove("label")
                    .and_then(|v| String::try_from(v).ok())
                    .map(|label| (id, label))
            })
            .collect::<BTreeMap<_, _>>();
        let graphs_visible = self.graphs_visible().await.unwrap_or(false);
        let (revision, layout_changed) = {
            let mut state = self
                .state
                .lock()
                .map_err(|e| anyhow::anyhow!(e.to_string()))?;
            let changed = state.graphs_visible != graphs_visible
                || state.snapshot.disks.len() != snapshot.disks.len();
            state.snapshot = snapshot;
            state.graphs_visible = graphs_visible;
            if changed {
                state.revision = state.revision.wrapping_add(1);
            }
            (state.revision, changed)
        };
        let changed = BTreeMap::from([("XAyatanaLabel".to_string(), string(label.clone()))]);
        self.connection
            .emit_signal(
                None::<&str>,
                ITEM_PATH,
                "org.freedesktop.DBus.Properties",
                "PropertiesChanged",
                &(ITEM_INTERFACE, changed, Vec::<String>::new()),
            )
            .await?;
        self.connection
            .emit_signal(
                None::<&str>,
                ITEM_PATH,
                ITEM_INTERFACE,
                "XAyatanaNewLabel",
                &(label, LABEL_GUIDE),
            )
            .await?;
        // A children-display property update rebuilds GNOME's submenu even
        // when unchanged. Send only changed labels, preserving focus and expansion.
        let updates = Menu::nodes_for(&self.state)
            .into_iter()
            .filter_map(|(id, mut props)| {
                props.retain(|key, _| key == "label");
                let label = props
                    .get("label")
                    .and_then(|value| <&str>::try_from(value).ok())?;
                (old_labels.get(&id).map(String::as_str) != Some(label)).then_some((id, props))
            })
            .collect::<Vec<_>>();
        if !updates.is_empty() {
            self.connection
                .emit_signal(
                    None::<&str>,
                    MENU_PATH,
                    MENU_INTERFACE,
                    "ItemsPropertiesUpdated",
                    &(updates, Vec::<(i32, Vec<String>)>::new()),
                )
                .await?;
        }
        if layout_changed {
            self.connection
                .emit_signal(
                    None::<&str>,
                    MENU_PATH,
                    MENU_INTERFACE,
                    "LayoutUpdated",
                    &(revision, 0_i32),
                )
                .await?;
        }
        Ok(())
    }

    // Reuse the existing bus rather than spawning a status subprocess each poll.
    async fn graphs_visible(&self) -> Result<bool> {
        let reply = self
            .connection
            .call_method(
                Some("org.freedesktop.systemd1"),
                "/org/freedesktop/systemd1",
                Some("org.freedesktop.systemd1.Manager"),
                "GetUnit",
                &("thermal-desktop.service",),
            )
            .await?;
        let path: OwnedObjectPath = reply.body().deserialize()?;
        let reply = self
            .connection
            .call_method(
                Some("org.freedesktop.systemd1"),
                path.as_str(),
                Some("org.freedesktop.DBus.Properties"),
                "Get",
                &("org.freedesktop.systemd1.Unit", "ActiveState"),
            )
            .await?;
        let value: OwnedVariant = reply.body().deserialize()?;
        let state = String::try_from(value)?;
        Ok(matches!(state.as_str(), "active" | "activating"))
    }

    /// Wait for a menu action until the next sampling deadline.
    pub fn wait(&self, timeout: Duration) -> Result<Option<Action>> {
        match self.events.recv_timeout(timeout) {
            Ok(action) => Ok(Some(action)),
            Err(mpsc::RecvTimeoutError::Timeout) => Ok(None),
            Err(error) => Err(error.into()),
        }
    }
}

#[cfg(test)]
// Fixture assertions deliberately unwrap to fail immediately on a protocol mismatch.
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;

    #[test]
    fn only_fixed_clicked_actions_are_dispatched() {
        let (events, receiver) = mpsc::channel();
        let menu = Menu {
            state: Arc::new(Mutex::new(State {
                snapshot: Snapshot::default(),
                revision: 1,
                graphs_visible: false,
            })),
            events,
        };
        menu.dispatch(1001, "hovered");
        menu.dispatch(1, "clicked");
        menu.dispatch(-1, "clicked");
        assert!(receiver.try_recv().is_err());
        menu.dispatch(1001, "clicked");
        assert_eq!(receiver.try_recv().unwrap(), Action::ShowGraphs);
    }

    #[test]
    fn menu_layout_has_protocol_signature_and_filters_properties() {
        use zbus::Type;
        assert_eq!(<(u32, Layout)>::SIGNATURE.to_string(), "(u(ia{sv}av))");
        let (events, _) = mpsc::channel();
        let menu = Menu {
            state: Arc::new(Mutex::new(State {
                snapshot: Snapshot::default(),
                revision: 7,
                graphs_visible: false,
            })),
            events,
        };
        let (revision, (id, _, children)) = menu.get_layout(0, -1, vec![]).unwrap();
        assert_eq!(revision, 7);
        assert_eq!(id, 0);
        assert!(!children.is_empty());
        assert!(menu.get_layout(0, 0, vec![]).unwrap().1.2.is_empty());
        assert!(menu.get_layout(-9, -1, vec![]).is_err());
        assert_eq!(
            menu.get_group_properties(vec![1001], vec!["label".into()])[0]
                .1
                .len(),
            1
        );
    }
    #[test]
    fn overview_is_read_only_and_extra_sensors_are_nested() {
        let (events, _) = mpsc::channel();
        let menu = Menu {
            state: Arc::new(Mutex::new(State {
                snapshot: Snapshot::default(),
                revision: 1,
                graphs_visible: false,
            })),
            events,
        };
        assert_eq!(menu.get_layout(0, 1, vec![]).unwrap().1.2.len(), 10);
        assert_eq!(menu.get_layout(500, -1, vec![]).unwrap().1.2.len(), 3);
        assert!(menu.get_layout(500, 0, vec![]).unwrap().1.2.is_empty());
        assert!(!bool::try_from(menu.get_property(1, "enabled").unwrap()).unwrap());
        assert_eq!(
            String::try_from(menu.get_property(3, "label").unwrap()).unwrap(),
            "VRAM   N/A"
        );
        assert!(menu.get_property(1002, "label").is_err());
        menu.state.lock().unwrap().graphs_visible = true;
        assert_eq!(
            String::try_from(menu.get_property(1002, "label").unwrap()).unwrap(),
            "Hide graphs"
        );
        assert!(menu.get_property(1001, "label").is_err());
    }

    #[test]
    fn icon_is_a_complete_argb_pixmap_with_transparent_background() {
        let icons = thermometer();
        assert_eq!((icons[0].0, icons[0].1), (32, 32));
        assert_eq!(icons[0].2.len(), 32 * 32 * 4);
        assert!(
            icons[0]
                .2
                .as_chunks::<4>()
                .0
                .contains(&[255, 255, 255, 255])
        );
        assert!(icons[0].2.as_chunks::<4>().0.contains(&[0, 0, 0, 0]));
    }
}
