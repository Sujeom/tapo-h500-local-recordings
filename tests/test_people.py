"""One person, however many times the hub clustered their face.

The hub hands out a stable id per cluster and clusters the same person more
than once -- different light, a hat, a different angle. Naming both clusters is
the only way to say they are one person, and until now nothing believed it: two
sensors called Alice, two arrival events for one arrival, and a trail split in
half so the direction she was walking could not be worked out from either half.

The merge is run for real against the coordinator; the entity wiring is checked
statically, the way every other platform here is.
"""
import asyncio
import importlib
import sys
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
SENSOR = (COMPONENT / "sensor.py").read_text()
BINARY = (COMPONENT / "binary_sensor.py").read_text()

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

coordinator_mod = importlib.import_module("tapo_h500.coordinator")
const = importlib.import_module("tapo_h500.const")

NOW = 1_786_600_000

CAMERAS = [{"device_id": "cam0", "alias": "Gate"},
           {"device_id": "cam1", "alias": "Door"}]


def sighting(face_id, when):
    return {"startTime": when, "endTime": when + 10,
            "events_1": 1 << (20 - 1),
            "event_info": [{"face_id": face_id}]}


class _Client:
    def __init__(self):
        self.per_camera = {}

    def cameras(self):
        return list(CAMERAS)

    def _for(self, camera):
        return list(self.per_camera.get(camera["alias"], []))

    def recent(self, camera, start, end):
        return self._for(camera)

    def detections(self, camera, start, end):
        return self._for(camera)

    def hub_status(self):
        return {}


def build(names=None, ranks=None):
    client = _Client()
    options = {"face_names": names or {}}
    if ranks is not None:
        options["camera_order"] = ranks
    coord = coordinator_mod.H500Coordinator(
        harness._Hass(), harness._Entry(20, **options), client)
    coord._download_new = lambda *a, **k: None
    return coord, client


def poll(coord):
    coord.data = asyncio.run(coord._async_update_data())
    return coord.data


class Merging(unittest.TestCase):
    def test_two_clusters_with_one_name_are_one_person(self):
        coord, client = build({"11": "Alice", "22": "Alice"})
        client.per_camera["Gate"] = [sighting(11, NOW - 300),
                                     sighting(22, NOW - 60)]
        poll(coord)
        people = coord.people()
        self.assertEqual(sorted(people), ["Alice"])
        self.assertEqual(people["Alice"]["ids"], ["11", "22"])

    def test_their_sightings_are_added_up(self):
        """Two entities each showing half the count is worse than one showing
        the wrong count, because both look plausible."""
        coord, client = build({"11": "Alice", "22": "Alice"})
        client.per_camera["Gate"] = [sighting(11, NOW - 300),
                                     sighting(11, NOW - 280),
                                     sighting(22, NOW - 60)]
        poll(coord)
        self.assertEqual(coord.people()["Alice"]["sightings"], 3)

    def test_last_seen_is_the_later_of_the_two(self):
        coord, client = build({"11": "Alice", "22": "Alice"})
        client.per_camera["Gate"] = [sighting(11, NOW - 300),
                                     sighting(22, NOW - 60)]
        poll(coord)
        person = coord.people()["Alice"]
        self.assertEqual(person["last_seen"], NOW - 60)
        self.assertEqual(person["first_seen"], NOW - 300)
        # ...and the cluster that actually saw them last, so the photograph
        # for that sighting can still be found.
        self.assertEqual(person["face_id"], "22")

    def test_different_names_stay_different_people(self):
        coord, client = build({"11": "Alice", "22": "Bob"})
        client.per_camera["Gate"] = [sighting(11, NOW - 300),
                                     sighting(22, NOW - 60)]
        poll(coord)
        self.assertEqual(sorted(coord.people()), ["Alice", "Bob"])

    def test_an_unnamed_cluster_is_not_a_person(self):
        """people() holds only names, which is the filter the arrival check
        used to apply by hand."""
        coord, client = build({"11": "Alice"})
        client.per_camera["Gate"] = [sighting(11, NOW - 300),
                                     sighting(999, NOW - 60)]
        poll(coord)
        self.assertEqual(sorted(coord.people()), ["Alice"])

    def test_the_split_trail_is_rejoined(self):
        """The reason this exists. Gate on one cluster and door on the other
        is a direction only once they are the same person -- each half holds
        one hop, and one hop is never a direction."""
        coord, client = build({"11": "Alice", "22": "Alice"},
                              ranks={"Gate": 0, "Door": 1})
        client.per_camera["Gate"] = [sighting(11, NOW - 90)]
        client.per_camera["Door"] = [sighting(22, NOW - 30)]
        poll(coord)
        self.assertIsNone(coord.faces_seen()["11"]["direction"])
        self.assertIsNone(coord.faces_seen()["22"]["direction"])
        self.assertEqual(coord.people()["Alice"]["direction"], "approaching")

    def test_a_circuit_split_across_clusters_is_still_a_circuit(self):
        """Front, side, front with the middle hop under a different cluster.
        Neither half contains the return that is the entire signal."""
        coord, client = build({"11": "Alice", "22": "Alice"})
        client.per_camera["Gate"] = [sighting(11, NOW - 300),
                                     sighting(11, NOW - 60)]
        client.per_camera["Door"] = [sighting(22, NOW - 180)]
        poll(coord)
        self.assertFalse(coord.faces_seen()["11"]["prowling"])
        self.assertTrue(coord.people()["Alice"]["prowling"])

    def test_every_camera_that_saw_them_is_listed_once(self):
        coord, client = build({"11": "Alice", "22": "Alice"})
        client.per_camera["Gate"] = [sighting(11, NOW - 300),
                                     sighting(22, NOW - 200)]
        client.per_camera["Door"] = [sighting(11, NOW - 60)]
        poll(coord)
        self.assertEqual(coord.people()["Alice"]["cameras"], ["Door", "Gate"])


