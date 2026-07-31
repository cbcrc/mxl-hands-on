<!--
SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
SPDX-License-Identifier: Apache-2.0
-->

# gst-apps on Kubernetes — deployment guide

Deploys the `gst-apps` MXL stack to the lab k3s cluster (`pre-prod` context) as plain YAML.
Helm migration comes later; see the last section.

## Current state (as of 2026-07-29)

Deployed and verified working in namespace `gst-apps`, all pods pinned to **`${MXL_NODE}`**:

| App | File | URL | Verified |
| --- | --- | --- | --- |
| test-generator | `30-test-generator.yaml` | http://${MXL_LB_IP_APPS}:9600 | writes video+audio flows |
| mxl2webrtc | `50-mxl2webrtc.yaml` | http://${MXL_LB_IP_APPS}:9601 | video+audio play in browser |
| file-player | `60-file-player.yaml` | http://${MXL_LB_IP_APPS}:9602 | plays `sizzle.ts` |
| hls2mxl | `70-hls2mxl.yaml` | http://${MXL_LB_IP_APPS}:9603 | up |
| input-selector | `80-input-selector.yaml` | http://${MXL_LB_IP_APPS}:9604 | up |
| mxl-info-gui | `20-mxl-info-gui.yaml` | http://${MXL_LB_IP_APPS}:9699 | lists domain + flows |
| mediamtx | `40-mediamtx.yaml` | http://${MXL_LB_IP_MEDIAMTX}:8889 | relays WHIP→WHEP |

Supporting objects: `00-namespace.yaml`, `10-domain.yaml` (PV + PVC + ConfigMap).

**Still to deploy: `html5-keyer` and `webrtc2mxl`.** Both below.

## The design, and why

**The MXL domain is node-local shared memory.** Flows are memory-mapped ring buffers in a
tmpfs directory. `dmf-mxl`'s `DomainWatcher` needs real `inotify` and `mxlIsFlowActive()`
needs real `flock()`, so a network filesystem breaks it — this is why the older
`k8s/kube-deployment.yaml` backing the domain with Longhorn RWX was wrong. Consequences:

- One directory, `/dev/shm/mxl/gst-apps`, on one node. `/dev/shm` is already tmpfs, so no
  privileged container is needed to create it — unlike `dmf-mxl/examples/kube-example.yaml`,
  which uses a `local:` PV and therefore needs a privileged `systemd-run mount` helper. We use
  `hostPath` + `type: DirectoryOrCreate` and the kubelet creates it.
- **Every pod must be pinned to that node**, and the pod `nodeSelector` must match the PV's
  `nodeAffinity`. If they disagree, pods hang in `Pending` forever. This is the single most
  common failure here.
- `storageClassName: mxl-domain` is a **fake class name** — it does not exist in the cluster.
  It just stops an unrelated PVC from claiming the PV. `mxl-test` and `mxl-testbench` use the
  same trick.
- `replicas: 1` everywhere, and **do not raise it**. These apps write to a shared ring buffer;
  they are not horizontally scalable.

**The domain is volatile.** `/dev/shm` is RAM-backed, so a node reboot wipes it. Every app pod
therefore carries an identical `initContainer` that re-seeds `domain_def.json` from the
`mxl-domain-def` ConfigMap on every start. It uses plain `cp` (not `cp -n`) so the ConfigMap is
the single source of truth and a corrupt definition self-repairs on restart. The `chmod` is
deliberately **not** recursive — a recursive chmod would walk every flow file on every pod start.

**Images are public.** All eight apps are on `ghcr.io/cbcrc/*` with anonymous pull. No
`imagePullSecrets`. `docker/exercise-4/docker-compose.yml` is the registry-based twin of
`gst-apps/docker-compose.yml` and is the better conversion reference.

**Three apps `apt-get install` at container startup**, not build time: `file-player` and
`hls2mxl` install `gstreamer1.0-libav`; `mxl2webrtc` installs `gstreamer1.0-plugins-ugly`.
This is deliberate — GPL codecs are kept out of the published images. It means those pods need
outbound internet **every time they start**, and their first start is slow. Verified working
in this cluster.

