// SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
// SPDX-License-Identifier: Apache-2.0
//
// MXL Input Selector — native Rust backend.
//
// Serves the REST API and the built React frontend on port 9600, and runs a
// grain-replicating router that routes 1-of-N MXL video flows to a single MXL
// output flow with frame-accurate, glitch-free switching.

mod api;
mod black;
mod flowdef;
mod format;
mod router;
mod scan;

use std::sync::Arc;

use tokio::net::TcpListener;
use tracing_subscriber::EnvFilter;

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info")))
        .init();

    let state = Arc::new(api::AppState::new());
    state.rescan_domains();

    let app = api::router(state);

    let port = std::env::var("PORT").ok().and_then(|v| v.parse::<u16>().ok()).unwrap_or(9600);
    let addr = format!("0.0.0.0:{port}");
    let listener = TcpListener::bind(&addr)
        .await
        .unwrap_or_else(|e| panic!("failed to bind {addr}: {e}"));
    tracing::info!("MXL Input Selector listening on {addr}");
    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown_signal())
        .await
        .expect("server error");
    tracing::info!("Shutting down");
}

/// Resolve on SIGTERM (e.g. `docker stop`) or SIGINT (Ctrl-C).  Without this the
/// process — which runs as PID 1 in the container — would ignore SIGTERM (PID 1
/// has no default signal disposition), forcing Docker to wait out its grace
/// period and SIGKILL it ~10 s later.
async fn shutdown_signal() {
    let ctrl_c = async {
        let _ = tokio::signal::ctrl_c().await;
    };

    #[cfg(unix)]
    let terminate = async {
        match tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()) {
            Ok(mut sig) => {
                sig.recv().await;
            }
            Err(e) => tracing::warn!("failed to install SIGTERM handler: {e}"),
        }
    };
    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();

    tokio::select! {
        _ = ctrl_c => {}
        _ = terminate => {}
    }
}
