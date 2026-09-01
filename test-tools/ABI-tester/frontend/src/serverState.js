// SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
// SPDX-License-Identifier: Apache-2.0
import { useState, useEffect } from "react";

const API = "";

// One /state for the whole app. The builder and the transport bar both need it, and
// two polls would mean two copies of `running` and `handles` that can disagree for a
// second -- so App owns it and passes it down, the same way it already owns useLog.
export function useServerState(pollMs = 1000) {
  const [state, setState] = useState({});

  useEffect(() => {
    let alive = true;
    async function poll() {
      try {
        const res = await fetch(API + "/state");
        const body = await res.json();
        if (alive && body?.handles) setState(body);     // the whole body, not just the handles
      } catch { /* nothing to say: the console's own poll already reports a dead backend */}
    }
    poll();
    const id = setInterval(poll, pollMs);
    return () => { alive = false; clearInterval(id); };
  }, [pollMs]);

  // Every transport POST answers with a full engine.state() -- /reset adds "released"
  // on top. Feeding that response straight in flips the buttons on the response
  // instead of up to a second later on the next poll. Unused until 15-2.
  const apply = (body) => { if (body?.handles) setState(body); };

  return { state, apply };
}