**`input-selector` carries `MAX_INPUTS: "3"` explicitly.** The Rust backend defaults to 3
(`gst-apps/input-selector/backend-rs/src/api.rs:51-55`) and so does the frontend, so omitting it
happens to behave correctly — but it left the manifest silently diverging from
`docker-compose.yml`, which passes `MAX_INPUTS=${INPUT_SELECTOR_MAX_INPUTS:-3}`. It is set
explicitly so the knob is where an operator looks for it. Values below 1 or unparseable are
silently ignored and fall back to 3; there is no upper bound.

**No `hostPort`, no `hostNetwork`.** Compose runs MediaMTX with `network_mode: host` so ICE
candidates carry a real host IP. The Kubernetes equivalent is `MTX_WEBRTCADDITIONALHOSTS`,
which tells MediaMTX what address to advertise while it sits behind a normal Service. Using
`hostPort` instead would collide with the MediaMTX pods already running in `mxl-test` and
`mxl-testbench` — one of those has been stuck `Pending` for hours for exactly that reason.

**Exposure: two MetalLB addresses.** The eight app UIs share `${MXL_LB_IP_APPS}` with the compose port
numbers preserved — every app listens on 9600 inside its container and the Service maps the
familiar port to it. They all carry:

```yaml
  annotations:
    metallb.universe.tf/allow-shared-ip: gst-apps
    metallb.universe.tf/loadBalancerIPs: ${MXL_LB_IP_APPS}
spec:
  externalTrafficPolicy: Cluster
```

`externalTrafficPolicy: Cluster` is **required** for a shared IP — MetalLB only shares an address
when all services are `Cluster` or all target the same pods. Do not set `Local` on these.

**MediaMTX is the exception: it holds `${MXL_LB_IP_MEDIAMTX}` alone, with `externalTrafficPolicy: Local`.**
It is the only service carrying media rather than a web UI, and `Cluster` costs it a cross-node
SNAT hop on every packet — see the resolved item 2 in "Known issues" for the full reasoning and
the tradeoff this accepts. Because it no longer shares, it has no `allow-shared-ip` key:

```yaml
  annotations:
    metallb.universe.tf/loadBalancerIPs: ${MXL_LB_IP_MEDIAMTX}
spec:
  externalTrafficPolicy: Local
```

Do not add `metallb.universe.tf/address-pool` alongside it — `loadBalancerIPs` already pins the
address and MetalLB infers the pool, so the annotation does nothing except emit a second
`deprecatedAnnotation` warning into `kubectl describe`.

Both browser-facing MediaMTX URLs (`MEDIAMTX_WEBRTC_URL` in `50-mxl2webrtc.yaml`,
`MEDIAMTX_WHIP_URL` in `95-webrtc2mxl.yaml`) must therefore name `${MXL_LB_IP_MEDIAMTX}:8889`, **with the
port explicit** — the frontends build the address from `cfg.origin`, which drops a default port.
The in-cluster URLs are unaffected; they use Service DNS.

---

## Remaining step 1 — `html5-keyer`

The heaviest image in the stack: CEF/Chromium keyed over an MXL video flow, plus a
teleprompter mode with offline Vosk speech recognition. Create
`k8s/gst-apps/90-html5-keyer.yaml`:

```yaml
# SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
# SPDX-License-Identifier: Apache-2.0
apiVersion: apps/v1
kind: Deployment
metadata:
  name: html5-keyer
  namespace: gst-apps
spec:
  replicas: 1
  selector:
    matchLabels:
      app: html5-keyer
  template:
    metadata:
      labels:
        app: html5-keyer
    spec:
      nodeSelector:
        kubernetes.io/hostname: ${MXL_NODE}
      initContainers:
        - name: seed-domain
          image: busybox:1.36
          command:
            - sh
            - -c
            - |
              set -e
              cp /seed/domain_def.json /mxl-domain/domain_def.json
              chmod a+rwX /mxl-domain
              chmod a+rw /mxl-domain/domain_def.json
          volumeMounts:
            - name: mxl-domain
              mountPath: /mxl-domain
            - name: seed
              mountPath: /seed
      containers:
        - name: app
          image: ghcr.io/cbcrc/html5-keyer:latest
          imagePullPolicy: Always
          ports:
            - name: http
              containerPort: 9600
          env:
            - name: MXL_DOMAIN
              value: /mxl-domain
            - name: KEYER_DEFAULT_MODE
              value: "key"
          volumeMounts:
            - name: mxl-domain
              mountPath: /mxl-domain
            - name: dshm
              mountPath: /dev/shm
          resources:
            requests:
              cpu: "2"
              memory: 4Gi
            limits:
              cpu: "8"
              memory: 8Gi
      volumes:
        - name: mxl-domain
          persistentVolumeClaim:
            claimName: mxl-domain
        - name: seed
          configMap:
            name: mxl-domain-def
        - name: dshm
          emptyDir:
            medium: Memory
            sizeLimit: 1Gi
---
apiVersion: v1
kind: Service
metadata:
  name: html5-keyer
  namespace: gst-apps
  annotations:
    metallb.universe.tf/allow-shared-ip: gst-apps
    metallb.universe.tf/loadBalancerIPs: ${MXL_LB_IP_APPS}
spec:
  type: LoadBalancer
  externalTrafficPolicy: Cluster
  selector:
    app: html5-keyer
  ports:
    - name: http
      port: 9605
      targetPort: 9600
      protocol: TCP
```

Three things differ from every other app:

- **`dshm` volume at `/dev/shm`.** This is compose's `shm_size: "1gb"`. CEF/Chromium spawns
  helper processes and crashes on the default 64 MB. `medium: Memory` makes it tmpfs.
  Note it counts against the pod's memory limit, which is why the limit is 8Gi.
- **Real resource limits.** It runs Xvfb at 3840x2160 plus a full Chromium. This is the only
  app in the stack with `limits` — everything else is requests-only.
- **Slow first start.** The image carries a ~600 MB CEF distribution and two Vosk models, so
  expect a long `ContainerCreating`. That is the image pull, not a hang. Watch it with
  `kubectl get pods -w`.

It needs no extra privileges — the entrypoint launches Chromium with `--no-sandbox` already.

**Test:** http://${MXL_LB_IP_APPS}:9605 — key a page over the test-generator flow, then confirm the
keyed output flow appears in `mxl-info-gui`.

---

## Remaining step 2 — `webrtc2mxl`

The reverse of `mxl2webrtc`: captures the browser microphone, publishes it to MediaMTX via
WHIP, pulls it back via WHEP, and writes an MXL audio flow. Create
`k8s/gst-apps/95-webrtc2mxl.yaml` — identical to `50-mxl2webrtc.yaml` except for the name,
image, port, and the two URLs:

```yaml
# SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
# SPDX-License-Identifier: Apache-2.0
apiVersion: apps/v1
kind: Deployment
metadata:
  name: webrtc2mxl
  namespace: gst-apps
spec:
  replicas: 1
  selector:
    matchLabels:
      app: webrtc2mxl
  template:
    metadata:
      labels:
        app: webrtc2mxl
    spec:
      nodeSelector:
        kubernetes.io/hostname: ${MXL_NODE}
      initContainers:
        - name: seed-domain
          image: busybox:1.36
          command:
            - sh
            - -c
            - |
              set -e
              cp /seed/domain_def.json /mxl-domain/domain_def.json
              chmod a+rwX /mxl-domain
              chmod a+rw /mxl-domain/domain_def.json
          volumeMounts:
            - name: mxl-domain
              mountPath: /mxl-domain
            - name: seed
              mountPath: /seed
      containers:
        - name: app
          image: ghcr.io/cbcrc/webrtc2mxl:latest
          imagePullPolicy: Always
          ports:
            - name: http
              containerPort: 9600
          env:
            - name: MXL_DOMAIN
              value: /mxl-domain
            - name: MEDIAMTX_WHEP_URL
              value: "http://mediamtx:8889/webrtc2mxl/whep"
            - name: MEDIAMTX_WHIP_URL
              value: "http://${MXL_LB_IP_MEDIAMTX}:8889/webrtc2mxl/whip"
          volumeMounts:
            - name: mxl-domain
              mountPath: /mxl-domain
          resources:
            requests:
              cpu: "1"
              memory: 1Gi
      volumes:
        - name: mxl-domain
          persistentVolumeClaim:
            claimName: mxl-domain
        - name: seed
          configMap:
            name: mxl-domain-def
---
apiVersion: v1
kind: Service
metadata:
  name: webrtc2mxl
  namespace: gst-apps
  annotations:
    metallb.universe.tf/allow-shared-ip: gst-apps
    metallb.universe.tf/loadBalancerIPs: ${MXL_LB_IP_APPS}
spec:
  type: LoadBalancer
  externalTrafficPolicy: Cluster
  selector:
    app: webrtc2mxl
  ports:
    - name: http
      port: 9606
      targetPort: 9600
      protocol: TCP
```

**The URL split is the thing to get right**, and it is the same distinction as in
`mxl2webrtc`. Compose used `host.docker.internal` for both and it didn't matter:

- `MEDIAMTX_WHEP_URL` — used by the **backend**, inside the cluster, to pull the stream.
  Must be Service DNS: `http://mediamtx:8889/...`
- `MEDIAMTX_WHIP_URL` — handed to the **browser** to publish to. Must be externally routable.

### The microphone needs a secure context — pick a workaround

This is the only app that calls `getUserMedia`, and **browsers only expose microphones in a secure
context** — HTTPS, or `localhost`. Over `http://${MXL_LB_IP_APPS}:9606` the browser does not define
`navigator.mediaDevices` *at all*, so `loadMics()` returns immediately
(`gst-apps/webrtc2mxl/frontend/src/App.jsx:238`) and the permission-priming call at `:273` throws.

**The symptom is an empty microphone list, not a permission prompt.** That is why it reads like a
deployment bug. It isn't one, and nothing in the manifests can fix it.

Both workarounds below are per-user. **Neither can be deployed cluster-wide**, and it is worth
being clear why: `kubectl port-forward` is a client-side tunnel whose entire value is producing a
`localhost` origin *on the viewer's own machine*. Nothing running in the cluster can do that for
somebody else's browser, and the tunnel dies whenever the pod restarts. The only mechanism that
would work for everyone is HTTPS — see item 8 in "Known issues" for why that is currently
unavailable here.

**Workaround A — tell Chrome to trust the origin.** No `kubectl` needed; persists per browser
profile. Chrome/Edge only. Open `chrome://flags/#unsafely-treat-insecure-origin-as-secure`, set it
to *Enabled*, put both origins in the box, and relaunch:

```
http://${MXL_LB_IP_APPS}:9606,http://${MXL_LB_IP_MEDIAMTX}:8889
```

MediaMTX's origin is in that list because flagging the page origin as trustworthy also brings the
page under Chrome's mixed-content rules, which would otherwise block the plain-HTTP WHIP POST.
Believed necessary rather than confirmed — it costs nothing to include, and if it *is* required
its absence is a confusing silent failure.

**Workaround B — port-forward.** Works in any browser; `localhost` is a secure context by
definition:

```sh
kubectl port-forward deploy/webrtc2mxl 9606:9600
```

then open **http://localhost:9606**.

**This got simpler with the item 1 fix in "Known issues".** The page is served from `localhost`,
but the WHIP host now comes from `MEDIAMTX_WHIP_URL` in the manifest, which is
`${MXL_LB_IP_MEDIAMTX}:8889` — a real address, so it is honoured instead of being rewritten to
`localhost`. On a machine that can reach the MetalLB address, that single port-forward is enough
and MediaMTX needs none.

