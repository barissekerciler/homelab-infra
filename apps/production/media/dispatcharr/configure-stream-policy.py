"""Keep verified RAW/HD channel identities separate without changing sources."""

import re

ACCOUNT_ID = 2
PROFILE_ID = 2
RAW_TOKEN = re.compile(r"\bRAW\b", re.IGNORECASE)
# Stable ids from the read-only provider/channel snapshot; group ids are 7/6.
RAW_CHANNELS = {
    91: (2923, "DISCOVERY (RAW)"),
    92: (2927, "ID (RAW)"),
    93: (2920, "NAT GEO (RAW)"),
    94: (2922, "NAT GEO WILD (RAW)"),
    95: (2926, "BBC EARTH (RAW)"),
    97: (2929, "LOVE NATURE (RAW)"),
    99: (2933, "VIASAT HISTORY (RAW)"),
    103: (2950, "TRT BELGESEL (RAW)"),
    105: (2948, "DMAX (RAW)"),
    106: (2945, "TLC (RAW)"),
    111: (2938, "DOCUSCREEN (RAW)"),
}
HD_CHANNELS = {
    96: (3122, "LOVE NATURE HD", 2929),
    112: (3115, "NATIONAL GEOGRAPHIC HD", 2920),
    113: (3118, "NAT GEO WILD HD", 2922),
    114: (3112, "DISCOVERY CHANNEL HD", 2923),
    115: (3120, "BBC EARTH HD", 2926),
    116: (3113, "ID HD", 2927),
    117: (3122, "LOVE NATURE HD", 2929),
    119: (3126, "VIASAT HISTORY HD", 2933),
    121: (5451, "DOCU SCREEN HD", 2938),
    122: (3135, "TLC HD", 2945),
    123: (3134, "DMAX HD", 2948),
    124: (3131, "TRT BELGESEL HD", 2950),
}


def plan_policy(channels, streams, links):
    """Return guarded link/order/identity edits; never modify the input rows."""
    streams = {s["id"]: s for s in streams}
    by_channel = {}
    for link in sorted(links, key=lambda row: (row["order"], row["id"])):
        by_channel.setdefault(link["channel_id"], []).append(link)
    plan = dict(delete_link_ids=[], orders={}, restore_channels={}, unresolved_channel_ids=[])
    valid_raw_sources = set()

    def owned(stream):
        return stream and stream["m3u_account_id"] == ACCOUNT_ID and not stream.get("is_stale", False)

    def finish(channel, retained, deleted, identity=None):
        plan["delete_link_ids"].extend(row["id"] for row in deleted)
        for order, row in enumerate(retained):
            if row["order"] != order:
                plan["orders"][row["id"]] = order
        if identity and any(channel.get(k) != v for k, v in identity.items()):
            plan["restore_channels"][channel["id"]] = identity

    # HD peers can acquire a RAW name during refresh; their verified ids take
    # precedence over the current name so they cannot enter the RAW pass.
    for channel in sorted(channels, key=lambda row: row["id"]):
        pk = channel["id"]
        if channel["auto_created_by_id"] != ACCOUNT_ID or pk in HD_CHANNELS:
            continue
        if pk not in RAW_CHANNELS and not RAW_TOKEN.search(channel["name"]):
            continue
        rows = by_channel.get(pk, [])
        own_raw = [r for r in rows if owned(streams.get(r["stream_id"]))
                   and RAW_TOKEN.search(streams[r["stream_id"]]["name"])
                   and streams[r["stream_id"]]["channel_group_id"] == (7 if pk in RAW_CHANNELS else channel["channel_group_id"])]
        if pk in RAW_CHANNELS:
            source_id, name = RAW_CHANNELS[pk]
            own_raw = [r for r in own_raw if r["stream_id"] == source_id and streams[source_id]["name"] == name]
        if not own_raw or any(not owned(streams.get(r["stream_id"])) for r in rows):
            plan["unresolved_channel_ids"].append(pk)
            continue
        retained = [r for r in rows if RAW_TOKEN.search(streams[r["stream_id"]]["name"])]
        deleted = [r for r in rows if r not in retained]
        if pk in RAW_CHANNELS:
            valid_raw_sources.update(r["stream_id"] for r in own_raw)
        identity = dict(name=RAW_CHANNELS[pk][1], channel_group_id=7) if pk in RAW_CHANNELS else None
        finish(channel, retained, deleted, identity)

    for channel in sorted(channels, key=lambda row: row["id"]):
        pk = channel["id"]
        if pk not in HD_CHANNELS or channel["auto_created_by_id"] != ACCOUNT_ID:
            continue
        source_id, name, raw_id = HD_CHANNELS[pk]
        rows = by_channel.get(pk, [])
        source = streams.get(source_id)
        own_hd = any(r["stream_id"] == source_id for r in rows)
        raw_links = [r for r in rows if streams.get(r["stream_id"]) and RAW_TOKEN.search(streams[r["stream_id"]]["name"])]
        if (not own_hd or not owned(source) or source["name"] != name or source["channel_group_id"] != 6
                or any(not owned(streams.get(r["stream_id"])) for r in rows)
                or any(r["stream_id"] != raw_id or raw_id not in valid_raw_sources for r in raw_links)):
            plan["unresolved_channel_ids"].append(pk)
            continue
        finish(channel, [r for r in rows if r not in raw_links], raw_links, dict(name=name, channel_group_id=6))

    plan["delete_link_ids"].sort()
    plan["unresolved_channel_ids"].sort()
    return plan


