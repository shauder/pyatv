"""Unit tests for pyatv.protocols.raop."""

from ipaddress import ip_address
import logging
from types import SimpleNamespace

from deepdiff import DeepDiff
import pytest

from pyatv import exceptions
from pyatv.const import DeviceModel, OperatingSystem, PairingRequirement, Protocol
from pyatv.core import MutableService, mdns
from pyatv.interface import DeviceInfo
from pyatv.protocols.raop import buddy_address, device_info, scan, service_info
from pyatv.protocols.raop.protocols import StreamContext, StreamMember
from pyatv.protocols.raop.stream_client import ControlClient
from pyatv.settings import RaopSettings

from tests.fake_device.airplay import DEVICE_CREDENTIALS as CREDENTIALS

RAOP_SERVICE = "_raop._tcp.local"
AIRPORT_SERVICE = "_airport._tcp.local"


def test_raop_scan_handlers_present():
    handlers = scan()
    assert len(handlers) == 2
    assert RAOP_SERVICE in handlers


def test_raop_handler_to_service():
    handler, _ = scan()[RAOP_SERVICE]

    mdns_service = mdns.Service(
        RAOP_SERVICE, "foo@bar", ip_address("127.0.0.1"), 1234, {"foo": "bar"}
    )
    mdns_response = mdns.Response([], False, None)

    name, service = handler(mdns_service, mdns_response)
    assert name == "bar"
    assert service.port == 1234
    assert service.credentials is None
    assert not DeepDiff(service.properties, {"foo": "bar"})


def test_raop_device_info_name():
    _, device_info_name = scan()[RAOP_SERVICE]
    assert device_info_name("ANY@Ohana") == "Ohana"


def test_airport_handler():
    handler, _ = scan()[AIRPORT_SERVICE]

    mdns_service = mdns.Service(
        RAOP_SERVICE, "foo@bar", ip_address("127.0.0.1"), 1234, {"foo": "bar"}
    )
    mdns_response = mdns.Response([], False, None)

    assert not handler(mdns_service, mdns_response)


def test_airport_info_name():
    _, device_info_name = scan()[AIRPORT_SERVICE]
    assert device_info_name("Ohana") == "Ohana"


@pytest.mark.parametrize(
    "service_type,properties,expected",
    [
        ("_dummy._tcp.local", {"am": "unknown"}, {DeviceInfo.RAW_MODEL: "unknown"}),
        (
            "_dummy._tcp.local",
            {"am": "AppleTV6,2"},
            {DeviceInfo.MODEL: DeviceModel.Gen4K, DeviceInfo.RAW_MODEL: "AppleTV6,2"},
        ),
        (
            "_dummy._tcp.local",
            {"am": "MacBookAir10,1"},
            {
                DeviceInfo.RAW_MODEL: "MacBookAir10,1",
                DeviceInfo.OPERATING_SYSTEM: OperatingSystem.MacOS,
            },
        ),
        ("_dummy._tcp.local", {"ov": "14.7"}, {DeviceInfo.VERSION: "14.7"}),
        # Special case for resolving MAC address and version on AirPort Express
        (
            "_dummy._tcp.local",
            {
                "wama": (
                    "AA-AA-AA-AA-AA-AA,"
                    "raMA=BB-BB-BB-BB-BB-BB,"
                    "raM2=CC-CC-CC-CC-CC-CC,"
                    "raNm=MyWifi,raCh=1,rCh2=2,"
                    "raSt=1,raNA=0,syFl=0x88C,"
                    "syAP=115,syVs=7.8.1,srcv=78100.3,bjSd=2"
                )
            },
            {
                DeviceInfo.MAC: "AA:AA:AA:AA:AA:AA",
                DeviceInfo.VERSION: "7.8.1",
            },
        ),
    ],
)
def test_device_info(service_type, properties, expected):
    assert not DeepDiff(device_info(service_type, properties), expected)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raop_props,mrp_props,requires_password",
    [
        ({}, {}, False),
        ({}, {"pw": "true"}, False),
        ({"pw": "true"}, {}, True),
        ({"pw": "TRUE"}, {}, True),
        ({"sf": "0x80"}, {}, True),
        ({}, {"sf": "0x80"}, False),
        ({"flags": "0x80"}, {}, True),
        ({}, {"flags": "0x80"}, False),
    ],
)
async def test_service_info_password(raop_props, mrp_props, requires_password):
    raop_service = MutableService("id", Protocol.RAOP, 0, raop_props)
    mrp_service = MutableService("mrp", Protocol.MRP, 0, mrp_props)

    assert not raop_service.requires_password
    assert not mrp_service.requires_password

    await service_info(
        raop_service,
        DeviceInfo({}),
        {Protocol.MRP: mrp_service, Protocol.RAOP: raop_service},
    )

    assert raop_service.requires_password == requires_password
    assert not mrp_service.requires_password


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raop_props,devinfo,pairing_req",
    [
        ({"sf": "0x0"}, {}, PairingRequirement.NotNeeded),
        ({"sf": "0x8"}, {}, PairingRequirement.Mandatory),
        ({"sf": "0x200"}, {}, PairingRequirement.Mandatory),
        ({"flags": "0x200"}, {}, PairingRequirement.Mandatory),
    ],
)
async def test_service_info_pairing(raop_props, devinfo, pairing_req):
    raop_service = MutableService("id", Protocol.RAOP, 0, raop_props)

    assert raop_service.pairing == PairingRequirement.Unsupported

    await service_info(
        raop_service,
        DeviceInfo(devinfo),
        {Protocol.RAOP: raop_service},
    )

    assert raop_service.pairing == pairing_req


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "props, pairing_req",
    [
        ({"acl": "1"}, PairingRequirement.Disabled),
        ({"act": "2"}, PairingRequirement.Unsupported),
    ],
)
async def test_service_info_pairing_acl(props, pairing_req):
    raop_service = MutableService("id", Protocol.RAOP, 0, {})
    airplay_props = MutableService("id", Protocol.AirPlay, 0, props)

    await service_info(
        raop_service,
        DeviceInfo({}),
        {Protocol.RAOP: raop_service, Protocol.AirPlay: airplay_props},
    )

    assert raop_service.pairing == pairing_req