If you are somewhere that *cannot* reach `${MXL_LB_IP_MEDIAMTX}`, point the app at a local alias so the
substitution kicks back in, and forward MediaMTX too:

```sh
kubectl set env deploy/webrtc2mxl MEDIAMTX_WHIP_URL=http://localhost:8889/webrtc2mxl/whip
kubectl port-forward deploy/mediamtx 8889:8889
```

Undo it afterwards with `kubectl set env deploy/webrtc2mxl MEDIAMTX_WHIP_URL-`, or re-apply the
manifest — otherwise every other user of the deployment is left pointing at their own machine.

---

## Final verification

```sh
kubectl get pods -o wide          # 9 pods Running, all on ${MXL_NODE}
kubectl get svc                   # eight on ${MXL_LB_IP_APPS}, mediamtx alone on .222
```

| App | URL |
| --- | --- |
| test-generator | http://${MXL_LB_IP_APPS}:9600 |
| mxl2webrtc | http://${MXL_LB_IP_APPS}:9601 |
| file-player | http://${MXL_LB_IP_APPS}:9602 |
| hls2mxl | http://${MXL_LB_IP_APPS}:9603 |
| input-selector | http://${MXL_LB_IP_APPS}:9604 |
| html5-keyer | http://${MXL_LB_IP_APPS}:9605 |
| webrtc2mxl | http://${MXL_LB_IP_APPS}:9606 (mic needs a secure-context workaround) |
| mxl-info-gui | http://${MXL_LB_IP_APPS}:9699 |
| mediamtx | http://${MXL_LB_IP_MEDIAMTX}:8889 |

The chain worth walking end to end:

1. **test-generator** → start bars+tone. **mxl-info-gui** lists the flows as *active*. Proves
   cross-pod shared memory — the whole point of the tmpfs design.
2. **mxl2webrtc** → select that flow → video plays. Proves the relay path and the
   server-DNS / browser-IP URL split.
3. **input-selector** → assign test-generator and file-player to two inputs, switch between
   them, watch the cut in mxl2webrtc. Proves multiple writers plus a reader on one domain.
4. **html5-keyer** → key a page over the test-generator flow. Proves CEF and the `/dev/shm`
   sizing.
5. **webrtc2mxl** → publish your mic, confirm the audio flow appears in mxl-info-gui.

Then the resilience check, which is the property the initContainers bought:

```sh
kubectl delete pod --all
kubectl get pods -w
```

Everything should come back unattended, with the domain re-seeded.

---

## Operations

**After a node reboot** nothing should be needed — `/dev/shm` is wiped, but each pod's
initContainer re-seeds the domain. If pods were running through the reboot and now show no
domain, `kubectl rollout restart deploy --all` fixes it.

**Debugging:**

```sh
kubectl logs -f deploy/<app>
kubectl describe pod -l app=<app>            # Events explain Pending / CrashLoop
kubectl exec deploy/mxl-info-gui -- ls -la /mxl-domain
kubectl port-forward deploy/<app> 9600:9600  # bypass MetalLB entirely
```

`port-forward` is the most useful tool here: it splits "is the app working" from "is the
networking working", which was the fastest path to every diagnosis during the initial rollout.

**Always validate before applying.** Both of the schema errors hit during rollout were
catchable locally:

```sh
kubectl apply -f k8s/gst-apps --dry-run=server
```

**Teardown:**

```sh
kubectl delete ns gst-apps
kubectl delete pv gst-apps-mxl-domain    # cluster-scoped, not in the namespace
```

The PV has `reclaimPolicy: Retain`, so `/dev/shm/mxl/gst-apps` is left on the node. It is
tmpfs — costs nothing and vanishes on reboot.

### Errors seen during rollout, and what they mean

