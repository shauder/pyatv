---
layout: template
title: Stream
permalink: /development/stream/
link_group: development
---
 Table of Contents
{:.no_toc}
* TOC
{:toc}

# Stream

It is possible to stream audio and video to a device via the stream interface using
AirPlay. The AirPlay suite consists of two protocols:

* AirTunes/RAOP - Used for real time streaming of audio
* "AirPlay - Everything else (video, images and screen mirroring)

Currently there is some AirPlay functionality supported in pyatv, but it is
very limited. These features are currently supported:

- Device authentication ("pairing")
- Playing media via URL
- Streaming of local files

Early support for streaming audio files via RAOP is also supported
(even for non-Apple TV devices). MP3, wav, FLAC and ogg files are
supported. Devices that require a password are only supported for the AirTunes/RAOP protocol, not the AirPlay protocol. 

In the external interface, AirPlay (including RAOP) support is implemented via
the {% include api i="interface.Stream" %} interface.

## Using the streaming API

Devices supporting the AirPlay protocol (e.g. Apple TV) can play files by simply providing
a URL. It will then be streamed directly from the device. Audio can be streamed to other
devices (like AirPlay speakers) that does not support this.

### Play from URL

Playing a URL is as simple as passing the URL to {% include api i="interface.Stream.play_url" %}:

```python
url = "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4"
await atv.stream.play_url(url)
```

If the device requires device authentication, credentials must be present for
the AirPlay service. Otherwise an error message will be shown on the screen.

To play a local file, just pass a local file to {% include api i="interface.Stream.play_url" %}
instead:

```python
url = "/home/user/BigBuckBunny.mp4"
await atv.stream.play_url(url)
```

When doing this, pyatv will internally start web server on a random port, serving this
file only and start streaming from there. When streaming is done, the web server is shut
down.

The Apple TV will not provide any feedback if anything is not working. If you have
problems, start by testing the example file above (`BigBuckBunny.mp4`) as that is
known to work. Also make sure that you don't stream from an HTTPS server with a bad
or self-signed certificate: that will not work.

### Stream a file

To stream a file, use {% include api i="interface.Stream.stream_file" %}:

```python
stream = ...
await stream.stream_file("sample.mp3")
```

Files in MP3, WAV, FLAC and OGG format are supported and will be automatically converted
to a format the receiving device supports. Metadata is also extracted from files
of these types and sent to the receiver.

It is also possible to stream directly from a buffer. In this example, a file is
read into a buffer and streamed:

```python
import io

with io.open("myfile.mp3", "rb") as source_file:
    await stream.stream_file(source_file)
```

Streaming directly from `stdin` also works, e.g. when piping output from another
process:

```python
await stream.stream_file(sys.stdin.buffer)
```

As `stdin` is a text stream, the underlying binary buffer must be retrieved and used.

