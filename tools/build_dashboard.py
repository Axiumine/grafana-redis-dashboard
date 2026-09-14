#!/usr/bin/env python3
#
# Copyright (C) 2026 Axiumine
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# this program. If not, see <https://www.gnu.org/licenses/>.
"""
Generates dashboard.json, a Grafana schema v2 dashboard covering Redis 8.10.

The dashboard is generated rather than hand edited because it carries roughly
fifty panels whose queries follow a handful of shapes. Run it with

    python3 tools/build_dashboard.py

Field names were captured from a live redis 8.10.1 node. Sections that only
exist on newer servers, and search_* fields that only appear once an index is
created, simply render as "No data" on older or empty servers.
"""

import json
import os

DATASOURCE = "${redis}"
# The datasource plugin id, which schema v2 calls the query "group". It is also
# what the Redis node variable filters the datasource picker by, below. The fork
# renamed it from upstream's `redis-datasource` so that the first segment matches
# the Axiumine organisation, which is what Grafana's signing and catalogue
# submission both require.
GROUP = "axiumine-redis-datasource"
# The streaming poll interval, in milliseconds. Zero is not "as fast as
# possible" but a setting of its own, added in the plugin fork's 3.0.0: the
# datasource starts no timer and reads once per subscription, and since Grafana
# resubscribes on every dashboard refresh the panels then advance at whatever
# the refresh picker says while still accumulating a series. A fixed interval
# here would override the picker instead, which is what the panels did until
# now: set to 1000 they redrew once a second whatever the user had chosen, and
# the picker could only make them faster, never slower.
STREAM_INTERVAL = 0
STREAM_CAPACITY = 1000

elements = {}
rows = []


def query(ref_id="A", *, stream=False, capacity=STREAM_CAPACITY, hidden=False, **spec):
    """One PanelQuery. Anything in spec goes straight to the plugin query model."""
    spec.setdefault("type", "command")
    spec.setdefault("query", "")

    if stream:
        spec.update(
            {
                "streaming": True,
                "streamingDataType": "TimeSeries",
                "streamingInterval": STREAM_INTERVAL,
                "streamingCapacity": capacity,
            }
        )

    return {
        "kind": "PanelQuery",
        "spec": {
            "query": {
                "kind": "DataQuery",
                "group": GROUP,
                "version": "v0",
                "datasource": {"name": DATASOURCE},
                "spec": spec,
            },
            "refId": ref_id,
            "hidden": hidden,
        },
    }


def info(section, *, stream=False, **kwargs):
    return query(command="info", section=section, stream=stream, **kwargs)


def transformation(transformation_id, options):
    """
    A TransformationKind.

    Grafana reads the transformation straight out of spec, so the id has to be
    repeated there: a spec carrying only options resolves to the empty id and
    the panel dies with `"" not found in: reduce,filterFieldsByName,...`.
    """
    return {"kind": transformation_id, "spec": {"id": transformation_id, "options": options}}


def keep(names, rename=None, exclude=None, order=None):
    """filterFieldsByName + organize, the shape the INFO frames need."""
    transformations = []

    if names:
        transformations.append(
            transformation("filterFieldsByName", {"include": {"names": list(names)}})
        )

    if rename or exclude or order:
        transformations.append(
            transformation(
                "organize",
                {
                    "excludeByName": {name: True for name in (exclude or [])},
                    "indexByName": {name: i for i, name in enumerate(order or [])},
                    "renameByName": rename or {},
                },
            )
        )

    return transformations


def panel(
    name,
    title,
    viz,
    queries,
    *,
    description="",
    options=None,
    defaults=None,
    overrides=None,
    transformations=None,
):
    if not isinstance(queries, list):
        queries = [queries]

    field_config = {"defaults": defaults or {}, "overrides": overrides or []}
    field_config["defaults"].setdefault(
        "thresholds", {"mode": "absolute", "steps": [{"value": None, "color": "green"}]}
    )

    elements[name] = {
        "kind": "Panel",
        "spec": {
            "id": len(elements) + 1,
            "title": title,
            "description": description,
            "links": [],
            "data": {
                "kind": "QueryGroup",
                "spec": {
                    "queries": queries,
                    "transformations": transformations or [],
                    "queryOptions": {},
                },
            },
            "vizConfig": {
                "kind": "VizConfig",
                "group": viz,
                "version": "",
                "spec": {"options": options or {}, "fieldConfig": field_config},
            },
        },
    }

    return name


def row(title, items, *, collapse=False):
    """items: list of (panel name, width, height); packed left to right over 24 columns."""
    grid = []
    x = 0
    y = 0
    row_height = 0

    for element, width, height in items:
        if x + width > 24:
            x = 0
            y += row_height
            row_height = 0

        grid.append(
            {
                "kind": "GridLayoutItem",
                "spec": {
                    "x": x,
                    "y": y,
                    "width": width,
                    "height": height,
                    "element": {"kind": "ElementReference", "name": element},
                },
            }
        )

        x += width
        row_height = max(row_height, height)

    rows.append(
        {
            "kind": "RowsLayoutRow",
            "spec": {
                "title": title,
                "collapse": collapse,
                "layout": {"kind": "GridLayout", "spec": {"items": grid}},
            },
        }
    )


# Reusable viz option blocks
# fields "/.*/" rather than "" because "" reduces numeric fields only, and the
# INFO frames a stat panel points at are as often a string: redis_version,
# maxmemory_policy, cluster_state, role.
STAT = {
    "reduceOptions": {"calcs": ["lastNotNull"], "fields": "/.*/", "values": False},
    "orientation": "auto",
    "textMode": "auto",
    "colorMode": "value",
    "graphMode": "none",
    "justifyMode": "auto",
}
STAT_ALL = dict(STAT, reduceOptions={"calcs": ["lastNotNull"], "fields": "/.*/", "values": False})
TABLE = {"showHeader": True, "cellHeight": "sm", "footer": {"show": False, "reducer": ["sum"], "countRows": False, "fields": ""}}
TS = {
    "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True, "calcs": []},
    "tooltip": {"mode": "multi", "sort": "desc"},
}
TS_LINE = {"drawStyle": "line", "lineWidth": 1, "fillOpacity": 8, "showPoints": "never", "spanNulls": True}
BARGAUGE = {
    "displayMode": "gradient",
    "orientation": "horizontal",
    "reduceOptions": {"calcs": ["lastNotNull"], "fields": "/.*/", "values": False},
    "showUnfilled": True,
}


