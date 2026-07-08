// SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
// SPDX-License-Identifier: Apache-2.0
//
// The grain router.
//
// On start, a dedicated OS thread creates the MXL instance, output writer and
// one reader per MXL input slot, then loops once per grain (= frame):
//
//   * pick the active slot (an atomic, flipped live by the HTTP layer)
//   * read the active source's complete grain at the current epoch index
//     (or synthesise a black grain for an empty slot)
//   * copy the payload verbatim into the output writer's grain and commit it
//
// Because each output grain is committed atomically from exactly one source,
// switching the atomic between iterations always lands on a frame boundary —
// the switch is clean by construction.  Only the active source is read each
// frame, so cost is O(1) regardless of the number of inputs.

use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::mpsc;
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::Duration;

use mxl::config::get_mxl_so_path;
use mxl::{GrainReader, GrainWriter, MxlInstance};

use crate::black::v210_black_grain;
use crate::flowdef::{build_output_flow_def, patch_flow_def_file};

/// How many grains behind the live epoch index the router writes, so the source
/// grain is guaranteed committed before we read it.
const LAG_GRAINS: u64 = 2;

/// Per-grain read timeout — the grain should already be present (we run LAG
/// grains behind), so this only guards against a stalled / disappeared source.
const READ_TIMEOUT: Duration = Duration::from_millis(500);

pub struct RouterConfig {
    pub domain_path: String,
    /// One entry per slot: `Some(uuid)` for an MXL source, `None` for black fill.
    pub slots: Vec<Option<String>>,
    pub output_uuid: String,
    pub grouphint: String,
    pub description: String,
    pub label: String,
}

/// Handle to a running router.  All fields are `Send`; the MXL objects live
/// entirely inside the router thread.
pub struct RouterHandle {
    active: Arc<AtomicUsize>,
    stop: Arc<AtomicBool>,
    join: Option<JoinHandle<()>>,
    error: Arc<Mutex<Option<String>>>,
    num_slots: usize,
}

impl RouterHandle {
    pub fn active_slot(&self) -> usize {
        self.active.load(Ordering::Relaxed)
    }

    pub fn set_active(&self, slot: usize) -> Result<(), String> {
        if slot >= self.num_slots {
            return Err(format!("slot must be in 0..{}", self.num_slots));
        }
        self.active.store(slot, Ordering::Relaxed);
        Ok(())
    }

    pub fn last_error(&self) -> Option<String> {
        self.error.lock().unwrap().clone()
    }

    pub fn stop(&mut self) {
        self.stop.store(true, Ordering::Release);
        if let Some(join) = self.join.take() {
            let _ = join.join();
        }
    }
}

impl Drop for RouterHandle {
    fn drop(&mut self) {
        self.stop();
    }
}

enum Slot {
    Mxl(GrainReader),
    Black,
}

/// Start a router.  Blocks until the MXL instance / writer / readers are set up
/// so configuration errors surface synchronously, then returns while the copy
/// loop keeps running on its thread.
pub fn start_router(cfg: RouterConfig) -> Result<RouterHandle, String> {
    let active = Arc::new(AtomicUsize::new(0));
    let stop = Arc::new(AtomicBool::new(false));
    let error = Arc::new(Mutex::new(None));
    let num_slots = cfg.slots.len();

    let (setup_tx, setup_rx) = mpsc::channel::<Result<(), String>>();

    let join = std::thread::Builder::new()
        .name("grain-router".into())
        .spawn({
            let active = active.clone();
            let stop = stop.clone();
            let error = error.clone();
            move || run(cfg, active, stop, error, setup_tx)
        })
        .map_err(|e| format!("failed to spawn router thread: {e}"))?;

    match setup_rx.recv() {
        Ok(Ok(())) => Ok(RouterHandle { active, stop, join: Some(join), error, num_slots }),
        Ok(Err(e)) => {
            let _ = join.join();
            Err(e)
        }
        Err(_) => {
            let _ = join.join();
            Err("router thread exited during setup".to_string())
        }
    }
}

