"""Explicit admin password provisioning; secrets never appear in output or argv.

Generates/reuses creds/fastshop-admin.json (0700 directory, 0600 file). Uploads
only a salted password hash to the resolved FastShop Coolify application when
--configure-production is supplied. Does not disable Google SSO or deploy.
"""

import argparse
import json
import os
import secrets
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.auth import hash_admin_password

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--email', required=True)
    parser.add_argument('--configure-production', action='store_true')
    args = parser.parse_args()
    email = args.email.strip().lower()
    if '@' not in email:
        raise SystemExit('A valid admin email is required.')
    directory = ROOT / 'creds'
    if directory.is_symlink():
        raise SystemExit('Refusing a symlink credentials directory.')
    directory.mkdir(mode=0o700, exist_ok=True)
    directory.chmod(0o700)
    path = directory / 'fastshop-admin.json'
    if path.is_symlink():
        raise SystemExit('Refusing a symlink credential file.')
    if path.exists():
        if stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise SystemExit('Existing credentials must have mode 0600.')
        record = json.loads(path.read_text())
        if record['email'] != email:
            raise SystemExit('Existing credential belongs to a different account; not overwritten.')
    else:
        record = {'email': email, 'password': secrets.token_urlsafe(36),
            'login_url': 'https://shop.fastsme.com/login', 'created_at': datetime.now(UTC).isoformat()}
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(record, stream, indent=2)
            stream.write('\n')
    if args.configure_production:
        sys.path.insert(0, str(ROOT.parent / 'FastDevOps'))
        from cli import client, load_control_plane_env, require_app
        load_control_plane_env()
        api = client('production')
        app = api.application(require_app(api, 'fastshop')['uuid'])
        if app.get('git_repository') != 'predictivelabsai/FastShop' or app.get('fqdn') != 'https://shop.fastsme.com':
            raise SystemExit('Production target does not match FastShop.')
        variables = {'FASTSHOP_ADMIN_EMAIL': email, 'FASTSHOP_ALLOW_PASSWORD_LOGIN': 'true',
            'FASTSHOP_ADMIN_PASSWORD_HASH': hash_admin_password(record['password'])}
        api.sync_environment(app['uuid'], variables)
        rows = api.request('GET', f"/applications/{app['uuid']}/envs")
        actual = {row['key']: row for row in rows if not row.get('is_preview')}
        if any(key not in actual or not actual[key].get('is_runtime') for key in variables):
            raise SystemExit('Runtime variable presence verification failed; no secret values printed.')
        print('Runtime variable names verified. Coolify redacts values; verify credentials with a login after redeployment. Google SSO unchanged.')
    print(f'Credentials stored at {path} (0600). Password not printed.')


if __name__ == '__main__':
    main()