def require_resolved(plan):
    if plan["unresolved_channel_ids"]:
        raise RuntimeError("Unresolved stream policy channel ids: " + str(plan["unresolved_channel_ids"]))


SLOT_PENDING_KEY = "dispatcharr_stream_policy_slot_migration_pending"
STOP_PENDING_KEY = "dispatcharr_stream_policy_pending_stops"


def slot_migration_pending(account_limit, profile_limit, settings):
    return account_limit == 0 or profile_limit == 0 or settings.get(SLOT_PENDING_KEY) is True


def plan_shutdowns(assignments, allowed_links, channel_ids, limit_transition):
    """Membership checks still find old assignments after link edits commit."""
    allowed_pairs = {(r["channel_id"], r["stream_id"]) for r in allowed_links}
    return list(dict.fromkeys(row["uuid"] for row in assignments
                if row["account_id"] == ACCOUNT_ID and row["profile_id"] == PROFILE_ID
                and (limit_transition or (row["channel_id"] in channel_ids
                     and (row["channel_id"], row["stream_id"]) not in allowed_pairs))))


def owner_stop_ack(event, channel_uuid, owner):
    return (event.get("event") == "channel_stopped" and str(event.get("channel_id")) == channel_uuid
            and event.get("worker_id") == owner)


def redis_text(value):
    return value.decode() if isinstance(value, bytes) else str(value) if value is not None else None


def read_assignments(redis_client, Channel, Stream):
    """Read only ids and producer state; include leftover cached assignments."""
    from uuid import UUID
    assignments = {}
    active_uuids = set()

    def collect(channel, stream_id, profile_id):
        try:
            stream_id, profile_id = int(stream_id), int(profile_id)
        except (TypeError, ValueError):
            return
        if profile_id == PROFILE_ID and Stream.objects.filter(pk=stream_id, m3u_account_id=ACCOUNT_ID).exists():
            uid = str(channel["uuid"])
            assignments[uid] = dict(channel_id=channel["id"], uuid=uid, stream_id=stream_id,
                                    account_id=ACCOUNT_ID, profile_id=PROFILE_ID)

    for key in redis_client.scan_iter(match="live:channel:*:metadata", count=100):
        uid = redis_text(key).split(":")[2]
        try:
            UUID(uid)
        except ValueError:
            continue
        state, sid, pid = [redis_text(v) for v in redis_client.hmget(key, "state", "stream_id", "m3u_profile")]
        if state not in {"active", "connecting", "initializing", "waiting_for_clients", "buffering"} or not sid:
            continue
        channel = Channel.objects.filter(uuid=uid).values("id", "uuid").first()
        if channel:
            active_uuids.add(str(channel["uuid"]))
            collect(channel, sid, pid or redis_text(redis_client.get(f"stream_profile:{sid}")))
    for key in redis_client.scan_iter(match="channel_stream:*", count=100):
        try:
            channel_id = int(redis_text(key).split(":")[1])
        except ValueError:
            continue
        channel = Channel.objects.filter(pk=channel_id).values("id", "uuid").first()
        sid = redis_text(redis_client.get(key))
        if channel and sid and str(channel["uuid"]) not in active_uuids:
            pid = redis_text(redis_client.hget(f"live:channel:{channel['uuid']}:metadata", "m3u_profile"))
            collect(channel, sid, pid or redis_text(redis_client.get(f"stream_profile:{sid}")))
    return list(assignments.values())


