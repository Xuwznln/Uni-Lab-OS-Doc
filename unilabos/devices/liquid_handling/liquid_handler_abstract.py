from __future__ import annotations

from math import e
import time
import traceback
from collections import Counter
from typing import List, Sequence, Optional, Literal, Union, Iterator, Dict, Any, Callable, Set, cast

from pylabrobot.liquid_handling import LiquidHandler, LiquidHandlerBackend, LiquidHandlerChatterboxBackend, Strictness
from pylabrobot.liquid_handling.liquid_handler import TipPresenceProbingMethod
from pylabrobot.liquid_handling.standard import GripDirection
from pylabrobot.resources.errors import TooLittleLiquidError, TooLittleVolumeError
from pylabrobot.resources import (
    Resource,
    TipRack,
    Container,
    Coordinate,
    Well,
    Deck,
    TipSpot,
    Plate,
    ResourceStack,
    ResourceHolder,
    Lid,
    Trash,
    Tip, TubeRack,
)
from typing_extensions import TypedDict

from unilabos.devices.liquid_handling.rviz_backend import UniLiquidHandlerRvizBackend
from unilabos.registry.placeholder_type import ResourceSlot
from unilabos.resources.resource_tracker import (
    ResourceTreeSet,
    ResourceDict,
    EXTRA_SAMPLE_UUID,
    EXTRA_UNILABOS_SAMPLE_UUID,
)
from unilabos.ros.nodes.base_device_node import BaseROS2DeviceNode, ROS2DeviceNode


class SimpleReturn(TypedDict):
    samples: List[List[ResourceDict]]
    volumes: List[float]


class SetLiquidReturn(TypedDict):
    wells: List[List[ResourceDict]]
    volumes: List[float]


class SetLiquidFromPlateReturn(TypedDict):
    plate: List[List[ResourceDict]]
    wells: List[List[ResourceDict]]
    volumes: List[float]


class TransferLiquidReturn(TypedDict):
    sources: List[List[ResourceDict]]
    targets: List[List[ResourceDict]]