# ---------------------------------------------------------------- Overview ---

panel("panel-version", "Version", "stat", info("server"),
      description="redis_version and the build mode reported by INFO server.",
      options=STAT, transformations=keep(["redis_version"]))

panel("panel-mode", "Mode", "stat", info("server"),
      description="standalone, sentinel or cluster.",
      options=STAT, transformations=keep(["redis_mode"]))

panel("panel-role", "Role", "stat", info("replication"),
      options=STAT, transformations=keep(["role"]))

panel("panel-uptime", "Uptime", "stat", info("server"),
      options=STAT, defaults={"unit": "s", "decimals": 0},
      transformations=keep(["uptime_in_seconds"]))

panel("panel-cluster-state", "Cluster state", "stat", query(command="clusterInfo"),
      options=dict(STAT, colorMode="background"),
      defaults={"mappings": [
          {"type": "value", "options": {"ok": {"color": "green", "index": 0},
                                        "fail": {"color": "red", "index": 1}}}]},
      transformations=keep(["cluster_state"]))

panel("panel-clients-now", "Connected clients", "stat", info("clients"),
      options=STAT, transformations=keep(["connected_clients"]))

panel("panel-keys-now", "Keys", "stat", info("keyspace"),
      description="Sum of the keys column of INFO keyspace, across every database.",
      options=dict(STAT, reduceOptions={"calcs": ["sum"], "fields": "/^keys$/", "values": False}))

panel("panel-eviction-policy", "Eviction policy", "stat", info("memory"),
      options=STAT, transformations=keep(["maxmemory_policy"]))

panel("panel-ops", "Commands per second", "timeseries", info("stats", stream=True),
      description="instantaneous_ops_per_sec, sampled by the plugin while the dashboard is open.",
      options=TS, defaults={"unit": "ops", "custom": TS_LINE},
      transformations=keep(["instantaneous_ops_per_sec", "time"],
                           rename={"instantaneous_ops_per_sec": "Ops/sec"}))

panel("panel-memory-used", "Memory", "timeseries", info("memory", stream=True),
      options=TS, defaults={"unit": "bytes", "custom": TS_LINE},
      transformations=keep(["used_memory", "used_memory_rss", "maxmemory", "total_system_memory", "time"],
                           rename={"used_memory": "Used", "used_memory_rss": "RSS",
                                   "maxmemory": "Limit", "total_system_memory": "System"}))

panel("panel-network", "Network", "timeseries", info("stats", stream=True),
      options=TS, defaults={"unit": "KBs", "decimals": 2, "custom": TS_LINE},
      transformations=keep(["instantaneous_input_kbps", "instantaneous_output_kbps",
                            "instantaneous_input_repl_kbps", "instantaneous_output_repl_kbps", "time"],
                           rename={"instantaneous_input_kbps": "Input",
                                   "instantaneous_output_kbps": "Output",
                                   "instantaneous_input_repl_kbps": "Input, replication",
                                   "instantaneous_output_repl_kbps": "Output, replication"}))

row("Overview", [
    ("panel-version", 3, 3), ("panel-mode", 3, 3), ("panel-role", 3, 3), ("panel-uptime", 3, 3),
    ("panel-cluster-state", 3, 3), ("panel-clients-now", 3, 3), ("panel-keys-now", 3, 3),
    ("panel-eviction-policy", 3, 3),
    ("panel-ops", 8, 8), ("panel-memory-used", 8, 8), ("panel-network", 8, 8),
])


# ------------------------------------------------------------------ Memory ---

panel("panel-memory-breakdown", "Memory breakdown", "timeseries", info("memory", stream=True),
      options=TS, defaults={"unit": "bytes", "custom": TS_LINE},
      transformations=keep(["used_memory", "used_memory_dataset", "used_memory_overhead",
                            "used_memory_peak", "used_memory_lua", "used_memory_scripts",
                            "used_memory_vm_total", "used_memory_functions", "time"],
                           rename={"used_memory": "Used", "used_memory_dataset": "Dataset",
                                   "used_memory_overhead": "Overhead", "used_memory_peak": "Peak",
                                   "used_memory_lua": "Lua", "used_memory_scripts": "Scripts",
                                   "used_memory_vm_total": "VM total (7.0)",
                                   "used_memory_functions": "Functions (7.0)"}))

panel("panel-memory-consumers", "Memory consumers", "timeseries", info("memory", stream=True),
      description="mem_cluster_links arrived in Redis 7.0.",
      options=TS, defaults={"unit": "bytes", "custom": TS_LINE},
      transformations=keep(["mem_clients_normal", "mem_clients_slaves", "mem_replication_backlog",
                            "mem_aof_buffer", "mem_cluster_links", "mem_not_counted_for_evict", "time"],
                           rename={"mem_clients_normal": "Clients", "mem_clients_slaves": "Replicas",
                                   "mem_replication_backlog": "Replication backlog",
                                   "mem_aof_buffer": "AOF buffer",
                                   "mem_cluster_links": "Cluster links (7.0)",
                                   "mem_not_counted_for_evict": "Not counted for evict"}))

panel("panel-fragmentation", "Fragmentation", "timeseries", info("memory", stream=True),
      options=TS, defaults={"unit": "none", "decimals": 3, "custom": TS_LINE},
      transformations=keep(["mem_fragmentation_ratio", "allocator_frag_ratio", "rss_overhead_ratio", "time"],
                           rename={"mem_fragmentation_ratio": "Fragmentation",
                                   "allocator_frag_ratio": "Allocator",
                                   "rss_overhead_ratio": "RSS overhead"}))

panel("panel-evicted", "Evictions", "timeseries", info("stats", stream=True),
      description="evicted_clients and evicted_scripts were added in Redis 7.0 and 7.4.",
      options=TS, defaults={"custom": TS_LINE},
      transformations=keep(["evicted_keys", "evicted_clients", "evicted_scripts", "time"],
                           rename={"evicted_keys": "Keys", "evicted_clients": "Clients (7.0)",
                                   "evicted_scripts": "Scripts (7.4)"}))

