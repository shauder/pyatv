"""Unit tests for the AirPlay v2 session setup body.

Nothing in the test suite can reach airplayv2.py over the wire (the fake device
speaks AirPlay v1), so the body is verified directly instead.
"""

from uuid import UUID

from pyatv.protocols.raop.protocols import new_group_uuid
from pyatv.protocols.raop.protocols.airplayv2 import session_setup_body

GROUP_UUID = "6B2C2F4E-2F60-4D6F-8A18-9F0A8B3C2A11"


def test_ungrouped_session_does_not_contain_group_keys():
    body = session_setup_body(1234)

    # Sending these would change how a receiver treats an ordinary session
    assert "groupUUID" not in body
    assert body["senderSupportsRelay"] is False


def test_ungrouped_session_is_unchanged():
    body = session_setup_body(1234)

    assert body["timingPort"] == 1234
    assert body["timingProtocol"] == "NTP"
    assert body["isMultiSelectAirPlay"] is True
    assert body["groupContainsGroupLeader"] is False


def test_grouped_session_contains_group_keys():
    body = session_setup_body(1234, GROUP_UUID)

    assert body["groupUUID"] == GROUP_UUID

    # A receiver ignores groupUUID unless relay support is claimed
    assert body["senderSupportsRelay"] is True

    # We are a sender: no receiver can be told to follow us
    assert body["groupContainsGroupLeader"] is False


def test_group_members_share_group_but_not_session():
    first = session_setup_body(1234, GROUP_UUID)
    second = session_setup_body(1234, GROUP_UUID)

    assert first["groupUUID"] == second["groupUUID"]
    assert first["sessionUUID"] != second["sessionUUID"]


def test_group_uuid_is_random_and_uppercase():
    group_uuid = new_group_uuid()

    # A version 5 UUID would claim the receivers formed this group themselves
    assert UUID(group_uuid).version == 4
    assert group_uuid == group_uuid.upper()
    assert group_uuid != new_group_uuid()