class LiquidHandlerMiddleware(LiquidHandler):
    def __init__(
        self, backend: LiquidHandlerBackend, deck: Deck, simulator: bool = False, channel_num: int = 8, **kwargs
    ):
        self._simulator = simulator
        self.channel_num = channel_num
        self.pending_liquids_dict = {}
        joint_config = kwargs.get("joint_config", None)
        if simulator:
            if joint_config:
                self._simulate_backend = UniLiquidHandlerRvizBackend(
                    channel_num, kwargs["total_height"], joint_config=joint_config, lh_device_id=deck.name
                )
            else:
                self._simulate_backend = LiquidHandlerChatterboxBackend(channel_num)
            self._simulate_handler = LiquidHandlerAbstract(self._simulate_backend, deck, False)
        super().__init__(backend, deck)

    async def setup(self, **backend_kwargs):
        if self._simulator:
            return await self._simulate_handler.setup(**backend_kwargs)
        return await super().setup(**backend_kwargs)

    def serialize_state(self) -> Dict[str, Any]:
        if self._simulator:
            self._simulate_handler.serialize_state()
        return super().serialize_state()

    def load_state(self, state: Dict[str, Any]):
        if self._simulator:
            self._simulate_handler.load_state(state)
        super().load_state(state)

    def update_head_state(self, state: Dict[int, Optional[Tip]]):
        if self._simulator:
            self._simulate_handler.update_head_state(state)
        super().update_head_state(state)

    def clear_head_state(self):
        if self._simulator:
            self._simulate_handler.clear_head_state()
        super().clear_head_state()

    def _run_async_in_thread(self, func, *args, **kwargs):
        super()._run_async_in_thread(func, *args, **kwargs)

    def _send_assigned_resource_to_backend(self, resource: Resource):
        if self._simulator:
            self._simulate_handler._send_assigned_resource_to_backend(resource)
        super()._send_assigned_resource_to_backend(resource)

    def _send_unassigned_resource_to_backend(self, resource: Resource):
        if self._simulator:
            self._simulate_handler._send_unassigned_resource_to_backend(resource)
        super()._send_unassigned_resource_to_backend(resource)

    def summary(self):
        if self._simulator:
            self._simulate_handler.summary()
        super().summary()

    def _assert_positions_unique(self, positions: List[str]):
        super()._assert_positions_unique(positions)

    def _assert_resources_exist(self, resources: Sequence[Resource]):
        super()._assert_resources_exist(resources)

    def _check_args(
        self, method: Callable, backend_kwargs: Dict[str, Any], default: Set[str], strictness: Strictness
    ) -> Set[str]:
        return super()._check_args(method, backend_kwargs, default, strictness)

    def _make_sure_channels_exist(self, channels: List[int]):
        super()._make_sure_channels_exist(channels)

    def _format_param(self, value: Any) -> Any:
        return super()._format_param(value)

    def _log_command(self, name: str, **kwargs) -> None:
        super()._log_command(name, **kwargs)

    async def pick_up_tips(
        self,
        tip_spots: List[TipSpot],
        use_channels: Optional[List[int]] = None,
        offsets: Optional[List[Coordinate]] = None,
        **backend_kwargs,
    ):

        if self._simulator:
            return await self._simulate_handler.pick_up_tips(tip_spots, use_channels, offsets, **backend_kwargs)
        return await super().pick_up_tips(tip_spots, use_channels, offsets, **backend_kwargs)

    async def drop_tips(
        self,
        tip_spots: Sequence[Union[TipSpot, Trash]],
        use_channels: Optional[List[int]] = None,
        offsets: Optional[List[Coordinate]] = None,
        allow_nonzero_volume: bool = False,
        **backend_kwargs,
    ):
        if self._simulator:
            return await self._simulate_handler.drop_tips(
                tip_spots, use_channels, offsets, allow_nonzero_volume, **backend_kwargs
            )
        await super().drop_tips(tip_spots, use_channels, offsets, allow_nonzero_volume, **backend_kwargs)
        self.pending_liquids_dict = {}
        return

    async def return_tips(
        self, use_channels: Optional[list[int]] = None, allow_nonzero_volume: bool = False, **backend_kwargs
    ):
        if self._simulator:
            return await self._simulate_handler.return_tips(use_channels, allow_nonzero_volume, **backend_kwargs)
        return await super().return_tips(use_channels, allow_nonzero_volume, **backend_kwargs)

    async def discard_tips(
        self,
        use_channels: Optional[List[int]] = None,
        allow_nonzero_volume: bool = True,
        offsets: Optional[List[Coordinate]] = None,
        **backend_kwargs,
    ):
        # 如果 use_channels 为 None，使用默认值（所有通道）
        if use_channels is None:
            use_channels = list(range(self.channel_num))
        if not offsets or (isinstance(offsets, list) and len(offsets) != len(use_channels)):
            offsets = [Coordinate.zero()] * len(use_channels)
        if self._simulator:
            return await self._simulate_handler.discard_tips(
                use_channels, allow_nonzero_volume, offsets, **backend_kwargs
            )
        await super().discard_tips(use_channels, allow_nonzero_volume, offsets, **backend_kwargs)
        self.pending_liquids_dict = {}
        return

    def _check_containers(self, resources: Sequence[Resource]):
        super()._check_containers(resources)

    async def aspirate(
        self,
        resources: Sequence[Container],
        vols: List[float],
        use_channels: Optional[List[int]] = None,
        flow_rates: Optional[List[Optional[float]]] = None,
        offsets: Optional[List[Coordinate]] = None,
        liquid_height: Optional[List[Optional[float]]] = None,
        blow_out_air_volume: Optional[List[Optional[float]]] = None,
        spread: Literal["wide", "tight", "custom"] = "wide",
        **backend_kwargs,
    ):
        if spread == "":
            spread = "wide"

        def _safe_aspirate_volumes(_resources: Sequence[Container], _vols: List[float]) -> List[float]:
            """将 aspirate 体积裁剪到源容器当前液量范围内，避免 volume tracker 报错。"""
            safe: List[float] = []
            for res, vol in zip(_resources, _vols):
                req = max(float(vol), 0.0)
                used_volume = None
                try:
                    tracker = getattr(res, "tracker", None)
                    if bool(getattr(tracker, "is_disabled", False)):
                        # tracker 关闭时（例如预吸空气），不按液体体积裁剪
                        safe.append(req)
                        continue
                    get_used = getattr(tracker, "get_used_volume", None)
                    if callable(get_used):
                        used_volume = get_used()
                except Exception:
                    used_volume = None

                if isinstance(used_volume, (int, float)):
                    req = min(req, max(float(used_volume), 0.0))
                safe.append(req)
            return safe

        actual_vols = _safe_aspirate_volumes(resources, vols)
        if actual_vols != vols and hasattr(self, "_ros_node") and self._ros_node is not None:
            self._ros_node.lab_logger().warning(f"[aspirate] volume adjusted, requested_vols={vols}, actual_vols={actual_vols}")
        if self._simulator:
            return await self._simulate_handler.aspirate(
                resources,
                actual_vols,
                use_channels,
                flow_rates,
                offsets,
                liquid_height,
                blow_out_air_volume,
                spread,
                **backend_kwargs,
            )
        try:
            await super().aspirate(
                resources,
                actual_vols,
                use_channels,
                flow_rates,
                offsets,
                liquid_height,
                blow_out_air_volume,
                spread,
                **backend_kwargs,
            )
        except ValueError as e:
            if "Resource is too small to space channels" in str(e) and spread != "custom":
                await super().aspirate(
                    resources,
                    actual_vols,
                    use_channels,
                    flow_rates,
                    offsets,
                    liquid_height,
                    blow_out_air_volume,
                    spread="custom",
                    **backend_kwargs,
                )
            else:
                raise
        except TooLittleLiquidError:
            # 再兜底一次：按实时可用液量重算后重试，避免状态更新竞争导致的瞬时不足
            retry_vols = _safe_aspirate_volumes(resources, actual_vols)
            if any(v > 0 for v in retry_vols):
                await super().aspirate(
                    resources,
                    retry_vols,
                    use_channels,
                    flow_rates,
                    offsets,
                    liquid_height,
                    blow_out_air_volume,
                    spread,
                    **backend_kwargs,
                )
                actual_vols = retry_vols
            else:
                actual_vols = retry_vols

        res_samples = []
        res_volumes = []
        # 处理 use_channels 为 None 的情况（通常用于单通道操作）
        if use_channels is None:
            # 对于单通道操作，推断通道为 [0]
            channels_to_use = [0] * len(resources)
        else:
            channels_to_use = use_channels

        for resource, volume, channel in zip(resources, actual_vols, channels_to_use):
            sample_uuid_value = getattr(resource, "unilabos_extra", {}).get(EXTRA_SAMPLE_UUID, None)
            res_samples.append({"name": resource.name, "sample_uuid": sample_uuid_value})
            res_volumes.append(volume)
            self.pending_liquids_dict[channel] = {
                EXTRA_SAMPLE_UUID: sample_uuid_value,
                "volume": volume,
            }
        return SimpleReturn(samples=res_samples, volumes=res_volumes)

    async def dispense(
        self,
        resources: Sequence[Container],
        vols: List[float],
        use_channels: Optional[List[int]] = None,
        flow_rates: Optional[List[Optional[float]]] = None,
        offsets: Optional[List[Coordinate]] = None,
        liquid_height: Optional[List[Optional[float]]] = None,
        blow_out_air_volume: Optional[List[Optional[float]]] = None,
        spread: Literal["wide", "tight", "custom"] = "wide",
        **backend_kwargs,
    ) -> SimpleReturn:
        if spread == "":
            spread = "wide"

        def _safe_dispense_volumes(_resources: Sequence[Container], _vols: List[float]) -> List[float]:
            """将 dispense 体积裁剪到目标容器可用体积范围内，避免 volume tracker 报错。"""
            safe: List[float] = []
            for res, vol in zip(_resources, _vols):
                req = max(float(vol), 0.0)
                free_volume = None
                try:
                    tracker = getattr(res, "tracker", None)
                    get_free = getattr(tracker, "get_free_volume", None)
                    if callable(get_free):
                        free_volume = get_free()
                except Exception:
                    free_volume = None

                if isinstance(free_volume, (int, float)):
                    req = min(req, max(float(free_volume), 0.0))
                safe.append(req)
            return safe

        actual_vols = _safe_dispense_volumes(resources, vols)

        if self._simulator:
            return await self._simulate_handler.dispense(
                resources,
                actual_vols,
                use_channels,
                flow_rates,
                offsets,
                liquid_height,
                blow_out_air_volume,
                spread,
                **backend_kwargs,
            )
        try:
            await super().dispense(
                resources,
                actual_vols,
                use_channels,
                flow_rates,
                offsets,
                liquid_height,
                blow_out_air_volume,
                spread,
                **backend_kwargs,
            )
        except ValueError as e:
            if "Resource is too small to space channels" in str(e) and spread != "custom":
                await super().dispense(
                    resources,
                    actual_vols,
                    use_channels,
                    flow_rates,
                    offsets,
                    liquid_height,
                    blow_out_air_volume,
                    "custom",
                    **backend_kwargs,
                )
            else:
                raise
        except TooLittleVolumeError:
            # 再兜底一次：按实时 free volume 重新裁剪后重试，避免并发状态更新导致的瞬时超量
            retry_vols = _safe_dispense_volumes(resources, actual_vols)
            if any(v > 0 for v in retry_vols):
                await super().dispense(
                    resources,
                    retry_vols,
                    use_channels,
                    flow_rates,
                    offsets,
                    liquid_height,
                    blow_out_air_volume,
                    spread,
                    **backend_kwargs,
                )
                actual_vols = retry_vols
            else:
                actual_vols = retry_vols
        res_samples = []
        res_volumes = []
        if use_channels is None:
            channels_to_use = [0] * len(resources)
        else:
            channels_to_use = use_channels
        for resource, volume, channel in zip(resources, actual_vols, channels_to_use):
            res_uuid = self.pending_liquids_dict[channel][EXTRA_SAMPLE_UUID]
            self.pending_liquids_dict[channel]["volume"] -= volume
            resource.unilabos_extra[EXTRA_SAMPLE_UUID] = res_uuid
            res_samples.append({"name": resource.name, EXTRA_SAMPLE_UUID: res_uuid})
            res_volumes.append(volume)

        return SimpleReturn(samples=res_samples, volumes=res_volumes)

    async def transfer(
        self,
        source: Well,
        targets: List[Well],
        source_vol: Optional[float] = None,
        ratios: Optional[List[float]] = None,
        target_vols: Optional[List[float]] = None,
        aspiration_flow_rate: Optional[float] = None,
        dispense_flow_rates: Optional[List[Optional[float]]] = None,
        **backend_kwargs,
    ):
        if self._simulator:
            return await self._simulate_handler.transfer(
                source,
                targets,
                source_vol,
                ratios,
                target_vols,
                aspiration_flow_rate,
                dispense_flow_rates,
                **backend_kwargs,
            )
        return await super().transfer(
            source,
            targets,
            source_vol,
            ratios,
            target_vols,
            aspiration_flow_rate,
            dispense_flow_rates,
            **backend_kwargs,
        )

    def use_channels(self, channels: List[int]):
        if self._simulator:
            self._simulate_handler.use_channels(channels)
        return super().use_channels(channels)

    async def pick_up_tips96(self, tip_rack: TipRack, offset: Coordinate = Coordinate.zero(), **backend_kwargs):
        if self._simulator:
            return await self._simulate_handler.pick_up_tips96(tip_rack, offset, **backend_kwargs)
        return await super().pick_up_tips96(tip_rack, offset, **backend_kwargs)

    async def drop_tips96(
        self,
        resource: Union[TipRack, Trash],
        offset: Coordinate = Coordinate.zero(),
        allow_nonzero_volume: bool = False,
        **backend_kwargs,
    ):
        if self._simulator:
            return await self._simulate_handler.drop_tips96(resource, offset, allow_nonzero_volume, **backend_kwargs)
        return await super().drop_tips96(resource, offset, allow_nonzero_volume, **backend_kwargs)

    def _get_96_head_origin_tip_rack(self) -> Optional[TipRack]:
        return super()._get_96_head_origin_tip_rack()

    async def return_tips96(self, allow_nonzero_volume: bool = False, **backend_kwargs):
        if self._simulator:
            return await self._simulate_handler.return_tips96(allow_nonzero_volume, **backend_kwargs)
        return await super().return_tips96(allow_nonzero_volume, **backend_kwargs)

    async def discard_tips96(self, allow_nonzero_volume: bool = True, **backend_kwargs):
        if self._simulator:
            return await self._simulate_handler.discard_tips96(allow_nonzero_volume, **backend_kwargs)
        return await super().discard_tips96(allow_nonzero_volume, **backend_kwargs)

    async def aspirate96(
        self,
        resource: Union[Plate, Container, List[Well]],
        volume: float,
        offset: Coordinate = Coordinate.zero(),
        flow_rate: Optional[float] = None,
        blow_out_air_volume: Optional[float] = None,
        **backend_kwargs,
    ):
        if self._simulator:
            return await self._simulate_handler.aspirate96(
                resource, volume, offset, flow_rate, blow_out_air_volume, **backend_kwargs
            )
        return await super().aspirate96(resource, volume, offset, flow_rate, blow_out_air_volume, **backend_kwargs)

    async def dispense96(
        self,
        resource: Union[Plate, Container, List[Well]],
        volume: float,
        offset: Coordinate = Coordinate.zero(),
        flow_rate: Optional[float] = None,
        blow_out_air_volume: Optional[float] = None,
        **backend_kwargs,
    ):
        if self._simulator:
            return await self._simulate_handler.dispense96(
                resource, volume, offset, flow_rate, blow_out_air_volume, **backend_kwargs
            )
        return await super().dispense96(resource, volume, offset, flow_rate, blow_out_air_volume, **backend_kwargs)

    async def stamp(
        self,
        source: Plate,
        target: Plate,
        volume: float,
        aspiration_flow_rate: Optional[float] = None,
        dispense_flow_rate: Optional[float] = None,
    ):
        if self._simulator:
            return await self._simulate_handler.stamp(source, target, volume, aspiration_flow_rate, dispense_flow_rate)
        return await super().stamp(source, target, volume, aspiration_flow_rate, dispense_flow_rate)

    async def pick_up_resource(
        self,
        resource: Resource,
        offset: Coordinate = Coordinate.zero(),
        pickup_distance_from_top: float = 0,
        direction: GripDirection = GripDirection.FRONT,
        **backend_kwargs,
    ):
        if self._simulator:
            return await self._simulate_handler.pick_up_resource(
                resource, offset, pickup_distance_from_top, direction, **backend_kwargs
            )
        return await super().pick_up_resource(resource, offset, pickup_distance_from_top, direction, **backend_kwargs)

    async def move_picked_up_resource(
        self,
        to: Coordinate,
        offset: Coordinate = Coordinate.zero(),
        direction: Optional[GripDirection] = None,
        **backend_kwargs,
    ):
        if self._simulator:
            return await self._simulate_handler.move_picked_up_resource(to, offset, direction, **backend_kwargs)
        return await super().move_picked_up_resource(to, offset, direction, **backend_kwargs)

    async def drop_resource(
        self,
        destination: Union[ResourceStack, ResourceHolder, Resource, Coordinate],
        offset: Coordinate = Coordinate.zero(),
        direction: GripDirection = GripDirection.FRONT,
        **backend_kwargs,
    ):
        if self._simulator:
            return await self._simulate_handler.drop_resource(destination, offset, direction, **backend_kwargs)
        return await super().drop_resource(destination, offset, direction, **backend_kwargs)

    async def move_resource(
        self,
        resource: Resource,
        to: Union[ResourceStack, ResourceHolder, Resource, Coordinate],
        intermediate_locations: Optional[List[Coordinate]] = None,
        pickup_offset: Coordinate = Coordinate.zero(),
        destination_offset: Coordinate = Coordinate.zero(),
        pickup_distance_from_top: float = 0,
        pickup_direction: GripDirection = GripDirection.FRONT,
        drop_direction: GripDirection = GripDirection.FRONT,
        **backend_kwargs,
    ):
        if self._simulator:
            return await self._simulate_handler.move_resource(
                resource,
                to,
                intermediate_locations,
                pickup_offset,
                destination_offset,
                pickup_distance_from_top,
                pickup_direction,
                drop_direction,
                **backend_kwargs,
            )
        return await super().move_resource(
            resource,
            to,
            intermediate_locations,
            pickup_offset,
            destination_offset,
            pickup_distance_from_top,
            pickup_direction,
            drop_direction,
            **backend_kwargs,
        )

    async def move_lid(
        self,
        lid: Lid,
        to: Union[Plate, ResourceStack, Coordinate],
        intermediate_locations: Optional[List[Coordinate]] = None,
        pickup_offset: Coordinate = Coordinate.zero(),
        destination_offset: Coordinate = Coordinate.zero(),
        pickup_direction: GripDirection = GripDirection.FRONT,
        drop_direction: GripDirection = GripDirection.FRONT,
        pickup_distance_from_top: float = 5.7 - 3.33,
        **backend_kwargs,
    ):
        if self._simulator:
            return await self._simulate_handler.move_lid(
                lid,
                to,
                intermediate_locations,
                pickup_offset,
                destination_offset,
                pickup_direction,
                drop_direction,
                pickup_distance_from_top,
                **backend_kwargs,
            )
        return await super().move_lid(
            lid,
            to,
            intermediate_locations,
            pickup_offset,
            destination_offset,
            pickup_direction,
            drop_direction,
            pickup_distance_from_top,
            **backend_kwargs,
        )

    async def move_plate(
        self,
        plate: Plate,
        to: Union[ResourceStack, ResourceHolder, Resource, Coordinate],
        intermediate_locations: Optional[List[Coordinate]] = None,
        pickup_offset: Coordinate = Coordinate.zero(),
        destination_offset: Coordinate = Coordinate.zero(),
        drop_direction: GripDirection = GripDirection.FRONT,
        pickup_direction: GripDirection = GripDirection.FRONT,
        pickup_distance_from_top: float = 13.2 - 3.33,
        **backend_kwargs,
    ):
        if self._simulator:
            return await self._simulate_handler.move_plate(
                plate,
                to,
                intermediate_locations,
                pickup_offset,
                destination_offset,
                drop_direction,
                pickup_direction,
                pickup_distance_from_top,
                **backend_kwargs,
            )
        return await super().move_plate(
            plate,
            to,
            intermediate_locations,
            pickup_offset,
            destination_offset,
            drop_direction,
            pickup_direction,
            pickup_distance_from_top,
            **backend_kwargs,
        )

    def serialize(self):
        if self._simulator:
            self._simulate_handler.serialize()
        return super().serialize()

    @classmethod
    def deserialize(cls, data: dict, allow_marshal: bool = False) -> LiquidHandler:
        return super().deserialize(data, allow_marshal)

    @classmethod
    def load(cls, path: str) -> LiquidHandler:
        return super().load(path)

    async def prepare_for_manual_channel_operation(self, channel: int):
        if self._simulator:
            return await self._simulate_handler.prepare_for_manual_channel_operation(channel)
        return await super().prepare_for_manual_channel_operation(channel)

    async def move_channel_x(self, channel: int, x: float):
        if self._simulator:
            return await self._simulate_handler.move_channel_x(channel, x)
        return await super().move_channel_x(channel, x)

    async def move_channel_y(self, channel: int, y: float):
        if self._simulator:
            return await self._simulate_handler.move_channel_y(channel, y)
        return await super().move_channel_y(channel, y)

    async def move_channel_z(self, channel: int, z: float):
        if self._simulator:
            return await self._simulate_handler.move_channel_z(channel, z)
        return await super().move_channel_z(channel, z)

    def assign_child_resource(self, resource: Resource, location: Optional[Coordinate], reassign: bool = True):
        if self._simulator:
            self._simulate_handler.assign_child_resource(resource, location, reassign)
        pass

    async def probe_tip_presence_via_pickup(
        self, tip_spots: List[TipSpot], use_channels: Optional[List[int]] = None
    ) -> Dict[str, bool]:
        if self._simulator:
            return await self._simulate_handler.probe_tip_presence_via_pickup(tip_spots, use_channels)
        return await super().probe_tip_presence_via_pickup(tip_spots, use_channels)

    async def probe_tip_inventory(
        self,
        tip_spots: List[TipSpot],
        probing_fn: Optional[TipPresenceProbingMethod] = None,
        use_channels: Optional[List[int]] = None,
    ) -> Dict[str, bool]:
        if self._simulator:
            return await self._simulate_handler.probe_tip_inventory(tip_spots, probing_fn, use_channels)
        return await super().probe_tip_inventory(tip_spots, probing_fn, use_channels)

    async def consolidate_tip_inventory(self, tip_racks: List[TipRack], use_channels: Optional[List[int]] = None):
        if self._simulator:
            return await self._simulate_handler.consolidate_tip_inventory(tip_racks, use_channels)
        return await super().consolidate_tip_inventory(tip_racks, use_channels)