panel("panel-defrag", "Active defragmentation", "timeseries", info("stats", stream=True),
      options=TS, defaults={"custom": TS_LINE},
      transformations=keep(["active_defrag_hits", "active_defrag_misses", "active_defrag_key_hits",
                            "active_defrag_key_misses", "total_active_defrag_time", "time"],
                           rename={"active_defrag_hits": "Hits", "active_defrag_misses": "Misses",
                                   "active_defrag_key_hits": "Key hits",
                                   "active_defrag_key_misses": "Key misses",
                                   "total_active_defrag_time": "Total time"}))

panel("panel-keysizes-chart", "Key size distribution", "barchart", info("keysizes"),
      description=("INFO keysizes, added in Redis 8.0. Every row is a power of two bucket, "
                   "so 4 covers keys of size 4 to 7. The plugin returns one row per bucket."),
      options={"orientation": "horizontal", "xTickLabelRotation": 0, "stacking": "normal",
               "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
               "tooltip": {"mode": "multi", "sort": "none"}},
      defaults={"custom": {"axisPlacement": "auto", "fillOpacity": 80, "lineWidth": 1}},
      # The section is a long frame, one row per database, type and bucket, so it
      # is pivoted into one series per data type before it can be charted.
      transformations=keep(["Type", "Range", "Bucket", "Count"]) + [
          transformation("sortBy", {"sort": [{"field": "Bucket", "desc": False}]}),
          transformation("groupingToMatrix", {"columnField": "Type", "rowField": "Range",
                                              "valueField": "Count"}),
      ])

row("Memory", [
    ("panel-memory-breakdown", 12, 8), ("panel-memory-consumers", 12, 8),
    ("panel-fragmentation", 8, 7), ("panel-evicted", 8, 7), ("panel-defrag", 8, 7),
    ("panel-keysizes-chart", 24, 9),
])


# ------------------------------------------------------- Clients & threads ---

panel("panel-clients-ts", "Clients", "timeseries", info("clients", stream=True),
      description=("pubsub_clients and watching_clients arrived in Redis 7.4, "
                   "active_clients in 8.0."),
      options=TS, defaults={"custom": TS_LINE},
      transformations=keep(["connected_clients", "blocked_clients", "tracking_clients",
                            "pubsub_clients", "watching_clients", "active_clients",
                            "cluster_connections", "clients_in_timeout_table", "time"],
                           rename={"connected_clients": "Connected", "blocked_clients": "Blocked",
                                   "tracking_clients": "Tracking",
                                   "pubsub_clients": "Pub/Sub (7.4)",
                                   "watching_clients": "Watching (7.4)",
                                   "active_clients": "Active (8.0)",
                                   "cluster_connections": "Cluster links",
                                   "clients_in_timeout_table": "In timeout table"}))

panel("panel-clients-blocking", "Watched and blocking keys", "stat", info("clients"),
      description="total_blocking_keys and total_blocking_keys_on_nokey arrived in Redis 7.2.",
      options=STAT_ALL,
      transformations=keep(["maxclients", "total_watched_keys", "total_blocking_keys",
                            "total_blocking_keys_on_nokey", "client_recent_max_input_buffer",
                            "client_recent_max_output_buffer"],
                           rename={"maxclients": "Max clients",
                                   "total_watched_keys": "Watched keys",
                                   "total_blocking_keys": "Blocking keys (7.2)",
                                   "total_blocking_keys_on_nokey": "Blocking on nokey (7.2)",
                                   "client_recent_max_input_buffer": "Max input buffer",
                                   "client_recent_max_output_buffer": "Max output buffer"}))

panel("panel-connections", "Connections and disconnections", "timeseries", info("stats", stream=True),
      description="The buffer limit disconnection counters arrived in Redis 7.0.",
      options=TS, defaults={"custom": TS_LINE},
      transformations=keep(["total_connections_received", "rejected_connections",
                            "client_query_buffer_limit_disconnections",
                            "client_output_buffer_limit_disconnections", "time"],
                           rename={"total_connections_received": "Received",
                                   "rejected_connections": "Rejected",
                                   "client_query_buffer_limit_disconnections": "Query buffer limit (7.0)",
                                   "client_output_buffer_limit_disconnections": "Output buffer limit (7.0)"}))

panel("panel-threads", "I/O threads", "table", info("threads"),
      description=("INFO threads, added in Redis 8.0. One row per I/O thread, with the "
                   "clients it owns and the reads and writes it has processed."),
      options=TABLE,
      transformations=keep([], rename={"clients": "Clients", "reads": "Reads", "writes": "Writes"}))

panel("panel-io-threaded", "Threaded I/O", "timeseries", info("stats", stream=True),
      description="The prefetch counters arrived with the Redis 8.0 threaded engine.",
      options=TS, defaults={"custom": TS_LINE},
      transformations=keep(["total_reads_processed", "total_writes_processed",
                            "io_threaded_reads_processed", "io_threaded_writes_processed",
                            "io_threaded_total_prefetch_batches",
                            "io_threaded_total_prefetch_entries", "time"],
                           rename={"total_reads_processed": "Reads",
                                   "total_writes_processed": "Writes",
                                   "io_threaded_reads_processed": "Threaded reads",
                                   "io_threaded_writes_processed": "Threaded writes",
                                   "io_threaded_total_prefetch_batches": "Prefetch batches (8.0)",
                                   "io_threaded_total_prefetch_entries": "Prefetch entries (8.0)"}))

panel("panel-eventloop", "Event loop", "timeseries", info("stats", stream=True),
      description="The eventloop and pipeline counters arrived in Redis 7.0.",
      options=TS, defaults={"custom": TS_LINE},
      transformations=keep(["instantaneous_eventloop_cycles_per_sec",
                            "instantaneous_eventloop_duration_usec",
                            "avg_pipeline_length", "time"],
                           rename={"instantaneous_eventloop_cycles_per_sec": "Cycles/sec (7.0)",
                                   "instantaneous_eventloop_duration_usec": "Duration, µs (7.0)",
                                   "avg_pipeline_length": "Pipeline length (7.0)"}))

