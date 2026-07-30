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
| mediamtx | `40-mediamtx.yaml` | http://${MXL_LB_IP_APPS}:8889 | relays WHIP→WHEP |

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

**Exposure: one shared MetalLB IP, `${MXL_LB_IP_APPS}`, with the compose port numbers preserved.**
Every app listens on 9600 inside its container; the Service maps the familiar port to it. All
services carry:

```yaml
  annotations:
    metallb.universe.tf/allow-shared-ip: gst-apps
    metallb.universe.tf/loadBalancerIPs: ${MXL_LB_IP_APPS}
spec:
  externalTrafficPolicy: Cluster
```

`externalTrafficPolicy: Cluster` is **required** for the shared IP — MetalLB only shares an
address when all services are `Cluster` or all target the same pods. Do not set `Local`; see
"Known issues".

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
              value: "http://${MXL_LB_IP_APPS}:8889/webrtc2mxl/whip"
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

### Expect the microphone to be blocked

This app is the one that needs `getUserMedia`, and **Chrome only grants microphone access in a
secure context** — HTTPS, or `localhost`. Loading it over `http://${MXL_LB_IP_APPS}:9606` will fail
with a permissions error that looks like a deployment bug but isn't.

Easiest workaround, no TLS needed:

```sh
kubectl port-forward deploy/webrtc2mxl 9606:9600
```

then open **http://localhost:9606**, which counts as a secure context.

**This changed with the item 1 fix in "Known issues".** The page is served from `localhost`, but
the WHIP host now comes from `MEDIAMTX_WHIP_URL` in the manifest, which is
`${MXL_LB_IP_APPS}:8889` — a real address, so it is honoured instead of being rewritten to
`localhost`. On a machine that can reach the MetalLB address, the single port-forward above is
now enough and MediaMTX needs no port-forward at all.

If you are working from somewhere that *cannot* reach `${MXL_LB_IP_APPS}`, point the app at a local
alias so the substitution kicks back in, and forward MediaMTX too:

```sh
kubectl set env deploy/webrtc2mxl MEDIAMTX_WHIP_URL=http://localhost:8889/webrtc2mxl/whip
kubectl port-forward deploy/mediamtx 8889:8889
```

Undo it afterwards with `kubectl set env deploy/webrtc2mxl MEDIAMTX_WHIP_URL-`, or re-apply the
manifest — otherwise every other user of the deployment is left pointing at their own machine.

For a permanent fix, put the app behind the cluster's Traefik ingress with TLS (`traefik`
ingressClass, LB at `…193`). That is the only way lab users get microphone access
without port-forwarding.

---

## Final verification

```sh
kubectl get pods -o wide          # 9 pods Running, all on ${MXL_NODE}
kubectl get svc                   # all EXTERNAL-IP ${MXL_LB_IP_APPS}
```

| App | URL |
| --- | --- |
| test-generator | http://${MXL_LB_IP_APPS}:9600 |
| mxl2webrtc | http://${MXL_LB_IP_APPS}:9601 |
| file-player | http://${MXL_LB_IP_APPS}:9602 |
| hls2mxl | http://${MXL_LB_IP_APPS}:9603 |
| input-selector | http://${MXL_LB_IP_APPS}:9604 |
| html5-keyer | http://${MXL_LB_IP_APPS}:9605 |
| webrtc2mxl | http://${MXL_LB_IP_APPS}:9606 (needs port-forward for mic) |
| mxl-info-gui | http://${MXL_LB_IP_APPS}:9699 |

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

**2. External traffic enters on an arbitrary node and is SNAT'd cross-node.** With
`externalTrafficPolicy: Cluster`, all five nodes advertise `.199` over BGP and the switch's
ECMP hash picks the entry node per flow. MediaMTX session logs show
`remote candidate: 10.42.5.0` — node 001's CNI gateway — while the pods are on node 002. Every
media packet takes an extra hop.

Audio glitched badly while `<sibling-node>` was mid-Mellanox-maintenance with dead BGP, and went
clean once that node was fixed — the hop itself is tolerable, but **media quality currently
depends on the health of whichever unrelated node the flow hashes to.** If glitching returns
and looks random, this is why.

`externalTrafficPolicy: Local` would fix it, but MetalLB refuses a shared IP unless all
services are `Cluster`. Item 1 is now fixed, so MediaMTX *can* hold its own address — the
remaining blocker is item 3: a second address out of the contested `upp-services-pool-lab` is
exactly what was invalidated mid-session during the rollout. Do this once the dedicated pool
exists.

**3. Ask for a dedicated `IPAddressPool`.** `upp-services-pool-lab` is shared and other teams
claim addresses continuously — `.200`, `.201` and the `artisto-002` namespace all appeared
*during* the initial rollout, invalidating an address mid-session. Request a small pool with
`autoAssign: false` reserved for gst-apps. Manifests then reference a pool name instead of a
hardcoded address, which also resolves the `deprecatedAnnotation` warning on
`metallb.universe.tf/loadBalancerIPs` and makes a second Helm release trivial.

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

**7. The old `k8s/*.yaml` are stale.** `kube-deployment.yaml` and `vnc-mxl.yaml` target a node
named `hol1`, pull `cbcrc/*` from Docker Hub, and back the domain with Longhorn RWX. They are
unrelated to gst-apps and nothing in the workshop docs references them. Delete or update them
separately.

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
| MetalLB IP + `allow-shared-ip` key | `${MXL_LB_IP_APPS}` / `gst-apps` | Two instances need two addresses and two sharing keys |
| Namespace | `gst-apps` | Cleanest isolation is one namespace per host |

Everything else — the initContainer, the domain ConfigMap, the Service shape, the per-app port
map — is identical across releases and becomes a single template. The eight app Deployments
differ only in name, image, port, and a couple of env vars, so they collapse into one template
driven by a values list, with `html5-keyer` (the `/dev/shm` volume and resource limits) and
`file-player` (the clips PVC) as the two special cases.