class LiquidHandlerAbstract(LiquidHandlerMiddleware):
    """Extended LiquidHandler with additional operations."""

    support_touch_tip = True
    _ros_node: BaseROS2DeviceNode

    def __init__(
        self,
        backend: LiquidHandlerBackend,
        deck: Deck,
        simulator: bool = False,
        channel_num: int = 8,
        total_height: float = 310,
    ):
        """Initialize a LiquidHandler.

        Args:
          backend: Backend to use.
          deck: Deck to use.
        """
        backend_type = None
        if isinstance(backend, dict) and "type" in backend:
            backend_dict = backend.copy()
            type_str = backend_dict.pop("type")
            try:
                # Try to get class from string using globals (current module), or fallback to pylabrobot or unilabos namespaces
                backend_cls = None
                if type_str in globals():
                    backend_cls = globals()[type_str]
                else:
                    # Try resolving dotted notation, e.g. "xxx.yyy.ClassName"
                    components = type_str.split(".")
                    mod = None
                    if len(components) > 1:
                        module_name = ".".join(components[:-1])
                        try:
                            import importlib

                            mod = importlib.import_module(module_name)
                        except ImportError:
                            mod = None
                        if mod is not None:
                            backend_cls = getattr(mod, components[-1], None)
                    if backend_cls is None:
                        # Try pylabrobot style import (if available)
                        try:
                            import pylabrobot

                            backend_cls = getattr(pylabrobot, type_str, None)
                        except Exception:
                            backend_cls = None
                if backend_cls is not None and isinstance(backend_cls, type):
                    backend_type = backend_cls(**backend_dict)  # pass the rest of dict as kwargs
            except Exception as exc:
                raise RuntimeError(f"Failed to convert backend type '{type_str}' to class: {exc}")
        else:
            backend_type = backend
        self._simulator = simulator
        self.group_info = dict()
        super().__init__(backend_type, deck, simulator, channel_num)

    def post_init(self, ros_node: BaseROS2DeviceNode):
        self._ros_node = ros_node

    async def _resolve_to_plr_resources(
        self,
        items: Sequence[Union[Container, TipRack, Dict[str, Any]]],
    ) -> List[Union[Container, TipRack]]:
        """将 dict 格式的资源解析为 PLR 实例。若全部已是 PLR，直接返回。"""
        dict_items = [(i, x) for i, x in enumerate(items) if isinstance(x, dict)]
        if not dict_items:
            return list(items)
        if not hasattr(self, "_ros_node") or self._ros_node is None:
            raise ValueError(
                "传入 dict 格式的 sources/targets/tip_racks 时，需通过 post_init 注入 _ros_node，"
                "才能从物料系统按 uuid 解析为 PLR 资源。"
            )
        uuids = [x.get("uuid") or x.get("unilabos_uuid") for _, x in dict_items]
        if any(u is None for u in uuids):
            raise ValueError("dict 格式的资源必须包含 uuid 或 unilabos_uuid 字段")

        def _resolve_from_local_by_uuids() -> List[Union[Container, TipRack]]:
            resolved_locals: List[Union[Container, TipRack]] = []
            missing: List[str] = []
            for uid in uuids:
                matches = self._ros_node.resource_tracker.figure_resource({"uuid": uid}, try_mode=True)
                if matches:
                    resolved_locals.append(cast(Union[Container, TipRack], matches[0]))
                else:
                    missing.append(str(uid))
            if missing:
                raise ValueError(
                    f"远端资源树未返回且本地资源也未命中，缺失 UUID: {missing}"
                )
            return resolved_locals

        # 优先走远端资源树查询；若远端为空或 requested_uuids 无法解析，则降级到本地 tracker 按 UUID 解析。
        resolved = []
        try:
            resource_tree = await self._ros_node.get_resource(uuids)
            plr_list = resource_tree.to_plr_resources(requested_uuids=uuids)
            for uid, plr in zip(uuids, plr_list):
                local_matches = self._ros_node.resource_tracker.figure_resource({"uuid": uid}, try_mode=True)
                if local_matches:
                    local = cast(Union[Container, TipRack], local_matches[0])
                else:
                    local = cast(Union[Container, TipRack], plr)
                if hasattr(plr, "unilabos_extra") and hasattr(local, "unilabos_extra"):
                    local.unilabos_extra = getattr(plr, "unilabos_extra", {}).copy()
                resolved.append(local)
            if len(resolved) != len(uuids):
                raise ValueError(
                    f"远端资源解析数量不匹配: requested={len(uuids)}, resolved={len(resolved)}"
                )
        except Exception:
            resolved = _resolve_from_local_by_uuids()

        result = list(items)
        for (idx, _), plr in zip(dict_items, resolved):
            result[idx] = plr
        return result

    @classmethod
    def set_liquid(cls, wells: list[Well], liquid_names: list[str], volumes: list[float]) -> SetLiquidReturn:
        """Set the liquid in a well.

        如果 liquid_names 和 volumes 为空，但 wells 不为空，直接返回 wells。
        """
        res_volumes = []
        # 如果 liquid_names 和 volumes 都为空，直接返回 wells
        if not liquid_names and not volumes:
            return SetLiquidReturn(
                wells=ResourceTreeSet.from_plr_resources(wells, known_newly_created=False).dump(), volumes=res_volumes  # type: ignore
            )

        def _clamp_volume(resource: Union[Well, Container], volume: float) -> float:
            # 防止初始化液量超过容器容量，导致后续 dispense 时 free volume 为负
            clamped = max(float(volume), 0.0)
            max_volume = getattr(resource, "max_volume", None)
            if isinstance(max_volume, (int, float)) and max_volume > 0:
                clamped = min(clamped, float(max_volume))
            return clamped

        for well, liquid_name, volume in zip(wells, liquid_names, volumes):
            safe_volume = _clamp_volume(well, volume)
            well.set_liquids([(liquid_name, safe_volume)])  # type: ignore
            res_volumes.append(safe_volume)

        return SetLiquidReturn(
            wells=ResourceTreeSet.from_plr_resources(wells, known_newly_created=False).dump(), volumes=res_volumes  # type: ignore
        )

    def set_liquid_from_plate(
        self, plate: ResourceSlot, well_names: list[str], liquid_names: list[str], volumes: list[float]
    ) -> SetLiquidFromPlateReturn:
        """Set the liquid in wells of a plate by well names (e.g., A1, A2, B3).

        如果 liquid_names 和 volumes 为空，但 plate 和 well_names 不为空，直接返回 plate 和 wells。
        """
        assert issubclass(plate.__class__, Plate) or issubclass(plate.__class__, TubeRack) , f"plate must be a Plate, now: {type(plate)}"
        plate: Union[Plate, TubeRack]
        # 根据 well_names 获取对应的 Well 对象
        if issubclass(plate.__class__, Plate):
            wells = [plate.get_well(name) for name in well_names]
        elif issubclass(plate.__class__, TubeRack):
            wells = [plate.get_tube(name) for name in well_names]
        res_volumes = []

        # 如果 liquid_names 和 volumes 都为空，直接返回
        if not liquid_names and not volumes:
            return SetLiquidFromPlateReturn(
                plate=ResourceTreeSet.from_plr_resources([plate], known_newly_created=False).dump(),  # type: ignore
                wells=ResourceTreeSet.from_plr_resources(wells, known_newly_created=False).dump(),  # type: ignore
                volumes=res_volumes,
            )

        def _clamp_volume(resource: Union[Well, Container], volume: float) -> float:
            # 防止初始化液量超过容器容量，导致后续 dispense 时 free volume 为负
            clamped = max(float(volume), 0.0)
            max_volume = getattr(resource, "max_volume", None)
            if isinstance(max_volume, (int, float)) and max_volume > 0:
                clamped = min(clamped, float(max_volume))
            return clamped

        for well, liquid_name, volume in zip(wells, liquid_names, volumes):
            safe_volume = _clamp_volume(well, volume)
            well.set_liquids([(liquid_name, safe_volume)])  # type: ignore
            res_volumes.append(safe_volume)

        task = ROS2DeviceNode.run_async_func(self._ros_node.update_resource, True, **{"resources": wells})
        submit_time = time.time()
        while not task.done():
            if time.time() - submit_time > 10:
                self._ros_node.lab_logger().info(f"set_liquid_from_plate {plate} 超时")
                break
            time.sleep(0.01)

        return SetLiquidFromPlateReturn(
            plate=ResourceTreeSet.from_plr_resources([plate], known_newly_created=False).dump(),  # type: ignore
            wells=ResourceTreeSet.from_plr_resources(wells, known_newly_created=False).dump(),  # type: ignore
            volumes=res_volumes,
        )

    # ---------------------------------------------------------------
    # REMOVE LIQUID --------------------------------------------------
    # ---------------------------------------------------------------

    def set_group(self, group_name: str, wells: List[Well], volumes: List[float]):
        if self.channel_num == 8 and len(wells) != 8:
            raise RuntimeError(f"Expected 8 wells, got {len(wells)}")
        self.group_info[group_name] = wells
        self.set_liquid(wells, [group_name] * len(wells), volumes)

    async def transfer_group(self, source_group_name: str, target_group_name: str, unit_volume: float):

        source_wells = self.group_info.get(source_group_name, [])
        target_wells = self.group_info.get(target_group_name, [])

        rack_info = dict()
        for child in self.deck.children:
            if issubclass(child.__class__, TipRack):
                rack: TipRack = cast(TipRack, child)
                if "plate" not in rack.name.lower():
                    for tip in rack.get_all_tips():
                        if unit_volume > tip.maximal_volume:
                            break
                        else:
                            rack_info[rack.name] = (rack, tip.maximal_volume - unit_volume)

        if len(rack_info) == 0:
            raise ValueError(f"No tip rack can support volume {unit_volume}.")

        rack_info = sorted(rack_info.items(), key=lambda x: x[1][1])
        for child in self.deck.children:
            if child.name == rack_info[0][0]:
                target_rack = child
        target_rack = cast(TipRack, target_rack)
        available_tips = {}
        for idx, tipSpot in enumerate(target_rack.get_all_items()):
            if tipSpot.has_tip():
                available_tips[idx] = tipSpot
                continue
        # 一般移动液体有两种方式，一对多和多对多
        print("channel_num", self.channel_num)
        if self.channel_num == 8:

            tip_prefix = list(available_tips.values())[0].name.split("_")[0]
            colnum_list = [int(tip.name.split("_")[-1][1:]) for tip in available_tips.values()]
            available_cols = [colnum for colnum, count in dict(Counter(colnum_list)).items() if count == 8]
            available_cols.sort()
            available_tips_dict = {tip.name: tip for tip in available_tips.values()}
            tips_to_use = [available_tips_dict[f"{tip_prefix}_{chr(65 + i)}{available_cols[0]}"] for i in range(8)]
            print("tips_to_use", tips_to_use)
            await self.pick_up_tips(tips_to_use, use_channels=list(range(0, 8)))
            print("source_wells", source_wells)
            await self.aspirate(source_wells, [unit_volume] * 8, use_channels=list(range(0, 8)))
            print("target_wells", target_wells)
            await self.dispense(target_wells, [unit_volume] * 8, use_channels=list(range(0, 8)))
            await self.discard_tips(use_channels=list(range(0, 8)))

        elif self.channel_num == 1:

            for num_well in range(len(target_wells)):
                tip_to_use = available_tips[list(available_tips.keys())[num_well]]
                print("tip_to_use", tip_to_use)
                await self.pick_up_tips([tip_to_use], use_channels=[0])
                print("source_wells", source_wells)
                print("target_wells", target_wells)
                if len(source_wells) == 1:
                    await self.aspirate([source_wells[0]], [unit_volume], use_channels=[0])
                else:
                    await self.aspirate([source_wells[num_well]], [unit_volume], use_channels=[0])
                await self.dispense([target_wells[num_well]], [unit_volume], use_channels=[0])
                await self.discard_tips(use_channels=[0])

        else:
            raise ValueError(f"Unsupported channel number {self.channel_num}.")

    async def create_protocol(
        self,
        protocol_name: str,
        protocol_description: str,
        protocol_version: str,
        protocol_author: str,
        protocol_date: str,
        protocol_type: str,
        none_keys: List[str] = [],
    ):
        """Create a new protocol with the given metadata."""
        pass

    async def remove_liquid(
        self,
        vols: List[float],
        sources: Sequence[Container],
        waste_liquid: Optional[Container] = None,
        *,
        use_channels: Optional[List[int]] = None,
        flow_rates: Optional[List[Optional[float]]] = None,
        offsets: Optional[List[Coordinate]] = None,
        liquid_height: Optional[List[Optional[float]]] = None,
        blow_out_air_volume: Optional[List[Optional[float]]] = None,
        spread: Optional[Literal["wide", "tight", "custom"]] = "wide",
        delays: Optional[List[int]] = None,
        is_96_well: Optional[bool] = False,
        top: Optional[List[float]] = None,
        none_keys: List[str] = [],
    ):
        """A complete *remove* (aspirate → waste) operation."""

        try:
            if is_96_well:
                pass  # This mode is not verified.
            else:
                # 首先应该对任务分组，然后每次1个/8个进行操作处理
                if len(use_channels) == 1 and self.backend.num_channels == 1:

                    for _ in range(len(sources)):
                        tip = []
                        for __ in range(len(use_channels)):
                            tip.extend(self._get_next_tip())
                        await self.pick_up_tips(tip)
                        await self.aspirate(
                            resources=[sources[_]],
                            vols=[vols[_]],
                            use_channels=use_channels,
                            flow_rates=[flow_rates[0]] if flow_rates else None,
                            offsets=[offsets[0]] if offsets else None,
                            liquid_height=[liquid_height[0]] if liquid_height else None,
                            blow_out_air_volume=[blow_out_air_volume[0]] if blow_out_air_volume else None,
                            spread=spread,
                        )
                        if delays is not None:
                            await self.custom_delay(seconds=delays[0])

                        await self.dispense(
                            resources=[waste_liquid],
                            vols=[vols[_]],
                            use_channels=use_channels,
                            flow_rates=[flow_rates[1]] if flow_rates else None,
                            offsets=[offsets[1]] if offsets else None,
                            blow_out_air_volume=[blow_out_air_volume[1]] if blow_out_air_volume else None,
                            liquid_height=[liquid_height[1]] if liquid_height else None,
                            spread=spread,
                        )
                        await self.discard_tips()

                elif len(use_channels) == 8 and self.backend.num_channels == 8:

                    # 对于8个的情况，需要判断此时任务是不是能被8通道移液站来成功处理
                    if len(sources) % 8 != 0:
                        raise ValueError(
                            f"Length of `sources` {len(sources)} must be a multiple of 8 for 8-channel mode."
                        )

                    # 8个8个来取任务序列

                    for i in range(0, len(sources), 8):
                        tip = []
                        for _ in range(len(use_channels)):
                            tip.extend(self._get_next_tip())
                        await self.pick_up_tips(tip)
                        current_targets = waste_liquid[i : i + 8]
                        current_reagent_sources = sources[i : i + 8]
                        current_asp_vols = vols[i : i + 8]
                        current_dis_vols = vols[i : i + 8]
                        current_asp_flow_rates = flow_rates[i : i + 8] if flow_rates else [None] * 8
                        current_dis_flow_rates = (
                            flow_rates[-i * 8 - 8 : len(flow_rates) - i * 8] if flow_rates else [None] * 8
                        )
                        current_asp_offset = offsets[i : i + 8] if offsets else [None] * 8
                        current_dis_offset = offsets[-i * 8 - 8 : len(offsets) - i * 8] if offsets else [None] * 8
                        current_asp_liquid_height = liquid_height[i : i + 8] if liquid_height else [None] * 8
                        current_dis_liquid_height = (
                            liquid_height[-i * 8 - 8 : len(liquid_height) - i * 8] if liquid_height else [None] * 8
                        )
                        current_asp_blow_out_air_volume = (
                            blow_out_air_volume[i : i + 8] if blow_out_air_volume else [None] * 8
                        )
                        current_dis_blow_out_air_volume = (
                            blow_out_air_volume[-i * 8 - 8 : len(blow_out_air_volume) - i * 8]
                            if blow_out_air_volume
                            else [None] * 8
                        )

                        await self.aspirate(
                            resources=current_reagent_sources,
                            vols=current_asp_vols,
                            use_channels=use_channels,
                            flow_rates=current_asp_flow_rates,
                            offsets=current_asp_offset,
                            liquid_height=current_asp_liquid_height,
                            blow_out_air_volume=current_asp_blow_out_air_volume,
                            spread=spread,
                        )
                        if delays is not None:
                            await self.custom_delay(seconds=delays[0])
                        await self.dispense(
                            resources=current_targets,
                            vols=current_dis_vols,
                            use_channels=use_channels,
                            flow_rates=current_dis_flow_rates,
                            offsets=current_dis_offset,
                            liquid_height=current_dis_liquid_height,
                            blow_out_air_volume=current_dis_blow_out_air_volume,
                            spread=spread,
                        )
                        if delays is not None and len(delays) > 1:
                            await self.custom_delay(seconds=delays[1])
                        await self.touch_tip(current_targets)
                        await self.discard_tips()

        except Exception as e:
            traceback.print_exc()
            raise RuntimeError(f"Liquid addition failed: {e}") from e

    # ---------------------------------------------------------------
    # ADD LIQUID -----------------------------------------------------
    # ---------------------------------------------------------------

    async def add_liquid(
        self,
        asp_vols: Union[List[float], float],
        dis_vols: Union[List[float], float],
        reagent_sources: Sequence[Container],
        targets: Sequence[Container],
        *,
        use_channels: Optional[List[int]] = None,
        flow_rates: Optional[List[Optional[float]]] = None,
        offsets: Optional[List[Coordinate]] = None,
        liquid_height: Optional[List[Optional[float]]] = None,
        blow_out_air_volume: Optional[List[Optional[float]]] = None,
        spread: Optional[Literal["wide", "tight", "custom"]] = "wide",
        is_96_well: bool = False,
        delays: Optional[List[int]] = None,
        mix_time: Optional[int] = None,
        mix_vol: Optional[int] = None,
        mix_rate: Optional[int] = None,
        mix_liquid_height: Optional[float] = None,
        none_keys: List[str] = [],
    ):
        # """A complete *add* (aspirate reagent → dispense into targets) operation."""

        # # try:
        if is_96_well:
            pass  # This mode is not verified.
        else:
            if len(asp_vols) != len(targets):
                raise ValueError(f"Length of `asp_vols` {len(asp_vols)} must match `targets` {len(targets)}.")
            # 首先应该对任务分组，然后每次1个/8个进行操作处理
            if len(use_channels) == 1:
                for _ in range(len(targets)):
                    tip = []
                    for x in range(len(use_channels)):
                        tip.extend(self._get_next_tip())
                    await self.pick_up_tips(tip)

                    await self.aspirate(
                        resources=[reagent_sources[_]],
                        vols=[asp_vols[_]],
                        use_channels=use_channels,
                        flow_rates=[flow_rates[0]] if flow_rates else None,
                        offsets=[offsets[0]] if offsets else None,
                        liquid_height=[liquid_height[0]] if liquid_height else None,
                        blow_out_air_volume=[blow_out_air_volume[0]] if blow_out_air_volume else None,
                        spread=spread,
                    )

                    if delays is not None:
                        await self.custom_delay(seconds=delays[0])
                    await self.dispense(
                        resources=[targets[_]],
                        vols=[dis_vols[_]],
                        use_channels=use_channels,
                        flow_rates=[flow_rates[1]] if flow_rates else None,
                        offsets=[offsets[1]] if offsets else None,
                        blow_out_air_volume=[blow_out_air_volume[1]] if blow_out_air_volume else None,
                        liquid_height=[liquid_height[1]] if liquid_height else None,
                        spread=spread,
                    )

                    if delays is not None and len(delays) > 1:
                        await self.custom_delay(seconds=delays[1])
                    # 只有在 mix_time 有效时才调用 mix
                    if mix_time is not None and mix_time > 0:
                        await self.mix(
                            targets=[targets[_]],
                            mix_time=mix_time,
                            mix_vol=mix_vol,
                            offsets=offsets if offsets else None,
                            height_to_bottom=mix_liquid_height if mix_liquid_height else None,
                            mix_rate=mix_rate if mix_rate else None,
                        )
                    if delays is not None and len(delays) > 1:
                        await self.custom_delay(seconds=delays[1])
                    await self.touch_tip(targets[_])
                    await self.discard_tips()

            elif len(use_channels) == 8:
                # 对于8个的情况，需要判断此时任务是不是能被8通道移液站来成功处理
                if len(targets) % 8 != 0:
                    raise ValueError(f"Length of `targets` {len(targets)} must be a multiple of 8 for 8-channel mode.")

                for i in range(0, len(targets), 8):
                    tip = []
                    for _ in range(len(use_channels)):
                        tip.extend(self._get_next_tip())
                    await self.pick_up_tips(tip)
                    current_targets = targets[i : i + 8]
                    current_reagent_sources = reagent_sources[i : i + 8]
                    current_asp_vols = asp_vols[i : i + 8]
                    current_dis_vols = dis_vols[i : i + 8]
                    current_asp_flow_rates = flow_rates[i : i + 8] if flow_rates else [None] * 8
                    current_dis_flow_rates = (
                        flow_rates[-i * 8 - 8 : len(flow_rates) - i * 8] if flow_rates else [None] * 8
                    )
                    current_asp_offset = offsets[i : i + 8] if offsets else [None] * 8
                    current_dis_offset = offsets[-i * 8 - 8 : len(offsets) - i * 8] if offsets else [None] * 8
                    current_asp_liquid_height = liquid_height[i : i + 8] if liquid_height else [None] * 8
                    current_dis_liquid_height = (
                        liquid_height[-i * 8 - 8 : len(liquid_height) - i * 8] if liquid_height else [None] * 8
                    )
                    current_asp_blow_out_air_volume = (
                        blow_out_air_volume[i : i + 8] if blow_out_air_volume else [None] * 8
                    )
                    current_dis_blow_out_air_volume = (
                        blow_out_air_volume[-i * 8 - 8 : len(blow_out_air_volume) - i * 8]
                        if blow_out_air_volume
                        else [None] * 8
                    )

                    await self.aspirate(
                        resources=current_reagent_sources,
                        vols=current_asp_vols,
                        use_channels=use_channels,
                        flow_rates=current_asp_flow_rates,
                        offsets=current_asp_offset,
                        liquid_height=current_asp_liquid_height,
                        blow_out_air_volume=current_asp_blow_out_air_volume,
                        spread=spread,
                    )
                    if delays is not None:
                        await self.custom_delay(seconds=delays[0])
                    await self.dispense(
                        resources=current_targets,
                        vols=current_dis_vols,
                        use_channels=use_channels,
                        flow_rates=current_dis_flow_rates,
                        offsets=current_dis_offset,
                        liquid_height=current_dis_liquid_height,
                        blow_out_air_volume=current_dis_blow_out_air_volume,
                        spread=spread,
                    )
                    if delays is not None and len(delays) > 1:
                        await self.custom_delay(seconds=delays[1])

                    # 只有在 mix_time 有效时才调用 mix
                    if mix_time is not None and mix_time > 0:
                        await self.mix(
                            targets=current_targets,
                            mix_time=mix_time,
                            mix_vol=mix_vol,
                            offsets=offsets if offsets else None,
                            height_to_bottom=mix_liquid_height if mix_liquid_height else None,
                            mix_rate=mix_rate if mix_rate else None,
                        )
                    if delays is not None and len(delays) > 1:
                        await self.custom_delay(seconds=delays[1])
                    await self.touch_tip(current_targets)
                    await self.discard_tips()

    # except Exception as e:
    #     traceback.print_exc()
    #     raise RuntimeError(f"Liquid addition failed: {e}") from e

    # ---------------------------------------------------------------
    # TRANSFER LIQUID ------------------------------------------------
    # ---------------------------------------------------------------
    async def transfer_liquid(
        self,
        sources: Sequence[Union[Container, Dict[str, Any]]],
        targets: Sequence[Union[Container, Dict[str, Any]]],
        tip_racks: Sequence[Union[TipRack, Dict[str, Any]]],
        *,
        use_channels: Optional[List[int]] = None,
        asp_vols: Union[List[float], float],
        dis_vols: Union[List[float], float],
        asp_flow_rates: Optional[List[Optional[float]]] = None,
        dis_flow_rates: Optional[List[Optional[float]]] = None,
        offsets: Optional[List[Coordinate]] = None,
        touch_tip: bool = False,
        liquid_height: Optional[List[Optional[float]]] = None,
        blow_out_air_volume: Optional[List[Optional[float]]] = None,
        blow_out_air_volume_before: Optional[List[Optional[float]]] = None,
        spread: Literal["wide", "tight", "custom"] = "wide",
        is_96_well: bool = False,
        mix_stage: Optional[Literal["none", "before", "after", "both"]] = "none",
        mix_times: Optional[int] = None,
        mix_vol: Optional[int] = None,
        mix_rate: Optional[int] = None,
        mix_liquid_height: Optional[float] = None,
        delays: Optional[List[int]] = None,
        none_keys: List[str] = [],
    ) -> TransferLiquidReturn:
        """Transfer liquid with automatic mode detection.

        Supports three transfer modes:
        1. One-to-many (1 source -> N targets): Distribute from one source to multiple targets
        2. One-to-one (N sources -> N targets): Standard transfer, each source to corresponding target
        3. Many-to-one (N sources -> 1 target): Combine multiple sources into one target

        Parameters
        ----------
        asp_vols, dis_vols
            Single volume (µL) or list. Automatically expanded based on transfer mode.
        sources, targets
            Containers (wells or plates)，可为 PLR 实例或 dict（含 uuid 字段，将自动解析）。
            Length determines transfer mode:
            - len(sources) == 1, len(targets) > 1: One-to-many mode
            - len(sources) == len(targets): One-to-one mode
            - len(sources) > 1, len(targets) == 1: Many-to-one mode
        tip_racks
            One or more TipRacks（可为 PLR 实例或含 uuid 的 dict）providing fresh tips.
        is_96_well
            Set *True* to use the 96‑channel head.
        mix_stage
            When to mix the target wells relative to dispensing. Default "none" means
            no mixing occurs even if mix_times is provided. Use "before", "after", or
            "both" to mix at the corresponding stage(s).
        mix_times
            Number of mix cycles. If *None* (default) no mixing occurs regardless of
            mix_stage.
        """
        # 若传入 dict（含 uuid），解析为 PLR Container/TipRack
        sources = await self._resolve_to_plr_resources(sources)
        targets = await self._resolve_to_plr_resources(targets)
        tip_racks = list(await self._resolve_to_plr_resources(tip_racks))
        num_sources = len(sources)
        num_targets = len(targets)
        len_asp_vols = len(asp_vols)
        len_dis_vols = len(dis_vols)
        # 确保 use_channels 有默认值
        if use_channels is None or len(use_channels) == 0:
            # 默认使用设备所有通道（例如 8 通道移液站默认就是 0-7）
            use_channels = list(range(self.channel_num)) if self.channel_num == 8 else [0]
        elif len(use_channels) == 8:
            if self.channel_num != 8:
                raise ValueError(f"if channel_num is 8, use_channels length must be 8, but got {len(use_channels)}")
            if num_sources%8 != 0 or num_targets%8 != 0 or len_asp_vols%8 != 0 or len_dis_vols%8 != 0:
                raise ValueError(f"if channel_num is 8, sources, targets, asp_vols, and dis_vols length must be divisible by 8, but got {num_sources}, {num_targets}, {len_asp_vols}, and {len_dis_vols}")

        if is_96_well:
            pass  # This mode is not verified.
        else:
            # 转换体积参数为列表
            if isinstance(asp_vols, (int, float)):
                asp_vols = [float(asp_vols)]
            else:
                asp_vols = [float(v) for v in asp_vols]

            if isinstance(dis_vols, (int, float)):
                dis_vols = [float(dis_vols)]
            else:
                dis_vols = [float(v) for v in dis_vols]

        # 统一混合次数为标量，防止数组/列表与 int 比较时报错
        if mix_times is not None and not isinstance(mix_times, (int, float)):
            try:
                mix_times = mix_times[0] if len(mix_times) > 0 else None
            except Exception:
                try:
                    mix_times = next(iter(mix_times))
                except Exception:
                    pass
        if mix_times is not None:
            mix_times = int(mix_times)

        # 设置tip racks
        self.set_tiprack(tip_racks)

        # 识别传输模式（mix_times 为 None 也应该能正常移液，只是不做 mix）
        num_sources = len(sources)
        num_targets = len(targets)
        len_asp_vols = len(asp_vols)
        len_dis_vols = len(dis_vols)

        # if num_targets != 1 and num_sources != 1:
        #     if len_asp_vols != num_sources and len_asp_vols != num_targets:
        #         raise ValueError(f"asp_vols length must be equal to sources or targets length, but got {len_asp_vols} and {num_sources} and {num_targets}")
        #     if len_dis_vols != num_sources and len_dis_vols != num_targets:
        #         raise ValueError(f"dis_vols length must be equal to sources or targets length, but got {len_dis_vols} and {num_sources} and {num_targets}")

        if len(use_channels) != 8:
            max_len = max(num_sources, num_targets)
            for i in range(max_len):

                # 辅助函数：
                # - wrap=True: 返回 [value]（用于 liquid_height 等列表参数）
                # - wrap=False: 返回 value（用于 mix_* 标量参数）
                def safe_get(value, idx, default=None, wrap: bool = True):
                    if value is None:
                        return default
                    try:
                        if isinstance(value, (list, tuple)):
                            if len(value) == 0:
                                return default
                            item = value[idx % len(value)]
                        else:
                            item = value
                        return [item] if wrap else item
                    except Exception:
                        return default

                # 动态构建参数字典，只传递实际提供的参数
                kwargs = {
                    'sources': [sources[i%num_sources]],
                    'targets': [targets[i%num_targets]],
                    'tip_racks': tip_racks,
                    'use_channels': use_channels,
                    'asp_vols': [asp_vols[i%len_asp_vols]],
                    'dis_vols': [dis_vols[i%len_dis_vols]],
                }

                # 条件性添加可选参数
                if asp_flow_rates is not None:
                    kwargs['asp_flow_rates'] = [asp_flow_rates[i%len_asp_vols]]
                if dis_flow_rates is not None:
                    kwargs['dis_flow_rates'] = [dis_flow_rates[i%len_dis_vols]]
                if offsets is not None:
                    kwargs['offsets'] = safe_get(offsets, i)
                if touch_tip is not None:
                    kwargs['touch_tip'] = touch_tip if touch_tip else False
                if liquid_height is not None:
                    kwargs['liquid_height'] = safe_get(liquid_height, i)
                if blow_out_air_volume is not None:
                    kwargs['blow_out_air_volume'] = safe_get(blow_out_air_volume, i)
                if blow_out_air_volume_before is not None:
                    kwargs['blow_out_air_volume_before'] = safe_get(blow_out_air_volume_before, i)
                if spread is not None:
                    kwargs['spread'] = spread
                if mix_stage is not None:
                    kwargs['mix_stage'] = safe_get(mix_stage, i, wrap=False)
                if mix_times is not None:
                    kwargs['mix_times'] = safe_get(mix_times, i, wrap=False)
                if mix_vol is not None:
                    kwargs['mix_vol'] = safe_get(mix_vol, i, wrap=False)
                if mix_rate is not None:
                    kwargs['mix_rate'] = safe_get(mix_rate, i, wrap=False)
                if mix_liquid_height is not None:
                    kwargs['mix_liquid_height'] = safe_get(mix_liquid_height, i, wrap=False)
                if delays is not None:
                    kwargs['delays'] = safe_get(delays, i)

                await self._transfer_base_method(**kwargs)


        return TransferLiquidReturn(
            sources=ResourceTreeSet.from_plr_resources(list(sources), known_newly_created=False).dump(),  # type: ignore
            targets=ResourceTreeSet.from_plr_resources(list(targets), known_newly_created=False).dump(),  # type: ignore
        )
    async def _transfer_base_method(
        self,
        sources: Sequence[Container],
        targets: Sequence[Container],
        tip_racks: Sequence[TipRack],
        use_channels: List[int],
        asp_vols: List[float],
        dis_vols: List[float],
        **kwargs
    ):

        # 从kwargs中提取参数，提供默认值
        asp_flow_rates = kwargs.get('asp_flow_rates')
        dis_flow_rates = kwargs.get('dis_flow_rates')
        offsets = kwargs.get('offsets')
        touch_tip = kwargs.get('touch_tip', False)
        liquid_height = kwargs.get('liquid_height')
        blow_out_air_volume = kwargs.get('blow_out_air_volume')
        blow_out_air_volume_before = kwargs.get('blow_out_air_volume_before')
        spread = kwargs.get('spread', 'wide')
        mix_stage = kwargs.get('mix_stage')
        mix_times = kwargs.get('mix_times')
        mix_vol = kwargs.get('mix_vol')
        mix_rate = kwargs.get('mix_rate')
        mix_liquid_height = kwargs.get('mix_liquid_height')
        delays = kwargs.get('delays')

        tip = []
        tip.extend(self._get_next_tip())
        await self.pick_up_tips(tip)
        blow_out_air_volume_before_vol = 0.0
        if blow_out_air_volume_before is not None and len(blow_out_air_volume_before) > 0:
            blow_out_air_volume_before_vol = float(blow_out_air_volume_before[0] or 0.0)
        blow_out_air_volume_vol = 0.0
        if blow_out_air_volume is not None and len(blow_out_air_volume) > 0:
            blow_out_air_volume_vol = float(blow_out_air_volume[0] or 0.0)
        # PLR 的 blow_out_air_volume 是空气参数，不计入液体体积。
        # before 空气通过单独预吸实现，after 空气通过 blow_out_air_volume 参数实现。

        if mix_stage in ["before", "both"] and mix_times is not None and mix_times > 0:
            await self.mix(
                targets=[targets[0]],
                mix_time=mix_times,
                mix_vol=mix_vol,
                offsets=offsets if offsets else None,
                height_to_bottom=mix_liquid_height if mix_liquid_height else None,
                mix_rate=mix_rate if mix_rate else None,
                use_channels=use_channels,
            )

        if blow_out_air_volume_before_vol > 0:
            source_tracker = getattr(sources[0], "tracker", None)
            source_tracker_was_disabled = bool(getattr(source_tracker, "is_disabled", False))
            try:
                if source_tracker is not None and hasattr(source_tracker, "disable"):
                    source_tracker.disable()
                await self.aspirate(
                    resources=[sources[0]],
                    vols=[blow_out_air_volume_before_vol],
                    use_channels=use_channels,
                    flow_rates=None,
                    offsets=[Coordinate(x=0, y=0, z=sources[0].get_size_z())],
                    liquid_height=None,
                    blow_out_air_volume=None,
                    spread="custom",
                )
            finally:
                if source_tracker is not None:
                    source_tracker.enable()

        await self.aspirate(
            resources=[sources[0]],
            vols=[asp_vols[0]],
            use_channels=use_channels,
            flow_rates=[asp_flow_rates[0]] if asp_flow_rates and len(asp_flow_rates) > 0 else None,
            offsets=[offsets[0]] if offsets and len(offsets) > 0 else None,
            liquid_height=[liquid_height[0]] if liquid_height and len(liquid_height) > 0 else None,
            blow_out_air_volume=(
                [blow_out_air_volume_vol] if blow_out_air_volume_vol > 0 else None
            ),
            spread=spread,
        )
        if delays is not None:
            await self.custom_delay(seconds=delays[0])
        await self.dispense(
            resources=[targets[0]],
            vols=[dis_vols[0]],
            use_channels=use_channels,
            flow_rates=[dis_flow_rates[0]] if dis_flow_rates and len(dis_flow_rates) > 0 else None,
            offsets=[offsets[0]] if offsets and len(offsets) > 0 else None,
            blow_out_air_volume=(
                [blow_out_air_volume_vol] if blow_out_air_volume_vol > 0 else None
            ),
            liquid_height=[liquid_height[0]] if liquid_height and len(liquid_height) > 0 else None,
            spread=spread,
        )
        if delays is not None and len(delays) > 1:
            await self.custom_delay(seconds=delays[1])
        if mix_stage in ["after", "both"] and mix_times is not None and mix_times > 0:
            await self.mix(
                targets=[targets[0]],
                mix_time=mix_times,
                mix_vol=mix_vol,
                offsets=offsets if offsets else None,
                height_to_bottom=mix_liquid_height if mix_liquid_height else None,
                mix_rate=mix_rate if mix_rate else None,
                use_channels=use_channels,
            )
        if delays is not None and len(delays) > 1:
            await self.custom_delay(seconds=delays[0])
        await self.touch_tip(targets[0])
        await self.discard_tips(use_channels=use_channels)

    # except Exception as e:
    #     traceback.print_exc()
    #     raise RuntimeError(f"Liquid addition failed: {e}") from e

    # ---------------------------------------------------------------
    # Helper utilities
    # ---------------------------------------------------------------

    async def custom_delay(self, seconds=0, msg=None):
        """
        seconds: seconds to wait
        msg: information to be printed
        """
        if seconds != None and seconds > 0:
            if msg:
                print(f"Waiting time: {msg}")
                print(f"Current time: {time.strftime('%H:%M:%S')}")
                print(f"Time to finish: {time.strftime('%H:%M:%S', time.localtime(time.time() + seconds))}")
            # Use ROS node sleep if available, otherwise use asyncio.sleep
            if hasattr(self, '_ros_node') and self._ros_node is not None:
                await self._ros_node.sleep(seconds)
            else:
                import asyncio
                await asyncio.sleep(seconds)
            if msg:
                print(f"Done: {msg}")
                print(f"Current time: {time.strftime('%H:%M:%S')}")

    async def touch_tip(self, targets: Sequence[Container]):
        """Touch the tip to the side of the well."""

        if not self.support_touch_tip:
            return
        await self.aspirate(
            resources=[targets],
            vols=[0],
            use_channels=None,
            flow_rates=None,
            offsets=[Coordinate(x=-targets.get_size_x() / 2, y=0, z=0)],
            liquid_height=None,
            blow_out_air_volume=None,
        )
        # await self.custom_delay(seconds=1) # In the simulation, we do not need to wait
        await self.aspirate(
            resources=[targets],
            vols=[0],
            use_channels=None,
            flow_rates=None,
            offsets=[Coordinate(x=targets.get_size_x() / 2, y=0, z=0)],
            liquid_height=None,
            blow_out_air_volume=None,
        )

    async def mix(
        self,
        targets: Sequence[Container],
        mix_time: int = None,
        mix_vol: Optional[int] = None,
        height_to_bottom: Optional[float] = None,
        offsets: Optional[Coordinate] = None,
        mix_rate: Optional[float] = None,
        use_channels: Optional[List[int]] = None,
        none_keys: List[str] = [],
    ):
        if mix_time is None or mix_time <= 0:  # No mixing required
            return
        """Mix the liquid in the target wells."""
        if mix_vol is None:
            raise ValueError("`mix_vol` must be provided when `mix_time` is set.")

        targets_list: List[Container] = list(targets)
        if len(targets_list) == 0:
            return

        def _expand(value, count: int):
            if value is None:
                return [None] * count
            if isinstance(value, (list, tuple)):
                if len(value) != count:
                    raise ValueError("Length of per-target parameters must match targets.")
                return list(value)
            return [value] * count

        offsets_list = _expand(offsets, len(targets_list))
        heights_list = _expand(height_to_bottom, len(targets_list))
        rates_list = _expand(mix_rate, len(targets_list))

        for _ in range(mix_time):
            for idx, target in enumerate(targets_list):
                offset_arg = (
                    [offsets_list[idx]] if offsets_list[idx] is not None else None
                )
                height_arg = (
                    [heights_list[idx]] if heights_list[idx] is not None else None
                )
                rate_arg = [rates_list[idx]] if rates_list[idx] is not None else None

                await self.aspirate(
                    resources=[target],
                    vols=[mix_vol],
                    use_channels=use_channels,
                    flow_rates=rate_arg,
                    offsets=offset_arg,
                    liquid_height=height_arg,
                )
                await self.custom_delay(seconds=1)
                await self.dispense(
                    resources=[target],
                    vols=[mix_vol],
                    use_channels=use_channels,
                    flow_rates=rate_arg,
                    offsets=offset_arg,
                    liquid_height=height_arg,
                )

    def iter_tips(self, tip_racks: Sequence[TipRack]) -> Iterator[Resource]:
        """Yield tips from a list of TipRacks one-by-one until depleted."""
        for rack in tip_racks:
            for tip in rack:
                yield tip
        # raise RuntimeError("Out of tips!")

    def _get_next_tip(self):
        """从 current_tip 迭代器获取下一个 tip，耗尽时抛出明确错误而非 StopIteration"""
        try:
            return next(self.current_tip)
        except StopIteration as e:
            raise RuntimeError("Tip rack exhausted: no more tips available for transfer") from e

    def set_tiprack(self, tip_racks: Sequence[TipRack]):
        """Set the tip racks for the liquid handler."""

        self.tip_racks = tip_racks
        tip_iter = self.iter_tips(tip_racks)
        self.current_tip = tip_iter

    async def move_to(self, well: Well, dis_to_top: float = 0, channel: int = 0):
        """
        Move a single channel to a specific well with a given z-height.

        Parameters
        ----------
        well : Well
            The target well.
        dis_to_top : float
            Height in mm to move to relative to the well top.
        channel : int
            Pipetting channel to move (default: 0).
        """
        await self.prepare_for_manual_channel_operation(channel=channel)
        abs_loc = well.get_absolute_location()
        well_height = well.get_absolute_size_z()
        await self.move_channel_x(channel, abs_loc.x)
        await self.move_channel_y(channel, abs_loc.y)
        await self.move_channel_z(channel, abs_loc.z + well_height + dis_to_top)