| Symptom | Cause |
| --- | --- |
| `strict decoding error: unknown field "data"` on a Job | Missing `---` between YAML documents — they merged, last `kind:` won |
| `strict decoding error: unknown field "spec.selector.template"` | `template:` indented under `selector:` instead of beside it |
| `unknown field "volumes[1].ConfigMap"` | Field names are camelCase (`configMap`); only `kind:` values are PascalCase |
| `unable to parse quantity's suffix` | CPU uses `m`; memory uses `Ki`/`Mi`/`Gi`. Case-sensitive — `mi`, `MB`, `GB` are invalid |
| `not compatible with requested address pool` | Pool is `upp-services-pool-lab` ("lab", not "label") |
| `can't change sharing key` | A shared MetalLB IP needs the same `allow-shared-ip` key **and** `externalTrafficPolicy: Cluster` on all of them |
| Pod `Pending`, no node | `nodeSelector` disagrees with the PV's `nodeAffinity`, or the node is cordoned |
| App running but "0 domains" | Malformed `domain_def.json` — `kubectl logs` names the parse error and the column |
| ConfigMap edited but nothing changed | Editing a ConfigMap does not restart pods. `kubectl rollout restart deploy/<app>`. Changing a pod template *does* roll automatically |

---

## Known issues and follow-ups

**1. `App.jsx` overriding the configured MediaMTX host — FIXED (2026-07-30).** Both frontends
used to discard the hostname from `MEDIAMTX_WEBRTC_URL` / `MEDIAMTX_WHIP_URL` and substitute
`window.location.hostname`, keeping only scheme and port. That is free in Compose, where
everything is on one host, but in Kubernetes it forced **MediaMTX onto the same address as the
app UIs** — it could not have its own IP or its own `externalTrafficPolicy`.

They now substitute only when the configured hostname is an alias that cannot mean anything but
the local machine:

```js
const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "0.0.0.0", "host.docker.internal"]);
```

Anything else is honoured as-is via `cfg.origin`. See
`gst-apps/mxl2webrtc/frontend/src/App.jsx:311` and
`gst-apps/webrtc2mxl/frontend/src/App.jsx:251`. Compose is unaffected — its shipped defaults are
all local aliases, so it takes the same branch as before. The cluster manifests already set real
addresses, so those are now used verbatim. Note `cfg.origin` **drops a default port**, which is
what makes a future `https://…` behind Traefik work — but it also means a configured URL must
carry `:8889` explicitly. The missing port in `95-webrtc2mxl.yaml` was masked by the old
`cfg.port || "8889"` fallback and is now fixed.

Requires a rebuild and push of the `mxl2webrtc` and `webrtc2mxl` images — the Vite bundles are
baked in at image build time (see `how_to_build.md` Step 4).

This unblocks item 2, which still waits on item 3.

**2. MediaMTX traffic was SNAT'd cross-node — FIXED (2026-07-31).** With
`externalTrafficPolicy: Cluster`, all five nodes advertise `.199` over BGP and the switch's
ECMP hash picks the entry node per flow. MediaMTX session logs showed
`remote candidate: 10.42.5.0` — node 001's CNI gateway — while the pods run on node 002. Every
media packet took an extra hop, and audio glitched badly while `<sibling-node>` was
mid-Mellanox-maintenance with dead BGP, going clean once that node was fixed. Media quality
depended on the health of whichever unrelated node the flow happened to hash to.

MediaMTX now holds **`${MXL_LB_IP_MEDIAMTX}`** by itself with **`externalTrafficPolicy: Local`**, which
item 1's fix unblocked. Only `${MXL_NODE}` advertises that address, so there is no ECMP choice
and no extra hop, and client source IPs survive instead of being SNAT'd — which also stops the
translation from muddying ICE candidate matching. The app UIs are untouched and still share `.199`
on `Cluster`.

Verified 2026-07-31. The clearest evidence is the MetalLB speaker event in
`kubectl describe svc -n gst-apps mediamtx` — before the change it read
`announcing from node "<unrelated-node>"`, a node with no gst-apps pods on it at all; after, it
reads `announcing from node "${MXL_NODE}"`. In the MediaMTX logs, browser sessions now show a
real client address as the remote candidate (`prflx/udp/<client-ip>/...`) where they previously
showed a `10.42.x.0` CNI gateway. Sessions created by `10.42.x.x` are the mxl2webrtc backend pod
publishing over WHIP from inside the cluster — a pod IP is correct there.