class Grouping(unittest.TestCase):
    def test_names_are_grouped_without_needing_a_sighting(self):
        """Somebody away all day still has their entities."""
        coord, _ = build({"11": "Alice", "22": "Alice", "33": "Bob"})
        self.assertEqual(coord.named_people,
                         {"Alice": ["11", "22"], "Bob": ["33"]})

    def test_the_entity_is_keyed_on_the_lowest_id(self):
        """So somebody the hub only ever clustered once keeps exactly the
        unique id they already had, and nothing in the registry is orphaned.

        Checked on a person with two clusters, and against the id the entity
        is actually built from: with one cluster the lowest and the highest
        are the same id, so a wrong choice reads as correct.
        """
        coord, client = build({"22": "Alice", "11": "Alice", "33": "Bob"})
        client.per_camera["Gate"] = [sighting(22, NOW - 60), sighting(33, NOW - 90)]
        poll(coord)
        self.assertEqual(coord.people()["Alice"]["id"], "11")
        self.assertEqual(coord.people()["Bob"]["id"], "33")
        # The same id _sync_faces hands the entity, or the record and the
        # entity would disagree about which cluster represents this person.
        for name, person in coord.people().items():
            self.assertEqual(person["id"], coord.named_people[name][0])

    def test_an_id_resolves_to_its_whole_person(self):
        coord, client = build({"11": "Alice", "22": "Alice"})
        client.per_camera["Gate"] = [sighting(11, NOW - 300),
                                     sighting(22, NOW - 60)]
        poll(coord)
        self.assertEqual(coord.person_for("11"), coord.person_for("22"))
        self.assertEqual(coord.person_for("11")["sightings"], 2)

    def test_an_unnamed_id_resolves_to_just_that_face(self):
        coord, client = build({})
        client.per_camera["Gate"] = [sighting(999, NOW - 60)]
        poll(coord)
        self.assertEqual(coord.person_for("999")["sightings"], 1)

    def test_an_id_nobody_has_seen_resolves_to_nothing(self):
        coord, _ = build({})
        poll(coord)
        self.assertEqual(coord.person_for("404"), {})


class Arrivals(unittest.TestCase):
    def _arrivals(self, coord):
        return [data for name, data in coord.hass.bus.fired
                if name == const.EVENT_ARRIVAL]

    def test_one_arrival_for_a_person_the_hub_clustered_twice(self):
        """The bug. Both clusters firing reads as Alice arriving, leaving and
        arriving again in the same minute."""
        coord, client = build({"11": "Alice", "22": "Alice"})
        poll(coord)                       # priming poll, silent
        client.per_camera["Gate"] = [sighting(11, NOW - 90),
                                     sighting(22, NOW - 60)]
        poll(coord)
        self.assertEqual([data["name"] for data in self._arrivals(coord)],
                         ["Alice"])

    def test_two_different_people_still_both_arrive(self):
        coord, client = build({"11": "Alice", "22": "Bob"})
        poll(coord)
        client.per_camera["Gate"] = [sighting(11, NOW - 90),
                                     sighting(22, NOW - 60)]
        poll(coord)
        self.assertEqual(
            sorted(data["name"] for data in self._arrivals(coord)),
            ["Alice", "Bob"])

    def test_the_announcement_carries_every_cluster(self):
        """An automation matching on one id would miss half their sightings."""
        coord, client = build({"11": "Alice", "22": "Alice"})
        poll(coord)
        client.per_camera["Gate"] = [sighting(22, NOW - 60)]
        poll(coord)
        data = self._arrivals(coord)[0]
        self.assertEqual(data["face_ids"], ["11", "22"])
        self.assertEqual(data["face_id"], "22")


class Entities(unittest.TestCase):
    def test_the_sensors_read_the_whole_person(self):
        self.assertEqual(SENSOR.count("self.coordinator.person_for(self.face_id)"), 2)

    def test_seen_recently_reads_the_whole_person(self):
        """Seen on either cluster is seen."""
        self.assertIn("self.coordinator.person_for(self.face_id)", BINARY)

    def test_prowling_uses_merged_people_and_unnamed_faces(self):
        body = BINARY.split("def _circling", 1)[1].split("@property", 1)[0]
        self.assertIn("self.coordinator.people()", body)
        self.assertIn('if not face.get("name")', body)

    def test_prowling_does_not_report_one_person_twice(self):
        """Reading people() AND every face would list a named person under
        both their merged record and each of their clusters. The unnamed
        filter is what stops that, so it has to apply to the faces_seen half
        rather than sit anywhere in the method."""
        body = BINARY.split("def _circling", 1)[1].split("@property", 1)[0]
        unmerged = body.split("faces_seen().values()", 1)[1]
        self.assertIn('if not face.get("name")', unmerged)


if __name__ == "__main__":
    unittest.main()
