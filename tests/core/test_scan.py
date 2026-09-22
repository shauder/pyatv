"""Unit tests for scan module."""

import asyncio
from ipaddress import IPv4Address
from typing import Mapping, Optional
from unittest.mock import patch

import pytest
from zeroconf import (
    DNSAddress,
    DNSOutgoing,
    DNSPointer,
    DNSService,
    DNSText,
    ServiceListener,
    Zeroconf,
    const,
)
from zeroconf.asyncio import AsyncServiceBrowser, AsyncZeroconf

from pyatv import scan
from pyatv.conf import AppleTV
from pyatv.const import DeviceModel, Protocol
from pyatv.core.mdns import Response, Service
from pyatv.core.scan import MulticastMdnsScanner, get_unique_identifiers

TEST_SERVICE1 = Service("_service1._tcp.local", "service1", None, 0, {"a": "b"})
TEST_SERVICE2 = Service("_service2._tcp.local", "service2", None, 0, {"c": "d"})

ALL_MDNS_SERVICES = [
    "_mediaremotetv._tcp.local.",
    "_companion-link._tcp.local.",
    "_airport._tcp.local.",
    "_device_info._tcp.local.",
    "_sleep-proxy._udp.local.",
    "_touch-able._tcp.local.",
    "_appletv-v2._tcp.local.",
    "_hscp._tcp.local.",
    "_airplay._tcp.local.",
    "_raop._tcp.local.",
]

DEVICE_A_RECORD = DNSAddress(
    "Ohana.local.",
    const._TYPE_A,
    const._CLASS_IN,
    const._DNS_HOST_TTL,
    b"\xc0\xa8k\xb8",
)
SLEEP_PROXY_PTR_RECORD = DNSPointer(
    "_sleep-proxy._udp.local.",
    const._TYPE_PTR,
    const._CLASS_IN,
    const._DNS_OTHER_TTL,
    "70-35-60-63.1 Ohana._sleep-proxy._udp.local.",
)
SLEEP_PROXY_SRV_RECORD = DNSService(
    "70-35-60-63.1 Ohana._sleep-proxy._udp.local.",
    const._TYPE_SRV,
    const._CLASS_IN,
    const._DNS_HOST_TTL,
    0,
    0,
    54942,
    "Ohana.local.",
)
SLEEP_PROXY_TXT_RECORD = DNSText(
    "70-35-60-63.1 Ohana._sleep-proxy._udp.local.",
    const._TYPE_TXT,
    const._CLASS_IN,
    const._DNS_OTHER_TTL,
    b"",
)
AIRPLAY_PTR_RECORD = DNSPointer(
    "_airplay._tcp.local.",
    const._TYPE_PTR,
    const._CLASS_IN,
    const._DNS_OTHER_TTL,
    "Ohana._airplay._tcp.local.",
)
AIRPLAY_SRV_RECORD = DNSService(
    "Ohana._airplay._tcp.local.",
    const._TYPE_SRV,
    const._CLASS_IN,
    const._DNS_HOST_TTL,
    0,
    0,
    7000,
    "Ohana.local.",
)
AIRPLAY_TXT_RECORD = DNSText(
    "Ohana._airplay._tcp.local.",
    const._TYPE_TXT,
    const._CLASS_IN,
    const._DNS_OTHER_TTL,
    b"",
)
RAOP_PTR_RECORD = DNSPointer(
    "_raop._tcp.local.",
    const._TYPE_PTR,
    const._CLASS_IN,
    const._DNS_OTHER_TTL,
    "54E61BF2ED74@Ohana._raop._tcp.local.",
)
RAOP_SERVICE_RECORD = DNSService(
    "54E61BF2ED74@Ohana._raop._tcp.local.",
    const._TYPE_SRV,
    const._CLASS_IN,
    const._DNS_HOST_TTL,
    0,
    0,
    7000,
    "Ohana.local.",
)
RAOP_TXT_RECORD = DNSText(
    "Ohana._airplay._tcp.local.",
    const._TYPE_TXT,
    const._CLASS_IN,
    const._DNS_OTHER_TTL,
    b"",
)
COMPANION_LINK_PTR_RECORD = DNSPointer(
    "_companion-link._tcp.local.",
    const._TYPE_PTR,
    const._CLASS_IN,
    const._DNS_OTHER_TTL,
    "Ohana._companion-link._tcp.local.",
)
COMPANION_LINK_SRV_RECORD = DNSService(
    "Ohana._companion-link._tcp.local.",
    const._TYPE_SRV,
    const._CLASS_IN,
    const._DNS_HOST_TTL,
    0,
    0,
    49152,
    "Ohana.local.",
)
COMPANION_LINK_TXT_RECORD = DNSText(
    "Ohana._companion-link._tcp.local.",
    const._TYPE_TXT,
    const._CLASS_IN,
    const._DNS_OTHER_TTL,
    b"",
)
DEVICE_INFO_TEXT_RECORD = DNSText(
    "Ohana._device-info._tcp.local.",
    const._TYPE_TXT,
    const._CLASS_IN,
    const._DNS_OTHER_TTL,
    b"\x0cmodel=J305AP",
)
SLEEP_PROXY_RECORDS = [
    SLEEP_PROXY_PTR_RECORD,
    SLEEP_PROXY_SRV_RECORD,
    SLEEP_PROXY_TXT_RECORD,
]
AIRPLAY_RECORDS = [
    AIRPLAY_PTR_RECORD,
    AIRPLAY_SRV_RECORD,
    AIRPLAY_TXT_RECORD,
]
RAOP_RECORDS = [
    RAOP_PTR_RECORD,
    RAOP_SERVICE_RECORD,
    RAOP_TXT_RECORD,
]
COMPANION_LINK_RECORDS = [
    COMPANION_LINK_PTR_RECORD,
    COMPANION_LINK_SRV_RECORD,
    COMPANION_LINK_TXT_RECORD,
]

