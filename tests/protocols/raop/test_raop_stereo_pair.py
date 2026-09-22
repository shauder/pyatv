"""Functional tests for streaming to both halves of a stereo pair.

Actual synchronization between two receivers cannot be verified here (sleeping
is stubbed in the test suite), so what is verified is what synchronization is
built on: both receivers get the same packets, from the same timeline, with the
same identity, and both are torn down afterwards.
"""

import pytest
import pytest_asyncio

from pyatv.auth.hap_pairing import parse_credentials
from pyatv.conf import AppleTV, ManualService
from pyatv.const import Protocol
from pyatv.core import MutableService, create_core
from pyatv.protocols.airplay.utils import pct_to_dbfs
from pyatv.protocols.raop import RaopPlaybackManager
from pyatv.storage.memory_storage import MemoryStorage

from tests.fake_device.airplay import DEVICE_CREDENTIALS as CREDENTIALS
from tests.protocols.raop.test_raop import BUDDY_CREDENTIALS
from tests.protocols.raop.test_raop_functional import audio_matches
from tests.utils import data_path, until

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("raop_properties", [{"et": "0"}])
async def test_stream_file_to_both_halves(raop_pair_client, raop_state, raop_state2):
    await raop_pair_client.stream.stream_file(data_path("audio_10_frames.wav"))

    assert await audio_matches(raop_state.raw_audio, frames=10)
    assert await audio_matches(raop_state2.raw_audio, frames=10)


@pytest.mark.parametrize("raop_properties", [{"et": "0"}])
async def test_stream_file_to_pair_found_by_scanning(
    raop_discovered_pair_client, raop_state, raop_state2
):
    # Nothing is configured here: the pair came out of a scan as one config
    await raop_discovered_pair_client.stream.stream_file(
        data_path("audio_10_frames.wav")
    )

    assert await audio_matches(raop_state.raw_audio, frames=10)
    assert await audio_matches(raop_state2.raw_audio, frames=10)


@pytest.mark.parametrize("raop_properties", [{"et": "0"}])
async def test_both_halves_share_one_timeline(
    raop_pair_client, raop_state, raop_state2
):
    await raop_pair_client.stream.stream_file(data_path("audio_3_packets.wav"))

    await until(
        lambda: raop_state.audio_packets.keys() == raop_state2.audio_packets.keys()
    )

    # Same sequence numbers from the same anchor is what "one timeline" means on
    # the wire: the packet with a given sequence number carries the same audio,
    # at the same point in time, to both halves
    assert raop_state.audio_packets
    assert raop_state.audio_packets.keys() == raop_state2.audio_packets.keys()
    assert raop_state.initial_audio_packet == raop_state2.initial_audio_packet
    assert raop_state.raw_audio == raop_state2.raw_audio


@pytest.mark.parametrize("raop_properties", [{"et": "0"}])
async def test_both_halves_share_one_ssrc(raop_pair_client, raop_state, raop_state2):
    await raop_pair_client.stream.stream_file(data_path("audio_10_frames.wav"))

    await until(lambda: raop_state2.audio_ssrc is not None)

    assert raop_state.audio_ssrc == raop_state2.audio_ssrc

    # ...and it is the stream identity of the device being streamed to
    assert raop_state.audio_ssrc == raop_state.rtsp_session_id


@pytest.mark.parametrize("raop_properties", [{"et": "0"}])
async def test_single_receiver_ssrc_is_unchanged(raop_client, raop_state):
    await raop_client.stream.stream_file(data_path("audio_10_frames.wav"))

    await until(lambda: raop_state.audio_ssrc is not None)

    assert raop_state.audio_ssrc == raop_state.rtsp_session_id


@pytest.mark.parametrize("raop_properties", [{"et": "0"}])
async def test_both_halves_receive_sync_packets(
    raop_pair_client, raop_state, raop_state2
):
    await raop_pair_client.stream.stream_file(data_path("audio_3_packets.wav"))

    # One control client serves both halves, so both are told about the very same
    # timeline
    await until(lambda: raop_state.sync_packets_received > 0)
    await until(lambda: raop_state2.sync_packets_received > 0)


@pytest.mark.parametrize("raop_properties", [{"et": "0"}])
async def test_both_halves_torn_down(raop_pair_client, raop_state, raop_state2):
    await raop_pair_client.stream.stream_file(data_path("audio_10_frames.wav"))

    assert raop_state.teardown_called
    assert raop_state2.teardown_called


@pytest.mark.parametrize("raop_properties", [{"et": "0"}])
async def test_failing_teardown_does_not_skip_other_half(
    raop_pair_client, raop_usecase, raop_state, raop_state2
):
    raop_usecase.teardown_fails(True)

    await raop_pair_client.stream.stream_file(data_path("audio_10_frames.wav"))

    # A session that is never torn down leaves a speaker stuck, so one half
    # failing must not stop the other half from being torn down
    assert raop_state.teardown_called
    assert raop_state2.teardown_called


@pytest.mark.parametrize("raop_properties", [{"et": "0"}])
async def test_buddy_setup_failure_tears_down_the_primary(
    raop_pair_client, raop_usecase2, raop_state
):
    # The buddy demands a password the session does not have (credentials and
    # password live on the shared context, so only the primary's are known).
    # Whatever the cause - busy, rebooting, rejected pairing - a buddy that fails
    # AFTER the primary has fully set up must not leave the primary holding a
    # session: nothing downstream sends that TEARDOWN, and a receiver left like
    # that stays out of service until it is restarted.
    raop_usecase2.password("not-the-one-we-have")

    with pytest.raises(Exception):
        await raop_pair_client.stream.stream_file(data_path("audio_10_frames.wav"))

    await until(lambda: raop_state.teardown_called)