panel("panel-acl-denied", "Access denied", "timeseries", info("stats", stream=True),
      description=("acl_access_denied_* was added in Redis 7.0 and is the fastest way to spot "
                   "a client authenticating with the wrong ACL user."),
      options=TS, defaults={"custom": TS_LINE},
      transformations=keep(["acl_access_denied_auth", "acl_access_denied_cmd",
                            "acl_access_denied_key", "acl_access_denied_channel",
                            "acl_access_denied_tls_cert", "time"],
                           rename={"acl_access_denied_auth": "Auth", "acl_access_denied_cmd": "Command",
                                   "acl_access_denied_key": "Key", "acl_access_denied_channel": "Channel",
                                   "acl_access_denied_tls_cert": "TLS certificate"}))

panel("panel-client-list", "Client connections", "table", query(command="clientList"),
      description="CLIENT LIST. Columns follow the server, so newer fields appear on their own.",
      options=TABLE,
      transformations=keep([], rename={"addr": "Address", "laddr": "Local address", "name": "Name",
                                       "age": "Age", "idle": "Idle", "cmd": "Command", "user": "User",
                                       "resp": "RESP", "tot-mem": "Memory", "lib-name": "Library",
                                       "lib-ver": "Library version"},
                           exclude=["fd", "qbuf-free", "argv-mem", "multi-mem", "rbs", "rbp",
                                    "obl", "oll", "events", "redir"]))

panel("panel-cpu", "CPU", "timeseries", info("cpu", stream=True),
      description=("INFO cpu. The counters are cumulative process time, so the lines only rise: "
                   "read the slope, or the Delta legend value, as CPU seconds burnt over the "
                   "window. The main thread counters arrived in Redis 7.0."),
      options=dict(TS, legend=dict(TS["legend"], displayMode="table", placement="right",
                                  calcs=["diff", "lastNotNull"])),
      defaults={"unit": "s", "custom": TS_LINE},
      transformations=keep(["used_cpu_sys", "used_cpu_user", "used_cpu_sys_children",
                            "used_cpu_user_children", "used_cpu_sys_main_thread",
                            "used_cpu_user_main_thread", "time"],
                           rename={"used_cpu_sys": "System", "used_cpu_user": "User",
                                   "used_cpu_sys_children": "System, children",
                                   "used_cpu_user_children": "User, children",
                                   "used_cpu_sys_main_thread": "System, main thread (7.0)",
                                   "used_cpu_user_main_thread": "User, main thread (7.0)"}))

row("Clients and threads", [
    ("panel-clients-ts", 12, 8), ("panel-connections", 12, 8),
    ("panel-clients-blocking", 8, 6), ("panel-eventloop", 8, 6), ("panel-acl-denied", 8, 6),
    ("panel-cpu", 14, 7), ("panel-threads", 10, 7),
    ("panel-io-threaded", 24, 6),
    ("panel-client-list", 24, 9),
])


# ------------------------------------------------------------- Persistence ---

panel("panel-rdb-status", "Last RDB save", "stat", info("persistence"),
      options=STAT_ALL,
      defaults={"mappings": [
          {"type": "value", "options": {"ok": {"color": "green", "index": 0},
                                        "err": {"color": "red", "index": 1}}}]},
      transformations=keep(["rdb_last_bgsave_status", "rdb_saves",
                            "rdb_saves_consecutive_failures", "rdb_bgsave_in_progress"],
                           rename={"rdb_last_bgsave_status": "Status", "rdb_saves": "Saves (7.0)",
                                   "rdb_saves_consecutive_failures": "Consecutive failures (7.0)",
                                   "rdb_bgsave_in_progress": "In progress"}))

panel("panel-aof-status", "AOF", "stat", info("persistence"),
      options=STAT_ALL,
      defaults={"mappings": [
          {"type": "value", "options": {"ok": {"color": "green", "index": 0},
                                        "err": {"color": "red", "index": 1}}}]},
      transformations=keep(["aof_enabled", "aof_last_write_status", "aof_last_bgrewrite_status",
                            "aof_rewrites", "aof_rewrites_consecutive_failures"],
                           rename={"aof_enabled": "Enabled", "aof_last_write_status": "Last write",
                                   "aof_last_bgrewrite_status": "Last rewrite",
                                   "aof_rewrites": "Rewrites (7.0)",
                                   "aof_rewrites_consecutive_failures": "Consecutive failures (7.0)"}))

panel("panel-rdb-changes", "Changes since last save", "timeseries", info("persistence", stream=True),
      options=TS, defaults={"custom": TS_LINE},
      transformations=keep(["rdb_changes_since_last_save", "time"],
                           rename={"rdb_changes_since_last_save": "Changes"}))

panel("panel-aof-size", "AOF size", "timeseries", info("persistence", stream=True),
      options=TS, defaults={"unit": "bytes", "custom": TS_LINE},
      transformations=keep(["aof_current_size", "aof_base_size", "aof_buffer_length",
                            "aof_pending_rewrite", "time"],
                           rename={"aof_current_size": "Current", "aof_base_size": "Base",
                                   "aof_buffer_length": "Buffer", "aof_pending_rewrite": "Pending rewrite"}))

panel("panel-fork", "Forks", "timeseries", info("stats", stream=True),
      options=TS, defaults={"custom": TS_LINE},
      transformations=keep(["latest_fork_usec", "total_forks", "time"],
                           rename={"latest_fork_usec": "Last fork, µs", "total_forks": "Total forks (7.0)"}))

panel("panel-cow", "Copy on write", "timeseries", info("persistence", stream=True),
      description="The current_cow_* and current_save_* counters arrived in Redis 7.0.",
      options=TS, defaults={"unit": "bytes", "custom": TS_LINE},
      transformations=keep(["current_cow_size", "current_cow_peak", "rdb_last_cow_size",
                            "aof_last_cow_size", "time"],
                           rename={"current_cow_size": "Current", "current_cow_peak": "Peak (7.0)",
                                   "rdb_last_cow_size": "Last RDB", "aof_last_cow_size": "Last AOF"}))

