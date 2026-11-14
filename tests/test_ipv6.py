"""Check IPv6 state transitions on isolated fake procfs and sysfs."""
import json
import os
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
HELPER = ROOT / 'package/arthur/files/usr/libexec/arthur-ipv6'


class IPv6Tests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = pathlib.Path(temp.name)
        self.proc = self.base / 'proc'
        self.net = self.base / 'net'
        self.conf = self.proc / 'sys/net/ipv6/conf'
        self.flag = self.base / 'etc/ipv6-disabled'
        self.state = self.base / 'run'
        self.env = dict(os.environ, ARTHUR_PROC_ROOT=str(self.proc),
                        ARTHUR_SYSFS_ROOT=str(self.net), ARTHUR_PERF_STATE=str(self.state),
                        ARTHUR_IPV6_FLAG=str(self.flag))
        self.node('default', 0)
        self.node('all', 0)
        self.node('lo', 0, 1)
        self.node('br-lan', 0, 2)
        self.node('wan', 1, 3)

    def node(self, name, value, index=None):
        p = self.conf / name / 'disable_ipv6'
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f'{value}\n')
        if index is not None:
            n = self.net / name / 'ifindex'
            n.parent.mkdir(parents=True, exist_ok=True)
            n.write_text(f'{index}\n')
        return p

    def read(self, name):
        return (self.conf / name / 'disable_ipv6').read_text().strip()

    def action(self, *args):
        r = subprocess.run(['sh', str(HELPER), *args], env=self.env, text=True,
                           capture_output=True, timeout=10, check=True)
        return json.loads(r.stdout) if r.stdout else None

    def test_disable_keeps_loopback_and_restores_per_device_policy(self):
        self.assertFalse(self.action('status')['requested_disabled'])
        self.assertFalse(self.flag.exists())
        self.assertTrue(self.action('set', '1')['ipv6']['disabled'])
        self.assertEqual(self.read('br-lan'), '1')
        self.assertEqual(self.read('lo'), '0')
        self.assertEqual(self.read('all'), '0')
        self.node('usb0', 1, 4)
        self.action('reconcile')
        self.assertTrue(self.action('set', '0')['ok'])
        self.assertEqual(self.read('default'), '0')
        self.assertEqual(self.read('br-lan'), '0')
        self.assertEqual(self.read('usb0'), '0')
        self.assertEqual(self.read('wan'), '1')
        self.assertFalse(self.flag.exists())

    def test_reconcile_reapplies_persistent_choice_after_reboot(self):
        self.flag.parent.mkdir()
        self.flag.touch()
        self.action('reconcile')
        self.assertTrue(self.action('status')['disabled'])
        self.assertEqual(self.read('br-lan'), '1')
        self.node('br-lan', 0, 2)  # network manager reconfigured a live device
        self.assertFalse(self.action('status')['disabled'])
        self.action('reconcile')
        self.assertEqual(self.read('br-lan'), '1')
        self.assertTrue(self.action('set', '0')['ok'])
        self.assertEqual(self.read('br-lan'), '0')

    def test_bad_request_and_missing_ipv6_do_not_persist(self):
        self.assertEqual(self.action('set', 'garbage')['error'], 'invalid')
        (self.conf / 'default' / 'disable_ipv6').unlink()
        self.assertEqual(self.action('set', '1')['error'], 'unavailable')
        self.assertFalse(self.flag.exists())
        self.assertEqual(self.read('br-lan'), '0')

    def test_rpc_write_requires_acl(self):
        files = ROOT / 'package/arthur/files'
        acl = json.loads((files / 'usr/share/rpcd/acl.d/luci-app-arthur.json').read_text())
        arthur = acl['luci-app-arthur']
        self.assertIn('ipv6_status', arthur['read']['ubus']['arthur'])
        self.assertNotIn('ipv6_set', arthur['read']['ubus']['arthur'])
        self.assertIn('ipv6_set', arthur['write']['ubus']['arthur'])
