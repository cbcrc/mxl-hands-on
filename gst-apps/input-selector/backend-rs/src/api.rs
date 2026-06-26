// SPDX-License-Identifier: Apache-2.0
//
// HTTP API + application state.  Endpoint paths and response shapes match the
// previous Python/FastAPI backend so the React frontend needs only minimal
// changes.  All request/response types are typed serde DTOs so an OpenAPI/
// Swagger layer (utoipa) can be added later without refactoring handlers.

use std::sync::{Arc, Mutex};

use axum::{
    extract::{Query, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use serde::Deserialize;
use serde_json::{json, Value};
use tower_http::services::{ServeDir, ServeFile};

use crate::flowdef::output_flow_uuid;
use crate::format::{fmt_slot_summary, validate_inputs, FlowFormat};
use crate::router::{start_router, RouterConfig, RouterHandle};
use crate::scan::{scan_domain_path, scan_domains, Domain};

pub struct AppState {
    domain_root: String,
    mxl_info_bin: String,
    frontend_dist: String,
    max_inputs: usize,
    domains: Mutex<Vec<Domain>>,
    pipeline: Mutex<Pipeline>,
}

#[derive(Default)]
struct Pipeline {
    handle: Option<RouterHandle>,
    domain_path: Option<String>,
    input_flow_uuids: Vec<Option<String>>,
    slot_kinds: Vec<String>,
    output_flow_uuid: Option<String>,
    format: Option<FlowFormat>,
    grouphint: Option<String>,
    description: Option<String>,
    label: Option<String>,
}

impl AppState {
    pub fn new() -> Self {
        let max_inputs = std::env::var("MAX_INPUTS")
            .ok()
            .and_then(|v| v.parse::<usize>().ok())
            .filter(|&n| n >= 1)
            .unwrap_or(3);
        Self {
            domain_root: std::env::var("MXL_DOMAIN").unwrap_or_else(|_| "/mxl-domain".into()),
            mxl_info_bin: std::env::var("MXL_INFO_BIN")
                .unwrap_or_else(|_| "/opt/mxl/tools/mxl-info/mxl-info".into()),
            frontend_dist: std::env::var("FRONTEND_DIST")
                .unwrap_or_else(|_| "/app/frontend/dist".into()),
            max_inputs,
            domains: Mutex::new(Vec::new()),
            pipeline: Mutex::new(Pipeline::default()),
        }
    }

    pub fn rescan_domains(&self) -> Vec<Domain> {
        let domains = scan_domains(&self.domain_root);
        *self.domains.lock().unwrap() = domains.clone();
        domains
    }
}

pub fn router(state: Arc<AppState>) -> Router {
    let dist = state.frontend_dist.clone();
    let index = format!("{dist}/index.html");
    let serve_dir = ServeDir::new(&dist).not_found_service(ServeFile::new(index));

    Router::new()
        .route("/get-domains", post(get_domains))
        .route("/domains", get(domains))
        .route("/scan-domain", get(scan_domain))
        .route("/pipeline/start", post(pipeline_start))
        .route("/pipeline/stop", post(pipeline_stop))
        .route("/pipeline/status", get(pipeline_status))
        .route("/pipeline/active-input", post(pipeline_active_input))
        .route("/config", get(config))
        .fallback_service(serve_dir)
        .with_state(state)
}

// ── Request DTOs ──────────────────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
struct StartConfig {
    domain_path: String,
    #[serde(default)]
    input_flow_uuids: Vec<Option<String>>,
    #[serde(default = "default_grouphint")]
    grouphint: String,
    #[serde(default = "default_description")]
    description: String,
    #[serde(default = "default_label")]
    label: String,
}

fn default_grouphint() -> String {
    "Input-Selector".into()
}
fn default_description() -> String {
    "selector-out-1".into()
}
fn default_label() -> String {
    "input-selector-video".into()
}

#[derive(Debug, Deserialize)]
struct ActiveInputBody {
    slot: usize,
}

#[derive(Debug, Deserialize)]
struct ScanQuery {
    domain_path: String,
}

// ── Status serialisation ──────────────────────────────────────────────────────

fn status_json(p: &Pipeline) -> Value {
    let running = p.handle.is_some();
    let active = p.handle.as_ref().map(|h| h.active_slot());
    let error = p.handle.as_ref().and_then(|h| h.last_error());
    json!({
        "running": running,
        "domain_path": p.domain_path,
        "input_flow_uuids": p.input_flow_uuids,
        "slot_kinds": p.slot_kinds,
        "active_input": active,
        "output_flow_uuid": p.output_flow_uuid,
        "format": p.format,
        "grouphint": p.grouphint,
        "description": p.description,
        "label": p.label,
        "error": error,
    })
}

// ── Handlers ──────────────────────────────────────────────────────────────────

async fn get_domains(State(state): State<Arc<AppState>>) -> Json<Vec<Domain>> {
    Json(state.rescan_domains())
}

async fn domains(State(state): State<Arc<AppState>>) -> Json<Vec<Domain>> {
    Json(state.domains.lock().unwrap().clone())
}

async fn scan_domain(
    State(state): State<Arc<AppState>>,
    Query(q): Query<ScanQuery>,
) -> Response {
    match scan_domain_path(&state.mxl_info_bin, &q.domain_path) {
        Ok(flows) => Json(flows).into_response(),
        Err(e) => (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({ "detail": e }))).into_response(),
    }
}

async fn config(State(state): State<Arc<AppState>>) -> Json<Value> {
    Json(json!({ "max_inputs": state.max_inputs }))
}

async fn pipeline_status(State(state): State<Arc<AppState>>) -> Json<Value> {
    Json(status_json(&state.pipeline.lock().unwrap()))
}

async fn pipeline_start(
    State(state): State<Arc<AppState>>,
    Json(cfg): Json<StartConfig>,
) -> Response {
    let uuids = cfg.input_flow_uuids;
    if uuids.is_empty() || uuids.len() > state.max_inputs {
        return bad_request(format!(
            "input_flow_uuids must have between 1 and {} entries",
            state.max_inputs
        ));
    }
    if cfg.description.trim().is_empty() || cfg.label.trim().is_empty() {
        return bad_request("description and label are required".into());
    }

    let (common, mut errors, per_slot) = validate_inputs(&cfg.domain_path, &uuids);
    if common.is_none() || !errors.is_empty() {
        if common.is_none() && errors.is_empty() {
            errors.push(
                "At least one MXL input flow must be selected (cannot derive output format from \
                 black-only inputs)."
                    .into(),
            );
        }
        let per_slot_strings: Vec<String> = per_slot.iter().map(fmt_slot_summary).collect();
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({
                "detail": "Input formats do not match",
                "errors": errors,
                "per_slot": per_slot_strings,
            })),
        )
            .into_response();
    }
    let common = common.unwrap();
    let out_uuid = output_flow_uuid(&cfg.grouphint);
    let slot_kinds: Vec<String> = uuids
        .iter()
        .map(|u| if u.is_some() { "mxl" } else { "black" }.to_string())
        .collect();

    let mut pipeline = state.pipeline.lock().unwrap();
    if let Some(mut h) = pipeline.handle.take() {
        h.stop();
    }

    let rcfg = RouterConfig {
        domain_path: cfg.domain_path.clone(),
        slots: uuids.clone(),
        output_uuid: out_uuid.clone(),
        grouphint: cfg.grouphint.clone(),
        description: cfg.description.clone(),
        label: cfg.label.clone(),
    };

    match start_router(rcfg) {
        Ok(handle) => {
            *pipeline = Pipeline {
                handle: Some(handle),
                domain_path: Some(cfg.domain_path),
                input_flow_uuids: uuids,
                slot_kinds,
                output_flow_uuid: Some(out_uuid),
                format: Some(common),
                grouphint: Some(cfg.grouphint),
                description: Some(cfg.description),
                label: Some(cfg.label),
            };
            Json(status_json(&pipeline)).into_response()
        }
        Err(e) => {
            *pipeline = Pipeline::default();
            (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({ "detail": e }))).into_response()
        }
    }
}

async fn pipeline_stop(State(state): State<Arc<AppState>>) -> Json<Value> {
    let mut pipeline = state.pipeline.lock().unwrap();
    if let Some(mut h) = pipeline.handle.take() {
        h.stop();
    }
    *pipeline = Pipeline::default();
    Json(status_json(&pipeline))
}

async fn pipeline_active_input(
    State(state): State<Arc<AppState>>,
    Json(body): Json<ActiveInputBody>,
) -> Response {
    let pipeline = state.pipeline.lock().unwrap();
    match &pipeline.handle {
        Some(h) => match h.set_active(body.slot) {
            Ok(()) => Json(status_json(&pipeline)).into_response(),
            Err(e) => bad_request(e),
        },
        None => (
            StatusCode::CONFLICT,
            Json(json!({ "detail": "Pipeline is not running" })),
        )
            .into_response(),
    }
}

fn bad_request(detail: String) -> Response {
    (StatusCode::BAD_REQUEST, Json(json!({ "detail": detail }))).into_response()
}