During the switch, `describe` shows `ClearAssignment` / `can't change sharing key` events. Those
are the transition, not a failure: MetalLB must release `.199` before it can grant `.222`, and
`IPAllocated ["${MXL_LB_IP_MEDIAMTX}"]` follows a second later.

**The tradeoff this accepts:** MediaMTX now depends entirely on node 002's BGP. Item 5 records
both peer sessions on `<sibling-node>` dying silently after a driver update; the same failure on 002
would take MediaMTX fully offline rather than merely degrading it. Check BGP on 002 first if
MediaMTX becomes unreachable while the pods look healthy.

**3. Ask for a dedicated `IPAddressPool`.** `upp-services-pool-lab` is shared and other teams
claim addresses continuously — `.200`, `.201` and the `artisto-002` namespace all appeared
*during* the initial rollout, invalidating an address mid-session. The item 2 fix consumed a
second address from that same contested pool, so this matters more now, not less. Request a small
pool with `autoAssign: false` reserved for gst-apps. Manifests then reference a pool name instead
of a hardcoded address, which also resolves the `deprecatedAnnotation` warning on
`metallb.universe.tf/loadBalancerIPs` and makes a second Helm release trivial.

Two things that cost time when picking `.222`, worth knowing before picking the next one:

- **The pool has a hole.** It is `…193-200` *and* `…204-222`. `.201`–`.203` look
  free in any naive scan but are outside the pool, and MetalLB rejects them with
  `not compatible with requested address pool`.
- **It is `autoAssign: true`**, and MetalLB allocates by walking the ranges from the low end. Any
  team's next `LoadBalancer` Service that doesn't name an address takes `.205`, then `.206`.
  **Pick from the top of the range** to stay out of that race — very likely what invalidated
  `.200`/`.201` last time.

MetalLB keeps no allocation table; the claims live in the `Service` objects, and `.status` is what
was granted (`.spec` is only what was asked for). So the authoritative check is:

```sh
kubectl get ipaddresspools.metallb.io -A \
  -o custom-columns='NAME:.metadata.name,AUTO:.spec.autoAssign,ADDRESSES:.spec.addresses'
kubectl get svc -A -o jsonpath='{range .items[?(@.spec.type=="LoadBalancer")]}{.status.loadBalancer.ingress[0].ip}{"\t"}{.metadata.namespace}/{.metadata.name}{"\n"}{end}' | sort -V
```

**Do not use `ping` to check whether an address is free.** Every address in this range answers
ICMP, including ones in the pool's gap that can never be assigned — something on the path, VPN
client or a switch doing proxy-ARP, answers for the whole prefix. `kubectl` is the only source of
truth. Allocation failures also surface as *events*, not as `apply` errors, so confirm with
`kubectl describe svc -n gst-apps mediamtx`.

**4. Clips are not reproducible.** `sizzle.ts` was uploaded with `kubectl cp` into the
`file-player-clips` PVC. It exists only in that volume — nobody else can recreate it from the
repo. For lab users, either add an initContainer that pulls clips from S3 (the cluster has the
`mountpoint-s3` CSI installed) or bake them into a small image.

**5. `<sibling-node>` BGP.** Both peer sessions were dead after the Mellanox driver update
(`Up/Down: never`, `State: Connect`, FRR reporting `Not advertised to any peer`). Reported and
since fixed. Worth checking after any future maintenance on that node — `LoadBalancer`
services with `externalTrafficPolicy: Local` silently break when it recurs.

**6. `MTX_WEBRTCIPSFROMINTERFACES: "no"` may do nothing.** It is set on MediaMTX to suppress
loopback and pod-IP ICE candidates, but `127.0.0.1` candidates still appeared in the logs. It
is harmless and possibly the log simply reports the socket's local address rather than the
advertised candidate. Not investigated.