fn run(
    cfg: RouterConfig,
    active: Arc<AtomicUsize>,
    stop: Arc<AtomicBool>,
    error: Arc<Mutex<Option<String>>>,
    setup_tx: mpsc::Sender<Result<(), String>>,
) {
    let setup = setup(&cfg);
    let (instance, writer, slots, rate) = match setup {
        Ok(parts) => {
            let _ = setup_tx.send(Ok(()));
            parts
        }
        Err(e) => {
            let _ = setup_tx.send(Err(e));
            return;
        }
    };

    let mut black: Option<Vec<u8>> = None;
    // Write the output grain at the live epoch index (writer idiom), copying the
    // source grain LAG grains in the past so it is already committed.
    let mut out_idx = instance.get_current_index(&rate);

    while !stop.load(Ordering::Acquire) {
        let slot = active.load(Ordering::Relaxed).min(slots.len().saturating_sub(1));
        let src_idx = out_idx.saturating_sub(LAG_GRAINS);
        match produce_grain(&writer, &slots[slot], out_idx, src_idx, &mut black) {
            Ok(()) => {
                // Clear any stale error after a good grain.
                if error.lock().unwrap().is_some() {
                    *error.lock().unwrap() = None;
                }
            }
            Err(e) => {
                *error.lock().unwrap() = Some(e);
            }
        }

        out_idx += 1;
        // Pace to real time: sleep until the next output index becomes current.
        if let Ok(d) = instance.get_duration_until_index(out_idx, &rate) {
            instance.sleep_for(d);
        }
    }

    tracing::info!("Router thread stopping; releasing MXL resources");
    drop(slots);
    drop(writer);
    drop(instance);
}

fn setup(cfg: &RouterConfig) -> Result<(MxlInstance, GrainWriter, Vec<Slot>, mxl::Rational), String> {
    let api = mxl::load_api(get_mxl_so_path()).map_err(|e| format!("load mxl api: {e}"))?;
    let instance =
        MxlInstance::new(api, &cfg.domain_path, "").map_err(|e| format!("create mxl instance: {e}"))?;

    // Derive the output flow def from the first MXL input slot.
    let first_mxl = cfg
        .slots
        .iter()
        .find_map(|s| s.as_ref())
        .ok_or("at least one MXL input is required")?;
    let src_def = instance
        .get_flow_def(first_mxl)
        .map_err(|e| format!("read source flow_def for {first_mxl}: {e}"))?;
    let out_def = build_output_flow_def(
        &src_def,
        &cfg.output_uuid,
        &cfg.grouphint,
        &cfg.description,
        &cfg.label,
    )?;

    let (writer, info, created) = instance
        .create_flow_writer(&out_def, None)
        .map_err(|e| format!("create output flow writer: {e}"))?;
    if !created {
        tracing::warn!("Reusing existing output flow {}", cfg.output_uuid);
    }
    let rate = info.common().grain_rate().map_err(|e| format!("output grain rate: {e}"))?;
    let writer = writer.to_grain_writer().map_err(|e| format!("to grain writer: {e}"))?;

    // Re-assert metadata on the on-disk def (grouphint tag for NMOS discovery).
    if let Err(e) = patch_flow_def_file(
        &cfg.domain_path,
        &cfg.output_uuid,
        &cfg.grouphint,
        &cfg.description,
        &cfg.label,
    ) {
        tracing::warn!("Could not patch output flow_def.json: {e}");
    }

    // Pre-open a reader per MXL slot so switching has zero setup latency.
    let mut slots = Vec::with_capacity(cfg.slots.len());
    for s in &cfg.slots {
        match s {
            Some(uuid) => {
                let reader = instance
                    .create_flow_reader(uuid)
                    .map_err(|e| format!("create reader for {uuid}: {e}"))?
                    .to_grain_reader()
                    .map_err(|e| format!("to grain reader for {uuid}: {e}"))?;
                slots.push(Slot::Mxl(reader));
            }
            None => slots.push(Slot::Black),
        }
    }

    tracing::info!(
        "Router started — output {}, {} slot(s), grain rate {}/{}",
        cfg.output_uuid,
        slots.len(),
        rate.numerator,
        rate.denominator
    );
    Ok((instance, writer, slots, rate))
}

fn produce_grain(
    writer: &GrainWriter,
    slot: &Slot,
    out_idx: u64,
    src_idx: u64,
    black: &mut Option<Vec<u8>>,
) -> Result<(), String> {
    let mut wa = writer
        .open_grain(out_idx)
        .map_err(|e| format!("open output grain {out_idx}: {e}"))?;
    let total_slices = wa.total_slices();

    match slot {
        Slot::Mxl(reader) => {
            match reader.get_complete_grain(src_idx, READ_TIMEOUT) {
                Ok(grain) => {
                    let dst = wa.payload_mut();
                    let n = dst.len().min(grain.payload.len());
                    dst[..n].copy_from_slice(&grain.payload[..n]);
                }
                Err(e) => {
                    // Source grain not available — drop this output grain rather
                    // than committing garbage.
                    let _ = wa.cancel();
                    return Err(format!("read source grain {src_idx}: {e}"));
                }
            }
        }
        Slot::Black => {
            let len = wa.payload_mut().len();
            let pattern = black.get_or_insert_with(|| v210_black_grain(len));
            let dst = wa.payload_mut();
            let n = dst.len().min(pattern.len());
            dst[..n].copy_from_slice(&pattern[..n]);
        }
    }

    wa.commit(total_slices).map_err(|e| format!("commit output grain {out_idx}: {e}"))
}