panel("panel-loading", "Loading and fsync", "stat", info("persistence"),
      description="async_loading arrived in Redis 7.0.",
      options=STAT_ALL,
      transformations=keep(["loading", "async_loading", "aof_delayed_fsync", "aof_pending_bio_fsync",
                            "rdb_last_load_keys_loaded", "rdb_last_load_keys_expired"],
                           rename={"loading": "Loading", "async_loading": "Async loading (7.0)",
                                   "aof_delayed_fsync": "Delayed fsync",
                                   "aof_pending_bio_fsync": "Pending fsync",
                                   "rdb_last_load_keys_loaded": "Keys loaded",
                                   "rdb_last_load_keys_expired": "Keys expired on load"}))

row("Persistence", [
    ("panel-rdb-status", 12, 5), ("panel-aof-status", 12, 5),
    ("panel-rdb-changes", 8, 7), ("panel-aof-size", 8, 7), ("panel-cow", 8, 7),
    ("panel-fork", 12, 6), ("panel-loading", 12, 6),
])


# ------------------------------------------ Commands, latency and errors ---

panel("panel-commandstats", "Command statistics", "table", info("commandstats"),
      description=("INFO commandstats. rejected_calls and failed_calls arrived in Redis 6.2 and "
                   "separate a command the server refused from one that ran and returned an error."),
      options=dict(TABLE, sortBy=[{"displayName": "Calls", "desc": True}]),
      defaults={"custom": {"align": "auto", "filterable": True}},
      transformations=keep([], rename={"Usec_per_call": "Usec per call",
                                       "RejectedCalls": "Rejected (6.2)",
                                       "FailedCalls": "Failed (6.2)",
                                       "CallsMaster": "Calls on master (7.0)"}))

panel("panel-latencystats", "Latency percentiles", "table", info("latencystats"),
      description=("INFO latencystats, added in Redis 7.0. The percentile columns follow the "
                   "latency-tracking-info-percentiles config, so a server tuned to report other "
                   "percentiles renders its own columns here."),
      options=dict(TABLE, sortBy=[{"displayName": "p99", "desc": True}]),
      defaults={"unit": "µs", "decimals": 2, "custom": {"align": "auto", "filterable": True}})

panel("panel-errorstats", "Error statistics", "table", info("errorstats"),
      description=("INFO errorstats, added in Redis 6.2. One row per error prefix, so a burst of "
                   "NOPERM or WRONGPASS is visible without reading the log."),
      options=dict(TABLE, sortBy=[{"displayName": "Count", "desc": True}]),
      defaults={"custom": {"align": "auto", "filterable": True}})

panel("panel-errors-ts", "Error replies", "timeseries", info("stats", stream=True),
      options=TS, defaults={"custom": TS_LINE},
      transformations=keep(["total_error_replies", "unexpected_error_replies", "time"],
                           rename={"total_error_replies": "Total (6.2)",
                                   "unexpected_error_replies": "Unexpected (6.2)"}))

panel("panel-hit-rate", "Keyspace hits and misses", "timeseries", info("stats", stream=True),
      options=TS, defaults={"custom": TS_LINE},
      transformations=keep(["keyspace_hits", "keyspace_misses", "time"],
                           rename={"keyspace_hits": "Hits", "keyspace_misses": "Misses"}))

panel("panel-expired", "Expiration", "timeseries", info("stats", stream=True),
      description=("expired_subkeys counts hash fields expired by the field level TTLs added in "
                   "Redis 7.4, which expired_keys does not cover."),
      options=TS, defaults={"custom": TS_LINE},
      transformations=keep(["expired_keys", "expired_keys_active", "expired_subkeys",
                            "expired_subkeys_active", "expired_stale_perc", "time"],
                           rename={"expired_keys": "Keys", "expired_keys_active": "Keys, active cycle",
                                   "expired_subkeys": "Subkeys (7.4)",
                                   "expired_subkeys_active": "Subkeys, active cycle (7.4)",
                                   "expired_stale_perc": "Stale %"}))

panel("panel-slowlog-counters", "Slow command counters", "timeseries", info("stats", stream=True),
      description="slowlog_commands_* was added in Redis 8.0 and needs no SLOWLOG GET call.",
      options=TS, defaults={"custom": TS_LINE},
      transformations=keep(["slowlog_commands_count", "slowlog_commands_time_ms_max",
                            "slowlog_commands_time_ms_sum", "time"],
                           rename={"slowlog_commands_count": "Count (8.0)",
                                   "slowlog_commands_time_ms_max": "Max, ms (8.0)",
                                   "slowlog_commands_time_ms_sum": "Sum, ms (8.0)"}))

panel("panel-slowlog", "Slow queries log", "table",
      query(command="slowlogGet", size=25),
      description=("SLOWLOG GET. Arg Count and Truncated come from the seventh entry element added "
                   "in Redis 8.10: Redis keeps at most 32 arguments, so Command shows what was "
                   "stored while Arg Count reports what the client actually sent."),
      options=dict(TABLE, sortBy=[{"displayName": "Timestamp", "desc": True}]),
      defaults={"custom": {"align": "auto", "filterable": True}},
      overrides=[{"matcher": {"id": "byName", "options": "Truncated"},
                  "properties": [{"id": "custom.cellOptions",
                                  "value": {"type": "color-text"}},
                                 {"id": "mappings",
                                  "value": [{"type": "value", "options": {
                                      "true": {"color": "orange", "index": 0},
                                      "false": {"color": "text", "index": 1}}}]}]}])

row("Commands, latency and errors", [
    ("panel-commandstats", 12, 10), ("panel-latencystats", 12, 10),
    ("panel-hit-rate", 8, 7), ("panel-errors-ts", 8, 7), ("panel-expired", 8, 7),
    ("panel-errorstats", 8, 8), ("panel-slowlog-counters", 16, 8),
    ("panel-slowlog", 24, 8),
])


# ------------------------------------------------------------- Replication ---

panel("panel-repl-status", "Replication status", "stat", info("replication"),
      description="master_failover_state arrived in Redis 6.2 with FAILOVER.",
      options=dict(STAT_ALL, colorMode="background"),
      defaults={"mappings": [
          {"type": "value", "options": {
              "up": {"color": "green", "index": 0},
              "down": {"color": "red", "index": 1},
              "no-failover": {"color": "green", "index": 2},
              "master": {"color": "blue", "index": 3},
              "slave": {"color": "purple", "index": 4}}}]},
      transformations=keep(["role", "connected_slaves", "master_failover_state",
                            "master_link_status", "master_last_io_seconds_ago",
                            "master_sync_in_progress"],
                           rename={"role": "Role", "connected_slaves": "Connected replicas",
                                   "master_failover_state": "Failover state (6.2)",
                                   "master_link_status": "Link status",
                                   "master_last_io_seconds_ago": "Last I/O, s",
                                   "master_sync_in_progress": "Sync in progress"}))