PTR_RECORDS_ONLY = [
    SLEEP_PROXY_PTR_RECORD,
    AIRPLAY_PTR_RECORD,
    RAOP_PTR_RECORD,
    COMPANION_LINK_PTR_RECORD,
]

COMPLETE_RECORD_SET_WITH_DEVICE_INFO = [
    DEVICE_A_RECORD,
    *SLEEP_PROXY_RECORDS,
    *AIRPLAY_RECORDS,
    *RAOP_RECORDS,
    *COMPANION_LINK_RECORDS,
    DEVICE_INFO_TEXT_RECORD,
]
COMPLETE_RECORD_SET = [
    DEVICE_A_RECORD,
    *SLEEP_PROXY_RECORDS,
    *AIRPLAY_RECORDS,
    *RAOP_RECORDS,
    *COMPANION_LINK_RECORDS,
]
RECORD_SET_WITH_DEVICE_INFO_MISSING_COMPANION_LINK = [
    DEVICE_A_RECORD,
    *SLEEP_PROXY_RECORDS,
    *AIRPLAY_RECORDS,
    *RAOP_RECORDS,
    DEVICE_INFO_TEXT_RECORD,
]
PARTIAL_RECORD_SET = [
    DEVICE_A_RECORD,
    *SLEEP_PROXY_RECORDS,
    *AIRPLAY_RECORDS,
]


from typing import List, Tuple

from zeroconf import DNSRecord


async def _create_zc_with_cache(
    records: List[DNSRecord],
) -> Tuple[AsyncZeroconf, AsyncServiceBrowser]:
    aiozc = AsyncZeroconf(interfaces=["127.0.0.1"])
    browser = AsyncServiceBrowser(
        aiozc.zeroconf,
        ALL_MDNS_SERVICES,
        None,
        DummyListener(),
    )
    aiozc.zeroconf.cache.async_add_records(records)
    await aiozc.zeroconf.async_wait_for_start()
    return aiozc, browser


@pytest.fixture
def response():
    yield Response([], False, None)


class DummyListener(ServiceListener):
    def add_service(self, zeroconf: Zeroconf, type: str, name: str) -> None:
        pass

    def remove_service(self, zeroconf: Zeroconf, type: str, name: str) -> None:
        pass

    def update_service(self, zeroconf: Zeroconf, type: str, name: str) -> None:
        pass


