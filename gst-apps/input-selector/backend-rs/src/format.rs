// SPDX-License-Identifier: Apache-2.0
//
// Flow format reading and cross-input validation — ported from the Python
// backend's `gst_selector.py` (`read_flow_format`, `validate_inputs`).
//
// All selected inputs must share frame_width, frame_height, grain_rate and
// interlace_mode, because the output flow has a single fixed grain size and the
// router copies grain payloads verbatim.

use std::path::Path;

use serde::Serialize;

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct GrainRate {
    pub numerator: i64,
    pub denominator: i64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct FlowFormat {
    pub frame_width: i64,
    pub frame_height: i64,
    pub grain_rate: GrainRate,
    pub interlace_mode: String,
}

/// Load `<domain>/<uuid>.mxl-flow/flow_def.json` and extract the validation
/// fields.  Returns a human-readable error string on failure.
pub fn read_flow_format(domain_path: &str, flow_uuid: &str) -> Result<FlowFormat, String> {
    let path = Path::new(domain_path)
        .join(format!("{flow_uuid}.mxl-flow"))
        .join("flow_def.json");
    if !path.exists() {
        return Err(format!("flow_def.json not found: {}", path.display()));
    }
    let text = std::fs::read_to_string(&path).map_err(|e| e.to_string())?;
    let v: serde_json::Value = serde_json::from_str(&text).map_err(|e| e.to_string())?;

    let frame_width = v
        .get("frame_width")
        .and_then(|x| x.as_i64())
        .ok_or("missing required field: frame_width")?;
    let frame_height = v
        .get("frame_height")
        .and_then(|x| x.as_i64())
        .ok_or("missing required field: frame_height")?;
    let gr = v.get("grain_rate").ok_or("missing required field: grain_rate")?;
    let numerator = gr
        .get("numerator")
        .and_then(|x| x.as_i64())
        .ok_or("missing required field: grain_rate.numerator")?;
    let denominator = gr.get("denominator").and_then(|x| x.as_i64()).unwrap_or(1);
    let interlace_mode = v
        .get("interlace_mode")
        .and_then(|x| x.as_str())
        .unwrap_or("progressive")
        .to_string();

    Ok(FlowFormat {
        frame_width,
        frame_height,
        grain_rate: GrainRate { numerator, denominator },
        interlace_mode,
    })
}

/// Human-readable `WxH @ num/den mode` summary, or `—` for an empty slot.
pub fn fmt_slot_summary(fmt: &Option<FlowFormat>) -> String {
    match fmt {
        None => "—".to_string(),
        Some(f) => format!(
            "{}x{} @ {}/{} {}",
            f.frame_width, f.frame_height, f.grain_rate.numerator, f.grain_rate.denominator, f.interlace_mode
        ),
    }
}

/// Read each non-None input's format and verify they all match.
///
/// Returns `(common_format, errors, per_slot)`:
///   - `common_format`: the reference format if all match, else `None`.
///   - `errors`: human-readable mismatch / read-error descriptions.
///   - `per_slot`: per-slot format (or `None` for empty / unreadable slots).
pub fn validate_inputs(
    domain_path: &str,
    input_flow_uuids: &[Option<String>],
) -> (Option<FlowFormat>, Vec<String>, Vec<Option<FlowFormat>>) {
    let mut per_slot: Vec<Option<FlowFormat>> = vec![None; input_flow_uuids.len()];
    let mut errors: Vec<String> = Vec::new();

    for (idx, uuid) in input_flow_uuids.iter().enumerate() {
        let Some(uuid) = uuid else { continue };
        match read_flow_format(domain_path, uuid) {
            Ok(f) => per_slot[idx] = Some(f),
            Err(e) => errors.push(format!("Input {}: could not read flow format — {}", idx + 1, e)),
        }
    }

    let selected: Vec<(usize, &FlowFormat)> = per_slot
        .iter()
        .enumerate()
        .filter_map(|(i, f)| f.as_ref().map(|f| (i, f)))
        .collect();

    let Some(&(ref_idx, ref_fmt)) = selected.first() else {
        return (None, errors, per_slot);
    };
    let ref_fmt = ref_fmt.clone();

    for &(idx, fmt) in &selected[1..] {
        let mut diffs = Vec::new();
        if fmt.frame_width != ref_fmt.frame_width || fmt.frame_height != ref_fmt.frame_height {
            diffs.push(format!(
                "raster {}x{} ≠ {}x{}",
                fmt.frame_width, fmt.frame_height, ref_fmt.frame_width, ref_fmt.frame_height
            ));
        }
        if fmt.grain_rate != ref_fmt.grain_rate {
            diffs.push(format!(
                "grain_rate {}/{} ≠ {}/{}",
                fmt.grain_rate.numerator,
                fmt.grain_rate.denominator,
                ref_fmt.grain_rate.numerator,
                ref_fmt.grain_rate.denominator
            ));
        }
        if fmt.interlace_mode != ref_fmt.interlace_mode {
            diffs.push(format!(
                "interlace_mode {} ≠ {}",
                fmt.interlace_mode, ref_fmt.interlace_mode
            ));
        }
        if !diffs.is_empty() {
            errors.push(format!(
                "Input {} differs from Input {}: {}",
                idx + 1,
                ref_idx + 1,
                diffs.join(", ")
            ));
        }
    }

    let common = if errors.is_empty() { Some(ref_fmt) } else { None };
    (common, errors, per_slot)
}
