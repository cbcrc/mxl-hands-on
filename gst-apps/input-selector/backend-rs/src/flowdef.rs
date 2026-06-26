// SPDX-License-Identifier: Apache-2.0
//
// Output flow-definition derivation.
//
// The output flow inherits its raster / grain_rate / colorspace / components /
// media_type from the first selected MXL input (read back via
// `MxlInstance::get_flow_def`), with the id replaced by a deterministic UUIDv5
// and the description / label / grouphint / NMOS group-hint tag set from the
// user's configuration.  This guarantees the output is byte-compatible with the
// inputs so grain payloads can be copied verbatim.

use std::path::Path;

use serde_json::{json, Value};
use uuid::Uuid;

/// Fixed namespace for the deterministic output UUIDv5.  Must match the value
/// used by the previous Python backend so restarts overwrite the same flow
/// directory.  Treat as immutable.
const MXL_SELECTOR_NS: Uuid = Uuid::from_bytes([
    0xc4, 0xb1, 0x8e, 0x9f, 0x2d, 0x31, 0x5a, 0x4f, 0x8e, 0x6b, 0x7c, 0x9d, 0x0a, 0x1b, 0x2c, 0x3d,
]);

/// Deterministic output flow UUID, derived from the group hint.
pub fn output_flow_uuid(grouphint: &str) -> String {
    Uuid::new_v5(&MXL_SELECTOR_NS, format!("{grouphint}:video").as_bytes()).to_string()
}

/// Build the output flow-def JSON from a source flow-def JSON string.
pub fn build_output_flow_def(
    src_def_json: &str,
    output_uuid: &str,
    grouphint: &str,
    description: &str,
    label: &str,
) -> Result<String, String> {
    let mut v: Value =
        serde_json::from_str(src_def_json).map_err(|e| format!("parse source flow_def: {e}"))?;
    apply_metadata(&mut v, output_uuid, grouphint, description, label)?;
    serde_json::to_string_pretty(&v).map_err(|e| e.to_string())
}

/// After the flow is created, re-assert the metadata on the on-disk
/// `flow_def.json` in case the MXL writer re-serialised it through a struct that
/// dropped the `grouphint` field (mirrors the Python backend's patch step, but
/// without polling — the file exists synchronously once the writer is created).
pub fn patch_flow_def_file(
    domain_path: &str,
    output_uuid: &str,
    grouphint: &str,
    description: &str,
    label: &str,
) -> Result<(), String> {
    let path = Path::new(domain_path)
        .join(format!("{output_uuid}.mxl-flow"))
        .join("flow_def.json");
    let text = std::fs::read_to_string(&path).map_err(|e| format!("read {}: {e}", path.display()))?;
    let mut v: Value = serde_json::from_str(&text).map_err(|e| e.to_string())?;
    apply_metadata(&mut v, output_uuid, grouphint, description, label)?;
    let out = serde_json::to_string_pretty(&v).map_err(|e| e.to_string())?;
    std::fs::write(&path, out).map_err(|e| format!("write {}: {e}", path.display()))
}

fn apply_metadata(
    v: &mut Value,
    output_uuid: &str,
    grouphint: &str,
    description: &str,
    label: &str,
) -> Result<(), String> {
    let obj = v.as_object_mut().ok_or("flow_def is not a JSON object")?;
    let full_grouphint = format!("{grouphint}:Video");
    obj.insert("id".into(), json!(output_uuid));
    obj.insert("description".into(), json!(description));
    obj.insert("label".into(), json!(label));
    obj.insert("grouphint".into(), json!(full_grouphint));
    let tags = obj.entry("tags").or_insert_with(|| json!({}));
    if let Some(t) = tags.as_object_mut() {
        t.insert("urn:x-nmos:tag:grouphint/v1.0".into(), json!([full_grouphint]));
    }
    Ok(())
}