def stop_assignment(redis_client, assignment, ChannelService, Channel):
    """Require owner completion, or release an inactive cached assignment."""
    import json
    import time
    uid, pk, sid = assignment["uuid"], assignment["channel_id"], str(assignment["stream_id"])
    metadata_key = f"live:channel:{uid}:metadata"
    assignment_key = f"channel_stream:{pk}"
    state, owner, current_sid, profile, init_time = [redis_text(v) for v in redis_client.hmget(
        metadata_key, "state", "owner", "stream_id", "m3u_profile", "init_time")]
    cached_sid = redis_text(redis_client.get(assignment_key))
    active = state in {"active", "connecting", "initializing", "waiting_for_clients", "buffering"}
    captured_owner = assignment.get("owner")
    if captured_owner and not active:
        raise RuntimeError(f"Owner completion still unconfirmed for channel id {pk}")
    if active and (current_sid != sid or profile != str(PROFILE_ID) or owner != captured_owner
                   or not assignment.get("init_time") or init_time != assignment["init_time"]):
        raise RuntimeError(f"Producer changed before stop for channel id {pk}")
    if not active:
        if cached_sid != sid:
            if cached_sid is not None:
                raise RuntimeError(f"Cached source changed before release for channel id {pk}")
            return
        result = ChannelService.stop_channel(uid)
        if (result.get("status") in {"channel_not_found", "not_found"} or result.get("reason") == "channel_not_found"
                or (result.get("status") == "error" and result.get("message") == "Channel not found")):
            if redis_text(redis_client.get(assignment_key)) == sid:
                Channel.objects.get(pk=pk, uuid=uid).release_stream()
        elif result.get("status") != "success" or result.get("local_stop_result") is False:
            raise RuntimeError(f"Cached assignment stop failed for channel id {pk}")
        if redis_text(redis_client.get(assignment_key)) == sid or redis_text(redis_client.hget(metadata_key, "stream_id")) == sid:
            raise RuntimeError(f"Cached assignment release not confirmed for channel id {pk}")
        return
    if not captured_owner:
        raise RuntimeError(f"Active stream owner missing for channel id {pk}")
    with redis_client.pubsub() as events:
        events.subscribe(f"live:events:{uid}")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            message = events.get_message(ignore_subscribe_messages=False, timeout=0.2)
            if message and message["type"] == "subscribe":
                break
        else:
            raise RuntimeError(f"Stop subscription not ready for channel id {pk}")
        result = ChannelService.stop_channel(uid)
        if result.get("status") != "success" or result.get("local_stop_result") is False:
            raise RuntimeError(f"Normal stop failed for channel id {pk}")
        acked = False
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            message = events.get_message(ignore_subscribe_messages=True, timeout=0.2)
            if message and message["type"] == "message":
                try:
                    event = json.loads(message["data"])
                except (ValueError, TypeError):
                    continue
                if isinstance(event, dict):
                    acked = acked or owner_stop_ack(event, uid, captured_owner)
            old_cached = redis_text(redis_client.get(assignment_key)) == sid
            old_metadata = redis_text(redis_client.hget(metadata_key, "stream_id")) == sid
            if acked and not old_cached and not old_metadata:
                return
        raise RuntimeError(f"Owner stop not confirmed for channel id {pk}")

