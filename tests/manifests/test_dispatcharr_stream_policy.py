import copy
import runpy
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "apps/production/media/dispatcharr/configure-stream-policy.py"

# Channel/source/link ids and names from the read-only live snapshot.
RAW_ROWS = (
    (91, "DISCOVERY (RAW)", 2923, 468, 3112, "DISCOVERY CHANNEL HD"),
    (92, "ID (RAW)", 2927, 469, 3113, "ID HD"),
    (93, "NAT GEO (RAW)", 2920, 470, 3115, "NATIONAL GEOGRAPHIC HD"),
    (94, "NAT GEO WILD (RAW)", 2922, 471, 3118, "NAT GEO WILD HD"),
    (95, "BBC EARTH (RAW)", 2926, 472, 3120, "BBC EARTH HD"),
    (97, "LOVE NATURE (RAW)", 2929, 474, 3123, "LOVE NATURE 4K"),
    (99, "VIASAT HISTORY (RAW)", 2933, 475, 3126, "VIASAT HISTORY HD"),
    (103, "TRT BELGESEL (RAW)", 2950, 476, 3131, "TRT BELGESEL HD"),
    (105, "DMAX (RAW)", 2948, 477, 3134, "DMAX HD"),
    (106, "TLC (RAW)", 2945, 478, 3135, "TLC HD"),
    (111, "DOCUSCREEN (RAW)", 2938, 480, 5451, "DOCU SCREEN HD"),
)
HD_ROWS = (
    (114, "DISCOVERY CHANNEL HD", 3112, 483, 2923),
    (116, "ID HD", 3113, 485, 2927),
    (112, "NATIONAL GEOGRAPHIC HD", 3115, 481, 2920),
    (113, "NAT GEO WILD HD", 3118, 482, 2922),
    (115, "BBC EARTH HD", 3120, 484, 2926),
    (117, "LOVE NATURE HD", 3122, 486, 2929),
    (119, "VIASAT HISTORY HD", 3126, 487, 2933),
    (124, "TRT BELGESEL HD", 3131, 491, 2950),
    (123, "DMAX HD", 3134, 490, 2948),
    (122, "TLC HD", 3135, 489, 2945),
    (121, "DOCU SCREEN HD", 5451, 488, 2938),
)


def live_fixture():
    channels, streams, links = [], {}, []
    def channel(pk, name, group):
        channels.append(dict(id=pk, name=name, auto_created_by_id=2, channel_group_id=group))
    def stream(pk, name, group):
        streams[pk] = dict(id=pk, name=name, m3u_account_id=2, channel_group_id=group, is_stale=False)
    def link(pk, channel_id, stream_id, order):
        links.append(dict(id=pk, channel_id=channel_id, stream_id=stream_id, order=order))
    for pk, name, raw, retained_link, other, other_name in RAW_ROWS:
        channel(pk, name, 7)
        stream(raw, name, 7)
        stream(other, other_name, 6)
        link(pk, pk, other, 0)
        link(retained_link, pk, raw, 1)
    for pk, name, hd, retained_link, raw in HD_ROWS:
        channel(pk, name, 6)
        stream(hd, name, 6)
        link(pk, pk, raw, 0)
        link(retained_link, pk, hd, 1)
    channel(96, "LOVE NATURE HD", 6)
    link(96, 96, 3122, 0)
    link(473, 96, 2929, 1)
    channel(31, "TRT 1 (RAW)", 3)
    stream(2888, "TRT 1 (RAW)", 3)
    link(31, 31, 2888, 0)
    return channels, list(streams.values()), links


def apply_plan(channels, links, plan):
    channels = copy.deepcopy(channels)
    links = [dict(link) for link in links if link["id"] not in plan["delete_link_ids"]]
    for link in links:
        if link["id"] in plan["orders"]:
            link["order"] = plan["orders"][link["id"]]
    for channel in channels:
        channel.update(plan["restore_channels"].get(channel["id"], {}))
    return channels, links


class StreamPolicyPlanTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = staticmethod(runpy.run_path(str(SCRIPT))["plan_policy"])

    def test_real_bbc_links_select_raw_and_keep_separate_hd_identity(self):
        channels, streams, links = live_fixture()
        plan = self.plan(channels, streams, links)
        self.assertIn(95, plan["delete_link_ids"])
        self.assertIn(115, plan["delete_link_ids"])
        self.assertEqual(plan["orders"][472], 0)
        self.assertEqual(plan["orders"][484], 0)
        channels, links = apply_plan(channels, links, plan)
        self.assertEqual([r["stream_id"] for r in links if r["channel_id"] == 95], [2926])
        self.assertEqual([r["stream_id"] for r in links if r["channel_id"] == 115], [3120])

    def test_snapshot_removes_23_wrong_links_without_orphaning_channels(self):
        channels, streams, links = live_fixture()
        before = copy.deepcopy((channels, streams, links))
        plan = self.plan(channels, streams, links)
        expected = {r[0] for r in RAW_ROWS} | {r[0] for r in HD_ROWS} | {473}
        self.assertEqual(set(plan["delete_link_ids"]), expected)
        self.assertEqual(len(expected), 23)
        self.assertEqual(plan["unresolved_channel_ids"], [])
        self.assertEqual((channels, streams, links), before)
        after_channels, after_links = apply_plan(channels, links, plan)
        self.assertEqual({c["id"] for c in after_channels}, {c["id"] for c in channels})
        self.assertEqual({r["channel_id"] for r in after_links}, {c["id"] for c in channels})
        self.assertTrue(all(r["order"] == 0 for r in after_links))
        self.assertIn(dict(id=31, channel_id=31, stream_id=2888, order=0), after_links)

    def test_applied_snapshot_is_idempotent(self):
        channels, streams, links = live_fixture()
        channels, links = apply_plan(channels, links, self.plan(channels, streams, links))
        second = self.plan(channels, streams, links)
        self.assertEqual(second["delete_link_ids"], [])
        self.assertEqual(second["orders"], {})
        self.assertEqual(second["restore_channels"], {})
        self.assertEqual(second["unresolved_channel_ids"], [])

    def test_missing_own_raw_link_preserves_unknown_source(self):
        channels, streams, links = live_fixture()
        links = [r for r in links if r["id"] != 472]
        plan = self.plan(channels, streams, links)
        self.assertIn(95, plan["unresolved_channel_ids"])
        self.assertNotIn(95, plan["delete_link_ids"])
        self.assertNotIn(115, plan["delete_link_ids"])
        self.assertNotIn(95, plan["restore_channels"])

    def test_missing_own_hd_link_preserves_peer_raw_link_and_metadata(self):
        channels, streams, links = live_fixture()
        links = [r for r in links if r["id"] != 484]
        next(c for c in channels if c["id"] == 115).update(name="BBC EARTH (RAW)", channel_group_id=7)
        plan = self.plan(channels, streams, links)
        self.assertIn(115, plan["unresolved_channel_ids"])
        self.assertNotIn(115, plan["delete_link_ids"])
        self.assertNotIn(115, plan["restore_channels"])

    def test_refresh_renamed_peer_restores_verified_hd_name_and_group(self):
        channels, streams, links = live_fixture()
        next(c for c in channels if c["id"] == 115).update(name="BBC EARTH (RAW)", channel_group_id=7)
        plan = self.plan(channels, streams, links)
        self.assertEqual(plan["restore_channels"][115], dict(name="BBC EARTH HD", channel_group_id=6))
        self.assertIn(115, plan["delete_link_ids"])

    def test_wrong_source_account_group_or_stale_source_cannot_pass_guard(self):
        for change in (dict(m3u_account_id=1), dict(channel_group_id=6), dict(is_stale=True), dict(name="BBC EARTH HD")):
            with self.subTest(change=change):
                channels, streams, links = live_fixture()
                next(s for s in streams if s["id"] == 2926).update(change)
                plan = self.plan(channels, streams, links)
                self.assertIn(95, plan["unresolved_channel_ids"])
                self.assertNotIn(95, plan["delete_link_ids"])

    def test_other_raw_channels_extra_source_cannot_authorize_bbc_peer_cleanup(self):
        channels, streams, links = live_fixture()
        next(r for r in links if r["id"] == 472)["channel_id"] = 91
        plan = self.plan(channels, streams, links)
        self.assertIn(95, plan["unresolved_channel_ids"])
        self.assertNotIn(115, plan["delete_link_ids"])

    def test_unresolved_plan_rejects_execution(self):
        channels, streams, links = live_fixture()
        links = [r for r in links if r["id"] != 472]
        plan = self.plan(channels, streams, links)
        with self.assertRaisesRegex(RuntimeError, "95"):
            runpy.run_path(str(SCRIPT))["require_resolved"](plan)

    def test_first_limit_transition_stops_only_account2_profile2_assignments(self):
        shutdowns = runpy.run_path(str(SCRIPT))["plan_shutdowns"]
        assignments = [
            dict(channel_id=95, uuid="channel-95", stream_id=3120, account_id=2, profile_id=2),
            dict(channel_id=115, uuid="channel-115", stream_id=3120, account_id=2, profile_id=2),
            dict(channel_id=31, uuid="channel-31", stream_id=2888, account_id=2, profile_id=1),
            dict(channel_id=95, uuid="other-account", stream_id=3120, account_id=1, profile_id=2),
        ]
        self.assertEqual(shutdowns(assignments, [], {95, 115}, True), ["channel-95", "channel-115"])

    def test_later_stop_matches_removed_channel_source_pair_only(self):
        shutdowns = runpy.run_path(str(SCRIPT))["plan_shutdowns"]
        assignments = [
            dict(channel_id=95, uuid="channel-95", stream_id=3120, account_id=2, profile_id=2),
            dict(channel_id=115, uuid="channel-115", stream_id=3120, account_id=2, profile_id=2),
        ]
        allowed = [dict(channel_id=95, stream_id=2926), dict(channel_id=115, stream_id=3120)]
        self.assertEqual(shutdowns(assignments, allowed, {95, 115}, False), ["channel-95"])
        assignments[0]["stream_id"] = 2926
        self.assertEqual(shutdowns(assignments, allowed, {95, 115}, False), [])
        self.assertEqual(shutdowns(assignments, allowed, set(), False), [])

    def test_retry_after_links_committed_still_finds_old_hd_assignment(self):
        channels, streams, links = live_fixture()
        channels, allowed = apply_plan(channels, links, self.plan(channels, streams, links))
        self.assertEqual(self.plan(channels, streams, allowed)["delete_link_ids"], [])
        assignments = [dict(channel_id=95, uuid="channel-95", stream_id=3120, account_id=2, profile_id=2)]
        shutdowns = runpy.run_path(str(SCRIPT))["plan_shutdowns"]
        self.assertEqual(shutdowns(assignments, allowed, {c["id"] for c in channels}, False), ["channel-95"])

    def test_slot_migration_pending_survives_committed_limits_until_cleanup(self):
        pending = runpy.run_path(str(SCRIPT))["slot_migration_pending"]
        key = "dispatcharr_stream_policy_slot_migration_pending"
        self.assertTrue(pending(0, 0, {}))
        self.assertTrue(pending(1, 1, {key: True}))
        self.assertFalse(pending(1, 1, {}))

    def test_stop_ack_requires_the_captured_owner_and_channel(self):
        ack = runpy.run_path(str(SCRIPT))["owner_stop_ack"]
        event = dict(event="channel_stopped", channel_id="channel-95", worker_id="web:123")
        self.assertTrue(ack(event, "channel-95", "web:123"))
        self.assertFalse(ack(event, "channel-95", "celery:456"))
        self.assertFalse(ack(event, "channel-115", "web:123"))
        self.assertFalse(ack(dict(event, event="channel_started"), "channel-95", "web:123"))
    def test_channels_from_other_account_are_untouched(self):
        channels, streams, links = live_fixture()
        next(c for c in channels if c["id"] == 95)["auto_created_by_id"] = 1
        plan = self.plan(channels, streams, links)
        self.assertNotIn(95, plan["delete_link_ids"])
        self.assertNotIn(95, plan["restore_channels"])




class StreamPolicyRedisTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = runpy.run_path(str(SCRIPT))

    def assignment(self, owner=None):
        row = dict(channel_id=95, uuid="00000000-0000-0000-0000-000000000095", stream_id=3120, account_id=2, profile_id=2)
        if owner:
            row["owner"] = owner
            row["init_time"] = "100.0"
        return row

    def test_active_metadata_wins_over_conflicting_cached_source(self):
        from unittest.mock import Mock
        for metadata_sid, cached_sid in ((2926, 3120), (3120, 2926)):
            with self.subTest(metadata_sid=metadata_sid):
                row = self.assignment()
                metadata_key = f"live:channel:{row['uuid']}:metadata"
                redis = Mock()
                redis.scan_iter.side_effect = [[metadata_key], ["channel_stream:95"]]
                redis.hmget.return_value = ("active", str(metadata_sid), "2")
                redis.hget.return_value = "2"
                redis.get.return_value = str(cached_sid)
                channel, stream = Mock(), Mock()
                channel.objects.filter.return_value.values.return_value.first.return_value = dict(id=95, uuid=row["uuid"])
                stream.objects.filter.return_value.exists.return_value = True
                actual = self.policy["read_assignments"](redis, channel, stream)
                self.assertEqual(actual[0]["stream_id"], metadata_sid)

    def test_inactive_cached_assignment_uses_normal_release_fallback(self):
        from unittest.mock import Mock
        redis, service, channel = Mock(), Mock(), Mock()
        state = {"cached": "3120"}
        redis.get.side_effect = lambda key: state["cached"] if key == "channel_stream:95" else None
        redis.hget.return_value = None
        redis.hmget.return_value = (None, None, None, None, None)
        service.stop_channel.return_value = dict(status="error", message="Channel not found")
        channel.objects.get.return_value.release_stream.side_effect = lambda: state.update(cached=None)
        self.policy["stop_assignment"](redis, self.assignment(), service, channel)
        service.stop_channel.assert_called_once_with(self.assignment()["uuid"])
        channel.objects.get.return_value.release_stream.assert_called_once_with()
        self.assertIsNone(state["cached"])

    def test_pending_stop_never_stops_a_new_valid_raw_producer(self):
        from unittest.mock import Mock
        redis, service, channel = Mock(), Mock(), Mock()
        redis.get.return_value = "3120"
        redis.hget.return_value = "2926"
        redis.hmget.return_value = ("active", "web:123", "2926", "2", "100.0")
        with self.assertRaises(RuntimeError):
            self.policy["stop_assignment"](redis, self.assignment("web:123"), service, channel)
        service.stop_channel.assert_not_called()
        channel.objects.get.return_value.release_stream.assert_not_called()

    def test_lost_owner_ack_cannot_become_success_on_retry(self):
        from unittest.mock import Mock
        redis, service, channel = Mock(), Mock(), Mock()
        redis.get.return_value = None
        redis.hget.return_value = None
        redis.hmget.return_value = (None, None, None, None, None)
        with self.assertRaisesRegex(RuntimeError, "completion"):
            self.policy["stop_assignment"](redis, self.assignment("web:123"), service, channel)
        service.stop_channel.assert_not_called()

    def test_same_source_worker_restart_does_not_stop_new_raw_generation(self):
        from unittest.mock import Mock
        redis, service, channel = Mock(), Mock(), Mock()
        row = self.assignment("web:123")
        row["stream_id"] = 2926
        values = dict(state="active", owner="web:123", stream_id="2926", m3u_profile="2", init_time="200.0")
        redis.hmget.side_effect = lambda key, *fields: tuple(values[f] for f in fields)
        redis.get.return_value = "2926"
        redis.hget.return_value = "2926"
        redis.pubsub.side_effect = AssertionError("New producer subscription attempted")
        with self.assertRaisesRegex(RuntimeError, "Producer changed"):
            self.policy["stop_assignment"](redis, row, service, channel)
        service.stop_channel.assert_not_called()
        channel.objects.get.return_value.release_stream.assert_not_called()
    def test_normal_stop_waits_for_original_owner_ack_and_old_keys_to_clear(self):
        import json
        from unittest.mock import Mock
        redis, service, channel, events = Mock(), Mock(), Mock(), Mock()
        row = self.assignment("web:123")
        state = {"old": "3120"}
        redis.get.side_effect = lambda key: state["old"]
        redis.hget.side_effect = lambda key, field: state["old"] if field == "stream_id" else None
        redis.hmget.return_value = ("active", "web:123", "3120", "2", "100.0")
        redis.pubsub.return_value.__enter__ = Mock(return_value=events)
        redis.pubsub.return_value.__exit__ = Mock(return_value=False)
        events.get_message.side_effect = [
            dict(type="subscribe"),
            dict(type="message", data=json.dumps(dict(event="channel_stopped", channel_id=row["uuid"], worker_id="other:456"))),
            dict(type="message", data=json.dumps(dict(event="channel_stopped", channel_id=row["uuid"], worker_id="web:123"))),
        ]
        def normal_stop(uid):
            self.assertEqual(events.get_message.call_count, 1)
            state["old"] = None
            return dict(status="success", local_stop_result=True)
        service.stop_channel.side_effect = normal_stop
        self.policy["stop_assignment"](redis, row, service, channel)
        self.assertEqual(events.get_message.call_count, 3)
        channel.objects.get.return_value.release_stream.assert_not_called()