def _member(remote_ip: str, control_port: int) -> StreamMember:
    """A member carrying only the two fields retransmit attribution looks at."""
    rtsp = SimpleNamespace(connection=SimpleNamespace(remote_ip=remote_ip))
    member = StreamMember(rtsp)
    member.control_port = control_port
    return member


@pytest.mark.parametrize(
    "addr, expected",
    [
        # Both halves picked the same ephemeral control port, which two identical
        # speakers booted together can do. The address is what tells them apart,
        # and a request must be served from the backlog of the receiver that sent
        # it: packets are encrypted per receiver, so the other half's copy cannot
        # be decrypted by the one asking for it and the gap is never repaired.
        (("10.0.0.21", 7011), 1),
        (("10.0.0.20", 7011), 0),
        # A receiver answering from a port other than the one it named is still
        # identified by its address.
        (("10.0.0.21", 54321), 1),
        (("10.0.0.99", 7011), None),
    ],
)
def test_retransmit_attributed_by_address_not_port(addr, expected):
    members = [_member("10.0.0.20", 7011), _member("10.0.0.21", 7011)]
    control = ControlClient(StreamContext(), members)

    found = control._find_member(addr)  # pylint: disable=protected-access

    assert found is (None if expected is None else members[expected])


def test_retransmit_attribution_tells_loopback_members_apart():
    """The fake devices all live on 127.0.0.1 and differ only by port."""
    members = [_member("127.0.0.1", 50001), _member("127.0.0.1", 50002)]
    control = ControlClient(StreamContext(), members)

    assert control._find_member(("127.0.0.1", 50002)) is members[1]
    assert control._find_member(("127.0.0.1", 50001)) is members[0]


# Stereo pair buddy: scanning fills it in on the service, a user can override it
# in the settings


def _raop_service(credentials=None, buddy=None) -> MutableService:
    service = MutableService("id", Protocol.RAOP, 7000, {}, credentials=credentials)
    service.pair_buddy_address = buddy
    return service


def test_buddy_address_without_pair():
    assert buddy_address(_raop_service(), RaopSettings()) is None


def test_buddy_address_from_scan():
    service = _raop_service(buddy="10.0.0.20:7000")

    assert buddy_address(service, RaopSettings()) == "10.0.0.20:7000"


def test_configured_buddy_address_wins():
    # The setting is the escape hatch for anything scanning got wrong, so it wins
    # even when scanning found a buddy of its own
    service = _raop_service(buddy="10.0.0.20:7000")
    settings = RaopSettings(pair_buddy_address="10.0.0.30")

    assert buddy_address(service, settings) == "10.0.0.30"


def test_configured_buddy_address_rejected_with_stored_credentials():
    settings = RaopSettings(pair_buddy_address="10.0.0.30")

    with pytest.raises(exceptions.NotSupportedError):
        buddy_address(_raop_service(credentials=CREDENTIALS), settings)


def test_discovered_buddy_address_dropped_with_stored_credentials(caplog):
    # Nobody asked for this buddy, so streaming to the device that was connected
    # to beats refusing to stream at all. It is warned about rather than silently
    # dropped: scanning has already folded that half away, so a pair has become
    # one speaker with nothing else to show for it
    service = _raop_service(credentials=CREDENTIALS, buddy="10.0.0.20:7000")

    with caplog.at_level(logging.WARNING, logger="pyatv.protocols.raop"):
        assert buddy_address(service, RaopSettings()) is None

    assert "10.0.0.20:7000" in caplog.text
