import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ArtifactContractTests(unittest.TestCase):
    def test_manifest_identity_singleton_entrypoints_and_settings(self):
        manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(1, manifest["schemaVersion"])
        self.assertEqual("godhiraj.local-test-pulse", manifest["id"])
        self.assertEqual(("Local Test Pulse", "0.1.0"), (manifest["name"], manifest["version"]))
        self.assertEqual({"service", "bar-widget"}, set(manifest["kinds"]))
        self.assertTrue(manifest["keepLoaded"])
        self.assertEqual({"service": "Service.qml", "barWidget": "Panel.qml"}, manifest["entryPoints"])
        widget = manifest["barWidget"]
        self.assertFalse(widget["allowMultiple"])
        self.assertEqual({"reportPath": "", "format": "auto", "refreshSeconds": 60,
                          "staleSeconds": 86400}, widget["defaults"])
        settings = {item["key"]: item for item in widget["schema"]}
        self.assertEqual("path", settings["reportPath"]["type"])
        self.assertEqual(["auto", "junit", "pytest", "playwright"], settings["format"]["options"])
        self.assertEqual((30, 3600, 60), (settings["refreshSeconds"]["min"],
                                             settings["refreshSeconds"]["max"],
                                             settings["refreshSeconds"]["defaultValue"]))
        self.assertEqual((60, 604800, 86400), (settings["staleSeconds"]["min"],
                                              settings["staleSeconds"]["max"],
                                              settings["staleSeconds"]["defaultValue"]))

    def test_service_has_fixed_argv_bounds_discard_and_overlap_contract(self):
        source = (ROOT / "Service.qml").read_text(encoding="utf-8")
        self.assertEqual(1, source.count("Process {"))
        self.assertIn('["python3", localScriptPath(), "--report", String(report),', source)
        self.assertIn('"--format", String(format), "--stale-seconds", String(stale)]', source)
        self.assertNotIn("sh -c", source)
        self.assertNotIn("shell=true", source.replace(" ", "").lower())
        self.assertIn("outputLimit: 1048576", source)
        self.assertEqual(2, source.count("SplitParser {"))
        self.assertGreaterEqual(source.count('splitMarker: ""'), 2)
        self.assertIn("adapter.signal(9)", source)
        self.assertIn("if (scanActive || adapter.running) return", source)
        self.assertIn("stdoutBuffer = \"\"", source)
        self.assertIn("Deliberately discarded", source)
        self.assertIn("scanTimeout", source)
        self.assertIn("refreshPending", source)
        self.assertIn("configurationGeneration += 1", source)
        self.assertIn('errorState("configuration_changed"', source)
        self.assertIn("activeGeneration = configurationGeneration", source)
        self.assertIn("shouldPublishScan(root.activeGeneration", source)
        self.assertIn("if (exitCode !== 0)", source)

    def test_panel_resolves_shared_service_configures_it_and_uses_plain_text(self):
        source = (ROOT / "Panel.qml").read_text(encoding="utf-8")
        self.assertIn('moduleName: "godhiraj.local-test-pulse"', source)
        self.assertIn('serviceFor("godhiraj.local-test-pulse")', source)
        self.assertIn("configure(settings || {})", source)
        self.assertNotIn("Process {", source)
        self.assertNotIn("RichText", source)
        self.assertGreaterEqual(source.count("Text.PlainText"), 7)
        self.assertIn("Within-run flaky hints", source)
        self.assertIn("pulseService.refresh()", source)

    def test_service_accepts_widget_settings_through_bounded_configure_contract(self):
        source = (ROOT / "Service.qml").read_text(encoding="utf-8")
        self.assertIn("function configure(settings)", source)
        self.assertIn("property string reportPath", source)
        self.assertIn("property string selectedFormat", source)
        self.assertNotIn("shell.shellConfig", source)

    def test_runtime_sources_have_no_network_execution_or_elevation_primitives(self):
        combined = "\n".join((ROOT / name).read_text(encoding="utf-8")
                             for name in ["Service.qml", "Panel.qml", "scripts/report_status.py"])
        forbidden = ["XMLHttpRequest", "curl ", "wget ", "requests.", "urllib.", "socket.",
                     "subprocess", "os.system", "Popen", "sudo ", "pkexec", "telemetry"]
        for token in forbidden:
            self.assertNotIn(token, combined, token)

    def test_adapter_contains_secure_open_and_all_declared_limits(self):
        source = (ROOT / "scripts" / "report_status.py").read_text(encoding="utf-8")
        for token in ["O_NOFOLLOW", "os.lstat", "os.fstat", "stat.S_ISREG", "MAX_FILE_BYTES = 4 * 1024 * 1024",
                      "MAX_RECORDS = 10_000", "MAX_FAILURES = 100", "MAX_FLAKY = 50",
                      "MAX_NAME = 240", "MAX_MESSAGE = 500", "MAX_OUTPUT_BYTES = 1024 * 1024",
                      'raw.decode("utf-8", "strict")', '"<!DOCTYPE"', '"<!ENTITY"']:
            self.assertIn(token, source)

    def test_shell_helpers_are_lf_executable_and_avoid_dangerous_features(self):
        attrs = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("*.sh text eol=lf", attrs)
        for name in ["install-local.sh", "remove-local.sh"]:
            path = ROOT / "scripts" / name
            raw = path.read_bytes()
            self.assertNotIn(b"\r\n", raw)
            if os.name != "nt":
                self.assertTrue(path.stat().st_mode & stat.S_IXUSR)
            source = raw.decode("utf-8")
            for token in ["sudo", "pkexec", "curl", "wget", "eval ", "source "]:
                self.assertNotIn(token, source)
        remove = (ROOT / "scripts" / "remove-local.sh").read_text(encoding="utf-8")
        self.assertIn(".local-test-pulse-install", remove)
        self.assertIn("Refusing to remove an unowned or unsafe path", remove)