def test_unique_identifier_empty(response):
    assert len(list(get_unique_identifiers(response))) == 0


@patch("pyatv.core.scan.get_unique_id")
def test_unique_identifiers(unique_id_mock, response):
    response.services.append(TEST_SERVICE1)
    response.services.append(TEST_SERVICE2)

    unique_id_mock.side_effect = ["id1", "id2"]

    identifiers = get_unique_identifiers(response)

    assert "id1" == next(identifiers)
    unique_id_mock.assert_called_with("_service1._tcp.local", "service1", {"a": "b"})
    assert "id2" == next(identifiers)
    unique_id_mock.assert_called_with("_service2._tcp.local", "service2", {"c": "d"})
    assert not next(identifiers, None)


@pytest.mark.asyncio
async def test_scan_with_zeroconf_complete_and_device_info():
    aiozc, browser = await _create_zc_with_cache(COMPLETE_RECORD_SET_WITH_DEVICE_INFO)
    results = await scan(asyncio.get_event_loop(), timeout=0, aiozc=aiozc)
    atv: AppleTV = results[0]
    assert isinstance(atv, AppleTV)
    assert "_sleep-proxy._udp.local" in atv.properties
    assert "_airplay._tcp.local" in atv.properties
    assert "_raop._tcp.local" in atv.properties
    assert "_companion-link._tcp.local" in atv.properties
    assert atv.deep_sleep is False
    assert atv.device_info.model == DeviceModel.AppleTV4KGen2
    await browser.async_cancel()
    await aiozc.async_close()


@pytest.mark.asyncio
async def test_scan_with_zeroconf_complete_and_device_info_specific_host_matching():
    aiozc, browser = await _create_zc_with_cache(COMPLETE_RECORD_SET_WITH_DEVICE_INFO)
    results = await scan(
        asyncio.get_event_loop(),
        hosts=["192.168.107.184"],
        timeout=0,
        aiozc=aiozc,
    )
    atv: AppleTV = results[0]
    assert isinstance(atv, AppleTV)
    assert "_sleep-proxy._udp.local" in atv.properties
    assert "_airplay._tcp.local" in atv.properties
    assert "_raop._tcp.local" in atv.properties
    assert "_companion-link._tcp.local" in atv.properties
    assert atv.deep_sleep is False
    assert atv.device_info.model == DeviceModel.AppleTV4KGen2
    await browser.async_cancel()
    await aiozc.async_close()


@pytest.mark.asyncio
async def test_scan_with_zeroconf_complete_and_device_info_specific_host_not_matching():
    aiozc, browser = await _create_zc_with_cache(COMPLETE_RECORD_SET_WITH_DEVICE_INFO)
    results = await scan(
        asyncio.get_event_loop(), hosts=["192.168.1.1"], timeout=0, aiozc=aiozc
    )
    assert len(results) == 0
    await browser.async_cancel()
    await aiozc.async_close()


@pytest.mark.asyncio
async def test_scan_with_zeroconf_complete():
    aiozc, browser = await _create_zc_with_cache(COMPLETE_RECORD_SET)
    results = await scan(asyncio.get_event_loop(), timeout=0, aiozc=aiozc)
    atv: AppleTV = results[0]
    assert isinstance(atv, AppleTV)
    assert "_sleep-proxy._udp.local" in atv.properties
    assert "_airplay._tcp.local" in atv.properties
    assert "_raop._tcp.local" in atv.properties
    assert "_companion-link._tcp.local" in atv.properties
    assert atv.deep_sleep is False
    assert atv.device_info.model == DeviceModel.Unknown
    await browser.async_cancel()
    await aiozc.async_close()


@pytest.mark.asyncio
async def test_scan_with_zeroconf_partial():
    aiozc, browser = await _create_zc_with_cache(PARTIAL_RECORD_SET)
    results = await scan(asyncio.get_event_loop(), timeout=0, aiozc=aiozc)
    assert len(results) == 0
    await browser.async_cancel()
    await aiozc.async_close()