# A pair whose halves have been PIN-paired cannot be streamed to by these fakes
# (they answer no pair-verify), so what is checked for those is the session that
# is set up: which receivers it drives and what each one is verified with.


def _peer_port(protocol) -> int:
    """Port of the receiver a connection was actually opened to."""
    connection = protocol.rtsp.connection
    return connection.transport.get_extra_info("socket").getpeername()[1]


@pytest_asyncio.fixture(name="pair_session")
async def pair_session_fixture(raop_device, raop_device2, raop_properties):
    """Set up a session to a pair, as connecting to a scanned pair would."""
    sessions = []

    async def _setup(credentials=None, buddy_credentials=None):
        buddy_port = raop_device2.get_port(Protocol.RAOP)
        storage = MemoryStorage()

        if buddy_credentials:
            # Storage as pairing the other half leaves it: its own settings,
            # under its own identifier
            buddy_config = AppleTV("127.0.0.1", "Other half")
            buddy_config.add_service(
                ManualService("buddy_id", Protocol.RAOP, buddy_port, {})
            )
            buddy_settings = await storage.get_settings(buddy_config)
            buddy_settings.protocols.raop.credentials = buddy_credentials

        # The configuration scanning produces for a pair: one config carrying the
        # other half's address and identifier
        service = MutableService(
            "raop_id",
            Protocol.RAOP,
            raop_device.get_port(Protocol.RAOP),
            raop_properties,
            credentials=credentials,
        )
        service.pair_buddy_address = f"127.0.0.1:{buddy_port}"
        service.pair_buddy_identifier = "buddy_id"
        config = AppleTV("127.0.0.1", "Stereo Pair")
        config.add_service(service)

        core = await create_core(
            config,
            service,
            settings=await storage.get_settings(config),
            storage=storage,
        )
        manager = RaopPlaybackManager(core)
        sessions.append((manager, core))

        client, _ = await manager.setup(service)
        return client

    yield _setup

    for manager, core in sessions:
        await manager.teardown()
        await core.session_manager.close()


@pytest.mark.parametrize("raop_properties", [{"et": "0"}])
async def test_paired_pair_drives_both_halves_with_own_credentials(
    pair_session, raop_device2
):
    client = await pair_session(
        credentials=CREDENTIALS, buddy_credentials=BUDDY_CREDENTIALS
    )

    protocols = client._protocols  # pylint: disable=protected-access

    assert len(protocols) == 2
    assert _peer_port(protocols[1]) == raop_device2.get_port(Protocol.RAOP)

    # Credentials belong to a receiver, not to the session: each half is verified
    # with the pairing made with that speaker
    assert protocols[0].credentials == parse_credentials(CREDENTIALS)
    assert protocols[1].credentials == parse_credentials(BUDDY_CREDENTIALS)


@pytest.mark.parametrize("raop_properties", [{"et": "0"}])
async def test_paired_half_alone_streams_without_the_other_half(
    pair_session, raop_device
):
    # Nothing is stored for the other half, so it has nothing to be verified with.
    # This device is streamed to on its own, as it was before a pair was folded
    # into one configuration, rather than not at all
    client = await pair_session(credentials=CREDENTIALS)

    protocols = client._protocols  # pylint: disable=protected-access

    assert len(protocols) == 1
    assert _peer_port(protocols[0]) == raop_device.get_port(Protocol.RAOP)
    assert protocols[0].credentials == parse_credentials(CREDENTIALS)


@pytest.mark.parametrize("raop_properties", [{"et": "0"}])
async def test_paired_other_half_is_driven_by_an_unpaired_device(
    pair_session, raop_device2
):
    # The other way around: only the half folded away is paired. Which half folding
    # kept is decided by the lower identifier, so a user who paired one speaker lands
    # in either orientation - and both halves play in both of them
    client = await pair_session(buddy_credentials=BUDDY_CREDENTIALS)

    protocols = client._protocols  # pylint: disable=protected-access

    assert len(protocols) == 2
    assert _peer_port(protocols[1]) == raop_device2.get_port(Protocol.RAOP)
    assert protocols[1].credentials == parse_credentials(BUDDY_CREDENTIALS)


@pytest.mark.parametrize("raop_properties", [{"et": "0"}])
async def test_unpaired_pair_drives_both_halves_with_the_session_credentials(
    pair_session, raop_device2
):
    # The normal case for HomePods, and unchanged: nothing is stored for either
    # half, so both are driven with what this device uses
    client = await pair_session()

    protocols = client._protocols  # pylint: disable=protected-access

    assert len(protocols) == 2
    assert _peer_port(protocols[1]) == raop_device2.get_port(Protocol.RAOP)
    assert protocols[0].credentials == protocols[1].credentials


@pytest.mark.parametrize("raop_properties", [{"et": "0"}])
async def test_volume_set_on_both_halves(raop_pair_client, raop_state, raop_state2):
    await raop_pair_client.audio.set_volume(80.0)

    await raop_pair_client.stream.stream_file(data_path("audio_10_frames.wav"))

    # Volume is set on a stereo pair as a whole, so both halves are told
    assert raop_state.volume == pct_to_dbfs(80.0)
    assert raop_state2.volume == pct_to_dbfs(80.0)