@unittest.skipIf(os.name == "nt", "lifecycle execution requires native Linux/POSIX paths")
class LifecycleExecutionTests(unittest.TestCase):
    def test_install_refuses_overwrite_and_remove_requires_marker(self):
        with tempfile.TemporaryDirectory() as td:
            env = dict(os.environ, XDG_CONFIG_HOME=str(Path(td) / "config"), HOME=td)
            install = ROOT / "scripts" / "install-local.sh"
            remove = ROOT / "scripts" / "remove-local.sh"
            first = subprocess.run(["bash", str(install)], env=env, text=True, capture_output=True, check=False)
            self.assertEqual(0, first.returncode, first.stderr)
            target = Path(env["XDG_CONFIG_HOME"]) / "omarchy" / "plugins" / "godhiraj.local-test-pulse"
            self.assertTrue((target / "manifest.json").is_file())
            self.assertEqual("godhiraj.local-test-pulse", (target / ".local-test-pulse-install").read_text().strip())
            second = subprocess.run(["bash", str(install)], env=env, text=True, capture_output=True, check=False)
            self.assertNotEqual(0, second.returncode)
            gone = subprocess.run(["bash", str(remove)], env=env, text=True, capture_output=True, check=False)
            self.assertEqual(0, gone.returncode, gone.stderr)
            self.assertFalse(target.exists())
            again = subprocess.run(["bash", str(remove)], env=env, text=True, capture_output=True, check=False)
            self.assertEqual(0, again.returncode)

    def test_install_rejects_symlinked_configuration_ancestors(self):
        for level in ("config", "omarchy", "plugins"):
            with self.subTest(level=level), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                config = root / "config"
                elsewhere = root / "elsewhere"
                elsewhere.mkdir()
                if level == "config":
                    config.symlink_to(elsewhere, target_is_directory=True)
                else:
                    config.mkdir()
                    if level == "omarchy":
                        (config / "omarchy").symlink_to(elsewhere, target_is_directory=True)
                    else:
                        (config / "omarchy").mkdir()
                        (config / "omarchy" / "plugins").symlink_to(elsewhere, target_is_directory=True)
                env = dict(os.environ, XDG_CONFIG_HOME=str(config), HOME=td)
                cp = subprocess.run(["bash", str(ROOT / "scripts" / "install-local.sh")], env=env,
                                    text=True, capture_output=True, check=False)
                self.assertNotEqual(0, cp.returncode)
                self.assertEqual([], list(elsewhere.iterdir()))

    def test_remove_rejects_symlinked_configuration_ancestors(self):
        plugin_id = "godhiraj.local-test-pulse"
        for level in ("config", "omarchy", "plugins"):
            with self.subTest(level=level), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                config = root / "config"
                elsewhere = root / "elsewhere"
                if level == "config":
                    target = elsewhere / "omarchy" / "plugins" / plugin_id
                    target.mkdir(parents=True)
                    config.symlink_to(elsewhere, target_is_directory=True)
                elif level == "omarchy":
                    config.mkdir()
                    target = elsewhere / "plugins" / plugin_id
                    target.mkdir(parents=True)
                    (config / "omarchy").symlink_to(elsewhere, target_is_directory=True)
                else:
                    (config / "omarchy").mkdir(parents=True)
                    target = elsewhere / plugin_id
                    target.mkdir(parents=True)
                    (config / "omarchy" / "plugins").symlink_to(elsewhere, target_is_directory=True)
                (target / ".local-test-pulse-install").write_text(plugin_id + "\n", encoding="utf-8")
                sentinel = target / "must-remain"
                sentinel.write_text("safe", encoding="utf-8")
                env = dict(os.environ, XDG_CONFIG_HOME=str(config), HOME=td)
                cp = subprocess.run(["bash", str(ROOT / "scripts" / "remove-local.sh")], env=env,
                                    text=True, capture_output=True, check=False)
                self.assertNotEqual(0, cp.returncode)
                self.assertTrue(sentinel.is_file())


if __name__ == "__main__":
    unittest.main()