@pytest.mark.asyncio
async def test_scan_with_zeroconf_missing_companion_link_only():
    aiozc, browser = await _create_zc_with_cache(
        RECORD_SET_WITH_DEVICE_INFO_MISSING_COMPANION_LINK
    )
    results = await scan(asyncio.get_event_loop(), timeout=0, aiozc=aiozc)
    atv: AppleTV = results[0]
    assert isinstance(atv, AppleTV)
    assert "_sleep-proxy._udp.local" in atv.properties
    assert "_airplay._tcp.local" in atv.properties
    assert "_raop._tcp.local" in atv.properties
    assert "_companion-link._tcp.local" not in atv.properties
    assert atv.deep_sleep is False
    assert atv.device_info.model == DeviceModel.AppleTV4KGen2
    await browser.async_cancel()
    await aiozc.async_close()


@pytest.mark.asyncio
async def test_scan_with_zeroconf_multicast_not_found():
    aiozc, browser = await _create_zc_with_cache(PTR_RECORDS_ONLY)
    loop = asyncio.get_event_loop()
    with patch("pyatv.core.scan.AsyncServiceInfo.async_request") as mock_async_request:
        results = await scan(loop, timeout=0, aiozc=aiozc)
    assert mock_async_request.mock_calls
    for call in mock_async_request.mock_calls:
        # Not called with host argument
        assert len(call[1]) == 2
    assert not results
    await browser.async_cancel()
    await aiozc.async_close()


@pytest.mark.asyncio
async def test_scan_with_zeroconf_unicast_not_found():
    aiozc, browser = await _create_zc_with_cache(PTR_RECORDS_ONLY)
    loop = asyncio.get_event_loop()
    with (
        patch("pyatv.core.scan.AsyncServiceInfo.async_request") as mock_async_request,
        patch("zeroconf.Zeroconf.async_send") as mock_async_send,
    ):
        results = await scan(loop, timeout=0, aiozc=aiozc, hosts=["127.0.0.1"])
    assert mock_async_request.mock_calls
    for call in mock_async_request.mock_calls:
        # Called with host argument
        assert call[1][2] == "127.0.0.1"
    calls = mock_async_send.mock_calls
    assert len(calls) >= 1
    # We should send a PTR query as a fallback to unicast
    # which has a target of 127.0.0.1
    last_call = calls[-1][1]
    target = last_call[1]
    assert target == "127.0.0.1"
    dns_outgoing: DNSOutgoing = last_call[0]
    assert len(dns_outgoing.questions) == 1
    question = dns_outgoing.questions[0]
    assert question.name == "_device-info._tcp.local."
    assert question.unicast is True
    assert not results
    await browser.async_cancel()
    await aiozc.async_close()


# A stereo pair is two addresses advertising the same tight-sync id ("tsid"). The
# halves below are named after the way a pair is usually set up, but a name never
# says anything about a pair: only tsid does.

PAIR_TSID = "0D4B3C2A-1F5E-5B8A-9C7D-6E2F4A8B1C30"
OTHER_TSID = "7A1E9C4B-2D6F-5A3E-8B0C-1F5D7E9A2B44"

LEFT_ID = "AA:BB:CC:DD:EE:01"
RIGHT_ID = "AA:BB:CC:DD:EE:02"
LEFT_ADDRESS = "192.168.107.10"
RIGHT_ADDRESS = "192.168.107.11"
LEFT_PORT = 7000
RIGHT_PORT = 7001


def _txt_record(properties: Mapping[str, str]) -> bytes:
    """Encode properties the way a TXT record carries them."""
    return b"".join(
        bytes([len(entry)]) + entry
        for entry in (f"{k}={v}".encode("utf-8") for k, v in properties.items())
    )


