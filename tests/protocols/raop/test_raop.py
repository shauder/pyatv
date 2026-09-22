"""Unit tests for pyatv.protocols.raop."""

from ipaddress import ip_address
import logging
from types import SimpleNamespace

from deepdiff import DeepDiff
import pytest

from pyatv import exceptions
from pyatv.auth.hap_pairing import NO_CREDENTIALS, parse_credentials
from pyatv.conf import AppleTV, ManualService
from pyatv.const import DeviceModel, OperatingSystem, PairingRequirement, Protocol
from pyatv.core import MutableService, mdns
from pyatv.interface import DeviceInfo
from pyatv.protocols.raop import device_info, pair_partner, scan, service_info
from pyatv.protocols.raop.protocols import StreamContext, StreamMember
from pyatv.protocols.raop.stream_client import ControlClient
from pyatv.settings import RaopSettings
from pyatv.storage.memory_storage import MemoryStorage

from tests.fake_device.airplay import DEVICE_CREDENTIALS as CREDENTIALS

# Credentials of the other half: a device of its own, paired on its own
PARTNER_CREDENTIALS = "aabbccdd:00112233445566778899aabbccddeeff"

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


# Stereo pair partner: scanning fills it in on the service, a user can override it
# in the settings, and what each half is verified with is decided here because
# only here are the stored credentials of both halves available


def _raop_service(credentials=None, partner=None, partner_id=None) -> MutableService:
    service = MutableService("id", Protocol.RAOP, 7000, {}, credentials=credentials)
    service.stereo_pair_address = partner
    service.stereo_pair_identifier = partner_id
    return service


async def _storage(paired=None, credentials=None) -> MemoryStorage:
    """Storage as pairing a device leaves it: settings under its own identifier."""
    storage = MemoryStorage()
    if paired:
        config = AppleTV("10.0.0.20", "Other half")
        config.add_service(ManualService(paired, Protocol.RAOP, 7000, {}))
        settings = await storage.get_settings(config)
        settings.protocols.raop.credentials = credentials
    return storage


@pytest.mark.asyncio
async def test_partner_without_pair():
    assert pair_partner(_raop_service(), RaopSettings(), await _storage()) is None


@pytest.mark.asyncio
async def test_partner_from_scan():
    service = _raop_service(partner="10.0.0.20:7000", partner_id="partner_id")

    partner = pair_partner(service, RaopSettings(), await _storage())

    # No credentials are stored for this device, so nothing is bound to it and the
    # other half is driven with the same (lack of) credentials
    assert partner == ("10.0.0.20", 7000, NO_CREDENTIALS)


@pytest.mark.asyncio
async def test_partner_port_defaults_to_the_port_of_this_device():
    service = _raop_service(partner="10.0.0.20", partner_id="partner_id")

    partner = pair_partner(service, RaopSettings(), await _storage())

    assert partner == ("10.0.0.20", 7000, NO_CREDENTIALS)


@pytest.mark.asyncio
async def test_configured_partner_address_wins():
    # The setting is the escape hatch for anything scanning got wrong, so it wins
    # even when scanning found a partner of its own
    service = _raop_service(partner="10.0.0.20:7000", partner_id="partner_id")
    settings = RaopSettings(stereo_pair_address="10.0.0.30")

    partner = pair_partner(service, settings, await _storage())

    assert partner.address == "10.0.0.30"


@pytest.mark.asyncio
async def test_paired_partner_is_verified_with_its_own_credentials():
    # Both halves paired: each connection is verified with the pairing made with
    # that speaker, which is what makes a paired pair play as a pair
    service = _raop_service(
        credentials=CREDENTIALS, partner="10.0.0.20:7000", partner_id="partner_id"
    )
    storage = await _storage("partner_id", PARTNER_CREDENTIALS)

    partner = pair_partner(service, RaopSettings(), storage)

    assert partner == ("10.0.0.20", 7000, parse_credentials(PARTNER_CREDENTIALS))


@pytest.mark.asyncio
async def test_paired_partner_brings_its_own_to_an_unpaired_device():
    # Only the half that folding put away is paired. Which half that is was decided
    # by the lower identifier, not by the user, so this has to come out the same as
    # the other way around: the paired half is verified with its own pairing, and
    # this device with what it has (here: nothing)
    service = _raop_service(partner="10.0.0.20:7000", partner_id="partner_id")
    storage = await _storage("partner_id", PARTNER_CREDENTIALS)

    partner = pair_partner(service, RaopSettings(), storage)

    assert partner == ("10.0.0.20", 7000, parse_credentials(PARTNER_CREDENTIALS))


@pytest.mark.asyncio
async def test_unpaired_partner_leaves_this_device_streaming_alone(caplog):
    # Only this half is paired, so the other half has nothing to be verified with:
    # this device is streamed to on its own, as it was before a pair became one
    # configuration. The log is the only sign of it, so it is part of the feature:
    # it names the half to pair to get both speakers playing
    service = _raop_service(
        credentials=CREDENTIALS, partner="10.0.0.20:7000", partner_id="partner_id"
    )

    with caplog.at_level(logging.WARNING, logger="pyatv.protocols.raop"):
        assert pair_partner(service, RaopSettings(), await _storage()) is None

    assert len(caplog.records) == 1
    assert "partner_id" in caplog.text
    assert "Pair that half" in caplog.text


@pytest.mark.asyncio
async def test_partner_without_identifier_cannot_be_looked_up(caplog):
    # A partner from a configuration made before identifiers were carried, or one
    # whose half advertised none: there is nothing to look credentials up by
    service = _raop_service(credentials=CREDENTIALS, partner="10.0.0.20:7000")
    storage = await _storage("partner_id", PARTNER_CREDENTIALS)

    with caplog.at_level(logging.WARNING, logger="pyatv.protocols.raop"):
        assert pair_partner(service, RaopSettings(), storage) is None

    assert len(caplog.records) == 1


@pytest.mark.asyncio
async def test_configured_partner_address_is_not_the_partner_scanning_found():
    # The setting names a place and overrides the address scanning worked out, so
    # the identifier that came with that address no longer says which device answers
    # there: nothing is looked up for it, and the other half gets what this device
    # uses, as it did before any of them were paired
    service = _raop_service(partner="10.0.0.20:7000", partner_id="partner_id")
    settings = RaopSettings(stereo_pair_address="10.0.0.30")
    storage = await _storage("partner_id", PARTNER_CREDENTIALS)

    partner = pair_partner(service, settings, storage)

    assert partner == ("10.0.0.30", 7000, NO_CREDENTIALS)


@pytest.mark.asyncio
async def test_configured_partner_address_rejected_with_stored_credentials():
    # An address does not say which device answers at it, so the credentials of
    # the half named by hand cannot be found even when they are stored
    settings = RaopSettings(stereo_pair_address="10.0.0.20")
    storage = await _storage("partner_id", PARTNER_CREDENTIALS)

    with pytest.raises(exceptions.NotSupportedError):
        pair_partner(_raop_service(credentials=CREDENTIALS), settings, storage)
