import pytest

from libre_dictum.errors import CameraError
from libre_dictum.tracking.v4l2 import enumerate_cameras, resolve, stable_path


class TestEnumerating:
    def test_a_node_that_answers_no_ioctl_is_left_out(self, tmp_path):
        (tmp_path / "video0").write_bytes(b"")

        assert enumerate_cameras(tmp_path) == ()

    def test_a_name_that_is_not_a_numbered_node_is_left_out(self, tmp_path):
        (tmp_path / "video-by-some-other-name").write_bytes(b"")

        assert enumerate_cameras(tmp_path) == ()

    def test_a_directory_with_no_video_nodes_is_no_cameras(self, tmp_path):
        (tmp_path / "media0").write_bytes(b"")

        assert enumerate_cameras(tmp_path) == ()


class TestResolvingASpec:
    def test_nothing_configured_stays_nothing(self):
        assert resolve(None) is None

    def test_an_index_is_passed_through(self):
        assert resolve(1) == 1

    def test_a_name_is_passed_through(self):
        assert resolve("SOLOMON") == "SOLOMON"

    def test_a_stable_symlink_becomes_the_index_it_points_at_today(self, tmp_path):
        (tmp_path / "video3").write_bytes(b"")
        link = tmp_path / "usb-Image__i-tec_SOLOMON_300_MT368-001-video-index0"
        link.symlink_to(tmp_path / "video3")

        assert resolve(str(link)) == 3

    def test_a_device_path_becomes_its_own_index(self, tmp_path):
        (tmp_path / "video2").write_bytes(b"")

        assert resolve(str(tmp_path / "video2")) == 2

    def test_a_path_that_is_not_there_says_so_rather_than_guessing(self):
        with pytest.raises(CameraError, match="no such device node"):
            resolve("/dev/v4l/by-id/usb-a-camera-that-was-unplugged")


class TestTheStableName:
    """The reverse of resolving: which by-id name points at the chosen node."""

    def by_id(self, tmp_path, *names):
        nodes, links = tmp_path / "dev", tmp_path / "by-id"
        nodes.mkdir(exist_ok=True)
        links.mkdir(exist_ok=True)
        for name, index in names:
            (nodes / f"video{index}").write_bytes(b"")
            (links / name).symlink_to(nodes / f"video{index}")
        return links

    def test_it_finds_the_link_pointing_at_the_node(self, tmp_path):
        links = self.by_id(tmp_path, ("usb-a_camera-video-index0", 0))

        assert stable_path(0, links) == str(links / "usb-a_camera-video-index0")

    def test_it_does_not_offer_another_node_s_link(self, tmp_path):
        links = self.by_id(tmp_path, ("usb-a_camera-video-index0", 0))

        assert stable_path(1, links) is None

    def test_a_kernel_that_made_no_links_offers_none(self, tmp_path):
        assert stable_path(0, tmp_path / "nothing-here") is None
