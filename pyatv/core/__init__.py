"""Core module of pyatv."""

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Mapping,
    NamedTuple,
    Optional,
    Set,
    Union,
)

from pyatv.const import FeatureName, PairingRequirement, Protocol
from pyatv.core.protocol import MessageDispatcher
from pyatv.interface import BaseConfig, BaseService, Playing, PushUpdater, Storage
from pyatv.settings import Settings
from pyatv.support.http import ClientSessionManager, create_session
from pyatv.support.state_producer import StateProducer

TakeoverMethod = Callable[
    ...,
    Callable[[], None],
]


# pylint: disable=invalid-name


class UpdatedState(Enum):
    """Name of states that can be updated."""

    Playing = 1
    """Playing state in metadata was updated."""

    Volume = 2
    """Volume was updated."""

    KeyboardFocus = 3
    """Keyboard focus was updated."""

    OutputDevices = 4
    """AirPlay output devices were updated."""

    OutputDeviceVolume = 5
    """Output device volume was updated."""


# pylint: enable=invalid-name


class StateMessage(NamedTuple):
    """Message sent when state of something changed."""

    protocol: Protocol
    state: UpdatedState

    # Type depending on value of state:
    # - UpdatedState.Playing -> interface.Playing
    # - UpdatedState.Volume -> float
    # - UpdatedState.KeyboardFocus -> const.KeyboardFocusState
    # - UpdatedState.OutputDevices -> List[interface.OutputDevice]
    value: Any

    def __str__(self):
        """Return string representation of object."""
        return f"[{self.protocol.name}.{self.state.name} -> {self.value}]"


def _no_filter(message: StateMessage) -> bool:
    return True


CoreStateDispatcher = MessageDispatcher[UpdatedState, StateMessage]


class ProtocolStateDispatcher:
    """Dispatch internal protocol state updates to listeners."""

    def __init__(
        self,
        protocol: Protocol,
        core_dispatcher: CoreStateDispatcher,
    ) -> None:
        """Initialize a new ProtocolStateDispatcher instance."""
        self._protocol = protocol
        self._core_dispatcher = core_dispatcher

    def create_copy(self, protocol: Protocol) -> "ProtocolStateDispatcher":
        """Create a copy of this instance but with a new protocol."""
        return ProtocolStateDispatcher(protocol, self._core_dispatcher)

    def listen_to(
        self,
        state: UpdatedState,
        func: Callable[[StateMessage], Union[None, Awaitable[None]]],
        message_filter: Callable[[StateMessage], bool] = _no_filter,
    ) -> None:
        """Listen to a specific type of message type."""
        return self._core_dispatcher.listen_to(state, func, message_filter)

    def dispatch(self, state: UpdatedState, value: Any) -> List[asyncio.Task]:
        """Dispatch a message to listeners."""
        return self._core_dispatcher.dispatch(
            state, StateMessage(self._protocol, state, value)
        )


class MutableService(BaseService):
    """Mutable version of BaseService allowing some fields to be changed.

    This is an internal implementation of BaseService that allows protocols to change
    some fields during set up. The mutable property is not exposed outside of core.
    """

    def __init__(
        self,
        identifier: Optional[str],
        protocol: Protocol,
        port: int,
        properties: Optional[Mapping[str, str]],
        credentials: Optional[str] = None,
        password: Optional[str] = None,
        enabled: bool = True,
    ) -> None:
        """Initialize a new MutableService."""
        super().__init__(
            identifier, protocol, port, properties, credentials, password, enabled
        )
        self._requires_password = False
        self._pairing_requirement = PairingRequirement.Unsupported

        # Address ("host" or "host:port") of a second receiver this service also
        # drives: the other half of a stereo pair, filled in by scanning when two
        # addresses turn out to be one device. Not a zeroconf property, as it is
        # derived from comparing what several addresses advertised.
        self.stereo_pair_address: Optional[str] = None

        # Identifier of that same second receiver. It travels with the address
        # because credentials are stored per device and keyed by identifier: a
        # half that has been paired on its own can only be looked up by this.
        self.stereo_pair_identifier: Optional[str] = None

    @property
    def requires_password(self) -> bool:
        """Return if a password is required to access service."""
        return self._requires_password

    @requires_password.setter
    def requires_password(self, value: bool) -> None:
        """Change if password is required or not."""
        self._requires_password = value

    @property
    def pairing(self) -> PairingRequirement:
        """Return if pairing is required by service."""
        return self._pairing_requirement

    @pairing.setter
    def pairing(self, value: PairingRequirement) -> None:
        """Change if pairing is required by service."""
        self._pairing_requirement = value

    def __deepcopy__(self, memo) -> "BaseService":
        """Return deep-copy of instance."""
        copy = MutableService(
            self.identifier,
            self.protocol,
            self.port,
            self.properties,
            self.credentials,
            self.password,
            self.enabled,
        )
        copy.pairing = self.pairing
        copy.requires_password = self.requires_password
        copy.stereo_pair_address = self.stereo_pair_address
        copy.stereo_pair_identifier = self.stereo_pair_identifier
        return copy