panel("panel-repl-offset", "Replication offset", "timeseries", info("replication", stream=True),
      description=("slave0_offset and slave0_lag are expanded from the slave0 record line, which "
                   "the plugin used to return as one opaque string."),
      options=TS, defaults={"custom": TS_LINE},
      transformations=keep(["master_repl_offset", "slave_repl_offset", "slave_read_repl_offset",
                            "slave0_offset", "slave0_lag", "time"],
                           rename={"master_repl_offset": "Master offset",
                                   "slave_repl_offset": "Replica offset",
                                   "slave_read_repl_offset": "Replica read offset",
                                   "slave0_offset": "slave0 offset", "slave0_lag": "slave0 lag"}))

panel("panel-repl-sync", "Synchronisations", "timeseries", info("stats", stream=True),
      options=TS, defaults={"custom": TS_LINE},
      transformations=keep(["sync_full", "sync_partial_ok", "sync_partial_err", "time"],
                           rename={"sync_full": "Full", "sync_partial_ok": "Partial, ok",
                                   "sync_partial_err": "Partial, failed"}))

panel("panel-repl-backlog", "Replication backlog", "stat", info("replication"),
      options=STAT_ALL,
      overrides=[{"matcher": {"id": "byNames", "options": {"mode": "include",
                                                          "names": ["Size", "History length"]}},
                  "properties": [{"id": "unit", "value": "bytes"}]}],
      transformations=keep(["repl_backlog_active", "repl_backlog_size", "repl_backlog_histlen"],
                           rename={"repl_backlog_active": "Active", "repl_backlog_size": "Size",
                                   "repl_backlog_histlen": "History length"}))

panel("panel-repl-net", "Replication traffic", "timeseries", info("stats", stream=True),
      description="total_net_repl_* was split out of total_net_* in Redis 7.0.",
      options=TS, defaults={"unit": "bytes", "custom": TS_LINE},
      transformations=keep(["total_net_repl_input_bytes", "total_net_repl_output_bytes", "time"],
                           rename={"total_net_repl_input_bytes": "Input (7.0)",
                                   "total_net_repl_output_bytes": "Output (7.0)"}))

row("Replication", [
    ("panel-repl-status", 14, 5), ("panel-repl-backlog", 10, 5),
    ("panel-repl-offset", 8, 7), ("panel-repl-sync", 8, 7), ("panel-repl-net", 8, 7),
])


# ---------------------------------------------------------------- Keyspace ---

panel("panel-keyspace-table", "Keyspace", "table", info("keyspace"),
      description=("INFO keyspace. subexpiry counts hash fields carrying their own TTL and was "
                   "added in Redis 7.4."),
      options=TABLE,
      transformations=keep([], rename={"keys": "Keys", "expires": "Expires",
                                       "avg_ttl": "Average TTL", "subexpiry": "Subexpiry (7.4)"}))

panel("panel-keysizes-table", "Key size buckets", "table", info("keysizes"),
      description=("INFO keysizes, added in Redis 8.0. Bucket is the power of two lower bound and "
                   "Range spells it out, so bucket 4 counts keys of size 4 to 7."),
      options=dict(TABLE, sortBy=[{"displayName": "Count", "desc": True}]),
      defaults={"custom": {"align": "auto", "filterable": True}})

panel("panel-keyspace-events", "Pub/Sub", "timeseries", info("stats", stream=True),
      description="pubsubshard_channels arrived with sharded Pub/Sub in Redis 7.0.",
      options=TS, defaults={"custom": TS_LINE},
      transformations=keep(["pubsub_channels", "pubsub_patterns", "pubsubshard_channels", "time"],
                           rename={"pubsub_channels": "Channels", "pubsub_patterns": "Patterns",
                                   "pubsubshard_channels": "Shard channels (7.0)"}))

panel("panel-tracking", "Client side caching", "timeseries", info("stats", stream=True),
      options=TS, defaults={"custom": TS_LINE},
      transformations=keep(["tracking_total_keys", "tracking_total_items",
                            "tracking_total_prefixes", "time"],
                           rename={"tracking_total_keys": "Keys", "tracking_total_items": "Items",
                                   "tracking_total_prefixes": "Prefixes"}))

panel("panel-hash-templates", "Hash field expiration", "stat", info("stats"),
      description=("hash_templates and hash_template_keys report the shared hash field TTL "
                   "templates introduced in Redis 8.x."),
      options=STAT_ALL,
      transformations=keep(["hash_templates", "hash_template_keys"],
                           rename={"hash_templates": "Templates", "hash_template_keys": "Keys"}))

row("Keyspace", [
    ("panel-keyspace-table", 12, 7), ("panel-keysizes-table", 12, 7),
    ("panel-keyspace-events", 8, 6), ("panel-tracking", 8, 6), ("panel-hash-templates", 8, 6),
])


# -------------------------------------------------------- Modules & search ---

panel("panel-modules", "Modules", "table", info("modules"),
      description=("INFO modules. Redis 8 ships search, ReJSON, timeseries, bf and vectorset in "
                   "the server, so this row is populated on a stock 8.x install."),
      options=TABLE,
      transformations=keep([], rename={"ver": "Version", "api": "API", "filters": "Filters",
                                       "usedby": "Used by", "using": "Using", "options": "Options"}))

panel("panel-search-version", "Search", "stat", info("search"),
      options=STAT_ALL,
      transformations=keep(["search_version", "search_number_of_indexes",
                            "search_number_of_active_indexes",
                            "search_number_of_active_indexes_running_queries",
                            "search_number_of_active_indexes_indexing"],
                           rename={"search_version": "Version",
                                   "search_number_of_indexes": "Indexes",
                                   "search_number_of_active_indexes": "Active",
                                   "search_number_of_active_indexes_running_queries": "Querying",
                                   "search_number_of_active_indexes_indexing": "Indexing"}))