It is also possible to use an asyncio
[StreamReader](https://docs.python.org/3/library/asyncio-stream.html#streamreader) as
input. Here is an example piping output from ffmpeg:

```python
import asyncio.subprocess as asp

process = await asp.create_subprocess_exec(
    "ffmpeg", "-i", "file.mp3", "-f", "mp3", "-",
    stdin=None, stdout=asp.PIPE, stderr=None,
)

await self.atv.stream.stream_file(process.stdout)
```

When streaming from a buffer, it's important to know that some audio formats are
not suitable for that. MP3 works fine, WAV and OGG does not. The reason is that
seeking is done in the stream and `stdin` does for instance not support that. If
the buffer supports seeking, then all formats will work fine, otherwise stick with
MP3. For the same reason, metadata will not work if seeking is not supported as
that is extracted prior to playing the file, so seeking is needed to return to
the beginning of file again before playback.

Note 1: Since  pyatv v0.13.0, buffer improvements have been made to support some
seeking, even in non-seekable streams. This is however not fool-proof and not all
audio formats (or files/streams) work, but compatibility is much better from that
version and onwards.

Note 2: that there's (roughly) a two second delay until audio starts to play. This
is part of the buffering mechanism and not much pyatv can do anything about.

#### Custom Metadata

By default, pyatv will try to extract metadata from whatever content you are playing.
Some file formats or streams either does not support nor provide any metadata,
in which case you can manually provide the metadata that pyatv will report to the
receiver by passing an instance of {% include api i="interface.MediaMetadata" %}
when starting to stream:

```python
from pyatv.interface import MediaMetadata

metadata = MediaMetadata(artist="pyatv", title="Look at me, I'm streaming")
await stream.stream_file("myfile.mp3", metadata=metadata)
```

All fields in {% include api i="interface.MediaMetadata" %} can be overridden (including
artwork) except for {% include api i="interface.MediaMetadata.duration" %}, which is
ignored. Please note that artwork must be in JPEG format.

Custom metadata will override any metadata provided by the streamed content. You
can however tell pyatv to only override metadata fields that are missing by setting
`override_missing_metadata` to `True`:

```python
from pyatv.interface import MediaMetadata

metadata = MediaMetadata(artist="pyatv")
await stream.stream_file("myfile.mp3", metadata=metadata, override_missing_metadata=True)
```

This will use all the metadata from `myfile.mp3`, but use _pyatv_ as artist but **only**
if that field is not present in the file.

#### Stream from HTTP(S)

There is experimental support for streaming directly from HTTP or HTTPS. A URL can
be passed instead of a file path:

```python
await stream.stream_file("https://foo.bar/test.mp3")
```

#### File Compatibility

It is possible to verify if a file is supported programmatically using
{% include api i="helpers.is_streamable" %}:

```python
from pyatv.helpers import is_streamable

if await is_streamable("myfile.mp3"):
    await atv.stream_file("myfile.mp3")
else:
    print("File is not supported")
```

There are a few caveats worth knowing:

* No exception is ever raised, even when file is not found or lack of permissions
* Only valid for {% include api i="interface.Stream.stream_file" %} (*not*
  {% include api i="interface.Stream.play_url" %})
* Only a basic check is made, the file might be broken and not still not playable

## Stereo Pairs

Two speakers that form a stereo pair (e.g. two HomePods) are two independent
receivers on the network, but one device: they are bonded in HomeKit, share one
volume and split one stereo image between them. Scanning returns them as **one**
configuration, and streaming to it drives both halves:

```python
atvs = await scan(loop)  # a stereo pair is one entry here, not two

atv = await connect(atvs[0], loop)
await atv.stream.stream_file("sample.mp3")
```

Both halves are then set up as one playback group and fed the same audio from one
timeline, anchored at the same point in time and kept in sync by one (NTP) timing
server. They do play in sync: this has been verified by ear on a pair of HomePods
running audioOS 27, over a 30 second and a five minute run, with both halves
taking their time from the one timing server (32 requests answered between them
over the 30 second run). That is a listener's verdict over those durations, not a
measurement of drift.

What makes two addresses one device is the tight-sync identifier (`tsid`) in the
AirPlay TXT record, which is the same on both halves and does not change. The
group a receiver is in (`gid`) and the "is group leader" flags are *not* used:
they change from session to session, and a pair that is playing, or that a sender
left split, can have both halves calling themselves a leader. A room name (`gpn`)
is not used either: a room can hold several independent speakers and pairs, and
those stay separate configurations.

A few things are worth knowing:

* The pair keeps the identifier of one of its halves rather than getting a new
  one, so credentials that were already stored still apply. Which half is picked
  does not depend on which one currently leads.
* Only a pair both of whose halves are seen in the same scan is folded together.
  A half whose partner is switched off is returned on its own and can be streamed
  to on its own, as before. One speaker answering at two addresses - a stale DNS
  record beside a new one after DHCP moved it - is not a pair and is left alone:
  two halves have to be two devices.
* The identifier scanning prints for a pair means the pair, whether it is passed
  back to `scan(loop, identifier=...)` or to `atvremote --id`. The *other* half's
  identifier still means that half alone, so a single speaker of a pair can be
  addressed when it needs to be.
* Volume is a property of the pair, so both halves are set to the same volume and
  a pair reports one volume level for both halves.
* Streaming twice in rapid succession can fail: a pair goes through a transitional
  state for a few seconds after a session is torn down, where it may refuse to set
  up a new session. Waiting about 15 seconds between streams avoids that.
* More than two receivers is not supported: only one buddy per device, and nothing
  beyond a stereo pair has been verified.
* The halves stay a stereo pair while pyatv streams to them, and render their own
  left and right channels ([below](#the-pair-stays-a-pair)).
* Credentials are per speaker: a pair whose halves have been paired with a PIN
  needs *both* of them paired ([below](#a-pair-that-is-already-paired)).

### Pairing the halves up by hand

`protocols.raop.pair_buddy_address` overrides what scanning worked out. Set it to
the address of the *other* half to pair two receivers up that scanning did not, or
to pick a different second half than it did:

```python
settings = await storage.get_settings(conf)
settings.protocols.raop.pair_buddy_address = "10.0.0.20"

atv = await connect(conf, loop, storage=storage)
await atv.stream.stream_file("sample.mp3")
```

A port can be included if the other half does not use the same port as the device
being connected to, e.g. `10.0.0.20:7000`. The same can be set with `atvremote`:

```shell
atvremote -s 10.0.0.10 --id <identifier> \
    change_setting=protocols.raop.pair_buddy_address,10.0.0.20 stream_file=sample.mp3
```

This address is static: it is not discovered, so a device that gets a new address
from DHCP silently breaks the setup. That is the price of overriding scanning.

The setting cannot be used when credentials are stored for the device. Each half
is then verified with the pairing made with *it*, and an address does not say
which device answers at it, so there is nothing to look the other half's
credentials up by: `NotSupportedError` is raised rather than failing
mid-handshake. Unset it and let scanning group the pair, which carries the other
half's identifier along with its address, or remove the credentials.

### A pair that is already paired

A HomePod normally stores no credentials at all - it uses transient pairing - and
that is why a pair usually just works. A half that *has* been paired with a PIN
has credentials of its own, stored against its own identifier, and a pairing made
with one half means nothing to the other.

So **pair both halves and the pair groups**. Each half is verified with what is
stored for *it*: the pairing made with that speaker when there is one, and what
the device pyatv connects to uses when there is not. Pairing exactly one half
therefore leaves one of two situations, depending on which half scanning kept -
the lower identifier, which is nothing a user picks:

* The *other* half is the paired one. It brings its own pairing along, and both
  speakers play.
* The half pyatv connects to is the paired one. That pairing means nothing to the
  other half and there is nothing else to offer it, so that speaker is streamed to
  on its own, exactly as it was before a pair became one configuration, and pyatv
  logs a warning naming the half to pair:

```shell
atvremote --id <other half> --protocol raop pair
```

That half keeps its own identifier and is still found by a scan that asks for it,
even though it no longer shows up on its own in `atvremote scan`. If the warning
is not at hand, scanning just that speaker (`atvremote -s 10.0.0.20 scan`) prints
it: one half alone is not a pair and is not folded.

Which half is paired cannot be worked out while scanning - it has no storage, and
folds the two halves together before any settings are loaded - so it is decided
when the session is set up.

A pairing that has been revoked or reset on one half is a different matter from
one that was never made: credentials are still stored for that half, so it is
still streamed to, and the receiver rejecting them fails the session for *both*
speakers rather than for one. Pair that half again, or stream to the other one on
its own with `--id <half>`.

### The pair stays a pair

While pyatv streams to both halves, they remain a stereo pair. Both halves join
the group pyatv names, and the pair splits the channels itself: the left channel
comes out of one speaker and the right channel out of the other, from an ordinary
stereo file. pyatv sends the same full mix to each half and does nothing
channel-related — the speakers do the rest.

What splits a pair is a session that names no group, not the timing protocol: a
sender whose session sends no `groupUUID` leaves each half in a group of its own,
rendering the whole mix. pyatv sends one whenever it streams to both halves.

#### Group state, read off the network

Group membership is announced in cleartext in the mDNS TXT record, where `gid` is
the group UUID, `igl` is "is group leader" and `gcgl` is "group contains group
leader" (they are listed with the other properties in the
[protocol documentation](/documentation/protocols/)). Watched on a bonded pair of
HomePods running audioOS 27, with samples taken through live sessions:

| State of the pair | One half | The other half |
| ----------------- | -------- | -------------- |
| Bonded, nothing playing | `igl=1 gcgl=1`, shared `gid` | `igl=0 gcgl=1`, the same `gid` |
| Streamed to by pyatv, both halves (NTP) | `igl=0 gcgl=0`, pyatv's `gid` | `igl=0 gcgl=0`, the same `gid` |
| Streamed to over a shared PTP timeline | `igl=0 gcgl=0`, the sender's `gid` | `igl=0 gcgl=0`, the same `gid` |
| Streamed to by a session that names no group | `igl=1`, a `gid` of its own | `igl=1`, a different `gid` |

The middle two rows are the same signature, and it is the one an Apple sender
produces: both halves are members of the sender's group and neither claims to lead
it. The clock makes no difference to it. The pair travels together and splits the
channels itself.

The bottom row is what a session without a `groupUUID` does; it was measured with
another sender, driven both ways on the same pair, not with pyatv. pyatv sends a
`groupUUID` whenever it streams to both halves, together with
`senderSupportsRelay: true`, without which a receiver ignores the group.

The TXT record is also how you can check it on your own pair, with `dns-sd -L` or
any other mDNS browser, while a stream is running.

#### The stereo image, by ear

Group membership is not the same thing as what comes out of the speakers, so the
image was checked by listening. The test file had a continuous low drone on the
left channel and a short high beep once a second on the right; it was streamed to
both halves with nothing done sender-side. The listener heard the beep from one
speaker only, and the drone from both positions — which is what a 150 Hz tone does
in a room rather than a sign of crosstalk, since low frequencies are close to
omnidirectional and a 1600 Hz beep localises cleanly. That is why the discriminating
tone was the high one. The same verdict was reached through this branch over NTP
and through another project's sender over PTP.

What that rests on: one stereo pair, one listener, one day of runs. The sync
verdict is a listener's verdict too, over a 30 second and a five minute run. Neither
is a measurement of drift or of channel separation.

#### NTP is enough for this

pyatv implements NTP timing only, and for stereo pairs that turns out not to cost
anything: the group forms with the same signature as under PTP, and the pair renders
its own image either way. PTP would still be a large change if it were ever wanted
for something else — a full IEEE 1588 clock on UDP ports 319 and 320, which are
privileged, and which on macOS are held by the host's own AirPlay stack while it is
streaming. A sender-side split, sending left only to one half and right only to the
other, is not needed for a bonded pair; pyatv does not do it.

#### Speaker names do not tell you which channel a half renders

On the pair used here, the half whose name ends in "Left" renders the **right**
channel. Names are assigned by whoever set the speakers up, and nothing observed on
the network — TXT record or session response — advertises the role. Do not infer it
from a name, in your own code or when setting `pair_buddy_address`: which half is
which does not affect grouping, and the only way to find out is to listen.

## Password

If you stream audio using the RAOP protocol and the device requires a password, you can set the password like this: 

```python
raop_service = atv_conf.get_service(Protocol.RAOP)
raop_service.password = "test"
atv = await connect(atv_conf, ...)
await atv.stream.stream_file("sample.mp3")
```

## Device Authentication

In tvOS 10.2, Apple started to enforce a feature called "device authentication".
This requires every device that streams content via AirPlay to enter a PIN code
the first time before playback is started. Once done, the user will never have
to do this again. The actual feature has been available for a while but as
opt-in, so it would have to be explicitly enabled. Now it is enabled by default
and cannot be disabled. Devices not running tvOS (e.g. Apple TV 2nd and 3rd
generation) are not affected, even though device authentication can be enabled
on these devices as well.

The device authentication process is based on the *Secure Remote Password*
protocol (SRP), with slight modifications. All the reverse engineering required
for this process was made by funtax (GitHub username) and has merely been ported
to python for usage in this library. Please see references at bottom of page
for reference implementation.

### Device Pairing in pyatv

When performing device authentication, a device identifier and a private key is
required. Once authenticated, they can be used to authenticate without using a
PIN code. So they must be saved and reused whenever something is to be played.

In this library, the device identifier and private key is called
*AirPlay credentials* and are concatenated into a string, using : as separator.
An example might look like this:

```raw
D9B75D737BE2F0F1:6A26D8EB6F4AE2408757D5CA5FF9C37E96BEBB22C632426C4A02AD4FA895A85B
        ^                       ^
    Identifier              Private key
```

The device authentication is performed via the pairing API, just like with
any other protocol. New random credentials are generated by default, as long
as no existing credentials are provided. So there is nothing special here.
