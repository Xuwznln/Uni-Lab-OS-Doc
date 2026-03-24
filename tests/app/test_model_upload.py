"""model_upload.py 单元测试（upload_device_model / download_model_from_oss）"""

import unittest
import tempfile
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from unilabos.app.model_upload import (
    upload_device_model,
    download_model_from_oss,
    _MODEL_EXTENSIONS,
)


class TestUploadDeviceModel(unittest.TestCase):
    """测试本地模型文件上传到 OSS"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.mock_client = MagicMock()

    def _create_model_files(self, subdir: str, filenames: list[str]):
        """在临时目录中创建设备模型文件"""
        model_dir = Path(self.tmp_dir) / "devices" / subdir
        model_dir.mkdir(parents=True, exist_ok=True)
        for name in filenames:
            p = model_dir / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("dummy content")
        return model_dir

    @patch("unilabos.app.model_upload._MESH_BASE_DIR")
    def test_upload_success(self, mock_base):
        """正常上传流程"""
        mock_base.__truediv__ = lambda self, x: Path(self.tmp_dir) / x
        # 直接 patch _MESH_BASE_DIR 为 Path(tmp_dir)
        with patch("unilabos.app.model_upload._MESH_BASE_DIR", Path(self.tmp_dir)):
            self._create_model_files("arm_slider", ["macro_device.xacro", "meshes/link1.stl"])

            self.mock_client.get_model_upload_urls.return_value = {
                "files": [
                    {"name": "macro_device.xacro", "upload_url": "https://oss.example.com/put1"},
                    {"name": "meshes/link1.stl", "upload_url": "https://oss.example.com/put2"},
                ]
            }
            self.mock_client.publish_model.return_value = {
                "path": "https://oss.example.com/arm_slider/macro_device.xacro"
            }

            with patch("unilabos.app.model_upload._put_upload") as mock_put:
                result = upload_device_model(
                    http_client=self.mock_client,
                    template_uuid="test-uuid",
                    mesh_name="arm_slider",
                    model_type="device",
                    version="1.0.0",
                )

            self.assertEqual(result, "https://oss.example.com/arm_slider/macro_device.xacro")
            self.mock_client.get_model_upload_urls.assert_called_once()
            self.mock_client.publish_model.assert_called_once()

    @patch("unilabos.app.model_upload._MESH_BASE_DIR")
    def test_upload_dir_not_exists(self, mock_base):
        """本地目录不存在时返回 None"""
        with patch("unilabos.app.model_upload._MESH_BASE_DIR", Path(self.tmp_dir)):
            result = upload_device_model(
                http_client=self.mock_client,
                template_uuid="test-uuid",
                mesh_name="nonexistent",
                model_type="device",
            )
        self.assertIsNone(result)

    @patch("unilabos.app.model_upload._MESH_BASE_DIR")
    def test_upload_no_valid_files(self, mock_base):
        """目录中无有效模型文件时返回 None"""
        with patch("unilabos.app.model_upload._MESH_BASE_DIR", Path(self.tmp_dir)):
            model_dir = Path(self.tmp_dir) / "devices" / "empty_model"
            model_dir.mkdir(parents=True, exist_ok=True)
            (model_dir / "readme.txt").write_text("not a model")

            result = upload_device_model(
                http_client=self.mock_client,
                template_uuid="test-uuid",
                mesh_name="empty_model",
                model_type="device",
            )
        self.assertIsNone(result)

    @patch("unilabos.app.model_upload._MESH_BASE_DIR")
    def test_upload_urls_failure(self, mock_base):
        """获取上传 URL 失败时返回 None"""
        with patch("unilabos.app.model_upload._MESH_BASE_DIR", Path(self.tmp_dir)):
            self._create_model_files("arm", ["device.xacro"])
            self.mock_client.get_model_upload_urls.return_value = None

            result = upload_device_model(
                http_client=self.mock_client,
                template_uuid="test-uuid",
                mesh_name="arm",
                model_type="device",
            )
        self.assertIsNone(result)


class TestDownloadModelFromOss(unittest.TestCase):
    """测试从 OSS 下载模型文件到本地"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def test_skip_no_mesh_name(self):
        """缺少 mesh 名称时跳过"""
        result = download_model_from_oss({"type": "device", "path": "https://x.com/a.xacro"})
        self.assertFalse(result)

    def test_skip_no_oss_path(self):
        """缺少 OSS path 时跳过"""
        result = download_model_from_oss({"mesh": "arm", "type": "device"})
        self.assertFalse(result)

    def test_skip_local_path(self):
        """非 https:// 路径时跳过"""
        result = download_model_from_oss({
            "mesh": "arm",
            "type": "device",
            "path": "file:///local/model.xacro",
        })
        self.assertFalse(result)

    def test_already_exists(self):
        """本地已有文件时跳过下载"""
        device_dir = Path(self.tmp_dir) / "devices" / "arm"
        device_dir.mkdir(parents=True, exist_ok=True)
        (device_dir / "model.xacro").write_text("existing")

        result = download_model_from_oss(
            {"mesh": "arm", "type": "device", "path": "https://oss.example.com/model.xacro"},
            mesh_base_dir=Path(self.tmp_dir),
        )
        self.assertTrue(result)

    @patch("unilabos.app.model_upload._download_file")
    def test_download_device(self, mock_download):
        """下载 device 模型到 devices/ 目录"""
        result = download_model_from_oss(
            {"mesh": "new_arm", "type": "device", "path": "https://oss.example.com/new_arm/macro_device.xacro"},
            mesh_base_dir=Path(self.tmp_dir),
        )
        self.assertTrue(result)
        mock_download.assert_called_once()
        call_args = mock_download.call_args
        self.assertIn("macro_device.xacro", str(call_args[0][1]))

    @patch("unilabos.app.model_upload._download_file")
    def test_download_resource(self, mock_download):
        """下载 resource 模型到 resources/ 目录"""
        result = download_model_from_oss(
            {
                "mesh": "plate_96/meshes/plate_96.stl",
                "type": "resource",
                "path": "https://oss.example.com/plate_96/modal.xacro",
            },
            mesh_base_dir=Path(self.tmp_dir),
        )
        self.assertTrue(result)
        target_dir = Path(self.tmp_dir) / "resources" / "plate_96"
        self.assertTrue(target_dir.exists())

    @patch("unilabos.app.model_upload._download_file")
    def test_download_with_children_mesh(self, mock_download):
        """下载包含 children_mesh 的模型"""
        result = download_model_from_oss(
            {
                "mesh": "tip_rack",
                "type": "device",
                "path": "https://oss.example.com/tip_rack/model.xacro",
                "children_mesh": {
                    "path": "https://oss.example.com/tip_rack/meshes/tip.stl",
                    "format": "stl",
                },
            },
            mesh_base_dir=Path(self.tmp_dir),
        )
        self.assertTrue(result)
        # 应调用两次：入口文件 + children_mesh
        self.assertEqual(mock_download.call_count, 2)

    @patch("unilabos.app.model_upload._download_file", side_effect=Exception("network error"))
    def test_download_failure_graceful(self, mock_download):
        """下载失败时返回 False（不抛异常）"""
        result = download_model_from_oss(
            {"mesh": "broken", "type": "device", "path": "https://oss.example.com/broken.xacro"},
            mesh_base_dir=Path(self.tmp_dir),
        )
        self.assertFalse(result)


class TestModelExtensions(unittest.TestCase):
    """测试支持的模型文件后缀集合"""

    def test_standard_extensions(self):
        """确认标准 3D 格式在支持列表中"""
        expected = {".stl", ".gltf", ".glb", ".xacro", ".urdf", ".obj", ".dae"}
        for ext in expected:
            self.assertIn(ext, _MODEL_EXTENSIONS, f"{ext} should be supported")

    def test_non_model_excluded(self):
        """非模型文件后缀不在列表中"""
        excluded = {".txt", ".json", ".py", ".png", ".jpg"}
        for ext in excluded:
            self.assertNotIn(ext, _MODEL_EXTENSIONS, f"{ext} should not be supported")


if __name__ == "__main__":
    unittest.main()