def apply_policy():
    # Import only at execution time: planners run without Django or a live DB.
    from django.db import transaction
    from django.db.models import Q
    from apps.channels.models import Channel, ChannelStream, Stream
    from apps.m3u.models import M3UAccount, M3UAccountProfile
    from apps.proxy.live_proxy.services.channel_service import ChannelService
    from core.models import CoreSettings
    from core.utils import RedisClient

    with transaction.atomic():
        account = M3UAccount.objects.select_for_update().only("id", "max_streams").get(pk=ACCOUNT_ID, name="kalamar", is_active=True)
        profile = M3UAccountProfile.objects.select_for_update().only("id", "max_streams").get(pk=PROFILE_ID, m3u_account_id=ACCOUNT_ID, is_default=True, is_active=True)
        proxy = CoreSettings.objects.select_for_update().get(key="proxy_settings")
        channel_rows = list(Channel.objects.select_for_update().filter(
            Q(name__icontains="RAW") | Q(pk__in=set(RAW_CHANNELS) | set(HD_CHANNELS)),
            auto_created_by_id=ACCOUNT_ID,
        ).order_by("id").values("id", "name", "auto_created_by_id", "channel_group_id"))
        channel_ids = [row["id"] for row in channel_rows]
        source_ids = ChannelStream.objects.filter(channel_id__in=channel_ids).values_list("stream_id", flat=True)
        stream_rows = list(Stream.objects.select_for_update().filter(pk__in=source_ids).order_by("id").values(
            "id", "name", "m3u_account_id", "channel_group_id", "is_stale"))
        link_rows = list(ChannelStream.objects.select_for_update().filter(channel_id__in=channel_ids).order_by("id").values(
            "id", "channel_id", "stream_id", "order"))
        plan = plan_policy(channel_rows, stream_rows, link_rows)
        require_resolved(plan)
        ChannelStream.objects.filter(pk__in=plan["delete_link_ids"]).delete()
        for link_id, order in plan["orders"].items():
            ChannelStream.objects.filter(pk=link_id).update(order=order)
        for channel_id, fields in plan["restore_channels"].items():
            Channel.objects.filter(pk=channel_id).update(**fields)
        settings = dict(proxy.value or {})
        pending = slot_migration_pending(account.max_streams, profile.max_streams, settings)
        targets_saved = STOP_PENDING_KEY in settings
        targets = list(settings.get(STOP_PENDING_KEY, []))
        # QuerySet updates preserve credentials and provider refresh schedules;
        # both values are set because this path bypasses default-profile sync.
        M3UAccount.objects.filter(pk=account.pk).update(max_streams=1)
        M3UAccountProfile.objects.filter(pk=profile.pk).update(max_streams=1)
        settings["channel_shutdown_delay"] = 0
        if pending:
            settings[SLOT_PENDING_KEY] = True
        proxy.value = settings
        proxy.save(update_fields=["value"])

    def save_targets(current_targets, complete=False):
        with transaction.atomic():
            current_proxy = CoreSettings.objects.select_for_update().get(key="proxy_settings")
            current_settings = dict(current_proxy.value or {})
            if complete:
                current_settings.pop(STOP_PENDING_KEY, None)
                current_settings.pop(SLOT_PENDING_KEY, None)
            else:
                current_settings[STOP_PENDING_KEY] = current_targets
            current_proxy.value = current_settings
            current_proxy.save(update_fields=["value"])

    # Freeze first-drain targets once. A retry never drains new correct RAW
    # producers just because the first migration still awaits an owner ACK.
    redis_client = RedisClient.get_client()
    assignments = read_assignments(redis_client, Channel, Stream)
    allowed_links = list(ChannelStream.objects.filter(channel_id__in=channel_ids).values("channel_id", "stream_id"))
    shutdowns = plan_shutdowns(assignments, allowed_links, set(channel_ids), pending and not targets_saved)
    saved_uuids = {target["uuid"] for target in targets}
    for assignment in assignments:
        if assignment["uuid"] not in shutdowns or assignment["uuid"] in saved_uuids:
            continue
        key = f"live:channel:{assignment['uuid']}:metadata"
        state, owner, sid, pid, init_time = [redis_text(v) for v in redis_client.hmget(
            key, "state", "owner", "stream_id", "m3u_profile", "init_time")]
        active = state in {"active", "connecting", "initializing", "waiting_for_clients", "buffering"}
        if active and (sid != str(assignment["stream_id"]) or pid != str(PROFILE_ID)):
            continue
        targets.append(dict(assignment, owner=owner if active else None, init_time=init_time if active else None))
        saved_uuids.add(assignment["uuid"])
    if targets or pending or targets_saved:
        # Only ids/source/owner/start time are persisted; no URL, command or credential.
        save_targets(targets)
        stopped = len(targets)
        for target in list(targets):
            stop_assignment(redis_client, target, ChannelService, Channel)
            targets.remove(target)
            save_targets(targets)
        save_targets([], complete=True)
    else:
        stopped = 0
    print("stream_policy", "removed_links", plan["delete_link_ids"], "reordered_links", len(plan["orders"]),
          "restored_channels", sorted(plan["restore_channels"]), "stopped_channels", stopped,
          "account_id", ACCOUNT_ID, "profile_id", PROFILE_ID, "max_streams", 1, "channel_shutdown_delay", 0)


if __name__ == "__main__":
    apply_policy()