Still true after the item 2 fix — a session on 2026-07-31 established with
`local candidate: host/udp/127.0.0.1/8189` and played normally, while the next session on the same
page picked `10.42.4.211`. So it is cosmetic, but it does mean the local candidate in the log is
not a reliable signal when debugging; use the remote candidate and the `nodeAssigned` event.

**7. The old `k8s/*.yaml` are stale.** `kube-deployment.yaml` and `vnc-mxl.yaml` target a node
named `hol1`, pull `cbcrc/*` from Docker Hub, and back the domain with Longhorn RWX. They are
unrelated to gst-apps and nothing in the workshop docs references them. Delete or update them
separately.

**8. There is no TLS path in this cluster, so the microphone stays a per-user workaround.**
Seamless `getUserMedia` for lab users needs HTTPS with a certificate their machines already trust.
Checked 2026-07-31 — none of the pieces are in place:

- `traefik` ingressClass exists (controller `traefik.io/ingress-controller`, LB `…193`)
  and the Traefik CRDs are installed, but there are **zero `Ingress` and zero `IngressRoute`
  objects cluster-wide**. Nobody uses it.
- **No cert-manager** — `kubectl get clusterissuers.cert-manager.io` returns nothing.
- All six `kubernetes.io/tls` secrets are internal and useless to a browser: `k3s-serving` is the
  API server's own cert, the rest are webhook CAs.

So HTTPS today means Traefik's built-in self-signed cert. That is worse than it sounds here,
because WHIP needs MediaMTX on a **second hostname** — the frontend resolves the WHIP `Location`
header against the WHIP URL and later `DELETE`s it to tear the session down
(`gst-apps/webrtc2mxl/frontend/src/App.jsx:199` and `:147`), so folding MediaMTX under a path
prefix on the app's hostname would break teardown and leak a publisher session per publish. Two
hostnames means two certs to accept, and the MediaMTX one is a background `fetch` rather than a
navigation, so the browser shows **no interstitial at all** — it just fails until the user
manually visits `https://mediamtx.<host>` once.

The clean fix is a certificate from an internal CA the lab machines already trust, which is a PKI
request rather than a technical blocker. **Deferred pending a team discussion on direction** — this
is a lab, and the goal was porting `gst-apps` to Kubernetes, not solving browser security policy.

If HTTPS is ever pursued, note that `webrtc2mxl` could serve WHIP from its own origin the way
`mxl2webrtc`'s direct mode already serves WHEP (`gst-apps/mxl2webrtc/backend/main.py:260-295`, a
native `webrtcbin` server). That removes MediaMTX from the browser path entirely and halves the
TLS surface to one hostname.

---

## Helm migration

One release per node, each with its own namespace, domain, and IP. The instances share
nothing, which is correct — MXL domains are node-local, and flows cannot span nodes without the
`MXLFlowMirror` CRD from the `mxl-k8s-operator` already installed in `mxl-system`.

Exactly four things vary per release:

| Value | Currently | Why it must vary |
| --- | --- | --- |
| `nodeSelector` **and** PV `nodeAffinity` | `${MXL_NODE}` | Must always agree, or pods hang `Pending` |
| **PV name** | `gst-apps-mxl-domain` | **PVs are cluster-scoped** — the one guaranteed collision. Template as `{{ .Release.Name }}-mxl-domain` or the second install fails |
| MetalLB IPs + `allow-shared-ip` key | `${MXL_LB_IP_APPS}` / `gst-apps`, plus `${MXL_LB_IP_MEDIAMTX}` for MediaMTX | Each instance needs its own **pair** of addresses and its own sharing key |
| Namespace | `gst-apps` | Cleanest isolation is one namespace per host |

Everything else — the initContainer, the domain ConfigMap, the Service shape, the per-app port
map — is identical across releases and becomes a single template. The eight app Deployments
differ only in name, image, port, and a couple of env vars, so they collapse into one template
driven by a values list, with `html5-keyer` (the `/dev/shm` volume and resource limits) and
`file-player` (the clips PVC) as the two special cases.