panel("panel-search-docs", "Search index size", "timeseries", info("search", stream=True),
      options=TS, defaults={"custom": TS_LINE},
      transformations=keep(["search_total_num_docs_in_indexes",
                            "search_total_inverted_index_blocks",
                            "search_total_indexing_time", "time"],
                           rename={"search_total_num_docs_in_indexes": "Documents",
                                   "search_total_inverted_index_blocks": "Inverted index blocks",
                                   "search_total_indexing_time": "Indexing time"}))

panel("panel-search-memory", "Search memory", "timeseries", info("search", stream=True),
      description="Emitted only once at least one index exists, see the note on the right.",
      options=TS, defaults={"unit": "bytes", "custom": TS_LINE},
      transformations=keep(["search_used_memory_indexes", "search_smallest_memory_index",
                            "search_largest_memory_index", "search_used_memory_vector_index", "time"],
                           rename={"search_used_memory_indexes": "Indexes",
                                   "search_smallest_memory_index": "Smallest index",
                                   "search_largest_memory_index": "Largest index",
                                   "search_used_memory_vector_index": "Vector index"}))

panel("panel-search-queries", "Search queries", "timeseries", info("search", stream=True),
      description="Emitted only once at least one index exists, see the note above.",
      options=TS, defaults={"custom": TS_LINE},
      transformations=keep(["search_total_queries_processed", "search_total_query_commands",
                            "search_total_query_execution_time_ms", "search_total_active_queries", "time"],
                           rename={"search_total_queries_processed": "Processed",
                                   "search_total_query_commands": "Commands",
                                   "search_total_query_execution_time_ms": "Execution time, ms",
                                   "search_total_active_queries": "Active"}))

panel("panel-search-errors", "Search errors and warnings", "timeseries", info("search", stream=True),
      description="Emitted only once at least one index exists, see the note above.",
      options=TS, defaults={"custom": TS_LINE},
      transformations=keep(["search_errors_indexing_failures",
                            "search_OOM_indexing_failures_indexes_count",
                            "search_shard_total_query_errors_timeout",
                            "search_shard_total_query_errors_oom",
                            "search_shard_total_query_warnings_timeout",
                            "search_shard_total_query_warnings_max_prefix_expansions", "time"],
                           rename={"search_errors_indexing_failures": "Indexing failures",
                                   "search_OOM_indexing_failures_indexes_count": "OOM indexing failures",
                                   "search_shard_total_query_errors_timeout": "Query timeouts",
                                   "search_shard_total_query_errors_oom": "Query OOM",
                                   "search_shard_total_query_warnings_timeout": "Timeout warnings",
                                   "search_shard_total_query_warnings_max_prefix_expansions":
                                       "Prefix expansion warnings"}))

panel("panel-search-note", "About the search fields", "text", [],
      options={"mode": "markdown", "content": (
          "`INFO SEARCH` only reports the version and the index counters while the server holds "
          "**no index**: `search_info_on_zero_indexes` is `OFF` by default, and the memory, query, "
          "cursor, garbage collector and dialect sub sections appear as soon as the first index is "
          "created.\n\n"
          "```\nCONFIG SET search-info-on-zero-indexes yes\n```\n\n"
          "turns them on for an empty server. Field names also move between RediSearch builds, so "
          "panels here render *No data* rather than breaking when a name is absent.")})

row("Modules and search", [
    ("panel-modules", 14, 7), ("panel-search-version", 10, 7),
    ("panel-search-docs", 8, 7), ("panel-search-memory", 8, 7), ("panel-search-note", 8, 7),
    ("panel-search-queries", 12, 7), ("panel-search-errors", 12, 7),
])


# ----------------------------------------------------------------- Cluster ---

panel("panel-cluster-nodes", "Nodes", "table", query(command="clusterNodes"),
      description=("CLUSTER NODES. Slots counts the slots served by the node, and a node serving "
                   "several ranges is counted across all of them."),
      options=TABLE,
      defaults={"custom": {"align": "auto", "filterable": True}},
      overrides=[{"matcher": {"id": "byName", "options": "Flags"},
                  "properties": [{"id": "custom.cellOptions", "value": {"type": "color-text"}},
                                 {"id": "mappings", "value": [
                                     {"type": "regex", "options": {
                                         "pattern": ".*fail.*",
                                         "result": {"color": "red", "index": 0}}}]}]}])

panel("panel-cluster-slots", "Slots", "stat", query(command="clusterInfo"),
      options=STAT_ALL,
      transformations=keep(["cluster_slots_assigned", "cluster_slots_ok", "cluster_slots_pfail",
                            "cluster_slots_fail", "cluster_known_nodes", "cluster_size"],
                           rename={"cluster_slots_assigned": "Assigned", "cluster_slots_ok": "Ok",
                                   "cluster_slots_pfail": "Possibly failing",
                                   "cluster_slots_fail": "Failing",
                                   "cluster_known_nodes": "Known nodes", "cluster_size": "Size"}))

panel("panel-cluster-epoch", "Epochs", "stat", query(command="clusterInfo"),
      options=STAT_ALL,
      transformations=keep(["cluster_current_epoch", "cluster_my_epoch"],
                           rename={"cluster_current_epoch": "Current", "cluster_my_epoch": "This node"}))

panel("panel-cluster-messages", "Cluster bus messages", "timeseries",
      query(command="clusterInfo", stream=True),
      options=TS, defaults={"custom": TS_LINE},
      transformations=keep(["cluster_stats_messages_sent", "cluster_stats_messages_received",
                            "cluster_stats_messages_fail_received",
                            "cluster_stats_messages_publish_sent", "time"],
                           rename={"cluster_stats_messages_sent": "Sent",
                                   "cluster_stats_messages_received": "Received",
                                   "cluster_stats_messages_fail_received": "Fail messages received",
                                   "cluster_stats_messages_publish_sent": "Publish sent"}))

panel("panel-cluster-links", "Cluster links", "timeseries", info("memory", stream=True),
      description=("mem_cluster_links, added in Redis 7.0, is the memory the cluster bus buffers "
                   "hold; a link that stops draining shows up here first."),
      options=TS, defaults={"unit": "bytes", "custom": TS_LINE},
      transformations=keep(["mem_cluster_links", "time"],
                           rename={"mem_cluster_links": "Buffers (7.0)"}))

