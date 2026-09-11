# Redis 8.x dashboard for Grafana

A Grafana dashboard covering Redis 8.x, generated from a Python script. 67 panels
in 10 rows, written in schema **v2beta1** (`dashboard.grafana.app/v2beta1`).

```
dashboard.json                  generated, do not edit by hand
tools/build_dashboard.py        the generator
```

Requirements: Grafana 12.x or 13.x, Redis 8.x, and
[Axiumine/grafana-redis-datasource](https://github.com/Axiumine/grafana-redis-datasource)
3.0.0. The stock `redis-datasource` 2.2.1 from the catalogue will render only part
of the dashboard, and 2.3.0 of the fork still registers under that same old id —
see [The plugin](#the-plugin) below.

## Audit of the previous dashboard

The starting point was the historical dashboard shipped with `redis-datasource`
(`pluginVersion` 7.1.4, title *Redis 12776*): 17 panels in 3 rows.

| Row | Panels |
|---|---|
| Main | Ops/sec, Network, Memory, Uptime, Connected Clients, Version, Number of Keys, Keys, Keyspace, Eviction Policy |
| Other | Client connections, Command statistics, Slow queries log |
| Redis Cluster | State, Role, (untitled bar gauge), Nodes |

It reads five INFO sections — `server`, `clients`, `memory`, `stats`, `replication` —
plus `CLUSTER INFO`, `CLUSTER NODES`, `CLIENT LIST`, `SLOWLOG GET` and a `DBSIZE`
through the raw `cli` query type.

### Coverage gaps

Everything Redis added from 6.2 onwards was missing, and so were several older
sections:

| Missing | Since | Now in |
|---|---|---|
| `INFO errorstats` — per-error counters | 6.2 | Commands, latency and errors |
| `INFO latencystats` — per-command latency percentiles | 7.0 | Commands, latency and errors |
| `INFO keysizes` — key and element size histograms | 8.0 | Memory, Keyspace |
| `INFO threads` — per I/O thread counters | 8.0 | Clients and threads |
| `INFO search_*` — query engine statistics | 8.0 | Modules and search |
| `INFO hotkeys` and `HOTKEYS GET` | 8.6 | Hot keys |
| `SLOWLOG GET` argument count | 8.10 | Commands, latency and errors |
| `INFO modules` — loaded modules, versions, API level | 4.0 | Modules and search |
| `INFO persistence` — RDB, AOF, fork and copy-on-write | 2.6 | Persistence |
| `INFO cpu` — process CPU time, main thread since 7.0 | 2.6 | Clients and threads |
| Replication detail: offsets, backlog, sync counters | — | Replication |
| Client eviction and blocked/watched clients | 7.0 | Clients and threads |
| Client side caching (tracking) counters | 6.0 | Keyspace |
| Hash field expiration counters | 7.4 | Keyspace |
| `INFO keyspace` subexpiry | 7.4 | Keyspace |
| Cluster links, bus messages, slot migration | 7.0 | Cluster |
| Active defragmentation counters | 4.0 | Memory |
| `acl_access_denied_*` counters | 7.0 | Clients and threads |

### Pre-existing defects

These are bugs in the old dashboard and in the plugin, independent of the Redis
version. All four were reproduced before being fixed.

**1. Every transformation was dead.** The old file writes transformations as

```json
{ "kind": "Transformation", "group": "organize", "spec": { "options": {} } }
```

Grafana does `transformations.map((te) => te.spec)` and then looks up `spec.id`. With
no `id` the lookup resolves to the empty string and the panel fails with

```
Error: "" not found in: reduce,filterFieldsByName,renameByRegex,…
```

so `filterFieldsByName` and `organize` never ran on any of the 17 panels. The correct
shape repeats the id inside the spec:

```json
{ "kind": "organize", "spec": { "id": "organize", "options": {} } }
```

`tools/build_dashboard.py` emits this through a single `transformation()` helper.

**2. A row repeat pointing at a variable that does not exist.** Both the *Main* and
*Other* rows carry `repeat: {mode: variable, value: redis}` while `spec.variables` is
`[]`. The dashboard has no datasource picker at all: each panel is pinned to whatever
datasource the export labels resolved to. The new dashboard declares a
`DatasourceVariable` named `redis` (`pluginId: axiumine-redis-datasource`) and every query
references it, so the node is switchable from the picker. That covers the
one-datasource-per-node workaround described in upstream issue #335, though not its
actual ask — seeing every node at once, which needs a multi-value variable and row
repeat.

**3. Streaming frames had no time field.** `TimeSeriesStreaming.update()` copied the
reply's fields into a `CircularDataFrame` and added nothing else. `INFO` never returns
a time field, so a streamed INFO query produced a frame that no time series panel
could plot — which is why the old dashboard has zero time series panels and shows
every metric as an instant stat or gauge. The plugin fork adds the arrival timestamp
when the reply does not carry one, so the 35 streaming panels in this dashboard chart
over time.

**4. `CLUSTER NODES` dropped slots and misread timestamps.** The parser kept only
`fields[8]`, one slot field, so a node owning several ranges (or importing/migrating
slots) reported only the first one. It also split on a single space, labelled
`ping-sent`/`pong-received` as `ms` durations when they are Unix millisecond
timestamps, and replaced a `0` ping — meaning *no ping pending* — with the current
time, so every idle node looked like it had just been pinged. Fixed in the fork, which
also splits out the hostname and adds a `Slots` count.

Two more, smaller: stat panels with `reduceOptions.fields: ""` reduce numeric fields
only, so any string value (`redis_version`, `maxmemory_policy`, `cluster_state`,
`role`) rendered as *No data* until the selector became `"/.*/"`; and the bundled
plugin dashboards referenced the streaming time field as `#time`, which never matched.

### On the `cli` workaround

Using `type: "cli"` for `keysizes`, `errorstats`, `latencystats` and `modules` does
work — the backend runs the command — but the reply comes back as a single string
field, one row, so the only usable visualisation is a text or table panel showing raw
INFO text. No filtering, no units, no charting. The fork instead parses these sections
into proper frames, and adds them to the section dropdown in the query editor, so this
dashboard uses `type: "command", command: "info", section: …` everywhere.

## The new dashboard

67 panels in 10 rows: 35 time series, 18 stats, 11 tables, 1 bar chart, 2 text.

| Row | Contents |
|---|---|
| Overview | version, mode, role, uptime, cluster state, clients, keys, eviction policy, ops/sec, memory, network |
| Memory | breakdown, consumers, fragmentation, evictions, active defrag, key size distribution |
| Clients and threads | clients, connections, watched and blocking keys, event loop, access denied, CPU, I/O threads, threaded I/O, `CLIENT LIST` |
| Persistence | last RDB save, AOF, changes since save, AOF size, copy-on-write, forks, loading and fsync |
| Commands, latency and errors | commandstats, latency percentiles, hits and misses, error replies, expiration, errorstats, slow command counters, slowlog |
| Replication | status, backlog, offset, synchronisations, traffic |
| Keyspace | keyspace, key size buckets, pub/sub, client side caching, hash field expiration |
| Modules and search | modules, search summary, index size, search memory, queries, errors and warnings |
| Cluster | nodes, slots, epochs, bus messages, links, slot migration |
| Hot keys (collapsed) | `HOTKEYS GET` table, how to enable, top keys by memory |

Panels that need a feature the server does not have degrade to an error or *No data*
rather than lying: the cluster row on a standalone server, the search row without the
query engine, the hot keys row before `HOTKEYS START`. Each of those panels says so in
its description, and the two text panels explain the setup.

### `search_*` field names

They do vary between builds, so the panels were built against the real node
(`INFO SEARCH` on a 3-node 8.10.1 cluster, 94 fields). The 22 fields the dashboard
uses all exist there. Two things to know:

- `search_info_on_zero_indexes` gates the rest: with no index defined, the sub-sections
  are not emitted at all and the search panels show *Data is missing a number field*.
- `search_shard_*` and `search_coord_*` mirror each other; the dashboard uses the shard
  counters, which are present on a standalone server too.

`latencystats` columns are equally dynamic — they follow
`latency-tracking-info-percentiles`, so the table renders whatever percentiles the
server is configured to track (p50/p99/p99.9 by default).

### Regenerating

```bash
python3 tools/build_dashboard.py     # writes dashboard.json, prints "67 panels, 10 rows"
```

The script has no dependencies. Import `dashboard.json` through
*Dashboards → New → Import*, or PUT it to
`/apis/dashboard.grafana.app/v2beta1/namespaces/default/dashboards/<uid>` with a
current `metadata.resourceVersion`.

Grafana 13.2 promoted the dashboard API to a stable `v2`, and still serves
`v2beta1` beside it: `v2`, `v2beta1`, `v2alpha1`, `v1`, `v1beta1` and `v0alpha1`
are all available, and asking 13.2 to convert this dashboard from `v2beta1` to
`v2` reports no failure. `v2beta1` is what the generator writes, because it is
the newest version Grafana 12 understands and the file has to import on both.

## The plugin

The dashboard needs [Axiumine/grafana-redis-datasource](https://github.com/Axiumine/grafana-redis-datasource)
3.0.0, a fork of `RedisGrafana/grafana-redis-datasource` 2.2.1, whose last release
predates Redis 8. In short, the fork adds:

- `SLOWLOG GET` **Arg Count** (Redis 8.10) and a **Truncated** flag
- typed frames for the `threads`, `keysizes`, `latencystats`, `modules`, `hotkeys` and
  `search` INFO sections, instead of raw text
- `HOTKEYS GET` (Redis 8.6) as a query type
- a time field on streamed replies, which is what makes streamed `INFO` plottable
- `CLUSTER NODES` slot, hostname and timestamp fixes

Full list in the fork's `CHANGELOG.md`, entries 2.3.0 and 3.0.0.

The plugin id changed in 3.0.0, from upstream's `redis-datasource` to
`axiumine-redis-datasource`, so the fork no longer drops in over the catalogue build.
Grafana's signing service checks that the first segment of the id matches the
organisation that issued the signing token, and its catalogue enforces the same
`<organisation>-<name>-<type>` shape, so an id of `redis-datasource` can be signed by
RedisGrafana and by nobody else. A fork that wants a signature anyone can install has
to carry its own id. The cost is that dashboards written against the old id do not
find the new datasource: `type` and `group` both name the plugin, and both have to be
updated. This dashboard is generated, so for it that is one constant in
`tools/build_dashboard.py`.

An unsigned local build additionally needs
`GF_PLUGINS_ALLOW_LOADING_UNSIGNED_PLUGINS=axiumine-redis-datasource`. That setting
covers unsigned plugins only — a *signed* build whose `rootUrls` do not match the
instance's `root_url` is rejected as invalid instead, and no setting waves it
through.

### Provisioning

ACL users need `acl: true`, otherwise the username is ignored and only the password is
sent:

```yaml
apiVersion: 1
datasources:
  - name: Redis db1
    uid: redis-db1
    type: axiumine-redis-datasource
    access: proxy
    url: redis://db1:6379
    jsonData: { client: standalone, poolSize: 5, timeout: 10, acl: true, user: grafana }
    secureJsonData: { password: "<password>" }
```

Use `client: cluster` with a comma-separated `url` to talk to the cluster as a whole;
one standalone datasource per node is what the `redis` variable is for, and it is the
only way to read node-local sections such as `threads`, `keysizes` and `commandstats`.

The Grafana user needs at minimum:

```
ACL SETUSER grafana on >… ~* &* +@read +info +client|list +slowlog|get +cluster|info +cluster|nodes +latency +memory|stats
```

Add `+hotkeys|get` (`@admin`, `@dangerous`) only if you want the hot keys row.

## Verified against

Redis 8.10.1, three nodes in cluster mode, and a standalone 8.10 container; Grafana
12.2.0 and 13.2.1. Every row was checked in a browser against both, including the
failure modes listed above.

On 13.2.1 the dashboard was also exercised through all three client configurations
of the datasource — cluster client, a single cluster node addressed as a standalone
server, and the standalone container — with every row expanded. The only panel that
reports an error is *Cluster state* against the standalone container, which is the
`CLUSTER INFO` failure mode described above.

Reaching that point needed a fix in the plugin, released in its 3.0.0: Grafana 13
releases the buffers of frames it has stopped rendering by assigning
`values.length = 0`, and recognises the streaming frames it must leave alone by the
`appendRow` method a `CircularDataFrame` carries. A panel transformation rebuilds
the frame as a plain object, so that recognition was lost while the fields still
pointed at the circular buffers, whose `length` is read-only — and the resulting
`TypeError` escaped into React and replaced the whole dashboard with *Page error*.
Thirty-five of the 67 panels here both stream and transform, so it took the page
down on every load. Plugin 3.0.0 hands out a detached copy of the buffer; against
an earlier build of the fork, this dashboard does not render on Grafana 13.

## Licence

GPL-3.0-or-later. See [COPYING](COPYING).

`dashboard.json` and `tools/build_dashboard.py` are original work and carry no
upstream code. The plugin is a separate repository under its own licence: the fork
stays Apache-2.0, as upstream is, and nothing here changes that.
