import struct

import pytest

from libre_dictum.errors import CameraError
from libre_dictum.tracking.cameras import (
    CAP_DEVICE_CAPS,
    CAP_META_CAPTURE,
    CAP_VIDEO_CAPTURE,
    CAP_VIDEO_CAPTURE_MPLANE,
    CAPABILITY_STRUCT,
    QUERYCAP,
    CameraNode,
    candidates,
    decode_capability,
    describe,
    index_from_device_name,
)

CARD = "i-tec SOLOMON 300: i-tec SOLOMO"
BUS = "usb-0000:0c:00.3-3"


def frames(index: int, card: str = CARD, bus: str = BUS) -> CameraNode:
    return CameraNode(index=index, card=card, bus_info=bus, capture=True)


def metadata(index: int, card: str = CARD, bus: str = BUS) -> CameraNode:
    return CameraNode(index=index, card=card, bus_info=bus, capture=False, metadata=True)


def querycap(driver: str, card: str, bus: str, capabilities: int, device_caps: int) -> bytes:
    """One VIDIOC_QUERYCAP answer, as the ioctl hands it back."""
    return struct.pack(
        CAPABILITY_STRUCT,
        driver.encode(),
        card.encode(),
        bus.encode(),
        0x00060C00,
        capabilities,
        device_caps,
    )


class TestTheIoctlNumber:
    def test_it_is_the_number_the_kernel_headers_define(self):
        assert QUERYCAP == 0x80685600

    def test_the_struct_is_the_size_the_ioctl_returns(self):
        assert struct.calcsize(CAPABILITY_STRUCT) == 104


class TestDecoding:
    def test_a_capture_node_says_so(self):
        node = decode_capability(0, querycap("uvcvideo", CARD, BUS, CAP_DEVICE_CAPS, 0x04200001))

        assert node.card == CARD
        assert node.driver == "uvcvideo"
        assert node.bus_info == BUS
        assert node.capture and not node.metadata
        assert node.path == "/dev/video0"

    def test_the_metadata_half_of_the_same_camera_is_told_apart(self):
        whole_device = CAP_DEVICE_CAPS | CAP_VIDEO_CAPTURE | CAP_META_CAPTURE
        node = decode_capability(1, querycap("uvcvideo", CARD, BUS, whole_device, CAP_META_CAPTURE))

        assert node.metadata and not node.capture

    def test_a_driver_that_fills_in_no_device_caps_is_read_from_the_device_flags(self):
        node = decode_capability(0, querycap("old", CARD, BUS, CAP_VIDEO_CAPTURE, 0))

        assert node.capture

    def test_a_multi_planar_capture_node_counts_as_frames(self):
        node = decode_capability(
            0, querycap("uvcvideo", CARD, BUS, CAP_DEVICE_CAPS, CAP_VIDEO_CAPTURE_MPLANE)
        )

        assert node.capture

    def test_kernel_strings_lose_their_padding(self):
        node = decode_capability(0, querycap("uvcvideo", "short", "bus", CAP_DEVICE_CAPS, 1))

        assert node.card == "short" and node.driver == "uvcvideo"


class TestFindingOne:
    def test_the_metadata_half_is_never_offered(self):
        assert candidates([frames(0), metadata(1)], None) == (frames(0),)

    def test_every_camera_is_offered_when_none_is_named(self):
        assert candidates([frames(2), metadata(3), frames(0)], None) == (frames(0), frames(2))

    def test_a_node_of_unknown_capability_is_still_worth_trying(self):
        unknown = CameraNode(index=0)
        assert candidates([unknown], None) == (unknown,)

    def test_no_video_device_at_all_says_so(self):
        with pytest.raises(CameraError, match="no video devices at all"):
            candidates([], None)

    def test_a_camera_with_only_a_metadata_node_says_so(self):
        with pytest.raises(CameraError, match="hands out frames"):
            candidates([metadata(1)], None)


class TestNamingOne:
    def test_a_name_matches_part_of_the_card(self):
        assert candidates([frames(0), frames(1, card="Integrated Camera")], "SOLOMON") == (
            frames(0),
        )

    def test_matching_ignores_case(self):
        assert candidates([frames(0)], "solomon") == (frames(0),)

    def test_a_name_that_matches_both_halves_still_yields_the_frames(self):
        assert candidates([frames(0), metadata(1)], "SOLOMON") == (frames(0),)

    def test_a_bus_id_names_a_port_rather_than_a_camera(self):
        other = frames(2, bus="usb-0000:0c:00.3-4")
        assert candidates([frames(0), other], "0c:00.3-4") == (other,)

    def test_a_name_nothing_answers_to_lists_what_is_there(self):
        with pytest.raises(CameraError, match="no video device is called that.*SOLOMON"):
            candidates([frames(0)], "Integrated")


class TestPinningOne:
    def test_an_index_opens_exactly_that_node(self):
        assert candidates([frames(0), frames(1, card="Integrated Camera")], 1) == (
            frames(1, card="Integrated Camera"),
        )

    def test_an_index_pointing_at_the_metadata_half_names_the_mistake(self):
        with pytest.raises(CameraError, match="metadata, not its picture"):
            candidates([frames(0), metadata(1)], 1)

    def test_an_index_that_is_not_there_lists_what_is(self):
        with pytest.raises(CameraError, match="no video4.*video0"):
            candidates([frames(0)], 4)

    def test_a_pin_that_fails_says_how_to_stop_pinning(self):
        with pytest.raises(CameraError, match="Remove 'camera'"):
            candidates([frames(0), metadata(1)], 1)


class TestDescribing:
    def test_a_node_says_what_it_is(self):
        assert describe([frames(0), metadata(1)]) == (
            f"video0 ({CARD}, frames), video1 ({CARD}, metadata)"
        )

    def test_an_unnamed_node_is_still_described(self):
        assert describe([CameraNode(index=0)]) == "video0 (unnamed, frames)"


class TestDeviceNames:
    def test_the_index_is_the_number_in_the_name(self):
        assert index_from_device_name("video7") == 7

    @pytest.mark.parametrize("name", ["video", "media0", "v4l-subdev0", "video0x"])
    def test_anything_else_is_not_a_camera(self, name):
        with pytest.raises(CameraError, match="not a video device node"):
            index_from_device_name(name)
