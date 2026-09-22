"""Functional tests for streaming to both halves of a stereo pair.

Actual synchronization between two receivers cannot be verified here (sleeping
is stubbed in the test suite), so what is verified is what synchronization is
built on: both receivers get the same packets, from the same timeline, with the
same identity, and both are torn down afterwards.
"""

import pytest

from pyatv.protocols.airplay.utils import pct_to_dbfs

from tests.protocols.raop.test_raop_functional import audio_matches
from tests.utils import data_path, until

pytestmark = pytest.mark.asyncio



@pytest.mark.parametrize("raop_properties", [{"et": "0"}])
async def test_stream_file_to_both_halves(raop_pair_client, raop_state, raop_state2):
    await raop_pair_client.stream.stream_file(data_path("audio_10_frames.wav"))

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


@pytest.mark.parametrize("raop_properties", [{"et": "0"}])
async def test_volume_set_on_both_halves(raop_pair_client, raop_state, raop_state2):
    await raop_pair_client.audio.set_volume(80.0)

    await raop_pair_client.stream.stream_file(data_path("audio_10_frames.wav"))

    # Volume is set on a stereo pair as a whole, so both halves are told
    assert raop_state.volume == pct_to_dbfs(80.0)
    assert raop_state2.volume == pct_to_dbfs(80.0)