class AbstractPushUpdater(PushUpdater):
    """Abstract push updater class.

    This class adds a `post_update` method that will publishes a new state, but only
    if it has been updated.
    """

    def __init__(self, state_dispatcher: ProtocolStateDispatcher):
        """Initialize a new AbstractPushUpdater."""
        super().__init__()
        self.loop: asyncio.AbstractEventLoop = asyncio.get_event_loop()
        self.state_dispatcher = state_dispatcher
        self._previous_state: Optional[Playing] = None

    def post_update(self, playing: Playing) -> None:
        """Post an update to listener."""
        if playing != self._previous_state:
            # Dispatch message using message dispatcher
            self.state_dispatcher.dispatch(UpdatedState.Playing, playing)

            # Publish using regular (external) interface. This shall be removed at
            # some point in time.
            self.loop.call_soon(self.listener.playstatus_update, self, playing)

        self._previous_state = playing


class SetupData(NamedTuple):
    """Information for setting up a protocol."""

    protocol: Protocol
    connect: Callable[[], Awaitable[bool]]
    close: Callable[[], Set[asyncio.Task]]
    device_info: Callable[[], Dict[str, Any]]
    interfaces: Mapping[Any, Any]
    features: Set[FeatureName]


class Core:
    """Instance for protocols to access core features."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        config: BaseConfig,
        service: BaseService,
        settings: Settings,
        device_listener: StateProducer,
        session_manager: ClientSessionManager,
        takeover: TakeoverMethod,
        state_dispatcher: ProtocolStateDispatcher,
        storage: Storage,
    ) -> None:
        """Initialize a new Core instance."""
        self.loop = loop
        self.config = config
        self.service = service
        self.settings = settings
        self.device_listener = device_listener
        self.session_manager = session_manager
        self.takeover = takeover
        self.state_dispatcher = state_dispatcher

        # Settings of *this* device are in "settings" above. Storage is here for the
        # rare case where a protocol needs the settings of another device: streaming
        # to the other half of a stereo pair needs that half's credentials, and
        # credentials are stored per device, keyed by identifier.
        self.storage = storage


async def create_core(
    config: BaseConfig,
    service: BaseService,
    /,
    storage: Storage,
    settings: Optional[Settings] = None,
    device_listener: Optional[StateProducer] = None,
    session_manager: Optional[ClientSessionManager] = None,
    core_dispatcher: Optional[CoreStateDispatcher] = None,
    takeover_method: Optional[TakeoverMethod] = None,
    loop: Optional[asyncio.AbstractEventLoop] = None,
) -> Core:
    """Create a new core instance.

    Mostly a convenience method that provides defaults for some arguments.
    """

    def _takeover():
        pass

    return Core(
        loop or asyncio.get_running_loop(),
        config,
        service,
        settings or Settings(),
        device_listener or StateProducer(),
        session_manager or await create_session(),
        takeover_method or _takeover,
        ProtocolStateDispatcher(
            service.protocol, core_dispatcher or CoreStateDispatcher()
        ),
        storage,
    )


@dataclass
class OutputDeviceState:
    """State information of an output device."""

    identifier: str
    volume: float = 0.0

    def __str__(self) -> str:
        """Convert app info to readable string."""
        return f"Device: {self.identifier} ({self.volume})"

    def __eq__(self, other) -> bool:
        """Return self==other."""
        if isinstance(other, OutputDeviceState):
            return self.identifier == other.identifier and self.volume == other.volume
        return False