class StreamPolicyMountTest(unittest.TestCase):
    def test_kustomize_mounts_generated_script_content_in_v6_job(self):
        import subprocess
        import yaml
        resources = list(yaml.safe_load_all(subprocess.check_output(
            ["kustomize", "build", str(SCRIPT.parent)], text=True, encoding="utf-8")))
        job = next(r for r in resources if r.get("kind") == "Job" and r["metadata"]["name"] == "dispatcharr-stream-settings-v6")
        config = next(r for r in resources if r.get("kind") == "ConfigMap" and "configure-stream-policy.py" in r.get("data", {}))
        self.assertRegex(config["metadata"]["name"], r"^dispatcharr-stream-policy-[a-z0-9]+$")
        self.assertEqual(config["data"]["configure-stream-policy.py"], SCRIPT.read_bytes().decode("utf-8"))
        spec = job["spec"]["template"]["spec"]
        volume = next(v for v in spec["volumes"] if v["name"] == "stream-policy")
        self.assertEqual(volume["configMap"]["name"], config["metadata"]["name"])
        container = spec["containers"][0]
        self.assertIn(dict(name="stream-policy", mountPath="/policy", readOnly=True), container["volumeMounts"])
        self.assertIn('runpy.run_path("/policy/configure-stream-policy.py", run_name="__main__")', container["command"][-1])

if __name__ == "__main__":
    unittest.main()
