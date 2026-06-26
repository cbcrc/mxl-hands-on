// SPDX-License-Identifier: Apache-2.0
//
// Domain and flow discovery — ported from the Python backend's `main.py`
// (`_scan_domains`, `_parse_scan_output`, `_read_flow_description`).  Flow
// listing shells out to `mxl-info -d <domain>` so the "active flows only"
// behaviour matches mxl-info-gui / mxl2webrtc exactly.

use std::path::{Path, PathBuf};
use std::process::Command;

use regex::Regex;
use serde::Serialize;

#[derive(Debug, Clone, Serialize)]
pub struct Domain {
    pub id: String,
    pub label: String,
    pub description: String,
    pub path: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct Flow {
    pub flow_uuid: String,
    pub flow_label: String,
    pub flow_grouphint: String,
    pub description: String,
}

/// Recursively walk `root` for `domain_def.json` files and parse the domain id,
/// label, description and containing directory.
pub fn scan_domains(root: &str) -> Vec<Domain> {
    let mut found = Vec::new();
    let root = Path::new(root);
    if !root.exists() {
        tracing::warn!("MXL domain root {} does not exist", root.display());
        return found;
    }
    let mut def_files = Vec::new();
    collect_files(root, "domain_def.json", &mut def_files);
    def_files.sort();
    for def in def_files {
        match std::fs::read_to_string(&def) {
            Ok(text) => match serde_json::from_str::<serde_json::Value>(&text) {
                Ok(v) => found.push(Domain {
                    id: v.get("id").and_then(|x| x.as_str()).unwrap_or("unknown").to_string(),
                    label: v.get("label").and_then(|x| x.as_str()).unwrap_or("").to_string(),
                    description: v
                        .get("description")
                        .and_then(|x| x.as_str())
                        .unwrap_or("")
                        .to_string(),
                    path: def.parent().map(|p| p.to_string_lossy().to_string()).unwrap_or_default(),
                }),
                Err(e) => tracing::warn!("Could not parse {}: {}", def.display(), e),
            },
            Err(e) => tracing::warn!("Could not read {}: {}", def.display(), e),
        }
    }
    tracing::info!("Domain scan found {} domain(s)", found.len());
    found
}

fn collect_files(dir: &Path, name: &str, out: &mut Vec<PathBuf>) {
    let entries = match std::fs::read_dir(dir) {
        Ok(e) => e,
        Err(_) => return,
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            collect_files(&path, name, out);
        } else if path.file_name().map(|n| n == name).unwrap_or(false) {
            out.push(path);
        }
    }
}

/// Run `mxl-info -d <domain_path>`, parse the output into a flow list, and
/// attach each flow's description from its `flow_def.json`.
pub fn scan_domain_path(mxl_info_bin: &str, domain_path: &str) -> Result<Vec<Flow>, String> {
    let output = Command::new(mxl_info_bin)
        .arg("-d")
        .arg(domain_path)
        .output()
        .map_err(|e| {
            if e.kind() == std::io::ErrorKind::NotFound {
                format!("mxl-info binary not found at {mxl_info_bin}")
            } else {
                e.to_string()
            }
        })?;

    if !output.status.success() {
        tracing::warn!("mxl-info stderr: {}", String::from_utf8_lossy(&output.stderr).trim());
    }

    let stdout = String::from_utf8_lossy(&output.stdout);
    let mut flows = parse_scan_output(&stdout);
    for flow in &mut flows {
        flow.description = read_flow_description(domain_path, &flow.flow_uuid);
    }
    Ok(flows)
}

/// Port of `_parse_scan_output`.  `mxl-info -d` prints groups as non-indented
/// lines and the flows under each group as indented `Role : <uuid> - <label>`
/// lines.  A leading `Domain Definition:` block (also indented) must be skipped.
fn parse_scan_output(stdout: &str) -> Vec<Flow> {
    let flow_re =
        Regex::new(r"(?i)^\s+(.+?)\s*:\s*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\s*-\s*(.+)$")
            .unwrap();
    let group_re = Regex::new(r"^([^:]+):").unwrap();

    let mut flows = Vec::new();
    let mut current_group = String::new();
    let mut in_domain_def = false;

    for line in stdout.lines() {
        if line.trim().is_empty() {
            continue;
        }
        let first = line.chars().next().unwrap();
        if first == '\t' || first == ' ' {
            // Indented line — a flow entry (or part of the domain-def block).
            if in_domain_def {
                continue;
            }
            if let Some(caps) = flow_re.captures(line) {
                let mut role = caps[1].trim().to_string();
                let uuid = caps[2].trim().to_string();
                let label = caps[3].trim().to_string();
                if role.eq_ignore_ascii_case("MISSING ROLE") {
                    role = String::new();
                }
                let grouphint = if !current_group.is_empty() && !role.is_empty() {
                    format!("{current_group}:{role}")
                } else if !current_group.is_empty() {
                    current_group.clone()
                } else {
                    role
                };
                flows.push(Flow {
                    flow_uuid: uuid,
                    flow_label: label,
                    flow_grouphint: grouphint,
                    description: String::new(),
                });
            }
        } else {
            // Non-indented line — group header, or the domain-def block start.
            let trimmed = line.trim();
            if trimmed.starts_with("Domain Definition") {
                in_domain_def = true;
                continue;
            }
            in_domain_def = false;
            if trimmed.starts_with("Invalid group name") {
                current_group = String::new();
            } else if let Some(caps) = group_re.captures(line) {
                current_group = caps[1].trim().to_string();
            }
        }
    }
    flows
}

fn read_flow_description(domain_path: &str, flow_uuid: &str) -> String {
    let path = Path::new(domain_path)
        .join(format!("{flow_uuid}.mxl-flow"))
        .join("flow_def.json");
    match std::fs::read_to_string(&path) {
        Ok(text) => serde_json::from_str::<serde_json::Value>(&text)
            .ok()
            .and_then(|v| v.get("description").and_then(|x| x.as_str()).map(String::from))
            .unwrap_or_default(),
        Err(_) => String::new(),
    }
}