def _speaker_records(
    name: str,
    identifier: str,
    address: str,
    port: int,
    tsid: Optional[str] = None,
    group: Optional[Mapping[str, str]] = None,
):
    """Return the records one AirPlay speaker announces."""
    airplay_name = f"{name}._airplay._tcp.local."
    raop_name = f"{identifier}@{name}._raop._tcp.local."
    properties = {"deviceid": identifier, **(group or {})}
    if tsid:
        properties["tsid"] = tsid

    return [
        DNSAddress(
            f"{name}.local.",
            const._TYPE_A,
            const._CLASS_IN,
            const._DNS_HOST_TTL,
            IPv4Address(address).packed,
        ),
        DNSPointer(
            "_airplay._tcp.local.",
            const._TYPE_PTR,
            const._CLASS_IN,
            const._DNS_OTHER_TTL,
            airplay_name,
        ),
        DNSService(
            airplay_name,
            const._TYPE_SRV,
            const._CLASS_IN,
            const._DNS_HOST_TTL,
            0,
            0,
            port,
            f"{name}.local.",
        ),
        DNSText(
            airplay_name,
            const._TYPE_TXT,
            const._CLASS_IN,
            const._DNS_OTHER_TTL,
            _txt_record(properties),
        ),
        DNSPointer(
            "_raop._tcp.local.",
            const._TYPE_PTR,
            const._CLASS_IN,
            const._DNS_OTHER_TTL,
            raop_name,
        ),
        DNSService(
            raop_name,
            const._TYPE_SRV,
            const._CLASS_IN,
            const._DNS_HOST_TTL,
            0,
            0,
            port,
            f"{name}.local.",
        ),
        DNSText(
            raop_name,
            const._TYPE_TXT,
            const._CLASS_IN,
            const._DNS_OTHER_TTL,
            _txt_record({"am": "AudioAccessory5,1"}),
        ),
    ]


def _left(tsid: Optional[str] = PAIR_TSID):
    return _speaker_records("OfficeLeft", LEFT_ID, LEFT_ADDRESS, LEFT_PORT, tsid)


def _right(tsid: Optional[str] = PAIR_TSID):
    return _speaker_records("OfficeRight", RIGHT_ID, RIGHT_ADDRESS, RIGHT_PORT, tsid)


async def _scan_records(records, **kwargs):
    aiozc, browser = await _create_zc_with_cache(records)
    try:
        return await scan(asyncio.get_event_loop(), timeout=0, aiozc=aiozc, **kwargs)
    finally:
        await browser.async_cancel()
        await aiozc.async_close()


@pytest.mark.asyncio
async def test_scan_folds_stereo_pair_into_one_config():
    results = await _scan_records([*_left(), *_right()])

    assert len(results) == 1

    # Folded into an identifier that already exists (and the one that does not
    # depend on which half is currently leading), so nothing a user typed or that
    # storage keyed credentials by changes meaning
    atv = results[0]
    assert atv.identifier == LEFT_ID
    assert atv.address == IPv4Address(LEFT_ADDRESS)

    # ...carrying the other half, so streaming drives both
    assert (
        atv.get_service(Protocol.RAOP).pair_buddy_address
        == f"{RIGHT_ADDRESS}:{RIGHT_PORT}"
    )


@pytest.mark.asyncio
async def test_scan_does_not_fold_half_without_partner():
    results = await _scan_records(_left())

    assert len(results) == 1
    assert results[0].identifier == LEFT_ID
    assert results[0].get_service(Protocol.RAOP).pair_buddy_address is None


@pytest.mark.asyncio
async def test_scan_does_not_fold_different_tight_sync_ids():
    results = await _scan_records([*_left(), *_right(tsid=OTHER_TSID)])

    assert len(results) == 2
    assert all(
        atv.get_service(Protocol.RAOP).pair_buddy_address is None for atv in results
    )


@pytest.mark.asyncio
async def test_scan_does_not_fold_speakers_without_tight_sync_id():
    results = await _scan_records([*_left(tsid=None), *_right(tsid=None)])

    assert len(results) == 2
    assert all(
        atv.get_service(Protocol.RAOP).pair_buddy_address is None for atv in results
    )