panel("panel-cluster-migration", "Slot migration", "timeseries",
      query(command="clusterInfo", stream=True),
      description="The cluster_slot_migration_* counters are Redis 8.x, atomic slot migration.",
      options=TS, defaults={"custom": TS_LINE},
      transformations=keep(["cluster_slot_migration_active_tasks",
                            "cluster_slot_migration_active_trim_running",
                            "cluster_slot_migration_stats_active_trim_started",
                            "cluster_slot_migration_stats_active_trim_completed",
                            "cluster_slot_migration_stats_active_trim_cancelled",
                            "total_cluster_links_buffer_limit_exceeded", "time"],
                           rename={"cluster_slot_migration_active_tasks": "Active tasks",
                                   "cluster_slot_migration_active_trim_running": "Trim running",
                                   "cluster_slot_migration_stats_active_trim_started": "Trim started",
                                   "cluster_slot_migration_stats_active_trim_completed": "Trim completed",
                                   "cluster_slot_migration_stats_active_trim_cancelled": "Trim cancelled",
                                   "total_cluster_links_buffer_limit_exceeded": "Link buffer limit hit"}))

row("Cluster", [
    ("panel-cluster-nodes", 24, 8),
    ("panel-cluster-slots", 12, 5), ("panel-cluster-epoch", 12, 5),
    ("panel-cluster-messages", 8, 7), ("panel-cluster-links", 8, 7), ("panel-cluster-migration", 8, 7),
])


# ----------------------------------------------------------------- Hotkeys ---

panel("panel-hotkeys", "Hot keys", "table", query(command="hotkeysGet"),
      description=("HOTKEYS GET, added in Redis 8.6. Empty until HOTKEYS START runs, and the "
                   "command is @admin @dangerous so a read only dashboard user gets NOPERM."),
      options=dict(TABLE, sortBy=[{"displayName": "CPU Time", "desc": True}]),
      defaults={"custom": {"align": "auto", "filterable": True}})

panel("panel-hotkeys-note", "Enabling hot key tracking", "text", [],
      options={"mode": "markdown", "content": (
          "`HOTKEYS GET` reports the keys collected since tracking was started. Tracking is not on "
          "by default and the dashboard never starts it, because `HOTKEYS START` and `HOTKEYS STOP` "
          "change server state and a dashboard refresh must not.\n\n"
          "Start it from a shell:\n\n"
          "```\nredis-cli HOTKEYS START\n```\n\n"
          "The command family is `@admin @dangerous`, so a dashboard user needs it granted "
          "explicitly, otherwise the panel shows *NOPERM*:\n\n"
          "```\nACL SETUSER grafana +hotkeys|get\n```")})

panel("panel-hotkeys-memory", "Top keys by memory", "timeseries", info("memory", stream=True),
      description="Peak allocation and the largest single allocation, next to the hot key list.",
      options=TS, defaults={"unit": "bytes", "custom": TS_LINE},
      transformations=keep(["used_memory_peak", "used_memory_startup", "allocator_allocated",
                            "allocator_active", "allocator_resident", "time"],
                           rename={"used_memory_peak": "Peak", "used_memory_startup": "Startup",
                                   "allocator_allocated": "Allocator allocated",
                                   "allocator_active": "Allocator active",
                                   "allocator_resident": "Allocator resident"}))

row("Hot keys", [
    ("panel-hotkeys", 10, 8), ("panel-hotkeys-note", 6, 8), ("panel-hotkeys-memory", 8, 8),
], collapse=True)


# ------------------------------------------------------------------ Output ---

VARIABLES = [
    {
        "kind": "DatasourceVariable",
        "spec": {
            "name": "redis",
            "label": "Redis node",
            "description": "One entry per provisioned Redis Data Source, so a cluster is browsed node by node.",
            "hide": "dontHide",
            "skipUrlSync": False,
            "pluginId": GROUP,
            "regex": "",
            "refresh": "onDashboardLoad",
            "current": {"text": "", "value": ""},
            "options": [],
            "multi": False,
            "includeAll": False,
            "allowCustomValue": True,
        },
    }
]

DASHBOARD = {
    "apiVersion": "dashboard.grafana.app/v2beta1",
    "kind": "Dashboard",
    "metadata": {"name": "redis-8x"},
    "spec": {
        "title": "Redis 8.x",
        "description": (
            "Redis 8.10 overview built on the Redis Data Source. Covers every INFO section the "
            "server reports, including the keysizes, threads, errorstats, latencystats, modules "
            "and search_* sections added since Redis 6.2."
        ),
        "tags": ["redis", "redis-8"],
        "editable": True,
        "cursorSync": "Off",
        "liveNow": False,
        "preload": False,
        "links": [
            {"title": "Redis INFO reference", "type": "link", "icon": "external link",
             "tooltip": "", "url": "https://redis.io/docs/latest/commands/info/", "tags": [],
             "asDropdown": False, "targetBlank": True, "includeVars": False, "keepTime": False}
        ],
        "annotations": [
            {"kind": "AnnotationQuery",
             "spec": {"query": {"kind": "DataQuery", "group": "", "version": "v0", "spec": {}},
                      "enable": True, "hide": True, "iconColor": "rgba(0, 211, 255, 1)",
                      "name": "Annotations & Alerts", "builtIn": True,
                      "legacyOptions": {"type": "dashboard"}}}
        ],
        "timeSettings": {
            "from": "now-15m",
            "to": "now",
            "autoRefresh": "5s",
            "autoRefreshIntervals": ["5s", "10s", "30s", "1m", "5m", "15m", "30m", "1h"],
            "hideTimepicker": False,
            "fiscalYearStartMonth": 0,
        },
        "variables": VARIABLES,
        "elements": elements,
        "layout": {"kind": "RowsLayout", "spec": {"rows": rows}},
    },
}


def main():
    target = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dashboard.json")

    with open(target, "w") as handle:
        json.dump(DASHBOARD, handle, indent=2)
        handle.write("\n")

    print(f"{target}: {len(elements)} panels, {len(rows)} rows")


if __name__ == "__main__":
    main()
