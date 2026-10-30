import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CONFIG = '''server {
    listen 443 ssl;
    server_name pars.promet.pw; # managed by Certbot
    ssl_certificate /keep/fullchain.pem;
    root /var/www/pars.promet.pw/public;
}
server {
    listen 80;
    server_name pars.promet.pw;
    return 301 https://$host$request_uri;
}
'''

class PasswordSetupTests(unittest.TestCase):
    def exercise(self, fail=False):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            nginx = base / 'nginx'
            config = nginx / 'sites-available/pars.promet.pw'
            config.parent.mkdir(parents=True)
            config.write_text(CONFIG)
            credential = nginx / 'pars.promet.pw.htpasswd'
            credential.write_text('previous:hash\n')
            source = (ROOT / 'deploy/set-password.sh').read_text().split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
            source = source.replace('/etc/nginx', str(nginx)).replace('/var/backups', str(base / 'backups'))
            def command(args, **kwargs):
                if args[0] == 'openssl':
                    return SimpleNamespace(stdout='$6$example$hash\n')
                if fail and args[0] == 'nginx':
                    raise subprocess.CalledProcessError(1, args)
                return SimpleNamespace(returncode=0)
            with patch('getpass.getpass', return_value='test-only'), patch('grp.getgrnam', return_value=SimpleNamespace(gr_gid=os.getgid())), patch('subprocess.run', side_effect=command):
                if fail:
                    with self.assertRaises(subprocess.CalledProcessError):
                        exec(compile(source, '<password-setup>', 'exec'), {})
                    self.assertEqual(config.read_text(), CONFIG)
                    self.assertEqual(credential.read_text(), 'previous:hash\n')
                else:
                    exec(compile(source, '<password-setup>', 'exec'), {})
                    changed = config.read_text()
                    self.assertIn('ssl_certificate /keep/fullchain.pem;', changed)
                    self.assertIn('return 301 https://$host$request_uri;', changed)
                    self.assertEqual(changed.count('include '), 2)
                    self.assertEqual(credential.stat().st_mode & 0o777, 0o640)
                    self.assertTrue(credential.read_text().startswith('MEBELPROMET:$6$'))

    def test_preserves_https_and_protects_password_file(self):
        self.exercise()

    def test_failed_nginx_check_restores_previous_config_and_hash(self):
        self.exercise(fail=True)

if __name__ == '__main__': unittest.main()