@pytest.mark.asyncio
async def test_scan_folds_pair_that_is_currently_split():
    # A pair that is mid-session, or that a sender left split, shows both halves
    # leading a group of their own (igl=1) with a gid of their own derived from
    # the tsid. It is still one device and must still fold, which is why nothing
    # but tsid is looked at.
    results = await _scan_records(
        [
            *_speaker_records(
                "OfficeLeft",
                LEFT_ID,
                LEFT_ADDRESS,
                LEFT_PORT,
                PAIR_TSID,
                group={"igl": "1", "gcgl": "1", "gid": f"{PAIR_TSID}+0"},
            ),
            *_speaker_records(
                "OfficeRight",
                RIGHT_ID,
                RIGHT_ADDRESS,
                RIGHT_PORT,
                PAIR_TSID,
                group={"igl": "1", "gcgl": "1", "gid": f"{PAIR_TSID}+1"},
            ),
        ]
    )

    assert len(results) == 1
    assert (
        results[0].get_service(Protocol.RAOP).pair_buddy_address
        == f"{RIGHT_ADDRESS}:{RIGHT_PORT}"
    )


@pytest.mark.asyncio
async def test_scan_for_one_half_returns_it_alone():
    # Asking for a half by identifier must keep returning that half: it is what
    # its own credentials are stored against
    results = await _scan_records([*_left(), *_right()], identifier=RIGHT_ID)

    assert len(results) == 1
    assert results[0].identifier == RIGHT_ID
    assert results[0].get_service(Protocol.RAOP).pair_buddy_address is None


@pytest.mark.asyncio
async def test_scan_for_pair_identifier_returns_the_pair():
    # The identifier a pair is folded into is the only one scanning prints for it,
    # so it is the one a user copies and passes back in (atvremote --id). It has to
    # mean the pair, not the half it came from
    results = await _scan_records([*_left(), *_right()], identifier=LEFT_ID)

    assert len(results) == 1
    assert results[0].identifier == LEFT_ID
    assert (
        results[0].get_service(Protocol.RAOP).pair_buddy_address
        == f"{RIGHT_ADDRESS}:{RIGHT_PORT}"
    )


@pytest.mark.asyncio
async def test_scan_does_not_fold_one_speaker_seen_at_two_addresses():
    # A stale A record lives beside the new one for the rest of its TTL after DHCP
    # moves a receiver, so for a while one speaker is two addresses with one
    # identifier and one tsid. That is one device twice, not a pair: folding it
    # would point a stream at two sessions on the same receiver, one of them
    # through an address that may already be dead.
    records = _left()
    records.append(
        DNSAddress(
            "OfficeLeft.local.",
            const._TYPE_A,
            const._CLASS_IN,
            const._DNS_HOST_TTL,
            IPv4Address("192.168.107.55").packed,
        )
    )
    results = await _scan_records(records)

    assert len(results) == 2
    assert all(atv.identifier == LEFT_ID for atv in results)
    assert all(
        atv.get_service(Protocol.RAOP).pair_buddy_address is None for atv in results
    )


def _multicast_response(identifier, tsid=None):
    properties = {"deviceid": identifier}
    if tsid:
        properties["tsid"] = tsid
    return Response(
        services=[
            Service(
                "_airplay._tcp.local",
                "OfficeLeft",
                IPv4Address(LEFT_ADDRESS),
                LEFT_PORT,
                properties,
            )
        ],
        deep_sleep=False,
        model=None,
    )


@patch("pyatv.core.scan.get_unique_id", side_effect=lambda t, n, p: p.get("deviceid"))
def test_multicast_scan_ends_early_on_a_plain_device(unique_id_mock):
    scanner = MulticastMdnsScanner(None, LEFT_ID)  # loop is unused here

    assert scanner._end_if_identifier_found(_multicast_response(LEFT_ID))
    assert not scanner._end_if_identifier_found(_multicast_response(RIGHT_ID))


@patch("pyatv.core.scan.get_unique_id", side_effect=lambda t, n, p: p.get("deviceid"))
def test_multicast_scan_waits_out_a_stereo_pair(unique_id_mock):
    # Ending the scan throws away every response but the matching one, so a half
    # of a pair must not end it: the other half answers under its own identifier
    # and there would be nothing left to fold the pair together from
    scanner = MulticastMdnsScanner(None, LEFT_ID)  # loop is unused here

    assert not scanner._end_if_identifier_found(
        _multicast_response(LEFT_ID, tsid=PAIR_TSID)
    )
